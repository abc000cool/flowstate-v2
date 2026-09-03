"""Performance targets (CLAUDE.md §3.4) — marked slow, excluded from CI's -m "not slow".

Targets (laptop-class, no GPU, libsumo):
* ``ring_sugiyama`` (600 sim-s): ≥ 50× real time.
* ``corridor_10km`` (20 sim-min ≈ 1200 sim-s): ≥ 5× real time.
* 20-replicate sweep of one ``corridor_10km`` config: ≤ 15 min wall-clock
  with a ``multiprocessing`` pool (``run_replicates``, 4 workers here).

Measured on the development machine: ring ≈ 300–900×, corridor ≈ 500–850× —
the assertions leave a wide margin for slower CI hardware. These run weekly
in ``.github/workflows/perf.yml`` (``pytest -m slow``).
"""

import json
import time

import pytest

from flowstate_core.rng import spawn_seeds
from microsim import load_scenario, run_micro, run_replicates

pytestmark = [pytest.mark.integration, pytest.mark.slow]

#: CLAUDE.md §3.4 sweep budget [s] and the replicate count it is stated for.
SWEEP_BUDGET_S = 900.0
SWEEP_REPLICATES = 20
SWEEP_PROCS = 4


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

    def test_20_replicate_sweep_under_15_min(self, tmp_path):
        """§3.4: 20 seeded replicates of the real ``corridor_10km`` config in ≤ 900 s.

        The scenario is run unshortened (20 sim-min, ~1,500 vehicles at the
        1800 veh/h demand) with the spawn pool at :data:`SWEEP_PROCS`
        workers. Every replicate must leave a ``meta.json`` carrying its own
        distinct seed from ``spawn_seeds`` (docs/CONTRACTS.md §6).
        """
        cfg = load_scenario("corridor_10km")
        assert cfg.replicates == SWEEP_REPLICATES
        assert cfg.sim.duration_s == pytest.approx(1200.0)

        t0 = time.perf_counter()
        paths = run_replicates(cfg, tmp_path, n_procs=SWEEP_PROCS)
        elapsed = time.perf_counter() - t0

        assert len(paths) == SWEEP_REPLICATES
        assert all(
            p.meta.is_file() and p.trajectories.is_file() and p.edges.is_file() for p in paths
        )
        seeds = [json.loads(p.meta.read_text())["seed"] for p in paths]
        assert len(set(seeds)) == SWEEP_REPLICATES
        assert seeds == spawn_seeds(cfg.seed, SWEEP_REPLICATES)
        assert elapsed < SWEEP_BUDGET_S, (
            f"{SWEEP_REPLICATES}-replicate corridor_10km sweep took {elapsed:.0f} s "
            f"with {SWEEP_PROCS} workers (budget {SWEEP_BUDGET_S:.0f} s)"
        )
