"""Seeded-perturbation labeling and visibility (CLAUDE.md §0.2)."""

import json

import pandas as pd
import pytest

from flowstate_core.config import ScenarioConfig
from microsim import run_micro

pytestmark = pytest.mark.integration

V_DROP = 15.0


@pytest.fixture(scope="module")
def perturbed_run(tmp_path_factory):
    cfg = ScenarioConfig.model_validate(
        {
            "name": "corridor_perturbed",
            "network": {
                "kind": "corridor",
                "length_m": 3000.0,
                "lanes": 1,
                "inflow": [[0.0, 0.25]],
            },
            "sim": {"duration_s": 180.0},
            "perturbation": {
                "t_s": 60.0,
                "position_m": 2500.0,
                "duration_s": 20.0,
                "v_drop_ms": V_DROP,
            },
        }
    )
    paths = run_micro(cfg, 7, tmp_path_factory.mktemp("pert"))
    return cfg, paths


class TestPerturbationLabeling:
    def test_meta_labeled_seeded_true(self, perturbed_run):
        cfg, paths = perturbed_run
        assert cfg.seeded is True
        meta = json.loads(paths.meta.read_text())
        assert meta["seeded"] is True
        assert meta["perturbed_vehicle"] is not None
        assert meta["config"]["perturbation"]["v_drop_ms"] == pytest.approx(V_DROP)

    def test_slowdown_visible_in_trajectories(self, perturbed_run):
        _, paths = perturbed_run
        meta = json.loads(paths.meta.read_text())
        df = pd.read_parquet(paths.trajectories)
        veh = df[df.veh_id == meta["perturbed_vehicle"]].sort_values("t")
        v_before = veh[(veh.t > 50.0) & (veh.t <= 60.0)].v.mean()
        v_during = veh[(veh.t > 60.0) & (veh.t <= 85.0)].v.min()
        # slowDown ramps the speed down by v_drop over the duration.
        assert v_before - v_during >= 0.8 * V_DROP, (
            f"perturbation invisible: {v_before:.1f} -> min {v_during:.1f} m/s"
        )
        # Control is released afterwards: the vehicle recovers.
        v_after = veh[veh.t > 120.0].v.mean()
        assert v_after > v_during + 5.0


class TestUnseededControl:
    def test_absent_perturbation_labels_seeded_false(self, tmp_path):
        cfg = ScenarioConfig.model_validate(
            {
                "name": "corridor_unseeded",
                "network": {
                    "kind": "corridor",
                    "length_m": 2000.0,
                    "lanes": 1,
                    "inflow": [[0.0, 0.2]],
                },
                "sim": {"duration_s": 60.0},
            }
        )
        paths = run_micro(cfg, 7, tmp_path)
        meta = json.loads(paths.meta.read_text())
        assert meta["seeded"] is False
        assert meta["perturbed_vehicle"] is None
