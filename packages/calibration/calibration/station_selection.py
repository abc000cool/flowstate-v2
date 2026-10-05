"""Mainline stations used for scoring (docs/FRISCO_PROTOCOL.md §2.2).

The protocol fixes, before the first simulation of a corridor, which mainline
stations its checks are scored at: **every station inside the stretch whose
verdict is not "exclude" on at least 80 % of the candidate days within the
study period** (:data:`MIN_NOT_EXCLUDED_SHARE`, a FlowState rule). The
candidate days are the protocol's §3.1 calendar screen — Tuesday to Thursday,
not a United States federal holiday, not a day the agency's logs name for an
incident or weather event (the caller's exclusion list) — applied *before* the
split and *without* the usable-station rule, which depends on this selection
(the protocol's clarification of 2026-10-04: the first wording used the
calibration days, which are drawn from days judged on the selected stations —
a circle).

**A station's verdict on a day** is read from the corridor's data-quality
report (:mod:`calibration.data_quality`, judged over the study period) exactly
as the day split reads it (:mod:`calibration.day_split`): the station is
excluded that day when any of its mainline sensors (the station itself, or
any installed lane — a lane that reported nothing on any date of the report
is not an installed lane) is judged ``exclude``; a station the report did not
judge that day counts as excluded (nothing vouches for it). Ramps are not
scored stations and are not selected.

The result is written into the corridor's ``selection.json`` under
:data:`SELECTION_KEY` (additively: every key already there is kept), with
every station's share, its excluded days and their checks, the candidate days,
the rule and the report's path and hash, so a reviewer can redo the selection
by hand. ``scripts/day_split.py --selection`` takes the selected stations from
it.

Nothing here looks at simulation output: the selection is decided from the
detector data alone, before any run.
"""

from __future__ import annotations

import json
import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from calibration.conservation import normalize_date
from calibration.data_quality import QualityInput, as_verdicts
from calibration.day_split import (
    CANDIDATE_WEEKDAYS,
    WEEKDAY_NAMES,
    calendar_reasons,
    holidays_for,
)

STATION_SELECTION_SCHEMA: Final[str] = "flowstate.station_selection/1"
"""Schema tag of the selection block."""

SELECTION_KEY: Final[str] = "scoring_stations"
"""The key of the block in a corridor's ``selection.json``."""

MIN_NOT_EXCLUDED_SHARE: Final[float] = 0.80
"""Least share of the candidate days on which a station's verdict is not
"exclude" for it to be scored (docs/FRISCO_PROTOCOL.md §2.2; a FlowState
rule, the same 80 % as the §3.1 usable-station rule)."""

_MAINLINE: Final[str] = "mainline"
_SHARE_TOLERANCE: Final[float] = 1e-12


def selection_rules() -> dict[str, Any]:
    """The rule, as recorded in every selection."""
    return {
        "protocol": "docs/FRISCO_PROTOCOL.md section 2.2 (clarified 2026-10-04)",
        "min_not_excluded_share": MIN_NOT_EXCLUDED_SHARE,
        "candidate_days": "section 3.1 calendar screen before the split: "
        + ", ".join(WEEKDAY_NAMES[d] for d in CANDIDATE_WEEKDAYS)
        + "; not a United States federal holiday (calibration.day_split.us_federal_holidays); "
        "not on the caller's exclusion list (incidents, weather, state and local holidays); "
        "the usable-station rule is not applied (it depends on this selection)",
        "station_verdict": "a station is excluded on a date when any of its mainline sensors "
        "(station, or an installed lane: a lane that reported nothing on any date of the report "
        "is not installed) is judged 'exclude'; a station the report did not judge that date "
        "counts as excluded",
        "stations": "mainline stations of the data-quality report (or the caller's list of "
        "the stretch's stations), in corridor order",
    }


@dataclass(frozen=True)
class StationShare:
    """One station's record.

    Attributes:
        station: Station id.
        x_m: Corridor position [m] (``None`` when the report gives none).
        n_candidate_days: Candidate days.
        n_not_excluded: Candidate days on which its verdict is not "exclude".
        share: ``n_not_excluded / n_candidate_days``.
        selected: The share reaches :data:`MIN_NOT_EXCLUDED_SHARE`.
        reason: The decision in words.
        excluded_days: The candidate days it was excluded: ``{"date",
            "sensors": [{"sensor", "date", "verdict", "checks"}]}``, with
            ``"sensors": []`` and ``"note"`` when the report did not judge it.
    """

    station: str
    x_m: float | None
    n_candidate_days: int
    n_not_excluded: int
    share: float
    selected: bool
    reason: str
    excluded_days: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {
            "station": self.station,
            "x_m": self.x_m,
            "n_candidate_days": self.n_candidate_days,
            "n_not_excluded": self.n_not_excluded,
            "share": round(self.share, 6),
            "selected": self.selected,
            "reason": self.reason,
            "excluded_days": [dict(d) for d in self.excluded_days],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> StationShare:
        """Rebuild from :meth:`to_dict`."""
        x = raw.get("x_m")
        return cls(
            station=str(raw["station"]),
            x_m=None if x is None else float(x),
            n_candidate_days=int(raw["n_candidate_days"]),
            n_not_excluded=int(raw["n_not_excluded"]),
            share=float(raw["share"]),
            selected=bool(raw["selected"]),
            reason=str(raw.get("reason", "")),
            excluded_days=tuple(dict(d) for d in raw.get("excluded_days", ())),
        )


@dataclass(frozen=True)
class StationSelection:
    """The §2.2 selection and everything it rests on (module docstring).

    Attributes:
        stations: The selected stations, in corridor order.
        records: One record per mainline station considered, corridor order.
        candidate_dates: The candidate days.
        not_candidate: Date → why it is not a candidate.
        exclusions: The caller's exclusion list (date → reason).
        study_period: ``(start, end)`` local clock of the report's span.
        quality: The report's path, hash, dates and span.
        notes: Plain statements about what was not checked.
        provenance: Inputs as the caller records them.
        rules: :func:`selection_rules` at the time.
        schema: :data:`STATION_SELECTION_SCHEMA`.
    """

    stations: tuple[str, ...]
    records: tuple[StationShare, ...]
    candidate_dates: tuple[str, ...]
    not_candidate: dict[str, tuple[str, ...]]
    exclusions: dict[str, str]
    study_period: tuple[str | None, str | None]
    quality: dict[str, Any]
    notes: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    rules: dict[str, Any] = field(default_factory=selection_rules)
    schema: str = STATION_SELECTION_SCHEMA

    def record(self, station: str) -> StationShare:
        """One station's record.

        Raises:
            KeyError: The station was not considered.
        """
        for r in self.records:
            if r.station == station:
                return r
        raise KeyError(station)

    def to_dict(self) -> dict[str, Any]:
        """JSON form (fixed keys)."""
        return {
            "schema": self.schema,
            "rules": dict(self.rules),
            "study_period": {"start": self.study_period[0], "end": self.study_period[1]},
            "quality": dict(self.quality),
            "exclusions": dict(sorted(self.exclusions.items())),
            "candidate_dates": list(self.candidate_dates),
            "not_candidate": {d: list(r) for d, r in sorted(self.not_candidate.items())},
            "stations": list(self.stations),
            "n_selected": len(self.stations),
            "n_considered": len(self.records),
            "per_station": [r.to_dict() for r in self.records],
            "notes": list(self.notes),
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> StationSelection:
        """Rebuild from :meth:`to_dict`.

        Raises:
            ValueError: Another schema.
        """
        schema = str(raw.get("schema", ""))
        if schema != STATION_SELECTION_SCHEMA:
            raise ValueError(f"expected schema {STATION_SELECTION_SCHEMA!r}, got {schema!r}")
        period = raw.get("study_period") or {}
        return cls(
            stations=tuple(str(s) for s in raw.get("stations", ())),
            records=tuple(StationShare.from_dict(r) for r in raw.get("per_station", ())),
            candidate_dates=tuple(str(d) for d in raw.get("candidate_dates", ())),
            not_candidate={
                str(d): tuple(str(x) for x in r)
                for d, r in (raw.get("not_candidate") or {}).items()
            },
            exclusions={str(k): str(v) for k, v in (raw.get("exclusions") or {}).items()},
            study_period=(period.get("start"), period.get("end")),
            quality=dict(raw.get("quality") or {}),
            notes=tuple(str(n) for n in raw.get("notes", ())),
            provenance=dict(raw.get("provenance") or {}),
            rules=dict(raw.get("rules") or {}),
            schema=schema,
        )


def select_stations(
    quality: QualityInput,
    *,
    exclusions: Mapping[str, str] | None = None,
    dates: Sequence[str] | None = None,
    stations: Collection[str] | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> StationSelection:
    """Select the scoring stations from a data-quality report (module docstring).

    Args:
        quality: The report, its JSON payload, or its verdicts
            (:meth:`calibration.data_quality.QualityVerdicts.from_json` keeps
            the file's path and hash).
        exclusions: Date → reason for incidents, weather events and state or
            local holidays (agency logs).
        dates: The study period's dates (``YYYYMMDD`` or ``YYYY-MM-DD``);
            default every date the report judged.
        stations: The stretch's stations (default: every mainline station
            the report judged).
        provenance: Recorded verbatim.

    Returns:
        The :class:`StationSelection`.

    Raises:
        ValueError: A requested date or station is not in the report, a
            requested station is not mainline, or no candidate day remains.
    """
    verdicts = as_verdicts(quality)
    judged_dates = set(verdicts.dates)
    if dates is None:
        examined = sorted(judged_dates)
    else:
        examined = sorted({normalize_date(d) for d in dates})
        absent = sorted(set(examined) - judged_dates)
        if absent:
            raise ValueError(f"date(s) {absent} are not covered by the data-quality report")
    excluded_days = {normalize_date(k): str(v) for k, v in (exclusions or {}).items()}
    holidays = holidays_for(examined)
    candidates: list[str] = []
    not_candidate: dict[str, tuple[str, ...]] = {}
    for day in examined:
        reasons = calendar_reasons(day, holidays, excluded_days)
        if reasons:
            not_candidate[day] = tuple(reasons)
        else:
            candidates.append(day)
    if not candidates:
        raise ValueError(
            f"no candidate day among the {len(examined)} date(s) of the data-quality report "
            "(Tuesday-Thursday, not a holiday, not excluded): nothing to select stations on"
        )

    kinds: dict[str, set[str]] = {}
    for sd in verdicts.sensor_days:
        kinds.setdefault(sd.station, set()).add(sd.kind)
    mainline = {st for st, k in kinds.items() if _MAINLINE in k}
    if stations is None:
        universe = sorted(mainline)
    else:
        universe = [str(s) for s in stations]
        unknown = sorted(set(universe) - set(kinds))
        if unknown:
            raise ValueError(f"station(s) {unknown} are not in the data-quality report")
        ramps = sorted(set(universe) - mainline)
        if ramps:
            raise ValueError(f"station(s) {ramps} are not mainline stations; only those are scored")

    def order(st: str) -> tuple[float, str]:
        x = verdicts.positions.get(st)
        return (math.inf if x is None else float(x), st)

    universe = sorted(set(universe), key=order)
    days = verdicts.station_days(kind=_MAINLINE, stations=set(universe))
    records: list[StationShare] = []
    for st in universe:
        not_excluded = 0
        excluded: list[dict[str, Any]] = []
        for day in candidates:
            station_day = days.get((st, day))
            if station_day is None or not station_day.judged:
                excluded.append(
                    {"date": day, "sensors": [], "note": "no verdict for the station that day"}
                )
            elif station_day.excluded:
                excluded.append({"date": day, "sensors": station_day.exclusions()})
            else:
                not_excluded += 1
        n = len(candidates)
        share = not_excluded / n
        selected = share + _SHARE_TOLERANCE >= MIN_NOT_EXCLUDED_SHARE
        relation = ">=" if selected else "<"
        reason = (
            f"not excluded on {not_excluded} of {n} candidate day(s) ({share:.0%} {relation} "
            f"{MIN_NOT_EXCLUDED_SHARE:.0%}): " + ("scored" if selected else "not scored")
        )
        records.append(
            StationShare(
                station=st,
                x_m=verdicts.positions.get(st),
                n_candidate_days=n,
                n_not_excluded=not_excluded,
                share=share,
                selected=selected,
                reason=reason,
                excluded_days=tuple(excluded),
            )
        )
    notes: list[str] = []
    if not excluded_days:
        notes.append(
            "no exclusion list was supplied: state and local holidays, incidents and weather "
            "events were not screened (federal holidays were)"
        )
    silent = sorted(verdicts.silent_lanes())
    if silent:
        notes.append(
            "lane sensors that reported nothing on any date of the report are not installed "
            "lanes and do not exclude their station: " + ", ".join(silent)
        )
    start, end = verdicts.span_local
    return StationSelection(
        stations=tuple(r.station for r in records if r.selected),
        records=tuple(records),
        candidate_dates=tuple(candidates),
        not_candidate=not_candidate,
        exclusions=excluded_days,
        study_period=(start, end),
        quality={
            "path": verdicts.path,
            "sha256": verdicts.sha256,
            "dates": list(verdicts.dates),
            "start_local": start,
            "end_local": end,
            "per_lane": verdicts.per_lane,
        },
        notes=tuple(notes),
        provenance=dict(provenance or {}),
    )


def selection_document(
    selection: StationSelection,
    base: Mapping[str, Any] | None = None,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """A ``selection.json`` payload: ``base``'s keys kept, the block added.

    Args:
        selection: The selection.
        base: The corridor's existing ``selection.json`` (route, span, dates
            of the fetch …), or None.
        replace: Overwrite a block ``base`` already carries.

    Returns:
        The payload.

    Raises:
        ValueError: ``base`` already holds a selection and ``replace`` is
            false (the protocol commits the list before the first simulation;
            a later change is a dated amendment).
    """
    out = dict(base or {})
    if SELECTION_KEY in out and not replace:
        raise ValueError(
            f"selection.json already holds '{SELECTION_KEY}' (committed before the first "
            "simulation, docs/FRISCO_PROTOCOL.md section 2.2); a change is a dated amendment: "
            "pass replace=True only for that"
        )
    out[SELECTION_KEY] = selection.to_dict()
    return out


def read_selection(source: str | Path | Mapping[str, Any]) -> StationSelection:
    """The selection block of a ``selection.json`` (path or payload).

    Raises:
        ValueError: The payload holds no selection block.
    """
    raw = json.loads(Path(source).read_text()) if isinstance(source, str | Path) else source
    block = raw.get(SELECTION_KEY)
    if not isinstance(block, Mapping):
        raise ValueError(
            f"no '{SELECTION_KEY}' block in the selection: run scripts/station_selection.py "
            "on the corridor's data-quality report first"
        )
    return StationSelection.from_dict(block)
