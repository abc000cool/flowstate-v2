"""DS (downstream-end fixture) tables: 2-h flows per section against the replica, per-window
comparison, per-lane flows, paired lever differences, safety counters, congestion content, and the
lane-change decomposition in the diverge and the weave.
Usage: uv run --no-sync python analyze_ds.py RUNS.jsonl OUT.json [--base k1]"""

from __future__ import annotations

import json
import sys
from collections import defaultdict

import numpy as np

from analyze_g import ci, pct, _f

REPO = "/Users/anshpathak/Desktop/apps/flowstate/artifacts/"
SECS = ("s2200", "s3200", "s4800", "s5400", "sbuf")
REPL_IDX = {"s2200": 2, "s3200": 3, "s4800": 4, "s5400": 5}


def windows(r, s):
    t = np.array([c[0] for c in r["cross"][s]])
    return np.array([((t >= 600 + 300 * i) & (t < 900 + 300 * i)).sum() * 12.0 for i in range(24)])


def lane_flows(r, s):
    by = defaultdict(int)
    for c in r["cross"][s]:
        if 600 <= c[0] < 7800:
            by[c[1]] += 1
    return {k: v / 2.0 for k, v in sorted(by.items())}


def congested_share(r, x_lo, x_hi, thr_kmh=40.0):
    """Share of 50-m x 300-s bins (all lanes pooled, vehicle-weighted) in [x_lo, x_hi) and the study
    window whose mean speed is below thr."""
    f = np.nan_to_num(np.array(r["field"], float))
    n = np.array(r["field_n"], float)
    dx = r["field_dx"]
    b0, b1 = int(x_lo // dx), int(x_hi // dx)
    t0, t1 = 2, 26  # 300-s bins 600-7800 s
    num = (f[t0:t1, b0:b1] * n[t0:t1, b0:b1]).sum(axis=2)
    den = n[t0:t1, b0:b1].sum(axis=2)
    v = np.where(den > 0, num / np.maximum(den, 1), np.nan) * 3.6
    ok = ~np.isnan(v)
    return float((v[ok] < thr_kmh).mean()), v


def join(r):
    reasons = defaultdict(list)
    for x in r.get("lc_reasons") or []:
        reasons[x["id"]].append(x)
    out = []
    for ev in r["lc"]:
        if not (600 <= ev["t"] < 7800):
            continue
        cand = [x for x in reasons.get(ev["vid"], []) if abs(float(x["time"]) - (ev["t"] - 0.5)) < 0.26]
        rs = cand[0] if cand else {}
        out.append({**ev, "reason": rs.get("reason")})
    return out


def moves(ev, x0):
    if not ev:
        return None
    fol = [e for e in ev if e.get("fol_id")]
    rs = defaultdict(int)
    for e in ev:
        rs[e["reason"]] += 1
    return {"n": len(ev), "pos_p10_p50_p90": [pct([e["x"] - x0 for e in ev], q) for q in (10, 50, 90)],
            "v_p10_p50_p90": [pct([e["v"] for e in ev], q) for q in (10, 50, 90)],
            "share_v_lt_5": float(np.mean([e["v"] < 5 for e in ev])), "reasons": dict(rs),
            "lead_tgap_p50": pct([e["lead_gap"] / e["v"] for e in ev if e["lead_gap"] is not None and e["v"] > 1], 50),
            "lag_tgap_p50": pct([e["fol_gap"] / e["fol_v0"] for e in fol if e.get("fol_v0", 0) > 1], 50),
            "changer_minus_newfol_p50": pct([e["v"] - e["fol_v0"] for e in fol], 50),
            "newlead_minus_changer_p50": pct([e["lead_v"] - e["v"] for e in ev if e.get("lead_v") is not None], 50),
            "fol_share_brake_gt_b": float(np.mean([e["fol_amin"] < -e["fol_b"] for e in fol if e.get("fol_amin") is not None])) if fol else None,
            "fol_amin_p10": pct([e.get("fol_amin") for e in fol], 10)}


def main():
    path, outp = sys.argv[1], sys.argv[2]
    args = sys.argv[3:]
    base = args[args.index("--base") + 1] if "--base" in args else "k1"
    rep = {k: json.load(open(REPO + f)) for k, f in (("dc", "i24_validation_dc.json"), ("refit", "i24_validation_dc_refit.json"))}
    rq = {k: np.array(v["simulated"]["hourly_flows_veh_h_mean"]) for k, v in rep.items()}
    obs = np.array(rep["dc"]["observed"]["hourly_flows_veh_h_recommended"])
    runs = defaultdict(list)
    for line in open(path):
        r = json.loads(line)
        runs[r["tag"]].append(r)
    res = {}
    for tag, rr in runs.items():
        rr.sort(key=lambda r: r["seed"])
        a = {"seeds": [r["seed"] for r in rr], "per_seed": {}}
        W = {s: np.array([windows(r, s) for r in rr]) for s in SECS}
        for s in SECS:
            a[s] = ci(W[s].mean(axis=1))
            a[s + "_windows"] = W[s].mean(axis=0).round(0).tolist()
            if s in REPL_IDX:
                a[s + "_corr_refit"] = float(np.corrcoef(W[s].mean(0), rq["refit"][REPL_IDX[s]])[0, 1])
            lanes = defaultdict(list)
            for r in rr:
                for k, v in lane_flows(r, s).items():
                    lanes[k].append(v)
            a[s + "_lane"] = {k: ci(v) for k, v in sorted(lanes.items())}
        for i, r in enumerate(rr):
            a["per_seed"][r["seed"]] = {s: float(W[s][i].mean()) for s in SECS}
            a["per_seed"][r["seed"]]["coll"] = r["n_collisions"]
            a["per_seed"][r["seed"]]["hard45"] = r["hard_steps"]["lt_-4.5"]
            a["per_seed"][r["seed"]]["hard89"] = r["hard_steps"]["lt_-8.9"]
            cs, _ = congested_share(r, 0.0, 5300.0)
            a["per_seed"][r["seed"]]["cong_share"] = cs
        a["collisions"] = sum(r["n_collisions"] for r in rr)
        a["hard45_per_run"] = float(np.mean([r["hard_steps"]["lt_-4.5"] for r in rr]))
        a["hard89_total"] = sum(r["hard_steps"]["lt_-8.9"] for r in rr)
        a["cong_share"] = ci([a["per_seed"][s]["cong_share"] for s in a["per_seed"]])
        a["departed_share"] = float(np.mean([r["n_departed"] / r["n_planned"] for r in rr]))
        if all(r.get("waves") for r in rr):
            from analyze_full import wave_stats
            a["waves"] = wave_stats(rr)
            for r in rr:
                a["per_seed"][r["seed"]]["wv_standard"] = r["waves"]["standard"]["n_backward"]
                a["per_seed"][r["seed"]]["wv_stripe"] = r["waves"]["stripe"]["n_backward"]
        # speed profile (2-h, vehicle-weighted, per 250 m) for the first run set
        prof = []
        for r in rr:
            _, v = congested_share(r, 0.0, 5300.0)
            prof.append(np.nanmean(v, axis=0))
        prof = np.nanmean(np.array(prof), axis=0)
        a["speed_profile_kmh_50m"] = np.round(prof, 1).tolist()
        if any(r.get("lc_reasons") for r in rr):
            ev = [e for r in rr for e in join(r)]
            g = rr[0]["geo"]
            a["lc"] = {
                "hh_exiters_to_aux": moves([e for e in ev if e["kind"] == "hhoff" and e["edge"] == "eB" and e["to"] < e["from"]], g["x_div"]),
                "hh_exiters_upstream_right": moves([e for e in ev if e["kind"] == "hhoff" and e["edge"] == "e0" and e["to"] < e["from"]], g["x_div"]),
                "weave_entrants": moves([e for e in ev if e["kind"] == "ramp" and e["edge"] == "eD" and e["from"] == 0], g["x_merge"]),
                "bell_exiters_to_aux": moves([e for e in ev if e["kind"] == "bell" and e["edge"] == "eD" and e["to"] < e["from"]], g["x_merge"]),
                "bell_exiters_upstream_right": moves([e for e in ev if e["kind"] == "bell" and e["edge"] in ("eC", "eB") and e["to"] < e["from"]], g["x_merge"]),
                "through_in_weave": moves([e for e in ev if e["kind"] == "main" and e["edge"] == "eD"], g["x_merge"]),
                "through_in_diverge": moves([e for e in ev if e["kind"] == "main" and e["edge"] == "eB"], g["x_div"]),
                "downstream_eE": moves([e for e in ev if e["edge"] == "eE"], g["x_acc_end"]),
            }
            a["lc_per_run_hour"] = {k: (v["n"] / len(rr) / 2.0 if v else 0) for k, v in a["lc"].items()}
        res[tag] = a
    if base in res:
        b = res[base]
        for tag, a in res.items():
            if tag == base:
                continue
            common = sorted(set(a["seeds"]) & set(b["seeds"]))
            a["paired_vs_" + base] = {s: ci([a["per_seed"][k][s] - b["per_seed"][k][s] for k in common]) for s in SECS}
            a["paired_vs_" + base]["cong_share"] = ci([a["per_seed"][k]["cong_share"] - b["per_seed"][k]["cong_share"] for k in common])
            for wk in ("wv_standard", "wv_stripe"):
                if all(wk in a["per_seed"][k] and wk in b["per_seed"][k] for k in common):
                    a["paired_vs_" + base][wk] = ci([a["per_seed"][k][wk] - b["per_seed"][k][wk] for k in common])
    res["_replica"] = {"refit_2h": {s: float(rq["refit"][i].mean()) for s, i in REPL_IDX.items()},
                       "dc_2h": {s: float(rq["dc"][i].mean()) for s, i in REPL_IDX.items()},
                       "observed_2h": {s: float(obs[i].mean()) for s, i in REPL_IDX.items()}}
    json.dump(res, open(outp, "w"), indent=1, default=float)
    f = lambda c: "-" if c["mean"] is None else f'{c["mean"]:,.0f}' + (f' ± {c["hw"]:,.0f}' if c["hw"] else "")
    print("replica refit 2h:", {k: round(v) for k, v in res["_replica"]["refit_2h"].items()}, " observed:", {k: round(v) for k, v in res["_replica"]["observed_2h"].items()})
    for tag, a in res.items():
        if tag.startswith("_"):
            continue
        print(f"== {tag}: " + " | ".join(f"{s} {f(a[s])}" for s in SECS) + f" | coll {a['collisions']} hard<-4.5/run {a['hard45_per_run']:.1f} <-8.9 {a['hard89_total']} | congested share {a['cong_share']['mean']:.3f} | departed {a['departed_share']:.3f}")
        if "waves" in a:
            print("   waves:", {d: (round(w["n_backward_per_run"]["mean"], 1), None if w["mean_backward_kmh"] is None else round(w["mean_backward_kmh"], 1),
                                  None if w["in_band_pooled"] is None else round(w["in_band_pooled"], 2)) for d, w in a["waves"].items()})
        print("   corr with refit windows:", {s: round(a[s + "_corr_refit"], 3) for s in REPL_IDX})
        print("   s5400 lanes", {k: f(v) for k, v in a["s5400_lane"].items()}, " s3200 lanes", {k: f(v) for k, v in a["s3200_lane"].items()})
        for k, v in a.items():
            if k.startswith("paired_vs_"):
                print("  ", k, {s: (f(c) if s != "cong_share" else f"{c['mean']:+.3f} ± {c['hw']:.3f}") if not s.startswith("wv_") else f"{c['mean']:+.1f} ± {c['hw']:.1f}" for s, c in v.items()})


if __name__ == "__main__":
    main()
