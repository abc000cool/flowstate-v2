"""Amendment W1b's criteria B0, B1, F1b, F3b, F4, F5b, L1 (docs/WEAVE_LOSS_DIAGNOSIS.md §10.5).

usage: eval.py DIR   (DIR holds <set>_<arm>.json from merge_model_selfcheck and <set>_<arm>_post.json
from post.py, for set in s1, s2, s3 and arm in ref, w1b; s3 may be split into s3a/s3b) -> DIR/criteria.json
"""

import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import stats

D = Path(sys.argv[1])


def family(fixture: str) -> str:
    if fixture.startswith("ruth"):
        return "Ruth St"
    if fixture.startswith("th52"):
        return "T.H.52"
    if fixture == "th61":
        return "T.H.61"
    if fixture in ("weave_moderate", "weave_golden"):
        return "golden weave (moderate + golden)"
    return fixture


def load(stem: str, arm: str) -> tuple[dict, dict]:
    """(selfcheck rows, post rows), both keyed by (fixture, seed)."""
    rows, post = {}, {}
    parts = [stem] if (D / f"{stem}_{arm}.json").exists() else [f"{stem}a", f"{stem}b"]
    for p in parts:
        sc = json.loads((D / f"{p}_{arm}.json").read_text())
        for r in sc["rows"]:
            rows[(r.get("fixture", "th52"), r["seed"])] = r
        for r in json.loads((D / f"{p}_{arm}_post.json").read_text()):
            post[(r["fixture"], r["seed"])] = r
    assert set(rows) == set(post), (stem, arm, set(rows) ^ set(post))
    return rows, post


def ci(d: list[float]) -> list[float]:
    a = np.asarray(d, float)
    n = len(a)
    sd = a.std(ddof=1)
    h = stats.t.ppf(0.975, n - 1) * sd / math.sqrt(n) if sd > 0 else 0.0
    return [float(a.mean()), float(a.mean() - h), float(a.mean() + h)]


sets = {s: {arm: load(s, arm) for arm in ("ref", "w1b")} for s in ("s1", "s2", "s3")}
out: dict = {"sets": {}, "criteria": {}}

# --- per-set summaries -----------------------------------------------------------
for s, arms in sets.items():
    (rr, pr), (rw, pw) = arms["ref"], arms["w1b"]
    assert set(rr) == set(rw), s
    keys = sorted(rr)
    summ: dict = {"n_runs": len(keys)}
    for arm, rows, post in (("ref", rr, pr), ("w1b", rw, pw)):
        secs = [(k, z) for k in keys for z in post[k]["sections"]]
        summ[arm] = {
            "collisions": sum(post[k]["n_collisions"] for k in keys),
            "hard_brake": sum(rows[k]["hard_brake_vehicle_steps"] for k in keys),
            "departed": sum(post[k]["departed"] for k in keys),
            "releases": sum((z["n_entrant_took_exit"] or 0) for _, z in secs),
            "exits_given_up": sum((z["n_missed_exit"] or 0) for _, z in secs),
            "runs_with_release": sorted(f"{k[0]}:{k[1]}" for k, z in secs if (z["n_entrant_took_exit"] or 0) > 0),
            "locks": [{"run": f"{k[0]}:{k[1]}", **lk} for k, z in secs for lk in z["locks"]],
            "flags": sorted(f"{k[0]}:{k[1]}" for k in keys if rows[k]["lock"]),
            "longest_stand_s": max((z["longest_stand_s"] for _, z in secs), default=0.0),
            "stands_ge_30s": sorted(x for _, z in secs for x in z["stands_ge_30s"]),
            "strand_s_mean": float(np.mean([z["strand_s"] for _, z in secs])) if secs else None,
        }
        fam: dict = {}
        for k, z in secs:
            f = fam.setdefault(family(k[0]), {"runs": 0, "releases": 0, "entrance_departed": 0})
            f["runs"] += 1
            f["releases"] += z["n_entrant_took_exit"] or 0
            f["entrance_departed"] += z["entrance_departed"]
        for f in fam.values():
            f["share"] = f["releases"] / f["entrance_departed"] if f["entrance_departed"] else None
        summ[arm]["by_section"] = fam
    out["sets"][s] = summ

# --- B0 ----------------------------------------------------------------------------
rel_all, longest = [], 0.0
for s in sets:
    _, pw = sets[s]["w1b"]
    for k, r in pw.items():
        for z in r["sections"]:
            longest = max(longest, z["longest_stand_s"])
            rel_all += [{"run": f"{s}:{k[0]}:{k[1]}", **x} for x in z["releases"]]
b0a = all(x["stand_len_s"] >= 59.5 - 1e-9 for x in rel_all)
b0b = longest <= 61.0 + 1e-9
out["criteria"]["B0"] = {"pass": b0a and b0b, "a_pass": b0a, "b_pass": b0b, "n_releases": len(rel_all),
                         "release_stands_s": sorted(x["stand_len_s"] for x in rel_all),
                         "longest_stand_w1b_s": longest, "releases": rel_all}

# --- B1 ----------------------------------------------------------------------------
checked, mism = 0, []
for s in sets:
    _, pr = sets[s]["ref"]
    _, pw = sets[s]["w1b"]
    for k in pw:
        if any((z["n_entrant_took_exit"] or 0) > 0 for z in pw[k]["sections"]):
            continue
        checked += 1
        same_pq = pw[k]["parquet_sha"] == pr[k]["parquet_sha"]
        same_meta = pw[k]["meta_digest"] == pr[k]["meta_digest"]
        if not (same_pq and same_meta):
            mism.append({"run": f"{s}:{k[0]}:{k[1]}", "parquet": same_pq, "meta": same_meta})
out["criteria"]["B1"] = {"pass": not mism, "runs_without_release": checked, "mismatches": mism}

# --- F1b, F4 (S1) ------------------------------------------------------------------
(rr, pr), (rw, pw) = sets["s1"]["ref"], sets["s1"]["w1b"]
keys = sorted(rr)
assert len(keys) == 20
flow = ci([rw[k]["exit_end_flow_vph"] - rr[k]["exit_end_flow_vph"] for k in keys])
out["criteria"]["F1b"] = {"pass": flow[2] >= 0.0, "paired_flow_w1b_minus_ref": flow,
                          "ref_mean": float(np.mean([rr[k]["exit_end_flow_vph"] for k in keys])),
                          "w1b_mean": float(np.mean([rw[k]["exit_end_flow_vph"] for k in keys]))}
s1r, s1w = out["sets"]["s1"]["ref"], out["sets"]["s1"]["w1b"]
gu_r = sum(rr[k]["exits_given_up"][0] for k in keys)
gu_w = sum(rw[k]["exits_given_up"][0] for k in keys)
out["criteria"]["F4"] = {
    "pass": s1w["collisions"] == 0 and s1w["hard_brake"] <= s1r["hard_brake"] + 2 and gu_w <= gu_r,
    "collisions_w1b": s1w["collisions"], "hard_brake_ref": s1r["hard_brake"], "hard_brake_w1b": s1w["hard_brake"],
    "exits_given_up_ref": gu_r, "exits_given_up_w1b": gu_w}

# --- F3b -----------------------------------------------------------------------------
f3 = {}
dep_s1 = sum(rw[k]["entrance_departed"][0] for k in keys)
rel_s1 = sum((rw[k]["entrants_took_exit"][0] or 0) for k in keys)
f3["s1"] = {"releases": rel_s1, "entrance_departed": dep_s1, "share": rel_s1 / dep_s1}
for s in ("s2", "s3"):
    for fam, f in out["sets"][s]["w1b"]["by_section"].items():
        f3[f"{s}:{fam}"] = f
out["criteria"]["F3b"] = {"pass": all((v["share"] or 0.0) <= 0.01 for v in f3.values()), "pools": f3}

# --- F5b -----------------------------------------------------------------------------
f5 = {"collisions_w1b": {}, "new_locks": [], "new_flags": []}
ok = True
for s in ("s2", "s3"):
    (rr2, pr2), (rw2, pw2) = sets[s]["ref"], sets[s]["w1b"]
    c = sum(pw2[k]["n_collisions"] for k in pw2)
    f5["collisions_w1b"][s] = c
    ok &= c == 0
    for k in pw2:
        weave = bool(pw2[k]["sections"])
        if not weave:
            continue
        if any(z["locks"] for z in pw2[k]["sections"]) and not any(z["locks"] for z in pr2[k]["sections"]):
            f5["new_locks"].append(f"{s}:{k[0]}:{k[1]}")
        if rw2[k]["lock"] and not rr2[k]["lock"]:
            f5["new_flags"].append(f"{s}:{k[0]}:{k[1]}")
ok &= not f5["new_locks"] and not f5["new_flags"]
out["criteria"]["F5b"] = {"pass": ok, **f5}

# --- L1 ------------------------------------------------------------------------------
prone, still = [], []
for s in sets:
    _, pr3 = sets[s]["ref"]
    _, pw3 = sets[s]["w1b"]
    for k in pr3:
        if any(z["locks"] for z in pr3[k]["sections"]):
            prone.append(f"{s}:{k[0]}:{k[1]}")
            if any(z["locks"] for z in pw3[k]["sections"]):
                still.append(f"{s}:{k[0]}:{k[1]}")
out["criteria"]["L1"] = {"testable": bool(prone), "pass": (not still) if prone else None,
                         "lock_prone_runs": prone, "locked_in_w1b": still}

(D / "criteria.json").write_text(json.dumps(out, indent=1, default=float))
for c, v in out["criteria"].items():
    print(c, {k: v[k] for k in v if k not in ("releases", "pools")} if c != "F3b" else v)
for s, v in out["sets"].items():
    print(s, v["n_runs"], {a: {k: v[a][k] for k in ("collisions", "hard_brake", "releases", "exits_given_up",
                                                     "longest_stand_s", "flags", "runs_with_release")}
                           for a in ("ref", "w1b")})
