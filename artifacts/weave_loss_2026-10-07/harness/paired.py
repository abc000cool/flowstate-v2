"""Paired comparison of arms against the weave baseline (arms/base.json), by seed."""
import json, sys, math
from pathlib import Path
import numpy as np
from scipy import stats
S = Path(__file__).resolve().parent
def load(n): return {r["seed"]: r for r in json.loads((S/"arms"/f"{n}.json").read_text())["rows"]}
def ci(d):
    d = np.asarray(d, float); n = len(d)
    if n < 2: return float(d.mean()), float("nan"), float("nan")
    h = stats.t.ppf(0.975, n - 1) * d.std(ddof=1) / math.sqrt(n)
    return float(d.mean()), float(d.mean() - h), float(d.mean() + h)
def metrics(r):
    dg = r.get("diag", {})
    return {"flow": r["exit_end_flow_vph"], "vmin": r["station_speed_min_ms"],
            "ent_dep": r["entrance_departed"][0] / r["entrance_departed"][1],
            "coll": r["n_collisions"], "hb9": r["hard_brake_vehicle_steps"],
            "gaveup": r["exits_given_up"][0], "pass": int(r["pass"]),
            "geh_ok": int(r["criteria"]["ii_a_flow_geh"]), "strand_s": dg.get("strand_s", float("nan")),
            "unfin": dg.get("unfinished", float("nan")), "pairrel": dg.get("pair_releases", float("nan"))}
base = load(sys.argv[1] if len(sys.argv) > 2 else "base")
names = sys.argv[2:] if len(sys.argv) > 2 else sys.argv[1:]
print(f"{'arm':14s} {'n':>2s} {'flow':>7s} {'dFlow [95% CI]':>24s} {'dVmin [95% CI]':>22s} {'dEntDep':>8s} {'coll':>4s} {'hb9':>4s} {'gaveup':>6s} {'strand_s':>8s} {'GEH<5':>5s} {'pass':>4s}")
for n in names:
    arm = load(n); seeds = sorted(set(arm) & set(base))
    A = [metrics(arm[s]) for s in seeds]; B = [metrics(base[s]) for s in seeds]
    df = ci([a["flow"] - b["flow"] for a, b in zip(A, B)])
    dv = ci([a["vmin"] - b["vmin"] for a, b in zip(A, B)])
    de = ci([a["ent_dep"] - b["ent_dep"] for a, b in zip(A, B)])
    m = lambda k, X: sum(x[k] for x in X)
    print(f"{n:14s} {len(seeds):2d} {np.mean([a['flow'] for a in A]):7.0f} {df[0]:+7.0f} [{df[1]:+6.0f},{df[2]:+6.0f}] {dv[0]:+6.2f} [{dv[1]:+5.2f},{dv[2]:+5.2f}] {de[0]:+8.3f} {m('coll',A):4d} {m('hb9',A):4d} {m('gaveup',A):3d}/{m('gaveup',B):<3d} {np.mean([a['strand_s'] for a in A]):8.1f} {m('geh_ok',A):2d}/{m('geh_ok',B):<2d} {m('pass',A):4d}")
