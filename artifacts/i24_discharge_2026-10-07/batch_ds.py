"""Batch DS (the replica's downstream end as a fixture) and the levers on it. Two processes.
Usage: uv run --no-sync python batch_ds.py OUT.jsonl ARM [ARM ...] [--seeds a-b] [--lc-log]"""

from __future__ import annotations

import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO = Path("/Users/anshpathak/Desktop/apps/flowstate")

FLEET = "scenarios/i24_replica_flow_speedcal_dc.yaml"
POPS = {"k0": "artifacts/idm_i24_capacity.json", "k05": "artifacts/idm_i24_capacity_amax_k0.5.json",
        "k1": "artifacts/idm_i24_capacity_amax_k1.0.json"}
SAT = 7400.0
P_HH, P_BELL = 0.0913, (1 - 0.0913) * 0.0529
DUR = 7800.0
SPEC = {"kind": "ds"}
SECTIONS = {"s2200": 1200.0, "s3200": 2200.0, "s4800": 3778.0, "s5400": 4367.0, "sbuf": 5341.0}

ARMS = {
    "k0": {"pop": "k0"},
    "k05": {"pop": "k05"},
    "k1": {"pop": "k1"},
    "L1a_assert125": {"pop": "k1", "lc_over": {"lc_assertive": 1.25}},
    "L1b_assert338": {"pop": "k1", "lc_over": {"lc_assertive": 3.38}},
    "L2_action1": {"pop": "k1", "action_step": 1.0},
    "L3_eidm": {"pop": "k1", "model": "EIDM"},
    # 05:37 entry: the replica's last 1,389 m (397 m + the 992-m limited last edge)
    "k1_992": {"pop": "k1", "spec": {"e": 397.0, "buffer_m": 992.0},
               "sections": {"s2200": 1200.0, "s3200": 2200.0, "s4800": 3778.0, "s5400": 4367.0, "sbuf": 4277.0}},
}
# 05:50 entry: the levers run on DS992, with wave readings (fixture x 0-4,377 = the 4-lane span before
# the limited edge, study window); "k1_992w" re-runs the DS992 base with the readings on
_D992 = ARMS["k1_992"]
for _name, _over in (("k1_992w", {}), ("L1a_992", {"lc_over": {"lc_assertive": 1.25}}),
                     ("L1b_992", {"lc_over": {"lc_assertive": 3.38}}), ("L2_992", {"action_step": 1.0}),
                     ("L3_992", {"model": "EIDM"}), ("k0_992w", {"pop": "k0"}), ("k05_992w", {"pop": "k05"})):
    ARMS[_name] = {**_D992, **_over, "wave_span": (0.0, 4377.0, 600.0)}


def scen():
    return yaml.safe_load((REPO / FLEET).read_text())


def demand():
    raw = scen()
    hh_on = next(r for r in raw["network"]["ramps"] if r["name"] == "Hickory Hollow Pkwy on-ramp")
    q = SAT / 3600.0
    return {"main": [(0.0, q * (1 - P_HH - P_BELL))], "hhoff": [(0.0, q * P_HH)], "bell": [(0.0, q * P_BELL)],
            "ramp": [(float(t), float(v)) for t, v in hh_on["inflow"]]}


def boundary():
    return [(float(t), float(v)) for t, v in scen()["network"]["boundary"]["steps"]]


def _job(p):
    import fx

    arm = dict(ARMS[p["arm"]])
    fleet = fx.fleet_from(FLEET)
    if "model" in arm:
        fleet["model"] = arm.pop("model")
    pop = POPS[arm.pop("pop")]
    spec = dict(SPEC)
    spec.update(arm.pop("spec", {}))
    secs = arm.pop("sections", SECTIONS)
    return fx.run(spec, fleet, p["seed"], demand(), DUR, pop=pop, boundary=boundary(), sections=secs,
                  lc_log=p["lc_log"], track_lc=True, lc_x_min=2000.0, field_dt=300.0, tag=p["arm"], **arm)


def main():
    out = Path(sys.argv[1])
    args = sys.argv[2:]
    seeds = list(range(1, 11))
    if "--seeds" in args:
        a, b = args[args.index("--seeds") + 1].split("-")
        seeds = list(range(int(a), int(b) + 1))
    arms = [a for a in args if a in ARMS]
    jobs = [{"arm": a, "seed": s, "lc_log": "--lc-log" in args} for a in arms for s in seeds]
    t0 = time.time()
    with mp.get_context("spawn").Pool(2) as pool, out.open("a") as fh:
        for i, r in enumerate(pool.imap_unordered(_job, jobs), 1):
            fh.write(json.dumps(r, default=float) + "\n")
            fh.flush()
            print(f"{i}/{len(jobs)} {r['tag']} s{r['seed']} {time.time() - t0:.0f}s wall_run {r['wall_s']}", flush=True)


if __name__ == "__main__":
    main()
