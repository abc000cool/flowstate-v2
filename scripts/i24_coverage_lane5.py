"""Tracking coverage of the I-24 auxiliary lane 5 (docs/MERGE_ROUND6_PLAN.md §2.3).

The per-lane coverage table of ``artifacts/i24_coverage.json`` covers mainline
lanes 1–4. The Old Hickory acceleration lane (lane 5, data ``x`` ∈ [750, 1950) m
— the lane-5 occupancy range of ``docs/I24_VALIDATION.md`` §0.5(d)) has never
been estimated, yet ``scripts/i24_build_replica.py`` divides the **ramp-lane**
crossing count at ``x = 950`` m by the **mainline** coverage to build the
corrected Old Hickory inflow. This script asks whether the gap-mixture
estimator of ``scripts/i24_coverage.py`` can supply lane 5's own coverage.

**It cannot, and the artifact says so instead of publishing a number.** Three
of the estimator's assumptions fail on an auxiliary lane, each quantified in
``assumption_checks`` of ``artifacts/i24_coverage_lane5.json``:

1. **Flow is not conserved along the lane** (``flow_conservation``). The
   section estimator ``coverage_section_crossings`` (eq. S of
   ``calibration.coverage``) needs a ramp-free stretch on which the tracked
   Edie flow equals the crossing rate up to coverage. Lane 5 sheds its traffic
   into lane 4 over its whole length: tracked crossings fall from the gore to
   the taper by a factor measured here, with an exponential decay length also
   measured here. The section estimator therefore has no valid stretch on
   lane 5 and is not computed.

2. **Merge-out is a second thinning the estimator cannot tell from tracking
   loss** (``synthetic_validation_auxiliary_lane``). Vehicles leaving the
   auxiliary lane remove points from exactly the same point process that
   missed tracking removes them from; the geometric-gamma mixture (eq. G)
   identifies the *product* of the two, not the tracking coverage. The
   synthetic lane built here — a renewal arrival process at the gore, constant
   speed, exponential merge-out at the decay length measured from the
   recording — measures the resulting bias directly: with no merge-out and
   regular arrivals the estimator recovers a known ``c`` to a few hundredths,
   and adding the recording's merge-out drives it far below the truth.

3. **The observed spacings are too irregular for the model to admit any
   coverage** (``spacing_regularity``). The model implies
   ``cv_obs² = 1 − c (1 − cv_true²) ≤ 1`` for every ``c ∈ (0, 1]`` and every
   ``cv_true < 1`` (module derivation of ``calibration.coverage``), so an
   observed coefficient of variation above 1 falsifies it outright. Lane 5
   produces such classes; the count and the per-class values are recorded.

What the artifact does publish: the raw mixture output labelled as a
*confounded lower bound* (tracking coverage × merge survival), the valid
capacity lower bound ``c ≥ q_tracked / q_cap``, the mainline per-lane table
that any extrapolation would have to rest on, and the resulting admissible
interval for the Old Hickory ramp-demand correction with the verdict on the
plan's probe levels {0.75, 0.875, 1.0}.

Memory: one lane, four columns and the 1.2 km acceleration-lane window are
read once (~23 MB) and freed before the synthetic work.

Run: ``uv run --no-sync python scripts/i24_coverage_lane5.py``
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_data import REPO_ROOT, SAMPLE_DT_S, WB_DIR, clock, data_hash

from calibration.coverage import (
    DEFAULT_S_DUP_M,
    DEFAULT_S_MAX_M,
    HCM_BASIC_FREEWAY_CAPACITY_PC_H_LN,
    coverage_capacity_bound,
    coverage_gap_mixture,
    fit_gap_mixture,
    idm_equilibrium_density,
    snapshot_spacings,
)
from calibration.loaders.i24motion import load_i24_parquet
from flowstate_core.rng import make_rng
from flowstate_core.units import kmh_to_ms, ms_to_kmh, veh_s_to_veh_h

FloatArray = NDArray[np.float64]

# --------------------------------------------------------------------------
# Constants — every one traced to a committed artifact or a named source
# --------------------------------------------------------------------------

#: Auxiliary-lane index in the processed export (mainline is 1-4).
AUX_LANE = 5

#: Data-x span of the Old Hickory acceleration lane [m]: the lane-5 occupancy
#: range of docs/I24_VALIDATION.md §0.5(d) and docs/MERGE_ROUND6_PLAN.md §2.3.
SPAN_M = (750.0, 1950.0)

#: Study period [s since 06:00 CST]: scripts/i24_build_replica.py's
#: T_STUDY_LO_S/T_STUDY_HI_S, the period the mainline lane table uses
#: (artifacts/i24_coverage.json parameters.study_period_s).
STUDY_T_LO_S = 1800.0  # 06:30 CST
STUDY_T_HI_S = 9000.0  # 08:30 CST
WINDOW_S = 900.0

#: Section where scripts/i24_build_replica.py counts the Old Hickory ramp-lane
#: crossings that become the ramp inflow (RAMPS[0]["count_x_m"]).
RAMP_COUNT_X_M = 950.0
#: Short stretch around it on which a local Edie flow is formed for the
#: crossing-to-Edie ratio; deliberately short because lane-5 flow decays.
RAMP_LOCAL_X_RANGE_M = (900.0, 1000.0)

#: Estimator settings, identical to scripts/i24_coverage.py so the lane-5
#: numbers sit on the same footing as the mainline table.
SNAPSHOT_DT_S = 2.0
SPEED_EDGES_KMH = (0.0, 5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0)
V_MAX_KMH = 60.0
MIN_N_PER_CLASS = 300

#: Bins for the along-lane flow/density profile [m].
PROFILE_BIN_M = 100.0
#: Sub-bins for the within-speed-class spacing-scale check [m].
SCALE_BIN_M = 200.0
#: Speed class the scale check uses [km/h] (the best-populated congested class).
SCALE_CLASS_KMH = (20.0, 30.0)

MAINLINE_COVERAGE_ARTIFACT = "artifacts/i24_coverage.json"
REPLICA_INPUTS = "artifacts/i24_replica_inputs.json"
OUT_ARTIFACT = "artifacts/i24_coverage_lane5.json"

#: Fleet the committed builder inputs were produced with, and the one whose
#: equilibrium densities docs/I24_DATA.md §4's coverage table was computed
#: with before the capacity calibration (docs/I24_CAPACITY.md) replaced it.
FLEET_ARTIFACT = "artifacts/idm_i24_capacity.json"
LEGACY_FLEET_ARTIFACT = "artifacts/idm_i24.json"
VEHICLE_LENGTH_M = 5.0  # scripts/i24_build_replica.py::VEHICLE_LENGTH_M

#: Old Hickory demand multipliers the plan proposes to probe
#: (docs/MERGE_ROUND6_PLAN.md §2.3), applied on top of the coverage-corrected
#: ramp inflow; 0.75 is the level the §0.6 out-of-sample joint fit chose.
PROBE_LEVELS = (0.75, 0.875, 1.0)

#: Seed and size of the auxiliary-lane synthetic validation.
SYNTH_SEED = 20260917
SYNTH_SNAPSHOTS = 600
SYNTH_SPEED_MS = 11.1  # ≈ 40 km/h, the lane-5 study-period Edie speed
SYNTH_C_VALUES = (0.5, 0.75)
SYNTH_HEADWAY_CV = (0.35, 0.7, 1.0)


# --------------------------------------------------------------------------
# Helpers (unit-tested in tests/test_calibration/test_i24_coverage_lane5.py)
# --------------------------------------------------------------------------


def exponential_decay_length(x_m: FloatArray, q: FloatArray) -> dict[str, float]:
    """Decay length of a monotonically shed flow profile, ``q = q0 e^(−x/L)``.

    Log-linear least squares on the positive samples. ``L`` is the distance
    over which the auxiliary lane sheds a factor ``e`` of its flow into the
    mainline; a long ``L`` relative to the span means little shedding.

    Args:
        x_m: Bin centres along the lane [m], at least two distinct values.
        q: Tracked flow in each bin, same length; non-positive bins are
            dropped.

    Returns:
        Mapping with ``decay_length_m`` (``+inf`` for a flat profile,
        negative if the flow grows), ``q0_veh_h`` at ``x = 0`` and ``r2`` of
        the log-linear fit.

    Raises:
        ValueError: On mismatched lengths or fewer than two usable bins.
    """
    x = np.asarray(x_m, dtype=np.float64)
    y = np.asarray(q, dtype=np.float64)
    if x.shape != y.shape:
        raise ValueError("x_m and q must have the same shape")
    ok = np.isfinite(x) & np.isfinite(y) & (y > 0.0)
    if int(ok.sum()) < 2 or np.unique(x[ok]).size < 2:
        raise ValueError("need at least two bins with a positive flow")
    x, ly = x[ok], np.log(y[ok])
    slope, intercept = np.polyfit(x, ly, 1)
    resid = ly - (slope * x + intercept)
    ss_tot = float(((ly - ly.mean()) ** 2).sum())
    r2 = 1.0 - float((resid**2).sum()) / ss_tot if ss_tot > 0 else math.nan
    # A profile whose log changes by less than this over the fitted range is
    # flat to numerical precision: report no decay rather than a huge length.
    flat = abs(slope) * float(x.max() - x.min()) < 1e-9
    return {
        "decay_length_m": math.inf if flat else float(-1.0 / slope),
        "q0_veh_h": float(math.exp(intercept)),
        "r2": r2,
    }


def auxiliary_lane_spacings(
    rng: np.random.Generator,
    *,
    c_track: float,
    n_snapshots: int,
    span_m: float,
    speed_ms: float,
    entry_headway_s: float,
    headway_cv: float,
    merge_length_m: float,
) -> FloatArray:
    """Observed spacings on a synthetic auxiliary lane with a known coverage.

    The generator reproduces the two features that separate an acceleration
    lane from a mainline lane, and nothing else:

    * vehicles enter **only** at the gore, as a renewal process with gamma
      headways of mean ``entry_headway_s`` and coefficient of variation
      ``headway_cv`` (0.35 ≈ a metered, regular feed; 1.0 = Poisson);
    * each vehicle **leaves** the lane after an exponentially distributed
      distance of mean ``merge_length_m`` (``inf`` = no merge-out, the
      mainline-like control), so the lane's flow decays as ``e^(−x/L)`` —
      the profile ``exponential_decay_length`` measures on the recording.

    At constant speed the vehicles present at an instant are the renewal
    points of the entry process thinned by the merge-survival probability,
    and the instrument then thins them again with probability ``c_track``.
    Snapshots are drawn independently, which removes the time correlation
    real snapshots have; the mixture uses spacings for distribution shape
    only, so that is the favourable case.

    Args:
        rng: Seeded generator.
        c_track: True tracking coverage in ``(0, 1]``.
        n_snapshots: Independent snapshots to pool.
        span_m: Auxiliary-lane length [m].
        speed_ms: Constant speed [m/s].
        entry_headway_s: Mean headway at the gore [s].
        headway_cv: Coefficient of variation of the entry headway (``> 0``).
        merge_length_m: Mean merge-out distance [m]; ``math.inf`` for none.

    Returns:
        Pooled spacings [m] between consecutive tracked vehicles.

    Raises:
        ValueError: On an out-of-range probability or a non-positive scale.
    """
    if not 0.0 < c_track <= 1.0:
        raise ValueError(f"c_track must be in (0, 1], got {c_track}")
    if headway_cv <= 0.0 or entry_headway_s <= 0.0 or speed_ms <= 0.0 or span_m <= 0.0:
        raise ValueError("entry_headway_s, headway_cv, speed_ms and span_m must be > 0")
    if merge_length_m <= 0.0:
        raise ValueError(f"merge_length_m must be > 0 (or inf), got {merge_length_m}")
    mean_spacing = speed_ms * entry_headway_s
    alpha = headway_cv**-2
    # Enough draws that the cumulative sum reliably overshoots the span.
    n_draw = int(span_m / mean_spacing * 3.0) + 20
    out: list[FloatArray] = []
    for _ in range(int(n_snapshots)):
        pos = np.cumsum(rng.gamma(alpha, mean_spacing / alpha, size=n_draw))
        pos = pos[pos < span_m]
        if math.isfinite(merge_length_m):
            pos = pos[rng.exponential(merge_length_m, size=pos.size) > pos]
        kept = pos[rng.random(pos.size) < c_track]
        if kept.size >= 2:
            out.append(np.diff(kept))
    return np.concatenate(out) if out else np.zeros(0, dtype=np.float64)


def auxiliary_lane_validation(
    *,
    seed: int = SYNTH_SEED,
    c_values: tuple[float, ...] = SYNTH_C_VALUES,
    headway_cvs: tuple[float, ...] = SYNTH_HEADWAY_CV,
    merge_lengths_m: tuple[float, ...] = (math.inf,),
    n_snapshots: int = SYNTH_SNAPSHOTS,
    span_m: float = SPAN_M[1] - SPAN_M[0],
    speed_ms: float = SYNTH_SPEED_MS,
    tracked_headway_s: float = 5.0,
) -> list[dict[str, Any]]:
    """Recovery of a known coverage on synthetic auxiliary lanes.

    Counterpart of ``calibration.coverage.synthetic_validation`` for the
    regime of §2.3: the same estimator, on lanes that differ from the
    mainline only by the entry-and-merge-out structure. ``merge_length_m =
    inf`` is the control (a lane nobody leaves), so each row's bias against
    its control isolates what the merge-out alone costs.

    ``entry_headway_s`` is set to ``tracked_headway_s · c_track`` so every
    row reproduces the same *tracked* flow as the recording whatever the true
    coverage is — the identifiability question asked at fixed observables.

    Args:
        seed: RNG seed.
        c_values: True tracking coverages to recover.
        headway_cvs: Entry-headway coefficients of variation.
        merge_lengths_m: Mean merge-out distances [m]; ``inf`` = control.
        n_snapshots: Snapshots pooled per row.
        span_m: Auxiliary-lane length [m].
        speed_ms: Constant speed [m/s].
        tracked_headway_s: Mean headway between *tracked* entries [s].

    Returns:
        One row per (merge length, headway cv, coverage) with the fit's
        ``c_hat``, its error, the fitted and observed spacing variability
        and the sample size.
    """
    rows: list[dict[str, Any]] = []
    for merge_m in merge_lengths_m:
        for cv_h in headway_cvs:
            for c_true in c_values:
                rng = make_rng(seed)
                sp = auxiliary_lane_spacings(
                    rng,
                    c_track=c_true,
                    n_snapshots=n_snapshots,
                    span_m=span_m,
                    speed_ms=speed_ms,
                    entry_headway_s=tracked_headway_s * c_true,
                    headway_cv=cv_h,
                    merge_length_m=merge_m,
                )
                fit = fit_gap_mixture(sp)
                rows.append(
                    {
                        "merge_length_m": None if not math.isfinite(merge_m) else float(merge_m),
                        "merge_out": math.isfinite(merge_m),
                        "entry_headway_cv": float(cv_h),
                        "c_true": float(c_true),
                        "n_spacings": int(sp.size),
                        "c_hat": fit.c,
                        "err_c": fit.c - c_true if math.isfinite(fit.c) else math.nan,
                        "cv_true_fitted": fit.cv_true,
                        "cv_obs": fit.cv_obs,
                        "converged": fit.converged,
                        "at_bound": fit.at_bound,
                    }
                )
    return rows


def crossings(df: pd.DataFrame, x_s: float) -> int:
    """Fragment crossings of section ``x_s`` (scripts/i24_coverage.py::crossings)."""
    if df.empty:
        return 0
    d = df.sort_values(["veh_id", "t"], kind="stable")
    same = d["veh_id"].to_numpy()[1:] == d["veh_id"].to_numpy()[:-1]
    x = d["x"].to_numpy()
    return int(((x[:-1] < x_s) & (x[1:] >= x_s) & same).sum())


def edie(df: pd.DataFrame, area_m_s: float) -> tuple[float, float, float]:
    """Edie density [veh/m], flow [veh/s] and speed [m/s] (as i24_coverage.py)."""
    if df.empty:
        return 0.0, 0.0, math.nan
    tt = len(df) * SAMPLE_DT_S
    td = float(df["v"].sum()) * SAMPLE_DT_S
    rho, q = tt / area_m_s, td / area_m_s
    return rho, q, (q / rho if rho > 0 else math.nan)


def harmonic_coverage(counts: FloatArray, coverage: FloatArray) -> float:
    """Count-weighted harmonic mean coverage, ``Σ N / Σ (N / c)``.

    The scalar that reproduces a per-window coverage correction on a total:
    ``Σ N_i / c_i = (Σ N_i) / harmonic_coverage``. Used to express the Old
    Hickory correction as one multiplier comparable with the plan's probe
    levels.

    Args:
        counts: Per-window counts (``≥ 0``).
        coverage: Per-window coverage in ``(0, 1]``, same length.

    Returns:
        The weighted harmonic mean; ``NaN`` if no window has a positive
        count and a positive coverage.

    Raises:
        ValueError: On mismatched lengths or a coverage outside ``(0, 1]``.
    """
    n = np.asarray(counts, dtype=np.float64)
    c = np.asarray(coverage, dtype=np.float64)
    if n.shape != c.shape:
        raise ValueError("counts and coverage must have the same shape")
    ok = np.isfinite(n) & np.isfinite(c) & (n > 0)
    if not ok.any():
        return math.nan
    if np.any((c[ok] <= 0.0) | (c[ok] > 1.0)):
        raise ValueError("coverage must be in (0, 1]")
    return float(n[ok].sum() / (n[ok] / c[ok]).sum())


# --------------------------------------------------------------------------
# Data pass
# --------------------------------------------------------------------------


def lane5_statistics(df: pd.DataFrame, q_cap_veh_s: float) -> dict[str, Any]:
    """Per-15-min estimators and diagnostics for the auxiliary lane."""
    span_len = SPAN_M[1] - SPAN_M[0]
    windows: list[dict[str, Any]] = []
    n_win = round((STUDY_T_HI_S - STUDY_T_LO_S) / WINDOW_S)
    for i in range(n_win):
        t_lo = STUDY_T_LO_S + i * WINDOW_S
        d = df.loc[(df["t"] >= t_lo) & (df["t"] < t_lo + WINDOW_S)]
        rho, q, v = edie(d, WINDOW_S * span_len)
        loc = d.loc[(d["x"] >= RAMP_LOCAL_X_RANGE_M[0]) & (d["x"] < RAMP_LOCAL_X_RANGE_M[1])]
        rho_loc, q_loc, _ = edie(
            loc, WINDOW_S * (RAMP_LOCAL_X_RANGE_M[1] - RAMP_LOCAL_X_RANGE_M[0])
        )
        n_cross = crossings(loc, RAMP_COUNT_X_M)
        q_cross = n_cross / WINDOW_S
        sp, vp = snapshot_spacings(
            d["t"].to_numpy(),
            d["x"].to_numpy(),
            d["v"].to_numpy(),
            sample_dt=SAMPLE_DT_S,
            snapshot_dt=SNAPSHOT_DT_S,
        )
        res = coverage_gap_mixture(
            sp,
            vp,
            speed_edges_ms=[kmh_to_ms(e) for e in SPEED_EDGES_KMH],
            min_n=MIN_N_PER_CLASS,
            v_max_ms=kmh_to_ms(V_MAX_KMH),
        )
        classes = [
            {
                "v_lo_kmh": ms_to_kmh(c["v_lo_ms"]),
                "v_hi_kmh": ms_to_kmh(c["v_hi_ms"]) if c["v_hi_ms"] else None,
                "n": c["n"],
                "c": c["c"],
                "cv_true": c["cv_true"],
                "cv_obs": c["cv_obs"],
                "converged": c["converged"],
                "at_bound": c["at_bound"],
                "usable": c["usable"],
            }
            for c in res.classes
            if c["n"] >= MIN_N_PER_CLASS
        ]
        windows.append(
            {
                "t_lo_s": t_lo,
                "window": clock(t_lo),
                "inputs": {
                    "rho_tracked_veh_km": rho * 1000.0,
                    "q_tracked_veh_h": veh_s_to_veh_h(q),
                    "v_edie_kmh": ms_to_kmh(v) if math.isfinite(v) else None,
                    "rho_local_veh_km": rho_loc * 1000.0,
                    "q_local_veh_h": veh_s_to_veh_h(q_loc),
                    "crossings_at_ramp_count_section": n_cross,
                    "q_crossings_veh_h": veh_s_to_veh_h(q_cross),
                    "crossing_to_local_edie_ratio": (q_cross / q_loc if q_loc > 0 else math.nan),
                    "n_spacings": int(sp.size),
                    "duplicate_fraction": (
                        float((sp < DEFAULT_S_DUP_M).mean()) if sp.size else math.nan
                    ),
                },
                "gap_mixture_confounded": {
                    "c": res.c,
                    "c_class_min": res.c_min,
                    "c_class_max": res.c_max,
                    "n_used": res.n_used,
                    "n_usable_classes": sum(1 for c in res.classes if c["usable"]),
                    "classes": classes,
                },
                "capacity_bound_hcm": coverage_capacity_bound(q_cross, q_cap_veh_s),
            }
        )
    return {"windows": windows}


def assumption_checks(df: pd.DataFrame, windows: list[dict[str, Any]]) -> dict[str, Any]:
    """The three failed assumptions, each with the number that fails it."""
    period_s = STUDY_T_HI_S - STUDY_T_LO_S

    # --- 1. flow conservation along the lane ------------------------------
    edges = np.arange(SPAN_M[0], SPAN_M[1] + 0.5 * PROFILE_BIN_M, PROFILE_BIN_M)
    lab = pd.cut(df["x"], edges, right=False)
    grp = df.groupby(lab, observed=True)
    tt = grp.size() * SAMPLE_DT_S
    td = grp["v"].sum() * SAMPLE_DT_S
    profile = []
    for iv in tt.index:
        area = period_s * PROFILE_BIN_M
        profile.append(
            {
                "x_lo_m": float(iv.left),
                "x_hi_m": float(iv.right),
                "x_mid_m": float(0.5 * (iv.left + iv.right)),
                "rho_tracked_veh_km": float(tt[iv] / area * 1000.0),
                "q_tracked_veh_h": float(td[iv] / area * 3600.0),
                "v_kmh": float(td[iv] / tt[iv] * 3.6) if tt[iv] > 0 else math.nan,
            }
        )
    # Fit the decay downstream of the gore, where the lane only sheds.
    fit_rows = [r for r in profile if r["x_mid_m"] >= RAMP_COUNT_X_M]
    decay = exponential_decay_length(
        np.array([r["x_mid_m"] for r in fit_rows]),
        np.array([r["q_tracked_veh_h"] for r in fit_rows]),
    )
    q_gore = next(r["q_tracked_veh_h"] for r in fit_rows)
    q_taper = fit_rows[-1]["q_tracked_veh_h"]

    # --- 2. spacing regularity --------------------------------------------
    all_classes = [c for w in windows for c in w["gap_mixture_confounded"]["classes"]]
    cv_obs = [c["cv_obs"] for c in all_classes if c["cv_obs"] is not None]
    n_above_1 = sum(1 for v in cv_obs if v >= 1.0)

    # --- 3. spacing-scale homogeneity within a speed class ----------------
    sp, vp = snapshot_spacings(
        df["t"].to_numpy(),
        df["x"].to_numpy(),
        df["v"].to_numpy(),
        sample_dt=SAMPLE_DT_S,
        snapshot_dt=SNAPSHOT_DT_S,
    )
    # Follower position of each pair, recomputed with the same ordering.
    k = np.rint(df["t"].to_numpy() / SAMPLE_DT_S).astype(np.int64)
    step = round(SNAPSHOT_DT_S / SAMPLE_DT_S)
    sel = (k % step) == 0
    k2, x2 = k[sel], df["x"].to_numpy()[sel]
    order = np.lexsort((x2, k2))
    k2, x2 = k2[order], x2[order]
    x_follower = x2[:-1][k2[1:] == k2[:-1]]
    vp_kmh = vp * 3.6
    in_class = (
        (vp_kmh >= SCALE_CLASS_KMH[0]) & (vp_kmh < SCALE_CLASS_KMH[1]) & (sp >= DEFAULT_S_DUP_M)
    )
    scale_rows = []
    for lo in np.arange(SPAN_M[0], SPAN_M[1], SCALE_BIN_M):
        m = in_class & (x_follower >= lo) & (x_follower < lo + SCALE_BIN_M)
        if int(m.sum()) < MIN_N_PER_CLASS // 10:
            continue
        s = np.minimum(sp[m], DEFAULT_S_MAX_M)
        scale_rows.append(
            {
                "x_lo_m": float(lo),
                "x_hi_m": float(lo + SCALE_BIN_M),
                "n": int(m.sum()),
                "mean_spacing_m": float(s.mean()),
                "cv": float(s.std() / s.mean()),
            }
        )
    means = [r["mean_spacing_m"] for r in scale_rows]

    # --- fragment persistence ---------------------------------------------
    g = df.groupby("veh_id")
    dur = (g["t"].max() - g["t"].min()).to_numpy()
    length = (g["x"].max() - g["x"].min()).to_numpy()

    free_flow_share = float((vp_kmh >= V_MAX_KMH).mean()) if vp.size else math.nan
    return {
        "flow_conservation": {
            "assumption": (
                "coverage_section_crossings (calibration.coverage eq. S) needs a stretch on "
                "which flow is conserved, so that the true flow is q_local_tracked / c_local"
            ),
            "verdict": "violated",
            "profile_bin_m": PROFILE_BIN_M,
            "profile": profile,
            "q_tracked_veh_h_at_gore_bin": q_gore,
            "q_tracked_veh_h_at_taper_bin": q_taper,
            "shed_fraction_gore_to_taper": 1.0 - q_taper / q_gore if q_gore > 0 else math.nan,
            "exponential_decay_fit": decay,
            "decay_length_over_span": (
                decay["decay_length_m"] / (SPAN_M[1] - RAMP_COUNT_X_M)
                if math.isfinite(decay["decay_length_m"])
                else math.inf
            ),
            "note": (
                "the auxiliary lane sheds its traffic into lane 4 over its whole length, so "
                "no stretch of it is ramp-free in the sense eq. (S) requires; the section "
                "estimator is therefore not computed for lane 5"
            ),
        },
        "spacing_regularity": {
            "assumption": (
                "the random-thinning model implies cv_obs^2 = 1 - c (1 - cv_true^2) <= 1 for "
                "every c in (0, 1] and cv_true < 1, so cv_obs > 1 admits no coverage at all"
            ),
            "verdict": "violated",
            "n_speed_classes": len(cv_obs),
            "n_classes_cv_obs_ge_1": n_above_1,
            "cv_obs_min": min(cv_obs) if cv_obs else math.nan,
            "cv_obs_max": max(cv_obs) if cv_obs else math.nan,
            "note": (
                "cv_obs is measured on spacings winsorized at s_max, which can only lower it, "
                "so a winsorized cv_obs above 1 implies an untruncated one above 1"
            ),
        },
        "scale_homogeneity": {
            "assumption": (
                "the mixture conditions on a speed class so the true spacing scale is "
                "homogeneous within it; heterogeneity biases it low (its own synthetic "
                "validation: 0.2-0.4 on a bimodal within-class scale mix)"
            ),
            "verdict": "violated",
            "speed_class_kmh": list(SCALE_CLASS_KMH),
            "sub_bin_m": SCALE_BIN_M,
            "rows": scale_rows,
            "mean_spacing_ratio_max_over_min": (
                max(means) / min(means) if means and min(means) > 0 else math.nan
            ),
            "tracked_density_ratio_max_over_min": (
                max(r["rho_tracked_veh_km"] for r in profile)
                / min(r["rho_tracked_veh_km"] for r in profile)
            ),
        },
        "fragment_persistence": {
            "n_fragments": int(df["veh_id"].nunique()),
            "median_duration_s": float(np.median(dur)),
            "median_length_m": float(np.median(length)),
            "p90_length_m": float(np.quantile(length, 0.9)),
            "span_length_m": SPAN_M[1] - SPAN_M[0],
            "note": (
                "the median fragment covers a small fraction of the acceleration lane, so a "
                "crossing count at one section misses vehicles whose vehicle-time is tracked; "
                "on the mainline that gap is what section_gap_mixture corrects, and it cannot "
                "be formed here (see flow_conservation)"
            ),
        },
        "free_flow_share": {
            "assumption": (
                "the mixture is uninformative at free-flow spacings and drops pairs at or "
                f"above {V_MAX_KMH:g} km/h"
            ),
            "verdict": "partial",
            "pair_share_at_or_above_v_max": free_flow_share,
        },
    }


# --------------------------------------------------------------------------
# The Old Hickory correction
# --------------------------------------------------------------------------


def mainline_coverage_variants(counts: FloatArray) -> dict[str, dict[str, Any]]:
    """The three mainline coverage conventions the ramp inflow could divide by.

    * ``equilibrium_capacity_fleet`` — what the committed
      ``artifacts/i24_replica_inputs.json`` carries and every scenario built
      since the capacity calibration uses (fleet
      ``artifacts/idm_i24_capacity.json``).
    * ``equilibrium_legacy_fleet`` — the same method on the pre-capacity fleet
      ``artifacts/idm_i24.json``; this reproduces the table printed in
      ``docs/I24_DATA.md`` §4 (the 0.52-0.66 range), which the capacity
      calibration superseded without the table being recomputed.
    * ``recommended`` — ``artifacts/i24_coverage.json``'s recommended
      estimator, selectable with ``--coverage-estimator recommended``.

    Args:
        counts: Per-5-min Old Hickory ramp-lane crossing counts, used to
            weight the harmonic mean.

    Returns:
        One record per convention with the per-window values, their range and
        the count-weighted harmonic mean.
    """
    inp = json.loads((REPO_ROOT / REPLICA_INPUTS).read_text())
    rows = inp["coverage"]["rows"]
    legacy = json.loads((REPO_ROOT / LEGACY_FLEET_ARTIFACT).read_text())["mean"]
    cov_art = json.loads((REPO_ROOT / MAINLINE_COVERAGE_ARTIFACT).read_text())
    by_t = {float(w["t_lo_s"]): w["pooled"]["recommended_filled"] for w in cov_art["windows"]}

    series: dict[str, list[float]] = {
        "equilibrium_capacity_fleet": [float(r["coverage_used"]) for r in rows],
        "equilibrium_legacy_fleet": [
            float(r["rho_tracked_veh_km_lane"])
            / (
                1000.0
                * idm_equilibrium_density(kmh_to_ms(r["v_edie_kmh"]), legacy, VEHICLE_LENGTH_M)
            )
            for r in rows
        ],
        "recommended": [float(by_t[float(r["t_lo_s"])]) for r in rows],
    }
    sources = {
        "equilibrium_capacity_fleet": (
            f"{REPLICA_INPUTS} coverage.rows[].coverage_used (fleet {FLEET_ARTIFACT}); "
            "the values the committed scenarios divide by"
        ),
        "equilibrium_legacy_fleet": (
            f"same method on the pre-capacity fleet {LEGACY_FLEET_ARTIFACT}; reproduces the "
            "coverage column of docs/I24_DATA.md §4, which the capacity calibration superseded"
        ),
        "recommended": (
            f"{MAINLINE_COVERAGE_ARTIFACT} windows[].pooled.recommended_filled "
            "(scripts/i24_build_replica.py --coverage-estimator recommended)"
        ),
    }
    out: dict[str, dict[str, Any]] = {}
    for name, vals in series.items():
        arr = np.array(vals, dtype=np.float64)
        reps = round(len(counts) / len(arr))
        out[name] = {
            "source": sources[name],
            "per_window": [float(v) for v in arr],
            "min": float(arr.min()),
            "max": float(arr.max()),
            "harmonic_mean_weighted_by_ramp_counts": harmonic_coverage(
                counts, np.repeat(arr, reps)
            ),
        }
    return out


def old_hickory_correction(bound_lo: float) -> dict[str, Any]:
    """What lane-5 coverage implies for the Old Hickory ramp inflow.

    ``scripts/i24_build_replica.py`` counts ramp-lane crossings at
    ``x = 950`` m (``RAMPS[0]["count_x_m"]``, lanes 5-9) and divides them by
    the **mainline** coverage of the window to build the corrected on-ramp
    inflow. If the count's own coverage is ``c5``, the inflow should instead
    be divided by ``c5``, i.e. multiplied by ``m = c_mainline / c5``. Written
    as one scalar on the whole profile, ``m = c̄_mainline / c5`` with
    ``c̄_mainline`` the count-weighted harmonic mean (``harmonic_coverage``).
    """
    inp = json.loads((REPO_ROOT / REPLICA_INPUTS).read_text())
    ramp = next(r for r in inp["ramps"] if r["name"].startswith("Old Hickory"))
    counts = np.array(ramp["ramp_lane_crossings"], dtype=np.float64)
    cov_rows = inp["coverage"]["rows"]
    cov = np.array([r["coverage_used"] for r in cov_rows], dtype=np.float64)
    reps = round(len(counts) / len(cov))
    c_per_count = np.repeat(cov, reps)
    c_bar = harmonic_coverage(counts, c_per_count)
    corrected_total = float((counts / c_per_count).sum())
    variants = mainline_coverage_variants(counts)

    def level_to_c5(m: float) -> float:
        return c_bar / m

    return {
        "builder": {
            "script": "scripts/i24_build_replica.py",
            "count_section_data_x_m": RAMP_COUNT_X_M,
            "counted_lanes": "5-9 (ramp lanes)",
            "divisor": (
                "the mainline (lanes 1-4) coverage of the same 15-min window, "
                "artifacts/i24_replica_inputs.json coverage.rows[].coverage_used"
            ),
            "mainline_coverage_used_min": float(cov.min()),
            "mainline_coverage_used_max": float(cov.max()),
            "mainline_coverage_harmonic_mean_weighted_by_ramp_counts": c_bar,
            "tracked_ramp_crossings_study_period": int(counts.sum()),
            "coverage_corrected_ramp_total": corrected_total,
        },
        "mainline_coverage_variants": variants,
        "multiplier_floor_by_variant": {
            name: v["harmonic_mean_weighted_by_ramp_counts"] for name, v in variants.items()
        },
        "relation": "multiplier m on the present corrected ramp inflow = c_mainline_bar / c5",
        "implied_multiplier": {
            "if_c5_equals_mainline": 1.0,
            "if_c5_is_one_no_correction_needed": c_bar,
            "at_capacity_lower_bound_on_c5": c_bar / bound_lo if bound_lo > 0 else math.inf,
            "admissible_interval": [c_bar, c_bar / bound_lo if bound_lo > 0 else math.inf],
            "admissible_interval_basis": (
                "c5 in [capacity lower bound, 1]; the gap-mixture estimator supplies no point "
                "value on this lane (see assumption_checks)"
            ),
        },
        "probe_levels": [
            {
                "level": lvl,
                "implied_c5": level_to_c5(lvl),
                "implied_c5_admissible": bound_lo <= level_to_c5(lvl) <= 1.0,
            }
            for lvl in PROBE_LEVELS
        ],
        "bracket_verdict": None,  # filled by main()
    }


# --------------------------------------------------------------------------


def _clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_clean(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating | float):
        return None if not math.isfinite(float(obj)) else float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--q-cap-veh-h-lane",
        type=float,
        default=HCM_BASIC_FREEWAY_CAPACITY_PC_H_LN,
        help="flow ceiling for the lane-5 capacity lower bound on the coverage",
    )
    ap.add_argument("--out", default=OUT_ARTIFACT)
    args = ap.parse_args()
    t_start = time.perf_counter()

    # --- one data pass ----------------------------------------------------
    df = load_i24_parquet(
        WB_DIR,
        t_range_s=(STUDY_T_LO_S, STUDY_T_HI_S),
        x_range_m=SPAN_M,
        lanes=(AUX_LANE, AUX_LANE),
        columns=["t", "x", "v", "veh_id"],
    )
    print(
        f"lane {AUX_LANE}: {len(df):,} samples, {df['veh_id'].nunique():,} fragments, "
        f"data x [{SPAN_M[0]:.0f}, {SPAN_M[1]:.0f}) m, {clock(STUDY_T_LO_S)}-{clock(STUDY_T_HI_S)} CST "
        f"[{time.perf_counter() - t_start:.0f} s]",
        flush=True,
    )
    q_cap_veh_s = args.q_cap_veh_h_lane / 3600.0
    stats = lane5_statistics(df, q_cap_veh_s)
    checks = assumption_checks(df, stats["windows"])
    n_fragments = int(df["veh_id"].nunique())
    n_samples = len(df)
    del df

    # --- synthetic validation in this regime ------------------------------
    decay_m = checks["flow_conservation"]["exponential_decay_fit"]["decay_length_m"]
    synth = auxiliary_lane_validation(merge_lengths_m=(math.inf, float(round(decay_m))))

    # --- what can and cannot be said --------------------------------------
    wins = stats["windows"]
    mix = [w["gap_mixture_confounded"]["c"] for w in wins if w["gap_mixture_confounded"]["c"]]
    bounds = [w["capacity_bound_hcm"] for w in wins if math.isfinite(w["capacity_bound_hcm"])]
    bound_lo = max(bounds) if bounds else math.nan

    mainline = json.loads((REPO_ROOT / MAINLINE_COVERAGE_ARTIFACT).read_text())
    per_lane: dict[str, Any] = {}
    for idx in range(4):
        vals_vt, vals_sec = [], []
        for w in mainline["windows"]:
            if not w["in_study_period"]:
                continue
            e = w["lanes"][idx]["estimators"]
            if e["gap_mixture"] is not None:
                vals_vt.append(e["gap_mixture"])
            if e["section_gap_mixture"] is not None:
                vals_sec.append(e["section_gap_mixture"])
        per_lane[f"lane_{idx + 1}"] = {
            "gap_mixture": [min(vals_vt), max(vals_vt)],
            "section_gap_mixture": [min(vals_sec), max(vals_sec)],
        }

    corr = old_hickory_correction(bound_lo)
    c_bar = corr["builder"]["mainline_coverage_harmonic_mean_weighted_by_ramp_counts"]
    lo_adm, hi_adm = corr["implied_multiplier"]["admissible_interval"]
    brackets = all(p["implied_c5_admissible"] for p in corr["probe_levels"]) and (
        min(PROBE_LEVELS) <= lo_adm and max(PROBE_LEVELS) >= hi_adm
    )
    corr["bracket_verdict"] = {
        "brackets_the_implied_correction": bool(brackets),
        "probe_levels": list(PROBE_LEVELS),
        "probe_levels_cover_c5_interval": [c_bar / max(PROBE_LEVELS), c_bar / min(PROBE_LEVELS)],
        "admissible_c5_interval": [bound_lo, 1.0],
        "admissible_multiplier_interval": [lo_adm, hi_adm],
        "reason": (
            f"the three levels are equivalent to assuming c5 in "
            f"[{c_bar / max(PROBE_LEVELS):.3f}, {c_bar / min(PROBE_LEVELS):.3f}], a strict "
            f"sub-interval of the admissible [{bound_lo:.3f}, 1.000]: a perfectly tracked "
            f"auxiliary lane (c5 = 1) needs multiplier {lo_adm:.3f}, below the lowest level "
            f"{min(PROBE_LEVELS):g}, and a lane tracked at the capacity bound needs "
            f"{hi_adm:.3f}, above the highest level {max(PROBE_LEVELS):g}"
        ),
    }

    created_at = subprocess.run(
        ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True, check=True
    ).stdout.strip()
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "created_at": created_at,
        "script": "scripts/i24_coverage_lane5.py",
        "data_hash": data_hash(),
        "source": (
            "I-24 MOTION INCEPTION v1.x, 30 Nov 2022 westbound "
            f"(6386d89efb3ff533c12df167__post10), lane {AUX_LANE} (Old Hickory acceleration "
            f"lane), data x in [{SPAN_M[0]:.0f}, {SPAN_M[1]:.0f}) m, "
            f"{clock(STUDY_T_LO_S)}-{clock(STUDY_T_HI_S)} CST, {WINDOW_S:.0f} s windows"
        ),
        "question": (
            "docs/MERGE_ROUND6_PLAN.md §2.3: the tracking coverage of the auxiliary lane, and "
            "the correction it implies for the Old Hickory ramp inflow, which "
            "scripts/i24_build_replica.py corrects with the mainline coverage instead"
        ),
        "parameters": {
            "lane": AUX_LANE,
            "span_data_x_m": list(SPAN_M),
            "period_s": [STUDY_T_LO_S, STUDY_T_HI_S],
            "period_cst": [clock(STUDY_T_LO_S), clock(STUDY_T_HI_S)],
            "window_s": WINDOW_S,
            "sample_dt_s": SAMPLE_DT_S,
            "snapshot_dt_s": SNAPSHOT_DT_S,
            "speed_edges_kmh": list(SPEED_EDGES_KMH),
            "v_max_kmh": V_MAX_KMH,
            "min_n_per_class": MIN_N_PER_CLASS,
            "s_dup_m": DEFAULT_S_DUP_M,
            "s_max_m": DEFAULT_S_MAX_M,
            "ramp_count_section_x_m": RAMP_COUNT_X_M,
            "ramp_local_x_range_m": list(RAMP_LOCAL_X_RANGE_M),
            "q_cap_veh_h_lane": args.q_cap_veh_h_lane,
            "q_cap_source": (
                "calibration.coverage.HCM_BASIC_FREEWAY_CAPACITY_PC_H_LN — HCM 6th ed. basic "
                "freeway capacity at >= 70 mi/h FFS, the largest tabulated value and hence an "
                "upper envelope, so c >= q_tracked / q_cap is a valid but weak bound; a "
                "single-lane ramp ceiling would be lower and the bound correspondingly stronger"
            ),
            "synthetic_seed": SYNTH_SEED,
            "synthetic_snapshots": SYNTH_SNAPSHOTS,
            "synthetic_speed_ms": SYNTH_SPEED_MS,
        },
        "method": (
            "the estimator of scripts/i24_coverage.py (calibration.coverage: geometric-gamma "
            "mixture on 2 s snapshot spacings per speed class, eq. G; section-crossing "
            "coverage eq. S; capacity bound c >= q_tracked/q_cap) applied to lane 5 with "
            "identical settings, plus the assumption checks the lane's geometry demands and a "
            "synthetic validation in this regime"
        ),
        "n_fragments": n_fragments,
        "n_samples": n_samples,
        "windows": wins,
        "assumption_checks": checks,
        "synthetic_validation_auxiliary_lane": synth,
        "synthetic_validation_mainline_cited": {
            "artifact": MAINLINE_COVERAGE_ARTIFACT,
            "created_at": mainline.get("created_at"),
            "data_hash": mainline.get("data_hash"),
            "function": "calibration.coverage.synthetic_validation",
            "reading": (
                "with a homogeneous true spacing scale the mixture recovers c within 0.01 "
                "(moments within 0.03); it is biased low by <= 0.05 under correlated losses "
                "and by 0.2-0.4 in free flow or under a bimodal within-class spacing mix "
                "(docs/I24_DATA.md, tests/test_calibration/test_calibration_coverage.py)"
            ),
            "rows": mainline["synthetic_validation"],
        },
        "mainline_per_lane_study_period": per_lane,
        "estimate": None,
        "old_hickory_correction": corr,
        "provenance": {
            "inputs": [
                REPLICA_INPUTS,
                MAINLINE_COVERAGE_ARTIFACT,
                "data/i24motion/processed/i24_wb_20221130 (trajectories.parquet)",
            ],
            "read_only_references": [
                "scripts/i24_build_replica.py (RAMPS, coverage_factors, corrected())",
                "scripts/i24_coverage.py (estimator settings)",
                "docs/I24_VALIDATION.md §0.5(d) (lane-5 occupancy range)",
                "docs/I24_DATA.md §4 and the gap-estimator section",
                "docs/MERGE_ROUND6_PLAN.md §2.3",
            ],
            "package_versions": {
                "numpy": np.__version__,
                "pandas": pd.__version__,
            },
        },
    }
    artifact["estimate"] = {
        "published": False,
        "value": None,
        "reason": (
            "the gap-mixture estimator is not identified on an auxiliary lane: merging out of "
            "the lane and failing to track are two thinnings of the same point process, and "
            "eq. (G) fits their product. The synthetic validation in this artifact measures "
            "the resulting bias directly, and two further assumptions fail (see "
            "assumption_checks: flow_conservation, spacing_regularity)."
        ),
        "confounded_gap_mixture": {
            "definition": (
                "what the mixture returns on lane 5: approximately (tracking coverage) x "
                "(merge-survival share), hence an upper bound on neither and a lower bound on "
                "the tracking coverage only"
            ),
            "per_window_min": min(mix) if mix else None,
            "per_window_max": max(mix) if mix else None,
        },
        "bounds": {
            "lower": bound_lo,
            "lower_basis": (
                "max over windows of the capacity bound c >= q_tracked/q_cap at the ramp count "
                "section with the HCM upper-envelope ceiling; taking the max across windows "
                "treats c5 as constant over the study period, which is also what expressing "
                "the ramp correction as one scalar multiplier assumes. The per-window bounds "
                "are in windows[].capacity_bound_hcm"
            ),
            "upper": 1.0,
            "upper_basis": "a coverage cannot exceed 1",
        },
        "why_extrapolation_from_the_mainline_table_is_not_offered": (
            "the mainline per-lane coverage has no monotone lane gradient to extrapolate "
            "along: lane 1 is the best tracked and the rightmost mainline lane 4 the worst, "
            "so neighbouring lane 4 would put lane 5 below the pooled mainline coverage while "
            "an edge-lane argument would put it above (see mainline_per_lane_study_period)"
        ),
    }

    out = REPO_ROOT / args.out
    out.write_text(json.dumps(_clean(artifact), indent=2))

    # --- console report ---------------------------------------------------
    def fmt(v: float | None) -> str:
        return f"{v:6.3f}" if v is not None and math.isfinite(v) else "   nan"

    print(f"\nlane {AUX_LANE}, per 15-min window:")
    print(
        f"{'win':5s} {'N@950':>6s} {'q_trk':>7s} {'v':>6s} {'rho':>6s} {'nsp':>6s} "
        f"{'mix*':>6s} {'cls':>4s} {'bound':>6s} {'xratio':>6s}"
    )
    for w in wins:
        i, g = w["inputs"], w["gap_mixture_confounded"]
        print(
            f"{w['window']:5s} {i['crossings_at_ramp_count_section']:6d} "
            f"{i['q_tracked_veh_h']:7.0f} {i['v_edie_kmh']:6.1f} {i['rho_tracked_veh_km']:6.1f} "
            f"{i['n_spacings']:6d} {fmt(g['c'])} {g['n_usable_classes']:4d} "
            f"{fmt(w['capacity_bound_hcm'])} {fmt(i['crossing_to_local_edie_ratio'])}"
        )
    print("  (* mix is NOT a coverage: it is tracking coverage x merge survival, see below)")

    fc = checks["flow_conservation"]
    print(f"\nassumption 1, flow conservation along the lane: {fc['verdict'].upper()}")
    print(
        f"  tracked flow {fc['q_tracked_veh_h_at_gore_bin']:.0f} veh/h at the gore bin -> "
        f"{fc['q_tracked_veh_h_at_taper_bin']:.0f} at the taper bin "
        f"({100 * fc['shed_fraction_gore_to_taper']:.0f}% shed), decay length "
        f"{fc['exponential_decay_fit']['decay_length_m']:.0f} m (r2 "
        f"{fc['exponential_decay_fit']['r2']:.3f}) over a {SPAN_M[1] - RAMP_COUNT_X_M:.0f} m run"
    )
    sr = checks["spacing_regularity"]
    print(f"assumption 2, spacing regularity: {sr['verdict'].upper()}")
    print(
        f"  cv_obs {sr['cv_obs_min']:.2f}-{sr['cv_obs_max']:.2f} over {sr['n_speed_classes']} "
        f"speed classes; {sr['n_classes_cv_obs_ge_1']} of them at or above 1, which the model "
        f"cannot produce for any coverage"
    )
    sh = checks["scale_homogeneity"]
    print(f"assumption 3, within-class scale homogeneity: {sh['verdict'].upper()}")
    print(
        f"  mean spacing in the {SCALE_CLASS_KMH[0]:.0f}-{SCALE_CLASS_KMH[1]:.0f} km/h class "
        f"varies by x{sh['mean_spacing_ratio_max_over_min']:.2f} along the lane; tracked "
        f"density by x{sh['tracked_density_ratio_max_over_min']:.2f}"
    )
    fp = checks["fragment_persistence"]
    print(
        f"fragments: {fp['n_fragments']:,}, median {fp['median_duration_s']:.1f} s / "
        f"{fp['median_length_m']:.0f} m over a {fp['span_length_m']:.0f} m lane"
    )

    print("\nsynthetic auxiliary lanes (same estimator, known coverage):")
    print(f"{'merge':>8} {'cv_h':>5} {'c_true':>7} {'n':>8} {'c_hat':>6} {'err':>7} {'cv_obs':>7}")
    for r in synth:
        lab = "none" if r["merge_length_m"] is None else f"{r['merge_length_m']:.0f} m"
        print(
            f"{lab:>8} {r['entry_headway_cv']:5.2f} {r['c_true']:7.2f} {r['n_spacings']:8d} "
            f"{fmt(r['c_hat'])} {r['err_c']:+7.3f} {r['cv_obs']:7.3f}"
        )

    est = artifact["estimate"]
    print(
        f"\nestimate published: {est['published']}  "
        f"(confounded mixture {est['confounded_gap_mixture']['per_window_min']:.3f}-"
        f"{est['confounded_gap_mixture']['per_window_max']:.3f}; "
        f"coverage bounded to [{est['bounds']['lower']:.3f}, {est['bounds']['upper']:.3f}])"
    )
    print("\nOld Hickory correction (multiplier on the present corrected ramp inflow):")
    print(
        f"  mainline coverage the builder divides by: "
        f"{corr['builder']['mainline_coverage_used_min']:.3f}-"
        f"{corr['builder']['mainline_coverage_used_max']:.3f}, count-weighted harmonic mean "
        f"{c_bar:.3f}"
    )
    print("  mainline coverage conventions (range, harmonic mean = the multiplier floor):")
    for name, v in corr["mainline_coverage_variants"].items():
        print(
            f"    {name:28s} {v['min']:.3f}-{v['max']:.3f}  floor "
            f"{v['harmonic_mean_weighted_by_ramp_counts']:.3f}"
        )
    print(
        f"  tracked ramp-lane crossings {corr['builder']['tracked_ramp_crossings_study_period']:,}"
        f" -> corrected total {corr['builder']['coverage_corrected_ramp_total']:.0f}"
    )
    for p in corr["probe_levels"]:
        print(
            f"  level {p['level']:5.3f} assumes lane-5 coverage {p['implied_c5']:.3f}"
            f"  (admissible: {p['implied_c5_admissible']})"
        )
    bv = corr["bracket_verdict"]
    print(f"  admissible multiplier interval [{lo_adm:.3f}, {hi_adm:.3f}]")
    print(f"  probe levels bracket the implied correction: {bv['brackets_the_implied_correction']}")
    print(f"\n-> {out}  ({time.perf_counter() - t_start:.0f} s)")


if __name__ == "__main__":
    main()
