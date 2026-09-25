"""Synthetic-driver tests for the critical-gap estimators (calibration.critical_gap, WP-78).

Drivers are drawn with critical gaps from a known log-normal and offered
random gaps until one clears their critical gap (the consistent driver of
Troutbeck's method); the estimators must recover the distribution they came
from. Hand-computed likelihoods pin the formulas, and the mapping onto the
weave's acceptance is checked against ``weave_acceptance`` and the IDM.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy.special import ndtr

from calibration.critical_gap import (
    MIN_REJECTING,
    _interval_prob,
    _joint_nll,
    _staircases,
    acceptance_mapping,
    bootstrap_medians,
    driver_gaps,
    fit_critical_gap,
    fit_groups,
    fit_joint_critical_gaps,
    implied_follower_decel,
    model_parity_critical_gaps,
    prepare_joint,
    rejected_points,
    select_drivers,
)
from calibration.lane_change_gaps import AcceptanceParams, gap_sequences, lane_change_gaps

MU_1, SIGMA_1 = math.log(1.5), 0.3
MU_L, SIGMA_L = math.log(1.2), 0.3
MU_G, SIGMA_G = math.log(1.8), 0.35
PARAMS = AcceptanceParams(
    accept_gap_s=0.6,
    exit_accept_gap_s=0.6,
    s0_m=2.5,
    T_s=1.3,
    a_max=1.0,
    b=1.7,
    v0_ms=30.0,
    source="test",
)


def _one_sided(seed: int, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Consistent drivers, ``ln t_c ~ N(ln 1.5, 0.3²)``, offered gaps 0.2 + Exp(2 s)."""
    rng = np.random.default_rng(seed)
    tc = np.exp(MU_1 + SIGMA_1 * rng.standard_normal(n))
    a, r = np.empty(n), np.zeros(n)
    for i in range(n):
        while True:
            g = 0.2 + rng.exponential(2.0)
            if g >= tc[i]:
                a[i] = g
                break
            r[i] = max(r[i], g)
    return a, r


def _two_sided(
    seed: int, n: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Drivers who need both sides: lead ``ln 1.2 / 0.3``, lag ``ln 1.8 / 0.35``;
    independent offers until both clear. Returns accepted, rejected points and
    each side's largest rejected value."""
    rng = np.random.default_rng(seed)
    tl = np.exp(MU_L + SIGMA_L * rng.standard_normal(n))
    tg = np.exp(MU_G + SIGMA_G * rng.standard_normal(n))
    al, ag, rl, rg = np.empty(n), np.empty(n), np.zeros(n), np.zeros(n)
    pd_, pl, pg = [], [], []
    for i in range(n):
        while True:
            lead, lag = 0.1 + rng.exponential(2.0), 0.1 + rng.exponential(2.5)
            if lead >= tl[i] and lag >= tg[i]:
                al[i], ag[i] = lead, lag
                break
            pd_.append(i)
            pl.append(lead)
            pg.append(lag)
            rl[i], rg[i] = max(rl[i], lead), max(rg[i], lag)
    return al, ag, np.array(pd_), np.array(pl), np.array(pg), rl, rg


class TestSeparateEstimator:
    def test_recovers_a_known_lognormal(self) -> None:
        a, r = _one_sided(1, 2000)
        f = fit_critical_gap(a, r)
        assert f.converged
        assert f.median == pytest.approx(1.5, rel=0.05)
        assert f.sigma == pytest.approx(SIGMA_1, abs=0.04)
        assert f.mean == pytest.approx(math.exp(f.mu + f.sigma**2 / 2))
        assert f.quantile(0.5) == pytest.approx(f.median)
        assert f.counts["n_used"] == 2000 and f.counts["n_inconsistent"] == 0
        assert 0 < f.counts["n_no_rejection"] < 2000

    def test_dropping_drivers_without_a_rejection_biases_upward(self) -> None:
        # Weinert (2000) kept only drivers who rejected a gap; under the
        # consistent-driver model the others are informative
        a, r = _one_sided(1, 2000)
        keep = fit_critical_gap(a, r)
        drop = fit_critical_gap(a, r, no_rejection="exclude")
        assert drop.counts["n_no_rejection_excluded"] == keep.counts["n_no_rejection"]
        assert drop.median > keep.median + 0.15

    def test_bootstrap_interval_covers_the_truth(self) -> None:
        a, r = _one_sided(2, 800)
        f = fit_critical_gap(a, r)

        def one(w: np.ndarray) -> tuple[float, ...]:
            g = fit_critical_gap(a, r, weights=w, x0=(f.mu, f.sigma))
            return (g.mu, g.sigma)

        b = bootstrap_medians(a.size, one, n_boot=60, seed=7)
        assert b.shape == (60, 2)
        lo, hi = np.exp(np.percentile(b[:, 0], [2.5, 97.5]))
        assert lo < 1.5 < hi
        assert np.array_equal(b, bootstrap_medians(a.size, one, n_boot=60, seed=7))
        assert bootstrap_medians(a.size, one, n_boot=0, seed=7).shape == (0, 2)

    def test_interval_probability_by_hand(self) -> None:
        mu, s = math.log(1.5), 0.3
        a = np.array([2.0, 1.0, np.inf, 4.0])
        r = np.array([1.0, 0.0, 1.2, 3.5])  # an interval, no rejection, censored, upper tail
        z = lambda t: (math.log(t) - mu) / s  # noqa: E731
        exp = [
            ndtr(z(2.0)) - ndtr(z(1.0)),
            ndtr(z(1.0)),
            1.0 - ndtr(z(1.2)),
            ndtr(-z(3.5)) - ndtr(-z(4.0)),
        ]
        assert _interval_prob(a, r, mu, s) == pytest.approx(exp, rel=1e-12)

    def test_edge_cases_are_counted(self) -> None:
        a = np.array([1.2, 2.0, np.inf, np.inf, np.nan, 1.0, 3.0, 1.6])
        r = np.array([0.0, 2.5, 0.0, 1.1, 0.5, 0.4, 1.0, 1.3])
        f = fit_critical_gap(a, r)
        c = f.counts
        assert c["n_undefined"] == 1  # NaN accepted gap
        assert c["n_inconsistent"] == 1 and c["n_inconsistent_excluded"] == 1  # 2.5 ≥ 2.0
        assert c["n_uninformative"] == 1  # inf accepted, nothing rejected
        assert c["n_censored"] == 1  # inf accepted, 1.1 rejected: 1 − F(1.1)
        assert c["n_no_rejection"] == 2 and c["n_with_rejection"] == 4
        assert c["n_used"] == 5
        # the inconsistent driver kept on its accepted gap alone
        g = fit_critical_gap(a, r, inconsistent="drop_rejected")
        assert g.counts["n_used"] == 6 and g.counts["n_inconsistent_excluded"] == 0
        # the log-likelihood is the sum of the interval log-probabilities
        use = np.array([True, False, False, True, False, True, True, True])
        p = _interval_prob(a[use], r[use], f.mu, f.sigma)
        assert f.loglik == pytest.approx(float(np.sum(np.log(p))), rel=1e-9)

    def test_bad_inputs_raise(self) -> None:
        with pytest.raises(ValueError, match="too few"):
            fit_critical_gap([1.0], [0.5])
        with pytest.raises(ValueError, match="one value per driver"):
            fit_critical_gap([1.0, 2.0], [0.5])
        with pytest.raises(ValueError, match="no_rejection"):
            fit_critical_gap([1.0, 2.0], [0.5, 0.5], no_rejection="drop")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="inconsistent"):
            fit_critical_gap([1.0, 2.0], [0.5, 0.5], inconsistent="keep")  # type: ignore[arg-type]


class TestJointEstimator:
    def test_recovers_both_sides_where_the_separate_one_is_biased(self) -> None:
        al, ag, pd_, pl, pg, rl, rg = _two_sided(3, 2000)
        data = prepare_joint(al, ag, pd_, pl, pg)
        jf = fit_joint_critical_gaps(data)
        assert jf.converged and data.counts["n_inconsistent"] == 0
        assert jf.lead.median == pytest.approx(1.2, rel=0.06)
        assert jf.lag.median == pytest.approx(1.8, rel=0.06)
        assert jf.lead.sigma == pytest.approx(SIGMA_L, abs=0.05)
        assert jf.lag.sigma == pytest.approx(SIGMA_G, abs=0.05)
        # a gap refused for one side counts as rejected on both: the separate
        # estimator overshoots both medians and invents inconsistent drivers
        sl, sg = fit_critical_gap(al, rl), fit_critical_gap(ag, rg)
        assert sl.median > 1.2 * 1.2 and sg.median > 1.8 * 1.1
        assert sl.counts["n_inconsistent"] > 100 and sg.counts["n_inconsistent"] > 100

    def test_reduces_to_the_separate_estimator_when_a_side_never_binds(self) -> None:
        a, r = _one_sided(4, 600)
        rng = np.random.default_rng(4)
        # every driver's rejected lead values below r (the largest), lag side always open
        pts_d, pts_l = [], []
        for i in np.flatnonzero(r > 0):
            for val in (r[i], r[i] * rng.uniform(0.2, 1.0)):
                pts_d.append(i)
                pts_l.append(val)
        inf = np.full(len(pts_d), np.inf)
        data = prepare_joint(a, np.full(a.size, np.inf), np.array(pts_d), np.array(pts_l), inf)
        jf = fit_joint_critical_gaps(data)
        sep = fit_critical_gap(a, r)
        assert jf.lead.mu == pytest.approx(sep.mu, abs=2e-3)
        assert jf.lead.sigma == pytest.approx(sep.sigma, abs=2e-3)
        assert jf.loglik == pytest.approx(sep.loglik, abs=1e-4)

    def test_staircase_and_likelihood_by_hand(self) -> None:
        # the passing driver of the gap_sequences test: accepted (0.65, 8/15),
        # rejected (0.1, 16/15), (0.35, 11/15), (0.6, 0.4), (0.85, 1/15), (0.2, 22/15)
        al, ag = np.array([0.65]), np.array([8.0 / 15.0])
        pl = np.array([0.1, 0.35, 0.6, 0.85, 0.2])
        pg = np.array([16.0, 11.0, 6.0, 1.0, 22.0]) / 15.0
        d, x, y, y_prev = _staircases(al, ag, np.zeros(5, dtype=np.int64), pl, pg)
        assert d.tolist() == [0, 0, 0]
        assert x == pytest.approx([0.65, 0.6, 0.35])
        assert y == pytest.approx([1.0 / 15.0, 0.4, 8.0 / 15.0])
        assert y_prev == pytest.approx([0.0, 1.0 / 15.0, 0.4])
        data = prepare_joint(al, ag, np.zeros(5, dtype=np.int64), pl, pg)
        assert data.counts["n_inconsistent"] == 0 and data.counts["n_steps"] == 3
        m_l, s_l, m_g, s_g = math.log(0.5), 0.4, math.log(0.45), 0.5

        def f_l(t: float) -> float:
            return float(ndtr((math.log(t) - m_l) / s_l))

        def f_g(t: float) -> float:
            return float(ndtr((math.log(t) - m_g) / s_g)) if t > 0 else 0.0

        inside = f_l(0.65) * f_g(8 / 15) - (
            f_l(0.65) * f_g(1 / 15)
            + f_l(0.6) * (f_g(0.4) - f_g(1 / 15))
            + f_l(0.35) * (f_g(8 / 15) - f_g(0.4))
        )
        theta = np.array([m_l, math.log(s_l), m_g, math.log(s_g)])
        assert _joint_nll(theta, data, np.ones(1)) == pytest.approx(-math.log(inside), rel=1e-10)

    def test_inconsistent_and_open_drivers(self) -> None:
        al = np.array([1.0, 2.0, np.inf, 1.5, np.nan])
        ag = np.array([1.0, 2.0, np.inf, np.inf, 1.0])
        # driver 0 rejected (1.2, 1.1) ⊇ its accepted (1.0, 1.0): inconsistent;
        # driver 3 rejected a combination with no lead within range and a 2 s lag
        pd_ = np.array([0, 1, 3, 3])
        pl = np.array([1.2, 1.0, np.inf, np.inf])
        pg = np.array([1.1, 0.5, 2.0, np.inf])  # (inf, inf) is not a rejection: dropped
        data = prepare_joint(al, ag, pd_, pl, pg)
        c = data.counts
        assert c["n_undefined"] == 1 and c["n_inconsistent"] == 1
        assert c["n_uninformative"] == 1  # driver 2: both sides open, nothing rejected
        assert c["n_used"] == 2 and c["n_with_rejection"] == 2 and c["n_censored"] == 1
        kept = prepare_joint(al, ag, pd_, pl, pg, inconsistent="drop_rejected")
        assert kept.counts["n_used"] == 3 and kept.counts["n_points"] == 2
        excl = prepare_joint(al, ag, pd_, pl, pg, no_rejection="exclude")
        assert excl.counts["n_used"] == 2
        with pytest.raises(ValueError, match="too few"):
            fit_joint_critical_gaps(prepare_joint(al[:1], ag[:1], pd_[:0], pl[:0], pg[:0]))


class TestCoverage:
    def test_hiding_half_the_vehicles_biases_the_estimate_upward(self) -> None:
        """I-24 MOTION tracks about half the vehicles: an untracked vehicle merges
        two true gaps into one observed gap. A gap stream (0.3 s + Exp(1.2 s))
        with each vehicle tracked at p = 0.5: the accepted gap is the observed
        gap containing the true one, the rejected ones the observed gaps before
        it. The fitted median rises (1.53 → 1.91 s at this seed)."""
        rng = np.random.default_rng(5)
        n = 1500
        tc = np.exp(MU_1 + SIGMA_1 * rng.standard_normal(n))
        a_t, r_t, a_o, r_o = (np.zeros(n) for _ in range(4))
        for i in range(n):
            h: list[float] = []
            while True:
                h.append(0.3 + rng.exponential(1.2))
                if h[-1] >= tc[i]:
                    break
            j = len(h) - 1
            h += list(0.3 + rng.exponential(1.2, size=20))
            a_t[i], r_t[i] = h[j], max(h[:j], default=0.0)
            tracked = rng.random(len(h) + 1) < 0.5
            tracked[0] = True
            edges = np.concatenate([[0.0], np.cumsum(h)])
            tb = np.flatnonzero(tracked[: edges.size])
            lo = tb[tb <= j].max()
            above = tb[tb >= j + 1]
            hi = above.min() if above.size else edges.size - 1
            a_o[i] = edges[hi] - edges[lo]
            before = np.diff(edges[tb[tb <= lo]])
            r_o[i] = before.max() if before.size else 0.0
        true_fit, observed = fit_critical_gap(a_t, r_t), fit_critical_gap(a_o, r_o)
        assert true_fit.median == pytest.approx(1.5, rel=0.05)
        assert observed.median > true_fit.median * 1.15
        assert np.all(a_o >= a_t - 1e-12)


def _sequence_frame() -> pd.DataFrame:
    """The gap_sequences test's passing driver (lane 5 → 4 at t = 112 s) in a weave zone."""
    dt = 0.2

    def track(vid: str, x0: float, v: float, lanes: list[int], k0: int = 0) -> pd.DataFrame:
        k = k0 + np.arange(len(lanes))
        return pd.DataFrame(
            {
                "t": 100.0 + dt * k,
                "veh_id": vid,
                "x": x0 + v * dt * np.arange(len(lanes)),
                "lane": np.asarray(lanes),
                "v": v,
                "length": 5.0,
            }
        )

    parts = [track("C", 1000.0, 20.0, [5] * 60 + [4] * 11)]
    for name, rel in (("S", -77.0), ("R", -41.0), ("P", -13.0), ("Q", 18.0), ("U", 60.0)):
        parts.append(track(name, 1240.0 + rel - 180.0, 15.0, [4] * 71))
    return pd.concat(parts, ignore_index=True)


class TestDriverTables:
    def test_driver_row_and_points_from_a_sequence(self) -> None:
        from calibration.lane_change_gaps import Zone

        df = _sequence_frame()
        zones = (Zone("w", "weave", 1000.0, 1500.0),)
        out = lane_change_gaps(df, zones, mainline_lanes=(1, 2, 3, 4), aux_lanes=(5,))
        seq = gap_sequences(df, out.records, zones=zones)
        drv = driver_gaps(out.records, seq.samples)
        assert len(drv) == 1
        d = drv.iloc[0]
        assert (d["movement"], d["zone_kind"]) == ("entering", "weave")
        assert d["a_lead_s"] == pytest.approx(13.0 / 20.0)
        assert d["a_lag_s"] == pytest.approx(8.0 / 15.0)
        # non-suspect rejected instants: leads 2, 7, 12, 17, 4 m over 20 m/s; lags 16, 11, 6, 1, 22 m / 15
        assert d["r_lead_s"] == pytest.approx(17.0 / 20.0)
        assert d["r_lag_s"] == pytest.approx(22.0 / 15.0)
        assert d["n_rejected_gaps"] == 2 and d["n_rejected_samples"] == 5
        assert d["lookback_s"] == pytest.approx(10.0)
        pts = rejected_points(seq.samples)
        assert len(pts) == 5 and set(pts["change"]) == {int(d["change"])}
        assert sorted(pts["lead_s"].round(9)) == pytest.approx([0.1, 0.2, 0.35, 0.6, 0.85])
        # the 5 s sensitivity keeps the instants at 4 and 5 s only
        short = driver_gaps(out.records, seq.samples, max_lookback_s=5.0).iloc[0]
        assert short["r_lead_s"] == pytest.approx(7.0 / 20.0)
        assert short["r_lag_s"] == pytest.approx(16.0 / 15.0)
        assert len(rejected_points(seq.samples, max_lookback_s=5.0)) == 2
        # separate: both sides inconsistent (0.85 ≥ 0.65, 1.47 ≥ 0.53); joint: consistent
        assert d["r_lead_s"] >= d["a_lead_s"] and d["r_lag_s"] >= d["a_lag_s"]
        data = prepare_joint(
            drv["a_lead_s"], drv["a_lag_s"], np.zeros(len(pts), dtype=np.int64), pts["lead_s"],
            pts["lag_s"],
        )  # fmt: skip
        assert data.counts["n_inconsistent"] == 0 and data.counts["n_used"] == 1

    def test_no_sampled_change_gives_an_empty_table(self) -> None:
        df = _sequence_frame()
        out = lane_change_gaps(df, (), mainline_lanes=(1, 2, 3, 4), aux_lanes=(5,))
        seq = gap_sequences(df, out.records, changes=[False])
        assert driver_gaps(out.records, seq.samples).empty
        assert rejected_points(seq.samples).empty


def _synthetic_drivers(
    seed: int, n: int, v: float, movement: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A driver table and its rejected points from :func:`_two_sided`, all at speed ``v``."""
    al, ag, pd_, pl, pg, rl, rg = _two_sided(seed, n)
    return pd.DataFrame(
        {
            "change": np.arange(n),
            "zone": "w",
            "zone_kind": "weave",
            "movement": movement,
            "v": v,
            "lag_v": v,
            "confirmed": True,
            "suspect": False,
            "a_lead_s": al,
            "a_lag_s": ag,
            "r_lead_s": rl,
            "r_lag_s": rg,
            "n_rejected_gaps": np.bincount(pd_, minlength=n),
            "lookback_s": 10.0,
        }
    ), pd.DataFrame({"change": pd_, "lead_s": pl, "lag_s": pg})


class TestGroupsAndMapping:
    def test_fit_groups_rows_and_small_classes(self) -> None:
        drv, pts = _synthetic_drivers(6, 400, 15.0, "entering")
        tiny, tiny_pts = _synthetic_drivers(7, 20, 25.0, "entering")
        tiny["change"] += 1000
        tiny_pts["change"] += 1000
        drivers = select_drivers(pd.concat([drv, tiny], ignore_index=True))
        points = pd.concat([pts, tiny_pts], ignore_index=True)
        rows, boots = fit_groups(drivers, points, n_boot=20, seed=11)
        assert [(r["speed_class"], r["n_drivers"]) for r in rows] == [
            ("all", 420),
            ("v<10", 0),
            ("10<=v<20", 400),
            ("v>=20", 20),
        ]
        mid = rows[2]
        assert mid["joint"]["fitted"] and mid["separate_lead"]["fitted"]
        lo, hi = mid["joint"]["lead"]["ci95"]["median_s"]
        assert lo < mid["joint"]["lead"]["median_s"] < hi
        assert mid["joint"]["lead"]["median_s"] == pytest.approx(1.2, rel=0.12)
        assert boots[2]["joint_lead"].shape == (20,)
        assert not rows[3]["joint"]["fitted"] and rows[3]["joint"]["n_used"] == 20
        assert "separate_lead" not in rows[1]
        # the same seed gives the same rows
        again, _ = fit_groups(drivers, points, n_boot=20, seed=11)
        assert again == rows

    def test_a_group_without_rejections_is_not_fitted(self) -> None:
        drv, pts = _synthetic_drivers(8, 100, 15.0, "exiting")
        keep = drv["n_rejected_gaps"].to_numpy() > 0
        rejecting = np.flatnonzero(keep)[: MIN_REJECTING - 1]
        drv.loc[~drv.index.isin(rejecting), ["r_lead_s", "r_lag_s"]] = 0.0
        pts = pts[pts["change"].isin(rejecting)]
        rows, _ = fit_groups(select_drivers(drv), pts, n_boot=5, seed=1)
        assert not rows[0]["joint"]["fitted"]
        assert rows[0]["joint"]["n_with_rejection"] == MIN_REJECTING - 1
        assert not rows[0]["separate_lead"]["fitted"]

    def test_model_parity_critical_gaps_by_hand(self) -> None:
        v = np.array([5.0, 10.0, 20.0])
        par = model_parity_critical_gaps(v, PARAMS, rightward=False)
        assert par["lead_s"] == pytest.approx(0.6 + 5.0 / v)
        absorb = (2.5 + v * 1.3) / np.sqrt(1.0 - (v / 30.0) ** 4 + 1.7)
        assert par["lag_absorb_s"] == pytest.approx(absorb / v)
        assert par["lag_time_s"] == pytest.approx(0.6 + 5.0 / v)
        assert par["lag_s"] == pytest.approx(np.maximum(0.6 + 5.0 / v, absorb / v))
        over = model_parity_critical_gaps(v, PARAMS, rightward=True, accept_s=0.9)
        assert over["lead_s"] == pytest.approx(0.9 + 5.0 / v)

    def test_implied_follower_deceleration_is_the_idm(self) -> None:
        t, vf = 0.5, 12.0
        s = t * vf
        exp = -1.0 * (1.0 - (vf / 30.0) ** 4 - ((2.5 + vf * 1.3) / s) ** 2)
        assert implied_follower_decel(t, vf, PARAMS) == pytest.approx(exp)

    def test_the_mapping_recovers_the_models_own_time_gap(self) -> None:
        """Rows whose fitted medians ARE the acceptance's parity critical gaps at
        A = 0.45 map back to 0.45; a lag median under the absorption floor is
        flagged with its implied follower deceleration."""
        rows, boots = [], []
        for label, v, n in (("v<10", 8.0, 100), ("10<=v<20", 15.0, 60)):
            par = model_parity_critical_gaps([v], PARAMS, rightward=False, accept_s=0.45)
            t_l, t_g = float(par["lead_s"][0]), float(par["lag_time_s"][0])
            fit = {"median_s": round(t_l, 4), "degenerate": False}
            fit_g = {"median_s": round(t_g, 4), "degenerate": False}
            rows.append(
                {
                    "speed_class": label,
                    "v_ms_p50": v,
                    "lag_v_ms_p50": v,
                    "joint": {"fitted": True, "n_used": n, "lead": fit, "lag": fit_g},
                }
            )
            boots.append({"joint_lead": np.full(10, t_l), "joint_lag": np.full(10, t_g)})
        rows.insert(0, {"speed_class": "all", "v_ms_p50": 10.0})
        boots.insert(0, {})
        out = acceptance_mapping(rows, boots, PARAMS, movement="entering")
        assert out["parameter"] == "accept_gap_s" and out["current"] == 0.6
        assert out["accept_s_lead"]["value"] == pytest.approx(0.45, abs=2e-4)
        slow, fast = out["classes"]
        # at 8 m/s the time term binds on the follower side; at 15 m/s the absorption floor does
        assert slow["lag_above_absorb_floor"] and slow["implied_accept_s_lag"] == pytest.approx(
            0.45, abs=2e-4
        )
        assert not fast["lag_above_absorb_floor"] and fast["implied_accept_s_lag"] is None
        exp_b = implied_follower_decel(fast["fitted_lag_median_s"], 15.0, PARAMS)
        assert fast["implied_follower_decel_ms2"] == pytest.approx(exp_b, abs=1e-4)
        assert exp_b > PARAMS.b
        assert out["accept_s"]["value"] == pytest.approx(0.45, abs=2e-4)
        assert out["accept_s"]["ci95"] == pytest.approx([0.45, 0.45], abs=2e-4)
        assert slow["model_lead_s_at_proposal"] == pytest.approx(slow["fitted_lead_median_s"])
        # an exiting group reads exit_accept_gap_s; no fitted class gives no proposal
        empty = acceptance_mapping(rows[:1], boots[:1], PARAMS, movement="exiting")
        assert empty["parameter"] == "exit_accept_gap_s" and empty["accept_s"] is None


class TestBoundedParameters:
    """VM Y (2026-09-25): the joint fit on the I-24 data died with OverflowError in ``_joint_nll``
    when Nelder-Mead walked a flat side's log-sigma past exp's range. Parameters are now held within
    bounds, and a fit at a bound is flagged ``at_bound`` (not identified) and skipped by the proposal."""

    @staticmethod
    def _uninformative_lag() -> tuple[np.ndarray, ...]:
        rng = np.random.default_rng(7)
        n = 200
        a_lead = np.exp(rng.normal(np.log(1.5), 0.4, n)) * 1.3
        a_lag = np.full(n, np.inf)  # no lag vehicle ever within range: the lag side carries no information
        drv = np.repeat(np.arange(n), 2)
        pl = np.stack([a_lead * 0.6, a_lead * 0.5], axis=1).ravel()
        pg = np.full(2 * n, np.inf)
        return a_lead, a_lag, drv, pl, pg

    def test_the_likelihood_does_not_overflow_at_extreme_log_sigma(self) -> None:
        from calibration import critical_gap as cg

        data = cg.prepare_joint(*self._uninformative_lag())
        w = np.ones(data.counts["n_used"])
        for theta in ([0.4, 800.0, 0.4, 800.0], [0.4, -800.0, 800.0, 800.0], [-800.0, 0.0, 0.0, 0.0]):
            value = cg._joint_nll(np.asarray(theta, dtype=np.float64), data, w)
            assert np.isfinite(value)

    def test_a_flat_side_is_flagged_not_identified(self) -> None:
        from calibration import critical_gap as cg

        fit = cg.fit_joint_critical_gaps(cg.prepare_joint(*self._uninformative_lag()))
        assert fit.lag.at_bound
        assert fit.lag.to_dict()["at_bound"] is True
        assert not fit.lead.at_bound
        assert 1.0 < fit.lead.median < 2.5
