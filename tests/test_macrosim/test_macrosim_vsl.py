"""Macro-tier VSL: per-cell ``V_f`` cap through the reduced diagram (CLAUDE.md §4.4, §5).

The scenarios below force the threshold controller down its ladder
deterministically by setting its escalation trigger ``v_on`` above the
diagram's ``v_f`` (every occupied downstream segment then counts as
congested), so the expected steady state is hand-computable from the
fundamental diagram: a capped segment in free flow runs at the effective
limit ``v_lim`` with density ``q_in / v_lim``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from controllers.vsl import effective_limit
from flowstate_core.config import AVSpec, CorridorNetwork, RingNetwork, ScenarioConfig, SimSpec
from flowstate_core.units import kmh_to_ms
from macrosim.fundamental import v1_legacy_fd
from macrosim.runner import VSL_INTERVAL_S, run_macro

LENGTH_M = 5000.0
INFLOW_VEH_S = 0.3
DURATION_S = 600.0
#: Escalation trigger above ``v_f`` (v1 legacy: 27.78 m/s) ⇒ the ladder is
#: walked to its deepest rung (50 km/h) one rung per dispatch.
ALWAYS_CONGESTED = {"v_on": 40.0}
DEEPEST_RUNG_MS = kmh_to_ms(50.0)


def _corridor_cfg(av: AVSpec) -> ScenarioConfig:
    return ScenarioConfig(
        name="macro_vsl_test",
        tier="macro",
        network=CorridorNetwork(length_m=LENGTH_M, lanes=1, inflow=[(0.0, INFLOW_VEH_S)]),
        sim=SimSpec(duration_s=DURATION_S, step_length_s=0.5, output_hz=0.2),
        av=av,
        seed=1,
        replicates=1,
    )


def _read(run_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    edges = pd.read_parquet(run_dir / "edges.parquet")
    meta = json.loads((run_dir / "meta.json").read_text())
    return edges, meta


@pytest.fixture(scope="module")
def runs(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, tuple[pd.DataFrame, dict[str, Any]]]:
    root = tmp_path_factory.mktemp("macro_vsl")
    cfgs = {
        "base": _corridor_cfg(AVSpec()),
        "full": _corridor_cfg(AVSpec(vsl="vsl_threshold", vsl_params=ALWAYS_CONGESTED)),
        "low": _corridor_cfg(
            AVSpec(compliance=0.1, vsl="vsl_threshold", vsl_params=ALWAYS_CONGESTED)
        ),
    }
    return {name: _read(run_macro(cfg, seed=1, out_dir=root / name)) for name, cfg in cfgs.items()}


def _late(edges: pd.DataFrame, x_lo: float, x_hi: float) -> pd.DataFrame:
    """Bins in ``[x_lo, x_hi)`` after the ladder bottomed out and the corridor filled.

    The deepest rung is reached at the 5th dispatch (150 s); the capped front
    then crosses 5 km at ≥ 13.9 m/s within a further 360 s, so t ≥ 540 s is
    steady state for every segment.
    """
    return edges[(edges.t_bin >= 540.0) & (edges.x_bin >= x_lo) & (edges.x_bin < x_hi)]


class TestSegmentsAndDispatch:
    def test_segments_are_one_km_cell_groups(self, runs):
        _, meta = runs["full"]
        d = meta["vsl_dispatch"]
        assert d["segments"] == [[0, 10], [10, 20], [20, 30], [30, 40], [40, 50]]
        assert d["segment_lengths_m"] == [1000.0] * 5
        assert d["segment_target_m"] == 1000.0
        assert d["interval_s"] == VSL_INTERVAL_S == 30.0
        assert d["n_dispatches"] == DURATION_S / VSL_INTERVAL_S
        assert d["base_limit_ms"] == v1_legacy_fd().v_f

    def test_ladder_walks_one_rung_per_dispatch_to_50_kmh(self, runs):
        _, meta = runs["full"]
        posted_seg0 = [h["posted_ms"][0] for h in meta["vsl_dispatch"]["history"]]
        ladder = [kmh_to_ms(v) for v in (90.0, 80.0, 70.0, 60.0, 50.0)]
        assert posted_seg0[:5] == pytest.approx(ladder)
        assert posted_seg0[5:] == pytest.approx([DEEPEST_RUNG_MS] * (len(posted_seg0) - 5))
        # Downstream-most segment has nothing ahead of it → relaxes to the free cap.
        v_f = v1_legacy_fd().v_f
        assert all(h["effective_ms"][-1] == v_f for h in meta["vsl_dispatch"]["history"])

    def test_meta_labels(self, runs):
        _, meta_base = runs["base"]
        _, meta_full = runs["full"]
        assert meta_full["tier"] == "screening"  # CLAUDE.md §5.6 — never validation
        assert meta_full["vsl"] == "vsl_threshold"
        assert meta_full["vsl_dispatch"]["controller"] == "vsl_threshold"
        assert meta_base["vsl"] is None and meta_base["vsl_dispatch"] is None


class TestCappedSpeeds:
    def test_capped_segments_run_at_the_limit_uncapped_segment_at_v_f(self, runs):
        edges_base, _ = runs["base"]
        edges_full, _ = runs["full"]
        v_f = v1_legacy_fd().v_f
        # Speed on the free-flow branch is exactly v_lim; density/flow carry
        # the tail of the contact discontinuity each ladder step launches
        # (first-order Godunov smears it), still ≈ 0.3 % low at t ≥ 540 s.
        for x_lo in (0.0, 1000.0, 2000.0, 3000.0):
            capped = _late(edges_full, x_lo, x_lo + 1000.0)
            base = _late(edges_base, x_lo, x_lo + 1000.0)
            # Free flow on the reduced diagram: V_e = v_lim, ρ = q / v_lim.
            assert capped.mean_speed.to_numpy() == pytest.approx(DEEPEST_RUNG_MS, rel=1e-9)
            assert capped.density.to_numpy() == pytest.approx(
                INFLOW_VEH_S / DEEPEST_RUNG_MS, rel=1e-2
            )
            assert capped.flow.to_numpy() == pytest.approx(INFLOW_VEH_S, rel=1e-2)
            assert base.mean_speed.to_numpy() == pytest.approx(v_f, rel=1e-9)
        # The uncapped tail segment is fed through the smeared contact, so
        # its flow is still ≈ 1.5 % low at 540 s and converges monotonically.
        last = _late(edges_full, 4000.0, LENGTH_M)
        assert last.mean_speed.to_numpy() == pytest.approx(v_f, rel=1e-9)
        assert last.flow.to_numpy() == pytest.approx(INFLOW_VEH_S, rel=2e-2)
        flow_by_t = last.groupby("t_bin").flow.mean()
        assert flow_by_t.is_monotonic_increasing and flow_by_t.iloc[-1] < INFLOW_VEH_S

    def test_reduced_compliance_weakens_the_cap_by_effective_limit(self, runs):
        _, meta_low = runs["low"]
        edges_low, _ = runs["low"]
        edges_full, _ = runs["full"]
        v_f = v1_legacy_fd().v_f
        v_low = effective_limit(DEEPEST_RUNG_MS, v_f, 0.1)  # 0.1·13.89 + 0.9·27.78
        assert v_low == pytest.approx(0.1 * DEEPEST_RUNG_MS + 0.9 * v_f)
        for h in meta_low["vsl_dispatch"]["history"]:
            assert h["effective_ms"] == [effective_limit(p, v_f, 0.1) for p in h["posted_ms"]]
        capped_low = _late(edges_low, 0.0, 4000.0)
        capped_full = _late(edges_full, 0.0, 4000.0)
        assert capped_low.mean_speed.to_numpy() == pytest.approx(v_low, rel=1e-9)
        assert capped_low.mean_speed.min() > capped_full.mean_speed.max()
        assert capped_low.mean_speed.max() < v_f

    def test_limit_equal_to_base_is_a_no_op(self, tmp_path: Path, runs):
        """Posted limits at/above v_f (what compliance 0 yields) change nothing."""
        edges_base, _ = runs["base"]
        v_f = v1_legacy_fd().v_f
        params = {**ALWAYS_CONGESTED, "v_free": v_f, **{f"ladder_{k}": v_f for k in range(5)}}
        cfg = _corridor_cfg(AVSpec(vsl="vsl_threshold", vsl_params=params))
        edges_noop, meta = _read(run_macro(cfg, seed=1, out_dir=tmp_path))
        assert meta["vsl_dispatch"]["n_dispatches"] == DURATION_S / VSL_INTERVAL_S
        assert all(v == v_f for h in meta["vsl_dispatch"]["history"] for v in h["effective_ms"])
        pd.testing.assert_frame_equal(edges_noop, edges_base)


class TestNumericsIntact:
    def test_cfl_step_from_uncapped_v_f_and_no_clamping(self, runs):
        _, meta_base = runs["base"]
        for name in ("full", "low"):
            _, meta = runs[name]
            assert meta["grid"]["dt_s"] == meta_base["grid"]["dt_s"]
            assert meta["clamped"] is False
            assert all(
                v <= meta["fd"]["v_f"]
                for h in meta["vsl_dispatch"]["history"]
                for v in h["effective_ms"]
            )

    def test_open_corridor_ledger_balances(self, runs):
        for name in ("full", "low"):
            _, meta = runs[name]
            led = meta["ledger"]
            assert abs(led["vehicles_in"] - led["vehicles_out"] - led["stored_veh"]) < 1e-8
            assert led["queue_veh"] == 0.0  # demand below the reduced capacity

    def test_ring_conserves_vehicles_across_the_wrap_interface(self, tmp_path: Path):
        """Caps on the wrap-around interfaces (0 and n) keep the ring exactly closed."""
        n_veh = 100
        cfg = ScenarioConfig(
            name="macro_vsl_ring",
            tier="macro",
            network=RingNetwork(circumference_m=5000.0, n_vehicles=n_veh),
            sim=SimSpec(duration_s=300.0, step_length_s=0.5, output_hz=0.5),
            av=AVSpec(vsl="vsl_threshold", vsl_params=ALWAYS_CONGESTED),
            seed=1,
            replicates=1,
        )
        edges, meta = _read(run_macro(cfg, seed=1, out_dir=tmp_path))
        assert meta["clamped"] is False
        assert meta["ledger"]["stored_veh"] == pytest.approx(n_veh, abs=1e-9)
        assert meta["vsl_dispatch"]["n_dispatches"] == 10
        # The cap is binding: some cells are slower than the uniform-ring v_f.
        v_f = v1_legacy_fd().v_f
        late = edges[edges.t_bin >= 200.0]
        assert late.mean_speed.min() < v_f
        assert late.mean_speed.max() <= v_f


def test_vsl_requires_the_controllers_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured VSL that cannot be applied is an error, never a silent skip."""
    monkeypatch.setitem(sys.modules, "controllers", None)  # forces ImportError
    cfg = _corridor_cfg(AVSpec(vsl="vsl_threshold"))
    with pytest.raises(ValueError, match="controllers package"):
        run_macro(cfg, seed=1, out_dir=tmp_path)
