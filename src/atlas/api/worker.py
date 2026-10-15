"""The worker side of interactive simulations: one M4 scenario per job, in a child process.

This module imports only ATLAS domain code (never the web stack), because every simulation
worker process spawned by :class:`atlas.api.simulation.SimulationPool` imports it. A worker
announces itself with :data:`READY` once its imports are done, then answers each job: a
canonical M4 configuration (JSON text) is parsed with M4's own parser, turned into a
scenario with M4's scenario builder, analysed with
:func:`atlas.decision_analysis.analyse_scenario` (without shadow-price brackets or
frontiers) and serialised with the M4 runner's own builders.

Each worker also asks the kernel to choose it, rather than the API server, when memory
runs out (:func:`prefer_oom_kill`): a killed worker is replaced by the pool and costs one
simulation, a killed server costs the whole service.
"""

from __future__ import annotations

import math
import signal
import time
import traceback
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, Final, Literal

from atlas.decision_analysis import analyse_scenario
from atlas.decision_inputs import M3Status, QuantilePath
from atlas.errors import ConfigError
from atlas.experiment import jsonable
from atlas.optimization_config import parse_optimization_config
from atlas.optimization_runner import (
    _scenario_metrics,
    plans_frame,
    scenario_frame,
    solver_settings,
)
from atlas.optimizer import OptimizationError
from atlas.scenarios import build_scenarios

#: The first message of every worker process, and the answer to a ping.
READY: Final = ("ready", None)
PONG: Final = ("ok", "pong")
#: The OOM score adjustment of a worker process (the server keeps its own, lower one).
WORKER_OOM_SCORE_ADJ: Final = 500
OOM_SCORE_ADJ: Final = Path("/proc/self/oom_score_adj")
OOM_SCORE_MAX: Final = 1000


@dataclass(frozen=True)
class WorkerContext:
    """Everything a worker needs besides the job: the verified reference inputs."""

    dates: tuple[date, ...]
    predictions: tuple[float, ...]
    quantile_paths: Mapping[float, QuantilePath]
    demand_percentile: float
    status: M3Status


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


class _Lifetime:
    """Signals sent to the whole process group (Ctrl+C) or control group (a service
    manager's SIGTERM) must not kill a simulation in flight: the API process decides when a
    worker stops (it closes the pipe, or kills the process at a deadline)."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.busy = False
        self.stop = False

    def on_term(self, signum: int, frame: object) -> None:
        if self.busy or self.connection.poll(0):
            self.stop = True  # finish and answer the current (or sent) job, then exit
        else:
            raise SystemExit(0)


def prefer_oom_kill(path: Path = OOM_SCORE_ADJ, score: int = WORKER_OOM_SCORE_ADJ) -> bool:
    """Raise this process's OOM score adjustment by ``score`` above the one it inherited
    from the server (at most 1000, the kernel's maximum), so the kernel picks a worker
    before the server whatever the server's own adjustment (best effort; Linux only).

    Any process may raise its own adjustment (lowering it needs privileges). Returns
    whether the adjustment was raised (or already is the maximum).
    """
    try:
        inherited = int(path.read_text(encoding="ascii").strip() or "0")
        target = min(OOM_SCORE_MAX, inherited + score)
        if target > inherited:
            path.write_text(f"{target}\n", encoding="ascii")
    except (OSError, ValueError):
        return False
    return True


def worker_main(connection: Connection, context: WorkerContext) -> None:  # pragma: no cover
    """The loop of one worker process (runs in a child process; tested through the pool).

    The process announces itself once its imports are done, so the import time of a fresh
    worker never counts against a simulation's deadline. It ignores SIGINT and leaves on
    SIGTERM only between simulations (see :class:`_Lifetime`).
    """
    lifetime = _Lifetime(connection)
    prefer_oom_kill()
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lifetime.on_term)
    connection.send(READY)
    while not lifetime.stop:
        try:
            job = connection.recv()
        except (EOFError, OSError):
            return
        if job is None:
            return
        lifetime.busy = True
        reply = compute(context, job) if isinstance(job, str) else PONG
        try:
            connection.send(reply)
        except OSError:
            return
        lifetime.busy = False
