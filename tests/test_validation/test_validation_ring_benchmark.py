"""Tests for validation.ring_benchmark on synthetic ring fields.

The checks mirror tests/test_microsim/test_microsim_ring_gate.py; here they
are exercised without SUMO on a planted backward-moving speed dip (and its
absence), plus the orchestration of ``evaluate_ring_benchmark`` with a fake
runner that writes contract-shaped run directories.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.units import ms_to_kmh
from validation.ring_benchmark import (
    DAMPENING_CONTROLLER,
    DRIFT_BAND_KMH,
    SIGMA_V_MIN_MS,
    SINGLE_AV_PENETRATION,
    TAIL_S,
    WARMUP_S,
    RingSlices,
    dampening_checks,
    emergence_checks,
    evaluate_ring_benchmark,
    jam_drift_kmh,
    min_speed_after,
    replicate_ci,
    ring_slices,
    sigma_v_tail,
)

C = 230.0
N = 22
DT = 0.5
T_END = 600.0
JAM_SPEED_MS = -4.0  # backward, -14.4 km/h: inside the empirical band


def _synthetic(wave: bool, seed: int = 0) -> pd.DataFrame:
    """22 vehicles at fixed ring positions; a Gaussian speed dip that moves
    backward at JAM_SPEED_MS (wave=True) or near-uniform speeds (False)."""
    rng = np.random.default_rng(seed)
    t = np.arange(0.0, T_END + DT / 2, DT)
    x0 = np.arange(N) * C / N
    frames = []
    for i in range(N):
        if wave:
            jam = (100.0 + JAM_SPEED_MS * t) % C
            d = (x0[i] - jam + C / 2.0) % C - C / 2.0
            v = 6.0 - 5.9 * np.exp(-((d / 20.0) ** 2))
        else:
            v = 6.0 + 0.05 * rng.standard_normal(len(t))
        frames.append(pd.DataFrame({"t": t, "veh_id": f"v{i:02d}", "x": x0[i], "v": v}))
    return pd.concat(frames, ignore_index=True)


def _meta(*, seeded: bool = False, av_ids: list[str] | None = None, damped: bool = False) -> dict:
    av = av_ids if av_ids is not None else (["v00"] if damped else [])
    return {
        "seeded": seeded,
        "config": {"perturbation": {"t_s": 1.0} if seeded else None},
        "av_ids": av,
        "complied_ids": list(av),
        "controller": DAMPENING_CONTROLLER if damped else None,
    }


class TestPureChecks:
    def test_slices_shape(self):
        s = ring_slices(_synthetic(True))
        assert isinstance(s, RingSlices)
        assert s.v.shape == (int(T_END / DT) + 1, N) and s.x.shape == s.v.shape
        assert np.all(np.diff(s.t) > 0)

    def test_sigma_v_matches_definition(self):
        s = ring_slices(_synthetic(True))
        last = s.t > s.t.max() - TAIL_S
        expected = float(np.mean(np.std(s.v[last], axis=1)))
        assert sigma_v_tail(s) == pytest.approx(expected)
        assert sigma_v_tail(s) > SIGMA_V_MIN_MS
        assert sigma_v_tail(ring_slices(_synthetic(False))) < 0.2

    def test_min_speed_after_warmup(self):
        s = ring_slices(_synthetic(True))
        assert min_speed_after(s, WARMUP_S) < 3.0
        assert min_speed_after(ring_slices(_synthetic(False)), WARMUP_S) > 5.0

    def test_jam_drift_recovers_planted_speed(self):
        drift = jam_drift_kmh(ring_slices(_synthetic(True)))
        assert drift == pytest.approx(ms_to_kmh(JAM_SPEED_MS), abs=1.0)
        assert DRIFT_BAND_KMH[0] <= drift <= DRIFT_BAND_KMH[1]

    def test_emergence_checks_pass_on_wave(self):
        e = emergence_checks(ring_slices(_synthetic(True)), _meta())
        assert e.passed and e.sigma_v_ok and e.deep_slowdown_ok and e.backward_ok
        assert e.unseeded and e.no_avs

    def test_emergence_fails_when_seeded_or_with_avs_or_without_wave(self):
        wave = ring_slices(_synthetic(True))
        assert not emergence_checks(wave, _meta(seeded=True)).passed
        assert not emergence_checks(wave, _meta(av_ids=["v03"])).passed
        flat = emergence_checks(ring_slices(_synthetic(False)), _meta())
        assert not flat.passed and not flat.sigma_v_ok and not flat.deep_slowdown_ok

    def test_dampening_checks(self):
        base = ring_slices(_synthetic(True))
        damped = ring_slices(_synthetic(False))
        d = dampening_checks(base, damped, _meta(damped=True))
        assert d.passed and d.single_compliant_av and d.sigma_v_ok and d.min_speed_ok
        assert d.still_flows_ok
        assert d.reduction_frac == pytest.approx(1.0 - d.sigma_v_damped_ms / d.sigma_v_baseline_ms)
        # Wrong AV bookkeeping fails even when the physics passes.
        assert not dampening_checks(base, damped, _meta(damped=False)).passed
        two = _meta(damped=True)
        two["av_ids"] = ["v00", "v01"]
        assert not dampening_checks(base, damped, two).passed
        # No dampening at all fails on the sigma check.
        same = dampening_checks(base, base, _meta(damped=True))
        assert not same.passed and not same.sigma_v_ok

    def test_replicate_ci(self):
        ci = replicate_ci([1.0, 2.0, 3.0])
        assert ci.mean == pytest.approx(2.0) and ci.n == 3 and ci.lo95 < 2.0 < ci.hi95
        one = replicate_ci([1.0])
        assert one.n == 1 and math.isnan(one.lo95)
        assert replicate_ci([]).n == 0


def _ring_cfg() -> ScenarioConfig:
    return ScenarioConfig.model_validate(
        {
            "name": "ring_sugiyama",
            "network": {"kind": "ring", "circumference_m": C, "n_vehicles": N},
            "sim": {"duration_s": T_END},
            "seed": 42,
        }
    )


class _FakePaths:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.trajectories = run_dir / "trajectories.parquet"
        self.meta = run_dir / "meta.json"


def _fake_run(cfg: ScenarioConfig, seed: int, out_dir: Path) -> _FakePaths:
    """Contract-shaped run directory: waves for the baseline arm, calm for
    the FollowerStopper arm."""
    damped = cfg.av.controller is not None
    run_dir = Path(out_dir) / config_hash(cfg) / str(seed)
    run_dir.mkdir(parents=True, exist_ok=True)
    _synthetic(not damped, seed=seed).to_parquet(run_dir / "trajectories.parquet")
    meta = _meta(damped=damped)
    meta["config_hash"] = config_hash(cfg)
    (run_dir / "meta.json").write_text(json.dumps(meta))
    return _FakePaths(run_dir)


class TestEvaluateRingBenchmark:
    def test_orchestration_with_fake_runner(self, tmp_path: Path):
        seen: list[tuple[float, int]] = []

        def run_fn(cfg: ScenarioConfig, seed: int, out_dir: Path) -> _FakePaths:
            seen.append((cfg.av.penetration, seed))
            return _fake_run(cfg, seed, out_dir)

        res = evaluate_ring_benchmark(
            [11, 12], tmp_path, run_fn=run_fn, load_fn=lambda name: _ring_cfg()
        )
        assert seen == [
            (0.0, 11),
            (SINGLE_AV_PENETRATION, 11),
            (0.0, 12),
            (SINGLE_AV_PENETRATION, 12),
        ]
        assert res.seeds == (11, 12)
        assert res.emergence_passed and res.dampening_passed
        assert res.config_hash_baseline != res.config_hash_damped
        assert len(res.per_seed) == 2
        assert all(Path(r.run_dir_baseline).is_dir() for r in res.per_seed)

        d = res.to_dict()
        assert d["emergence"]["passed"] and d["emergence"]["n_pass"] == 2
        assert d["dampening"]["passed"] and d["dampening"]["n_seeds"] == 2
        assert d["emergence"]["sigma_v_ms"]["n"] == 2
        assert d["emergence"]["drift_kmh"]["mean"] == pytest.approx(
            ms_to_kmh(JAM_SPEED_MS), abs=1.0
        )
        assert d["thresholds"]["sigma_v_min_ms"] == SIGMA_V_MIN_MS
        assert d["emergence"]["sigma_v_ms"]["underpowered"]  # 2 < 20 seeds
        json.dumps(d)  # JSON-serialisable

    def test_one_failing_seed_fails_the_benchmark(self, tmp_path: Path):
        def run_fn(cfg: ScenarioConfig, seed: int, out_dir: Path) -> _FakePaths:
            paths = _fake_run(cfg, seed, out_dir)
            if seed == 2 and cfg.av.controller is None:
                # Overwrite the baseline of seed 2 with a calm field: no emergence.
                _synthetic(False, seed=seed).to_parquet(paths.trajectories)
            return paths

        res = evaluate_ring_benchmark(
            [1, 2], tmp_path, run_fn=run_fn, load_fn=lambda name: _ring_cfg()
        )
        assert not res.emergence_passed
        assert [r.emergence.passed for r in res.per_seed] == [True, False]
        assert res.to_dict()["emergence"]["n_pass"] == 1

    def test_no_seeds_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError, match="at least one seed"):
            evaluate_ring_benchmark([], tmp_path, run_fn=_fake_run, load_fn=lambda n: _ring_cfg())
