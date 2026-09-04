"""Tracking-coverage estimators on the I-24 MOTION westbound day.

Computes, per 15-min window (06:00–10:00 CST) and per mainline lane 1–4
over the replica's measured span (data ``x`` ∈ [0, 5492) m, from
``artifacts/i24_replica_inputs.json``), every estimator in
``calibration.coverage``:

* ``equilibrium`` — tracked Edie density over the IDM population's
  equilibrium density at the Edie speed (the method of
  ``scripts/i24_build_replica.py::coverage_factors``; model-dependent).
* ``gap_moments`` — random-thinning moment estimator on snapshot spacings
  per speed class, with the true-spacing cv taken from the mixture fit.
* ``gap_mixture`` — maximum-likelihood geometric-gamma mixture on snapshot
  spacings per speed class (pairs below 60 km/h), combined by spacing
  count.
* ``capacity_bound_fd`` / ``capacity_bound_hcm`` — ``c ≥ q_tracked / q_cap``
  at the mainline count section (``x = 200`` m, the demand input) with the
  fitted FD's ``q_max`` upper CI (``artifacts/fd_i24.json``) and with the HCM
  passenger-car ceiling.
* ``section_gap_mixture`` / ``section_equilibrium`` — the coverage that
  applies to the crossing count at ``x = 200`` m: the vehicle-time coverage
  on the ramp-free stretch [0, 900) m times the ratio of crossings to local
  Edie flow.
* ``recommended`` — ``max(section_gap_mixture, capacity_bound_fd)``; see the
  artifact's ``recommendation`` for the rule and its caveats.

Every number printed is also written to ``artifacts/i24_coverage.json``
together with the inputs it came from, the synthetic-validation table of
``calibration.coverage.synthetic_validation`` and provenance. Memory stays
below ~1 GB: one lane of one window is loaded at a time.

Run: ``uv run --no-sync python scripts/i24_coverage.py``
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_data import (
    MAINLINE_LANES,
    REPO_ROOT,
    SAMPLE_DT_S,
    WB_DIR,
    clock,
    data_hash,
)

from calibration.coverage import (
    DEFAULT_S_DUP_M,
    DEFAULT_S_MAX_M,
    HCM_BASIC_FREEWAY_CAPACITY_PC_H_LN,
    combine_with_bound,
    coverage_capacity_bound,
    coverage_equilibrium,
    coverage_gap_mixture,
    coverage_gap_moments,
    coverage_section_crossings,
    snapshot_spacings,
    synthetic_validation,
)
from calibration.loaders.i24motion import load_i24_parquet
from flowstate_core.units import kmh_to_ms, ms_to_kmh, veh_s_to_veh_h

T_LO_S = 0.0  # 06:00 CST
T_HI_S = 14400.0  # 10:00 CST
WINDOW_S = 900.0
STUDY_T_LO_S = 1800.0  # 06:30 CST, scripts/i24_build_replica.py::T_STUDY_LO_S
STUDY_T_HI_S = 9000.0  # 08:30 CST
N_LANES = MAINLINE_LANES[1] - MAINLINE_LANES[0] + 1

#: Mainline demand count section [m] (scripts/i24_build_replica.py::MAINLINE_COUNT_X_M)
#: and the ramp-free stretch around it (the Old Hickory on-ramp gore is at
#: ~950 m; ramp-lane crossings are counted at 950 m by the builder).
COUNT_X_M = 200.0
LOCAL_X_RANGE_M = (0.0, 900.0)

SNAPSHOT_DT_S = 2.0
#: Speed classes for the mixture fit [km/h]; 5 km/h wide below 20 km/h where
#: the equilibrium spacing is most speed-sensitive in relative terms.
SPEED_EDGES_KMH = (0.0, 5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0)
COARSE_SPEED_EDGES_KMH = (0.0, 10.0, 20.0, 30.0, 40.0, 50.0)
#: Pairs at or above this speed are free flow: the spacing model is uninformative.
V_MAX_KMH = 60.0
MIN_N_PER_CLASS = 300
VEHICLE_LENGTH_M = 5.0  # scripts/i24_build_replica.py::VEHICLE_LENGTH_M

FLEET_ARTIFACT = "artifacts/idm_i24_capacity.json"
FD_ARTIFACT = "artifacts/fd_i24.json"
REPLICA_INPUTS = "artifacts/i24_replica_inputs.json"
OUT_ARTIFACT = "artifacts/i24_coverage.json"
FALLBACK_SPAN_HI_M = (
    5492.4989040731325  # artifacts/i24_replica_inputs.json measured_span_data_x_m[1]
)


def edie(df: pd.DataFrame, area_m_s: float) -> tuple[float, float, float]:
    """Edie density [veh/m], flow [veh/s] and speed [m/s] of sampled rows."""
    if df.empty:
        return 0.0, 0.0, math.nan
    tt = len(df) * SAMPLE_DT_S
    td = float(df["v"].sum()) * SAMPLE_DT_S
    rho, q = tt / area_m_s, td / area_m_s
    return rho, q, (q / rho if rho > 0 else math.nan)


def crossings(df: pd.DataFrame, x_s: float) -> int:
    """Fragment crossings of section ``x_s`` (consecutive samples of one id)."""
    if df.empty:
        return 0
    d = df.sort_values(["veh_id", "t"], kind="stable")
    same = d["veh_id"].to_numpy()[1:] == d["veh_id"].to_numpy()[:-1]
    x = d["x"].to_numpy()
    return int(((x[:-1] < x_s) & (x[1:] >= x_s) & same).sum())


def lane_window(
    t_lo: float, lane: int, span_hi: float, idm_mean: dict[str, float], q_caps: dict[str, float]
) -> dict[str, Any]:
    """All statistics and estimators for one lane in one window."""
    t_hi = t_lo + WINDOW_S
    df = load_i24_parquet(
        WB_DIR,
        t_range_s=(t_lo, t_hi),
        x_range_m=(0.0, span_hi),
        lanes=(lane, lane),
        columns=["t", "x", "v", "veh_id"],
    )
    rho, q, v = edie(df, WINDOW_S * span_hi)
    loc = df.loc[(df["x"] >= LOCAL_X_RANGE_M[0]) & (df["x"] < LOCAL_X_RANGE_M[1])]
    rho_loc, q_loc, v_loc = edie(loc, WINDOW_S * (LOCAL_X_RANGE_M[1] - LOCAL_X_RANGE_M[0]))
    n_cross = crossings(loc, COUNT_X_M)
    q_cross = n_cross / WINDOW_S

    def spacing_fits(sub: pd.DataFrame, edges_kmh: tuple[float, ...]) -> dict[str, Any]:
        sp, vp = snapshot_spacings(
            sub["t"].to_numpy(),
            sub["x"].to_numpy(),
            sub["v"].to_numpy(),
            sample_dt=SAMPLE_DT_S,
            snapshot_dt=SNAPSHOT_DT_S,
        )
        res = coverage_gap_mixture(
            sp,
            vp,
            speed_edges_ms=[kmh_to_ms(e) for e in edges_kmh],
            min_n=MIN_N_PER_CLASS,
            v_max_ms=kmh_to_ms(V_MAX_KMH),
        )
        # Moment estimator per usable class with that class's fitted cv_true.
        num = den = 0.0
        for cl in res.classes:
            if not cl["usable"]:
                continue
            m = (vp >= cl["v_lo_ms"]) & (vp < (cl["v_hi_ms"] or math.inf))
            c_m = coverage_gap_moments(sp[m], min(cl["cv_true"], 0.999), s_max=DEFAULT_S_MAX_M)
            cl["gap_moments"] = c_m
            if math.isfinite(c_m):
                num += c_m * cl["n"]
                den += cl["n"]
        n_dup = int((sp < DEFAULT_S_DUP_M).sum())
        return {
            "n_spacings": int(sp.size),
            "duplicate_fraction": n_dup / sp.size if sp.size else math.nan,
            "n_used": res.n_used,
            "gap_mixture": res.c,
            "gap_mixture_class_min": res.c_min,
            "gap_mixture_class_max": res.c_max,
            "gap_moments": num / den if den > 0 else math.nan,
            "classes": [
                {
                    k: (None if isinstance(val, float) and not math.isfinite(val) else val)
                    for k, val in cl.items()
                }
                for cl in res.classes
            ],
        }

    span_fit = spacing_fits(df, SPEED_EDGES_KMH)
    span_fit_coarse = spacing_fits(df, COARSE_SPEED_EDGES_KMH)
    local_fit = spacing_fits(loc, SPEED_EDGES_KMH)
    del df, loc

    c_eq = coverage_equilibrium(rho, v, idm_mean, vehicle_length=VEHICLE_LENGTH_M)
    c_eq_loc = coverage_equilibrium(rho_loc, v_loc, idm_mean, vehicle_length=VEHICLE_LENGTH_M)
    c_mix, c_mix_loc = span_fit["gap_mixture"], local_fit["gap_mixture"]
    bounds_section = {k: coverage_capacity_bound(q_cross, qc) for k, qc in q_caps.items()}
    bounds_span = {k: coverage_capacity_bound(q, qc) for k, qc in q_caps.items()}
    crossing_ratio = q_cross / q_loc if q_loc > 0 else math.nan
    c_sec_mix = coverage_section_crossings(n_cross, WINDOW_S, q_loc, c_mix_loc)
    c_sec_eq = coverage_section_crossings(n_cross, WINDOW_S, q_loc, c_eq_loc)
    return {
        "lane": lane,
        "inputs": {
            "rho_tracked_veh_km": rho * 1000.0,
            "q_tracked_veh_h": veh_s_to_veh_h(q),
            "v_edie_kmh": ms_to_kmh(v) if math.isfinite(v) else None,
            "rho_local_veh_km": rho_loc * 1000.0,
            "q_local_veh_h": veh_s_to_veh_h(q_loc),
            "v_local_kmh": ms_to_kmh(v_loc) if math.isfinite(v_loc) else None,
            "crossings_at_count_section": n_cross,
            "q_crossings_veh_h": veh_s_to_veh_h(q_cross),
            "crossing_to_local_edie_ratio": crossing_ratio,
        },
        "estimators": {
            "equilibrium": c_eq,
            "equilibrium_local": c_eq_loc,
            "gap_moments": span_fit["gap_moments"],
            "gap_mixture": c_mix,
            "gap_mixture_coarse_classes": span_fit_coarse["gap_mixture"],
            "gap_mixture_local": c_mix_loc,
            "gap_mixture_class_range": [
                span_fit["gap_mixture_class_min"],
                span_fit["gap_mixture_class_max"],
            ],
            "capacity_bound_fd": bounds_section["fd"],
            "capacity_bound_hcm": bounds_section["hcm"],
            "capacity_bound_fd_span": bounds_span["fd"],
            "section_gap_mixture": c_sec_mix,
            "section_equilibrium": c_sec_eq,
            "recommended": combine_with_bound(c_sec_mix, bounds_section["fd"]),
        },
        "spacings_span": span_fit,
        "spacings_span_coarse": {k: v_ for k, v_ in span_fit_coarse.items() if k != "classes"},
        "spacings_local": local_fit,
    }


def pooled(lanes: list[dict[str, Any]], idm_mean: dict[str, float]) -> dict[str, Any]:
    """Lane-pooled estimators for one window.

    Vehicle-time estimators are weighted by tracked vehicle-time (density);
    section estimators pool as the effective coverage of the summed count,
    ``Σ N / Σ (N / c)``; bounds use the summed flows. ``equilibrium_pooled``
    applies the equilibrium ratio to the pooled density and speed exactly
    as ``scripts/i24_build_replica.py::coverage_factors`` does.
    """
    rho = np.array([ln["inputs"]["rho_tracked_veh_km"] for ln in lanes])
    q_span = np.array([ln["inputs"]["q_tracked_veh_h"] for ln in lanes])
    n_cross = np.array([ln["inputs"]["crossings_at_count_section"] for ln in lanes], dtype=float)
    q_loc = np.array([ln["inputs"]["q_local_veh_h"] for ln in lanes])
    out: dict[str, Any] = {}
    # The builder's exact method: pooled density and speed, then the ratio.
    rho_pool = float(rho.mean()) / 1000.0
    v_pool = float(q_span.sum() / 3600.0) / (rho_pool * len(lanes)) if rho_pool > 0 else math.nan
    out["equilibrium_pooled"] = coverage_equilibrium(
        rho_pool, v_pool, idm_mean, vehicle_length=VEHICLE_LENGTH_M
    )

    def wmean(key: str, w: np.ndarray) -> float:
        vals = np.array([ln["estimators"][key] for ln in lanes], dtype=float)
        ok = np.isfinite(vals) & (w > 0)
        return float(np.sum(vals[ok] * w[ok]) / np.sum(w[ok])) if ok.any() else math.nan

    def effective(key: str) -> float:
        vals = np.array([ln["estimators"][key] for ln in lanes], dtype=float)
        ok = np.isfinite(vals) & (n_cross > 0)
        return float(np.sum(n_cross[ok]) / np.sum(n_cross[ok] / vals[ok])) if ok.any() else math.nan

    for key in (
        "equilibrium",
        "equilibrium_local",
        "gap_moments",
        "gap_mixture",
        "gap_mixture_coarse_classes",
        "gap_mixture_local",
    ):
        out[key] = wmean(key, rho)
    for key in ("section_gap_mixture", "section_equilibrium"):
        out[key] = effective(key)
    q_cross_total = float(n_cross.sum()) / WINDOW_S
    out["crossing_to_local_edie_ratio"] = (
        veh_s_to_veh_h(q_cross_total) / float(q_loc.sum()) if q_loc.sum() > 0 else math.nan
    )
    out["capacity_bound_fd"] = effective("capacity_bound_fd")
    out["capacity_bound_hcm"] = effective("capacity_bound_hcm")
    out["capacity_bound_fd_span"] = wmean("capacity_bound_fd_span", rho)
    # The bound alone is the smallest admissible coverage (largest
    # correction) and is not an estimate: no recommendation without the
    # section estimate (free-flow windows), filled later from neighbours.
    out["recommended"] = (
        combine_with_bound(out["section_gap_mixture"], out["capacity_bound_fd"])
        if math.isfinite(out["section_gap_mixture"])
        else math.nan
    )
    return out


def fill_recommended(windows: list[dict[str, Any]]) -> None:
    """``recommended_filled``: nearest finite ``recommended`` (ffill, then bfill)."""
    vals = pd.Series([w["pooled"]["recommended"] for w in windows], dtype=float)
    filled = vals.ffill().bfill()
    for w, v, f in zip(windows, vals, filled, strict=True):
        w["pooled"]["recommended_filled"] = float(f) if math.isfinite(f) else math.nan
        w["pooled"]["recommended_is_filled"] = not math.isfinite(v)


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
    ap.add_argument("--fleet", default=FLEET_ARTIFACT)
    ap.add_argument("--fd", default=FD_ARTIFACT)
    ap.add_argument(
        "--q-cap-physical-veh-h-lane",
        type=float,
        default=HCM_BASIC_FREEWAY_CAPACITY_PC_H_LN,
        help="physical flow ceiling per lane for capacity_bound_hcm",
    )
    ap.add_argument("--out", default=OUT_ARTIFACT)
    args = ap.parse_args()
    t_start = time.perf_counter()

    idm_mean = json.loads((REPO_ROOT / args.fleet).read_text())["mean"]
    fd = json.loads((REPO_ROOT / args.fd).read_text())
    q_cap_fd_veh_s = float(fd["fd"]["ci95"]["q_max"][1])
    q_caps = {"fd": q_cap_fd_veh_s, "hcm": args.q_cap_physical_veh_h_lane / 3600.0}
    replica_inputs = REPO_ROOT / REPLICA_INPUTS
    if replica_inputs.is_file():
        span_hi = float(
            json.loads(replica_inputs.read_text())["geometry"]["measured_span_data_x_m"][1]
        )
    else:
        span_hi = FALLBACK_SPAN_HI_M

    windows: list[dict[str, Any]] = []
    n_win = round((T_HI_S - T_LO_S) / WINDOW_S)
    for i in range(n_win):
        t_lo = T_LO_S + i * WINDOW_S
        lanes = [
            lane_window(t_lo, lane, span_hi, idm_mean, q_caps)
            for lane in range(MAINLINE_LANES[0], MAINLINE_LANES[1] + 1)
        ]
        pool = pooled(lanes, idm_mean)
        n_cross = sum(ln["inputs"]["crossings_at_count_section"] for ln in lanes)
        windows.append(
            {
                "t_lo_s": t_lo,
                "window": clock(t_lo),
                "in_study_period": STUDY_T_LO_S <= t_lo < STUDY_T_HI_S,
                "crossings_at_count_section": n_cross,
                "q_crossings_veh_h_lane": n_cross / N_LANES * 3600.0 / WINDOW_S,
                "pooled": pool,
                "lanes": lanes,
            }
        )
        print(
            f"{clock(t_lo)}  N={n_cross:4d}  eq={pool['equilibrium_pooled']:.3f}  "
            f"mix={pool['gap_mixture']:.3f}  sec_mix={pool['section_gap_mixture']:.3f}  "
            f"bound_fd={pool['capacity_bound_fd']:.3f}  [{time.perf_counter() - t_start:.0f} s]",
            flush=True,
        )
    fill_recommended(windows)

    # --- peak corrected inflow per lane under each estimator ---------------
    names = (
        "equilibrium_pooled",
        "equilibrium",
        "gap_moments",
        "gap_mixture",
        "gap_mixture_local",
        "section_gap_mixture",
        "section_equilibrium",
        "capacity_bound_fd",
        "capacity_bound_hcm",
        "recommended",
    )
    peaks: dict[str, Any] = {}
    for name in names:
        best = None
        for w in windows:
            if not w["in_study_period"]:
                continue
            c = w["pooled"][name]
            if c is None or not math.isfinite(c) or c <= 0:
                continue
            q_corr = w["q_crossings_veh_h_lane"] / c
            if best is None or q_corr > best["q_corrected_veh_h_lane"]:
                best = {
                    "window": w["window"],
                    "coverage": c,
                    "q_tracked_veh_h_lane": w["q_crossings_veh_h_lane"],
                    "q_corrected_veh_h_lane": q_corr,
                }
        peaks[name] = best
    q_cap_fd_veh_h = veh_s_to_veh_h(q_cap_fd_veh_s)
    for pk in peaks.values():
        if pk is not None:
            pk["exceeds_fd_q_max_upper_ci"] = pk["q_corrected_veh_h_lane"] > q_cap_fd_veh_h

    synthetic = synthetic_validation(idm_mean=idm_mean)
    created_at = subprocess.run(
        ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True, check=True
    ).stdout.strip()
    artifact = {
        "schema_version": 1,
        "created_at": created_at,
        "script": "scripts/i24_coverage.py",
        "data_hash": data_hash(),
        "source": (
            "I-24 MOTION INCEPTION v1.x, 30 Nov 2022 westbound (6386d89efb3ff533c12df167__post10), "
            f"mainline lanes 1-4, data x in [0, {span_hi:.0f}) m, {clock(T_LO_S)}-{clock(T_HI_S)} CST, "
            f"{WINDOW_S:.0f} s windows"
        ),
        "parameters": {
            "window_s": WINDOW_S,
            "span_data_x_m": [0.0, span_hi],
            "local_x_range_m": list(LOCAL_X_RANGE_M),
            "count_section_x_m": COUNT_X_M,
            "sample_dt_s": SAMPLE_DT_S,
            "snapshot_dt_s": SNAPSHOT_DT_S,
            "speed_edges_kmh": list(SPEED_EDGES_KMH),
            "coarse_speed_edges_kmh": list(COARSE_SPEED_EDGES_KMH),
            "v_max_kmh": V_MAX_KMH,
            "min_n_per_class": MIN_N_PER_CLASS,
            "s_dup_m": DEFAULT_S_DUP_M,
            "s_max_m": DEFAULT_S_MAX_M,
            "vehicle_length_m": VEHICLE_LENGTH_M,
            "fleet_artifact": args.fleet,
            "idm_mean": idm_mean,
            "fd_artifact": args.fd,
            "q_cap_fd_veh_h_lane": q_cap_fd_veh_h,
            "q_cap_fd_source": "fd.ci95.q_max[1] (coverage-limited tracked capacity; NOT a facility capacity)",
            "q_cap_hcm_veh_h_lane": args.q_cap_physical_veh_h_lane,
            "q_cap_hcm_source": "HCM 6th ed. basic freeway segment capacity at >= 70 mi/h FFS, pc/h/ln (upper envelope)",
            "study_period_s": [STUDY_T_LO_S, STUDY_T_HI_S],
        },
        "estimator_definitions": {
            "equilibrium_pooled": "rho_tracked(span, 4-lane mean) / rho_eq(pooled v_edie): exactly scripts/i24_build_replica.py::coverage_factors",
            "equilibrium": "per lane rho_tracked(span) / rho_eq(v_edie; IDM mean params, L=5 m), density weighted; NaN in free flow",
            "gap_moments": "(1 - cv_obs^2)/(1 - cv_true^2) per speed class (cv_true from the mixture fit, spacings winsorized at s_max), spacing-count weighted",
            "gap_mixture": "MLE geometric-gamma mixture per speed class (< 60 km/h) on 2 s snapshot spacings over the span, spacing-count weighted",
            "gap_mixture_coarse_classes": "as gap_mixture with 10 km/h classes (class-width sensitivity)",
            "gap_mixture_local": "as gap_mixture on the ramp-free stretch [0, 900) m",
            "capacity_bound_fd": "crossings at x=200 m / (window * q_cap_fd), effective over lanes",
            "capacity_bound_hcm": "crossings at x=200 m / (window * q_cap_hcm), effective over lanes",
            "capacity_bound_fd_span": "Edie flow over the span / q_cap_fd",
            "section_gap_mixture": "gap_mixture_local * crossings / (window * q_local_edie): coverage of the crossing count at x=200 m",
            "section_equilibrium": "equilibrium_local * crossings / (window * q_local_edie)",
            "recommended": "max(section_gap_mixture, capacity_bound_fd); NaN where the section estimate is undefined (free flow)",
            "recommended_filled": "recommended with undefined windows taking the nearest defined window's value (forward, then backward fill); recommended_is_filled marks them",
        },
        "windows": windows,
        "peak_corrected_inflow": peaks,
        "synthetic_validation": synthetic,
        "recommendation": None,
    }
    artifact["recommendation"] = {
        "estimator": "recommended",
        "rule": "max(section_gap_mixture, capacity_bound_fd)",
        "note": (
            "section_gap_mixture is the only estimator that (a) needs no car-following model, "
            "(b) applies to the crossing count that is the demand input, and (c) recovers a known "
            "coverage on synthetic lanes with a homogeneous spacing scale (see synthetic_validation); "
            "it is biased LOW where the true spacing scale is heterogeneous within a speed class "
            "and uninformative in free flow, so the FD capacity bound is applied as a floor. "
            "Windows outside the congested regime carry the bound only."
        ),
    }
    out = REPO_ROOT / args.out
    out.write_text(json.dumps(_clean(artifact), indent=2))

    # --- summary tables ----------------------------------------------------
    print("\nper window (lanes pooled):")
    hdr = (
        f"{'win':5s} {'N':>5s} {'q_trk':>6s} {'eqpool':>6s} {'eq':>6s} {'mom':>6s} {'mix':>6s} "
        f"{'mix10':>6s} {'mixloc':>6s} {'xratio':>6s} {'secmix':>6s} {'seceq':>6s} {'b_fd':>6s} "
        f"{'b_hcm':>6s} {'rec':>6s} {'recfil':>6s}"
    )
    print(hdr)

    def fmt(v: float | None) -> str:
        return f"{v:6.3f}" if v is not None and math.isfinite(v) else "   nan"

    for w in windows:
        p = w["pooled"]
        print(
            f"{w['window']:5s} {w['crossings_at_count_section']:5d} {w['q_crossings_veh_h_lane']:6.0f} "
            f"{fmt(p['equilibrium_pooled'])} {fmt(p['equilibrium'])} {fmt(p['gap_moments'])} "
            f"{fmt(p['gap_mixture'])} {fmt(p['gap_mixture_coarse_classes'])} "
            f"{fmt(p['gap_mixture_local'])} {fmt(p['crossing_to_local_edie_ratio'])} "
            f"{fmt(p['section_gap_mixture'])} {fmt(p['section_equilibrium'])} "
            f"{fmt(p['capacity_bound_fd'])} {fmt(p['capacity_bound_hcm'])} "
            f"{fmt(p['recommended'])} {fmt(p['recommended_filled'])}"
        )
    print("\nper lane, study period (eq / mix / mix_local / xratio / sec_mix / dup%):")
    for w in windows:
        if not w["in_study_period"]:
            continue
        cells = []
        for ln in w["lanes"]:
            e, i = ln["estimators"], ln["inputs"]
            cells.append(
                f"L{ln['lane']} {fmt(e['equilibrium']).strip()}/{fmt(e['gap_mixture']).strip()}/"
                f"{fmt(e['gap_mixture_local']).strip()}/{fmt(i['crossing_to_local_edie_ratio']).strip()}/"
                f"{fmt(e['section_gap_mixture']).strip()}/"
                f"{100 * ln['spacings_span']['duplicate_fraction']:.1f}"
            )
        print(f"{w['window']:5s} " + "  ".join(cells))
    print(
        f"\npeak corrected mainline inflow per lane (study period), FD q_max upper CI = {q_cap_fd_veh_h:.0f} veh/h/lane:"
    )
    for name, pk in peaks.items():
        if pk is None:
            print(f"  {name:22s} n/a")
            continue
        print(
            f"  {name:22s} {pk['window']}  c={pk['coverage']:.3f}  tracked {pk['q_tracked_veh_h_lane']:.0f} "
            f"-> corrected {pk['q_corrected_veh_h_lane']:.0f} veh/h/lane"
            f"{'  EXCEEDS FD q_max' if pk['exceeds_fd_q_max_upper_ci'] else ''}"
        )
    print(f"\nsynthetic validation ({len(synthetic)} rows):")
    print(
        f"{'regime':26s} {'c':>4s} {'cv_t':>5s} {'moments':>8s} {'mixture':>8s} {'equil':>8s} {'bound1.1':>8s}"
    )
    for r in synthetic:
        print(
            f"{r['regime']:26s} {r['c_true']:4.1f} {r['cv_true']:5.2f} {fmt(r['gap_moments']):>8s} "
            f"{fmt(r['gap_mixture']):>8s} {fmt(r['equilibrium']):>8s} {fmt(r['capacity_bound_1p1']):>8s}"
        )
    print(f"\n-> {out}  ({time.perf_counter() - t_start:.0f} s)")


if __name__ == "__main__":
    main()
