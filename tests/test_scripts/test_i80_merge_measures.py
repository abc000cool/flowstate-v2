"""scripts/i80_merge_measures.py (E11): the merge measures on synthetic trajectory tables.

A planted merge: a lane-6 platoon at 10 m/s (30 m front to front) and 40 entrants on lane 7 at
14 m/s, each crossing into the middle of a platoon gap (offset by -2..2 m) inside the acceleration
lane [100, 250) m, then following at 10 m/s. Known by construction: 40 entering changes, the
accepted lead time gaps (10 - δ) / 14 s and lag gaps (10 + δ) / 10 s (medians 10 / 14 and 1.0),
partner speeds +4 m/s (follower side: changer - new follower) and -4 m/s (leader side), lane-7
speed 14 m/s. The same scene on a run's axis and 0.5-s grid (the simulated path: band lanes,
compiled edge lengths a fraction off the measured ones, a warm-up) reads the same.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"e11_{name}", SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mm = _load("i80_merge_measures")
Zone = mm.Zone

T_END = 220.0
L = 500.0
GORE, END = 100.0, 250.0
VM, VE = 10.0, 14.0
SPACING = 30.0
LEN = 5.0
N_ENTRANTS = 40
DELTAS = (-2.0, -1.0, 0.0, 1.0, 2.0)
ZONE = Zone(mm.ZONE_NAME, "merge", GORE, END)


def scene(dt: float = 0.1) -> pd.DataFrame:
    """The planted merge on I-80's axis (``t, veh_id, x, lane, v, length``), rows on [0, L)."""
    t = np.round(np.arange(0.0, T_END + 1e-9, dt), 6)
    rows = []
    n_platoon = int((L + VM * T_END) / SPACING) + 2
    x0 = np.array([L - k * SPACING for k in range(n_platoon)])
    for k in range(n_platoon):
        x = x0[k] + VM * t
        m = (x >= 0) & (x < L)
        rows.append(
            pd.DataFrame(
                {"t": t[m], "veh_id": f"m{k}", "x": x[m], "lane": 6, "v": VM, "length": LEN}
            )
        )
    used: set[int] = set()
    for j in range(N_ENTRANTS):
        tc = 20.0 + 4.0 * j
        target = 180.0 + (j % 5) * 10.0
        mids = x0 + VM * tc - 15.0
        k = int(np.argmin(np.abs(mids - target)))
        assert k not in used
        used.add(k)
        xc = float(mids[k] + DELTAS[(j * 3) % 5])
        t0 = tc - (xc - 60.0) / VE
        tt = t[t >= t0 - 1e-9]
        before = tt < tc - 1e-9
        ta = tt[~before]
        first = ta - tc < dt / 2
        x = np.concatenate(
            [
                60.0 + VE * (tt[before] - t0),
                xc + np.where(first, 0.0, VE * dt + VM * (ta - tc - dt)),
            ]
        )
        v = np.concatenate([np.full(before.sum(), VE), np.where(first, VE, VM)])
        lane = np.concatenate([np.full(before.sum(), 7), np.full((~before).sum(), 6)])
        m = (x >= 0) & (x < L)
        rows.append(
            pd.DataFrame(
                {
                    "t": tt[m],
                    "veh_id": f"e{j}",
                    "x": x[m],
                    "lane": lane[m],
                    "v": v[m],
                    "length": LEN,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


@pytest.fixture(scope="module")
def observed_summary() -> dict[str, Any]:
    part = mm.extract(scene(), ZONE, dt_s=0.1, windows=[(0.0, T_END)], x_range=(0.0, L))
    assert part.counts["n_selected"] == N_ENTRANTS
    return mm.summarize(mm.pool([part]), ZONE, n_boot=40, seed=7)


def test_accepted_gaps_and_partner_speeds_are_the_planted_ones(
    observed_summary: dict[str, Any],
) -> None:
    s = observed_summary
    assert s["n_changes"] == N_ENTRANTS
    lead, lag = s["accepted_gap_s"]["lead"], s["accepted_gap_s"]["lag"]
    assert lead["n"] == lag["n"] == N_ENTRANTS
    assert lead["median"] == pytest.approx(10.0 / 14.0, abs=1e-4)
    assert lag["median"] == pytest.approx(1.0, abs=1e-4)
    assert lead["p25"] == pytest.approx(9.0 / 14.0, abs=1e-4)
    assert lead["ci95"][0] <= lead["median"] <= lead["ci95"][1]
    fol, lea = s["partner_speed_ms"]["follower"], s["partner_speed_ms"]["leader"]
    assert fol["median"] == pytest.approx(4.0) and lea["median"] == pytest.approx(-4.0)
    assert fol["n"] == lea["n"] == N_ENTRANTS
    assert fol["ci95"] == [4.0, 4.0]


def test_gap_ratios_speed_profile_and_critical_gaps(observed_summary: dict[str, Any]) -> None:
    s = observed_summary
    assert set(s["gap_ratio"]["follower"]) == {"0", "2", "5", "10"}
    at0 = s["gap_ratio"]["follower"]["0"]
    # the follower's time gap (10 + δ) / 10 s over the platoon's normal 2.5 s at 10 m/s
    assert at0["time_gap_s"]["median"] == pytest.approx(1.0, abs=1e-3)
    assert at0["ratio_pop"]["median"] == pytest.approx(0.4, abs=0.01)
    assert at0["ratio_pop"]["n"] == N_ENTRANTS
    assert s["gap_ratio"]["follower"]["10"]["ratio_pop"]["n"] > 0
    prof = s["accel_lane_speed_ms"]
    assert [(b["bin_lo_m"], b["bin_hi_m"]) for b in prof] == [(0, 50), (50, 100), (100, 150)]
    assert all(b["median"] == pytest.approx(VE) for b in prof if b["n"])
    cg = s["critical_gap_s"]
    assert cg["fitted"] and cg["n_drivers"] == N_ENTRANTS
    assert cg["n_with_rejection"] >= 10
    for side in ("lead", "lag"):
        # a critical gap is at most the gap accepted (consistent drivers, Troutbeck 1992)
        assert 0.0 < cg[side]["median"] <= s["accepted_gap_s"][side]["median"] + 1e-9
        assert cg[side]["ci95"] is not None and cg[side]["n_boot"] == 40


def test_windows_select_the_changes() -> None:
    part = mm.extract(scene(), ZONE, dt_s=0.1, windows=[(0.0, 100.0)], x_range=(0.0, L))
    # changes at t = 20 + 4 j: j = 0..19 fall before 100 s
    assert part.counts["n_selected"] == 20
    both = mm.extract(
        scene(), ZONE, dt_s=0.1, windows=[(0.0, 50.0), (150.0, 400.0)], x_range=(0.0, L)
    )
    assert both.counts["n_selected"] == 8 + 7  # t = 20..48 s, and t = 152..176 s
    assert mm.in_windows(
        np.array([0.0, 49.9, 50.0, 150.0]), [(0.0, 50.0), (150.0, 400.0)]
    ).tolist() == [
        True,
        True,
        False,
        True,
    ]


def test_median_ci_is_seeded() -> None:
    v = np.random.default_rng(1).normal(size=200)
    a = mm.median_ci(v, n_boot=200, seed=3)
    assert a == mm.median_ci(v, n_boot=200, seed=3)
    assert a is not None and a[0] < float(np.median(v)) < a[1]
    assert mm.median_ci(v[:1], n_boot=200, seed=3) is None
    assert mm.median_ci(v, n_boot=0, seed=3) is None
    s = mm.stats(np.array([1.0, np.nan, np.inf, 3.0]), n_boot=10, seed=1)
    assert s["n"] == 2 and s["median"] == 2.0


# --- the simulated path ---------------------------------------------------------------------------

#: the builder's measured spans (data axis) and a run's compiled offsets, a fraction off
SPANS = {
    "9001": [-L, 0.0],
    "9002": [0.0, GORE],
    "9003": [GORE, END],
    "9004": [END, L],
    "9005": [L, L + 200.0],
}
COMPILED = {"9001": 500.3, "9002": 100.2, "9003": 149.7, "9004": 250.1, "9005": 199.9}
WARMUP = 180.0


def _prov() -> dict[str, Any]:
    edges = list(SPANS)
    offsets = np.cumsum([0.0, *[COMPILED[e] for e in edges[:-1]]]).tolist()
    return {"edges": edges, "edge_offsets_m": offsets, "edge_lanes": [6, 6, 7, 6, 6]}


def _to_run_axis(x: np.ndarray) -> np.ndarray:
    """Inverse of the edge-knot map: data axis -> a run's compiled axis."""
    prov = _prov()
    out = np.empty_like(x)
    for e, off in zip(prov["edges"], prov["edge_offsets_m"], strict=True):
        lo, hi = SPANS[e]
        m = (x >= lo) & (x < hi)
        out[m] = off + (x[m] - lo) * COMPILED[e] / (hi - lo)
    return out


def test_data_axis_maps_the_edge_starts_onto_the_measured_ones() -> None:
    prov = _prov()
    starts = np.array(prov["edge_offsets_m"])
    got = mm.data_axis(starts, prov, SPANS)
    assert got.tolist() == pytest.approx([SPANS[e][0] for e in SPANS])
    x = np.array([-300.0, 0.0, 37.0, 120.0, 260.0, 499.0])
    assert mm.data_axis(_to_run_axis(x), prov, SPANS) == pytest.approx(x)
    with pytest.raises(ValueError, match="not on the run's corridor"):
        mm.data_axis(starts, prov, {**SPANS, "9999": [0.0, 1.0]})


def test_the_simulated_path_reads_the_same(observed_summary: dict[str, Any]) -> None:
    obs = scene(dt=0.5)
    sim = obs.copy()
    sim["x"] = _to_run_axis(sim["x"].to_numpy(dtype=np.float64))
    sim["t"] = sim["t"] + WARMUP
    prov = _prov()
    zone_run = Zone(
        "9003:Powell St on-ramp", "merge", prov["edge_offsets_m"][2], prov["edge_offsets_m"][3]
    )
    diverge = Zone("x", "diverge", 5000.0, 5100.0)
    with pytest.raises(ValueError, match="one merge zone"):
        mm.sim_on_data_axis(sim, [zone_run, zone_run], prov, edge_spans_data=SPANS, site_length_m=L)
    df, zone = mm.sim_on_data_axis(
        sim, [zone_run, diverge], prov, edge_spans_data=SPANS, site_length_m=L
    )
    assert (zone.x_lo_m, zone.x_hi_m) == pytest.approx((GORE, END))
    assert df["x"].min() >= 0.0 and df["x"].max() < L
    part = mm.extract(
        df, zone, dt_s=0.5, windows=[(WARMUP + 0.0, WARMUP + T_END)], x_range=(0.0, L)
    )
    s = mm.summarize(mm.pool([part]), zone, n_boot=0, seed=mm.SEED)
    assert s["n_changes"] == observed_summary["n_changes"]
    for q, sides in (
        ("accepted_gap_s", ("lead", "lag")),
        ("partner_speed_ms", ("follower", "leader")),
    ):
        for side in sides:
            assert s[q][side]["median"] == pytest.approx(
                observed_summary[q][side]["median"], abs=1e-3
            )
            assert "ci95" not in s[q][side]
    assert s["critical_gap_s"]["fitted"]
    assert s["critical_gap_s"]["lead"]["ci95"] is None


def test_pooling_offsets_change_ids_and_sums_the_normals() -> None:
    a = mm.extract(scene(), ZONE, dt_s=0.1, windows=[(0.0, 100.0)], x_range=(0.0, L))
    b = mm.extract(scene(), ZONE, dt_s=0.1, windows=[(100.0, T_END)], x_range=(0.0, L))
    p = mm.pool([a, b])
    assert len(p.drivers) == len(a.drivers) + len(b.drivers) == N_ENTRANTS
    assert p.drivers["change"].is_unique
    assert set(p.points["change"]) <= set(p.drivers["change"])
    assert int(p.normal.counts.sum()) == int(a.normal.counts.sum() + b.normal.counts.sum())
    assert np.isfinite(p.post.values["follower"]["ratio_pop"][:, 0]).sum() == N_ENTRANTS


def test_on_ramp_for_arrivals_reads_the_attach_span_and_band() -> None:
    prov = _prov()
    meta = {
        "ramps": [
            {
                "kind": "on",
                "name": "Powell St on-ramp",
                "attach_edge": "9003",
                "attach_x_m": 600.5,
                "attach_end_x_m": 750.2,
            }
        ]
    }
    assert mm.on_ramp_for_arrivals(meta, prov) == {
        "name": "Powell St on-ramp",
        "x_lo_m": 600.5,
        "x_hi_m": 750.2,
        "aux_band": 7,
    }
    with pytest.raises(ValueError, match="one on-ramp"):
        mm.on_ramp_for_arrivals({"ramps": []}, prov)


def test_arrival_crossings_are_counted_and_confirmed() -> None:
    """A change restored by WP-82's fix (a one-sample stay on lane 7 at the track's start) is
    confirmed and selected; without the key it is an unconfirmed track-start change."""
    df = scene(dt=0.5)
    e = df[df["veh_id"] == "e0"]
    tc = float(e.loc[e["lane"] == 6, "t"].min())
    late = df[~((df["veh_id"] == "e0") & (df["t"] < tc - 0.5 - 1e-9))]
    keys = {("e0", round(tc, 6))}
    without = mm.extract(late, ZONE, dt_s=0.5, windows=[(0.0, T_END)], x_range=(0.0, L))
    with_keys = mm.extract(
        late, ZONE, dt_s=0.5, windows=[(0.0, T_END)], x_range=(0.0, L), arrival_keys=keys
    )
    assert with_keys.counts["n_selected"] == without.counts["n_selected"] + 1 == N_ENTRANTS
    assert with_keys.counts["n_arrival_crossings"] == 1


# --- the observed run end to end --------------------------------------------------------------------


def test_run_observed_on_synthetic_periods(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_scripts.test_i80_data import T0_MS, ngsim_period

    p2 = ngsim_period(T0_MS, 1800, seed=2)
    p3 = ngsim_period(T0_MS + 160_000, 1800, seed=3)
    periods, _ = mm.i80_data.dedupe_and_split(pd.concat([p2, p3], ignore_index=True))
    monkeypatch.setattr(mm.i80_data, "load_periods", lambda labels=None, directory=None: periods)
    summary = tmp_path / "i80_data.json"
    summary.write_text(
        json.dumps({"replica_block": ["p1", "p2"], "data_hash": "abc", "data_version": "synthetic"})
    )
    out = tmp_path / "observed.json"
    args = mm.argparse.Namespace(
        data_summary=str(summary), block=None, n_boot=10, seed=mm.SEED, out=str(out)
    )
    art = mm.run_observed(args)
    assert json.loads(out.read_text())["kind"] == "observed"
    assert art["block"] == ["p1", "p2"]
    assert [w["period"] for w in art["windows"]] == ["p1", "p2"]
    assert art["zone"]["kind"] == "merge" and art["zone"]["x_lo_m"] < art["zone"]["x_hi_m"]
    m = art["measures"]
    assert m["n_changes"] > 20
    assert m["partner_speed_ms"]["follower"]["ci95"] is not None
    assert m["accepted_gap_s"]["lead"]["n"] > 0
    assert m["critical_gap_s"]["n_drivers"] == m["n_changes"]
