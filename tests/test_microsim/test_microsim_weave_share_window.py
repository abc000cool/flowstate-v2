"""The per-window ramp-to-ramp share (``WeaveSpec.ramp_to_ramp_share: {u, s_max}``, Amendment 3, 2026-10-07).

docs/FRISCO_PROTOCOL.md, adoption of Amendment 3, item 1; docs/A3_RANGE_ROUND.md. In each 300-s window of free-flow
arrival at the weave, ``s_w = P_w + u · (s_max − P_w)`` clipped to the window's exit volume; every leg and exit keeps
its volume per window; unset, nothing changes.

* the planner on a synthetic corridor: the window rows (``P_w``, ``s_w``, the clip, the realized share), the clip
  count, conservation per arrival window, the timing rule, monotonicity in u, ``u = 0`` against unset, ``s_max``;
* unset is the plain plan, the single share is untouched by the geometry, the geometry is required when set;
* the runner (SUMO, the T.H.52 corridor fixture): the record in ``meta.json`` matches the route file.
"""

from __future__ import annotations

import collections
import json
import math
import xml.etree.ElementTree as ET

import pytest

from flowstate_core.config import AVSpec, FleetSpec, RampSpec, ScenarioConfig, WeaveSpec
from flowstate_core.rng import make_rng
from microsim.vehicles import (
    RAMP_TO_RAMP_RANGE_TIMING,
    RAMP_TO_RAMP_WINDOW_S,
    build_corridor_plan,
    ramp_routes,
    write_corridor_routes,
)

CORRIDOR = ("e0", "e1", "e2", "e3", "e4")
#: edge -> (lane-0 length [m], base limit [m/s]); the entry is 3,500 m (140 s at 25 m/s) from the weave
GEOMETRY = {
    "e0": (3000.0, 25.0),
    "e1": (500.0, 25.0),
    "e2": (300.0, 25.0),
    "e3": (400.0, 25.0),
    "e4": (500.0, 25.0),
    "on_u": (200.0, 20.0),
    "on_a": (250.0, 20.0),
}
#: the paired exit's fraction drops to 0.05 at 900 s: the high shares are clipped there
EXIT_B = ((0.0, 0.3), (600.0, 0.2), (900.0, 0.05))
DURATION_S = 1500.0
K, J = 1, 2  # the entrance and its paired exit in ramp order


def ramps(share=None, *, weave=True, exit_b=EXIT_B) -> list[RampSpec]:
    entrance: dict = {
        "kind": "on",
        "edges": ["on_a"],
        "attach_edge": "e2",
        "name": "A",
        "inflow": [(0.0, 0.3), (600.0, 0.4)],
    }
    if weave:
        entrance["merge"] = "weave"
        entrance["weave"] = WeaveSpec(exit_ramp="B", ramp_to_ramp_share=share)
    return [
        RampSpec(kind="on", edges=["on_u"], attach_edge="e1", name="U", inflow=[(0.0, 0.2)]),
        RampSpec(**entrance),
        RampSpec(
            kind="off", edges=["off_b"], attach_edge="e2", name="B", exit_fraction=list(exit_b)
        ),
        RampSpec(
            kind="off", edges=["off_c"], attach_edge="e3", name="C", exit_fraction=[(0.0, 0.1)]
        ),
    ]


def plan(share=None, *, seed=7, geometry=GEOMETRY, weave=True, exit_b=EXIT_B, fleet=None):
    return build_corridor_plan(
        [(0.0, 1.0)],
        DURATION_S,
        fleet or FleetSpec(),
        AVSpec(penetration=0.05),
        make_rng(seed),
        ramps=ramps(share, weave=weave, exit_b=exit_b),
        corridor_edges=CORRIDOR,
        edge_geometry=geometry,
    )


def arrival(p, i: int) -> float:
    """Vehicle i's free-flow arrival at the start of e2 (the weave), recomputed here from the geometry."""
    rid = p.route[i]
    base = rid.partition("_")[0]
    edges = {"main": ["e0", "e1"], "on0": ["on_u", "e1"], f"on{K}": ["on_a"]}[base]
    v0 = p.params[i]["v0"]
    f = p.speed_factor_of(i)
    return p.depart_s[i] + sum(GEOMETRY[e][0] / min(v0, f * GEOMETRY[e][1]) for e in edges)


def window_legs(p) -> collections.Counter:
    """Per window of arrival at the weave: vehicles reaching it by origin and by destination."""
    out: collections.Counter = collections.Counter()
    for i, rid in enumerate(p.route):
        origin, _, dest = rid.partition("_")
        w = int(arrival(p, i) // RAMP_TO_RAMP_WINDOW_S)
        out[(w, "origin", origin)] += 1
        out[(w, "dest", dest or "end")] += 1
    return out


def p_window(t0: float) -> float:
    """The exit fraction's time average over [t0, t0 + 300) (the entrants draw only B before reaching it)."""
    edges = [*(t for t, _ in EXIT_B if t0 < t < t0 + 300.0), t0 + 300.0]
    total, a = 0.0, t0
    for b in edges:
        total += next(v for t, v in reversed(EXIT_B) if t <= a) * (b - a)
        a = b
    return total / 300.0


# ------------------------------------------------------------------------- unset and the single share


def test_unset_with_geometry_is_the_plain_plan_byte_for_byte(tmp_path) -> None:
    """Unset, the geometry is never read: the plan and its route file are those of the same ramps without any
    weave block and without geometry — the plan every scenario had before the per-window form existed."""
    unset = plan(None)
    plain = build_corridor_plan(
        [(0.0, 1.0)],
        DURATION_S,
        FleetSpec(),
        AVSpec(penetration=0.05),
        make_rng(7),
        ramps=ramps(weave=False),
        corridor_edges=CORRIDOR,
    )
    assert unset == plain and unset.ramp_to_ramp == ()
    files = [
        write_corridor_routes(
            CORRIDOR,
            p,
            "IDM",
            0.5,
            tmp_path / f"{tag}.rou.xml",
            lanes=2,
            routes=ramp_routes(CORRIDOR, r),
        ).read_bytes()
        for tag, p, r in (("unset", unset, ramps(None)), ("plain", plain, ramps(weave=False)))
    ]
    assert files[0] == files[1]


def test_the_single_share_is_untouched_by_the_geometry() -> None:
    # the single share refuses a window it cannot meet; this exit carries 0.4 throughout
    flat = ((0.0, 0.4),)
    with_geom, without = plan(0.4, exit_b=flat), plan(0.4, exit_b=flat, geometry=None)
    assert with_geom == without
    (rec,) = with_geom.ramp_to_ramp
    assert "form" not in rec and rec["share"] == 0.4  # the single share's record, as before


def test_the_per_window_form_needs_the_edge_geometry() -> None:
    with pytest.raises(ValueError, match="needs the network's edge geometry"):
        plan({"u": 0.5}, geometry=None)
    partial = {k: v for k, v in GEOMETRY.items() if k != "on_u"}
    with pytest.raises(ValueError, match=r"edge_geometry lacks \['on_u'\]"):
        plan({"u": 0.5}, geometry=partial)


# ------------------------------------------------------------------------- the window rows


@pytest.mark.parametrize("u", [0.0, 0.25, 0.5, 1.0])
@pytest.mark.parametrize("seed", [7, 3])
def test_window_shares_are_p_plus_u_times_the_range_clipped(u: float, seed: int) -> None:
    p = plan({"u": u}, seed=seed)
    (rec,) = p.ramp_to_ramp
    assert rec["form"] == "per_window" and rec["u"] == u and rec["s_max"] == 0.70
    assert rec["timing"] == RAMP_TO_RAMP_RANGE_TIMING == "free_flow_arrival"
    assert rec["arrival_at"] == "e2" and rec["window_s"] == 300.0
    assert set(rec["free_flow_s"]) == {"corridor entry", "U", "A"}
    assert rec["free_flow_s"]["corridor entry"]["min_s"] == pytest.approx(3500.0 / 25.0)
    assert rec["free_flow_s"]["A"]["min_s"] == pytest.approx(250.0 / 20.0)
    assigned = 0
    exact = 0.0
    for row in rec["windows"]:
        assert row["p"] == pytest.approx(p_window(row["t0_s"]))
        assert row["s"] == pytest.approx(row["p"] + u * (0.70 - row["p"]))
        assert row["min_share"] == 0.0  # at or above the drawn split the low clip never binds here
        assert row["s_applied"] == pytest.approx(min(row["s"], row["max_share"]))
        assert (row["clip"] == "exit_volume") == (row["s"] > row["max_share"])
        n = row["n_entrants"]
        assert row["share_realized"] == row["n_ramp_to_ramp"] / n
        assert row["n_ramp_to_ramp"] <= row["n_ramp_to_ramp_drawn"] + row["n_partners_to_exit"]
        assert row["n_ramp_to_ramp"] - row["n_ramp_to_ramp_drawn"] == (
            row["n_swapped_to_exit"] - row["n_swapped_from_exit"]
        )
        # cumulative rounding: the assigned total within half a vehicle of the exact total
        exact += row["s_applied"] * n
        assigned += row["n_ramp_to_ramp"]
        assert abs(assigned - exact) <= 0.5 + 1e-9
    assert rec["n_clipped"] == sum(r["clip"] is not None for r in rec["windows"])
    assert rec["clipped_windows_t0_s"] == [r["t0_s"] for r in rec["windows"] if r["clip"]]
    assert rec["n_windows"] == len(rec["windows"])
    entrants = [r for r in p.route if r.startswith(f"on{K}")]
    assert rec["n_entrants"] == len(entrants) == sum(r["n_entrants"] for r in rec["windows"])
    assert rec["n_ramp_to_ramp"] == sum(r == f"on{K}_off{J}" for r in entrants) == assigned
    assert rec["share_realized"] == assigned / len(entrants)


def test_the_low_exit_fraction_windows_are_clipped_and_counted() -> None:
    """From 900 s the exit carries 5 % of the vehicles: at u = 1 the pool cannot give 0.70 there, so those
    windows take every pool vehicle bound for the exit and are counted; at u = 0 nothing is clipped."""
    (r1,) = plan({"u": 1.0}).ramp_to_ramp
    clipped = [w for w in r1["windows"] if w["clip"]]
    assert clipped and r1["n_clipped"] == len(clipped)
    assert all(w["t0_s"] >= 900.0 for w in clipped)
    for w in clipped:
        assert w["n_ramp_to_ramp"] == w["n_ramp_to_ramp_drawn"] + w["n_partners_to_exit"]
        assert w["share_realized"] == pytest.approx(w["max_share"])
    (r0,) = plan({"u": 0.0}).ramp_to_ramp
    assert r0["n_clipped"] == 0


def test_every_leg_and_exit_keeps_its_volume_per_window_of_arrival() -> None:
    base = plan(None)
    for u in (0.0, 0.5, 1.0):
        p = plan({"u": u})
        assert window_legs(p) == window_legs(base), u
        for field in ("params", "is_av", "complied", "depart_s", "depart_lane", "speed_factor"):
            assert getattr(p, field) == getattr(base, field), field
        # every vehicle keeps its origin; the upstream exits' vehicles never reach the weave and never move
        for a, b in zip(base.route, p.route, strict=True):
            assert a.partition("_")[0] == b.partition("_")[0]
        assert set(p.route) <= set(ramp_routes(CORRIDOR, ramps({"u": u})))


def test_the_partners_reach_the_weave_with_the_entrants_they_trade_with() -> None:
    """The timing rule: in each window of free-flow arrival as many partners as entrants changed destination
    (departure windows would pair the entrants with mainline vehicles 140 s further from the weave)."""
    base = plan(None)
    p = plan({"u": 1.0})
    moved: collections.Counter = collections.Counter()
    for i, (a, b) in enumerate(zip(base.route, p.route, strict=True)):
        if a != b:
            kind = "entrant" if a.startswith(f"on{K}") else "partner"
            moved[(int(arrival(p, i) // 300.0), kind)] += 1
    windows = {w for w, _ in moved}
    assert windows
    for w in windows:
        assert moved[(w, "entrant")] == moved[(w, "partner")], w
    # and the record's per-window swaps are the moved entrants
    (rec,) = p.ramp_to_ramp
    for row in rec["windows"]:
        w = int(row["t0_s"] // 300.0)
        assert row["n_swapped_to_exit"] + row["n_swapped_from_exit"] == moved[(w, "entrant")]


def test_the_share_rises_with_u_in_every_window() -> None:
    rows = {u: plan({"u": u}).ramp_to_ramp[0]["windows"] for u in (0.0, 0.25, 0.5, 0.75, 1.0)}
    us = sorted(rows)
    for w in range(len(rows[0.0])):
        applied = [rows[u][w]["s_applied"] for u in us]
        assert applied == sorted(applied), w
    totals = [plan({"u": u}).ramp_to_ramp[0]["share_realized"] for u in us]
    assert totals == sorted(totals)


def test_u0_is_the_proportional_expectation_not_the_unset_draw() -> None:
    """u = 0 asks P_w in every window: the realized count is the rounded expectation of the proportional draw,
    so it is not the unset plan (docs/A3_RANGE_ROUND.md §3: the u0 arm runs the committed scenario, unset)."""
    differs = 0
    for seed in (7, 3, 11):
        base, p = plan(None, seed=seed), plan({"u": 0.0}, seed=seed)
        (rec,) = p.ramp_to_ramp
        expected = sum(w["p"] * w["n_entrants"] for w in rec["windows"])
        assert abs(rec["n_ramp_to_ramp"] - expected) <= 0.5
        assert rec["n_ramp_to_ramp_drawn"] == sum(r == f"on{K}_off{J}" for r in base.route)
        differs += base.route != p.route
    assert differs >= 1


def test_s_max_is_a_parameter_and_recorded() -> None:
    (rec,) = plan({"u": 1.0, "s_max": 0.5}).ramp_to_ramp
    assert rec["s_max"] == 0.5
    for row in rec["windows"]:
        assert row["s"] == pytest.approx(0.5)
        assert row["s_applied"] == pytest.approx(min(0.5, row["max_share"]))


def test_the_window_record_is_json_and_deterministic() -> None:
    a, b = plan({"u": 0.5}), plan({"u": 0.5})
    assert a == b
    json.dumps(a.ramp_to_ramp, allow_nan=False)


def test_slower_drivers_arrive_later() -> None:
    """Each vehicle's own v0 and speed factor set its free-flow time (the limit binds above it)."""
    p = plan({"u": 0.5}, fleet=FleetSpec(v0=24.0, heterogeneity_frac=0.15))
    (rec,) = p.ramp_to_ramp
    entry = rec["free_flow_s"]["corridor entry"]
    assert entry["max_s"] > entry["min_s"] >= 3500.0 / 25.0 - 1e-9
    assert math.isclose(
        entry["mean_s"],
        sum(arrival(p, i) - p.depart_s[i] for i, r in enumerate(p.route) if r.startswith("main"))
        / sum(r.startswith("main") for r in p.route),
    )


# ------------------------------------------------------------------------- the runner (SUMO)


class TestRampToRampRangeRun:
    """Through the runner on the T.H.52 corridor fixture (seed 4): the record matches the route file."""

    def test_record_route_file_and_legs(self, tmp_path) -> None:
        from microsim import run_micro
        from tests.test_microsim.test_microsim_merge_managed_meter import _th52_corridor_config

        def run(share, tag):
            raw = _th52_corridor_config(4).model_dump(mode="json")
            if share is not None:
                raw["network"]["ramps"][0]["weave"]["ramp_to_ramp_share"] = share
            paths = run_micro(ScenarioConfig.model_validate(raw), 4, tmp_path / tag)
            meta = json.loads(paths.meta.read_text())
            routes = ET.parse(paths.run_dir / "net" / "demand.rou.xml").getroot()
            return meta, [v.get("route") for v in routes.findall("vehicle")]

        meta_unset, unset = run(None, "unset")
        assert "ramp_to_ramp_shares" not in meta_unset
        meta, planned = run({"u": 1.0}, "u1")
        (rec,) = meta["ramp_to_ramp_shares"]
        assert rec["form"] == "per_window" and rec["u"] == 1.0 and rec["s_max"] == 0.7
        assert rec["ramp"] == "th52" and rec["exit_ramp"] == "th52 exit"
        weave = meta["config"]["network"]["ramps"][0]["weave"]
        assert weave["ramp_to_ramp_share"] == {"u": 1.0, "s_max": 0.7}
        n_on = sum(r.startswith("on0") for r in planned)
        assert rec["n_entrants"] == n_on
        assert rec["n_ramp_to_ramp"] == planned.count("on0_off1")
        assert rec["n_ramp_to_ramp_drawn"] == unset.count("on0_off1")
        assert rec["n_windows"] == len(rec["windows"]) and rec["n_clipped"] >= 0
        assert set(rec["free_flow_s"]) == {"corridor entry", "th52"}
        assert all(v["min_s"] > 0 for v in rec["free_flow_s"].values())
        for part in (0, 1):
            a = collections.Counter(r.partition("_")[2 * part] for r in planned)
            b = collections.Counter(r.partition("_")[2 * part] for r in unset)
            assert a == b, part
        assert meta["config_hash"] != meta_unset["config_hash"]
