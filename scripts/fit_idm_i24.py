"""ROADMAP §1.2 task 2 — IDM population calibration from I-24 MOTION episodes.

Runs the calibration package's population fit (seeded differential evolution
per episode, gap-RMSE objective, 70/30 holdout, q = 0.9 RMSE trim) over the
episodes extracted by ``scripts/i24_extract_episodes.py``. When the episode
count exceeds ``--max-episodes`` a **seeded random subsample** is fitted and
the artifact notes say so; the default cap keeps the run inside a few hours
on 8 processes at 5 Hz (each episode costs ~3 s to fit).

Input: ``data/i24motion/processed/i24_wb_episodes.pkl``.
Output: ``artifacts/idm_i24.json`` (IDMCalibration artifact).

Run: ``uv run --no-sync python scripts/fit_idm_i24.py [--max-episodes N] [--procs P]``
"""

from __future__ import annotations

import argparse
import json
import pickle
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from calibration.idm_fit import PARAM_ORDER, fit_population
from calibration.loaders.i24motion import I24_CITATION
from flowstate_core.rng import make_rng

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_data import PROCESSED_DIR, REPO_ROOT, data_hash

SEED = 42

NOTES = (
    "I-24 MOTION INCEPTION v1.x westbound, 30 Nov 2022 06:00-10:00 CST (Gloudemans et al. "
    "2023). Positions are the pipeline's smoothed back-center coordinates at 25 Hz; speed "
    "is their gradient (no NGSIM-style differentiation noise), decimated to 5 Hz. Episodes "
    "are cut on unstitched trajectory FRAGMENTS (median fragment ~6 s / ~120 m), so only "
    "the ~11% of fragments lasting >= 30 s can host an episode and episodes are short "
    "(rarely > 60 s); long-horizon behaviour (v0) is therefore still weakly excited. "
    "Leaders are position-ordered within the lane (the schema publishes none): a gap "
    "outside [0.5, 100] m is masked as 'untracked true leader' / 'duplicate fragment' and "
    "cuts the episode, but an untracked leader closer than 100 m cannot be detected, so a "
    "minority of episodes pair a follower with the wrong vehicle; the q=0.9 RMSE trim and "
    "the holdout number carry that cost honestly. Followers: passenger classes 0-3, "
    "mainline lanes 1-4."
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--max-episodes", type=int, default=12000)
    ap.add_argument("--procs", type=int, default=8)
    ap.add_argument("--de-maxiter", type=int, default=60)
    args = ap.parse_args()

    with open(PROCESSED_DIR / "i24_wb_episodes.pkl", "rb") as f:
        episodes = pickle.load(f)
    n_all = len(episodes)
    subsample_note = ""
    if args.max_episodes > 0 and n_all > args.max_episodes:
        rng = make_rng(SEED)
        idx = np.sort(rng.choice(n_all, size=args.max_episodes, replace=False))
        episodes = [episodes[i] for i in idx]
        subsample_note = (
            f"Seeded (seed {SEED}) random subsample of {len(episodes)} of {n_all} extracted "
            "episodes fitted for wall-clock budget."
        )
    created_at = subprocess.run(
        ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True, check=True
    ).stdout.strip()
    print(
        f"fitting {len(episodes)} of {n_all} episodes with {args.procs} processes (seed {SEED}) ...",
        flush=True,
    )
    t0 = time.perf_counter()
    cal = fit_population(
        episodes,
        seed=SEED,
        created_at=created_at,
        source=(
            "I-24 MOTION INCEPTION v1.x, 30 Nov 2022 westbound (6386d89efb3ff533c12df167__post10), "
            f"{len(episodes)} of {n_all} leader-follower episodes (>= 30 s, 5 Hz, passenger "
            "followers, mainline lanes 1-4); cite " + I24_CITATION
        ),
        data_hash=data_hash(),
        holdout_frac=0.3,
        trim_quantile=0.9,
        de_maxiter=args.de_maxiter,
        n_procs=args.procs,
        notes=(subsample_note + " " if subsample_note else "") + NOTES,
    )
    wall = time.perf_counter() - t0
    out = REPO_ROOT / "artifacts" / "idm_i24.json"
    cal.save(out)
    sd = np.sqrt(np.diag(np.array(cal.cov)))
    print(f"done in {wall:.0f} s -> {out}")
    print(f"train/holdout: {cal.n_episodes_fit}/{cal.n_episodes_holdout}")
    print(f"holdout gap RMSE (population-mean params): {cal.holdout_gap_rmse_m:.2f} m")
    for i, name in enumerate(PARAM_ORDER):
        print(f"  {name:6s} mean={cal.mean[name]:7.3f}  sd={sd[i]:6.3f}")
    rmses = np.array(cal.per_episode_rmse_m)
    print(
        f"training per-episode gap RMSE: median={np.median(rmses):.2f} m, "
        f"mean={rmses.mean():.2f} m, q90={np.quantile(rmses, 0.9):.2f} m"
    )
    (PROCESSED_DIR / "i24_idm_fit_run.json").write_text(
        json.dumps(
            {
                "n_episodes_available": n_all,
                "n_episodes_fitted": len(episodes),
                "procs": args.procs,
                "wall_s": round(wall, 1),
                "artifact": str(out.relative_to(REPO_ROOT)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
