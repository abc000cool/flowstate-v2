"""Amendment W2's diagnostic arms (docs/I94_CAL_COLLISIONS.md §13.3; reported, never criteria).

usage (repository root): diag.py DIR OUT_JSON

DIR holds ``<set>_<arm>.json`` and ``<set>_<arm>_post.json`` for the arms hb, cl, op (each W2 key alone)
against ``ref`` on s1, xr1, xt1, xt2, and w1b, w1bw2 on s3a; ``lockprobe_<arm>*.json`` (the grid run
th52_upstream_fleet seed 3) are read from DIR's parent. Per (set, arm): collisions with their
mechanisms, the W2 counters, given-up exits, hard brakes, locks (§13.4) and, on s1, the paired
exit-end flow against the reference.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import stats

D = Path(sys.argv[1])
W2_COUNTERS = ("n_handback_skips", "n_close_leader_withheld", "n_opposing_deferred", "n_opposing_vetoed")


def ci(d: list[float]) -> list[float]:
    a = np.asarray(d, float)
    sd = a.std(ddof=1) if len(a) > 1 else 0.0
    h = stats.t.ppf(0.975, len(a) - 1) * sd / math.sqrt(len(a)) if sd > 0 else 0.0
    return [round(float(a.mean()), 2), round(float(a.mean() - h), 2), round(float(a.mean() + h), 2)]


def load(prefix: Path) -> tuple[dict, dict] | None:
    f, fp = Path(f"{prefix}.json"), Path(f"{prefix}_post.json")
    if not (f.exists() and fp.exists()):
        return None
    rows = {(r.get("fixture", "th52"), int(r["seed"])): r for r in json.loads(f.read_text())["rows"]}
    post = {(r["fixture"], int(r["seed"])): r for r in json.loads(fp.read_text())}
    return rows, post


def summary(rows: dict, post: dict) -> dict:
    keys = sorted(post)
    return {
        "runs": len(keys),
        "collisions": [f"{k[0]}:{k[1]}:{c['mechanism']}" for k in keys for c in post[k]["collisions"]],
        "counters": {c: sum((z.get(c) or 0) for k in keys for z in post[k]["sections"]) for c in W2_COUNTERS},
        "exits_given_up": sum((z["n_missed_exit"] or 0) for k in keys for z in post[k]["sections"]),
        "hard_brake": sum(post[k]["hard_brake"] for k in keys),
        "departed": sum(post[k]["departed"] for k in keys),
        "locks": [f"{k[0]}:{k[1]}:lane{lk['lane']}:{lk['kind']}:{lk['len_s']}"
                  for k in keys for z in post[k]["sections"] for lk in z["locks"]],
        "lock_flags": sorted(f"{k[0]}:{k[1]}" for k in keys if rows.get(k, {}).get("lock")),
        "releases": sum((z.get("n_entrant_took_exit") or 0) for k in keys for z in post[k]["sections"]),
    }


out: dict = {}
for s, arms in (("s1", ("ref", "hb", "cl", "op")), ("xr1", ("ref", "hb", "cl", "op")),
                ("xt1", ("ref", "hb", "cl", "op")), ("xt2", ("ref", "hb", "cl", "op")),
                ("s3a", ("ref", "w1b", "w1bw2"))):
    got = {a: load(D / f"{s}_{a}") for a in arms}
    out[s] = {a: summary(*g) for a, g in got.items() if g is not None}
    if s == "s1" and got.get("ref"):
        rr = got["ref"][0]
        for a in ("hb", "cl", "op"):
            if got.get(a):
                rw = got[a][0]
                ks = sorted(rr)
                out[s][a]["paired_flow_vs_ref"] = ci([rw[k]["exit_end_flow_vph"] - rr[k]["exit_end_flow_vph"] for k in ks])
                out[s][a]["paired_given_up_vs_ref"] = ci(
                    [rw[k]["exits_given_up"][0] - rr[k]["exits_given_up"][0] for k in ks])
    if s == "s3a" and got.get("w1b") and got.get("w1bw2"):
        pa, pb = got["w1b"][1], got["w1bw2"][1]
        out[s]["w1bw2_vs_w1b_departed_paired"] = ci([pb[k]["departed"] - pa[k]["departed"] for k in sorted(pa)])
lp = {}
for a in ("ref", "hb", "cl", "op", "w2", "w1b", "w1bw2"):
    g = load(D.parent / f"lockprobe_{a}")
    if g is not None:
        rows, post = g
        (k,) = list(post)
        lp[a] = {"departed": post[k]["departed"], "lock_flag": rows[k]["lock"],
                 "lowest_zone_minute_ms": rows[k]["lowest_zone_minute_ms"],
                 "locks": [f"lane{lk['lane']}:{lk['kind']}:{lk['t0']}-{lk['t1']}" for z in post[k]["sections"]
                           for lk in z["locks"]],
                 "n_unfinished": [z["n_unfinished"] for z in post[k]["sections"]],
                 "counters": {c: [z.get(c) for z in post[k]["sections"]] for c in W2_COUNTERS},
                 "releases": [z.get("n_entrant_took_exit") for z in post[k]["sections"]],
                 "collisions": post[k]["n_collisions"], "parquet_sha": post[k]["parquet_sha"]}
out["lockprobe_th52_upstream_fleet_s3"] = lp
Path(sys.argv[2]).write_text(json.dumps(out, indent=1, default=float))
for s, v in out.items():
    print("==", s)
    for a, x in v.items():
        print(" ", a, json.dumps(x, default=float)[:700])
