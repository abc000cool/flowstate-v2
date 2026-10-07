"""Batch G and its levers: the I-24 Old Hickory geometry fixture (oh4), BLOCK mode, free outflow.
Usage: uv run --no-sync python batch_g.py OUT.jsonl ARM [ARM ...] [--seeds 1-20] [--lc-log]

Arms are defined in ARMS below (population and overrides). Two processes.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FLEET = "scenarios/i24_replica_flow_speedcal_dc.yaml"
K0 = "artifacts/idm_i24_capacity.json"
K05 = "artifacts/idm_i24_capacity_amax_k0.5.json"
K1 = "artifacts/idm_i24_capacity_amax_k1.0.json"

C = 7200.0
RAMP_SHARE = 0.16
RED = (300.0, 390.0)
DUR = 1200.0
SPEC = {"kind": "oh4", "up_len": 2800.0, "acc_len": 975.0, "dn_len": 2000.0, "ramp_len": 500.0, "lanes": 4,
        "census_to": "s2200"}


def sections(spec: dict) -> dict:
    up, acc = spec["up_len"], spec["acc_len"]
    return {"up": up - 500.0, "acc_mid": up + acc / 2, "acc_end": up + acc - 1.0,
            "s2200": up + acc + 367.0, "s3200": up + acc + 1347.0}


def demand(c: float = C, share: float = RAMP_SHARE) -> dict:
    tot = [(0.0, 0.9 * c / 3600.0), (120.0, 1.35 * c / 3600.0)]
    return {"main": [(t, q * (1 - share)) for t, q in tot], "ramp": [(t, q * share) for t, q in tot]}


ARMS: dict[str, dict] = {
    "k0": {"pop": K0},
    "k05": {"pop": K05},
    "k1": {"pop": K1},
}


def _job(p):
    import fx

    arm = dict(ARMS[p["arm"]])
    spec = dict(SPEC)
    spec.update(arm.pop("spec", {}))
    fleet = fx.fleet_from(FLEET)
    dem = demand(arm.pop("cap", C), arm.pop("share", RAMP_SHARE))
    r = fx.run(spec, fleet, p["seed"], dem, DUR, red=RED, sections=sections(spec), lc_log=p["lc_log"],
               tag=p["arm"], **arm)
    return r


def main():
    out = Path(sys.argv[1])
    args = sys.argv[2:]
    lc_log = "--lc-log" in args
    seeds = list(range(1, 21))
    if "--seeds" in args:
        a, b = args[args.index("--seeds") + 1].split("-")
        seeds = list(range(int(a), int(b) + 1))
    arms = [a for a in args if a in ARMS]
    jobs = [{"arm": a, "seed": s, "lc_log": lc_log} for a in arms for s in seeds]
    t0 = time.time()
    with mp.get_context("spawn").Pool(2) as pool, out.open("a") as fh:
        for i, r in enumerate(pool.imap_unordered(_job, jobs), 1):
            fh.write(json.dumps(r, default=float) + "\n")
            fh.flush()
            if i % 5 == 0 or i == len(jobs):
                print(f"{i}/{len(jobs)} {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
