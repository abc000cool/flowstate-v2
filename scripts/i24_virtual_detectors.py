"""Virtual loop detectors on I-24 MOTION mornings → a generic detector CSV (docs/PRE_FRISCO_PROGRAM.md C9).

The corridor-study protocol judges, selects and splits days from detector data
(docs/FRISCO_PROTOCOL.md §2–3: ``scripts/data_quality_report.py``,
``scripts/station_selection.py``, ``scripts/day_split.py``). I-24 MOTION has
trajectories, not loops, so this script reads each processed westbound morning
(``scripts/i24_extract.py`` with the day's ``--t-origin``,
``scripts/i24_data.py``) the way a loop station over mainline lanes 1–4 would,
at the validator's six count sections (data x = 200, 1000, 2200, 3200, 4800,
5400 m, ``scripts/i24_validate.py`` ``SECTIONS_M``), per 5-minute window over
06:00–10:00 CST:

* ``flow_veh_h`` — the fragment crossings of the section, by the validator's
  rule (``scripts/i24_build_replica.py::crossings_per_window``: two consecutive
  samples of one fragment straddle it, counted in the window of the later
  sample), ×12, divided by the day's recommended coverage: its coverage
  artifact's ``pooled.recommended_filled`` for the 15-minute window that
  contains the 5-minute window (``scripts/i24_validate.py``
  ``_recommended_coverage``, the criteria row's scaling). The tracked counts
  are lower bounds (docs/I24_DATA.md §4); this is the protocol's flow.
* ``speed_ms`` — the mean speed of those crossings, each interpolated at the
  section between its two samples: the time-mean speed a loop reports.
  Speeds are coverage-robust and are not scaled. No crossing → empty.
* ``occupancy_pct`` — the share of the window during which a tracked vehicle
  body covers the section (front bumper past it, rear bumper not yet: the
  5 Hz samples with ``x ≥ x_s > x − length``, 0.2 s each), averaged over the
  four lanes and divided by the same coverage: occupancy adds over vehicles
  as flow does, so a random thinning to coverage c scales both by c, and the
  implied vehicle length ``v·o/q`` stays a length.

Beside them, ignored by the loader: ``n_crossings_tracked``,
``flow_tracked_veh_h``, ``occupancy_tracked_pct`` and ``coverage``. The
canonical columns are ``calibration.loaders.detector_csv.DETECTOR_COLUMNS``
(``lanes`` = 4, ``kind`` = mainline, ``x_m`` = data x), with ISO timestamps
(window start, ``-06:00``), so the three protocol scripts read the file with
``--detectors`` unchanged. A window outside the recording's sample span
(``meta.json`` ``t_min_s`` … ``t_max_s``, 1 s slack) is unmeasured: every
reading empty, never zero; so is a flow or occupancy without a coverage.

Also written: ``stations.csv`` (the six stations and the four ramps without a
detector at the replica builder's count positions,
``artifacts/i24_replica_inputs.json`` ``ramps[].count_x_m``, for the mass
balance) and ``virtual_detectors.json`` (provenance: each day's recording and
data hash, its coverage artifact and sha256, the rules; per section the
study-period tracked crossings and their ratio to the day's six-section
median, a coverage-hole diagnostic reported only).

**Checks** (each refuses, exit 2): the processed directory holds the stated
date's morning (``I24Day.check_recording``); the coverage artifact was
computed on that recording (same ``data_hash``); and, for each ``--observed
DATE=PATH``, the day's study-period crossing counts equal that observed side's
``counts_tracked`` (``scripts/i24_validate.py --observed-only``, or the
committed ``artifacts/i24_validation_observed.json`` for 30 Nov 2022). The
study period is read as one block exactly as the observed side reads it (same
time and position slice, 60-s pads), so the counts agree by construction; a
difference means the files describe different recordings or code.

Memory: one block at a time (06:00–06:30, 06:30–08:30, 08:30–10:00, each with
60-s pads); the study block is the observed side's own load.

Run (the VM; stage ``p20_i24_days``, artifacts/i24_days_2026-10-07/stage_p20_c9.sh.txt)::

    uv run --no-sync python scripts/i24_virtual_detectors.py \\
        --day 2022-11-30=data/i24motion/processed/i24_wb_20221130 \\
        --coverage 2022-11-30=artifacts/i24_coverage.json \\
        --observed 2022-11-30=artifacts/i24_validation_observed.json \\
        --day 2022-11-29=data/i24motion/processed/i24_wb_20221129 \\
        --coverage 2022-11-29=artifacts/i24_days_2026-10-07/coverage_20221129.json \\
        --out-dir artifacts/i24_days_2026-10-07
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_build_replica import T_STUDY_HI_S, T_STUDY_LO_S, WINDOW_S
from i24_data import (
    CST_OFFSET_S,
    MAINLINE_LANES,
    REPO_ROOT,
    SAMPLE_DT_S,
    I24Day,
    t0_unix_for_date,
)
from i24_validate import INPUTS, SECTIONS_M, _recommended_coverage, _span

SCHEMA = "flowstate.i24_virtual_detectors/1"
CSV_NAME = "detectors.csv"
STATIONS_NAME = "stations.csv"
SUMMARY_NAME = "virtual_detectors.json"

#: The span written: the INCEPTION mornings' 06:00–10:00 CST (``scripts/i24_coverage.py``).
T_LO_S = 0.0
T_HI_S = 14400.0
#: Read blocks: before, the study period itself (the observed side's slice), after.
BLOCKS_S = ((T_LO_S, T_STUDY_LO_S), (T_STUDY_LO_S, T_STUDY_HI_S), (T_STUDY_HI_S, T_HI_S))
#: Pads around each block, the observed side's (``scripts/i24_validate.py`` ``observed_side``).
PAD_S = 60.0
#: Position pad around the measured span, the observed side's.
X_PAD_M = 200.0
#: Slack on the recording's sample span when deciding a window was recorded [s].
SPAN_SLACK_S = 1.0
N_LANES = MAINLINE_LANES[1] - MAINLINE_LANES[0] + 1
#: Data x [m] per mile (data x = (62.7 − MM) × 1609.344, docs/I24_DATA.md §1).
M_PER_MILE = 1609.344
MM_AT_X0 = 62.7
CST = timezone(timedelta(seconds=CST_OFFSET_S))
EXTRA_COLUMNS = ("n_crossings_tracked", "flow_tracked_veh_h", "occupancy_tracked_pct", "coverage")
CANONICAL_COLUMNS = (
    "timestamp",
    "station",
    "flow_veh_h",
    "occupancy_pct",
    "speed_ms",
    "lanes",
    "kind",
    "x_m",
)


def station_id(x_m: float) -> str:
    """``S0200`` for the section at data x = 200 m."""
    return f"S{round(x_m):04d}"


def sha256_file(path: Path) -> str:
    """Hex sha256 of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    """``path`` relative to the repository when inside it."""
    p = path.resolve()
    return str(p.relative_to(REPO_ROOT)) if p.is_relative_to(REPO_ROOT) else str(p)


@dataclass(frozen=True)
class BlockReadings:
    """One read block's per-(section, window) sums (windows from the block's start)."""

    counts: np.ndarray  # [section][window] crossings
    speed_sums: np.ndarray  # [section][window] summed crossing speeds [m/s]
    occupied_s: np.ndarray  # [section][window] summed covered time over the lanes [s]


def block_readings(
    df: pd.DataFrame, sections_m: Sequence[float], t_lo: float, t_hi: float
) -> BlockReadings:
    """Crossings, crossing speeds and occupied time per section and 5-min window of ``[t_lo, t_hi)``.

    Args:
        df: Mainline rows with ``t, veh_id, x, v, length`` (the block and its pads).
        sections_m: Section positions (data x) [m].
        t_lo: Block start [s] (window 0 starts here).
        t_hi: Block end [s].

    Returns:
        The :class:`BlockReadings`. Crossings follow
        ``scripts/i24_build_replica.py::crossings_per_window`` exactly (the
        rows sorted by fragment then time; a crossing is two consecutive rows
        of one fragment with ``x_prev < x_s <= x_cur`` and the later row's
        ``t`` in the block); occupancy counts the rows of the block (pads
        excluded) whose body covers the section.
    """
    n_win = round((t_hi - t_lo) / WINDOW_S)
    n_sec = len(sections_m)
    counts = np.zeros((n_sec, n_win), dtype=np.int64)
    speed_sums = np.zeros((n_sec, n_win))
    occupied = np.zeros((n_sec, n_win))
    if df.empty:
        return BlockReadings(counts, speed_sums, occupied)
    codes, _ = pd.factorize(df["veh_id"].to_numpy())
    t = df["t"].to_numpy(dtype=np.float64)
    order = np.lexsort((t, codes))
    codes, t = codes[order], t[order]
    x = df["x"].to_numpy(dtype=np.float64)[order]
    v = df["v"].to_numpy(dtype=np.float64)[order]
    length = df["length"].to_numpy(dtype=np.float64)[order]
    same = codes[1:] == codes[:-1]
    x_prev, x_cur, t_cur = x[:-1][same], x[1:][same], t[1:][same]
    v_prev, v_cur = v[:-1][same], v[1:][same]
    in_block = (t >= t_lo) & (t < t_hi)
    for i, x_s in enumerate(sections_m):
        hit = (x_prev < x_s) & (x_cur >= x_s) & (t_cur >= t_lo) & (t_cur < t_hi)
        w = ((t_cur[hit] - t_lo) // WINDOW_S).astype(np.int64)
        frac = (x_s - x_prev[hit]) / (x_cur[hit] - x_prev[hit])
        v_x = v_prev[hit] + frac * (v_cur[hit] - v_prev[hit])
        counts[i] = np.bincount(w, minlength=n_win)[:n_win]
        speed_sums[i] = np.bincount(w, weights=v_x, minlength=n_win)[:n_win]
        covers = in_block & (x >= x_s) & (x - length < x_s)
        w_o = ((t[covers] - t_lo) // WINDOW_S).astype(np.int64)
        occupied[i] = np.bincount(w_o, minlength=n_win)[:n_win] * SAMPLE_DT_S
    return BlockReadings(counts, speed_sums, occupied)


def day_readings(
    day: I24Day, sections_m: Sequence[float], span_m: tuple[float, float]
) -> BlockReadings:
    """:func:`block_readings` over 06:00–10:00, block by block (module docstring, "Memory")."""
    parts = []
    for t_lo, t_hi in BLOCKS_S:
        df = day.load_mainline(
            t_range_s=(t_lo - PAD_S, t_hi + PAD_S),
            x_range_m=(span_m[0] - X_PAD_M, span_m[1] + X_PAD_M),
            columns=["t", "veh_id", "x", "v", "length"],
        )
        parts.append(block_readings(df, sections_m, t_lo, t_hi))
        del df
    return BlockReadings(
        counts=np.concatenate([p.counts for p in parts], axis=1),
        speed_sums=np.concatenate([p.speed_sums for p in parts], axis=1),
        occupied_s=np.concatenate([p.occupied_s for p in parts], axis=1),
    )


def recorded_windows(meta: Mapping[str, Any], n_win: int) -> np.ndarray:
    """Whether each 5-min window of 06:00–10:00 lies inside the recording's sample span."""
    t_min, t_max = float(meta["t_min_s"]), float(meta["t_max_s"])
    starts = T_LO_S + WINDOW_S * np.arange(n_win)
    return (starts >= t_min - SPAN_SLACK_S) & (starts + WINDOW_S <= t_max + SPAN_SLACK_S)


def detector_rows(
    day_date: str,
    sections_m: Sequence[float],
    readings: BlockReadings,
    coverage: np.ndarray,
    recorded: np.ndarray,
) -> pd.DataFrame:
    """One day's CSV rows (module docstring): window-major, sections in order."""
    t0_local = datetime.fromisoformat(f"{day_date}T06:00:00").replace(tzinfo=CST)
    rows = []
    n_win = readings.counts.shape[1]
    for w in range(n_win):
        stamp = (t0_local + timedelta(seconds=T_LO_S + w * WINDOW_S)).isoformat()
        c = float(coverage[w])
        for i, x_s in enumerate(sections_m):
            n = int(readings.counts[i, w])
            ok = bool(recorded[w])
            scaled = ok and math.isfinite(c) and c > 0.0
            flow_tracked = n * 3600.0 / WINDOW_S if ok else math.nan
            occ_tracked = (
                100.0 * readings.occupied_s[i, w] / (WINDOW_S * N_LANES) if ok else math.nan
            )
            rows.append(
                {
                    "timestamp": stamp,
                    "station": station_id(x_s),
                    "flow_veh_h": flow_tracked / c if scaled else math.nan,
                    "occupancy_pct": occ_tracked / c if scaled else math.nan,
                    "speed_ms": readings.speed_sums[i, w] / n if ok and n > 0 else math.nan,
                    "lanes": N_LANES,
                    "kind": "mainline",
                    "x_m": float(x_s),
                    "n_crossings_tracked": n if ok else math.nan,
                    "flow_tracked_veh_h": flow_tracked,
                    "occupancy_tracked_pct": occ_tracked,
                    "coverage": c if math.isfinite(c) else math.nan,
                }
            )
    out = pd.DataFrame(rows, columns=[*CANONICAL_COLUMNS, *EXTRA_COLUMNS])
    out["n_crossings_tracked"] = out["n_crossings_tracked"].astype("Int64")
    return out


def stations_table(sections_m: Sequence[float], ramps: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """The stations table: the six sections, then the ramps without detectors."""
    rows = [
        {
            "station": station_id(x),
            "kind": "mainline",
            "x_m": float(x),
            "label": f"data x {x:.0f} m (MM {MM_AT_X0 - x / M_PER_MILE:.2f})",
        }
        for x in sections_m
    ]
    for r in ramps:
        kind = "on_ramp" if str(r["kind"]).startswith("on") else "off_ramp"
        x = float(r["count_x_m"])
        rows.append(
            {
                "station": f"R{round(x):04d}_{'on' if kind == 'on_ramp' else 'off'}",
                "kind": kind,
                "x_m": x,
                "label": f"{r['name']} (no detector; the replica builder's count position)",
            }
        )
    return pd.DataFrame(rows, columns=["station", "kind", "x_m", "label"])


def parse_dated(items: Sequence[str], what: str) -> dict[str, Path]:
    """``DATE=PATH`` items → date → path (relative paths are the repository's)."""
    out: dict[str, Path] = {}
    for item in items:
        day, sep, path = str(item).partition("=")
        if not sep or not path:
            raise ValueError(f"{what} {item!r}: expected DATE=PATH")
        d = date.fromisoformat(day.strip()).isoformat()
        if d in out:
            raise ValueError(f"{what}: date {d} given twice")
        p = Path(path)
        out[d] = p if p.is_absolute() else REPO_ROOT / p
    return out


def build(
    days: Mapping[str, Path],
    coverages: Mapping[str, Path],
    observed: Mapping[str, Path],
    out_dir: Path,
    argv: Sequence[str] = (),
) -> dict[str, Any]:
    """Write the CSV, the stations table and the summary; returns the summary.

    Raises:
        ValueError: A check of the module docstring fails, or a day lacks its coverage.
    """
    missing = sorted(set(days) - set(coverages))
    if missing:
        raise ValueError(f"no --coverage for {missing}")
    stray = sorted((set(coverages) | set(observed)) - set(days))
    if stray:
        raise ValueError(f"--coverage/--observed for date(s) without --day: {stray}")
    span = _span()
    inputs = json.loads(INPUTS.read_text())
    sections = list(SECTIONS_M)
    n_win = round((T_HI_S - T_LO_S) / WINDOW_S)
    study = slice(
        round((T_STUDY_LO_S - T_LO_S) / WINDOW_S), round((T_STUDY_HI_S - T_LO_S) / WINDOW_S)
    )
    frames, summaries = [], []
    for d in sorted(days):
        day = I24Day(days[d], t0_unix_for_date(d))
        problems = day.check_recording(d)
        if problems:
            raise ValueError(f"{d} ({rel(days[d])}): " + "; ".join(problems))
        meta = day.meta()
        cov_doc = json.loads(coverages[d].read_text())
        if cov_doc.get("data_hash") != meta["data_hash"]:
            raise ValueError(
                f"{d}: {rel(coverages[d])} was computed on data {str(cov_doc.get('data_hash'))[:12]}, "
                f"not this recording's {str(meta['data_hash'])[:12]}"
            )
        coverage, coverage_rule = _recommended_coverage(n_win, T_LO_S, coverages[d])
        readings = day_readings(day, sections, span)
        recorded = recorded_windows(meta, n_win)
        frames.append(detector_rows(d, sections, readings, coverage, recorded))
        study_counts = readings.counts[:, study]
        totals = study_counts.sum(axis=1)
        median = float(np.median(totals))
        check: dict[str, Any] | None = None
        if d in observed:
            obs = json.loads(observed[d].read_text())
            expected = np.asarray(obs["counts_tracked"], dtype=np.int64)
            if obs.get("data_hash") != meta["data_hash"]:
                raise ValueError(f"{d}: {rel(observed[d])} is another recording's observed side")
            if expected.shape != study_counts.shape or not np.array_equal(expected, study_counts):
                diff = (
                    int(np.abs(expected - study_counts).max())
                    if expected.shape == study_counts.shape
                    else None
                )
                raise ValueError(
                    f"{d}: study-period crossings differ from {rel(observed[d])} counts_tracked "
                    f"(shape {study_counts.shape} vs {expected.shape}, largest difference {diff})"
                )
            check = {
                "observed": rel(observed[d]),
                "sha256": sha256_file(observed[d]),
                "equal": True,
            }
        summaries.append(
            {
                "date": d,
                "weekday": date.fromisoformat(d).strftime("%A"),
                "source": day.source_label(),
                "day_dir": rel(days[d]),
                "t0_unix": day.t0_unix,
                "data_hash": meta["data_hash"],
                "recording_t_s": [meta["t_min_s"], meta["t_max_s"]],
                "n_windows_unrecorded": int((~recorded).sum()),
                "coverage_artifact": {
                    "path": rel(coverages[d]),
                    "sha256": sha256_file(coverages[d]),
                    "rule": coverage_rule,
                },
                "study_period_tracked_crossings": {
                    station_id(x): int(n) for x, n in zip(sections, totals, strict=True)
                },
                "study_period_crossings_over_day_median": {
                    station_id(x): (float(n) / median if median > 0 else None)
                    for x, n in zip(sections, totals, strict=True)
                },
                "cross_check": check,
            }
        )
        print(
            f"{d} {summaries[-1]['weekday']}: {int(totals.sum())} tracked crossings at "
            f"{len(sections)} sections 06:30-08:30, coverage "
            f"{np.nanmin(coverage):.3f}-{np.nanmax(coverage):.3f}"
            + (", counts equal the observed side" if check else ""),
            flush=True,
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path, stations_path = out_dir / CSV_NAME, out_dir / STATIONS_NAME
    pd.concat(frames, ignore_index=True).to_csv(csv_path, index=False)
    stations_table(sections, inputs["ramps"]).to_csv(stations_path, index=False)
    summary = {
        "schema": SCHEMA,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": "scripts/i24_virtual_detectors.py",
        "argv": list(argv),
        "detectors": {"path": rel(csv_path), "sha256": sha256_file(csv_path)},
        "stations": {"path": rel(stations_path), "sha256": sha256_file(stations_path)},
        "sections_m": sections,
        "span_written": "06:00-10:00 CST, 5-min windows",
        "study_period": "06:30-08:30 CST",
        "columns": {
            "flow_veh_h": "tracked fragment crossings x12 / the day's recommended coverage [veh/h, 4 lanes]",
            "occupancy_pct": "time a tracked vehicle body covers the section, lane mean, / the same coverage [%]",
            "speed_ms": "mean speed of the crossings, interpolated at the section [m/s], not scaled",
            "n_crossings_tracked": "tracked fragment crossings (lower bound)",
            "flow_tracked_veh_h": "tracked crossings x12 [veh/h]",
            "occupancy_tracked_pct": "tracked occupancy [%]",
            "coverage": "the coverage artifact's pooled.recommended_filled for the containing 15-min window",
        },
        "unmeasured": "a window outside the recording's sample span (1 s slack) has every reading empty; "
        "a window without a coverage has no flow or occupancy",
        "days": summaries,
    }
    (out_dir / SUMMARY_NAME).write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    return summary


def main(argv: list[str] | None = None) -> int:
    """Build the detector CSV; returns the process exit code."""
    ap = argparse.ArgumentParser(
        prog="python scripts/i24_virtual_detectors.py", description=__doc__.split("\n", 1)[0]
    )
    ap.add_argument(
        "--day",
        action="append",
        default=[],
        required=True,
        metavar="DATE=DIR",
        help="a processed westbound morning (repeatable); its origin is 06:00 CST of DATE",
    )
    ap.add_argument(
        "--coverage",
        action="append",
        default=[],
        metavar="DATE=PATH",
        help="that day's coverage artifact (scripts/i24_coverage.py on the day), one per --day",
    )
    ap.add_argument(
        "--observed",
        action="append",
        default=[],
        metavar="DATE=PATH",
        help="cross-check: that day's observed side (counts_tracked must equal the crossings)",
    )
    ap.add_argument("--out-dir", required=True, type=Path)
    args = ap.parse_args(argv)
    try:
        days = parse_dated(args.day, "--day")
        coverages = parse_dated(args.coverage, "--coverage")
        observed = parse_dated(args.observed, "--observed")
        out_dir = args.out_dir if args.out_dir.is_absolute() else REPO_ROOT / args.out_dir
        build(
            days,
            coverages,
            observed,
            out_dir,
            argv=list(sys.argv[1:] if argv is None else argv),
        )
    except (OSError, ValueError, KeyError) as exc:
        print(f"i24_virtual_detectors: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {rel(out_dir / CSV_NAME)}, {STATIONS_NAME} and {SUMMARY_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
