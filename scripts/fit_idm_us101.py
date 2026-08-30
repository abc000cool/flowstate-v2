"""M2 task 2 — IDM population calibration from NGSIM US-101 episodes.

Runs the calibration package's population fit (seeded differential evolution
per episode, gap-RMSE objective, 70/30 holdout, q=0.9 RMSE trim) over ALL
extracted episodes — at ~1.3 s per episode fit the full set (2,452 episodes)
completes in well under the 60-minute budget with 8 worker processes, so no
longest-N subselection is applied.

Input: ``data/processed/us101_episodes.pkl`` (run
``scripts/extract_us101_episodes.py`` first).
Output: ``artifacts/idm_us101.json`` (IDMCalibration artifact) and a fit
summary printed to stdout.

Run: ``uv run --no-sync python scripts/fit_idm_us101.py``
"""

from __future__ import annotations

import pickle
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from calibration.idm_fit import PARAM_ORDER, fit_population

sys.path.insert(0, str(Path(__file__).resolve().parent))
from us101_data import PROCESSED_DIR, REPO_ROOT, data_hash

SEED = 42
N_PROCS = 8

NOTES = (
    "Raw (not Montanino-Punzo reconstructed) NGSIM trajectories: speeds/accelerations "
    "carry known differentiation noise; the gap-RMSE objective (Kesting & Treiber 2008) "
    "mitigates but does not remove this — treat per-episode parameters as noisy. "
    "Site is heavily congested (US-101 AM peak): v0 (desired speed) is weakly "
    "identified because followers rarely drive near free flow; its population "
    "statistics mostly reflect the search bounds, not driver preference. "
    "Episodes: v_class-2 autos, mainline lanes 1-5, >=30 s continuous, no lane "
    "change, leader length known; periods split on recording origin "
    "(vehicle ids restart per 15-min period)."
)


def main() -> None:
    with open(PROCESSED_DIR / "us101_episodes.pkl", "rb") as f:
        episodes = pickle.load(f)
    created_at = subprocess.run(
        ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True, check=True
    ).stdout.strip()
    print(f"fitting {len(episodes)} episodes with {N_PROCS} processes (seed {SEED}) ...")
    t0 = time.perf_counter()
    cal = fit_population(
        episodes,
        seed=SEED,
        created_at=created_at,
        source=(
            "NGSIM US-101 raw vehicle trajectories (data.transportation.gov 8ect-6jqj, "
            "location='us-101'; 2005-06-15 07:50-08:20 PDT, periods 1 + partial 2 of the "
            "2.4M-row ordered dump); ALL 2452 extracted episodes, no subselection"
        ),
        data_hash=data_hash(),
        holdout_frac=0.3,
        trim_quantile=0.9,
        n_procs=N_PROCS,
        notes=NOTES,
    )
    wall = time.perf_counter() - t0
    out = REPO_ROOT / "artifacts" / "idm_us101.json"
    cal.save(out)
    sd = np.sqrt(np.diag(np.array(cal.cov)))
    print(f"done in {wall:.0f} s -> {out}")
    print(f"train/holdout: {cal.n_episodes_fit}/{cal.n_episodes_holdout}")
    print(f"holdout gap RMSE (population-mean params): {cal.holdout_gap_rmse_m:.2f} m")
    for i, name in enumerate(PARAM_ORDER):
        print(f"  {name:6s} mean={cal.mean[name]:7.3f}  sd={sd[i]:6.3f}")
    rmses = np.array(cal.per_episode_rmse_m)
    print(
        f"training per-episode gap RMSE: median={np.median(rmses):.2f} m, q90={np.quantile(rmses, 0.9):.2f} m"
    )


if __name__ == "__main__":
    main()
