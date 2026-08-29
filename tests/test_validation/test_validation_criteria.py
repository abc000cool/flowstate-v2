"""Tests for validation.criteria: honest pass/fail against the FHWA-style
default profile (CLAUDE.md §7.1)."""

import math

import pytest

from flowstate_core.constants import WAVE_SPEED_BAND_KMH
from validation.criteria import CriteriaProfile, evaluate


def _row(rows, name):
    matches = [r for r in rows if r.name == name]
    assert len(matches) == 1, f"expected exactly one {name} row"
    return matches[0]


class TestDefaults:
    def test_default_profile_encodes_spec_table(self):
        p = CriteriaProfile()
        assert p.geh_threshold == 5.0
        assert p.geh_pass_fraction == 0.85
        assert p.rmspe_max == 0.15
        assert p.wave_speed_band_kmh == WAVE_SPEED_BAND_KMH
        assert p.min_seeds == 20

    def test_all_checks_present_and_unevaluated_fail(self):
        rows = evaluate()
        names = {r.name for r in rows}
        assert names == {
            "link_flows_geh",
            "speeds_rmspe",
            "wave_speed",
            "ring_emergence",
            "ring_dampening",
            "n_seeds",
        }
        for r in rows:
            assert not r.evaluated
            assert not r.passed
            assert r.value is None


class TestGeh:
    def test_pass_at_90_percent_under_threshold(self):
        values = [1.0] * 18 + [9.0] * 2  # 90% below 5
        row = _row(evaluate(geh_values=values), "link_flows_geh")
        assert row.evaluated and row.passed
        assert row.value == pytest.approx(0.9)

    def test_fail_at_80_percent(self):
        values = [1.0] * 16 + [9.0] * 4  # 80% below 5
        row = _row(evaluate(geh_values=values), "link_flows_geh")
        assert row.evaluated and not row.passed
        assert row.value == pytest.approx(0.8)

    def test_boundary_fraction_is_inclusive_and_geh_strict(self):
        # Exactly 85% under -> pass (>=); a GEH of exactly 5 does NOT count.
        values = [1.0] * 17 + [5.0] * 3
        row = _row(evaluate(geh_values=values), "link_flows_geh")
        assert row.value == pytest.approx(0.85)
        assert row.passed

    def test_empty_comparisons_fail(self):
        row = _row(evaluate(geh_values=[]), "link_flows_geh")
        assert row.evaluated and not row.passed


class TestScalarChecks:
    def test_rmspe(self):
        assert _row(evaluate(rmspe_value=0.10), "speeds_rmspe").passed
        assert _row(evaluate(rmspe_value=0.15), "speeds_rmspe").passed  # inclusive
        assert not _row(evaluate(rmspe_value=0.20), "speeds_rmspe").passed

    def test_wave_speed_band(self):
        assert _row(evaluate(wave_speed_kmh=18.0), "wave_speed").passed
        assert _row(evaluate(wave_speed_kmh=14.0), "wave_speed").passed  # inclusive
        assert not _row(evaluate(wave_speed_kmh=25.0), "wave_speed").passed
        assert not _row(evaluate(wave_speed_kmh=10.0), "wave_speed").passed

    def test_wave_speed_nan_fails_honestly(self):
        row = _row(evaluate(wave_speed_kmh=math.nan), "wave_speed")
        assert row.evaluated and not row.passed
        assert "no backward wave" in row.detail

    def test_ring_booleans(self):
        rows = evaluate(ring_emergence=True, ring_dampening=False)
        assert _row(rows, "ring_emergence").passed
        assert not _row(rows, "ring_dampening").passed

    def test_ring_checks_can_be_disabled(self):
        p = CriteriaProfile(require_ring_emergence=False, require_ring_dampening=False)
        names = {r.name for r in evaluate(p)}
        assert "ring_emergence" not in names
        assert "ring_dampening" not in names

    def test_n_seeds(self):
        assert _row(evaluate(n_seeds=20), "n_seeds").passed
        assert not _row(evaluate(n_seeds=5), "n_seeds").passed

    def test_custom_profile_thresholds(self):
        p = CriteriaProfile(name="txdot_variant", rmspe_max=0.10, min_seeds=30)
        assert not _row(evaluate(p, rmspe_value=0.12), "speeds_rmspe").passed
        assert not _row(evaluate(p, n_seeds=20), "n_seeds").passed
