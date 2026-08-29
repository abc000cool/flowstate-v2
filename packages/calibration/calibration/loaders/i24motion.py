"""I-24 MOTION trajectory loader (CLAUDE.md §6.2).

Parses trajectory documents in the I-24 MOTION testbed schema (Gloudemans et
al., "I-24 MOTION: an instrument for freeway traffic science", arXiv:2302.12308;
i24motion.org). **Access requires registration** at https://i24motion.org —
the INCEPTION v1 release is distributed to registered researchers; at its
scale the Vanderbilt virtual-trajectory tools (arXiv:2311.10888) are the
recommended companion. This loader is therefore exercised only on tiny
synthetic fixtures shaped like the documented schema.

Documented schema: one JSON document per vehicle with (at least)
``_id``, ``timestamp`` (array, s — Unix epoch, ~25 Hz), ``x_position``
(array, **feet** along the roadway), ``y_position`` (array, feet lateral),
``length``, ``width`` (feet), ``direction`` (+1 eastbound, −1 westbound) and
``coarse_vehicle_class``. Files are either a JSON array of documents or one
document per line (both accepted).

Positions convert to meters via the sanctioned
:data:`~calibration.loaders.ngsim.FEET_TO_M` ingestion constant. The schema
carries no per-sample speed, so speed is computed as the finite-difference
gradient of position along the direction of travel (documented estimator —
inherits the positional noise of the source). No leader ids are published;
leaders are re-derived by position ordering per lane via
:func:`calibration.episodes.extract_episodes`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd

from calibration.episodes import (
    MIN_EPISODE_DURATION_S,
    LeaderFollowerEpisode,
    extract_episodes,
)
from calibration.loaders.ngsim import FEET_TO_M

LANE_WIDTH_FT_DEFAULT: Final[float] = 12.0
"""Standard US freeway lane width [ft] used to bin y_position into lanes."""


def _read_documents(path: str | Path) -> list[dict[str, object]]:
    """Read a JSON array file or a line-delimited JSON file of documents."""
    text = Path(path).read_text()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON array or line-delimited documents")
    return data


def load_i24_trajectories(
    path: str | Path,
    *,
    direction: int = -1,
    lane_width_ft: float = LANE_WIDTH_FT_DEFAULT,
) -> pd.DataFrame:
    """Load I-24 MOTION trajectory documents into a tidy SI trajectory table.

    Timestamps are snapped to a shared uniform grid (median sample interval,
    global origin at the earliest timestamp) so that different vehicles' rows
    align exactly for leader pairing. The along-road coordinate is oriented
    with travel: ``x = direction × x_position × FEET_TO_M``, so a leader
    always has larger ``x`` regardless of carriageway. Speed is the magnitude
    of the finite-difference gradient of ``x``.

    Args:
        path: JSON file (array or line-delimited documents).
        direction: Which carriageway to keep (+1 eastbound, −1 westbound —
            the I-24 MOTION convention; westbound is the instrumented
            congestion direction).
        lane_width_ft: Lane width [ft] used to bin ``y_position`` into
            integer lane indices.

    Returns:
        DataFrame with columns ``t`` [s, grid-aligned, origin at first
        sample], ``veh_id`` (str), ``x`` [m, increasing with travel],
        ``lane`` (int), ``v`` [m/s] and ``length`` [m].

    Raises:
        ValueError: On schema violations or no documents for ``direction``.
    """
    if lane_width_ft <= 0:
        raise ValueError(f"lane_width_ft must be > 0, got {lane_width_ft}")
    docs = [d for d in _read_documents(path) if int(d.get("direction", 0)) == direction]
    if not docs:
        raise ValueError(f"{path}: no documents with direction {direction}")

    t0 = min(float(np.min(np.asarray(d["timestamp"], dtype=float))) for d in docs)
    dts = []
    for d in docs:
        ts = np.asarray(d["timestamp"], dtype=float)
        if ts.shape[0] >= 2:
            dts.append(float(np.median(np.diff(ts))))
    if not dts:
        raise ValueError(f"{path}: no document has >= 2 samples")
    dt = float(np.median(dts))

    frames_list: list[pd.DataFrame] = []
    for d in docs:
        ts = np.asarray(d["timestamp"], dtype=float)
        x_ft = np.asarray(d["x_position"], dtype=float)
        y_ft = np.asarray(d["y_position"], dtype=float)
        if not (ts.shape == x_ft.shape == y_ft.shape):
            raise ValueError(f"{path}: ragged arrays in document {d.get('_id')}")
        if ts.shape[0] < 2:
            continue
        t = np.round((ts - t0) / dt) * dt
        x = direction * x_ft * FEET_TO_M
        v = np.abs(np.gradient(x, t))
        frames_list.append(
            pd.DataFrame(
                {
                    "t": t,
                    "veh_id": str(d["_id"]),
                    "x": x,
                    "lane": np.floor(y_ft / lane_width_ft).astype(int),
                    "v": v,
                    "length": float(d.get("length", 0.0)) * FEET_TO_M,
                }
            )
        )
    if not frames_list:
        raise ValueError(f"{path}: no usable documents")
    return pd.concat(frames_list, ignore_index=True)


def load_i24_episodes(
    path: str | Path,
    *,
    direction: int = -1,
    lane_width_ft: float = LANE_WIDTH_FT_DEFAULT,
    min_duration_s: float = MIN_EPISODE_DURATION_S,
) -> list[LeaderFollowerEpisode]:
    """Load an I-24 MOTION file and extract leader-follower episodes.

    Leaders are derived by per-lane position ordering (the schema publishes
    none) and gaps are bumper-to-bumper using each leader's ``length``; see
    :func:`calibration.episodes.extract_episodes`.

    Args:
        path: JSON file (array or line-delimited documents).
        direction: Carriageway filter (+1 / −1).
        lane_width_ft: Lane width [ft] for lane binning.
        min_duration_s: Minimum episode duration [s] (contract default 30 s).

    Returns:
        List of validated episodes with ``metadata['dataset'] == 'i24motion'``.
    """
    df = load_i24_trajectories(path, direction=direction, lane_width_ft=lane_width_ft)
    return extract_episodes(df, dataset="i24motion", min_duration_s=min_duration_s)
