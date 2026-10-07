"""Decomposition of weave-fixture runs (batch W and its levers).
Usage: uv run --no-sync python analyze_w.py RUNS.jsonl OUT.json [--base TAG]"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict

import numpy as np

from analyze_g import ci, pct, _f

T_LO, T_HI = 300.0, 1200.0


def flows(r, s):
    rows = [c for c in r["cross"][s] if T_LO <= c[0] < T_HI]
    h = (T_HI - T_LO) / 3600.0
    by = defaultdict(int)
    for c in rows:
        by[c[1]] += 1
    return len(rows) / h, {k: v / h for k, v in sorted(by.items())}


def join(r):
    reasons = defaultdict(list)
    for x in r.get("lc_reasons") or []:
        reasons[x["id"]].append(x)
    out = []
    for ev in r["lc"]:
        if not (T_LO <= ev["t"] < T_HI):
            continue
        cand = [x for x in reasons.get(ev["vid"], []) if abs(float(x["time"]) - (ev["t"] - 0.5)) < 0.26]
        rs = cand[0] if cand else {}
        out.append({**ev, "reason": rs.get("reason"), "lsg": _f(rs.get("leaderSecureGap")), "fsg": _f(rs.get("followerSecureGap"))})
    return out


def mv_summary(ev, x0):
    if not ev:
        return None
    v = [e["v"] for e in ev]
    pos = [e["x"] - x0 for e in ev]
    rs = defaultdict(int)
    for e in ev:
        rs[e["reason"]] += 1
    fol = [e for e in ev if e.get("fol_id")]
    return {
        "n": len(ev), "pos_p10_p50_p90": [pct(pos, q) for q in (10, 50, 90)], "v_p10_p50_p90": [pct(v, q) for q in (10, 50, 90)],
        "share_v_lt_5": float(np.mean(np.array(v) < 5.0)), "reasons": dict(rs),
        "lead_tgap_p10_p50": [pct([e["lead_gap"] / e["v"] for e in ev if e["lead_gap"] is not None and e["v"] > 1], q) for q in (10, 50)],
        "lag_tgap_p10_p50": [pct([e["fol_gap"] / e["fol_v0"] for e in fol if e.get("fol_v0", 0) > 1], q) for q in (10, 50)],
        "changer_minus_newfol_p50": pct([e["v"] - e["fol_v0"] for e in fol], 50),
        "newlead_minus_changer_p50": pct([e["lead_v"] - e["v"] for e in ev if e.get("lead_v") is not None], 50),
        "fol_gap_ratio_eq_p50": pct([e["fol_gap"] / (e["fol_s0"] + e["fol_v0"] * e["fol_T"]) for e in fol], 50),
        "fol_amin_p10": pct([e.get("fol_amin") for e in fol], 10),
        "fol_share_brake_gt_b": float(np.mean([e["fol_amin"] < -e["fol_b"] for e in fol if e.get("fol_amin") is not None])) if fol else None,
        "fol_dv_p50_p90": [pct([e["fol_v0"] - e["fol_vmin"] for e in fol if e.get("fol_vmin") is not None], q) for q in (50, 90)],
    }


def main():
    path, outp = sys.argv[1], sys.argv[2]
    args = sys.argv[3:]
    base = args[args.index("--base") + 1] if "--base" in args else None
    runs = defaultdict(list)
    for line in open(path):
        r = json.loads(line)
        runs[r["tag"]].append(r)
    res = {}
    for tag, rr in runs.items():
        rr.sort(key=lambda r: r["seed"])
        geo = rr[0]["geo"]
        x0, x1 = geo["x_merge"], geo["x_acc_end"]
        per = {}
        allev = []
        for r in rr:
            rec = {}
            for s in r["cross"]:
                q, by = flows(r, s)
                rec[s], rec[s + "_lane"] = q, by
            rec["coll"] = r["n_collisions"]
            rec["hard45"] = r["hard_steps"]["lt_-4.5"]
            rec["hard89"] = r["hard_steps"]["lt_-8.9"]
            rec["departed_share"] = r["n_departed"] / r["n_planned"]
            per[r["seed"]] = rec
            allev.extend(join(r))
        a = {"seeds": sorted(per), "per_seed": per}
        for s in rr[0]["cross"]:
            a[s] = ci([p[s] for p in per.values()])
            lanes = sorted({ln for p in per.values() for ln in p[s + "_lane"]})
            a[s + "_lane"] = {ln: ci([p[s + "_lane"].get(ln, 0.0) for p in per.values()]) for ln in lanes}
        a["collisions"] = sum(p["coll"] for p in per.values())
        a["hard45_per_run"] = float(np.mean([p["hard45"] for p in per.values()]))
        a["hard89_total"] = sum(p["hard89"] for p in per.values())
        a["departed_share"] = float(np.mean([p["departed_share"] for p in per.values()]))
        ent = [e for e in allev if e["kind"] == "ramp" and e["edge"] == "e1" and e["from"] == 0 and e["to"] == 1]
        exi_w = [e for e in allev if e["kind"] == "exit" and e["edge"] == "e1" and e["to"] < e["from"]]
        exi_up = [e for e in allev if e["kind"] == "exit" and e["edge"] == "e0" and e["to"] < e["from"]]
        thr_w = [e for e in allev if e["kind"] == "main" and e["edge"] == "e1"]
        thr_up = [e for e in allev if e["kind"] == "main" and e["edge"] == "e0" and e["x"] > x0 - 1000.0]
        nr = len(rr) * (T_HI - T_LO) / 3600.0
        a["moves_per_hour"] = {k: len(v) / nr for k, v in (("entrants_in_weave", ent), ("exiters_in_weave", exi_w),
                                                           ("exiters_upstream_rightward", exi_up), ("through_in_weave", thr_w),
                                                           ("through_last_1km_upstream", thr_up))}
        a["entrants"] = mv_summary(ent, x0)
        a["exiters_weave"] = mv_summary(exi_w, x0)
        a["exiters_upstream"] = mv_summary(exi_up, x0)
        a["through_weave"] = mv_summary(thr_w, x0)
        a["through_upstream_1km"] = mv_summary(thr_up, x0)
        res[tag] = a
    if base and base in res:
        b = res[base]
        for tag, a in res.items():
            if tag == base:
                continue
            common = sorted(set(a["seeds"]) & set(b["seeds"]))
            a["paired_vs_base"] = {s: ci([a["per_seed"][k][s] - b["per_seed"][k][s] for k in common])
                                   for s in ("up", "w_mid", "dn300", "dn1000")}
    json.dump(res, open(outp, "w"), indent=1, default=float)
    f = lambda c: "-" if c["mean"] is None else f'{c["mean"]:,.0f}' + (f' ± {c["hw"]:,.0f}' if c["hw"] else "")
    for tag, a in res.items():
        print(f"== {tag}: dn300 {f(a['dn300'])} | dn1000 {f(a['dn1000'])} | up {f(a['up'])} | w_mid {f(a['w_mid'])} | coll {a['collisions']} "
              f"| hard<-4.5/run {a['hard45_per_run']:.1f} | <-8.9 {a['hard89_total']} | departed {a['departed_share']:.3f}")
        print("   dn300 lanes", {k: f(v) for k, v in a["dn300_lane"].items()}, " up lanes", {k: f(v) for k, v in a["up_lane"].items()})
        if "paired_vs_base" in a:
            print("   paired vs base", {k: f(v) for k, v in a["paired_vs_base"].items()})


if __name__ == "__main__":
    main()
