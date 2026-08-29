"""Performance targets (CLAUDE.md §3.4) — marked slow, excluded from CI's -m "not slow".

Targets (laptop-class, no GPU, libsumo):
* ``ring_sugiyama`` (600 sim-s): ≥ 50× real time.
* ``corridor_10km`` (20 sim-min ≈ 1200 sim-s): ≥ 5× real time.

Measured on the development machine: ring ≈ 300–900×, corridor ≈ 500–850× —
the assertions leave a wide margin for slower CI hardware.
"""

import json

import pytest

from microsim import load_scenario, run_micro

pytestmark = [pytest.mark.integration, pytest.mark.slow]


class TestPerformance:
    def test_ring_600s_at_least_50x_realtime(self, tmp_path):
        cfg = load_scenario("ring_sugiyama")
        paths = run_micro(cfg, cfg.seed, tmp_path)
        meta = json.loads(paths.meta.read_text())
        assert meta["realtime_factor"] >= 50.0, (
            f"ring realtime factor {meta['realtime_factor']:.1f}x < 50x"
        )

    def test_corridor_10km_20min_at_least_5x_realtime(self, tmp_path):
        cfg = load_scenario("corridor_10km")
        assert cfg.sim.duration_s == pytest.approx(1200.0)  # 20 sim-min
        paths = run_micro(cfg, cfg.seed, tmp_path)
        meta = json.loads(paths.meta.read_text())
        assert meta["realtime_factor"] >= 5.0, (
            f"corridor realtime factor {meta['realtime_factor']:.1f}x < 5x"
        )
