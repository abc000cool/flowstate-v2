"""The T.H.61 lane end: entrance 53062592 to exit 18207912 on I-94 WB (WP-66).

VM R (docs/ONBOARDING_MNDOT.md §11) mapped a corridor seed that locks, from
minute 155, with the standstill ending at 8.45-8.50 km: the end of edge
``45608485``, where the two lanes the T.H.61 NB entrance adds at 7.39 km
leave as the Mounds / Kellogg exit ``18207912``. On the corrected map
(docs/ONBOARDING_MNDOT.md §9) that stretch is a two-lane auxiliary
connection of 1,135.6 m from an entrance to an exit — a weave by the
engine's own detector (:func:`microsim.networks.weave_sections`) and by the
HCM 7th ed. ch. 13 maximum weaving length — which the corridor scenario runs
on ``merge: lane_change`` (docs/WEAVE_MODEL_PLAN.md, 2026-09-25 block 3,
WP-66).

``tests/fixtures/weave_th61_lane_end.osm`` is the stretch as the corridor
compiles it, with a 3 -> 2 lane drop 1,500 m past the exit gore (where the
corridor's queue head forms) so that a queue arrives from downstream;
``tests/fixtures/weave_th61_lane_end_left_drop.con.xml`` ends the left lane
of that drop instead of netconvert's right one, so the queue loads the right
through lanes as the corridor's T.H.52 queue does.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest
import sumolib
import yaml

from flowstate_core.config import OSMNetwork, ScenarioConfig
from microsim import run_micro
from microsim.networks import accel_lane_end, expand_ramp_splits, osm_import, weave_sections
from microsim.runner import HALTING_SPEED_MS, _resolve_ramp_pieces

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures"
TH61_OSM = FIXTURES / "weave_th61_lane_end.osm"
TH61_LEFT_DROP = FIXTURES / "weave_th61_lane_end_left_drop.con.xml"
CORRIDOR_SCENARIO = REPO / "scenarios" / "mndot_i94_wb_stpaul_weave.yaml"
CORRIDOR_OSM = REPO / "data" / "osm" / "mndot_i94_wb_stpaul.osm"

#: The corridor's movements at the stretch in the 06:30-07:00 steps of
#: ``scenarios/mndot_i94_wb_stpaul_weave.yaml`` (the 3600-5100 s steps, when
#: VM R's queue reaches the stretch, minutes 55-65): the mainline arriving at
#: the entrance is the scenario's upstream ``inflow`` propagated through the
#: ten ramps before it in corridor order (on-ramps add their ``inflow``,
#: off-ramps take their ``exit_fraction``); the entrance is on-ramp
#: 53062592's ``inflow`` (``artifacts/demand_mndot_i94_wb_stpaul.json``:
#: ``detector_scaled`` off the two-lane ramp station rnd_88807); the exit
#: fraction is off-ramp 18207912's (``method: conservation``), applied, as the
#: corridor applies it, to the mainline and the entrants alike. Rounded to
#: 0.1 veh/h and 1e-4.
TH61_DEMAND_0630: dict[str, tuple[tuple[float, float], ...]] = {
    "mainline_vph": (
        (0.0, 4541.3),
        (300.0, 4556.0),
        (600.0, 4470.4),
        (900.0, 3733.3),
        (1200.0, 3770.7),
        (1500.0, 3402.7),
    ),
    "entrance_vph": (
        (0.0, 1788.0),
        (300.0, 1676.0),
        (600.0, 1750.7),
        (900.0, 1718.7),
        (1200.0, 1588.0),
        (1500.0, 1680.0),
    ),
    "exit_fraction": (
        (0.0, 0.2115),
        (300.0, 0.2221),
        (600.0, 0.4302),
        (900.0, 0.2799),
        (1200.0, 0.2768),
        (1500.0, 0.2095),
    ),
}

#: The fixture's 5-lane edge (the corridor's 51866108 + 45608490 + 45608485).
TH61_EDGE = "102"
#: A lane-end hold longer than one of VM R's 1-min map bins is counted as a
#: lock in the making [s].
TH61_HOLD_MAX_S = 60.0


def _corridor_fleet_block() -> dict:
    """The corridor scenario's ``fleet`` block (EIDM, the I-24 capacity-scaled
    population, heterogeneity 0.15, ``lc_strategic`` 5.0, ``lc_keep_right`` 0)."""
    return dict(yaml.safe_load(CORRIDOR_SCENARIO.read_text())["fleet"])


def th61_config(
    seed: int,
    merge: str = "lane_change",
    restriction: str = "left",
    duration_s: float = 1800.0,
) -> ScenarioConfig:
    """The T.H.61 fixture under :data:`TH61_DEMAND_0630` on the corridor's
    fleet, from an empty network, step 0.5 s.

    ``restriction``: ``"left"`` — the 3 -> 2 lane drop 1,500 m past the exit
    gore with its left lane ending (:data:`TH61_LEFT_DROP`); ``"right"`` —
    the same drop as netconvert builds it (its right lane ending); ``"none"``
    — no drop (the corridor stops 1,500 m past the gore). ``merge`` is the
    entrance's merge model; ``"weave"`` pairs it with the exit.
    """
    d = TH61_DEMAND_0630
    corridor = ["100", "101", "102", "103", "104"] + ([] if restriction == "none" else ["105"])
    entrance: dict = {
        "kind": "on",
        "name": "th61",
        "edges": ["200"],
        "attach_edge": "102",
        "inflow": [[t, q / 3600.0] for t, q in d["entrance_vph"]],
        "merge": merge,
    }
    if merge == "weave":
        entrance["weave"] = {"exit_ramp": "mounds exit"}
    return ScenarioConfig.model_validate(
        {
            "name": f"th61_lane_end_{merge}_{restriction}",
            "fleet": _corridor_fleet_block(),
            "network": {
                "kind": "osm",
                "osm_file": str(TH61_OSM),
                **({"patch_files": [str(TH61_LEFT_DROP)]} if restriction == "left" else {}),
                "corridor_edges": corridor,
                "inflow": [[t, q / 3600.0] for t, q in d["mainline_vph"]],
                "ramps": [
                    entrance,
                    {
                        "kind": "off",
                        "name": "mounds exit",
                        "edges": ["201"],
                        "attach_edge": "102",
                        "exit_fraction": [list(s) for s in d["exit_fraction"]],
                    },
                ],
            },
            "sim": {"duration_s": duration_s},
            "seed": seed,
        }
    )


def th61_lane_end_state(paths, duration_s: float) -> dict:
    """What happens at the end of the 5-lane edge over a run.

    * ``holds``: per vehicle halted (below ``HALTING_SPEED_MS``) within the
      last 10 m of the edge in a lane its route does not continue on — a
      vehicle bound through in lanes 0-1 (they lead only to the exit) or one
      bound for the exit in lanes 2-4 — its route and how long it stood there;
    * ``lane_speed_by_minute``: the mean speed of each lane over the edge's
      last 100 m per whole minute of the run;
    * ``zero_minutes``: the minutes in which every lane there averages below
      ``HALTING_SPEED_MS`` (VM R's "0.0 m/s");
    * ``through_lanes_min_ms``: the lowest minute mean of lanes 2-4 there
      (the queue from downstream reaching the gore);
    * departures, entrance departures, exits taken and exit-bound vehicles
      seen past the gore (missed), and collisions.
    """
    meta = json.loads(paths.meta.read_text())
    rou = (paths.run_dir / "net" / "demand.rou.xml").read_text()
    route_of = dict(re.findall(r'<vehicle id="([^"]+)"[^>]*route="([^"]+)"', rou))
    net = sumolib.net.readNet(str(next(paths.run_dir.glob("net/**/*.net.xml"))))
    x0 = sum(net.getEdge(e).getLength() for e in ("100", "101"))
    x1 = x0 + net.getEdge(TH61_EDGE).getLength()
    df = pd.read_parquet(paths.trajectories, columns=["t", "veh_id", "x", "lane", "v"])
    df = df[df.t < duration_s]
    exit_bound = df.veh_id.map(route_of).str.contains("_off")
    df = df.assign(route=df.veh_id.map(route_of), exit_bound=exit_bound)
    edge = df[(df.x >= x0) & (df.x < x1)]
    end = edge[(edge.x >= x1 - 10.0) & (edge.v < HALTING_SPEED_MS)]
    wrong = end[((end.lane <= 1) & ~end.exit_bound) | ((end.lane >= 2) & end.exit_bound)]
    holds = {
        vid: {"route": g.route.iloc[0], "lane": int(g.lane.iloc[0]), "held_s": len(g) * 0.5}
        for vid, g in wrong.groupby("veh_id")
    }
    last = edge[edge.x >= x1 - 100.0]
    by_min = last.groupby([(last.t // 60).astype(int), "lane"]).v.mean().unstack()
    zero = [int(m) for m, row in by_min.iterrows() if (row.dropna() < HALTING_SPEED_MS).all()]
    eb = df[df.exit_bound]
    seen_edge = set(eb[(eb.x >= x0) & (eb.x < x1)].veh_id)
    seen_past = set(eb[eb.x >= x1].veh_id)
    last_seen = eb.groupby("veh_id").t.max()
    t_end = float(df.t.max())
    on = next(r for r in meta["ramps"] if r["name"] == "th61")
    return {
        "departed": (meta["n_vehicles_departed"], meta["n_vehicles_planned"]),
        "entrance_departed": (on["n_departed"], on["n_planned"]),
        "exits_taken": len([v for v in seen_edge - seen_past if last_seen[v] < t_end]),
        "exits_missed": len(seen_past),
        "n_collisions": meta["n_collisions"],
        "holds": holds,
        "longest_hold_s": max((h["held_s"] for h in holds.values()), default=0.0),
        "zero_minutes": zero,
        "through_lanes_min_ms": round(float(by_min[[2, 3, 4]].min().min()), 1),
        "lane_speed_by_minute": {
            int(m): [round(float(v), 1) for v in row.to_numpy()] for m, row in by_min.iterrows()
        },
        "weave": [
            {k: v for k, v in ws.items() if k.startswith("n_")}
            for ws in meta.get("weave_sections") or []
        ],
    }


class TestTh61Geometry:
    """The stretch as the corrected corridor compiles it, and the fixture."""

    @pytest.mark.skipif(
        not (CORRIDOR_SCENARIO.is_file() and CORRIDOR_OSM.is_file()), reason="MnDOT files absent"
    )
    def test_corridor_stretch_is_a_two_lane_auxiliary_connection(self, tmp_path):
        """On the corrected map the T.H.61 NB entrance adds two lanes (3 -> 5)
        whose only outlet is the Mounds / Kellogg exit 1,135.5 m on, and the
        weave detector pairs the two; the acceleration-lane walk still words
        it as an added through lane, because its 400 m tail bound fires
        before the walk reaches the exit (docs/WEAVE_MODEL_PLAN.md,
        2026-09-25 block 3, WP-66)."""
        cfg = ScenarioConfig.model_validate(yaml.safe_load(CORRIDOR_SCENARIO.read_text()))
        net_cfg = cfg.network
        assert isinstance(net_cfg, OSMNetwork)
        bundle = osm_import(
            osm_file=net_cfg.osm_file,
            corridor_edges=tuple(net_cfg.corridor_edges),
            workdir=tmp_path,
            keep_edges=tuple(e for r in net_cfg.ramps for e in r.edges),
            patch_files=[REPO / p for p in net_cfg.patch_files],
            netconvert_extra=tuple(net_cfg.netconvert_extra),
        )
        net = sumolib.net.readNet(str(bundle.net_path))

        def outs(edge: str) -> list[list[tuple[str, int]]]:
            return [
                sorted((c.getTo().getID(), c.getToLane().getIndex()) for c in ln.getOutgoing())
                for ln in net.getEdge(edge).getLanes()
            ]

        assert net.getEdge("45608474").getLaneNumber() == 3
        assert outs("45608474") == [[("51866108", 2)], [("51866108", 3)], [("51866108", 4)]]
        assert outs("59339950") == [[("51866108", 0)], [("51866108", 1)]]
        assert outs("45608485") == [
            [("18207912", 0)],
            [("18207912", 1)],
            [("45782590", 0)],
            [("45782590", 1)],
            [("45782590", 2)],
        ]
        chain = expand_ramp_splits(list(net_cfg.corridor_edges), bundle.edge_ids)
        ramps = list(_resolve_ramp_pieces(cfg, bundle).network.ramps)
        found = {
            (ramps[s.on_ramp].name, ramps[s.off_ramp].name): s
            for s in weave_sections(net, chain, ramps)
        }
        section = found[("on-ramp 53062592", "off-ramp 18207912")]
        assert section.edges == ("51866108", "45608490", "45608485")
        assert 1135.0 < section.length_m < 1136.0
        assert all(section.exit_only)
        on61 = next(r for r in ramps if r.name == "on-ramp 53062592")
        verdict = accel_lane_end(net, chain, on61.attach_edge)
        assert "added through lane" in verdict.reason, verdict

    def test_fixture_compiles_as_the_corridor_stretch(self, tmp_path):
        """Compiled lengths within 0.1 m of the corridor's, lanes 3 -> 5 -> 3,
        lanes 0-1 of the 5-lane edge to the exit only, and the drop."""
        cfg = th61_config(3)
        net_cfg = cfg.network
        assert isinstance(net_cfg, OSMNetwork)
        bundle = osm_import(
            osm_file=net_cfg.osm_file,
            corridor_edges=tuple(net_cfg.corridor_edges),
            workdir=tmp_path,
            keep_edges=("200", "201"),
            patch_files=[Path(p) for p in net_cfg.patch_files],
        )
        net = sumolib.net.readNet(str(bundle.net_path))
        for edge, length, lanes in (
            ("101", 445.4, 3),
            ("102", 1135.6, 5),
            ("103", 770.4, 3),
            ("200", 915.2, 2),
            ("201", 556.0, 2),
        ):
            assert abs(net.getEdge(edge).getLength() - length) <= 0.1, edge
            assert net.getEdge(edge).getLaneNumber() == lanes, edge
        outs = [
            sorted((c.getTo().getID(), c.getToLane().getIndex()) for c in ln.getOutgoing())
            for ln in net.getEdge("102").getLanes()
        ]
        assert outs == [[("201", 0)], [("201", 1)], [("103", 0)], [("103", 1)], [("103", 2)]]
        drop = [
            sorted((c.getTo().getID(), c.getToLane().getIndex()) for c in ln.getOutgoing())
            for ln in net.getEdge("104").getLanes()
        ]
        assert drop == [[("105", 0)], [("105", 1)], []]  # the left lane ends
        (section,) = weave_sections(
            net,
            list(bundle.edge_ids),
            list(_resolve_ramp_pieces(cfg, bundle).network.ramps),
        )
        assert section.edges == ("102",)


class TestTh61LaneEnd:
    """The stretch under a queue arriving from downstream, on the corridor's
    fleet and its 06:30-07:00 movements (:data:`TH61_DEMAND_0630`)."""

    def test_queue_from_downstream_does_not_lock_the_lane_end(self, tmp_path):
        """VM R's lock does not form at fixture scale (docs/WEAVE_MODEL_PLAN.md,
        2026-09-25 block 3, WP-66): with the left-lane drop 1,500 m past the
        gore the queue reaches the gore (a through lane's last 100 m below
        5 m/s in some minute) and the lane end holds only briefly — no minute
        with every lane there below ``HALTING_SPEED_MS``, no vehicle halted at
        the end of a lane its route does not continue on for longer than
        :data:`TH61_HOLD_MAX_S` — as ``merge: lane_change``, the corridor
        scenario's setting, 30 simulated minutes, seed 3.

        Measured at seeds 3-12 (the dated section has the table): the queue
        reaches the gore in minute 2-11 and holds a through lane there under
        5 m/s in 18-24 of the 30 minutes; no minute at 0.0 m/s; 1-5 lane-end
        holds per run, every one a T.H.61 entrant bound through standing at
        the end of lane 1, the longest 0.5-17.5 s; 2,797-2,885 of 2,885
        depart; no collision. Seed 3: one hold of 1.0 s, 2,797 depart.
        """
        cfg = th61_config(3)
        state = th61_lane_end_state(run_micro(cfg, 3, tmp_path / "th61"), cfg.sim.duration_s)
        departed, planned = state["departed"]
        assert state["n_collisions"] == 0, state
        assert state["through_lanes_min_ms"] < 5.0, state
        assert state["zero_minutes"] == [], state
        assert state["longest_hold_s"] <= TH61_HOLD_MAX_S, state
        assert departed >= 0.95 * planned, state

    @pytest.mark.xfail(
        strict=True,
        reason="the weave model has one auxiliary lane (docs/CONTRACTS.md §2: entrants on "
        "lane 0 change left, exiters on lanes >= 1 change right to lane 0); here lanes 0 and "
        "1 both lead only to the exit (docs/WEAVE_MODEL_PLAN.md, 2026-09-25 block 3, WP-66; "
        "the exit link's class corrected to the corridor's, WP-83). "
        "Seed 3: 2,565 of 2,885 depart (lane_change: 2,810), the entrance 837 of 848; the "
        "section drives 4,273 exits for 600 exiters reaching it — 569 of them change from "
        "lane 1 to lane 0 4,229 times and back 3,807 times (lane_change: 412 and 204) — and "
        "gives up 5 exits; no minute at 0.0 m/s at the gore, longest lane-end hold 1.0 s, "
        "no collision",
    )
    def test_weave_configuration_carries_the_stretch(self, tmp_path):
        """Configured as a weave section (``merge: weave`` paired with the
        exit), the stretch must carry its demand at least as the corridor's
        ``lane_change`` does: at least 95 % of the planned vehicles depart (the
        corridor section test's criterion (i)), no collision. The acceptance
        test of a weave model with two auxiliary lanes, the condition for
        adding the stretch to the corridor as a third section."""
        cfg = th61_config(3, merge="weave")
        state = th61_lane_end_state(run_micro(cfg, 3, tmp_path / "th61w"), cfg.sim.duration_s)
        departed, planned = state["departed"]
        assert state["n_collisions"] == 0, state
        assert departed >= 0.95 * planned, state
