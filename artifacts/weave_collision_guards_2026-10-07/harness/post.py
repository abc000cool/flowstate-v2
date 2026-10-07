"""Per-run readouts of amendment W2's evaluation (docs/I94_CAL_COLLISIONS.md §13.4).

usage (repository root): post.py WORK_DIR OUT_JSON

For every run tree WORK_DIR/<fixture>_weave/<hash>/<seed>/ (``merge_model_selfcheck.py --keep`` or
``repro.py``):

* every logged collision with its mechanism (§13.4), read from the trajectories (one sample per
  0.5-s step) restricted to the section's span, where trajectory lanes are section lanes:
  - R: the event's lane is lane 0 of a weave section edge and neither party has a sample in
    [t - 5, t] outside the section's lane 0 (a ramp vehicle has no sample there, which is allowed);
  - T: the event's lane is a section lane k >= 1 and the two parties' last entries into lane k
    before t are at the same sample within [t - 10, t], the collider from lane k + 1, the victim
    from lane k - 1;
  - other: anything else.
  Each event also carries the parties' roles, their speeds at the contact and, for the collider, the
  accelerations of its last 4 samples beside its vType's ``decel`` (the run's routes file);
* per weave section: amendment W2's counters, given-up exits, the lock of W1b's fixture form (after
  the 120-s warm-up the same vehicle the front of a section lane within 15 m of the gore below
  0.1 m/s for >= 120 s without a break);
* hard brakes (vehicle-steps with a <= -9 + 1e-6 m/s^2);
* D1's digests: sha256 of every Parquet file; meta.json without wall time, realtime factor,
  config_hash, run-directory strings, both copies of the weave params and the W2 / W1 counters.
"""

from __future__ import annotations

import copy
import glob
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import sumolib

DT = 0.5
WARMUP_S = 120.0
LOCK_S = 120.0
FRONT_M = 15.0
HALT = 0.1
W2_COUNTERS = ("n_handback_skips", "n_close_leader_withheld", "n_opposing_deferred", "n_opposing_vetoed")


def runs_of(t: np.ndarray) -> list[tuple[int, int]]:
    """Index ranges [i, j] of consecutive samples (step DT) in a sorted time array."""
    if len(t) == 0:
        return []
    breaks = np.flatnonzero(np.diff(t) > DT + 1e-6)
    starts = np.r_[0, breaks + 1]
    ends = np.r_[breaks, len(t) - 1]
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def meta_digest(meta: dict, rd: Path) -> str:
    m = copy.deepcopy(meta)
    for k in ("wall_time_s", "realtime_factor", "config_hash"):
        m.pop(k, None)
    for r in m.get("config", {}).get("network", {}).get("ramps", []) or []:
        if r.get("weave") is not None:
            r["weave"].pop("weave_params", None)
    for z in m.get("weave_sections") or []:
        z.pop("params", None)
        z.pop("n_entrant_took_exit", None)
        for k in W2_COUNTERS:
            z.pop(k, None)
    text = json.dumps(m, sort_keys=True)
    text = text.replace(str(rd), "<RUN>").replace(str(rd.parent), "<HASHDIR>")
    text = re.sub(r"[0-9a-f]{12}", "<HASH>", text)
    return hashlib.sha256(text.encode()).hexdigest()


def vtype_decel(rd: Path) -> dict[str, float]:
    """``decel`` per vehicle id from the run's routes file (vType per vehicle or shared)."""
    out: dict[str, float] = {}
    files = sorted(rd.glob("net/**/*.rou.xml"))
    if not files:
        return out
    text = files[0].read_text()
    vt = {m.group(1): float(m.group(2)) for m in re.finditer(r'<vType id="([^"]+)"[^>]*?decel="([0-9.eE+-]+)"', text)}
    for m in re.finditer(r'<vehicle id="([^"]+)"[^>]*?type="([^"]+)"', text):
        if m.group(2) in vt:
            out[m.group(1)] = vt[m.group(2)]
    return out


def classify(
    ev: dict, df: pd.DataFrame, sec: pd.DataFrame, x0: float, gore: float, roles: dict
) -> dict:
    """Mechanism of one collision on a weave section (§13.4)."""
    t = float(ev["t"])
    edge, _, lane_s = str(ev["lane"]).rpartition("_")
    k = int(lane_s)
    out: dict = {"section_lane": k, "mechanism": "other"}
    c, v = str(ev["collider"]), str(ev["victim"])
    gc = sec[sec.veh_id == c].sort_values("t")
    gv = sec[sec.veh_id == v].sort_values("t")
    if k == 0:
        ok = True
        for vid in (c, v):
            g = df[df.veh_id == vid]
            w = g[(g.t >= t - 5.0 - 1e-9) & (g.t <= t + 1e-9)]
            # every sample in the window on the section's lane 0 (a ramp vehicle
            # has no sample before it reaches the section)
            if (w.lane != 0).any() or (w.x < x0 - 1e-6).any() or (w.x >= gore).any():
                ok = False
        if ok:
            out["mechanism"] = "R"
    else:

        def last_entry(g: pd.DataFrame) -> tuple[float, int] | None:
            # the last entry into lane k before t (samples strictly before the
            # contact's sample: a change in the contact step's lane-change stage
            # comes after its movement); the vehicle may have left lane k again
            # in the contact step itself (fixed 2026-10-07 09:05 CDT: the first
            # version also required it to be in lane k at the contact's sample,
            # which the registered definition does not)
            g = g[g.t < t - 1e-9]
            lanes = g.lane.to_numpy()
            ts = g.t.to_numpy()
            for i in range(len(g) - 1, 0, -1):
                if lanes[i] == k and lanes[i - 1] != k:
                    if ts[i] - ts[i - 1] > DT + 1e-6:
                        return None  # a gap in the samples: not one step
                    return float(ts[i]), int(lanes[i - 1])
            return None

        ec, evv = last_entry(gc), last_entry(gv)
        out["collider_entry"], out["victim_entry"] = ec, evv
        if (
            ec is not None
            and evv is not None
            and abs(ec[0] - evv[0]) < 1e-6
            and ec[0] >= t - 10.0 - 1e-9
            and ec[1] == k + 1
            and evv[1] == k - 1
        ):
            out["mechanism"] = "T"
    for who, g in (("collider", gc), ("victim", gv)):
        at = g[(g.t <= t + 1e-9)].tail(1)
        out[f"{who}_v_ms"] = float(at.v.iloc[0]) if len(at) else None
        out[f"{who}_role"] = roles.get(who)
    last = gc[(gc.t <= t + 1e-9)].tail(4)
    out["collider_last_a"] = [round(float(a), 3) for a in last.a]
    out["collider_last_lane"] = [int(x) for x in last.lane]
    out["victim_last_lane"] = [int(x) for x in gv[(gv.t <= t + 1e-9)].tail(4).lane]
    return out


def one(rd: Path) -> dict:
    meta = json.loads((rd / "meta.json").read_text())
    veh = pd.read_parquet(rd / "vehicles.parquet").set_index("veh_id")
    df = pd.read_parquet(rd / "trajectories.parquet", columns=["t", "veh_id", "x", "lane", "v", "a"])
    net = sumolib.net.readNet(str(next(rd.glob("net/**/*.net.xml"))))
    out: dict = {
        "dir": str(rd),
        "fixture": rd.parents[1].name.removesuffix("_weave"),
        "seed": meta["seed"],
        "config_hash": meta["config_hash"],
        "n_collisions": meta["n_collisions"],
        "departed": meta["n_vehicles_departed"],
        "hard_brake": int((df.a <= -9.0 + 1e-6).sum()),
        "parquet_sha": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(rd.glob("*.parquet"))},
        "meta_digest": meta_digest(meta, rd),
        "sections": [],
        "collisions": [],
    }
    decel = vtype_decel(rd) if meta["n_collisions"] else {}
    origin = veh.origin.astype(str) if "origin" in veh else pd.Series(dtype=str)
    dest = veh.destination.astype(str) if "destination" in veh else pd.Series(dtype=str)
    sec_frames = []
    for z in meta.get("weave_sections") or []:
        ramp = next(r for r in meta["ramps"] if r["name"] == z["ramp"])
        x0 = float(ramp["attach_x_m"])
        gore = x0 + sum(net.getEdge(e).getLength() for e in z["edges"])
        sec = df[(df.x >= x0) & (df.x < gore)]
        sec_frames.append((z, x0, gore, sec))
        entrants = set(veh.index[(origin == z["ramp"]) & (dest != z["exit"])])
        exiters = set(veh.index[(origin != z["ramp"]) & (dest == z["exit"])])

        def kind(v: str, entrants=entrants, exiters=exiters, z=z) -> str:
            if v in entrants:
                return "entrant"
            if v in exiters:
                return "exiter"
            if v in veh.index and origin.get(v) == z["ramp"]:
                return "ramp_to_exit"
            return "through"

        w = sec[sec.t >= WARMUP_S]
        fronts = w.loc[w.groupby(["t", "lane"]).x.idxmax()] if len(w) else w
        stuck = fronts[(gore - fronts.x <= FRONT_M) & (fronts.v < HALT)] if len(fronts) else fronts
        front_stands = []
        for (lane, vid), g in stuck.sort_values("t").groupby(["lane", "veh_id"], sort=False):
            t = g.t.to_numpy()
            for i, j in runs_of(t):
                front_stands.append({"lane": int(lane), "veh_id": vid, "kind": kind(vid),
                                     "t0": float(t[i]), "t1": float(t[j]), "len_s": float(t[j] - t[i])})
        locks = [f for f in front_stands if f["len_s"] >= LOCK_S - 1e-9]
        out["sections"].append({
            "ramp": z["ramp"], "exit": z["exit"], "edges": z["edges"], "x0": x0, "gore": gore,
            "n_entered": z.get("n_entered"), "n_missed_exit": z.get("n_missed_exit"),
            "n_unfinished": z.get("n_unfinished"), "n_entrant_took_exit": z.get("n_entrant_took_exit"),
            "n_cooperations": z.get("n_cooperations"), "n_changer_eased": z.get("n_changer_eased"),
            **{k: z.get(k) for k in W2_COUNTERS},
            "locks": locks,
            "longest_front_stand_s": max((f["len_s"] for f in front_stands), default=0.0),
        })
        z["_kind"] = kind
    for ev in meta.get("collisions") or []:
        edge = str(ev["lane"]).rpartition("_")[0]
        rec = {k: ev[k] for k in ("t", "collider", "victim", "type", "lane", "pos_m")}
        hit = next(((z, x0, gore, sec) for z, x0, gore, sec in sec_frames if edge in z["edges"]), None)
        if hit is None:
            rec.update({"section": None, "mechanism": "other (not on a weave section)"})
        else:
            z, x0, gore, sec = hit
            roles = {"collider": z["_kind"](str(ev["collider"])), "victim": z["_kind"](str(ev["victim"]))}
            rec.update({"section": z["ramp"], **classify(ev, df, sec, x0, gore, roles)})
        rec["collider_decel"] = decel.get(str(ev["collider"]))
        out["collisions"].append(rec)
    for z, *_ in sec_frames:
        z.pop("_kind", None)
    return out


if __name__ == "__main__":
    res = []
    for m in sorted(glob.glob(f"{sys.argv[1]}/*/*/*/meta.json")):
        r = one(Path(m).parent)
        res.append(r)
        print(r["fixture"], r["seed"], r["n_collisions"], [c["mechanism"] for c in r["collisions"]],
              [(s["n_handback_skips"], s["n_close_leader_withheld"], s["n_opposing_deferred"], len(s["locks"]))
               for s in r["sections"]], flush=True)
    Path(sys.argv[2]).write_text(json.dumps(res, indent=1, default=float))
