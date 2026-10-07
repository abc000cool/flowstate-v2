"""Calibration / validation day split (docs/FRISCO_PROTOCOL.md §3).

A corridor study calibrates its model on some days and tests it, unchanged,
on others. Which days go where is decided here, mechanically and before any
simulation, by the rules the protocol locks:

1. **Candidate days** (§3.1): Tuesday, Wednesday and Thursday; not a public
   holiday; not a day the agency's logs name for an incident or weather event
   on the stretch during the study period; and not a day on which fewer than
   80 % of the selected mainline stations are usable in the study period.
2. **The split** (§3.2, as amended before any data on 2026-10-04): the
   candidates are ranked by their mainline volume in the study period and cut
   into terciles; 60 % of ALL candidates, rounded down, go to **calibration**,
   allocated across the terciles in proportion to their size by largest
   remainder (ties broken by a seeded order), with at least one per tercile
   whenever the total allows; within each tercile a seeded draw (seed
   :data:`SPLIT_SEED`, ``numpy.random.default_rng``) picks which days; the
   rest go to **validation**. (The first wording rounded down inside each
   tercile, which with three-day terciles sends one day in three to
   calibration, not 60 %.)
3. **Minimum** (§3.3): five calibration and three validation days. With fewer
   the split is still made and flagged ``underpowered``.

Every step is recorded in the :class:`DaySplit` (``to_dict``): each date's
weekday, why it is or is not a candidate, its usable-station share and its
volume, the terciles with their volume bounds, the positions the draw picked
in each tercile, and the rules themselves, so a reviewer can redo the split
by hand.

**Usable station-day.** With a data-quality report
(:mod:`calibration.data_quality`, the ``flowstate.data_quality/1`` JSON
computed over the study period) a station is usable on a date when no
mainline sensor of that station (the station itself, or any of its lanes;
a lane that reported nothing on any date of the report is not an installed
lane and is left out) is judged ``exclude`` that day, and a station with no
judged sensor that day is not usable. A date the report does not cover at all
is not judged "0 stations usable": the split refuses it, or — with
``allow_uncovered_dates`` — records it as "not covered by the data-quality
report" and leaves it out. Without a report, a station-day is usable when the
share of the study period's windows with a finite flow is at least
``1 − MISSING_EXCLUDE_SHARE`` — the data-quality module's own exclusion rule
for missing data, applied to the study period. Which rule was used is
recorded (``usability_source``).

**Per-lane input.** A per-lane frame is summed to station totals by
:func:`station_totals` with the rules of :mod:`calibration.lane_totals`, the
same as :func:`calibration.conservation.station_grid` and the data-quality
report: a lane with no finite flow in the study period on any examined date
is a placeholder and is not counted; a lane excluded by name is counted and
missing; a station-window is a total only when every counted lane reported
it. Nothing is scaled up, so a station that lost a lane has no volume rather
than a low one.

**Selected stations** are the protocol's §2.2 list, computed from the
data-quality report by :mod:`calibration.station_selection` and stored in the
corridor's ``selection.json`` (``scripts/day_split.py --selection``).

**Volume.** A day's volume is the **station-mean study-period volume**: for
each selected station usable that day, its mean observed flow over the study
period's windows [veh/h] times the period's length [h]; then the mean over
those stations. With every station complete this is the total mainline
volume divided by the station count, so it ranks days exactly as the total
does; it differs only in that a day with an unusable station, or with a few
missing windows, is not ranked low for that reason alone. Nothing is imputed
into any data: the volume is a ranking statistic and is used for nothing
else.

**Public holidays.** United States federal holidays are screened
automatically (:func:`us_federal_holidays`, rules stated there). State and
local holidays, incidents and weather events come from the caller's
exclusion list, with a reason per date; the split records when no list was
supplied.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from calibration.conservation import normalize_date
from calibration.data_quality import MISSING_EXCLUDE_SHARE, QualityVerdicts
from calibration.lane_totals import (
    complete_sum,
    frame_exclusions,
    is_per_lane,
    require_attributed,
    station_lanes,
)
from calibration.loaders.detector_csv import detector_interval_s, local_dates, local_seconds

DAY_SPLIT_SCHEMA: Final[str] = "flowstate.day_split/1"
"""Schema tag of the split's JSON form."""

SPLIT_SEED: Final[int] = 20261004
"""Seed of the draw (docs/FRISCO_PROTOCOL.md §3.2): ``numpy.random.default_rng``."""

CANDIDATE_WEEKDAYS: Final[tuple[int, ...]] = (1, 2, 3)
"""Weekdays that may be candidates, ``datetime.date.weekday()`` numbering
(Monday is zero): Tuesday, Wednesday, Thursday (§3.1)."""

MIN_USABLE_STATION_SHARE: Final[float] = 0.80
"""A candidate day needs at least this share of the selected stations usable
in the study period (§3.1; a FlowState rule)."""

N_STRATA: Final[int] = 3
"""Volume strata: terciles (§3.2)."""

CALIBRATION_SHARE: Final[float] = 0.60
"""Share of all candidate days sent to calibration, rounded down, allocated
across terciles by largest remainder (§3.2, amended 2026-10-04)."""

MIN_CALIBRATION_PER_STRATUM: Final[int] = 1
"""At least this many calibration days per non-empty tercile (§3.2, "at least
one per tercile where possible")."""

MIN_CALIBRATION_DAYS: Final[int] = 5
"""Fewer calibration days flag the split ``underpowered`` (§3.3)."""

MIN_VALIDATION_DAYS: Final[int] = 3
"""Fewer validation days flag the split ``underpowered`` (§3.3)."""

MIN_USABLE_WINDOW_SHARE: Final[float] = 1.0 - MISSING_EXCLUDE_SHARE
"""Without a data-quality report, a station-day is usable when at least this
share of the study period's windows carry a finite flow: the complement of
``calibration.data_quality.MISSING_EXCLUDE_SHARE`` (a day missing more than
that share is excluded there)."""

STRATUM_LABELS: Final[tuple[str, ...]] = ("low", "middle", "high")
"""Names of the terciles, lowest volume first."""

WEEKDAY_NAMES: Final[tuple[str, ...]] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

NOT_COVERED_REASON: Final[str] = "not covered by the data-quality report"
"""The reason a date the data-quality report never judged is left out (only
with ``allow_uncovered_dates``; otherwise the split is refused)."""

JUNETEENTH_FIRST_YEAR: Final[int] = 2021
"""First year Juneteenth National Independence Day is a federal holiday
(Pub. L. 117-17, signed 17 June 2021, amending 5 U.S.C. 6103(a))."""

_MAINLINE: Final[str] = "mainline"
_S_PER_DAY: Final[float] = 86400.0
_S_PER_H: Final[float] = 3600.0


# ---------------------------------------------------------------------------
# Holidays
# ---------------------------------------------------------------------------


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``-th ``weekday`` (Monday = 0) of a month; ``n = -1`` is the last."""
    if n > 0:
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + timedelta(days=offset + 7 * (n - 1))
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    """The weekday a fixed-date federal holiday is observed on (5 U.S.C. 6103(b))."""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def us_federal_holidays(year: int) -> dict[str, str]:
    """United States federal holidays of one year (5 U.S.C. 6103).

    Fixed-date holidays (New Year's Day, Juneteenth from
    :data:`JUNETEENTH_FIRST_YEAR`, Independence Day, Veterans Day, Christmas
    Day) are listed on their date and, when that is a weekend day, also on the
    weekday they are observed; the others by their rule (third Monday of
    January and February, last Monday of May, first Monday of September,
    second Monday of October, fourth Thursday of November). State and local holidays are not included: they come from the
    caller's exclusion list.

    Args:
        year: Calendar year.

    Returns:
        ``YYYY-MM-DD`` → holiday name.
    """
    fixed = {"New Year's Day": date(year, 1, 1)}
    if year >= JUNETEENTH_FIRST_YEAR:
        fixed["Juneteenth National Independence Day"] = date(year, 6, 19)
    fixed |= {
        "Independence Day": date(year, 7, 4),
        "Veterans Day": date(year, 11, 11),
        "Christmas Day": date(year, 12, 25),
    }
    out: dict[str, str] = {}
    for name, day in fixed.items():
        out[day.isoformat()] = name
        observed = _observed(day)
        if observed != day:
            out[observed.isoformat()] = f"{name} (observed)"
    ruled = {
        "Birthday of Martin Luther King, Jr.": _nth_weekday(year, 1, 0, 3),
        "Washington's Birthday": _nth_weekday(year, 2, 0, 3),
        "Memorial Day": _nth_weekday(year, 5, 0, -1),
        "Labor Day": _nth_weekday(year, 9, 0, 1),
        "Columbus Day": _nth_weekday(year, 10, 0, 2),
        "Thanksgiving Day": _nth_weekday(year, 11, 3, 4),
    }
    for name, day in ruled.items():
        out[day.isoformat()] = name
    return out


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


def _num(value: float | None, digits: int = 6) -> float | None:
    """Finite float rounded for JSON, else ``None``."""
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


@dataclass(frozen=True)
class DayRecord:
    """One date's screening.

    Attributes:
        date: Local date ``YYYY-MM-DD``.
        weekday: Weekday name.
        candidate: Whether the date entered the split.
        reasons: Why it did not (empty for a candidate).
        n_selected: Selected stations.
        n_usable: Selected stations usable that day in the study period.
        usable_share: ``n_usable / n_selected``.
        unusable_stations: The selected stations not usable that day.
        volume_veh: Station-mean study-period volume [veh] (module
            docstring), ``None`` when no usable station measured a flow.
        n_station_windows: Station-windows with a finite flow behind
            ``volume_veh``.
    """

    date: str
    weekday: str
    candidate: bool
    reasons: tuple[str, ...]
    n_selected: int
    n_usable: int
    usable_share: float
    unusable_stations: tuple[str, ...]
    volume_veh: float | None
    n_station_windows: int

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "date": self.date,
            "weekday": self.weekday,
            "candidate": self.candidate,
            "reasons": list(self.reasons),
            "n_selected": self.n_selected,
            "n_usable": self.n_usable,
            "usable_share": _num(self.usable_share),
            "unusable_stations": list(self.unusable_stations),
            "volume_veh": _num(self.volume_veh, 1),
            "n_station_windows": self.n_station_windows,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> DayRecord:
        """Rebuild from :meth:`to_dict`."""
        volume = raw.get("volume_veh")
        share = raw.get("usable_share")
        return cls(
            date=str(raw["date"]),
            weekday=str(raw["weekday"]),
            candidate=bool(raw["candidate"]),
            reasons=tuple(str(r) for r in raw.get("reasons", ())),
            n_selected=int(raw["n_selected"]),
            n_usable=int(raw["n_usable"]),
            usable_share=math.nan if share is None else float(share),
            unusable_stations=tuple(str(s) for s in raw.get("unusable_stations", ())),
            volume_veh=None if volume is None else float(volume),
            n_station_windows=int(raw.get("n_station_windows", 0)),
        )


@dataclass(frozen=True)
class Stratum:
    """One volume tercile and its draw.

    Attributes:
        label: ``low``, ``middle`` or ``high``.
        dates: The tercile's days in date order (the order the draw indexes).
        volume_min_veh: Smallest day volume in the tercile.
        volume_max_veh: Largest.
        n_calibration: Days drawn for calibration.
        drawn_positions: Positions in ``dates`` the draw picked, ascending.
        calibration_dates: ``dates`` at those positions.
        validation_dates: The rest.
    """

    label: str
    dates: tuple[str, ...]
    volume_min_veh: float | None
    volume_max_veh: float | None
    n_calibration: int
    drawn_positions: tuple[int, ...]
    calibration_dates: tuple[str, ...]
    validation_dates: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "label": self.label,
            "dates": list(self.dates),
            "volume_min_veh": _num(self.volume_min_veh, 1),
            "volume_max_veh": _num(self.volume_max_veh, 1),
            "n_days": len(self.dates),
            "n_calibration": self.n_calibration,
            "drawn_positions": list(self.drawn_positions),
            "calibration_dates": list(self.calibration_dates),
            "validation_dates": list(self.validation_dates),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Stratum:
        """Rebuild from :meth:`to_dict`."""
        lo, hi = raw.get("volume_min_veh"), raw.get("volume_max_veh")
        return cls(
            label=str(raw["label"]),
            dates=tuple(str(d) for d in raw.get("dates", ())),
            volume_min_veh=None if lo is None else float(lo),
            volume_max_veh=None if hi is None else float(hi),
            n_calibration=int(raw["n_calibration"]),
            drawn_positions=tuple(int(p) for p in raw.get("drawn_positions", ())),
            calibration_dates=tuple(str(d) for d in raw.get("calibration_dates", ())),
            validation_dates=tuple(str(d) for d in raw.get("validation_dates", ())),
        )


def split_rules() -> dict[str, Any]:
    """The locked rules, as recorded in every split (module docstring)."""
    return {
        "protocol": "docs/FRISCO_PROTOCOL.md section 3",
        "seed": SPLIT_SEED,
        "rng": "numpy.random.default_rng(seed); one generator: first rng.permutation(n_strata) "
        "orders tied remainders, then terciles drawn low to high, within a tercile "
        "rng.choice(n_days, size=n_calibration, replace=False) over the tercile's days in "
        "date order",
        "candidate_weekdays": [WEEKDAY_NAMES[d] for d in CANDIDATE_WEEKDAYS],
        "min_usable_station_share": MIN_USABLE_STATION_SHARE,
        "n_strata": N_STRATA,
        "strata": "candidates ranked by (volume_veh, date) and cut by numpy.array_split into "
        "terciles, the lower terciles taking any remainder",
        "calibration_share": CALIBRATION_SHARE,
        "min_calibration_per_stratum": MIN_CALIBRATION_PER_STRATUM,
        "n_calibration_rule": "total = floor(calibration_share x n_candidates); quota of a "
        "tercile = n_days x total / n_candidates; each tercile gets floor(quota), raised to "
        "min_calibration_per_stratum for a non-empty tercile when the total is at least the "
        "number of non-empty terciles; the remaining days go one each to the largest "
        "fractional remainders, ties in the seeded permutation's order",
        "min_calibration_days": MIN_CALIBRATION_DAYS,
        "min_validation_days": MIN_VALIDATION_DAYS,
        "volume": "station-mean study-period volume: per usable selected station, mean finite "
        "flow over the study period's windows [veh/h] x period length [h]; mean over "
        "those stations",
        "usable_without_quality_report": "share of the study period's windows with a finite "
        f"flow >= {MIN_USABLE_WINDOW_SHARE:g} (1 - calibration.data_quality."
        "MISSING_EXCLUDE_SHARE)",
        "usable_with_quality_report": "no mainline sensor of the station (station or lane; lanes "
        "that reported nothing on any date of the report are not installed lanes) judged "
        "'exclude' that date; a station with no judged sensor that date is not usable; a date "
        "the report does not cover is refused, or with allow_uncovered_dates left out as "
        f"'{NOT_COVERED_REASON}'",
        "holidays": "United States federal holidays screened automatically "
        "(calibration.day_split.us_federal_holidays); state and local holidays, incidents "
        "and weather events from the caller's exclusion list",
    }


@dataclass(frozen=True)
class DaySplit:
    """The split and every step that produced it (module docstring).

    Attributes:
        study_period: ``(start, end)`` local clock, ``"HH:MM"``.
        seed: Seed of the draw.
        selected_stations: The stations whose usability and volume count.
        usability_source: ``"data_quality"`` or ``"completeness"``.
        days: One record per date examined, in date order.
        strata: The terciles, lowest volume first.
        calibration_dates: Calibration days, date order.
        validation_dates: Validation days, date order.
        underpowered: Fewer than :data:`MIN_CALIBRATION_DAYS` calibration or
            :data:`MIN_VALIDATION_DAYS` validation days.
        underpowered_reason: Plain statement of the shortfall, empty if none.
        exclusions: The caller's exclusion list (date → reason).
        notes: Plain statements about what was not checked.
        provenance: Inputs (paths, hashes) as the caller records them.
        rules: :func:`split_rules` at the time of the split.
        schema: :data:`DAY_SPLIT_SCHEMA`.
    """

    study_period: tuple[str, str]
    seed: int
    selected_stations: tuple[str, ...]
    usability_source: str
    days: tuple[DayRecord, ...]
    strata: tuple[Stratum, ...]
    calibration_dates: tuple[str, ...]
    validation_dates: tuple[str, ...]
    underpowered: bool
    underpowered_reason: str
    exclusions: dict[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    rules: dict[str, Any] = field(default_factory=split_rules)
    schema: str = DAY_SPLIT_SCHEMA

    @property
    def candidate_dates(self) -> tuple[str, ...]:
        """Dates that entered the split, date order."""
        return tuple(d.date for d in self.days if d.candidate)

    def dates_of(self, which: str) -> tuple[str, ...]:
        """``calibration`` or ``validation`` dates.

        Raises:
            ValueError: Any other name.
        """
        if which == "calibration":
            return self.calibration_dates
        if which == "validation":
            return self.validation_dates
        raise ValueError(f"day set must be 'calibration' or 'validation', got {which!r}")

    def summary(self) -> dict[str, Any]:
        """The headline a report prints (dates, counts, seed, flag)."""
        return {
            "seed": self.seed,
            "study_period": list(self.study_period),
            "n_dates_examined": len(self.days),
            "n_candidates": len(self.candidate_dates),
            "calibration_dates": list(self.calibration_dates),
            "validation_dates": list(self.validation_dates),
            "underpowered": self.underpowered,
            "underpowered_reason": self.underpowered_reason,
        }

    def to_dict(self) -> dict[str, Any]:
        """JSON form (fixed keys)."""
        return {
            "schema": self.schema,
            "study_period": {"start": self.study_period[0], "end": self.study_period[1]},
            "seed": self.seed,
            "rules": dict(self.rules),
            "selected_stations": list(self.selected_stations),
            "usability_source": self.usability_source,
            "exclusions": dict(sorted(self.exclusions.items())),
            "days": [d.to_dict() for d in self.days],
            "candidate_dates": list(self.candidate_dates),
            "strata": [s.to_dict() for s in self.strata],
            "calibration_dates": list(self.calibration_dates),
            "validation_dates": list(self.validation_dates),
            "n_calibration": len(self.calibration_dates),
            "n_validation": len(self.validation_dates),
            "underpowered": self.underpowered,
            "underpowered_reason": self.underpowered_reason,
            "notes": list(self.notes),
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> DaySplit:
        """Rebuild from :meth:`to_dict`.

        Raises:
            ValueError: A different schema.
        """
        schema = str(raw.get("schema", ""))
        if schema != DAY_SPLIT_SCHEMA:
            raise ValueError(f"expected schema {DAY_SPLIT_SCHEMA!r}, got {schema!r}")
        period = raw.get("study_period") or {}
        return cls(
            study_period=(str(period.get("start", "")), str(period.get("end", ""))),
            seed=int(raw["seed"]),
            selected_stations=tuple(str(s) for s in raw.get("selected_stations", ())),
            usability_source=str(raw.get("usability_source", "")),
            days=tuple(DayRecord.from_dict(d) for d in raw.get("days", ())),
            strata=tuple(Stratum.from_dict(s) for s in raw.get("strata", ())),
            calibration_dates=tuple(str(d) for d in raw.get("calibration_dates", ())),
            validation_dates=tuple(str(d) for d in raw.get("validation_dates", ())),
            underpowered=bool(raw.get("underpowered", False)),
            underpowered_reason=str(raw.get("underpowered_reason", "")),
            exclusions={str(k): str(v) for k, v in (raw.get("exclusions") or {}).items()},
            notes=tuple(str(n) for n in raw.get("notes", ())),
            provenance=dict(raw.get("provenance") or {}),
            rules=dict(raw.get("rules") or {}),
            schema=schema,
        )

    def to_json(self, path: str | Path) -> Path:
        """Write the JSON form (parents created); returns the path."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, allow_nan=False) + "\n")
        return target

    @classmethod
    def from_json(cls, path: str | Path) -> DaySplit:
        """Read a split written by :meth:`to_json`."""
        return cls.from_dict(json.loads(Path(path).read_text()))


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def parse_clock_span(start: str, end: str) -> tuple[float, float]:
    """``("HH:MM", "HH:MM")`` → seconds since local midnight; ``end`` may be ``24:00``.

    Raises:
        ValueError: Unparseable, or ``end`` not after ``start``.
    """

    def secs(text: str, allow_midnight: bool) -> float:
        parts = str(text).strip().split(":")
        if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
            raise ValueError(f"clock time must be HH:MM, got {text!r}")
        hours, minutes = int(parts[0]), int(parts[1])
        seconds = int(parts[2]) if len(parts) == 3 else 0
        total = hours * _S_PER_H + minutes * 60.0 + seconds
        if not (0 <= minutes < 60 and 0 <= seconds < 60):
            raise ValueError(f"clock time {text!r} is not a time of day")
        limit = _S_PER_DAY if allow_midnight else _S_PER_DAY - 1.0
        if not 0.0 <= total <= limit:
            raise ValueError(f"clock time {text!r} is not a time of day")
        return total

    lo, hi = secs(start, False), secs(end, True)
    if hi <= lo:
        raise ValueError(f"study period end {end!r} is not after its start {start!r}")
    return lo, hi


def station_totals(
    frame: pd.DataFrame,
    *,
    dates: Sequence[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    excluded_lanes: Mapping[str, Iterable[str]] | None = None,
) -> pd.DataFrame:
    """Station-level rows of a tidy detector frame (lanes summed when per lane).

    A station frame is returned as is. A per-lane frame (a ``lane`` column
    with values) is summed per station and timestamp by the rules of
    :mod:`calibration.lane_totals`, as :func:`calibration.conservation.station_grid`
    sums a grid: a lane with no finite flow anywhere in the period (``dates``
    and ``start``–``end``; the whole frame by default) is a placeholder and is
    not counted; a lane excluded by name (``frame.attrs["excluded_lanes"]``,
    ``excluded_lanes``) is counted and missing in every window; and a
    station-window is NaN unless every counted lane has a finite flow in it —
    a lane with no row there is missing too. Nothing is scaled up: a partial
    sum would be read as a low count.

    Args:
        frame: Tidy detector frame (``calibration.loaders.detector_csv``).
        dates: Local dates of the placeholder period (``YYYYMMDD`` or
            ``YYYY-MM-DD``); all when None.
        start: Start of the placeholder period, local ``"HH:MM"``; give it
            with ``end``.
        end: Its end, local ``"HH:MM"`` (``"24:00"`` allowed).
        excluded_lanes: Station → lane ids excluded by name, added to the
            frame's own record.

    Returns:
        Frame with ``station``, ``timestamp``, ``flow_veh_h`` and, when the
        input carries it, ``kind`` (one row per station and timestamp at
        which any of its lanes has a row; every timestamp of the frame, not
        only the period's).

    Raises:
        ValueError: Only one of ``start`` and ``end``; a lane with two rows
            at one timestamp; or excluded detectors the frame does not
            attribute to a station (:func:`calibration.lane_totals.require_attributed`).
    """
    if not is_per_lane(frame):
        cols = [c for c in ("timestamp", "station", "flow_veh_h", "kind") if c in frame.columns]
        return frame[cols].copy()
    if (start is None) != (end is None):
        raise ValueError("station_totals: give start and end together")
    exclusions = frame_exclusions(frame, excluded_lanes)
    require_attributed(exclusions.unattributed, "station_totals")

    in_period = np.ones(len(frame), dtype=bool)
    if dates is not None:
        wanted = sorted({normalize_date(d) for d in dates})
        in_period &= np.isin(local_dates(frame).to_numpy(dtype=object), wanted)
    if start is not None and end is not None:
        lo_s, hi_s = parse_clock_span(start, end)
        secs = local_seconds(frame).to_numpy(dtype=float)
        in_period &= (secs >= lo_s) & (secs < hi_s)
    work = pd.DataFrame(
        {
            "timestamp": frame["timestamp"].to_numpy(dtype=object),
            "station": frame["station"].astype(str).to_numpy(dtype=object),
            "lane": frame["lane"].astype(str).to_numpy(dtype=object),
            "flow": pd.to_numeric(frame["flow_veh_h"], errors="coerce").to_numpy(dtype=float),
            "in_period": in_period,
        }
    )
    has_kind = "kind" in frame.columns
    if has_kind:
        work["kind"] = frame["kind"].to_numpy(dtype=object)
    dup = work.duplicated(subset=["station", "lane", "timestamp"])
    if dup.any():
        twice = work.loc[dup].iloc[0]
        raise ValueError(
            f"station_totals: lane {twice['lane']!r} of station {twice['station']!r} has two "
            f"rows at {twice['timestamp']}"
        )

    parts: list[pd.DataFrame] = []
    for station, group in work.groupby("station", sort=False):
        t_codes, t_values = pd.factorize(group["timestamp"].to_numpy(dtype=object))
        l_codes, l_values = pd.factorize(group["lane"].to_numpy(dtype=object))
        delivered = [str(lane) for lane in l_values]
        flows = np.full((len(delivered), len(t_values)), np.nan)
        flows[l_codes, t_codes] = group["flow"].to_numpy(dtype=float)
        finite = np.isfinite(group["flow"].to_numpy(dtype=float))
        reported = finite & group["in_period"].to_numpy(dtype=bool)
        reporting = {delivered[k] for k in np.unique(l_codes[reported])}
        lanes = station_lanes(
            str(station),
            delivered,
            [lane for lane in delivered if lane not in reporting],
            exclusions.by_station.get(str(station), ()),
        )
        total = complete_sum(lanes, {lane: flows[k] for k, lane in enumerate(delivered)})
        part = pd.DataFrame(
            {
                "station": str(station),
                "timestamp": pd.Series(list(t_values), dtype=object),
                "flow_veh_h": total,
            }
        )
        if has_kind:
            kinds = group["kind"].dropna()
            part["kind"] = kinds.iloc[0] if len(kinds) else None
        parts.append(part)
    if not parts:
        empty = ["station", "timestamp", "flow_veh_h", *(["kind"] if has_kind else [])]
        return pd.DataFrame(columns=empty)
    return pd.concat(parts, ignore_index=True)


def _quality_usable(
    quality: Mapping[str, Any] | QualityVerdicts, stations: Sequence[str]
) -> tuple[dict[tuple[str, str], bool], set[str], dict[str, Any]]:
    """Usable station-days, the dates covered, and the report's identity.

    Args:
        quality: A ``flowstate.data_quality/1`` payload or its verdicts.
        stations: The selected stations.

    Returns:
        ``(usable, covered_dates, meta)``: ``(station, date)`` → usable for
        every station-day the report judged; the dates it judged; and its
        schema, span and dates as recorded on the split.
    """
    verdicts = (
        quality if isinstance(quality, QualityVerdicts) else QualityVerdicts.from_dict(quality)
    )
    judged = {
        key: not day.excluded
        for key, day in verdicts.station_days(kind=_MAINLINE, stations=set(stations)).items()
    }
    grid = {} if isinstance(quality, QualityVerdicts) else dict(quality.get("grid") or {})
    start, end = verdicts.span_local
    meta = {
        "schema": "flowstate.data_quality/1"
        if isinstance(quality, QualityVerdicts)
        else quality.get("schema"),
        "start_local": grid.get("start_local", start),
        "end_local": grid.get("end_local", end),
        "dates": list(grid.get("dates") or verdicts.dates),
    }
    if verdicts.path is not None:
        meta["path"] = verdicts.path
    if verdicts.sha256 is not None:
        meta["sha256"] = verdicts.sha256
    return judged, set(verdicts.dates), meta


def calendar_reasons(
    day: str, holidays: Mapping[str, str], exclusions: Mapping[str, str]
) -> list[str]:
    """Why a date is not a candidate on the calendar alone (§3.1, first three rules).

    Weekday (Tuesday–Thursday), United States federal holidays and the
    caller's exclusion list; the usable-station rule is applied separately
    (it depends on the selected stations).

    Args:
        day: ``YYYY-MM-DD``.
        holidays: Date → holiday name (:func:`us_federal_holidays`).
        exclusions: Date → reason (agency logs).

    Returns:
        The reasons, empty for a calendar candidate.
    """
    weekday = date.fromisoformat(day).weekday()
    reasons: list[str] = []
    if weekday not in CANDIDATE_WEEKDAYS:
        reasons.append(f"weekday {WEEKDAY_NAMES[weekday]} is not a candidate weekday")
    if day in holidays:
        reasons.append(f"federal holiday: {holidays[day]}")
    if day in exclusions:
        reasons.append(f"excluded by the caller: {exclusions[day]}")
    return reasons


def holidays_for(days: Iterable[str]) -> dict[str, str]:
    """Federal holidays of every year the dates touch."""
    out: dict[str, str] = {}
    for year in sorted({int(d[:4]) for d in days}):
        out.update(us_federal_holidays(year))
    return out


def _allocate_calibration(
    sizes: Sequence[int], share: Fraction, tie_order: Sequence[int]
) -> list[int]:
    """Calibration days per tercile (§3.2 as amended 2026-10-04).

    ``total = floor(share × Σ sizes)``; a tercile's quota is ``size × total /
    Σ sizes``; each gets ``floor(quota)``, raised to
    :data:`MIN_CALIBRATION_PER_STRATUM` for a non-empty tercile when ``total``
    is at least the number of non-empty terciles; the days left go one each to
    the largest fractional remainders, ties in ``tie_order`` (a seeded
    permutation of the tercile indices).

    Args:
        sizes: Days per tercile, low to high.
        share: The calibration share as an exact fraction.
        tie_order: Tercile indices in tie-breaking order.

    Returns:
        Calibration days per tercile; never above a tercile's size, summing to
        ``total``.
    """
    n_total = sum(sizes)
    total = n_total * share.numerator // share.denominator
    if n_total == 0 or total == 0:
        return [0 for _ in sizes]
    quotas = [Fraction(n * total, n_total) for n in sizes]
    alloc = [int(q) for q in quotas]  # floor: quotas are non-negative
    non_empty = [k for k, n in enumerate(sizes) if n > 0]
    if total >= len(non_empty):
        for k in non_empty:
            alloc[k] = max(alloc[k], MIN_CALIBRATION_PER_STRATUM)
    rank = {int(k): i for i, k in enumerate(tie_order)}
    order = sorted(
        (k for k in range(len(sizes)) if alloc[k] < sizes[k]),
        key=lambda k: (-(quotas[k] - int(quotas[k])), rank[k]),
    )
    remaining = total - sum(alloc)
    for k in order:
        if remaining <= 0:
            break
        alloc[k] += 1
        remaining -= 1
    if sum(alloc) != total or any(a > n for a, n in zip(alloc, sizes, strict=True)):
        raise AssertionError(f"allocation {alloc} of {total} over terciles {list(sizes)}")
    return alloc


def build_day_split(
    frame: pd.DataFrame,
    *,
    start: str,
    end: str,
    stations: Sequence[str] | None = None,
    dates: Sequence[str] | None = None,
    quality: Mapping[str, Any] | QualityVerdicts | None = None,
    exclusions: Mapping[str, str] | None = None,
    seed: int = SPLIT_SEED,
    provenance: Mapping[str, Any] | None = None,
    allow_uncovered_dates: bool = False,
) -> DaySplit:
    """Screen the dates and draw the calibration / validation split.

    Args:
        frame: Tidy detector frame (station or per-lane rows).
        start: Study period start, local ``"HH:MM"``.
        end: Study period end, local ``"HH:MM"`` (``"24:00"`` allowed).
        stations: The selected mainline stations; default every mainline
            station of the frame (every station when it has no ``kind``).
        dates: Dates to examine (``YYYYMMDD`` or ``YYYY-MM-DD``); default
            every local date of the frame.
        quality: A ``flowstate.data_quality/1`` payload (the JSON of
            ``scripts/data_quality_report.py``, judged over the study
            period), its :class:`calibration.data_quality.QualityVerdicts`,
            or None (module docstring, "Usable station-day").
        exclusions: Date → reason for state/local holidays, incidents and
            weather events (agency logs).
        seed: Seed of the draw. The protocol fixes :data:`SPLIT_SEED`; the
            parameter exists for tests.
        provenance: Recorded verbatim on the split.
        allow_uncovered_dates: With ``quality``, leave a date the report does
            not cover out of the split with the reason
            :data:`NOT_COVERED_REASON` instead of refusing.

    Returns:
        The :class:`DaySplit`.

    Raises:
        ValueError: The frame lacks a column, a requested station or date is
            absent, the study period is malformed, or (without
            ``allow_uncovered_dates``) an examined date is not covered by
            ``quality``.
    """
    for col in ("timestamp", "station", "flow_veh_h"):
        if col not in frame.columns:
            raise ValueError(f"detector frame is missing column {col!r}")
    lo_s, hi_s = parse_clock_span(start, end)
    all_dates = sorted({normalize_date(d) for d in local_dates(frame)})
    if dates is None:
        examined = all_dates
    else:
        examined = sorted({normalize_date(d) for d in dates})
        absent = sorted(set(examined) - set(all_dates))
        if absent:
            raise ValueError(f"date(s) {absent} have no rows in the detector frame")
    # Per lane, a lane is a placeholder when it reports nothing in the study
    # period on any examined date: the data-quality report's rule (its dates
    # and span are the study's).
    totals = station_totals(frame, dates=examined, start=start, end=end)
    interval_s = detector_interval_s(totals)
    if interval_s <= 0.0:
        raise ValueError("the detector frame holds a single timestamp; no window grid")
    n_expected = round((hi_s - lo_s) / interval_s)
    period_h = (hi_s - lo_s) / _S_PER_H

    if stations is None:
        if "kind" in totals.columns:
            pool = totals.loc[totals["kind"].fillna(_MAINLINE) == _MAINLINE, "station"]
        else:
            pool = totals["station"]
        selected = tuple(sorted({str(s) for s in pool}))
    else:
        selected = tuple(str(s) for s in stations)
        missing = sorted(set(selected) - {str(s) for s in totals["station"]})
        if missing:
            raise ValueError(f"selected station(s) {missing} are not in the detector frame")
    if not selected:
        raise ValueError("no selected stations")

    work = totals.loc[totals["station"].astype(str).isin(selected)].copy()
    work["station"] = work["station"].astype(str)
    work["_date"] = local_dates(work)
    work["_secs"] = local_seconds(work)
    work = work.loc[(work["_secs"] >= lo_s) & (work["_secs"] < hi_s)]

    excluded_days = {normalize_date(k): str(v) for k, v in (exclusions or {}).items()}
    notes: list[str] = []
    if not excluded_days:
        notes.append(
            "no exclusion list was supplied: state and local holidays, incidents and weather "
            "events were not screened (federal holidays were)"
        )

    usable_judged: dict[tuple[str, str], bool] | None = None
    covered: set[str] = set()
    usability_source = "completeness"
    quality_meta: dict[str, Any] = {}
    if quality is not None:
        usable_judged, covered, quality_meta = _quality_usable(quality, selected)
        usability_source = "data_quality"
        span = (quality_meta.get("start_local"), quality_meta.get("end_local"))
        if span != (start, end):
            notes.append(
                f"the data-quality report was judged over {span[0]}-{span[1]}, not the study "
                f"period {start}-{end}"
            )
        uncovered = [d for d in examined if d not in covered]
        if uncovered and not allow_uncovered_dates:
            raise ValueError(
                f"date(s) {uncovered} are not covered by the data-quality report (it judged "
                f"{len(covered)} date(s)); run the check over them, or pass "
                f"allow_uncovered_dates to leave them out as '{NOT_COVERED_REASON}'"
            )
        if uncovered:
            notes.append(
                f"{len(uncovered)} date(s) left out as {NOT_COVERED_REASON}: "
                + ", ".join(uncovered)
            )

    holidays = holidays_for(examined)

    by_station_day = {key: rows for key, rows in work.groupby(["station", "_date"], sort=False)}
    records: list[DayRecord] = []
    for day in examined:
        weekday = date.fromisoformat(day).weekday()
        reasons = calendar_reasons(day, holidays, excluded_days)
        if usable_judged is not None and day not in covered:
            reasons.append(NOT_COVERED_REASON)
            records.append(
                DayRecord(
                    date=day,
                    weekday=WEEKDAY_NAMES[weekday],
                    candidate=False,
                    reasons=tuple(reasons),
                    n_selected=len(selected),
                    n_usable=0,
                    usable_share=math.nan,
                    unusable_stations=(),
                    volume_veh=None,
                    n_station_windows=0,
                )
            )
            continue
        usable: list[str] = []
        unusable: list[str] = []
        station_volumes: list[float] = []
        n_windows_used = 0
        for station in selected:
            rows = by_station_day.get((station, day))
            flows = (
                np.asarray(rows["flow_veh_h"].to_numpy(dtype=np.float64))
                if rows is not None
                else np.zeros(0)
            )
            finite = flows[np.isfinite(flows)]
            if usable_judged is not None:
                ok = usable_judged.get((station, day), False)
            else:
                ok = n_expected > 0 and finite.size / n_expected >= MIN_USABLE_WINDOW_SHARE
            if ok and finite.size > 0:
                usable.append(station)
                station_volumes.append(float(finite.mean()) * period_h)
                n_windows_used += int(finite.size)
            else:
                unusable.append(station)
        share = len(usable) / len(selected)
        if share < MIN_USABLE_STATION_SHARE:
            reasons.append(
                f"{len(usable)} of {len(selected)} selected stations usable "
                f"({share:.0%} < {MIN_USABLE_STATION_SHARE:.0%})"
            )
        volume = float(np.mean(station_volumes)) if station_volumes else None
        records.append(
            DayRecord(
                date=day,
                weekday=WEEKDAY_NAMES[weekday],
                candidate=not reasons,
                reasons=tuple(reasons),
                n_selected=len(selected),
                n_usable=len(usable),
                usable_share=share,
                unusable_stations=tuple(unusable),
                volume_veh=volume,
                n_station_windows=n_windows_used,
            )
        )

    candidates = [r for r in records if r.candidate]
    ranked = sorted(candidates, key=lambda r: (float(r.volume_veh or 0.0), r.date))
    pieces = np.array_split(np.arange(len(ranked)), N_STRATA)
    share_fraction = Fraction(CALIBRATION_SHARE).limit_denominator(1000)
    rng = np.random.default_rng(seed)
    allocation = _allocate_calibration(
        [len(piece) for piece in pieces], share_fraction, rng.permutation(N_STRATA)
    )
    strata: list[Stratum] = []
    calibration: list[str] = []
    validation: list[str] = []
    for label, piece, n_alloc in zip(STRATUM_LABELS, pieces, allocation, strict=True):
        members = [ranked[int(i)] for i in piece]
        in_order = sorted(m.date for m in members)
        n_days = len(in_order)
        n_cal = n_alloc
        if n_cal == 0:
            drawn: tuple[int, ...] = ()
        else:
            picked = rng.choice(n_days, size=n_cal, replace=False)
            drawn = tuple(sorted(int(p) for p in picked))
        cal = tuple(in_order[p] for p in drawn)
        val = tuple(d for d in in_order if d not in cal)
        volumes = [float(m.volume_veh) for m in members if m.volume_veh is not None]
        strata.append(
            Stratum(
                label=label,
                dates=tuple(in_order),
                volume_min_veh=min(volumes) if volumes else None,
                volume_max_veh=max(volumes) if volumes else None,
                n_calibration=n_cal,
                drawn_positions=drawn,
                calibration_dates=cal,
                validation_dates=val,
            )
        )
        calibration.extend(cal)
        validation.extend(val)

    shortfalls: list[str] = []
    if len(calibration) < MIN_CALIBRATION_DAYS:
        shortfalls.append(
            f"{len(calibration)} calibration day(s), fewer than {MIN_CALIBRATION_DAYS}"
        )
    if len(validation) < MIN_VALIDATION_DAYS:
        shortfalls.append(f"{len(validation)} validation day(s), fewer than {MIN_VALIDATION_DAYS}")
    reason = (
        "; ".join(shortfalls)
        + " (docs/FRISCO_PROTOCOL.md section 3.3: the study proceeds and states that its "
        "validation is underpowered)"
        if shortfalls
        else ""
    )
    prov = dict(provenance or {})
    if quality_meta:
        prov.setdefault("data_quality", quality_meta)
    prov.setdefault("interval_s", interval_s)
    return DaySplit(
        study_period=(start, end),
        seed=int(seed),
        selected_stations=selected,
        usability_source=usability_source,
        days=tuple(records),
        strata=tuple(strata),
        calibration_dates=tuple(sorted(calibration)),
        validation_dates=tuple(sorted(validation)),
        underpowered=bool(shortfalls),
        underpowered_reason=reason,
        exclusions=excluded_days,
        notes=tuple(notes),
        provenance=prov,
    )


def read_exclusions(paths: Iterable[str | Path] = (), items: Iterable[str] = ()) -> dict[str, str]:
    """Exclusion list from files and ``DATE=REASON`` items.

    A file is a JSON object (date → reason) or a CSV with ``date`` and
    ``reason`` columns.

    Raises:
        ValueError: An item without ``=`` or a reason, or a file in neither form.
    """
    out: dict[str, str] = {}
    for path in paths:
        p = Path(path)
        if p.suffix.lower() == ".json":
            raw = json.loads(p.read_text())
            if not isinstance(raw, dict):
                raise ValueError(f"{p}: expected a JSON object of date -> reason")
            for k, v in raw.items():
                out[normalize_date(str(k))] = str(v)
        else:
            table = pd.read_csv(p)
            if not {"date", "reason"} <= set(table.columns):
                raise ValueError(f"{p}: expected columns 'date' and 'reason'")
            for _, row in table.iterrows():
                out[normalize_date(str(row["date"]))] = str(row["reason"])
    for item in items:
        day, sep, why = str(item).partition("=")
        if not sep or not why.strip():
            raise ValueError(f"exclusion {item!r} must read DATE=REASON")
        out[normalize_date(day)] = why.strip()
    return out
