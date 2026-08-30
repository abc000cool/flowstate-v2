"""M2 task 1 — extract leader-follower episodes from NGSIM US-101.

Loads all ``data/ngsim/us101_chunk_*.csv`` chunks through the package NGSIM
loader, dedupes, splits recording periods (see ``scripts/us101_data.py``),
and extracts ≥ 30 s continuous car-following episodes for v_class-2 autos on
mainline lanes 1-5 with a valid, known-length leader.

Outputs (both under ``data/processed/``, which is gitignored):

* ``us101_episodes.pkl`` — pickled ``list[LeaderFollowerEpisode]`` consumed
  by the IDM fit driver.
* ``us101_episode_summary.json`` — per-period episode counts and duration
  stats; every number in docs/M2_RESULTS.md traces here.

Run: ``uv run --no-sync python scripts/extract_us101_episodes.py``
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from us101_data import PROCESSED_DIR, build_period_episodes, data_hash, load_us101


def main() -> None:
    periods = load_us101()
    all_eps = []
    summary: dict[str, object] = {"periods": {}}
    for label, df in periods.items():
        eps = build_period_episodes(df, label)
        durs = np.array([ep.duration_s for ep in eps])
        gt = df["global_time_ms"]
        summary["periods"][label] = {  # type: ignore[index]
            "rows": len(df),
            "vehicles": int(df["veh_id"].nunique()),
            "wall_clock_start_ms": int(gt.min()),
            "wall_clock_end_ms": int(gt.max()),
            "n_episodes": len(eps),
            "episode_duration_s": {
                "min": float(durs.min()) if len(eps) else None,
                "median": float(np.median(durs)) if len(eps) else None,
                "max": float(durs.max()) if len(eps) else None,
                "total": float(durs.sum()) if len(eps) else None,
            },
        }
        all_eps.extend(eps)
        print(f"{label}: {len(df)} rows, {len(eps)} episodes")
    summary["n_episodes_total"] = len(all_eps)
    summary["data_hash"] = data_hash()
    summary["filters"] = (
        "followers v_class=2 (auto), lanes 1-5 (mainline), >=30 s continuous, "
        "uniform dt 0.1 s, no lane change, no leader change, leader present in "
        "recording with known v_Length; non-positive recorded gaps masked as cuts"
    )

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with open(PROCESSED_DIR / "us101_episodes.pkl", "wb") as f:
        pickle.dump(all_eps, f)
    (PROCESSED_DIR / "us101_episode_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"total: {len(all_eps)} episodes -> {PROCESSED_DIR / 'us101_episodes.pkl'}")


if __name__ == "__main__":
    main()
