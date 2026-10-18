"""Recompute the figures quoted by the M8 documentation corrections (read-only).

The M8 corrections of docs/UNCERTAINTY.md (findings M8-08 and M8-39), docs/DATA_QUALITY.md
(M8-10, M8-11) and docs/BACKTESTING.md section 10 (M8-10) quote numbers that are not stored
in any committed metrics file: the direction of the forecast errors on the flagged target
days, the faithfulness of ``spot_check_intervals.csv``, the power limits of the
data-quality detectors and the composition of the flagged-target sensitivity. This script
recomputes each of them from the committed snapshot and results and from a regenerated M3
reference run, and prints them as Markdown. It changes nothing.

The M3 run's ``intervals.csv`` is not committed (the manifest pins its SHA-256), so it is
regenerated first; the script refuses a run whose outputs differ from the committed
manifest. From the repository root::

    uv run atlas uncertainty run --period m3_selection --period m3_evaluation \\
        --sealed-selection docs/results/atlas-m3-intervals-v1/selection/selection.json \\
        --output-dir runs/m3-full                                       # about 5 minutes
    uv run python docs/audit/scripts/m8_doc_figures.py runs/m3-full \\
        > docs/audit/scripts/m8_doc_figures_output.md                   # a few seconds
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from atlas.experiment import csv_bytes
from atlas.quality import (
    citywide_anomalies,
    same_weekday_scores,
    segment_interruptions,
    segment_matrix,
)
from atlas.series import DemandSeries

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
DATA: Final = REPO_ROOT / "01 Data"
M2_FULL: Final = REPO_ROOT / "docs" / "results" / "atlas-m2-rolling-origin-v1" / "full"
M3_FULL: Final = REPO_ROOT / "docs" / "results" / "atlas-m3-intervals-v1" / "full"
PRIMARY: Final = "atlas_linear_regression"
KEY: Final = ["period", "model_id", "pair_id", "origin", "target_date", "horizon"]
FLOATS: Final = (
    "prediction",
    "cal_n_effective",
    "q_0.025",
    "q_0.1",
    "q_0.25",
    "q_0.5",
    "q_0.75",
    "q_0.9",
    "q_0.975",
)
CONFIRMATION: Final = (date(2025, 7, 1), date(2026, 6, 30))
#: Target days of the development period: first origin + 1 to last origin + 30.
DEVELOPMENT: Final = (date(2024, 2, 2), date(2025, 6, 30))


def _read_json(path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def check_run(run: Path) -> dict[str, Any]:
    """The regenerated M3 run must equal the committed reference, file by file."""
    committed = _read_json(M3_FULL / "manifest.json")
    for name, expected in committed["outputs"].items():
        actual = hashlib.sha256((run / name).read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"{run / name} differs from the committed manifest")
    return committed


def flagged_days(run: Path, manifest: dict[str, Any], emit: Callable[[str], None]) -> None:
    """UNCERTAINTY.md section 9.6: the forecasts on the flagged evaluation target days."""
    metrics = _read_json(M3_FULL / "metrics.json")
    pair = metrics["decided_pair"]
    flagged = set(manifest["flagged_target_dates"])
    frame = pd.read_csv(run / "intervals.csv", float_precision="round_trip")
    cell = frame[
        (frame["period"] == "m3_evaluation")
        & (frame["model_id"] == PRIMARY)
        & (frame["pair_id"] == pair)
        & frame["target_date"].isin(flagged)
    ]
    error = cell["prediction"] - cell["actual"]
    by_day = cell.groupby("target_date").agg(
        actual=("actual", "first"), mean=("prediction", "mean")
    )
    stratum = next(
        r
        for r in metrics["strata"]
        if (r["period"], r["model_id"], r["pair_id"], r["stratum"])
        == ("m3_evaluation", PRIMARY, pair, "flagged")
    )
    emit("## 1. Flagged evaluation target days (UNCERTAINTY.md, section 9.6)")
    emit("")
    emit(f"- pair `{pair}`, primary model; error = prediction - actual")
    emit(f"- forecasts {len(cell)}, target days {cell['target_date'].nunique()}")
    emit(f"- mean error {error.mean():,.1f} (committed strata bias {stratum['bias']:,.1f})")
    emit(f"- MAE {error.abs().mean():,.1f} (committed {stratum['mae']:,.1f})")
    means = f"{cell['actual'].mean():,.1f}, mean forecast {cell['prediction'].mean():,.1f}"
    emit(f"- mean outcome {means}")
    emit(f"- forecasts with outcome above the forecast: {int((error < 0).sum())} of {len(cell)}")
    above_mean = int((by_day["actual"] > by_day["mean"]).sum())
    emit(f"- days whose outcome exceeds the day's average forecast: {above_mean} of {len(by_day)}")
    above = float((cell["actual"] > cell["q_0.975"]).mean())
    below = float((cell["actual"] < cell["q_0.025"]).mean())
    emit(f"- outcomes above q_0.975: {100 * above:.1f} %; below q_0.025: {100 * below:.1f} %")
    emit(f"- days: {', '.join(sorted(by_day.index))}")
    emit("")


def spot_check(run: Path, manifest: dict[str, Any], emit: Callable[[str], None]) -> None:
    """UNCERTAINTY.md section 9: what full/spot_check_intervals.csv is."""
    spot_path = M3_FULL / "spot_check_intervals.csv"
    with (run / "intervals.csv").open(encoding="utf-8", newline="") as handle:
        reference = {tuple(r[k] for k in KEY): r for r in csv.DictReader(handle)}
    with spot_path.open(encoding="utf-8", newline="") as handle:
        spot = list(csv.DictReader(handle))
    lines = set((run / "intervals.csv").read_text(encoding="utf-8").splitlines())
    verbatim = sum(line in lines for line in spot_path.read_text(encoding="utf-8").splitlines()[1:])
    float_fields = other_fields = 0
    largest = 0.0
    ulps = 0
    for row in spot:
        ref = reference[tuple(row[k] for k in KEY)]
        for column, value in row.items():
            if value == ref[column]:
                continue
            if column not in FLOATS:
                other_fields += 1
                continue
            float_fields += 1
            a, b = np.float64(value), np.float64(ref[column])
            largest = max(largest, abs(float(a - b)) / abs(float(b)))
            ulps = max(ulps, abs(int(a.view(np.int64)) - int(b.view(np.int64))))
    default = pd.read_csv(run / "intervals.csv")  # pandas' default (lossy) float parser
    spot_frame = pd.read_csv(spot_path)
    rows = default.set_index(KEY).loc[spot_frame.set_index(KEY).index].reset_index()
    rebuilt = csv_bytes(rows[spot_frame.columns])
    emit("## 2. spot_check_intervals.csv (UNCERTAINTY.md, section 9)")
    emit("")
    emit(f"- listed in the run manifest: {'spot_check_intervals.csv' in manifest['outputs']}")
    emit(f"- rows {len(spot)}; origins {sorted({r['origin'] for r in spot})}")
    pairs = sorted({(r["model_id"], r["pair_id"]) for r in spot})
    emit(f"- model and pair: {pairs}")
    emit(f"- lines identical to a line of the regenerated intervals.csv: {verbatim} of {len(spot)}")
    emit(
        f"- floating-point fields that differ: {float_fields} (largest relative {largest:.2e},"
        f" at most {ulps} units in the last place); other fields that differ: {other_fields}"
    )
    same = rebuilt == spot_path.read_bytes()
    emit(f"- equals csv_bytes of those rows read with pandas' default float parser: {same}")
    emit("")


def detectors(emit: Callable[[str], None]) -> None:
    """DATA_QUALITY.md section 3: the power limits of the detectors; the HPD catch-up."""
    total = pd.read_csv(DATA / "daily_total.csv")
    segments = pd.read_csv(DATA / "daily_segments.csv")
    series = DemandSeries.from_frame(total, name="daily_total", allow_missing=True)
    scores = same_weekday_scores(series)
    observed, reference = scores.observed, scores.reference
    ratio = observed / reference
    surge: set[date] = set()
    for flag in citywide_anomalies(series):
        if flag.code.value == "surge":
            surge.update(flag.start + timedelta(days=k) for k in range(flag.days))
    high = [d.date() for d in ratio[ratio >= 1.25].index]
    late = [d for d in high if CONFIRMATION[0] <= d <= CONFIRMATION[1]]
    emit("## 3. Data-quality detectors (DATA_QUALITY.md, section 3)")
    emit("")
    emit(f"- surge days flagged: {len(surge)}")
    emit(
        f"- days at least 25 % above the same-weekday reference: {len(high)}, flagged "
        f"{sum(d in surge for d in high)}"
    )
    emit(f"- of which in 2025-07..2026-06: {len(late)}, flagged {sum(d in surge for d in late)}")
    emit("")
    emit("| day | requests | reference | above reference | scale | score | surge flag |")
    emit("|---|---:|---:|---:|---:|---:|---|")
    for stamp in observed.sort_values(ascending=False).head(5).index:
        day = pd.Timestamp(stamp)
        emit(
            f"| {day.date()} | {int(observed[day]):,} | {reference[day]:,.1f} | "
            f"{100 * (ratio[day] - 1):.0f} % | {scores.scale[day]:,.1f} | "
            f"{scores.score[day]:+.2f} | {day.date() in surge} |"
        )
    emit("")
    day = pd.Timestamp("2026-01-27")
    previous = np.array([observed[day - pd.Timedelta(days=7 * k)] for k in range(1, 9)])
    mad = float(np.median(np.abs(previous - np.median(previous))))
    emit(
        f"- 2026-01-27: the eight preceding Tuesdays ranged from {previous.min():,.0f} to "
        f"{previous.max():,.0f}; MAD {mad:,.0f}, scale {scores.scale[day]:,.1f}"
    )
    calendar = pd.DatetimeIndex(pd.to_datetime(total["request_date"]))
    frame = segments.assign(request_date=pd.to_datetime(segments["request_date"]))
    agencies = segment_matrix(frame, "agency", calendar)
    dhs = agencies["DHS"]
    dip = dhs[pd.Timestamp("2026-02-22") : pd.Timestamp("2026-02-25")]
    window = dhs[pd.Timestamp("2026-01-25") : pd.Timestamp("2026-02-21")]
    for agency in ("DOB", "DHS"):
        for flag in segment_interruptions(agencies[agency], f"agency:{agency}"):
            if flag.code.value != "intake_interruption" or flag.end < CONFIRMATION[0]:
                continue
            emit(
                f"- {agency} interruption {flag.start}..{flag.end} ({flag.days} days): "
                f"{flag.observed:g} requests against {flag.expected:g} expected"
            )
    emit(
        f"- DHS 2026-02-22..25: {[int(v) for v in dip]}; 10th percentile of the 28 days "
        f"before: {float(np.quantile(window, 0.1)):g}"
    )
    share = 100 * float(dhs.sum()) / float(agencies.to_numpy().sum())
    emit(f"- DHS share of five-borough requests: {share:.2f} %")
    hpd = agencies["HPD"]
    gap = list(pd.date_range("2024-04-11", "2024-04-14"))
    catch = pd.Timestamp("2024-04-15")
    blind = float(np.median(hpd[pd.Timestamp("2024-03-14") : pd.Timestamp("2024-04-10")]))
    emit(
        f"- HPD catch-up share, weekday-blind 28-day median {blind:g}: "
        f"{100 * (hpd[catch] - blind) / (4 * blind):.1f} %"
    )
    for weeks in (8, 4):
        expected = [
            float(np.median([hpd[d - pd.Timedelta(days=7 * k)] for k in range(1, weeks + 1)]))
            for d in [*gap, catch]
        ]
        share_w = 100 * (hpd[catch] - expected[-1]) / sum(expected[:-1])
        emit(
            f"- HPD catch-up share, medians of the {weeks} preceding same weekdays: {share_w:.1f} %"
        )
    emit("")


def flagged_sensitivity(emit: Callable[[str], None]) -> None:
    """BACKTESTING.md section 10: what the exclude-flagged-targets sensitivity removes."""
    dates = [
        date.fromisoformat(d) for d in _read_json(M2_FULL / "manifest.json")["flagged_target_dates"]
    ]
    early = [d for d in dates if DEVELOPMENT[0] <= d <= DEVELOPMENT[1]]
    late = [d for d in dates if CONFIRMATION[0] <= d <= CONFIRMATION[1]]
    before = [d for d in dates if d < DEVELOPMENT[0]]
    dhs = [d for d in late if date(2026, 1, 31) <= d <= date(2026, 2, 12)]
    emit("## 4. The flagged-target sensitivity (BACKTESTING.md, section 10)")
    emit("")
    emit(
        f"- flagged target dates: {len(dates)}; in the confirmation year: {len(late)}, of which "
        f"{len(dhs)} in the DHS interruption 2026-01-31..02-12"
    )
    emit(
        f"- in the backtest's target range: {len(early) + len(late)} ({len(early)} development, "
        f"{len(late)} confirmation); before its first target day {DEVELOPMENT[0]}: {len(before)}"
    )
    emit("")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("m3_run", type=Path, help="a regenerated M3 full reference run")
    args = parser.parse_args(argv)
    manifest = check_run(args.m3_run)
    print("# Figures quoted by the M8 documentation corrections\n")
    print("Generated by `docs/audit/scripts/m8_doc_figures.py` from `legacy-v0`, the committed")
    print("results and a regenerated M3 reference run identical to its committed manifest.\n")
    flagged_days(args.m3_run, manifest, print)
    spot_check(args.m3_run, manifest, print)
    detectors(print)
    flagged_sensitivity(print)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
