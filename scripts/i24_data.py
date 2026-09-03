"""Shared I-24 MOTION data access for the flagship drivers (ROADMAP §1).

Data: ``data/i24motion/processed/i24_wb_20221130/`` — the westbound
carriageway of the I-24 MOTION INCEPTION run of 30 Nov 2022 (06:00–10:00 CST,
MM 58.7–62.7), streamed out of the 5.8 GB MongoDB export by
``scripts/i24_extract.py`` (``calibration.loaders.i24motion.convert_i24_to_parquet``)
into 5 Hz Parquet. Conventions are the loader's (see its module docstring):
``t`` in seconds since 06:00:00 CST (``T0_UNIX``), ``x`` the front bumper in
meters along travel with 0 at MM 62.7, ``lane`` 1–4 mainline (1 = HOV).

Facts every consumer must respect (verified on this run):

1. **Documents are fragments.** 576,511 westbound fragments; median 117 m
   long, median 6 s; ~64,600 last ≥ 30 s. Nothing is stitched — episodes,
   counts and fields are computed on fragments as delivered.
2. **Coverage is incomplete and locally variable** (overpasses, tall-vehicle
   occlusion, tracker breaks; data documentation "Known artifacts"). Any
   count or Edie density/flow from this data is a lower bound at the local
   tracking coverage; speeds (TTD/TTT) are coverage-robust.
3. **Provenance.** ``data_hash`` is the sha256 of the source zip, recorded
   in ``meta.json`` by the conversion and copied into every artifact.

Run scripts from the repo root with ``uv run --no-sync python scripts/...``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from calibration.episodes import LeaderFollowerEpisode, episodes_from_pairs
from calibration.loaders.i24motion import (
    I24_MAINLINE_LANES,
    I24_PASSENGER_CLASSES,
    load_i24_parquet,
    load_i24_vehicles,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
I24_DIR = REPO_ROOT / "data" / "i24motion"
PROCESSED_DIR = I24_DIR / "processed"
WB_DIR = PROCESSED_DIR / "i24_wb_20221130"

#: Unix time of ``t = 0`` (2022-11-30 06:00:00 CST); asserted against meta.json.
T0_UNIX = 1669809600.0

#: Sampling interval of the processed Parquet [s] (25 Hz decimated by 5).
SAMPLE_DT_S = 0.2

#: Mainline lanes (1 = HOV/leftmost … 4 = rightmost), from the loader.
MAINLINE_LANES = I24_MAINLINE_LANES

#: Study span [m] along travel (0 = MM 62.7). The instrument covers ~MM 58.55–62.93
#: but coverage is thin at both ends; the 4-mile testbed is [0, 6437 m].
TESTBED_LENGTH_M = 4.0 * 1609.344

#: Gap plausibility bounds for position-ordered leader pairing on fragments.
#: A pair whose bumper-to-bumper gap exceeds ``MAX_GAP_M`` is treated as "no
#: tracked leader" (the true leader is most likely an untracked fragment,
#: so the next tracked vehicle would be paired spuriously); a gap under
#: ``MIN_GAP_M`` is a duplicate fragment of the same vehicle (documented
#: homography artifact). Both mask the sample so it cuts the episode.
MAX_GAP_M = 100.0
MIN_GAP_M = 0.5


def meta() -> dict[str, Any]:
    """The conversion's ``meta.json`` (provenance + parameters)."""
    m = json.loads((WB_DIR / "meta.json").read_text())
    if abs(float(m["t_origin_unix"]) - T0_UNIX) > 1e-6:
        raise ValueError(f"unexpected time origin {m['t_origin_unix']} (expected {T0_UNIX})")
    return m


def data_hash() -> str:
    """sha256 of the source zip, as recorded by the conversion."""
    return str(meta()["data_hash"])


def clock(t_s: float) -> str:
    """``t`` seconds after 06:00 CST → ``HH:MM`` local time string."""
    h = 6 + int(t_s // 3600)
    m = int((t_s % 3600) // 60)
    return f"{h:02d}:{m:02d}"


def load_mainline(
    t_range_s: tuple[float, float] | None = None,
    x_range_m: tuple[float, float] | None = None,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """Westbound mainline (lanes 1–4) trajectory rows, optionally sliced."""
    return load_i24_parquet(
        WB_DIR, t_range_s=t_range_s, x_range_m=x_range_m, lanes=MAINLINE_LANES, columns=columns
    )


def load_vehicles() -> pd.DataFrame:
    """Per-fragment table."""
    return load_i24_vehicles(WB_DIR)


def build_lane_episodes(
    lane_df: pd.DataFrame,
    lane: int,
    *,
    min_duration_s: float = 30.0,
    max_gap_m: float = MAX_GAP_M,
    min_gap_m: float = MIN_GAP_M,
    follower_classes: frozenset[int] = I24_PASSENGER_CLASSES,
) -> list[LeaderFollowerEpisode]:
    """Leader-follower episodes for one mainline lane from fragment rows.

    Leaders are derived by position ordering within each ``(t, lane)`` group
    (the schema publishes none): the leader is the nearest tracked vehicle
    ahead in the same lane at the same 0.2 s grid slot. The bumper-to-bumper
    gap is ``x_leader − length_leader − x_follower`` (``x`` is the front
    bumper). Fragment coverage makes position-ordered pairing fallible in
    two documented ways, both masked to NaN so they cut the episode instead
    of poisoning it: a gap above ``max_gap_m`` (true leader probably
    untracked) and a gap below ``min_gap_m`` (duplicate fragment of the same
    vehicle). Followers are restricted to passenger classes; leaders may be
    any class (their length is known). Episode cutting and validation
    (≥ ``min_duration_s`` continuous, uniform dt, no leader change) is the
    package's ``episodes_from_pairs``.

    Args:
        lane_df: Rows of one lane with ``t, veh_id, x, v, length, cls``.
        lane: Lane index (stored in the episode metadata).
        min_duration_s: Minimum continuous episode duration [s].
        max_gap_m: Upper gap plausibility bound [m].
        min_gap_m: Lower gap plausibility bound [m].
        follower_classes: ``coarse_vehicle_class`` codes accepted as followers.

    Returns:
        Validated episodes; ``metadata['dataset'] == 'i24motion_wb'``.
    """
    df = lane_df.sort_values(["t", "x"], ascending=[True, False], kind="stable").copy()
    df["leader_id"] = df.groupby("t", sort=False)["veh_id"].shift(1)
    df["x_leader"] = df.groupby("t", sort=False)["x"].shift(1)
    df["v_leader"] = df.groupby("t", sort=False)["v"].shift(1)
    df["len_leader"] = df.groupby("t", sort=False)["length"].shift(1)
    df["gap_m"] = df["x_leader"] - df["len_leader"] - df["x"]
    bad = (df["gap_m"] < min_gap_m) | (df["gap_m"] > max_gap_m)
    df.loc[bad, "gap_m"] = np.nan
    df = df.loc[df["cls"].isin(list(follower_classes))]
    df["lane"] = lane
    return episodes_from_pairs(
        df[["t", "veh_id", "lane", "leader_id", "gap_m", "v", "v_leader"]],
        dataset="i24motion_wb",
        min_duration_s=min_duration_s,
    )
