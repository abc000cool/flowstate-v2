"""The infrastructure strategy patch shared by the CLI sweep and the API.

``scripts/corridor_sweep.py`` and ``POST /api/v1/sweeps`` build their cells
from this one function, so a CLI cell and an API cell of the same grid point
must be the same configuration — and therefore the same ``config_hash``.
"""

from __future__ import annotations

import copy
from typing import Any, get_args

import pytest

from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.strategies import (
    STRATEGIES,
    Strategy,
    StrategyError,
    apply_strategy,
    needs_target,
    on_ramps,
)

RHO_TARGET_VEH_KM = 19.9


def _osm_config() -> dict[str, Any]:
    """A serialized OSM scenario with two on-ramps and one off-ramp."""
    cfg = ScenarioConfig.model_validate(
        {
            "name": "strategy_fixture",
            "network": {
                "kind": "osm",
                "osm_file": "x.osm",
                "corridor_edges": ["a", "b", "c"],
                "inflow": [(0.0, 0.5)],
                "ramps": [
                    {
                        "kind": "on",
                        "edges": ["r1"],
                        "attach_edge": "a",
                        "inflow": [(0.0, 0.1)],
                        "name": "first on",
                    },
                    {
                        "kind": "off",
                        "edges": ["r2"],
                        "attach_edge": "b",
                        "exit_fraction": [(0.0, 0.2)],
                    },
                    {
                        "kind": "on",
                        "edges": ["r3"],
                        "attach_edge": "c",
                        "inflow": [(0.0, 0.05)],
                    },
                ],
            },
            "sim": {"duration_s": 60.0},
        }
    )
    return cfg.model_dump(mode="json")


def _corridor_config() -> dict[str, Any]:
    """A serialized corridor scenario — no ramps anywhere in the schema."""
    cfg = ScenarioConfig.model_validate(
        {
            "name": "strategy_fixture_corridor",
            "network": {"kind": "corridor", "length_m": 1000.0, "lanes": 1, "inflow": [(0.0, 0.3)]},
            "sim": {"duration_s": 60.0},
        }
    )
    return cfg.model_dump(mode="json")


class TestStrategyNames:
    def test_strategies_are_the_literal_members(self):
        assert STRATEGIES == get_args(Strategy)
        assert STRATEGIES == ("none", "vsl", "alinea", "vsl+alinea")

    def test_only_the_metering_strategies_need_a_target(self):
        assert [needs_target(s) for s in STRATEGIES] == [False, False, True, True]

    def test_unknown_strategy_is_refused(self):
        cfg = _corridor_config()
        with pytest.raises(StrategyError, match="unknown strategy"):
            apply_strategy(cfg, "ramp_meter", None)
        assert cfg == _corridor_config()


class TestNone:
    def test_none_leaves_the_scenario_exactly_as_calibrated(self):
        cfg = _corridor_config()
        before = copy.deepcopy(cfg)
        apply_strategy(cfg, "none", RHO_TARGET_VEH_KM)
        assert cfg == before

    def test_none_does_not_strip_what_the_scenario_itself_configures(self):
        """Additive, not normalizing: a scenario's own VSL survives ``none``."""
        cfg = _osm_config()
        cfg["av"]["vsl"] = "vsl_threshold"
        apply_strategy(cfg, "none", None)
        assert cfg["av"]["vsl"] == "vsl_threshold"


class TestVSL:
    def test_vsl_posts_the_segment_controller(self):
        cfg = _corridor_config()
        apply_strategy(cfg, "vsl", None)
        assert cfg["av"]["vsl"] == "vsl_threshold"
        assert cfg["av"]["vsl_params"] == {}
        assert ScenarioConfig.model_validate(cfg).av.vsl == "vsl_threshold"

    def test_vsl_is_idempotent_and_keeps_configured_params(self):
        cfg = _corridor_config()
        cfg["av"]["vsl_params"] = {"v_on": 15.0}
        apply_strategy(cfg, "vsl", None)
        once = copy.deepcopy(cfg)
        apply_strategy(cfg, "vsl", None)
        assert cfg == once
        assert cfg["av"]["vsl_params"] == {"v_on": 15.0}


class TestAlinea:
    def test_every_on_ramp_is_metered_and_no_off_ramp_is(self):
        cfg = _osm_config()
        apply_strategy(cfg, "alinea", RHO_TARGET_VEH_KM)
        ramps = cfg["network"]["ramps"]
        assert [r.get("meter") is not None for r in ramps] == [True, False, True]
        for ramp in on_ramps(cfg):
            assert ramp["meter"]["controller"] == "alinea"
            assert ramp["meter"]["params"] == {"rho_target_veh_km": RHO_TARGET_VEH_KM}
        # The patch is schema-valid: the meter round-trips into RampMeterSpec.
        model = ScenarioConfig.model_validate(cfg)
        assert model.network.kind == "osm"
        metered = [r for r in model.network.ramps if r.meter is not None]
        assert len(metered) == 2
        assert metered[0].meter is not None
        assert metered[0].meter.params["rho_target_veh_km"] == RHO_TARGET_VEH_KM

    def test_alinea_without_a_target_is_refused_before_anything_is_patched(self):
        cfg = _osm_config()
        with pytest.raises(StrategyError, match="rho_target_veh_km"):
            apply_strategy(cfg, "alinea", None)
        assert cfg == _osm_config()

    def test_alinea_needs_an_on_ramp_to_meter(self):
        cfg = _corridor_config()
        with pytest.raises(StrategyError, match="on-ramp"):
            apply_strategy(cfg, "alinea", RHO_TARGET_VEH_KM)
        assert cfg == _corridor_config()

        off_only = _osm_config()
        off_only["network"]["ramps"] = [
            r for r in off_only["network"]["ramps"] if r["kind"] == "off"
        ]
        with pytest.raises(StrategyError, match="on-ramp"):
            apply_strategy(off_only, "alinea", RHO_TARGET_VEH_KM)

    def test_alinea_is_idempotent(self):
        cfg = _osm_config()
        apply_strategy(cfg, "alinea", RHO_TARGET_VEH_KM)
        once = copy.deepcopy(cfg)
        apply_strategy(cfg, "alinea", RHO_TARGET_VEH_KM)
        assert cfg == once


class TestCombined:
    def test_vsl_plus_alinea_applies_both(self):
        cfg = _osm_config()
        apply_strategy(cfg, "vsl+alinea", RHO_TARGET_VEH_KM)
        assert cfg["av"]["vsl"] == "vsl_threshold"
        assert all(r["meter"]["controller"] == "alinea" for r in on_ramps(cfg))

    def test_each_strategy_is_its_own_configuration(self):
        """Four strategies, four hashes — the sweep axis is real, not cosmetic."""
        hashes = set()
        for strategy in STRATEGIES:
            cfg = _osm_config()
            apply_strategy(cfg, strategy, RHO_TARGET_VEH_KM)
            hashes.add(config_hash(ScenarioConfig.model_validate(cfg)))
        assert len(hashes) == len(STRATEGIES)


def test_on_ramps_returns_the_config_dicts_and_is_empty_without_ramps():
    cfg = _osm_config()
    ramps = on_ramps(cfg)
    assert [r["name"] for r in ramps] == ["first on", ""]
    ramps[0]["meter"] = {"controller": "alinea", "params": {}}
    assert cfg["network"]["ramps"][0]["meter"] is not None  # a view, not a copy
    assert on_ramps(_corridor_config()) == []
    assert on_ramps({}) == []
