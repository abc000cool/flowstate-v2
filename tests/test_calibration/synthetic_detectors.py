"""Synthetic detector corridors for the WP-101 tests (no real data is ever read).

Layout (positions in m)::

    A (0) ── R0 off (300, optional) ── R1 on (500) ── B (1000) ── R2 off (1600) ── C (2000)

Two generators:

* :func:`daily_frame` — a realistic-looking day (AM and PM peaks, Poisson
  counts, noisy speeds and occupancies consistent with a 6.5 m effective
  length), conserving vehicles exactly between stations with no travel-time
  lag. Used to plant faults for the data-quality checks.
* :func:`linear_frame` — flows linear in time and a constant speed, built
  *with* the travel-time lag, so that the lag-aligned conservation of
  ``calibration.conservation`` recovers a ramp exactly (a window mean of a
  linear function is its value at the window centre, and linear interpolation
  of a linear function is exact).

Loaded by path from the test modules (pytest runs with ``--import-mode=importlib``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

TZ = timezone(timedelta(hours=-5))
DATES: tuple[str, ...] = ("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04")
EFFECTIVE_LENGTH_M = 6.5
LANE_SHARES: tuple[float, ...] = (0.30, 0.33, 0.37)

STATIONS: tuple[dict[str, Any], ...] = (
    {"station": "A", "label": "A", "x_m": 0.0, "lanes": 3, "kind": "mainline"},
    {"station": "R1", "label": "R1 entrance", "x_m": 500.0, "lanes": 1, "kind": "on_ramp"},
    {"station": "B", "label": "B", "x_m": 1000.0, "lanes": 3, "kind": "mainline"},
    {"station": "R2", "label": "R2 exit", "x_m": 1600.0, "lanes": 1, "kind": "off_ramp"},
    {"station": "C", "label": "C", "x_m": 2000.0, "lanes": 3, "kind": "mainline"},
)
R0_ROW: dict[str, Any] = {
    "station": "R0",
    "label": "R0 exit",
    "x_m": 300.0,
    "lanes": 1,
    "kind": "off_ramp",
}


def stations_table(with_r0: bool = False) -> pd.DataFrame:
    """The stations table (positions, kinds, lanes)."""
    rows = [dict(r) for r in STATIONS]
    if with_r0:
        rows.insert(1, dict(R0_ROW))
    return pd.DataFrame(rows)


def _bump(t: np.ndarray, centre_h: float, width_h: float) -> np.ndarray:
    return np.exp(-(((t - centre_h * 3600.0) / (width_h * 3600.0)) ** 2))


def mainline_profile(t: np.ndarray) -> np.ndarray:
    """Station A flow [veh/h] at local seconds ``t``."""
    return (
        500.0
        + 2600.0 * _bump(t, 8.0, 1.5)
        + 2200.0 * _bump(t, 17.0, 2.0)
        + 400.0 * _bump(t, 12.5, 3.0)
    )


def entrance_profile(t: np.ndarray) -> np.ndarray:
    """R1 flow [veh/h]."""
    return (
        40.0 + 500.0 * _bump(t, 8.0, 1.5) + 200.0 * _bump(t, 17.0, 2.0) + 60.0 * _bump(t, 12.5, 3.0)
    )


EXIT_SHARE = 0.12
"""R2 takes this share of B's vehicles."""


def _occupancy(q_lane: np.ndarray, speed: np.ndarray, noise: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        occ = q_lane / 3600.0 * EFFECTIVE_LENGTH_M / speed * 100.0 * (1.0 + noise)
    return np.where(q_lane > 0.0, occ, 0.0)


def daily_frame(
    *,
    seed: int = 0,
    dates: Sequence[str] = DATES,
    interval_s: float = 300.0,
    per_lane: bool = False,
) -> pd.DataFrame:
    """A tidy detector frame of the five-station corridor (module docstring).

    Args:
        seed: RNG seed.
        dates: Local dates.
        interval_s: Window [s].
        per_lane: One row per lane (``lane`` column), mainline split into
            three lanes by :data:`LANE_SHARES`.

    Returns:
        The frame, ``attrs["interval_s"]`` set.
    """
    rng = np.random.default_rng(seed)
    n = round(86400.0 / interval_s)
    centres = (np.arange(n) + 0.5) * interval_s
    to_veh = interval_s / 3600.0
    rows: list[dict[str, Any]] = []
    for date in dates:
        midnight = datetime.fromisoformat(date).replace(tzinfo=TZ)
        counts: dict[str, np.ndarray] = {}
        counts["A"] = rng.poisson(mainline_profile(centres) * to_veh)
        counts["R1"] = rng.poisson(entrance_profile(centres) * to_veh)
        counts["B"] = counts["A"] + counts["R1"]
        counts["R2"] = rng.binomial(counts["B"], EXIT_SHARE)
        counts["C"] = counts["B"] - counts["R2"]
        peak = _bump(centres, 8.0, 1.0) + _bump(centres, 17.0, 1.2)
        for spec in STATIONS:
            sid = spec["station"]
            speed = 29.0 - 4.0 * peak + rng.normal(0.0, 0.6, n)
            if per_lane and spec["kind"] == "mainline":
                lane_counts = np.stack(
                    [rng.multinomial(int(c), LANE_SHARES) for c in counts[sid]]
                ).T
                parts = [(str(i + 1), lane_counts[i], 1) for i in range(len(LANE_SHARES))]
            else:
                parts = [("1" if per_lane else None, counts[sid], int(spec["lanes"]))]
            for lane, c, lanes in parts:
                q = c / to_veh
                v = np.where(c > 0, speed + rng.normal(0.0, 0.3, n), np.nan)
                occ = _occupancy(q / lanes, v, rng.normal(0.0, 0.03, n))
                for k in range(n):
                    row = {
                        "timestamp": midnight + timedelta(seconds=k * interval_s),
                        "station": sid,
                        "flow_veh_h": float(q[k]),
                        "occupancy_pct": float(occ[k]),
                        "speed_ms": float(v[k]),
                        "lanes": 1 if lane is not None else lanes,
                        "kind": spec["kind"],
                        "x_m": spec["x_m"],
                    }
                    if lane is not None:
                        row["lane"] = lane
                    rows.append(row)
    frame = pd.DataFrame(rows)
    frame.attrs["interval_s"] = float(interval_s)
    return frame


Linear = Callable[[np.ndarray], np.ndarray]


def linear_frame(
    *,
    dates: Sequence[str] = DATES[:2],
    interval_s: float = 300.0,
    speed_ms: float = 25.0,
    with_r0: bool = False,
    r0_share: float = 0.08,
    r1_slope: float = 0.004,
) -> pd.DataFrame:
    """Lag-consistent linear flows at a constant speed (module docstring).

    ``A(t) = 1500 + 0.02 t``, ``R1(t) = 200 + r1_slope·t``,
    ``R2(t) = 150 + 0.003 t`` (veh/h, ``t`` in s after midnight) and, when
    ``with_r0``, ``R0(t) = r0_share · A(t − 300/v)``; B and C follow by
    conservation with the travel-time lag.
    """
    n = round(86400.0 / interval_s)
    c = (np.arange(n) + 0.5) * interval_s
    v = speed_ms

    def qa(t: np.ndarray) -> np.ndarray:
        return 1500.0 + 0.02 * t

    def qr1(t: np.ndarray) -> np.ndarray:
        return 200.0 + r1_slope * t

    def qr2(t: np.ndarray) -> np.ndarray:
        return 150.0 + 0.003 * t

    def qr0(t: np.ndarray) -> np.ndarray:
        return r0_share * qa(t - 300.0 / v) if with_r0 else np.zeros_like(t)

    def qb(t: np.ndarray) -> np.ndarray:
        return qa(t - 1000.0 / v) - qr0(t - 700.0 / v) + qr1(t - 500.0 / v)

    def qc(t: np.ndarray) -> np.ndarray:
        return qb(t - 1000.0 / v) - qr2(t - 400.0 / v)

    series: dict[str, Linear] = {"A": qa, "R1": qr1, "B": qb, "R2": qr2, "C": qc}
    specs = list(STATIONS)
    if with_r0:
        series["R0"] = qr0
        specs.insert(1, R0_ROW)
    rows: list[dict[str, Any]] = []
    for date in dates:
        midnight = datetime.fromisoformat(date).replace(tzinfo=TZ)
        for spec in specs:
            q = series[spec["station"]](c)
            lanes = int(spec["lanes"])
            occ = q / lanes / 3600.0 * EFFECTIVE_LENGTH_M / v * 100.0
            for k in range(n):
                rows.append(
                    {
                        "timestamp": midnight + timedelta(seconds=k * interval_s),
                        "station": spec["station"],
                        "flow_veh_h": float(q[k]),
                        "occupancy_pct": float(occ[k]),
                        "speed_ms": v,
                        "lanes": lanes,
                        "kind": spec["kind"],
                        "x_m": spec["x_m"],
                    }
                )
    frame = pd.DataFrame(rows)
    frame.attrs["interval_s"] = float(interval_s)
    return frame


def truth_period_means(
    which: str,
    *,
    interval_s: float = 300.0,
    period_s: float = 900.0,
    speed_ms: float = 25.0,
    r0_share: float = 0.08,
    r1_slope: float = 0.004,
) -> np.ndarray:
    """Period means of a ramp's linear flow on its own clock (one day)."""
    n = round(86400.0 / interval_s)
    c = (np.arange(n) + 0.5) * interval_s
    if which == "R1":
        q = 200.0 + r1_slope * c
    elif which == "R2":
        q = 150.0 + 0.003 * c
    elif which == "R0":
        q = r0_share * (1500.0 + 0.02 * (c - 300.0 / speed_ms))
    else:
        raise ValueError(which)
    per = round(period_s / interval_s)
    return q[: (n // per) * per].reshape(-1, per).mean(axis=1)


def set_values(
    frame: pd.DataFrame,
    *,
    station: str,
    date: str,
    start_h: float,
    end_h: float,
    lane: str | None = None,
    **values: float,
) -> pd.DataFrame:
    """Overwrite columns for one station (lane) between two local hours of a date."""
    out = frame.copy()
    out.attrs = dict(frame.attrs)
    ts = pd.to_datetime(out["timestamp"].astype(str))
    hours = ts.dt.hour + ts.dt.minute / 60.0
    mask = (
        (out["station"] == station)
        & (ts.dt.strftime("%Y-%m-%d") == date)
        & (hours >= start_h)
        & (hours < end_h)
    )
    if lane is not None:
        mask &= out["lane"] == lane
    for column, value in values.items():
        out.loc[mask, column] = value
    return out


def scale_values(
    frame: pd.DataFrame,
    *,
    station: str,
    factor: float,
    date: str | None = None,
    lane: str | None = None,
    column: str = "flow_veh_h",
) -> pd.DataFrame:
    """Multiply a column for one station (lane, date) by ``factor``."""
    out = frame.copy()
    out.attrs = dict(frame.attrs)
    mask = out["station"] == station
    if date is not None:
        mask &= pd.to_datetime(out["timestamp"].astype(str)).dt.strftime("%Y-%m-%d") == date
    if lane is not None:
        mask &= out["lane"] == lane
    out.loc[mask, column] = out.loc[mask, column] * factor
    return out


def drop_rows(frame: pd.DataFrame, *, station: str, date: str) -> pd.DataFrame:
    """Remove every row of one station on one date."""
    keep = ~(
        (frame["station"] == station)
        & (pd.to_datetime(frame["timestamp"].astype(str)).dt.strftime("%Y-%m-%d") == date)
    )
    out = frame.loc[keep].reset_index(drop=True)
    out.attrs = dict(frame.attrs)
    return out


# ---------------------------------------------------------------------------
# Lane order (data_quality.lane_order_check)
# ---------------------------------------------------------------------------

LANE_ORDER_STATIONS: tuple[tuple[str, float, int], ...] = (
    ("S1", 0.0, 3),
    ("S2", 800.0, 3),
    ("S3", 1600.0, 3),
    ("W", 2000.0, 4),
    ("S4", 2400.0, 3),
    ("S5", 3200.0, 3),
)
"""``(station, x_m, lanes)``: five three-lane stations and one four-lane
station (``W``) that no other station matches."""

#: Free-flow speed [m/s] and effective vehicle length [m] by lane, lane 1 the
#: rightmost: the right lane slower and carrying the trucks, so its occupancy
#: per vehicle is the highest (docs/I94_LANE_SHARES.md §3).
_LANE_SPEED_MS: tuple[float, ...] = (26.0, 28.0, 30.0, 31.0)
_LANE_LENGTH_M: tuple[float, ...] = (8.0, 6.5, 6.0, 6.0)


def lane_shares_at(u: np.ndarray, lanes: int, offset: float = 0.0) -> np.ndarray:
    """Lane shares (lane 1 = rightmost first) at per-lane flow ``u`` (fraction of 2,000 veh/h).

    Light traffic keeps right; as the flow rises it spreads to the inner lanes —
    the shared lane-use pattern the lane-order check reads.
    """
    right = (1.86 - 1.35 * u) / lanes + offset
    left = (0.30 + 1.20 * u) / lanes - offset / 2.0
    if lanes == 2:
        out = np.stack([right + 0.15, 1.0 - right - 0.15])
    else:
        middle = (1.0 - right - left) / (lanes - 2)
        out = np.stack([right, *([middle] * (lanes - 2)), left])
    clipped: np.ndarray = np.clip(out, 0.02, None)
    return clipped / clipped.sum(axis=0)


def lane_order_frame(
    *,
    seed: int = 0,
    dates: Sequence[str] = DATES[:3],
    interval_s: float = 30.0,
    start_h: float = 5.0,
    end_h: float = 11.0,
    reversed_stations: Sequence[str] = (),
    stations: Sequence[tuple[str, float, int]] = LANE_ORDER_STATIONS,
    lane_ids: str = "number",
) -> pd.DataFrame:
    """A per-lane frame (30-s by default) whose lane use follows :func:`lane_shares_at`.

    Args:
        seed: RNG seed.
        dates: Local dates.
        interval_s: Window [s].
        start_h: First local hour.
        end_h: Local hour the span ends before.
        reversed_stations: Stations whose lane labels are reversed (lane k
            reported as lane n + 1 − k: counts, occupancy and speed move
            together, as with a mislabelled loop).
        stations: ``(station, x_m, lanes)`` rows.
        lane_ids: ``"number"`` (lane ids 1..n, a generic per-lane CSV) or
            ``"detector"`` (detector names ``<station>d<k>``, MnDOT-style;
            :func:`lane_order_numbers` gives the true numbering).

    Returns:
        The frame, ``attrs["interval_s"]`` set.
    """
    rng = np.random.default_rng(seed)
    n = round((end_h - start_h) * 3600.0 / interval_s)
    t = start_h * 3600.0 + (np.arange(n) + 0.5) * interval_s
    to_veh = interval_s / 3600.0
    frames: list[pd.DataFrame] = []
    for date in dates:
        midnight = datetime.fromisoformat(date).replace(tzinfo=TZ)
        stamps = [midnight + timedelta(seconds=start_h * 3600.0 + k * interval_s) for k in range(n)]
        day = float(rng.uniform(0.92, 1.08))
        base = mainline_profile(t) * day
        for station, x_m, lanes in stations:
            q = base * (1.0 + 0.15 * (lanes - 3))
            u = np.clip(q / lanes / 2000.0, 0.0, 1.0)
            shares = lane_shares_at(u, lanes, offset=float(rng.uniform(-0.03, 0.03)))
            total = rng.poisson(q * to_veh)
            counts = np.zeros((lanes, n), dtype=np.int64)
            remaining = total.copy()
            left_share = np.ones(n)
            for k in range(lanes - 1):
                p = np.clip(shares[k] / left_share, 0.0, 1.0)
                counts[k] = rng.binomial(remaining, p)
                remaining = remaining - counts[k]
                left_share = left_share - shares[k]
            counts[lanes - 1] = remaining
            slow = 1.0 - 0.35 * u
            for k in range(lanes):
                v = _LANE_SPEED_MS[k] * slow + rng.normal(0.0, 0.5, n)
                flow = counts[k] / to_veh
                occ = flow / 3600.0 * _LANE_LENGTH_M[k] / v * 100.0
                label = lanes - k if station in reversed_stations else k + 1
                lane = str(label) if lane_ids == "number" else f"{station}d{label}"
                frames.append(
                    pd.DataFrame(
                        {
                            "timestamp": stamps,
                            "station": station,
                            "flow_veh_h": flow.astype(float),
                            "occupancy_pct": occ,
                            "speed_ms": np.where(counts[k] > 0, v, np.nan),
                            "lanes": 1,
                            "kind": "mainline",
                            "x_m": x_m,
                            "lane": lane,
                        }
                    )
                )
    frame = pd.concat(frames, ignore_index=True)
    frame.attrs["interval_s"] = float(interval_s)
    return frame


def lane_order_numbers(
    stations: Sequence[tuple[str, float, int]] = LANE_ORDER_STATIONS,
) -> dict[str, int]:
    """Sensor id → lane number for :func:`lane_order_frame`'s ``lane_ids="detector"``."""
    return {
        f"{station}:{station}d{k}": k for station, _, lanes in stations for k in range(1, lanes + 1)
    }


def lane_order_stations_table(
    stations: Sequence[tuple[str, float, int]] = LANE_ORDER_STATIONS,
) -> pd.DataFrame:
    """The stations table of :func:`lane_order_frame` (positions, kinds, lane counts)."""
    return pd.DataFrame(
        [
            {"station": s, "label": s, "x_m": x, "lanes": lanes, "kind": "mainline"}
            for s, x, lanes in stations
        ]
    )
