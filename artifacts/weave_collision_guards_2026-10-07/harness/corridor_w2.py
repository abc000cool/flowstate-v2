"""Amendment W2's corridor criteria CW1-CW5 (docs/I94_CAL_COLLISIONS.md §13.6), from two I-94 batteries.

usage (repository root):
  corridor_w2.py --a ARTIFACT --b ARTIFACT [--context ARTIFACT] --out OUT_JSON

A is the W1b arm (``_dc_cal`` with W1b on both weaves), B the W1b + W2 arm; both are
``scripts/corridor_battery.py`` outputs on the same seeds. Each replicate's ``run_dir`` must hold
``meta.json`` and ``vehicles.parquet`` (the battery keeps both); trajectories are never read.
``--context`` (optional) is p8's ``_dc_cal`` battery, reported beside (collisions per seed), never paired.

* CW1  S790 06:30-07:30 simulated flow, paired B - A: 95 % t-interval lower bound > -50 veh/h.
* CW2  departed share, paired B - A: lower bound > -0.01.
* CW3  zero collisions in B; A's (and the context's) collisions reported by section.
* CW4  zero locks in B, by corridor_w1b.py's end-of-run front-row reading (C4b) and by the battery's
       own ``per_seed[i].locks`` (validation.locks); a disagreement is reported, not resolved.
* CW5  given-up exits (``n_missed_exit``) per weave, pooled over seeds: B <= A + 2 + 2*sqrt(2*A);
       W1b's releases (``n_entrant_took_exit``) <= 1 % of each entrance's departures, pooled, in B.

Reported beside: the W2 counters per weave, link-flow GEH < 5 share, the battery criteria rows.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
W1B = HERE.parents[1] / "weave_loss_2026-10-07" / "w1b" / "harness" / "corridor_w1b.py"
_spec = importlib.util.spec_from_file_location("corridor_w1b", W1B)
assert _spec is not None and _spec.loader is not None
cw1b = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cw1b)

#: Weave section edges of the I-94 corridor (docs/I94_CAL_COLLISIONS.md §1).
SECTION_OF_EDGE = {"999007700": "Ruth St", "51388891": "T.H.52"}
W2_COUNTERS = ("n_handback_skips", "n_close_leader_withheld", "n_opposing_deferred", "n_opposing_vetoed")


def band(ref: float) -> float:
    return ref + 2.0 + 2.0 * math.sqrt(2.0 * ref)


def extra(art_path: Path) -> dict[int, dict]:
    """Per seed: collisions by section, weave counters and the battery's own lock record."""
    art = json.loads(art_path.read_text())
    out: dict[int, dict] = {}
    for r in art["per_seed"]:
        rd = Path(r["run_dir"])
        rec: dict = {"battery_locks": (r.get("locks") or {}).get("n_locks"),
                     "battery_locked": (r.get("locks") or {}).get("locked")}
        meta_p = rd / "meta.json"
        if meta_p.exists():
            meta = json.loads(meta_p.read_text())
            by: dict[str, int] = {}
            for ev in meta.get("collisions") or []:
                edge = str(ev["lane"]).rpartition("_")[0]
                sec = SECTION_OF_EDGE.get(edge, "other")
                by[sec] = by.get(sec, 0) + 1
            rec["collisions_by_section"] = by
            rec["collisions_logged"] = meta.get("collisions") or []
            rec["weaves"] = {
                z["ramp"]: {"n_missed_exit": z.get("n_missed_exit"),
                            "n_entrant_took_exit": z.get("n_entrant_took_exit"),
                            **{k: z.get(k) for k in W2_COUNTERS}}
                for z in meta.get("weave_sections") or []
            }
        out[r["seed"]] = rec
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", type=Path, required=True, help="W1b arm battery artifact")
    ap.add_argument("--b", type=Path, required=True, help="W1b + W2 arm battery artifact")
    ap.add_argument("--context", type=Path, default=None, help="p8's _dc_cal battery (reported only)")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    A, B = cw1b.arm(a.a), cw1b.arm(a.b)
    xa, xb = extra(a.a), extra(a.b)
    seeds = sorted(set(A) & set(B))
    assert seeds == sorted(A) == sorted(B), "the arms ran different seeds"
    c1 = cw1b.ci([B[s]["s790"] - A[s]["s790"] for s in seeds])
    c2 = cw1b.ci([B[s]["departed"] - A[s]["departed"] for s in seeds])
    crit: dict = {
        "CW1": {"pass": c1[1] > -50.0, "paired_s790_veh_h": c1},
        "CW2": {"pass": c2[1] > -0.01, "paired_departed_share": c2},
        "CW3": {"pass": sum(B[s]["collisions"] for s in seeds) == 0,
                "collisions_b": sum(B[s]["collisions"] for s in seeds),
                "collisions_a": sum(A[s]["collisions"] for s in seeds),
                "by_section_b": [xb[s].get("collisions_by_section") for s in seeds],
                "by_section_a": [xa[s].get("collisions_by_section") for s in seeds]},
    }
    if all(B[s]["sections"] is not None and A[s]["sections"] is not None for s in seeds):
        def scan_locks(rows: dict) -> list[str]:
            return [f"{s}:{z['ramp']}" for s in seeds for z in rows[s]["sections"] if z["locked"]]

        bat_b = [s for s in seeds if xb[s]["battery_locked"]]
        crit["CW4"] = {"pass": not scan_locks(B) and not bat_b,
                       "front_row_locks_b": scan_locks(B), "front_row_locks_a": scan_locks(A),
                       "battery_locked_b": bat_b, "battery_locked_a": [s for s in seeds if xa[s]["battery_locked"]],
                       "readers_disagree_b": sorted({s for s in seeds
                                                     if any(z["locked"] for z in B[s]["sections"])
                                                     != bool(xb[s]["battery_locked"])})}
        gu: dict = {}
        rel: dict = {}
        for arm, rows, ex in (("a", A, xa), ("b", B, xb)):
            for s in seeds:
                for ramp, w in (ex[s].get("weaves") or {}).items():
                    g = gu.setdefault(ramp, {"a": 0, "b": 0})
                    g[arm] += w["n_missed_exit"] or 0
            if arm == "b":
                for s in seeds:
                    for z in rows[s]["sections"]:
                        p = rel.setdefault(z["ramp"], {"releases": 0, "entrance_departed": 0})
                        p["releases"] += z["n_entrant_took_exit"] or 0
                        p["entrance_departed"] += z["entrance_departed"] or 0
        for g in gu.values():
            g["bound"] = band(g["a"])
            g["pass"] = g["b"] <= g["bound"]
        for p in rel.values():
            p["share"] = p["releases"] / p["entrance_departed"] if p["entrance_departed"] else None
        crit["CW5"] = {"pass": all(g["pass"] for g in gu.values())
                       and all((p["share"] or 0.0) <= 0.01 for p in rel.values()),
                       "given_up_exits": gu, "w1b_releases_b": rel}
    else:
        crit["CW4"] = crit["CW5"] = {"pass": None, "note": "a replicate's meta.json / vehicles.parquet is missing"}
    res: dict = {"seeds": seeds, "criteria": crit,
                 "w2_counters_b": {s: {r: {k: w.get(k) for k in W2_COUNTERS} for r, w in (xb[s].get("weaves") or {}).items()}
                                   for s in seeds},
                 "per_seed": {s: {"a": {**A[s], **xa[s]}, "b": {**B[s], **xb[s]}} for s in seeds}}
    for name, p in (("a", a.a), ("b", a.b)):
        art = json.loads(p.read_text())
        res[f"battery_criteria_{name}"] = art.get("criteria")
    if a.context is not None:
        ctx = json.loads(a.context.read_text())
        res["context_collisions_per_seed"] = {r["seed"]: r["n_collisions"] for r in ctx["per_seed"]}
    a.out.write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(crit, indent=1, default=float)[:4000])


if __name__ == "__main__":
    main()
