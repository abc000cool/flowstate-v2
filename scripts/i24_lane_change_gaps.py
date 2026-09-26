"""Accepted lane-change gaps: I-24 MOTION against the weave model (WP-77).

The weave model's acceptance (``microsim.runner._weave_step``: ``s0 +
accept · v`` on both sides, the changer's brake gap on its new leader, the
follower absorbing the changer within its ``b``, the forced guard) has never
been compared with how real drivers change lanes in weaving sections
(docs/WEAVE_MODEL_PLAN.md, WP-76 hand-on (c)). This driver measures, with
``calibration.lane_change_gaps``, every lane change's gaps and evaluates the
model's acceptance on them, on either side of the comparison:

* **observed** (default; a cloud stage — it reads the 993 MB processed
  westbound table, which the laptop must not): the I-24 MOTION westbound day
  (``data/i24motion/processed/i24_wb_20221130``, 06:00–10:00 CST, 5 Hz,
  fragments), in 15-min chunks padded by 8 s so the debounce at the chunk
  edges sees what the whole record would, over the measured span (data x
  0–5,492 m) cut into the ramp zones of ``scripts/i24_lanechange_observed.py``
  — the Old Hickory acceleration lane (merge), the Hickory Hollow deceleration
  lane (diverge), the Hickory Hollow–Bell Road weaving section (a one-sided
  ramp weave, 585 m between the landmarks), the Bell Road diverge — with the
  landmarks projected from ``artifacts/i24_replica_inputs.json``. Writes
  ``artifacts/i24_lane_change_gaps.json`` (summaries, counts, a 200-row sample)
  and the per-change table
  ``data/i24motion/processed/i24_wb_lane_change_gaps.parquet`` (gitignored;
  it rides along in the pipeline archive).
* **simulated** (``--sim-run-dir DIR [DIR ...]``; small runs only locally):
  microsim run directories (``trajectories.parquet``, ``vehicles.parquet``,
  ``meta.json``, the compiled net), lanes mapped to the band convention
  through the net's lane counts, zones from ``meta.json["ramps"]`` (an attach
  edge with an on- and an off-ramp is a weave, with only an on-ramp a merge,
  with only an off-ramp a diverge), each change tagged with the vehicle's
  ``origin->destination``; the records of all given runs are pooled.

The model's acceptance is evaluated on both sides with the same population
means (the fleet's IDM artifact, ``artifacts/idm_i24_capacity.json``, the
corridor fleet and the T.H.52 fixture's) and ``WEAVE_DEFAULTS``; the
follower's desired speed is capped at the site's lane speed as the runner
caps it.

Run (VM):  ``uv run --no-sync python scripts/i24_lane_change_gaps.py``
Run (sim): ``uv run --no-sync python scripts/i24_lane_change_gaps.py
--sim-run-dir runs/x/<hash>/3 runs/x/<hash>/4 --out artifacts/<name>.json``
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from calibration.lane_change_gaps import (
    AcceptanceParams,
    LaneChangeGaps,
    Zone,
    lane_change_gaps,
    sample_records,
    sim_band_lanes,
    summarize_gaps,
)
from calibration.lanechange import DEFAULT_MAX_GAP_FACTOR, DEFAULT_MIN_DWELL_S
from calibration.loaders.i24motion import (
    I24_CITATION,
    I24_MAINLINE_LANES,
    load_i24_parquet,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WB_DIR = REPO_ROOT / "data" / "i24motion" / "processed" / "i24_wb_20221130"
INPUTS = REPO_ROOT / "artifacts" / "i24_replica_inputs.json"
POPULATION = REPO_ROOT / "artifacts" / "idm_i24_capacity.json"
OUT = REPO_ROOT / "artifacts" / "i24_lane_change_gaps.json"
RECORDS_OUT = REPO_ROOT / "data" / "i24motion" / "processed" / "i24_wb_lane_change_gaps.parquet"

SAMPLE_DT_S = 0.2
"""Sampling interval of the processed table [s] (25 Hz decimated by 5)."""
CHUNK_S = 900.0
PAD_S = 2.0 * (DEFAULT_MIN_DWELL_S + DEFAULT_MAX_GAP_FACTOR * SAMPLE_DT_S) + 5.0
"""Load pad per chunk side [s], as ``scripts/i24_lanechange_observed.py`` (8 s)."""
X_MARGIN_M = 250.0
"""Rows loaded beyond the span on each side [m]: more than the 200 m
neighbour range, so a change at the span's edge sees its neighbours."""
T_RANGE_S: tuple[float, float] = (0.0, 14400.0)
"""The whole recording, 06:00–10:00 CST (``t`` = seconds after 06:00)."""
I24_MAINLINE_LANES_TUPLE: tuple[int, ...] = tuple(
    range(I24_MAINLINE_LANES[0], I24_MAINLINE_LANES[1] + 1)
)
"""Mainline lanes 1–4 (``I24_MAINLINE_LANES`` is the inclusive range)."""
AUX_LANES: tuple[int, ...] = (5,)
"""The auxiliary lane band: lane-5 occupancy stands at 0.75–1.95 km,
3.45–3.95 km (docs/I24_VALIDATION.md, the map defects) and 4.5–5.5 km
(``artifacts/i24_lane_profile.json``, observed rows), the three ramp lanes."""
I24_WB_LANE_SPEED_MS = 31.29
"""Lane speed of the westbound weave edge 992666043 in the compiled replica
net (``data/i24motion/processed/net_raw_corrected/osm.net.xml``; OSM
``maxspeed`` 70 mph): the cap the runner would put on the follower's v0."""

#: The ramp-relative partition of scripts/i24_lanechange_observed.py
#: (``RAMP_ZONES``) with each zone's kind: (name, landmark that opens it, kind).
I24_ZONES: tuple[tuple[str, str | None, str], ...] = (
    ("upstream_of_OH", None, "basic"),
    ("OH_acceleration_lane", "wb_OH_on_start", "merge"),
    ("OH_to_HH_diverge", "wb_OH_on_end", "basic"),
    ("HH_diverge", "wb_HH_off_start", "diverge"),
    ("HH_off_to_HH_on", "wb_HH_off_end", "basic"),
    ("HH_on_BR_off_weave", "wb_HH_on_start", "weave"),
    ("BR_diverge", "wb_BR_off_start", "diverge"),
)

WEAVE_COUNTERS: tuple[str, ...] = (
    "n_entered",
    "n_changed_in",
    "n_changed_out",
    "n_forced",
    "n_unfinished",
    "n_missed_exit",
    "n_reached_section_exiting",
    "n_exited",
)
"""``meta.json["weave_sections"]`` counters copied into a simulated artifact:
how many crossings the weave drove (the rest are SUMO's own changes)."""

SAMPLE_N = 200
SAMPLE_COLUMNS: tuple[str, ...] = (
    "seed",
    "t",
    "veh_id",
    "x",
    "zone",
    "movement",
    "group",
    "from_lane",
    "to_lane",
    "v",
    "confirmed",
    "suspect",
    "lead_gap_m",
    "lead_v",
    "lead_time_gap_s",
    "lag_gap_m",
    "lag_v",
    "lag_time_gap_s",
    "need_lead_m",
    "need_lag_m",
    "model_accepts",
)
"""Columns of the sample table (``seed`` and ``group`` on simulated records only)."""
SAMPLE_SEED = 20260925

LIMITATIONS: tuple[str, ...] = (
    "I-24 MOTION tracks about half of the peak vehicle-time (docs/I24_DATA.md §4): the "
    "nearest tracked vehicle is not always the nearest vehicle, so space gaps and lead time "
    "gaps (over the changer's own speed) are upper bounds. A recorded lag can be a different "
    "vehicle at a different speed, so lag time gaps are expected to read long but are not "
    "bounds, and the share the model's acceptance refuses is expected to read low but is not "
    "a lower bound: only the lead time-gap term's refusals are, as every other term reads a "
    "neighbour's speed. Changes made while a vehicle was untracked are missing.",
    "Documents are fragments (median 117 m, 9.9 s): a change is seen only when one fragment "
    "spans it; a change within 1 s of a fragment's start or end is unconfirmed and left out "
    "of the summaries (counted).",
    "Lanes are lateral bands floor(y / 12 ft) of the tracker's smoothed position; the A-B-A "
    "debounce (1 s) and the non-adjacent drop are the only noise guards; a change is timed "
    "when the vehicle's centre crosses the band edge, mid-manoeuvre, while SUMO's changes are "
    "instantaneous.",
    "Movements are read from lanes and zones (trajectories carry no route): an auxiliary-to-"
    "mainline change in a weave is 'entering' even if a through driver made it.",
    "One day (30 Nov 2022), one direction, one weaving section (Hickory Hollow-Bell Road, "
    "585 m), congested for most of the morning; the model's acceptance is evaluated at the "
    "population means, not at each driver's own parameters.",
)


def git_head() -> str:
    """The code's commit (the VM's snapshot commit message names the source SHA)."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H %s"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def rel(path: Path) -> str:
    """``path`` relative to the repository when it lies inside it."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def population_means(path: Path) -> dict[str, float]:
    """``mean`` of an IDM population artifact (keys v0, T, a_max, b, s0)."""
    return {k: float(v) for k, v in json.loads(path.read_text())["mean"].items()}


def i24_zones(inputs_path: Path = INPUTS) -> tuple[list[Zone], tuple[float, float]]:
    """The ramp zones on the data axis and the measured span [data m]."""
    geo = json.loads(inputs_path.read_text())["geometry"]
    x0 = float(geo["data_x0_chain_m"])
    scale = float(geo["chain_m_per_data_m"])
    marks = {k: (float(v) - x0) / scale for k, v in geo["ramp_landmarks_chain_m"].items()}
    lo, hi = (float(v) for v in geo["measured_span_data_x_m"])
    edges = [lo] + [marks[key] for _, key, _ in I24_ZONES if key is not None] + [hi]
    if any(b <= a for a, b in pairwise(edges)):
        raise ValueError(f"zone edges are not increasing: {edges}")
    zones = [
        Zone(name, kind, a, b)
        for (name, _, kind), (a, b) in zip(I24_ZONES, pairwise(edges), strict=True)
    ]
    return zones, (lo, hi)


def _counts_add(total: dict[str, int], part: dict[str, int]) -> None:
    for k, val in part.items():
        total[k] = total.get(k, 0) + int(val)


def group_counts(records: pd.DataFrame, by: tuple[str, ...]) -> list[dict[str, Any]]:
    """Changes per group: all, confirmed, suspect, and used by the summaries."""
    if records.empty:
        return []
    rows = []
    for key, sub in records.groupby(list(by), sort=True):
        key_t = key if isinstance(key, tuple) else (key,)
        conf = sub["confirmed"].astype(bool)
        sus = sub["suspect"].astype(bool)
        rows.append(
            {
                **{k: str(v) for k, v in zip(by, key_t, strict=True)},
                "n_all": len(sub),
                "n_confirmed": int(conf.sum()),
                "n_suspect": int(sus.sum()),
                "n_used": int((conf & ~sus).sum()),
            }
        )
    return rows


def sample_table(records: pd.DataFrame) -> dict[str, Any]:
    """A seeded sample of the records as a compact table (``columns`` + ``rows``)."""
    cols = [c for c in SAMPLE_COLUMNS if c in records.columns]
    rows = sample_records(records[cols], SAMPLE_N, seed=SAMPLE_SEED) if len(records) else []
    return {
        "n": len(rows),
        "seed": SAMPLE_SEED,
        "columns": cols,
        "rows": [[r[c] for c in cols] for r in rows],
    }


def build_artifact(
    records: pd.DataFrame,
    counts: dict[str, int],
    *,
    kind: str,
    zones: list[Zone],
    acceptance: AcceptanceParams,
    parameters: dict[str, Any],
    provenance: dict[str, Any],
    group_by: tuple[str, ...],
    wall_s: float,
) -> dict[str, Any]:
    """The JSON artifact (docs/CONTRACTS.md, "Lane-change gap records")."""
    by_kind = ("zone_kind", "movement")
    by_zone = ("zone", "zone_kind", "movement")
    out: dict[str, Any] = {
        "schema_version": 1,
        "kind": kind,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "scripts/i24_lane_change_gaps.py",
        "code": git_head(),
        **provenance,
        "method": {
            **parameters,
            "module": "calibration.lane_change_gaps",
            "change_time": "first sample in the new (debounced) lane",
            "lead": "nearest vehicle in the target lane whose front is strictly ahead of the "
            "changer's front, at the change's time stamp",
            "lag": "nearest vehicle in the target lane whose front is at or behind it",
            "gaps": "bumper to bumper; time gaps over the rear vehicle's speed",
            "summaries_exclude": "unconfirmed and suspect changes",
        },
        "zones": [z.to_dict() for z in zones],
        "acceptance": acceptance.to_dict(),
        "counts": counts,
        "counts_by_zone_kind": group_counts(records, by_kind),
        "counts_by_zone": group_counts(records, by_zone),
        "summary_by_zone_kind": summarize_gaps(records, by=by_kind),
        "summary_by_zone": summarize_gaps(records, by=by_zone),
        # sensitivity: with the changes at a track's first or last second kept (on a simulated
        # run those are the ones made on arriving from, or just before leaving onto, a ramp)
        "summary_by_zone_kind_incl_unconfirmed": summarize_gaps(
            records, by=by_kind, include_unconfirmed=True
        ),
        "sample": sample_table(records),
        "wall_s": round(wall_s, 1),
    }
    if group_by:
        out["counts_by_group"] = group_counts(records, (*group_by, "zone_kind", "movement"))
        out["summary_by_group"] = summarize_gaps(records, by=(*group_by, "zone_kind", "movement"))
    return out


def run_observed(args: argparse.Namespace) -> None:
    """The I-24 MOTION westbound day, chunked."""
    t_start = time.time()
    if not (WB_DIR / "trajectories.parquet").exists():
        raise SystemExit(
            f"{rel(WB_DIR)}/trajectories.parquet is missing: this mode reads the processed "
            "I-24 MOTION table (a cloud stage; launch the VM with --data-set i24)"
        )
    meta = json.loads((WB_DIR / "meta.json").read_text())
    zones, span = i24_zones()
    acceptance = AcceptanceParams.from_population(
        population_means(POPULATION),
        v0_cap_ms=I24_WB_LANE_SPEED_MS,
        source=f"{rel(POPULATION)} means; v0 capped at the weave edge's "
        f"lane speed {I24_WB_LANE_SPEED_MS} m/s; WEAVE_DEFAULTS time gaps",
    )
    t_lo, t_hi = args.t_range
    parts: list[pd.DataFrame] = []
    counts: dict[str, int] = {}
    params: dict[str, Any] = {}
    n_chunks = int(np.ceil((t_hi - t_lo) / CHUNK_S))
    for k in range(n_chunks):
        lo = t_lo + k * CHUNK_S
        hi = min(lo + CHUNK_S, t_hi)
        df = load_i24_parquet(
            WB_DIR,
            t_range_s=(lo - PAD_S, hi + PAD_S),
            x_range_m=(span[0] - X_MARGIN_M, span[1] + X_MARGIN_M),
            columns=["t", "veh_id", "x", "lane", "v", "length"],
        )
        out: LaneChangeGaps = lane_change_gaps(
            df,
            zones,
            mainline_lanes=I24_MAINLINE_LANES_TUPLE,
            aux_lanes=AUX_LANES,
            dt_s=SAMPLE_DT_S,
            window_s=(lo, hi),
            x_range_m=span,
            acceptance=acceptance,
        )
        if len(out.records):  # an empty chunk's object-typed frame would upcast the concat
            parts.append(out.records)
        _counts_add(counts, out.counts)
        params = out.parameters
        print(
            f"chunk {k + 1}/{n_chunks} t [{lo:.0f}, {hi:.0f}) rows {len(df):,} "
            f"changes {len(out.records):,} ({time.time() - t_start:.0f} s)",
            flush=True,
        )
        del df
    records = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    params = {**params, "window_s": [t_lo, t_hi], "chunk_s": CHUNK_S, "pad_s": PAD_S}
    RECORDS_OUT.parent.mkdir(parents=True, exist_ok=True)
    records.to_parquet(RECORDS_OUT, index=False)
    art = build_artifact(
        records,
        counts,
        kind="observed",
        zones=zones,
        acceptance=acceptance,
        parameters=params,
        provenance={
            "data_hash": meta["data_hash"],
            "data": rel(WB_DIR),
            "time_origin": "t = seconds after 06:00:00 CST, 30 Nov 2022",
            "x_axis": "data x [m], front bumper, 0 at MM 62.7, westbound",
            "span_data_x_m": list(span),
            "citation": I24_CITATION,
            "records_file": rel(RECORDS_OUT),
            "limitations": list(LIMITATIONS),
        },
        group_by=(),
        wall_s=time.time() - t_start,
    )
    Path(args.out).write_text(json.dumps(art, indent=1, allow_nan=False) + "\n")
    print(f"wrote {args.out} ({len(records):,} changes) and {RECORDS_OUT}", flush=True)


def _sim_run_frame(
    run_dir: Path,
    length_m: float,
) -> tuple[pd.DataFrame, list[Zone], tuple[int, ...], tuple[int, ...], dict[str, Any]]:
    """One microsim run: band-lane trajectories with lengths, zones, lanes and provenance."""
    import sumolib

    meta = json.loads((run_dir / "meta.json").read_text())
    net = sumolib.net.readNet(str(next(run_dir.glob("**/*.net.xml"))))
    edges = [str(e) for e in meta["config"]["network"]["corridor_edges"]]
    lengths = [float(net.getEdge(e).getLength()) for e in edges]
    lanes = [len(net.getEdge(e).getLanes()) for e in edges]
    offsets = [
        float(meta["corridor"]["x_first_edge_m"]) + float(v)
        for v in np.cumsum([0.0, *lengths[:-1]])
    ]
    df = pd.read_parquet(
        run_dir / "trajectories.parquet", columns=["t", "veh_id", "x", "lane", "v", "is_heavy"]
    )
    heavy = meta["config"]["fleet"].get("heavy") or {}
    heavy_len = float(heavy.get("length_m") or length_m)
    # every vehicle has the passenger vType length unless the fleet's heavy block gives its own
    df["length"] = np.where(df.pop("is_heavy").to_numpy(dtype=bool), heavy_len, length_m)
    df = sim_band_lanes(df, offsets, lanes)
    n_through = min(lanes)
    mainline = tuple(range(1, n_through + 1))
    aux = tuple(range(n_through + 1, max(lanes) + 1))
    by_edge: dict[str, dict[str, Any]] = {}
    for r in meta.get("ramps") or []:
        z = by_edge.setdefault(
            str(r["attach_edge"]),
            {
                "kinds": set(),
                "names": [],
                "lo": float(r["attach_x_m"]),
                "hi": float(r["attach_end_x_m"]),
            },
        )
        z["kinds"].add(str(r["kind"]))
        z["names"].append(str(r["name"]))
    zones = []
    for edge, z in by_edge.items():
        kind = (
            "weave"
            if z["kinds"] == {"on", "off"}
            else ("merge" if z["kinds"] == {"on"} else "diverge")
        )
        zones.append(Zone(f"{edge}:{'+'.join(z['names'])}", kind, z["lo"], z["hi"]))
    weave_speed = [
        float(net.getEdge(e).getSpeed())
        for ws in meta.get("weave_sections") or []
        for e in ws["edges"]
    ]
    v_cap = (
        min(weave_speed) if weave_speed else max(float(net.getEdge(e).getSpeed()) for e in edges)
    )
    prov = {
        "run_dir": f"<runs>/{run_dir.parent.name}/{run_dir.name}",
        "config_hash": meta["config_hash"],
        "seed": meta["seed"],
        "scenario": meta["config"]["name"],
        "fleet_idm_calibration": meta["config"]["fleet"].get("idm_calibration"),
        "edges": edges,
        "edge_offsets_m": offsets,
        "edge_lanes": lanes,
        "v0_cap_ms": v_cap,
        "n_collisions": meta.get("n_collisions"),
        "weave_sections": [
            {k: ws.get(k) for k in WEAVE_COUNTERS} | {"edges": ws.get("edges")}
            for ws in meta.get("weave_sections") or []
        ],
    }
    return df, sorted(zones, key=lambda z: z.x_lo_m), mainline, aux, prov


def run_simulated(args: argparse.Namespace) -> None:
    """Pooled records of microsim run directories."""
    t_start = time.time()
    parts: list[pd.DataFrame] = []
    counts: dict[str, int] = {}
    runs: list[dict[str, Any]] = []
    zones: list[Zone] = []
    params: dict[str, Any] = {}
    acceptance: AcceptanceParams | None = None
    for d in args.sim_run_dir:
        run_dir = Path(d)
        df, zones, mainline, aux, prov = _sim_run_frame(run_dir, float(args.sim_length_m))
        pop_path = REPO_ROOT / str(
            prov["fleet_idm_calibration"] or POPULATION.relative_to(REPO_ROOT)
        )
        acceptance = AcceptanceParams.from_population(
            population_means(pop_path),
            v0_cap_ms=float(prov["v0_cap_ms"]),
            source=f"{rel(pop_path)} means; v0 capped at the weave edge's lane "
            f"speed {prov['v0_cap_ms']} m/s; WEAVE_DEFAULTS time gaps",
        )
        veh = pd.read_parquet(
            run_dir / "vehicles.parquet", columns=["veh_id", "origin", "destination"]
        )
        groups = {
            str(i): f"{o}->{dst}"
            for i, o, dst in zip(veh["veh_id"], veh["origin"], veh["destination"], strict=True)
        }
        out = lane_change_gaps(
            df,
            zones,
            mainline_lanes=mainline,
            aux_lanes=aux,
            acceptance=acceptance,
            groups=groups,
        )
        rec = out.records
        rec.insert(0, "seed", prov["seed"])
        if len(rec):
            parts.append(rec)
        _counts_add(counts, out.counts)
        params = {**out.parameters, "default_length_m": args.sim_length_m}
        runs.append({**prov, "counts": out.counts})
        print(f"{run_dir}: {len(rec):,} changes", flush=True)
    if acceptance is None:
        raise SystemExit("no run directories given")
    records = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    art = build_artifact(
        records,
        counts,
        kind="simulated",
        zones=zones,
        acceptance=acceptance,
        parameters=params,
        provenance={"runs": runs},
        group_by=("group",),
        wall_s=time.time() - t_start,
    )
    Path(args.out).write_text(json.dumps(art, indent=1, allow_nan=False) + "\n")
    print(f"wrote {args.out} ({len(records):,} changes)", flush=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--out",
        default=None,
        help=f"artifact path (observed default: {rel(OUT)}; required with --sim-run-dir)",
    )
    ap.add_argument(
        "--t-range",
        nargs=2,
        type=float,
        default=list(T_RANGE_S),
        metavar=("LO", "HI"),
        help="observed: window [s after 06:00 CST] (default: the whole recording)",
    )
    ap.add_argument("--sim-run-dir", nargs="+", default=None, help="simulated: microsim run dirs")
    ap.add_argument(
        "--sim-length-m",
        type=float,
        default=None,
        help="simulated: passenger vehicle length [m] (default: microsim.vehicles.VEHICLE_LENGTH_M; "
        "heavy vehicles take the fleet's heavy length_m)",
    )
    args = ap.parse_args(argv)
    if args.sim_run_dir:
        if args.out is None:
            ap.error("--out is required with --sim-run-dir")
        if args.sim_length_m is None:
            from microsim.vehicles import VEHICLE_LENGTH_M

            args.sim_length_m = float(VEHICLE_LENGTH_M)
        run_simulated(args)
    else:
        if args.out is None:
            args.out = str(OUT)
        run_observed(args)


if __name__ == "__main__":
    main()
