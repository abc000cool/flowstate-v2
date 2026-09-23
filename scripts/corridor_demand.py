"""Calibrate an onboarded corridor's demand from its detector observations.

Fills a scenario written by ``scripts/onboard_corridor.py`` (mainline inflow,
every discovered ramp, the downstream speed boundary, warm-up and the driver
population) from a ``flowstate.observations/1`` artifact, and writes the
``flowstate.demand/1`` artifact that records how each number was derived.

Positions: the observations artifact carries detector positions measured along
the inventory (haversine between IRIS nodes); the simulation measures x along
the SUMO chain. The station table written by the onboarding CLI (``x_m`` on the
chain, plus the projection offset) is the bridge — every observed station's
``x_m`` is rewritten to its chain position so the report compares crossings at
the same cross-section, and the inventory values are kept under
``source["x_m_inventory"]``.

Ramp flows close the mainline balance bracket by bracket. Between two
consecutive mainline stations the observed change ``q_down − q_up`` (per
window) is assigned to the discovered ramps of that bracket, in x order: live
ramp detectors (mean flow over the span ≥ ``--alive-veh-h`` and below 90 % of
the arriving mainline flow) fix the split between ramps of the same kind and
the flow of the kind that is not the closing one; the closing kind absorbs the
remainder so the simulated mainline flow reproduces every station count in
free flow. A bracket with no ramp of the needed kind carries its residual into
the next bracket (listed in the artifact); ramps outside the observed span are
set to zero (their traffic is inside the nearest mainline count already).
Discovered ramps are matched to observed ramp detectors of the same kind by
global nearest distance within ``--match-radius-m``, each detector once.

Example::

    uv run --no-sync python scripts/corridor_demand.py \\
        --scenario scenarios/mndot_i94_wb_stpaul.yaml \\
        --observations data/mndot/mndot_i94_wb_stpaul/observations.json \\
        --stations-x data/mndot/mndot_i94_wb_stpaul/stations_x.csv \\
        --upstream S1063 --downstream S97 \\
        --idm-calibration artifacts/idm_i24_capacity.json \\
        --demand-out artifacts/demand_mndot_i94_wb_stpaul.json
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import math
import sys
from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml

from calibration.demand import DemandArtifact, demand_from_observations
from calibration.observations import Observations
from flowstate_core.config import ScenarioConfig, config_hash


def _chain_x(net_path: Path, chain: list[str]) -> dict[str, tuple[float, float]]:
    """Start and end x [m] of every chain edge, walking the SUMO net in order."""
    import sumolib

    net = sumolib.net.readNet(str(net_path))
    out: dict[str, tuple[float, float]] = {}
    x = 0.0
    for edge_id in chain:
        length = float(net.getEdge(edge_id).getLength())
        out[edge_id] = (x, x + length)
        x += length
    return out


def _read_stations_x(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("x_m") in (None, ""):
                continue
            rows[row["station"]] = {
                "x_m": float(row["x_m"]),
                "offset_m": float(row.get("offset_m") or "nan"),
                "kind": row.get("kind", "mainline"),
            }
    return rows


def _steps_from_series(values: list[float], step_s: float, fallback: float) -> list[list[float]]:
    """``[[t, v], ...]`` from a per-window series; NaN carries the previous value."""
    steps: list[list[float]] = []
    last = fallback
    for k, v in enumerate(values):
        if v is not None and not math.isnan(v):
            last = float(v)
        steps.append([k * step_s, last])
    return steps


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--scenario", required=True, type=Path)
    ap.add_argument("--observations", required=True, type=Path)
    ap.add_argument("--stations-x", required=True, type=Path, help="station table with chain x_m")
    ap.add_argument(
        "--net",
        type=Path,
        help="SUMO net of the onboarding workdir (default: derived from --workdir)",
    )
    ap.add_argument(
        "--workdir", type=Path, default=None, help="onboarding workdir (net/osm.net.xml)"
    )
    ap.add_argument("--upstream", required=True, help="mainline station supplying the entry inflow")
    ap.add_argument(
        "--downstream", required=True, help="mainline station supplying the exit speed boundary"
    )
    ap.add_argument(
        "--idm-calibration", default=None, help="IDMCalibration artifact for the driver population"
    )
    ap.add_argument("--warmup-s", type=float, default=1800.0)
    ap.add_argument("--match-radius-m", type=float, default=350.0)
    ap.add_argument(
        "--alive-veh-h",
        type=float,
        default=30.0,
        help="a ramp detector below this mean flow is dead",
    )
    ap.add_argument(
        "--out", type=Path, default=None, help="scenario YAML to write (default: in place)"
    )
    ap.add_argument("--demand-out", required=True, type=Path)
    args = ap.parse_args(argv)

    raw: dict[str, Any] = yaml.safe_load(args.scenario.read_text())
    net_path = (
        args.net or (args.workdir or Path("runs/onboard") / raw["name"]) / "net" / "osm.net.xml"
    )
    if not net_path.exists():
        print(f"net not found: {net_path}", file=sys.stderr)
        return 2
    chain: list[str] = [str(e) for e in raw["network"]["corridor_edges"]]
    edge_x = _chain_x(net_path, chain)
    length_m = max(e for _, e in edge_x.values())

    obs = Observations.from_json(args.observations)
    step_s = float(obs.window_s)
    sx = _read_stations_x(args.stations_x)

    # 1. Observed stations onto the chain.
    inventory_x: dict[str, float | None] = {}
    placed = []
    for st in obs.stations:
        inventory_x[st.id] = st.x_m
        if st.id not in sx:
            print(
                f"station {st.id} has no chain position in {args.stations_x}; kept inventory x",
                file=sys.stderr,
            )
            placed.append(st)
            continue
        placed.append(dataclasses.replace(st, x_m=sx[st.id]["x_m"]))
    obs = dataclasses.replace(obs, stations=placed)
    obs.source["x_reference"] = (
        "x_m along the SUMO corridor chain (scripts/onboard_corridor.py projection); "
        "inventory (IRIS r_node haversine) positions kept under x_m_inventory"
    )
    obs.source["x_m_inventory"] = inventory_x
    obs.source["chain_length_m"] = length_m

    # 2. Discovered ramps with chain x, matched to observed ramp detectors (global nearest).
    observed_ramps = {st.id: st for st in obs.stations if st.kind in ("on_ramp", "off_ramp")}
    ramps_yaml = raw["network"]["ramps"]
    descriptors: list[dict[str, Any]] = []
    for ramp in ramps_yaml:
        kind = "on" if str(ramp["kind"]) == "on" else "off"
        x0, x1 = edge_x[str(ramp["attach_edge"])]
        descriptors.append({"name": ramp["name"], "kind": kind, "x_m": x0 if kind == "on" else x1})
    pairs = sorted(
        (abs(st.x_m - d["x_m"]), i, sid)
        for i, d in enumerate(descriptors)
        for sid, st in observed_ramps.items()
        if st.x_m is not None
        and st.kind == ("on_ramp" if d["kind"] == "on" else "off_ramp")
        and abs(st.x_m - d["x_m"]) <= args.match_radius_m
    )
    used: set[str] = set()
    for dist, i, sid in pairs:
        if sid in used or "station" in descriptors[i]:
            continue
        used.add(sid)
        descriptors[i]["station"] = sid
        descriptors[i]["match_distance_m"] = round(dist, 1)
    unmatched_detectors = sorted(set(observed_ramps) - used)

    # 3. Profiles: entry inflow from the upstream station; ramps close the bracket balance.
    inflow_steps = demand_from_observations(obs, args.upstream, step_s=step_s)
    n = len(inflow_steps)
    mainline = sorted(
        (st for st in obs.stations if st.kind == "mainline" and st.x_m is not None),
        key=lambda st: st.x_m,
    )
    first_x = next(st.x_m for st in mainline if st.id == args.upstream)
    last_x = next(st.x_m for st in mainline if st.id == args.downstream)
    mainline = [st for st in mainline if first_x <= st.x_m <= last_x]

    def series(sid: str) -> list[float]:
        return [float("nan") if v is None else float(v) for v in obs.flows_veh_h[sid]]

    def alive(desc: dict[str, Any], q_arrive: list[float]) -> bool:
        sid = desc.get("station")
        if sid is None:
            return False
        q = series(sid)
        vals = [v for v in q if not math.isnan(v)]
        if not vals or sum(vals) / len(vals) < args.alive_veh_h:
            return False
        ratio = [
            v / a
            for v, a in zip(q, q_arrive, strict=True)
            if not math.isnan(v) and not math.isnan(a) and a > 0
        ]
        return bool(ratio) and (sum(ratio) / len(ratio)) < 0.9

    for d in descriptors:
        d["on_veh_h"] = [0.0] * n
        d["exit_frac"] = [0.0] * n
        d["method"] = "zero_outside_observed_span"
    zeroed = [
        f"{d['name']} (x={d['x_m']:.0f} m): outside the observed span [{first_x:.0f}, {last_x:.0f}] m"
        for d in descriptors
        if not (first_x < d["x_m"] <= last_x)
    ]
    residual_log: list[dict[str, Any]] = []
    carried = [0.0] * n
    for up, down in pairwise(mainline):
        q_up, q_down = series(up.id), series(down.id)
        bracket = sorted(
            (d for d in descriptors if up.x_m < d["x_m"] <= down.x_m), key=lambda d: d["x_m"]
        )
        ons = [d for d in bracket if d["kind"] == "on"]
        offs = [d for d in bracket if d["kind"] == "off"]
        live_on = {id(d) for d in ons if alive(d, q_up)}
        live_off = {id(d) for d in offs if alive(d, q_up)}
        for d in bracket:
            d["method"] = "detector" if id(d) in live_on | live_off else "conservation"
        last_on = [0.0] * len(ons)
        last_off = [0.0] * len(offs)
        for k in range(n):
            if math.isnan(q_up[k]) or math.isnan(q_down[k]):
                for j, d in enumerate(ons):
                    d["on_veh_h"][k] = last_on[j]
                for j, d in enumerate(offs):
                    d["exit_frac"][k] = last_off[j]
                continue
            delta = q_down[k] - q_up[k] + carried[k]
            on_total = [
                max(series(d["station"])[k], 0.0)
                if id(d) in live_on and not math.isnan(series(d["station"])[k])
                else 0.0
                for d in ons
            ]
            off_total = [
                max(series(d["station"])[k], 0.0)
                if id(d) in live_off and not math.isnan(series(d["station"])[k])
                else 0.0
                for d in offs
            ]
            # what the live detectors leave unexplained
            r = delta - (sum(on_total) - sum(off_total))
            dead_on = [j for j, d in enumerate(ons) if id(d) not in live_on]
            dead_off = [j for j, d in enumerate(offs) if id(d) not in live_off]
            leftover = 0.0
            if r > 0 and dead_on:
                for j in dead_on:
                    on_total[j] += r / len(dead_on)
            elif r < 0 and dead_off:
                for j in dead_off:
                    off_total[j] += -r / len(dead_off)
            elif r > 0 and ons:
                shares = on_total if sum(on_total) > 0 else [1.0] * len(ons)
                for j in range(len(ons)):
                    on_total[j] += r * shares[j] / sum(shares)
                    ons[j]["method"] = "detector_scaled"
            elif r < 0 and offs:
                shares = off_total if sum(off_total) > 0 else [1.0] * len(offs)
                for j in range(len(offs)):
                    off_total[j] += -r * shares[j] / sum(shares)
                    offs[j]["method"] = "detector_scaled"
            else:
                leftover = r
            carried[k] = leftover
            # write per-ramp values in x order, exit fractions relative to the arriving flow
            q_cur = q_up[k]
            for d in bracket:
                if d["kind"] == "on":
                    j = ons.index(d)
                    d["on_veh_h"][k] = on_total[j]
                    q_cur += on_total[j]
                else:
                    j = offs.index(d)
                    frac = off_total[j] / q_cur if q_cur > 0 else 0.0
                    frac = min(max(frac, 0.0), 0.95)
                    d["exit_frac"][k] = frac
                    q_cur -= frac * q_cur
            for j, d in enumerate(ons):
                last_on[j] = d["on_veh_h"][k]
            for j, d in enumerate(offs):
                last_off[j] = d["exit_frac"][k]
        span = [v for v in carried if v != 0.0]
        if span:
            residual_log.append(
                {
                    "from": up.id,
                    "to": down.id,
                    "mean_residual_veh_h": round(sum(carried) / n, 1),
                    "note": "unexplained change with no ramp of the needed kind (or sign) in this bracket; carried into the next bracket",
                }
            )
    ramp_records: list[dict[str, Any]] = []
    for d in descriptors:
        rec: dict[str, Any] = {k: d[k] for k in ("name", "kind", "x_m", "method") if k in d}
        if d.get("station"):
            rec["station"] = d["station"]
            rec["match_distance_m"] = d.get("match_distance_m")
        rec["n_steps"] = n
        rec["n_steps_carried"] = 0
        if d["kind"] == "on":
            rec["inflow_steps"] = [[k * step_s, v / 3600.0] for k, v in enumerate(d["on_veh_h"])]
        else:
            rec["exit_fraction_steps"] = [[k * step_s, v] for k, v in enumerate(d["exit_frac"])]
        ramp_records.append(rec)

    # 4. Write the scenario.
    raw["network"]["inflow"] = [[float(t), float(v)] for t, v in inflow_steps]
    for ramp, rec in zip(raw["network"]["ramps"], ramp_records, strict=True):
        if rec["kind"] == "on":
            ramp["inflow"] = [[float(t), float(v)] for t, v in rec["inflow_steps"]]
            ramp["exit_fraction"] = []
        else:
            ramp["exit_fraction"] = [[float(t), float(v)] for t, v in rec["exit_fraction_steps"]]
            ramp["inflow"] = []
    down = obs.speeds_ms[args.downstream]
    limit = (
        next((st.speed_limit_ms for st in obs.stations if st.id == args.downstream), None) or 24.6
    )
    raw["network"]["boundary"] = {
        "kind": "speed_schedule",
        "steps": _steps_from_series(list(down), step_s, float(limit)),
        "exit_buffer_m": max(50.0, round(length_m - sx[args.downstream]["x_m"], 1)),
    }
    if args.idm_calibration:
        raw["fleet"]["idm_calibration"] = args.idm_calibration
    raw["sim"]["warmup_s"] = float(args.warmup_s)
    out = args.out or args.scenario
    out.write_text(yaml.safe_dump(raw, sort_keys=False))
    cfg = ScenarioConfig.from_yaml(out)
    obs.to_json(args.observations)

    # 5. Demand artifact.
    artifact = DemandArtifact(
        corridor=str(raw["name"]),
        observations=str(args.observations),
        upstream_station=args.upstream,
        inflow_steps=[(float(t), float(v)) for t, v in inflow_steps],
        ramps=ramp_records,
        step_s=step_s,
        coverage={
            "n_steps": float(len(inflow_steps)),
            "n_ramps": float(len(ramp_records)),
            "n_ramps_detector": float(
                sum(1 for r in ramp_records if r.get("method") == "detector")
            ),
            "n_ramps_conservation": float(
                sum(1 for r in ramp_records if r.get("method") == "conservation")
            ),
            "n_ramps_zeroed": float(len(zeroed)),
            "n_brackets_with_residual": float(len(residual_log)),
        },
    )
    artifact.to_json(args.demand_out)
    payload = json.loads(args.demand_out.read_text())
    payload["scenario"] = str(out)
    payload["config_hash"] = config_hash(cfg)
    payload["downstream_station"] = args.downstream
    payload["boundary"] = (
        "speed_schedule from the downstream station's observed mean speed; NaN windows carry"
    )
    payload["idm_calibration"] = args.idm_calibration
    payload["zeroed_ramps"] = zeroed
    payload["unmatched_ramp_detectors"] = unmatched_detectors
    payload["bracket_residuals"] = residual_log
    payload["method"] = (
        "entry inflow = upstream station count; ramp flows close the mainline balance bracket by bracket "
        "(live ramp detectors fix shares; the closing kind absorbs the remainder; residuals carried)"
    )
    args.demand_out.write_text(json.dumps(payload, indent=1))

    print(f"scenario {out}  config_hash {config_hash(cfg)}  chain {length_m:.0f} m")
    print(
        f"  inflow from {args.upstream}: {len(inflow_steps)} steps, peak {max(v for _, v in inflow_steps) * 3600:.0f} veh/h"
    )
    for r in ramp_records:
        key = "inflow_steps" if r["kind"] == "on" else "exit_fraction_steps"
        peak = max(v for _, v in r[key])
        unit = "veh/h" if r["kind"] == "on" else "frac"
        if r["kind"] == "on":
            peak *= 3600
        print(
            f"  {r['kind']:3} x={r['x_m']:7.0f} m  {r.get('method'):28} peak {peak:8.2f} {unit}  {r['name']}"
            + (f"  ← {r['station']} ({r.get('match_distance_m')} m)" if r.get("station") else "")
        )
    if zeroed:
        print("  zeroed (outside observed span):")
        for z in zeroed:
            print("   ", z)
    if unmatched_detectors:
        print(
            "  observed ramp detectors not matched to any discovered ramp:",
            ", ".join(unmatched_detectors),
        )
    for r in residual_log:
        print(
            f"  residual carried {r['from']}→{r['to']}: mean {r['mean_residual_veh_h']:+.0f} veh/h"
        )
    print(
        f"  boundary: {len(raw['network']['boundary']['steps'])} speed steps from {args.downstream}, exit buffer {raw['network']['boundary']['exit_buffer_m']} m"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
