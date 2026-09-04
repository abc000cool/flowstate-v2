"""ROADMAP §1.3 — build the ``i24_replica`` scenario from data and geometry.

Turns the processed I-24 MOTION westbound day (``scripts/i24_extract.py``),
the OSM network (``data/osm/i24_motion.osm``) and the auxiliary landmark
layers into a runnable, data-driven scenario:

* **Network** — the westbound OSM mainline chain from ~2.3 km upstream of the
  testbed (natural insertion buffer, edge ``635462235``) to the Bell Road
  interchange, plus the exit edge ``634155175`` that hosts the measured
  downstream boundary. The measured span is data ``x`` ∈ [0, 5492) m
  (MM 62.7 → the Bell Road collector road), 3.4 of the instrument's 4 miles:
  the last edge before Bell Road ends there and OSM edges are not split.
* **Geometry mapping** — mile-marker and ramp landmarks projected onto the
  chain (``scripts/i24_geometry.py``): a linear fit of chain position against
  mile marker places data ``x = 0`` and sets the chain-metre-per-data-metre
  scale (≈ 0.98; the fit reproduces the on-ramp gore positions seen in the
  ramp-lane data to ~10 m, a single MM 60 anchor does not).
* **Demand** — fragment crossings per 5-min window: mainline inflow at
  ``x = 200`` m (the first high-coverage section, upstream of every ramp),
  on-ramp inflows from ramp-lane (``lane ≥ 5``) crossings just downstream of
  each gore, off-ramp exit fractions as ramp-lane crossings in the diverge
  zone over mainline crossings just upstream of it. **All counts are lower
  bounds at the instrument's tracking coverage** (docs/I24_DATA.md); they are
  used as-is and never inflated, and the observed-side comparison in
  ``scripts/i24_validate.py`` carries the same bias.
* **Boundary** — observed mean mainline speed in the last 945 m of the
  instrument (data ``x`` ∈ [5492, 6437) m, i.e. just downstream of the
  measured span) per 30 s window, applied to the exit edge (FHWA measured
  boundary practice; docs/M3_US101_VALIDATION.md §2).
* **Study period** — 06:30–08:30 CST (data ``t`` 1800–9000 s): onset,
  peak and the start of recovery per ``artifacts/i24_wb_overview.json``,
  preceded by a 600 s warmup at the first window's demand.

Outputs: ``scenarios/i24_replica.yaml``, ``artifacts/demand_i24.json``
(mainline DemandProfile) and ``artifacts/i24_replica_inputs.json`` (every
derived number: mapping constants, ramp flows, exit fractions, boundary
schedule, section choices, provenance).

Run: ``uv run --no-sync python scripts/i24_build_replica.py``
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import sumolib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_data import (
    REPO_ROOT,
    TESTBED_LENGTH_M,
    WB_DIR,
    clock,
    data_hash,
    load_mainline,
)
from i24_geometry import OSM_FILE, chain_geometry, check_projection, read_projection

from calibration.loaders.i24motion import load_i24_parquet
from flowstate_core.artifacts import DemandProfile
from flowstate_core.config import ScenarioConfig, config_hash
from microsim.networks import osm_import

# --- study design -----------------------------------------------------------
T_STUDY_LO_S = 1800.0  # 06:30 CST
T_STUDY_HI_S = 9000.0  # 08:30 CST
WARMUP_S = 600.0
WINDOW_S = 300.0
BOUNDARY_WINDOW_S = 30.0

#: Westbound chain edges used by the replica, upstream → downstream. The
#: first three edges are the insertion buffer (2.2 km before MM 62.7); the
#: last is the 992 m exit edge hosting the boundary (starts at the Bell Road
#: collector road, data x ≈ 5492 m).
CORRIDOR_EDGES = (
    "635462235",
    "173720368",
    "27828382",
    "974949114",
    "977008894",
    "977008893",
    "977008892",
    "977008891",
    "992666043",
    "992666042",
    "108161916",
    "108162464",
    "634155175",
)
"""Raw OSM way ids (``osm_import(geometry_remove=False)`` granularity). A
geometry-joined edge id names only one of its member ways, so a corridor
pruned by joined ids silently loses the rest (docs/gallery/README.md)."""

#: Ramps inside the measured span (OSM link ids; see scripts/i24_geometry.py
#: output and data/i24motion/auxiliary_information/ramp_and_landmark_layer.csv).
#: ``count_x_m`` is the data-x section where the ramp-lane crossings are
#: counted; ``ref_x_m`` (off-ramps) the mainline section the fraction is
#: taken against.
RAMPS = (
    {
        "name": "Old Hickory Blvd on-ramp",
        "kind": "on",
        "edges": ["1070403831#1"],
        "attach_edge": "977008894",
        "count_x_m": 950.0,
    },
    {
        "name": "Hickory Hollow Pkwy off-ramp",
        "kind": "off",
        "edges": ["1138588478"],
        "attach_edge": "977008892",
        "count_x_m": 3700.0,
        "ref_x_m": 3200.0,
    },
    {
        "name": "Hickory Hollow Pkwy on-ramp",
        "kind": "on",
        "edges": ["19441652#0", "19441652#1"],
        "attach_edge": "992666043",
        "count_x_m": 4600.0,
    },
    {
        "name": "Bell Road off-ramp (collector road)",
        "kind": "off",
        "edges": ["19442635"],
        "attach_edge": "992666043",
        "count_x_m": 5050.0,
        "ref_x_m": 4800.0,
    },
)

MAINLINE_COUNT_X_M = 200.0
BOUNDARY_X_RANGE_M = (5492.0, TESTBED_LENGTH_M)

FLEET_ARTIFACT = "artifacts/idm_i24_capacity.json"  # step-1 capacity-calibrated population (docs/I24_CAPACITY.md)

#: SUMO ``lcStrategic`` for the replica fleet. With SUMO's default 1.0,
#: exiting vehicles still in an inner lane at the Hickory Hollow diverge stop
#: at the edge end and wait for a gap, creating a fixed bottleneck the data
#: does not have (894 stalled 10-s samples in 2 h at the diverge, 3.0-3.9 km
#: mean speed 29.8 km/h; 44 samples and 84.7 km/h at 5.0; 3 samples and
#: 86.8 km/h at 20.0 — same seed, same demand; docs/I24_VALIDATION.md). 5.0
#: is the smallest tested value that removes the artifact.
LC_STRATEGIC = 5.0

#: SUMO ``lcKeepRight`` for the replica fleet. US freeways carry no keep-right
#: obligation; the observed vehicle-time by lane on the span (06:30-08:30) is
#: 30/24/20/26 % left to right with all lanes at similar speed. With SUMO's
#: default 1.0 the replica spreads 24/25/25/27 % but crawls in the two right
#: lanes through the Old Hickory merge (11,535 stalled 10-s samples in 2 h);
#: at 0 it gives 32/26/22/20 % and 200 stalled samples (same seed, same
#: demand; docs/I24_VALIDATION.md). Car-following untouched.
LC_KEEP_RIGHT = 0.0


def crossings_per_window(df: pd.DataFrame, x_s: float, t_lo: float, t_hi: float) -> np.ndarray:
    """Fragment crossings of section ``x_s`` per 5-min window in [t_lo, t_hi)."""
    df = df.sort_values(["veh_id", "t"], kind="stable")
    same = df["veh_id"].to_numpy()[1:] == df["veh_id"].to_numpy()[:-1]
    x = df["x"].to_numpy()
    t = df["t"].to_numpy()
    x_prev, x_cur, t_cur = x[:-1][same], x[1:][same], t[1:][same]
    hit = (x_prev < x_s) & (x_cur >= x_s) & (t_cur >= t_lo) & (t_cur < t_hi)
    n_win = round((t_hi - t_lo) / WINDOW_S)
    w = ((t_cur[hit] - t_lo) // WINDOW_S).astype(np.int64)
    return np.bincount(w, minlength=n_win)[:n_win]


def boundary_schedule(t_lo: float, t_hi: float) -> list[tuple[float, float]]:
    """Observed mean mainline speed in the boundary zone per 30 s (data time)."""
    tail = load_mainline(t_range_s=(t_lo, t_hi), x_range_m=BOUNDARY_X_RANGE_M, columns=["t", "v"])
    n_win = round((t_hi - t_lo) / BOUNDARY_WINDOW_S)
    w = ((tail["t"].to_numpy() - t_lo) // BOUNDARY_WINDOW_S).astype(np.int64)
    sums = np.bincount(w, weights=tail["v"].to_numpy(), minlength=n_win)[:n_win]
    cnts = np.bincount(w, minlength=n_win)[:n_win]
    vals = np.full(n_win, np.nan)
    np.divide(sums, cnts, out=vals, where=cnts > 0)
    filled = pd.Series(vals).ffill().bfill().to_numpy()
    if np.isnan(filled).any():
        raise ValueError("boundary zone has no samples at all")
    return [(t_lo + i * BOUNDARY_WINDOW_S, float(max(v, 0.5))) for i, v in enumerate(filled)]


COVERAGE_WINDOW_S = 900.0
VEHICLE_LENGTH_M = 5.0


def coverage_factors(t_lo: float, t_hi: float, span_hi_data_x: float, idm_mean: dict) -> list[dict]:
    """Apparent tracking coverage per 15-min window over the measured span.

    Edie density of the tracked mainline fragments (lanes 1-4) divided by the
    density the calibrated IDM population would hold at the observed Edie
    speed: ``rho_eq(v) = 1 / (s_eq(v) + L)`` with
    ``s_eq = (s0 + v T) / sqrt(1 - (v/v0)^4)`` (CLAUDE.md §9 closed form) and
    ``L`` the 5 m vType length. Meaningful only where traffic is congested
    enough for spacing to sit at equilibrium (``v`` below ~0.9 v0); windows
    outside that regime inherit the nearest congested window's value. The
    factor is clipped to (0, 1]. Speeds are coverage-robust (TTD/TTT), so
    the ratio isolates the share of vehicle-time the instrument tracked.
    """
    from validation.fields import density_field, flow_field

    df = load_mainline(
        t_range_s=(t_lo, t_hi), x_range_m=(0.0, span_hi_data_x), columns=["t", "x", "v", "veh_id"]
    )
    dens = (
        density_field(df, dt_bin=COVERAGE_WINDOW_S, dx_bin=span_hi_data_x, sample_dt=0.2).density[
            :, 0
        ]
        / 4.0
    )
    flow = (
        flow_field(df, dt_bin=COVERAGE_WINDOW_S, dx_bin=span_hi_data_x, sample_dt=0.2).flow[:, 0]
        / 4.0
    )
    v0, T, s0 = idm_mean["v0"], idm_mean["T"], idm_mean["s0"]
    rows = []
    for i, (rho, q) in enumerate(zip(dens, flow, strict=True)):
        v = q / rho if rho > 0 else math.nan
        if math.isfinite(v) and v < 0.9 * v0:
            s_eq = (s0 + v * T) / math.sqrt(1.0 - (v / v0) ** 4)
            rho_eq = 1.0 / (s_eq + VEHICLE_LENGTH_M)
            factor = min(max(rho / rho_eq, 1e-3), 1.0)
        else:
            rho_eq, factor = math.nan, math.nan
        rows.append(
            {
                "t_lo_s": t_lo + i * COVERAGE_WINDOW_S,
                "window": clock(t_lo + i * COVERAGE_WINDOW_S),
                "rho_tracked_veh_km_lane": rho * 1000.0,
                "v_edie_kmh": v * 3.6,
                "rho_eq_veh_km_lane": rho_eq * 1000.0 if math.isfinite(rho_eq) else None,
                "coverage": factor if math.isfinite(factor) else None,
            }
        )
    vals = pd.Series([r["coverage"] for r in rows], dtype=float).ffill().bfill()
    if vals.isna().any():
        raise ValueError("no congested window to estimate coverage from")
    for r, v in zip(rows, vals, strict=True):
        r["coverage_used"] = float(v)
    return rows


def to_sim_time(t_data: float) -> float:
    """Data time [s since 06:00] → sim time (warmup precedes the study period)."""
    return t_data - T_STUDY_LO_S + WARMUP_S


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--allow-missing-fleet", action="store_true")
    ap.add_argument(
        "--coverage-estimator",
        choices=("equilibrium", "gap_mixture", "section_gap_mixture", "recommended"),
        default="equilibrium",
        help="coverage factor for the corrected arm: the IDM-equilibrium ratio computed here "
        "(default, keeps config hashes) or a gap-based estimator read from "
        "artifacts/i24_coverage.json (scripts/i24_coverage.py, docs/I24_DATA.md)",
    )
    args = ap.parse_args()
    fleet_path = REPO_ROOT / FLEET_ARTIFACT
    if not fleet_path.is_file() and not args.allow_missing_fleet:
        raise SystemExit(f"{fleet_path} missing — run scripts/fit_idm_i24.py first")

    # --- geometry ---------------------------------------------------------
    workdir = REPO_ROOT / "data" / "i24motion" / "processed" / "net_raw"
    bundle = osm_import(osm_file=OSM_FILE, workdir=workdir, geometry_remove=False)
    net = sumolib.net.readNet(str(bundle.net_path))
    proj = read_projection(bundle.net_path)
    proj_err = check_projection(net, proj, OSM_FILE)
    geo = chain_geometry(net, proj)
    chain_off = dict(zip(geo.edge_ids, geo.offsets, strict=True))
    chain_len = dict(zip(geo.edge_ids, geo.edge_lengths, strict=True))
    for e in CORRIDOR_EDGES:
        if e not in chain_off:
            raise SystemExit(f"edge {e} not on the westbound chain")
    sim_origin_chain = chain_off[CORRIDOR_EDGES[0]]
    span_lo_sim = geo.chain_pos_at_mm_upstream - sim_origin_chain
    span_hi_sim = chain_off[CORRIDOR_EDGES[-1]] - sim_origin_chain
    scale = geo.slope_m_per_mile / 1609.344  # chain metres per data metre

    def sim_x_of_data_x(x: float) -> float:
        return geo.chain_pos_of_data_x(x) - sim_origin_chain

    # --- demand -----------------------------------------------------------
    t_lo, t_hi = T_STUDY_LO_S, T_STUDY_HI_S
    n_win = round((t_hi - t_lo) / WINDOW_S)
    main_df = load_mainline(t_range_s=(t_lo - 60.0, t_hi + 60.0), columns=["t", "veh_id", "x"])
    main_counts = crossings_per_window(main_df, MAINLINE_COUNT_X_M, t_lo, t_hi)
    ramp_df = load_i24_parquet(
        WB_DIR, t_range_s=(t_lo - 60.0, t_hi + 60.0), lanes=(5, 9), columns=["t", "veh_id", "x"]
    )
    ramp_specs = []
    ramp_records = []
    for r in RAMPS:
        cnt = crossings_per_window(ramp_df, r["count_x_m"], t_lo, t_hi)
        rec = {
            "name": r["name"],
            "kind": r["kind"],
            "edges": r["edges"],
            "attach_edge": r["attach_edge"],
            "count_x_m": r["count_x_m"],
            "ramp_lane_crossings": cnt.tolist(),
            "ramp_lane_veh_h": (cnt * 3600.0 / WINDOW_S).round(1).tolist(),
        }
        if r["kind"] == "on":
            steps = [
                (to_sim_time(t_lo + i * WINDOW_S), float(cnt[i] / WINDOW_S)) for i in range(n_win)
            ]
            steps[0] = (0.0, steps[0][1])  # first rate also covers the warmup
            spec = {
                "kind": "on",
                "edges": r["edges"],
                "attach_edge": r["attach_edge"],
                "inflow": [[t, round(q, 6)] for t, q in steps],
                "name": r["name"],
            }
        else:
            ref = crossings_per_window(main_df, r["ref_x_m"], t_lo, t_hi)
            frac = np.divide(cnt, ref, out=np.zeros(n_win), where=ref > 0)
            frac = np.clip(frac, 0.0, 1.0)
            rec["ref_x_m"] = r["ref_x_m"]
            rec["mainline_ref_crossings"] = ref.tolist()
            rec["exit_fraction"] = frac.round(4).tolist()
            steps = [(to_sim_time(t_lo + i * WINDOW_S), float(frac[i])) for i in range(n_win)]
            steps[0] = (0.0, steps[0][1])
            spec = {
                "kind": "off",
                "edges": r["edges"],
                "attach_edge": r["attach_edge"],
                "exit_fraction": [[t, round(f, 6)] for t, f in steps],
                "name": r["name"],
            }
        ramp_specs.append(spec)
        ramp_records.append(rec)

    inflow_steps = [
        (to_sim_time(t_lo + i * WINDOW_S), float(main_counts[i] / WINDOW_S)) for i in range(n_win)
    ]
    inflow_steps[0] = (0.0, inflow_steps[0][1])

    # --- coverage-corrected demand (second arm) ---------------------------
    span_hi_data = geo.data_x_of_chain_pos(chain_off[CORRIDOR_EDGES[-1]])
    cov_rows = None
    if fleet_path.is_file():
        idm_mean = json.loads(fleet_path.read_text())["mean"]
        cov_rows = coverage_factors(t_lo, t_hi, span_hi_data, idm_mean)
        coverage_source: dict = {"estimator": "equilibrium", "artifact": None}
        if args.coverage_estimator != "equilibrium":
            cov_art = json.loads((REPO_ROOT / "artifacts" / "i24_coverage.json").read_text())
            key = (
                "recommended_filled"
                if args.coverage_estimator == "recommended"
                else args.coverage_estimator
            )
            by_t = {float(w["t_lo_s"]): w["pooled"].get(key) for w in cov_art["windows"]}
            for r in cov_rows:
                v = by_t.get(float(r["t_lo_s"]))
                if v is None or not (0.0 < float(v) <= 1.0):
                    raise SystemExit(
                        f"coverage estimator {key!r} has no usable value for window t_lo={r['t_lo_s']}"
                    )
                r["coverage_equilibrium"] = r["coverage_used"]
                r["coverage_used"] = float(v)
            coverage_source = {
                "estimator": key,
                "artifact": "artifacts/i24_coverage.json",
                "artifact_created_at": cov_art.get("created_at"),
                "artifact_data_hash": cov_art.get("data_hash"),
            }

    def corrected(steps: list[tuple[float, float]]) -> list[tuple[float, float]]:
        assert cov_rows is not None
        out = []
        for i, (t_sim, q) in enumerate(steps):
            t_data = t_lo + i * WINDOW_S
            k = min(int((t_data - t_lo) // COVERAGE_WINDOW_S), len(cov_rows) - 1)
            out.append((t_sim, q / cov_rows[k]["coverage_used"]))
        return out

    # --- boundary ---------------------------------------------------------
    sched = boundary_schedule(t_lo, t_hi)
    bsteps = [(0.0, sched[0][1])] + [(to_sim_time(t), v) for t, v in sched[1:]]

    created_at = subprocess.run(
        ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True, check=True
    ).stdout.strip()
    dh = data_hash()
    demand = DemandProfile(
        created_at=created_at,
        source=(
            "I-24 MOTION INCEPTION v1.x westbound, 30 Nov 2022: mainline (lanes 1-4) fragment "
            f"crossings at data x = {MAINLINE_COUNT_X_M:g} m (MM 62.7 - {MAINLINE_COUNT_X_M / 1609.344:.3f} mi) "
            f"per 5-min window, {clock(t_lo)}-{clock(t_hi)} CST, shifted by the {WARMUP_S:g} s "
            "warmup (sim t = data t - 1800 + 600). LOWER BOUND at the instrument's tracking "
            "coverage; not inflated."
        ),
        data_hash=dh,
        steps=[(t, round(q, 6)) for t, q in inflow_steps],
        geh_vs_counts=None,
    )
    demand_path = REPO_ROOT / "artifacts" / "demand_i24.json"
    demand.save(demand_path)

    # --- scenario ---------------------------------------------------------
    duration_s = WARMUP_S + (t_hi - t_lo)
    scenario = {
        "name": "i24_replica",
        "tier": "micro",
        "network": {
            "kind": "osm",
            "osm_file": str(OSM_FILE.relative_to(REPO_ROOT)),
            "corridor_edges": list(CORRIDOR_EDGES),
            "inflow": [[t, round(q, 6)] for t, q in inflow_steps],
            "boundary": {"kind": "speed_schedule", "steps": [[t, round(v, 4)] for t, v in bsteps]},
            "ramps": ramp_specs,
        },
        "fleet": {
            "model": "IDM",
            "idm_calibration": FLEET_ARTIFACT,
            "lc_strategic": LC_STRATEGIC,
            "lc_keep_right": LC_KEEP_RIGHT,
        },
        "av": {"penetration": 0.0, "compliance": 1.0, "controller": None, "controller_params": {}},
        "sim": {
            "duration_s": duration_s,
            "step_length_s": 0.5,
            "action_step_s": 0.5,
            "warmup_s": WARMUP_S,
            "output_hz": 2.0,
        },
        "perturbation": None,
        "seed": 42,
        "replicates": 20,
    }
    cfg = ScenarioConfig.model_validate(scenario)
    header = f"""# i24_replica — I-24 westbound, Nashville TN (I-24 MOTION testbed), ROADMAP §1.3.
#
# GENERATED by scripts/i24_build_replica.py from the processed I-24 MOTION
# INCEPTION day (30 Nov 2022; data hash {dh[:12]}…) — do not edit by hand,
# re-run the builder. Every number here traces to
# artifacts/i24_replica_inputs.json.
#
# Network: OSM westbound mainline chain (data/osm/i24_motion.osm) from ~2.3 km
# upstream of MM 62.7 (insertion buffer, first edge) to the Bell Road
# interchange; the last edge (634155175) is the exit edge carrying the measured
# downstream boundary. Measured span = sim x in [{span_lo_sim:.1f}, {span_hi_sim:.1f}) m
# = data x in [0, {geo.data_x_of_chain_pos(chain_off[CORRIDOR_EDGES[-1]]):.0f}) m (MM 62.7 -> Bell Road, 3.4 mi).
# Ramps: Old Hickory on, Hickory Hollow off/on, Bell Road off (collector road);
# the Bell Road on-ramp merges onto the exit edge and is not modeled.
# Demand: fragment crossings per 5 min ({clock(t_lo)}-{clock(t_hi)} CST), mainline at
# data x = {MAINLINE_COUNT_X_M:g} m, ramp lanes at each gore; LOWER BOUNDS at tracking coverage.
# Boundary: observed mean speed in data x [{BOUNDARY_X_RANGE_M[0]:.0f}, {BOUNDARY_X_RANGE_M[1]:.0f}) m per 30 s.
# Sim t = data t - {T_STUDY_LO_S:g} + {WARMUP_S:g} (warmup at the first window's demand).
# Fleet: {FLEET_ARTIFACT} (IDM population fitted on the same day's episodes, mean T
# scaled to the tracked capacity per FHWA Vol. III step 1, docs/I24_CAPACITY.md);
# lc_strategic {LC_STRATEGIC:g} removes SUMO's diverge lane-change stall and lc_keep_right
# {LC_KEEP_RIGHT:g} matches the observed lane use (both measured; see builder constants).
# seeded=False: the boundary and ramp inputs are calibration inputs, not shocks.
"""
    out_yaml = REPO_ROOT / "scenarios" / "i24_replica.yaml"
    cfg.to_yaml(out_yaml)
    out_yaml.write_text(header + out_yaml.read_text())

    corrected_hash = None
    if cov_rows is not None:
        sc2 = json.loads(json.dumps(scenario))
        sc2["name"] = "i24_replica_corrected"
        sc2["network"]["inflow"] = [[t, round(q, 6)] for t, q in corrected(inflow_steps)]
        for spec in sc2["network"]["ramps"]:
            if spec["kind"] == "on":
                spec["inflow"] = [
                    [t, round(q, 6)] for t, q in corrected([(t, q) for t, q in spec["inflow"]])
                ]
        cfg2 = ScenarioConfig.model_validate(sc2)
        corrected_hash = config_hash(cfg2)
        header2 = (
            header.replace(
                "# i24_replica — I-24 westbound",
                "# i24_replica_corrected — I-24 westbound",
            )
            + f"""#
# COVERAGE-CORRECTED ARM: identical to i24_replica except that the mainline and
# on-ramp inflows are divided by the instrument's apparent tracking coverage per
# 15-min window (tracked Edie density / density the calibrated IDM population
# holds at the observed Edie speed; {min(r["coverage_used"] for r in cov_rows):.2f}-{max(r["coverage_used"] for r in cov_rows):.2f} here — see
# artifacts/i24_replica_inputs.json 'coverage'). Exit fractions and the boundary
# schedule are ratios/speeds and need no correction. This is a documented
# instrument correction derived from the data itself, not a fit to any
# validation target; both arms are reported side by side.
"""
        )
        out2 = REPO_ROOT / "scenarios" / "i24_replica_corrected.yaml"
        cfg2.to_yaml(out2)
        out2.write_text(header2 + out2.read_text())

    inputs = {
        "created_at": created_at,
        "data_hash": dh,
        "osm_file": str(OSM_FILE.relative_to(REPO_ROOT)),
        "config_hash": config_hash(cfg),
        "study_period": {
            "t_lo_s": t_lo,
            "t_hi_s": t_hi,
            "clock": f"{clock(t_lo)}-{clock(t_hi)} CST",
            "warmup_s": WARMUP_S,
            "duration_s": duration_s,
        },
        "geometry": {
            "projection_check_worst_m": proj_err,
            "corridor_edges": list(CORRIDOR_EDGES),
            "edge_lengths_m": [chain_len[e] for e in CORRIDOR_EDGES],
            "edge_lanes": [int(net.getEdge(e).getLaneNumber()) for e in CORRIDOR_EDGES],
            "sim_origin_chain_m": sim_origin_chain,
            "chain_m_per_mile_fit": geo.slope_m_per_mile,
            "chain_m_per_data_m": scale,
            "mm_fit_residual_rms_m": geo.residual_rms_m,
            "data_x0_chain_m": geo.chain_pos_at_mm_upstream,
            "measured_span_sim_x_m": [span_lo_sim, span_hi_sim],
            "measured_span_data_x_m": [0.0, geo.data_x_of_chain_pos(chain_off[CORRIDOR_EDGES[-1]])],
            "sim_x_of_data_x": {"a": -sim_origin_chain + geo.chain_pos_at_mm_upstream, "b": scale},
            "mile_markers_chain_m": {str(k): v for k, v in sorted(geo.mm_chain_pos.items())},
            "ramp_landmarks_chain_m": geo.ramp_chain_pos,
        },
        "mainline": {
            "count_x_m": MAINLINE_COUNT_X_M,
            "crossings": main_counts.tolist(),
            "veh_h": (main_counts * 3600.0 / WINDOW_S).round(1).tolist(),
            "inflow_steps_sim": [[t, round(q, 6)] for t, q in inflow_steps],
        },
        "ramps": ramp_records,
        "boundary": {
            "x_range_m": list(BOUNDARY_X_RANGE_M),
            "window_s": BOUNDARY_WINDOW_S,
            "schedule_data_time": [[t, round(v, 4)] for t, v in sched],
            "v_min_ms": min(v for _, v in sched),
            "v_max_ms": max(v for _, v in sched),
        },
        "fleet_artifact": FLEET_ARTIFACT,
        "fleet_artifact_present": fleet_path.is_file(),
        "coverage": (
            {
                "window_s": COVERAGE_WINDOW_S,
                "method": "tracked Edie density (lanes 1-4, measured span) / IDM-population "
                "equilibrium density at the Edie speed; clipped to (0, 1]; free-flow "
                "windows inherit the nearest congested value",
                "rows": cov_rows,
                "source": coverage_source,
            }
            if cov_rows is not None
            else None
        ),
        "corrected_config_hash": corrected_hash,
        "notes": [
            "all crossing counts are fragment crossings: lower bounds at the local tracking "
            "coverage (docs/I24_DATA.md); never inflated",
            "off-ramp exit fractions = ramp-lane crossings in the diverge zone / mainline "
            "crossings just upstream; the Bell Road value is taken inside the Hickory "
            "Hollow-Bell Road weaving section and mixes merging and diverging vehicles",
            "the Bell Road on-ramp merges onto the exit edge (outside the measured span) "
            "and is not modeled; its effect on the span enters through the observed "
            "boundary speed",
            f"chain/data scale {scale:.4f}: OSM chain metres per data metre from the "
            "mile-marker fit; ramp gores from the fit match the ramp-lane data to ~10 m",
        ],
    }
    (REPO_ROOT / "artifacts" / "i24_replica_inputs.json").write_text(json.dumps(inputs, indent=2))

    print(
        f"projection check {proj_err:.3f} m; chain scale {scale:.4f}; MM fit RMS {geo.residual_rms_m:.1f} m"
    )
    print(
        f"measured span sim x [{span_lo_sim:.1f}, {span_hi_sim:.1f}) m; duration {duration_s:.0f} s"
    )
    print("mainline veh/h:", inputs["mainline"]["veh_h"])
    for rec in ramp_records:
        key = "exit_fraction" if rec["kind"] == "off" else "ramp_lane_veh_h"
        print(f"  {rec['name']}: {rec[key]}")
    print(
        f"boundary v [{inputs['boundary']['v_min_ms']:.1f}, {inputs['boundary']['v_max_ms']:.1f}] m/s over {len(sched)} steps"
    )
    print(f"-> {out_yaml} (config {config_hash(cfg)}), {demand_path}")
    if cov_rows is not None:
        print("coverage per 15 min:", [round(r["coverage_used"], 3) for r in cov_rows])
        print(f"-> scenarios/i24_replica_corrected.yaml (config {corrected_hash})")
    if math.isnan(scale):
        raise SystemExit("geometry fit failed")


if __name__ == "__main__":
    main()
