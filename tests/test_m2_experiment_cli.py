"""Experiment configuration, provenance, run persistence, M2 CLI commands, legacy-v0 tie-ins.

Covers :mod:`atlas.experiment` (strict configuration parsing, pinned protocol fingerprints,
deterministic JSON/CSV serialisation, atomic run directories, git, code, environment and
series provenance), :mod:`atlas.runner` and the M2 commands of :mod:`atlas.cli`
(``backtest run`` / ``verify`` / ``legacy`` and ``forecast``, including the pre-registration
gate: selection period by default, ``--sealed-selection`` for every other period,
``--require-clean``, snapshot checks, fail-closed integrity), :mod:`atlas.legacy_refit`, and
the backtest engine on the committed legacy-v0 snapshot.

Expected values never come from the code under test. They come from:

* the committed CSV files read with the ``csv`` module: actual demand (``daily_total``), the
  90 published holdout predictions, the 30 published recursive forecasts and the published
  metrics (514.30 / 676.70 / 4.93 for the regression, 851.16 / 1107.24 / 8.23 for the
  7-day seasonal baseline);
* closed forms: origin counts by date arithmetic, ``target = origin + h``, naive ``y(o)``,
  seasonal naive ``y(o + h - 7 * ceil(h / 7))``, same-weekday windows
  ``y(o + h - 7w)`` for ``w = ceil(h / 7) ..``, clip-and-round post-processing, and the
  legacy design rank (32 features less 3 exact dependencies, less 1 more while the training
  data span only 2023-2024, when trend = 365 * (year - 2023) + day_of_year - 1);
* plain-Python references in this file: SHA-256 of files and of the documented canonical
  series lines (``YYYY-MM-DD,repr(float(value))``), MAE / RMSE / MAPE with ``math.fsum``, and
  the pre-registered selection rule applied to the persisted forecasts;
* the pinned protocol fingerprints: a different value means the frozen protocol changed.

Tests that read the committed snapshot are marked ``integration``; every run directory is
written under pytest's temporary directories.
"""

from __future__ import annotations

import csv
import functools
import hashlib
import io
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pydantic
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import atlas
import atlas.runner as runner
from atlas.backtest import BacktestPlan, Period, PeriodRole, run_backtest
from atlas.cli import EXIT_FAILED, EXIT_OK, EXIT_USAGE, main
from atlas.errors import ConfigError
from atlas.experiment import (
    ExperimentConfig,
    code_fingerprint,
    csv_bytes,
    environment_info,
    git_info,
    json_bytes,
    load_config,
    parse_config,
    result_fingerprint,
    series_fingerprint,
    write_run,
)
from atlas.legacy_refit import legacy_refit_check
from atlas.loader import LoadedTable
from atlas.models import ModelFamily, SeasonalNaiveForecaster, legacy_linear_regression
from atlas.series import DemandSeries
from tests.conftest import REPO_ROOT
from tests.m2_helpers import series_from

CONFIG_DIR = REPO_ROOT / "configs" / "backtest"
V1 = CONFIG_DIR / "atlas-m2-rolling-origin-v1.json"
V1_DELAY1 = CONFIG_DIR / "atlas-m2-rolling-origin-v1-delay1.json"
SMOKE = CONFIG_DIR / "smoke.json"
PINNED_FINGERPRINTS = {
    V1.name: "78da64e8c2ce75d9318cf855137d015e9692dbd50517a70d00243d7f731b1753",
    V1_DELAY1.name: "26ef670d6da2bff677235a45dbb6ab04a6a30169aa9a372bfa936a98be56ceb4",
    SMOKE.name: "e13beac4647716858dcc35f51812c155e5008381b660bb2cfed1f0ab88c7fdb9",
}
DATA_DIR = REPO_ROOT / "01 Data"
RESULT_FILES = (
    "forecasts.csv",
    "fits.csv",
    "coefficients.csv",
    "observed_inputs.csv",
    "metrics.json",
)
BACKTEST_FILES = frozenset({*RESULT_FILES, "report.md", "config.json", "manifest.json"})
REGRESSIONS = frozenset({"legacy_linear_regression", "atlas_linear_regression"})
DIRECT_BASELINES = frozenset(
    {"naive", "seasonal_naive", "same_weekday_mean_4w", "same_weekday_median_5w"}
)
LEGACY_TABLES = ("forecast_test_results", "future_30_day_forecast", "model_metrics")
SMOKE_MODELS = (
    "naive",
    "seasonal_naive",
    "same_weekday_mean_4w",
    "same_weekday_median_5w",
    "seasonal_exponential_smoothing",
    "legacy_linear_regression",
    "atlas_linear_regression",
)
HEX64 = re.compile(r"[0-9a-f]{64}")
HOLDOUT_FIRST = date(2026, 4, 2)
HOLDOUT_LAST = date(2026, 6, 30)
LAST_OBSERVED = date(2026, 6, 30)


# --- Plain-Python references ----------------------------------------------------------------


def _csv_rows(name: str) -> list[dict[str, str]]:
    with (DATA_DIR / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@functools.cache
def daily_total() -> dict[date, int]:
    return {
        date.fromisoformat(row["request_date"]): int(row["request_count"])
        for row in _csv_rows("daily_total.csv")
    }


def y(day: date | str) -> int:
    return daily_total()[day if isinstance(day, date) else date.fromisoformat(day)]


@functools.cache
def published_holdout() -> dict[date, int]:
    return {
        date.fromisoformat(row["request_date"]): int(row["model_forecast"])
        for row in _csv_rows("forecast_test_results.csv")
    }


@functools.cache
def published_future() -> dict[date, int]:
    return {
        date.fromisoformat(row["request_date"]): int(row["forecast_requests"])
        for row in _csv_rows("future_30_day_forecast.csv")
    }


def ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def same_weekday_sources(origin: date, horizon: int, weeks: int) -> list[date]:
    """``o + h - 7w`` for ``w = ceil(h / 7) .. ceil(h / 7) + weeks - 1``."""
    first = ceil_div(horizon, 7)
    return [origin + timedelta(days=horizon - 7 * w) for w in range(first, first + weeks)]


def reference_metrics(pairs: Iterable[tuple[float, float]]) -> tuple[float, float, float]:
    """MAE, RMSE and MAPE (%) of ``(prediction, actual)`` pairs."""
    errors = [(float(p) - float(a), float(a)) for p, a in pairs]
    n = len(errors)
    mae = math.fsum(abs(e) for e, _ in errors) / n
    rmse = math.sqrt(math.fsum(e * e for e, _ in errors) / n)
    mape = 100.0 * math.fsum(abs(e) / a for e, a in errors) / n
    return mae, rmse, mape


def reference_series_fingerprint(start: date, values: Sequence[float | None]) -> str:
    """The documented canonical form: one ``YYYY-MM-DD,value`` line per day (empty = gap)."""
    lines = [
        f"{(start + timedelta(days=k)).isoformat()},{'' if v is None else repr(float(v))}\n"
        for k, v in enumerate(values)
    ]
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_records(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, float_precision="round_trip")


def round_half_even(value: float) -> float:
    return float(round(value))  # Python's round is half to even


# --- Configurations ---------------------------------------------------------------------------


def v1_payload() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(V1.read_text(encoding="utf-8"))
    return payload


def short_protocol() -> dict[str, Any]:
    """The v1 protocol with very few origins per period (same schema, rule and models)."""
    payload = v1_payload()
    payload["experiment_id"] = "atlas-m2-short-protocol-test"
    windows = {
        "development": ("2024-02-01", "2024-02-10"),
        "confirmation": ("2025-07-01", "2025-07-05"),
        "legacy_window": ("2026-06-20", "2026-06-29"),
    }
    for period in payload["periods"]:
        period["first_origin"], period["last_origin"] = windows[period["name"]]
    return payload


MINI_MODELS = ("naive", "seasonal_naive", "same_weekday_mean_4w")


def mini_protocol(*, with_selection: bool = True) -> dict[str, Any]:
    """Three cheap baselines, horizons 1..7, a 3-origin development and 2-origin confirmation."""
    payload: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": "atlas-m2-mini-protocol-test",
        "description": "Tiny protocol for CLI tests (not evidence about accuracy).",
        "snapshot_id": "legacy-v0",
        "target_table": "daily_total",
        "max_horizon": 7,
        "min_history_days": 365,
        "periods": [
            {
                "name": "development",
                "role": "selection",
                "first_origin": "2024-02-01",
                "last_origin": "2024-02-03",
            },
            {
                "name": "confirmation",
                "role": "confirmation",
                "first_origin": "2024-03-01",
                "last_origin": "2024-03-02",
            },
        ],
        "horizon_buckets": [
            {"label": "day_1", "first": 1, "last": 1},
            {"label": "days_2_7", "first": 2, "last": 7},
        ],
        "models": [
            {"type": "naive", "model_id": "naive"},
            {"type": "seasonal_naive", "model_id": "seasonal_naive", "season_days": 7},
            {"type": "same_weekday_mean", "model_id": "same_weekday_mean_4w", "weeks": 4},
        ],
        "reference_model": "seasonal_naive",
    }
    if with_selection:
        payload["selection"] = {
            "period": "development",
            "candidates": list(MINI_MODELS),
            "metric": "mae",
            "first_horizon": 1,
            "last_horizon": 7,
            "max_failure_rate": 0.0,
            "regression_margin_pct": 3.0,
            "alpha": 0.05,
        }
    return payload


def write_config(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def run_cli(*args: str, repo_root: Path = REPO_ROOT) -> tuple[int, str]:
    buffer = io.StringIO()
    code = main(["--repo-root", str(repo_root), *args], stdout=buffer)
    return code, buffer.getvalue()


def backtest_args(config: Path, output: Path, *extra: str) -> list[str]:
    return ["backtest", "run", "--config", str(config), "--output-dir", str(output), *extra]


# --- 1. Configuration: committed protocols and pinned fingerprints ----------------------------


@pytest.mark.parametrize("path", [V1, V1_DELAY1, SMOKE], ids=lambda p: p.name)
def test_committed_configs_load_and_match_their_pinned_fingerprints(path: Path) -> None:
    config = load_config(path)
    assert isinstance(config, ExperimentConfig)
    assert config.schema_version == 1
    assert config.snapshot_id == "legacy-v0"
    # A different value means the frozen protocol changed: report it, do not update the pin.
    assert config.fingerprint() == PINNED_FINGERPRINTS[path.name]
    assert parse_config(path.read_text(encoding="utf-8")).fingerprint() == config.fingerprint()
    assert parse_config(json.loads(path.read_text(encoding="utf-8"))) == config
    assert [spec.model_id for spec in (m.spec for m in config.build_models())] == [
        m.model_id for m in config.models
    ]


def _reorder(value: Any) -> Any:
    """Every mapping with its keys in reverse order (lists keep their order)."""
    if isinstance(value, dict):
        return {key: _reorder(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [_reorder(item) for item in value]
    return value


def test_fingerprint_ignores_key_order_and_whitespace_but_not_values() -> None:
    payload = v1_payload()
    reference = parse_config(json.dumps(payload)).fingerprint()
    shuffled = json.dumps(_reorder(payload), indent=7, separators=(" ,  ", " :\t"))
    assert shuffled != V1.read_text(encoding="utf-8")
    assert parse_config(shuffled).fingerprint() == reference
    assert parse_config(_reorder(payload)).fingerprint() == reference
    for path, change in (
        (("selection", "alpha"), 0.049),
        (("max_horizon",), 29),
        (("description",), payload["description"] + "."),
        (("sensitivity", "holiday_stratum"), False),
    ):
        changed = json.loads(json.dumps(payload))
        target = changed
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = change
        if path == ("max_horizon",):  # keep the buckets within the shorter horizon
            changed["horizon_buckets"][-1]["last"] = 29
            changed["selection"]["last_horizon"] = 29
        assert parse_config(json.dumps(changed)).fingerprint() != reference, path


def test_v1_plan_has_the_documented_origin_counts() -> None:
    config = load_config(V1)
    plan = config.plan()
    counts = {p.name: len(p.origins()) for p in plan.periods}
    span = {
        "development": (date(2024, 2, 1), date(2025, 5, 31)),
        "confirmation": (date(2025, 6, 30), date(2026, 5, 31)),
        "legacy_window": (date(2026, 3, 3), date(2026, 6, 29)),
    }
    expected = {name: (last - first).days + 1 for name, (first, last) in span.items()}
    assert expected == {"development": 486, "confirmation": 336, "legacy_window": 119}
    assert counts == expected
    # confirmation and legacy_window share 2026-03-03..2026-05-31 (90 days)
    shared = (date(2026, 5, 31) - date(2026, 3, 3)).days + 1
    assert len(plan.origins()) == sum(expected.values()) - shared == 851
    assert plan.origins()[0] == date(2024, 2, 1)
    assert plan.origins()[-1] == date(2026, 6, 29)
    assert plan.max_horizon == 30
    assert plan.min_history_days == 365
    assert plan.data_delay_days == 0
    assert plan.observed_input_diagnostic is True
    assert plan.training_window.days is None
    assert [p.role for p in plan.periods] == [
        PeriodRole.SELECTION,
        PeriodRole.CONFIRMATION,
        PeriodRole.DESCRIPTIVE,
    ]
    assert plan.periods[2].allow_incomplete_horizons is True
    # restricted plans
    assert [p.name for p in config.plan(["confirmation"]).periods] == ["confirmation"]
    with pytest.raises(ConfigError, match="unknown period"):
        config.plan(["no_such_period"])


def test_delay_config_and_default_periods() -> None:
    delay = load_config(V1_DELAY1)
    plan = delay.plan()
    assert plan.data_delay_days == 1
    assert plan.observed_input_diagnostic is False
    assert len(plan.origins()) == 486
    assert runner.default_periods(load_config(V1)) == ["development"]
    assert runner.default_periods(delay) == ["development"]
    assert runner.default_periods(load_config(SMOKE)) == ["early", "late"]  # no selection rule


@pytest.mark.integration
@pytest.mark.parametrize("path", [V1, V1_DELAY1, SMOKE], ids=lambda p: p.name)
def test_committed_protocols_fit_the_committed_snapshot(
    path: Path, legacy_tables: dict[str, LoadedTable]
) -> None:
    series = DemandSeries.from_frame(legacy_tables["daily_total"].frame, name="daily_total")
    load_config(path).plan().check_against(series)  # raises ConfigError on any problem


# --- 2. Configuration: strict rejections -------------------------------------------------------

Mutation = Callable[[dict[str, Any]], None]


def _set(*path: Any, value: Any) -> Mutation:
    def mutate(payload: dict[str, Any]) -> None:
        target: Any = payload
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def _append(*path: Any, value: Any) -> Mutation:
    def mutate(payload: dict[str, Any]) -> None:
        target: Any = payload
        for key in path:
            target = target[key]
        target.append(value)

    return mutate


REJECTIONS: list[tuple[str, Mutation, str]] = [
    ("max_horizon_bool", _set("max_horizon", value=True), "max_horizon"),
    ("max_horizon_float", _set("max_horizon", value=30.0), "max_horizon"),
    ("max_horizon_string", _set("max_horizon", value="30"), "max_horizon"),
    ("max_horizon_zero", _set("max_horizon", value=0), "max_horizon"),
    ("max_horizon_367", _set("max_horizon", value=367), "max_horizon"),
    ("stride_float", _set("periods", 0, "origin_stride_days", value=1.0), "origin_stride_days"),
    ("unknown_top_key", _set("unexpected", value=1), "unexpected"),
    ("unknown_period_key", _set("periods", 0, "weight", value=1), "weight"),
    ("unknown_model_key", _set("models", 0, "lag", value=2), "lag"),
    ("schema_version_2", _set("schema_version", value=2), "schema_version"),
    ("timestamp", _set("periods", 0, "first_origin", value="2024-02-01T00:00:00"), "YYYY-MM-DD"),
    ("spaced_timestamp", _set("periods", 0, "last_origin", value="2025-05-31 00:00"), "YYYY-MM"),
    ("loose_date", _set("periods", 0, "first_origin", value="2024-2-1"), "YYYY-MM-DD"),
    ("compact_date", _set("periods", 0, "first_origin", value="20240201"), "YYYY-MM-DD"),
    ("impossible_date", _set("periods", 0, "first_origin", value="2024-02-30"), "first_origin"),
    ("integer_date", _set("periods", 0, "first_origin", value=20240201), "first_origin"),
    ("reference_naive", _set("reference_model", value="naive"), "reference_model"),
    ("reference_unknown", _set("reference_model", value="nope"), "reference_model"),
    ("reference_season_14", _set("models", 1, "season_days", value=14), "reference_model"),
    ("selection_on_confirmation", _set("selection", "period", value="confirmation"), "role"),
    ("selection_unknown_period", _set("selection", "period", value="nope"), "role"),
    ("unknown_candidate", _append("selection", "candidates", value="no_such_model"), "no_such"),
    ("duplicate_candidate", _append("selection", "candidates", value="naive"), "candidates"),
    ("selection_beyond_horizon", _set("selection", "last_horizon", value=31), "horizons"),
    ("bucket_beyond_horizon", _set("horizon_buckets", 3, "last", value=31), "days_15_30"),
    ("bucket_reversed", _set("horizon_buckets", 2, "first", value=15), "days_8_14"),
    ("duplicate_bucket", _set("horizon_buckets", 1, "label", value="day_1"), "unique"),
    ("duplicate_model_id", _set("models", 2, "model_id", value="naive"), "unique"),
    ("window_unknown_period", _set("comparison_windows", 0, "period", value="nope"), "unknown"),
    (
        "window_reversed",
        _set("comparison_windows", 0, "last_target", value="2026-04-01"),
        "ends before",
    ),
    ("sliding_without_days", _set("training_window", value={"kind": "sliding"}), "days"),
    (
        "expanding_with_days",
        _set("training_window", value={"kind": "expanding", "days": 400}),
        "days",
    ),
    (
        "exclusion_reversed",
        _set("sensitivity", "origin_exclusions", 0, "last_origin", value="2024-12-01"),
        "ends before",
    ),
    ("unknown_model_type", _set("models", 0, "type", value="prophet"), "models"),
    ("bad_snapshot_id", _set("snapshot_id", value="../other"), "snapshot_id"),
    ("other_table", _set("target_table", value="daily_segments"), "target_table"),
    ("negative_delay", _set("data_delay_days", value=-1), "data_delay_days"),
]


def test_the_base_of_the_rejection_cases_is_valid() -> None:
    """Positive control: without a mutation the payload is accepted."""
    assert parse_config(json.dumps(v1_payload())).fingerprint() == PINNED_FINGERPRINTS[V1.name]
    sliding = v1_payload()
    sliding["training_window"] = {"kind": "sliding", "days": 400}
    assert parse_config(sliding).training_window.days == 400


@pytest.mark.parametrize(
    ("mutation", "match"),
    [pytest.param(m, match, id=name) for name, m, match in REJECTIONS],
)
def test_invalid_configurations_raise_config_error(mutation: Mutation, match: str) -> None:
    payload = v1_payload()
    mutation(payload)
    with pytest.raises(ConfigError, match=match):
        parse_config(json.dumps(payload))
    with pytest.raises(ConfigError, match=match):
        parse_config(payload)


@pytest.mark.parametrize(
    ("text", "match"),
    [
        pytest.param('{"schema_version": 1, "schema_version": 1}', "duplicate", id="top_dup"),
        pytest.param("{", "not valid JSON", id="truncated"),
        pytest.param("", "not valid JSON", id="empty"),
        pytest.param("[]", "object", id="array"),
        pytest.param("{'schema_version': 1}", "not valid JSON", id="single_quotes"),
    ],
)
def test_invalid_json_text_raises_config_error(text: str, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        parse_config(text)


def test_duplicate_keys_are_rejected_at_any_depth() -> None:
    text = json.dumps(v1_payload())
    nested = text.replace('"role": "selection"', '"role": "selection", "role": "descriptive"', 1)
    assert nested != text
    with pytest.raises(ConfigError, match=r"duplicate key\(s\).*role"):
        parse_config(nested)
    top = text.replace('"max_horizon": 30', '"max_horizon": 30, "max_horizon": 30', 1)
    with pytest.raises(ConfigError, match="max_horizon"):
        parse_config(top)


def test_load_config_reports_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing.json")
    broken = tmp_path / "broken.json"
    broken.write_text('{"schema_version": ', encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(broken)


@pytest.mark.parametrize(
    ("change", "match"),
    [
        pytest.param(
            lambda p: p["periods"][1].update(name="development"), "unique", id="dup_period_names"
        ),
        pytest.param(
            lambda p: p["periods"][2].update(role="selection"), "at most one", id="two_selections"
        ),
        pytest.param(
            lambda p: p["periods"][0].update(last_origin="2025-06-15"), "overlap", id="overlap"
        ),
    ],
)
def test_plan_level_rejections(change: Mutation, match: str) -> None:
    """Invalid schedules are refused when the plan is built (before any computation)."""
    payload = v1_payload()
    payload["selection"] = None  # the selection rule is not what is tested here
    change(payload)
    config = parse_config(payload)
    with pytest.raises(ConfigError, match=match):
        config.plan()


def test_v1_development_and_confirmation_targets_are_disjoint_by_construction() -> None:
    """dev.last_origin + H <= conf.first_origin (closed form), and one day less is refused."""
    assert date(2025, 5, 31) + timedelta(days=30) <= date(2025, 6, 30)
    payload = v1_payload()
    payload["selection"] = None
    payload["periods"][0]["last_origin"] = "2025-06-01"  # targets through 2025-07-01
    with pytest.raises(ConfigError, match="overlap"):
        parse_config(payload).plan()


# --- 3. Deterministic serialisation -------------------------------------------------------------


def test_json_bytes_is_strict_sorted_and_unwraps_numpy() -> None:
    payload = {
        "b": float("nan"),
        "a": {"z": float("inf"), "y": -float("inf"), "x": np.float64("nan")},
        "neg_zero": -0.0,
        "np_neg_zero": np.float64(-0.0),
        "int64": np.int64(7),
        "int32": np.int32(-3),
        "float64": np.float64(0.1),
        "float32": np.float32(0.5),
        "flag": np.bool_(True),
        "plain_flag": False,
        "day": date(2024, 2, 29),
        "stamp": pd.Timestamp("2024-03-01"),
        "seq": (1, np.int64(2), [np.float64(2.5)]),
        "third": 1 / 3,
    }
    raw = json_bytes(payload)
    text = raw.decode("utf-8")
    assert raw.endswith(b"\n")
    for token in ("NaN", "Infinity", "-0.0"):
        assert token not in text
    decoded = json.loads(text)
    assert decoded == {
        "a": {"x": None, "y": None, "z": None},
        "b": None,
        "day": "2024-02-29",
        "flag": True,
        "float32": 0.5,
        "float64": 0.1,
        "int32": -3,
        "int64": 7,
        "neg_zero": 0.0,
        "np_neg_zero": 0.0,
        "plain_flag": False,
        "seq": [1, 2, [2.5]],
        "stamp": "2024-03-01",
        "third": 1 / 3,
    }
    assert list(decoded) == sorted(decoded)
    assert list(decoded["a"]) == ["x", "y", "z"]
    assert type(decoded["int64"]) is int
    assert type(decoded["flag"]) is bool
    assert decoded["third"] == 1 / 3  # lossless, not rounded to a fixed precision
    assert math.copysign(1.0, decoded["neg_zero"]) == 1.0
    assert math.copysign(1.0, decoded["np_neg_zero"]) == 1.0
    assert json_bytes(dict(reversed(list(payload.items())))) == raw  # key order irrelevant


@settings(max_examples=20, deadline=None)
@given(
    st.dictionaries(
        st.text(min_size=1, max_size=6),
        st.floats(allow_nan=True, allow_infinity=True),
        min_size=1,
        max_size=8,
    )
)
def test_json_bytes_round_trips_every_finite_float(values: dict[str, float]) -> None:
    decoded = json.loads(json_bytes(values))
    assert list(decoded) == sorted(values)
    for key, value in values.items():
        if not math.isfinite(value):
            assert decoded[key] is None
        else:
            assert decoded[key] == value
            if value == 0:
                assert math.copysign(1.0, decoded[key]) == 1.0  # never "-0.0"


@settings(max_examples=20, deadline=None)
@given(
    st.lists(st.floats(allow_nan=False, allow_infinity=False, width=64), min_size=1, max_size=25)
)
def test_csv_bytes_round_trips_floats_losslessly(values: list[float]) -> None:
    frame = pd.DataFrame({"x": pd.Series(values, dtype="float64"), "k": range(len(values))})
    back = pd.read_csv(io.BytesIO(csv_bytes(frame)), float_precision="round_trip")
    assert back["x"].to_numpy(dtype="float64").tolist() == values
    assert not np.signbit(back["x"].to_numpy(dtype="float64")[np.asarray(values) == 0]).any()


def test_csv_bytes_formats_dates_signed_zero_gaps_and_line_ends() -> None:
    frame = pd.DataFrame(
        {
            "day": pd.to_datetime(["2024-02-29", "2024-03-01", "2024-03-02"]),
            "value": [-0.0, 0.1 + 0.2, float("nan")],
            "n": [1, 2, 3],
            "label": ["a", None, "c"],
        }
    )
    text = csv_bytes(frame).decode("utf-8")
    assert text == (
        "day,value,n,label\n2024-02-29,0,1,a\n2024-03-01,0.30000000000000004,2,\n2024-03-02,,3,c\n"
    )
    assert csv_bytes(frame) == csv_bytes(frame.copy())
    assert frame["value"].iloc[0] == 0.0
    assert np.signbit(frame["value"].iloc[0])  # the caller's frame is not modified


def test_result_fingerprint_covers_listed_files_only() -> None:
    outputs = {"forecasts.csv": "a" * 64, "metrics.json": "b" * 64, "report.md": "c" * 64}
    files = ["forecasts.csv", "metrics.json"]
    base = result_fingerprint(outputs, files)
    assert HEX64.fullmatch(base)
    assert result_fingerprint({**outputs, "report.md": "d" * 64}, files) == base
    assert result_fingerprint({**outputs, "metrics.json": "e" * 64}, files) != base
    assert result_fingerprint({**outputs, "forecasts.csv": "e" * 64}, files) != base


# --- 4. Atomic run directories -----------------------------------------------------------------


def _children(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


def test_write_run_writes_every_file_then_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    original = Path.write_bytes

    def recording(self: Path, data: bytes) -> int:
        order.append(self.name)
        return original(self, data)

    monkeypatch.setattr(Path, "write_bytes", recording)
    target = tmp_path / "nested" / "run"  # the parent does not exist yet
    files = {"forecasts.csv": b"a,b\n1,2\n", "metrics.json": b'{"x": 1}\n'}
    manifest = {"record_type": "historical_backtest", "value": float("nan")}
    write_run(target, files, manifest)
    assert _children(target) == ["forecasts.csv", "manifest.json", "metrics.json"]
    assert (target / "forecasts.csv").read_bytes() == files["forecasts.csv"]
    assert (target / "metrics.json").read_bytes() == files["metrics.json"]
    assert read_json(target / "manifest.json") == {
        "record_type": "historical_backtest",
        "value": None,
    }
    assert order[-1] == "manifest.json"  # the manifest is written last
    assert sorted(order[:-1]) == sorted(files)
    assert _children(target.parent) == ["run"]  # no staging directory left behind


def test_write_run_refuses_an_existing_directory(tmp_path: Path) -> None:
    target = tmp_path / "run"
    target.mkdir()
    (target / "keep.txt").write_text("previous run", encoding="utf-8")
    with pytest.raises(ConfigError, match="already exists"):
        write_run(target, {"forecasts.csv": b"new"}, {"record_type": "x"})
    assert _children(target) == ["keep.txt"]
    assert (target / "keep.txt").read_text(encoding="utf-8") == "previous run"
    assert _children(tmp_path) == ["run"]


def test_write_run_leaves_nothing_when_a_file_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.write_bytes

    def failing(self: Path, data: bytes) -> int:
        if self.name == "fits.csv":
            raise OSError("disk full")
        return original(self, data)

    monkeypatch.setattr(Path, "write_bytes", failing)
    target = tmp_path / "parent" / "run"
    with pytest.raises(OSError, match="disk full"):
        write_run(target, {"forecasts.csv": b"1", "fits.csv": b"2"}, {"record_type": "x"})
    assert not target.exists()
    assert _children(target.parent) == []


def test_write_run_leaves_nothing_when_the_manifest_cannot_be_serialised(tmp_path: Path) -> None:
    target = tmp_path / "run"
    with pytest.raises(TypeError):
        write_run(target, {"forecasts.csv": b"1"}, {"record_type": object()})
    assert _children(tmp_path) == []


def test_write_run_leaves_nothing_when_the_final_rename_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_rename(self: Path, target: Path) -> Path:
        raise OSError("rename failed")

    monkeypatch.setattr(Path, "rename", failing_rename)
    target = tmp_path / "run"
    with pytest.raises(OSError, match="rename failed"):
        write_run(target, {"forecasts.csv": b"1"}, {"record_type": "x"})
    assert _children(tmp_path) == []


# --- 5. Provenance -----------------------------------------------------------------------------


def test_git_info_without_a_git_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert git_info(REPO_ROOT) == {"available": False, "commit": None, "dirty": None}


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_info_outside_a_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    info = git_info(tmp_path)
    assert info["available"] is False
    assert info["commit"] is None
    assert info["dirty"] is None


def _git(repo: Path, *args: str) -> str:
    executable = shutil.which("git")
    assert executable is not None
    completed = subprocess.run(  # noqa: S603 - absolute executable, fixed arguments, no shell
        [
            executable,
            "-C",
            str(repo),
            "-c",
            "user.name=ATLAS test",
            "-c",
            "user.email=atlas-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return completed.stdout.strip()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_info_reports_commit_and_scoped_dirty_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "configs").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "configs" / "c.json").write_text("{}\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    head = _git(repo, "rev-parse", "HEAD")

    clean = git_info(repo)
    assert clean == {"available": True, "commit": head, "dirty": False, "dirty_paths": 0}

    (repo / "notes.txt").write_text("outside the tracked scope\n", encoding="utf-8")
    assert git_info(repo)["dirty"] is False  # untracked file outside src/configs/tests

    (repo / "src" / "new.py").write_text("y = 2\n", encoding="utf-8")  # untracked, in scope
    untracked = git_info(repo)
    assert untracked["dirty"] is True
    assert untracked["dirty_paths"] == 1

    (repo / "configs" / "c.json").write_text('{"changed": true}\n', encoding="utf-8")
    modified = git_info(repo)
    assert modified["dirty_paths"] == 2
    assert modified["commit"] == head


def _copy_code(destination: Path) -> Path:
    (destination / "src").mkdir(parents=True)
    shutil.copytree(
        REPO_ROOT / "src" / "atlas",
        destination / "src" / "atlas",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copy2(REPO_ROOT / "pyproject.toml", destination / "pyproject.toml")
    shutil.copy2(REPO_ROOT / "uv.lock", destination / "uv.lock")
    return destination


def test_code_fingerprint_tracks_every_source_file(tmp_path: Path) -> None:
    root = _copy_code(tmp_path / "copy")
    sources = sorted((root / "src" / "atlas").rglob("*.py"))
    base = code_fingerprint(root)
    assert HEX64.fullmatch(base["code_sha256"])
    assert base["files"] == len(sources) + 2  # every module plus pyproject.toml and uv.lock
    assert base["uv_lock_sha256"] == sha256_of(root / "uv.lock")
    assert code_fingerprint(root) == base  # deterministic

    target = root / "src" / "atlas" / "metrics.py"
    original = target.read_bytes()
    target.write_bytes(original + b"\n# edited\n")
    assert code_fingerprint(root)["code_sha256"] != base["code_sha256"]
    target.write_bytes(original)
    assert code_fingerprint(root) == base

    renamed = root / "src" / "atlas" / "metrics_renamed.py"
    target.rename(renamed)  # same bytes under another name
    assert code_fingerprint(root)["code_sha256"] != base["code_sha256"]
    renamed.rename(target)

    pyproject = root / "pyproject.toml"
    pyproject.write_bytes(pyproject.read_bytes() + b"\n")
    assert code_fingerprint(root)["code_sha256"] != base["code_sha256"]


def test_code_fingerprint_ignores_caches_and_non_python_files(tmp_path: Path) -> None:
    root = _copy_code(tmp_path / "copy")
    base = code_fingerprint(root)
    cache = root / "src" / "atlas" / "__pycache__"
    cache.mkdir()
    (cache / "stale.py").write_text("garbage = True\n", encoding="utf-8")
    (root / "src" / "atlas" / "notes.txt").write_text("not code\n", encoding="utf-8")
    assert code_fingerprint(root) == base


def test_environment_info_records_versions_and_thread_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "3")
    monkeypatch.delenv("MKL_NUM_THREADS", raising=False)
    info = environment_info()
    assert info["packages"] == {
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "pydantic": pydantic.VERSION,
    }
    assert info["thread_settings"] == {
        "OPENBLAS_NUM_THREADS": "3",
        "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"),
        "MKL_NUM_THREADS": None,
    }
    assert info["atlas"] == atlas.__version__
    assert info["python"] == "{}.{}.{}".format(*sys.version_info[:3])
    json.loads(json_bytes(info))  # serialisable as strict JSON


def test_series_fingerprint_follows_the_documented_canonical_form() -> None:
    start = date(2024, 2, 27)
    values: list[float | None] = [5, 6, None, 7]  # 2024-02-29 is missing
    series = series_from(start, values)
    assert series_fingerprint(series) == reference_series_fingerprint(start, values)
    assert series_fingerprint(series_from(start, values)) == series_fingerprint(series)


@settings(max_examples=20, deadline=None)
@given(
    st.lists(st.integers(min_value=0, max_value=50_000), min_size=2, max_size=20),
    st.data(),
)
def test_series_fingerprint_changes_with_any_single_value(
    values: list[int], data: st.DataObject
) -> None:
    start = date(2023, 12, 30)
    base = series_fingerprint(series_from(start, values))
    position = data.draw(st.integers(min_value=0, max_value=len(values) - 1))
    changed = list(values)
    changed[position] += data.draw(st.integers(min_value=1, max_value=1000))
    assert series_fingerprint(series_from(start, changed)) != base
    assert series_fingerprint(series_from(start + timedelta(days=1), values)) != base
    gap: list[int | None] = list(values)
    gap[position] = None
    if 0 < position < len(values) - 1:  # a series cannot start or end with a gap
        assert series_fingerprint(series_from(start, gap)) != base


# --- 6. Sealed selections (no data needed) -------------------------------------------------------


def _seal(config: ExperimentConfig, data: str, /, **changes: Any) -> dict[str, Any]:
    record = {
        "record_type": "sealed_selection",
        "config_fingerprint": config.fingerprint(),
        "data_fingerprint": data,
        "selection": {"selected_model": "seasonal_naive", "ranking": []},
    }
    record.update(changes)
    return record


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        pytest.param({"record_type": "recommendation"}, "not a sealed selection", id="type"),
        pytest.param({"config_fingerprint": "0" * 64}, "different configuration", id="config"),
        pytest.param({"data_fingerprint": "1" * 64}, "different data", id="data"),
        pytest.param({"selection": None}, "no selection", id="missing"),
        pytest.param({"selection": {"selected_model": ""}}, "no selection", id="empty"),
    ],
)
def test_load_sealed_selection_refuses_foreign_records(
    tmp_path: Path, changes: dict[str, Any], match: str
) -> None:
    config = load_config(V1)
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(_seal(config, "f" * 64)), encoding="utf-8")
    assert runner.load_sealed_selection(path, config, "f" * 64)["selection"] == {
        "selected_model": "seasonal_naive",
        "ranking": [],
    }
    path.write_text(json.dumps(_seal(config, "f" * 64, **changes)), encoding="utf-8")
    with pytest.raises(ConfigError, match=match):
        runner.load_sealed_selection(path, config, "f" * 64)


def test_load_sealed_selection_refuses_unreadable_files(tmp_path: Path) -> None:
    config = load_config(V1)
    with pytest.raises(ConfigError, match="cannot read"):
        runner.load_sealed_selection(tmp_path / "missing.json", config, "f" * 64)
    listing = tmp_path / "list.json"
    listing.write_text("[]", encoding="utf-8")
    with pytest.raises(ConfigError, match="not a JSON object"):
        runner.load_sealed_selection(listing, config, "f" * 64)


# --- 7. CLI gates that stop before any computation -----------------------------------------------


def test_confirmation_period_needs_a_sealed_selection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_config(tmp_path / "short.json", short_protocol())
    output = tmp_path / "run"
    for periods in (["confirmation"], ["development", "confirmation"], ["legacy_window"]):
        extra = [arg for name in periods for arg in ("--period", name)]
        code, _ = run_cli(*backtest_args(config, output, *extra))
        assert code == EXIT_USAGE, periods
        assert "--sealed-selection" in capsys.readouterr().err
        assert not output.exists()


@pytest.mark.parametrize(
    "state",
    [
        pytest.param({"available": True, "commit": "a" * 40, "dirty": True}, id="dirty"),
        pytest.param({"available": False, "commit": None, "dirty": None}, id="no_git"),
    ],
)
def test_require_clean_refuses_a_dirty_or_unknown_tree(
    state: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(runner, "git_info", lambda root: dict(state))
    output = tmp_path / "run"
    code, _ = run_cli(*backtest_args(SMOKE, output, "--require-clean"))
    assert code == EXIT_USAGE
    assert "uncommitted" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize(
    "command",
    [
        pytest.param(["backtest", "run"], id="backtest"),
        pytest.param(["forecast"], id="forecast"),
    ],
)
def test_configuration_for_another_snapshot_is_refused(
    command: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "run"
    code, _ = run_cli(
        "--snapshot", "other", *command, "--config", str(SMOKE), "--output-dir", str(output)
    )
    assert code == EXIT_USAGE
    assert "legacy-v0" in capsys.readouterr().err
    assert not output.exists()


def test_unknown_period_and_missing_config_are_usage_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "run"
    code, _ = run_cli(*backtest_args(SMOKE, output, "--period", "no_such_period"))
    assert code == EXIT_USAGE
    assert "unknown period" in capsys.readouterr().err
    code, _ = run_cli(*backtest_args(tmp_path / "missing.json", output))
    assert code == EXIT_USAGE
    assert "cannot read" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize(
    ("command", "stage"),
    [
        pytest.param(["backtest", "run", "--config", str(SMOKE)], "backtest", id="backtest"),
        pytest.param(["forecast", "--config", str(SMOKE)], "projection", id="forecast"),
        pytest.param(["backtest", "legacy"], "legacy_refit", id="legacy"),
    ],
)
def test_m2_commands_are_skipped_when_snapshot_integrity_fails(
    command: list[str], stage: str, repo_copy: Path, tmp_path: Path
) -> None:
    target = repo_copy / "01 Data" / "daily_total.csv"
    target.write_bytes(target.read_bytes().replace(b"6209", b"6210", 1))
    output = tmp_path / "out"
    code, text = run_cli(*command, "--output-dir", str(output), repo_root=repo_copy)
    assert code == EXIT_FAILED
    assert "[FAIL] integrity" in text
    assert f"[SKIP] {stage}: snapshot integrity check failed" in text
    assert not output.exists()


# --- 8. Smoke backtest end to end (committed snapshot) --------------------------------------------


@dataclass(frozen=True)
class CliRun:
    code: int
    text: str
    directory: Path


@pytest.fixture(scope="module")
def smoke_runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[CliRun, CliRun]:
    base = tmp_path_factory.mktemp("smoke")
    runs = []
    for name in ("first", "second"):
        directory = base / name
        code, text = run_cli(*backtest_args(SMOKE, directory))
        runs.append(CliRun(code, text, directory))
    return runs[0], runs[1]


SMOKE_ORIGINS = {
    "early": [date(2024, 2, 1), date(2024, 2, 4), date(2024, 2, 7)],
    "late": [date(2024, 3, 15), date(2024, 3, 18), date(2024, 3, 21)],
}


@pytest.mark.integration
def test_smoke_run_writes_a_labelled_and_fingerprinted_run_directory(
    smoke_runs: tuple[CliRun, CliRun],
) -> None:
    run = smoke_runs[0]
    assert run.code == EXIT_OK, run.text
    assert "[PASS] backtest" in run.text
    assert set(_children(run.directory)) == BACKTEST_FILES  # no selection or recommendation
    manifest = read_json(run.directory / "manifest.json")
    assert manifest["record_type"] == "historical_backtest"
    assert "HISTORICAL BACKTEST" in manifest["label"]
    assert manifest["complete"] is True
    assert manifest["periods_run"] == ["early", "late"]
    assert manifest["sealed_selection"] is None
    assert manifest["config_fingerprint"] == PINNED_FINGERPRINTS[SMOKE.name]
    assert parse_config(manifest["config"]).fingerprint() == PINNED_FINGERPRINTS[SMOKE.name]
    config_text = (run.directory / "config.json").read_text(encoding="utf-8")
    assert parse_config(config_text).fingerprint() == PINNED_FINGERPRINTS[SMOKE.name]
    # every output hash describes the bytes on disk
    expected_outputs = BACKTEST_FILES - {"manifest.json"}
    assert set(manifest["outputs"]) == expected_outputs
    for name in expected_outputs:
        assert manifest["outputs"][name] == sha256_of(run.directory / name), name
    assert manifest["result_files"] == list(RESULT_FILES)
    joined = "".join(f"{name}:{sha256_of(run.directory / name)}\n" for name in RESULT_FILES)
    fingerprint = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    assert manifest["result_fingerprint"] == fingerprint
    assert f"result fingerprint {fingerprint}" in run.text
    # provenance
    data = manifest["data"]
    assert data["snapshot_id"] == "legacy-v0"
    assert data["table"] == "daily_total"
    assert data["table_sha256"] == sha256_of(DATA_DIR / "daily_total.csv")
    assert data["manifest_sha256"] == sha256_of(
        REPO_ROOT / "snapshots" / "legacy-v0" / "manifest.json"
    )
    days = sorted(daily_total())
    assert (data["first_date"], data["last_date"]) == ("2023-01-01", "2026-06-30")
    assert data["n_days"] == len(days) == (days[-1] - days[0]).days + 1 == 1277
    assert data["n_missing_days"] == 0
    assert data["series_fingerprint"] == reference_series_fingerprint(
        days[0], [daily_total()[d] for d in days]
    )
    assert HEX64.fullmatch(data["data_fingerprint"])
    sources = [
        p for p in (REPO_ROOT / "src" / "atlas").rglob("*.py") if "__pycache__" not in p.parts
    ]
    assert manifest["code"]["files"] == len(sources) + 2
    assert HEX64.fullmatch(manifest["code"]["code_sha256"])
    assert isinstance(manifest["git"]["available"], bool)
    assert set(manifest["environment"]["packages"]) == {"numpy", "pandas", "pydantic"}
    assert set(manifest["environment"]["thread_settings"]) == {
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
    }
    assert [m["model_id"] for m in manifest["models"]] == list(SMOKE_MODELS)
    for model in manifest["models"]:
        assert HEX64.fullmatch(model["spec_fingerprint"])
    # the metrics file and the report say the same
    metrics = read_json(run.directory / "metrics.json")
    assert metrics["record_type"] == "historical_backtest"
    assert metrics["config_fingerprint"] == PINNED_FINGERPRINTS[SMOKE.name]
    assert metrics["data_fingerprint"] == data["data_fingerprint"]
    assert metrics["selection"] is None
    assert metrics["recommendation"] is None
    report = (run.directory / "report.md").read_text(encoding="utf-8")
    assert "HISTORICAL BACKTEST" in report
    assert PINNED_FINGERPRINTS[SMOKE.name] in report


@pytest.mark.integration
def test_smoke_forecasts_match_closed_forms_and_actuals(
    smoke_runs: tuple[CliRun, CliRun],
) -> None:
    run = smoke_runs[0]
    frame = read_records(run.directory / "forecasts.csv")
    n_origins = sum(len(v) for v in SMOKE_ORIGINS.values())
    assert len(frame) == n_origins * len(SMOKE_MODELS) * 30 == 1260
    assert set(frame["record_type"]) == {"historical_backtest"}
    assert not frame.duplicated(["period", "model_id", "origin", "horizon"]).any()
    assert set(frame["status"]) == {"ok"}
    for period, origins in SMOKE_ORIGINS.items():
        cell = frame[frame["period"] == period]
        assert sorted(set(cell["origin"])) == [o.isoformat() for o in origins]
    for row in frame.itertuples(index=False):
        origin = date.fromisoformat(row.origin)
        horizon = int(row.horizon)
        target = origin + timedelta(days=horizon)
        assert row.target_date == target.isoformat()
        assert row.data_cutoff == row.origin
        assert row.actual == y(target)
        prediction = float(row.prediction)
        assert math.isfinite(prediction)
        assert prediction >= 0
        if row.model_id == "naive":
            assert prediction == y(origin)
        elif row.model_id == "seasonal_naive":
            assert prediction == y(origin + timedelta(days=horizon - 7 * ceil_div(horizon, 7)))
        elif row.model_id == "same_weekday_mean_4w":
            sources = same_weekday_sources(origin, horizon, 4)
            assert prediction == sum(y(d) for d in sources) / 4
        elif row.model_id == "same_weekday_median_5w":
            sources = same_weekday_sources(origin, horizon, 5)
            assert prediction == statistics.median(y(d) for d in sources)
        elif row.model_id == "legacy_linear_regression":
            # clipped at 0 and rounded half to even before feedback
            assert prediction == round_half_even(max(float(row.prediction_raw), 0.0))
        else:  # the modernised regression and exponential smoothing clip only
            assert prediction == max(float(row.prediction_raw), 0.0)
        if row.model_id in DIRECT_BASELINES:
            assert float(row.prediction_raw) == prediction


@pytest.mark.integration
def test_smoke_fits_coefficients_and_observed_inputs(smoke_runs: tuple[CliRun, CliRun]) -> None:
    run = smoke_runs[0]
    fits = read_records(run.directory / "fits.csv")
    assert len(fits) == 6 * len(SMOKE_MODELS)
    assert not fits.duplicated(["period", "model_id", "origin"]).any()
    legacy = fits[fits["model_id"] == "legacy_linear_regression"]
    assert legacy["n_features"].tolist() == [32] * 6
    assert legacy["rank"].tolist() == [28] * 6  # 2024 origins: 32 - 3 - 1 (leap-day identity)
    for row in legacy.itertuples(index=False):
        first = date(2023, 1, 1) + timedelta(days=28)
        assert row.train_first == first.isoformat()
        assert row.train_last == row.origin
        assert row.n_train_rows == (date.fromisoformat(row.origin) - first).days + 1
    coefficients = read_records(run.directory / "coefficients.csv")
    assert "legacy_linear_regression" not in set(coefficients["model_id"])  # not identifiable
    assert set(coefficients["model_id"]) == {"atlas_linear_regression"}
    atlas_fits = fits[fits["model_id"] == "atlas_linear_regression"]
    for row in atlas_fits.itertuples(index=False):
        terms = coefficients.loc[coefficients["origin"] == row.origin, "term"].tolist()
        assert terms[0] == "intercept"
        assert len(terms) == int(row.n_features) + 1
    # observed-input diagnostic: separate table, regressions only, h = 1 equals the forecast
    observed = read_records(run.directory / "observed_inputs.csv")
    assert set(observed["record_type"]) == {"diagnostic_observed_inputs"}
    assert set(observed["model_id"]) == set(REGRESSIONS)
    forecasts = read_records(run.directory / "forecasts.csv")
    first_steps = forecasts[forecasts["model_id"].isin(REGRESSIONS) & (forecasts["horizon"] == 1)]
    merged = first_steps.merge(
        observed[observed["horizon"] == 1], on=["period", "model_id", "origin", "target_date"]
    )
    assert len(merged) == 2 * 6
    for row in merged.itertuples(index=False):
        recursive = max(float(row.prediction_raw), 0.0)
        assert row.prediction_observed_inputs == pytest.approx(recursive, rel=1e-9)
    for row in observed.itertuples(index=False):
        assert row.actual == y(row.target_date)


@pytest.mark.integration
def test_smoke_metrics_and_status_counts_agree_with_the_forecasts(
    smoke_runs: tuple[CliRun, CliRun],
) -> None:
    run = smoke_runs[0]
    forecasts = read_records(run.directory / "forecasts.csv")
    metrics = read_json(run.directory / "metrics.json")
    manifest = read_json(run.directory / "manifest.json")
    counts: dict[str, dict[str, dict[str, int]]] = {}
    for (period, model_id, status), group in forecasts.groupby(["period", "model_id", "status"]):
        counts.setdefault(str(period), {}).setdefault(str(model_id), {})[str(status)] = len(group)
    assert manifest["status_counts"] == counts
    assert metrics["status_counts"] == counts
    buckets = [row for row in metrics["summary"] if row["scope"] == "bucket"]
    labels = {row["label"] for row in buckets}
    assert {"day_1", "days_2_7", "days_8_14", "days_15_30"} <= labels
    assert len(buckets) == len(labels) * 2 * len(SMOKE_MODELS)
    for row in buckets:
        cell = forecasts[
            (forecasts["period"] == row["period"])
            & (forecasts["model_id"] == row["model_id"])
            & forecasts["horizon"].between(row["first_horizon"], row["last_horizon"])
        ]
        mae, rmse, _ = reference_metrics(zip(cell["prediction"], cell["actual"], strict=True))
        assert (
            row["n_predictions"]
            == len(cell)
            == 3 * (row["last_horizon"] - row["first_horizon"] + 1)
        )
        if row["model_id"] in {"naive", "seasonal_naive"}:  # integer errors: exact arithmetic
            assert row["mae"] == mae
        else:
            assert row["mae"] == pytest.approx(mae, rel=1e-12)
        assert row["rmse"] == pytest.approx(rmse, rel=1e-12)


@pytest.mark.integration
def test_a_second_smoke_run_writes_identical_results(smoke_runs: tuple[CliRun, CliRun]) -> None:
    first, second = smoke_runs
    assert second.code == EXIT_OK, second.text
    assert _children(first.directory) == _children(second.directory)
    for name in BACKTEST_FILES - {"manifest.json"}:
        assert (first.directory / name).read_bytes() == (second.directory / name).read_bytes()
    manifests = [read_json(run.directory / "manifest.json") for run in smoke_runs]
    for manifest in manifests:
        assert set(manifest.pop("informational_not_fingerprinted")) == {
            "created_at_utc",
            "runtime_seconds",
        }
    assert manifests[0] == manifests[1]


@pytest.mark.integration
def test_verify_passes_on_a_recorded_smoke_run(smoke_runs: tuple[CliRun, CliRun]) -> None:
    run = smoke_runs[0]
    code, text = run_cli("backtest", "verify", str(run.directory))
    assert code == EXIT_OK, text
    assert "[PASS] verification" in text
    assert "results identical" in text
    for name in RESULT_FILES:
        assert f"{name} identical" in text
    fingerprint = read_json(run.directory / "manifest.json")["result_fingerprint"]
    assert f"result fingerprint {fingerprint}" in text


# --- 9. Verification of tampered runs (tiny protocol) ---------------------------------------------


@pytest.fixture(scope="module")
def tiny_run(tmp_path_factory: pytest.TempPathFactory) -> CliRun:
    base = tmp_path_factory.mktemp("tiny")
    config = write_config(base / "tiny.json", mini_protocol(with_selection=False))
    directory = base / "run"
    code, text = run_cli("--format", "json", *backtest_args(config, directory))
    return CliRun(code, text, directory)


def _tampered_copy(run: CliRun, tmp_path: Path) -> Path:
    copy = tmp_path / "copy"
    shutil.copytree(run.directory, copy)
    return copy


@pytest.mark.integration
def test_tiny_run_json_output_parses(tiny_run: CliRun) -> None:
    assert tiny_run.code == EXIT_OK, tiny_run.text
    payload = json.loads(tiny_run.text)
    assert payload["ok"] is True
    assert payload["snapshot"] == "legacy-v0"
    assert [r["name"] for r in payload["reports"]] == ["integrity", "validation", "backtest"]
    facts = payload["reports"][-1]["facts"]
    assert facts["record_type"] == "historical_backtest"
    assert facts["periods"] == ["development", "confirmation"]  # no selection rule: all periods
    assert facts["origins"] == 5
    assert facts["records"] == 5 * len(MINI_MODELS) * 7
    assert facts["selection"] is None
    manifest = read_json(tiny_run.directory / "manifest.json")
    assert facts["result_fingerprint"] == manifest["result_fingerprint"]


@pytest.mark.integration
@pytest.mark.parametrize("name", ["forecasts.csv", "metrics.json"])
def test_verify_fails_when_a_recorded_hash_was_edited(
    name: str, tiny_run: CliRun, tmp_path: Path
) -> None:
    copy = _tampered_copy(tiny_run, tmp_path)
    manifest = read_json(copy / "manifest.json")
    manifest["outputs"][name] = "0" * 64
    (copy / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    code, text = run_cli("backtest", "verify", str(copy))
    assert code == EXIT_FAILED
    assert "[FAIL] verification" in text
    assert "results DIFFERENT" in text
    assert f"{name} different" in text
    assert [f"{other} identical" in text for other in RESULT_FILES if other != name] == [True] * 4


@pytest.mark.integration
def test_verify_fails_when_a_result_file_on_disk_was_altered(
    tiny_run: CliRun, tmp_path: Path
) -> None:
    copy = _tampered_copy(tiny_run, tmp_path)
    forecasts = read_records(copy / "forecasts.csv")
    forecasts.loc[0, "prediction"] = forecasts.loc[0, "prediction"] + 1.0
    (copy / "forecasts.csv").write_bytes(csv_bytes(forecasts))
    code, text = run_cli("backtest", "verify", str(copy))
    assert "max_abs_prediction_difference': 1.0" in text  # the diagnostic sees the change
    assert code == EXIT_FAILED  # ... but verification still reports "results identical"


@pytest.mark.integration
def test_verify_fails_when_the_recorded_result_fingerprint_differs(
    tiny_run: CliRun, tmp_path: Path
) -> None:
    copy = _tampered_copy(tiny_run, tmp_path)
    manifest = read_json(copy / "manifest.json")
    manifest["result_fingerprint"] = "0" * 64
    (copy / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    code, _ = run_cli("backtest", "verify", str(copy))
    assert code == EXIT_FAILED


@pytest.mark.integration
def test_verify_refuses_inconsistent_or_foreign_manifests(
    tiny_run: CliRun, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    copy = _tampered_copy(tiny_run, tmp_path)
    manifest = read_json(copy / "manifest.json")
    edited = json.loads(json.dumps(manifest))
    edited["config"]["max_horizon"] = 6
    edited["config"]["horizon_buckets"][-1]["last"] = 6
    (copy / "manifest.json").write_text(json.dumps(edited), encoding="utf-8")
    code, _ = run_cli("backtest", "verify", str(copy))
    assert code == EXIT_USAGE
    assert "recorded fingerprint" in capsys.readouterr().err
    foreign = dict(manifest, record_type="future_projection")
    (copy / "manifest.json").write_text(json.dumps(foreign), encoding="utf-8")
    code, _ = run_cli("backtest", "verify", str(copy))
    assert code == EXIT_USAGE
    assert "not a historical backtest" in capsys.readouterr().err
    code, _ = run_cli("backtest", "verify", str(tmp_path / "nowhere"))
    assert code == EXIT_USAGE


@pytest.mark.integration
def test_require_clean_runs_on_a_clean_tree_and_records_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean = {"available": True, "commit": "c" * 40, "dirty": False, "dirty_paths": 0}
    monkeypatch.setattr(runner, "git_info", lambda root: dict(clean))
    config = write_config(tmp_path / "tiny.json", mini_protocol(with_selection=False))
    output = tmp_path / "run"
    code, text = run_cli(*backtest_args(config, output, "--require-clean"))
    assert code == EXIT_OK, text
    assert read_json(output / "manifest.json")["git"] == clean


# --- 10. Pre-registration gate with a short copy of the v1 protocol -------------------------------


@dataclass(frozen=True)
class ShortRuns:
    config: Path
    development: CliRun
    confirmation: CliRun


@pytest.fixture(scope="module")
def short_runs(tmp_path_factory: pytest.TempPathFactory) -> ShortRuns:
    base = tmp_path_factory.mktemp("short")
    config = write_config(base / "short.json", short_protocol())
    development = base / "development"
    dev_result = run_cli(*backtest_args(config, development))
    confirmation = base / "confirmation"
    conf_result = run_cli(
        *backtest_args(
            config,
            confirmation,
            "--period",
            "confirmation",
            "--sealed-selection",
            str(development / "selection.json"),
        )
    )
    return ShortRuns(
        config,
        CliRun(*dev_result, development),
        CliRun(*conf_result, confirmation),
    )


def reference_ranking(
    records: pd.DataFrame, period: str, candidates: Sequence[str], first: int, last: int
) -> tuple[list[tuple[str, float]], int]:
    """The pre-registered rule's ranking: MAE on the pairs every eligible candidate got ok."""
    scope = records[(records["period"] == period) & records["horizon"].between(first, last)]
    eligible = [
        m
        for m in candidates
        if (scope["model_id"] == m).any()
        and not (scope.loc[scope["model_id"] == m, "status"] == "failed").any()
    ]
    by_model: dict[str, dict[tuple[str, int], tuple[float, float]]] = {}
    common: set[tuple[str, int]] | None = None
    for model_id in eligible:
        cell = scope[(scope["model_id"] == model_id) & (scope["status"] == "ok")]
        by_model[model_id] = {
            (o, int(h)): (float(p), float(a))
            for o, h, p, a in zip(
                cell["origin"], cell["horizon"], cell["prediction"], cell["actual"], strict=True
            )
        }
        keys = set(by_model[model_id])
        common = keys if common is None else common & keys
    assert common
    order = {m: i for i, m in enumerate(candidates)}
    maes = {
        m: math.fsum(abs(by_model[m][k][0] - by_model[m][k][1]) for k in common) / len(common)
        for m in eligible
    }
    return sorted(maes.items(), key=lambda item: (item[1], order[item[0]])), len(common)


@pytest.mark.integration
def test_default_run_evaluates_only_the_development_period(short_runs: ShortRuns) -> None:
    run = short_runs.development
    assert run.code == EXIT_OK, run.text
    assert set(_children(run.directory)) == BACKTEST_FILES | {"selection.json"}
    manifest = read_json(run.directory / "manifest.json")
    assert manifest["periods_run"] == ["development"]
    forecasts = read_records(run.directory / "forecasts.csv")
    assert set(forecasts["period"]) == {"development"}
    origins = [date(2024, 2, 1) + timedelta(days=k) for k in range(10)]
    assert sorted(set(forecasts["origin"])) == [o.isoformat() for o in origins]
    assert len(forecasts) == 10 * len(SMOKE_MODELS) * 30
    assert forecasts["target_date"].max() == (origins[-1] + timedelta(days=30)).isoformat()
    config = load_config(short_runs.config)
    seal = read_json(run.directory / "selection.json")
    assert seal["record_type"] == "sealed_selection"
    assert seal["config_fingerprint"] == config.fingerprint() == manifest["config_fingerprint"]
    assert seal["data_fingerprint"] == manifest["data"]["data_fingerprint"]
    assert seal["result_fingerprint"] == manifest["result_fingerprint"]
    assert seal["run_id"] == manifest["run_id"]
    assert read_json(run.directory / "metrics.json")["recommendation"] is None


@pytest.mark.integration
def test_development_selection_follows_the_preregistered_rule(short_runs: ShortRuns) -> None:
    run = short_runs.development
    selection = read_json(run.directory / "selection.json")["selection"]
    rule = v1_payload()["selection"]
    forecasts = read_records(run.directory / "forecasts.csv")
    ranking, n_pairs = reference_ranking(
        forecasts, "development", rule["candidates"], rule["first_horizon"], rule["last_horizon"]
    )
    assert "legacy_linear_regression" not in rule["candidates"]  # evaluated, never recommended
    assert [r["model_id"] for r in selection["ranking"]] == [m for m, _ in ranking]
    for row, (_, mae) in zip(selection["ranking"], ranking, strict=True):
        assert row["mae"] == pytest.approx(mae, rel=1e-12)
    assert selection["n_common_pairs"] == n_pairs == 10 * 30
    assert selection["n_common_origins"] == 10
    best_baseline = next(m for m, _ in ranking if m not in REGRESSIONS)
    best_regression = next(m for m, _ in ranking if m in REGRESSIONS)
    assert selection["best_baseline"] == best_baseline
    assert selection["best_regression"] == best_regression
    # 10 origins are far fewer than the 50 the DM test needs, so the regression cannot be
    # shown to be significantly better and the rule must fall back to the best baseline.
    assert selection["significant"] is False
    assert selection["selected_model"] == best_baseline
    assert selection["selected_family"] == ModelFamily.BASELINE.value


@pytest.mark.integration
def test_confirmation_with_the_sealed_selection_writes_a_recommendation(
    short_runs: ShortRuns,
) -> None:
    run = short_runs.confirmation
    assert run.code == EXIT_OK, run.text
    assert set(_children(run.directory)) == BACKTEST_FILES | {"recommendation.json"}
    sealed = read_json(short_runs.development.directory / "selection.json")
    manifest = read_json(run.directory / "manifest.json")
    assert manifest["periods_run"] == ["confirmation"]
    assert manifest["sealed_selection"] == sealed
    forecasts = read_records(run.directory / "forecasts.csv")
    assert set(forecasts["period"]) == {"confirmation"}
    assert sorted(set(forecasts["origin"])) == [f"2025-07-0{k}" for k in range(1, 6)]
    metrics = read_json(run.directory / "metrics.json")
    assert metrics["selection"] == sealed["selection"]  # used unchanged
    recommendation = read_json(run.directory / "recommendation.json")
    assert recommendation["record_type"] == "recommendation"
    assert recommendation["config_fingerprint"] == sealed["config_fingerprint"]
    assert recommendation["data_fingerprint"] == sealed["data_fingerprint"]
    assert recommendation["result_fingerprint"] == manifest["result_fingerprint"]
    # A selected baseline stays recommended (confirm-or-fallback only applies to regressions).
    assert (
        recommendation["recommendation"]["selected_model"] == sealed["selection"]["selected_model"]
    )
    assert (
        recommendation["recommendation"]["recommended_model"]
        == sealed["selection"]["selected_model"]
    )
    assert recommendation["recommendation"]["period"] == "confirmation"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        pytest.param("config_fingerprint", "0" * 64, "different configuration", id="config"),
        pytest.param("data_fingerprint", "0" * 64, "different data", id="data"),
        pytest.param("record_type", "recommendation", "not a sealed selection", id="type"),
        pytest.param("selection", None, "no selection", id="selection"),
    ],
)
def test_a_tampered_sealed_selection_is_refused(
    key: str,
    value: Any,
    message: str,
    short_runs: ShortRuns,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seal = read_json(short_runs.development.directory / "selection.json")
    seal[key] = value
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(seal), encoding="utf-8")
    output = tmp_path / "run"
    args = ["--period", "confirmation", "--sealed-selection", str(path)]
    code, _ = run_cli(*backtest_args(short_runs.config, output, *args))
    assert code == EXIT_USAGE
    assert message in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.integration
def test_forecast_with_a_recommendation_projects_only_the_recommended_model(
    short_runs: ShortRuns, tmp_path: Path
) -> None:
    recommendation = short_runs.confirmation.directory / "recommendation.json"
    recommended = read_json(recommendation)["recommendation"]["recommended_model"]
    output = tmp_path / "projection"
    code, text = run_cli(
        "forecast",
        "--config",
        str(short_runs.config),
        "--recommendation",
        str(recommendation),
        "--output-dir",
        str(output),
    )
    assert code == EXIT_OK, text
    projections = read_records(output / "projections.csv")
    assert set(projections["model_id"]) == {recommended}
    assert projections["horizon"].tolist() == list(range(1, 31))
    manifest = read_json(output / "manifest.json")
    assert manifest["record_type"] == "future_projection"
    assert manifest["recommended_model"] == recommended
    assert [m["model_id"] for m in manifest["models"]] == [recommended]
    assert f"recommended by the backtest: `{recommended}`" in (output / "report.md").read_text(
        encoding="utf-8"
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("config", "change", "message"),
    [
        pytest.param(SMOKE, {}, "different configuration", id="other_config"),
        pytest.param(None, {"record_type": "sealed_selection"}, "not a recommendation", id="type"),
        pytest.param(None, {"data_fingerprint": "0" * 64}, "different data", id="data"),
    ],
)
def test_forecast_refuses_a_foreign_recommendation(
    config: Path | None,
    change: dict[str, Any],
    message: str,
    short_runs: ShortRuns,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = read_json(short_runs.confirmation.directory / "recommendation.json")
    record.update(change)
    path = tmp_path / "recommendation.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    output = tmp_path / "projection"
    code, _ = run_cli(
        "forecast",
        "--config",
        str(config or short_runs.config),
        "--recommendation",
        str(path),
        "--output-dir",
        str(output),
    )
    assert code == EXIT_USAGE
    assert message in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.integration
def test_a_combined_run_must_reproduce_the_sealed_selection(tmp_path: Path) -> None:
    config = write_config(tmp_path / "mini.json", mini_protocol())
    development = tmp_path / "development"
    code, text = run_cli(*backtest_args(config, development))
    assert code == EXIT_OK, text
    seal = read_json(development / "selection.json")
    selected = seal["selection"]["selected_model"]
    both = ["--period", "development", "--period", "confirmation", "--sealed-selection"]

    genuine = tmp_path / "genuine"
    code, text = run_cli(
        *backtest_args(config, genuine, *both, str(development / "selection.json"))
    )
    assert code == EXIT_OK, text
    assert "selection_differs_from_seal" not in text
    assert set(_children(genuine)) == BACKTEST_FILES | {"recommendation.json"}
    assert read_json(genuine / "manifest.json")["complete"] is True

    other = next(m for m in MINI_MODELS if m != selected)
    seal["selection"]["selected_model"] = other
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(seal), encoding="utf-8")
    tampered = tmp_path / "tampered"
    code, text = run_cli(*backtest_args(config, tampered, *both, str(forged)))
    assert code == EXIT_FAILED
    assert "selection_differs_from_seal" in text
    assert read_json(tampered / "manifest.json")["complete"] is False
    metrics = read_json(tampered / "metrics.json")
    assert metrics["selection"]["selected_model"] == other  # the seal is used unchanged ...
    assert metrics["selection_recomputed"]["selected_model"] == selected  # ... and contradicted


# --- 11. Future projection -----------------------------------------------------------------------


@pytest.mark.integration
def test_forecast_writes_labelled_projections_without_actuals(tmp_path: Path) -> None:
    output = tmp_path / "projection"
    code, text = run_cli("forecast", "--config", str(SMOKE), "--output-dir", str(output))
    assert code == EXIT_OK, text
    assert f"FUTURE PROJECTION from {LAST_OBSERVED.isoformat()}" in text
    assert "NOT VALIDATED" in text
    assert _children(output) == [
        "coefficients.csv",
        "config.json",
        "fits.csv",
        "manifest.json",
        "projections.csv",
        "report.md",
    ]
    projections = read_records(output / "projections.csv")
    assert "actual" not in projections.columns
    assert "mase_scale" not in projections.columns
    assert set(projections["record_type"]) == {"future_projection"}
    assert set(projections["status"]) == {"projected"}
    assert b"historical_backtest" not in (output / "projections.csv").read_bytes()
    assert projections["model_id"].drop_duplicates().tolist() == list(SMOKE_MODELS)
    assert len(projections) == len(SMOKE_MODELS) * 30
    targets = [LAST_OBSERVED + timedelta(days=h) for h in range(1, 31)]
    for model_id, cell in projections.groupby("model_id", sort=False):
        assert cell["horizon"].tolist() == list(range(1, 31))
        assert cell["target_date"].tolist() == [d.isoformat() for d in targets]
        assert set(cell["origin"]) == {LAST_OBSERVED.isoformat()}
        values = cell["prediction"].tolist()
        if model_id == "naive":
            assert values == [y(LAST_OBSERVED)] * 30
        elif model_id == "seasonal_naive":
            assert values == [
                y(LAST_OBSERVED + timedelta(days=h - 7 * ceil_div(h, 7))) for h in range(1, 31)
            ]
        elif model_id == "legacy_linear_regression":
            # the same code path reproduces the published recursive 30-day forecast
            assert values == [published_future()[d] for d in targets]
    manifest = read_json(output / "manifest.json")
    assert manifest["record_type"] == "future_projection"
    assert "FUTURE PROJECTION" in manifest["label"]
    assert manifest["origin"] == LAST_OBSERVED.isoformat()
    assert manifest["recommended_model"] is None
    for name, digest in manifest["outputs"].items():
        assert digest == sha256_of(output / name), name
    assert "FUTURE PROJECTION" in (output / "report.md").read_text(encoding="utf-8")


# --- 12. Legacy refit (committed snapshot) -------------------------------------------------


@pytest.mark.integration
def test_backtest_legacy_command_writes_the_one_step_holdout(tmp_path: Path) -> None:
    output = tmp_path / "legacy"
    code, text = run_cli("backtest", "legacy", "--output-dir", str(output))
    assert code == EXIT_OK, text
    assert "[PASS] legacy_refit" in text
    assert "90 of 90 predictions reproduced" in text
    assert "30 of 30 reproduced" in text
    holdout = read_records(output / "legacy_one_step_holdout.csv")
    dates = [HOLDOUT_FIRST + timedelta(days=k) for k in range(90)]
    assert dates[-1] == HOLDOUT_LAST
    assert len(holdout) == 90
    assert set(holdout["record_type"]) == {"legacy_one_step_holdout"}
    assert holdout["horizon"].tolist() == [1] * 90
    assert holdout["target_date"].tolist() == [d.isoformat() for d in dates]
    assert holdout["actual"].tolist() == [y(d) for d in dates]
    assert holdout["prediction_rounded"].tolist() == [published_holdout()[d] for d in dates]
    assert (np.round(holdout["prediction"]) == holdout["prediction_rounded"]).all()
    mae, rmse, mape = reference_metrics(zip(holdout["prediction"], holdout["actual"], strict=True))
    assert (round(mae, 2), round(rmse, 2), round(mape, 2)) == (514.3, 676.7, 4.93)
    manifest = read_json(output / "manifest.json")
    assert manifest["record_type"] == "legacy_one_step_holdout"
    assert "LEGACY ONE-STEP HOLDOUT" in manifest["label"]
    for name, digest in manifest["outputs"].items():
        assert digest == sha256_of(output / name), name
    summary = read_json(output / "legacy_one_step_holdout.json")
    assert summary["record_type"] == "legacy_one_step_holdout"
    assert (summary["first_target"], summary["last_target"]) == ("2026-04-02", "2026-06-30")


@pytest.fixture(scope="module")
def legacy_series(legacy_tables: dict[str, LoadedTable]) -> DemandSeries:
    return DemandSeries.from_frame(legacy_tables["daily_total"].frame, name="daily_total")


def _legacy_frames(legacy_tables: dict[str, LoadedTable]) -> list[pd.DataFrame]:
    return [legacy_tables[name].frame.copy() for name in LEGACY_TABLES]


@pytest.mark.integration
def test_legacy_refit_check_reproduces_the_published_outputs(
    legacy_series: DemandSeries, legacy_tables: dict[str, LoadedTable]
) -> None:
    report, holdout = legacy_refit_check(legacy_series, *_legacy_frames(legacy_tables))
    assert report.ok, report.issues
    assert [i.code for i in report.issues] == ["legacy_design_rank_deficient"]
    dates = [HOLDOUT_FIRST + timedelta(days=k) for k in range(90)]
    assert [d.date() for d in holdout.dates] == dates
    assert holdout.actual.tolist() == [y(d) for d in dates]
    assert np.round(holdout.predictions).tolist() == [published_holdout()[d] for d in dates]
    mae, rmse, mape = reference_metrics(zip(holdout.predictions, holdout.actual, strict=True))
    assert (round(mae, 2), round(rmse, 2), round(mape, 2)) == (514.3, 676.7, 4.93)
    facts = report.facts
    assert facts["holdout"]["predictions_reproduced"] == 90
    assert facts["holdout"]["predictions_published"] == 90
    assert [(r["metric"], r["ok"]) for r in facts["holdout"]["metrics"]] == [
        ("mae", True),
        ("rmse", True),
        ("mape_pct", True),
    ]
    recursive = facts["recursive_forecast"]
    assert recursive["forecasts_reproduced"] == 30
    assert recursive["forecasts_published"] == 30
    assert recursive["fed_back_inputs_equal_published"] is True
    assert (recursive["first_date"], recursive["last_date"]) == ("2026-07-01", "2026-07-30")
    assert 0 <= recursive["max_abs_raw_minus_rounded"] < 0.5
    assert facts["design_rank"] == 29  # 32 features less 3 exact dependencies
    assert facts["n_features"] == 32
    first_train = date(2023, 1, 1) + timedelta(days=28)
    assert holdout.n_train_rows == (HOLDOUT_FIRST - timedelta(days=1) - first_train).days + 1


def _bump_holdout(frames: list[pd.DataFrame]) -> None:
    frames[0].loc[frames[0].index[0], "model_forecast"] += 1


def _bump_future(frames: list[pd.DataFrame]) -> None:
    frames[1].loc[frames[1].index[-1], "forecast_requests"] += 1


def _bump_mae(frames: list[pd.DataFrame]) -> None:
    frames[2].loc[frames[2]["model_name"] == "Linear Regression Forecast", "mae"] = 514.29


@pytest.mark.integration
@pytest.mark.parametrize(
    ("tamper", "code", "facts"),
    [
        pytest.param(
            _bump_holdout, "holdout_mismatch", ("holdout", "predictions_reproduced", 89), id="h"
        ),
        pytest.param(
            _bump_future,
            "recursive_forecast_mismatch",
            ("recursive_forecast", "forecasts_reproduced", 29),
            id="future",
        ),
        pytest.param(
            _bump_mae, "holdout_metric_mismatch", ("holdout", "predictions_reproduced", 90), id="m"
        ),
    ],
)
def test_legacy_refit_check_detects_a_changed_published_value(
    tamper: Callable[[list[pd.DataFrame]], None],
    code: str,
    facts: tuple[str, str, int],
    legacy_series: DemandSeries,
    legacy_tables: dict[str, LoadedTable],
) -> None:
    frames = _legacy_frames(legacy_tables)
    tamper(frames)
    report, _ = legacy_refit_check(legacy_series, *frames)
    assert not report.ok
    assert [i.code for i in report.issues] == [code]
    block, key, value = facts
    assert report.facts[block][key] == value
    if code == "recursive_forecast_mismatch":
        assert report.facts["recursive_forecast"]["fed_back_inputs_equal_published"] is False


# --- 13. The engine on the committed snapshot ---------------------------------------------

LEGACY_ORIGINS = {
    "autumn_2024": date(2024, 11, 15),
    "year_end_2024": date(2024, 12, 31),
    "spring_2025": date(2025, 3, 3),
    "holdout_start": date(2026, 4, 1),
}


@pytest.fixture(scope="module")
def legacy_backtest(legacy_series: DemandSeries) -> Any:
    plan = BacktestPlan(
        periods=tuple(
            Period(name, PeriodRole.DESCRIPTIVE, origin, origin)
            for name, origin in LEGACY_ORIGINS.items()
        ),
        max_horizon=30,
        min_history_days=365,
    )
    return run_backtest(legacy_series, plan, [legacy_linear_regression()])


@pytest.mark.integration
def test_legacy_backtest_h1_at_the_holdout_start_equals_the_published_prediction(
    legacy_backtest: Any,
) -> None:
    records = legacy_backtest.records
    row = records[(records["period"] == "holdout_start") & (records["horizon"] == 1)]
    assert len(row) == 1
    assert row["target_date"].iloc[0] == pd.Timestamp(HOLDOUT_FIRST)
    assert round(float(row["prediction"].iloc[0])) == published_holdout()[HOLDOUT_FIRST] == 10788
    assert row["actual"].iloc[0] == y(HOLDOUT_FIRST)


@pytest.mark.integration
def test_legacy_estimability_flips_at_the_leap_day_identity(legacy_backtest: Any) -> None:
    records = legacy_backtest.records
    fits = legacy_backtest.fits.set_index("period")

    def estimable(period: str) -> list[Any]:
        cell = records[records["period"] == period].sort_values("horizon")
        assert cell["status"].tolist() == ["ok"] * 30
        return cell["estimable"].tolist()

    # Origin 2024-12-31: training 2023-2024 only, every target in 2025 -> none estimable.
    assert estimable("year_end_2024") == [False] * 30
    assert fits.loc["year_end_2024", "n_nonestimable"] == 30
    # Origin 2024-11-15: every target in 2024 -> all estimable.
    assert estimable("autumn_2024") == [True] * 30
    assert fits.loc["autumn_2024", "n_nonestimable"] == 0


@pytest.mark.integration
def test_legacy_design_rank_is_28_in_2024_and_29_in_2025(legacy_backtest: Any) -> None:
    fits = legacy_backtest.fits.set_index("period")
    expected_rank = {
        "autumn_2024": 32 - 3 - 1,
        "year_end_2024": 32 - 3 - 1,
        "spring_2025": 32 - 3,
        "holdout_start": 32 - 3,
    }
    first_train = date(2023, 1, 1) + timedelta(days=28)
    for period, origin in LEGACY_ORIGINS.items():
        row = fits.loc[period]
        assert row["rank"] == expected_rank[period], period
        assert row["n_features"] == 32
        assert bool(row["identifiable"]) is False
        assert row["train_first"] == first_train
        assert row["train_last"] == origin
        assert row["n_train_rows"] == (origin - first_train).days + 1


@pytest.mark.integration
def test_seasonal_naive_reproduces_the_published_baseline_at_every_weekly_horizon(
    legacy_series: DemandSeries,
) -> None:
    plan = BacktestPlan(
        periods=(
            Period(
                "legacy_window",
                PeriodRole.DESCRIPTIVE,
                date(2026, 3, 26),
                date(2026, 6, 29),
                1,
                True,
            ),
        ),
        max_horizon=30,
        min_history_days=365,
    )
    result = run_backtest(legacy_series, plan, [SeasonalNaiveForecaster("seasonal_naive", 7)])
    records = result.records
    beyond = records["target_date"] > pd.Timestamp(LAST_OBSERVED)
    assert set(records.loc[beyond, "status"]) == {"beyond_snapshot"}
    assert set(records.loc[~beyond, "status"]) == {"ok"}
    window = records[
        records["target_date"].between(pd.Timestamp(HOLDOUT_FIRST), pd.Timestamp(HOLDOUT_LAST))
    ]
    for horizon in range(1, 8):
        cell = window[window["horizon"] == horizon].sort_values("target_date")
        targets = [d.date() for d in pd.DatetimeIndex(cell["target_date"])]
        assert targets == [HOLDOUT_FIRST + timedelta(days=k) for k in range(90)], horizon
        assert cell["prediction"].tolist() == [y(t - timedelta(days=7)) for t in targets]
        assert cell["actual"].tolist() == [y(t) for t in targets]
        mae, rmse, mape = reference_metrics(zip(cell["prediction"], cell["actual"], strict=True))
        assert (round(mae, 2), round(rmse, 2), round(mape, 2)) == (851.16, 1107.24, 8.23), horizon
