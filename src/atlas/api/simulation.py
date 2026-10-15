"""Bounded interactive simulations with the M4 engine, isolated in worker processes.

A request becomes an M4 configuration: a copy of the pinned reference configuration with
the client's HYPOTHETICAL values, one scenario (``interactive``) and no sweeps, frontiers
or shadow-price brackets. It is validated by M4's own parser and scenario builder (units,
HYPOTHETICAL labels, the surge start, the rule that a demand percentile is never a
capacity), so a simulation obeys exactly the rules of a configured M4 scenario. The worker
then runs :func:`atlas.decision_analysis.analyse_scenario` and serialises the result with
the M4 runner's own row and metrics builders, so a simulation is described exactly like a
scenario of the committed reference run.

Why worker processes: the M4 solver layer captures warnings with the process-global
``warnings.catch_warnings`` around every solve, so a warning raised by any other thread
during a solve would fail it, and concurrent solves corrupt the filter list. Solves
therefore never run in an API thread. Each worker process solves one simulation at a time;
the pool has ``max_concurrent_simulations`` workers and no queue (a request that finds no
idle worker gets 503 ``simulations_busy`` at once). A simulation that misses its deadline
gets 504: its worker process is killed and replaced, so an abandoned computation never
keeps running. The API's own threads only wait on pipes, and metadata requests never wait
for a solve.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import multiprocessing
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from multiprocessing.connection import Connection
from typing import Any, Final, Literal

from atlas.api.errors import ApiError
from atlas.api.reference import SIMULATION, ReferenceStore, day_plan, plan_variant
from atlas.api.schemas import (
    DayPlan,
    ParameterSpec,
    PlanVariant,
    ResolvedParameter,
    ScenarioOutcome,
    SimulationData,
    SimulationParameters,
    SimulationParametersData,
    SimulationRequest,
)
from atlas.decision_analysis import analyse_scenario
from atlas.decision_inputs import MARGINAL_QUANTILE_NOTICE, M3Status, QuantilePath
from atlas.errors import ConfigError
from atlas.experiment import jsonable
from atlas.optimization_config import OptimizationConfig, parse_optimization_config
from atlas.optimization_runner import (
    LABEL,
    STRUCTURAL_ASSUMPTIONS,
    _scenario_metrics,
    plans_frame,
    scenario_frame,
    solver_settings,
)
from atlas.optimizer import OptimizationError
from atlas.planning import MAX_HORIZON_DAYS
from atlas.scenarios import Scenario, build_scenarios

LOGGER: Final = logging.getLogger("atlas.api")

SCENARIO_ID: Final = "interactive"
EXPERIMENT_ID: Final = "atlas-m5-interactive-simulation"
CLIENT_ASSUMPTION: Final = (
    "Value supplied by an API client for an interactive simulation: HYPOTHETICAL, not an "
    "estimate of any NYC agency's operations."
)
REMOVED_ASSUMPTION: Final = "Limit removed by the API client for an interactive simulation."

#: Unit of every simulation parameter (the M4 configuration's units).
PARAMETER_UNITS: Final = {
    "initial_backlog": "requests",
    "regular_hours_per_day": "staff_hours_per_day",
    "regular_productivity": "requests_per_staff_hour",
    "overtime_productivity": "requests_per_staff_hour",
    "overtime_max_hours_per_day": "staff_hours_per_day",
    "overtime_total_hours": "staff_hours",
    "surge_max_per_day": "requests_per_day",
    "surge_total": "requests",
    "surge_available_from_day": "planning_day",
    "regular_wage": "usd_per_staff_hour",
    "overtime_wage": "usd_per_staff_hour",
    "surge_price": "usd_per_request",
    "backlog_penalty": "usd_per_request_day",
    "terminal_penalty": "usd_per_request",
    "terminal_backlog_max": "requests",
    "backlog_trigger": "requests",
    "discretionary_budget": "usd",
}
#: Parameters for which an explicit ``null`` removes the limit.
NULLABLE: Final = frozenset(
    {"overtime_total_hours", "surge_total", "terminal_backlog_max", "discretionary_budget"}
)
UNIT_PARAMETERS: Final = tuple(name for name in PARAMETER_UNITS if name != "discretionary_budget")

SIMULATION_LIMITATIONS: Final = (
    "Every operational parameter is HYPOTHETICAL; ATLAS holds no staffing, productivity, "
    "cost, budget, backlog or capacity data for any NYC agency.",
    "The demand is an unvalidated point forecast (or a labelled stress path), treated as "
    "known: the plan is optimal only for this demand path, and the forecast's historical "
    "error (MAE about 985 requests/day over horizons 1-30) is about the size of the daily "
    "overtime capacity.",
    "No probability of meeting any target is computed or implied: the model is deterministic "
    "and the M3 status in integrity governs every interval.",
    "One pooled city-wide unit with continuous quantities: the most favourable sharing "
    "assumption, without shifts, integer staff or fixed costs.",
    "The penalised score mixes cash with notional weights; it ranks plans and is never a "
    "cost. Shadow prices are not computed for interactive simulations.",
    "Nothing here is a validated staffing, budget or service-capacity recommendation.",
)


# --- Request -> M4 configuration --------------------------------------------------------


@dataclass(frozen=True)
class PreparedSimulation:
    """A request turned into a validated M4 configuration and scenario (in the API)."""

    config: OptimizationConfig
    config_json: str
    scenario: Scenario
    parameters: tuple[ResolvedParameter, ...]
    dates: tuple[date, ...]
    forecast_mean: float


def _quantity(value: float, unit: str, assumption: str) -> dict[str, Any]:
    return {"value": value, "unit": unit, "provenance": "HYPOTHETICAL", "assumption": assumption}


def _resolve(
    name: str, chosen: set[str], requested: Any, reference: Any
) -> tuple[Any, ResolvedParameter]:
    """The configuration entry of one parameter and how it was resolved."""
    unit = PARAMETER_UNITS[name]
    if name not in chosen:
        if reference is None:
            entry = None
            resolved = ResolvedParameter(
                name=name,
                value=None,
                unit=unit,
                provenance="HYPOTHETICAL",
                source="reference_default",
                assumption="Not limited in the reference configuration.",
            )
        else:
            entry = reference.model_dump(mode="json")
            resolved = ResolvedParameter(
                name=name,
                value=entry["value"],
                unit=unit,
                provenance="HYPOTHETICAL",
                source="reference_default",
                assumption=entry["assumption"],
            )
        return entry, resolved
    if requested is None:
        if name not in NULLABLE:
            raise ApiError(
                422,
                "validation_error",
                f"parameter {name!r} cannot be null (only {', '.join(sorted(NULLABLE))} can)",
                details=[{"field": f"parameters.{name}", "message": "null is not allowed"}],
            )
        resolved = ResolvedParameter(
            name=name,
            value=None,
            unit=unit,
            provenance="HYPOTHETICAL",
            source="removed_by_request",
            assumption=REMOVED_ASSUMPTION,
        )
        return None, resolved
    value: float | int = int(requested) if unit == "planning_day" else float(requested)
    resolved = ResolvedParameter(
        name=name,
        value=value,
        unit=unit,
        provenance="HYPOTHETICAL",
        source="request",
        assumption=CLIENT_ASSUMPTION,
    )
    return _quantity(value, unit, CLIENT_ASSUMPTION), resolved


def build_configuration(
    reference: OptimizationConfig, request: SimulationRequest
) -> tuple[dict[str, Any], tuple[ResolvedParameter, ...]]:
    """The M4 configuration payload of ``request`` (validated later by M4's parser)."""
    payload = reference.model_dump(mode="json")
    unit = dict(payload["service_units"][0])
    params = request.parameters
    chosen = set(params.model_fields_set)
    resolved: list[ResolvedParameter] = []
    base_unit = reference.unit
    for name in UNIT_PARAMETERS:
        entry, item = _resolve(name, chosen, getattr(params, name), getattr(base_unit, name))
        unit[name] = entry
        resolved.append(item)
    budget, item = _resolve(
        "discretionary_budget", chosen, params.discretionary_budget, reference.discretionary_budget
    )
    resolved.append(item)
    demand = request.demand
    if demand.basis == "m3_marginal_quantile":
        if demand.quantile is None:
            raise ApiError(
                422,
                "validation_error",
                "demand.quantile is required with basis m3_marginal_quantile",
                details=[{"field": "demand.quantile", "message": "required"}],
            )
        basis: dict[str, Any] = {
            "kind": "m3_marginal_quantile",
            "quantile": demand.quantile,
            "interpretation": "hypothetical_stress_not_a_probability",
        }
    else:
        if demand.quantile is not None:
            raise ApiError(
                422,
                "validation_error",
                "demand.quantile is only allowed with basis m3_marginal_quantile",
                details=[{"field": "demand.quantile", "message": "not allowed"}],
            )
        basis = {"kind": "point_forecast"}
    adjustments: dict[str, Any] = {}
    if demand.multiplier != 1.0:
        adjustments["demand_multiplier"] = _quantity(demand.multiplier, "ratio", CLIENT_ASSUMPTION)
    if demand.shift_per_day != 0.0:
        adjustments["demand_shift_per_day"] = _quantity(
            demand.shift_per_day, "requests_per_day", CLIENT_ASSUMPTION
        )
    payload.update(
        experiment_id=EXPERIMENT_ID,
        description=(
            "Interactive M5 simulation: the M4 reference configuration with HYPOTHETICAL "
            "values supplied by an API client."
        ),
        horizon_days=request.horizon_days,
        service_units=[unit],
        discretionary_budget=budget,
        scenarios=[
            {
                "scenario_id": SCENARIO_ID,
                "description": "HYPOTHETICAL scenario defined by an API client (interactive).",
                "demand_basis": basis,
                "adjustments": adjustments,
            }
        ],
        sensitivity=None,
        frontier=None,
        shadow_prices=None,
    )
    return payload, tuple(resolved)


def _rejected(exc: ConfigError) -> ApiError:
    message = str(exc)
    message = message.removeprefix("invalid optimization configuration: ")
    return ApiError(422, "simulation_rejected", message[:600])


def prepare(store: ReferenceStore, request: SimulationRequest) -> PreparedSimulation:
    """Validate ``request`` with M4's parser and scenario builder (no solve; 422 on error)."""
    reference, inputs = store.require_simulation()
    payload, resolved = build_configuration(reference, request)
    try:
        config = parse_optimization_config(payload)
        dates, point = inputs.projection.first(config.horizon_days)
        (scenario,) = build_scenarios(
            config, dates, point, store.quantile_paths, inputs.demand_percentile
        )
    except ConfigError as exc:
        raise _rejected(exc) from exc
    return PreparedSimulation(
        config=config,
        config_json=config.canonical_json(),
        scenario=scenario,
        parameters=resolved,
        dates=tuple(dates),
        forecast_mean=math.fsum(point) / len(point),
    )


# --- Worker side ------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkerContext:
    """Everything a worker needs besides the job: the verified reference inputs."""

    dates: tuple[date, ...]
    predictions: tuple[float, ...]
    quantile_paths: Mapping[float, QuantilePath]
    demand_percentile: float
    status: M3Status


def worker_context(store: ReferenceStore) -> WorkerContext:
    _, inputs = store.require_simulation()
    return WorkerContext(
        dates=inputs.projection.target_dates,
        predictions=inputs.projection.predictions,
        quantile_paths=dict(store.quantile_paths),
        demand_percentile=inputs.demand_percentile,
        status=inputs.status,
    )


Reply = tuple[Literal["ok", "rejected", "failed", "error"], Any]


def compute(context: WorkerContext, config_json: str) -> Reply:
    """Run one simulation (in a worker process; also callable directly in tests).

    Returns ``("ok", payload)`` with the JSON-ready M4 serialisation of the scenario,
    ``("rejected", message)`` for an input M4 refuses, ``("failed", message)`` when no
    certified result exists, and ``("error", traceback)`` for an unexpected exception.
    """
    started = time.perf_counter()
    try:
        config = parse_optimization_config(config_json)
        horizon = config.horizon_days
        (scenario,) = build_scenarios(
            config,
            context.dates[:horizon],
            context.predictions[:horizon],
            context.quantile_paths,
            context.demand_percentile,
        )
        result = analyse_scenario(
            scenario.scenario_id,
            scenario.problem,
            solver_settings(config),
            scenario.triggers,
            shadow_steps=None,
            frontier_points=None,
        )
    except ConfigError as exc:
        return "rejected", str(exc)
    except OptimizationError as exc:
        return "failed", f"the solver could not produce a certified result: {exc}"[:600]
    except Exception:  # an unexpected failure is reported to the API, which logs it
        return "error", traceback.format_exc()
    if result.problems:
        return "failed", "a programme failed an independent check: " + "; ".join(result.problems)[
            :600
        ]
    forecast_mean = math.fsum(context.predictions[:horizon]) / horizon
    return "ok", {
        "outcome": jsonable(_scenario_metrics(result, scenario)),
        "plans": jsonable(plans_frame([result], [scenario], context.status).to_dict("records")),
        "variants": jsonable(
            scenario_frame([result], [scenario], context.status, forecast_mean).to_dict("records")
        ),
        "compute_seconds": time.perf_counter() - started,
    }


def worker_main(connection: Connection, context: WorkerContext) -> None:  # pragma: no cover
    """The loop of one worker process (runs in a child process; tested through the pool).

    The process announces itself once its imports are done, so the import time of a fresh
    worker never counts against a simulation's deadline.
    """
    connection.send(READY)
    while True:
        try:
            job = connection.recv()
        except (EOFError, OSError):
            return
        if job is None:
            return
        connection.send(compute(context, job) if isinstance(job, str) else PONG)


# --- The pool ---------------------------------------------------------------------------

#: The first message of every worker process, and the answer to a ping.
READY: Final = ("ready", None)
PONG: Final = ("ok", "pong")
#: How long a fresh worker may take to import ATLAS before it counts as failed.
STARTUP_SECONDS: Final = 120.0

#: The function a worker process runs: ``target(connection, context)``.
WorkerTarget = Callable[[Connection, WorkerContext], None]


class _Worker:
    """One worker process and its pipe. Every state change holds the worker's lock, so a
    restart after a timeout and the pool's shutdown cannot interleave; once closed, a
    worker is never started again."""

    def __init__(self, context: WorkerContext, mp: Any, target: WorkerTarget) -> None:
        self._context = context
        self._mp = mp
        self._target = target
        self._lock = threading.Lock()
        self.closed = False
        self.ready = False
        self.process: Any = None
        self.connection: Connection | None = None
        with self._lock:
            self._start()

    def _start(self) -> None:
        parent, child = self._mp.Pipe(duplex=True)
        process = self._mp.Process(
            target=self._target,
            args=(child, self._context),
            name="atlas-simulation-worker",
            daemon=True,
        )
        process.start()
        child.close()
        self.process, self.connection, self.ready = process, parent, False

    def _stop(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.process is not None:
            if self.process.is_alive():
                self.process.kill()
            self.process.join(timeout=5)
            self.process = None
        self.ready = False

    def restart(self) -> None:
        """Kill the process and start a fresh one (nothing, once the worker is closed)."""
        with self._lock:
            self._stop()
            if not self.closed:
                self._start()

    def close(self) -> None:
        with self._lock:
            self.closed = True
            if self.connection is not None:
                with contextlib.suppress(OSError, BrokenPipeError):
                    self.connection.send(None)
            self._stop()


class Busy(Exception):
    """No idle worker."""


class Timeout(Exception):
    """The deadline passed; the worker was replaced."""


class Crashed(Exception):
    """The worker process died or never became ready; it was replaced."""


class SimulationPool:
    """A fixed number of worker processes, one simulation each, no queue."""

    def __init__(
        self, context: WorkerContext, size: int, target: WorkerTarget = worker_main
    ) -> None:
        # spawn, never fork: the API process has threads (and HiGHS starts native ones),
        # and a forked child of a multi-threaded process can deadlock.
        mp = multiprocessing.get_context("spawn")
        self._lock = threading.Lock()
        self._workers = [_Worker(context, mp, target) for _ in range(size)]
        self._idle = list(self._workers)
        self._closed = False
        self.size = size

    def acquire(self) -> _Worker:
        with self._lock:
            if self._closed or not self._idle:
                raise Busy
            return self._idle.pop()

    def release(self, worker: _Worker) -> None:
        with self._lock:
            if not self._closed:
                self._idle.append(worker)

    def pids(self) -> list[int]:
        return [w.process.pid for w in self._workers if w.process is not None]

    @property
    def idle(self) -> int:
        with self._lock:
            return len(self._idle)

    def exchange(self, worker: _Worker, job: str | None, deadline: float) -> Reply:
        """Send ``job`` to ``worker`` and wait at most ``deadline`` seconds (blocking).

        ``None`` pings the worker. A fresh worker is first given ``STARTUP_SECONDS`` to
        finish importing (not counted against the deadline). On timeout or a dead worker
        the process is killed and replaced before the worker is released, so it can never
        return a stale reply to a later request.
        """
        try:
            connection = worker.connection
            if connection is None:
                raise Crashed
            try:
                if not worker.ready:
                    if not connection.poll(STARTUP_SECONDS) or connection.recv() != READY:
                        worker.restart()
                        raise Crashed
                    worker.ready = True
                connection.send(job if job is not None else ("ping",))
                if not connection.poll(deadline):
                    worker.restart()
                    raise Timeout
                reply: Reply = connection.recv()
            except (EOFError, OSError, BrokenPipeError) as exc:
                worker.restart()
                raise Crashed from exc
            return reply
        finally:
            self.release(worker)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._idle = []
        for worker in self._workers:
            worker.close()


class SimulationService:
    """Bounded simulations for the HTTP layer (see the module docstring)."""

    def __init__(
        self,
        store: ReferenceStore,
        *,
        size: int,
        timeout_seconds: float,
        worker_target: WorkerTarget = worker_main,
    ) -> None:
        self._store = store
        self._timeout = timeout_seconds
        self._target = worker_target
        self._pool: SimulationPool | None = None
        self._threads = ThreadPoolExecutor(max_workers=size, thread_name_prefix="atlas-sim-wait")
        self._size = size
        self._lock = threading.Lock()
        self._closed = False

    def _ensure_pool(self) -> SimulationPool:
        with self._lock:
            if self._closed:
                raise ApiError(
                    503, "artifact_unavailable", "the simulation service has been stopped"
                )
            if self._pool is None:
                self._pool = SimulationPool(worker_context(self._store), self._size, self._target)
            return self._pool

    def start(self) -> None:
        """Start the worker processes now (they import ATLAS in the background).

        Never raises: when the reference data for simulations is unavailable, no worker is
        started and every simulation request answers 503 (``/health/ready`` says why).
        """
        state = self._store.status.get(SIMULATION)
        if state is None or not state.ready:
            return
        try:
            self._ensure_pool()
        except Exception:  # startup must not fail: simulations then answer 503 or 500
            LOGGER.exception(
                "simulation workers not started", extra={"event": "workers_not_started"}
            )

    def warm_up(self, timeout: float = 60.0) -> int:
        """Ping every idle worker (blocking); returns how many answered."""
        pool = self._ensure_pool()
        answered = 0
        for _ in range(pool.size):
            try:
                worker = pool.acquire()
            except Busy:
                break
            try:
                reply = pool.exchange(worker, None, timeout)
                answered += reply == PONG
            except (Timeout, Crashed):
                continue
        return answered

    def worker_pids(self) -> list[int]:
        """Process ids of the current worker processes (empty before the first start)."""
        pool = self._pool
        return [] if pool is None else pool.pids()

    @property
    def idle_workers(self) -> int:
        return 0 if self._pool is None else self._pool.idle

    def close(self) -> None:
        with self._lock:
            self._closed = True
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.close()
        self._threads.shutdown(wait=False, cancel_futures=True)

    async def run(self, request: SimulationRequest) -> tuple[SimulationData, float]:
        """Validate, then compute in a worker; returns the data and the compute seconds."""
        prepared = prepare(self._store, request)
        pool = self._ensure_pool()
        try:
            worker = pool.acquire()
        except Busy as exc:
            raise ApiError(
                503,
                "simulations_busy",
                f"all {pool.size} simulation workers are busy; retry shortly",
                headers={"Retry-After": "1"},
            ) from exc
        loop = asyncio.get_running_loop()
        try:
            status, payload = await loop.run_in_executor(
                self._threads, pool.exchange, worker, prepared.config_json, self._timeout
            )
        except Timeout as exc:
            raise ApiError(
                504,
                "simulation_timeout",
                f"the simulation did not finish within {self._timeout:g} s and was stopped",
            ) from exc
        except Crashed as exc:
            raise ApiError(
                500, "simulation_failed", "the simulation worker stopped unexpectedly"
            ) from exc
        if status == "rejected":
            raise _rejected(ConfigError(str(payload)))
        if status == "failed":
            LOGGER.warning("simulation failed: %s", payload, extra={"event": "simulation_failed"})
            raise ApiError(
                500,
                "simulation_failed",
                "no certified result: " + str(payload)[:300],
            )
        if status != "ok" or not isinstance(payload, dict):
            LOGGER.error(
                "simulation worker error:\n%s", payload, extra={"event": "simulation_error"}
            )
            raise ApiError(500, "internal_error", "internal server error")
        return self._data(prepared, payload), float(payload["compute_seconds"])

    def _data(self, prepared: PreparedSimulation, payload: Mapping[str, Any]) -> SimulationData:
        scenario = prepared.scenario
        outcome = ScenarioOutcome.model_validate(payload["outcome"])
        plans: list[DayPlan] = [day_plan(row) for row in payload["plans"]]
        variants: list[PlanVariant] = [plan_variant(row) for row in payload["variants"]]
        unit = scenario.problem.units[0]
        capacity = math.fsum(unit.regular_capacity(d) for d in range(unit.horizon)) / unit.horizon
        quantile = scenario.quantile is not None
        return SimulationData(
            simulation_id=prepared.config.fingerprint(),
            label=LABEL,
            horizon_days=prepared.config.horizon_days,
            first_date=prepared.dates[0],
            last_date=prepared.dates[-1],
            demand_basis=scenario.demand_basis,
            demand_notice=MARGINAL_QUANTILE_NOTICE if quantile else None,
            parameters=list(prepared.parameters),
            capacity_to_point_forecast_mean_ratio=capacity / prepared.forecast_mean,
            outcome=outcome,
            plans=variants,
            daily_plans=plans,
            structural_assumptions=list(STRUCTURAL_ASSUMPTIONS),
            limitations=list(SIMULATION_LIMITATIONS),
        )


# --- Parameter catalogue ----------------------------------------------------------------


def _bounds(name: str) -> tuple[float, float]:
    field = SimulationParameters.model_fields[name]
    low = high = None
    for item in field.metadata:
        low = getattr(item, "ge", low)
        high = getattr(item, "le", high)
    if low is None or high is None:  # pragma: no cover - every field declares both
        raise AssertionError(f"{name} has no declared range")
    return float(low), float(high)


def parameter_catalogue(store: ReferenceStore) -> SimulationParametersData:
    """Defaults (the reference configuration) and hard ranges of every parameter."""
    reference, inputs = store.require_simulation()
    specs = []
    for name in (*UNIT_PARAMETERS, "discretionary_budget"):
        current = (
            reference.discretionary_budget
            if name == "discretionary_budget"
            else getattr(reference.unit, name)
        )
        low, high = _bounds(name)
        specs.append(
            ParameterSpec(
                name=name,
                unit=PARAMETER_UNITS[name],
                default=None if current is None else current.value,
                minimum=low,
                maximum=high,
                nullable=name in NULLABLE,
                default_assumption="Not limited in the reference configuration."
                if current is None
                else current.assumption,
            )
        )
    return SimulationParametersData(
        horizon_days={"default": reference.horizon_days, "minimum": 1, "maximum": MAX_HORIZON_DAYS},
        demand={
            "basis": ["point_forecast", "m3_marginal_quantile"],
            "quantile": sorted(store.quantile_paths),
            "multiplier": {"default": 1.0, "minimum": 0.5, "maximum": 2.0},
            "shift_per_day": {
                "default": 0.0,
                "minimum": 0.0,
                "maximum": 5_000.0,
                "unit": "requests_per_day",
            },
            "first_date": inputs.projection.target_dates[0].isoformat(),
            "notice": MARGINAL_QUANTILE_NOTICE,
        },
        parameters=specs,
        legacy_demand_percentile_requests_per_day=inputs.demand_percentile,
        notes=[
            "Every value is HYPOTHETICAL. Defaults are the illustrative values of the M4 "
            "reference configuration, chosen to exercise the model, not estimates.",
            "Money and penalty rates are 0 or at least 0.01; smaller nonzero rates would be "
            "dropped by the solver.",
            "A regular capacity (hours x productivity) within 1 % of the legacy demand "
            "percentile is rejected (422 simulation_rejected): a demand percentile is never "
            "a service capacity.",
        ],
    )
