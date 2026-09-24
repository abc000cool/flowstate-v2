"""Adversarial checks of the C-D pair rule in the balance step.

Built on the corridor of ``test_calibration_onboarding_cd`` (its fixture
helpers are reused): stations M1 (0 m), M2 (2000 m), M3 (3000 m); on-ramp A
(live, 400), on-ramp B (no detector), the C-D split at 2000 m and its
re-entry at 3000 m. Each case changes one thing and states what the pair
carries afterwards:

* the split's detector is dead and the re-entry has none — the split carries
  nothing (a dead exit in a bracket whose flow rises gets no share), so the
  re-entry has nothing to take back and closes its bracket alone;
* both ends sit in one bracket — the re-entry takes the split's detector
  value back before the remainder is shared;
* the mainline drops across the re-entry's bracket — the re-entry returns
  nothing and the drop is a recorded residual, never a negative return.

Plus the artifact's shape: same input, same bytes; ``flowstate.demand/1``
round-trips through :class:`calibration.demand.DemandArtifact`; the ramp
records are the scenario's ramps in order; the pair record names them; and
the ``RampSpec`` placeholders ``microsim.scenarios`` emits for a discovered
pair go through the step under their generated names.
"""

from __future__ import annotations

import copy
import dataclasses
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from calibration import onboarding
from calibration.demand import DemandArtifact
from calibration.observations import Observations
from calibration.onboarding import OnboardingResult, calibrate_scenario
from microsim.geo import RampCandidate
from microsim.scenarios import _ramp_placeholder

_spec = importlib.util.spec_from_file_location(
    "_onboarding_cd_fixtures", Path(__file__).with_name("test_calibration_onboarding_cd.py")
)
assert _spec is not None and _spec.loader is not None
fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fx)

METHODS = {"detector", "detector_scaled", "conservation", "zero_outside_observed_span"}


def _run(
    monkeypatch: pytest.MonkeyPatch,
    scenario: dict[str, Any],
    obs: Observations | None = None,
    stations_x: dict[str, float] | None = None,
) -> OnboardingResult:
    monkeypatch.setattr(onboarding, "chain_edge_x", lambda net_path, chain: fx.EDGE_X)
    return calibrate_scenario(
        scenario,
        observations=obs or fx._observations(),
        stations_x=stations_x or fx.STATIONS_X,
        net_path="unused.net.xml",
        upstream="M1",
        downstream="M3",
        warmup_s=120.0,
    )


def _with_flows(obs: Observations, **flows: list[float]) -> Observations:
    return dataclasses.replace(obs, flows_veh_h={**obs.flows_veh_h, **flows})


def _without_station(obs: Observations, sid: str) -> Observations:
    return dataclasses.replace(
        obs,
        stations=tuple(s for s in obs.stations if s.id != sid),
        flows_veh_h={k: v for k, v in obs.flows_veh_h.items() if k != sid},
        speeds_ms={k: v for k, v in obs.speeds_ms.items() if k != sid},
        occupancy_pct={k: v for k, v in obs.occupancy_pct.items() if k != sid},
    )


def _on_veh_h(result: OnboardingResult, name: str) -> list[float]:
    return [v * 3600.0 for _, v in fx._by_name(result, name)["inflow_steps"]]


def _pair(result: OnboardingResult) -> tuple[float, float, float]:
    (p,) = result.cd_pairs
    return p["mean_out_veh_h"], p["mean_back_veh_h"], p["mean_net_veh_h"]


class TestDeadSplitDetector:
    def test_the_split_carries_nothing_and_the_reentry_closes_its_bracket_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        obs = _with_flows(fx._observations(), D_CD=[10.0, 10.0])  # below alive_veh_h
        result = _run(monkeypatch, fx._scenario(), obs)
        split = fx._by_name(result, "C-D split")
        assert split["method"] == "conservation" and split["station"] == "D_CD"
        # bracket M1→M2 rises by 600: A's 400 and dead B's 200; a dead exit
        # takes no share of a rise, so the split sends nothing out
        assert [f for _, f in split["exit_fraction_steps"]] == [0.0, 0.0]
        assert _on_veh_h(result, "on B") == pytest.approx([200.0, 200.0])
        # the re-entry has nothing to take back: the +400 of M2→M3 is all its own
        assert fx._by_name(result, "C-D re-entry")["method"] == "conservation"
        assert _on_veh_h(result, "C-D re-entry") == pytest.approx([400.0, 400.0])
        assert _pair(result) == pytest.approx((0.0, 400.0, 400.0))
        assert result.residuals == []

    def test_every_station_count_is_still_reproduced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        obs = _with_flows(fx._observations(), D_CD=[10.0, 10.0])
        result = _run(monkeypatch, fx._scenario(), obs)
        q = 3000.0 + sum(_on_veh_h(result, n)[0] for n in ("on A", "on B"))
        q -= fx._by_name(result, "C-D split")["exit_fraction_steps"][0][1] * q
        assert q == pytest.approx(3600.0)
        assert q + _on_veh_h(result, "C-D re-entry")[0] == pytest.approx(4000.0)


class TestBothEndsInOneBracket:
    @pytest.fixture()
    def one_bracket(self) -> tuple[Observations, dict[str, float]]:
        obs = _without_station(fx._observations(), "M2")
        return obs, {k: v for k, v in fx.STATIONS_X.items() if k != "M2"}

    def test_live_split_is_taken_back_first_then_the_rest_is_shared(
        self, monkeypatch: pytest.MonkeyPatch, one_bracket: tuple[Observations, dict[str, float]]
    ) -> None:
        obs, sx = one_bracket
        result = _run(monkeypatch, fx._scenario(), obs, sx)
        # M1→M3 rises by 1000; A 400 and the split's 300 out leave 900 for the
        # two dead entrances: the re-entry takes 300 back, then 300 each.
        assert _on_veh_h(result, "C-D re-entry") == pytest.approx([600.0, 600.0])
        assert _on_veh_h(result, "on B") == pytest.approx([300.0, 300.0])
        assert _pair(result) == pytest.approx((300.0, 600.0, 300.0))
        assert result.cd_pairs[0]["reentry_bracket"] == ["M1", "M3"]
        assert result.residuals == []

    def test_dead_split_in_the_same_bracket_returns_nothing(
        self, monkeypatch: pytest.MonkeyPatch, one_bracket: tuple[Observations, dict[str, float]]
    ) -> None:
        obs, sx = one_bracket
        result = _run(monkeypatch, fx._scenario(), _with_flows(obs, D_CD=[10.0, 10.0]), sx)
        assert _on_veh_h(result, "C-D re-entry") == pytest.approx([300.0, 300.0])
        assert _on_veh_h(result, "on B") == pytest.approx([300.0, 300.0])
        assert _pair(result) == pytest.approx((0.0, 300.0, 300.0))


class TestDropAcrossTheReentryBracket:
    def test_a_drop_is_a_residual_not_a_negative_return(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        obs = _with_flows(fx._observations(), M3=[3300.0, 3300.0])
        result = _run(monkeypatch, fx._scenario(), obs)
        assert _on_veh_h(result, "C-D re-entry") == [0.0, 0.0]
        assert _pair(result) == pytest.approx((300.0, 0.0, -300.0))
        assert [(r["from"], r["to"], r["mean_residual_veh_h"]) for r in result.residuals] == [
            ("M2", "M3", -300.0)
        ]


class TestArtifactShape:
    def test_same_input_same_bytes_and_the_scenario_is_filled_in_place(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scenario = fx._scenario()
        pristine = copy.deepcopy(scenario)
        first = _run(monkeypatch, scenario)
        again_same_dict = _run(monkeypatch, scenario)
        fresh = _run(monkeypatch, copy.deepcopy(pristine))
        dumps = [json.dumps(r.demand, sort_keys=False) for r in (first, again_same_dict, fresh)]
        assert dumps[0] == dumps[1] == dumps[2]
        assert json.dumps(first.scenario) == json.dumps(fresh.scenario)
        assert first.config_hash == fresh.config_hash
        assert scenario != pristine  # documented: filled in place

    def test_demand_artifact_round_trips_with_only_additive_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = _run(monkeypatch, fx._scenario())
        payload = result.demand
        assert payload["schema"] == "flowstate.demand/1"
        art = DemandArtifact.from_dict(payload)
        assert art.schema == "flowstate.demand/1" and art.ramps == payload["ramps"]
        assert all(isinstance(v, float) for v in art.coverage.values())
        assert art.coverage["n_cd_pairs"] == 1.0 == float(len(payload["cd_pairs"]))
        # the artifact's own keys come first in their own order; the pipeline's
        # provenance keys (config_hash … cd_pairs) follow, all additive
        own = list(art.to_dict())
        assert list(payload)[: len(own)] == own
        assert set(payload) - set(own) >= {"cd_pairs", "bracket_residuals", "config_hash"}
        assert {r["method"] for r in payload["ramps"]} <= METHODS
        for c in payload["cd_pairs"]:
            assert c["split_method"] in METHODS and c["reentry_method"] in METHODS

    def test_ramp_records_are_the_scenario_ramps_in_order_and_the_pair_names_them(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scenario = fx._scenario()
        result = _run(monkeypatch, scenario)
        assert [(r["name"], r["kind"]) for r in result.demand["ramps"]] == [
            (r["name"], r["kind"]) for r in scenario["network"]["ramps"]
        ]
        (pair,) = result.cd_pairs
        names = {r["name"] for r in result.demand["ramps"]}
        assert pair["split"] in names and pair["reentry"] in names
        for spec in scenario["network"]["ramps"]:
            assert bool(spec["inflow"]) == (spec["kind"] == "on")
            assert bool(spec["exit_fraction"]) == (spec["kind"] == "off")

    def test_scenario_builder_placeholders_carry_the_pair_through_the_step(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        split = RampCandidate(
            kind="off",
            edges=("cd_1",),
            attach_edge="e2",
            x_m=2000.0,
            discovery="cd_road",
            cd_road=True,
            cd_pair="cd_1",
            rejoin_edge="e4",
            rejoin_x_m=3000.0,
        )
        reentry = RampCandidate(
            kind="on",
            edges=("cd_2",),
            attach_edge="e4",
            x_m=3000.0,
            discovery="cd_road",
            cd_road=True,
            cd_pair="cd_1",
        )
        specs = [_ramp_placeholder(c).model_dump(mode="json") for c in (split, reentry)]
        assert [s["name"] for s in specs] == ["C-D split cd_1", "C-D re-entry cd_2"]
        scenario = fx._scenario()
        scenario["network"]["ramps"] = [
            r for r in scenario["network"]["ramps"] if not r.get("cd_road")
        ] + specs
        result = _run(monkeypatch, scenario)
        (pair,) = result.cd_pairs
        assert (pair["split"], pair["reentry"]) == ("C-D split cd_1", "C-D re-entry cd_2")
        assert _pair(result) == pytest.approx((300.0, 400.0, 100.0))
        assert result.residuals == []
