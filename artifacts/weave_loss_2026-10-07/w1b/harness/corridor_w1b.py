"""Amendment W1b's corridor criteria C1-C5b (docs/WEAVE_LOSS_DIAGNOSIS.md §10.6), from two I-94 batteries.

usage (repository root):
  corridor_w1b.py --ref ARTIFACT --w1b ARTIFACT [--committed-ref ARTIFACT] --out OUT_JSON

ARTIFACTs are scripts/corridor_battery.py outputs (``per_seed[*]``: ``seed``, ``run_dir``, ``insertion``,
``link_hours``, ``n_collisions``). Each replicate's ``run_dir`` must hold ``meta.json`` and
``vehicles.parquet`` (the battery keeps both for every replicate); trajectories are never read.

* C1  S790 06:30-07:30 simulated flow, paired W1b - reference: 95 % t-interval upper bound >= 0.
* C2  departed share, paired: upper bound >= 0.
* C3  zero collisions in the W1b arm.
* C4b zero locks at T.H.52 and Ruth St over the W1b replicates. Lock (the diagnosis's Table 4 reading,
      any lane front): at the run's end no vehicle between the section's gore (the weave on-ramp's
      ``attach_end_x_m``) and the next on-ramp downstream (or the corridor's end), and a vehicle at the
      front of a section lane within 15 m of the gore. vehicles.parquet holds positions, not speeds:
      "at rest" is carried by the empty reach past the gore (I94_COLLAPSE_DIAGNOSIS §9, "Front row").
* C5b entrants taking the exit (``weave_sections[i].n_entrant_took_exit``) <= 1 % of that entrance's
      departures, pooled over the seeds, per weave section.
* reproduction: the reference arm's per-seed departed shares against ``--committed-ref`` (step 3).
"""

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

FRONT_M = 15.0
STATION, CLOCK = "S790", "06:30"


def ci(d: list[float]) -> list[float]:
    a = np.asarray(d, float)
    sd = a.std(ddof=1) if len(a) > 1 else 0.0
    h = stats.t.ppf(0.975, len(a) - 1) * sd / math.sqrt(len(a)) if sd > 0 else 0.0
    return [float(a.mean()), float(a.mean() - h), float(a.mean() + h)]


def lock_scan(run_dir: Path) -> list[dict]:
    """Per weave section of one replicate: the end-of-run front row and the lock verdict."""
    meta = json.loads((run_dir / "meta.json").read_text())
    veh = pd.read_parquet(run_dir / "vehicles.parquet")
    end = float(meta["config"]["sim"]["duration_s"])
    present = veh[~veh.arrived & (veh.last_t_s >= end - 1.0)]
    ons = [r for r in meta["ramps"] if r["kind"] == "on" and r.get("attach_x_m") is not None]
    out = []
    for z in meta.get("weave_sections") or []:
        ramp = next(r for r in meta["ramps"] if r["name"] == z["ramp"])
        x0, gore = float(ramp["attach_x_m"]), float(ramp["attach_end_x_m"])
        nxt = min((float(r["attach_x_m"]) for r in ons if float(r["attach_x_m"]) > gore), default=math.inf)
        past = present[(present.last_x_m > gore) & (present.last_x_m < nxt)]
        sec = present[(present.last_x_m >= x0) & (present.last_x_m < gore)]
        fronts = []
        for lane, g in sec.groupby("last_lane"):
            f = g.loc[g.last_x_m.idxmax()]
            kind = ("entrant" if f.origin == z["ramp"] and f.destination != z["exit"] else
                    "exiter" if f.destination == z["exit"] else "through")
            fronts.append({"lane": int(lane), "veh_id": str(f.veh_id), "kind": kind,
                           "short_of_gore_m": round(gore - float(f.last_x_m), 2)})
        locked = len(past) == 0 and any(f["short_of_gore_m"] <= FRONT_M for f in fronts)
        on = next(r for r in meta["ramps"] if r["name"] == z["ramp"])
        out.append({"ramp": z["ramp"], "exit": z["exit"], "gore_x_m": gore, "next_entrance_x_m": nxt,
                    "n_past_gore": int(len(past)), "fronts": fronts, "locked": bool(locked),
                    "n_entrant_took_exit": z.get("n_entrant_took_exit"),
                    "entrance_departed": on.get("n_departed")})
    return out


def arm(path: Path) -> dict[int, dict]:
    art = json.loads(path.read_text())
    rows = {}
    for r in art["per_seed"]:
        s790 = [x["sim_veh_h"] for x in r["link_hours"] if x["station"] == STATION and x["clock"] == CLOCK]
        rd = Path(r["run_dir"])
        rows[r["seed"]] = {
            "s790": s790[0] if s790 else None,
            "departed": r["insertion"]["departed_fraction"],
            "collisions": r["n_collisions"],
            "sections": lock_scan(rd) if (rd / "vehicles.parquet").exists() else None,
        }
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", type=Path, required=True)
    ap.add_argument("--w1b", type=Path, required=True)
    ap.add_argument("--committed-ref", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    ref, w = arm(a.ref), arm(a.w1b)
    seeds = sorted(set(ref) & set(w))
    assert seeds == sorted(ref) == sorted(w), "the arms ran different seeds"
    res: dict = {"seeds": seeds, "per_seed": {s: {"ref": ref[s], "w1b": w[s]} for s in seeds}}
    c1 = ci([w[s]["s790"] - ref[s]["s790"] for s in seeds])
    c2 = ci([w[s]["departed"] - ref[s]["departed"] for s in seeds])
    crit: dict = {
        "C1": {"pass": c1[2] >= 0.0, "paired_s790_veh_h": c1},
        "C2": {"pass": c2[2] >= 0.0, "paired_departed_share": c2},
        "C3": {"pass": sum(w[s]["collisions"] for s in seeds) == 0,
               "collisions_w1b": sum(w[s]["collisions"] for s in seeds),
               "collisions_ref": sum(ref[s]["collisions"] for s in seeds)},
    }
    if all(w[s]["sections"] is not None and ref[s]["sections"] is not None for s in seeds):
        def locks(rows: dict) -> list[str]:
            return [f"{s}:{z['ramp']}" for s in seeds for z in rows[s]["sections"] if z["locked"]]
        crit["C4b"] = {"pass": not locks(w), "locks_w1b": locks(w), "locks_ref": locks(ref)}
        pools: dict = {}
        for s in seeds:
            for z in w[s]["sections"]:
                p = pools.setdefault(z["ramp"], {"releases": 0, "entrance_departed": 0})
                p["releases"] += z["n_entrant_took_exit"] or 0
                p["entrance_departed"] += z["entrance_departed"] or 0
        for p in pools.values():
            p["share"] = p["releases"] / p["entrance_departed"] if p["entrance_departed"] else None
        crit["C5b"] = {"pass": all((p["share"] or 0.0) <= 0.01 for p in pools.values()), "pools": pools}
    else:
        crit["C4b"] = crit["C5b"] = {"pass": None, "note": "a replicate's meta.json / vehicles.parquet is missing"}
    if a.committed_ref is not None:
        com = {r["seed"]: r["insertion"]["departed_fraction"]
               for r in json.loads(a.committed_ref.read_text())["per_seed"]}
        res["reproduction"] = {"identical": all(com.get(s) == ref[s]["departed"] for s in seeds),
                               "max_abs_diff": max(abs(com.get(s, math.nan) - ref[s]["departed"]) for s in seeds)}
    res["criteria"] = crit
    a.out.write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(crit, indent=1, default=float))
    print(json.dumps(res.get("reproduction"), default=float))


if __name__ == "__main__":
    main()
