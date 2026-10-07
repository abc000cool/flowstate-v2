"""Amendment W2's stress sets X-R1, X-R2, X-T1, X-T2 (docs/I94_CAL_COLLISIONS.md §13.3), one arm per call.

usage (repository root):
  repro.py SET OUT_JSON WORK_DIR [--seeds 3-22] [--weave-set KEY=VALUE ...]

SET is one of xr1, xr2, xt1, xt2 (configs fixed in the pre-registration, §13.3):

* xr1  ``ruth_entr`` (RUTH_DEMAND entrance_peak) with the calibrated I-94 drivers, network.boundary
       2 m/s on the last corridor edge from t = 300 s, 2,400 s;
* xr2  ``ruth_exit`` (RUTH_DEMAND exit_peak), the calibrated drivers, 1 m/s from t = 300 s, 2,400 s;
* xt1  the T.H.52 section test's fixture (``th52_corridor``) with the calibrated drivers and
       ``ramp_to_ramp_share`` 0 on the weave;
* xt2  ``th52_corridor_demand`` with the calibrated drivers and ``ramp_to_ramp_share`` 0.

Runs one SUMO process at a time, keeps every run tree under WORK_DIR/<fixture>_weave/<hash>/<seed>/
(the layout of ``merge_model_selfcheck.py --keep``) and writes the rows of ``run_summary`` (plus
``th52_criteria`` for xt1) with the set name to OUT_JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))

import merge_model_selfcheck as M  # noqa: E402

DC = REPO / "scenarios" / "mndot_i94_wb_stpaul_weave_dc.yaml"

#: (fixture, boundary steps or None, duration override or None, ramp_to_ramp_share or None)
SETS: dict[str, tuple[str, list[list[float]] | None, float | None, float | None]] = {
    "xr1": ("ruth_entr", [[300.0, 2.0]], 2400.0, None),
    "xr2": ("ruth_exit", [[300.0, 1.0]], 2400.0, None),
    "xt1": ("th52_corridor", None, None, 0.0),
    "xt2": ("th52_corridor_demand", None, None, 0.0),
}


def config(name: str, seed: int, weave_set: dict[str, float]):  # type: ignore[no-untyped-def]
    """The stress config of SET ``name`` at ``seed`` with ``weave_set`` on every weave block."""
    from flowstate_core.config import ScenarioConfig

    fixture, boundary, duration, share = SETS[name]
    cfg, _ = M.fixture(fixture, seed)
    cfg = M.with_fleet(cfg, None, DC)
    raw = cfg.model_dump(mode="json")
    raw["name"] = f"{raw['name']}_{name}"
    if boundary is not None:
        raw["network"]["boundary"] = {"kind": "speed_schedule", "steps": boundary}
    if duration is not None:
        raw["sim"]["duration_s"] = duration
    cfg = ScenarioConfig.model_validate(raw)
    cfg = M.with_ramp_to_ramp_share(cfg, share)
    return M.with_weave_params(cfg, weave_set)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("set", choices=sorted(SETS))
    ap.add_argument("out", type=Path)
    ap.add_argument("work", type=Path)
    ap.add_argument("--seeds", default="3-22")
    ap.add_argument("--weave-set", action="append", default=None)
    a = ap.parse_args()
    from microsim import run_micro

    weave_set = M.parse_weave_set(a.weave_set)
    fixture = SETS[a.set][0]
    rows = []
    for seed in M._seeds(a.seeds):
        cfg = config(a.set, seed, weave_set)
        t0 = time.perf_counter()
        paths = run_micro(cfg, seed, a.work / f"{fixture}_weave")
        wall = time.perf_counter() - t0
        row = {"set": a.set, "fixture": fixture, "model": "weave", **M.run_summary(paths, wall)}
        if fixture == "th52_corridor":
            crit = M.th52_criteria(paths)
            row.update({k: crit[k] for k in ("exit_end_flow_vph", "exits_given_up", "entrance_departed")})
        row["run_dir"] = str(paths.run_dir)
        rows.append(row)
        print(json.dumps({k: row[k] for k in ("set", "seed", "collisions", "lock", "wall_s")}), flush=True)
        a.out.write_text(json.dumps({"set": a.set, "weave_set": weave_set, "rows": rows}, indent=1, default=float))


if __name__ == "__main__":
    main()
