"""Straight-road bottleneck fixtures for the queue-discharge question (scratch, phase 3 p31).

Fixtures (all straight, built with netconvert from plain XML, step 0.5 s, <= 20 min):

* ``LD``  - 3 lanes x 3 km, the rightmost lane ends at x = 3,000 m, then 2 lanes x 3 km.
            A merge (lane-drop) bottleneck: SUMO LC2013 lane-end merging + car following.
* ``SR1`` - single lane, 6 km, a 500 m zone at x = 3,000-3,500 m with a reduced speed limit
            (``zone_kmh``); pure car following, no lane changes.
* ``SAT`` - 3 lanes x 4 km, no bottleneck, saturating insertion (the repo's straight-road
            capacity probe, scripts/calibrate_capacity.py, at 20 min instead of 30).

Fleet: per-vehicle draws from an ``IDMCalibration`` population (microsim.vehicles.
_draw_from_calibration, the runner's draw), vTypes written by microsim.vehicles._vtype_xml
with the corridor fleet's model, actionStepLength 0.5 and lane-change values
(lc_strategic 5, lc_keep_right 0, others 1), speedFactor 1, plus optional EIDM attributes.
Insertion as the runner's multi-lane boundary path: departPos="base" departSpeed="avg",
lane round-robin (single lane: departPos="free" departSpeed="max", as the runner).

Measurement: induction loops (one per lane) at D_up (500 m upstream of the bottleneck),
D_down (500 m downstream of the bottleneck end) and D_far (1,500 m downstream), 60-s periods.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

REPO = Path("/Users/anshpathak/Desktop/apps/flowstate")
os.chdir(REPO)
# 2026-10-07 (i24_discharge): import the HEAD af138fd snapshot ahead of the editable install
import sys as _sys  # noqa: E402
_HEAD = Path(os.environ.get("I24DIS_HEAD", "/private/tmp/claude-501/-Users-anshpathak-Desktop-apps-flowstate/b1e30512-d596-4228-ba4a-2c84632c93b4/scratchpad/i24dis/head"))
for _p in ("validation", "microsim", "flowstate_core"):
    _sys.path.insert(0, str(_HEAD / "packages" / _p))
TMPDIR = _HEAD.parent / "tmp"

from flowstate_core.artifacts import IDMCalibration  # noqa: E402
from flowstate_core.rng import make_rng, sumo_seed  # noqa: E402
from microsim.vehicles import _draw_from_calibration, _vtype_xml, corridor_departures  # noqa: E402

import sumo as _sumo  # noqa: E402

NETCONVERT = os.path.join(_sumo.SUMO_HOME, "bin", "netconvert")
MPH = 0.44704
BREAK = 40 * MPH
DIFF = 20 * MPH
STEP = 0.5
PERIOD = 60.0

FLEETS = {
    # scenarios/i24_replica_flow_speedcal.yaml fleet block; I-24 posted 70 mph (OSM maxspeed)
    "i24": {"model": "IDM", "limit_ms": 70 * MPH, "pop": "artifacts/idm_i24_capacity.json"},
    # scenarios/mndot_i94_wb_stpaul_weave.yaml fleet block; posted 55 mph (stations.csv)
    "i94": {"model": "EIDM", "limit_ms": 24.5872, "pop": "artifacts/idm_i24_capacity.json"},
    # 2026-10-07 (i24_discharge): the calibrated I-24 fleet (scenarios/i24_replica_flow_speedcal_dc.yaml) and k = 0.5
    "i24dc": {"model": "IDM", "limit_ms": 70 * MPH, "pop": "artifacts/idm_i24_capacity_amax_k1.0.json"},
    "i24k05": {"model": "IDM", "limit_ms": 70 * MPH, "pop": "artifacts/idm_i24_capacity_amax_k0.5.json"},
}
LC = dict(lc_strategic=5.0, lc_keep_right=0.0, lc_cooperative=1.0, lc_assertive=1.0, lc_speed_gain=1.0)


def population(path: str, mean_over: dict | None = None) -> IDMCalibration:
    cal = IDMCalibration.load(REPO / path)
    if mean_over:
        m = dict(cal.mean)
        m.update(mean_over)
        cal = cal.model_copy(update={"mean": m})
    return cal


# ---------------------------------------------------------------- network
def build_net(kind: str, limit: float, workdir: Path, zone_kmh: float = 60.0) -> dict:
    nod, edg, con = [], [], []
    if kind == "LD":
        xs = [0, 3000, 6000]
        lanes = [3, 2]
        speeds = [limit, limit]
        x_bn_start, x_bn_end = 3000.0, 3000.0
    elif kind == "SR1":
        xs = [0, 3000, 3500, 6000]
        lanes = [1, 1, 1]
        speeds = [limit, zone_kmh / 3.6, limit]
        x_bn_start, x_bn_end = 3000.0, 3500.0
    elif kind == "SR3":
        xs = [0, 3000, 3500, 6000]
        lanes = [3, 3, 3]
        speeds = [limit, zone_kmh / 3.6, limit]
        x_bn_start, x_bn_end = 3000.0, 3500.0
    elif kind == "J1":
        xs = [0, 3000, 6000]
        lanes = [1, 1]
        speeds = [limit, limit]
        x_bn_start, x_bn_end = 3000.0, 3000.0
    elif kind == "SAT":
        xs = [0, 4000]
        lanes = [3]
        speeds = [limit]
        x_bn_start, x_bn_end = None, None
    else:
        raise ValueError(kind)
    tl_node = 1 if kind in ("LD", "SR1", "SR3", "J1") else None
    for i, x in enumerate(xs):
        typ = "traffic_light" if i == tl_node else "priority"
        nod.append(f'<node id="n{i}" x="{x}" y="0" type="{typ}"/>')
    for i in range(len(xs) - 1):
        edg.append(
            f'<edge id="e{i}" from="n{i}" to="n{i+1}" numLanes="{lanes[i]}" speed="{speeds[i]:.4f}" priority="10"/>'
        )
    if kind == "LD":
        con.append('<connection from="e0" to="e1" fromLane="1" toLane="0"/>')
        con.append('<connection from="e0" to="e1" fromLane="2" toLane="1"/>')
    (workdir / "n.nod.xml").write_text("<nodes>\n" + "\n".join(nod) + "\n</nodes>\n")
    (workdir / "n.edg.xml").write_text("<edges>\n" + "\n".join(edg) + "\n</edges>\n")
    (workdir / "n.con.xml").write_text("<connections>\n" + "\n".join(con) + "\n</connections>\n")
    subprocess.run(
        [NETCONVERT, "-n", "n.nod.xml", "-e", "n.edg.xml", "-x", "n.con.xml", "-o", "net.net.xml",
         "--no-turnarounds", "true", "--no-warnings", "true", "--offset.disable-normalization", "true"],
        cwd=workdir, check=True, capture_output=True,
    )
    edges = [f"e{i}" for i in range(len(xs) - 1)]
    # detector positions (x along the road)
    if kind == "SAT":
        dets = {"D_ref": 3000.0}
    else:
        dets = {"D_up": x_bn_start - 500.0, "D_down": x_bn_end + 500.0, "D_far": x_bn_end + 1500.0}
    # add induction loops
    net = ET.parse(workdir / "net.net.xml").getroot()
    lane_len = {}
    for e in net.findall("edge"):
        if e.get("function") == "internal":
            continue
        for ln in e.findall("lane"):
            lane_len[ln.get("id")] = float(ln.get("length"))
    add = ["<additional>"]
    for name, x in dets.items():
        # which edge
        k = max(i for i in range(len(xs) - 1) if xs[i] <= x)
        pos = x - xs[k]
        for ln in range(lanes[k]):
            lid = f"e{k}_{ln}"
            p = min(pos, lane_len[lid] - 0.1)
            add.append(
                f'<inductionLoop id="{name}_{ln}" lane="{lid}" pos="{p:.2f}" period="{PERIOD}" file="det.xml"/>'
            )
    add.append("</additional>")
    (workdir / "det.add.xml").write_text("\n".join(add))
    return {"edges": edges, "lanes": lanes, "xs": xs, "dets": dets, "x_bn": (x_bn_start, x_bn_end),
            "tl": "n1" if tl_node else None}


# ---------------------------------------------------------------- demand
def demand_profile(kind: str, q0: float, q1: float, t_hold: float, t_ramp_end: float, dur: float):
    """Piecewise-constant 30-s steps: q0 until t_hold, linear to q1 at t_ramp_end, then q1 [veh/h]."""
    steps = []
    t = 0.0
    while t < dur:
        tm = t + 15.0
        if tm <= t_hold:
            q = q0
        elif tm >= t_ramp_end:
            q = q1
        else:
            q = q0 + (q1 - q0) * (tm - t_hold) / (t_ramp_end - t_hold)
        steps.append((t, q / 3600.0))
        t += 30.0
    return steps


def write_routes(workdir: Path, net: dict, cal: IDMCalibration, model: str, seed: int,
                 inflow, dur: float, eidm: dict | None) -> int:
    rng = make_rng(seed)
    times = corridor_departures(inflow, dur, rng)
    params = _draw_from_calibration(cal, len(times), rng)
    extra = {k: f"{v:g}" for k, v in (eidm or {}).items()} if model == "EIDM" else {}
    lanes0 = net["lanes"][0]
    lines = ["<routes>"]
    for i, p in enumerate(params):
        lines.append(_vtype_xml(f"t{i:05d}", p, model, STEP, LC["lc_strategic"], LC["lc_keep_right"],
                                LC["lc_cooperative"], LC["lc_assertive"], LC["lc_speed_gain"],
                                extra_attrs=extra or None))
    lines.append(f'  <route id="main" edges="{" ".join(net["edges"])}"/>')
    for i, t in enumerate(times):
        da = f'departPos="base" departSpeed="avg" departLane="{i % lanes0}"'
        lines.append(f'  <vehicle id="v{i:05d}" type="t{i:05d}" depart="{t:.3f}" {da} route="main"/>')
    lines.append("</routes>")
    (workdir / "r.rou.xml").write_text("\n".join(lines))
    return len(times)


# ---------------------------------------------------------------- run
# nominal bottleneck capacities [veh/h total] used only to set demand levels
CAP_GUESS = {"LD": 3600.0, "SR1": 1700.0, "SR3": 5000.0, "J1": 1800.0, "SAT": 7200.0}


def mode_demand(kind: str, mode: str, dur: float, cap: float | None = None) -> list:
    c = cap or CAP_GUESS[kind]
    if mode == "RAMP":   # 0.8 C at 0-60 s, linear to 1.6 C at 600 s, then held
        return demand_profile(kind, 0.8 * c, 1.6 * c, 60.0, 600.0, dur)
    if mode == "BLOCK":  # 0.9 C fill for 120 s, then 1.35 C; bottleneck red 300-390 s
        return [(0.0, 0.9 * c / 3600.0), (120.0, 1.35 * c / 3600.0)]
    if mode == "SAT":
        return [(0.0, 7200.0 / 3600.0)]
    raise ValueError(mode)


BLOCK_REDS = {"LD": (300.0, 390.0), "J1": (240.0, 480.0), "SR1": (300.0, 390.0), "SR3": (300.0, 390.0)}
SLOW_MS = 8.0


def run(kind: str, fleet: str, seed: int, mode: str = "BLOCK", *, model: str | None = None,
        mean_over: dict | None = None, eidm: dict | None = None, dur: float = 1200.0,
        zone_kmh: float = 60.0, limit_ms: float | None = None, cap: float | None = None,
        tag: str = "") -> dict:
    import libsumo as ts

    fl = FLEETS[fleet]
    model = model or fl["model"]
    limit = limit_ms if limit_ms is not None else fl["limit_ms"]
    cal = population(fl["pop"], mean_over)
    if kind == "SAT":
        mode = "SAT"
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(dir=str(TMPDIR)) as td:
        wd = Path(td)
        net = build_net(kind, limit, wd, zone_kmh)
        inflow = mode_demand(kind, mode, dur, cap)
        n_planned = write_routes(wd, net, cal, model, seed, inflow, dur, eidm)
        cmd = ["sumo", "-n", str(wd / "net.net.xml"), "-r", str(wd / "r.rou.xml"), "-a", str(wd / "det.add.xml"),
               "--step-length", str(STEP), "--seed", str(sumo_seed(seed)), "--time-to-teleport", "-1",
               "--no-warnings", "--collision.action", "warn", "--no-step-log", "--begin", "0",
               "--end", str(dur)]
        ts.start(cmd)
        tl = net["tl"]
        nlinks = 0
        if tl:
            nlinks = len(ts.trafficlight.getRedYellowGreenState(tl))
            ts.trafficlight.setRedYellowGreenState(tl, "G" * nlinks)
        n_coll = 0
        n_departed = 0
        slow_census = []
        x_dd = net["dets"].get("D_down", 1e9)
        offs = {e: float(x) for e, x in zip(net["edges"], net["xs"])}
        n_steps = int(round(dur / STEP))
        for k in range(n_steps):
            tnow = k * STEP
            if tl and mode == "BLOCK":
                red = BLOCK_REDS[kind]
                if abs(tnow - red[0]) < 1e-6:
                    ts.trafficlight.setRedYellowGreenState(tl, "r" * nlinks)
                elif abs(tnow - red[1]) < 1e-6:
                    ts.trafficlight.setRedYellowGreenState(tl, "G" * nlinks)
            ts.simulationStep()
            if k % 120 == 119:
                ns = 0; xmin = None
                for vid in ts.vehicle.getIDList():
                    e = ts.vehicle.getRoadID(vid)
                    if e not in offs:
                        continue
                    x = offs[e] + ts.vehicle.getLanePosition(vid)
                    if x < x_dd and ts.vehicle.getSpeed(vid) < SLOW_MS:
                        ns += 1
                slow_census.append(ns)
            n_coll += ts.simulation.getCollidingVehiclesNumber()
            n_departed += ts.simulation.getDepartedNumber()
        ts.close()
        det = parse_det(wd / "det.xml", net)
    out = {
        "tag": tag, "kind": kind, "mode": mode, "fleet": fleet, "model": model, "seed": seed, "limit_ms": limit,
        "mean_over": mean_over or {}, "eidm": eidm or {}, "zone_kmh": zone_kmh if kind.startswith("SR") else None,
        "cap_guess": cap or CAP_GUESS[kind], "n_planned": n_planned, "n_departed": n_departed,
        "n_collisions": n_coll, "slow_census": slow_census, "pop_mean": dict(cal.mean), "wall_s": round(time.perf_counter() - t0, 1),
        "det": det,
    }
    out["meas"] = measure(out, net)
    return out


def parse_det(path: Path, net: dict) -> dict:
    root = ET.parse(path).getroot()
    rows: dict[str, dict[float, list]] = {}
    for it in root.findall("interval"):
        did = it.get("id")
        name, ln = did.rsplit("_", 1)
        b = float(it.get("begin"))
        n = int(float(it.get("nVehContrib")))
        sp = float(it.get("speed"))  # -1 when no vehicle
        rows.setdefault(name, {}).setdefault(b, []).append((int(ln), n, sp))
    out = {}
    for name, d in rows.items():
        ts_ = sorted(d)
        cnt, spd, lanes_n = [], [], []
        for t in ts_:
            vals = sorted(d[t])
            cnt.append(sum(n for _, n, _ in vals))
            num = sum(n * s for _, n, s in vals if n > 0 and s >= 0)
            den = sum(n for _, n, s in vals if n > 0 and s >= 0)
            spd.append(num / den if den else float("nan"))
            lanes_n.append([n for _, n, _ in vals])
        out[name] = {"t": ts_, "count": cnt, "speed": spd, "lane_counts": lanes_n}
    return out


def _roll_max(q: np.ndarray, t: np.ndarray, w: int, t_lo: float, t_hi: float):
    best, at = None, None
    for j in range(0, len(q) - w + 1):
        if t[j] < t_lo or t[j + w - 1] + PERIOD > t_hi + 1e-9:
            continue
        v = float(q[j:j + w].mean())
        if best is None or v > best:
            best, at = v, float(t[j])
    return best, at


def measure(r: dict, net: dict) -> dict:
    det = r["det"]
    if r["mode"] == "SAT":
        d = det["D_ref"]
        t = np.array(d["t"]); c = np.array(d["count"], float)
        m = t >= 300.0
        lanes = net["lanes"][0]
        return {"throughput_veh_h_lane": float(c[m].sum() / (m.sum() * PERIOD) * 3600 / lanes),
                "speed_ms": float(np.nanmean(np.array(d["speed"])[m])),
                "inserted_fraction": r["n_departed"] / r["n_planned"]}
    up, dn, far = det["D_up"], det["D_down"], det["D_far"]
    t = np.array(dn["t"])
    qd = np.array(dn["count"], float) * 3600.0 / PERIOD
    vu = np.array(up["speed"]); vd = np.array(dn["speed"])
    lanes_dn = 3 if r["kind"] == "SR3" else net["lanes"][-1]
    res: dict = {"lanes_down": lanes_dn}
    if r["mode"] == "RAMP":
        # breakdown onset: D_up 1-min speed < 15 m/s and >= 3 m/s below D_down, in 2 of 3 minutes
        cond = (vu < 15.0) & (vu < vd - 3.0)
        onset = None
        for j in range(2, len(t) - 2):
            if cond[j] and cond[j:j + 3].sum() >= 2:
                onset = j
                break
        res["onset_s"] = float(t[onset]) if onset is not None else None
        t_hi = float(t[onset]) if onset is not None else 1200.0
        for w in (1, 3, 5):
            v, at = _roll_max(qd, t, w, 120.0, t_hi)
            res[f"pre{w}_veh_h"] = v
            res[f"pre{w}_at_s"] = at
        res["broke_down"] = onset is not None
        if res["pre5_veh_h"] is not None:
            res["pre5_per_lane"] = res["pre5_veh_h"] / lanes_dn
            j = int(np.searchsorted(t, res["pre5_at_s"]))
            res["speed_down_at_pre5"] = float(np.nanmean(vd[j:j + 5]))
        if onset is not None:
            m = t >= t[onset] + 180.0
            if m.sum() >= 3:
                res["post_discharge_veh_h"] = float(qd[m].mean())
                res["post_n_min"] = int(m.sum())
        return res
    # BLOCK: discharge after release, over minutes with a queue (a vehicle < SLOW_MS upstream of D_down)
    red = BLOCK_REDS[r["kind"]]
    dis_from = red[1] + (180.0 if r["kind"] != "J1" else 120.0)
    sc = np.array(r.get("slow_census") or [0] * len(t), float)
    sc = np.concatenate([sc, np.zeros(max(0, len(t) - len(sc)))])[: len(t)]
    m = (t >= dis_from) & (sc > 0)
    res["n_discharge_min"] = int(m.sum())
    res["queue_to_end"] = bool(sc[-1] > 0)
    if not m.any():
        return res
    res["discharge_veh_h"] = float(qd[m].mean())
    res["discharge_per_lane"] = res["discharge_veh_h"] / lanes_dn
    res["discharge_far_veh_h"] = float(np.array(far["count"], float)[m].mean() * 60.0)
    res["queue_speed_up_ms"] = float(np.nanmean(vu[m]))
    res["speed_down_ms"] = float(np.nanmean(vd[m]))
    res["speed_far_ms"] = float(np.nanmean(np.array(far["speed"])[m]))
    lc = np.array(up["lane_counts"], float)
    res["up_lane_share"] = (lc[m].sum(axis=0) / max(lc[m].sum(), 1)).round(3).tolist()
    return res


if __name__ == "__main__":
    import sys
    kind = sys.argv[1] if len(sys.argv) > 1 else "LD"
    fleet = sys.argv[2] if len(sys.argv) > 2 else "i24"
    mode = sys.argv[3] if len(sys.argv) > 3 else "BLOCK"
    r = run(kind, fleet, 1, mode)
    print(json.dumps({k: v for k, v in r.items() if k != "det"}, indent=1, default=str))
