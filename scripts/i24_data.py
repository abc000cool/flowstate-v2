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

**Other mornings** (docs/PRE_FRISCO_PROGRAM.md C9, 2026-10-07). A day is an
:class:`I24Day`: its processed directory and the Unix time of its ``t = 0``
(06:00 CST of that date, :func:`t0_unix_for_date`). Every module-level name
(``WB_DIR``, ``T0_UNIX``, :func:`meta`, :func:`data_hash`, :func:`clock`,
:func:`load_mainline`, :func:`load_vehicles`, :func:`source_label`) serves the
*active* day, :data:`DAY`, which is the committed 30 Nov 2022 day unless the
environment names another with both ``I24_DAY_DIR`` and ``I24_T0_UNIX``
(:func:`day_from_env`; one without the other is refused). So every script that
imports this module runs on another morning unchanged, e.g.::

    I24_DAY_DIR=data/i24motion/processed/i24_wb_20221129 I24_T0_UNIX=1669723200 \\
        uv run --no-sync python scripts/i24_coverage.py --out <path>

Without the two variables every name and every function returns exactly what
it returned before the parameterisation (tests/test_scripts/test_i24_data_days.py
proves it byte for byte on a synthetic day against the earlier code). A script
that reads several days at once (``scripts/i24_virtual_detectors.py``) holds
:class:`I24Day` objects instead. ``python scripts/i24_data.py t0 YYYY-MM-DD``
prints a date's origin and ``python scripts/i24_data.py check YYYY-MM-DD DIR``
checks that a processed directory holds that date's morning
(:meth:`I24Day.check_recording`).

Run scripts from the repo root with ``uv run --no-sync python scripts/...``.
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
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

#: The committed day: the 30 Nov 2022 westbound conversion (``scripts/i24_extract.py``'s
#: default output), on which every I-24 artifact before C9 was built.
DEFAULT_DAY_DIR = PROCESSED_DIR / "i24_wb_20221130"

#: Unix time of the committed day's ``t = 0`` (2022-11-30 06:00:00 CST); asserted against meta.json.
DEFAULT_T0_UNIX = 1669809600.0

#: The committed day's source, as every artifact built on it spells it (``artifacts/i24_coverage.json``).
DEFAULT_SOURCE_LABEL = (
    "I-24 MOTION INCEPTION v1.x, 30 Nov 2022 westbound (6386d89efb3ff533c12df167__post10)"
)

#: Environment variables naming another day (both or neither; :func:`day_from_env`).
DAY_DIR_ENV = "I24_DAY_DIR"
T0_ENV = "I24_T0_UNIX"

#: The recordings' clock: Central Standard Time, UTC−6. ``t = 0`` is 06:00 CST of the
#: recording's date (the INCEPTION mornings run 06:00–10:00 CST, docs/I24_DATA.md §3).
CST_OFFSET_S = -6 * 3600
DAY_START_LOCAL_S = 6 * 3600

#: What a morning must cover (:meth:`I24Day.check_recording`): the study period
#: 06:30–08:30 CST with the validator's 60-s pads (``scripts/i24_validate.py``
#: ``observed_side``), and a first sample no earlier than 05:00 CST — a recording of
#: another date sits whole days away from ``t = 0``.
REQUIRED_SPAN_S = (1740.0, 9060.0)
EARLIEST_START_S = -3600.0

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


def _second_sunday_march(year: int) -> date:
    first = date(year, 3, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7)


def _first_sunday_november(year: int) -> date:
    first = date(year, 11, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7)


def central_daylight_time(day: date) -> bool:
    """Whether 06:00 local in Nashville is daylight time (CDT) on ``day``.

    United States daylight saving time since 2007 (15 U.S.C. 260a as amended
    by the Energy Policy Act of 2005): from the second Sunday of March to the
    first Sunday of November, switching at 02:00 local, so 06:00 of the first
    day is already daylight time and 06:00 of the last is standard time.
    """
    return _second_sunday_march(day.year) <= day < _first_sunday_november(day.year)


def t0_unix_for_date(day: str | date) -> float:
    """Unix time of 06:00 CST on ``day`` — an INCEPTION morning's ``t = 0``.

    ``scripts/i24_extract.py --t-origin`` takes it, so ``t`` is seconds after
    06:00 CST on every day as on 30 Nov 2022 (``t0_unix_for_date("2022-11-30")
    == DEFAULT_T0_UNIX``).

    Args:
        day: ``YYYY-MM-DD`` or a :class:`datetime.date`.

    Raises:
        ValueError: An unparseable date, or one on which Nashville keeps daylight
            time (06:00 local would be CDT, not CST; every artifact labels the
            clock CST).
    """
    d = day if isinstance(day, date) else date.fromisoformat(str(day))
    if central_daylight_time(d):
        raise ValueError(
            f"{d.isoformat()} is in daylight time in Nashville: 06:00 local is CDT, not the CST "
            "every I-24 artifact assumes; give the origin explicitly"
        )
    midnight_utc = calendar.timegm((d.year, d.month, d.day, 0, 0, 0))
    return float(midnight_utc + DAY_START_LOCAL_S - CST_OFFSET_S)


@dataclass(frozen=True)
class I24Day:
    """One processed I-24 MOTION morning (module docstring, "Other mornings").

    Attributes:
        day_dir: The conversion's output directory (``trajectories.parquet``,
            ``vehicles.parquet``, ``meta.json``).
        t0_unix: Unix time of its ``t = 0`` (the conversion's ``--t-origin``).
    """

    day_dir: Path
    t0_unix: float

    @property
    def is_default(self) -> bool:
        """Whether this is the committed day (its directory and origin)."""
        return (
            self.day_dir.resolve() == DEFAULT_DAY_DIR.resolve() and self.t0_unix == DEFAULT_T0_UNIX
        )

    @property
    def date(self) -> str:
        """Local (CST) date of ``t = 0``, ``YYYY-MM-DD``."""
        return datetime.fromtimestamp(self.t0_unix + CST_OFFSET_S, UTC).date().isoformat()

    def meta(self) -> dict[str, Any]:
        """The conversion's ``meta.json`` (provenance + parameters), origin checked."""
        m = json.loads((self.day_dir / "meta.json").read_text())
        if abs(float(m["t_origin_unix"]) - self.t0_unix) > 1e-6:
            raise ValueError(
                f"unexpected time origin {m['t_origin_unix']} (expected {self.t0_unix})"
            )
        return m

    def data_hash(self) -> str:
        """sha256 of the source zip, as recorded by the conversion."""
        return str(self.meta()["data_hash"])

    def clock(self, t_s: float) -> str:
        """``t`` seconds after the origin → ``HH:MM`` local (CST) time string."""
        local0 = (self.t0_unix + CST_OFFSET_S) % 86400.0
        if local0 % 3600.0 == 0.0:
            h = int(local0 // 3600.0) + int(t_s // 3600)
            m = int((t_s % 3600) // 60)
        else:
            s = local0 + t_s
            h = int(s // 3600)
            m = int((s % 3600) // 60)
        return f"{h:02d}:{m:02d}"

    def source_label(self) -> str:
        """The recording as artifacts name it: the committed day's fixed label, or
        ``I-24 MOTION INCEPTION v1.x, <d Mon YYYY> <direction> (<zip stem>)``."""
        if self.is_default:
            return DEFAULT_SOURCE_LABEL
        m = self.meta()
        d = date.fromisoformat(self.date)
        name = Path(str(m.get("source", self.day_dir.name)))
        stem = name.stem if name.suffix.lower() in (".zip", ".json") else name.name
        way = "westbound" if int(m.get("direction", -1)) < 0 else "eastbound"
        return f"I-24 MOTION INCEPTION v1.x, {d.day} {d:%b} {d.year} {way} ({stem})"

    def load_mainline(
        self,
        t_range_s: tuple[float, float] | None = None,
        x_range_m: tuple[float, float] | None = None,
        columns: list[str] | None = None,
    ) -> pd.DataFrame:
        """Mainline (lanes 1–4) trajectory rows, optionally sliced."""
        return load_i24_parquet(
            self.day_dir,
            t_range_s=t_range_s,
            x_range_m=x_range_m,
            lanes=MAINLINE_LANES,
            columns=columns,
        )

    def load_vehicles(self) -> pd.DataFrame:
        """Per-fragment table."""
        return load_i24_vehicles(self.day_dir)

    def check_recording(self, expected_date: str | None = None) -> list[str]:
        """Why this directory does not hold a usable morning of its date (empty: it does).

        Reads ``meta.json`` only: the origin must be this day's (:meth:`meta`),
        the carriageway westbound, the first sample no earlier than
        :data:`EARLIEST_START_S` and the samples must span :data:`REQUIRED_SPAN_S`
        (06:29–08:31 CST). A zip of another date converted with this date's
        origin fails the span checks by whole days.

        Args:
            expected_date: ``YYYY-MM-DD`` the caller believes the recording is
                (default: the origin's date).
        """
        problems: list[str] = []
        if expected_date is not None and expected_date != self.date:
            problems.append(f"the origin {self.t0_unix:.0f} is {self.date}, not {expected_date}")
        try:
            m = self.meta()
        except (OSError, ValueError, KeyError) as exc:
            return [*problems, f"meta.json: {exc}"]
        if int(m.get("direction", 0)) != -1:
            problems.append(f"direction {m.get('direction')} is not westbound (-1)")
        t_min, t_max = m.get("t_min_s"), m.get("t_max_s")
        if t_min is None or t_max is None:
            return [*problems, "meta.json records no sample times"]
        if not EARLIEST_START_S <= float(t_min) <= REQUIRED_SPAN_S[0]:
            problems.append(
                f"first sample at t = {float(t_min):.0f} s, outside [{EARLIEST_START_S:.0f}, "
                f"{REQUIRED_SPAN_S[0]:.0f}] s of 06:00 CST {self.date}"
            )
        if float(t_max) < REQUIRED_SPAN_S[1]:
            problems.append(
                f"last sample at t = {float(t_max):.0f} s, before {REQUIRED_SPAN_S[1]:.0f} s "
                "(08:31 CST): the study period is not covered"
            )
        return problems


def day_from_env(environ: Mapping[str, str] | None = None) -> I24Day:
    """The day the environment names (``I24_DAY_DIR`` + ``I24_T0_UNIX``), else the committed one.

    A relative directory is taken from the repository root.

    Raises:
        ValueError: Only one of the two variables is set, or the origin is not a number.
    """
    env = os.environ if environ is None else environ
    day_dir, t0 = env.get(DAY_DIR_ENV), env.get(T0_ENV)
    if day_dir is None and t0 is None:
        return I24Day(DEFAULT_DAY_DIR, DEFAULT_T0_UNIX)
    if day_dir is None or t0 is None:
        raise ValueError(
            f"set both {DAY_DIR_ENV} and {T0_ENV} to use another I-24 day, or neither "
            f"(got {DAY_DIR_ENV}={day_dir!r}, {T0_ENV}={t0!r})"
        )
    path = Path(day_dir)
    return I24Day(path if path.is_absolute() else REPO_ROOT / path, float(t0))


_ENV_DAY = day_from_env()

#: The active day's processed directory (the committed day unless the environment names another).
WB_DIR = _ENV_DAY.day_dir

#: Unix time of the active day's ``t = 0`` (2022-11-30 06:00:00 CST by default); asserted against meta.json.
T0_UNIX = _ENV_DAY.t0_unix


def active_day() -> I24Day:
    """The day the module-level functions read: ``I24Day(WB_DIR, T0_UNIX)``."""
    return I24Day(WB_DIR, T0_UNIX)


def meta() -> dict[str, Any]:
    """The conversion's ``meta.json`` (provenance + parameters)."""
    return active_day().meta()


def data_hash() -> str:
    """sha256 of the source zip, as recorded by the conversion."""
    return active_day().data_hash()


def clock(t_s: float) -> str:
    """``t`` seconds after 06:00 CST → ``HH:MM`` local time string."""
    return active_day().clock(t_s)


def source_label() -> str:
    """The active recording as artifacts name it (:meth:`I24Day.source_label`)."""
    return active_day().source_label()


def load_mainline(
    t_range_s: tuple[float, float] | None = None,
    x_range_m: tuple[float, float] | None = None,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """Westbound mainline (lanes 1–4) trajectory rows, optionally sliced."""
    return active_day().load_mainline(t_range_s=t_range_s, x_range_m=x_range_m, columns=columns)


def load_vehicles() -> pd.DataFrame:
    """Per-fragment table."""
    return active_day().load_vehicles()


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


def main(argv: list[str] | None = None) -> int:
    """``t0 DATE`` prints a date's origin; ``check DATE DIR`` checks a processed morning."""
    ap = argparse.ArgumentParser(prog="python scripts/i24_data.py", description=main.__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_t0 = sub.add_parser("t0", help="Unix time of 06:00 CST on DATE (the extraction's --t-origin)")
    p_t0.add_argument("date")
    p_check = sub.add_parser("check", help="refuse unless DIR holds the westbound morning of DATE")
    p_check.add_argument("date")
    p_check.add_argument("day_dir", type=Path)
    args = ap.parse_args(argv)
    try:
        t0 = t0_unix_for_date(args.date)
    except ValueError as exc:
        print(f"i24_data: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "t0":
        print(f"{t0:.0f}")
        return 0
    day = I24Day(args.day_dir if args.day_dir.is_absolute() else REPO_ROOT / args.day_dir, t0)
    problems = day.check_recording(args.date)
    for p in problems:
        print(f"i24_data: {args.day_dir}: {p}", file=sys.stderr)
    if problems:
        return 1
    m = day.meta()
    print(
        f"{args.date}: {day.source_label()}, t in [{float(m['t_min_s']):.0f}, "
        f"{float(m['t_max_s']):.0f}] s, data_hash {str(m['data_hash'])[:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
