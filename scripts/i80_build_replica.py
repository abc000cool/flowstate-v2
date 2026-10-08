"""E11 — build the ``i80_replica`` scenario from NGSIM I-80, the way the US-101 replica was built.

docs/PRE_FRISCO_PROGRAM.md, "E11": a replica of NGSIM I-80's study area with its geometry, its
counted demand and its measured downstream boundary, driven by the I-24 population, with
**nothing fitted on I-80**. The US-101 recipe (``scenarios/us101_replica.yaml``,
``scripts/extract_demand_us101.py``, ``scripts/m3_us101_validate.py``; docs/M2_RESULTS.md §5-§6,
docs/M3_US101_VALIDATION.md §2) applied to I-80, plus the on-ramp the US-101 replica lacked:

* **Geometry** (FHWA-HRT-06-137 and the data; ``scripts/i80_data.py``): a straight eastbound
  corridor with six mainline lanes; the site's length, the gore and the end of the acceleration
  lane read off the recording (the 99.9th percentile of the mainline positions; the 0.5th
  percentile of the positions of 7 -> 6 transitions; the 99.5th percentile of the lane-7
  positions), written as a hand-built OpenStreetMap file (the precedent is the merge fixtures,
  ``tests/fixtures/merge.osm``, ``tests/fixtures/mcknight_merge.osm``): an approach (the
  insertion buffer, ``min(2,000 m, site length)`` as ``microsim.runner.CORRIDOR_INSERTION_BUFFER_M``
  gave US-101), the site up to the gore (6 lanes), the acceleration lane's span (7 lanes: lane 0
  is the acceleration lane and ends there, as an explicit right-side lane drop), the rest of the
  site (6 lanes), a 200-m exit edge hosting the boundary (US-101's exit buffer) and a one-lane
  ramp of :data:`RAMP_EDGE_M` joining at the gore; the nodes are moved until netconvert compiles
  every corridor edge to its measured length (:func:`corrected_layout`: netconvert moves the
  merge junction upstream). Mainline ``maxspeed`` 65 mph, California's
  maximum (Vehicle Code §22349(a)); the segment's posting is not verified here. The ramp keeps
  netconvert's ``motorway_link`` default (80 km/h), as untagged ramps of the other replicas do.
* **Counted demand**: per stream (mainline lanes 1-6, the ramp lane 7), every vehicle first seen
  after its period's first frame is an entry (those in the first frame are the initial state),
  stitched across the block's periods at each period's last recorded entry (US-101's rule) and
  counted in 5-min windows on the block's wall clock (the last one partial), shifted by the
  180-s warm-up (US-101's). US-101's spatial rule (first ``local_y`` at most 30 m) is counted
  beside. Mainline entries are split across lanes by their measured first lane
  (``entry_lane_shares``, the kept I-24 arm's practice; ``--entry-lanes round_robin`` is
  US-101's).
* **Measured downstream boundary**: the mean mainline speed in the site's last 100 m per 30-s
  window (US-101's), from each period up to its switch, empty windows filled forward then
  backward, the first value held through the warm-up, on the exit edge.
* **Fleet**: the ``fleet`` block of the kept I-24 arm, copied whole
  (``--fleet-from``, default ``scenarios/i24_replica_flow_rc_speedcal_dc_refit.yaml``: the I-24
  population ``artifacts/idm_i24_capacity_amax_k1.0.json`` and its lane-change settings). No AVs.
* **Merge**: the kept configuration at an acceleration lane, ``merge: lane_change`` (LC2013).
  ``corridor_e11.py make-arm`` writes the ``merge: measured`` copy.

The YAML is produced on the VM where the data is. Outputs: ``scenarios/i80_replica.yaml``,
``data/osm/i80_ngsim.osm`` and ``artifacts/i80_replica_inputs.json`` (every derived number, the
OSM text's parameters and sha256). The archive carries ``artifacts/*.json`` and
``scenarios/*.yaml`` only, so after the ingest ``write-osm`` rebuilds the OSM file from the inputs
artifact and checks its sha256.

Run (VM, repository root)::

    uv run --no-sync python scripts/i80_build_replica.py build --data-summary artifacts/i80_data.json
    uv run --no-sync python scripts/i80_build_replica.py write-osm   # locally, after the ingest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd
import yaml

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import i80_data  # noqa: E402

from flowstate_core.config import ScenarioConfig, config_hash  # noqa: E402

REPO_ROOT = SCRIPTS.parent
SCENARIO_OUT = REPO_ROOT / "scenarios" / "i80_replica.yaml"
INPUTS_OUT = REPO_ROOT / "artifacts" / "i80_replica_inputs.json"
OSM_OUT = REPO_ROOT / "data" / "osm" / "i80_ngsim.osm"
FLEET_FROM = REPO_ROOT / "scenarios" / "i24_replica_flow_rc_speedcal_dc_refit.yaml"
SCENARIO_NAME: Final[str] = "i80_replica"

WARMUP_S: Final[float] = 180.0
"""US-101's warm-up (``scenarios/us101_replica.yaml``)."""
WINDOW_S: Final[float] = 300.0
"""Demand windows [s] (US-101's 5-min windows)."""
BOUNDARY_WINDOW_S: Final[float] = 30.0
BOUNDARY_TAIL_M: Final[float] = 100.0
EXIT_EDGE_M: Final[float] = 200.0
"""The boundary edge's length (US-101's ``BOUNDARY_EXIT_BUFFER_M``)."""
INSERTION_BUFFER_M: Final[float] = 2000.0
"""``microsim.runner.CORRIDOR_INSERTION_BUFFER_M``; the approach is ``min(this, site length)``,
the entry buffer the US-101 corridor got (a test checks the two agree)."""
RAMP_EDGE_M: Final[float] = 200.0
"""The ramp edge before the gore [m]: not measured (the recording sees only its last metres);
ramp vehicles are inserted at its start and drive it to the gore."""
RAMP_ANGLE_DEG: Final[float] = 45.0
"""The angle at which the hand-built ramp meets the road: a drawing choice. The runner compiles
without internal junction lanes, so the angle sets only how much netconvert trims the edges at
the merge junction, which :func:`corrected_layout` removes (about 9 m here; at 10 degrees about
60 m, more than a short acceleration lane)."""
MAINLINE_MAXSPEED: Final[str] = "65 mph"
OUTPUT_HZ: Final[float] = 2.0
STEP_S: Final[float] = 0.5
SEED: Final[int] = 42
REPLICATES: Final[int] = 20

ORIGIN_LATLON: Final[tuple[float, float]] = (37.8300, -122.2970)
"""Where the synthetic road starts (Emeryville, CA). The layout is a straight line heading north
with the ramp joining from the east (the right); it is not the real road's alignment."""

#: way ids of the hand-built map (the corridor's SUMO edge ids)
EDGE_APPROACH, EDGE_PRE, EDGE_ACCEL, EDGE_POST, EDGE_EXIT, EDGE_RAMP = (
    "9001",
    "9002",
    "9003",
    "9004",
    "9005",
    "9010",
)
CORRIDOR_EDGES: Final[tuple[str, ...]] = (EDGE_APPROACH, EDGE_PRE, EDGE_ACCEL, EDGE_POST, EDGE_EXIT)
SITE_EDGES: Final[tuple[str, ...]] = (EDGE_PRE, EDGE_ACCEL, EDGE_POST)
RAMP_NAME: Final[str] = "Powell St on-ramp"


# --- demand ------------------------------------------------------------------------------------


def stitched_entries(
    periods: Mapping[str, pd.DataFrame], block: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Per stream, the block's entries on its wall clock after the stitch.

    Returns:
        ``{stream: {"times_s": array, "lanes": array, "switches_ms": [...], "per_period": [...]}}``
        for ``mainline``, ``ramp`` and ``mainline_spatial`` (US-101's rule, reported).
    """
    t0 = i80_data.block_t0_ms(periods, block)
    out: dict[str, dict[str, Any]] = {}
    for stream in ("mainline", "ramp", "mainline_spatial"):
        switches = i80_data.switch_times_ms(periods, block, stream)
        times, lanes, per = [], [], []
        for pos, label in enumerate(block):
            ent = i80_data.stream_entries(periods[label])[stream]
            g = ent["global_time_ms"].to_numpy(dtype=np.float64)
            keep = i80_data.stitched_mask(g, pos, switches)
            times.append((g[keep] - t0) / 1000.0)
            lanes.append(ent["lane"].to_numpy(dtype=np.int64)[keep])
            per.append({"period": label, "n_entries": len(ent), "n_used": int(keep.sum())})
        out[stream] = {
            "times_s": np.concatenate(times) if times else np.zeros(0),
            "lanes": np.concatenate(lanes) if lanes else np.zeros(0, dtype=np.int64),
            "switches_ms": [None if not math.isfinite(s) else int(s) for s in switches],
            "per_period": per,
        }
    return out


def rate_windows(
    times_s: np.ndarray, span_s: float, window_s: float = WINDOW_S
) -> list[dict[str, float]]:
    """Entries counted in windows of ``window_s`` over ``[0, span_s)``, the last one partial
    (``scripts/extract_demand_us101.py`` ``_windows``)."""
    t = np.asarray(times_s, dtype=np.float64)
    out = []
    start = 0.0
    while start < span_s - 1e-9:
        end = min(start + window_s, span_s)
        n = int(np.sum((t >= start) & (t < end)))
        out.append(
            {"t_start_s": start, "t_end_s": end, "n_entries": n, "inflow_veh_s": n / (end - start)}
        )
        start += window_s
    return out


def inflow_steps(windows: Sequence[Mapping[str, float]], warmup_s: float) -> list[list[float]]:
    """Sim-time inflow steps: the first window's rate from t = 0 through the warm-up, then every
    window at ``warm-up + its start`` (``scenarios/us101_replica.yaml``)."""
    steps = []
    for i, w in enumerate(windows):
        t = 0.0 if i == 0 else warmup_s + float(w["t_start_s"])
        steps.append([round(t, 3), round(float(w["inflow_veh_s"]), 6)])
    return steps


def entry_lane_shares(lanes: np.ndarray) -> list[float]:
    """Share of mainline entries per lane, left (lane 1) to right (lane 6)."""
    counts = np.array([np.sum(lanes == k) for k in i80_data.MAINLINE_LANES], dtype=np.float64)
    if counts.sum() <= 0:
        raise ValueError("no mainline entries: cannot measure the entry lane shares")
    return [round(float(c), 6) for c in counts / counts.sum()]


# --- boundary --------------------------------------------------------------------------------


def boundary_schedule(
    periods: Mapping[str, pd.DataFrame], block: Sequence[str], site_length_m: float
) -> list[tuple[float, float]]:
    """The mean mainline speed in the site's last :data:`BOUNDARY_TAIL_M` per
    :data:`BOUNDARY_WINDOW_S` on the block's wall clock (``scripts/m3_us101_validate.py``
    ``_boundary_schedule_wall``), each period's samples up to its switch."""
    t0 = i80_data.block_t0_ms(periods, block)
    switches = i80_data.switch_times_ms(periods, block, "mainline")
    ts, vs = [], []
    for pos, label in enumerate(block):
        df = periods[label]
        x = df["x"].to_numpy(dtype=np.float64)
        sel = (
            df["lane"].isin(i80_data.MAINLINE_LANES).to_numpy()
            & (x >= site_length_m - BOUNDARY_TAIL_M)
            & (x < site_length_m)
        )
        g = df["global_time_ms"].to_numpy(dtype=np.float64)[sel]
        keep = i80_data.stitched_mask(g, pos, switches)
        ts.append((g[keep] - t0) / 1000.0)
        vs.append(df["v"].to_numpy(dtype=np.float64)[sel][keep])
    t = np.concatenate(ts)
    v = np.concatenate(vs)
    span = i80_data.block_span_s(periods, block)
    n_win = max(math.ceil(span / BOUNDARY_WINDOW_S), 1)
    vals = []
    for w in range(n_win):
        m = (t >= w * BOUNDARY_WINDOW_S) & (t < (w + 1) * BOUNDARY_WINDOW_S)
        vals.append(float(v[m].mean()) if m.any() else math.nan)
    filled = pd.Series(vals).ffill().bfill().to_numpy(dtype=np.float64)
    if np.isnan(filled).any():
        raise ValueError("the boundary schedule has no observed samples at all")
    return [(w * BOUNDARY_WINDOW_S, float(val)) for w, val in enumerate(filled)]


def boundary_steps(schedule: Sequence[tuple[float, float]], warmup_s: float) -> list[list[float]]:
    """Sim-time boundary steps: the first value from t = 0 through the warm-up
    (``scripts/m3_us101_validate.py`` ``build_boundary_spec``)."""
    steps = [[0.0, round(float(schedule[0][1]), 4)]]
    steps += [[round(warmup_s + float(t), 3), round(float(v), 4)] for t, v in schedule[1:]]
    return steps


# --- geometry and the hand-built map ------------------------------------------------------------


def geometry(frames: Sequence[pd.DataFrame]) -> dict[str, Any]:
    """The site's layout from the data (``scripts/i80_data.py``'s rules)."""
    lanes = i80_data.check_lanes(frames)
    length = i80_data.site_length_m(frames)
    gore, end, zcounts = i80_data.merge_zone_bounds(frames)
    if not 0.0 < gore < end < length:
        raise ValueError(f"layout out of order: gore {gore:.1f}, lane end {end:.1f}, site {length}")
    x7 = np.concatenate(
        [df.loc[df["lane"] == i80_data.RAMP_LANE, "x"].to_numpy(dtype=np.float64) for df in frames]
    )
    v7 = np.concatenate(
        [df.loc[df["lane"] == i80_data.RAMP_LANE, "v"].to_numpy(dtype=np.float64) for df in frames]
    )
    up = x7 < gore
    return {
        "site_length_m": length,
        "gore_m": round(gore, 2),
        "accel_end_m": round(end, 2),
        "approach_m": min(INSERTION_BUFFER_M, length),
        "exit_m": EXIT_EDGE_M,
        "ramp_m": RAMP_EDGE_M,
        "zone_counts": zcounts,
        "lanes": lanes,
        "lane7_visible_upstream_of_gore_m": round(float(gore - np.quantile(x7, 0.005)), 1),
        "lane7_speed_upstream_of_gore_ms_p50": (
            round(float(np.median(v7[up])), 3) if up.any() else None
        ),
        "lane7_speed_in_zone_ms_p50": (
            round(float(np.median(v7[~up])), 3) if (~up).any() else None
        ),
    }


def _m_per_deg(lat_deg: float) -> tuple[float, float]:
    """Metres per degree of latitude and of longitude at a latitude (WGS84 series)."""
    p = math.radians(lat_deg)
    m_lat = 111132.92 - 559.82 * math.cos(2 * p) + 1.175 * math.cos(4 * p)
    m_lon = 111412.84 * math.cos(p) - 93.5 * math.cos(3 * p) + 0.118 * math.cos(5 * p)
    return m_lat, m_lon


def edge_spans(geom: Mapping[str, Any]) -> dict[str, list[float]]:
    """Each corridor edge's span on the data axis [m] (0 = the site's upstream end)."""
    a, g, e, length = (
        float(geom["approach_m"]),
        float(geom["gore_m"]),
        float(geom["accel_end_m"]),
        float(geom["site_length_m"]),
    )
    return {
        EDGE_APPROACH: [-a, 0.0],
        EDGE_PRE: [0.0, g],
        EDGE_ACCEL: [g, e],
        EDGE_POST: [e, length],
        EDGE_EXIT: [length, length + float(geom["exit_m"])],
    }


NODES: Final[tuple[str, ...]] = ("1", "2", "3", "4", "5", "6")
"""The road's nodes in driving order: the approach's start, the site's start, the gore, the
acceleration lane's end, the site's end, the exit edge's end."""
WAYS: Final[tuple[tuple[str, str, str, int, str, str | None], ...]] = (
    (EDGE_APPROACH, "1", "2", 6, "motorway", MAINLINE_MAXSPEED),
    (EDGE_PRE, "2", "3", 6, "motorway", MAINLINE_MAXSPEED),
    (EDGE_ACCEL, "3", "4", 7, "motorway", MAINLINE_MAXSPEED),
    (EDGE_POST, "4", "5", 6, "motorway", MAINLINE_MAXSPEED),
    (EDGE_EXIT, "5", "6", 6, "motorway", MAINLINE_MAXSPEED),
    (EDGE_RAMP, "10", "3", 1, "motorway_link", None),
)
LAYOUT_TOL_M: Final[float] = 0.5
"""Largest difference between an edge's compiled length and its measured one."""
LAYOUT_MAX_ITER: Final[int] = 4


def nominal_nodes(geom: Mapping[str, Any]) -> dict[str, float]:
    """The road's nodes at the measured positions: distance along the road from node 1 [m]."""
    spans = edge_spans(geom)
    a = float(geom["approach_m"])
    starts = [spans[e][0] + a for e in CORRIDOR_EDGES] + [spans[EDGE_EXIT][1] + a]
    return {nid: round(float(v), 3) for nid, v in zip(NODES, starts, strict=True)}


def osm_xml(node_s: Mapping[str, float], ramp_m: float = RAMP_EDGE_M) -> str:
    """The hand-built map: a straight road heading north, the ramp joining node 3 from the east.

    Args:
        node_s: Each road node's distance along the road from node 1 [m] (:data:`NODES`).
        ramp_m: The ramp's length before the gore [m].
    """
    lat0, lon0 = ORIGIN_LATLON
    m_lat, m_lon = _m_per_deg(lat0)
    pts = {nid: (lat0 + float(node_s[nid]) / m_lat, lon0) for nid in NODES}
    ang = math.radians(RAMP_ANGLE_DEG)
    lat_g, lon_g = pts["3"]
    pts["10"] = (lat_g - ramp_m * math.cos(ang) / m_lat, lon_g + ramp_m * math.sin(ang) / m_lon)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<osm version="0.6" generator="scripts/i80_build_replica.py">',
        "  <!-- NGSIM I-80 eastbound study area (Emeryville, CA) as a straight road: generated "
        "from artifacts/i80_replica_inputs.json, not traced from a map. -->",
    ]
    for nid, (la, lo) in pts.items():
        lines.append(f'  <node id="{nid}" lat="{la:.8f}" lon="{lo:.8f}"/>')
    for wid, n_from, n_to, n_lanes, hw, speed in WAYS:
        lines.append(f'  <way id="{wid}">')
        lines.append(f'    <nd ref="{n_from}"/><nd ref="{n_to}"/>')
        lines.append(f'    <tag k="highway" v="{hw}"/>')
        lines.append('    <tag k="oneway" v="yes"/>')
        lines.append(f'    <tag k="lanes" v="{n_lanes}"/>')
        if speed is not None:
            lines.append(f'    <tag k="maxspeed" v="{speed}"/>')
        lines.append("  </way>")
    lines.append("</osm>")
    return "\n".join(lines) + "\n"


def compiled_lengths(osm_text: str) -> dict[str, float]:
    """The corridor edges' lengths as netconvert compiles the map (the runner's import)."""
    import tempfile

    from microsim.networks import osm_import

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "i80.osm"
        path.write_text(osm_text)
        bundle = osm_import(
            osm_file=path,
            corridor_edges=list(CORRIDOR_EDGES),
            workdir=Path(d) / "net",
            keep_edges=[EDGE_RAMP],
        )
        return {e: float(v) for e, v in zip(bundle.edge_ids, bundle.edge_lengths, strict=True)}


def corrected_layout(
    geom: Mapping[str, Any],
    compile_fn: Callable[[str], Mapping[str, float]] = compiled_lengths,
) -> dict[str, Any]:
    """Node positions whose compiled edges have the measured lengths.

    netconvert trims the edges at the junction where the ramp joins and moves the gore upstream
    (by about 9 m at this ramp angle; the golden ``tests/fixtures/merge.osm`` compiles its 511-m
    ways to 471 / 556 m), which would lengthen the acceleration lane. Each pass compiles the map
    and moves every node by the difference between its measured and its compiled position along
    the road, until every corridor edge is within :data:`LAYOUT_TOL_M` of its measured length.

    Raises:
        RuntimeError: Not within the tolerance after :data:`LAYOUT_MAX_ITER` passes, or a pass
            that would put two road nodes less than a metre apart.
    """
    spans = edge_spans(geom)
    target = np.cumsum([0.0, *[spans[e][1] - spans[e][0] for e in CORRIDOR_EDGES]])
    nodes = np.array([nominal_nodes(geom)[n] for n in NODES], dtype=np.float64)
    history = []
    for it in range(1, LAYOUT_MAX_ITER + 1):
        node_s = {n: round(float(v), 3) for n, v in zip(NODES, nodes, strict=True)}
        lengths = dict(compile_fn(osm_xml(node_s, float(geom["ramp_m"]))))
        got = np.cumsum([0.0, *[lengths[e] for e in CORRIDOR_EDGES]])
        err = target - got
        history.append([round(float(v), 3) for v in err])
        if float(np.max(np.abs(np.diff(err)))) <= LAYOUT_TOL_M:
            return {
                "node_s_m": node_s,
                "compiled_lengths_m": {e: round(lengths[e], 3) for e in lengths},
                "measured_lengths_m": {
                    e: round(spans[e][1] - spans[e][0], 3) for e in CORRIDOR_EDGES
                },
                "passes": it,
                "boundary_errors_m": history,
            }
        nodes = nodes + err
        if float(np.min(np.diff(nodes))) < 1.0:
            raise RuntimeError(
                f"the layout's nodes would cross: boundary errors per pass {history}"
            )
    raise RuntimeError(f"the layout did not converge: boundary errors per pass {history}")


# --- the scenario --------------------------------------------------------------------------------


def fleet_block(path: Path) -> dict[str, Any]:
    """The ``fleet`` block of a committed scenario, whole."""
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict) or "fleet" not in raw:
        raise ValueError(f"{path} has no fleet block")
    return dict(raw["fleet"])


def scenario_raw(
    *,
    osm_file: str,
    mainline_inflow: list[list[float]],
    ramp_inflow: list[list[float]],
    boundary: list[list[float]],
    lane_shares: list[float] | None,
    fleet: Mapping[str, Any],
    duration_s: float,
) -> dict[str, Any]:
    """The kept configuration's scenario document (``merge`` left at its default, LC2013)."""
    network: dict[str, Any] = {
        "kind": "osm",
        "osm_file": osm_file,
        "corridor_edges": list(CORRIDOR_EDGES),
        "inflow": mainline_inflow,
        "boundary": {"steps": boundary, "exit_buffer_m": EXIT_EDGE_M},
        "ramps": [
            {
                "kind": "on",
                "name": RAMP_NAME,
                "edges": [EDGE_RAMP],
                "attach_edge": EDGE_ACCEL,
                "inflow": ramp_inflow,
            }
        ],
    }
    if lane_shares is not None:
        network["entry_lane_shares"] = lane_shares
    return {
        "name": SCENARIO_NAME,
        "tier": "micro",
        "network": network,
        "fleet": dict(fleet),
        "sim": {
            "duration_s": float(duration_s),
            "step_length_s": STEP_S,
            "action_step_s": STEP_S,
            "warmup_s": WARMUP_S,
            "output_hz": OUTPUT_HZ,
        },
        "perturbation": None,
        "seed": SEED,
        "replicates": REPLICATES,
    }


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def build(args: argparse.Namespace) -> dict[str, Any]:
    """Everything from the prepared periods: the map, the scenario, the inputs artifact."""
    t_start = time.time()
    summary = json.loads(Path(args.data_summary).read_text())
    block = list(args.block or summary["replica_block"])
    periods = i80_data.load_periods(block)
    frames = [periods[p] for p in block]
    geom = geometry(frames)
    span = i80_data.block_span_s(periods, block)
    ent = stitched_entries(periods, block)
    win_main = rate_windows(ent["mainline"]["times_s"], span)
    win_ramp = rate_windows(ent["ramp"]["times_s"], span)
    win_spatial = rate_windows(ent["mainline_spatial"]["times_s"], span)
    shares = entry_lane_shares(ent["mainline"]["lanes"]) if args.entry_lanes == "observed" else None
    schedule = boundary_schedule(periods, block, float(geom["site_length_m"]))
    fleet_path = Path(args.fleet_from)
    fleet = fleet_block(fleet_path)
    layout = corrected_layout(geom)
    osm_text = osm_xml(layout["node_s_m"], float(geom["ramp_m"]))
    osm_out = Path(args.osm_out)
    raw = scenario_raw(
        osm_file=rel(osm_out),
        mainline_inflow=inflow_steps(win_main, WARMUP_S),
        ramp_inflow=inflow_steps(win_ramp, WARMUP_S),
        boundary=boundary_steps(schedule, WARMUP_S),
        lane_shares=shares,
        fleet=fleet,
        duration_s=math.ceil(WARMUP_S + span),
    )
    cfg = ScenarioConfig.model_validate(raw)
    h = config_hash(cfg)
    windows = i80_data.analysis_windows(periods, block)
    inputs = {
        "schema_version": 1,
        "kind": "i80_replica_inputs",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "scripts/i80_build_replica.py",
        "code": i80_data.git_head(),
        "code_dirty": i80_data.git_dirty(),
        "data_hash": summary["data_hash"],
        "data_version": summary["data_version"],
        "citation": i80_data.CITATION,
        "block": block,
        "block_t0_wall_clock": i80_data.wall_clock(i80_data.block_t0_ms(periods, block)),
        "block_span_s": round(span, 1),
        "nothing_fitted": "every number below is counted, measured or a stated constant; no "
        "parameter was fitted on I-80",
        "geometry": geom,
        "corridor": {
            "edges": list(CORRIDOR_EDGES),
            "site_first_edge": EDGE_PRE,
            "site_edges": list(SITE_EDGES),
            "attach_edge": EDGE_ACCEL,
            "ramp_edges": [EDGE_RAMP],
            "edge_spans_data_m": edge_spans(geom),
        },
        "osm": {
            "path": rel(osm_out),
            "sha256": sha256_text(osm_text),
            "origin_latlon": list(ORIGIN_LATLON),
            "ramp_angle_deg": RAMP_ANGLE_DEG,
            "mainline_maxspeed": MAINLINE_MAXSPEED,
            "mainline_maxspeed_source": "California Vehicle Code §22349(a) maximum; the "
            "segment's posting is not verified",
            "ramp_maxspeed": "netconvert motorway_link default (80 km/h)",
            "layout": layout,
            "layout_rule": "node positions moved until every corridor edge compiles (netconvert, "
            f"as the runner imports the map) within {LAYOUT_TOL_M} m of its measured length",
        },
        "demand": {
            "rule": "an entry is a vehicle first seen after its period's first frame, on lanes "
            "1-6 (mainline) or 7 (ramp); stitched at each period's last recorded entry of the "
            "stream; 5-min windows on the block's wall clock, the last partial",
            "mainline_windows": win_main,
            "ramp_windows": win_ramp,
            "mainline_windows_us101_spatial_rule": win_spatial,
            "per_period": {s: ent[s]["per_period"] for s in ent},
            "switches_ms": {s: ent[s]["switches_ms"] for s in ent},
            "entry_lanes": args.entry_lanes,
            "entry_lane_shares": shares,
            "entry_lane_counts": [
                int(np.sum(ent["mainline"]["lanes"] == k)) for k in i80_data.MAINLINE_LANES
            ],
        },
        "boundary": {
            "rule": f"mean mainline speed in the site's last {BOUNDARY_TAIL_M:g} m per "
            f"{BOUNDARY_WINDOW_S:g} s on the block's wall clock (each period up to its switch), "
            "empty windows filled forward then backward; the first value held through the "
            "warm-up; on the exit edge",
            "schedule_wall": [[t, round(v, 4)] for t, v in schedule],
            "min_ms": round(min(v for _, v in schedule), 4),
            "max_ms": round(max(v for _, v in schedule), 4),
        },
        "fleet": {
            "from": rel(fleet_path),
            "sha256": hashlib.sha256(fleet_path.read_bytes()).hexdigest(),
            "block": fleet,
        },
        "analysis_windows": windows,
        "warmup_s": WARMUP_S,
        "scenario": {"path": rel(Path(args.out)), "name": SCENARIO_NAME, "config_hash": h},
        "limitations": [
            "The road is a straight hand-built layout with the measured lengths; curvature, "
            "grade and lane widths are not modelled.",
            "Lane 1 is an HOV lane in the recording; the replica has no HOV rule.",
            "The ramp edge (200 m) and its speed are not measured; the mainline speed limit is "
            "California's maximum, not a verified posting.",
            f"Trucks are {summary_truck_share(summary, block)} of the block's vehicles; the "
            "kept I-24 fleet has no heavy vehicles.",
        ],
        "wall_s": round(time.time() - t_start, 1),
    }
    osm_out.parent.mkdir(parents=True, exist_ok=True)
    osm_out.write_text(osm_text)
    header = (
        f"# {SCENARIO_NAME} — NGSIM I-80 eastbound (Emeryville, CA; 13 April 2005), block "
        f"{' + '.join(block)} ({inputs['block_t0_wall_clock']}, {span:.1f} s), E11 of\n"
        "# docs/PRE_FRISCO_PROGRAM.md. Written on the VM by scripts/i80_build_replica.py from the "
        "raw NGSIM\n"
        f"# export (data hash {summary['data_hash'][:12]}…); every number is in "
        f"{rel(Path(args.inputs_out))}.\n"
        "# Geometry, counted demand and the measured boundary as the US-101 replica; the fleet "
        f"block copied from\n# {rel(fleet_path)}; nothing fitted on I-80. "
        f"merge: lane_change (the kept configuration). Do not edit.\n"
        f"# config hash {h}; seeded=False.\n"
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + yaml.safe_dump(raw, sort_keys=False))
    if config_hash(ScenarioConfig.from_yaml(out)) != h:
        raise RuntimeError(f"{out} does not reload to config hash {h}")
    Path(args.inputs_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.inputs_out).write_text(json.dumps(inputs, indent=1, allow_nan=False) + "\n")
    print(f"wrote {out} (config hash {h}), {osm_out}, {args.inputs_out}", flush=True)
    return inputs


def summary_truck_share(summary: Mapping[str, Any], block: Sequence[str]) -> str:
    """The block's truck shares per period, as text for the limitations."""
    shares = [
        f"{p['label']} {p['truck_share']:.1%}"
        for p in summary.get("periods", [])
        if p["label"] in block and p.get("truck_share") is not None
    ]
    return ", ".join(shares) if shares else "an unrecorded share"


def write_osm(inputs_path: Path, out: Path | None = None) -> Path:
    """Rebuild the hand-built map from the inputs artifact and check its sha256."""
    inputs = json.loads(inputs_path.read_text())
    text = osm_xml(inputs["osm"]["layout"]["node_s_m"], float(inputs["geometry"]["ramp_m"]))
    if sha256_text(text) != inputs["osm"]["sha256"]:
        raise RuntimeError("the rebuilt map's sha256 differs from the one the VM recorded")
    path = out if out is not None else REPO_ROOT / inputs["osm"]["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="the scenario from the prepared periods (VM)")
    b.add_argument("--data-summary", default=str(i80_data.SUMMARY_OUT))
    b.add_argument("--block", nargs="+", default=None, help="periods (default: the replica block)")
    b.add_argument("--fleet-from", default=str(FLEET_FROM))
    b.add_argument("--entry-lanes", choices=("observed", "round_robin"), default="observed")
    b.add_argument("--out", default=str(SCENARIO_OUT))
    b.add_argument("--inputs-out", default=str(INPUTS_OUT))
    b.add_argument("--osm-out", default=str(OSM_OUT))
    w = sub.add_parser("write-osm", help="rebuild the map from the inputs artifact (after ingest)")
    w.add_argument("--inputs", default=str(INPUTS_OUT))
    w.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "build":
        build(args)
    else:
        print(write_osm(Path(args.inputs), Path(args.out) if args.out else None))


if __name__ == "__main__":
    main()
