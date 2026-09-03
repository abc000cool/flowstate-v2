"""Integration: the microscopic-tier demand adapter (CLAUDE.md §6.3).

``microsim.demand_adapter.make_simulate_fn`` turns a candidate inflow profile
into binned boundary counts from a real SUMO run, and
``calibration.demand.fit_inflow(scenario, counts)`` uses it by default. A
1 km single-lane corridor (1 km insertion buffer) run for 300 s is enough
to count crossings of the corridor-proper start in four 60 s windows.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from calibration.demand import fit_inflow
from flowstate_core.artifacts import DemandProfile
from flowstate_core.config import ScenarioConfig
from microsim.demand_adapter import MicrosimSimulator, corridor_x_offset_m, make_simulate_fn

pytestmark = pytest.mark.integration

CREATED = "2026-09-03T00:00:00+00:00"
TRUTH_VEH_S = 0.4
BINS = [(60.0, 120.0), (120.0, 180.0), (180.0, 240.0), (240.0, 300.0)]


def _cfg(inflow_veh_s: float) -> ScenarioConfig:
    return ScenarioConfig.model_validate(
        {
            "name": "demand_adapter_smoke",
            "network": {
                "kind": "corridor",
                "length_m": 1000.0,
                "lanes": 1,
                "inflow": [[0.0, inflow_veh_s]],
            },
            "sim": {"duration_s": 300.0},
            "seed": 42,
        }
    )


def _profile(q: float) -> DemandProfile:
    return DemandProfile(created_at=CREATED, source="test", data_hash="", steps=[(0.0, q)])


class TestAdapter:
    def test_counts_on_requested_bins(self, tmp_path: Path):
        cfg = _cfg(TRUTH_VEH_S)
        sim = make_simulate_fn(cfg, tmp_path, bins=BINS)
        assert isinstance(sim, MicrosimSimulator)
        assert sim.x_ref_m == corridor_x_offset_m(cfg) == 1000.0
        assert sim.seed == 42
        df = sim(_profile(TRUTH_VEH_S))
        assert list(df.columns) == ["t_start_s", "t_end_s", "flow_veh_s"]
        assert df["t_start_s"].tolist() == [a for a, _ in BINS]
        assert df["t_end_s"].tolist() == [b for _, b in BINS]
        # Vehicles inserted at ~30 m/s reach x = 1000 m after ~35 s, so every
        # window from 60 s on sees traffic near the inserted rate.
        assert (df["flow_veh_s"] > 0.0).all()
        assert df["flow_veh_s"].iloc[1:].mean() == pytest.approx(TRUTH_VEH_S, rel=0.35)
        assert sim.n_calls == 1
        assert len(sim.run_dirs) == 1 and (sim.run_dirs[0] / "meta.json").is_file()
        assert sim.run_dirs[0].is_relative_to(tmp_path / "iter001")

    def test_validation(self, tmp_path: Path):
        cfg = _cfg(TRUTH_VEH_S)
        with pytest.raises(ValueError, match="duration"):
            make_simulate_fn(cfg, tmp_path, bins=[(0.0, 600.0)])
        with pytest.raises(ValueError, match="width"):
            make_simulate_fn(cfg, tmp_path, bins=[(10.0, 10.0)])
        with pytest.raises(ValueError, match="empty"):
            make_simulate_fn(cfg, tmp_path, bins=[])
        ring = ScenarioConfig.model_validate(
            {
                "name": "ring",
                "network": {"kind": "ring", "circumference_m": 230.0, "n_vehicles": 22},
                "sim": {"duration_s": 60.0},
            }
        )
        with pytest.raises(ValueError, match="no inflow"):
            make_simulate_fn(ring, tmp_path, bins=BINS)


class TestFitInflowWithMicrosim:
    def test_recovers_inflow_from_synthetic_counts(self, tmp_path: Path):
        """Observed counts synthesized from one run at the truth inflow; the
        fitter starts from a deliberately low profile and must scale up.
        Same seed on both sides, so the residual is only insertion
        discretisation."""
        truth_sim = make_simulate_fn(_cfg(TRUTH_VEH_S), tmp_path / "truth", bins=BINS)
        observed = truth_sim(_profile(TRUTH_VEH_S))
        fitted = fit_inflow(
            _cfg(0.25),
            observed,
            created_at=CREATED,
            source="synthetic microsim counts",
            workdir=tmp_path / "fit",
            max_iters=3,
        )
        assert fitted.steps[0][0] == 0.0
        assert fitted.steps[0][1] == pytest.approx(TRUTH_VEH_S, rel=0.15)
        assert fitted.geh_vs_counts is not None and fitted.geh_vs_counts < 5.0
        assert sorted(p.name for p in (tmp_path / "fit").iterdir())[0] == "iter001"
