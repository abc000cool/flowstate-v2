"""Batches B (constant boundary limits), S (the replica's boundary schedule) and W (Hickory Hollow-Bell
Road weave) on straight fixtures. Two processes.
Usage: uv run --no-sync python batch_b.py OUT.jsonl B|S|W [--seeds a-b] [--arms a,b] [--fleet-pop K]"""

from __future__ import annotations

import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FLEET = "scenarios/i24_replica_flow_speedcal_dc.yaml"
POPS = {"k0": "artifacts/idm_i24_capacity.json", "k1": "artifacts/idm_i24_capacity_amax_k1.0.json"}
SAT = 7600.0


def schedule() -> list[tuple[float, float]]:
    raw = yaml.safe_load((Path("/Users/anshpathak/Desktop/apps/flowstate") / FLEET).read_text())
    return [(float(t), float(v)) for t, v in raw["network"]["boundary"]["steps"]]


# lever arms (overrides passed to fx.run); filled by later pre-registered entries
LEVERS: dict[str, dict] = {"base": {}}


def _job(p):
    import fx

    fleet = fx.fleet_from(FLEET)
    lev = dict(LEVERS[p.get("lever", "base")])
    if p["batch"] in ("B", "S", "S992"):
        buf = 992.0 if p["batch"] == "S992" else 200.0
        spec = {"kind": "straight", "len": 3000.0, "lanes": 4, "buffer_m": buf if p["vb"] != "none" else None}
        dem = {"main": [(0.0, SAT / 3600.0)]}
        if p["batch"] == "B":
            bnd = None if p["vb"] == "none" else [(0.0, p["vb"] / 3.6)]
            dur = 900.0
        else:
            bnd = schedule()
            dur = 7800.0
        return fx.run(spec, fleet, p["seed"], dem, dur, pop=POPS[p["pop"]], boundary=bnd,
                      sections={"x2900": 2900.0, "x1500": 1500.0}, track_lc=False, field_dt=300.0,
                      tag=f'{p["batch"]}|{p["pop"]}|{p["vb"]}|{p.get("lever", "base")}', **lev)
    if p["batch"] == "W":
        spec = {"kind": "weave", "up_len": 2500.0, "weave_len": 565.0, "dn_len": 2000.0, "ramp_len": 400.0, "lanes": 4}
        qm = 7200.0
        share_exit = 0.055
        dem = {"main": [(0.0, qm * (1 - share_exit) / 3600.0)], "exit": [(0.0, qm * share_exit / 3600.0)],
               "ramp": [(0.0, 687.0 / 3600.0)]}
        up, wl = spec["up_len"], spec["weave_len"]
        return fx.run(spec, fleet, p["seed"], dem, 1200.0, pop=POPS[p["pop"]],
                      sections={"up": up - 500.0, "w_mid": up + wl / 2, "dn300": up + wl + 300.0, "dn1000": up + wl + 1000.0},
                      track_lc=True, lc_log=p.get("lc_log", False), tag=f'W|{p["pop"]}|{p.get("lever", "base")}', **lev)
    raise ValueError(p["batch"])


def main():
    out = Path(sys.argv[1])
    batch = sys.argv[2]
    args = sys.argv[3:]
    seeds = None
    if "--seeds" in args:
        a, b = args[args.index("--seeds") + 1].split("-")
        seeds = list(range(int(a), int(b) + 1))
    levers = args[args.index("--levers") + 1].split(",") if "--levers" in args else ["base"]
    jobs = []
    if batch == "B":
        for lev in levers:
            for vb in (20, 30, 40, 50, 60, 70, "none"):
                for s in seeds or range(1, 11):
                    jobs.append({"batch": "B", "pop": "k1", "vb": vb, "seed": s, "lever": lev})
            if lev == "base":
                for vb in (30, 50, 70):
                    for s in seeds or range(1, 11):
                        jobs.append({"batch": "B", "pop": "k0", "vb": vb, "seed": s, "lever": lev})
    elif batch in ("S", "S992"):
        for lev in levers:
            for s in seeds or range(1, 6):
                jobs.append({"batch": batch, "pop": "k1", "vb": "sched", "seed": s, "lever": lev})
    elif batch == "W":
        for lev in levers:
            for s in seeds or range(1, 11):
                jobs.append({"batch": "W", "pop": "k1", "vb": None, "seed": s, "lever": lev, "lc_log": "--lc-log" in args})
    t0 = time.time()
    with mp.get_context("spawn").Pool(2) as pool, out.open("a") as fh:
        for i, r in enumerate(pool.imap_unordered(_job, jobs), 1):
            fh.write(json.dumps(r, default=float) + "\n")
            fh.flush()
            if i % 10 == 0 or i == len(jobs):
                print(f"{i}/{len(jobs)} {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
