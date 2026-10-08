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
* **Ramp through traffic (amendment B2, opt-in)** — ``--ramp-through-traffic
  exclude`` subtracts, per 5-min window and before any coverage scaling, the
  ramp-lane crossings ``scripts/i24_count_consistency.py`` flags as through
  traffic (on-ramps: vehicles in lanes 1-4 before the count; off-ramps:
  vehicles that return to them after it), clipped at zero
  (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.3; proposed, not adopted). Only the
  ramp demand changes; it writes a new family (a ``--suffix`` ending in
  ``rc``). The default ``keep`` writes what the builder wrote before, byte for
  byte, and never reads the artifact.
* **C7b consistency corrections (opt-in; docs/I24_CONSISTENCY_C7B.md, proposed,
  not adopted)** — ``--demand-coverage recommended`` divides the corrected
  arm's mainline and on-ramp inflows by the coverage the validator's link-flow
  row divides the observed counts by (``artifacts/i24_coverage.json``
  ``pooled.recommended_filled`` per 15-min window) instead of the equilibrium
  estimate, so the planned demand over the row's target is one constant in
  every window. ``--insertion-shift-s free_flow`` moves the mainline inflow's
  steps (after the first) earlier by the free-flow time from the network entry
  to the count section at the fleet's mean ``v0``, so a vehicle counted at data
  x = 200 m in a window is inserted that long before it (the inflow is
  otherwise stamped at the count's clock time while vehicles enter 2.45 km
  upstream). Each writes a new family (its letter in the ``--suffix``:
  ``rc`` + ``c`` + ``s``, e.g. ``flow_rccs``); the defaults write what the
  builder wrote before, byte for byte.
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
import hashlib
import json
import math
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

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

#: Amendment B2 (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.3; proposed, not
#: adopted): the count-consistency artifact (scripts/i24_count_consistency.py)
#: whose through-traffic flags ``--ramp-through-traffic exclude`` subtracts, and
#: the flagged series per ramp kind — vehicles in lanes 1-4 before an on-ramp
#: count, vehicles that return to them after an off-ramp count.
COUNT_CONSISTENCY_ARTIFACT = "artifacts/i24_count_consistency.json"
THROUGH_TRAFFIC_SERIES = {"on": "prior_mainline_per_window", "off": "later_mainline_per_window"}

#: The coverage artifact whose recommended estimator ``--coverage-estimator`` /
#: ``--demand-coverage recommended`` read (``scripts/i24_coverage.py``); the
#: validator's link-flow target divides the observed counts by the same values.
COVERAGE_ARTIFACT = "artifacts/i24_coverage.json"

#: C7b's insertion shift is quoted to 0.1 s (docs/I24_CONSISTENCY_C7B.md): SUMO
#: inserts on 0.5-s steps, so nothing finer reaches a run.
INSERTION_SHIFT_DECIMALS = 1
#: ``--insertion-shift-s free_flow``: the shift is computed, not given.
INSERTION_SHIFT_FREE_FLOW = "free_flow"

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
CORRECTED_OSM_FILE = REPO_ROOT / "data" / "osm" / "i24_motion_corrected.osm"
HEAVY_FLEET_ARTIFACT = "artifacts/idm_i24_heavy.json"
HEAVY_EMISSION_CLASS = "HBEFA4/TT_AT_gt34-40t_Euro-VI_A-C"
"""HBEFA4 tractor-trailer class (34-40 t, Euro VI) for the heavy vTypes; the
recording's heavy fragments are 87% semis by count (artifacts/i24_heavy_observed.json)."""
ENTRY_LANE_X_M = (0.0, 250.0)
"""Data-x window where the recording's lane shares are measured for
``--entry-lanes observed`` (just inside the span, after the Old Hickory
off-ramp gore at data x ≈ 34 m and before the on-ramp gore at ≈ 900 m)."""

#: Offset, inside a lane-profile bin, of the section at which ``--entry-lanes
#: observed_flow`` counts vehicles. It places the entry window's count section
#: at data x = ``MAINLINE_COUNT_X_M`` (200 m) — the corridor's mainline count
#: section, the first high-coverage one, and the section whose crossings *are*
#: the replica's mainline inflow, so the flow shares decompose exactly the
#: demand the replica inserts. It also sits clear of the (unmodelled) Old
#: Hickory off-ramp taper, whose drain of the right lane is still visible at
#: data x ≈ 125 m (right-lane crossing share 0.25 at 125 m against 0.21 at
#: 200-250 m; docs/MERGE_ROUND6_PLAN.md §2.1).
FLOW_SECTION_OFFSET_M = MAINLINE_COUNT_X_M

#: Lanes present in the frame the flow counts are taken on (1-4 mainline plus
#: the auxiliary lane 5). Crossings are counted on the frame as a whole — a
#: vehicle that changes lane across the section is counted in the lane it
#: lands in — so the lane set is part of the estimator: it is fixed here and
#: shared with ``scripts/i24_lane_profile.py`` so the builder and the
#: lane-profile artifact count the same crossings.
FLOW_COUNT_LANES = (1, 2, 3, 4, 5)

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


def first_crossing_lane_counts(
    df: pd.DataFrame, sections_m: Sequence[float], lanes: Sequence[int]
) -> dict[float, dict[int, int]]:
    """Vehicles crossing each section, counted once each, by the lane they are in.

    The **flow** observable behind ``--entry-lanes observed_flow`` and the
    ``flow_share`` rows of ``artifacts/i24_lane_profile.json``. A share of
    5 Hz *samples* per lane is a share of vehicle-time, which over-represents
    slow lanes; SUMO applies ``entry_lane_shares`` as a share of *flow*
    (docs/MERGE_ROUND6_PLAN.md §2.1). The rule, stated once here:

    * a vehicle (here: a tracked fragment, docs/I24_DATA.md §2) is counted at
      section ``x_s`` when two consecutive samples of it straddle it
      (``x_prev < x_s <= x_cur``), in the lane of the *later* sample;
    * it is counted **once per section**, at its first crossing in time, so a
      vehicle that oscillates across the section does not count twice;
    * a sample-to-sample jump over more than one section (a tracker hole) is
      counted at every section it spans — at 0.2 s sampling this is rare and
      the sections here are 250 m apart.

    The same rule gives the replica's mainline demand (:func:`crossings_per_window`
    counts fragment crossings without the per-vehicle deduplication, which at
    a 0.2 s sample interval differs only for that oscillation case), so the
    lane shares at ``MAINLINE_COUNT_X_M`` decompose the inflow exactly.

    Args:
        df: Trajectory rows with ``t, veh_id, x, lane`` (``veh_id`` of any
            hashable dtype; integer codes are accepted and are cheaper).
        sections_m: Sections [m] in the frame's ``x`` units.
        lanes: Lanes to report (missing lanes report 0).

    Returns:
        ``{section_m: {lane: n_vehicles}}``.
    """
    veh = df["veh_id"].to_numpy()
    if veh.dtype == object or not np.issubdtype(veh.dtype, np.number):
        veh = pd.factorize(veh, sort=False)[0]
    order = np.lexsort((df["t"].to_numpy(), veh))
    veh, x = veh[order], df["x"].to_numpy()[order]
    lane = df["lane"].to_numpy()[order]
    same = veh[1:] == veh[:-1]
    x_prev, x_cur = x[:-1][same], x[1:][same]
    veh_cur, lane_cur = veh[1:][same], lane[1:][same]
    out: dict[float, dict[int, int]] = {}
    for x_s in sections_m:
        hit = (x_prev < x_s) & (x_cur >= x_s)
        first = pd.DataFrame({"veh": veh_cur[hit], "lane": lane_cur[hit]}).drop_duplicates(
            "veh", keep="first"
        )
        counts = first["lane"].value_counts()
        out[float(x_s)] = {int(ln): int(counts.get(ln, 0)) for ln in lanes}
    return out


def entry_lane_flow_shares(t_lo: float, t_hi: float) -> tuple[list[float], list[int]]:
    """Share of mainline *vehicles* per lane (1-4, left to right) at the entry.

    Counts each vehicle once at its first crossing of data
    ``x = FLOW_SECTION_OFFSET_M`` within :data:`ENTRY_LANE_X_M`
    (:func:`first_crossing_lane_counts`) over ``[t_lo, t_hi)``, on a frame of
    :data:`FLOW_COUNT_LANES`; the shares are over the mainline lanes 1-4, the
    lanes vehicles are inserted into. Counts are lower bounds at the
    instrument's per-lane tracking coverage (docs/I24_DATA.md §4); the shares
    are not coverage-corrected.

    Returns:
        ``(shares, counts)`` — shares over lanes 1-4, and the vehicle counts.
    """
    df = load_i24_parquet(
        WB_DIR,
        t_range_s=(t_lo, t_hi),
        x_range_m=ENTRY_LANE_X_M,
        lanes=(min(FLOW_COUNT_LANES), max(FLOW_COUNT_LANES)),
        columns=["t", "veh_id", "x", "lane"],
    )
    counts = first_crossing_lane_counts(df, [FLOW_SECTION_OFFSET_M], FLOW_COUNT_LANES)[
        FLOW_SECTION_OFFSET_M
    ]
    n = [int(counts[lane]) for lane in range(1, 5)]
    if sum(n) == 0:
        raise ValueError(f"no mainline crossings of data x = {FLOW_SECTION_OFFSET_M:g} m")
    return [round(v / sum(n), 4) for v in n], n


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


def check_count_consistency(
    art: Mapping[str, Any],
    *,
    data_hash: str,
    t_lo: float = T_STUDY_LO_S,
    t_hi: float = T_STUDY_HI_S,
    window_s: float = WINDOW_S,
    ramps: Sequence[Mapping[str, Any]] = RAMPS,
) -> None:
    """Refuse a count-consistency artifact that was not made for this builder.

    Amendment B2 subtracts the artifact's per-window flags from the builder's
    per-window ramp counts, so both must describe the same recording (data
    hash), the same window grid (study period and window length) and the same
    ramps (name, kind and count section, in order). A run whose counts did not
    reproduce the committed ones is void (docs/I24_DISCHARGE_DIAGNOSIS.md
    §8.4.1) and is refused as well.

    Args:
        art: The parsed ``artifacts/i24_count_consistency.json``.
        data_hash: The recording's data hash (``i24_data.data_hash()``).
        t_lo: Study period start [s after 06:00 CST].
        t_hi: Study period end [s after 06:00 CST].
        window_s: Count window [s].
        ramps: The builder's ramps (:data:`RAMPS`).

    Raises:
        ValueError: Naming every mismatch found.
    """

    def num(v: Any) -> float | None:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    problems: list[str] = []
    if art.get("data_hash") != data_hash:
        problems.append(
            f"data hash {str(art.get('data_hash'))[:12]}… is not the recording's {data_hash[:12]}…"
        )
    params = art.get("parameters") or {}
    period = [num(v) for v in params.get("period_s") or []]
    if period != [float(t_lo), float(t_hi)]:
        problems.append(f"period {period} s is not the builder's [{t_lo:g}, {t_hi:g}] s")
    if num(params.get("window_s")) != float(window_s):
        problems.append(f"window {params.get('window_s')} s is not the builder's {window_s:g} s")
    want = [(str(r["name"]), str(r["kind"]), float(r["count_x_m"])) for r in ramps]
    got = [(r.get("name"), r.get("kind"), num(r.get("count_x_m"))) for r in art.get("ramps") or []]
    if got != want:
        problems.append(f"ramps {got} are not the builder's {want} (name, kind, count section)")
    else:
        n_win = round((t_hi - t_lo) / window_s)
        for r in art["ramps"]:
            for key in ("counts_per_window", THROUGH_TRAFFIC_SERIES[r["kind"]]):
                n = len(r.get(key) or [])
                if n != n_win:
                    problems.append(f"{r['name']}: {key} has {n} windows, not {n_win}")
    if (art.get("checks") or {}).get("reproduces_committed_counts") is not True:
        problems.append(
            "checks.reproduces_committed_counts is not true: the run is void "
            "(docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.1)"
        )
    if problems:
        raise ValueError("; ".join(problems))


def ramp_through_corrections(
    art: Mapping[str, Any], counted: Mapping[str, np.ndarray]
) -> dict[str, dict[str, Any]]:
    """Each ramp's counts net of its flagged through traffic (amendment B2).

    Per 5-min window ``corrected = max(counted − flagged, 0)``, with
    ``flagged`` the artifact's ``prior_mainline_per_window`` for an on-ramp
    (vehicles in lanes 1-4 before the count) and ``later_mainline_per_window``
    for an off-ramp (vehicles that return to them after it;
    docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.3). The flags are a subset of the
    artifact's own counted crossings (``counts_per_window``), so the
    subtraction is exact only where those are the builder's counts: a ramp
    whose counts differ in any window is refused. Run
    :func:`check_count_consistency` on ``art`` first.

    Args:
        art: The count-consistency artifact.
        counted: The builder's ramp-lane crossings per window
            (:func:`crossings_per_window`), by ramp name.

    Returns:
        By ramp name: the per-window ``counted`` / ``flagged`` / ``corrected``
        series; their study-period totals and hourly means (tracked, not
        coverage-corrected); ``removed`` (``counted − corrected``, below
        ``flagged`` only where a window was clipped) and ``windows_clipped``;
        ``share_2h`` = flagged / counted over the study period (the share of
        docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.2).

    Raises:
        ValueError: A ramp has no builder count or its counts are not the builder's.
    """
    out: dict[str, dict[str, Any]] = {}
    for r in art["ramps"]:
        name, kind = str(r["name"]), str(r["kind"])
        if name not in counted:
            raise ValueError(f"{name}: the builder has no count for this ramp")
        cnt = np.asarray(counted[name], dtype=np.int64)
        theirs = np.asarray(r["counts_per_window"], dtype=np.int64)
        if theirs.shape != cnt.shape or not np.array_equal(theirs, cnt):
            diff = np.abs(theirs - cnt) if theirs.shape == cnt.shape else None
            where = (
                f"in {int((diff > 0).sum())} window(s), max |difference| {int(diff.max())}"
                if diff is not None
                else f"shape {theirs.shape} against {cnt.shape}"
            )
            raise ValueError(
                f"{name}: the artifact's counts_per_window differ from the builder's crossings "
                f"({where}); its flags count its own crossings, so the subtraction would not be exact"
            )
        series = THROUGH_TRAFFIC_SERIES[kind]
        flagged = np.asarray(r[series], dtype=np.int64)
        corrected = np.maximum(cnt - flagged, 0)
        n_cnt, n_flag, n_corr = int(cnt.sum()), int(flagged.sum()), int(corrected.sum())
        per_h = 3600.0 / (cnt.size * WINDOW_S)
        out[name] = {
            "name": name,
            "kind": kind,
            "count_x_m": float(r["count_x_m"]),
            "flagged_series": series,
            "counted_per_window": cnt.tolist(),
            "flagged_per_window": flagged.tolist(),
            "corrected_per_window": corrected.tolist(),
            "counted": n_cnt,
            "flagged": n_flag,
            "corrected": n_corr,
            "removed": n_cnt - n_corr,
            "windows_clipped": int((cnt < flagged).sum()),
            "counted_veh_h": round(n_cnt * per_h, 1),
            "flagged_veh_h": round(n_flag * per_h, 1),
            "corrected_veh_h": round(n_corr * per_h, 1),
            "share_2h": round(n_flag / n_cnt, 4) if n_cnt else None,
        }
    return out


def ramp_through_header(block: Mapping[str, Any], inputs_rel: str) -> str:
    """Scenario header lines recording amendment B2 (``--ramp-through-traffic exclude``)."""
    lines = [
        "#",
        "# AMENDMENT B2 (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.3; PROPOSED, not adopted):",
        "# --ramp-through-traffic exclude. Per 5-min window and before any coverage scaling,",
        "# each ramp-lane count is net of the through traffic flagged in",
        f"# {block['artifact']} (data hash {block['artifact_data_hash'][:12]}…,",
        f"# sha256 {block['artifact_sha256'][:12]}…): on-ramps less the vehicles in lanes 1-4",
        "# before the count, off-ramps less those that return to them after it, clipped at 0.",
        "# Tracked crossings over the study period:",
    ]
    for r in block["ramps"]:
        share = "n/a" if r["share_2h"] is None else f"{100.0 * r['share_2h']:.1f}%"
        clip = f"; {r['windows_clipped']} window(s) clipped at 0" if r["windows_clipped"] else ""
        lines.append(
            f"#   {r['name']}: counted {r['counted']}, flagged {r['flagged']} ({share}), "
            f"corrected {r['corrected']}{clip}"
        )
    lines += [
        "# The mainline entry demand and the boundary are unchanged. Per-window values:",
        f"# {inputs_rel} 'ramp_through_traffic'.",
    ]
    return "\n".join(lines) + "\n"


def family_code(*, exclude: bool, demand_coverage: bool, shift: bool) -> str:
    """The suffix token a family built with these demand corrections must end in.

    ``rc`` for amendment B2's ramp counts (``--ramp-through-traffic exclude``),
    then ``c`` for C7b's coverage-consistent demand (``--demand-coverage
    recommended``) and ``s`` for its insertion shift (``--insertion-shift-s``),
    in that order (docs/I24_CONSISTENCY_C7B.md: ``_rcc``, ``_rcs``, ``_rccs``);
    empty when none is on. Each changes the demand a published family was built
    with, so it never writes over one.
    """
    return ("rc" if exclude else "") + ("c" if demand_coverage else "") + ("s" if shift else "")


def free_flow_shift_s(distance_m: float, v0_ms: float) -> float:
    """C7b's computed insertion shift: ``distance / v0`` to :data:`INSERTION_SHIFT_DECIMALS`.

    The free-flow travel time from the network entry (sim x = 0, the first
    corridor edge's start, where ``microsim.vehicles`` inserts with
    ``departPos="base"``) to the mainline count section at the fleet's mean
    desired speed. It is a lower bound on the fleet's mean time (heterogeneous
    ``v0``, an edge limit below ``v0``, insertion below ``v0`` and congestion all
    lengthen it; docs/I24_GEH_DIAGNOSIS.md §6).

    Raises:
        ValueError: A distance or speed that is not positive and finite.
    """
    if not (math.isfinite(distance_m) and distance_m > 0.0):
        raise ValueError(f"entry-to-count-section distance {distance_m!r} m is not positive")
    if not (math.isfinite(v0_ms) and v0_ms > 0.0):
        raise ValueError(f"mean v0 {v0_ms!r} m/s is not positive")
    return round(distance_m / v0_ms, INSERTION_SHIFT_DECIMALS)


def shifted_steps(
    steps: Sequence[tuple[float, float]], shift_s: float
) -> list[tuple[float, float]]:
    """``(t, rate)`` steps with every start after the first moved ``shift_s`` earlier.

    The first step starts at 0 and also covers the warm-up, so it stays; the
    others start at their count window's sim time less the shift (to 1e-6 s),
    and every rate is unchanged. The shift must lie in (0, one 5-min window), so
    the steps keep their order and the first keeps a positive span.

    Raises:
        ValueError: A shift outside (0, :data:`WINDOW_S`).
    """
    if not (math.isfinite(shift_s) and 0.0 < shift_s < WINDOW_S):
        raise ValueError(f"insertion shift {shift_s!r} s is not in (0, {WINDOW_S:g}) s")
    return [steps[0]] + [(round(t - shift_s, 6), q) for t, q in steps[1:]]


def insertion_shift_header(block: Mapping[str, Any], inputs_rel: str) -> str:
    """Scenario header lines recording C7b's insertion shift (``--insertion-shift-s``)."""
    if block["mode"] == INSERTION_SHIFT_FREE_FLOW:
        how = [
            "# computed, not fitted: the free-flow time from the network entry (sim x = 0) to the",
            f"# count section (data x = {block['count_x_m']:g} m, sim x = {block['distance_m']:.1f} m) at the "
            f"fleet's mean v0 = {block['v0_ms']:.4f} m/s",
            f"# ({block['fleet_artifact']}, sha256 {block['fleet_artifact_sha256'][:12]}…), "
            f"{block['exact_s']:.3f} s rounded to 0.1 s.",
        ]
    else:
        how = ["# given explicitly (--insertion-shift-s), not computed here."]
    lines = [
        "#",
        "# C7b INSERTION SHIFT (docs/I24_CONSISTENCY_C7B.md; PROPOSED, not adopted): the mainline",
        f"# inflow steps after the first start {block['shift_s']:g} s before their count windows' clock times,",
        *how,
        "# On-ramp inflows, exit fractions and the boundary are unchanged. Per-step times:",
        f"# {inputs_rel} 'insertion_shift'.",
    ]
    return "\n".join(lines) + "\n"


def demand_coverage_paragraph(rows: Sequence[Mapping[str, Any]], inputs_rel: str) -> str:
    """The corrected arm's coverage paragraph under ``--demand-coverage recommended``."""
    used = [float(r["coverage_used"]) for r in rows]
    return f"""#
# COVERAGE-CORRECTED ARM, C7b COVERAGE-CONSISTENT DEMAND (docs/I24_CONSISTENCY_C7B.md; PROPOSED,
# not adopted): identical to the family's tracked arm except that the mainline and on-ramp
# inflows are divided by the RECOMMENDED tracking coverage per 15-min window
# ({COVERAGE_ARTIFACT} pooled.recommended_filled = max(section_gap_mixture, capacity_bound_fd);
# {min(used):.2f}-{max(used):.2f} here), the coverage scripts/i24_validate.py divides the observed
# counts by for the link-flow row's target, so the planned demand over that target is one
# constant in every window. Exit fractions and the boundary schedule are ratios/speeds and need
# no correction. Per-window values: {inputs_rel} 'demand_coverage'.
"""


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
    ap.add_argument(
        "--osm",
        choices=("original", "corrected"),
        default="original",
        help="map: the OSM extract as downloaded (default, keeps config hashes) or "
        "data/osm/i24_motion_corrected.osm (scripts/i24_correct_osm.py: auxiliary lanes at the "
        "provider's landmark positions, docs/I24_VALIDATION.md §0.5)",
    )
    ap.add_argument(
        "--lc-strategic",
        type=float,
        default=LC_STRATEGIC,
        help="FleetSpec.lc_strategic (SUMO lcStrategic); the builder constant is the value the "
        "short diverge pocket of the original map required",
    )
    ap.add_argument(
        "--entry-lanes",
        choices=("roundrobin", "observed", "observed_flow"),
        default="roundrobin",
        help="mainline insertion across the entry edge's lanes: SUMO round-robin (default, keeps "
        "config hashes); 'observed' = the recording's lane shares of vehicle-time at data x in "
        f"[0, {ENTRY_LANE_X_M[1]:g}) m over the study period (OSMNetwork.entry_lane_shares, "
        "docs/I24_VALIDATION.md §0.5); 'observed_flow' = the same window in flow units, each "
        f"vehicle counted once at its first crossing of data x = {FLOW_SECTION_OFFSET_M:g} m, "
        "which is the unit SUMO applies the shares in (docs/MERGE_ROUND6_PLAN.md §2.1)",
    )
    ap.add_argument(
        "--heavy",
        action="store_true",
        help="add the recording's heavy vehicles to the fleet: share and median length from "
        "artifacts/i24_heavy_observed.json (scripts/i24_heavy_share.py), population from "
        f"{HEAVY_FLEET_ARTIFACT} (scripts/fit_idm_i24.py --classes heavy); off by default "
        "(keeps config hashes)",
    )
    ap.add_argument(
        "--suffix",
        default="",
        help="write a separate scenario family: scenarios/i24_replica_<suffix>[_corrected].yaml, "
        "artifacts/demand_i24_<suffix>.json, artifacts/i24_replica_inputs_<suffix>.json "
        "(default: the canonical files)",
    )
    ap.add_argument(
        "--lc-strategic-ramp",
        type=float,
        default=None,
        help="FleetSpec.lc_strategic_ramp for ramp-origin vehicles (None = same as --lc-strategic)",
    )
    ap.add_argument(
        "--merge",
        choices=("lane_change", "zipper"),
        default="lane_change",
        help="RampSpec.merge for the on-ramps named by --merge-ramps",
    )
    ap.add_argument(
        "--merge-ramps",
        nargs="*",
        default=["Old Hickory Blvd on-ramp"],
        help="on-ramp names that get --merge (default: Old Hickory, whose acceleration lane "
        "dead-ends; the Hickory Hollow lane continues into the Bell Road weave)",
    )
    ap.add_argument(
        "--jm-timegap",
        type=float,
        default=None,
        help="FleetSpec.jm_timegap_minor_s (SUMO jmTimegapMinor, the zipper's merged-lane lever)",
    )
    ap.add_argument(
        "--ramp-through-traffic",
        choices=("keep", "exclude"),
        default="keep",
        help="amendment B2 (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.3; proposed, not adopted): "
        "'keep' (default, the builder as before) or 'exclude' = subtract, per 5-min window and "
        "before coverage scaling, the ramp-lane crossings --count-consistency flags as through "
        "traffic; needs a --suffix ending in 'rc' (a new family)",
    )
    ap.add_argument(
        "--count-consistency",
        default=COUNT_CONSISTENCY_ARTIFACT,
        metavar="PATH",
        help="count-consistency artifact read by --ramp-through-traffic exclude (relative to the "
        "repository root unless absolute); its data hash, window grid and ramps must be this "
        "builder's",
    )
    ap.add_argument(
        "--demand-coverage",
        choices=("equilibrium", "recommended"),
        default="equilibrium",
        help="C7b (docs/I24_CONSISTENCY_C7B.md; proposed, not adopted): the coverage the corrected "
        "arm's mainline and on-ramp inflows are divided by: 'equilibrium' (default, the builder as "
        f"before) or 'recommended' = {COVERAGE_ARTIFACT} pooled.recommended_filled per 15-min "
        "window, the coverage scripts/i24_validate.py's link-flow target divides the observed "
        "counts by (the arithmetic of --coverage-estimator recommended, plus the family guard "
        "and the provenance); a new family: 'c' in the --suffix token",
    )
    ap.add_argument(
        "--insertion-shift-s",
        default=None,
        metavar=f"SECONDS|{INSERTION_SHIFT_FREE_FLOW}",
        help="C7b (docs/I24_CONSISTENCY_C7B.md; proposed, not adopted): start the mainline inflow's "
        "steps after the first this many seconds before their count windows' clock times (in "
        f"(0, {WINDOW_S:g}) s); '{INSERTION_SHIFT_FREE_FLOW}' computes it: the distance from the "
        f"network entry (sim x = 0) to the count section (data x = {MAINLINE_COUNT_X_M:g} m) over "
        f"the fleet's mean v0 ({FLEET_ARTIFACT}), to 0.1 s. Default: no shift (the builder as "
        "before); a new family: 's' in the --suffix token",
    )
    args = ap.parse_args()
    suffix = f"_{args.suffix}" if args.suffix else ""
    shift_request: float | str | None = None
    if args.insertion_shift_s is not None:
        if args.insertion_shift_s == INSERTION_SHIFT_FREE_FLOW:
            shift_request = INSERTION_SHIFT_FREE_FLOW
        else:
            try:
                shift_request = float(args.insertion_shift_s)
            except ValueError:
                raise SystemExit(
                    f"--insertion-shift-s {args.insertion_shift_s!r}: a number of seconds or "
                    f"{INSERTION_SHIFT_FREE_FLOW!r}"
                ) from None
            if not (math.isfinite(shift_request) and 0.0 < shift_request < WINDOW_S):
                raise SystemExit(
                    f"--insertion-shift-s {args.insertion_shift_s}: must lie in (0, {WINDOW_S:g}) s "
                    "(no shift is the default: leave the option out)"
                )
    demand_cov = args.demand_coverage == "recommended"
    if demand_cov and args.coverage_estimator != "equilibrium":
        raise SystemExit(
            "--demand-coverage recommended divides by the recommended estimator itself; leave "
            "--coverage-estimator at its default"
        )
    code = family_code(
        exclude=args.ramp_through_traffic == "exclude",
        demand_coverage=demand_cov,
        shift=shift_request is not None,
    )
    if code == "rc":  # amendment B2 alone: its guard and message, as before
        if not (args.suffix == "rc" or args.suffix.endswith("_rc")):
            raise SystemExit(
                "--ramp-through-traffic exclude changes the ramp demand, so it writes a new family: "
                f"give a --suffix ending in 'rc' (e.g. flow_rc), not {args.suffix!r}"
            )
    elif code and not (args.suffix == code or args.suffix.endswith(f"_{code}")):
        raise SystemExit(
            "--ramp-through-traffic exclude / --demand-coverage recommended / --insertion-shift-s "
            "change the demand, so they write a new family: with these options give a --suffix "
            f"ending in '{code}' (e.g. flow_{code}), not {args.suffix!r}"
        )
    through_art: dict[str, Any] | None = None
    through_path: Path | None = None
    through_bytes = b""
    if args.ramp_through_traffic == "exclude":
        through_path = Path(args.count_consistency)
        if not through_path.is_absolute():
            through_path = REPO_ROOT / through_path
        if not through_path.is_file():
            raise SystemExit(
                f"--ramp-through-traffic exclude: {through_path} is missing "
                "(scripts/i24_count_consistency.py writes it)"
            )
        through_bytes = through_path.read_bytes()
        through_art = json.loads(through_bytes)
        try:
            check_count_consistency(through_art, data_hash=data_hash())
        except ValueError as e:
            raise SystemExit(
                f"--ramp-through-traffic exclude: {through_path} does not match this builder: {e}"
            ) from None
    osm_file = OSM_FILE if args.osm == "original" else CORRECTED_OSM_FILE
    heavy_block: dict[str, Any] | None = None
    if args.heavy:
        obs_heavy = json.loads((REPO_ROOT / "artifacts" / "i24_heavy_observed.json").read_text())
        heavy_block = {
            "fraction": float(obs_heavy["fraction_fragments"]),
            "length_m": float(obs_heavy["length_median_m"]),
            "emission_class": HEAVY_EMISSION_CLASS,
            "vclass": "truck",
            "idm_calibration": HEAVY_FLEET_ARTIFACT,
        }
    lc_strategic = float(args.lc_strategic)
    fleet_path = REPO_ROOT / FLEET_ARTIFACT
    if not fleet_path.is_file() and not args.allow_missing_fleet:
        raise SystemExit(f"{fleet_path} missing — run scripts/fit_idm_i24.py first")

    # --- geometry ---------------------------------------------------------
    workdir = (
        REPO_ROOT
        / "data"
        / "i24motion"
        / "processed"
        / ("net_raw" if args.osm == "original" else "net_raw_corrected")
    )
    bundle = osm_import(osm_file=osm_file, workdir=workdir, geometry_remove=False)
    net = sumolib.net.readNet(str(bundle.net_path))
    proj = read_projection(bundle.net_path)
    proj_err = check_projection(net, proj, osm_file)
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
    counted = {r["name"]: crossings_per_window(ramp_df, r["count_x_m"], t_lo, t_hi) for r in RAMPS}
    through: dict[str, dict[str, Any]] | None = None
    if through_art is not None:
        try:
            through = ramp_through_corrections(through_art, counted)
        except ValueError as e:
            raise SystemExit(f"--ramp-through-traffic exclude: {through_path}: {e}") from None
    ramp_specs = []
    ramp_records = []
    for r in RAMPS:
        cnt = counted[r["name"]]
        rec = {
            "name": r["name"],
            "kind": r["kind"],
            "edges": r["edges"],
            "attach_edge": r["attach_edge"],
            "count_x_m": r["count_x_m"],
            "ramp_lane_crossings": cnt.tolist(),
            "ramp_lane_veh_h": (cnt * 3600.0 / WINDOW_S).round(1).tolist(),
        }
        if through is not None:
            # amendment B2: the ramp's demand is built from its counts net of the flagged through
            # traffic; ramp_lane_crossings above stay the recording's counts
            cnt = np.asarray(through[r["name"]]["corrected_per_window"], dtype=np.int64)
            rec["ramp_lane_crossings_corrected"] = cnt.tolist()
            rec["ramp_lane_veh_h_corrected"] = (cnt * 3600.0 / WINDOW_S).round(1).tolist()
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

    # --- C7b insertion shift (opt-in): mainline steps start before their count windows ---
    shift_block: dict[str, Any] | None = None
    if shift_request is not None:
        count_window_times = [t for t, _ in inflow_steps]
        if shift_request == INSERTION_SHIFT_FREE_FLOW:
            if not fleet_path.is_file():
                raise SystemExit(
                    f"--insertion-shift-s {INSERTION_SHIFT_FREE_FLOW} needs the fleet's mean v0: "
                    f"{fleet_path} is missing"
                )
            v0 = float(json.loads(fleet_path.read_text())["mean"]["v0"])
            distance = float(sim_x_of_data_x(MAINLINE_COUNT_X_M))
            try:
                shift_s = free_flow_shift_s(distance, v0)
                inflow_steps = shifted_steps(inflow_steps, shift_s)
            except ValueError as e:
                raise SystemExit(f"--insertion-shift-s {INSERTION_SHIFT_FREE_FLOW}: {e}") from None
            shift_block = {
                "mode": INSERTION_SHIFT_FREE_FLOW,
                "shift_s": shift_s,
                "exact_s": distance / v0,
                "rule": "the free-flow travel time from the network entry (sim x = 0, the first "
                "corridor edge's start, departPos base) to the mainline count section at the "
                "fleet's mean v0, rounded to 0.1 s: a lower bound on the fleet's mean time "
                "(heterogeneous v0, edge limits below v0, insertion below v0 and congestion "
                "lengthen it)",
                "count_x_m": MAINLINE_COUNT_X_M,
                "distance_m": distance,
                "v0_ms": v0,
                "fleet_artifact": FLEET_ARTIFACT,
                "fleet_artifact_sha256": hashlib.sha256(fleet_path.read_bytes()).hexdigest(),
            }
        else:
            assert isinstance(shift_request, float)
            shift_s = shift_request
            inflow_steps = shifted_steps(inflow_steps, shift_s)
            shift_block = {"mode": "explicit", "shift_s": shift_s}
        shift_block.update(
            {
                "proposal": "C7b, docs/I24_CONSISTENCY_C7B.md (proposed, not adopted)",
                "applies_to": "the mainline inflow of both arms (steps after the first; the "
                "first starts at 0 and covers the warm-up); on-ramp inflows, exit fractions and "
                "the boundary are unchanged",
                "count_window_times_sim": count_window_times,
                "inflow_step_times_sim": [t for t, _ in inflow_steps],
            }
        )

    # --- coverage-corrected demand (second arm) ---------------------------
    span_hi_data = geo.data_x_of_chain_pos(chain_off[CORRIDOR_EDGES[-1]])
    estimator = "recommended" if demand_cov else args.coverage_estimator
    cov_rows = None
    cov_bytes = b""
    if demand_cov and not fleet_path.is_file():
        raise SystemExit(
            f"--demand-coverage recommended writes the corrected arm, which needs {fleet_path}"
        )
    if fleet_path.is_file():
        idm_mean = json.loads(fleet_path.read_text())["mean"]
        cov_rows = coverage_factors(t_lo, t_hi, span_hi_data, idm_mean)
        coverage_source: dict = {"estimator": "equilibrium", "artifact": None}
        if estimator != "equilibrium":
            cov_bytes = (REPO_ROOT / COVERAGE_ARTIFACT).read_bytes()
            cov_art = json.loads(cov_bytes)
            key = "recommended_filled" if estimator == "recommended" else estimator
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
    demand_cov_block: dict[str, Any] | None = None
    if demand_cov:
        assert cov_rows is not None
        cov_art = json.loads(cov_bytes)
        if cov_art.get("data_hash") != dh:
            raise SystemExit(
                f"--demand-coverage recommended: {COVERAGE_ARTIFACT} was made from data hash "
                f"{str(cov_art.get('data_hash'))[:12]}…, not the recording's {dh[:12]}…"
            )
        demand_cov_block = {
            "mode": "recommended",
            "proposal": "C7b, docs/I24_CONSISTENCY_C7B.md (proposed, not adopted)",
            "rule": "the corrected arm's mainline and on-ramp inflows are the tracked counts "
            f"divided by {COVERAGE_ARTIFACT} pooled.recommended_filled per 15-min window (each "
            "5-min window takes the 15-min window it starts in), the values "
            "scripts/i24_validate.py divides the observed counts by for the link-flow row's "
            "target (hourly_flows_veh_h_recommended); the planned demand over that target is "
            "then one constant in every window",
            "artifact": COVERAGE_ARTIFACT,
            "artifact_sha256": hashlib.sha256(cov_bytes).hexdigest(),
            "artifact_data_hash": cov_art.get("data_hash"),
            "artifact_created_at": cov_art.get("created_at"),
            "per_window": [
                {
                    "t_lo_s": r["t_lo_s"],
                    "window": r.get("window"),
                    "c_equilibrium": r["coverage_equilibrium"],
                    "c_used": r["coverage_used"],
                    "c_used_over_c_equilibrium": r["coverage_used"] / r["coverage_equilibrium"],
                }
                for r in cov_rows
            ],
        }
    demand_source = (
        "I-24 MOTION INCEPTION v1.x westbound, 30 Nov 2022: mainline (lanes 1-4) fragment "
        f"crossings at data x = {MAINLINE_COUNT_X_M:g} m (MM 62.7 - {MAINLINE_COUNT_X_M / 1609.344:.3f} mi) "
        f"per 5-min window, {clock(t_lo)}-{clock(t_hi)} CST, shifted by the {WARMUP_S:g} s "
        "warmup (sim t = data t - 1800 + 600). LOWER BOUND at the instrument's tracking "
        "coverage; not inflated."
    )
    if shift_block is not None:
        demand_source += (
            f" C7b insertion shift: every step after the first starts {shift_block['shift_s']:g} s "
            "earlier (docs/I24_CONSISTENCY_C7B.md)."
        )
    demand = DemandProfile(
        created_at=created_at,
        source=demand_source,
        data_hash=dh,
        steps=[(t, round(q, 6)) for t, q in inflow_steps],
        geh_vs_counts=None,
    )
    demand_path = REPO_ROOT / "artifacts" / f"demand_i24{suffix}.json"
    demand.save(demand_path)

    # --- entry lane distribution ------------------------------------------
    entry_lane_shares: list[float] | None = None
    entry_lane_counts: list[int] | None = None
    if args.entry_lanes == "observed":
        lane_df = load_mainline(t_range_s=(t_lo, t_hi), x_range_m=ENTRY_LANE_X_M, columns=["lane"])
        counts_by_lane = lane_df["lane"].value_counts()
        raw_shares = [float(counts_by_lane.get(lane, 0)) for lane in range(1, 5)]  # left → right
        entry_lane_shares = [round(v / sum(raw_shares), 4) for v in raw_shares]
        print(f"entry lane shares (lanes 1-4, left to right): {entry_lane_shares}", flush=True)
    elif args.entry_lanes == "observed_flow":
        entry_lane_shares, entry_lane_counts = entry_lane_flow_shares(t_lo, t_hi)
        print(
            f"entry lane FLOW shares (lanes 1-4, left to right): {entry_lane_shares} "
            f"from {entry_lane_counts} vehicles at data x = {FLOW_SECTION_OFFSET_M:g} m",
            flush=True,
        )

    # --- scenario ---------------------------------------------------------
    duration_s = WARMUP_S + (t_hi - t_lo)
    scenario = {
        "name": f"i24_replica{suffix}",
        "tier": "micro",
        "network": {
            "kind": "osm",
            "osm_file": str(osm_file.relative_to(REPO_ROOT)),
            "corridor_edges": list(CORRIDOR_EDGES),
            "inflow": [[t, round(q, 6)] for t, q in inflow_steps],
            "boundary": {"kind": "speed_schedule", "steps": [[t, round(v, 4)] for t, v in bsteps]},
            "ramps": ramp_specs,
            "entry_lane_shares": entry_lane_shares,
        },
        "fleet": {
            "model": "IDM",
            "idm_calibration": FLEET_ARTIFACT,
            "lc_strategic": lc_strategic,
            "lc_strategic_ramp": args.lc_strategic_ramp,
            "lc_keep_right": LC_KEEP_RIGHT,
            "heavy": heavy_block,
            "jm_timegap_minor_s": args.jm_timegap,
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
    if args.merge != "lane_change":
        for spec in scenario["network"]["ramps"]:
            if spec.get("kind") == "on" and spec.get("name") in set(args.merge_ramps):
                spec["merge"] = args.merge
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
# lc_strategic {lc_strategic:g} removes SUMO's diverge lane-change stall and lc_keep_right
# {LC_KEEP_RIGHT:g} matches the observed lane use (both measured; see builder constants).
# seeded=False: the boundary and ramp inputs are calibration inputs, not shocks.
"""
    through_block: dict[str, Any] | None = None
    if through is not None:
        assert through_art is not None and through_path is not None
        through_block = {
            "mode": "exclude",
            "amendment": "B2, docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.3 (proposed, not adopted)",
            "rule": "per 5-min window, before coverage scaling: corrected = max(counted - "
            "flagged, 0), flagged = prior_mainline_per_window for an on-ramp (in lanes 1-4 "
            "before the count) and later_mainline_per_window for an off-ramp (back in lanes 1-4 "
            "after it); on-ramp inflows and off-ramp exit fractions are built from the corrected "
            "counts; the mainline entry demand and the boundary are unchanged",
            "artifact": (
                str(through_path.relative_to(REPO_ROOT))
                if through_path.is_relative_to(REPO_ROOT)
                else str(through_path)
            ),
            "artifact_sha256": hashlib.sha256(through_bytes).hexdigest(),
            "artifact_data_hash": through_art["data_hash"],
            "artifact_created_at": through_art.get("created_at"),
            "artifact_code": through_art.get("code"),
            "ramps": [through[r["name"]] for r in RAMPS],
        }
        header += ramp_through_header(through_block, f"artifacts/i24_replica_inputs{suffix}.json")
    if shift_block is not None:
        header += insertion_shift_header(shift_block, f"artifacts/i24_replica_inputs{suffix}.json")
    out_yaml = REPO_ROOT / "scenarios" / f"i24_replica{suffix}.yaml"
    cfg.to_yaml(out_yaml)
    out_yaml.write_text(header + out_yaml.read_text())

    corrected_hash = None
    if cov_rows is not None:
        sc2 = json.loads(json.dumps(scenario))
        sc2["name"] = f"i24_replica{suffix}_corrected"
        sc2["network"]["inflow"] = [[t, round(q, 6)] for t, q in corrected(inflow_steps)]
        for spec in sc2["network"]["ramps"]:
            if spec["kind"] == "on":
                spec["inflow"] = [
                    [t, round(q, 6)] for t, q in corrected([(t, q) for t, q in spec["inflow"]])
                ]
        cfg2 = ScenarioConfig.model_validate(sc2)
        corrected_hash = config_hash(cfg2)
        coverage_paragraph = (
            demand_coverage_paragraph(cov_rows, f"artifacts/i24_replica_inputs{suffix}.json")
            if demand_cov
            else f"""#
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
        header2 = (
            header.replace(
                "# i24_replica — I-24 westbound",
                "# i24_replica_corrected — I-24 westbound",
            )
            + coverage_paragraph
        )
        out2 = REPO_ROOT / "scenarios" / f"i24_replica{suffix}_corrected.yaml"
        cfg2.to_yaml(out2)
        out2.write_text(header2 + out2.read_text())

    inputs = {
        "created_at": created_at,
        "data_hash": dh,
        "osm_file": str(osm_file.relative_to(REPO_ROOT)),
        "map": args.osm,
        "lc_strategic": lc_strategic,
        "entry_lanes": args.entry_lanes,
        "heavy": heavy_block,
        "suffix": args.suffix,
        "merge": args.merge,
        "merge_ramps": list(args.merge_ramps) if args.merge != "lane_change" else [],
        "lc_strategic_ramp": args.lc_strategic_ramp,
        "jm_timegap_minor_s": args.jm_timegap,
        "entry_lane_shares": entry_lane_shares,
        "entry_lane_x_m": list(ENTRY_LANE_X_M),
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
    if entry_lane_counts is not None:  # --entry-lanes observed_flow only
        inputs["entry_lane_count_x_m"] = FLOW_SECTION_OFFSET_M
        inputs["entry_lane_vehicles"] = entry_lane_counts
    if through_block is not None:  # --ramp-through-traffic exclude only
        inputs["ramp_through_traffic"] = through_block
    if demand_cov_block is not None:  # --demand-coverage recommended only
        assert inputs["coverage"] is not None
        inputs["coverage"]["method"] = (
            f"{COVERAGE_ARTIFACT} pooled.recommended_filled per 15-min window (C7b "
            "--demand-coverage recommended); coverage_equilibrium keeps the builder's IDM-"
            "equilibrium value"
        )
        inputs["demand_coverage"] = demand_cov_block
    if shift_block is not None:  # --insertion-shift-s only
        inputs["insertion_shift"] = shift_block
    (REPO_ROOT / "artifacts" / f"i24_replica_inputs{suffix}.json").write_text(
        json.dumps(inputs, indent=2)
    )

    print(
        f"projection check {proj_err:.3f} m; chain scale {scale:.4f}; MM fit RMS {geo.residual_rms_m:.1f} m"
    )
    print(
        f"measured span sim x [{span_lo_sim:.1f}, {span_hi_sim:.1f}) m; duration {duration_s:.0f} s"
    )
    print("mainline veh/h:", inputs["mainline"]["veh_h"])
    for rec in ramp_records:
        key = "exit_fraction" if rec["kind"] == "off" else "ramp_lane_veh_h"
        if through is not None and rec["kind"] == "on":
            key = "ramp_lane_veh_h_corrected"
        print(f"  {rec['name']}: {rec[key]}")
    if through_block is not None:
        for t in through_block["ramps"]:
            print(
                f"  B2 {t['name']}: counted {t['counted']}, flagged {t['flagged']} "
                f"({t['flagged_series']}), corrected {t['corrected']} over the study period"
            )
    if shift_block is not None:
        print(
            f"  C7b insertion shift {shift_block['shift_s']:g} s ({shift_block['mode']}): mainline "
            f"steps from {shift_block['inflow_step_times_sim'][1]:g} s"
        )
    if demand_cov_block is not None:
        print(
            "  C7b demand coverage: recommended (artifacts/i24_coverage.json pooled.recommended_filled)"
        )
    print(
        f"boundary v [{inputs['boundary']['v_min_ms']:.1f}, {inputs['boundary']['v_max_ms']:.1f}] m/s over {len(sched)} steps"
    )
    print(f"-> {out_yaml} (config {config_hash(cfg)}), {demand_path}")
    if cov_rows is not None:
        print("coverage per 15 min:", [round(r["coverage_used"], 3) for r in cov_rows])
        print(f"-> scenarios/i24_replica{suffix}_corrected.yaml (config {corrected_hash})")
    if math.isnan(scale):
        raise SystemExit("geometry fit failed")


if __name__ == "__main__":
    main()
