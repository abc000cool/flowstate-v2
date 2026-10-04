"""Synthetic corridors with a planted free-flow speed and capacity (WP-103 tests).

No real data is ever read. Four mainline stations, 3 lanes each, 1 km apart::

    S1 (0) ── S2 (1000) ── S3 (2000) ── S4 (3000)

Each day runs 05:00–11:00 local in 5-minute windows:

* 05:00–06:30 and 09:00–11:00 — light traffic (400–900 veh/h/lane) at the
  planted free-flow speed ``ff_speed`` (± ``ff_sd`` noise) everywhere;
* 06:30–07:00 — loading (1,300–1,600 veh/h/lane, never above the planted
  capacity less 100), a little slower;
* 07:00–09:00 — the peak. With ``bottleneck`` the S2→S3 pair is an active
  bottleneck: S2 queues at 11 m/s while S3, downstream, discharges the planted
  ``capacity`` per lane (± ``cap_sd``) at 27 m/s; S1 and S4 stay fast. Without
  it every station stays above 25 m/s (the road never reaches capacity).

Occupancies follow ``q·L/v`` with a 6.5 m effective length (consistent with
the data-quality implied-length check). Loaded by path from the test modules.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

TZ = timezone(timedelta(hours=-5))
DATES: tuple[str, ...] = (
    "2026-09-01",
    "2026-09-02",
    "2026-09-03",
    "2026-09-08",
    "2026-09-09",
)
EFFECTIVE_LENGTH_M = 6.5
LANES = 3
LANE_SHARES: tuple[float, ...] = (0.31, 0.33, 0.36)
STATIONS: tuple[tuple[str, float], ...] = (
    ("S1", 0.0),
    ("S2", 1000.0),
    ("S3", 2000.0),
    ("S4", 3000.0),
)
START_H = 5.0
END_H = 11.0
INTERVAL_S = 300.0


def stations_table(speed_limit_ms: float | None = None) -> pd.DataFrame:
    """The stations table (positions, lanes, kinds, optional posted limit)."""
    rows = [
        {"station": s, "x_m": x, "lanes": LANES, "kind": "mainline", "label": s}
        for s, x in STATIONS
    ]
    frame = pd.DataFrame(rows)
    if speed_limit_ms is not None:
        frame["speed_limit_ms"] = speed_limit_ms
    return frame


def corridor_frame(
    *,
    ff_speed: float = 30.0,
    ff_sd: float = 0.8,
    capacity: float = 1800.0,
    cap_sd: float = 20.0,
    bottleneck: bool = True,
    dates: Sequence[str] = DATES,
    per_lane: bool = False,
    seed: int = 0,
) -> pd.DataFrame:
    """A tidy detector frame of the four-station corridor (module docstring)."""
    rng = np.random.default_rng(seed)
    n = round((END_H - START_H) * 3600.0 / INTERVAL_S)
    hours = START_H + np.arange(n) * INTERVAL_S / 3600.0
    light = (hours < 6.5) | (hours >= 9.0)
    loading = (hours >= 6.5) & (hours < 7.0)
    peak = (hours >= 7.0) & (hours < 9.0)
    rows: list[dict[str, Any]] = []
    for date in dates:
        midnight = datetime.fromisoformat(date).replace(tzinfo=TZ)
        base_q = np.where(
            hours < 6.5,
            400.0 + 500.0 * (hours - START_H) / 1.5,
            900.0 - 400.0 * np.clip((hours - 9.0) / 2.0, 0.0, 1.0),
        )
        for station, x in STATIONS:
            q = base_q + rng.normal(0.0, 15.0, n)
            v = ff_speed + rng.normal(0.0, ff_sd, n)
            loading_q = np.minimum(rng.uniform(1300.0, 1600.0, n), capacity - 100.0)
            q = np.where(loading, loading_q, q)
            v = np.where(loading, ff_speed - 2.0 + rng.normal(0.0, 0.5, n), v)
            if bottleneck:
                peak_q = {
                    "S1": 1650.0 + rng.normal(0.0, 20.0, n),
                    "S2": capacity - 30.0 + rng.normal(0.0, cap_sd, n),
                    "S3": capacity + rng.normal(0.0, cap_sd, n),
                    "S4": capacity - 80.0 + rng.normal(0.0, cap_sd, n),
                }[station]
                peak_v = {
                    "S1": 26.0 + rng.normal(0.0, 0.5, n),
                    "S2": 11.0 + rng.normal(0.0, 1.0, n),
                    "S3": 27.0 + rng.normal(0.0, 0.5, n),
                    "S4": 28.0 + rng.normal(0.0, 0.5, n),
                }[station]
            else:
                peak_q = capacity - 100.0 + rng.normal(0.0, cap_sd, n)
                peak_v = 26.0 + rng.normal(0.0, 0.5, n)
            q = np.where(peak, peak_q, q)
            v = np.where(peak, peak_v, v)
            assert not (light & peak).any()
            if per_lane:
                parts = [(str(i + 1), q * share * LANES, 1) for i, share in enumerate(LANE_SHARES)]
            else:
                parts = [(None, q * LANES, LANES)]
            for lane, q_total, lanes in parts:
                q_lane = q_total / lanes
                occ = q_lane / 3600.0 * EFFECTIVE_LENGTH_M / v * 100.0
                occ = occ * (1.0 + rng.normal(0.0, 0.02, n))
                for k in range(n):
                    row: dict[str, Any] = {
                        "timestamp": midnight + timedelta(hours=float(hours[k])),
                        "station": station,
                        "flow_veh_h": float(q_total[k]),
                        "occupancy_pct": float(occ[k]),
                        "speed_ms": float(v[k] + (rng.normal(0.0, 0.2) if lane else 0.0)),
                        "lanes": lanes,
                        "kind": "mainline",
                        "x_m": x,
                    }
                    if lane is not None:
                        row["lane"] = lane
                    rows.append(row)
    frame = pd.DataFrame(rows)
    frame.attrs["interval_s"] = INTERVAL_S
    return frame


def classification_frame(
    share: float, *, dates: Sequence[str] = DATES, total: int = 10000
) -> pd.DataFrame:
    """Daily classification counts per station with an exact heavy share."""
    rows = [
        {
            "Site": station,
            "Day": date,
            "Trucks": round(total * share),
            "Volume": total,
        }
        for date in dates
        for station, _ in STATIONS
    ]
    return pd.DataFrame(rows)


def write_tidy(frame: pd.DataFrame, path: Any) -> Any:
    """Write a tidy frame as CSV (timestamps ISO-8601 with offset)."""
    out = frame.copy()
    out["timestamp"] = [t.isoformat() for t in out["timestamp"]]
    out.to_csv(path, index=False)
    return path
