"""Finite-sample size of the HLN Diebold-Mariano test as M2 used it (M8 erratum, finding M8-04).

M2 compares two forecasters through one loss differential per forecast origin: the mean,
over horizons 1-30, of the difference of their absolute errors. ``atlas.stats.diebold_mariano``
tests whether its mean is zero with the Harvey-Leybourne-Newbold statistic: a rectangular HAC
variance truncated at lag ``h - 1 = 29``, the HLN small-sample factor and a Student t reference
with ``T - 1`` degrees of freedom, plus a Bartlett ``2h`` sensitivity; the pre-registered
decision p-value (``p_value_conservative``) is the larger of the two p-values.
``atlas.stats.bandwidth_sensitivity`` reports the same statistic under wider bandwidths, with
the same ``t(T - 1)`` reference.

The test's maintained assumption is that losses on different target days are uncorrelated,
so that the per-origin differential is a moving average of order ``h - 1 = 29``. This script
simulates exactly that null hypothesis: daily shocks ``e_s`` are independent with mean zero,
and the differential at origin ``t`` is ``(e_(t+1) + ... + e_(t+30)) / 30``. Each simulated
series goes through the unchanged M2 functions. The script reports:

1. the empirical size (rejection rate at a nominal 5 %) of the pre-registered rule
   ``p_value_conservative < 0.05``, of the HLN p-value alone and of every bandwidth variant,
   at the numbers of origins of the committed tests (development T = 486, confirmation
   T = 336);
2. size-corrected 95 % critical values of ``|t|``: the value the statistic must exceed for
   the rule to reject 5 % of the simulated null series;
3. size-corrected p-values of the committed selection, H1 and H2 statistics (the share of
   simulated null series whose p-value is at most the committed one), read from
   ``docs/results/atlas-m2-rolling-origin-v1/development/metrics.json`` and
   ``.../full/metrics.json``, and a Holm adjustment of the size-corrected H1 and H2 p-values;
4. a control: with T = 3000 origins the same rule's size is close to 5 %, so the
   over-rejection at T = 336-486 is a finite-sample property of the test, not an error in its
   formulas;
5. the size-corrected p-values of the M3 tests that use the same rule on the same grid
   (evaluation period, horizons 1-30, T = 336), read from
   ``docs/results/atlas-m3-intervals-v1/full/metrics.json``.

Two shock distributions are simulated: Gaussian (primary) and Student t with 3 degrees of
freedom (heavy-tailed, a robustness check). The results are deterministic for a given seed and
number of replications: every setting draws from its own ``numpy.random.default_rng`` stream.
A count could change only if a simulated p-value fell within rounding of a threshold.
Monte Carlo standard errors are binomial: ``sqrt(p (1 - p) / N)``.

Nothing here changes a committed result: the script only reads the committed metrics.

Usage, from the repository root (about two minutes on one core with the defaults)::

    uv run python docs/audit/scripts/dm_size_study.py > docs/audit/scripts/dm_size_study_output.md

Options: ``--replications N`` per setting (default 20000), ``--control-replications N``
(default 4000), ``--seed S`` (default 20261016).
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np

from atlas.stats import (
    SENSITIVITY_BANDWIDTHS,
    bandwidth_sensitivity,
    diebold_mariano,
    holm,
    student_t_quantile,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
RESULTS: Final = REPO_ROOT / "docs" / "results" / "atlas-m2-rolling-origin-v1"
M3_METRICS: Final = (
    REPO_ROOT / "docs" / "results" / "atlas-m3-intervals-v1" / "full" / "metrics.json"
)
NOMINAL: Final = 0.05
CONTROL_ORIGINS: Final = 3000
#: Columns of the size tables: the pre-registered rule, its two legs, the wider bandwidths.
RULE: Final = "pre-registered rule (larger of HLN and Bartlett 60)"
HLN: Final = "HLN alone (rectangular 29)"
VARIANTS: Final = (
    ("rectangular", 29),
    ("rectangular", 59),
    ("bartlett", 60),
    ("bartlett", 90),
    ("bartlett", 120),
)
SHOCKS: Final = ("gaussian", "student_t3")


def _variant_name(kernel: str, bandwidth: int) -> str:
    return f"{kernel} {bandwidth}"


COLUMNS: Final = (RULE, HLN, *(_variant_name(k, b) for k, b in VARIANTS[1:]))


@dataclass(frozen=True)
class CommittedTest:
    """One committed test: its origins and the nominal p-value of every column."""

    name: str
    period: str
    origins: int
    overlap: int
    p_values: dict[str, float]
    statistic: float
    holm_p: float | None = None


def _read(path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"inconsistent committed metrics: {message}")


def committed_tests() -> list[CommittedTest]:
    """The selection, H1 and H2 statistics exactly as the committed metrics record them."""
    development = _read(RESULTS / "development" / "metrics.json")
    full = _read(RESULTS / "full" / "metrics.json")
    selection = development["selection"]["regression_vs_best_baseline"]
    _check(development["selection"]["significant"] is True, "the selection was not significant")
    _check(full["selection"] == development["selection"], "the full run changed the selection")
    primary = {t["hypothesis"][:2]: t for t in full["recommendation"]["primary_tests"]}
    sources = {"selection": selection, "H1": primary["H1"], "H2": primary["H2"]}
    tests: list[CommittedTest] = []
    for row in full["bandwidth_sensitivity"]:
        key = row["test"].split(":")[0]
        source = sources[key]
        variants = {
            _variant_name(v["kernel"], v["bandwidth"]): v["p_value"] for v in row["variants"]
        }
        _check(
            [(v["kernel"], v["bandwidth"]) for v in row["variants"]] == list(VARIANTS),
            f"{key}: unexpected bandwidths",
        )
        _check(row["n_origins"] == source["dm_n_origins"] == source["n_origins"], f"{key}: origins")
        _check(row["overlap"] == source["last_horizon"] == 30, f"{key}: overlap")
        _check(math.isclose(variants["rectangular 29"], source["dm_p_value"], rel_tol=1e-12), key)
        _check(
            math.isclose(variants["bartlett 60"], source["dm_bartlett_p_value"], rel_tol=1e-12), key
        )
        _check(
            source["dm_p_value_conservative"]
            == max(source["dm_p_value"], source["dm_bartlett_p_value"]),
            f"{key}: conservative p",
        )
        p_values = {RULE: source["dm_p_value_conservative"], HLN: source["dm_p_value"]}
        p_values.update({name: variants[name] for name in COLUMNS[2:]})
        tests.append(
            CommittedTest(
                name=key,
                period=row["period"],
                origins=row["n_origins"],
                overlap=row["overlap"],
                p_values=p_values,
                statistic=source["dm_statistic"],
                holm_p=source.get("holm_adjusted_p_value"),
            )
        )
    _check([t.name for t in tests] == ["selection", "H1", "H2"], "test names")
    return tests


def m3_tests() -> list[tuple[str, str, int, float]]:
    """M3's evaluation-period tests over horizons 1-30: ``(family, description, origins, p)``.

    They use the same rule (``p_value_conservative`` of ``atlas.stats.diebold_mariano``) on
    one value per origin with an overlap of 30 days.
    """
    m3 = _read(M3_METRICS)
    pair, primary = m3["decided_pair"], "atlas_linear_regression"
    summary = next(
        r
        for r in m3["interval_summary"]
        if (r["period"], r["model_id"], r["pair_id"], r["label"])
        == ("m3_evaluation", primary, pair, "days_1_30")
    )
    _check(summary["n_origins"] == 336, "M3 evaluation origins")
    tests: list[tuple[str, str, int, float]] = [
        ("coverage", f"coverage = {lv['level']:g}, primary model", 336, lv["p_value_conservative"])
        for lv in m3["decision_status"]["by_model"][primary]["intervals"]["levels"]
    ]
    for r in m3["comparisons"]:
        if (r["period"], r["label"], r["pair_id"]) != ("m3_evaluation", "days_1_30", pair):
            continue
        horizons = r["last_horizon"] - r["first_horizon"] + 1
        _check(horizons == 30 and r["n_common"] == 336 * horizons, "M3 comparison grid")
        name = f"WIS, {r['model_id']} vs {r['reference_model']} {r['reference_pair']}"
        tests.append(("WIS", name, 336, r["wis_p_conservative"]))
    for r in m3["exceedance_summary"]:
        if (r["period"], r["label"], r["model_id"], r["pair_id"]) != (
            "m3_evaluation",
            "days_1_30",
            primary,
            pair,
        ):
            continue
        _check(r["n_origins"] == 336, "M3 exceedance origins")
        name = f"Brier vs climatology, {r['threshold_label']}"
        tests.append(("Brier", name, 336, r["brier_p_conservative"]))
    return tests


def null_series(rng: np.random.Generator, origins: int, overlap: int, shocks: str) -> np.ndarray:
    """One per-origin loss differential under the maintained MA(overlap - 1) null."""
    size = origins + overlap - 1
    daily = rng.standard_normal(size) if shocks == "gaussian" else rng.standard_t(3, size)
    return np.convolve(daily, np.full(overlap, 1.0 / overlap), mode="valid")


def simulate(
    origins: int, overlap: int, shocks: str, replications: int, seed: int, *, variants: bool
) -> dict[str, np.ndarray]:
    """Nominal p-values of every column for ``replications`` null series (missing -> 1.0)."""
    rng = np.random.default_rng([seed, origins, SHOCKS.index(shocks)])
    names = COLUMNS if variants else (RULE, HLN)
    out = {name: np.ones(replications) for name in names}
    for i in range(replications):
        series = null_series(rng, origins, overlap, shocks)
        test = diebold_mariano(series, horizon=overlap)
        if not test.computed or test.p_value is None or test.p_value_conservative is None:
            continue  # cannot reject: the p-values stay at 1.0
        out[RULE][i] = test.p_value_conservative
        out[HLN][i] = test.p_value
        if variants:
            for row in bandwidth_sensitivity(series, horizon=overlap):
                name = _variant_name(row["kernel"], row["bandwidth"])
                if name in out and row["p_value"] is not None:
                    out[name][i] = row["p_value"]
    return out


def binomial_se(p: float, n: int) -> float:
    return math.sqrt(p * (1.0 - p) / n)


def critical_t(p_values: np.ndarray, origins: int) -> tuple[float, float, float]:
    """Size-corrected 95 % critical |t| and a 95 % Monte Carlo interval (order statistics)."""
    ordered = np.sort(p_values)
    n = ordered.size
    k = math.ceil(NOMINAL * n)
    spread = 1.96 * math.sqrt(n * NOMINAL * (1.0 - NOMINAL))
    low_k, high_k = max(1, math.floor(k - spread)), min(n, math.ceil(k + spread))

    def to_t(alpha: float) -> float:
        return student_t_quantile(1.0 - alpha / 2.0, origins - 1)

    # A smaller p-value threshold is a larger critical value.
    return (
        to_t(float(ordered[k - 1])),
        to_t(float(ordered[high_k - 1])),
        to_t(float(ordered[low_k - 1])),
    )


def nominal_t(p_value: float, origins: int) -> float:
    return student_t_quantile(1.0 - p_value / 2.0, origins - 1)


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def _pct(value: float) -> str:
    return f"{100.0 * value:.1f} %"


def report(args: argparse.Namespace, emit: Callable[[str], None]) -> None:
    started = time.perf_counter()
    tests = committed_tests()
    overlap = tests[0].overlap
    origins = sorted({t.origins for t in tests}, reverse=True)
    n = args.replications
    sims: dict[tuple[str, int], dict[str, np.ndarray]] = {}
    for shocks in SHOCKS:
        for t in origins:
            sims[(shocks, t)] = simulate(t, overlap, shocks, n, args.seed, variants=True)
    control = simulate(
        CONTROL_ORIGINS, overlap, "gaussian", args.control_replications, args.seed, variants=False
    )
    print(f"simulations took {time.perf_counter() - started:.0f} s", file=sys.stderr)

    emit("# Size of the HLN Diebold-Mariano test as M2 used it (M8 erratum)")
    emit("")
    emit(
        "Generated by `docs/audit/scripts/dm_size_study.py` (read its docstring for the method). "
        f"Overlap h = {overlap}; null: per-origin differential = mean of {overlap} consecutive "
        f"independent daily shocks (an exact MA({overlap - 1})); {n:,} replications per setting; "
        f"seed {args.seed}; Python {platform.python_version()}, NumPy {np.__version__}. "
        "Monte Carlo standard errors (MC SE) are binomial."
    )
    emit("")
    emit("## 1. Empirical size at a nominal 5 %")
    emit("")
    header = ["rule or variant"]
    for shocks in SHOCKS:
        header += [f"T = {t}, {shocks}" for t in origins]
    rows = []
    for name in COLUMNS:
        row = [name]
        for shocks in SHOCKS:
            for t in origins:
                rate = float(np.mean(sims[(shocks, t)][name] < NOMINAL))
                row.append(f"{_pct(rate)} (MC SE {100 * binomial_se(rate, n):.1f})")
        rows.append(row)
    emit(_table(header, rows))
    emit("")
    for shocks in SHOCKS:
        for t in origins:
            sim = sims[(shocks, t)]
            hln_only = int(np.sum((sim[HLN] < NOMINAL) & (sim[RULE] >= NOMINAL)))
            emit(
                f"- T = {t}, {shocks}: of the {int(np.sum(sim[HLN] < NOMINAL)):,} null series the "
                f"HLN p-value rejects, the Bartlett 60 leg of the pre-registered rule keeps "
                f"{hln_only:,} from being rejected."
            )
    rate = float(np.mean(control[RULE] < NOMINAL))
    emit(
        f"- Control, T = {CONTROL_ORIGINS:,}, gaussian, {args.control_replications:,} "
        f"replications: the pre-registered rule rejects {_pct(rate)} (MC SE "
        f"{100 * binomial_se(rate, args.control_replications):.1f}), the HLN p-value alone "
        f"{_pct(float(np.mean(control[HLN] < NOMINAL)))}."
    )
    emit("")
    emit("## 2. Size-corrected 95 % critical values of |t| (gaussian shocks)")
    emit("")
    emit(
        "The nominal critical value is the 0.975 quantile of t(T - 1): "
        + ", ".join(f"{nominal_t(NOMINAL, t):.3f} for T = {t}" for t in origins)
        + ". For the pre-registered rule the statistic is the smaller of the two |t| values. "
        "In brackets: a 95 % Monte Carlo interval from the binomial order statistics."
    )
    emit("")
    rows = []
    for name in COLUMNS:
        row = [name]
        for t in origins:
            value, low, high = critical_t(sims[("gaussian", t)][name], t)
            row.append(f"{value:.2f} [{low:.2f}, {high:.2f}]")
        rows.append(row)
    emit(_table(["rule or variant", *(f"T = {t}" for t in origins)], rows))
    emit("")
    emit("## 3. Committed statistics and their size-corrected p-values")
    emit("")
    emit(
        "Nominal p: as committed. Absolute t: the t(T - 1) value equivalent to that p-value "
        "(for the pre-registered rule, the smaller of the two legs). Size-corrected p: the "
        "share of simulated null series whose p-value is at most the committed one."
    )
    emit("")
    rows = []
    for test in tests:
        for name in COLUMNS:
            p = test.p_values[name]
            row = [test.name, f"{test.period}, T = {test.origins}", name, f"{p:.4f}"]
            row.append(f"{nominal_t(p, test.origins):.3f}")
            for shocks in SHOCKS:
                corrected = float(np.mean(sims[(shocks, test.origins)][name] <= p))
                row.append(f"{corrected:.3f} (MC SE {binomial_se(corrected, n):.3f})")
            rows.append(row)
    emit(
        _table(
            [
                "test",
                "period",
                "rule or variant",
                "nominal p",
                "absolute t",
                *(f"size-corrected p, {s}" for s in SHOCKS),
            ],
            rows,
        )
    )
    emit("")
    for test in tests:
        emit(
            f"- {test.name}: committed HLN statistic {test.statistic:.4f}; |t| recovered from the "
            f"committed HLN p-value {nominal_t(test.p_values[HLN], test.origins):.4f}."
        )
    emit("")
    emit("## 4. Holm adjustment of the primary family with size-corrected p-values")
    emit("")
    rows = []
    family = [t for t in tests if t.name in ("H1", "H2")]
    for shocks in SHOCKS:
        family_p = [
            float(np.mean(sims[(shocks, t.origins)][RULE] <= t.p_values[RULE])) for t in family
        ]
        adjusted = holm(family_p)
        for test, raw, adj in zip(family, family_p, adjusted, strict=True):
            committed = "n/a" if test.holm_p is None else f"{test.holm_p:.4f}"
            rows.append([test.name, shocks, committed, f"{raw:.3f}", f"{adj:.3f}"])
    emit(
        _table(
            [
                "test",
                "shocks",
                "committed Holm p",
                "size-corrected p",
                "size-corrected Holm p",
            ],
            rows,
        )
    )
    emit("")
    emit("## 5. The same rule in M3 (evaluation period, horizons 1-30, T = 336)")
    emit("")
    emit(
        "M3 tests coverage, WIS differences and Brier differences with the same rule on the "
        "same grid. Its p-values are nominal and not adjusted for multiplicity. Holm: adjusted "
        "within the family (the five evaluation-period WIS comparisons, the three Brier "
        "thresholds); not applied to the coverage tests, whose pass condition is "
        "non-rejection."
    )
    emit("")
    m3 = m3_tests()
    columns: dict[str, list[float]] = {"nominal": [p for _, _, _, p in m3]}
    for shocks in SHOCKS:
        columns[shocks] = [float(np.mean(sims[(shocks, t)][RULE] <= p)) for _, _, t, p in m3]
    m3_holm: dict[str, list[float | None]] = {key: [None] * len(m3) for key in columns}
    for group in ("WIS", "Brier"):
        members = [i for i, test in enumerate(m3) if test[0] == group]
        for key, values in columns.items():
            chosen: list[float | None] = [values[i] for i in members]
            for i, adjusted_p in zip(members, holm(chosen), strict=True):
                m3_holm[key][i] = adjusted_p

    def holm_cell(value: float | None) -> str:
        return "-" if value is None else f"{value:.4f}"

    rows = []
    for i, (_, name, _, p) in enumerate(m3):
        row = [name, f"{p:.4f}", holm_cell(m3_holm["nominal"][i])]
        for shocks in SHOCKS:
            corrected = columns[shocks][i]
            row.append(f"{corrected:.4f} (MC SE {binomial_se(corrected, n):.4f})")
            row.append(holm_cell(m3_holm[shocks][i]))
        rows.append(row)
    header = ["M3 test", "nominal p", "nominal p, Holm"]
    for shocks in SHOCKS:
        header += [f"size-corrected p, {shocks}", f"size-corrected p, {shocks}, Holm"]
    emit(_table(header, rows))
    emit("")
    emit(
        "SENSITIVITY_BANDWIDTHS in atlas.stats: "
        + ", ".join(f"{k} {m}h{o:+d}" if o else f"{k} {m}h" for k, m, o in SENSITIVITY_BANDWIDTHS)
        + f" (with h = {overlap}: "
        + ", ".join(f"{k} {b}" for k, b in VARIANTS)
        + ")."
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--replications", type=int, default=20_000)
    parser.add_argument("--control-replications", type=int, default=4_000)
    parser.add_argument("--seed", type=int, default=20261016)
    args = parser.parse_args(argv)
    if args.replications < 1000 or args.control_replications < 100:
        parser.error("use at least 1000 replications (100 for the control)")
    report(args, print)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
