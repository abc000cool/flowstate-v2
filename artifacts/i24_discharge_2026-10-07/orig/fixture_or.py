"""On-ramp merge bottleneck fixture ("OR"), built on fixture.py (scratch, phase 3 p31).

Geometry: mainline 2 lanes x 2,800 m -> merge node n1 (traffic light, all green except a 90-s red in
BLOCK mode) -> 250 m section with 3 lanes whose rightmost lane is the acceleration lane fed only by a
1-lane on-ramp (500 m) and ending at n2 -> 2 lanes x 2,950 m. Ramp vehicles must merge inside the
250 m acceleration lane: a merge bottleneck with a controlled merge share (``ramp_share`` of demand,
default 0.25; I-24 Old Hickory ~0.3, I-94 T.H.52 ~0.21 of the downstream flow).

Ramp vehicles carry lcStrategic = fleet.lc_strategic_ramp (1.0), mainline vehicles 5.0, as the corridor
fleets write them (microsim.vehicles.write_corridor_routes).
Detectors: D_up on the mainline 500 m upstream of the merge node, D_ramp on the ramp, D_down 500 m
downstream of the acceleration-lane end, D_far 1,500 m downstream.
"""
from __future__ import annotations

import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

import fixture as F
from flowstate_core.rng import make_rng, sumo_seed
from microsim.vehicles import _draw_from_calibration, _vtype_xml, corridor_departures

X_MERGE = 2800.0
ACC_LEN = 250.0
RAMP_LEN = 500.0


def build_net_or(limit: float, wd: Path) -> dict:
    x1, x2, x3 = X_MERGE, X_MERGE + ACC_LEN, 6000.0
    nod = [
        '<node id="n0" x="0" y="0" type="priority"/>',
        f'<node id="n1" x="{x1}" y="0" type="traffic_light"/>',
        f'<node id="n2" x="{x2}" y="0" type="priority"/>',
        f'<node id="n3" x="{x3}" y="0" type="priority"/>',
        f'<node id="nr" x="{x1 - RAMP_LEN * 0.996:.2f}" y="-45" type="priority"/>',
    ]
    edg = [
        f'<edge id="e0" from="n0" to="n1" numLanes="2" speed="{limit:.4f}" priority="10"/>',
        f'<edge id="er" from="nr" to="n1" numLanes="1" speed="{limit:.4f}" priority="5"/>',
        f'<edge id="e1" from="n1" to="n2" numLanes="3" speed="{limit:.4f}" priority="10"/>',
        f'<edge id="e2" from="n2" to="n3" numLanes="2" speed="{limit:.4f}" priority="10"/>',
    ]
    con = [
        '<connection from="er" to="e1" fromLane="0" toLane="0"/>',
        '<connection from="e0" to="e1" fromLane="0" toLane="1"/>',
        '<connection from="e0" to="e1" fromLane="1" toLane="2"/>',
        '<connection from="e1" to="e2" fromLane="1" toLane="0"/>',
        '<connection from="e1" to="e2" fromLane="2" toLane="1"/>',
    ]
    (wd / "n.nod.xml").write_text("<nodes>\n" + "\n".join(nod) + "\n</nodes>\n")
    (wd / "n.edg.xml").write_text("<edges>\n" + "\n".join(edg) + "\n</edges>\n")
    (wd / "n.con.xml").write_text("<connections>\n" + "\n".join(con) + "\n</connections>\n")
    subprocess.run(
        [F.NETCONVERT, "-n", "n.nod.xml", "-e", "n.edg.xml", "-x", "n.con.xml", "-o", "net.net.xml",
         "--no-turnarounds", "true", "--no-warnings", "true", "--offset.disable-normalization", "true"],
        cwd=wd, check=True, capture_output=True,
    )
    root = ET.parse(wd / "net.net.xml").getroot()
    lane_len = {ln.get("id"): float(ln.get("length")) for e in root.findall("edge")
                if e.get("function") != "internal" for ln in e.findall("lane")}
    dets = {  # name: (edge, pos, lanes)
        "D_up": ("e0", X_MERGE - 500.0, 2),
        "D_ramp": ("er", lane_len["er_0"] - 50.0, 1),
        "D_down": ("e2", 500.0, 2),
        "D_far": ("e2", 1500.0, 2),
    }
    add = ["<additional>"]
    for name, (e, pos, nl) in dets.items():
        for ln in range(nl):
            add.append(f'<inductionLoop id="{name}_{ln}" lane="{e}_{ln}" pos="{min(pos, lane_len[f"{e}_{ln}"] - 0.1):.2f}" '
                       f'period="{F.PERIOD}" file="det.xml"/>')
    add.append("</additional>")
    (wd / "det.add.xml").write_text("\n".join(add))
    return {"edges": ["e0", "e1", "e2"], "lanes": [2, 3, 2], "xs": [0.0, X_MERGE, X_MERGE + ACC_LEN],
            "dets": {"D_down": X_MERGE + ACC_LEN + 500.0}, "tl": "n1"}


def write_routes_or(wd: Path, cal, model: str, seed: int, inflow, dur: float, eidm, ramp_share: float) -> int:
    rng = make_rng(seed)
    times = corridor_departures(inflow, dur, rng)
    params = _draw_from_calibration(cal, len(times), rng)
    is_ramp = rng.random(len(times)) < ramp_share
    extra = {k: f"{v:g}" for k, v in (eidm or {}).items()} if model == "EIDM" else {}
    lines = ["<routes>"]
    for i, p in enumerate(params):
        strat = 1.0 if is_ramp[i] else F.LC["lc_strategic"]
        lines.append(_vtype_xml(f"t{i:05d}", p, model, F.STEP, strat, F.LC["lc_keep_right"], F.LC["lc_cooperative"],
                                F.LC["lc_assertive"], F.LC["lc_speed_gain"], extra_attrs=extra or None))
    lines.append('  <route id="main" edges="e0 e1 e2"/>')
    lines.append('  <route id="ramp" edges="er e1 e2"/>')
    k_main = 0
    for i, t in enumerate(times):
        if is_ramp[i]:
            da = 'departPos="base" departSpeed="avg" departLane="0" route="ramp"'
        else:
            da = f'departPos="base" departSpeed="avg" departLane="{k_main % 2}" route="main"'
            k_main += 1
        lines.append(f'  <vehicle id="v{i:05d}" type="t{i:05d}" depart="{t:.3f}" {da}/>')
    lines.append("</routes>")
    (wd / "r.rou.xml").write_text("\n".join(lines))
    return len(times)


def run_or(fleet: str, seed: int, mode: str = "BLOCK", *, model=None, mean_over=None, eidm=None,
           dur: float = 1200.0, limit_ms=None, cap: float = 3600.0, ramp_share: float = 0.25, tag: str = "") -> dict:
    import libsumo as ts
    fl = F.FLEETS[fleet]
    model = model or fl["model"]
    limit = limit_ms if limit_ms is not None else fl["limit_ms"]
    cal = F.population(fl["pop"], mean_over)
    red = (300.0, 390.0)
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(dir=str(F.TMPDIR)) as td:
        wd = Path(td)
        net = build_net_or(limit, wd)
        if mode == "RAMP":
            inflow = F.demand_profile("OR", 0.8 * cap, 1.6 * cap, 60.0, 600.0, dur)
        else:
            inflow = [(0.0, 0.9 * cap / 3600.0), (120.0, 1.35 * cap / 3600.0)]
        n_planned = write_routes_or(wd, cal, model, seed, inflow, dur, eidm, ramp_share)
        ts.start(["sumo", "-n", str(wd / "net.net.xml"), "-r", str(wd / "r.rou.xml"), "-a", str(wd / "det.add.xml"),
                  "--step-length", str(F.STEP), "--seed", str(sumo_seed(seed)), "--time-to-teleport", "-1",
                  "--no-warnings", "--collision.action", "warn", "--no-step-log", "--begin", "0", "--end", str(dur)])
        n = len(ts.trafficlight.getRedYellowGreenState("n1"))
        ts.trafficlight.setRedYellowGreenState("n1", "G" * n)
        n_coll = n_dep = 0
        census = []
        x_dd = X_MERGE + ACC_LEN + 500.0
        offs = {"e0": 0.0, "er": X_MERGE - RAMP_LEN, "e1": X_MERGE, "e2": X_MERGE + ACC_LEN}
        for k in range(int(round(dur / F.STEP))):
            tn = k * F.STEP
            if mode == "BLOCK":
                if abs(tn - red[0]) < 1e-6:
                    ts.trafficlight.setRedYellowGreenState("n1", "r" * n)
                elif abs(tn - red[1]) < 1e-6:
                    ts.trafficlight.setRedYellowGreenState("n1", "G" * n)
            ts.simulationStep()
            if k % 120 == 119:
                ns = 0
                for vid in ts.vehicle.getIDList():
                    e = ts.vehicle.getRoadID(vid)
                    if e in offs and offs[e] + ts.vehicle.getLanePosition(vid) < x_dd and ts.vehicle.getSpeed(vid) < F.SLOW_MS:
                        ns += 1
                census.append(ns)
            n_coll += ts.simulation.getCollidingVehiclesNumber()
            n_dep += ts.simulation.getDepartedNumber()
        ts.close()
        det = F.parse_det(wd / "det.xml", net)
    out = {"tag": tag, "kind": "OR", "mode": mode, "fleet": fleet, "model": model, "seed": seed, "limit_ms": limit,
           "mean_over": mean_over or {}, "eidm": eidm or {}, "ramp_share": ramp_share, "cap_guess": cap,
           "n_planned": n_planned, "n_departed": n_dep, "n_collisions": n_coll, "slow_census": census,
           "pop_mean": dict(cal.mean), "wall_s": round(time.perf_counter() - t0, 1), "det": det}
    out["meas"] = measure_or(out, red)
    return out


def measure_or(r: dict, red) -> dict:
    det = r["det"]
    up, dn, far, rp = det["D_up"], det["D_down"], det["D_far"], det["D_ramp"]
    t = np.array(dn["t"])
    qd = np.array(dn["count"], float) * 60.0
    vu = np.array(up["speed"]); vd = np.array(dn["speed"])
    res: dict = {"lanes_down": 2}
    if r["mode"] == "RAMP":
        cond = (vu < 15.0) & (vu < vd - 3.0)
        onset = None
        for j in range(2, len(t) - 2):
            if cond[j] and cond[j:j + 3].sum() >= 2:
                onset = j
                break
        res["onset_s"] = float(t[onset]) if onset is not None else None
        res["broke_down"] = onset is not None
        t_hi = float(t[onset]) if onset is not None else 1200.0
        for w in (1, 3, 5):
            v, at = F._roll_max(qd, t, w, 120.0, t_hi)
            res[f"pre{w}_veh_h"] = v
            res[f"pre{w}_at_s"] = at
        if res["pre5_veh_h"]:
            res["pre5_per_lane"] = res["pre5_veh_h"] / 2
        if onset is not None:
            m = t >= t[onset] + 180.0
            if m.sum() >= 3:
                res["post_discharge_veh_h"] = float(qd[m].mean())
        return res
    sc = np.array(r["slow_census"], float)
    sc = np.concatenate([sc, np.zeros(max(0, len(t) - len(sc)))])[: len(t)]
    m = (t >= red[1] + 180.0) & (sc > 0)
    res["n_discharge_min"] = int(m.sum())
    res["queue_to_end"] = bool(sc[-1] > 0)
    if not m.any():
        return res
    res["discharge_veh_h"] = float(qd[m].mean())
    res["discharge_per_lane"] = res["discharge_veh_h"] / 2
    res["ramp_flow_veh_h"] = float(np.array(rp["count"], float)[m].mean() * 60.0)
    res["ramp_share_dis"] = res["ramp_flow_veh_h"] / res["discharge_veh_h"]
    res["queue_speed_up_ms"] = float(np.nanmean(vu[m]))
    res["ramp_speed_ms"] = float(np.nanmean(np.array(rp["speed"])[m]))
    res["speed_down_ms"] = float(np.nanmean(vd[m]))
    return res


if __name__ == "__main__":
    import json
    import sys
    fleet = sys.argv[1] if len(sys.argv) > 1 else "i24"
    mode = sys.argv[2] if len(sys.argv) > 2 else "BLOCK"
    for s in (1, 2):
        r = run_or(fleet, s, mode)
        print("seed", s, r["n_planned"], r["n_departed"], r["n_collisions"], r["wall_s"],
              {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r["meas"].items()})
        for name, d in r["det"].items():
            print("  ", name, "q", [int(c * 60) for c in d["count"]])
            print("  ", name, "v", [round(x, 1) for x in d["speed"]])
