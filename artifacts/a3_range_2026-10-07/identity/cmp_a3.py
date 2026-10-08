"""Byte identity of the per-window ramp-to-ramp share's build (docs/A3_RANGE_ROUND.md §3).

W1's method (artifacts/weave_loss_2026-10-07/w1/harness/cmp.py, docs/WEAVE_LOSS_DIAGNOSIS.md §8.2), extended: run
the micro goldens and the weave fixtures with whichever packages lead sys.path (PYTHONPATH on one tree's
packages) and record per run every Parquet file's sha256, the planned route file's sha256, the whole meta.json
(wall time removed), compute_metrics and the config hash. Case configs always come from CFG_TREE (the HEAD tree in
both legs).

usage: cmp_a3.py CFG_TREE OUT_JSON WORK [only,...]

Cases beyond W1's 37: the T.H.52 section with the calibrated drivers and
* ``th52_dc_explicit_none``: every weave block's ``ramp_to_ramp_share`` present as null (seed 3);
* ``th52_dc_share05``: the single share 0.5 (seeds 3, 4; the refactored single-share path);
* ``th52_dc_u0`` / ``th52_dc_u05`` / ``th52_dc_u1``: the per-window form at u = 0 (seeds 3-5), 0.5 and 1 (seed 3),
  run only where the packages know the form (the change tree); recorded with their ramp_to_ramp_shares record.
"""

import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

CFG = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
WORK = Path(sys.argv[3]).resolve()
ONLY = set(sys.argv[4].split(",")) if len(sys.argv) > 4 and sys.argv[4] else set()
sys.path.insert(0, str(CFG / "scripts"))
os.chdir(CFG)  # golden merge cases name their OSM fixture repo-relative
import flowstate_core  # noqa: E402
import microsim  # noqa: E402

print(
    "microsim from", microsim.__file__, "flowstate_core from", flowstate_core.__file__, flush=True
)
import merge_model_selfcheck as M  # noqa: E402

from flowstate_core import config as fc  # noqa: E402
from flowstate_core.config import ScenarioConfig  # noqa: E402
from microsim import run_micro  # noqa: E402
from validation.metrics import compute_metrics  # noqa: E402

KNOWS_RANGE = hasattr(fc, "RampToRampRange")
gold = M._load_test_module("test_microsim_golden")


def digest(paths):
    rd = Path(paths.run_dir)
    files = {}
    for f in sorted(rd.rglob("*.parquet")):
        files[str(f.relative_to(rd))] = hashlib.sha256(f.read_bytes()).hexdigest()
    routes = rd / "net" / "demand.rou.xml"
    meta = json.loads(paths.meta.read_text())
    for k in ("wall_time_s", "realtime_factor"):
        meta.pop(k, None)
    text = json.dumps(meta, sort_keys=True).replace(str(rd), "<RUN>").replace(str(WORK), "<WORK>")
    m = compute_metrics(rd)
    mtxt = json.dumps(m.__dict__ if hasattr(m, "__dict__") else m, sort_keys=True, default=str)
    return {
        "parquet": files,
        "routes_sha": hashlib.sha256(routes.read_bytes()).hexdigest() if routes.is_file() else None,
        "meta_sha": hashlib.sha256(text.encode()).hexdigest(),
        "meta_wo_config_sha": hashlib.sha256(
            json.dumps(
                {k: v for k, v in meta.items() if k not in ("config", "config_hash")},
                sort_keys=True,
            )
            .replace(str(rd), "<RUN>")
            .replace(str(WORK), "<WORK>")
            .encode()
        ).hexdigest(),
        "metrics_sha": hashlib.sha256(mtxt.encode()).hexdigest(),
        "config_hash": meta["config_hash"],
        "n_collisions": meta["n_collisions"],
        "ramp_to_ramp_shares": meta.get("ramp_to_ramp_shares"),
        "weave": [
            {k: z.get(k) for k in ("ramp", "n_missed", "n_missed_exit", "n_entrant_took_exit")}
            for z in meta.get("weave_sections") or []
        ],
    }


cases = []
for name in sorted(gold.CASES):
    cases.append(
        ("golden:" + name, lambda name=name: (gold.case_config(name), gold.case_config(name).seed))
    )
FIX = [
    ("th52_corridor", 3),
    ("th52_corridor", 4),
    ("th52_capacity", 4),
    ("th52_capacity", 5),
    ("th52_corridor_demand", 3),
    ("th52_upstream", 3),
    ("th52_upstream_fleet", 3),
    ("ruth_entr", 3),
    ("ruth_exit", 3),
    ("ruth_entr_fleet", 3),
    ("ruth_exit_fleet", 3),
    ("ruth_exit_fleet_271", 3),
    ("weave_moderate", 3),
    ("weave_golden", 3),
    ("mcknight", 3),
    ("mcknight", 4),
    ("mcknight", 5),
    ("th61", 3),
    ("merge_golden", 3),
]
for name, seed in FIX:
    cases.append(
        (f"fixture:{name}:s{seed}", lambda name=name, seed=seed: (M.fixture(name, seed)[0], seed))
    )
DC = CFG / "scenarios/mndot_i94_wb_stpaul_weave_dc.yaml"
mmt = M._load_test_module("test_microsim_merge_managed_meter")


def th52_dc(seed, share="unset"):
    cfg = M.with_fleet(mmt._th52_corridor_config(seed), None, DC)
    if share == "unset":
        return cfg
    raw = cfg.model_dump(mode="json")
    for r in raw["network"]["ramps"]:
        if r.get("weave"):
            r["weave"]["ramp_to_ramp_share"] = share
    return ScenarioConfig.model_validate(raw)


for seed in (3, 4, 5):
    cases.append((f"th52_dc:s{seed}", lambda seed=seed: (th52_dc(seed), seed)))
for name, seed in (("th52_corridor", 3), ("ruth_entr", 3), ("mcknight", 3)):

    def f(name=name, seed=seed):
        cfg, extra = M.fixture(name, seed)
        return M.to_model(cfg, "measured", extra), seed

    cases.append((f"measured:{name}:s{seed}", f))
cases.append(("th52_dc_explicit_none:s3", lambda: (th52_dc(3, None), 3)))
for seed in (3, 4):
    cases.append((f"th52_dc_share05:s{seed}", lambda seed=seed: (th52_dc(seed, 0.5), seed)))
RANGE = [(f"th52_dc_u0:s{s}", s, 0.0) for s in (3, 4, 5)] + [
    ("th52_dc_u05:s3", 3, 0.5),
    ("th52_dc_u1:s3", 3, 1.0),
]
for key, seed, u in RANGE:
    cases.append(
        (key, lambda seed=seed, u=u: (th52_dc(seed, {"u": u}), seed) if KNOWS_RANGE else None)
    )

res = {
    "_packages": {"microsim": microsim.__file__, "flowstate_core": flowstate_core.__file__},
    "_knows_range": KNOWS_RANGE,
}
for key, make in cases:
    if ONLY and key not in ONLY:
        continue
    made = make()
    if made is None:
        res[key] = {"skipped": "these packages have no per-window form (RampToRampRange)"}
        continue
    cfg, seed = made
    t0 = time.perf_counter()
    paths = run_micro(cfg, seed, WORK / key.replace(":", "_"))
    res[key] = {**digest(paths), "wall_s": round(time.perf_counter() - t0, 1)}
    shutil.rmtree(paths.run_dir, ignore_errors=True)
    print(key, res[key]["config_hash"], res[key]["meta_sha"][:10], res[key]["wall_s"], flush=True)
    OUT.write_text(json.dumps(res, indent=1))
OUT.write_text(json.dumps(res, indent=1))
print("done", len(res) - 2)
