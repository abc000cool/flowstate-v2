"""Demand-fitter tests on a fast surrogate simulator (calibration.demand).

The simulate function is injected (docs: the microscopic tier provides the
real one), so a toy surrogate with a known ground-truth inflow lets us assert
convergence of the iterative proportional scaling to the GEH criterion.

The second half covers ``fit_multipliers`` (compass search over named scalar
multipliers) on a quadratic objective with a known minimum, and the
scenario transformation and objective plumbing of
``scripts/i24_fit_boundary_ramps.py`` with a fake simulator that returns
constructed segment speeds — no SUMO run anywhere here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable
from math import sqrt
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from calibration.demand import (
    EvalRecord,
    EvalResult,
    MultiplierSpec,
    fit_inflow,
    fit_inflow_profile,
    fit_multipliers,
    geh,
)
from flowstate_core.artifacts import DemandProfile
from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.units import veh_s_to_veh_h

CREATED = "2026-08-29T00:00:00+00:00"

TRUTH_STEPS = [(0.0, 0.5), (900.0, 0.9), (1800.0, 0.4)]
BIN_S = 300.0
HORIZON_S = 2700.0


def _profile(steps: list[tuple[float, float]]) -> DemandProfile:
    return DemandProfile(created_at=CREATED, source="test", data_hash="", steps=steps)


def _surrogate(profile: DemandProfile) -> pd.DataFrame:
    """Toy link 'simulator': attenuated inflow plus a base flow, per bin."""
    starts = np.arange(0.0, HORIZON_S, BIN_S)
    flows = [0.92 * profile.inflow_at(t0 + BIN_S / 2.0) + 0.01 for t0 in starts]
    return pd.DataFrame({"t_start_s": starts, "t_end_s": starts + BIN_S, "flow_veh_s": flows})


def _observed() -> pd.DataFrame:
    return _surrogate(_profile(list(TRUTH_STEPS)))


class TestGeh:
    def test_hand_computed(self) -> None:
        # GEH = sqrt(2 (m-c)^2 / (m+c)) on hourly volumes (FHWA-HOP-18-036).
        assert geh(1000.0, 900.0) == pytest.approx(sqrt(2 * 100.0**2 / 1900.0), rel=1e-12)

    def test_zero_volumes(self) -> None:
        assert geh(0.0, 0.0) == 0.0

    def test_symmetry(self) -> None:
        assert geh(700.0, 500.0) == pytest.approx(geh(500.0, 700.0), rel=1e-12)


class TestFitInflow:
    def test_converges_to_known_truth(self) -> None:
        initial = _profile([(0.0, 0.6), (900.0, 0.6), (1800.0, 0.6)])
        fitted = fit_inflow(
            _observed(),
            initial,
            _surrogate,
            created_at=CREATED,
            source="toy surrogate",
            geh_threshold=1.0,  # much tighter than the FHWA 5 to force recovery
            geh_pass_frac=1.0,
            max_iters=30,
        )
        assert fitted.geh_vs_counts is not None
        assert fitted.geh_vs_counts < 1.0  # worst-bin GEH of the returned profile
        for (t_fit, q_fit), (t_true, q_true) in zip(fitted.steps, TRUTH_STEPS, strict=True):
            assert t_fit == t_true
            assert q_fit == pytest.approx(q_true, rel=0.02)

    def test_meets_fhwa_criterion_shape(self) -> None:
        initial = _profile([(0.0, 0.45), (900.0, 1.1), (1800.0, 0.5)])
        fitted = fit_inflow(
            _observed(),
            initial,
            _surrogate,
            created_at=CREATED,
            source="toy surrogate",
            geh_threshold=5.0,
            geh_pass_frac=0.85,
            max_iters=25,
        )
        sim = _surrogate(fitted)
        obs = _observed()
        gehs = [
            geh(veh_s_to_veh_h(m), veh_s_to_veh_h(c))
            for m, c in zip(sim["flow_veh_s"], obs["flow_veh_s"], strict=True)
        ]
        assert np.mean(np.array(gehs) < 5.0) >= 0.85

    def test_zero_iters_returns_scored_initial(self) -> None:
        initial = _profile([(0.0, 0.6), (900.0, 0.6), (1800.0, 0.6)])
        fitted = fit_inflow(
            _observed(),
            initial,
            _surrogate,
            created_at=CREATED,
            source="toy surrogate",
            geh_threshold=0.001,  # unreachable -> no early stop
            geh_pass_frac=1.0,
            max_iters=0,
        )
        # No scaling happened; the reported GEH describes the initial profile.
        assert [q for _, q in fitted.steps] == [0.6, 0.6, 0.6]
        assert fitted.geh_vs_counts is not None and fitted.geh_vs_counts > 0.001

    def test_scale_damping_is_bounded(self) -> None:
        initial = _profile([(0.0, 0.001), (900.0, 0.001), (1800.0, 0.001)])
        fitted = fit_inflow(
            _observed(),
            initial,
            _surrogate,
            created_at=CREATED,
            source="toy surrogate",
            max_iters=1,
            max_scale_step=2.0,
            geh_threshold=0.001,
            geh_pass_frac=1.0,
        )
        # One damped iteration can at most double each step.
        for (_, q_fit), (_, q0) in zip(fitted.steps, initial.steps, strict=True):
            assert q_fit <= 2.0 * q0 + 1e-12

    def test_mismatched_bins_raise(self) -> None:
        def bad_sim(profile: DemandProfile) -> pd.DataFrame:
            return pd.DataFrame({"t_start_s": [0.0], "t_end_s": [1.0], "flow_veh_s": [0.1]})

        with pytest.raises(ValueError, match="bins"):
            fit_inflow(
                _observed(),
                _profile(list(TRUTH_STEPS)),
                bad_sim,
                created_at=CREATED,
                source="toy",
            )

    def test_missing_columns_raise(self) -> None:
        with pytest.raises(ValueError, match="missing column"):
            fit_inflow(
                pd.DataFrame({"t_start_s": [0.0]}),
                _profile(list(TRUTH_STEPS)),
                _surrogate,
                created_at=CREATED,
                source="toy",
            )


def _corridor_cfg(steps: list[tuple[float, float]]) -> ScenarioConfig:
    return ScenarioConfig.model_validate(
        {
            "name": "demand_fit_scenario",
            "network": {"kind": "corridor", "length_m": 3000.0, "lanes": 1, "inflow": steps},
            "sim": {"duration_s": HORIZON_S},
            "seed": 7,
        }
    )


class TestScenarioForm:
    """``fit_inflow(scenario, counts, ...)`` — the CLAUDE.md §6.3 entry point."""

    def test_scenario_inflow_is_the_starting_profile(self) -> None:
        calls: list[list[tuple[float, float]]] = []

        def spy(profile: DemandProfile) -> pd.DataFrame:
            calls.append(list(profile.steps))
            return _surrogate(profile)

        cfg = _corridor_cfg([(0.0, 0.6), (900.0, 0.6), (1800.0, 0.6)])
        fitted = fit_inflow(
            cfg,
            _observed(),
            spy,
            created_at=CREATED,
            source="toy surrogate via scenario form",
            geh_threshold=1.0,
            geh_pass_frac=1.0,
            max_iters=30,
        )
        assert calls[0] == [(0.0, 0.6), (900.0, 0.6), (1800.0, 0.6)]
        for (t_fit, q_fit), (t_true, q_true) in zip(fitted.steps, TRUTH_STEPS, strict=True):
            assert t_fit == t_true
            assert q_fit == pytest.approx(q_true, rel=0.02)
        assert fitted.source == "toy surrogate via scenario form"

    def test_ring_scenario_has_no_inflow(self) -> None:
        ring = ScenarioConfig.model_validate(
            {
                "name": "ring",
                "network": {"kind": "ring", "circumference_m": 230.0, "n_vehicles": 22},
                "sim": {"duration_s": 60.0},
            }
        )
        with pytest.raises(ValueError, match="no inflow"):
            fit_inflow(ring, _observed(), _surrogate, created_at=CREATED, source="toy")

    def test_missing_microsim_is_a_clear_import_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A None entry in sys.modules makes the import raise ImportError.
        monkeypatch.setitem(sys.modules, "microsim.demand_adapter", None)
        with pytest.raises(ImportError, match="microsim"):
            fit_inflow(_corridor_cfg([(0.0, 0.5)]), _observed(), created_at=CREATED, source="toy")

    def test_legacy_form_requires_simulate_fn(self) -> None:
        with pytest.raises(TypeError, match="simulate_fn"):
            fit_inflow(_observed(), _profile(list(TRUTH_STEPS)), created_at=CREATED, source="t")

    def test_mixed_forms_rejected(self) -> None:
        with pytest.raises(TypeError):
            fit_inflow(_observed(), _observed(), _surrogate, created_at=CREATED, source="t")

    def test_fit_inflow_profile_is_the_shared_core(self) -> None:
        initial = _profile([(0.0, 0.6), (900.0, 0.6), (1800.0, 0.6)])
        kwargs = dict(created_at=CREATED, source="toy", geh_threshold=1.0, geh_pass_frac=1.0)
        a = fit_inflow_profile(_observed(), initial, _surrogate, **kwargs)
        b = fit_inflow(_observed(), initial, _surrogate, **kwargs)
        assert a.steps == b.steps and a.geh_vs_counts == b.geh_vs_counts

    def test_malformed_bins_rejected(self) -> None:
        bad = pd.DataFrame({"t_start_s": [0.0], "t_end_s": [0.0], "flow_veh_s": [0.1]})
        with pytest.raises(ValueError, match="t_end_s > t_start_s"):
            fit_inflow(_corridor_cfg([(0.0, 0.5)]), bad, _surrogate, created_at=CREATED, source="t")


# --- fit_multipliers: compass search over named scalar multipliers -------------


def _quadratic(target: dict[str, float]) -> Callable[[dict[str, float]], EvalResult]:
    """Separable quadratic with a known minimum; diagnostics carry a 'held-out' value."""

    def evaluate(values: dict[str, float]) -> EvalResult:
        obj = sum((values[n] - t) ** 2 for n, t in target.items())
        return EvalResult(objective=obj, diagnostics={"held_out": 2.0 * obj, "n": len(values)})

    return evaluate


def _counting(
    fn: Callable[[dict[str, float]], EvalResult],
) -> tuple[Callable[..., EvalResult], list]:
    calls: list[dict[str, float]] = []

    def wrapped(values: dict[str, float]) -> EvalResult:
        calls.append(dict(values))
        return fn(values)

    return wrapped, calls


AB = (MultiplierSpec("a", 1.0, 0.5, 1.5), MultiplierSpec("b", 1.0, 0.5, 1.5))


class TestFitMultipliers:
    def test_converges_to_on_grid_minimum_in_one_round(self) -> None:
        # Both coordinates improve in round 1; the combined point is the minimum.
        fit = fit_multipliers(AB, _quadratic({"a": 1.25, "b": 0.75}), rounds=3)
        assert fit.best == {"a": 1.25, "b": 0.75}
        assert fit.objective == 0.0
        assert fit.diagnostics == {"held_out": 0.0, "n": 2}
        assert fit.rounds[0].moved and fit.rounds[0].best == fit.best
        # Round 1: 4 axis candidates + the combined point, all fresh.
        assert fit.rounds[0].n_fresh == 5
        assert [r.round for r in fit.log[:6]] == [0, 1, 1, 1, 1, 1]

    def test_converges_to_off_grid_minimum_within_tol(self) -> None:
        tol = 1e-3
        fit = fit_multipliers(
            (MultiplierSpec("a", 1.0, 0.5, 1.5),),
            _quadratic({"a": 1.1}),
            rounds=40,
            tol=tol,
        )
        assert fit.converged
        assert abs(fit.best["a"] - 1.1) < 2.0 * tol
        assert max(fit.step.values()) < tol

    def test_respects_bounds(self) -> None:
        fit = fit_multipliers(AB, _quadratic({"a": 2.0, "b": 0.0}), rounds=8)
        assert fit.best == {"a": 1.5, "b": 0.5}
        for rec in fit.log:
            assert 0.5 <= rec.values["a"] <= 1.5 and 0.5 <= rec.values["b"] <= 1.5

    def test_memoizes_every_point(self) -> None:
        evaluate, calls = _counting(_quadratic({"a": 1.25, "b": 1.25}))
        fit = fit_multipliers(AB, evaluate, rounds=4)
        keys = {(round(c["a"], 9), round(c["b"], 9)) for c in calls}
        assert len(keys) == len(calls) == len(fit.log) == fit.n_evaluations
        # Round 2 re-proposes the round-1 incumbent as a neighbour: answered from the cache.
        assert fit.n_cached > 0
        assert sum(r.n_cached for r in fit.rounds) == fit.n_cached

    def test_resumes_from_prior_log_without_evaluating(self) -> None:
        first = fit_multipliers(AB, _quadratic({"a": 1.25, "b": 0.75}), rounds=3)
        evaluate, calls = _counting(_quadratic({"a": 1.25, "b": 0.75}))
        again = fit_multipliers(AB, evaluate, rounds=3, prior=first.log)
        assert calls == [] and again.n_evaluations == 0
        assert again.best == first.best and again.objective == first.objective
        assert again.log[: len(first.log)] == first.log

    def test_tie_breaks_toward_initial(self) -> None:
        # Flat at and above the initial value: 1.0, 1.25, 1.5 all score 0.
        def plateau(values: dict[str, float]) -> EvalResult:
            return EvalResult(objective=0.0 if values["a"] >= 1.0 else 1.0)

        fit = fit_multipliers((MultiplierSpec("a", 1.0, 0.5, 1.5),), plateau, rounds=5)
        assert fit.best == {"a": 1.0}
        assert not any(r.moved for r in fit.rounds)

    def test_constant_objective_stays_at_initial_and_converges(self) -> None:
        fit = fit_multipliers(AB, lambda v: EvalResult(0.5), rounds=50, step=0.25, tol=0.2)
        assert fit.best == {"a": 1.0, "b": 1.0}
        assert fit.converged and len(fit.rounds) == 1  # 0.25 -> 0.125 < tol after one round

    def test_candidates_go_through_map_fn_in_batches(self) -> None:
        batches: list[int] = []

        def map_fn(fn, points):  # type: ignore[no-untyped-def]
            batches.append(len(points))
            return [fn(p) for p in points]

        fit_multipliers(AB, _quadratic({"a": 1.25, "b": 0.75}), rounds=1, map_fn=map_fn)
        # Initial point, then the four axis candidates at once, then the combined point.
        assert batches == [1, 4, 1]

    def test_grid_five_places_half_steps(self) -> None:
        evaluate, calls = _counting(_quadratic({"a": 1.0}))
        fit_multipliers((MultiplierSpec("a", 1.0, 0.5, 1.5),), evaluate, rounds=1, grid=5)
        assert sorted(c["a"] for c in calls) == [0.75, 0.875, 1.0, 1.125, 1.25]

    def test_zero_rounds_scores_only_the_initial_point(self) -> None:
        fit = fit_multipliers(AB, _quadratic({"a": 1.25, "b": 0.75}), rounds=0)
        assert len(fit.log) == 1 and fit.best == {"a": 1.0, "b": 1.0}
        assert fit.rounds == []

    def test_on_round_sees_partial_results(self) -> None:
        seen: list[int] = []
        fit = fit_multipliers(
            AB,
            _quadratic({"a": 1.25, "b": 0.75}),
            rounds=3,
            on_round=lambda f: seen.append(len(f.log)),
        )
        assert len(seen) == len(fit.rounds)
        assert seen[-1] == len(fit.log)

    def test_non_finite_objective_is_never_selected(self) -> None:
        def evaluate(values: dict[str, float]) -> EvalResult:
            a = values["a"]
            return EvalResult(objective=float("nan") if a > 1.0 else (a - 0.75) ** 2)

        fit = fit_multipliers((MultiplierSpec("a", 1.0, 0.5, 1.5),), evaluate, rounds=2)
        assert fit.best == {"a": 0.75}
        assert any(not np.isfinite(rec.objective) for rec in fit.log)

    @pytest.mark.parametrize(
        "kwargs, match",
        [
            ({"grid": 4}, "odd"),
            ({"grid": 1}, "odd"),
            ({"rounds": -1}, "rounds"),
            ({"shrink": 1.0}, "shrink"),
            ({"step": 0.0}, "steps must be > 0"),
            ({"step": {"a": 0.1}}, "lacks"),
        ],
    )
    def test_rejects_bad_settings(self, kwargs: dict, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            fit_multipliers(AB, _quadratic({"a": 1.0, "b": 1.0}), **kwargs)

    def test_rejects_duplicate_names_and_bad_specs(self) -> None:
        with pytest.raises(ValueError, match="unique"):
            fit_multipliers((AB[0], AB[0]), _quadratic({"a": 1.0}))
        with pytest.raises(ValueError, match="outside"):
            MultiplierSpec("a", 2.0, 0.5, 1.5)
        with pytest.raises(ValueError, match="lower"):
            MultiplierSpec("a", 1.0, 1.5, 0.5)
        with pytest.raises(ValueError, match="prior record names"):
            fit_multipliers(
                AB,
                _quadratic({"a": 1.0, "b": 1.0}),
                prior=[EvalRecord(values={"a": 1.0}, objective=0.0, diagnostics={}, round=0)],
            )


# --- scripts/i24_fit_boundary_ramps.py: scenario transformation and objective plumbing


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "i24_fit_boundary_ramps.py"


@pytest.fixture(scope="module")
def ramps_script() -> Any:
    if not SCRIPT.is_file():
        pytest.skip(f"{SCRIPT} not present")
    spec = importlib.util.spec_from_file_location("i24_fit_boundary_ramps", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _ramps_base(m: Any) -> dict[str, Any]:
    return {
        "name": "base",
        "network": {
            "kind": "osm",
            "boundary": {
                "kind": "speed_schedule",
                "steps": [[0.0, 10.0], [30.0, 5.0], [60.0, 20.0]],
            },
            "ramps": [
                {"kind": "on", "name": m.OH_ON, "inflow": [[0.0, 0.2], [900.0, 0.4]]},
                {"kind": "off", "name": m.HH_OFF, "exit_fraction": [[0.0, 0.1], [900.0, 0.8]]},
                {"kind": "on", "name": m.HH_ON, "inflow": [[0.0, 0.3]]},
                {"kind": "off", "name": m.BELL_OFF, "exit_fraction": [[0.0, 0.05], [900.0, 0.9]]},
            ],
        },
        "sim": {"duration_s": 7800.0, "warmup_s": 600.0},
    }


def _target_profile(raw: dict[str, Any], m: Any, name: str) -> list:
    if name == m.LC_ASSERTIVE_MULTIPLIER:
        return [[0.0, raw.get("fleet", {}).get("lc_assertive", 1.0)]]
    if name == m.BOUNDARY_MULTIPLIER:
        return raw["network"]["boundary"]["steps"]
    ramp_name, field_name = m.RAMP_TARGETS[name]
    return next(r for r in raw["network"]["ramps"] if r["name"] == ramp_name)[field_name]


class TestApplyMultipliers:
    def test_each_multiplier_touches_only_its_profile(self, ramps_script: Any) -> None:
        m = ramps_script
        base = _ramps_base(m)
        for name in m.MULTIPLIER_NAMES:
            out = m.apply_multipliers(base, {name: 1.2})
            assert out["name"] == m.SCENARIO_NAME
            for other in m.MULTIPLIER_NAMES:
                if other != name:
                    assert _target_profile(out, m, other) == _target_profile(base, m, other)
            # times unchanged, values scaled (before clipping)
            for (t0, v0), (t1, v1) in zip(
                _target_profile(base, m, name), _target_profile(out, m, name), strict=True
            ):
                assert t0 == t1
                assert v1 == pytest.approx(min(v0 * 1.2, 1.0 if "exit" in name else 20.0), rel=1e-6)
        assert base == _ramps_base(m)  # never mutated

    def test_identity_at_one(self, ramps_script: Any) -> None:
        m = ramps_script
        base = _ramps_base(m)
        out = m.apply_multipliers(base, dict.fromkeys(m.MULTIPLIER_NAMES, 1.0))
        out["name"] = base["name"]
        assert out == base

    def test_exit_fractions_clip_to_unit_interval(self, ramps_script: Any) -> None:
        m = ramps_script
        out = m.apply_multipliers(_ramps_base(m), {"hh_exit": 1.5, "bell_exit": 0.5})
        assert _target_profile(out, m, "hh_exit") == [[0.0, 0.15], [900.0, 1.0]]
        assert _target_profile(out, m, "bell_exit") == [[0.0, 0.025], [900.0, 0.45]]

    def test_boundary_speed_clips_to_measured_range(self, ramps_script: Any) -> None:
        m = ramps_script
        up = m.apply_multipliers(_ramps_base(m), {"boundary_speed": 1.5})
        assert _target_profile(up, m, "boundary_speed") == [[0.0, 15.0], [30.0, 7.5], [60.0, 20.0]]
        down = m.apply_multipliers(_ramps_base(m), {"boundary_speed": 0.5})
        assert _target_profile(down, m, "boundary_speed") == [[0.0, 5.0], [30.0, 5.0], [60.0, 10.0]]

    def test_unknown_multiplier_and_missing_ramp_raise(self, ramps_script: Any) -> None:
        m = ramps_script
        with pytest.raises(ValueError, match="unknown multipliers"):
            m.apply_multipliers(_ramps_base(m), {"mainline": 1.0})
        base = _ramps_base(m)
        base["network"]["ramps"] = base["network"]["ramps"][1:]
        with pytest.raises(ValueError, match="Old Hickory"):
            m.apply_multipliers(base, {"oh_ramp": 1.0})
        base["network"].pop("boundary")
        with pytest.raises(ValueError, match="boundary"):
            m.apply_multipliers(base, {"boundary_speed": 1.0})

    def test_real_scenario_validates_at_the_bounds(self, ramps_script: Any) -> None:
        m = ramps_script
        if not m.BASE_YAML.is_file():
            pytest.skip("base scenario not present")
        base = m.base_scenario()
        hashes = set()
        for level in (0.5, 1.0, 1.5):
            raw = m.apply_multipliers(base, dict.fromkeys(m.MULTIPLIER_NAMES, level))
            cfg = ScenarioConfig.model_validate(raw)
            hashes.add(config_hash(cfg))
            speeds = [v for _, v in raw["network"]["boundary"]["steps"]]
            lo, hi = (
                min(v for _, v in base["network"]["boundary"]["steps"]),
                max(v for _, v in base["network"]["boundary"]["steps"]),
            )
            assert lo <= min(speeds) and max(speeds) <= hi
        assert len(hashes) == 3
        smoke = m.base_scenario(smoke=True)
        assert smoke["sim"]["duration_s"] <= 600.0
        ScenarioConfig.model_validate(smoke)


def _fake_simulate(seg: np.ndarray, seen: list) -> Callable[..., Any]:
    def simulate(raw: dict[str, Any], seed: int, n_win: int) -> Any:
        seen.append((raw, seed, n_win))
        import i24_fit_boundary_ramps as m

        return m.SimResult(
            segment_speeds_ms=seg,
            inserted_fraction=0.9,
            config_hash="deadbeef0000",
            ramps=[{"name": m.OH_ON, "kind": "on", "n_planned": 10, "n_departed": 9}],
            wall_s=1.0,
        )

    return simulate


class TestObjectivePlumbing:
    def test_objective_is_first_hour_and_second_hour_is_held_out(self, ramps_script: Any) -> None:
        m = ramps_script
        obs = 10.0 + np.arange(240, dtype=float).reshape(24, 10) / 10.0
        seg = obs.copy()
        seg[list(m.TRAIN_WINDOWS)] *= 1.10
        seg[list(m.TEST_WINDOWS)] *= 0.80
        seen: list = []
        values = {
            "oh_ramp": 1.2,
            "hh_ramp": 0.9,
            "hh_exit": 1.0,
            "bell_exit": 1.0,
            "boundary_speed": 1.1,
        }
        res = m.evaluate_point(
            values, seed=123, simulate=_fake_simulate(seg, seen), base=_ramps_base(m), observed=obs
        )
        assert res.objective == pytest.approx(0.10, rel=1e-9)
        assert res.diagnostics["rmspe_test"] == pytest.approx(0.20, rel=1e-9)
        assert res.diagnostics["rmspe_all"] == pytest.approx(np.sqrt((0.01 + 0.04) / 2.0), rel=1e-9)
        assert res.diagnostics["inserted_fraction"] == 0.9
        assert res.diagnostics["config_hash"] == "deadbeef0000"
        assert res.diagnostics["ramps"][0]["n_departed"] == 9
        raw, seed, n_win = seen[0]
        assert seed == 123 and n_win == 24
        assert _target_profile(raw, m, "oh_ramp") == [
            [0.0, pytest.approx(0.24)],
            [900.0, pytest.approx(0.48)],
        ]
        assert _target_profile(raw, m, "hh_ramp") == [[0.0, pytest.approx(0.27)]]

    def test_nan_bins_are_masked_and_empty_windows_give_nan(self, ramps_script: Any) -> None:
        m = ramps_script
        obs = np.full((24, 10), 20.0)
        seg = obs * 1.05
        seg[0, 3] = np.nan  # one empty train bin
        seg[list(m.TEST_WINDOWS)] = np.nan  # a smoke-length run never reaches the second hour
        res = m.evaluate_point(
            {"oh_ramp": 1.0},
            seed=1,
            simulate=_fake_simulate(seg, []),
            base=_ramps_base(m),
            observed=obs,
        )
        assert res.objective == pytest.approx(0.05, rel=1e-9)
        assert np.isnan(res.diagnostics["rmspe_test"])
        assert res.diagnostics["rmspe_all"] == pytest.approx(0.05, rel=1e-9)

    def test_shape_mismatch_raises(self, ramps_script: Any) -> None:
        m = ramps_script
        obs = np.full((24, 10), 20.0)
        with pytest.raises(ValueError, match="segment speeds"):
            m.evaluate_point(
                {},
                seed=1,
                simulate=_fake_simulate(np.ones((12, 10)), []),
                base=_ramps_base(m),
                observed=obs,
            )

    def test_study_frame_uses_the_scenario_warmup(self, ramps_script: Any, tmp_path: Path) -> None:
        m = ramps_script
        df = pd.DataFrame(
            {
                "t": [0.0, 100.0, 700.0, 1300.0],
                "x": [2256.0, 3000.0, 4000.0, 5000.0],
                "v": [1.0, 2.0, 3.0, 4.0],
            }
        )
        df.to_parquet(tmp_path / "trajectories.parquet")
        a, b = 2256.2, 0.98
        generic = m.study_frame(tmp_path, a, b, 60.0)
        assert generic["t"].tolist() == [40.0, 640.0, 1240.0]
        assert generic["x"].tolist() == pytest.approx(
            [(3000.0 - a) / b, (4000.0 - a) / b, (5000.0 - a) / b]
        )
        replica = m.study_frame(tmp_path, a, b, m.WARMUP_S)  # delegates to i24_validate._sim_frame
        assert replica["t"].tolist() == [100.0, 700.0]
        assert replica["v"].tolist() == [3.0, 4.0]

    def test_artifact_rows_round_trip_as_prior(self, ramps_script: Any, tmp_path: Path) -> None:
        m = ramps_script
        target = dict.fromkeys(m.MULTIPLIER_NAMES, 1.25)
        fit = fit_multipliers(m.MULTIPLIERS, _quadratic(target), rounds=1)
        prov = {"base_config_hash": "abc", "seed": 7, "smoke": False, "observed_data_hash": "x"}
        art = m.artifact(fit, prov, "2026-09-03T00:00:00Z")
        assert art["best"]["values"] == fit.best and art["best"]["rmspe_train"] == fit.objective
        assert (
            len(art["log"]) == len(fit.log) and art["rounds"][0]["n_fresh"] == fit.rounds[0].n_fresh
        )
        out = tmp_path / "fit.json"
        out.write_text(json.dumps(art))
        prior = m.load_prior(out, prov)
        assert [p.values for p in prior] == [r.values for r in fit.log]
        assert [p.objective for p in prior] == [r.objective for r in fit.log]
        assert m.load_prior(out, {**prov, "seed": 8}) == []
        assert m.load_prior(tmp_path / "missing.json", prov) == []
        resumed = fit_multipliers(m.MULTIPLIERS, _quadratic(target), rounds=1, prior=prior)
        assert resumed.n_evaluations == 0 and resumed.best == fit.best

    def test_scenario_header_states_fit_and_holdout(self, ramps_script: Any) -> None:
        m = ramps_script
        fit = fit_multipliers(
            m.MULTIPLIERS, _quadratic(dict.fromkeys(m.MULTIPLIER_NAMES, 1.0)), rounds=0
        )
        fit.diagnostics.update({"rmspe_test": 0.4, "inserted_fraction": 0.95})
        header = m.scenario_header(fit.best, fit, "0123456789ab")
        assert header.startswith("# i24_replica_speedcal_ramps")
        assert "windows 0-11" in header and "held out" in header and "windows 12-23" in header
        assert "0123456789ab" in header and "seeded=False" in header
        assert all(line.startswith("#") for line in header.strip().splitlines())


def test_apply_multipliers_lc_assertive_sets_fleet_field():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import i24_fit_boundary_ramps as m

    base = {
        "name": "x",
        "fleet": {"lc_assertive": 1.0},
        "network": {"ramps": [], "boundary": {"steps": [[0.0, 10.0]]}},
    }
    out = m.apply_multipliers(base, {"lc_assertive": 1.5})
    assert out["fleet"]["lc_assertive"] == 1.5
    assert base["fleet"]["lc_assertive"] == 1.0
    assert m.apply_multipliers(base, {})["fleet"]["lc_assertive"] == 1.0
