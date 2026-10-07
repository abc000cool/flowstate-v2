"""Every W1b release beside its reference run: what the same entrant (same seed, so the same run up to
the first release, B1) did without the rule. usage: releases.py EVAL_DIR -> EVAL_DIR/releases.json

Per release: the set, fixture, seed, entrant, release time and its stand; in the reference run the
same entrant's stand that began at the same first sample (how long it stood without W1b) and how it
ended (changed into lane 1 / still standing at the run's end); and the run-level counters of both arms.
"""

import json
import sys
from pathlib import Path

import pandas as pd

E = Path(sys.argv[1])
crit = json.loads((E / "criteria.json").read_text())


def post(stem: str, arm: str) -> dict:
    out = {}
    parts = [stem] if (E / f"{stem}_{arm}_post.json").exists() else [f"{stem}a", f"{stem}b"]
    for p in parts:
        for r in json.loads((E / f"{p}_{arm}_post.json").read_text()):
            out[(r["fixture"], r["seed"])] = r
    return out


def rows(stem: str, arm: str) -> dict:
    out = {}
    parts = [stem] if (E / f"{stem}_{arm}.json").exists() else [f"{stem}a", f"{stem}b"]
    for p in parts:
        for r in json.loads((E / f"{p}_{arm}.json").read_text())["rows"]:
            out[(r.get("fixture", "th52"), r["seed"])] = r
    return out


res = []
for rel in crit["criteria"]["B0"]["releases"]:
    s, fx, seed = rel["run"].split(":")
    k = (fx, int(seed))
    pr, pw = post(s, "ref")[k], post(s, "w1b")[k]
    rr, rw = rows(s, "ref")[k], rows(s, "w1b")[k]
    zr, zw = pr["sections"][0], pw["sections"][0]
    # the same entrant in the reference run: its trajectory at the gore from the stand's first sample
    df = pd.read_parquet(Path(pr["dir"]) / "trajectories.parquet", columns=["t", "veh_id", "x", "lane", "v"])
    g = df[(df.veh_id == rel["veh_id"]) & (df.t >= rel["stand_t0"])].sort_values("t")
    standing = (g.lane == 0) & (g.v < 0.1) & (g.x >= zr["gore"] - 5.0) & (g.x < zr["gore"])
    run_len = int(standing.cumprod().sum())
    after = g.iloc[run_len] if run_len < len(g) else None
    ref_stand_s = (run_len - 1) * 0.5 if run_len else 0.0
    ended = ("run end" if after is None else
             f"lane {int(after.lane)} at x {after.x - zr['gore']:+.1f} m, v {after.v:.1f} m/s")
    res.append({
        **rel, "set": s, "fixture": fx, "seed": int(seed),
        "ref_same_entrant_stand_s": ref_stand_s, "ref_stand_ended": ended,
        "ref_locks": zr["locks"], "w1b_locks": zw["locks"],
        "flag_ref": rr["lock"], "flag_w1b": rw["lock"],
        "missed_exit_ref": zr["n_missed_exit"], "missed_exit_w1b": zw["n_missed_exit"],
        "lowest_minute_ref": rr.get("lowest_zone_minute_ms"), "lowest_minute_w1b": rw.get("lowest_zone_minute_ms"),
        "departed_ref": pr["departed"], "departed_w1b": pw["departed"],
        "entrance_departed": zw["entrance_departed"],
        "hard_brake_ref": rr["hard_brake_vehicle_steps"], "hard_brake_w1b": rw["hard_brake_vehicle_steps"],
    })
(E / "releases.json").write_text(json.dumps(res, indent=1, default=float))
for r in res:
    print({k: r[k] for k in ("set", "fixture", "seed", "veh_id", "gave_up_s", "stand_len_s",
                             "ref_same_entrant_stand_s", "ref_stand_ended", "missed_exit_ref", "missed_exit_w1b",
                             "lowest_minute_ref", "lowest_minute_w1b", "flag_ref", "flag_w1b")})
