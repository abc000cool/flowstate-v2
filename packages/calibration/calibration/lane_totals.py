"""Station totals from per-lane detector data: one rule set for every module.

A per-lane detector frame (``calibration.loaders.detector_csv`` rows with a
``lane`` column, or :func:`calibration.loaders.mndot_lanes.lane_frame`) is
summed to station totals by :func:`calibration.conservation.station_grid`
(the data-quality mass balance, ramp estimation, the transfer check) and by
:func:`calibration.day_split.station_totals` (the day split's usability and
volume). Both take their rules from this module, and the data-quality
report's station membership (``QualityVerdicts.station_sensors``) applies the
same placeholder rule (:func:`counted_lanes`). The contract is docs/CONTRACTS.md
(WP-101): "Per-lane frames are summed to station totals only where every
installed lane reported; lanes that never report anywhere in the data are not
installed lanes. Nothing is scaled up and nothing is imputed."

**1. The lane set.** A station's lanes are the lanes the data deliver for it
plus the lanes *excluded by name*: installed detectors a reviewer set aside
(``--exclude-detectors``), which the loader does not read at all. The loader
records them per station under ``frame.attrs["excluded_lanes"]``
(:data:`EXCLUDED_LANES_ATTR`, built by :func:`attribute_exclusions`), or the
caller passes them.

**2. Placeholders leave the set.** A delivered lane with no finite flow
anywhere in the period examined (an inventory placeholder such as MnDOT's
``T…`` loops, or a loop that never communicated) is not an installed lane and
is not counted, unless every delivered lane of the station is one and no lane
of it was excluded: the station then keeps them all and has no total. An
excluded lane is never a placeholder: it was set aside, not absent. *Known
limit:* per-lane data cannot tell a placeholder from a real lane whose loop is
dead for the whole period, so such a station sums its other lanes; every such
lane is named in the callers' notes
(:func:`calibration.conservation.silent_lane_note`).

**3. A window is a station total only when every counted lane reported it.**
A counted lane that is missing in the window (no row), NaN there (not
measured, or set aside by a quality verdict) or excluded by name makes that
window's total NaN. Nothing is scaled up and nothing is imputed. This differs
on purpose from :func:`calibration.loaders.mndot.station_frame`, whose station
totals scale a station with a dead lane by ``lanes / lanes_reporting`` and
record it under ``attrs["scaled_station_days"]``: a per-lane total is either
complete or absent, so a partial sum is never read as a low count.

**Exclusions without a station.** A per-lane frame whose
``attrs["excluded_detectors"]`` names a detector that is neither a lane of the
frame nor attributed to a station under ``attrs["excluded_lanes"]`` cannot be
summed: some station lost a lane and nothing says which. :func:`frame_exclusions`
reports such names and :func:`require_attributed` refuses them
(``ValueError``) rather than let a partial sum through.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import pandas as pd

EXCLUDED_LANES_ATTR: Final[str] = "excluded_lanes"
"""``frame.attrs`` key: station → lane ids excluded by name (installed, not read)."""

EXCLUDED_DETECTORS_ATTR: Final[str] = "excluded_detectors"
"""``frame.attrs`` key the loaders set: the detector names excluded by name."""


def is_per_lane(frame: pd.DataFrame) -> bool:
    """True when a tidy detector frame holds per-lane rows (a ``lane`` column with values)."""
    return "lane" in frame.columns and bool(frame["lane"].notna().any())


def attribute_exclusions(
    lanes_by_station: Mapping[str, Iterable[str]], excluded: Iterable[str]
) -> dict[str, list[str]]:
    """Station → its excluded lanes, the value of ``attrs["excluded_lanes"]``.

    Args:
        lanes_by_station: Every station's (or ramp node's) lane ids as
            installed, before exclusion (MnDOT: its detector names).
        excluded: The lane ids excluded by name.

    Returns:
        Station → sorted excluded lane ids; stations with none are left out.
    """
    gone = {str(e) for e in excluded}
    out: dict[str, list[str]] = {}
    for station, lanes in lanes_by_station.items():
        hit = sorted({str(lane) for lane in lanes} & gone)
        if hit:
            out[str(station)] = hit
    return out


@dataclass(frozen=True)
class FrameExclusions:
    """The lanes a per-lane frame excludes by name (module docstring).

    Attributes:
        by_station: Station → excluded lane ids (sorted).
        unattributed: Excluded detector names no station can be found for.
    """

    by_station: dict[str, tuple[str, ...]]
    unattributed: tuple[str, ...] = ()


def _mapping_of(value: Any, origin: str) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{origin} must map station -> lane ids, got {type(value).__name__}")
    out: dict[str, tuple[str, ...]] = {}
    for station, lanes in value.items():
        items = [lanes] if isinstance(lanes, str) else list(lanes)
        out[str(station)] = tuple(sorted({str(lane) for lane in items}))
    return out


def frame_exclusions(
    frame: pd.DataFrame, extra: Mapping[str, Iterable[str]] | None = None
) -> FrameExclusions:
    """The lanes a per-lane frame excludes by name, by station.

    Sources, merged: ``attrs["excluded_lanes"]``, ``extra``, and every name in
    ``attrs["excluded_detectors"]`` that is a lane id of the frame (attributed
    to each station it is a lane of). A station frame has none.

    Args:
        frame: Tidy detector frame.
        extra: Station → lane ids excluded, from the caller.

    Returns:
        The :class:`FrameExclusions`.

    Raises:
        ValueError: ``attrs["excluded_lanes"]`` or ``extra`` is not a mapping.
    """
    if not is_per_lane(frame):
        return FrameExclusions(by_station={})
    merged: dict[str, set[str]] = {}
    for source, origin in (
        (frame.attrs.get(EXCLUDED_LANES_ATTR), f'attrs["{EXCLUDED_LANES_ATTR}"]'),
        (extra, "excluded_lanes"),
    ):
        for station, lanes in _mapping_of(source, origin).items():
            merged.setdefault(station, set()).update(lanes)
    named = frame.attrs.get(EXCLUDED_DETECTORS_ATTR) or ()
    names = [named] if isinstance(named, str) else [str(n) for n in named]
    attributed = {lane for lanes in merged.values() for lane in lanes}
    pending = sorted({n for n in names if n not in attributed})
    unattributed: list[str] = []
    if pending:
        pairs = frame.loc[frame["lane"].astype(str).isin(pending), ["station", "lane"]]
        found: dict[str, set[str]] = {}
        for station, lane in zip(
            pairs["station"].astype(str), pairs["lane"].astype(str), strict=True
        ):
            found.setdefault(lane, set()).add(station)
        for name in pending:
            if name in found:
                for station in found[name]:
                    merged.setdefault(station, set()).add(name)
            else:
                unattributed.append(name)
    return FrameExclusions(
        by_station={st: tuple(sorted(lanes)) for st, lanes in sorted(merged.items()) if lanes},
        unattributed=tuple(unattributed),
    )


def require_attributed(unattributed: Collection[str], where: str) -> None:
    """Refuse station totals when an excluded detector has no station (module docstring).

    Raises:
        ValueError: ``unattributed`` is not empty.
    """
    if unattributed:
        raise ValueError(
            f"{where}: detector(s) {sorted(unattributed)} are excluded by name "
            f'(attrs["{EXCLUDED_DETECTORS_ATTR}"]) but the per-lane frame does not say which '
            f"station each served, so the station that lost a lane cannot be told and its total "
            f"would be a partial sum; record them per station under "
            f'attrs["{EXCLUDED_LANES_ATTR}"] (calibration.lane_totals.attribute_exclusions) or '
            f"pass excluded_lanes"
        )


@dataclass(frozen=True)
class StationLanes:
    """One station's lane set (module docstring, rules 1 and 2).

    Attributes:
        station: Station id.
        counted: The lanes a total needs, delivered first (in input order),
            then the excluded ones.
        placeholders: Delivered lanes left out: no finite flow in the period.
        excluded: Lanes excluded by name; counted, missing in every window.
    """

    station: str
    counted: tuple[str, ...]
    placeholders: tuple[str, ...]
    excluded: tuple[str, ...]

    @property
    def n_lanes(self) -> int:
        """Lanes counted."""
        return len(self.counted)

    @property
    def measured(self) -> tuple[str, ...]:
        """Counted lanes that carry readings (the counted, less the excluded)."""
        return tuple(lane for lane in self.counted if lane not in self.excluded)


def counted_lanes(delivered: Sequence[str], placeholders: Collection[str]) -> tuple[str, ...]:
    """Rule 2 alone: the delivered lanes that are not placeholders, or all when every one is.

    This is the data-quality report's station membership
    (``QualityVerdicts.station_sensors``), which has no exclusions to add.
    """
    kept = tuple(lane for lane in delivered if lane not in placeholders)
    return kept or tuple(delivered)


def station_lanes(
    station: str,
    delivered: Sequence[str],
    placeholders: Collection[str],
    excluded: Iterable[str] = (),
) -> StationLanes:
    """A station's lane set (module docstring, rules 1 and 2).

    Args:
        station: Station id.
        delivered: Lanes the data hold rows for.
        placeholders: Delivered lanes with no finite flow in the period.
        excluded: Lanes excluded by name (whether or not the data hold them).

    Returns:
        The :class:`StationLanes`.
    """
    gone = tuple(sorted({str(e) for e in excluded}))
    present = [lane for lane in delivered if lane not in gone]
    real = [lane for lane in present if lane not in placeholders]
    if real or gone:
        counted = (*real, *gone)
        left_out = tuple(lane for lane in present if lane in placeholders)
    else:
        counted = tuple(present)
        left_out = ()
    return StationLanes(station=str(station), counted=counted, placeholders=left_out, excluded=gone)


def lane_stack(lanes: StationLanes, values: Mapping[str, np.ndarray]) -> np.ndarray:
    """The counted lanes' values stacked on axis 0; NaN for an excluded or absent lane.

    The shape is that of the station's arrays in ``values`` (a placeholder's
    serves when every counted lane is excluded).

    Raises:
        ValueError: No lane of the station has values, or their shapes differ.
    """
    shapes = {
        np.shape(values[lane]) for lane in (*lanes.counted, *lanes.placeholders) if lane in values
    }
    if not shapes:
        raise ValueError(f"station {lanes.station!r}: no lane of it carries readings")
    if len(shapes) > 1:
        raise ValueError(f"station {lanes.station!r}: lane arrays differ in shape {sorted(shapes)}")
    shape = shapes.pop()
    rows = [
        np.asarray(values[lane], dtype=float)
        if lane in values and lane not in lanes.excluded
        else np.full(shape, np.nan)
        for lane in lanes.counted
    ]
    return np.stack(rows)


def complete_sum(lanes: StationLanes, values: Mapping[str, np.ndarray]) -> np.ndarray:
    """Rule 3: the sum over the counted lanes where every one is finite, NaN elsewhere."""
    stack = lane_stack(lanes, values)
    return np.where(np.isfinite(stack).all(axis=0), stack.sum(axis=0), np.nan)


def complete_mean(lanes: StationLanes, values: Mapping[str, np.ndarray]) -> np.ndarray:
    """The mean over the counted lanes where every one is finite, NaN elsewhere (occupancy)."""
    stack = lane_stack(lanes, values)
    return np.where(np.isfinite(stack).all(axis=0), stack.mean(axis=0), np.nan)
