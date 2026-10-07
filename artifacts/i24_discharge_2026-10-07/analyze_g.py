"""Decomposition tables for oh4 runs (batch G and its levers).
Usage: uv run --no-sync python analyze_g.py RUNS.jsonl OUT.json [--base ARM] [--arms A,B,...]"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict

import numpy as np

T95 = {4: 2.776, 9: 2.262, 19: 2.093}
RED_END = 390.0


def ci(xs):
    xs = np.array([x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))], float)
    n = len(xs)
    if n == 0:
        return {"mean": None, "hw": None, "n": 0}
    hw = T95.get(n - 1, 1.96) * xs.std(ddof=1) / math.sqrt(n) if n > 1 else None
    return {"mean": float(xs.mean()), "hw": None if hw is None else float(hw), "n": n}


def window(r, settle=180.0):
    """Minutes from red end + settle with a vehicle < 8 m/s upstream of the census section."""
    sc = r["census"]
    mins = [i for i, n in enumerate(sc) if i * 60.0 >= RED_END + settle - 1e-9 and n > 0]
    return mins


def flow_minutes(r, name, mins):
    if not mins:
        return None, {}
    sel = set(mins)
    rows = [c for c in r["cross"][name] if int(c[0] // 60) in sel]
    h = len(mins) / 60.0
    by = defaultdict(int)
    for c in rows:
        by[c[1]] += 1
    return len(rows) / h, {k: v / h for k, v in sorted(by.items())}


def headways(r, name, mins):
    sel = set(mins)
    per = defaultdict(list)
    for c in r["cross"][name]:
        if int(c[0] // 60) in sel:
            per[c[1]].append(c[0])
    out = {}
    for ln, ts in per.items():
        ts = np.sort(np.array(ts))
        h = np.diff(ts)
        h = h[h < 30.0]  # gaps across excluded minutes
        out[ln] = h.tolist()
    return out


def lc_table(r, mins):
    """Lane-change events in the window, joined to SUMO's reasons (same vehicle, time within 0.5 s)."""
    sel = set(mins)
    reasons = defaultdict(list)
    for x in r.get("lc_reasons") or []:
        reasons[x["id"]].append(x)
    geo = r["geo"]
    rows = []
    for ev in r["lc"]:
        if int(ev["t"] // 60) not in sel:
            continue
        cand = [x for x in reasons.get(ev["vid"], []) if abs(float(x["time"]) - (ev["t"] - 0.5)) < 0.26]
        rs = cand[0] if cand else {}
        rows.append({**ev, "reason": rs.get("reason"), "lg": _f(rs.get("leaderGap")), "lsg": _f(rs.get("leaderSecureGap")),
                     "fg": _f(rs.get("followerGap")), "fsg": _f(rs.get("followerSecureGap")),
                     "fv": _f(rs.get("followerSpeed")), "lv": _f(rs.get("leaderSpeed")),
                     "x_rel_merge": ev["x"] - geo["x_merge"] if geo["x_merge"] is not None else None,
                     "x_rel_end": ev["x"] - geo["x_acc_end"] if geo["x_acc_end"] is not None else None})
    return rows


def _f(s):
    try:
        v = float(s)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def pct(a, q):
    a = np.array([x for x in a if x is not None], float)
    return None if len(a) == 0 else float(np.percentile(a, q))


def summarize_arm(runs):
    per_seed = {}
    pooled_lc = []
    pooled_hw = {"s2200": defaultdict(list), "s3200": defaultdict(list)}
    for r in runs:
        mins = window(r)
        rec = {"n_min": len(mins), "coll": r["n_collisions"], "planned": r["n_planned"], "departed": r["n_departed"]}
        for s in ("up", "acc_mid", "acc_end", "s2200", "s3200"):
            q, by = flow_minutes(r, s, mins)
            rec[s] = q
            rec[s + "_lane"] = by
        # hard braking outside the red transition at the light (t 300-330 s, x < merge + 50)
        hv = [h for h in r.get("hard_ev", []) if not (300.0 <= h[0] <= 330.0)]
        rec["hard_lt45"] = len(hv)
        rec["hard_lt89"] = sum(1 for h in hv if h[4] < -8.9)
        rec["hard_where"] = hv[:50]
        per_seed[r["seed"]] = rec
        for s in ("s2200", "s3200"):
            for ln, hs in headways(r, s, mins).items():
                pooled_hw[s][ln].extend(hs)
        pooled_lc.extend(lc_table(r, mins) if mins else [])
    return per_seed, pooled_lc, pooled_hw


def lc_summary(rows, geo, n_runs, minutes):
    ent = [x for x in rows if x["kind"] == "ramp" and x["edge"] == "e1" and x["from"] == 0 and x["to"] == 1]
    acc_len = geo["x_acc_end"] - geo["x_merge"]
    out = {"entrants": {}, "mainline": {}}
    if ent:
        pos = [x["x_rel_merge"] for x in ent]
        v = [x["v"] for x in ent]
        end50 = [x for x in ent if x["x_rel_end"] > -50.0]
        slow = [x for x in ent if x["v"] < 5.0]
        reasons = defaultdict(int)
        for x in ent:
            reasons[x["reason"]] += 1
        out["entrants"] = {
            "n_per_run_hour": len(ent) / n_runs / (minutes / 60.0),
            "pos_p10_p50_p90_m": [pct(pos, 10), pct(pos, 50), pct(pos, 90)], "acc_len_m": acc_len,
            "share_last_50m": len(end50) / len(ent), "share_v_lt_5": len(slow) / len(ent),
            "v_p10_p50_p90_ms": [pct(v, 10), pct(v, 50), pct(v, 90)],
            "reasons": dict(reasons),
            "lead_gap_p10_p50_m": [pct([x["lead_gap"] for x in ent], 10), pct([x["lead_gap"] for x in ent], 50)],
            "fol_gap_p10_p50_m": [pct([x["fol_gap"] for x in ent], 10), pct([x["fol_gap"] for x in ent], 50)],
            "lead_tgap_p10_p50_s": [pct([x["lead_gap"] / x["v"] for x in ent if x["lead_gap"] is not None and x["v"] > 1], 10),
                                    pct([x["lead_gap"] / x["v"] for x in ent if x["lead_gap"] is not None and x["v"] > 1], 50)],
            "fol_tgap_p10_p50_s": [pct([x["fol_gap"] / x["fol_v0"] for x in ent if x.get("fol_gap") is not None and x.get("fol_v0", 0) > 1], 10),
                                   pct([x["fol_gap"] / x["fol_v0"] for x in ent if x.get("fol_gap") is not None and x.get("fol_v0", 0) > 1], 50)],
            "entrant_minus_newfollower_ms_p50": pct([x["v"] - x["fol_v0"] for x in ent if x.get("fol_v0") is not None], 50),
            "newleader_minus_entrant_ms_p50": pct([x["lead_v"] - x["v"] for x in ent if x.get("lead_v") is not None], 50),
            "fol_gap_ratio_to_eq_p50": pct([x["fol_gap"] / (x["fol_s0"] + x["fol_v0"] * x["fol_T"]) for x in ent
                                             if x.get("fol_gap") is not None and x.get("fol_T") is not None], 50),
            "fol_amin_p10_p50": [pct([x.get("fol_amin") for x in ent if x.get("fol_id")], 10),
                                 pct([x.get("fol_amin") for x in ent if x.get("fol_id")], 50)],
            "fol_share_brake_gt_b": float(np.mean([x["fol_amin"] < -x["fol_b"] for x in ent
                                                   if x.get("fol_id") and x.get("fol_amin") is not None])) if ent else None,
            "fol_share_brake_gt_3": float(np.mean([x["fol_amin"] < -3.0 for x in ent
                                                   if x.get("fol_id") and x.get("fol_amin") is not None])) if ent else None,
            "fol_dv_p50_ms": pct([x["fol_v0"] - x["fol_vmin"] for x in ent if x.get("fol_vmin") is not None], 50),
        }
    # mainline (non-entering) changes by zone and reason
    zones = {"upstream (e0)": lambda x: x["edge"] == "e0", "acc zone (e1)": lambda x: x["edge"] == "e1",
             "downstream (e2)": lambda x: x["edge"] == "e2"}
    for zn, fn in zones.items():
        sel = [x for x in rows if fn(x) and not (x["kind"] == "ramp" and x["edge"] == "e1" and x["from"] == 0)]
        rs = defaultdict(int)
        for x in sel:
            d = "right" if x["to"] < x["from"] else "left"
            rs[f'{x["reason"]}:{d}'] += 1
        cut = [x for x in sel if x.get("fol_id")]
        out["mainline"][zn] = {
            "n_per_run_hour": len(sel) / n_runs / (minutes / 60.0),
            "reasons": dict(rs),
            "fol_share_brake_gt_b": float(np.mean([x["fol_amin"] < -x["fol_b"] for x in cut if x.get("fol_amin") is not None])) if cut else None,
            "fol_amin_p10": pct([x.get("fol_amin") for x in cut], 10),
            "fol_dv_p50": pct([x["fol_v0"] - x["fol_vmin"] for x in cut if x.get("fol_vmin") is not None], 50),
        }
    return out


def main():
    path, outp = sys.argv[1], sys.argv[2]
    args = sys.argv[3:]
    base = args[args.index("--base") + 1] if "--base" in args else None
    arms_f = args[args.index("--arms") + 1].split(",") if "--arms" in args else None
    runs = defaultdict(list)
    for line in open(path):
        r = json.loads(line)
        runs[r["tag"]].append(r)
    res = {}
    for arm, rr in runs.items():
        if arms_f and arm not in arms_f:
            continue
        rr.sort(key=lambda r: r["seed"])
        per_seed, lc_rows, hw = summarize_arm(rr)
        minutes = float(np.mean([p["n_min"] for p in per_seed.values()]))
        a = {"seeds": sorted(per_seed), "per_seed": per_seed}
        for s in ("up", "acc_end", "s2200", "s3200"):
            a[s] = ci([p[s] for p in per_seed.values()])
            lanes = sorted({ln for p in per_seed.values() for ln in p[s + "_lane"]})
            a[s + "_lane"] = {ln: ci([p[s + "_lane"].get(ln, 0.0) for p in per_seed.values() if p[s] is not None]) for ln in lanes}
        a["collisions"] = sum(p["coll"] for p in per_seed.values())
        a["hard_lt45_per_run"] = float(np.mean([p["hard_lt45"] for p in per_seed.values()]))
        a["hard_lt89_total"] = sum(p["hard_lt89"] for p in per_seed.values())
        a["n_min_mean"] = minutes
        a["headways"] = {s: {ln: {"n": len(h), "mean": float(np.mean(h)), "p10": pct(h, 10), "p50": pct(h, 50), "p90": pct(h, 90),
                                  "veh_h": 3600.0 / float(np.mean(h)), "share_lt_1.5": float(np.mean(np.array(h) < 1.5))}
                             for ln, h in sorted(hw[s].items()) if len(h) > 10} for s in hw}
        a["lc"] = lc_summary(lc_rows, rr[0]["geo"], len(rr), minutes) if lc_rows else None
        res[arm] = a
    if base and base in res:
        b = res[base]
        for arm, a in res.items():
            if arm == base:
                continue
            common = sorted(set(a["seeds"]) & set(b["seeds"]))
            a["paired_vs_" + base] = {}
            for s in ("s2200", "s3200", "up"):
                d = [a["per_seed"][k][s] - b["per_seed"][k][s] for k in common
                     if a["per_seed"][k][s] is not None and b["per_seed"][k][s] is not None]
                a["paired_vs_" + base][s] = ci(d)
    json.dump(res, open(outp, "w"), indent=1, default=float)
    for arm, a in res.items():
        f = lambda c: "-" if c["mean"] is None else f'{c["mean"]:,.0f}' + (f' ± {c["hw"]:,.0f}' if c["hw"] else "")
        print(f"== {arm}: s2200 {f(a['s2200'])} | s3200 {f(a['s3200'])} | up {f(a['up'])} | window {a['n_min_mean']:.1f} min | "
              f"coll {a['collisions']} | hard<-4.5 /run {a['hard_lt45_per_run']:.1f} | <-8.9 total {a['hard_lt89_total']}")
        print("   s2200 by lane:", {k: f(v) for k, v in a["s2200_lane"].items()}, " up by lane:", {k: f(v) for k, v in a["up_lane"].items()})
        for k, v in a.items():
            if k.startswith("paired_vs_"):
                print("  ", k, {s: f(c) for s, c in v.items()})


if __name__ == "__main__":
    main()
