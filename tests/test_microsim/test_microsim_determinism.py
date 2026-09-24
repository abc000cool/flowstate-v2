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

    def test_weave_section_is_byte_identical(self, tmp_path):
        """The runner-driven weave (``RampSpec.merge = "weave"``) adds no
        unseeded randomness: TraCI requests are issued in sorted-id order."""
        from pathlib import Path

        from flowstate_core.config import ScenarioConfig

        osm = Path(__file__).resolve().parents[1] / "fixtures" / "weave.osm"
        cfg = ScenarioConfig.model_validate(
            {
                "name": "weave_determinism",
                "network": {
                    "kind": "osm",
                    "osm_file": str(osm),
                    "corridor_edges": ["100", "101", "102", "103", "104"],
                    "inflow": [[0.0, 0.5]],
                    "ramps": [
                        {
                            "kind": "on",
                            "name": "on",
                            "edges": ["200"],
                            "attach_edge": "102",
                            "inflow": [[0.0, 0.2]],
                            "merge": "weave",
                            "weave": {"exit_ramp": "off"},
                        },
                        {
                            "kind": "off",
                            "name": "off",
                            "edges": ["201"],
                            "attach_edge": "102",
                            "exit_fraction": [[0.0, 0.3]],
                        },
                    ],
                },
                "sim": {"duration_s": 150.0},
            }
        )
        p1 = run_micro(cfg, 5, tmp_path / "a")
        p2 = run_micro(cfg, 5, tmp_path / "b")
        assert p1.trajectories.read_bytes() == p2.trajectories.read_bytes()
        w1 = json.loads(p1.meta.read_text())["weave_sections"]
        w2 = json.loads(p2.meta.read_text())["weave_sections"]
        assert w1 == w2 and w1[0]["n_entered"] > 0

    def test_th52_weave_at_capacity_is_byte_identical(self, tmp_path):
        """The T.H.52 fixture at capacity (2026-09-24, block 3), where the
        third to sixth derivations all act — through vehicles vacate, pairs
        are released (seed 5), changers are eased — writes byte-identical
        trajectories and an identical ``weave_sections`` entry twice."""
        from pathlib import Path

        from flowstate_core.config import ScenarioConfig

        osm = Path(__file__).resolve().parents[1] / "fixtures" / "weave_th52.osm"
        cfg = ScenarioConfig.model_validate(
            {
                "name": "weave_th52_determinism",
                "network": {
                    "kind": "osm",
                    "osm_file": str(osm),
                    "corridor_edges": ["100", "101", "102", "103", "104"],
                    "inflow": [[0.0, 4500.0 / 3600.0]],
                    "ramps": [
                        {
                            "kind": "on",
                            "name": "th52",
                            "edges": ["200"],
                            "attach_edge": "102",
                            "inflow": [[0.0, 1400.0 / 3600.0]],
                            "merge": "weave",
                            "weave": {"exit_ramp": "cd exit"},
                        },
                        {
                            "kind": "off",
                            "name": "cd exit",
                            "edges": ["201"],
                            "attach_edge": "102",
                            "exit_fraction": [[0.0, 0.25]],
                        },
                    ],
                },
                "sim": {"duration_s": 1200.0},
            }
        )
        p1 = run_micro(cfg, 5, tmp_path / "a")
        p2 = run_micro(cfg, 5, tmp_path / "b")
        assert p1.trajectories.read_bytes() == p2.trajectories.read_bytes()
        (w1,) = json.loads(p1.meta.read_text())["weave_sections"]
        (w2,) = json.loads(p2.meta.read_text())["weave_sections"]
        assert w1 == w2
        # every rule of the third to sixth derivations fired in this run
        assert w1["n_vacated"] > 0 and w1["n_pair_releases"] > 0 and w1["n_changer_eased"] > 0
        assert w1["n_cooperations"] > 0 and w1["n_forced"] > 0
