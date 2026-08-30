"""Registry round-trip tests (docs/CONTRACTS.md §1)."""

import pytest

import controllers
from controllers import (
    ALL_SEGMENT_CONTROLLERS,
    ALL_VEHICLE_CONTROLLERS,
    default_params,
    get_segment_controller,
    get_vehicle_controller,
    list_controllers,
)
from flowstate_core.controller_types import ControllerObs, SegmentObs

# docs/CONTRACTS.md §1 names; "pi_meanfrac" is the superseded §4.2 simplification
# retained beside the faithful Stern et al. (2018) "pi_saturation".
CONTRACT_VEHICLE_NAMES = {"follower_stopper", "pi_saturation", "jad", "pi_meanfrac"}
CONTRACT_SEGMENT_NAMES = {"vsl_threshold"}


class TestRoundTrip:
    def test_contract_names_registered(self):
        assert set(ALL_VEHICLE_CONTROLLERS) == CONTRACT_VEHICLE_NAMES
        assert set(ALL_SEGMENT_CONTROLLERS) == CONTRACT_SEGMENT_NAMES

    def test_vehicle_lookup_returns_callable_obeying_contract(self):
        obs = ControllerObs(t=0.0, dt=0.5, v=10.0, gap=50.0, v_leader=10.0, v_ref=15.0)
        for name in CONTRACT_VEHICLE_NAMES:
            fn = get_vehicle_controller(name)
            v_cmd, mem = fn(obs, default_params(name), {})
            assert isinstance(v_cmd, float)
            assert isinstance(mem, dict)

    def test_segment_lookup_returns_callable_obeying_contract(self):
        obs = SegmentObs(t=0.0, dt=30.0, seg_speed=(30.0, 30.0), seg_density=(0.01, 0.01))
        fn = get_segment_controller("vsl_threshold")
        limits, mem = fn(obs, default_params("vsl_threshold"), {})
        assert len(limits) == 2
        assert isinstance(mem, dict)

    def test_all_vehicle_controllers_matches_lookups(self):
        for name, fn in ALL_VEHICLE_CONTROLLERS.items():
            assert get_vehicle_controller(name) is fn

    def test_exported_from_package_root(self):
        assert controllers.ALL_VEHICLE_CONTROLLERS is ALL_VEHICLE_CONTROLLERS

    def test_list_controllers_groups_sorted_names(self):
        listed = list_controllers()
        assert listed["vehicle"] == tuple(sorted(CONTRACT_VEHICLE_NAMES))
        assert listed["segment"] == tuple(sorted(CONTRACT_SEGMENT_NAMES))


class TestUnknownNames:
    def test_unknown_vehicle_name_lists_available(self):
        with pytest.raises(KeyError, match="follower_stopper") as excinfo:
            get_vehicle_controller("nope")
        assert "nope" in str(excinfo.value)
        assert "jad" in str(excinfo.value)
        assert "pi_saturation" in str(excinfo.value)

    def test_unknown_segment_name_lists_available(self):
        with pytest.raises(KeyError, match="vsl_threshold"):
            get_segment_controller("nope")

    def test_unknown_default_params_lists_available(self):
        with pytest.raises(KeyError, match="vsl_threshold"):
            default_params("nope")

    def test_vehicle_lookup_rejects_segment_name(self):
        with pytest.raises(KeyError):
            get_vehicle_controller("vsl_threshold")


class TestDefaultParams:
    def test_all_values_are_floats(self):
        for name in CONTRACT_VEHICLE_NAMES | CONTRACT_SEGMENT_NAMES:
            params = default_params(name)
            assert params, name
            assert all(isinstance(v, float) for v in params.values()), name

    def test_returns_fresh_copy(self):
        p = default_params("follower_stopper")
        p["dx0_1"] = 999.0
        assert default_params("follower_stopper")["dx0_1"] == 4.5

    def test_follower_stopper_literature_defaults(self):
        """Stern et al. (2018) §3.1 constants, verified against the paper."""
        p = default_params("follower_stopper")
        assert (p["dx0_1"], p["dx0_2"], p["dx0_3"]) == (4.5, 5.25, 6.0)
        assert (p["d_1"], p["d_2"], p["d_3"]) == (1.5, 1.0, 0.5)
