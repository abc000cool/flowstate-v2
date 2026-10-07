"""Run one arm of the T.H.52 section test on the HEAD snapshot with existing knobs.

usage: arm.py NAME SEEDS [--fleet k=v ...] [--weave k=v ...] [--net k=v ...] [--keep] [--ceiling]
Writes $S/arms/NAME.json (rows: th52_criteria + diagnostics). Run trees deleted unless --keep.
"""
import argparse, json, shutil, sys, time
from pathlib import Path
S = Path(__file__).resolve().parent
H = S / "head"
sys.path.insert(0, str(H / "scripts")); sys.path.insert(0, str(S))
import os
os.chdir(H)
import merge_model_selfcheck as M
import strand
from flowstate_core.config import ScenarioConfig

def kv(items):
    out = {}
    for it in items or []:
        k, v = it.split("=", 1)
        try: v = float(v)
        except ValueError: pass
        out[k] = v
    return out

ap = argparse.ArgumentParser()
ap.add_argument("name"); ap.add_argument("seeds")
ap.add_argument("--fleet", nargs="*"); ap.add_argument("--weave", nargs="*"); ap.add_argument("--net", nargs="*")
ap.add_argument("--keep", action="store_true"); ap.add_argument("--ceiling", action="store_true")
ap.add_argument("--relocate-p", type=float, default=None)
a = ap.parse_args()
fleet, weave, netkw = kv(a.fleet), kv(a.weave), kv(a.net)
FLEET_FROM = H / "scenarios/mndot_i94_wb_stpaul_weave_dc.yaml"
mmt = M._load_test_module("test_microsim_merge_managed_meter")

def config(seed):
    cfg = M.with_fleet(mmt._th52_corridor_config(seed), None, FLEET_FROM)
    raw = cfg.model_dump(mode="json")
    raw["fleet"].update(fleet)
    raw["network"].update(netkw)
    for r in raw["network"]["ramps"]:
        if r.get("weave"):
            r["weave"]["weave_params"] = {**r["weave"].get("weave_params", {}), **weave}
    raw["name"] = f"th52_{a.name}"
    return ScenarioConfig.model_validate(raw)

work = S / "arms_runs" / a.name
rows = []
from microsim import run_micro
from microsim import runner as R
orig = R._build_plan_and_routes
if a.ceiling:
    import dataclasses
    def build(cfg, bundle, rng, routes_path, **kw):
        plan = orig(cfg, bundle, rng, routes_path, **kw)
        M.relocate_crossings(routes_path, cfg, "th52", "th52 exit")
        k, j = M._crossing_pair(cfg, "th52", "th52 exit")
        return dataclasses.replace(plan, route=tuple(M.relocated_route(r, k, j) or r for r in plan.route))
    R._build_plan_and_routes = build
RELOC = {}
if a.relocate_p is not None:
    import dataclasses, re, random
    def build(cfg, bundle, rng, routes_path, **kw):
        plan = orig(cfg, bundle, rng, routes_path, **kw)
        k, j = M._crossing_pair(cfg, "th52", "th52 exit")
        pick = random.Random(int(cfg.seed) * 1000003 + 17)
        text = routes_path.read_text()
        chosen, lcs_of = set(), {}
        main_lcs = float(cfg.fleet.lc_strategic)
        ramp_lcs = cfg.fleet.lc_strategic_ramp
        ramp_lcs = main_lcs if ramp_lcs is None else float(ramp_lcs)
        def veh(m):
            line = m.group(0)
            vid = re.search(r' id="([^"]+)"', line).group(1)
            rid = re.search(r'route="([^"]+)"', line).group(1)
            to = M.relocated_route(rid, k, j)
            if to is None or pick.random() >= a.relocate_p:
                return line
            chosen.add(vid)
            vtype = re.search(r'type="([^"]+)"', line).group(1)
            lcs_of[vtype] = main_lcs if to.startswith("main") else ramp_lcs
            return re.sub(r'departLane="[^"]*"', 'departLane="free"', line.replace(f'route="{rid}"', f'route="{to}"'))
        text = re.sub(r"<vehicle [^>]*/>", veh, text)
        def vt(m):
            line = m.group(0)
            tid = re.search(r'id="([^"]+)"', line).group(1)
            if tid not in lcs_of:
                return line
            line = re.sub(r' lcStrategic="[^"]*"', "", line)
            if lcs_of[tid] != 1.0:
                line = line.replace("/>", f' lcStrategic="{lcs_of[tid]:g}"/>')
            return line
        text = re.sub(r"<vType [^>]*/>", vt, text)
        routes_path.write_text(text)
        idx = {int(v[1:]) for v in chosen}
        route = tuple((M.relocated_route(r, k, j) or r) if i in idx else r for i, r in enumerate(plan.route))
        RELOC["n"] = len(chosen)
        return dataclasses.replace(plan, route=route)
    R._build_plan_and_routes = build
for seed in M._seeds(a.seeds):
    t0 = time.perf_counter()
    paths = run_micro(config(seed), seed, work)
    row = {**M.th52_criteria(paths), "wall_s": round(time.perf_counter() - t0, 2)}
    try:
        row["diag"] = strand.one(str(paths.run_dir))
    except Exception as e:  # ceiling runs have no weave section counters
        row["diag"] = {"error": repr(e)}
    row["relocated_n"] = RELOC.get("n")
    rows.append(row)
    print(json.dumps({k: row[k] for k in ("seed", "exit_end_flow_vph", "station_speed_min_ms", "n_collisions", "exits_given_up", "hard_brake_vehicle_steps")}), flush=True)
    if not a.keep:
        shutil.rmtree(paths.run_dir, ignore_errors=True)
R._build_plan_and_routes = orig
(S / "arms").mkdir(exist_ok=True)
(S / "arms" / f"{a.name}.json").write_text(json.dumps({"name": a.name, "fleet": fleet, "weave": weave, "net": netkw, "ceiling": a.ceiling, "rows": rows}, indent=1, default=float))
print("wrote", S / "arms" / f"{a.name}.json")
