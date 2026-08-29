"""Demand (inflow) calibration against observed link counts (CLAUDE.md §6.3).

Iterative proportional scaling: simulate the corridor with the current inflow
profile, compare simulated link counts to observed counts per time bin with
the GEH statistic (FHWA Traffic Analysis Toolbox Vol. III, FHWA-HOP-18-036:
GEH = √(2(m−c)²/(m+c)) on *hourly* volumes; acceptance GEH < 5 for ≥ 85% of
comparisons), and scale each inflow step by the (damped) observed/simulated
ratio of its overlapping bins until the criterion is met or the iteration cap
is reached.

The simulator is *injected* (``simulate_fn``) so the fitter is engine-
agnostic and unit-testable against a fast surrogate; in production the
microscopic tier (``microsim``) supplies the real simulate function that runs
the corridor scenario and returns binned boundary counts. All interfaces are
SI (veh/s); GEH is computed on veh/h via ``flowstate_core.units`` at the
comparison boundary only.
"""

from __future__ import annotations

from collections.abc import Callable
from math import sqrt

import numpy as np
import pandas as pd

from flowstate_core.artifacts import DemandProfile
from flowstate_core.units import veh_s_to_veh_h

SimulateFn = Callable[[DemandProfile], pd.DataFrame]
"""Injected simulator: profile → DataFrame with columns ``t_start_s``,
``t_end_s``, ``flow_veh_s`` on the same bins as the observed counts. The
microscopic tier provides the real implementation (a corridor run returning
binned link counts); tests use a fast surrogate."""


def geh(m_veh_h: float, c_veh_h: float) -> float:
    """GEH statistic between modeled and counted *hourly* volumes.

    ``GEH = √(2(m−c)²/(m+c))`` (FHWA-HOP-18-036 usage). Returns 0 when both
    volumes are 0.

    Args:
        m_veh_h: Modeled volume [veh/h].
        c_veh_h: Counted volume [veh/h].
    """
    denom = m_veh_h + c_veh_h
    if denom <= 0.0:
        return 0.0
    return sqrt(2.0 * (m_veh_h - c_veh_h) ** 2 / denom)


def _step_spans(steps: list[tuple[float, float]], horizon_s: float) -> list[tuple[float, float]]:
    """[start, end) span of each profile step, closed by ``horizon_s``."""
    starts = [t for t, _ in steps]
    ends = [*starts[1:], max(horizon_s, starts[-1])]
    return list(zip(starts, ends, strict=True))


def fit_inflow(
    observed_counts: pd.DataFrame,
    initial_profile: DemandProfile,
    simulate_fn: SimulateFn,
    *,
    created_at: str,
    source: str,
    data_hash: str = "",
    geh_threshold: float = 5.0,
    geh_pass_frac: float = 0.85,
    max_iters: int = 25,
    max_scale_step: float = 2.0,
) -> DemandProfile:
    """Fit a corridor inflow profile to observed link counts (§6.3).

    Each iteration simulates with the current profile, computes per-bin GEH
    (on veh/h), and stops when at least ``geh_pass_frac`` of bins have
    ``GEH < geh_threshold`` (the FHWA Vol. III shape: GEH < 5 for ≥ 85%).
    Otherwise every profile step is scaled by the duration-weighted mean of
    the observed/simulated flow ratios of the bins overlapping that step,
    clipped to ``[1/max_scale_step, max_scale_step]`` per iteration for
    stability (proportional fitting with damping).

    The returned profile's ``geh_vs_counts`` is the **worst-bin (max) GEH**
    of the returned profile — a conservative, honest summary (the pass/fail
    fraction is what the criterion checks).

    Args:
        observed_counts: DataFrame with columns ``t_start_s``, ``t_end_s``
            [s] and ``flow_veh_s`` [veh/s] — observed link counts per bin.
        initial_profile: Starting ``DemandProfile`` (steps in SI veh/s).
        simulate_fn: Injected simulator (see :data:`SimulateFn`); the real
            one comes from the microscopic tier.
        created_at: ISO-8601 timestamp for the artifact (caller-supplied).
        source: Human-readable provenance of the observed counts.
        data_hash: Hash of the observed-count data (provenance).
        geh_threshold: Per-bin GEH acceptance threshold.
        geh_pass_frac: Required fraction of bins meeting the threshold.
        max_iters: Iteration cap.
        max_scale_step: Per-iteration scale-factor clip (> 1).

    Returns:
        Fitted ``DemandProfile`` (converged, or the best profile at the
        iteration cap) with ``geh_vs_counts`` set.

    Raises:
        ValueError: On malformed inputs or a simulator returning mismatched
            bins.
    """
    for col in ("t_start_s", "t_end_s", "flow_veh_s"):
        if col not in observed_counts.columns:
            raise ValueError(f"fit_inflow: observed_counts missing column {col!r}")
    if max_scale_step <= 1.0:
        raise ValueError(f"max_scale_step must be > 1, got {max_scale_step}")
    if not 0.0 < geh_pass_frac <= 1.0:
        raise ValueError(f"geh_pass_frac must be in (0, 1], got {geh_pass_frac}")

    obs = observed_counts.sort_values("t_start_s")
    bin_start = obs["t_start_s"].to_numpy(dtype=float)
    bin_end = obs["t_end_s"].to_numpy(dtype=float)
    obs_flow = obs["flow_veh_s"].to_numpy(dtype=float)
    horizon = float(bin_end.max())

    def make_profile(steps: list[tuple[float, float]]) -> DemandProfile:
        return DemandProfile(
            created_at=created_at,
            source=source,
            data_hash=data_hash,
            steps=steps,
            geh_vs_counts=None,
        )

    profile = make_profile(list(initial_profile.steps))
    gehs = np.empty(0)
    for iteration in range(max_iters + 1):
        sim = simulate_fn(profile).sort_values("t_start_s")
        sim_start = sim["t_start_s"].to_numpy(dtype=float)
        if sim_start.shape != bin_start.shape or not np.allclose(sim_start, bin_start):
            raise ValueError("simulate_fn returned bins that do not match observed_counts")
        sim_flow = sim["flow_veh_s"].to_numpy(dtype=float)
        gehs = np.array(
            [
                geh(veh_s_to_veh_h(m), veh_s_to_veh_h(c))
                for m, c in zip(sim_flow, obs_flow, strict=True)
            ]
        )
        # Stop on the criterion, or at the cap *without* a trailing unscored
        # scale step — the returned GEH always describes the returned profile.
        if float(np.mean(gehs < geh_threshold)) >= geh_pass_frac or iteration == max_iters:
            break
        # Proportional scaling, damped.
        with np.errstate(divide="ignore", invalid="ignore"):
            ratios = np.where(sim_flow > 0, obs_flow / np.maximum(sim_flow, 1e-12), max_scale_step)
        ratios = np.clip(ratios, 1.0 / max_scale_step, max_scale_step)
        new_steps: list[tuple[float, float]] = []
        for (s_start, s_end), (_, q) in zip(
            _step_spans(list(profile.steps), horizon), profile.steps, strict=True
        ):
            overlap = np.minimum(bin_end, s_end) - np.maximum(bin_start, s_start)
            weights = np.maximum(overlap, 0.0)
            factor = float(np.average(ratios, weights=weights)) if weights.sum() > 0 else 1.0
            new_steps.append((s_start, q * factor))
        profile = make_profile(new_steps)

    return DemandProfile(
        created_at=created_at,
        source=source,
        data_hash=data_hash,
        steps=list(profile.steps),
        geh_vs_counts=float(np.max(gehs)) if gehs.size else None,
    )
