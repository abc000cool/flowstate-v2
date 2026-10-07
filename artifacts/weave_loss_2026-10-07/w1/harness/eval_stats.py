"""F1-F4 from the two th52 arms (ref = without the key, w1 = entrant_giveup_m 5)."""
import json, math, sys
import numpy as np
from scipy import stats
E = sys.argv[1]
def load(n):
    rows = {r["seed"]: r for r in json.load(open(f"{E}/{n}.json"))["rows"]}
    post = {int(k): v for k, v in json.load(open(f"{E}/{n}_post.json")).items()}
    for s, r in rows.items():
        r["post"] = post[s]
    return rows
ref, w1 = load("ref"), load("w1")
seeds = sorted(set(ref) & set(w1)); assert len(seeds) == 20, seeds
def ci(d):
    d = np.asarray(d, float); n = len(d); h = stats.t.ppf(0.975, n - 1) * d.std(ddof=1) / math.sqrt(n)
    return d.mean(), d.mean() - h, d.mean() + h
def col(rows, f): return [f(rows[s]) for s in seeds]
flow = lambda r: r["exit_end_flow_vph"]
vmin = lambda r: r["station_speed_min_ms"]
entdep = lambda r: r["entrance_departed"][0] / r["entrance_departed"][1]
maindep = lambda r: r["mainline_departed"][0] / r["mainline_departed"][1]
strand_s = lambda r: r["post"]["strand"]["strand_s"]
out = {}
for name, f in (("flow", flow), ("vmin", vmin), ("entrance_departed_share", entdep), ("mainline_departed_share", maindep), ("strand_s", strand_s)):
    a, b = col(w1, f), col(ref, f)
    out[name] = {"w1_mean": float(np.mean(a)), "ref_mean": float(np.mean(b)), "paired": [float(x) for x in ci(np.subtract(a, b))]}
S = lambda rows, f: sum(col(rows, f))
summ = {}
for n, rows in (("ref", ref), ("w1", w1)):
    summ[n] = {
        "collisions": S(rows, lambda r: r["n_collisions"]),
        "hard_brake": S(rows, lambda r: r["hard_brake_vehicle_steps"]),
        "exits_given_up": S(rows, lambda r: r["exits_given_up"][0]),
        "exits_reached": S(rows, lambda r: r["exits_given_up"][1]),
        "entrants_took_exit": S(rows, lambda r: r["entrants_took_exit"][0] or 0),
        "entrance_departed": S(rows, lambda r: r["entrance_departed"][0]),
        "max_entrant_share": max((r["entrants_took_exit"][0] or 0) / r["entrance_departed"][0] for r in rows.values()),
        "per_seed_entrants_took_exit": col(rows, lambda r: r["entrants_took_exit"][0]),
        "geh_ok": S(rows, lambda r: int(r["criteria"]["ii_a_flow_geh"])),
        "all_pass": S(rows, lambda r: int(r["pass"])),
        "locks": S(rows, lambda r: int(r["post"]["lock"])),
        "strand_eps": S(rows, lambda r: r["post"]["strand"]["n_strand_eps"]),
        "longest_strand_s": max(col(rows, lambda r: r["post"]["strand"]["longest_strand_s"])),
        "abreast_pairs_ge5s_mean": float(np.mean(col(rows, lambda r: r["post"]["strand"]["n_abreast_pairs_ge5s"]))),
        "unfinished": S(rows, lambda r: r["post"]["strand"]["unfinished"]),
        "params": rows[seeds[0]]["post"]["params"],
        "flow_mean": float(np.mean(col(rows, flow))), "flow_sd": float(np.std(col(rows, flow), ddof=1)),
    }
out["summary"] = summ
r, w = summ["ref"], summ["w1"]
crit = {
    "F1": out["flow"]["paired"][1] > 0,
    "F2": out["strand_s"]["w1_mean"] <= 9.5,
    "F3": w["max_entrant_share"] <= 0.01,
    "F4_collisions": w["collisions"] == 0,
    "F4_hard_brake": w["hard_brake"] <= r["hard_brake"] + 2,
    "F4_given_up_vs_ref": w["exits_given_up"] <= r["exits_given_up"],
    "F4_given_up_vs_66": w["exits_given_up"] <= 66,
}
out["criteria"] = crit
out["per_seed"] = [{"seed": s, "flow_ref": ref[s]["exit_end_flow_vph"], "flow_w1": w1[s]["exit_end_flow_vph"],
                    "strand_ref": strand_s(ref[s]), "strand_w1": strand_s(w1[s]),
                    "took_exit": w1[s]["entrants_took_exit"][0], "ent_dep": w1[s]["entrance_departed"][0],
                    "gave_up_ref": ref[s]["exits_given_up"][0], "gave_up_w1": w1[s]["exits_given_up"][0],
                    "hb_ref": ref[s]["hard_brake_vehicle_steps"], "hb_w1": w1[s]["hard_brake_vehicle_steps"],
                    "coll_w1": w1[s]["n_collisions"]} for s in seeds]
json.dump(out, open(f"{E}/f1_f4.json", "w"), indent=1, default=float)
print(json.dumps({k: out[k] for k in ("flow", "vmin", "entrance_departed_share", "mainline_departed_share", "strand_s", "criteria")}, indent=1, default=float))
print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk not in ("params",)} for k, v in summ.items()}, default=float))
for p in out["per_seed"]: print(p)
