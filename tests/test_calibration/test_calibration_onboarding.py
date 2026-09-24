"""The corridor-onboarding glue (:mod:`calibration.onboarding`).

A hand-built corridor with known answers, so the bracket-closing rule is
checked against arithmetic rather than against its own output: three mainline
stations, three discovered ramps, and one ramp detector that matches nothing.

Geometry (four 1 km chain edges, so ``x`` is exact by construction)::

    x [m]      0        1000       2000       3000       4000
               |---e1---|---e2---|---e3---|---e4---|
    stations   M1                 M2         M3
    ramps               on A      on B       off C
                        (live)    (dead)     (dead)

Bracket M1→M2 (3000 → 3600 veh/h, +600): the live detector on A fixes 400
veh/h and the dead ramp B absorbs the remaining 200 — the bracket closes
exactly. Bracket M2→M3 (3600 → 4000 veh/h, +400) holds no on-ramp at all, so
nothing can carry the increase: the 400 veh/h is recorded as a residual rather
than smeared over the off-ramp (CLAUDE.md §0.1).

``chain_edge_x`` is the only part that needs a compiled SUMO net; it is
replaced here so the rule is tested without netconvert. The net reading itself
is covered by ``tests/test_microsim/test_microsim_geo.py``.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from calibration import onboarding
from calibration.observations import Observations, ObservedStation
from calibration.onboarding import OnboardingResult, calibrate_scenario

EDGE_X: dict[str, tuple[float, float]] = {
    "e1": (0.0, 1000.0),
    "e2": (1000.0, 2000.0),
    "e3": (2000.0, 3000.0),
    "e4": (3000.0, 4000.0),
}
WINDOW_S = 300.0
N_WINDOWS = 2

#: Observed mainline flows [veh/h] per window: +600 then +400 along the span.
FLOWS: dict[str, list[float]] = {
    "M1": [3000.0, 3000.0],
    "M2": [3600.0, 3600.0],
    "M3": [4000.0, 4000.0],
    "D_A": [400.0, 400.0],  # the one live ramp detector
    "D_FAR": [500.0, 500.0],  # an on-ramp detector matching no discovered ramp
}


def _stations() -> list[ObservedStation]:
    return [
        ObservedStation(id="M1", label="upstream", x_m=0.0, lanes=3, kind="mainline"),
        ObservedStation(id="M2", label="middle", x_m=2000.0, lanes=3, kind="mainline"),
        ObservedStation(
            id="M3", label="downstream", x_m=3000.0, lanes=3, kind="mainline", speed_limit_ms=30.0
        ),
        ObservedStation(id="D_A", label="ramp A", x_m=1050.0, lanes=1, kind="on_ramp"),
        ObservedStation(id="D_FAR", label="unmatched", x_m=3800.0, lanes=1, kind="on_ramp"),
    ]


def _observations(speeds: dict[str, list[float]] | None = None) -> Observations:
    ids = list(FLOWS)
    default_speeds = {sid: [25.0, 20.0] for sid in ids}
    return Observations(
        corridor="unit_corridor",
        source={"provider": "hand-built fixture"},
        window_s=WINDOW_S,
        t0_local="06:00",
        duration_s=WINDOW_S * N_WINDOWS,
        stations=tuple(_stations()),
        flows_veh_h={sid: list(FLOWS[sid]) for sid in ids},
        speeds_ms=speeds or default_speeds,
        occupancy_pct={sid: [10.0, 12.0] for sid in ids},
    )


def _ramp(name: str, kind: str, attach: str, edges: list[str]) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "kind": kind,
        "edges": edges,
        "attach_edge": attach,
        "name": name,
        "inflow": [],
        "exit_fraction": [],
    }
    if kind == "on":
        spec["inflow"] = [[0.0, 0.0]]
    else:
        spec["exit_fraction"] = [[0.0, 0.0]]
    return spec


def _scenario(extra_ramps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    ramps = [
        _ramp("on A", "on", "e2", ["r_a"]),
        _ramp("on B", "on", "e3", ["r_b"]),
        _ramp("off C", "off", "e3", ["r_c"]),
    ]
    ramps += extra_ramps or []
    return {
        "name": "unit_corridor",
        "tier": "micro",
        "network": {
            "kind": "osm",
            "bbox": [44.9, -93.3, 45.0, -93.2],
            "corridor_edges": ["e1", "e2", "e3", "e4"],
            "inflow": [[0.0, 0.5]],
            "ramps": ramps,
        },
        "fleet": {"model": "IDM"},
        "sim": {"duration_s": 600.0},
        "seed": 1,
        "replicates": 1,
    }


#: Station id → chain position [m], as the onboarding step's station table
#: would report it (M2 sits 10 m off its inventory position on purpose).
STATIONS_X: dict[str, float] = {"M1": 0.0, "M2": 2000.0, "M3": 3000.0, "D_A": 1050.0}


@pytest.fixture()
def result(monkeypatch: pytest.MonkeyPatch) -> OnboardingResult:
    monkeypatch.setattr(onboarding, "chain_edge_x", lambda net_path, chain: EDGE_X)
    return calibrate_scenario(
        _scenario(),
        observations=_observations(),
        stations_x=STATIONS_X,
        net_path="unused.net.xml",
        upstream="M1",
        downstream="M3",
        idm_calibration="artifacts/idm_unit.json",
        warmup_s=120.0,
    )


class TestBracketClosing:
    def test_live_detector_fixes_its_own_ramp(self, result: OnboardingResult) -> None:
        a = next(r for r in result.demand["ramps"] if r["name"] == "on A")
        assert a["method"] == "detector"
        assert a["station"] == "D_A"
        assert a["match_distance_m"] == pytest.approx(50.0)
        # 400 veh/h, in SI, in every window
        assert a["inflow_steps"] == [[0.0, 400.0 / 3600.0], [WINDOW_S, 400.0 / 3600.0]]

    def test_dead_ramp_absorbs_what_the_detector_leaves(self, result: OnboardingResult) -> None:
        b = next(r for r in result.demand["ramps"] if r["name"] == "on B")
        assert b["method"] == "conservation"
        assert "station" not in b
        # +600 observed − 400 measured = 200 veh/h with nowhere else to go
        assert b["inflow_steps"] == [[0.0, 200.0 / 3600.0], [WINDOW_S, 200.0 / 3600.0]]

    def test_bracket_without_the_needed_kind_carries_a_residual(
        self, result: OnboardingResult
    ) -> None:
        (residual,) = result.residuals
        assert (residual["from"], residual["to"]) == ("M2", "M3")
        assert residual["mean_residual_veh_h"] == pytest.approx(400.0)
        assert "carried into the next bracket" in residual["note"]
        # and nothing was invented on the off-ramp to hide it
        c = next(r for r in result.demand["ramps"] if r["name"] == "off C")
        assert c["method"] == "conservation"
        assert [v for _, v in c["exit_fraction_steps"]] == [0.0, 0.0]

    def test_mainline_flow_reproduces_every_station_count(self, result: OnboardingResult) -> None:
        inflow = [v * 3600.0 for _, v in result.demand["inflow_steps"]]
        ramps = {r["name"]: r for r in result.demand["ramps"]}
        on_a = [v * 3600.0 for _, v in ramps["on A"]["inflow_steps"]]
        on_b = [v * 3600.0 for _, v in ramps["on B"]["inflow_steps"]]
        at_m2 = [q + a + b for q, a, b in zip(inflow, on_a, on_b, strict=True)]
        assert at_m2 == pytest.approx(FLOWS["M2"])

    def test_coverage_counts_how_each_ramp_was_derived(self, result: OnboardingResult) -> None:
        coverage = result.demand["coverage"]
        assert coverage["n_ramps"] == 3.0
        assert coverage["n_ramps_detector"] == 1.0
        assert coverage["n_ramps_conservation"] == 2.0
        assert coverage["n_ramps_zeroed"] == 0.0
        assert coverage["n_brackets_with_residual"] == 1.0

    def test_unmatched_ramp_detector_is_reported_not_used(self, result: OnboardingResult) -> None:
        assert result.unmatched_detectors == ["D_FAR"]
        assert result.demand["unmatched_ramp_detectors"] == ["D_FAR"]
        assert all(r.get("station") != "D_FAR" for r in result.demand["ramps"])


class TestScenarioFilling:
    def test_inflow_boundary_and_population_are_written(self, result: OnboardingResult) -> None:
        network = result.scenario["network"]
        assert network["inflow"] == [[0.0, 3000.0 / 3600.0], [WINDOW_S, 3000.0 / 3600.0]]
        assert network["boundary"]["kind"] == "speed_schedule"
        assert network["boundary"]["steps"] == [[0.0, 25.0], [WINDOW_S, 20.0]]
        # 4000 m chain − the downstream station at 3000 m
        assert network["boundary"]["exit_buffer_m"] == pytest.approx(1000.0)
        assert result.scenario["fleet"]["idm_calibration"] == "artifacts/idm_unit.json"
        assert result.scenario["sim"]["warmup_s"] == 120.0
        assert result.chain_length_m == pytest.approx(4000.0)
        assert result.config_hash == result.demand["config_hash"]

    def test_the_fleet_block_is_recorded_in_the_artifact_and_the_summary(
        self, result: OnboardingResult, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fleet reset by a re-onboarding (docs/ONBOARDING_MNDOT.md §11) must
        show up in the demand artifact and in the printed record, not only in
        the scenario file: ``fleet_settings`` states the block the scenario
        carries, with ``idm_calibration`` applied."""
        assert result.demand["fleet_settings"] == {
            "model": "IDM",
            "heterogeneity_frac": 0.12,
            "lc_strategic": 1.0,
            "lc_strategic_ramp": None,
            "lc_keep_right": 1.0,
            "idm_calibration": "artifacts/idm_unit.json",
        }
        assert result.summary[-1] == (
            "  fleet: model IDM, heterogeneity_frac 0.12, lc_strategic 1.0, "
            "lc_strategic_ramp None, lc_keep_right 1.0, idm_calibration artifacts/idm_unit.json"
        )

        # the I-94 corridor's deliberate settings, as the record shows them
        monkeypatch.setattr(onboarding, "chain_edge_x", lambda net_path, chain: EDGE_X)
        scenario = _scenario()
        scenario["fleet"] = {
            "model": "EIDM",
            "heterogeneity_frac": 0.15,
            "lc_strategic": 5.0,
            "lc_strategic_ramp": 1.0,
            "lc_keep_right": 0.0,
        }
        deliberate = calibrate_scenario(
            scenario,
            observations=_observations(),
            stations_x=STATIONS_X,
            net_path="unused.net.xml",
            upstream="M1",
            downstream="M3",
            warmup_s=120.0,
        )
        assert deliberate.demand["fleet_settings"] == {
            "model": "EIDM",
            "heterogeneity_frac": 0.15,
            "lc_strategic": 5.0,
            "lc_strategic_ramp": 1.0,
            "lc_keep_right": 0.0,
            "idm_calibration": None,
        }
        assert deliberate.summary[-1].startswith("  fleet: model EIDM, heterogeneity_frac 0.15, ")
        assert deliberate.demand["fleet_settings"] != result.demand["fleet_settings"]

    def test_unmeasured_boundary_window_carries_the_previous_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        speeds = {sid: [25.0, 20.0] for sid in FLOWS}
        speeds["M3"] = [float("nan"), 18.0]
        monkeypatch.setattr(onboarding, "chain_edge_x", lambda net_path, chain: EDGE_X)
        out = calibrate_scenario(
            _scenario(),
            observations=_observations(speeds),
            stations_x=STATIONS_X,
            net_path="unused.net.xml",
            upstream="M1",
            downstream="M3",
        )
        # the posted limit stands in until the first measured window; nothing
        # is interpolated
        assert out.scenario["network"]["boundary"]["steps"] == [[0.0, 30.0], [WINDOW_S, 18.0]]

    def test_stations_are_moved_onto_the_chain(self, result: OnboardingResult) -> None:
        source = result.observations.source
        assert source["chain_length_m"] == pytest.approx(4000.0)
        assert source["x_m_inventory"]["D_FAR"] == pytest.approx(3800.0)
        assert result.stations_without_chain_x == ["D_FAR"]
        assert result.observations.station("M2").x_m == pytest.approx(2000.0)

    def test_summary_names_the_evidence(self, result: OnboardingResult) -> None:
        text = "\n".join(result.summary)
        assert "inflow from M1: 2 steps, peak 3000 veh/h" in text
        assert "← D_A (50.0 m)" in text
        assert "residual carried M2→M3: mean +400 veh/h" in text
        assert "boundary: 2 speed steps from M3" in text


class TestRefusals:
    def test_ramp_outside_the_observed_span_is_zeroed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(onboarding, "chain_edge_x", lambda net_path, chain: EDGE_X)
        out = calibrate_scenario(
            _scenario([_ramp("off D", "off", "e4", ["r_d"])]),
            observations=_observations(),
            stations_x=STATIONS_X,
            net_path="unused.net.xml",
            upstream="M1",
            downstream="M3",
        )
        (zeroed,) = out.zeroed_ramps
        assert zeroed.startswith("off D (x=4000 m): outside the observed span")
        d = next(r for r in out.demand["ramps"] if r["name"] == "off D")
        assert d["method"] == "zero_outside_observed_span"
        assert [v for _, v in d["exit_fraction_steps"]] == [0.0, 0.0]

    @pytest.mark.parametrize(
        ("upstream", "downstream", "needle"),
        [
            ("nope", "M3", "upstream station 'nope' is not a mainline station"),
            ("M1", "D_A", "downstream station 'D_A' is not a mainline station"),
            ("M3", "M1", "must sit before downstream station"),
        ],
    )
    def test_bad_boundary_stations_are_plain_errors(
        self, monkeypatch: pytest.MonkeyPatch, upstream: str, downstream: str, needle: str
    ) -> None:
        monkeypatch.setattr(onboarding, "chain_edge_x", lambda net_path, chain: EDGE_X)
        with pytest.raises(ValueError, match=needle):
            calibrate_scenario(
                _scenario(),
                observations=_observations(),
                stations_x=STATIONS_X,
                net_path="unused.net.xml",
                upstream=upstream,
                downstream=downstream,
            )


def test_speed_steps_carry_missing_windows() -> None:
    steps = onboarding.speed_steps([float("nan"), 22.0, float("nan")], 60.0, 31.3)
    assert steps == [[0.0, 31.3], [60.0, 22.0], [120.0, 22.0]]
    assert not any(math.isnan(v) for _, v in steps)
