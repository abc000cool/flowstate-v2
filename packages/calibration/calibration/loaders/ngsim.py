"""NGSIM trajectory loader (CLAUDE.md §6.2).

Parses the public NGSIM vehicle-trajectory CSVs (I-80 / US-101 / Lankershim)
published on data.transportation.gov (dataset ``8ect-6jqj``, "Next Generation
Simulation (NGSIM) Vehicle Trajectories and Supporting Data"). Recording rate
is 10 Hz; longitudinal quantities are in **feet**. Raw NGSIM kinematics are
notoriously noisy (differentiation artifacts); prefer the Montanino & Punzo
reconstructed trajectories when available (CLAUDE.md §6.2) — this loader
parses either, as the reconstructed files keep the official column schema.

Unit policy: FlowState converts units ONLY via ``flowstate_core.units``; the
single sanctioned exception is :data:`FEET_TO_M` below, because NGSIM (and
I-24 MOTION) publish in US customary feet, a unit that has no place in the
core SI helpers. It is defined exactly once, here, and imported by the other
loaders that need it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pandas as pd

from calibration.episodes import (
    MIN_EPISODE_DURATION_S,
    LeaderFollowerEpisode,
    episodes_from_pairs,
)

FEET_TO_M: Final[float] = 0.3048
"""Exact international foot [m]. The one non-SI ingestion conversion allowed
outside ``flowstate_core.units`` (NGSIM and I-24 MOTION publish in feet)."""

NGSIM_DT_S: Final[float] = 0.1
"""NGSIM native frame interval [s] (10 Hz)."""

# Official data.transportation.gov column names → canonical names. Matching is
# case-insensitive after stripping, so raw spellings ("Vehicle_ID") and
# lowercase exports ("vehicle_id") both work.
_COLUMN_MAP: Final[dict[str, str]] = {
    "vehicle_id": "veh_id",
    "frame_id": "frame",
    "global_time": "global_time_ms",
    "local_y": "y_ft",
    "v_vel": "v_ftps",
    "v_class": "v_class",
    "v_length": "length_ft",
    "lane_id": "lane",
    "preceding": "leader_id",
    "space_headway": "spacing_ft",
}

_REQUIRED: Final[frozenset[str]] = frozenset(
    {"veh_id", "frame", "v_ftps", "lane", "leader_id", "spacing_ft", "length_ft"}
)


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map raw/lowercase NGSIM column spellings to canonical names."""
    renames = {}
    for col in df.columns:
        key = str(col).strip().lower()
        if key in _COLUMN_MAP:
            renames[col] = _COLUMN_MAP[key]
    return df.rename(columns=renames)


def load_ngsim_trajectories(
    path: str | Path,
    *,
    chunksize: int = 200_000,
    downsample: int = 1,
) -> pd.DataFrame:
    """Read an NGSIM trajectory CSV into a tidy SI-unit trajectory table.

    Reads in chunks (the full I-80 file is ~1.5 GB) and converts feet → meters
    via :data:`FEET_TO_M`. Optionally downsamples by keeping every
    ``downsample``-th frame, giving an effective dt of
    ``downsample × 0.1 s`` — useful because IDM calibration does not need
    10 Hz and the differentiation noise partially averages out.

    Args:
        path: Path to the CSV (raw or reconstructed, official column schema).
        chunksize: Rows per read chunk.
        downsample: Keep frames where ``Frame_ID % downsample == 0`` (1 ⇒ all).

    Returns:
        DataFrame with columns ``t`` [s] (``Frame_ID × 0.1``), ``veh_id``
        (str), ``frame`` (int), ``x`` [m] (Local_Y — distance along the
        section in the direction of travel), ``lane`` (int), ``v`` [m/s],
        ``leader_id`` (str, ``"0"`` = none), ``spacing_m`` [m]
        (Space_Headway, front-to-front) and ``length_m`` [m] (vehicle length).
        When the source carries them, ``global_time_ms`` (int, Unix epoch ms —
        NGSIM sites recorded several 15-minute periods whose vehicle/frame ids
        restart per period, so callers split periods on it) and ``v_class``
        (int, 1=motorcycle 2=auto 3=truck) pass through as extra columns.

    Raises:
        ValueError: If required NGSIM columns are missing or downsample < 1.
    """
    if downsample < 1:
        raise ValueError(f"downsample must be >= 1, got {downsample}")
    chunks: list[pd.DataFrame] = []
    for chunk in pd.read_csv(path, chunksize=chunksize):
        chunk = _normalize_columns(chunk)
        missing = _REQUIRED - set(chunk.columns)
        if missing:
            raise ValueError(f"{path}: missing NGSIM columns {sorted(missing)}")
        if downsample > 1:
            chunk = chunk.loc[chunk["frame"].astype(int) % downsample == 0]
        if not chunk.empty:
            chunks.append(chunk)
    if not chunks:
        raise ValueError(f"{path}: no rows parsed")
    raw = pd.concat(chunks, ignore_index=True)

    out = pd.DataFrame(
        {
            "t": raw["frame"].astype(int) * NGSIM_DT_S,
            "veh_id": raw["veh_id"].astype(int).astype(str),
            "frame": raw["frame"].astype(int),
            "lane": raw["lane"].astype(int),
            "v": raw["v_ftps"].astype(float) * FEET_TO_M,
            "leader_id": raw["leader_id"].astype(int).astype(str),
            "spacing_m": raw["spacing_ft"].astype(float) * FEET_TO_M,
            "length_m": raw["length_ft"].astype(float) * FEET_TO_M,
        }
    )
    if "y_ft" in raw.columns:
        out["x"] = raw["y_ft"].astype(float) * FEET_TO_M
    if "global_time_ms" in raw.columns:
        out["global_time_ms"] = raw["global_time_ms"].astype("int64")
    if "v_class" in raw.columns:
        out["v_class"] = raw["v_class"].astype(int)
    return out


def build_ngsim_episodes(
    df: pd.DataFrame,
    *,
    min_duration_s: float = MIN_EPISODE_DURATION_S,
) -> list[LeaderFollowerEpisode]:
    """Build leader-follower episodes from a loaded NGSIM table.

    Pairs each follower with its recorded ``Preceding`` vehicle (no positional
    re-derivation needed — NGSIM ships the pairing). The bumper-to-bumper gap
    is ``Space_Headway − leader_length``: NGSIM's Space_Headway is the
    front-to-front distance to the preceding vehicle, so subtracting the
    leader's ``v_Length`` yields the IDM gap ``s``. Leader speed comes from a
    self-join on (frame, leader id). Episodes are cut at lane changes, leader
    changes and frame gaps (CLAUDE.md §6.2).

    Args:
        df: Output of :func:`load_ngsim_trajectories`.
        min_duration_s: Minimum episode duration [s] (contract default 30 s).

    Returns:
        List of validated episodes with ``metadata['dataset'] == 'ngsim'``.
    """
    lengths = df.groupby("veh_id")["length_m"].first()
    leader_side = df[["frame", "veh_id", "v"]].rename(
        columns={"veh_id": "leader_id", "v": "v_leader"}
    )
    paired = df.merge(leader_side, on=["frame", "leader_id"], how="left")
    paired["gap_m"] = paired["spacing_m"] - paired["leader_id"].map(lengths).fillna(0.0)
    return episodes_from_pairs(
        paired[["t", "veh_id", "lane", "leader_id", "gap_m", "v", "v_leader"]],
        dataset="ngsim",
        min_duration_s=min_duration_s,
    )


def load_ngsim_episodes(
    path: str | Path,
    *,
    chunksize: int = 200_000,
    downsample: int = 1,
    min_duration_s: float = MIN_EPISODE_DURATION_S,
) -> list[LeaderFollowerEpisode]:
    """Convenience: :func:`load_ngsim_trajectories` + :func:`build_ngsim_episodes`."""
    df = load_ngsim_trajectories(path, chunksize=chunksize, downsample=downsample)
    return build_ngsim_episodes(df, min_duration_s=min_duration_s)
