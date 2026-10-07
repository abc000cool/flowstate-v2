"""The demand method's balance rules (:mod:`calibration.onboarding`, 2026-10-07).

``carry_residuals``, ``skip_stations`` and ``ignore_ramp_detectors`` are off by
default; each is checked here against hand arithmetic on a corridor built so
that every rule changes a number.

Geometry (five 1 km chain edges; an on-ramp sits at the start of the edge it
joins, an exit at the end of the edge it leaves)::

    x [m]      0      1000     2000     3000     4000     4500  5000
               |--e1--|--e2--|--e3--|--e4--|--e5--------|
    stations   M1              M2       M3              M4
    ramps             on A              off C    on E
                      (D_A live)        (dead)   (dead)

Counts [veh/h]: M1 3000, M2 3600, M3 3300, M4 3500; D_A reads 800, more than
the +600 the mainline gains across M1→M2 (the I-94 T.H.61 NB pattern). By
default the −200 nobody can explain in M1→M2 is carried and lands on exit C
(500 instead of the bracket's own 300).
"""

from __future__ import annotations

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
    "e5": (4000.0, 5000.0),
}
WINDOW_S = 300.0
FLOWS: dict[str, list[float]] = {
    "M1": [3000.0, 3000.0],
    "M2": [3600.0, 3600.0],
    "M3": [3300.0, 3300.0],
    "M4": [3500.0, 3500.0],
    "D_A": [800.0, 800.0],
}
STATIONS_X = {"M1": 0.0, "M2": 2000.0, "M3": 3000.0, "M4": 4500.0, "D_A": 1050.0}


def _observations() -> Observations:
    stations = (
        ObservedStation(id="M1", label="m1", x_m=0.0, lanes=3, kind="mainline"),
        ObservedStation(id="M2", label="m2", x_m=2000.0, lanes=3, kind="mainline"),
        ObservedStation(id="M3", label="m3", x_m=3000.0, lanes=3, kind="mainline"),
        ObservedStation(
            id="M4", label="m4", x_m=4500.0, lanes=3, kind="mainline", speed_limit_ms=30.0
        ),
        ObservedStation(id="D_A", label="ramp A", x_m=1050.0, lanes=1, kind="on_ramp"),
    )
    return Observations(
        corridor="rules_corridor",
        source={"provider": "hand-built fixture"},
        window_s=WINDOW_S,
        t0_local="06:00",
        duration_s=2 * WINDOW_S,
        stations=stations,
        flows_veh_h={k: list(v) for k, v in FLOWS.items()},
        speeds_ms={k: [25.0, 25.0] for k in FLOWS},
        occupancy_pct={k: [10.0, 10.0] for k in FLOWS},
    )


def _ramp(name: str, kind: str, attach: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "edges": [f"r_{name[-1].lower()}"],
        "attach_edge": attach,
        "name": name,
        "inflow": [[0.0, 0.0]] if kind == "on" else [],
        "exit_fraction": [] if kind == "on" else [[0.0, 0.0]],
    }


def _scenario() -> dict[str, Any]:
    return {
        "name": "rules_corridor",
        "tier": "micro",
        "network": {
            "kind": "osm",
            "bbox": [44.9, -93.3, 45.0, -93.2],
            "corridor_edges": list(EDGE_X),
            "inflow": [[0.0, 0.5]],
            "ramps": [
                _ramp("on A", "on", "e2"),
                _ramp("off C", "off", "e3"),
                _ramp("on E", "on", "e5"),
            ],
        },
        "fleet": {"model": "IDM"},
        "sim": {"duration_s": 600.0},
        "seed": 1,
        "replicates": 1,
    }


@pytest.fixture(autouse=True)
def _no_net(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboarding, "chain_edge_x", lambda net_path, chain: EDGE_X)


def _run(**rules: Any) -> OnboardingResult:
    return calibrate_scenario(
        _scenario(),
        observations=_observations(),
        stations_x=STATIONS_X,
        net_path="unused.net.xml",
        upstream="M1",
        downstream="M4",
        **rules,
    )


def _ramp_rec(result: OnboardingResult, name: str) -> dict[str, Any]:
    return next(r for r in result.demand["ramps"] if r["name"] == name)


def _veh_h(result: OnboardingResult, name: str) -> list[float]:
    rec = _ramp_rec(result, name)
    if rec["kind"] == "on":
        return [v * 3600.0 for _, v in rec["inflow_steps"]]
    return [v for _, v in rec["exit_fraction_steps"]]


class TestDefaultsUnchanged:
    def test_the_residual_is_carried_onto_the_next_exit(self) -> None:
        r = _run()
        assert _veh_h(r, "on A") == pytest.approx([800.0, 800.0])
        # −300 own change plus the −200 carried from M1→M2, of the 3600 arriving
        assert _veh_h(r, "off C") == pytest.approx([500.0 / 3600.0] * 2)
        assert _veh_h(r, "on E") == pytest.approx([200.0, 200.0])
        (res,) = r.residuals
        assert (res["from"], res["to"], res["mean_residual_veh_h"]) == ("M1", "M2", -200.0)
        assert "carried into the next bracket" in res["note"]

    def test_no_rules_record_and_no_summary_line(self) -> None:
        r = _run()
        assert "balance_rules" not in r.demand
        assert not any("balance rules" in line for line in r.summary)
        assert "detector_not_used" not in _ramp_rec(r, "on A")


class TestNoCarry:
    def test_each_exit_takes_only_its_own_segment(self) -> None:
        r = _run(carry_residuals=False)
        assert _veh_h(r, "on A") == pytest.approx([800.0, 800.0])
        assert _veh_h(r, "off C") == pytest.approx([300.0 / 3600.0] * 2)
        assert _veh_h(r, "on E") == pytest.approx([200.0, 200.0])

    def test_the_residual_is_recorded_not_dropped_silently(self) -> None:
        r = _run(carry_residuals=False)
        (res,) = r.residuals
        assert (res["from"], res["to"], res["mean_residual_veh_h"]) == ("M1", "M2", -200.0)
        assert "not carried" in res["note"]
        assert r.demand["bracket_residuals"] == r.residuals
        rules = r.demand["balance_rules"]
        assert rules["carry_residuals"] is False
        assert rules["skipped_stations"] == [] and rules["ignored_ramp_detectors"] == []
        assert r.summary[-2].startswith("  balance rules: residuals not carried")
        assert r.summary[-1].startswith("  fleet: ")  # the fleet line stays last


class TestIgnoredDetector:
    def test_the_ramp_closes_its_bracket_by_conservation(self) -> None:
        r = _run(ignore_ramp_detectors={"D_A": "reads more than the mainline gains"})
        a = _ramp_rec(r, "on A")
        assert a["method"] == "conservation"
        assert a["station"] == "D_A"  # still matched, so not reported as unmatched
        assert a["detector_not_used"] == "reads more than the mainline gains"
        assert _veh_h(r, "on A") == pytest.approx([600.0, 600.0])
        assert _veh_h(r, "off C") == pytest.approx([300.0 / 3600.0] * 2)
        assert r.residuals == []
        assert r.unmatched_detectors == []
        assert r.demand["balance_rules"]["ignored_ramp_detectors"] == [
            {"detector": "D_A", "reason": "reads more than the mainline gains"}
        ]
        assert r.demand["coverage"]["n_ramps_detector"] == 0.0

    def test_an_unmatched_detector_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not matched to any ramp"):
            _run(ignore_ramp_detectors={"NOPE": "x"})


class TestSkippedStation:
    def test_the_brackets_either_side_merge(self) -> None:
        r = _run(carry_residuals=False, skip_stations={"M2": "undercounts"})
        # M1→M3: +300 observed, D_A fixes 800, so exit C takes 500 of the 3800 arriving
        assert _veh_h(r, "on A") == pytest.approx([800.0, 800.0])
        assert _veh_h(r, "off C") == pytest.approx([500.0 / 3800.0] * 2)
        assert r.residuals == []
        assert r.demand["balance_rules"]["skipped_stations"] == [
            {"station": "M2", "reason": "undercounts"}
        ]

    def test_the_skipped_station_stays_in_the_observations(self) -> None:
        r = _run(skip_stations={"M2": "undercounts"})
        assert "M2" in {s.id for s in r.observations.stations}
        assert r.observations.flows_veh_h["M2"] == FLOWS["M2"]

    @pytest.mark.parametrize("sid", ["M1", "M4", "D_A", "M9"])
    def test_only_interior_mainline_stations_can_be_skipped(self, sid: str) -> None:
        with pytest.raises(ValueError, match="interior mainline station"):
            _run(skip_stations={sid: "x"})

    def test_a_rule_needs_a_reason(self) -> None:
        with pytest.raises(ValueError, match="a reason is required"):
            _run(skip_stations={"M2": "  "})
