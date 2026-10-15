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
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from http import HTTPStatus
from multiprocessing.connection import Connection
from typing import Any, Final

from pydantic import BaseModel

from atlas.api.problems import ApiError, invalid
from atlas.api.reference import SIMULATION, ReferenceStore, day_plan, plan_variant
from atlas.api.schemas import (
    STRESS_QUANTILE_LEVELS,
    ComponentStatus,
    DayPlan,
    DemandOptions,
    DemandSettings,
    HorizonSpec,
    ParameterSpec,
    PlanVariant,
    RangeSpec,
    ResolvedParameter,
    ScenarioOutcome,
    SimulationData,
    SimulationParameters,
    SimulationParametersData,
    SimulationRequest,
    WorkerPoolStatus,
)
from atlas.api.worker import PONG, READY, Reply, WorkerContext, worker_main
from atlas.decision_inputs import MARGINAL_QUANTILE_NOTICE
from atlas.errors import ConfigError
from atlas.optimization_config import OptimizationConfig, parse_optimization_config
from atlas.optimization_runner import (
    LABEL,
    STRUCTURAL_ASSUMPTIONS,
)
from atlas.planning import MAX_HORIZON_DAYS
from atlas.scenarios import Scenario, build_scenarios

LOGGER: Final = logging.getLogger("atlas.api")

#: The readiness component of the worker processes (``/health/ready``).
WORKERS: Final = "simulation_workers"
SCENARIO_ID: Final = "interactive"
EXPERIMENT_ID: Final = "atlas-m5-interactive-simulation"
CLIENT_ASSUMPTION: Final = (
    "Value supplied by an API client for an interactive simulation: HYPOTHETICAL, not an "
    "estimate of any NYC agency's operations."
)
REMOVED_ASSUMPTION: Final = "Limit removed by the API client for an interactive simulation."
REFERENCE_ASSUMPTION: Final = (
    "Reference value of the M4 configuration: illustrative and HYPOTHETICAL, not an estimate. "
    "Its documented rationale (reference_assumption) describes the 30-day reference with "
    "every other value at its default and may not hold for this simulation."
)
UNLIMITED_ASSUMPTION: Final = "Not limited in the reference configuration."

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
#: Limits that apply to the whole horizon (never prorated to a shorter one).
HORIZON_TOTALS: Final = ("overtime_total_hours", "surge_total", "discretionary_budget")

FIXED_LIMITATIONS: Final = (
    "Every operational parameter is HYPOTHETICAL; ATLAS holds no staffing, productivity, "
    "cost, budget, backlog or capacity data for any NYC agency.",
    "No probability of meeting any target is computed or implied: the model is deterministic "
    "and the M3 status in integrity governs every interval.",
    "One pooled city-wide unit with continuous quantities: the most favourable sharing "
    "assumption, without shifts, integer staff or fixed costs.",
    "The penalised score mixes cash with notional weights; it ranks plans and is never a "
    "cost. Shadow prices are not computed for interactive simulations.",
    "Nothing here is a validated staffing, budget or service-capacity recommendation.",
)


def limitations(
    config: OptimizationConfig,
    parameters: Sequence[ResolvedParameter],
    reference_horizon: int,
    mae: float | None,
) -> list[str]:
    """The limitations of one simulation, worded from its own resolved values."""
    unit = config.unit
    overtime = unit.overtime_max_hours_per_day.value * unit.overtime_productivity.value
    demand = (
        "The demand is an unvalidated point forecast (or a labelled stress path), treated as "
        "known: the plan is optimal only for this demand path."
    )
    if mae is not None:
        demand += f" The forecast's historical MAE over horizons 1-30 was {mae:,.0f} requests/day"
        demand += (
            f", {mae / overtime:.1f} times this simulation's largest daily overtime capacity "
            f"({overtime:,.0f} requests/day)."
            if overtime > 0
            else "; this simulation has no overtime capacity."
        )
    items = [FIXED_LIMITATIONS[0], demand, *FIXED_LIMITATIONS[1:]]
    defaults = [
        p.name
        for p in parameters
        if p.name in HORIZON_TOTALS and p.source == "reference_default" and p.value is not None
    ]
    if config.horizon_days < reference_horizon and defaults:
        items.insert(
            2,
            f"Horizon totals are not prorated: {', '.join(defaults)} keep their "
            f"{reference_horizon}-day reference values over this {config.horizon_days}-day "
            "horizon.",
        )
    return items


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
                assumption=UNLIMITED_ASSUMPTION,
                reference_assumption=None,
            )
        else:
            # The configuration keeps the reference entry unchanged (so an empty request
            # resolves to the reference scenario); the response words it neutrally.
            entry = reference.model_dump(mode="json")
            resolved = ResolvedParameter(
                name=name,
                value=entry["value"],
                unit=unit,
                provenance="HYPOTHETICAL",
                source="reference_default",
                assumption=REFERENCE_ASSUMPTION,
                reference_assumption=entry["assumption"],
            )
        return entry, resolved
    if requested is None:
        if name not in NULLABLE:
            raise invalid(
                f"parameters.{name}",
                f"null is not allowed (only {', '.join(sorted(NULLABLE))} can be null)",
                f"parameter {name!r} cannot be null",
            )
        resolved = ResolvedParameter(
            name=name,
            value=None,
            unit=unit,
            provenance="HYPOTHETICAL",
            source="removed_by_request",
            assumption=REMOVED_ASSUMPTION,
            reference_assumption=None,
        )
        return None, resolved
    # + 0.0 turns -0.0 into 0.0: equal simulations get equal configurations and ids.
    value: float | int = int(requested) if unit == "planning_day" else float(requested) + 0.0
    resolved = ResolvedParameter(
        name=name,
        value=value,
        unit=unit,
        provenance="HYPOTHETICAL",
        source="request",
        assumption=CLIENT_ASSUMPTION,
        reference_assumption=None,
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
            raise invalid(
                "demand.quantile",
                "required with basis m3_marginal_quantile",
                "demand.quantile is required with basis m3_marginal_quantile",
            )
        basis: dict[str, Any] = {
            "kind": "m3_marginal_quantile",
            "quantile": demand.quantile,
            "interpretation": "hypothetical_stress_not_a_probability",
        }
    else:
        if demand.quantile is not None:
            raise invalid(
                "demand.quantile",
                "only allowed with basis m3_marginal_quantile",
                "demand.quantile is only allowed with basis m3_marginal_quantile",
            )
        basis = {"kind": "point_forecast"}
    adjustments: dict[str, Any] = {}
    if demand.multiplier != 1.0:
        adjustments["demand_multiplier"] = _quantity(demand.multiplier, "ratio", CLIENT_ASSUMPTION)
    if demand.shift_per_day != 0.0:  # -0.0 == 0.0: no adjustment
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


_CONFIG_PREFIX: Final = "invalid optimization configuration: "
_SCENARIO_PREFIX: Final = f"scenario {SCENARIO_ID}: "
#: Request fields of the scenario adjustments M4 validates (configuration path -> field).
_ADJUSTMENT_FIELDS: Final = {
    "demand_multiplier": "demand.multiplier",
    "demand_shift_per_day": "demand.shift_per_day",
}


def _field(location: str, message: str) -> list[str]:
    """The request fields an M4 configuration error is about (``<body>`` if unknown)."""
    parts = location.split(".")
    if len(parts) > 2 and parts[:2] == ["service_units", "0"] and parts[2] in PARAMETER_UNITS:
        return [f"parameters.{parts[2]}"]
    if parts[0] == "discretionary_budget":
        return ["parameters.discretionary_budget"]
    if parts[0] == "horizon_days":
        return ["horizon_days"]
    if len(parts) > 3 and parts[:3] == ["scenarios", "0", "adjustments"]:
        return [_ADJUSTMENT_FIELDS.get(parts[3], "demand")]
    if parts[:3] == ["scenarios", "0", "demand_basis"]:
        return ["demand.quantile"]
    if "surge_available_from_day" in message:
        return ["parameters.surge_available_from_day"]
    if "regular capacity" in message:
        return ["parameters.regular_hours_per_day", "parameters.regular_productivity"]
    return ["<body>"]


def _rejected(exc: ConfigError) -> ApiError:
    """422 ``simulation_rejected`` for an input M4's own rules refuse, by request field."""
    text = str(exc)
    if text.startswith(_CONFIG_PREFIX):
        problems = [
            (location, message.removeprefix("Value error, "))
            for location, _, message in (
                part.partition(": ") for part in text.removeprefix(_CONFIG_PREFIX).split("; ")
            )
        ]
    else:
        problems = [("<root>", text.removeprefix(_SCENARIO_PREFIX))]
    details = [
        {"field": name, "message": message}
        for location, message in problems
        for name in _field(location, message)
    ]
    summary = "; ".join(dict.fromkeys(message for _, message in problems))
    return ApiError(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        "simulation_rejected",
        summary[:600],
        details=details,
    )


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


def worker_context(store: ReferenceStore) -> WorkerContext:
    _, inputs = store.require_simulation()
    return WorkerContext(
        dates=inputs.projection.target_dates,
        predictions=inputs.projection.predictions,
        quantile_paths=dict(store.quantile_paths),
        demand_percentile=inputs.demand_percentile,
        status=inputs.status,
    )


# --- The pool ---------------------------------------------------------------------------

#: How long a fresh worker may take to import ATLAS before it counts as failed.
STARTUP_SECONDS: Final = 120.0
#: Consecutive worker crashes after which ``/health/ready`` reports the workers not ready.
CRASH_LIMIT: Final = 3

#: The function a worker process runs: ``target(connection, context)``.
WorkerTarget = Callable[[Connection, WorkerContext], None]


@dataclass(frozen=True)
class Stopped:
    """A worker process that was stopped (for the log)."""

    pid: int | None
    exit_code: int | None


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

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.is_alive()

    def _start(self) -> None:
        """Start a fresh process. A failure (no memory, no process or file slot) is logged
        and leaves the worker without a process; the next exchange tries again."""
        parent = child = None
        try:
            parent, child = self._mp.Pipe(duplex=True)
            process = self._mp.Process(
                target=self._target,
                args=(child, self._context),
                name="atlas-simulation-worker",
                daemon=True,
            )
            process.start()
        except Exception:
            for end in (parent, child):
                if end is not None:
                    end.close()
            LOGGER.exception(
                "simulation worker could not be started", extra={"event": "worker_start_failed"}
            )
            return
        child.close()
        self.process, self.connection, self.ready = process, parent, False

    def _stop(self) -> Stopped:
        stopped = Stopped(None, None)
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.process is not None:
            if self.process.is_alive():
                self.process.kill()
            self.process.join(timeout=5)
            stopped = Stopped(self.process.pid, self.process.exitcode)
            self.process = None
        self.ready = False
        return stopped

    def restart(self) -> Stopped:
        """Kill the process and start a fresh one (nothing, once the worker is closed).
        Never raises: a failed start leaves the worker to be restarted on its next use."""
        with self._lock:
            stopped = self._stop()
            if not self.closed:
                self._start()
            return stopped

    def revive(self) -> None:
        """Start a process for a worker that has none (after a failed start)."""
        with self._lock:
            if not self.closed and self.connection is None:
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

    def __init__(self, stopped: Stopped) -> None:
        super().__init__("simulation deadline passed")
        self.stopped = stopped


class Crashed(Exception):
    """The worker process died or never became ready; it was replaced."""

    def __init__(self, stopped: Stopped) -> None:
        super().__init__("simulation worker stopped")
        self.stopped = stopped


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
    def alive(self) -> int:
        return sum(w.alive for w in self._workers)

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
            if worker.connection is None:
                worker.revive()  # a previous start failed: try again now
            connection = worker.connection
            if connection is None:
                raise Crashed(Stopped(None, None))
            try:
                if not worker.ready:
                    if not connection.poll(STARTUP_SECONDS) or connection.recv() != READY:
                        raise Crashed(worker.restart())
                    worker.ready = True
                connection.send(job if job is not None else ("ping",))
                if not connection.poll(deadline):
                    raise Timeout(worker.restart())
                reply: Reply = connection.recv()
            except (EOFError, OSError, BrokenPipeError) as exc:
                raise Crashed(worker.restart()) from exc
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
        self._completed = self._timeouts = self._crashes = self._consecutive_crashes = 0

    def _ensure_pool(self) -> SimulationPool:
        with self._lock:
            if self._closed:
                raise ApiError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "artifact_unavailable",
                    "the simulation service has been stopped",
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

    def _record(self, outcome: str, stopped: Stopped | None = None) -> None:
        """Count one exchange (``answered``, ``timeout`` or ``crash``) and log a lost worker."""
        with self._lock:
            if outcome == "answered":
                self._completed += 1
                self._consecutive_crashes = 0
            elif outcome == "timeout":
                self._timeouts += 1
            else:
                self._crashes += 1
                self._consecutive_crashes += 1
        if stopped is not None:
            event = "simulation_timeout" if outcome == "timeout" else "worker_crashed"
            LOGGER.warning(
                "simulation worker replaced (%s)",
                event,
                extra={
                    "event": event,
                    "worker_pid": stopped.pid,
                    "exit_code": stopped.exit_code,
                },
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
            except Timeout as exc:
                self._record("timeout", exc.stopped)
                continue
            except Crashed as exc:
                self._record("crash", exc.stopped)
                continue
            answered += reply == PONG
        return answered

    def worker_pids(self) -> list[int]:
        """Process ids of the current worker processes (empty before the first start)."""
        pool = self._pool
        return [] if pool is None else pool.pids()

    @property
    def idle_workers(self) -> int:
        return 0 if self._pool is None else self._pool.idle

    def status(self) -> WorkerPoolStatus:
        """Pool size, idle workers and counters since the service started."""
        pool = self._pool
        with self._lock:
            return WorkerPoolStatus(
                workers=self._size,
                alive=0 if pool is None else pool.alive,
                idle=0 if pool is None else pool.idle,
                started=pool is not None and not self._closed,
                completed=self._completed,
                timeouts=self._timeouts,
                crashes=self._crashes,
                consecutive_crashes=self._consecutive_crashes,
            )

    def health(self) -> ComponentStatus:
        """The ``simulation_workers`` readiness component."""
        status = self.status()
        if not status.started:
            detail = "stopped" if self._closed else "the worker processes are not running"
        elif status.alive == 0:
            detail = "no worker process could be started (see the server log)"
        elif status.consecutive_crashes >= CRASH_LIMIT:
            detail = f"the last {status.consecutive_crashes} simulations lost their worker"
        else:
            return ComponentStatus(component=WORKERS, ready=True, detail=None)
        return ComponentStatus(component=WORKERS, ready=False, detail=detail)

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
                HTTPStatus.SERVICE_UNAVAILABLE,
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
            self._record("timeout", exc.stopped)
            raise ApiError(
                HTTPStatus.GATEWAY_TIMEOUT,
                "simulation_timeout",
                f"the simulation did not finish within {self._timeout:g} s and was stopped",
            ) from exc
        except Crashed as exc:
            self._record("crash", exc.stopped)
            raise ApiError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "simulation_failed",
                "the simulation worker stopped unexpectedly",
            ) from exc
        self._record("answered")
        if status == "rejected":
            raise _rejected(ConfigError(str(payload)))
        if status == "failed":
            LOGGER.warning("simulation failed: %s", payload, extra={"event": "simulation_failed"})
            raise ApiError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "simulation_failed",
                "no certified result: " + str(payload)[:300],
            )
        if status != "ok" or not isinstance(payload, dict):
            LOGGER.error(
                "simulation worker error:\n%s", payload, extra={"event": "simulation_error"}
            )
            raise ApiError(
                HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "internal server error"
            )
        return self._data(prepared, payload), float(payload["compute_seconds"])

    def _data(self, prepared: PreparedSimulation, payload: Mapping[str, Any]) -> SimulationData:
        scenario = prepared.scenario
        outcome = ScenarioOutcome.model_validate(payload["outcome"])
        plans: list[DayPlan] = [day_plan(row) for row in payload["plans"]]
        variants: list[PlanVariant] = [plan_variant(row) for row in payload["variants"]]
        quantile = scenario.quantile is not None
        reference, inputs = self._store.require_simulation()
        return SimulationData(
            simulation_id=prepared.config.fingerprint(),
            label=LABEL,
            horizon_days=prepared.config.horizon_days,
            first_date=prepared.dates[0],
            last_date=prepared.dates[-1],
            demand_basis=scenario.demand_basis,
            demand_notice=MARGINAL_QUANTILE_NOTICE if quantile else None,
            parameters=list(prepared.parameters),
            capacity_to_point_forecast_mean_ratio=(
                scenario.regular_capacity_mean() / prepared.forecast_mean
            ),
            outcome=outcome,
            plans=variants,
            daily_plans=plans,
            structural_assumptions=list(STRUCTURAL_ASSUMPTIONS),
            limitations=limitations(
                prepared.config,
                prepared.parameters,
                reference.horizon_days,
                inputs.projection.accuracy.mae,
            ),
        )


# --- Parameter catalogue ----------------------------------------------------------------


def _bounds(name: str, model: type[BaseModel] = SimulationParameters) -> tuple[float, float]:
    field = model.model_fields[name]
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
                default_assumption=UNLIMITED_ASSUMPTION if current is None else current.assumption,
            )
        )
    defaults = DemandSettings()

    def demand_range(name: str, unit: str) -> RangeSpec:
        low, high = _bounds(name, DemandSettings)
        return RangeSpec(default=getattr(defaults, name), minimum=low, maximum=high, unit=unit)

    return SimulationParametersData(
        horizon_days=HorizonSpec(
            default=reference.horizon_days,
            minimum=1,
            maximum=MAX_HORIZON_DAYS,
            first_date=inputs.projection.target_dates[0],
        ),
        demand=DemandOptions(
            basis=["point_forecast", "m3_marginal_quantile"],
            quantile=[level for level in STRESS_QUANTILE_LEVELS if level in store.quantile_paths],
            multiplier=demand_range("multiplier", "ratio"),
            shift_per_day=demand_range("shift_per_day", "requests_per_day"),
            notice=MARGINAL_QUANTILE_NOTICE,
        ),
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
