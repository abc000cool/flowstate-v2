"""Batch FULL: the replica's corridor as a straight plain-XML fixture with the replica's own demand
(build_corridor_plan + write_corridor_routes on the scenario config, seeded with the replica's seeds).
Two processes. Usage: uv run --no-sync python batch_full.py OUT.jsonl ARM [ARM ...] [--n-seeds 10]"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO = Path("/Users/anshpathak/Desktop/apps/flowstate")

SCEN = {"dc": "scenarios/i24_replica_flow_speedcal_dc.yaml", "refit": "scenarios/i24_replica_flow_speedcal_dc_refit.yaml"}
A, B = 2256.533707135535, 0.9798534039465184  # sim x of data x (artifacts/i24_replica_inputs_flow.json)
SECTIONS = {f"s{d}": A + B * d for d in (200, 1000, 2200, 3200, 4800, 5400)}
SPEC_REPLICA = {"kind": "full", "lE": 397.0, "buffer_m": 992.0}  # schedule on the 992-m last edge
SPEC_B200 = {"kind": "full", "lE": 397.0 + 992.0, "buffer_m": 200.0}  # last edge at the posted limit + 200-m buffer
ARMS = {
    "full_dc": {"scen": "dc", "spec": SPEC_REPLICA},
    "full_refit": {"scen": "refit", "spec": SPEC_REPLICA},
    "full_dc_b200": {"scen": "dc", "spec": SPEC_B200},
    "full_refit_b200": {"scen": "refit", "spec": SPEC_B200},
    # L5 (prereg): the schedule scaled by the equilibrium-consistent factor on the replica's 992-m edge
    "full_refit_l5": {"scen": "refit", "spec": SPEC_REPLICA, "bscale": 1.2185},
}


def replica_seeds(n: int) -> list[int]:
    d = json.loads((REPO / "artifacts/i24_validation_dc.json").read_text())
    return [int(s) for s in d["simulated"]["seeds"][:n]]


def make_route_fn(scen_path: str, model: str | None = None, action_step: float | None = None, lc_over: dict | None = None):
    def fn(wd, net, seed):
        os.chdir(REPO)
        from flowstate_core.config import ScenarioConfig
        from flowstate_core.rng import make_rng
        from microsim.vehicles import build_corridor_plan, write_corridor_routes

        cfg = ScenarioConfig.from_yaml(REPO / scen_path)
        n = cfg.network
        plan = build_corridor_plan(list(n.inflow), cfg.sim.duration_s, cfg.fleet, cfg.av, make_rng(seed),
                                   ramps=n.ramps, corridor_edges=n.corridor_edges,
                                   entry_lane_shares=n.entry_lane_shares)
        fl = cfg.fleet
        lc = {"lc_strategic": fl.lc_strategic, "lc_keep_right": fl.lc_keep_right, "lc_cooperative": fl.lc_cooperative,
              "lc_assertive": fl.lc_assertive, "lc_speed_gain": fl.lc_speed_gain}
        lc.update(lc_over or {})
        write_corridor_routes(tuple(net["routes"]["main"]), plan, model or fl.model, action_step or cfg.sim.action_step_s,
                              wd / "r.rou.xml", lanes=4, routes=net["routes"], lc_strategic_ramp=fl.lc_strategic_ramp,
                              heavy=fl.heavy, jm_timegap_minor_s=fl.jm_timegap_minor_s,
                              jm_ignore_foe_prob=fl.jm_ignore_foe_prob, **lc)
        kinds = {plan.vehicle_id(i): plan.route_of(i) for i in range(plan.n)}
        params = {plan.vehicle_id(i): plan.params[i] for i in range(plan.n)}
        return {"n_planned": plan.n, "kinds": kinds, "params": params}

    return fn


def boundary(scen_path: str):
    import yaml

    raw = yaml.safe_load((REPO / scen_path).read_text())
    return [(float(t), float(v)) for t, v in raw["network"]["boundary"]["steps"]]


def _job(p):
    import fx

    arm = ARMS[p["arm"]]
    scen = SCEN[arm["scen"]]
    fleet = fx.fleet_from(scen)
    spec = dict(arm["spec"])
    wave_hi = A + B * 5491.93  # the measured span's end (data x 5,492)
    bnd = [(t, v * arm.get("bscale", 1.0)) for t, v in boundary(scen)]
    r = fx.run(spec, fleet, p["seed"], {}, 7800.0, boundary=bnd, sections=SECTIONS, track_lc=False,
               field_dt=300.0, route_fn=make_route_fn(scen), wave_span=(A, min(wave_hi, A + B * 5491.0), 600.0),
               tag=p["arm"])
    r.pop("cross_full", None)
    return r


def main():
    out = Path(sys.argv[1])
    args = sys.argv[2:]
    n = int(args[args.index("--n-seeds") + 1]) if "--n-seeds" in args else 10
    arms = [a for a in args if a in ARMS]
    jobs = [{"arm": a, "seed": s} for s in replica_seeds(n) for a in arms]
    t0 = time.time()
    with mp.get_context("spawn").Pool(2) as pool, out.open("a") as fh:
        for i, r in enumerate(pool.imap_unordered(_job, jobs), 1):
            fh.write(json.dumps(r, default=float) + "\n")
            fh.flush()
            print(f"{i}/{len(jobs)} {r['tag']} {time.time() - t0:.0f}s wall_run {r['wall_s']}", flush=True)


if __name__ == "__main__":
    main()
