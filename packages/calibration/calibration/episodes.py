"""Leader-follower episode schema and extraction (CLAUDE.md §6.2).

Car-following calibration needs clean, continuous leader-follower pairs:
Kesting & Treiber (2008, "Calibrating car-following models by using trajectory
data") and Punzo & Montanino's NGSIM reconstruction work both stress that
episodes must be free of lane changes and long enough (tens of seconds) to
excite the model dynamics. Every dataset loader in ``calibration.loaders``
normalizes to the :class:`LeaderFollowerEpisode` schema defined here
(docs/CONTRACTS.md §5, CLAUDE.md §6.2).

All quantities are SI: seconds, meters, m/s. ``gap_m`` is the bumper-to-bumper
gap — exactly the ``s`` that enters the IDM acceleration (CLAUDE.md §3.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

MIN_EPISODE_DURATION_S = 30.0
"""Default minimum continuous car-following duration [s] (CLAUDE.md §6.2)."""

_DT_RTOL = 1e-3
"""Relative tolerance for time-step uniformity checks."""

_MISSING_LEADER = {"", "0", "0.0", "nan", "none", "null"}
"""Leader-id sentinels (case-insensitive) meaning 'no leader'."""


@dataclass
class LeaderFollowerEpisode:
    """One continuous car-following episode for a single follower.

    Attributes:
        veh_id: Follower vehicle id (dataset-native id, stringified).
        t: Time stamps [s], strictly increasing, uniform spacing.
        gap_m: Bumper-to-bumper gap to the leader [m].
        v_follower: Follower speed [m/s].
        v_leader: Leader speed [m/s].
        metadata: Provenance dict. Always contains ``dataset`` (source name),
            ``lane`` (the single lane the pair occupied — a scalar, because an
            episode never spans a lane change), ``duration_s``, ``dt_s`` and
            ``leader_id``.
    """

    veh_id: str
    t: np.ndarray
    gap_m: np.ndarray
    v_follower: np.ndarray
    v_leader: np.ndarray
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def dt(self) -> float:
        """Uniform sample interval [s]."""
        return float(self.t[1] - self.t[0])

    @property
    def duration_s(self) -> float:
        """Episode span [s] from first to last sample."""
        return float(self.t[-1] - self.t[0])

    @property
    def n(self) -> int:
        """Number of samples."""
        return int(self.t.shape[0])


def validate_episode(
    ep: LeaderFollowerEpisode,
    min_duration_s: float = MIN_EPISODE_DURATION_S,
    dt_rtol: float = _DT_RTOL,
) -> None:
    """Validate an episode against the CLAUDE.md §6.2 requirements.

    Requirements: all four arrays the same length (>= 2 samples), strictly
    increasing time with uniform dt (within ``dt_rtol``), duration at least
    ``min_duration_s`` (>= 30 s continuous car-following by default), all
    values finite, positive gaps, non-negative speeds, and a scalar ``lane``
    in the metadata (an episode spanning a lane change is invalid by
    construction).

    Args:
        ep: Episode to validate.
        min_duration_s: Minimum continuous duration [s].
        dt_rtol: Relative tolerance on time-step uniformity.

    Raises:
        ValueError: On the first violated requirement.
    """
    n = ep.t.shape[0]
    for name in ("gap_m", "v_follower", "v_leader"):
        arr = getattr(ep, name)
        if arr.shape != ep.t.shape:
            raise ValueError(f"{ep.veh_id}: {name} shape {arr.shape} != t shape {ep.t.shape}")
    if n < 2:
        raise ValueError(f"{ep.veh_id}: need >= 2 samples, got {n}")
    diffs = np.diff(ep.t)
    if np.any(diffs <= 0):
        raise ValueError(f"{ep.veh_id}: t must be strictly increasing")
    dt = float(np.median(diffs))
    if not np.allclose(diffs, dt, rtol=dt_rtol, atol=1e-9):
        raise ValueError(f"{ep.veh_id}: non-uniform dt (median {dt:.4f} s)")
    if ep.duration_s < min_duration_s:
        raise ValueError(
            f"{ep.veh_id}: duration {ep.duration_s:.1f} s < required {min_duration_s:.1f} s"
        )
    for name in ("t", "gap_m", "v_follower", "v_leader"):
        if not np.all(np.isfinite(getattr(ep, name))):
            raise ValueError(f"{ep.veh_id}: non-finite values in {name}")
    if np.any(ep.gap_m <= 0):
        raise ValueError(f"{ep.veh_id}: gap must be > 0 everywhere")
    if np.any(ep.v_follower < 0) or np.any(ep.v_leader < 0):
        raise ValueError(f"{ep.veh_id}: speeds must be >= 0")
    lane = ep.metadata.get("lane")
    if lane is None or isinstance(lane, (list, tuple, set, np.ndarray)):
        raise ValueError(f"{ep.veh_id}: metadata['lane'] must be a single scalar lane")


def is_valid_episode(
    ep: LeaderFollowerEpisode,
    min_duration_s: float = MIN_EPISODE_DURATION_S,
) -> bool:
    """True iff :func:`validate_episode` passes."""
    try:
        validate_episode(ep, min_duration_s=min_duration_s)
    except ValueError:
        return False
    return True


def _leader_is_missing(value: object) -> bool:
    """True when a leader-id cell means 'no leader'."""
    if value is None:
        return True
    if isinstance(value, float) and np.isnan(value):
        return True
    return str(value).strip().lower() in _MISSING_LEADER


def episodes_from_pairs(
    df: pd.DataFrame,
    *,
    dataset: str,
    min_duration_s: float = MIN_EPISODE_DURATION_S,
    dt_rtol: float = _DT_RTOL,
) -> list[LeaderFollowerEpisode]:
    """Cut a paired follower-leader table into valid episodes.

    Expects a tidy frame with one row per (follower, time): columns ``t`` [s],
    ``veh_id``, ``lane``, ``leader_id``, ``gap_m``, ``v`` [m/s] (follower) and
    ``v_leader`` [m/s]. Rows with a missing leader or non-finite gap/speeds
    are dropped first. Per follower, maximal runs are cut wherever the leader
    changes, the lane changes, or the time step deviates from the follower's
    modal dt (a recording gap). Runs shorter than ``min_duration_s`` are
    discarded; survivors are returned as validated episodes.

    Args:
        df: Paired trajectory table as described above.
        dataset: Source label stored in each episode's metadata.
        min_duration_s: Minimum episode duration [s].
        dt_rtol: Relative tolerance on dt uniformity within a run.

    Returns:
        List of validated :class:`LeaderFollowerEpisode`, ordered by follower
        id then time.
    """
    required = {"t", "veh_id", "lane", "leader_id", "gap_m", "v", "v_leader"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"episodes_from_pairs: missing columns {sorted(missing)}")

    work = df.copy()
    keep = ~work["leader_id"].map(_leader_is_missing)
    keep &= np.isfinite(work["gap_m"]) & np.isfinite(work["v"]) & np.isfinite(work["v_leader"])
    work = work.loc[keep].sort_values(["veh_id", "t"], kind="stable")

    episodes: list[LeaderFollowerEpisode] = []
    for veh_id, grp in work.groupby("veh_id", sort=True):
        t = grp["t"].to_numpy(dtype=float)
        if t.shape[0] < 2:
            continue
        diffs = np.diff(t)
        dt = float(np.median(diffs))
        leader = grp["leader_id"].astype(str).to_numpy()
        lane = grp["lane"].to_numpy()
        # Break where the leader changes, the lane changes, or time jumps.
        brk = np.zeros(t.shape[0], dtype=bool)
        brk[1:] |= leader[1:] != leader[:-1]
        brk[1:] |= lane[1:] != lane[:-1]
        brk[1:] |= ~np.isclose(diffs, dt, rtol=dt_rtol, atol=1e-9)
        seg_id = np.cumsum(brk)
        for seg in np.unique(seg_id):
            idx = np.flatnonzero(seg_id == seg)
            if (t[idx[-1]] - t[idx[0]]) < min_duration_s:
                continue
            sub = grp.iloc[idx]
            ep = LeaderFollowerEpisode(
                veh_id=str(veh_id),
                t=sub["t"].to_numpy(dtype=float),
                gap_m=sub["gap_m"].to_numpy(dtype=float),
                v_follower=sub["v"].to_numpy(dtype=float),
                v_leader=sub["v_leader"].to_numpy(dtype=float),
                metadata={
                    "dataset": dataset,
                    "lane": lane[idx[0]].item() if hasattr(lane[idx[0]], "item") else lane[idx[0]],
                    "leader_id": str(leader[idx[0]]),
                    "dt_s": dt,
                    "duration_s": float(t[idx[-1]] - t[idx[0]]),
                },
            )
            if is_valid_episode(ep, min_duration_s=min_duration_s):
                episodes.append(ep)
    return episodes


def extract_episodes(
    df: pd.DataFrame,
    *,
    dataset: str = "unknown",
    min_duration_s: float = MIN_EPISODE_DURATION_S,
    default_leader_length_m: float = 0.0,
) -> list[LeaderFollowerEpisode]:
    """Extract leader-follower episodes from a raw trajectory table.

    Expects columns ``t`` [s], ``veh_id``, ``x`` [m], ``lane``, ``v`` [m/s],
    optionally ``leader_id`` and ``length`` [m]. ``x`` must increase in the
    direction of travel (loaders for datasets recorded the other way flip the
    axis before calling this). Timestamps must lie on a shared sampling grid
    so that follower and leader rows align exactly (loaders snap to the frame
    grid).

    When ``leader_id`` is absent, followers are paired with leaders by
    position ordering: within each (t, lane) group vehicles are sorted by
    descending ``x`` and each vehicle's leader is the next vehicle ahead of it
    in the same lane. The bumper-to-bumper gap is
    ``x_leader − x_follower − leader_length``, with the leader length taken
    from the ``length`` column when present, else ``default_leader_length_m``
    (0.0 ⇒ front-to-front spacing; pass a real length for calibration use).

    Episodes are cut at lane changes, leader changes and recording gaps via
    :func:`episodes_from_pairs`.

    Args:
        df: Raw trajectory table as described above.
        dataset: Source label stored in metadata.
        min_duration_s: Minimum episode duration [s].
        default_leader_length_m: Leader length fallback [m] when the table has
            no ``length`` column.

    Returns:
        List of validated :class:`LeaderFollowerEpisode`.
    """
    required = {"t", "veh_id", "x", "lane", "v"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"extract_episodes: missing columns {sorted(missing)}")

    work = df.copy()
    work["veh_id"] = work["veh_id"].astype(str)
    if "leader_id" not in work.columns:
        ordered = work.sort_values(["t", "lane", "x"], ascending=[True, True, False], kind="stable")
        ordered["leader_id"] = ordered.groupby(["t", "lane"], sort=False)["veh_id"].shift(1)
        work = ordered
    else:
        work["leader_id"] = work["leader_id"].map(
            lambda s: None if _leader_is_missing(s) else str(s).split(".")[0]
        )

    if "length" in work.columns:
        lengths = work.groupby("veh_id")["length"].first()
    else:
        lengths = pd.Series(dtype=float)

    leader_side = work[["t", "veh_id", "x", "v"]].rename(
        columns={"veh_id": "leader_id", "x": "x_leader", "v": "v_leader"}
    )
    paired = work.merge(leader_side, on=["t", "leader_id"], how="left")
    leader_len = (
        paired["leader_id"].map(lengths).fillna(default_leader_length_m)
        if not lengths.empty
        else pd.Series(default_leader_length_m, index=paired.index)
    )
    paired["gap_m"] = paired["x_leader"] - paired["x"] - leader_len
    return episodes_from_pairs(
        paired[["t", "veh_id", "lane", "leader_id", "gap_m", "v", "v_leader"]],
        dataset=dataset,
        min_duration_s=min_duration_s,
    )
