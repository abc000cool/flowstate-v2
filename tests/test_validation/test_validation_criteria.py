"""Tests for validation.criteria: honest pass/fail against the FHWA-style
default profile (CLAUDE.md §7.1)."""

import math

import pytest

from flowstate_core.constants import WAVE_SPEED_BAND_KMH
from validation.criteria import (
    CRITERIA_PROFILES,
    REQUIRED_COMPLIANCES,
    REQUIRED_PENETRATIONS,
    CriteriaProfile,
    evaluate,
    get_profile,
)


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
            "sensitivity_grid",
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


class TestProfiles:
    """The registry (CLAUDE.md §7.1 selectable state-DOT profiles)."""

    def test_registry_has_default_and_a_state_profile(self):
        assert "fhwa_default" in CRITERIA_PROFILES
        assert get_profile("fhwa_default") == CriteriaProfile()
        assert {"odot_vissim_2011", "txdot_tsap_ch13", "fhwa_tat3_2004"} <= set(CRITERIA_PROFILES)
        for name, p in CRITERIA_PROFILES.items():
            assert p.name == name

    def test_every_profile_records_its_source(self):
        for p in CRITERIA_PROFILES.values():
            assert p.source.strip()
            # Each source names the document/section it was verified against
            # and flags the FlowState-only rows.
            assert "verified" in p.source or "read 2026" in p.source
            assert "FlowState" in p.source

    def test_unknown_profile_lists_available(self):
        with pytest.raises(KeyError, match="fhwa_default"):
            get_profile("no_such_profile")

    def test_empty_source_rejected(self):
        with pytest.raises(ValueError, match="source"):
            CriteriaProfile(name="x", source="   ")

    def test_fhwa_2004_fraction_is_strict(self):
        # The 2004 table says "> 85% of cases": exactly 85% fails there but
        # passes the inclusive default.
        values = [1.0] * 17 + [9.0] * 3
        assert _row(evaluate(geh_values=values), "link_flows_geh").passed
        row = _row(evaluate(get_profile("fhwa_tat3_2004"), geh_values=values), "link_flows_geh")
        assert not row.passed
        assert "> 85%" in row.threshold
        assert _row(
            evaluate(get_profile("fhwa_tat3_2004"), geh_values=[1.0] * 18 + [9.0] * 2),
            "link_flows_geh",
        ).passed

    def test_txdot_state_facility_row(self):
        p = get_profile("txdot_tsap_ch13")
        assert p.geh_threshold == 3.0 and p.geh_pass_fraction == 1.0
        assert _row(evaluate(p, geh_values=[2.9, 1.0]), "link_flows_geh").passed
        assert not _row(evaluate(p, geh_values=[3.0, 1.0]), "link_flows_geh").passed
        assert not _row(evaluate(p, geh_values=[4.0] + [1.0] * 99), "link_flows_geh").passed

    def test_odot_min_runs(self):
        p = get_profile("odot_vissim_2011")
        assert p.min_seeds == 10
        assert p.geh_threshold == 5.0 and p.geh_pass_fraction == 0.85
        assert _row(evaluate(p, n_seeds=10), "n_seeds").passed
        assert not _row(evaluate(p, n_seeds=9), "n_seeds").passed

    def test_profiles_without_rmspe_bound_emit_no_speed_row(self):
        for name in ("odot_vissim_2011", "txdot_tsap_ch13", "fhwa_tat3_2004"):
            p = get_profile(name)
            assert p.rmspe_max is None
            names = {r.name for r in evaluate(p, rmspe_value=0.1)}
            assert "speeds_rmspe" not in names
            assert "link_flows_geh" in names and "n_seeds" in names


class TestSensitivityGrid:
    """CLAUDE.md §7.1 'Sensitivity' row: every required cell must be present."""

    @staticmethod
    def _full_grid():
        return [(pen, comp) for pen in REQUIRED_PENETRATIONS for comp in REQUIRED_COMPLIANCES]

    def test_required_cells_match_spec(self):
        p = CriteriaProfile()
        assert p.required_penetrations == (0.01, 0.02, 0.05, 0.10, 0.15, 0.20)
        assert p.required_compliances == (0.25, 0.5, 0.8, 1.0)

    def test_absent_grid_is_not_evaluated(self):
        row = _row(evaluate(), "sensitivity_grid")
        assert not row.evaluated and not row.passed and row.value is None

    def test_full_grid_passes(self):
        row = _row(evaluate(sweep_grid=self._full_grid()), "sensitivity_grid")
        assert row.evaluated and row.passed
        assert row.value == pytest.approx(1.0)
        assert "24/24" in row.detail

    def test_missing_cell_fails_and_is_named(self):
        grid = [c for c in self._full_grid() if c != (0.15, 0.8)]
        row = _row(evaluate(sweep_grid=grid), "sensitivity_grid")
        assert row.evaluated and not row.passed
        assert row.value == pytest.approx(23.0 / 24.0)
        assert "(15%, 80%)" in row.detail

    def test_float_tolerance_and_extra_cells(self):
        grid = [(pen + 1e-9, comp - 1e-9) for pen, comp in self._full_grid()] + [(0.3, 1.0)]
        assert _row(evaluate(sweep_grid=grid), "sensitivity_grid").passed

    def test_profile_can_drop_the_row(self):
        p = CriteriaProfile(require_sensitivity_grid=False)
        assert "sensitivity_grid" not in {r.name for r in evaluate(p, sweep_grid=[])}

    def test_empty_grid_fails_honestly(self):
        row = _row(evaluate(sweep_grid=[]), "sensitivity_grid")
        assert row.evaluated and not row.passed and row.value == 0.0


class TestWaveDetectorSetting:
    """The wave-speed row names the detector that must produce its value."""

    def test_default_detector_is_stack_in_every_profile(self):
        from validation.waves import STACK_DETECTOR

        assert CriteriaProfile().wave_detector is STACK_DETECTOR
        for p in CRITERIA_PROFILES.values():
            assert p.wave_detector is STACK_DETECTOR
            assert "wave_detector" in p.source and "'stack'" in p.source

    def test_row_detail_carries_detector_name_and_parameters(self):
        from validation.waves import STACK_DETECTOR

        row = _row(evaluate(wave_speed_kmh=18.0, wave_detector=STACK_DETECTOR), "wave_speed")
        assert row.evaluated and row.passed
        assert row.detail.startswith("detector: " + STACK_DETECTOR.describe())
        assert "[-40, -2] km/h" in row.detail and "contrast >= 3" in row.detail
        assert "did not state" not in row.detail

    def test_unstated_detector_is_noted_but_still_scored(self):
        row = _row(evaluate(wave_speed_kmh=18.0), "wave_speed")
        assert row.evaluated and row.passed
        assert "detector: stack:" in row.detail
        assert "caller did not state which detector produced the value" in row.detail
        nan_row = _row(evaluate(wave_speed_kmh=math.nan), "wave_speed")
        assert "no backward wave detected" in nan_row.detail and not nan_row.passed

    def test_mismatched_detector_is_not_evaluated(self):
        from validation.waves import STANDARD_DETECTOR

        row = _row(evaluate(wave_speed_kmh=18.0, wave_detector=STANDARD_DETECTOR), "wave_speed")
        assert not row.evaluated and not row.passed
        assert row.value == 18.0
        assert "measured with standard: jam = v < 40 km/h" in row.detail
        assert "profile requires stack:" in row.detail

    def test_profile_can_select_another_registered_detector(self):
        from validation.waves import get_detector

        p = CriteriaProfile(name="standard_variant", wave_detector=get_detector("standard"))
        row = _row(
            evaluate(p, wave_speed_kmh=18.0, wave_detector=get_detector("standard")), "wave_speed"
        )
        assert row.evaluated and row.passed
        assert row.detail.startswith("detector: standard: jam = v < 40 km/h on 15 s x 75 m bins")
        stripe = _row(
            evaluate(p, wave_speed_kmh=18.0, wave_detector=get_detector("stripe")), "wave_speed"
        )
        assert not stripe.evaluated
