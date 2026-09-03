"""ROADMAP §1.2 task 1 — leader-follower episodes from I-24 MOTION westbound.

Reads the 5 Hz Parquet (``scripts/i24_extract.py``) lane by lane, pairs each
mainline vehicle with the nearest tracked vehicle ahead in its lane at the
same 0.2 s grid slot, and cuts ≥ 30 s continuous car-following episodes for
passenger-class followers (``scripts/i24_data.build_lane_episodes``; gap
plausibility masks documented there). Fragments are used as delivered — no
stitching — so an episode never outlives the shorter of its two fragments.

Outputs (``data/i24motion/processed/``, gitignored):

* ``i24_wb_episodes.pkl`` — pickled ``list[LeaderFollowerEpisode]``
* ``i24_wb_episode_summary.json`` — per-lane counts, duration/gap/speed
  statistics, pairing yield; every number in docs/I24_DATA.md traces here.

Run: ``uv run --no-sync python scripts/i24_extract_episodes.py``
"""

from __future__ import annotations

import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_data import (
    MAINLINE_LANES,
    MAX_GAP_M,
    MIN_GAP_M,
    PROCESSED_DIR,
    WB_DIR,
    build_lane_episodes,
    clock,
    data_hash,
    load_vehicles,
)

from calibration.loaders.i24motion import I24_PASSENGER_CLASSES, load_i24_parquet

MIN_DURATION_S = 30.0
V_CONGESTED_MS = 40.0 / 3.6


def main() -> None:
    t0 = time.perf_counter()
    veh = load_vehicles()
    all_eps = []
    per_lane: dict[str, object] = {}
    for lane in range(MAINLINE_LANES[0], MAINLINE_LANES[1] + 1):
        df = load_i24_parquet(
            WB_DIR, lanes=(lane, lane), columns=["t", "veh_id", "x", "v", "length", "cls"]
        )
        eps = build_lane_episodes(df, lane, min_duration_s=MIN_DURATION_S)
        n_frag_ge30 = int(
            (
                (veh["duration_s"] >= MIN_DURATION_S) & veh["cls"].isin(list(I24_PASSENGER_CLASSES))
            ).sum()
        )
        durs = np.array([ep.duration_s for ep in eps])
        per_lane[str(lane)] = {
            "rows": len(df),
            "n_episodes": len(eps),
            "n_passenger_fragments_ge_30s_all_lanes": n_frag_ge30,
            "episode_duration_s": {
                "min": float(durs.min()) if len(eps) else None,
                "median": float(np.median(durs)) if len(eps) else None,
                "max": float(durs.max()) if len(eps) else None,
                "total": float(durs.sum()) if len(eps) else None,
            },
        }
        all_eps.extend(eps)
        print(
            f"lane {lane}: {len(df)} rows -> {len(eps)} episodes "
            f"({time.perf_counter() - t0:.0f} s)",
            flush=True,
        )
        del df

    durs = np.array([ep.duration_s for ep in all_eps])
    starts = np.array([float(ep.t[0]) for ep in all_eps])
    gaps = np.concatenate([ep.gap_m for ep in all_eps]) if all_eps else np.array([])
    vf = np.concatenate([ep.v_follower for ep in all_eps]) if all_eps else np.array([])
    hist_edges = np.arange(0, 4 * 3600 + 1, 900)
    by_quarter = np.histogram(starts, bins=hist_edges)[0] if all_eps else np.zeros(16, int)
    summary = {
        "data_hash": data_hash(),
        "min_duration_s": MIN_DURATION_S,
        "gap_bounds_m": [MIN_GAP_M, MAX_GAP_M],
        "follower_classes": sorted(I24_PASSENGER_CLASSES),
        "lanes": per_lane,
        "n_episodes_total": len(all_eps),
        "n_distinct_followers": len({ep.veh_id for ep in all_eps}),
        "episode_duration_s": {
            "min": float(durs.min()) if len(all_eps) else None,
            "median": float(np.median(durs)) if len(all_eps) else None,
            "p90": float(np.percentile(durs, 90)) if len(all_eps) else None,
            "max": float(durs.max()) if len(all_eps) else None,
            "total_h": float(durs.sum() / 3600.0) if len(all_eps) else None,
        },
        "episodes_by_15min": {
            f"{clock(lo)}": int(n) for lo, n in zip(hist_edges[:-1], by_quarter, strict=True)
        },
        "samples": {
            "n": int(vf.size),
            "gap_m_median": float(np.median(gaps)) if gaps.size else None,
            "gap_m_p90": float(np.percentile(gaps, 90)) if gaps.size else None,
            "v_follower_ms_median": float(np.median(vf)) if vf.size else None,
            "frac_samples_below_40kmh": float((vf < V_CONGESTED_MS).mean()) if vf.size else None,
        },
        "filters": (
            "followers of coarse class 0-3 (sedan/midsize/van/pickup), mainline lanes 1-4, "
            f">= {MIN_DURATION_S:g} s continuous, uniform 0.2 s dt (5 Hz), single lane, "
            "single position-ordered leader; gap outside "
            f"[{MIN_GAP_M:g}, {MAX_GAP_M:g}] m masked (untracked true leader / duplicate "
            "fragment) so it cuts the episode; fragments unstitched"
        ),
        "wall_s": round(time.perf_counter() - t0, 1),
    }
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with open(PROCESSED_DIR / "i24_wb_episodes.pkl", "wb") as f:
        pickle.dump(all_eps, f)
    (PROCESSED_DIR / "i24_wb_episode_summary.json").write_text(json.dumps(summary, indent=2))
    print(
        f"total: {len(all_eps)} episodes, {summary['episode_duration_s']['total_h']:.1f} h "
        f"-> {PROCESSED_DIR / 'i24_wb_episodes.pkl'} ({summary['wall_s']} s)"
    )


if __name__ == "__main__":
    main()
