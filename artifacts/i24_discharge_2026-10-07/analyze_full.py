"""FULL and lever tables: 2-h section flows against the replica and the recording, paired arm
differences, safety counters, realised demand and wave readings.
Usage: uv run --no-sync python analyze_full.py RUNS.jsonl OUT.json PAIRS
  PAIRS: comma list of arm:base pairs, e.g. full_refit_b200:full_refit,full_dc_b200:full_dc"""

from __future__ import annotations

import json
import sys
from collections import defaultdict

import numpy as np

from analyze_g import ci

REPO = "/Users/anshpathak/Desktop/apps/flowstate/artifacts/"
IDX = {"s200": 0, "s1000": 1, "s2200": 2, "s3200": 3, "s4800": 4, "s5400": 5}


def windows(r, s):
    t = np.array([c[0] for c in r["cross"][s]])
    return np.array([((t >= 600 + 300 * i) & (t < 900 + 300 * i)).sum() * 12.0 for i in range(24)])


A, B = 2256.533707135535, 0.9798534039465184


def seg_speeds(rr):
    """Vehicle-step-weighted mean speed per 549.2-m data-x segment (0-5,492) over the study window,
    pooled over lanes and runs (the replica validator's segments)."""
    seg = 5491.93 / 10
    num = np.zeros(10)
    den = np.zeros(10)
    for r in rr:
        f = np.nan_to_num(np.array(r["field"], float))
        n = np.array(r["field_n"], float)
        dx, dt = r["field_dx"], r["field_dt"]
        t0, t1 = int(600 // dt), int(7800 // dt)
        fs = (f[t0:t1] * n[t0:t1]).sum(axis=(0, 2))
        ns = n[t0:t1].sum(axis=(0, 2))
        for bx in range(len(fs)):
            xc = (bx + 0.5) * dx
            d = (xc - A) / B
            k = int(d // seg)
            if 0 <= k < 10:
                num[k] += fs[bx]
                den[k] += ns[bx]
    return (num / np.maximum(den, 1) * 3.6).round(1).tolist()


def wave_stats(rr):
    out = {}
    for det in ("standard", "stripe", "relative", "stack"):
        rows = [r["waves"][det] for r in rr if r.get("waves")]
        if not rows:
            continue
        allb = [b for x in rows for b in x["backward_kmh"]]
        out[det] = {"n_backward_per_run": ci([x["n_backward"] for x in rows]),
                    "mean_backward_kmh": float(np.mean(allb)) if allb else None,
                    "in_band_pooled": float(np.mean([14 <= b <= 22 for b in allb])) if allb else None,
                    "runs_with_backward": sum(1 for x in rows if x["n_backward"] > 0), "n_runs": len(rows),
                    "per_run_speed_kmh": [x["speed_kmh"] for x in rows]}
        if det == "stack":
            out[det]["contrast"] = [x["contrast"] for x in rows]
    return out


def main():
    path, outp = sys.argv[1], sys.argv[2]
    pairs = [p.split(":") for p in sys.argv[3].split(",")] if len(sys.argv) > 3 else []
    rep = {k: json.load(open(REPO + f)) for k, f in (("dc", "i24_validation_dc.json"), ("refit", "i24_validation_dc_refit.json"))}
    rq = {k: np.array(v["simulated"]["hourly_flows_veh_h_mean"]).mean(axis=1) for k, v in rep.items()}
    obs = np.array(rep["dc"]["observed"]["hourly_flows_veh_h_recommended"]).mean(axis=1)
    runs = defaultdict(list)
    for line in open(path):
        r = json.loads(line)
        runs[r["tag"]].append(r)
    res = {"_replica": {k: dict(zip(IDX, v.round(1).tolist())) for k, v in rq.items()},
           "_observed": dict(zip(IDX, obs.round(1).tolist()))}
    for tag, rr in runs.items():
        rr.sort(key=lambda r: r["seed"])
        secs = [s for s in IDX if s in rr[0]["cross"]]
        a = {"seeds": [r["seed"] for r in rr], "per_seed": {r["seed"]: {} for r in rr}}
        for s in secs:
            W = np.array([windows(r, s) for r in rr])
            a[s] = ci(W.mean(axis=1))
            for r, w in zip(rr, W):
                a["per_seed"][r["seed"]][s] = float(w.mean())
        for r in rr:
            a["per_seed"][r["seed"]]["realised"] = r["n_departed"] / r["n_planned"]
        a["realised"] = ci([r["n_departed"] / r["n_planned"] for r in rr])
        a["collisions"] = sum(r["n_collisions"] for r in rr)
        a["hard45_per_run"] = float(np.mean([r["hard_steps"]["lt_-4.5"] for r in rr]))
        a["hard89_total"] = sum(r["hard_steps"]["lt_-8.9"] for r in rr)
        a["waves"] = wave_stats(rr)
        a["segment_speeds_kmh"] = seg_speeds(rr)
        a["wall_s_mean"] = float(np.mean([r["wall_s"] for r in rr]))
        res[tag] = a
    for arm, base in pairs:
        if arm in res and base in res:
            a, b = res[arm], res[base]
            common = sorted(set(a["seeds"]) & set(b["seeds"]))
            keys = [s for s in IDX if s in a] + ["realised"]
            a["paired_vs_" + base] = {s: ci([a["per_seed"][k][s] - b["per_seed"][k][s] for k in common]) for s in keys}
            for det in ("standard", "stripe"):
                if det in a["waves"] and det in b["waves"]:
                    na = [r["waves"][det]["n_backward"] for r in sorted(runs[arm], key=lambda r: r["seed"])]
                    nb = [r["waves"][det]["n_backward"] for r in sorted(runs[base], key=lambda r: r["seed"])]
                    a["paired_vs_" + base][f"waves_{det}_n_backward"] = ci([x - y for x, y in zip(na, nb)])
    json.dump(res, open(outp, "w"), indent=1, default=float)
    f = lambda c: "-" if c["mean"] is None else f'{c["mean"]:,.0f}' + (f' ± {c["hw"]:,.0f}' if c["hw"] else "")
    print("observed:", {k: round(v) for k, v in res["_observed"].items()})
    for k in ("dc", "refit"):
        print(f"replica {k}:", {s: round(v) for s, v in res["_replica"][k].items()})
    for tag, a in res.items():
        if tag.startswith("_"):
            continue
        print(f"== {tag}: " + " | ".join(f"{s} {f(a[s])}" for s in IDX if s in a)
              + f" | realised {a['realised']['mean']:.3f} | coll {a['collisions']} | hard<-4.5/run {a['hard45_per_run']:.1f} <-8.9 {a['hard89_total']}")
        print("   segment speeds km/h:", a["segment_speeds_kmh"])
        for det, w in a["waves"].items():
            print(f"   waves {det}: backward/run {f(w['n_backward_per_run'])}, mean {w['mean_backward_kmh'] if w['mean_backward_kmh'] is None else round(w['mean_backward_kmh'], 1)} km/h, "
                  f"in band {w['in_band_pooled'] if w['in_band_pooled'] is None else round(w['in_band_pooled'], 2)}, runs with any {w['runs_with_backward']}/{w['n_runs']}")
        for k, v in a.items():
            if k.startswith("paired_vs_"):
                print("  ", k, {s: (f(c) if not s.startswith("realised") else f"{c['mean']:+.4f} ± {(c['hw'] or 0):.4f}") for s, c in v.items()})


if __name__ == "__main__":
    main()
