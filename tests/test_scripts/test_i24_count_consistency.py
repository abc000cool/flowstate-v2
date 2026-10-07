"""scripts/i24_count_consistency.py on synthetic trajectory tables.

The I-24 MOTION table is never read here. Small corridors at constant speed on
the 0.2-s grid, laid out as ``convert_i24_to_parquet`` writes them (``t,
veh_id, x, lane, v``), with a compact layout (three sections, optional ramps)
for the conservation tests and the I-24 layout for the end-to-end run.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _load() -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "flowstate_i24_count_consistency", SCRIPTS / "i24_count_consistency.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cc = _load()

DT = 0.2
V = 15.0  # m/s (54 km/h: congested speed classes, below the 60 km/h cut)
SECTIONS = (300.0, 900.0, 1500.0)
NO_CAP = 10.0  # veh/s: a capacity floor that never binds on these corridors


def _layout(ramps: tuple = (), **kw: object) -> object:
    return cc.Layout(
        sections_m=SECTIONS,
        ramps=ramps,
        spans_m=((300.0, 1500.0),),
        primary_span_m=(300.0, 1500.0),
        peak_sections_m=(900.0,),
        far_end_m=1500.0,
        main_lanes=(1, 2),
        **kw,
    )


def _period(t_lo: float = 300.0, t_hi: float = 600.0, **kw: object) -> object:
    return cc.Period(
        t_lo_s=t_lo,
        t_hi_s=t_hi,
        window_s=100.0,
        coverage_window_s=300.0,
        storage_half_slots=0,
        **kw,
    )


def _long_period() -> object:
    """Three 5-min windows in one 15-min coverage window, as on I-24 (enough vehicles per cell
    for the mixture to agree between cells within about 2 %)."""
    return cc.Period(t_lo_s=300.0, t_hi_s=1200.0, storage_half_slots=0)


def _grid(t0: float, t1: float) -> np.ndarray:
    return np.round(np.arange(round(t0 / DT), round(t1 / DT)) * DT, 6)


def _stream(
    lane: int,
    *,
    spacing: float,
    keep: float,
    rng: np.random.Generator,
    prefix: str,
    t0: float = 230.0,
    t1: float = 612.0,
    x_lo: float = -100.0,
    x_hi: float = 2200.0,
    cv: float = 0.3,
) -> tuple[pd.DataFrame, np.ndarray]:
    """One lane of vehicles at V with gamma spacings; a random share ``keep`` tracked.

    Returns the rows (inside [x_lo, x_hi)) and the tracked vehicles' positions at t = 0.
    """
    tg = _grid(t0, t1)
    n = int(((x_hi - x_lo) + V * (t1 - t0)) / spacing) + 5
    gaps = rng.gamma(1.0 / cv**2, spacing * cv**2, n)
    p0 = x_hi + spacing - V * t0 - np.cumsum(gaps)
    p0 = p0[rng.random(n) < keep]
    xx = p0[:, None] + V * tg[None, :]
    m = (xx >= x_lo) & (xx < x_hi)
    vi, ti = np.nonzero(m)
    df = pd.DataFrame(
        {
            "t": tg[ti],
            "veh_id": [f"{prefix}{i}" for i in vi],
            "x": xx[vi, ti],
            "lane": lane,
            "v": V,
        }
    )
    return df, p0


def _break_at(df: pd.DataFrame, x_s: float, ids: set[str]) -> pd.DataFrame:
    """Break the given fragments at ``x_s``: drop [x_s − 5, x_s + 5) and relabel the rest."""
    hit = df["veh_id"].isin(ids)
    hole = hit & (df["x"] >= x_s - 5.0) & (df["x"] < x_s + 5.0)
    out = df.loc[~hole].copy()
    after = out["veh_id"].isin(ids) & (out["x"] >= x_s + 5.0)
    out.loc[after, "veh_id"] = out.loc[after, "veh_id"] + "_b"
    return out.reset_index(drop=True)


def _run(df: pd.DataFrame, layout: object, period: object, c_pool: float, **kw: object) -> dict:
    tally = cc.run_check(cc.frame_reader(df, layout), layout, period, q_cap_fd_veh_s=NO_CAP)
    res = cc.analyze(tally, layout, period, np.full(period.n_windows, c_pool), n_boot=200, **kw)
    res["_tally"] = tally
    return res


def _pair(res: dict, a: float, b: float) -> dict:
    return next(p for p in res["pairs"] if (p["from_m"], p["to_m"]) == (a, b))


def _section(res: dict, x: float) -> dict:
    return next(s for s in res["sections"] if s["x_m"] == x)


def _corridor(seed: int) -> tuple[pd.DataFrame, list[str]]:
    rng = np.random.default_rng(seed)
    parts = [
        _stream(ln, spacing=20.0, keep=0.6, rng=rng, prefix=f"L{ln}_", t1=1212.0)[0]
        for ln in (1, 2)
    ]
    df = pd.concat(parts, ignore_index=True)
    return df, sorted(df["veh_id"].unique())


def test_conserving_corridor_gives_zero_residual() -> None:
    df, _ = _corridor(1)
    layout, period = _layout(), _long_period()
    res = _run(df, layout, period, 0.6)
    for p in res["pairs"]:
        # exact storage (one slot) and continuous fragments: every window balances exactly
        assert p["residual_veh_h_per_window"]["raw_storage"] == [0.0, 0.0, 0.0]
        assert p["residual_veh_h_per_window"]["pooled_storage"] == [0.0, 0.0, 0.0]
        assert abs(p["residual_veh_h"]["section_storage"]["mean"]) < 0.05 * 3600 * 2 * 0.75
    q = [_section(res, x)["flow_veh_h"]["tracked"]["mean"] for x in SECTIONS]
    assert min(q) > 0.0
    # the same stream passes every cell: the measured section coverage agrees between sections
    # and sits near the planted 0.6 (random thinning, gamma spacings)
    c = [_section(res, x)["coverage"]["c_recommended_period"] for x in SECTIONS]
    assert max(c) / min(c) < 1.1
    assert all(0.45 < v < 0.75 for v in c)
    ratio = [
        _section(res, x)["coverage"]["crossing_to_edie_per_coverage_window"][0] for x in SECTIONS
    ]
    assert all(abs(r - 1.0) < 0.08 for r in ratio)
    v = res["verdict"]
    assert v["outcome"] == "consistent" and not v["nonconservation"]


def test_planted_section_coverage_drop_gives_the_expected_residual() -> None:
    df, ids = _corridor(2)
    broken = set(ids[::10]) | set(ids[3::10]) | set(ids[6::10])  # 30 % of the tracked vehicles
    df = _break_at(df, 1500.0, broken)
    layout, period = _layout(), _long_period()
    model = {"flows_veh_h": {"900": 0.0, "1500": 0.0}}
    res = _run(df, layout, period, 0.6, model=model)
    q300 = _section(res, 300.0)["flow_veh_h"]["tracked"]["mean"]
    q1500 = _section(res, 1500.0)["flow_veh_h"]["tracked"]["mean"]
    assert q1500 / q300 == pytest.approx(0.7, abs=0.05)
    # the broken fragments end inside (900, 1500) and never reach the section: the residual is
    # minus the lost crossings, upstream pair untouched, and the pooled variant scales it by 1/c
    up, down, span = _pair(res, 300.0, 900.0), _pair(res, 900.0, 1500.0), _pair(res, 300.0, 1500.0)
    assert up["residual_veh_h_per_window"]["raw_storage"] == [0.0, 0.0, 0.0]
    r = down["residual_veh_h"]["raw_storage"]["mean"]
    assert r == pytest.approx(-0.3 * q300, rel=0.15)
    assert span["residual_veh_h"]["raw_storage"]["mean"] == pytest.approx(r, abs=1e-6)
    rp = span["residual_veh_h"]["pooled_storage"]
    assert rp["mean"] == pytest.approx(r / 0.6, rel=1e-9)
    assert rp["ci_block"][1] < 0.0
    # the section coverage measures the drop where it happened, and correcting each section by
    # its own coverage closes the balance
    c300 = _section(res, 300.0)["coverage"]["c_recommended_period"]
    c1500 = _section(res, 1500.0)["coverage"]["c_recommended_period"]
    assert c1500 / c300 == pytest.approx(0.7, abs=0.05)
    x_ratio = _section(res, 1500.0)["coverage"]["crossing_to_edie_per_coverage_window"][0]
    assert x_ratio == pytest.approx(0.7, abs=0.06)
    rs = span["residual_veh_h"]["section_storage"]["mean"]
    assert abs(rs) < 0.25 * abs(rp["mean"])
    v = res["verdict"]
    assert v["nonconservation"] and v["explained_by_section_coverage"]
    assert v["outcome"] == "inconsistent_section_coverage"
    assert v["targets_adopted"] == "section"
    row = next(r_ for r_ in v["model_vs_targets"] if r_["x_m"] == 1500.0)
    # the restated far-end target recovers the true flow, the pooled one is 30 % low
    q_true = 2 * V / 20.0 * 3600.0
    assert row["target_veh_h"] == pytest.approx(q_true, rel=0.15)
    assert row["original_target_veh_h"] == pytest.approx(0.7 * q_true, rel=0.15)


def _on_ramp_stream(rng: np.random.Generator, *, count_x: float, join_x: float) -> pd.DataFrame:
    """Ramp vehicles appearing in lane 5 at 450 m and joining lane 2 at ``join_x``."""
    df, _ = _stream(5, spacing=150.0, keep=1.0, rng=rng, prefix="R_", x_lo=450.0)
    df.loc[df["x"] >= join_x, "lane"] = 2
    assert count_x < join_x
    return df


def test_ramps_are_counted_correctly() -> None:
    rng = np.random.default_rng(3)
    main1, _ = _stream(1, spacing=30.0, keep=1.0, rng=rng, prefix="A_")
    main2, _ = _stream(2, spacing=30.0, keep=1.0, rng=rng, prefix="B_")
    on = _on_ramp_stream(rng, count_x=600.0, join_x=700.0)
    ids1, ids2 = sorted(main1["veh_id"].unique()), sorted(main2["veh_id"].unique())
    # through traffic in the on-ramp lane: every 5th lane-1 vehicle is in lane 5 over [580, 650)
    through_on = set(ids1[::5])
    m = main1["veh_id"].isin(through_on) & (main1["x"] >= 580.0) & (main1["x"] < 650.0)
    main1.loc[m, "lane"] = 5
    # exiters: every 4th lane-2 vehicle moves to lane 5 at 1,100 m and leaves the camera at 1,300
    exiters = set(ids2[::4])
    ex = main2["veh_id"].isin(exiters)
    main2.loc[ex & (main2["x"] >= 1100.0), "lane"] = 5
    main2 = main2.loc[~(ex & (main2["x"] >= 1300.0))]
    # through traffic in the off-ramp lane: every 6th lane-1 vehicle is in lane 5 over [1150, 1250)
    through_off = set(ids1[2::6]) - through_on
    m = main1["veh_id"].isin(through_off) & (main1["x"] >= 1150.0) & (main1["x"] < 1250.0)
    main1.loc[m, "lane"] = 5
    df = pd.concat([main1, main2, on], ignore_index=True)
    ramps = (cc.Ramp("on", "on", 600.0), cc.Ramp("off", "off", 1200.0))
    layout, period = _layout(ramps=ramps), _period()
    res = _run(df, layout, period, 1.0)
    tally = res["_tally"]

    def planted(frame: pd.DataFrame, ids: set[str] | None, x_s: float) -> np.ndarray:
        """First sample at or past x_s per vehicle, in [300, 600), binned by window."""
        sub = frame if ids is None else frame.loc[frame["veh_id"].isin(ids)]
        first = sub.loc[sub["x"] >= x_s].groupby("veh_id")["t"].min()
        first = first[(first >= 300.0) & (first < 600.0)]
        return np.bincount(((first - 300.0) // 100.0).astype(int), minlength=3)

    want_on = planted(on, None, 600.0)
    want_through_on = planted(main1, through_on, 600.0)
    want_off = planted(main2, exiters, 1200.0)
    want_through_off = planted(main1, through_off, 1200.0)
    assert min(want_on.sum(), want_through_on.sum(), want_off.sum(), want_through_off.sum()) > 0
    assert tally.ramp["count"][0].tolist() == (want_on + want_through_on).tolist()
    assert tally.ramp["prior_main"][0].tolist() == want_through_on.tolist()
    assert tally.ramp["count"][1].tolist() == (want_off + want_through_off).tolist()
    assert tally.ramp["later_main"][1].tolist() == want_through_off.tolist()
    assert tally.ramp["count_rule"].tolist() == tally.ramp["count"].tolist()
    # through traffic counted as ramp flow shows up as a residual of its size; removing the
    # flagged crossings restores the balance (up to vehicles in flight at the period's ends)
    f = 3600.0 / (period.t_hi_s - period.t_lo_s)  # one vehicle over the period, in veh/h
    up = _pair(res, 300.0, 900.0)["residual_veh_h"]
    assert up["raw_storage"]["mean"] == pytest.approx(-want_through_on.sum() * f, abs=2 * f)
    assert abs(up["pooled_storage_rampadj"]["mean"]) <= 2 * f
    down = _pair(res, 900.0, 1500.0)["residual_veh_h"]
    assert down["raw_storage"]["mean"] == pytest.approx(want_through_off.sum() * f, abs=2 * f)
    assert abs(down["pooled_storage_rampadj"]["mean"]) <= 2 * f
    names = [r["name"] for r in _pair(res, 300.0, 1500.0)["ramps"]]
    assert names == ["on", "off"]


def _frag(vid: str, t: list[float], x: list[float], lane: int = 1) -> pd.DataFrame:
    return pd.DataFrame({"t": t, "veh_id": vid, "x": x, "lane": lane, "v": 10.0})


def _moving(vid: str, t0: float, x0: float, x1: float) -> pd.DataFrame:
    """A fragment at 10 m/s from x0 (at t0) until it reaches x1."""
    n = int((x1 - x0) / (10.0 * DT)) + 1
    t = np.round(t0 + np.arange(n) * DT, 6)
    return _frag(vid, list(t), list(x0 + 10.0 * (t - t0)))


def test_fragments_that_cross_a_section_are_counted_once() -> None:
    # a: jitter across 300 m (the validator's rule counts it twice), then on to 600 m
    jitter = _frag("a", [349.6, 349.8, 350.0, 350.2, 350.4], [299.7, 299.9, 300.1, 299.95, 300.2])
    a_rest = _moving("a", 350.6, 302.0, 600.0)
    # b: crosses 900 m between 599.8 s and 600.0 s, i.e. across the chunk boundary
    b = _moving("b", 590.0, 800.5, 1000.0)
    # c: one vehicle in two fragments, broken at 600 m (between sections)
    c1, c2 = _moving("c1", 400.0, 200.0, 598.0), _moving("c2", 440.4, 602.0, 1600.0)
    # d: one fragment through every section
    d = _moving("d", 400.0, 100.0, 1700.0)
    df = pd.concat([jitter, a_rest, b, c1, c2, d], ignore_index=True)
    layout = _layout()
    period = _period(t_lo=300.0, t_hi=900.0)  # two chunks: [300, 600) and [600, 900)
    tally = cc.run_check(cc.frame_reader(df, layout), layout, period, q_cap_fd_veh_s=NO_CAP)
    counted = tally.counts["main"].sum(axis=1).tolist()
    assert counted == [3, 3, 2]  # 300: a, c1, d; 900: b, c2, d; 1500: c2, d
    assert tally.counts["main_rule"].sum(axis=1).tolist() == [4, 3, 2]
    w_b = int((600.0 - 300.0) // 100.0)
    assert tally.counts["main"][1, w_b] == 1  # b, in the later chunk only
    assert tally.counts["main"][1, w_b - 1] == 0
    res = cc.analyze(tally, layout, period, np.ones(period.n_windows), n_boot=50)
    assert [s["recrossings"] for s in res["sections"]] == [1, 0, 0]


def test_verdict_rules() -> None:
    layout = _layout()

    def prim(rp: float, rs: float, ra: float, rsa: float, ci: list[float]) -> dict:
        return {
            "pooled_storage": {"mean": rp, "ci_block": ci},
            "section_storage": {"mean": rs, "ci_block": None},
            "pooled_storage_rampadj": {"mean": ra, "ci_block": None},
            "section_storage_rampadj": {"mean": rsa, "ci_block": None},
        }

    targets = {
        "pooled": {900.0: 6626.0, 1500.0: 6009.0},
        "section": {900.0: 6200.0, 1500.0: 6100.0},
    }
    model = {"flows_veh_h": {"900": 6047.0, "1500": 5735.0}, "arm": "x"}
    v = cc.verdict(prim(-390, -60, -380, -50, [-450, -330]), targets, model, layout)
    assert v["outcome"] == "inconsistent_section_coverage" and v["targets_adopted"] == "section"
    peak = next(r for r in v["model_vs_targets"] if r["x_m"] == 900.0)
    assert peak["target_veh_h"] == 6200.0 and peak["geh_pass"]
    assert v["peak_shortfall_is_the_models"] is False
    v = cc.verdict(prim(-390, -300, -100, -50, [-450, -330]), targets, model, layout)
    assert v["outcome"] == "ramp_counts_contaminated" and v["targets_adopted"] == "pooled"
    assert v["peak_shortfall_is_the_models"] is True  # 6,626 against 6,047: GEH 7.3
    v = cc.verdict(prim(-390, -300, -300, -100, [-450, -330]), targets, model, layout)
    assert v["outcome"] == "inconsistent_section_coverage_and_ramp_contamination"
    v = cc.verdict(prim(-390, -300, -300, -300, [-450, -330]), targets, model, layout)
    assert v["outcome"] == "unexplained" and v["peak_shortfall_is_the_models"] is None
    v = cc.verdict(prim(-40, -30, -20, -10, [-120, 30]), targets, model, layout)
    assert v["outcome"] == "consistent" and v["targets_adopted"] == "pooled"
    assert v["peak_shortfall_is_the_models"] is True  # the targets stand: the shortfall is real
    # a residual whose interval excludes 0 but is below the material floor is not non-conservation
    v = cc.verdict(prim(-60, -60, -60, -60, [-70, -50]), targets, model, layout)
    assert v["outcome"] == "consistent" and not v["nonconservation"]


def test_i24_layout_and_pooled_coverage_match_the_committed_observed_side() -> None:
    observed = json.loads((REPO_ROOT / "artifacts" / "i24_validation_observed.json").read_text())
    inputs = json.loads((REPO_ROOT / "artifacts" / "i24_replica_inputs.json").read_text())
    cov = json.loads((REPO_ROOT / "artifacts" / "i24_coverage.json").read_text())
    layout, period = cc.i24_layout(), cc.Period()
    assert list(layout.sections_m) == [float(s) for s in observed["sections_m"]]
    assert [period.t_lo_s, period.t_hi_s] == [float(t) for t in observed["t_range_s"]]
    assert period.window_s == float(observed["window_s"]) and period.n_windows == 24
    assert [(r.name, r.kind, r.count_x_m) for r in layout.ramps] == [
        (r["name"], r["kind"], float(r["count_x_m"])) for r in inputs["ramps"]
    ]
    c = cc.recommended_coverage(cov, period)
    assert np.round(c, 4).tolist() == observed["coverage_recommended_per_window"]
    # the targets the check reads are the validator's recommended flows
    flows = np.asarray(observed["counts_tracked"]) * 12.0 / c[None, :]
    rec = np.asarray(observed["hourly_flows_veh_h_recommended"])
    assert np.allclose(flows, rec, atol=0.05)


def test_end_to_end_on_a_synthetic_table(tmp_path: Path) -> None:
    rng = np.random.default_rng(4)
    df, _ = _stream(
        2,
        spacing=500.0,
        keep=1.0,
        rng=rng,
        prefix="e",
        t0=1730.0,
        t1=2712.0,
        x_lo=-150.0,
        x_hi=6100.0,
    )
    wb = tmp_path / "wb"
    wb.mkdir()
    df.to_parquet(wb / "trajectories.parquet", index=False)
    (wb / "meta.json").write_text(json.dumps({"data_hash": "synthetic"}))
    out = tmp_path / "cc.json"
    cc.main(["--wb-dir", str(wb), "--out", str(out), "--t-range", "1800", "2700", "--n-boot", "20"])
    art = json.loads(out.read_text())
    assert art["data_hash"] == "synthetic" and len(art["table_sha256"]) == 64
    assert art["code"] and art["spec"].startswith("docs/I24_DISCHARGE_DIAGNOSIS.md")
    assert art["parameters"]["sections_m"] == [200.0, 1000.0, 2200.0, 3200.0, 4800.0, 5400.0]
    assert art["checks"]["validator_counts"]["compared"] is False
    assert art["checks"]["reproduces_committed_counts"] is None
    assert len(art["pairs"]) == 8 and len(art["ramps"]) == 4
    assert art["verdict"]["model"]["arm"] == "dc_refit"
    assert art["verdict"]["outcome"] == "consistent"  # residuals of a vehicle or two: immaterial
    # one lane, one stream: every section sees the same vehicles, up to the boundary windows
    q = [s["flow_veh_h"]["tracked"]["mean"] for s in art["sections"]]
    assert max(q) - min(q) <= 3 * 12.0
    assert art["counts"]["rows_read"] > 0


def test_reproduction_checks_void_a_run_whose_counts_differ() -> None:
    observed = json.loads((REPO_ROOT / "artifacts" / "i24_validation_observed.json").read_text())
    inputs = json.loads((REPO_ROOT / "artifacts" / "i24_replica_inputs.json").read_text())
    cov = json.loads((REPO_ROOT / "artifacts" / "i24_coverage.json").read_text())
    layout, period = cc.i24_layout(), cc.Period()
    tally = cc.Tally.empty(layout, period)
    tally.counts["main_rule"][:] = np.asarray(observed["counts_tracked"])
    for i, r in enumerate(inputs["ramps"]):
        tally.ramp["count_rule"][i] = r["ramp_lane_crossings"]
    c_pool = cc.recommended_coverage(cov, period)

    def checks() -> dict:
        return cc.consistency_checks(
            tally,
            layout,
            period,
            c_pool,
            observed["data_hash"],
            observed=observed,
            inputs=inputs,
            coverage=cov,
        )

    assert cc.reproduces(checks()) is True
    tally.counts["main_rule"][2, 5] += 1  # one crossing more at 2,200 m
    out = checks()
    assert out["validator_counts"]["max_abs_diff"] == 1
    assert cc.reproduces(out) is False
