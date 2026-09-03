"""Macro-vs-micro sanity (CLAUDE.md §9): the same seeded shock congests
backward in both tiers.

One :class:`ScenarioConfig` — a short single-lane corridor with inflow in
the unstable band and a :class:`PerturbationSpec` (so ``seeded=True``) — is
run through :func:`macrosim.run_macro` and :func:`microsim.run_micro` with
the same seed. Each tier's ``edges.parquet`` (docs/CONTRACTS.md §3) is
loaded into a :class:`validation.fields.SpeedField` and passed to
:func:`validation.waves.detect_waves`; both tiers must show at least one
backward-propagating (upstream-moving) jam front. The assertion is
qualitative agreement only, as the spec says: the macro tier is a
string-stable screening model (ADR-1) and its front speed is the FD's
congested-branch slope, not the micro tier's emergent value.

Coordinate note: both runners apply the shock at ``position_m`` in their own
linear ``x``. The micro corridor's ``x`` starts at the 2 km insertion buffer
(``microsim.runner.CORRIDOR_INSERTION_BUFFER_M``), so ``position_m = 2500``
is 500 m into the corridor proper there and 2500 m into the 3 km corridor
in the macro tier — inside the modeled road in both, which is all the
qualitative check needs.

Run order matters: libsumo's bundled libarrow breaks path-based parquet
writes for the rest of the process (see ``tests/test_microsim/conftest.py``;
the shim there is only installed when that directory is collected), and
:func:`run_macro` writes ``edges.parquet`` by path — so the macro run comes
first, before SUMO is loaded.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from flowstate_core.config import ScenarioConfig
from flowstate_core.constants import V_JAM_THRESH
from macrosim.runner import run_macro
from microsim import run_micro
from validation.fields import SpeedField
from validation.waves import WaveSet, detect_waves

pytestmark = pytest.mark.integration

#: Time bin [s] the edges data is regrouped into before wave detection —
#: the micro tier's Edie bin, and the CLAUDE.md §7.2 field resolution.
DT_BIN_S = 15.0
SEED = 7
SHOCK_POSITION_M = 2500.0


def tier_sanity_config() -> ScenarioConfig:
    """The shared seeded-shock scenario (one config object for both tiers)."""
    return ScenarioConfig.model_validate(
        {
            "name": "tier_sanity_corridor",
            "network": {
                "kind": "corridor",
                "length_m": 3000.0,
                "lanes": 1,
                "inflow": [[0.0, 0.45]],
            },
            "sim": {"duration_s": 420.0, "step_length_s": 0.5, "output_hz": 2.0},
            "perturbation": {
                "t_s": 150.0,
                "position_m": SHOCK_POSITION_M,
                "duration_s": 90.0,
                "v_drop_ms": 30.0,
            },
            "seed": SEED,
            "replicates": 1,
        }
    )


def field_from_edges(edges: pd.DataFrame, dt_bin: float = DT_BIN_S) -> SpeedField:
    """Regroup an ``edges.parquet`` frame into a uniform speed field.

    Rows are binned by ``floor(t_bin / dt_bin)`` (NaN-aware mean of
    ``mean_speed`` per bin, so empty micro bins stay empty) and pivoted on
    the recorded ``x_bin`` centers; space edges are the midpoints between
    centers, extended by half a bin at each end.

    Args:
        edges: Contract edges frame (``t_bin``, ``x_bin``, ``mean_speed``).
        dt_bin: Target time bin [s].

    Returns:
        A :class:`SpeedField` with ``mean_speed`` of shape ``[nt, nx]``.
    """
    t_idx = np.floor(edges["t_bin"].to_numpy(dtype=np.float64) / dt_bin).astype(np.int64)
    pivot = edges.assign(_t_idx=t_idx).pivot_table(
        index="_t_idx", columns="x_bin", values="mean_speed", aggfunc="mean", dropna=False
    )
    rows = pivot.index.to_numpy(dtype=np.int64)
    assert np.array_equal(rows, np.arange(rows[0], rows[-1] + 1)), "non-contiguous time bins"
    t_edges = np.arange(rows[0], rows[-1] + 2, dtype=np.float64) * dt_bin
    x_centers = pivot.columns.to_numpy(dtype=np.float64)
    mid = 0.5 * (x_centers[:-1] + x_centers[1:])
    x_edges = np.concatenate(
        [[x_centers[0] - (mid[0] - x_centers[0])], mid, [x_centers[-1] + (x_centers[-1] - mid[-1])]]
    )
    return SpeedField(t_edges=t_edges, x_edges=x_edges, mean_speed=pivot.to_numpy(dtype=np.float64))


def _jam_upstream_extent(field: SpeedField, v_thresh: float) -> float:
    """Minimum bin-center position [m] of any jammed (``v < v_thresh``) bin."""
    jammed = np.nan_to_num(field.mean_speed, nan=np.inf) < v_thresh
    assert jammed.any(), "no jammed bins at all"
    x_centers = 0.5 * (field.x_edges[:-1] + field.x_edges[1:])
    return float(x_centers[np.flatnonzero(jammed.any(axis=0))].min())


def _describe(waves: WaveSet) -> str:
    return ", ".join(f"{w.speed_ms * 3.6:+.1f} km/h" for w in waves.waves) or "none"


def test_both_tiers_show_backward_propagating_congestion(tmp_path: Path) -> None:
    """Same config + seed ⇒ backward-moving jam fronts in macro AND micro."""
    cfg = tier_sanity_config()
    assert cfg.seeded is True

    # Macro first (see module docstring for the parquet ordering constraint).
    macro_dir = run_macro(cfg, SEED, tmp_path / "macro")
    micro = run_micro(cfg, SEED, tmp_path / "micro")

    meta_macro = json.loads((macro_dir / "meta.json").read_text())
    meta_micro = json.loads(micro.meta.read_text())
    assert meta_macro["tier"] == "screening" and meta_macro["seeded"] is True
    assert meta_micro["tier"] == "micro" and meta_micro["seeded"] is True
    assert meta_macro["config_hash"] == meta_micro["config_hash"]
    assert meta_micro["perturbed_vehicle"] is not None

    field_macro = field_from_edges(pd.read_parquet(macro_dir / "edges.parquet"))
    field_micro = field_from_edges(pd.read_parquet(micro.edges))

    waves_macro = detect_waves(field_macro)
    waves_micro = detect_waves(field_micro)
    assert waves_macro.backward(), f"macro: no backward front (fronts: {_describe(waves_macro)})"
    assert waves_micro.backward(), f"micro: no backward front (fronts: {_describe(waves_micro)})"
    assert all(w.speed_ms < 0.0 for w in waves_macro.backward())
    assert all(w.speed_ms < 0.0 for w in waves_micro.backward())

    # Qualitative agreement: the congestion extends UPSTREAM of the shock in
    # both tiers (a queue behind the slowdown, not a disturbance ahead of it).
    # Macro caps the flux at a fixed interface, so "the shock" is
    # position_m. Micro applies SUMO ``slowDown`` to the nearest vehicle,
    # which ramps its speed down OVER the perturbation window while it keeps
    # moving, so the reference is that vehicle's position at release.
    assert _jam_upstream_extent(field_macro, V_JAM_THRESH) < SHOCK_POSITION_M
    pert = cfg.perturbation
    assert pert is not None
    traj = pd.read_parquet(micro.trajectories, columns=["t", "veh_id", "x"])
    slowed = traj[
        (traj.veh_id == meta_micro["perturbed_vehicle"]) & (traj.t <= pert.t_s + pert.duration_s)
    ]
    x_release = float(slowed.sort_values("t").x.iloc[-1])
    assert SHOCK_POSITION_M <= x_release
    assert _jam_upstream_extent(field_micro, V_JAM_THRESH) < x_release
