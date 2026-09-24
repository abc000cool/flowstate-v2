"""Collector–distributor pairs in the balance step (:mod:`calibration.onboarding`).

The hand-built corridor of ``test_calibration_onboarding.py`` with a C-D
road added: the split leaves at the end of ``e2`` (x = 2000 m, bracket
M1→M2) and the re-entry joins at the start of ``e4`` (x = 3000 m, bracket
M2→M3). Numbers are chosen so every assertion is arithmetic::

    x [m]      0        1000       2000       3000       4000
               |---e1---|---e2---|---e3---|---e4---|
    stations   M1                 M2         M3
    ramps               on A     split      re-entry
                        (live)   (live 300) (no detector)

Bracket M1→M2 (3000 → 3600, +600): A's detector fixes 400, the split's
detector fixes 300 out, the dead on-ramp B takes the remaining 500.
Bracket M2→M3 (3600 → 4000, +400): the only entrance is the C-D re-entry,
which first takes back the 300 its split sent out and then the bracket's
remaining 100 — so the pair's net exchange is +100 veh/h and the bracket
that carried a 400 veh/h residual without the re-entry now closes.
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
}
WINDOW_S = 300.0
N_WINDOWS = 2
FLOWS: dict[str, list[float]] = {
    "M1": [3000.0, 3000.0],
    "M2": [3600.0, 3600.0],
    "M3": [4000.0, 4000.0],
    "D_A": [400.0, 400.0],
    "D_CD": [300.0, 300.0],  # detector on the C-D split
}
STATIONS_X: dict[str, float] = {
    "M1": 0.0,
    "M2": 2000.0,
    "M3": 3000.0,
    "D_A": 1050.0,
    "D_CD": 2000.0,
}


def _observations() -> Observations:
    ids = list(FLOWS)
    return Observations(
        corridor="unit_cd",
        source={"provider": "hand-built fixture"},
        window_s=WINDOW_S,
        t0_local="06:00",
        duration_s=WINDOW_S * N_WINDOWS,
        stations=(
            ObservedStation(id="M1", label="upstream", x_m=0.0, lanes=3, kind="mainline"),
            ObservedStation(id="M2", label="middle", x_m=2000.0, lanes=3, kind="mainline"),
            ObservedStation(
                id="M3",
                label="downstream",
                x_m=3000.0,
                lanes=3,
                kind="mainline",
                speed_limit_ms=30.0,
            ),
            ObservedStation(id="D_A", label="ramp A", x_m=1050.0, lanes=1, kind="on_ramp"),
            ObservedStation(id="D_CD", label="C-D split", x_m=2000.0, lanes=1, kind="off_ramp"),
        ),
        flows_veh_h={sid: list(FLOWS[sid]) for sid in ids},
        speeds_ms={sid: [25.0, 20.0] for sid in ids},
        occupancy_pct={sid: [10.0, 12.0] for sid in ids},
    )


def _ramp(name: str, kind: str, attach: str, edges: list[str], **extra: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "kind": kind,
        "edges": edges,
        "attach_edge": attach,
        "name": name,
        "inflow": [[0.0, 0.0]] if kind == "on" else [],
        "exit_fraction": [[0.0, 0.0]] if kind == "off" else [],
    }
    spec.update(extra)
    return spec


def _scenario(extra_ramps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    ramps = [
        _ramp("on A", "on", "e2", ["r_a"]),
        _ramp("on B", "on", "e3", ["r_b"]),
        _ramp("C-D split", "off", "e2", ["cd_1"], cd_road=True, cd_pair="cd_1"),
        _ramp("C-D re-entry", "on", "e4", ["cd_2"], cd_road=True, cd_pair="cd_1"),
    ]
    ramps += extra_ramps or []
    return {
        "name": "unit_cd",
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


def _run(monkeypatch: pytest.MonkeyPatch, scenario: dict[str, Any]) -> OnboardingResult:
    monkeypatch.setattr(onboarding, "chain_edge_x", lambda net_path, chain: EDGE_X)
    return calibrate_scenario(
        scenario,
        observations=_observations(),
        stations_x=STATIONS_X,
        net_path="unused.net.xml",
        upstream="M1",
        downstream="M3",
        warmup_s=120.0,
    )


@pytest.fixture()
def result(monkeypatch: pytest.MonkeyPatch) -> OnboardingResult:
    return _run(monkeypatch, _scenario())


def _by_name(result: OnboardingResult, name: str) -> dict[str, Any]:
    return next(r for r in result.demand["ramps"] if r["name"] == name)


class TestPairClosing:
    def test_split_takes_its_detector_and_reentry_returns_it_plus_the_net(
        self, result: OnboardingResult
    ) -> None:
        split = _by_name(result, "C-D split")
        assert split["method"] == "detector" and split["station"] == "D_CD"
        # 300 of the 3900 veh/h arriving at x = 2000 m (3000 + A 400 + B 500).
        assert [f for _, f in split["exit_fraction_steps"]] == pytest.approx([300.0 / 3900.0] * 2)
        reentry = _by_name(result, "C-D re-entry")
        assert reentry["method"] == "conservation"
        assert [v * 3600.0 for _, v in reentry["inflow_steps"]] == pytest.approx([400.0, 400.0])

    def test_the_bracket_no_longer_carries_a_residual(self, result: OnboardingResult) -> None:
        assert result.residuals == [] and result.demand["bracket_residuals"] == []
        assert result.demand["coverage"]["n_brackets_with_residual"] == 0.0

    def test_pair_record_states_out_back_and_net(self, result: OnboardingResult) -> None:
        (pair,) = result.cd_pairs
        assert pair["pair"] == "cd_1"
        assert (pair["split"], pair["reentry"]) == ("C-D split", "C-D re-entry")
        assert (pair["split_x_m"], pair["reentry_x_m"]) == (2000.0, 3000.0)
        assert (pair["split_method"], pair["reentry_method"]) == ("detector", "conservation")
        assert pair["reentry_bracket"] == ["M2", "M3"]
        assert pair["mean_out_veh_h"] == pytest.approx(300.0)
        assert pair["mean_back_veh_h"] == pytest.approx(400.0)
        assert pair["mean_net_veh_h"] == pytest.approx(100.0)
        assert result.demand["cd_pairs"] == result.cd_pairs
        assert result.demand["coverage"]["n_cd_pairs"] == 1.0

    def test_summary_names_the_pair(self, result: OnboardingResult) -> None:
        line = next(s for s in result.summary if "C-D pair cd_1" in s)
        assert "out 300 veh/h" in line and "back 400 veh/h" in line and "net +100 veh/h" in line
        assert "closes bracket M2→M3" in line

    def test_mainline_flow_reproduces_every_station_count(self, result: OnboardingResult) -> None:
        q = 3000.0
        for name in ("on A", "on B"):
            q += _by_name(result, name)["inflow_steps"][0][1] * 3600.0
        q -= _by_name(result, "C-D split")["exit_fraction_steps"][0][1] * q
        assert q == pytest.approx(3600.0)
        q += _by_name(result, "C-D re-entry")["inflow_steps"][0][1] * 3600.0
        assert q == pytest.approx(4000.0)


class TestFirstClaim:
    def test_reentry_takes_the_split_outflow_before_sharing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A second dead entrance in the re-entry's bracket: the re-entry first
        # takes back the 300 its split sent out, then the remaining 100 is
        # shared equally.
        result = _run(monkeypatch, _scenario([_ramp("on D", "on", "e4", ["r_d"])]))
        reentry = _by_name(result, "C-D re-entry")["inflow_steps"][0][1] * 3600.0
        other = _by_name(result, "on D")["inflow_steps"][0][1] * 3600.0
        assert reentry == pytest.approx(350.0) and other == pytest.approx(50.0)
        assert result.cd_pairs[0]["mean_net_veh_h"] == pytest.approx(50.0)
        assert result.residuals == []

    def test_a_split_without_its_reentry_is_a_plain_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scenario = _scenario()
        scenario["network"]["ramps"] = [
            r for r in scenario["network"]["ramps"] if r["name"] != "C-D re-entry"
        ]
        result = _run(monkeypatch, scenario)
        (pair,) = result.cd_pairs
        assert pair["reentry"] is None and "missing" in pair["note"]
        # Bracket M2→M3 has no entrance again: the 400 veh/h is a residual.
        assert [r["mean_residual_veh_h"] for r in result.residuals] == [400.0]

    def test_scenario_without_pairs_reports_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scenario = _scenario()
        for r in scenario["network"]["ramps"]:
            r.pop("cd_road", None)
            r.pop("cd_pair", None)
        result = _run(monkeypatch, scenario)
        assert result.cd_pairs == [] and result.demand["cd_pairs"] == []
        assert result.demand["coverage"]["n_cd_pairs"] == 0.0
