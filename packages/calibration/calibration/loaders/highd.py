"""highD trajectory loader (CLAUDE.md §6.2).

Parses the highD drone dataset (Krajewski et al. 2018, "The highD Dataset: A
Drone Dataset of Naturalistic Vehicle Trajectories on German Highways", IEEE
ITSC). **Access requires registration**: request the dataset from leveldXdata
at https://levelxdata.com/highd-dataset/ (research license). This loader is
therefore exercised only on tiny synthetic fixtures shaped like the documented
format.

Documented format (per-recording):
    ``XX_tracks.csv`` — one row per (frame, vehicle): ``frame``, ``id``,
    ``x``, ``y`` [m, bounding-box position], ``width``, ``height`` [m —
    highD's bounding box is road-aligned, so ``width`` is the *longitudinal*
    extent, i.e. the vehicle length], ``xVelocity``, ``yVelocity`` [m/s],
    ``precedingId``, ``followingId`` (0 = none), ``laneId``, plus derived
    ``dhw``, ``thw``, ``ttc``, ``precedingXVelocity``.
    ``XX_recordingMeta.csv`` — one row: ``id``, ``frameRate`` [Hz]
    (typically 25), ``locationId``, ``speedLimit``, …

highD is already metric (m, m/s) — no unit conversion is needed. Vehicles in
the upper lanes drive in −x; speeds are taken as magnitudes and the
bumper-to-bumper gap as ``|x_lead − x_ego| − width_lead`` (leader length from
its bounding box), a documented bounding-box approximation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd

from calibration.episodes import (
    MIN_EPISODE_DURATION_S,
    LeaderFollowerEpisode,
    episodes_from_pairs,
)

_REQUIRED_TRACKS: Final[frozenset[str]] = frozenset(
    {"frame", "id", "x", "width", "xVelocity", "precedingId", "laneId"}
)


def load_highd_episodes(
    tracks_csv: str | Path,
    recording_meta_csv: str | Path,
    *,
    min_duration_s: float = MIN_EPISODE_DURATION_S,
) -> list[LeaderFollowerEpisode]:
    """Build leader-follower episodes from one highD recording.

    Uses the recorded ``precedingId`` pairing. Leader kinematics come from a
    self-join on (frame, precedingId); ``precedingXVelocity`` is used for the
    leader speed when present (it is in the documented schema), else the
    joined leader ``xVelocity``. Time is ``frame / frameRate`` with
    ``frameRate`` read from the recording meta file. Episodes are cut at lane
    changes, leader changes and frame gaps (CLAUDE.md §6.2).

    Args:
        tracks_csv: Path to ``XX_tracks.csv``.
        recording_meta_csv: Path to ``XX_recordingMeta.csv`` (for frameRate).
        min_duration_s: Minimum episode duration [s] (contract default 30 s).

    Returns:
        List of validated episodes with ``metadata['dataset'] == 'highd'``.

    Raises:
        ValueError: On missing schema columns or a non-positive frame rate.
    """
    tracks = pd.read_csv(tracks_csv)
    missing = _REQUIRED_TRACKS - set(tracks.columns)
    if missing:
        raise ValueError(f"{tracks_csv}: missing highD columns {sorted(missing)}")
    meta = pd.read_csv(recording_meta_csv)
    if "frameRate" not in meta.columns or meta.empty:
        raise ValueError(f"{recording_meta_csv}: missing frameRate")
    frame_rate = float(meta["frameRate"].iloc[0])
    if frame_rate <= 0:
        raise ValueError(f"{recording_meta_csv}: frameRate must be > 0, got {frame_rate}")

    leader_side = tracks[["frame", "id", "x", "width", "xVelocity"]].rename(
        columns={
            "id": "precedingId",
            "x": "x_lead",
            "width": "width_lead",
            "xVelocity": "xVelocity_lead",
        }
    )
    paired = tracks.merge(leader_side, on=["frame", "precedingId"], how="left")
    if "precedingXVelocity" in paired.columns:
        v_leader = paired["precedingXVelocity"].astype(float).abs()
    else:
        v_leader = paired["xVelocity_lead"].astype(float).abs()

    pairs = pd.DataFrame(
        {
            "t": paired["frame"].astype(int) / frame_rate,
            "veh_id": paired["id"].astype(int).astype(str),
            "lane": paired["laneId"].astype(int),
            "leader_id": paired["precedingId"].astype(int).astype(str),
            "gap_m": (paired["x_lead"] - paired["x"]).abs() - paired["width_lead"],
            "v": paired["xVelocity"].astype(float).abs(),
            "v_leader": v_leader,
        }
    )
    eps = episodes_from_pairs(pairs, dataset="highd", min_duration_s=min_duration_s)
    for ep in eps:
        ep.metadata["frame_rate_hz"] = frame_rate
    return eps


def frame_rate_of(recording_meta_csv: str | Path) -> float:
    """Read the recording frame rate [Hz] from a highD recordingMeta CSV."""
    meta = pd.read_csv(recording_meta_csv)
    rate = float(meta["frameRate"].iloc[0])
    if not np.isfinite(rate) or rate <= 0:
        raise ValueError(f"{recording_meta_csv}: bad frameRate {rate}")
    return rate
