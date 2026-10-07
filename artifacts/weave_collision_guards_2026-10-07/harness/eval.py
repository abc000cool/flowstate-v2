"""Amendment W2's criteria G1-G7, D1 and the reference reproduction (docs/I94_CAL_COLLISIONS.md §13.5).

usage (repository root): eval.py DIR

DIR holds, per set and arm, ``<set>_<arm>.json`` (rows of ``merge_model_selfcheck.py`` / ``repro.py``)
and ``<set>_<arm>_post.json`` (``post.py``), for set in s1, s2, s3a, s3b, xr1, xr2, xt1, xt2 and arm in
ref, w2. Writes DIR/criteria.json. G0 (byte identity) is read from identity_*.json and
scenario_hashes_*.json in the same directory.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import stats

D = Path(sys.argv[1])
W1B = Path("artifacts/weave_loss_2026-10-07/w1b")
SETS_S = ("s1", "s2", "s3a", "s3b")
SETS_X = ("xr1", "xr2", "xt1", "xt2")
W2_COUNTERS = ("n_handback_skips", "n_close_leader_withheld", "n_opposing_deferred", "n_opposing_vetoed")


def key_of(r: dict, s: str) -> tuple[str, int]:
    return (r.get("fixture", "th52"), int(r["seed"]))


def load(s: str, arm: str) -> tuple[dict, dict] | None:
    f, fp = D / f"{s}_{arm}.json", D / f"{s}_{arm}_post.json"
    if not (f.exists() and fp.exists()):
        return None
    rows = {key_of(r, s): r for r in json.loads(f.read_text())["rows"]}
    post = {}
    for r in json.loads(fp.read_text()):
        fx = "th52" if s == "s1" else r["fixture"]
        post[(fx, int(r["seed"]))] = r
    assert set(rows) == set(post), (s, arm, sorted(set(rows) ^ set(post))[:5])
    return rows, post


def ci(d: list[float]) -> list[float]:
    a = np.asarray(d, float)
    n = len(a)
    sd = a.std(ddof=1) if n > 1 else 0.0
    h = stats.t.ppf(0.975, n - 1) * sd / math.sqrt(n) if sd > 0 else 0.0
    return [float(a.mean()), float(a.mean() - h), float(a.mean() + h)]


def band(ref: float) -> float:
    return ref + 2.0 + 2.0 * math.sqrt(2.0 * ref)


def given_up(post_row: dict) -> int:
    return sum(z["n_missed_exit"] or 0 for z in post_row["sections"])


def mech(post_row: dict) -> list[str]:
    return [c["mechanism"] for c in post_row["collisions"]]


arms: dict[str, dict[str, tuple[dict, dict]]] = {}
for s in SETS_S + SETS_X:
    for arm in ("ref", "w2"):
        got = load(s, arm)
        if got is not None:
            arms.setdefault(s, {})[arm] = got
out: dict = {"sets": {}, "criteria": {}, "diagnostics": {}}

# --- per-set summaries ---------------------------------------------------------------
for s, a in arms.items():
    summ: dict = {}
    for arm, (rows, post) in a.items():
        keys = sorted(rows)
        secs = [z for k in keys for z in post[k]["sections"]]
        summ[arm] = {
            "runs": len(keys),
            "collisions": sum(post[k]["n_collisions"] for k in keys),
            "collision_mechanisms": [f"{k[0]}:{k[1]}:{m}" for k in keys for m in mech(post[k])],
            "hard_brake": sum(post[k]["hard_brake"] for k in keys),
            "exits_given_up": sum(given_up(post[k]) for k in keys),
            "departed": sum(post[k]["departed"] for k in keys),
            "locks": [f"{k[0]}:{k[1]}:lane{lk['lane']}:{lk['kind']}:{lk['len_s']}" for k in keys
                      for z in post[k]["sections"] for lk in z["locks"]],
            "lock_flags": sorted(f"{k[0]}:{k[1]}" for k in keys if rows[k].get("lock")),
            "counters": {c: sum(z.get(c) or 0 for z in secs) for c in W2_COUNTERS},
            "runs_with_activity": sum(
                1 for k in keys if any((z.get(c) or 0) > 0 for z in post[k]["sections"] for c in W2_COUNTERS)
            ),
        }
    out["sets"][s] = summ

# --- reproduction of W1b's reference rows --------------------------------------------
repro: dict = {}
for s, f in (("s1", "s1_ref.json"), ("s2", "s2_ref.json"), ("s3a", "s3a_ref.json"), ("s3b", "s3b_ref.json")):
    if s not in arms or not (W1B / f).exists():
        continue
    old = {key_of(r, s): r for r in json.loads((W1B / f).read_text())["rows"]}
    new = arms[s]["ref"][0]
    diffs = []
    n_fields = 0
    for k in sorted(old):
        if k not in new:
            diffs.append(f"{k}: missing")
            continue
        for fld, v in old[k].items():
            if fld in ("config_hash", "wall_s"):
                continue
            n_fields += 1
            if new[k].get(fld) != v:
                diffs.append(f"{k[0]}:{k[1]}:{fld}")
    repro[s] = {"runs": len(old), "fields": n_fields, "differ": diffs[:20], "n_differ": len(diffs)}
out["diagnostics"]["reference_reproduces_w1b"] = repro

# --- G1 --------------------------------------------------------------------------------
g1 = {s: out["sets"][s]["w2"]["collisions"] for s in SETS_S if s in arms}
out["criteria"]["G1"] = {"pass": all(v == 0 for v in g1.values()) and len(g1) == 4, "w2_collisions": g1}

# --- G2 --------------------------------------------------------------------------------
repro_runs, failing = [], []
for s, a in arms.items():
    _, pr = a["ref"]
    _, pw = a["w2"]
    for k in sorted(pr):
        ms = mech(pr[k])
        if any(m in ("R", "T") for m in ms):
            repro_runs.append({"run": f"{s}:{k[0]}:{k[1]}", "ref_mechanisms": ms,
                               "w2_collisions": pw[k]["n_collisions"], "w2_mechanisms": mech(pw[k])})
            if pw[k]["n_collisions"] != 0:
                failing.append(f"{s}:{k[0]}:{k[1]}")
out["criteria"]["G2"] = {
    "testable": bool(repro_runs),
    "pass": (not failing) if repro_runs else None,
    "reproducing_runs": repro_runs,
    "failing": failing,
}

# --- G3, G4a (S1) ----------------------------------------------------------------------
if "s1" in arms:
    (rr, pr), (rw, pw) = arms["s1"]["ref"], arms["s1"]["w2"]
    keys = sorted(rr)
    flow = ci([rw[k]["exit_end_flow_vph"] - rr[k]["exit_end_flow_vph"] for k in keys])
    out["criteria"]["G3"] = {"pass": flow[1] > -50.0, "paired_flow_w2_minus_ref": flow,
                             "ref_mean": float(np.mean([rr[k]["exit_end_flow_vph"] for k in keys])),
                             "w2_mean": float(np.mean([rw[k]["exit_end_flow_vph"] for k in keys]))}
    gu = ci([rw[k]["exits_given_up"][0] - rr[k]["exits_given_up"][0] for k in keys])
    g4a = {"pass": gu[2] <= 1.5, "paired_given_up_per_run": gu,
           "ref_total": sum(rr[k]["exits_given_up"][0] for k in keys),
           "w2_total": sum(rw[k]["exits_given_up"][0] for k in keys)}
else:
    g4a = {"pass": None}

# --- G4b, G5 ---------------------------------------------------------------------------
def pooled(sets: tuple[str, ...], what: str) -> dict:
    ref = sum(out["sets"][s]["ref"][what] for s in sets if s in arms)
    w2 = sum(out["sets"][s]["w2"][what] for s in sets if s in arms)
    return {"ref": ref, "w2": w2, "bound": band(ref), "pass": w2 <= band(ref)}


g4b = {"s2": pooled(("s2",), "exits_given_up"), "s3": pooled(("s3a", "s3b"), "exits_given_up")}
out["criteria"]["G4"] = {"pass": bool(g4a["pass"]) and all(v["pass"] for v in g4b.values()), "a": g4a, "b": g4b}
g5 = {"s1": pooled(("s1",), "hard_brake"), "s2": pooled(("s2",), "hard_brake"),
      "s3": pooled(("s3a", "s3b"), "hard_brake")}
out["criteria"]["G5"] = {"pass": all(v["pass"] for v in g5.values()), **g5}

# --- G6 --------------------------------------------------------------------------------
new_locks, new_flags = [], []
for s, a in arms.items():
    (rr, pr), (rw, pw) = a["ref"], a["w2"]
    for k in sorted(pw):
        if not pw[k]["sections"]:
            continue
        if any(z["locks"] for z in pw[k]["sections"]) and not any(z["locks"] for z in pr[k]["sections"]):
            new_locks.append(f"{s}:{k[0]}:{k[1]}")
        if rw[k].get("lock") and not rr[k].get("lock"):
            new_flags.append(f"{s}:{k[0]}:{k[1]}")
out["criteria"]["G6"] = {"pass": not new_locks and not new_flags, "new_locks": new_locks, "new_flags": new_flags}

# --- G7 --------------------------------------------------------------------------------
xs = [s for s in SETS_X if s in arms]
rt_w2 = [m for s in xs for m in out["sets"][s]["w2"]["collision_mechanisms"] if m.endswith((":R", ":T"))]
c_ref = sum(out["sets"][s]["ref"]["collisions"] for s in xs)
c_w2 = sum(out["sets"][s]["w2"]["collisions"] for s in xs)
out["criteria"]["G7"] = {"pass": len(xs) == 4 and not rt_w2 and c_w2 <= c_ref, "w2_r_or_t": rt_w2,
                         "collisions_ref": c_ref, "collisions_w2": c_w2,
                         "by_set": {s: {a: out["sets"][s][a]["collision_mechanisms"] for a in ("ref", "w2")}
                                    for s in xs}}

# --- D1 --------------------------------------------------------------------------------
checked, mism = 0, []
for s, a in arms.items():
    (_, pr), (_, pw) = a["ref"], a["w2"]
    for k in sorted(pw):
        if any((z.get(c) or 0) > 0 for z in pw[k]["sections"] for c in W2_COUNTERS):
            continue
        checked += 1
        if pw[k]["parquet_sha"] != pr[k]["parquet_sha"] or pw[k]["meta_digest"] != pr[k]["meta_digest"]:
            mism.append(f"{s}:{k[0]}:{k[1]}")
out["diagnostics"]["D1_inert_runs"] = {"runs_without_activity": checked, "not_identical": mism}

# --- G0 (read from the identity files) ------------------------------------------------
try:
    h = json.loads((D / "identity_head.json").read_text())
    c = json.loads((D / "identity_change.json").read_text())
    same = [k for k in h if {x: y for x, y in h[k].items() if x != "wall_s"}
            == {x: y for x, y in c.get(k, {}).items() if x != "wall_s"}]
    sh = json.loads((D / "scenario_hashes_head.json").read_text())
    sc = json.loads((D / "scenario_hashes_change.json").read_text())
    ks = [k for k in sh if not k.startswith("_")]
    out["criteria"]["G0"] = {"pass": len(same) == len(h) == 37 and all(sh[k] == sc[k] for k in ks)
                             and sh["_weave_defaults"] == sc["_weave_defaults"],
                             "identical_cases": len(same), "cases": len(h), "scenarios": len(ks),
                             "scenarios_same": sum(sh[k] == sc[k] for k in ks)}
except FileNotFoundError as e:
    out["criteria"]["G0"] = {"pass": None, "missing": str(e)}

(D / "criteria.json").write_text(json.dumps(out, indent=1, default=float))
for k, v in out["criteria"].items():
    print(k, json.dumps(v, default=float)[:600])
print("reproduction", json.dumps(repro))
print("D1", json.dumps(out["diagnostics"]["D1_inert_runs"])[:400])
for s, v in out["sets"].items():
    print(s, {a: {kk: v[a][kk] for kk in ("runs", "collisions", "hard_brake", "exits_given_up", "counters",
                                           "runs_with_activity")} for a in v})
