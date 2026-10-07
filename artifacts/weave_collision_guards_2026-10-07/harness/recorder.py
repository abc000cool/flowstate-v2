"""Command recorder for one collision of the W2 stress sets (docs/I94_CAL_COLLISIONS.md §10, §14).

usage (repository root):
  recorder.py SET SEED COLLIDER VICTIM T_CONTACT POST_JSON OUT_JSON WORK_DIR [--weave-set KEY=VALUE ...]

Re-runs one run of ``repro.py`` (SET, SEED, the arm's keys) with libsumo's ``vehicle.slowDown`` and
``vehicle.getLeader`` wrapped to log, for COLLIDER and VICTIM only, every weave speed target (time,
vehicle, target speed) and every leader reading (reported gap, leader). At each target issued to
them it also evaluates amendment W2's handback test as ``microsim.runner._handback_needed`` does
(``getLeader`` + ``getFollowSpeed``; ``decel`` for EIDM). Nothing else is called and no argument
is changed; the run must reproduce POST_JSON's sha256 of every Parquet file for (SET, SEED), which
shows the logging (and the handback queries) left the run unchanged. Then the 12 s before the
contact are printed per step for the two vehicles from the trajectories: lane, x, v, a, bumper gap,
the target in force (issued the step before) and the handback verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import libsumo
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import repro  # noqa: E402

from microsim import run_micro  # noqa: E402
from microsim.runner import LEADER_LOOKAHEAD_M  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("set")
    ap.add_argument("seed", type=int)
    ap.add_argument("collider")
    ap.add_argument("victim")
    ap.add_argument("t_contact", type=float)
    ap.add_argument("post", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("work", type=Path)
    ap.add_argument("--weave-set", action="append", default=None)
    a = ap.parse_args()
    who = {a.collider, a.victim}
    log: list[dict] = []
    V = libsumo.vehicle
    orig_slow, orig_lead = V.slowDown, V.getLeader

    def slow(vid, speed, duration):  # type: ignore[no-untyped-def]
        if vid in who:
            t = libsumo.simulation.getTime()
            v = V.getSpeed(vid)
            lead = orig_lead(vid, LEADER_LOOKAHEAD_M)
            rec = {"t": t, "what": "slowDown", "vid": vid, "target": speed, "dur": duration, "v": v,
                   "decel": V.getDecel(vid)}
            if lead is not None and lead[0] != "":
                vf = V.getFollowSpeed(vid, v, float(lead[1]), V.getSpeed(lead[0]), V.getDecel(lead[0]), lead[0])
                rec.update({"leader": lead[0], "reported_gap": float(lead[1]), "follow_speed": vf,
                            "handback_needed": vf < v - V.getDecel(vid) * 0.5 - 1e-9})
            log.append(rec)
        return orig_slow(vid, speed, duration)

    def get_leader(vid, dist=LEADER_LOOKAHEAD_M):  # type: ignore[no-untyped-def]
        r = orig_lead(vid, dist)
        if vid in who:
            log.append({"t": libsumo.simulation.getTime(), "what": "getLeader", "vid": vid,
                        "leader": None if r is None else r[0], "reported_gap": None if r is None else float(r[1])})
        return r

    V.slowDown, V.getLeader = slow, get_leader
    try:
        cfg = repro.config(a.set, a.seed, repro.M.parse_weave_set(a.weave_set))
        paths = run_micro(cfg, a.seed, a.work)
    finally:
        V.slowDown, V.getLeader = orig_slow, orig_lead
    rd = Path(paths.run_dir)
    sha = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(rd.glob("*.parquet"))}
    fixture = repro.SETS[a.set][0]
    stored = next(r for r in json.loads(a.post.read_text()) if r["fixture"] == fixture and r["seed"] == a.seed)
    identical = sha == stored["parquet_sha"]
    meta = json.loads((rd / "meta.json").read_text())
    df = pd.read_parquet(rd / "trajectories.parquet", columns=["t", "veh_id", "x", "lane", "v", "a"])
    w = df[(df.veh_id.isin(who)) & (df.t >= a.t_contact - 12.0) & (df.t <= a.t_contact + 0.5)]
    steps = []
    for t, g in w.groupby("t"):
        row: dict = {"t": float(t)}
        for vid, tag in ((a.collider, "c"), (a.victim, "v")):
            r = g[g.veh_id == vid]
            if len(r):
                row.update({f"{tag}_lane": int(r.lane.iloc[0]), f"{tag}_x": round(float(r.x.iloc[0]), 2),
                            f"{tag}_v": round(float(r.v.iloc[0]), 3), f"{tag}_a": round(float(r.a.iloc[0]), 3)})
        if "c_x" in row and "v_x" in row:
            row["bumper_gap_m"] = round(row["v_x"] - 5.0 - row["c_x"], 2)
        # the target applied in this step was issued at the previous step's time
        cmd = [e for e in log if e["what"] == "slowDown" and e["vid"] == a.collider
               and abs(e["t"] - (float(t) - 0.5)) < 1e-6]
        if cmd:
            row["c_target_in_force"] = round(cmd[0]["target"], 3)
            row["c_handback_needed"] = cmd[0].get("handback_needed")
            row["c_follow_speed"] = round(cmd[0]["follow_speed"], 3) if "follow_speed" in cmd[0] else None
        steps.append(row)
    res = {"set": a.set, "seed": a.seed, "collider": a.collider, "victim": a.victim, "t_contact": a.t_contact,
           "weave_set": a.weave_set, "parquet_identical_to_stored": identical, "n_collisions": meta["n_collisions"],
           "collisions": meta["collisions"], "window": steps,
           "commands": [e for e in log if e["what"] == "slowDown" and e["t"] >= a.t_contact - 30.0],
           "n_slowdown_collider": sum(1 for e in log if e["what"] == "slowDown" and e["vid"] == a.collider)}
    a.out.write_text(json.dumps(res, indent=1, default=float))
    print("identical to the stored run:", identical, "collisions:", meta["collisions"])
    for r in steps:
        print(r)


if __name__ == "__main__":
    main()
