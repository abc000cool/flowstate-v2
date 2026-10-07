"""Run the golden cases and the weave fixtures with whichever packages lead sys.path
(PYTHONPATH) and record output digests. Case configs always come from the BEFORE tree
(identical configs in both legs). usage: cmp.py CFG_TREE OUT_JSON WORK [only,...]"""
import hashlib, json, os, sys, time, shutil
from pathlib import Path
CFG = Path(sys.argv[1]); OUT = Path(sys.argv[2]); WORK = Path(sys.argv[3])
ONLY = set(sys.argv[4].split(",")) if len(sys.argv) > 4 and sys.argv[4] else set()
sys.path.insert(0, str(CFG / "scripts"))
os.chdir(CFG)  # golden merge cases name their OSM fixture repo-relative
import microsim, flowstate_core
print("microsim from", microsim.__file__, "flowstate_core from", flowstate_core.__file__, flowstate_core.__file__, flush=True)
import merge_model_selfcheck as M
from microsim import run_micro
from validation.metrics import compute_metrics
from flowstate_core.config import config_hash
gold = M._load_test_module("test_microsim_golden")

def digest(paths):
    rd = Path(paths.run_dir)
    files = {}
    for f in sorted(rd.rglob("*.parquet")):
        files[str(f.relative_to(rd))] = hashlib.sha256(f.read_bytes()).hexdigest()
    meta = json.loads(paths.meta.read_text())
    for k in ("wall_time_s", "realtime_factor"):
        meta.pop(k, None)
    text = json.dumps(meta, sort_keys=True).replace(str(rd), "<RUN>").replace(str(WORK), "<WORK>")
    m = compute_metrics(rd)
    mtxt = json.dumps(m.__dict__ if hasattr(m, "__dict__") else m, sort_keys=True, default=str)
    return {"parquet": files, "meta_sha": hashlib.sha256(text.encode()).hexdigest(),
            "metrics_sha": hashlib.sha256(mtxt.encode()).hexdigest(), "config_hash": meta["config_hash"],
            "n_collisions": meta["n_collisions"],
            "weave": [{k: z.get(k) for k in ("ramp", "n_missed", "n_missed_exit", "n_entrant_took_exit")} for z in meta.get("weave_sections") or []]}

cases = []
for name in sorted(gold.CASES):
    cases.append(("golden:" + name, lambda name=name: (gold.case_config(name), gold.case_config(name).seed)))
FIX = [("th52_corridor", 3), ("th52_corridor", 4), ("th52_capacity", 4), ("th52_capacity", 5), ("th52_corridor_demand", 3),
       ("th52_upstream", 3), ("th52_upstream_fleet", 3), ("ruth_entr", 3), ("ruth_exit", 3), ("ruth_entr_fleet", 3),
       ("ruth_exit_fleet", 3), ("ruth_exit_fleet_271", 3), ("weave_moderate", 3), ("weave_golden", 3),
       ("mcknight", 3), ("mcknight", 4), ("mcknight", 5), ("th61", 3), ("merge_golden", 3)]
for name, seed in FIX:
    cases.append((f"fixture:{name}:s{seed}", lambda name=name, seed=seed: (M.fixture(name, seed)[0], seed)))
DC = CFG / "scenarios/mndot_i94_wb_stpaul_weave_dc.yaml"
mmt = M._load_test_module("test_microsim_merge_managed_meter")
for seed in (3, 4, 5):
    cases.append((f"th52_dc:s{seed}", lambda seed=seed: (M.with_fleet(mmt._th52_corridor_config(seed), None, DC), seed)))
for name, seed in (("th52_corridor", 3), ("ruth_entr", 3), ("mcknight", 3)):
    def f(name=name, seed=seed):
        cfg, extra = M.fixture(name, seed)
        return M.to_model(cfg, "measured", extra), seed
    cases.append((f"measured:{name}:s{seed}", f))
res = {}
for key, make in cases:
    if ONLY and key not in ONLY:
        continue
    cfg, seed = make()
    t0 = time.perf_counter()
    paths = run_micro(cfg, seed, WORK / key.replace(":", "_"))
    res[key] = {**digest(paths), "wall_s": round(time.perf_counter() - t0, 1)}
    shutil.rmtree(paths.run_dir, ignore_errors=True)
    print(key, res[key]["config_hash"], res[key]["meta_sha"][:10], res[key]["wall_s"], flush=True)
    OUT.write_text(json.dumps(res, indent=1))
print("done", len(res))
