"""Determinism: identical (config, seed) ⇒ byte-identical artifacts.

SUMO with a fixed ``--seed`` and step length is deterministic per version
(CLAUDE.md §9; ``eclipse-sumo`` is pinned to 1.27.1), all FlowState-side
randomness flows through ``flowstate_core.rng`` from the run seed, and the
parquet writer is deterministic — so we assert **exact bytes**, the stronger
of the two contract options (byte equality vs 1e-12 summary stats).
"""

import json

import pytest

from microsim import load_scenario, run_micro

pytestmark = pytest.mark.integration


class TestDeterminism:
    def test_same_cfg_and_seed_twice_is_byte_identical(self, tmp_path):
        cfg = load_scenario("ring_sugiyama").model_copy(deep=True)
        cfg.sim.duration_s = 90.0
        p1 = run_micro(cfg, 42, tmp_path / "a")
        p2 = run_micro(cfg, 42, tmp_path / "b")
        assert p1.trajectories.read_bytes() == p2.trajectories.read_bytes()
        assert p1.edges.read_bytes() == p2.edges.read_bytes()
        m1 = json.loads(p1.meta.read_text())
        m2 = json.loads(p2.meta.read_text())
        assert m1["config_hash"] == m2["config_hash"]
        assert m1["fuel_ml_per_vehicle"] == m2["fuel_ml_per_vehicle"]

    def test_different_seed_differs(self, tmp_path):
        cfg = load_scenario("ring_sugiyama").model_copy(deep=True)
        cfg.sim.duration_s = 60.0
        p1 = run_micro(cfg, 42, tmp_path / "a")
        p2 = run_micro(cfg, 43, tmp_path / "b")
        assert p1.trajectories.read_bytes() != p2.trajectories.read_bytes()
