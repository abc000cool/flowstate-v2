"""Shared NGSIM US-101 data access for the M2 calibration drivers.

Data: ``data/ngsim/us101_chunk_00..11.csv`` — the raw NGSIM US-101 vehicle
trajectories from data.transportation.gov (Socrata resource ``8ect-6jqj``,
``location='us-101'``), exported ordered by ``(global_time, vehicle_id)`` in
200k-row chunks. All longitudinal quantities are feet in the source; the
``calibration.loaders.ngsim`` loader converts to SI.

Two dataset facts every consumer must respect (verified on this dump):

1. **Duplicate rows.** The Socrata export contains exact duplicate rows
   (~21.5% of the 2.4M rows here); they are dropped before any use.
2. **Recording periods.** US-101 was recorded in three 15-minute periods
   (07:50-08:05-08:20-08:35 PDT on 2005-06-15) that are *contiguous in wall
   time* — there are NO global_time gaps between them — while ``vehicle_id``
   and ``frame_id`` restart per period (and the periods overlap by ~90 s of
   wall clock at each boundary). Splitting on time gaps therefore does not
   work; instead each row's *recording origin*
   ``t0 = global_time - frame_id * 100 ms`` is constant per period and splits
   the dump exactly. This 2.4M-row export covers period 1 completely
   (07:49:39.7-08:05:32.5) and the first ~8.8 min of period 2
   (08:04:03.0-08:12:50.3); period 3 is not present.

Run scripts from the repo root with ``uv run --no-sync python scripts/...``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from calibration.episodes import LeaderFollowerEpisode, episodes_from_pairs
from calibration.loaders.ngsim import NGSIM_DT_S, load_ngsim_trajectories

REPO_ROOT = Path(__file__).resolve().parents[1]
NGSIM_DIR = REPO_ROOT / "data" / "ngsim"
PROCESSED_DIR = REPO_ROOT / "data" / "processed"

#: NGSIM frame interval in the source's epoch-millisecond clock.
NGSIM_DT_MS = round(NGSIM_DT_S * 1000.0)

#: Mainline lane ids (1-5). 6 = auxiliary lane, 7/8 = on/off ramps.
MAINLINE_LANES = (1, 5)

#: NGSIM v_Class code for autos (1 = motorcycle, 3 = truck).
V_CLASS_AUTO = 2

#: US-101 study-section length [ft] → the loader converts positions to m;
#: kept for reference in provenance strings (~2,224 ft observed local_y max).
SITE_LENGTH_M = 640.0


def chunk_files() -> list[Path]:
    """Sorted list of the US-101 chunk CSVs."""
    files = sorted(NGSIM_DIR.glob("us101_chunk_*.csv"))
    if not files:
        raise FileNotFoundError(f"no us101_chunk_*.csv under {NGSIM_DIR}")
    return files


def data_hash() -> str:
    """sha256 over the sorted chunk files' individual sha256 hex digests."""
    outer = hashlib.sha256()
    for f in chunk_files():
        outer.update(hashlib.sha256(f.read_bytes()).hexdigest().encode())
    return outer.hexdigest()


def load_us101() -> dict[str, pd.DataFrame]:
    """Load all chunks, dedupe, and split into recording periods.

    Returns:
        Ordered ``{period_label: tidy SI trajectory frame}`` with labels
        ``"p1"``, ``"p2"``, ... in recording order. Each frame is the
        ``load_ngsim_trajectories`` schema plus ``global_time_ms`` and
        ``v_class``; ``t`` (= frame × 0.1 s) is period-local time on the
        period's shared 10 Hz frame grid.
    """
    frames = [load_ngsim_trajectories(f) for f in chunk_files()]
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(ignore_index=True)
    if "global_time_ms" not in df.columns:
        raise ValueError("US-101 chunks lack global_time — cannot split recording periods")
    origin = df["global_time_ms"] - df["frame"].astype("int64") * NGSIM_DT_MS
    periods: dict[str, pd.DataFrame] = {}
    for i, t0 in enumerate(sorted(origin.unique()), start=1):
        periods[f"p{i}"] = df.loc[origin == t0].reset_index(drop=True)
    return periods


def build_period_episodes(
    period_df: pd.DataFrame,
    period_label: str,
    min_duration_s: float = 30.0,
) -> list[LeaderFollowerEpisode]:
    """Leader-follower episodes for auto followers on the mainline of one period.

    Pairs each follower with its recorded ``Preceding`` vehicle via a
    self-join on ``(frame, leader_id)`` (ids are period-unique, so this must
    run on a single period). The bumper-to-bumper gap is
    ``Space_Headway − leader v_Length``; a leader absent from the recording
    leaves ``v_leader``/``gap_m`` NaN and the sample is dropped (episodes
    require a valid leader with a known length). Follower rows are kept only
    for ``v_class == 2`` (autos) on mainline lanes 1-5. Samples with a
    non-positive recorded gap (raw-NGSIM digitization junk) are masked to NaN
    so they cut the episode instead of poisoning it. Episode cutting and
    validation (≥ ``min_duration_s`` continuous, uniform 0.1 s dt, single
    lane, no leader change) is the package's ``episodes_from_pairs``.

    Args:
        period_df: One period frame from :func:`load_us101`.
        period_label: e.g. ``"p1"`` — prefixes follower ids so episodes from
            different periods can never collide.
        min_duration_s: Minimum continuous episode duration [s].

    Returns:
        Validated episodes; ``metadata['dataset']`` is
        ``"ngsim_us101_<period_label>"``.
    """
    df = period_df
    lengths = df.groupby("veh_id")["length_m"].first()
    leader_side = df[["frame", "veh_id", "v"]].rename(
        columns={"veh_id": "leader_id", "v": "v_leader"}
    )
    paired = df.merge(leader_side, on=["frame", "leader_id"], how="left")
    paired["gap_m"] = paired["spacing_m"] - paired["leader_id"].map(lengths)
    paired.loc[paired["gap_m"] <= 0.0, "gap_m"] = np.nan
    follower_ok = (paired["v_class"] == V_CLASS_AUTO) & paired["lane"].between(*MAINLINE_LANES)
    paired = paired.loc[follower_ok].copy()
    paired["veh_id"] = period_label + "-" + paired["veh_id"]
    return episodes_from_pairs(
        paired[["t", "veh_id", "lane", "leader_id", "gap_m", "v", "v_leader"]],
        dataset=f"ngsim_us101_{period_label}",
        min_duration_s=min_duration_s,
    )
