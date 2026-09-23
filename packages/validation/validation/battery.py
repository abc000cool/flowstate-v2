"""Per-replicate scoring shared by the corridor battery and the report job.

``scripts/corridor_battery.py`` (a seeded replicate battery on a corridor with
observations) and ``api.jobs.report_job`` (the same comparison on a set of
finished API runs) need identical answers to the same three questions about
one completed replicate directory: what its measurement window is, how it
scores against the corridor's observations (:mod:`validation.observed`), and
what backward wave speed a given detector reads on its field. They live here
so the two callers cannot drift apart — the failure mode CLAUDE.md §0.1 is
about, where a number in a report and the same number in an artifact were
computed by two copies of the code.

Everything in this module reads a replicate directory as written by
``microsim.runner.run_micro`` (docs/CONTRACTS.md §3): ``meta.json`` plus
``trajectories.parquet``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from validation.fields import speed_field
from validation.metrics import warmup_from_meta
from validation.observed import ObservedCorridor, ObservedScores, score_run_against_observed
from validation.waves import WaveDetector

#: Trajectory columns every comparison here needs (contract §3).
TRAJECTORY_COLUMNS: tuple[str, ...] = ("t", "veh_id", "x", "v")


def load_meta(run_dir: str | Path) -> dict[str, Any]:
    """Parse one replicate's ``meta.json``.

    Args:
        run_dir: Replicate directory.

    Returns:
        The parsed metadata.

    Raises:
        ValueError: The file does not hold a JSON object.
    """
    raw = json.loads((Path(run_dir) / "meta.json").read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{run_dir}: meta.json is not a JSON object")
    return raw


def measurement_window(meta: Mapping[str, Any]) -> tuple[float, float]:
    """The replicate's ``(warmup_s, duration_s)`` [s] from its metadata.

    ``warmup_s`` is resolved by :func:`validation.metrics.warmup_from_meta`
    (the single place the warm-up convention lives) and ``duration_s`` is the
    run's configured simulated length — the *nominal* recorded span, not the
    last output sample, so the final analysis window is compared rather than
    dropped for want of one sampling interval.

    Args:
        meta: Parsed ``meta.json``.

    Returns:
        ``(warmup_s, duration_s)``.

    Raises:
        ValueError: The metadata carries no ``config.sim.duration_s``.
    """
    config = meta.get("config")
    sim = config.get("sim") if isinstance(config, dict) else None
    value = sim.get("duration_s") if isinstance(sim, dict) else None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("meta.json carries no config.sim.duration_s")
    return warmup_from_meta(meta), float(value)


def read_trajectories(
    run_dir: str | Path, columns: Sequence[str] = TRAJECTORY_COLUMNS
) -> pd.DataFrame:
    """Read one replicate's trajectory rows.

    Args:
        run_dir: Replicate directory.
        columns: Columns to read (default :data:`TRAJECTORY_COLUMNS`).

    Returns:
        The trajectory frame.
    """
    return pd.read_parquet(Path(run_dir) / "trajectories.parquet", columns=list(columns))


def score_replicate(
    run_dir: str | Path,
    observed: ObservedCorridor,
    *,
    x_offset_m: float = 0.0,
) -> ObservedScores:
    """Score one completed replicate against a corridor's observations.

    Args:
        run_dir: Replicate directory (``meta.json`` + ``trajectories.parquet``).
        observed: The corridor's observations.
        x_offset_m: Simulation ``x`` of the observed origin [m]; see
            :func:`validation.observed.score_run_against_observed`.

    Returns:
        The replicate's :class:`validation.observed.ObservedScores`.
    """
    meta = load_meta(run_dir)
    warmup_s, duration_s = measurement_window(meta)
    trajectories = read_trajectories(run_dir)
    return score_run_against_observed(
        trajectories,
        observed,
        warmup_s=warmup_s,
        duration_s=duration_s,
        x_offset_m=x_offset_m,
    )


def replicate_wave_speed_kmh(run_dir: str | Path, detector: WaveDetector) -> float:
    """Backward wave-front speed [km/h] one detector reads on a replicate.

    The field is binned at the detector's own bins (a
    :class:`validation.waves.WaveDetector` refuses any other binning) over the
    replicate's measurement window, so the reading describes the scored
    period — exactly what :func:`validation.report.generate_report` measures
    for its wave-speed criterion row.

    Args:
        run_dir: Replicate directory.
        detector: Detector recipe, normally the criteria profile's.

    Returns:
        Mean backward-front speed magnitude [km/h]; NaN when the detector
        found no backward front.
    """
    meta = load_meta(run_dir)
    warmup_s, _ = measurement_window(meta)
    traj = read_trajectories(run_dir, columns=("t", "x", "v"))
    if warmup_s > 0.0:
        windowed = traj.loc[traj["t"] >= warmup_s]
        if not windowed.empty:
            traj = windowed
    field = speed_field(traj, dt_bin=detector.dt_bin_s, dx_bin=detector.dx_bin_m)
    return float(detector.measure(field).speed_kmh)


def mean_finite(values: Sequence[float]) -> float:
    """Mean of the finite entries; NaN when none are finite.

    Replicates that produced no reading (no backward front, no comparable
    speed cell) contribute nothing rather than a zero — the convention
    :func:`validation.metrics.aggregate` already follows.

    Args:
        values: Per-replicate values.

    Returns:
        The mean, or NaN.
    """
    finite = [float(v) for v in values if math.isfinite(float(v))]
    return float(np.mean(finite)) if finite else math.nan
