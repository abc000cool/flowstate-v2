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
import sys
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
    re-measured at the 500 m vacate window; the speed-aware acceptance of
    2026-09-24 block 3 removes the collision at seed 4 and the exit-peak
    marks fail on lane 1 and, at seed 3, on the give-ups).
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
            pytest.param(
                "entrance_peak",
                3,
                marks=pytest.mark.xfail(
                    strict=True,
                    reason="Ruth St twin, the corridor's fleet at the entrance's peak, seed 3, "
                    "measured 2026-10-07 under the W1b/W2 defaults (docs/E12_PLATFORM_TESTS.md): "
                    "fails on both platforms, on the give-ups and lane 1. macOS arm64: 1 of 45 "
                    "exits given up (2.2 % against 2 %), lane 1's last 60 m 4.33 m/s in minute "
                    "17 (above 5 required), 44 of 45 exit, 0 of 31 unfinished. Linux x86_64: 2 "
                    "of 45 given up (4.4 %), lane 1's last 60 m 1.31 m/s in minute 19 (2 minutes "
                    "at or below 5), 42 of 45 exit, 3 of 49 unfinished. Both: the entrance "
                    "departs 128 of 128, no collision. The numbers differ because SUMO 1.27.1 "
                    "arithmetic differs between arm64 macOS and x86_64 Linux (this fixture is "
                    "not probed per step; its trajectories part in the first minute, as the "
                    "probed fixtures' do from steps 20-47; docs/E12_PLATFORM_TESTS.md). Earlier "
                    "readings: docs/WEAVE_MODEL_PLAN.md, short sections",
                ),
            ),
            # seed 4 passes on both platforms with the same counters (0 of 41 given up, lane
            # 1's last 60 m never below 12.61 m/s, 41 of 41 exit; docs/E12_PLATFORM_TESTS.md):
            # its non-strict mark was removed on 2026-10-07
            ("entrance_peak", 4),
            pytest.param(
                "entrance_peak",
                5,
                marks=pytest.mark.xfail(
                    strict=True,
                    reason="Ruth St twin, the corridor's fleet at the entrance's peak, seed 5, "
                    "measured 2026-10-07 under the W1b/W2 defaults (docs/E12_PLATFORM_TESTS.md): "
                    "fails on both platforms with the same counters, macOS arm64 and Linux "
                    "x86_64 alike: lane 1's last 60 m 1.09 m/s in minute 19 (above 5 required), "
                    "3 of 29 driven vehicles unfinished (at most 2.9), 32 of 34 exit, none given "
                    "up, the entrance departs 128 of 128, no collision. The minute speeds agree "
                    "to 3e-14 m/s: SUMO 1.27.1 arithmetic differs between arm64 macOS and x86_64 "
                    "Linux (not probed per step; the trajectories part in the first minute; "
                    "docs/E12_PLATFORM_TESTS.md) without moving this run. Earlier readings: "
                    "docs/WEAVE_MODEL_PLAN.md, short sections",
                ),
            ),
            pytest.param(
                "exit_peak",
                3,
                marks=pytest.mark.xfail(
                    strict=True,
                    reason="Ruth St twin, the corridor's fleet at the C-D split's exit peak, "
                    "seed 3, measured 2026-10-07 under the W1b/W2 defaults "
                    "(docs/E12_PLATFORM_TESTS.md): fails on both platforms with the same "
                    "counters, macOS arm64 and Linux x86_64 alike: 15 of 290 exits given up "
                    "(5.2 % against 2 %), lane 1's last 60 m 2.58 m/s in minute 12 (3 minutes at "
                    "or below 5; above 5 required), 273 of 290 exit, 0 of 159 unfinished, the "
                    "entrance departs 73 of 73, no collision. The minute speeds agree to 2e-13 "
                    "m/s: SUMO 1.27.1 arithmetic differs between arm64 macOS and x86_64 Linux "
                    "(not probed per step; the trajectories part in the first minute; "
                    "docs/E12_PLATFORM_TESTS.md) without moving this run. The 500 m vacate "
                    "window, the seed-4 collision and the speed-aware acceptance that removed "
                    "it: docs/WEAVE_MODEL_PLAN.md, short sections, and TestExitSideAcceptance",
                ),
            ),
            pytest.param(
                "exit_peak",
                4,
                marks=pytest.mark.xfail(
                    strict=True,
                    reason="Ruth St twin, the corridor's fleet at the C-D split's exit peak, "
                    "seed 4, measured 2026-10-07 under the W1b/W2 defaults "
                    "(docs/E12_PLATFORM_TESTS.md): fails on both platforms with the same "
                    "counters, macOS arm64 and Linux x86_64 alike: lane 1's last 60 m 4.97 m/s "
                    "in minute 8 (above 5 required), 2 of 281 exits given up (0.7 %, within 2 "
                    "%), 278 of 281 exit, 0 of 76 unfinished, the entrance departs 73 of 73, no "
                    "collision. The minute speeds agree to 3e-14 m/s: SUMO 1.27.1 arithmetic "
                    "differs between arm64 macOS and x86_64 Linux (not probed per step; the "
                    "trajectories part in the first minute; docs/E12_PLATFORM_TESTS.md) without "
                    "moving this run. Earlier readings: docs/WEAVE_MODEL_PLAN.md, short sections",
                ),
            ),
            pytest.param(
                "exit_peak",
                5,
                marks=pytest.mark.xfail(
                    condition=sys.platform == "linux",
                    strict=True,
                    reason="Ruth St twin, the corridor's fleet at the C-D split's exit peak, "
                    "seed 5, per platform, measured 2026-10-07 under the W1b/W2 defaults "
                    "(docs/E12_PLATFORM_TESTS.md): asserted to pass on macOS and to fail on "
                    "Linux. macOS arm64 meets every criterion: 4 of 274 exits given up (1.5 %, "
                    "within 2 %), lane 1's last 60 m never below 5.55 m/s, 266 of 274 exit, 2 "
                    "of 71 unfinished. Linux x86_64 fails on the give-ups and lane 1: 6 of 274 "
                    "given up (2.2 %), lane 1's last 60 m 4.29 m/s in minute 19 (above 5 "
                    "required), 267 of 274 exit, 0 of 77 unfinished. Both: the entrance departs "
                    "73 of 73, no collision. SUMO 1.27.1 arithmetic differs between arm64 macOS "
                    "and x86_64 Linux from step 20 (docs/E12_PLATFORM_TESTS.md); the runner's "
                    "commands stay identical to step 117. On 0b9ab40 the Linux runner gave up 7 "
                    "of 274 (docs/WEAVE_MODEL_PLAN.md)",
                ),
            ),
        ],
    )
    def test_short_section_with_the_corridor_fleet(self, tmp_path, seed, demand):
        cfg = ruth_config(seed, **RUTH_DEMAND[demand], fleet=CORRIDOR_FLEET)
        paths = run_micro(cfg, seed, tmp_path / f"ruth_fleet_{demand}_{seed}")
        meta = json.loads(paths.meta.read_text())
        assert_exit_side(paths, meta)


class TestExitSideAcceptance:
    """The two seeded collisions of the exit-side acceptance (2026-09-24, block
    3, the short section at the 500 m window; docs/WEAVE_MODEL_PLAN.md).

    Corridor fleet at the C-D split's exit peak. At the 500 m vacate window,
    seed 4 collided at t = 600.5 s on ``102_0`` at 35.5 m: exiter v00447
    changed into the auxiliary lane 15 m into the section at 23 m/s with a
    27 m gap to a queue head at 1.0 m/s, braked at −9 m/s² and stopped 1.9 m
    short; v00450 did the same 3 s later at 20 m/s with 22 m and hit it. At
    the 271.4 m window (twice the section's length, the cap measured and
    rejected) seed 5 collided at t = 717.5 s at 39.2 m by the same trace
    (v00542 into v00539). The exit-side acceptance was a time gap at the
    changer's own speed, ``s0 + 0.6 · 23 = 16 m``, with no term for the
    leader's; the speed-aware acceptance (the changer's IDM desired gap at
    its speed and closing rate, ``microsim.runner._weave_lead_gap_min``, in
    the acceptance and the forced guard) refuses both changes.
    """

    @pytest.mark.parametrize(
        ("seed", "vacate_ahead_m"),
        [(4, 500.0), (5, 271.4)],
        ids=["seed4_window500m", "seed5_window271m"],
    )
    def test_no_collision_at_the_traced_seed_and_window(self, tmp_path, seed, vacate_ahead_m):
        cfg = ruth_config(
            seed,
            **RUTH_DEMAND["exit_peak"],
            weave_params={"vacate_ahead_m": vacate_ahead_m},
            fleet=CORRIDOR_FLEET,
        )
        paths = run_micro(cfg, seed, tmp_path / f"ruth_accept_{seed}")
        meta = json.loads(paths.meta.read_text())
        (ws,) = meta["weave_sections"]
        assert meta["n_collisions"] == 0, meta["collisions"]
        assert ws["n_exited"] >= 0.9 * ws["n_reached_section_exiting"], ws
