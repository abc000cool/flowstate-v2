"""Candidate 2.2: is the scored speed target itself lane-coverage weighted?

``docs/MERGE_ROUND6_PLAN.md`` §2.2. The observed segment mean speed of
``scripts/i24_validate.py`` is the arithmetic mean of the recording's 5 Hz
speed samples in each (5-min window × 549 m segment) bin, pooled over mainline
lanes 1–4 (``_segment_speeds``; the lane column is not even read). Uniform
sampling makes that mean exactly Edie's speed of the *tracked* vehicles —
vehicle-distance over vehicle-time — so the pooling weight of a lane is its
**tracked** vehicle-time. The tracking rate is lane-dependent (lane 1 at
0.70–0.76, lane 3 at 0.40–0.54, ``docs/I24_DATA.md``) and the lanes differ by
20–30 km/h through the merge zone, so the target over-weights the
well-tracked lanes.

The alternative implemented here re-weights the per-lane Edie speeds by
**coverage-corrected** vehicle-time. Under the random-thinning model the
coverage estimators of ``calibration.coverage`` assume (each vehicle tracked
independently with probability ``c``), tracked vehicle-time in lane ``l`` is
``t_l = c_l · T_l`` and tracked vehicle-distance ``d_l = c_l · D_l``, so the
true pooled Edie speed of a bin is::

    v_true = Σ_l D_l / Σ_l T_l = Σ_l (t_l / c_l) · v_l / Σ_l (t_l / c_l)

with ``v_l = d_l / t_l`` the (coverage-robust) per-lane Edie speed. The
present target is the same expression with every ``c_l`` set equal; the two
differ only through the *relative* per-lane coverage, and are identical when
the lanes track alike or run alike.

What the script does:

1. **Rebuilds the observed field lane by lane** from the recording (one
   streaming pass over the Parquet, four columns, batch-accumulated) and
   checks that re-pooling the lanes reproduces the committed unweighted field
   of ``artifacts/i24_validation_observed.json`` bit-for-bit up to
   floating-point summation order.
2. **Validates the weighting on synthetic lanes** whose truth is known:
   lane speeds, lane vehicle-time shares and lane-dependent tracking rates are
   set, the population is thinned per vehicle, and both targets are built from
   the thinned samples through the same code path. The weighted target must
   recover the untinned population's pooled speed; the unweighted one is
   biased by an amount the script reports.
3. **Re-scores the four committed 20-seed arms** (read-only) against both
   targets, reusing each artifact's stored replicate-mean simulated field:
   the 5-min RMSPE row (which must reproduce the committed value to the digit)
   and the 15-min row of ``docs/I24_VALIDATION.md`` §0.5(a).
4. Applies §2.2's decider: material only if the fitted arm's RMSPE moves by
   more than that arm's replicate-noise floor (§0.6).

Writes ``artifacts/i24_validation_weighted_target.json``. Modifies nothing.
Run from the repo root::

    uv run --no-sync python scripts/i24_weighted_target.py
    uv run --no-sync python scripts/i24_weighted_target.py --no-recording  # synthetic + re-score only
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_build_replica import T_STUDY_HI_S, T_STUDY_LO_S, WINDOW_S
from i24_data import REPO_ROOT, WB_DIR, data_hash

from validation.metrics import rmspe

#: Mainline lanes of the observed side (1 = leftmost/HOV); the auxiliary lane
#: 5 is outside ``load_mainline`` and has no coverage estimate (plan §2.3).
LANES: tuple[int, ...] = (1, 2, 3, 4)
N_SEGMENTS = 10
SAMPLE_DT_S = 0.2

OUT = REPO_ROOT / "artifacts" / "i24_validation_weighted_target.json"
COVERAGE_ARTIFACT = REPO_ROOT / "artifacts" / "i24_coverage.json"
OBSERVED_ARTIFACT = REPO_ROOT / "artifacts" / "i24_validation_observed.json"
INPUTS = REPO_ROOT / "artifacts" / "i24_replica_inputs.json"
TRAJECTORIES = WB_DIR / "trajectories.parquet"

#: Arms re-scored, with the replicate-noise floor of docs/I24_VALIDATION.md §0.6
#: (the zip batteries measure 11.4–15.6%, the canonical heavy arm 16.8%; the
#: four canonical arms below did not record their own floor).
ARMS = ("tracked", "corrected", "speedcal", "ramps")
FITTED_ARM = "speedcal"

#: The 15-min RMSPE of each arm as published in docs/I24_VALIDATION.md §0.5(a),
#: used to check that this script's aggregation convention is that table's.
PUBLISHED_15MIN = {"tracked": 1.528, "corrected": 0.271, "speedcal": 0.263, "ramps": 0.255}
FLOOR_RANGE_POINTS = (11.4, 15.6)
FLOOR_HEAVY_POINTS = 16.8

#: Per-lane coverage estimator used for the weights. ``gap_mixture`` is the
#: per-lane maximum-likelihood thinning probability over the span — the share
#: of *vehicle-time* tracked, which is what these weights multiply, and the
#: estimator docs/I24_DATA.md quotes per lane. ``section_gap_mixture`` and the
#: artifact's ``recommended`` rescale it to the crossing count at x = 200 m,
#: which is the demand (flow) quantity, not the vehicle-time share; they are
#: carried as sensitivity rows.
PRIMARY_ESTIMATOR = "gap_mixture"
SENSITIVITY_ESTIMATORS = ("recommended", "gap_mixture_local", "equilibrium")


# --------------------------------------------------------------------------
# The estimator
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LaneTables:
    """Per-(window, segment, lane) tracked sample count and speed sum [m/s].

    ``counts`` is tracked vehicle-time in units of the sampling interval and
    ``sums`` is tracked vehicle-distance in the same units, so ``sums/counts``
    is the lane's Edie speed in the bin.
    """

    counts: np.ndarray  # (n_win, n_seg, n_lane), float
    sums: np.ndarray  # (n_win, n_seg, n_lane), float
    lanes: tuple[int, ...]
    window_s: float
    segment_m: float

    @property
    def shape(self) -> tuple[int, int, int]:
        return tuple(self.counts.shape)  # type: ignore[return-value]

    def __add__(self, other: LaneTables) -> LaneTables:
        return LaneTables(
            self.counts + other.counts,
            self.sums + other.sums,
            self.lanes,
            self.window_s,
            self.segment_m,
        )


def empty_tables(
    n_win: int, span_hi: float, lanes: tuple[int, ...] = LANES, n_segments: int = N_SEGMENTS
) -> LaneTables:
    """Zeroed tables for ``n_win`` windows on ``[0, span_hi)``."""
    shape = (n_win, n_segments, len(lanes))
    return LaneTables(np.zeros(shape), np.zeros(shape), lanes, WINDOW_S, span_hi / n_segments)


def accumulate(
    tab: LaneTables,
    t: np.ndarray,
    x: np.ndarray,
    v: np.ndarray,
    lane: np.ndarray,
) -> None:
    """Add one chunk of samples to ``tab`` in place (bins of ``_segment_speeds``).

    The binning is exactly ``scripts/i24_validate.py::_segment_speeds``:
    window ``t // WINDOW_S`` on ``t ∈ [0, n_win·WINDOW_S)``, segment
    ``min(x // segment_m, n_segments-1)`` on ``x ∈ [0, span_hi)``, with ``t``
    already shifted to seconds since the start of the study period.
    """
    n_win, n_seg, _ = tab.counts.shape
    span_hi = tab.segment_m * n_seg
    lanes_arr = np.asarray(tab.lanes, dtype=np.int64)
    if np.any(np.diff(lanes_arr) <= 0):
        raise ValueError(f"lanes must be strictly increasing, got {tab.lanes}")
    ok = (t >= 0.0) & (t < n_win * tab.window_s) & (x >= 0.0) & (x < span_hi)
    ok &= np.isin(lane, lanes_arr)
    if not ok.any():
        return
    wi = (t[ok] // tab.window_s).astype(np.int64)
    si = np.minimum((x[ok] // tab.segment_m).astype(np.int64), n_seg - 1)
    li = np.searchsorted(lanes_arr, lane[ok])
    np.add.at(tab.counts, (wi, si, li), 1.0)
    np.add.at(tab.sums, (wi, si, li), v[ok])


def lane_tables_from_frame(
    df: pd.DataFrame,
    span_hi: float,
    n_win: int,
    lanes: tuple[int, ...] = LANES,
    n_segments: int = N_SEGMENTS,
) -> LaneTables:
    """Per-lane tables from a ``t, x, v, lane`` frame (``t`` since study start)."""
    tab = empty_tables(n_win, span_hi, lanes, n_segments)
    accumulate(
        tab,
        df["t"].to_numpy(dtype=np.float64),
        df["x"].to_numpy(dtype=np.float64),
        df["v"].to_numpy(dtype=np.float64),
        df["lane"].to_numpy(dtype=np.int64),
    )
    return tab


def pooled_field(tab: LaneTables) -> np.ndarray:
    """The present target: lanes pooled by **tracked** vehicle-time [m/s].

    Identical to ``scripts/i24_validate.py::_segment_speeds`` up to the order
    of the floating-point summation.
    """
    cnt = tab.counts.sum(axis=2)
    out = np.full(cnt.shape, np.nan)
    np.divide(tab.sums.sum(axis=2), cnt, out=out, where=cnt > 0)
    return out


def weighted_field(tab: LaneTables, coverage: np.ndarray) -> np.ndarray:
    """The alternative: lanes pooled by **coverage-corrected** vehicle-time [m/s].

    Args:
        tab: Per-lane tables.
        coverage: ``(n_win, n_lane)`` tracking rates in ``(0, 1]``, aligned
            with ``tab.lanes``.

    Returns:
        ``(n_win, n_seg)`` field; NaN where no lane has a tracked sample.
    """
    cov = np.asarray(coverage, dtype=np.float64)
    if cov.shape != (tab.counts.shape[0], tab.counts.shape[2]):
        raise ValueError(f"coverage shape {cov.shape} != {(tab.counts.shape[0], len(tab.lanes))}")
    if not np.all(np.isfinite(cov)) or np.any(cov <= 0.0) or np.any(cov > 1.0):
        raise ValueError("coverage must be finite and in (0, 1]")
    w = cov[:, None, :]
    num = (tab.sums / w).sum(axis=2)
    den = (tab.counts / w).sum(axis=2)
    out = np.full(den.shape, np.nan)
    np.divide(num, den, out=out, where=den > 0)
    return out


def lane_time_shares(tab: LaneTables, coverage: np.ndarray | None = None) -> np.ndarray:
    """Per-(window, segment) lane share of vehicle-time, tracked or corrected."""
    w = 1.0 if coverage is None else np.asarray(coverage, dtype=np.float64)[:, None, :]
    c = tab.counts / w
    tot = c.sum(axis=2, keepdims=True)
    return np.divide(c, tot, out=np.full(c.shape, np.nan), where=tot > 0)


# --------------------------------------------------------------------------
# Per-lane coverage from the committed artifact
# --------------------------------------------------------------------------


def lane_coverage(
    estimator: str = PRIMARY_ESTIMATOR,
    n_win: int = 24,
    t_lo: float = T_STUDY_LO_S,
    lanes: tuple[int, ...] = LANES,
) -> tuple[np.ndarray, str]:
    """``(n_win, n_lane)`` per-lane tracking rate from ``artifacts/i24_coverage.json``.

    The artifact estimates one value per lane per 15-min window; each 5-min
    window inherits the 15-min window containing its start, the rule
    ``scripts/i24_validate.py::_recommended_coverage`` already uses for the
    pooled value.
    """
    cov = json.loads(COVERAGE_ARTIFACT.read_text())
    win_s = float(cov["parameters"]["window_s"])
    rows = sorted(cov["windows"], key=lambda w: float(w["t_lo_s"]))
    out = np.empty((n_win, len(lanes)))
    for i in range(n_win):
        t = t_lo + i * WINDOW_S
        row = next((w for w in rows if float(w["t_lo_s"]) <= t < float(w["t_lo_s"]) + win_s), None)
        if row is None:
            raise ValueError(f"no coverage window contains t={t}")
        by_lane = {int(entry["lane"]): entry["estimators"] for entry in row["lanes"]}
        for j, ln in enumerate(lanes):
            if ln not in by_lane:
                raise ValueError(f"coverage artifact has no lane {ln} in window {row['window']}")
            val = by_lane[ln].get(estimator)
            if val is None:
                raise ValueError(f"lane {ln}, window {row['window']}: {estimator} is null")
            out[i, j] = float(val)
    src = (
        f"{COVERAGE_ARTIFACT.relative_to(REPO_ROOT)}: windows[].lanes[].estimators.{estimator} "
        f"({cov['estimator_definitions'][estimator]}) per {win_s:g} s window, inherited by the "
        f"{WINDOW_S:g} s windows starting inside it"
    )
    return out, src


# --------------------------------------------------------------------------
# The recording, lane by lane (one streaming pass)
# --------------------------------------------------------------------------


def span() -> tuple[float, float]:
    """Measured span in data x [m] (``artifacts/i24_replica_inputs.json``)."""
    lo, hi = json.loads(INPUTS.read_text())["geometry"]["measured_span_data_x_m"]
    return float(lo), float(hi)


def observed_lane_tables(
    t_lo: float = T_STUDY_LO_S,
    t_hi: float = T_STUDY_HI_S,
    lanes: tuple[int, ...] = LANES,
    batch_rows: int = 2_000_000,
) -> LaneTables:
    """Per-lane tables of the recording over the study period / measured span.

    Streams the Parquet in batches (four columns, filters pushed down) and
    accumulates; the whole window is ~2·10⁷ samples and is never materialised.
    """
    _, span_hi = span()
    n_win = round((t_hi - t_lo) / WINDOW_S)
    tab = empty_tables(n_win, span_hi, lanes)
    dataset = ds.dataset(TRAJECTORIES, format="parquet")
    flt = (
        (ds.field("t") >= t_lo)
        & (ds.field("t") < t_hi)
        & (ds.field("x") >= 0.0)
        & (ds.field("x") < span_hi)
        & (ds.field("lane") >= min(lanes))
        & (ds.field("lane") <= max(lanes))
    )
    scanner = dataset.scanner(columns=["t", "x", "v", "lane"], filter=flt, batch_size=batch_rows)
    n_rows = 0
    for batch in scanner.to_batches():
        if batch.num_rows == 0:
            continue
        n_rows += batch.num_rows
        accumulate(
            tab,
            batch.column("t").to_numpy(zero_copy_only=False) - t_lo,
            batch.column("x").to_numpy(zero_copy_only=False),
            batch.column("v").to_numpy(zero_copy_only=False),
            batch.column("lane").to_numpy(zero_copy_only=False).astype(np.int64),
        )
    print(f"observed lane tables: {n_rows:,} samples in {n_win} windows", flush=True)
    return tab


# --------------------------------------------------------------------------
# Synthetic validation: lanes whose truth is known
# --------------------------------------------------------------------------


def synthetic_samples(
    lane_speeds_ms: dict[int, float],
    lane_vehicles: dict[int, int],
    coverage: dict[int, float],
    *,
    span_hi: float,
    n_win: int,
    rng: np.random.Generator | None = None,
    speed_cv: float = 0.0,
    deterministic_thinning: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A synthetic multi-lane field with known lane speeds and tracking rates.

    Each lane's vehicles enter uniformly over the study period and cross the
    span at the lane speed (optionally with a per-vehicle lognormal spread of
    coefficient of variation ``speed_cv``), sampled at 5 Hz as the recording
    is. Tracking is **per vehicle** — the random-thinning model the coverage
    estimators assume — so a tracked vehicle contributes all of its samples.

    Returns:
        ``(truth, tracked)`` sample frames with columns ``t, x, v, lane``;
        ``truth`` is the whole population, ``tracked`` the thinned subset.
    """
    rng = np.random.default_rng(0) if rng is None else rng
    t_span = n_win * WINDOW_S
    rows_t, rows_x, rows_v, rows_lane, rows_keep = [], [], [], [], []
    for lane, n_veh in lane_vehicles.items():
        v0 = lane_speeds_ms[lane]
        c = coverage[lane]
        if deterministic_thinning:
            keep_flags = np.zeros(n_veh, dtype=bool)
            keep_flags[: round(c * n_veh)] = True
        else:
            keep_flags = rng.random(n_veh) < c
        speeds = (
            np.full(n_veh, v0)
            if speed_cv == 0.0
            else v0 * rng.lognormal(-0.5 * speed_cv**2, speed_cv, n_veh)
        )
        t_enter = rng.uniform(-span_hi / speeds.min(), t_span, n_veh)
        for veh in range(n_veh):
            v = speeds[veh]
            n_s = int(np.floor(span_hi / v / SAMPLE_DT_S))
            if n_s <= 0:
                continue
            t = t_enter[veh] + np.arange(n_s) * SAMPLE_DT_S
            x = (t - t_enter[veh]) * v
            rows_t.append(t)
            rows_x.append(x)
            rows_v.append(np.full(n_s, v))
            rows_lane.append(np.full(n_s, lane))
            rows_keep.append(np.full(n_s, keep_flags[veh]))
    truth = pd.DataFrame(
        {
            "t": np.concatenate(rows_t),
            "x": np.concatenate(rows_x),
            "v": np.concatenate(rows_v),
            "lane": np.concatenate(rows_lane),
        }
    )
    keep = np.concatenate(rows_keep)
    return truth, truth.loc[keep].reset_index(drop=True)


def synthetic_case(
    name: str,
    lane_speeds_kmh: dict[int, float],
    lane_vehicles: dict[int, int],
    coverage: dict[int, float],
    *,
    n_win: int = 4,
    span_hi: float = 2000.0,
    seed: int = 0,
    speed_cv: float = 0.0,
    deterministic_thinning: bool = False,
) -> dict:
    """Run one synthetic case through the real construction and score both targets.

    The truth is the pooled Edie speed of the *untinned* population, built with
    the same binning; the tracked subset is pooled both ways. Errors are
    reported on the bin field (RMS and mean signed) and on the study-period
    pooled mean.
    """
    lanes = tuple(sorted(lane_vehicles))
    speeds_ms = {ln: kmh / 3.6 for ln, kmh in lane_speeds_kmh.items()}
    truth_df, tracked_df = synthetic_samples(
        speeds_ms,
        lane_vehicles,
        coverage,
        span_hi=span_hi,
        n_win=n_win,
        rng=np.random.default_rng(seed),
        speed_cv=speed_cv,
        deterministic_thinning=deterministic_thinning,
    )
    n_seg = 2
    truth_tab = lane_tables_from_frame(truth_df, span_hi, n_win, lanes, n_seg)
    tab = lane_tables_from_frame(tracked_df, span_hi, n_win, lanes, n_seg)
    cov = np.array([[coverage[ln] for ln in lanes]] * n_win)
    truth = pooled_field(truth_tab)
    unw = pooled_field(tab)
    wgt = weighted_field(tab, cov)
    ok = np.isfinite(truth) & np.isfinite(unw) & np.isfinite(wgt)

    def _err(field: np.ndarray) -> dict:
        d = (field[ok] - truth[ok]) * 3.6
        return {
            "rms_error_kmh": round(float(np.sqrt(np.mean(d**2))), 4),
            "mean_signed_error_kmh": round(float(np.mean(d)), 4),
            "rmspe_vs_truth": round(float(rmspe(field[ok], truth[ok])), 6),
        }

    def _pooled(tb: LaneTables, cv: np.ndarray | None) -> float:
        w = 1.0 if cv is None else cv[:, None, :]
        return float((tb.sums / w).sum() / (tb.counts / w).sum() * 3.6)

    return {
        "case": name,
        "lane_speeds_kmh": lane_speeds_kmh,
        "lane_vehicles": lane_vehicles,
        "coverage": coverage,
        "speed_cv": speed_cv,
        "seed": seed,
        "deterministic_thinning": deterministic_thinning,
        "n_bins": int(ok.sum()),
        "true_pooled_kmh": round(_pooled(truth_tab, None), 4),
        "unweighted_pooled_kmh": round(_pooled(tab, None), 4),
        "weighted_pooled_kmh": round(_pooled(tab, cov), 4),
        "unweighted": _err(unw),
        "weighted": _err(wgt),
    }


def analytic_case(
    name: str,
    lane_speeds_kmh: dict[int, float],
    lane_tracked_time_shares: dict[int, float],
    coverage: dict[int, float],
) -> dict:
    """The bias of the present target, in closed form, on one bin.

    Inputs are what the recording shows — the lane's **tracked** vehicle-time
    share and its (coverage-robust) speed — plus the lane's tracking rate. The
    true vehicle-time share is then ``s_l / c_l`` renormalised, so the truth is
    ``Σ (s_l/c_l) v_l / Σ (s_l/c_l)``: the weighted target reproduces it by
    construction. This case measures the size of the bias; the Monte-Carlo
    cases below are what validates the construction end to end.
    """
    lanes = tuple(sorted(lane_speeds_kmh))
    v = np.array([lane_speeds_kmh[ln] / 3.6 for ln in lanes])
    s = np.array([lane_tracked_time_shares[ln] for ln in lanes])
    c = np.array([coverage[ln] for ln in lanes])
    tab = LaneTables(
        s.reshape(1, 1, -1).copy(), (s * v).reshape(1, 1, -1).copy(), lanes, WINDOW_S, 1.0
    )
    cov = c.reshape(1, -1)
    truth = float((s / c) @ v / (s / c).sum())
    unw = float(pooled_field(tab)[0, 0])
    wgt = float(weighted_field(tab, cov)[0, 0])
    return {
        "case": name,
        "kind": "analytic",
        "lane_speeds_kmh": lane_speeds_kmh,
        "lane_tracked_time_shares": lane_tracked_time_shares,
        "coverage": coverage,
        "true_lane_time_shares": {
            str(ln): round(float(x), 4)
            for ln, x in zip(lanes, (s / c) / (s / c).sum(), strict=True)
        },
        "true_pooled_kmh": round(truth * 3.6, 4),
        "unweighted_pooled_kmh": round(unw * 3.6, 4),
        "weighted_pooled_kmh": round(wgt * 3.6, 4),
        "unweighted_bias_kmh": round((unw - truth) * 3.6, 4),
        "unweighted_bias_percent": round((unw - truth) / truth * 100.0, 4),
        "weighted_bias_kmh": round((wgt - truth) * 3.6, 6),
    }


def synthetic_validation() -> list[dict]:
    """The cases published with the estimator.

    Lane speeds and the coverage spread are the corridor's own: the observed
    merge-zone lane profile (33 / 30 / 27 / 24 km/h at 1.0–1.5 km,
    ``artifacts/i24_lane_profile_zip.json``) and the per-lane ``gap_mixture``
    rates of ``docs/I24_DATA.md`` (0.73 / 0.55 / 0.47 / 0.56).
    """
    merge = {1: 33.0, 2: 30.0, 3: 27.0, 4: 24.0}
    # Mid-points of the per-lane gap_mixture ranges over the study period
    # (artifacts/i24_coverage.json: 0.699-0.760, 0.504-0.583, 0.403-0.540,
    # 0.491-0.634; docs/I24_DATA.md quotes lanes 1 and 3).
    cov = {1: 0.73, 2: 0.54, 3: 0.47, 4: 0.56}
    equal_cov = {ln: 0.60 for ln in cov}
    flat = {ln: 30.0 for ln in merge}
    veh = {1: 400, 2: 400, 3: 400, 4: 400}
    cases: list[dict] = [
        # The corridor's own numbers: observed tracked vehicle-time shares and
        # lane speeds at data x = 1,500 m (artifacts/i24_lane_profile.json,
        # observed rows, lanes 1-4 renormalised) with the per-lane gap_mixture
        # rates of docs/I24_DATA.md.
        analytic_case(
            "corridor_x1500_observed_lanes",
            {1: 33.26, 2: 29.77, 3: 29.47, 4: 19.25},
            {1: 0.2688, 2: 0.2387, 3: 0.1675, 4: 0.3250},
            cov,
        ),
        analytic_case("analytic_equal_tracked_shares", merge, {ln: 0.25 for ln in merge}, cov),
        analytic_case("analytic_null_equal_coverage", merge, {ln: 0.25 for ln in merge}, equal_cov),
    ]
    cases += [
        synthetic_case(
            "merge_zone_lane_speeds_deterministic_thinning",
            merge,
            veh,
            cov,
            deterministic_thinning=True,
        ),
        synthetic_case("merge_zone_lane_speeds_random_thinning", merge, veh, cov, seed=11),
        synthetic_case(
            "merge_zone_with_per_vehicle_speed_spread",
            merge,
            veh,
            cov,
            seed=12,
            speed_cv=0.20,
        ),
        synthetic_case(
            "uneven_lane_occupancy",
            merge,
            {1: 250, 2: 350, 3: 450, 4: 700},
            cov,
            seed=13,
            speed_cv=0.20,
        ),
        synthetic_case("null_equal_coverage", merge, veh, equal_cov, seed=14, speed_cv=0.20),
        synthetic_case("null_equal_lane_speeds", flat, veh, cov, seed=15, speed_cv=0.20),
        synthetic_case(
            "strong_coverage_contrast",
            merge,
            veh,
            {1: 0.90, 2: 0.60, 3: 0.40, 4: 0.30},
            seed=16,
            speed_cv=0.20,
        ),
    ]
    # Repeated-seed summaries: is the weighted target unbiased, or only quieter?
    cases.append(repeat_summary("merge_zone_random_thinning", merge, veh, cov, speed_cv=0.20))
    cases.append(
        repeat_summary(
            "strong_coverage_contrast",
            merge,
            veh,
            {1: 0.90, 2: 0.60, 3: 0.40, 4: 0.30},
            speed_cv=0.20,
        )
    )
    return cases


def repeat_summary(
    name: str,
    lane_speeds_kmh: dict[int, float],
    lane_vehicles: dict[int, int],
    coverage: dict[int, float],
    *,
    n_seeds: int = 20,
    seed0: int = 100,
    speed_cv: float = 0.0,
) -> dict:
    """Mean ± sd of each target's error over ``n_seeds`` independent thinnings.

    An estimator that is merely quieter shows a mean error far from zero; an
    unbiased one shows a mean within a standard error of zero.
    """
    reps = [
        synthetic_case(name, lane_speeds_kmh, lane_vehicles, coverage, seed=s, speed_cv=speed_cv)
        for s in range(seed0, seed0 + n_seeds)
    ]

    def _stat(key: str) -> dict:
        e = np.array([r[key]["mean_signed_error_kmh"] for r in reps])
        sd = float(np.std(e, ddof=1))
        return {
            "mean_signed_error_kmh": round(float(e.mean()), 4),
            "sd_signed_error_kmh": round(sd, 4),
            "standard_error_kmh": round(sd / np.sqrt(len(e)), 4),
            "t_statistic_vs_zero": round(float(e.mean() / (sd / np.sqrt(len(e)))), 3),
        }

    return {
        "case": f"{name}_{n_seeds}_seeds",
        "kind": "monte_carlo_repeats",
        "lane_speeds_kmh": lane_speeds_kmh,
        "lane_vehicles": lane_vehicles,
        "coverage": coverage,
        "speed_cv": speed_cv,
        "n_seeds": n_seeds,
        "true_pooled_kmh": round(float(np.mean([r["true_pooled_kmh"] for r in reps])), 4),
        "unweighted": _stat("unweighted"),
        "weighted": _stat("weighted"),
    }


# --------------------------------------------------------------------------
# Re-scoring the committed arms
# --------------------------------------------------------------------------


def aggregate_windows(field: np.ndarray, k: int) -> np.ndarray:
    """Mean of ``k`` consecutive 5-min bins per segment (NaN-aware).

    The convention of the aggregation table of ``docs/I24_VALIDATION.md``
    §0.5(a): both sides are averaged over the coarser window, then compared.
    """
    n = field.shape[0] // k
    with np.errstate(invalid="ignore"):
        return np.array([np.nanmean(field[i * k : (i + 1) * k], axis=0) for i in range(n)])


def score_arm(arm: str, obs_unweighted: np.ndarray, obs_weighted: np.ndarray) -> dict:
    """Both RMSPE rows of one committed arm, from its stored simulated field."""
    path = REPO_ROOT / "artifacts" / f"i24_validation_{arm}.json"
    d = json.loads(path.read_text())
    sim = np.asarray(d["simulated"]["segment_speeds_ms_mean"], dtype=np.float64)
    stored_obs = np.asarray(d["observed"]["segment_speeds_ms"], dtype=np.float64)
    same = np.array_equal(
        np.nan_to_num(stored_obs, nan=-1.0), np.nan_to_num(obs_unweighted, nan=-1.0)
    )

    def _row(obs: np.ndarray) -> dict:
        both = np.isfinite(sim) & np.isfinite(obs)
        five = float(rmspe(sim[both], obs[both]))
        s15, o15 = aggregate_windows(sim, 3), aggregate_windows(obs, 3)
        b15 = np.isfinite(s15) & np.isfinite(o15)
        return {
            "rmspe_5min": five,
            "n_bins_5min": int(both.sum()),
            "rmspe_15min": float(rmspe(s15[b15], o15[b15])),
            "n_bins_15min": int(b15.sum()),
        }

    unw, wgt = _row(obs_unweighted), _row(obs_weighted)
    stored = float(d["rmspe"]["value"])
    return {
        "arm": arm,
        "scenario": d["scenario"],
        "config_hash": d["config_hash"],
        "replicates": int(d["replicates"]),
        "artifact": str(path.relative_to(REPO_ROOT)),
        "artifact_created_at": d.get("created_at"),
        "observed_side_matches_committed": bool(same),
        "stored_rmspe_5min": stored,
        "unweighted_target": unw,
        "weighted_target": wgt,
        "reproduces_committed_5min": bool(unw["rmspe_5min"] == stored),
        "published_15min_0_5a": PUBLISHED_15MIN.get(arm),
        "reproduces_published_15min": (
            None
            if arm not in PUBLISHED_15MIN
            # §0.5(a) publishes the row to 0.1 point, so agreement is checked there.
            else bool(abs(unw["rmspe_15min"] - PUBLISHED_15MIN[arm]) < 1e-3)
        ),
        "delta_points_5min": round((wgt["rmspe_5min"] - unw["rmspe_5min"]) * 100.0, 4),
        "delta_points_15min": round((wgt["rmspe_15min"] - unw["rmspe_15min"]) * 100.0, 4),
    }


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover - provenance only
        return "unknown"


def _json_safe(o):
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if not np.isfinite(f) else f
    if isinstance(o, (np.integer, int)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--no-recording",
        action="store_true",
        help="skip the Parquet pass (synthetic validation only; writes nothing)",
    )
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    print("synthetic validation ...", flush=True)
    synth = synthetic_validation()
    for c in synth:
        if c.get("kind") == "analytic":
            err_u, err_w = c["unweighted_bias_kmh"], c["weighted_bias_kmh"]
        elif "rms_error_kmh" in c.get("unweighted", {}):
            err_u = c["unweighted"]["mean_signed_error_kmh"]
            err_w = c["weighted"]["mean_signed_error_kmh"]
        else:
            continue
        print(
            f"  {c['case']:<48} truth {c['true_pooled_kmh']:6.2f} km/h | "
            f"unweighted {err_u:+6.2f} | weighted {err_w:+6.2f} km/h",
            flush=True,
        )
    if args.no_recording:
        return

    obs_art = json.loads(OBSERVED_ARTIFACT.read_text())
    obs_committed = np.asarray(obs_art["segment_speeds_ms"], dtype=np.float64)
    n_win = int(obs_art["n_windows"])
    span_lo, span_hi = span()

    print("rebuilding the observed field lane by lane ...", flush=True)
    tab = observed_lane_tables()
    rebuilt = pooled_field(tab)
    diff = np.abs(rebuilt - obs_committed)
    finite_match = bool(np.array_equal(np.isfinite(rebuilt), np.isfinite(obs_committed)))
    max_abs = float(np.nanmax(diff)) if np.isfinite(diff).any() else float("nan")
    print(
        f"  reconstruction vs committed field: max |Δ| = {max_abs:.3e} m/s, "
        f"NaN pattern identical = {finite_match}",
        flush=True,
    )
    if not finite_match or max_abs > 1e-9:
        raise SystemExit(
            "the lane decomposition does not reproduce the committed observed field; stopping"
        )

    cov_primary, cov_src = lane_coverage(PRIMARY_ESTIMATOR, n_win)
    obs_weighted = weighted_field(tab, cov_primary)
    variants = {}
    for est in SENSITIVITY_ESTIMATORS:
        cov_e, src_e = lane_coverage(est, n_win)
        f = weighted_field(tab, cov_e)
        variants[est] = {
            "source": src_e,
            "coverage_per_window": cov_e.round(4).tolist(),
            "segment_means_kmh": (np.nanmean(f, axis=0) * 3.6).round(2).tolist(),
            "field_ms": f.round(6).tolist(),
        }

    print("re-scoring the committed arms ...", flush=True)
    arms = [score_arm(a, obs_committed, obs_weighted) for a in ARMS]
    for a in arms:
        if not a["reproduces_committed_5min"]:
            raise SystemExit(
                f"{a['arm']}: recomputed 5-min RMSPE {a['unweighted_target']['rmspe_5min']!r} "
                f"does not reproduce the committed {a['stored_rmspe_5min']!r}; stopping"
            )
        for est, var in variants.items():
            row = score_arm(a["arm"], obs_committed, np.asarray(var["field_ms"], dtype=np.float64))
            a.setdefault("sensitivity", {})[est] = {
                "rmspe_5min": row["weighted_target"]["rmspe_5min"],
                "rmspe_15min": row["weighted_target"]["rmspe_15min"],
                "delta_points_5min": row["delta_points_5min"],
            }

    fitted = next(a for a in arms if a["arm"] == FITTED_ARM)
    move = abs(fitted["delta_points_5min"])
    material = move > FLOOR_RANGE_POINTS[0]
    with np.errstate(invalid="ignore"):
        seg_unw = (np.nanmean(obs_committed, axis=0) * 3.6).round(2)
        seg_wgt = (np.nanmean(obs_weighted, axis=0) * 3.6).round(2)
        share_tracked = np.nanmean(lane_time_shares(tab), axis=0)
        share_corr = np.nanmean(lane_time_shares(tab, cov_primary), axis=0)
    ok_bins = np.isfinite(obs_committed) & np.isfinite(obs_weighted)
    d_bin = (obs_weighted[ok_bins] - obs_committed[ok_bins]) * 3.6
    target_shift = {
        "n_bins": int(ok_bins.sum()),
        "rms_shift_kmh": round(float(np.sqrt(np.mean(d_bin**2))), 4),
        "mean_signed_shift_kmh": round(float(d_bin.mean()), 4),
        "max_abs_shift_kmh": round(float(np.abs(d_bin).max()), 4),
        "rmspe_weighted_vs_unweighted": round(
            float(rmspe(obs_weighted[ok_bins], obs_committed[ok_bins])), 6
        ),
        "note": "how far the target itself moves, per 5-min x 549 m bin; the last row is on the same scale as the arms' RMSPE rows",
    }

    out = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "script": "scripts/i24_weighted_target.py",
        "git_head": _git_head(),
        "candidate": "docs/MERGE_ROUND6_PLAN.md §2.2 — the scored target is itself lane-coverage weighted",
        "data_hash": data_hash(),
        "observed_artifact": str(OBSERVED_ARTIFACT.relative_to(REPO_ROOT)),
        "observed_artifact_data_hash": obs_art["data_hash"],
        "versions": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "pyarrow": __import__("pyarrow").__version__,
        },
        "parameters": {
            "period": obs_art["period"],
            "t_range_s": [T_STUDY_LO_S, T_STUDY_HI_S],
            "span_data_x_m": [span_lo, span_hi],
            "window_s": WINDOW_S,
            "n_windows": n_win,
            "n_segments": N_SEGMENTS,
            "segment_m": span_hi / N_SEGMENTS,
            "lanes": list(LANES),
            "sample_dt_s": SAMPLE_DT_S,
            "primary_estimator": PRIMARY_ESTIMATOR,
            "primary_estimator_source": cov_src,
            "sensitivity_estimators": list(SENSITIVITY_ESTIMATORS),
        },
        "definitions": {
            "unweighted_target": "v = Σ_l Σ_samples v / Σ_l n_samples over mainline lanes 1-4 in the bin — exactly scripts/i24_validate.py::_segment_speeds; the lane weight is TRACKED vehicle-time",
            "weighted_target": "v = Σ_l (t_l/c_l)·v_l / Σ_l (t_l/c_l) with t_l the lane's tracked vehicle-time, v_l its Edie speed and c_l its tracking rate; the lane weight is coverage-corrected vehicle-time (the true pooled Edie speed under random thinning)",
            "rmspe_15min": "both fields averaged over 3 consecutive 5-min windows per segment, then compared (the aggregation table of docs/I24_VALIDATION.md §0.5(a))",
        },
        "reconstruction_check": {
            "max_abs_diff_ms": max_abs,
            "nan_pattern_identical": finite_match,
            "n_bins": int(np.isfinite(obs_committed).sum()),
            "note": "the per-lane decomposition re-pooled reproduces the committed observed field to floating-point summation order, so the weighted target differs from it only through the lane weights",
        },
        "lane_coverage_primary": {
            "estimator": PRIMARY_ESTIMATOR,
            "source": cov_src,
            "per_window": cov_primary.round(4).tolist(),
            "per_lane_range": {
                str(ln): [
                    round(float(cov_primary[:, j].min()), 4),
                    round(float(cov_primary[:, j].max()), 4),
                ]
                for j, ln in enumerate(LANES)
            },
        },
        "observed": {
            "segment_speeds_ms_unweighted": obs_committed.round(6).tolist(),
            "segment_speeds_ms_weighted": np.round(obs_weighted, 6).tolist(),
            "segment_means_kmh_unweighted": seg_unw.tolist(),
            "segment_means_kmh_weighted": seg_wgt.tolist(),
            "segment_means_shift_kmh": (seg_wgt - seg_unw).round(2).tolist(),
            "target_shift": target_shift,
            "lane_time_share_tracked_per_segment": share_tracked.round(4).tolist(),
            "lane_time_share_corrected_per_segment": share_corr.round(4).tolist(),
            "lane_counts_per_window_segment_lane": tab.counts.astype(np.int64).tolist(),
            "lane_speed_sums_ms_per_window_segment_lane": np.round(tab.sums, 4).tolist(),
        },
        "weighted_variants": variants,
        "synthetic_validation": synth,
        "arms": arms,
        "decision": {
            "decider": "docs/MERGE_ROUND6_PLAN.md §2.2: material only if the fitted arm's 5-min RMSPE moves by more than the replicate-noise floor (11.4-15.6 points for the zip batteries, 16.8 for the canonical heavy arm, §0.6)",
            "fitted_arm": FITTED_ARM,
            "rmspe_5min_unweighted": fitted["unweighted_target"]["rmspe_5min"],
            "rmspe_5min_weighted": fitted["weighted_target"]["rmspe_5min"],
            "move_points": move,
            "floor_points": [*FLOOR_RANGE_POINTS, FLOOR_HEAVY_POINTS],
            "material": material,
            "verdict": (
                "MATERIAL: the weighted target moves the fitted arm's row by more than the "
                "replicate-noise floor"
                if material
                else "NOT MATERIAL: the move is far inside the replicate-noise floor; the row "
                "stands as scored and candidate 2.2 closes"
            ),
        },
        "notes": [
            "Data only: no simulation was run and no committed artifact was modified; the "
            "simulated side of every row is each arm's own stored replicate-mean field.",
            "The criterion row keeps the unweighted target unless the weighted one is first shown "
            "correct on synthetic validation (plan §2.2); the synthetic block is that evidence.",
            "Coverage is available per lane per 15-min window only, so the weights do not vary "
            "along x; the true tracking rate does (overpasses, occlusion), which this cannot see.",
            "The auxiliary lane 5 is outside both targets: the observed side reads mainline lanes "
            "1-4 and no coverage has been estimated for lane 5 (plan §2.3).",
            "Both targets are biased the same way by anything that is not random per-vehicle "
            "thinning (correlated losses, fragment breaks); the weighting only removes the "
            "lane-to-lane part of it.",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(_json_safe(out), indent=2, allow_nan=False))
    print(f"\nwrote {args.out.relative_to(REPO_ROOT)}", flush=True)
    print(
        f"{'arm':<10} {'5-min unw':>10} {'5-min wgt':>10} {'Δ pts':>7} "
        f"{'15-min unw':>11} {'15-min wgt':>11} {'Δ pts':>7}",
        flush=True,
    )
    for a in arms:
        u, w = a["unweighted_target"], a["weighted_target"]
        print(
            f"{a['arm']:<10} {u['rmspe_5min']:>10.1%} {w['rmspe_5min']:>10.1%} "
            f"{a['delta_points_5min']:>+7.2f} {u['rmspe_15min']:>11.1%} "
            f"{w['rmspe_15min']:>11.1%} {a['delta_points_15min']:>+7.2f}"
            + ("" if a["reproduces_published_15min"] else "   [15-min differs from §0.5(a)]"),
            flush=True,
        )
    print(out["decision"]["verdict"], flush=True)


if __name__ == "__main__":
    main()
