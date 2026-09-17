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

The episode schema (``calibration.episodes.LeaderFollowerEpisode``) carries no
position: it keeps ``t``, ``gap_m``, the two speeds and a metadata dict with
``lane``/``leader_id``/``dt_s``/``duration_s``, which is all the gap-RMSE
objective needs. ``--positions`` therefore adds a **sidecar index**, keyed by
the episode's ordinal in the pickled list, that recovers where along the
corridor each episode happened, so an episode set can be selected by position
(the Old Hickory merge zone, for instance) without refitting anything:

* ``i24_wb_episode_positions.json`` — one row per episode with the follower's
  ``x`` [m, data frame, 0 = MM 62.7] at its first and last sample, looked up
  in the same 5 Hz Parquet by (``veh_id``, 0.2 s slot). ``x`` is
  travel-oriented and a follower never moves backwards, so ``[x_start,
  x_end]`` is the episode's position span. Additive: the committed
  ``i24_wb_episodes.pkl`` is read, never rewritten, and the default run is
  unchanged.

Run: ``uv run --no-sync python scripts/i24_extract_episodes.py``
     ``uv run --no-sync python scripts/i24_extract_episodes.py --positions``
"""

from __future__ import annotations

import json
import pickle
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_data import (
    MAINLINE_LANES,
    MAX_GAP_M,
    MIN_GAP_M,
    PROCESSED_DIR,
    SAMPLE_DT_S,
    WB_DIR,
    build_lane_episodes,
    clock,
    data_hash,
    load_vehicles,
)

from calibration.episodes import LeaderFollowerEpisode
from calibration.loaders.i24motion import I24_PASSENGER_CLASSES, load_i24_parquet

MIN_DURATION_S = 30.0
V_CONGESTED_MS = 40.0 / 3.6


CLASS_SETS = {
    "passenger": tuple(sorted(I24_PASSENGER_CLASSES)),
    "heavy": (4, 5),  # coarse classes semi, truck (data documentation v1.x)
}

#: Columns of the ``--positions`` sidecar, in row order.
POSITION_COLUMNS = (
    "index",
    "veh_id",
    "lane",
    "t_start_s",
    "t_end_s",
    "x_start_m",
    "x_end_m",
)


def _slot(t: np.ndarray | float) -> np.ndarray:
    """0.2 s grid slot index of a time stamp (the Parquet's own sampling grid)."""
    return np.rint(np.asarray(t, dtype=float) / SAMPLE_DT_S).astype(np.int64)


def episode_positions(episodes: list[LeaderFollowerEpisode]) -> list[list[Any]]:
    """Follower position at each episode's first and last sample.

    The episode schema keeps no position, so the endpoints are looked up in the
    trajectory Parquet by (``veh_id``, 0.2 s slot) — the same grid the episodes
    were cut on, so the join is exact rather than interpolated. One lane is read
    at a time and immediately narrowed to the vehicles that host an episode in
    it, so the pass stays column-pruned and small.

    Args:
        episodes: The pickled episode list, in file order.

    Returns:
        One list per episode in :data:`POSITION_COLUMNS` order; ``x_start_m`` /
        ``x_end_m`` are ``None`` for an episode whose rows are not found (none
        are expected: the episodes come from these rows).
    """
    rows: list[list[Any]] = [
        [
            i,
            ep.veh_id,
            int(ep.metadata["lane"]),  # type: ignore[arg-type]
            float(ep.t[0]),
            float(ep.t[-1]),
            None,
            None,
        ]
        for i, ep in enumerate(episodes)
    ]
    by_lane: dict[int, list[int]] = {}
    for row in rows:
        by_lane.setdefault(int(row[2]), []).append(int(row[0]))
    for lane, idx in sorted(by_lane.items()):
        ids = {rows[i][1] for i in idx}
        df = load_i24_parquet(WB_DIR, lanes=(lane, lane), columns=["t", "veh_id", "x"])
        df = df.loc[df["veh_id"].isin(ids)].copy()
        df["slot"] = _slot(df["t"].to_numpy())
        df = df[["veh_id", "slot", "x"]].drop_duplicates(subset=["veh_id", "slot"])
        want = pd.DataFrame(
            {
                "index": np.repeat(idx, 2),
                "veh_id": [rows[i][1] for i in idx for _ in (0, 1)],
                "slot": _slot([rows[i][3 + k] for i in idx for k in (0, 1)]),
            }
        )
        got = want.merge(df, on=["veh_id", "slot"], how="left")
        x = got["x"].to_numpy(dtype=float).reshape(-1, 2)
        for k, i in enumerate(idx):
            rows[i][5] = None if not np.isfinite(x[k, 0]) else float(x[k, 0])
            rows[i][6] = None if not np.isfinite(x[k, 1]) else float(x[k, 1])
        print(f"lane {lane}: positions for {len(idx)} episodes", flush=True)
        del df, want, got
    return rows


def write_position_index(suffix: str) -> Path:
    """Build and write the ``--positions`` sidecar for the pickled episode set."""
    pkl = PROCESSED_DIR / f"i24_wb_episodes{suffix}.pkl"
    with open(pkl, "rb") as f:
        episodes = pickle.load(f)
    rows = episode_positions(episodes)
    spans = np.array(
        [[r[5], r[6]] for r in rows if r[5] is not None and r[6] is not None], dtype=float
    )
    out = PROCESSED_DIR / f"i24_wb_episode_positions{suffix}.json"
    out.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "data_hash": data_hash(),
                "source_pkl": pkl.name,
                "n_episodes": len(rows),
                "n_located": int(spans.shape[0]),
                "key": "index = ordinal of the episode in the pickled list (file order)",
                "x_frame": "data x [m] along travel, 0 = MM 62.7 (scripts/i24_data.py)",
                "columns": list(POSITION_COLUMNS),
                "x_span_m": {
                    "min": float(spans[:, 0].min()) if spans.size else None,
                    "max": float(spans[:, 1].max()) if spans.size else None,
                    "length_median": float(np.median(spans[:, 1] - spans[:, 0]))
                    if spans.size
                    else None,
                    "n_backwards": int((spans[:, 1] < spans[:, 0]).sum()) if spans.size else 0,
                },
                "rows": rows,
            }
        )
    )
    print(f"{spans.shape[0]}/{len(rows)} episodes located -> {out}")
    return out


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--classes",
        choices=tuple(CLASS_SETS),
        default="passenger",
        help="follower vehicle classes: passenger (0-3, the default artifact) or heavy (4-5, "
        "semis and trucks; output files carry the _heavy suffix)",
    )
    ap.add_argument(
        "--positions",
        action="store_true",
        help="do not extract: read the existing episode pickle and write the position "
        "sidecar i24_wb_episode_positions[suffix].json (follower x at the first and last "
        "sample of every episode, keyed by its ordinal in the pickle)",
    )
    args = ap.parse_args()
    classes = CLASS_SETS[args.classes]
    suffix = "" if args.classes == "passenger" else f"_{args.classes}"
    if args.positions:
        write_position_index(suffix)
        return
    t0 = time.perf_counter()
    veh = load_vehicles()
    all_eps = []
    per_lane: dict[str, object] = {}
    for lane in range(MAINLINE_LANES[0], MAINLINE_LANES[1] + 1):
        df = load_i24_parquet(
            WB_DIR, lanes=(lane, lane), columns=["t", "veh_id", "x", "v", "length", "cls"]
        )
        eps = build_lane_episodes(df, lane, min_duration_s=MIN_DURATION_S, follower_classes=classes)
        n_frag_ge30 = int(
            ((veh["duration_s"] >= MIN_DURATION_S) & veh["cls"].isin(list(classes))).sum()
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
        "follower_classes": sorted(classes),
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
            f"followers of coarse class {sorted(classes)} "
            f"({'sedan/midsize/van/pickup' if args.classes == 'passenger' else 'semi/truck'}), "
            "mainline lanes 1-4, "
            f">= {MIN_DURATION_S:g} s continuous, uniform 0.2 s dt (5 Hz), single lane, "
            "single position-ordered leader; gap outside "
            f"[{MIN_GAP_M:g}, {MAX_GAP_M:g}] m masked (untracked true leader / duplicate "
            "fragment) so it cuts the episode; fragments unstitched"
        ),
        "wall_s": round(time.perf_counter() - t0, 1),
    }
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with open(PROCESSED_DIR / f"i24_wb_episodes{suffix}.pkl", "wb") as f:
        pickle.dump(all_eps, f)
    (PROCESSED_DIR / f"i24_wb_episode_summary{suffix}.json").write_text(
        json.dumps(summary, indent=2)
    )
    print(
        f"total: {len(all_eps)} episodes, {summary['episode_duration_s']['total_h']:.1f} h "
        f"-> {PROCESSED_DIR / f'i24_wb_episodes{suffix}.pkl'} ({summary['wall_s']} s)"
    )


if __name__ == "__main__":
    main()
