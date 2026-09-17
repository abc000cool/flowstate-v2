"""Helpers behind the lane-5 coverage verdict (docs/MERGE_ROUND6_PLAN.md §2.3).

``scripts/i24_coverage_lane5.py`` concludes that the gap-mixture estimator of
``calibration.coverage`` cannot supply a tracking coverage for the Old Hickory
acceleration lane, because merging out of an auxiliary lane and failing to
track a vehicle are two thinnings of the same point process and eq. (G) fits
only their product. That conclusion rests on a synthetic auxiliary lane, and
these tests pin it down on small, fast instances:

* with **no merge-out and regular arrivals** the estimator recovers a known
  coverage to within 0.05 — the generator lands in the regime the module's own
  ``synthetic_validation`` certifies, so a failure here is the generator's
  fault, not the estimator's;
* with the recording's **merge-out** switched on, the same estimator falls far
  below the truth. That bias is the finding, so it is asserted.

Data-free and fast: no recording, no SUMO.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

from calibration.coverage import fit_gap_mixture
from flowstate_core.rng import make_rng

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"


def _load(name: str) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod() -> ModuleType:
    return _load("i24_coverage_lane5")


@pytest.fixture(scope="module")
def validation_rows(mod: ModuleType) -> list[dict]:
    """The two rows the §2.3 verdict rests on, on a small fast instance."""
    return mod.auxiliary_lane_validation(
        c_values=(0.75,),
        headway_cvs=(0.35,),
        merge_lengths_m=(math.inf, 753.0),
        n_snapshots=120,
    )


# --------------------------------------------------------------------------
# exponential_decay_length
# --------------------------------------------------------------------------


class TestExponentialDecayLength:
    def test_recovers_a_planted_decay(self, mod: ModuleType) -> None:
        x = np.linspace(0.0, 1000.0, 11)
        q = 800.0 * np.exp(-x / 500.0)
        got = mod.exponential_decay_length(x, q)
        assert got["decay_length_m"] == pytest.approx(500.0, rel=1e-6)
        assert got["q0_veh_h"] == pytest.approx(800.0, rel=1e-6)
        assert got["r2"] == pytest.approx(1.0, abs=1e-9)

    def test_flat_profile_is_infinite(self, mod: ModuleType) -> None:
        x = np.array([0.0, 100.0, 200.0])
        got = mod.exponential_decay_length(x, np.array([500.0, 500.0, 500.0]))
        assert not math.isfinite(got["decay_length_m"])

    def test_growing_profile_is_negative(self, mod: ModuleType) -> None:
        x = np.array([0.0, 100.0, 200.0])
        got = mod.exponential_decay_length(x, np.array([100.0, 200.0, 400.0]))
        assert got["decay_length_m"] < 0.0

    def test_non_positive_bins_dropped(self, mod: ModuleType) -> None:
        x = np.array([0.0, 100.0, 200.0, 300.0])
        q = np.array([800.0, 0.0, 800.0 * math.exp(-200.0 / 500.0), 0.0])
        got = mod.exponential_decay_length(x, q)
        assert got["decay_length_m"] == pytest.approx(500.0, rel=1e-6)

    def test_too_few_usable_bins_raises(self, mod: ModuleType) -> None:
        with pytest.raises(ValueError):
            mod.exponential_decay_length(np.array([0.0, 100.0]), np.array([100.0, 0.0]))

    def test_mismatched_shapes_raise(self, mod: ModuleType) -> None:
        with pytest.raises(ValueError):
            mod.exponential_decay_length(np.array([0.0, 1.0]), np.array([1.0]))


# --------------------------------------------------------------------------
# harmonic_coverage
# --------------------------------------------------------------------------


class TestHarmonicCoverage:
    def test_hand_computed(self, mod: ModuleType) -> None:
        # 100 / 0.5 + 100 / 1.0 = 300 corrected from 200 counted -> 2/3.
        got = mod.harmonic_coverage(np.array([100.0, 100.0]), np.array([0.5, 1.0]))
        assert got == pytest.approx(2.0 / 3.0)

    def test_reproduces_the_corrected_total(self, mod: ModuleType) -> None:
        n = np.array([47.0, 53.0, 58.0, 80.0])
        c = np.array([0.6, 0.55, 0.52, 0.5])
        assert float((n / c).sum()) == pytest.approx(n.sum() / mod.harmonic_coverage(n, c))

    def test_constant_coverage_is_itself(self, mod: ModuleType) -> None:
        n = np.array([3.0, 11.0, 7.0])
        assert mod.harmonic_coverage(n, np.full(3, 0.62)) == pytest.approx(0.62)

    def test_zero_count_windows_ignored(self, mod: ModuleType) -> None:
        got = mod.harmonic_coverage(np.array([100.0, 0.0]), np.array([0.5, 0.01]))
        assert got == pytest.approx(0.5)

    def test_no_usable_window_is_nan(self, mod: ModuleType) -> None:
        assert math.isnan(mod.harmonic_coverage(np.zeros(3), np.full(3, 0.5)))

    def test_out_of_range_coverage_raises(self, mod: ModuleType) -> None:
        with pytest.raises(ValueError):
            mod.harmonic_coverage(np.array([1.0, 1.0]), np.array([0.5, 1.5]))


# --------------------------------------------------------------------------
# auxiliary_lane_spacings
# --------------------------------------------------------------------------


class TestAuxiliaryLaneSpacings:
    def _kwargs(self, **over: float) -> dict[str, float]:
        kw: dict[str, float] = {
            "c_track": 1.0,
            "n_snapshots": 200,
            "span_m": 1200.0,
            "speed_ms": 11.1,
            "entry_headway_s": 5.0,
            "headway_cv": 0.35,
            "merge_length_m": math.inf,
        }
        kw.update(over)
        return kw

    def test_untracked_lane_reproduces_the_entry_spacing(self, mod: ModuleType) -> None:
        """With c = 1 and no merge-out the spacings are the entry spacings."""
        sp = mod.auxiliary_lane_spacings(make_rng(3), **self._kwargs())
        assert sp.size > 1000
        assert float(sp.mean()) == pytest.approx(11.1 * 5.0, rel=0.05)
        assert float(sp.std() / sp.mean()) == pytest.approx(0.35, rel=0.15)

    def test_merge_out_thins_the_lane(self, mod: ModuleType) -> None:
        """An exponential merge-out removes points and widens the spacings."""
        base = mod.auxiliary_lane_spacings(make_rng(3), **self._kwargs())
        merged = mod.auxiliary_lane_spacings(make_rng(3), **self._kwargs(merge_length_m=753.0))
        assert merged.size < base.size
        assert float(merged.mean()) > float(base.mean())

    def test_tracking_thinning_scales_the_mean_spacing(self, mod: ModuleType) -> None:
        """Random thinning at c multiplies the mean spacing by 1/c (Wald)."""
        full = mod.auxiliary_lane_spacings(make_rng(5), **self._kwargs())
        half = mod.auxiliary_lane_spacings(make_rng(5), **self._kwargs(c_track=0.5))
        assert float(half.mean()) == pytest.approx(2.0 * float(full.mean()), rel=0.08)

    def test_seeded_and_deterministic(self, mod: ModuleType) -> None:
        a = mod.auxiliary_lane_spacings(make_rng(11), **self._kwargs(n_snapshots=20))
        b = mod.auxiliary_lane_spacings(make_rng(11), **self._kwargs(n_snapshots=20))
        np.testing.assert_allclose(a, b)

    @pytest.mark.parametrize(
        "bad",
        [
            {"c_track": 0.0},
            {"c_track": 1.5},
            {"headway_cv": 0.0},
            {"entry_headway_s": 0.0},
            {"speed_ms": -1.0},
            {"span_m": 0.0},
            {"merge_length_m": 0.0},
        ],
    )
    def test_invalid_parameters_raise(self, mod: ModuleType, bad: dict[str, float]) -> None:
        with pytest.raises(ValueError):
            mod.auxiliary_lane_spacings(make_rng(1), **self._kwargs(**bad))


# --------------------------------------------------------------------------
# The finding: the estimator is not identified on an auxiliary lane
# --------------------------------------------------------------------------


class TestAuxiliaryLaneValidation:
    """The finding of §2.3, asserted so it stays documented."""

    def test_control_recovers_the_known_coverage(self, validation_rows: list[dict]) -> None:
        control = next(r for r in validation_rows if not r["merge_out"])
        assert control["converged"] and not control["at_bound"]
        assert control["c_hat"] == pytest.approx(0.75, abs=0.05)

    def test_merge_out_biases_the_estimator_far_low(self, validation_rows: list[dict]) -> None:
        merged = next(r for r in validation_rows if r["merge_out"])
        # The documented failure: the fit sees tracking loss and merge-out as
        # one thinning and returns roughly their product.
        assert merged["err_c"] < -0.2
        assert merged["c_hat"] < 0.6

    def test_row_shape(self, validation_rows: list[dict]) -> None:
        assert len(validation_rows) == 2
        for r in validation_rows:
            assert r["c_true"] == 0.75
            assert r["n_spacings"] > 500
            assert 0.0 < r["cv_obs"] < 2.0

    def test_seeded_and_deterministic(self, mod: ModuleType) -> None:
        kw = {
            "c_values": (0.5,),
            "headway_cvs": (0.35,),
            "merge_lengths_m": (math.inf,),
            "n_snapshots": 60,
        }
        assert mod.auxiliary_lane_validation(**kw) == mod.auxiliary_lane_validation(**kw)

    def test_the_generator_lands_in_the_certified_regime(self, mod: ModuleType) -> None:
        """Sanity: a plain thinned renewal lane is what the module validates."""
        sp = mod.auxiliary_lane_spacings(
            make_rng(9),
            c_track=0.5,
            n_snapshots=150,
            span_m=1200.0,
            speed_ms=11.1,
            entry_headway_s=2.5,
            headway_cv=0.35,
            merge_length_m=math.inf,
        )
        assert fit_gap_mixture(sp).c == pytest.approx(0.5, abs=0.05)
