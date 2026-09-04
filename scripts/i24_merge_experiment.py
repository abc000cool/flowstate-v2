"""Is the Old Hickory merge the replica's remaining defect? Four one-seed variants.

docs/I24_CAPACITY.md §5: with the capacity-calibrated population and the
fitted demand level, the replica's first kilometre (the Old Hickory Boulevard
on-ramp merge) runs a third too slow and the next kilometre a third too
fast, while everything downstream of 2.2 km is within 4% of the recording.
This script runs the ``i24_replica_speedcal`` arm, one seed, in four
variants and reports the same segment-speed table:

* ``as_is`` — the arm unchanged;
* ``oh_tracked`` — the Old Hickory on-ramp inflow at its tracked (uncorrected)
  level, everything else unchanged (is the ramp's coverage correction the
  overshoot?);
* ``oh_closed`` — the Old Hickory on-ramp inflow set to zero (an upper bound
  on the merge's effect on the mainline);
* ``lc_default`` — ``lc_strategic`` at SUMO's default 1.0 instead of the
  replica's 5.0 (the diverge fix; does it hurt the merge?);
* ``coop_0.5`` / ``coop_0`` — mainline cooperation (``lcCooperative``) halved
  / off;
* ``assertive_2`` / ``assertive_4`` — ramp and mainline gap acceptance
  (``lcAssertive``) doubled / quadrupled;
* ``speedgain_0`` — tactical lane changes (``lcSpeedGain``) off;
* ``coop_0.5_assertive_2`` — both merge levers together.

Each variant reports the segment-speed RMSPE over the fitted first hour and
the held-out second hour separately, so a merge setting can be adopted the
way the demand level was.

Writes ``artifacts/i24_merge_experiment.json``. Diagnostic only: nothing here
is a calibration, and no variant is adopted by this script.

Run: ``uv run --no-sync python scripts/i24_merge_experiment.py --procs 2``
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from i24_validate import _inputs, _segment_speeds, _sim_frame, _span

from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from microsim.runner import _versions, run_micro
from validation.metrics import rmspe

REPO = Path(__file__).resolve().parents[1]
ARM_YAML = REPO / "scenarios" / "i24_replica_speedcal.yaml"
TRACKED_YAML = REPO / "scenarios" / "i24_replica.yaml"
OBSERVED = REPO / "artifacts" / "i24_validation_observed.json"
OUT = REPO / "artifacts" / "i24_merge_experiment.json"
OH = "Old Hickory Blvd on-ramp"
VARIANTS = (
    "as_is",
    "oh_tracked",
    "oh_closed",
    "lc_default",
    "coop_0.5",
    "coop_0",
    "assertive_2",
    "assertive_4",
    "speedgain_0",
    "coop_0.5_assertive_2",
)
TRAIN_WINDOWS = range(0, 12)
TEST_WINDOWS = range(12, 24)


def variant_config(name: str) -> dict[str, Any]:
    raw = yaml.safe_load(ARM_YAML.read_text())
    ramps = {r["name"]: r for r in raw["network"]["ramps"]}
    if name == "oh_tracked":
        tracked = {
            r["name"]: r for r in yaml.safe_load(TRACKED_YAML.read_text())["network"]["ramps"]
        }
        ramps[OH]["inflow"] = tracked[OH]["inflow"]
    elif name == "oh_closed":
        ramps[OH]["inflow"] = [[t, 0.0] for t, _ in ramps[OH]["inflow"]]
    elif name == "lc_default":
        raw["fleet"]["lc_strategic"] = 1.0
    elif name == "coop_0.5":
        raw["fleet"]["lc_cooperative"] = 0.5
    elif name == "coop_0":
        raw["fleet"]["lc_cooperative"] = 0.0
    elif name == "assertive_2":
        raw["fleet"]["lc_assertive"] = 2.0
    elif name == "assertive_4":
        raw["fleet"]["lc_assertive"] = 4.0
    elif name == "speedgain_0":
        raw["fleet"]["lc_speed_gain"] = 0.0
    elif name == "coop_0.5_assertive_2":
        raw["fleet"]["lc_cooperative"] = 0.5
        raw["fleet"]["lc_assertive"] = 2.0
    elif name != "as_is":
        raise ValueError(name)
    raw["name"] = f"i24_merge_{name}"
    return raw


def _job(args: tuple[str, int]) -> dict[str, Any]:
    name, seed = args
    geo = _inputs()["geometry"]
    a, b = geo["sim_x_of_data_x"]["a"], geo["sim_x_of_data_x"]["b"]
    _lo, span_hi = _span()
    obs = np.array(json.loads(OBSERVED.read_text())["segment_speeds_ms"], dtype=float)
    cfg = ScenarioConfig.model_validate(variant_config(name))
    with tempfile.TemporaryDirectory() as td:
        t0 = time.perf_counter()
        paths = run_micro(cfg, seed, Path(td))
        meta = json.loads(paths.meta.read_text())
        seg = _segment_speeds(_sim_frame(paths.run_dir, a, b), span_hi, obs.shape[0])
    ok = np.isfinite(seg) & np.isfinite(obs)

    def _win(windows: range) -> float:
        m = np.zeros_like(ok)
        m[list(windows)] = True
        sel = ok & m
        return round(float(rmspe(seg[sel], obs[sel])), 4)

    return {
        "variant": name,
        "seed": seed,
        "config_hash": meta["config_hash"],
        "inserted_fraction": round(meta["n_vehicles_departed"] / meta["n_vehicles_planned"], 4),
        "ramps": meta.get("ramps"),
        "rmspe_all": round(float(rmspe(seg[ok], obs[ok])), 4),
        "rmspe_train": _win(TRAIN_WINDOWS),
        "rmspe_test": _win(TEST_WINDOWS),
        "segment_mean_kmh": [round(float(v) * 3.6, 2) for v in np.nanmean(seg, axis=0)],
        "segment_rel_error": [round(float(v), 4) for v in np.nanmean((seg - obs) / obs, axis=0)],
        "wall_s": round(time.perf_counter() - t0, 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--variants", nargs="*", default=list(VARIANTS))
    a = ap.parse_args()
    base = ScenarioConfig.model_validate(variant_config("as_is"))
    seed = spawn_seeds(base.seed, base.replicates)[0]
    with mp.get_context("spawn").Pool(min(a.procs, len(a.variants))) as pool:
        rows = pool.map(_job, [(v, seed) for v in a.variants])
    obs = np.array(json.loads(OBSERVED.read_text())["segment_speeds_ms"], dtype=float)
    result = {
        "schema_version": 1,
        "versions": _versions(),
        "arm": str(ARM_YAML.relative_to(REPO)),
        "arm_config_hash": config_hash(base),
        "seed": seed,
        "observed_segment_mean_kmh": [round(float(v) * 3.6, 2) for v in np.nanmean(obs, axis=0)],
        "variants": rows,
    }
    OUT.write_text(json.dumps(result, indent=1))
    for r in rows:
        oh = next((x for x in (r["ramps"] or []) if x["name"] == OH), None)
        print(
            f"  {r['variant']:<22} inserted={r['inserted_fraction']:.3f} rmspe all/train/test="
            f"{r['rmspe_all']:.3f}/{r['rmspe_train']:.3f}/{r['rmspe_test']:.3f} "
            f"OH {oh['n_departed'] if oh else '-'}/{oh['n_planned'] if oh else '-'} "
            f"seg km/h: {' '.join(f'{v:.0f}' for v in r['segment_mean_kmh'])}"
        )
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
