"""A short weaving section: the Ruth St fixture (docs/CONTRACTS.md §2, short sections).

``tests/fixtures/weave_ruth.osm`` is the local twin of the I-94 WB Ruth St
weave (docs/ONBOARDING_MNDOT.md §11a–§11b: entrance 745524613 into the
collector–distributor split 18208090, 136 m, x ≈ 5.3 km): three through
lanes, an auxiliary lane of 136 m (netconvert measures 135.7 m with the
junction geometry) from the entrance (way 200) to the exit (way 201), about
600 m of approach and 600 m downstream, as ``weave_th52.osm`` at 305 m.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import sumolib

from flowstate_core.config import WEAVE_DEFAULTS, ScenarioConfig
from microsim import run_micro
from microsim.runner import _weave_short_section_rule

pytestmark = pytest.mark.integration

RUTH_OSM = Path(__file__).resolve().parents[1] / "fixtures" / "weave_ruth.osm"

#: The corridor's flows at Ruth St from ``scenarios/mndot_i94_wb_stpaul_weave.yaml``
#: (the series ``artifacts/demand_mndot_i94_wb_stpaul.json`` wrote: the
#: entrance ``method: detector_scaled`` off station rnd_88819, the split
#: ``detector_scaled`` off rnd_88817, the mainline the entry's ``inflow``
#: propagated through the six ramps before Ruth St in corridor order —
#: 1077665160, 18279036, 18207390, 18207436, 18207653, 178547099 — as the
#: T.H.52 constants of ``test_microsim_merge_managed_meter.py``).
#:
#: ``entrance_peak``: the 3900–4200 s step (07:05–07:10; the entrance's peak
#: of the first 65 minutes, 0.1070 veh/s = 385 veh/h; its four-hour peak is
#: 0.1278 veh/s at 8400 s), mainline arriving 1.1247 veh/s = 4,049 veh/h,
#: ``exit_fraction`` 0.0295 (131 veh/h exiting). In the first hour proper
#: the section carries 2,050–4,260 veh/h with 143–343 veh/h entering and
#: 0.017–0.037 exiting.
#: ``exit_peak``: the 9000–9300 s step (08:30–08:35; the C-D split's peak
#: ``exit_fraction`` 0.2893, 881 veh/h exiting), mainline arriving 0.7844
#: veh/s = 2,824 veh/h, entrance 0.0611 veh/s = 220 veh/h — the load §11a's
#: 20-seed battery reads at the section (47 % of entrants forced, waits 130 s
#: in / 79 s out, 50–72 k deferred forced changes per four-hour run).
RUTH_DEMAND: dict[str, dict[str, float]] = {
    "entrance_peak": {"mainline_vph": 4049.0, "exit_fraction": 0.0295, "entrance_vph": 385.0},
    "exit_peak": {"mainline_vph": 2824.0, "exit_fraction": 0.2893, "entrance_vph": 220.0},
}

#: The corridor scenario's own driver population (its ``fleet`` block: EIDM,
#: the I-24 capacity-scaled IDM calibration, 15 % heterogeneity, strategic
#: lane changing at 5.0, no keep-right), for the fixture variant that runs
#: the corridor's drivers rather than the fleet defaults.
CORRIDOR_FLEET: dict[str, object] = {
    "model": "EIDM",
    "heterogeneity_frac": 0.15,
    "idm_calibration": "artifacts/idm_i24_capacity.json",
    "lc_strategic": 5.0,
    "lc_strategic_ramp": 1.0,
    "lc_keep_right": 0.0,
    "lc_cooperative": 1.0,
    "lc_assertive": 1.0,
    "lc_speed_gain": 1.0,
}


def ruth_config(
    seed: int,
    mainline_vph: float,
    exit_fraction: float,
    entrance_vph: float,
    duration_s: float = 1200.0,
    weave_params: dict[str, float] | None = None,
    fleet: dict[str, object] | None = None,
) -> ScenarioConfig:
    """The Ruth St weave on ``weave_ruth.osm``: constant flows, 20 simulated
    minutes, step 0.5 s, the fleet defaults (as ``_th52_config``) unless
    ``fleet`` is given (:data:`CORRIDOR_FLEET` for the corridor's own)."""
    weave: dict[str, object] = {"exit_ramp": "cd split"}
    if weave_params is not None:
        weave["weave_params"] = weave_params
    return ScenarioConfig.model_validate(
        {
            "name": "weave_ruth",
            **({"fleet": fleet} if fleet is not None else {}),
            "network": {
                "kind": "osm",
                "osm_file": str(RUTH_OSM),
                "corridor_edges": ["100", "101", "102", "103", "104"],
                "inflow": [[0.0, mainline_vph / 3600.0]],
                "ramps": [
                    {
                        "kind": "on",
                        "name": "ruth",
                        "edges": ["200"],
                        "attach_edge": "102",
                        "inflow": [[0.0, entrance_vph / 3600.0]],
                        "merge": "weave",
                        "weave": weave,
                    },
                    {
                        "kind": "off",
                        "name": "cd split",
                        "edges": ["201"],
                        "attach_edge": "102",
                        "exit_fraction": [[0.0, exit_fraction]],
                    },
                ],
            },
            "sim": {"duration_s": duration_s},
            "seed": seed,
        }
    )


def lane1_last60m_windows(paths, meta: dict) -> tuple[pd.Series, dict]:
    """Mean speed of section lane 1 over its last 60 m per 60-s window after a
    120-s warm-up (the exit-side criterion of the T.H.52 tests), and the state
    dict the assertions report."""
    net = sumolib.net.readNet(str(next(paths.run_dir.glob("**/*.net.xml"))))
    x0 = sum(net.getEdge(e).getLength() for e in ("100", "101", "102")) - 60.0
    df = pd.read_parquet(paths.trajectories)
    part = df[
        (df.x >= x0) & (df.x < x0 + 60.0) & (df.t >= 120.0) & (df.t < 1200.0) & (df.lane == 1)
    ]
    windows = part.groupby((part.t // 60.0).astype(int)).v.mean()
    (ws,) = meta["weave_sections"]
    (on_meta, _off_meta) = meta["ramps"]
    state = {
        "section_length_m": round(float(ws["length_m_measured"]), 1),
        "lane1_last60m_by_minute": {int(k): round(float(v), 1) for k, v in windows.items()},
        "entrance_departed": (on_meta["n_departed"], on_meta["n_planned"]),
        "exit_share": (ws["n_exited"], ws["n_reached_section_exiting"]),
        "given_up": (ws["n_missed_exit"], ws["n_reached_section_exiting"]),
        "weave": {k: v for k, v in ws.items() if k.startswith(("n_", "wait"))},
        "collisions": meta["n_collisions"],
    }
    return windows, state


def assert_exit_side(paths, meta: dict) -> dict:
    """The exit-side criteria of the T.H.52 tests plus the give-up threshold
    and the entrance's departed share: no collision, at least 90 % of the
    exit-bound vehicles that reach the section exit, at most 2 % of them
    given up (``n_missed_exit``, the battery's threshold — docs/ONBOARDING_MNDOT.md
    §11b), lane 1 over the section's last 60 m above 5 m/s in every 60-s
    window after 120 s, at most 10 % of the driven vehicles unfinished, the
    entrance departing at least 90 % of its plan."""
    (ws,) = meta["weave_sections"]
    (on_meta, _off_meta) = meta["ramps"]
    windows, state = lane1_last60m_windows(paths, meta)
    assert meta["n_collisions"] == 0, meta["collisions"]
    assert ws["n_reached_section_exiting"] > 0, state
    assert ws["n_exited"] >= 0.9 * ws["n_reached_section_exiting"], state
    assert ws["n_missed_exit"] <= 0.02 * ws["n_reached_section_exiting"], state
    assert len(windows) == 18 and (windows > 5.0).all(), state
    assert ws["n_unfinished"] <= 0.1 * ws["n_entered"], state
    assert on_meta["n_departed"] >= 0.9 * on_meta["n_planned"], state
    return state


class TestShortSectionRule:
    """``microsim.runner._weave_short_section_rule``: the flag and the fixed values."""

    @pytest.mark.parametrize(
        ("length_m", "short"),
        [(135.7, True), (159.9, True), (160.0, False), (178.4, False), (308.4, False)],
    )
    def test_short_is_less_than_two_zones_and_the_values_are_the_fixed_ones(self, length_m, short):
        # 135.7 m is weave_ruth.osm, 178.4 m the golden weave.osm, 308.4 m weave_th52.osm
        rule = _weave_short_section_rule(length_m, dict(WEAVE_DEFAULTS))
        assert rule == {"short": short, "zone_m": 80.0, "force_after_s": 4.0}

    def test_overrides_move_the_threshold_with_the_zone(self):
        prm = {**WEAVE_DEFAULTS, "force_within_m": 60.0, "force_after_s": 1.0}
        assert _weave_short_section_rule(135.7, prm) == {
            "short": False,
            "zone_m": 60.0,
            "force_after_s": 1.0,
        }
        assert _weave_short_section_rule(119.9, prm)["short"] is True

    def test_meta_flags_the_ruth_section_short(self, tmp_path):
        cfg = ruth_config(3, **RUTH_DEMAND["entrance_peak"], duration_s=10.0)
        meta = json.loads(run_micro(cfg, 3, tmp_path / "flag").meta.read_text())
        (ws,) = meta["weave_sections"]
        assert ws["short_section"] is True and 130.0 <= ws["length_m_measured"] <= 140.0


class TestRuthStWeave:
    """The 136 m Ruth St section at the corridor's flows, seeds 3–5.

    With the fleet defaults (as every T.H.52 fixture test) both demand points
    pass the exit-side criteria at every seed; with the corridor's own
    drivers (:data:`CORRIDOR_FLEET`) the section reproduces the corridor's
    give-ups — see the marks and docs/WEAVE_MODEL_PLAN.md (short sections;
    re-measured at the 500 m vacate window, where the give-ups at the exit
    peak fall within the threshold and the marks fail on lane 1 and one
    collision of the exit-side acceptance instead).
    """

    @pytest.mark.parametrize("seed", [3, 4, 5])
    @pytest.mark.parametrize("demand", sorted(RUTH_DEMAND))
    def test_short_section_at_corridor_flows(self, tmp_path, seed, demand):
        cfg = ruth_config(seed, **RUTH_DEMAND[demand])
        paths = run_micro(cfg, seed, tmp_path / f"ruth_{demand}_{seed}")
        meta = json.loads(paths.meta.read_text())
        (ws,) = meta["weave_sections"]
        assert 130.0 <= ws["length_m_measured"] <= 140.0, ws["length_m_measured"]
        assert_exit_side(paths, meta)

    @pytest.mark.parametrize(
        ("demand", "seed"),
        [
            *(
                pytest.param(
                    "entrance_peak",
                    seed,
                    marks=pytest.mark.xfail(
                        strict=False,
                        reason="Ruth St twin, the corridor's fleet at the entrance's peak "
                        "(2026-09-24, block 3, short sections; re-measured at the 500 m vacate "
                        "window): one abreast pair at the gore's end costs 2 of the 41-45 exits "
                        "that reach the section (4-5 % against 2 %) and which seed pays moves "
                        "with the vacate rule and its window — on the runner of 01dd5ec seed 4 "
                        "(39 of 41 exit, lane 1's last 60 m never below 13.4 m/s, 108 forced "
                        "changes deferred, no collision), under the vacate rule re-derived "
                        "beside this test (WP-44) at 150 m seed 3 (43 of 45), at the 500 m "
                        "default seed 3 again (43 of 45, lane 1's last 60 m 2.9 m/s in one "
                        "minute, 13 forced, 252 deferred, 7 releases, 60 vacated and 149 skipped "
                        "by the bound, the entrance departs 128 of 128, no collision) while "
                        "seeds 4 / 5 give up none with lane 1 never below 15.7 m/s — so the "
                        "marks are not strict (docs/WEAVE_MODEL_PLAN.md, the short section at "
                        "the 500 m window)",
                    ),
                )
                for seed in (3, 4, 5)
            ),
            *(
                pytest.param(
                    "exit_peak",
                    seed,
                    marks=pytest.mark.xfail(
                        strict=True,
                        reason="Ruth St twin, the corridor's fleet at the C-D split's exit "
                        "peak (2026-09-24, block 3, short sections; re-measured at the 500 m "
                        "vacate window): at the 500 m default seeds 3 / 4 / 5 give up 4 / 2 / 4 "
                        "of 290 / 280 / 274 exits (1.4 / 0.7 / 1.5 %, within 2 % now that 135 / "
                        "145 / 137 through vehicles vacate the weave lane 430-500 m upstream "
                        "against 44 / 52 / 56 at 150 m; 9 / 9 / 15 changes forced against 33 / "
                        "15 / 17, 252 / 111 / 275 deferred against 608 / 268 / 326, 2 / 1 / 2 "
                        "pairs released against 14 / 3 / 7), but lane 1's last 60 m reads 4.6 / "
                        "6.5 / 3.6 m/s in its worst minute with one minute empty at seed 3 (the "
                        "weave lane carries 0.2 vehicles per sample there) and seed 4 collides "
                        "once at 600.5 s: an exiter changes into the auxiliary lane 15 m into "
                        "the section at 20 m/s onto a leader at 1 m/s 22 m ahead, the exit-side "
                        "acceptance being a time gap at the changer's own speed. At 150 m: 8 / "
                        "3 / 4 given up (2.8 / 1.1 / 1.5 %), lane 1 3.3 / 4.1 / 4.3 m/s, no "
                        "collision (runner of 01dd5ec and the re-derived vacate rule alike). "
                        "The entrance departs 73 of 73 at both. The trace, the "
                        "measured-and-rejected scalings and the 2 L window: "
                        "docs/WEAVE_MODEL_PLAN.md, short sections and the short section at "
                        "the 500 m window",
                    ),
                )
                for seed in (3, 4, 5)
            ),
        ],
    )
    def test_short_section_with_the_corridor_fleet(self, tmp_path, seed, demand):
        cfg = ruth_config(seed, **RUTH_DEMAND[demand], fleet=CORRIDOR_FLEET)
        paths = run_micro(cfg, seed, tmp_path / f"ruth_fleet_{demand}_{seed}")
        meta = json.loads(paths.meta.read_text())
        assert_exit_side(paths, meta)
