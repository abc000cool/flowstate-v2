"""I-24 discharge diagnosis: fixture builder, runner and readers (scratch harness, 2026-10-07).

Not package code. Imports a snapshot of HEAD af138fd (``git archive af138fd packages/flowstate_core
packages/microsim packages/validation``) placed ahead of the editable install, so concurrent edits to
the working tree cannot reach these runs. Snapshot path: env ``I24DIS_HEAD`` (default: the session
scratch directory).

Fixtures are straight plain-XML networks built with netconvert (x = distance along the mainline):

* ``oh4``  - I-24 Old Hickory geometry: 4 through lanes; a 1-lane on-ramp joins as lane 0 of a 5-lane
             edge whose lane 0 (the acceleration lane) ends after ``acc_len`` (975 m, the replica's
             attach edge 977008894); 4 lanes downstream; optional 200-m exit buffer whose speed limit
             follows a schedule (the replica's measured downstream boundary, applied as there with
             ``edge.setMaxSpeed``).
* ``weave`` - the Hickory Hollow on-ramp / Bell Road off-ramp section: a 5-lane edge (565 m) whose
             lane 0 starts at an on-ramp and ends at an off-ramp (the replica's 992666043).
* ``straight`` - 4 lanes with an optional exit buffer (boundary throughput).

Fleet: per-vehicle draws from an IDMCalibration population with the corridor's own draw
(microsim.vehicles._draw_from_calibration) and vType writer (_vtype_xml), the lane-change values of a
scenario's ``fleet`` block (``fleet_from``), insertion as the runner's multi-lane boundary path.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

REPO = Path("/Users/anshpathak/Desktop/apps/flowstate")
HEAD = Path(
    os.environ.get(
        "I24DIS_HEAD",
        "/private/tmp/claude-501/-Users-anshpathak-Desktop-apps-flowstate/"
        "b1e30512-d596-4228-ba4a-2c84632c93b4/scratchpad/i24dis/head",
    )
)
for _p in ("validation", "microsim", "flowstate_core"):
    sys.path.insert(0, str(HEAD / "packages" / _p))
TMP = Path(os.environ.get("I24DIS_TMP", str(HEAD.parent / "tmp")))
TMP.mkdir(parents=True, exist_ok=True)

import yaml  # noqa: E402

from flowstate_core.artifacts import IDMCalibration  # noqa: E402
from flowstate_core.rng import make_rng, sumo_seed  # noqa: E402
from microsim.vehicles import _draw_from_calibration, _vtype_xml, corridor_departures  # noqa: E402

import sumo as _sumo  # noqa: E402

NETCONVERT = os.path.join(_sumo.SUMO_HOME, "bin", "netconvert")
STEP = 0.5
MPH = 0.44704
LIMIT_I24 = 70 * MPH  # OSM maxspeed of the corridor edges
RAMP_SPEED = 22.22  # netconvert's motorway_link default (the OH ramp way has no maxspeed tag)
VEH_LEN = 5.0
SLOW_MS = 8.0


# ------------------------------------------------------------------ fleet
def fleet_from(path: str | Path) -> dict:
    """The lane-change values, model and population of a scenario's fleet block."""
    raw = yaml.safe_load((REPO / path).read_text())
    fl = raw["fleet"]
    return {
        "model": fl["model"],
        "pop": fl["idm_calibration"],
        "lc_strategic": float(fl.get("lc_strategic", 1.0)),
        "lc_strategic_ramp": float(fl.get("lc_strategic_ramp") or fl.get("lc_strategic", 1.0)),
        "lc_keep_right": float(fl.get("lc_keep_right", 1.0)),
        "lc_cooperative": float(fl.get("lc_cooperative", 1.0)),
        "lc_assertive": float(fl.get("lc_assertive", 1.0)),
        "lc_speed_gain": float(fl.get("lc_speed_gain", 1.0)),
        "action_step": float(raw["sim"].get("action_step_s", STEP)),
        "speed_factor": float(fl.get("speed_factor") or 1.0),
        "source": str(path),
    }


def population(path: str, mean_over: dict | None = None) -> IDMCalibration:
    cal = IDMCalibration.load(REPO / path)
    if mean_over:
        m = dict(cal.mean)
        m.update(mean_over)
        cal = cal.model_copy(update={"mean": m})
    return cal


# ------------------------------------------------------------------ network
def _netconvert(wd: Path, nod: list[str], edg: list[str], con: list[str]) -> None:
    (wd / "n.nod.xml").write_text("<nodes>\n" + "\n".join(nod) + "\n</nodes>\n")
    (wd / "n.edg.xml").write_text("<edges>\n" + "\n".join(edg) + "\n</edges>\n")
    (wd / "n.con.xml").write_text("<connections>\n" + "\n".join(con) + "\n</connections>\n")
    subprocess.run(
        [NETCONVERT, "-n", "n.nod.xml", "-e", "n.edg.xml", "-x", "n.con.xml", "-o", "net.net.xml",
         "--no-turnarounds", "true", "--no-warnings", "true", "--offset.disable-normalization", "true"],
        cwd=wd, check=True, capture_output=True,
    )


def build(spec: dict, wd: Path) -> dict:
    """Build a fixture network; returns edge offsets, lane counts, routes and the aux-lane layout."""
    kind = spec["kind"]
    lim = spec.get("limit", LIMIT_I24)
    tl = spec.get("tl", False)
    nod, edg, con = [], [], []
    routes: dict[str, list[str]] = {}
    offs: dict[str, float] = {}
    lanes: dict[str, int] = {}
    aux: dict[str, str] = {}  # edge -> 'acc' | 'weave'
    if kind == "oh4":
        up, acc, dn = spec.get("up_len", 2800.0), spec.get("acc_len", 975.0), spec.get("dn_len", 2000.0)
        rl = spec.get("ramp_len", 500.0)
        n = spec.get("lanes", 4)
        xs = [0.0, up, up + acc, up + acc + dn]
        buf = spec.get("buffer_m")
        if buf:
            xs.append(xs[-1] + buf)
        for i, x in enumerate(xs):
            typ = "traffic_light" if (tl and i == 1) else "priority"
            nod.append(f'<node id="n{i}" x="{x:.2f}" y="0" type="{typ}"/>')
        nod.append(f'<node id="nr" x="{up - rl * 0.996:.2f}" y="-45" type="priority"/>')
        edg.append(f'<edge id="e0" from="n0" to="n1" numLanes="{n}" speed="{lim:.4f}" priority="10"/>')
        edg.append(f'<edge id="er" from="nr" to="n1" numLanes="1" speed="{spec.get("ramp_speed", RAMP_SPEED):.4f}" priority="5"/>')
        edg.append(f'<edge id="e1" from="n1" to="n2" numLanes="{n + 1}" speed="{lim:.4f}" priority="10"/>')
        edg.append(f'<edge id="e2" from="n2" to="n3" numLanes="{n}" speed="{lim:.4f}" priority="10"/>')
        con.append('<connection from="er" to="e1" fromLane="0" toLane="0"/>')
        for i in range(n):
            con.append(f'<connection from="e0" to="e1" fromLane="{i}" toLane="{i + 1}"/>')
            con.append(f'<connection from="e1" to="e2" fromLane="{i + 1}" toLane="{i}"/>')
        tail = ["e2"]
        if buf:
            edg.append(f'<edge id="eb" from="n3" to="n4" numLanes="{n}" speed="{lim:.4f}" priority="10"/>')
            for i in range(n):
                con.append(f'<connection from="e2" to="eb" fromLane="{i}" toLane="{i}"/>')
            tail.append("eb")
        offs = {"e0": 0.0, "er": up - rl, "e1": up, "e2": up + acc}
        if buf:
            offs["eb"] = xs[3]
        lanes = {"e0": n, "er": 1, "e1": n + 1, "e2": n}
        if buf:
            lanes["eb"] = n
        aux = {"e1": "acc"}
        routes = {"main": ["e0", "e1", *tail], "ramp": ["er", "e1", *tail]}
        geo = {"x_merge": up, "x_acc_end": up + acc, "x_end": xs[3], "x_buf": xs[3] if buf else None}
    elif kind == "weave":
        up, wl, dn = spec.get("up_len", 2500.0), spec.get("weave_len", 565.0), spec.get("dn_len", 2000.0)
        rl = spec.get("ramp_len", 400.0)
        n = spec.get("lanes", 4)
        xs = [0.0, up, up + wl, up + wl + dn]
        buf = spec.get("buffer_m")
        if buf:
            xs.append(xs[-1] + buf)
        for i, x in enumerate(xs):
            nod.append(f'<node id="n{i}" x="{x:.2f}" y="0" type="priority"/>')
        nod.append(f'<node id="nr" x="{up - rl * 0.996:.2f}" y="-45" type="priority"/>')
        nod.append(f'<node id="nx" x="{up + wl + rl * 0.996:.2f}" y="-45" type="priority"/>')
        edg.append(f'<edge id="e0" from="n0" to="n1" numLanes="{n}" speed="{lim:.4f}" priority="10"/>')
        edg.append(f'<edge id="er" from="nr" to="n1" numLanes="1" speed="{spec.get("ramp_speed", RAMP_SPEED):.4f}" priority="5"/>')
        edg.append(f'<edge id="e1" from="n1" to="n2" numLanes="{n + 1}" speed="{lim:.4f}" priority="10"/>')
        edg.append(f'<edge id="ex" from="n2" to="nx" numLanes="1" speed="{spec.get("ramp_speed", RAMP_SPEED):.4f}" priority="5"/>')
        edg.append(f'<edge id="e2" from="n2" to="n3" numLanes="{n}" speed="{lim:.4f}" priority="10"/>')
        con.append('<connection from="er" to="e1" fromLane="0" toLane="0"/>')
        con.append('<connection from="e1" to="ex" fromLane="0" toLane="0"/>')
        for i in range(n):
            con.append(f'<connection from="e0" to="e1" fromLane="{i}" toLane="{i + 1}"/>')
            con.append(f'<connection from="e1" to="e2" fromLane="{i + 1}" toLane="{i}"/>')
        tail = ["e2"]
        if buf:
            edg.append(f'<edge id="eb" from="n3" to="n4" numLanes="{n}" speed="{lim:.4f}" priority="10"/>')
            for i in range(n):
                con.append(f'<connection from="e2" to="eb" fromLane="{i}" toLane="{i}"/>')
            tail.append("eb")
        offs = {"e0": 0.0, "er": up - rl, "e1": up, "e2": up + wl}
        if buf:
            offs["eb"] = xs[3]
        lanes = {"e0": n, "er": 1, "e1": n + 1, "e2": n, "ex": 1}
        if buf:
            lanes["eb"] = n
        aux = {"e1": "weave"}
        routes = {"main": ["e0", "e1", *tail], "ramp": ["er", "e1", *tail], "exit": ["e0", "e1", "ex"],
                  "r2r": ["er", "e1", "ex"]}
        geo = {"x_merge": up, "x_acc_end": up + wl, "x_end": xs[3], "x_buf": xs[3] if buf else None}
    elif kind == "full":
        # the replica's corridor geometry from the mainline entry to the boundary, edge lengths and
        # lane counts from artifacts/i24_replica_inputs_flow.json on the trajectory chain (meta attach_x):
        # 4 lanes 3,070 m; Old Hickory acceleration lane 5 lanes 975 m; 4 lanes 1,496 m; Hickory Hollow
        # diverge 5 lanes 473 m; 4 lanes 590 m; Hickory Hollow on / Bell Road off 5 lanes 565 m; 4 lanes
        # 1,389 m; 200-m exit buffer. Sim x = this x; data x = (x - 2256.53) / 0.979853.
        L = [spec.get("l0", 3070.0), 975.0, 1496.0, 473.0, 590.0, 565.0, spec.get("lE", 1389.0)]
        buf = spec.get("buffer_m", 200.0)
        rs = spec.get("ramp_speed", RAMP_SPEED)
        names = ["e0", "e1", "e2", "eB", "eC", "eD", "eE"] + (["eb"] if buf else [])
        nl = [4, 5, 4, 5, 4, 5, 4] + ([4] if buf else [])
        xs = np.cumsum([0.0, *L] + ([buf] if buf else [])).tolist()
        for i, x in enumerate(xs):
            nod.append(f'<node id="n{i}" x="{x:.2f}" y="0" type="priority"/>')
        rl = spec.get("ramp_len", 500.0)
        nod.append(f'<node id="nr" x="{xs[1] - rl * 0.996:.2f}" y="-45" type="priority"/>')
        nod.append(f'<node id="nxh" x="{xs[4] + 300:.2f}" y="-45" type="priority"/>')
        nod.append(f'<node id="nrh" x="{xs[5] - 400 * 0.996:.2f}" y="-45" type="priority"/>')
        nod.append(f'<node id="nxb" x="{xs[6] + 300:.2f}" y="-45" type="priority"/>')
        for i, (nm, k_) in enumerate(zip(names, nl)):
            edg.append(f'<edge id="{nm}" from="n{i}" to="n{i + 1}" numLanes="{k_}" speed="{lim:.4f}" priority="10"/>')
        edg.append(f'<edge id="er" from="nr" to="n1" numLanes="1" speed="{rs:.4f}" priority="5"/>')
        edg.append(f'<edge id="xh" from="n4" to="nxh" numLanes="1" speed="{rs:.4f}" priority="5"/>')
        edg.append(f'<edge id="rh" from="nrh" to="n5" numLanes="1" speed="{rs:.4f}" priority="5"/>')
        edg.append(f'<edge id="xb" from="n6" to="nxb" numLanes="1" speed="{rs:.4f}" priority="5"/>')
        con.append('<connection from="er" to="e1" fromLane="0" toLane="0"/>')
        con.append('<connection from="e2" to="eB" fromLane="0" toLane="0"/>')
        con.append('<connection from="eB" to="xh" fromLane="0" toLane="0"/>')
        con.append('<connection from="rh" to="eD" fromLane="0" toLane="0"/>')
        con.append('<connection from="eD" to="xb" fromLane="0" toLane="0"/>')
        for i in range(4):
            con.append(f'<connection from="e0" to="e1" fromLane="{i}" toLane="{i + 1}"/>')
            con.append(f'<connection from="e1" to="e2" fromLane="{i + 1}" toLane="{i}"/>')
            con.append(f'<connection from="e2" to="eB" fromLane="{i}" toLane="{i + 1}"/>')
            con.append(f'<connection from="eB" to="eC" fromLane="{i + 1}" toLane="{i}"/>')
            con.append(f'<connection from="eC" to="eD" fromLane="{i}" toLane="{i + 1}"/>')
            con.append(f'<connection from="eD" to="eE" fromLane="{i + 1}" toLane="{i}"/>')
            if buf:
                con.append(f'<connection from="eE" to="eb" fromLane="{i}" toLane="{i}"/>')
        offs = {nm: xs[i] for i, nm in enumerate(names)}
        offs.update({"er": xs[1] - rl, "rh": xs[5] - 400.0})
        lanes = dict(zip(names, nl))
        lanes.update({"er": 1, "xh": 1, "rh": 1, "xb": 1})
        aux = {"e1": "acc", "eB": "div", "eD": "weave"}
        tail = names[6:]
        routes = {"main": names, "on0": ["er", *names[1:]], "main_off1": ["e0", "e1", "e2", "eB", "xh"],
                  "main_off3": ["e0", "e1", "e2", "eB", "eC", "eD", "xb"], "on0_off1": ["er", "e1", "e2", "eB", "xh"],
                  "on0_off3": ["er", "e1", "e2", "eB", "eC", "eD", "xb"], "on2": ["rh", "eD", *tail],
                  "on2_off3": ["rh", "eD", "xb"]}
        geo = {"x_merge": xs[1], "x_acc_end": xs[2], "x_div": xs[3], "x_div_end": xs[4], "x_weave": xs[5],
               "x_weave_end": xs[6], "x_end": xs[7], "x_buf": xs[7] if buf else None}
        ramp_edges = {"er", "xh", "rh", "xb"}
    elif kind == "ds":
        # the replica's downstream end, data x 1,000 -> boundary (fixture x = data x - 1,000):
        # 4 lanes to the Hickory Hollow diverge (data 3,352), 5 lanes 473 m (lane 0 -> off-ramp xh),
        # 4 lanes 590 m, 5 lanes 565 m (lane 0 from on-ramp rh, -> Bell Road off-ramp xb),
        # 4 lanes 1,461 m to the 200-m exit buffer (boundary schedule).
        La, Lb, Lc, Ld, Le = (spec.get(k, d) for k, d in (("a", 2352.0), ("b", 473.0), ("c", 590.0), ("d", 565.0), ("e", 1461.0)))
        buf = spec.get("buffer_m", 200.0)
        rs = spec.get("ramp_speed", RAMP_SPEED)
        n = 4
        xs = np.cumsum([0.0, La, Lb, Lc, Ld, Le] + ([buf] if buf else [])).tolist()
        for i, x in enumerate(xs):
            nod.append(f'<node id="n{i}" x="{x:.2f}" y="0" type="priority"/>')
        nod.append(f'<node id="nxh" x="{xs[2] + 300:.2f}" y="-45" type="priority"/>')
        nod.append(f'<node id="nrh" x="{xs[3] - 400 * 0.996:.2f}" y="-45" type="priority"/>')
        nod.append(f'<node id="nxb" x="{xs[4] + 300:.2f}" y="-45" type="priority"/>')
        names = ["e0", "eB", "eC", "eD", "eE"] + (["eb"] if buf else [])
        nl = [4, 5, 4, 5, 4] + ([4] if buf else [])
        for i, (nm, k_) in enumerate(zip(names, nl)):
            edg.append(f'<edge id="{nm}" from="n{i}" to="n{i + 1}" numLanes="{k_}" speed="{lim:.4f}" priority="10"/>')
        edg.append(f'<edge id="xh" from="n2" to="nxh" numLanes="1" speed="{rs:.4f}" priority="5"/>')
        edg.append(f'<edge id="rh" from="nrh" to="n3" numLanes="1" speed="{rs:.4f}" priority="5"/>')
        edg.append(f'<edge id="xb" from="n4" to="nxb" numLanes="1" speed="{rs:.4f}" priority="5"/>')
        con.append('<connection from="e0" to="eB" fromLane="0" toLane="0"/>')
        for i in range(n):
            con.append(f'<connection from="e0" to="eB" fromLane="{i}" toLane="{i + 1}"/>')
            con.append(f'<connection from="eB" to="eC" fromLane="{i + 1}" toLane="{i}"/>')
            con.append(f'<connection from="eC" to="eD" fromLane="{i}" toLane="{i + 1}"/>')
            con.append(f'<connection from="eD" to="eE" fromLane="{i + 1}" toLane="{i}"/>')
            if buf:
                con.append(f'<connection from="eE" to="eb" fromLane="{i}" toLane="{i}"/>')
        con.append('<connection from="eB" to="xh" fromLane="0" toLane="0"/>')
        con.append('<connection from="rh" to="eD" fromLane="0" toLane="0"/>')
        con.append('<connection from="eD" to="xb" fromLane="0" toLane="0"/>')
        offs = {nm: xs[i] for i, nm in enumerate(names)}
        offs.update({"rh": xs[3] - 400.0})
        lanes = dict(zip(names, nl))
        lanes.update({"xh": 1, "rh": 1, "xb": 1})
        aux = {"eB": "div", "eD": "weave"}
        tail = names[4:]
        routes = {"main": names, "hhoff": ["e0", "eB", "xh"], "bell": ["e0", "eB", "eC", "eD", "xb"],
                  "ramp": ["rh", "eD", *tail]}
        geo = {"x_merge": xs[3], "x_acc_end": xs[4], "x_end": xs[5], "x_buf": xs[5] if buf else None,
               "x_div": xs[1], "x_div_end": xs[2]}
        ramp_edges = {"xh", "rh", "xb"}
    elif kind == "straight":
        L = spec.get("len", 3000.0)
        n = spec.get("lanes", 4)
        buf = spec.get("buffer_m")
        xs = [0.0, L] + ([L + buf] if buf else [])
        for i, x in enumerate(xs):
            nod.append(f'<node id="n{i}" x="{x:.2f}" y="0" type="priority"/>')
        edg.append(f'<edge id="e0" from="n0" to="n1" numLanes="{n}" speed="{lim:.4f}" priority="10"/>')
        offs, lanes = {"e0": 0.0}, {"e0": n}
        tail = []
        if buf:
            edg.append(f'<edge id="eb" from="n1" to="n2" numLanes="{n}" speed="{lim:.4f}" priority="10"/>')
            for i in range(n):
                con.append(f'<connection from="e0" to="eb" fromLane="{i}" toLane="{i}"/>')
            offs["eb"], lanes["eb"] = L, n
            tail = ["eb"]
        routes = {"main": ["e0", *tail]}
        geo = {"x_merge": None, "x_acc_end": None, "x_end": L, "x_buf": L if buf else None}
    else:
        raise ValueError(kind)
    _netconvert(wd, nod, edg, con)
    root = ET.parse(wd / "net.net.xml").getroot()
    lane_len = {ln.get("id"): float(ln.get("length")) for e in root.findall("edge")
                if e.get("function") != "internal" for ln in e.findall("lane")}
    return {"offs": offs, "lanes": lanes, "routes": routes, "aux": aux, "geo": geo, "lane_len": lane_len,
            "n_through": spec.get("lanes", 4), "ramp_edges": locals().get("ramp_edges", {"er", "ex"})}


# ------------------------------------------------------------------ demand + routes
def write_routes(wd: Path, net: dict, fleet: dict, cal: IDMCalibration, seed: int, demand: dict,
                 dur: float, *, extra_attrs: dict | None = None, action_step: float | None = None,
                 lc_over: dict | None = None) -> dict:
    """``demand``: {route: [(t, veh/s), ...]} per route (merged in time order)."""
    rng = make_rng(seed)
    events = []
    for rname in sorted(demand):
        for t in corridor_departures(demand[rname], dur, rng):
            events.append((t, rname))
    events.sort()
    params = _draw_from_calibration(cal, len(events), rng)
    lc = {k: fleet[k] for k in ("lc_strategic", "lc_keep_right", "lc_cooperative", "lc_assertive", "lc_speed_gain")}
    lc_ramp = fleet["lc_strategic_ramp"]
    if lc_over:
        lc.update({k: v for k, v in lc_over.items() if k in lc})
        lc_ramp = lc_over.get("lc_strategic_ramp", lc_ramp)
    a_step = action_step if action_step is not None else fleet["action_step"]
    n0 = net["lanes"][net["routes"]["main"][0]]
    lines = ["<routes>"]
    for i, p in enumerate(params):
        rname = events[i][1]
        strat = lc_ramp if rname in ("ramp", "r2r") else lc["lc_strategic"]
        lines.append(_vtype_xml(f"t{i:05d}", p, fleet["model"], a_step, strat, lc["lc_keep_right"],
                                lc["lc_cooperative"], lc["lc_assertive"], lc["lc_speed_gain"],
                                extra_attrs=extra_attrs or None))
    for r, edges in net["routes"].items():
        lines.append(f'  <route id="{r}" edges="{" ".join(edges)}"/>')
    k_main = 0
    kinds = {}
    for i, (t, rname) in enumerate(events):
        if rname in ("ramp", "r2r"):
            da = 'departPos="base" departSpeed="avg" departLane="0"'
        else:
            da = f'departPos="base" departSpeed="avg" departLane="{k_main % n0}"'
            k_main += 1
        vid = f"v{i:05d}"
        kinds[vid] = rname
        lines.append(f'  <vehicle id="{vid}" type="t{i:05d}" depart="{t:.3f}" {da} route="{rname}"/>')
    lines.append("</routes>")
    (wd / "r.rou.xml").write_text("\n".join(lines))
    return {"n_planned": len(events), "kinds": kinds,
            "params": {f"v{i:05d}": p for i, p in enumerate(params)}}


# ------------------------------------------------------------------ run
def run(spec: dict, fleet: dict, seed: int, demand: dict, dur: float, *, pop: str | None = None,
        mean_over: dict | None = None, extra_attrs: dict | None = None, action_step: float | None = None,
        lc_over: dict | None = None, red: tuple[float, float] | None = None,
        boundary: list[tuple[float, float]] | None = None, sections: dict | None = None,
        lc_log: bool = False, follow_s: float = 5.0, route_fn=None, field_dx: float = 50.0, field_dt: float = 60.0,
        boundary_mode: str = "maxspeed", track_lc: bool = True, lc_x_min: float = -1e9, wave_span: tuple[float, float, float] | None = None,
        tag: str = "") -> dict:
    """One fixture run. Returns crossings per section and lane, lane-change events with the new
    follower's response, the speed field (x bins x 60 s) per lane and run counters."""
    import libsumo as ts
    from libsumo import constants as tc

    cal = population(pop or fleet["pop"], mean_over)
    t0w = time.perf_counter()
    with tempfile.TemporaryDirectory(dir=str(TMP)) as td:
        wd = Path(td)
        spec = dict(spec)
        spec["tl"] = red is not None
        net = build(spec, wd)
        if route_fn is not None:
            rr = route_fn(wd, net, seed)
        else:
            rr = write_routes(wd, net, fleet, cal, seed, demand, dur, extra_attrs=extra_attrs,
                              action_step=action_step, lc_over=lc_over)
        kinds, vparams = rr["kinds"], rr["params"]
        cmd = ["sumo", "-n", str(wd / "net.net.xml"), "-r", str(wd / "r.rou.xml"),
               "--step-length", str(STEP), "--seed", str(sumo_seed(seed)), "--time-to-teleport", "-1",
               "--no-warnings", "--collision.action", "warn", "--no-step-log", "--begin", "0",
               "--end", str(dur)]
        if lc_log:
            cmd += ["--lanechange-output", str(wd / "lc.xml")]
        ts.start(cmd)
        nlinks = 0
        if red is not None:
            nlinks = len(ts.trafficlight.getRedYellowGreenState("n1"))
            ts.trafficlight.setRedYellowGreenState("n1", "G" * nlinks)
        bsteps = list(boundary or [])
        bidx = 0
        offs = net["offs"]
        sections = sections or {}
        sec_x = sorted(sections.items(), key=lambda kv: kv[1])
        cross: dict[str, list] = {name: [] for name in sections}  # (t, lane_from_right_through, v, kind, vid)
        prev_x: dict[str, float] = {}
        prev_lane: dict[str, tuple[str, int]] = {}
        lc_events: list[dict] = []
        watch: dict[str, dict] = {}  # follower id -> record being filled
        RAMPS = net["ramp_edges"]
        x_hi = max(offs[e] + net["lane_len"].get(f"{e}_0", 0.0) for e in offs if e not in RAMPS)
        nbx = int(math.ceil(x_hi / field_dx)) + 1
        nbt = int(math.ceil(dur / field_dt)) + 1
        nl = max(net["lanes"].values())
        fsum = np.zeros((nbt, nbx, nl))
        fcnt = np.zeros((nbt, nbx, nl))
        n_coll = n_dep = 0
        hard = {"lt_-4.5": 0, "lt_-7": 0, "lt_-8.9": 0}
        hard_ev: list[tuple] = []
        # pooled-lane speed fields over the measured span for the registered wave detectors
        # (standard / relative / stack: 15 s x 75 m; stripe: 10 s x 50 m); wave_span = (x_lo, x_hi, t_lo)
        wf = None
        if wave_span is not None:
            wx0, wx1, wt0 = wave_span
            wf = {}
            for key, dtb, dxb in (("std", 15.0, 75.0), ("stripe", 10.0, 50.0)):
                nt_ = int((dur - wt0) // dtb)
                nx_ = int((wx1 - wx0) // dxb)
                wf[key] = (dtb, dxb, np.zeros((nt_, nx_)), np.zeros((nt_, nx_)))
        census = []
        x_slow_hi = sections.get(spec.get("census_to", ""), None)
        vars_ = [tc.VAR_ROAD_ID, tc.VAR_LANEPOSITION, tc.VAR_LANE_INDEX, tc.VAR_SPEED, tc.VAR_ACCELERATION]
        through_shift = {e: (1 if net["aux"].get(e) else 0) for e in net["lanes"]}
        n_steps = int(round(dur / STEP))
        for k in range(n_steps):
            tnow = k * STEP
            if red is not None:
                if abs(tnow - red[0]) < 1e-6:
                    ts.trafficlight.setRedYellowGreenState("n1", "r" * nlinks)
                elif abs(tnow - red[1]) < 1e-6:
                    ts.trafficlight.setRedYellowGreenState("n1", "G" * nlinks)
            while bidx < len(bsteps) and tnow >= bsteps[bidx][0] - 1e-9:
                if boundary_mode == "maxspeed":
                    ts.edge.setMaxSpeed("eb", float(bsteps[bidx][1]))
                bidx += 1
            ts.simulationStep()
            t = tnow + STEP
            for vid in ts.simulation.getDepartedIDList():
                ts.vehicle.subscribe(vid, vars_)
                n_dep += 1
            n_coll += ts.simulation.getCollidingVehiclesNumber()
            res = ts.vehicle.getAllSubscriptionResults()
            ns = 0
            for vid, r in res.items():
                e = r[tc.VAR_ROAD_ID]
                if e not in offs:
                    continue
                v = r[tc.VAR_SPEED]
                a = r[tc.VAR_ACCELERATION]
                li = r[tc.VAR_LANE_INDEX]
                x = offs[e] + r[tc.VAR_LANEPOSITION]
                if a < -4.5:
                    hard["lt_-4.5"] += 1
                    if len(hard_ev) < 5000:
                        hard_ev.append((round(t, 1), e, li, round(x, 1), round(a, 2), round(v, 2), kinds.get(vid, "?")))
                    if a < -7.0:
                        hard["lt_-7"] += 1
                        if a < -8.9:
                            hard["lt_-8.9"] += 1
                if wf is not None and e not in RAMPS and wx0 <= x < wx1 and t >= wt0:
                    for dtb, dxb, ssum, scnt in wf.values():
                        it_, ix_ = int((t - wt0) // dtb), int((x - wx0) // dxb)
                        if it_ < ssum.shape[0] and ix_ < ssum.shape[1]:
                            ssum[it_, ix_] += v
                            scnt[it_, ix_] += 1
                if e not in RAMPS:
                    bt, bx = int(t // field_dt), int(x // field_dx)
                    if 0 <= bx < nbx:
                        fsum[bt, bx, li] += v
                        fcnt[bt, bx, li] += 1
                    if x_slow_hi is not None and x < x_slow_hi and v < SLOW_MS:
                        ns += 1
                # through-lane number from the right (1..n); 0 = an auxiliary lane
                lt = li + 1 - through_shift.get(e, 0)
                px = prev_x.get(vid)
                if px is not None and e not in RAMPS:
                    for name, xs_ in sec_x:
                        if px < xs_ <= x:
                            cross[name].append((round(t, 1), lt, round(v, 2), kinds.get(vid, "?"), vid))
                if e not in RAMPS:
                    prev_x[vid] = x
                pl = prev_lane.get(vid)
                if track_lc and pl is not None and pl[0] == e and pl[1] != li and e not in RAMPS and x >= lc_x_min:
                    fol = ts.vehicle.getFollower(vid, 0.0)
                    lea = ts.vehicle.getLeader(vid, 0.0)
                    ev = {"t": round(t, 1), "vid": vid, "kind": kinds.get(vid, "?"), "edge": e, "from": pl[1],
                          "to": li, "x": round(x, 1), "v": round(v, 2),
                          "lead_id": lea[0] if lea else "", "lead_gap": round(lea[1], 2) if lea else None,
                          "fol_id": fol[0] if fol and fol[0] else "", "fol_gap": round(fol[1], 2) if fol and fol[0] else None}
                    if lea:
                        ev["lead_v"] = round(ts.vehicle.getSpeed(lea[0]), 2)
                    if fol and fol[0]:
                        ev["fol_v0"] = round(ts.vehicle.getSpeed(fol[0]), 2)
                        ev["fol_b"] = round(vparams.get(fol[0], {}).get("b", float("nan")), 3)
                        ev["fol_T"] = round(vparams.get(fol[0], {}).get("T", float("nan")), 3)
                        ev["fol_s0"] = round(vparams.get(fol[0], {}).get("s0", float("nan")), 3)
                        watch[fol[0]] = {"ev": len(lc_events), "until": t + follow_s, "amin": 0.0, "vmin": ev["fol_v0"]}
                    lc_events.append(ev)
                prev_lane[vid] = (e, li)
            # follower responses
            done = []
            for fid, w in watch.items():
                r = res.get(fid)
                if r is None or t > w["until"]:
                    done.append(fid)
                    continue
                w["amin"] = min(w["amin"], r[tc.VAR_ACCELERATION])
                w["vmin"] = min(w["vmin"], r[tc.VAR_SPEED])
            for fid in done:
                w = watch.pop(fid)
                lc_events[w["ev"]]["fol_amin"] = round(w["amin"], 3)
                lc_events[w["ev"]]["fol_vmin"] = round(w["vmin"], 2)
            if k % 120 == 119:
                census.append(ns)
            # forget vehicles that left
            if k % 200 == 199:
                live = set(res)
                for d in (prev_x, prev_lane):
                    for vid in [v for v in d if v not in live]:
                        d.pop(vid, None)
        for fid, w in watch.items():
            lc_events[w["ev"]]["fol_amin"] = round(w["amin"], 3)
            lc_events[w["ev"]]["fol_vmin"] = round(w["vmin"], 2)
        n_arrived_total = ts.simulation.getArrivedNumber()
        n_loaded = ts.simulation.getLoadedNumber()
        ts.close()
        lc_reasons = parse_lc(wd / "lc.xml") if lc_log else None
    with np.errstate(invalid="ignore"):
        field = np.where(fcnt > 0, fsum / np.maximum(fcnt, 1), np.nan)
    waves = None
    if wf is not None:
        waves = wave_readings(wf, wave_span)
    out = {
        "tag": tag, "spec": spec, "fleet": fleet["source"], "model": fleet["model"], "pop": pop or fleet["pop"],
        "mean_over": mean_over or {}, "extra_attrs": extra_attrs or {}, "action_step": action_step or fleet["action_step"],
        "lc_over": lc_over or {}, "seed": seed, "dur": dur, "red": red, "boundary_n": len(bsteps),
        "boundary_mode": boundary_mode,
        "n_planned": rr["n_planned"], "n_departed": n_dep, "n_collisions": n_coll, "hard_steps": hard, "hard_ev": hard_ev,
        "census": census, "sections": sections, "cross": cross, "lc": lc_events, "lc_reasons": lc_reasons,
        "field_dx": field_dx, "field_dt": field_dt, "field": np.round(field, 2).tolist(),
        "field_n": fcnt.astype(int).tolist(), "geo": net["geo"], "lanes": net["lanes"],
        "wall_s": round(time.perf_counter() - t0w, 1), "waves": waves,
    }
    return out


def wave_readings(wf: dict, span: tuple[float, float, float]) -> dict:
    """The registered detectors of validation.waves on the pooled fields (positive = backward km/h)."""
    from validation.fields import SpeedField
    from validation.waves import WAVE_DETECTORS

    x0, _, t0 = span
    fields = {}
    for key, (dtb, dxb, ssum, scnt) in wf.items():
        with np.errstate(invalid="ignore"):
            ms = np.where(scnt > 0, ssum / np.maximum(scnt, 1), np.nan)
        fields[key] = SpeedField(t_edges=t0 + dtb * np.arange(ms.shape[0] + 1),
                                 x_edges=x0 + dxb * np.arange(ms.shape[1] + 1), mean_speed=ms)
    out = {}
    for name, det in WAVE_DETECTORS.items():
        fld = fields["stripe"] if det.dt_bin_s == 10.0 else fields["std"]
        m = det.measure(fld)
        out[name] = {"speed_kmh": None if not np.isfinite(m.speed_kmh) else float(m.speed_kmh),
                     "n_backward": m.n_backward, "n_components": m.n_components,
                     "in_band": None if not m.backward_speeds_kmh else float(m.in_band_fraction()),
                     "backward_kmh": [round(float(b), 2) for b in m.backward_speeds_kmh],
                     "contrast": None if not np.isfinite(m.contrast) else float(m.contrast), "note": m.note}
    return out


def parse_lc(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for el in ET.parse(path).getroot():
        d = dict(el.attrib)
        out.append({k: d.get(k) for k in ("id", "time", "from", "to", "dir", "speed", "pos", "reason",
                                           "leaderGap", "leaderSecureGap", "followerGap", "followerSecureGap",
                                           "leaderSpeed", "followerSpeed")})
    return out


# ------------------------------------------------------------------ readers
def flows(r: dict, name: str, t_lo: float, t_hi: float) -> dict:
    """Flow [veh/h] at a section in [t_lo, t_hi), total and by through lane (1 = rightmost; 0 = aux)."""
    rows = [c for c in r["cross"][name] if t_lo <= c[0] < t_hi]
    h = (t_hi - t_lo) / 3600.0
    by: dict[int, int] = {}
    for c in rows:
        by[c[1]] = by.get(c[1], 0) + 1
    return {"total": len(rows) / h, "by_lane": {k: v / h for k, v in sorted(by.items())}, "n": len(rows)}


def headways(r: dict, name: str, t_lo: float, t_hi: float) -> dict:
    """Per-lane time headways between successive crossings in [t_lo, t_hi)."""
    out = {}
    per: dict[int, list] = {}
    for c in r["cross"][name]:
        if t_lo <= c[0] < t_hi:
            per.setdefault(c[1], []).append(c[0])
    for ln, ts_ in per.items():
        ts_ = np.sort(np.array(ts_))
        h = np.diff(ts_)
        if len(h) >= 5:
            out[ln] = {"n": int(len(h)), "mean": float(h.mean()), "p10": float(np.percentile(h, 10)),
                       "p50": float(np.percentile(h, 50)), "p90": float(np.percentile(h, 90)),
                       "share_lt_1.5": float((h < 1.5).mean()), "share_gt_4": float((h > 4.0).mean())}
    return out


def discharge_window(r: dict, red_end: float, settle: float = 180.0) -> tuple[float, float] | None:
    """[t_lo, t_hi): from red_end + settle while the slow census (vehicles < 8 m/s upstream of the
    census section) stays positive (the original BLOCK definition, 60-s resolution)."""
    sc = r["census"]
    t_lo = red_end + settle
    ok = [i for i, n in enumerate(sc) if (i + 1) * 60.0 > t_lo and n > 0]
    if not ok:
        return None
    # contiguous from the first qualifying minute
    i0 = ok[0]
    i1 = i0
    while i1 + 1 < len(sc) and sc[i1 + 1] > 0:
        i1 += 1
    return max(t_lo, i0 * 60.0), (i1 + 1) * 60.0


def save(r: dict, path: Path) -> None:
    with path.open("a") as fh:
        fh.write(json.dumps(r, default=float) + "\n")
