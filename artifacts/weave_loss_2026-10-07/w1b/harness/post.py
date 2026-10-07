"""Per-run readouts of amendment W1b's evaluation (docs/WEAVE_LOSS_DIAGNOSIS.md §10.4).

usage: post.py WORK_DIR OUT_JSON   (from the repository root; WORK_DIR as merge_model_selfcheck --keep left it)

For every run tree WORK_DIR/<fixture>_weave/<hash>/<seed>/ and every weave section of it:

* stands (the rule's definition, §10.4): an entrant (from the weave's on-ramp, not planned for the
  paired exit) in section lane 0 below 0.1 m/s within 5 m of the gore on consecutive 0.5-s samples;
  a rerouted entrant's samples after its gave_up_s are not its stand. Length = last - first sample.
* releases: entrants the rule rerouted (gave_up, destination_final the exit) with the stand that
  ends at their gave_up_s (B0 a).
* lock (fixture form of I94_COLLAPSE_DIAGNOSIS §7.1): after the 120-s warm-up, the same vehicle the
  front of a section lane, within 15 m of the gore, below 0.1 m/s for >= 120 s without a break.
* strand.py's stranded time (front of lane 0 within 15 m below 0.5 m/s, an entrant), for the report.
* B1's digests: sha256 of every Parquet file; meta.json without wall time, realtime factor,
  config_hash, both copies of the weave params and n_entrant_took_exit (§10.5 and its clarification).
"""

import copy
import glob
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import sumolib

DT = 0.5
WARMUP_S = 120.0
LOCK_S = 120.0
FRONT_M = 15.0
STAND_M = 5.0
HALT = 0.1


def runs_of(t: np.ndarray) -> list[tuple[int, int]]:
    """Index ranges [i, j] of consecutive samples (step DT) in a sorted time array."""
    if len(t) == 0:
        return []
    breaks = np.flatnonzero(np.diff(t) > DT + 1e-6)
    starts = np.r_[0, breaks + 1]
    ends = np.r_[breaks, len(t) - 1]
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def meta_digest(meta: dict) -> str:
    m = copy.deepcopy(meta)
    for k in ("wall_time_s", "realtime_factor", "config_hash"):
        m.pop(k, None)
    for r in m.get("config", {}).get("network", {}).get("ramps", []) or []:
        if r.get("weave") is not None:
            r["weave"].pop("weave_params", None)
    for z in m.get("weave_sections") or []:
        z.pop("params", None)
        z.pop("n_entrant_took_exit", None)
    return hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()


def one(rd: Path) -> dict:
    meta = json.loads((rd / "meta.json").read_text())
    veh = pd.read_parquet(rd / "vehicles.parquet").set_index("veh_id")
    df = pd.read_parquet(rd / "trajectories.parquet", columns=["t", "veh_id", "x", "lane", "v"])
    net = sumolib.net.readNet(str(next(rd.glob("net/**/*.net.xml"))))
    out = {
        "dir": str(rd),
        "fixture": rd.parents[1].name.removesuffix("_weave"),
        "seed": meta["seed"],
        "config_hash": meta["config_hash"],
        "n_collisions": meta["n_collisions"],
        "departed": meta["n_vehicles_departed"],
        "parquet_sha": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(rd.glob("*.parquet"))
        },
        "meta_digest": meta_digest(meta),
        "sections": [],
    }
    for z in meta.get("weave_sections") or []:
        ramp = next(r for r in meta["ramps"] if r["name"] == z["ramp"])
        x0 = float(ramp["attach_x_m"])
        gore = x0 + sum(net.getEdge(e).getLength() for e in z["edges"])
        origin = veh.origin.astype(str)
        dest = veh.destination.astype(str)
        entrants = set(veh.index[(origin == z["ramp"]) & (dest != z["exit"])])
        exiters = set(veh.index[(origin != z["ramp"]) & (dest == z["exit"])])
        r2r = set(veh.index[(origin == z["ramp"]) & (dest == z["exit"])])
        gave_up_s = veh.gave_up_s.where(veh.gave_up)
        released = veh[
            veh.gave_up
            & veh.index.isin(entrants)
            & (veh.destination_final.astype(str) == z["exit"])
        ]
        sec = df[(df.x >= x0) & (df.x < gore)]
        # --- stands (the rule's definition) ---------------------------------
        st = sec[
            sec.veh_id.isin(entrants) & (sec.lane == 0) & (sec.v < HALT) & (sec.x >= gore - STAND_M)
        ]
        g_rel = st.veh_id.map(gave_up_s)
        st = st[g_rel.isna() | (st.t <= g_rel + 1e-9)]
        stands = []
        for vid, g in st.sort_values("t").groupby("veh_id", sort=False):
            t = g.t.to_numpy()
            for i, j in runs_of(t):
                stands.append({"veh_id": vid, "t0": float(t[i]), "t1": float(t[j]), "len_s": float(t[j] - t[i])})
        rel = []
        for vid, t_rel in released.gave_up_s.items():
            s = [s for s in stands if s["veh_id"] == vid and abs(s["t1"] - t_rel) < 1e-6]
            rel.append({"veh_id": vid, "gave_up_s": float(t_rel), "stand_len_s": s[0]["len_s"] if s else 0.0,
                        "stand_t0": s[0]["t0"] if s else None})
        # --- lane fronts and locks --------------------------------------------
        w = sec[sec.t >= WARMUP_S]
        fronts = w.loc[w.groupby(["t", "lane"]).x.idxmax()]
        stuck = fronts[(gore - fronts.x <= FRONT_M) & (fronts.v < HALT)]

        def kind(v: str) -> str:
            return ("entrant" if v in entrants else "exiter" if v in exiters
                    else "ramp_to_exit" if v in r2r else "through")

        front_stands = []
        for (lane, vid), g in stuck.sort_values("t").groupby(["lane", "veh_id"], sort=False):
            t = g.t.to_numpy()
            for i, j in runs_of(t):
                front_stands.append({"lane": int(lane), "veh_id": vid, "kind": kind(vid),
                                     "t0": float(t[i]), "t1": float(t[j]), "len_s": float(t[j] - t[i])})
        locks = [f for f in front_stands if f["len_s"] >= LOCK_S - 1e-9]
        longest_front = {}
        for f in front_stands:
            if f["len_s"] > longest_front.get(f["lane"], {"len_s": -1})["len_s"]:
                longest_front[f["lane"]] = f
        # --- strand.py's stranded time (report only) --------------------------
        l0 = sec[sec.lane == 0]
        f0 = l0.loc[l0.groupby("t").x.idxmax()]
        strand = f0[(f0.x > gore - 15.0) & (f0.v < 0.5) & f0.veh_id.isin(entrants)]
        on = next(r for r in meta["ramps"] if r["name"] == z["ramp"])
        out["sections"].append({
            "ramp": z["ramp"], "exit": z["exit"], "x0": x0, "gore": gore,
            "gore_attach_end_x_m": ramp.get("attach_end_x_m"),
            "entrance_departed": on["n_departed"],
            "n_entrant_took_exit": z.get("n_entrant_took_exit"),
            "n_missed_exit": z.get("n_missed_exit"), "n_unfinished": z.get("n_unfinished"),
            "n_entered": z.get("n_entered"),
            "releases": rel,
            "n_stands": len(stands),
            "longest_stand_s": max((s["len_s"] for s in stands), default=0.0),
            "stands_ge_30s": sorted(s["len_s"] for s in stands if s["len_s"] >= 30.0),
            "locks": locks,
            "longest_front_stand_by_lane": {str(k): v for k, v in sorted(longest_front.items())},
            "strand_s": float(len(strand) * DT),
        })
    return out


if __name__ == "__main__":
    res = []
    for m in sorted(glob.glob(f"{sys.argv[1]}/*/*/*/meta.json")):
        r = one(Path(m).parent)
        res.append(r)
        s = r["sections"]
        print(r["fixture"], r["seed"], [(z["n_entrant_took_exit"], round(z["longest_stand_s"], 1),
                                         len(z["locks"])) for z in s], flush=True)
    Path(sys.argv[2]).write_text(json.dumps(res, indent=1, default=float))
