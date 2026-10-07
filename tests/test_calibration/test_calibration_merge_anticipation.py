"""Synthetic-frame tests for the merge anticipation reach (calibration.merge_anticipation, M1).

Every frame is built by hand on a shared 0.2 s grid (the I-24 MOTION
processed cadence). A target-lane platoon (lane 4) runs at V with fronts S
apart; the planted gap is (F, L), F's front at ``XF0 + V t``. An entrant in
the auxiliary lane (5) runs at ``V + dv`` until ``t_on`` (overtaking the
platoon when ``dv > 0``, falling back through it when ``dv < 0``), then holds
``V`` at ``X_MID`` past F's front and changes into lane 4 at ``T_CHANGE``. It
is beside the gap from the first sample at which its front is past F's and
behind L's, so the planted ``gap`` reach is ``x(T_CHANGE) − x(that sample)``;
its speed is within ±1 m/s of L's from ``t_on``, so the planted ``speed_1``
reach is ``x(T_CHANGE) − x(t_on)``. The edge cases each move or stop one
of these at a known sample for a known reason.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from calibration.lane_change_gaps import Zone, lane_change_gaps
from calibration.merge_anticipation import (
    CENSOR_REASONS,
    OUTCOMES,
    AdoptionRule,
    Stratum,
    _empty_events,
    anticipation_events,
    change_positions,
    definitions,
    km_bootstrap,
    km_curve,
    km_quantile,
    km_quantiles,
    propose,
    speed_definition_name,
    stratum_summary,
    summarize_anticipation,
)
from flowstate_core.rng import make_rng

DT = 0.2
V = 25.0
LEN = 5.0
S = 60.0
XF0 = 1000.0
X_MID = 31.3
T_END = 45.0
T_CHANGE = 40.0
MAIN = (1, 2, 3, 4)
AUX = (5,)
WIDE = Zone("weave", "weave", -1.0e6, 1.0e7)


def _grid(t0: float = 0.0, t1: float = T_END) -> np.ndarray:
    k = np.arange(round(t0 / DT), round(t1 / DT) + 1)
    return np.round(k * DT, 6)


def _veh(
    vid: str,
    t: np.ndarray,
    x: np.ndarray,
    lane: np.ndarray | int,
    v: np.ndarray | float = V,
    length: float = LEN,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "t": t,
            "veh_id": vid,
            "x": x,
            "lane": np.broadcast_to(np.asarray(lane, dtype=np.int64), t.shape).copy(),
            "v": np.broadcast_to(np.asarray(v, dtype=float), t.shape).copy(),
            "length": length,
        }
    )


def _xf(t: np.ndarray, x0: float = XF0) -> np.ndarray:
    """F's front."""
    return x0 + V * t


def _platoon(
    t: np.ndarray, *, x0: float = XF0, ahead: int = 3, behind: int = 4, skip: tuple[str, ...] = ()
) -> list[pd.DataFrame]:
    """Lane-4 vehicles ``m{i}``: ``m0`` is F, ``m-1`` is L, fronts S apart."""
    out = []
    for i in range(-ahead, behind + 1):
        name = f"m{i}"
        if name in skip:
            continue
        out.append(_veh(name, t, x0 - i * S + V * t, 4))
    return out


def _entrant(
    t: np.ndarray,
    *,
    t_on: float = 30.0,
    dv: float = 3.0,
    t_change: float = T_CHANGE,
    x0: float = XF0,
    lane_before: int = 5,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Positions, speeds and lanes of the planted entrant."""
    x_on = x0 + V * t_on + X_MID
    x = np.where(t < t_on, x_on + (V + dv) * (t - t_on), x_on + V * (t - t_on))
    v = np.where(t < t_on, V + dv, V)
    lane = np.where(t < t_change, lane_before, 4)
    return x, v, lane


def _at(t: np.ndarray, x: np.ndarray, when: float) -> float:
    return float(x[np.flatnonzero(np.isclose(t, when))[0]])


def _first_beside(t: np.ndarray, x: np.ndarray, x0: float = XF0) -> float:
    """The first sample of the final run in which the entrant's front is past F's and behind L's."""
    beside = (x > _xf(t, x0)) & (x < _xf(t, x0) + S)
    before = np.flatnonzero(~beside & (t < T_CHANGE))
    return float(t[before[-1] + 1])


def _measure(df: pd.DataFrame, zones: list[Zone] | None = None, **kw: object) -> pd.DataFrame:
    zones = zones or [WIDE]
    recs = lane_change_gaps(df, zones, mainline_lanes=MAIN, aux_lanes=(*AUX, 6), dt_s=DT).records
    out = anticipation_events(df, recs, zones, mainline_lanes=MAIN, dt_s=DT, **kw)  # type: ignore[arg-type]
    return out.events


def _base(**entrant_kw: float) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    t = _grid()
    x, v, lane = _entrant(t, **entrant_kw)
    df = pd.concat([*_platoon(t), _veh("c", t, x, lane, v)], ignore_index=True)
    return df, t, x


def _one(ev: pd.DataFrame) -> pd.Series:
    assert len(ev) == 1, ev
    return ev.iloc[0]


# --- the planted reach is recovered ------------------------------------------------------------


class TestPlantedReach:
    @pytest.mark.parametrize(
        ("t_on", "dv"),
        # dv < 0: the entrant is slower than the target lane and falls back into its gap
        # (L overtakes it), the geometry of a slow ramp onto a fast mainline
        [(30.0, 3.0), (36.0, 2.5), (20.0, 4.0), (39.0, 5.0), (30.0, -3.0), (25.0, -6.0)],
    )
    def test_gap_and_speed_reach(self, t_on: float, dv: float) -> None:
        df, t, x = _base(t_on=t_on, dv=dv)
        e = _one(_measure(df))
        x_c = _at(t, x, T_CHANGE)
        planted_gap = x_c - _at(t, x, _first_beside(t, x))
        planted_speed = x_c - _at(t, x, t_on)
        one_sample = (V + abs(dv)) * DT
        assert e["gap_outcome"] == "onset"
        assert not e["gap_censored"]
        assert abs(e["gap_reach_m"] - planted_gap) <= one_sample
        # exact on this grid: the run's first sample is the onset
        assert e["gap_reach_m"] == pytest.approx(planted_gap, abs=1e-6)
        assert e["gap_time_s"] == pytest.approx(T_CHANGE - _first_beside(t, x), abs=1e-6)
        assert e["speed_1_outcome"] == "onset"
        assert e["speed_1_reach_m"] == pytest.approx(planted_speed, abs=1e-6)
        # |dv| > 2 m/s: the ±2 m/s band starts where the ±1 does
        assert e["speed_2_reach_m"] == pytest.approx(planted_speed, abs=1e-6)
        assert not e["changer_stitched"]

    def test_onset_diagnostics(self) -> None:
        df, t, x = _base()
        e = _one(_measure(df))
        t_b = _first_beside(t, x)
        # at onset the entrant's front is just past F's: F is 0-(V+dv)·dt behind it, L 60 m ahead of F
        assert 0.0 < e["gap_onset_lag_dist_m"] <= (V + 3.0) * DT + 1e-9
        assert e["gap_onset_lead_dv_ms"] == pytest.approx(-3.0)
        assert e["gap_onset_lag_dv_ms"] == pytest.approx(3.0)
        assert e["gap_onset_v_ms"] == pytest.approx(V + 3.0)
        assert e["gap_onset_x_rel_m"] == pytest.approx(_at(t, x, t_b) - WIDE.x_lo_m)
        assert e["d_change_m"] == pytest.approx(_at(t, x, T_CHANGE) - WIDE.x_lo_m)
        assert e["speed_class"] == "v>=20"

    def test_speed_not_matched_before_the_change_reads_zero(self) -> None:
        # the entrant is 3 m/s faster than L up to the change: no speed match, gap reach > 0
        df, _, _ = _base(t_on=T_CHANGE)
        e = _one(_measure(df))
        assert e["speed_1_outcome"] == "onset"
        assert e["speed_1_reach_m"] == 0.0
        assert e["gap_reach_m"] > 100.0

    def test_a_short_excursion_does_not_end_a_run_a_long_one_does(self) -> None:
        for n_spike, ends in ((2, False), (6, True)):
            t = _grid()
            x, v, lane = _entrant(t)
            spike = (t >= 35.0 - 1e-9) & (t < 35.0 + n_spike * DT - 1e-9)
            v = np.where(spike, V + 3.0, v)
            df = pd.concat([*_platoon(t), _veh("c", t, x, lane, v)], ignore_index=True)
            e = _one(_measure(df))
            x_c = _at(t, x, T_CHANGE)
            if ends:
                last = 35.0 + n_spike * DT
                assert e["speed_1_reach_m"] == pytest.approx(x_c - _at(t, x, last))
            else:
                assert e["speed_1_reach_m"] == pytest.approx(x_c - _at(t, x, 30.0))
            assert e["gap_reach_m"] == pytest.approx(x_c - _at(t, x, _first_beside(t, x)))

    def test_many_events_recover_their_median(self) -> None:
        # twelve worlds 20 km apart, each with its own platoon and planted reach
        t = _grid()
        frames, planted = [], []
        for i, t_on in enumerate(np.linspace(14.0, 38.0, 12)):
            x0 = XF0 + 20_000.0 * i
            x, v, lane = _entrant(t, t_on=float(t_on), x0=x0)
            frames += [f.assign(veh_id=f["veh_id"] + f"_{i}") for f in _platoon(t, x0=x0)] + [
                _veh(f"c_{i}", t, x, lane, v)
            ]
            planted.append(_at(t, x, T_CHANGE) - _at(t, x, _first_beside(t, x, x0)))
        ev = _measure(pd.concat(frames, ignore_index=True))
        assert len(ev) == 12
        assert (ev["gap_outcome"] == "onset").all()
        got = np.sort(ev["gap_reach_m"].to_numpy())
        np.testing.assert_allclose(got, np.sort(planted), atol=1e-6)
        med = km_quantiles(got, np.zeros(12, dtype=bool), (0.5,))["p50"]
        assert med == pytest.approx(np.sort(planted)[5])


# --- fragments, partners and the walk's bounds -------------------------------------------------


class TestFragmentsAndCensoring:
    def test_changer_fragment_switch_is_followed(self) -> None:
        df, t, x = _base()
        early = (df["veh_id"] == "c") & (df["t"] < 25.0 - 1e-9)
        df.loc[early, "veh_id"] = "c_early"
        e = _one(_measure(df))
        assert e["changer_stitched"]
        assert e["gap_outcome"] == "onset"
        assert e["gap_reach_m"] == pytest.approx(
            _at(t, x, T_CHANGE) - _at(t, x, _first_beside(t, x))
        )

    def test_a_short_gap_in_the_changer_track_is_bridged(self) -> None:
        df, t, x = _base()
        hole = (df["veh_id"] == "c") & (df["t"] > 24.5) & (df["t"] < 25.1)
        df = df[~hole].reset_index(drop=True)
        e = _one(_measure(df))
        assert e["gap_outcome"] == "onset"
        assert e["gap_reach_m"] == pytest.approx(
            _at(t, x, T_CHANGE) - _at(t, x, _first_beside(t, x))
        )
        assert e["gap_unknown_s"] == pytest.approx(0.6)
        # without bridging the walk stops at the hole, censored
        e0 = _one(_measure(df, max_bridge_s=0.0))
        assert e0["gap_outcome"] == "changer_track_start"
        assert e0["gap_censored"]
        assert e0["gap_reach_m"] == pytest.approx(_at(t, x, T_CHANGE) - _at(t, x, 25.2))

    def test_changer_track_start_censors(self) -> None:
        df, t, x = _base()
        df = df[~((df["veh_id"] == "c") & (df["t"] < 25.0 - 1e-9))].reset_index(drop=True)
        e = _one(_measure(df))
        assert e["gap_outcome"] == "changer_track_start"
        assert e["gap_reach_m"] == pytest.approx(_at(t, x, T_CHANGE) - _at(t, x, 25.0))
        # the speed onset (t_on = 30) lies inside the track: observed
        assert e["speed_1_outcome"] == "onset"

    def test_partner_track_start_censors(self) -> None:
        df, t, x = _base()
        df = df[~((df["veh_id"] == "m-1") & (df["t"] < 28.0 - 1e-9))].reset_index(drop=True)
        e = _one(_measure(df))
        assert e["gap_outcome"] == "partner_track_start"
        assert e["gap_reach_m"] == pytest.approx(_at(t, x, T_CHANGE) - _at(t, x, 28.0))
        assert e["speed_1_outcome"] == "onset"
        df2, _, _ = _base()
        df2 = df2[~((df2["veh_id"] == "m-1") & (df2["t"] < 33.0 - 1e-9))].reset_index(drop=True)
        assert _one(_measure(df2))["speed_1_outcome"] == "partner_track_start"

    def test_an_intruder_whose_track_ends_censors(self) -> None:
        df, t, x = _base()
        tq = t[t < 32.0 - 1e-9]
        q = _veh("q", tq, _xf(tq) + 48.0, 4)
        e = _one(_measure(pd.concat([df, q], ignore_index=True)))
        assert e["gap_outcome"] == "intruder_track_end"
        assert e["gap_reach_m"] == pytest.approx(_at(t, x, T_CHANGE) - _at(t, x, 32.0))

    def test_an_intruder_that_leaves_the_lane_ends_the_run(self) -> None:
        df, t, x = _base()
        q = _veh("q", t, _xf(t) + 48.0, np.where(t < 32.0 - 1e-9, 4, 3))
        e = _one(_measure(pd.concat([df, q], ignore_index=True)))
        assert e["gap_outcome"] == "onset"
        assert e["gap_reach_m"] == pytest.approx(_at(t, x, T_CHANGE) - _at(t, x, 32.0))

    def test_duplicate_fragments_do_not_break_the_run(self) -> None:
        df, t, x = _base()
        td = t[(t >= 33.0 - 1e-9) & (t <= 36.0 + 1e-9)]
        l_dup = _veh("l_dup", td, _xf(td) + S - 0.3, 4)
        tc = t[(t >= 34.0 - 1e-9) & (t <= 34.4 + 1e-9)]
        xc = x[(t >= 34.0 - 1e-9) & (t <= 34.4 + 1e-9)]
        c_dup = _veh("c_dup", tc, xc + 0.2, 4)
        e = _one(_measure(pd.concat([df, l_dup, c_dup], ignore_index=True)))
        assert e["gap_outcome"] == "onset"
        assert e["gap_reach_m"] == pytest.approx(
            _at(t, x, T_CHANGE) - _at(t, x, _first_beside(t, x))
        )
        # the entrant's own duplicate: three unknown instants, not a break
        assert e["gap_unknown_s"] == pytest.approx(0.6)

    def test_lookback_cap_and_window_start(self) -> None:
        df, t, x = _base()
        e = _one(_measure(df, lookback_s=10.0))
        assert e["gap_outcome"] == "lookback_cap"
        assert e["gap_reach_m"] == pytest.approx(_at(t, x, T_CHANGE) - _at(t, x, 30.0))
        late = df[df["t"] >= 25.0 - 1e-9].reset_index(drop=True)
        e2 = _one(_measure(late))
        assert e2["gap_outcome"] == "window_start"
        assert e2["gap_reach_m"] == pytest.approx(_at(t, x, T_CHANGE) - _at(t, x, 25.0))

    def test_a_changer_from_the_mainline_is_censored_where_it_was_there(self) -> None:
        t = _grid()
        x, v, _ = _entrant(t)
        lane = np.where(t < 22.0 - 1e-9, 4, np.where(t < T_CHANGE - 1e-9, 5, 4))
        df = pd.concat([*_platoon(t), _veh("c", t, x, lane, v)], ignore_index=True)
        e = _one(_measure(df))
        assert e["gap_outcome"] == "changer_lane"
        assert e["gap_reach_m"] == pytest.approx(_at(t, x, T_CHANGE) - _at(t, x, 22.0))


class TestRampStartAndNoChange:
    def test_change_soon_after_the_track_appears_at_the_gore(self) -> None:
        # ramp-lane fragments appear at the gore (docs/I24_DATA.md §6): the entrant is first
        # seen at the zone start, already beside its gap, and changes 1.6 s later
        t = _grid()
        x, v, lane = _entrant(t, t_on=10.0, t_change=30.0)
        zone_lo = _at(t, x, 28.4)
        zone = Zone("w", "weave", zone_lo, zone_lo + 600.0)
        keep = t >= 28.4 - 1e-9
        df = pd.concat(
            [*_platoon(t), _veh("c", t[keep], x[keep], lane[keep], v[keep])], ignore_index=True
        )
        e = _one(_measure(df, [zone]))
        assert e["gap_outcome"] == "changer_track_start"
        assert e["gap_censored"]
        assert e["d_change_m"] == pytest.approx(_at(t, x, 30.0) - zone_lo)
        assert e["gap_reach_m"] == pytest.approx(_at(t, x, 30.0) - zone_lo)
        assert e["gap_onset_x_rel_m"] == pytest.approx(0.0)

    def test_the_walk_continues_up_a_tracked_ramp_to_its_limit(self) -> None:
        # the ramp lane (band 6) upstream of the gore is tracked: the onset lies upstream
        t = _grid()
        x, v, lane = _entrant(t)
        zone_lo = _at(t, x, 34.0)
        lane = np.where(x < zone_lo, 6, lane)
        zone = Zone("w", "weave", zone_lo, zone_lo + 600.0)
        df = pd.concat([*_platoon(t), _veh("c", t, x, lane, v)], ignore_index=True)
        e = _one(_measure(df, [zone]))
        assert e["gap_outcome"] == "onset"
        assert e["gap_onset_x_rel_m"] < -200.0
        assert e["gap_reach_m"] == pytest.approx(
            _at(t, x, T_CHANGE) - _at(t, x, _first_beside(t, x))
        )
        e2 = _one(_measure(df, [zone], upstream_limit_m=50.0))
        assert e2["gap_outcome"] == "upstream_limit"
        first_in = float(t[np.flatnonzero(x >= zone_lo - 50.0)[0]])
        assert e2["gap_reach_m"] == pytest.approx(_at(t, x, T_CHANGE) - _at(t, x, first_in))

    def test_no_lane_change_no_event(self) -> None:
        df, t, _ = _base()
        stay = _veh("s", t, _xf(t) + 150.0, 5)
        ev = _measure(pd.concat([df, stay], ignore_index=True))
        assert list(ev["veh_id"]) == ["c"]

    def test_only_a_stayer_gives_an_empty_table_with_every_column(self) -> None:
        t = _grid()
        df = pd.concat([*_platoon(t), _veh("s", t, _xf(t) + 150.0, 5)], ignore_index=True)
        recs = lane_change_gaps(df, [WIDE], mainline_lanes=MAIN, aux_lanes=AUX, dt_s=DT).records
        out = anticipation_events(df, recs, [WIDE], mainline_lanes=MAIN, dt_s=DT)
        assert out.events.empty
        assert list(out.events.columns) == list(_empty_events(definitions()).columns)
        assert out.counts["n_events"] == 0

    def test_no_follower_leaves_the_gap_definition_out(self) -> None:
        t = _grid()
        x, v, lane = _entrant(t)
        df = pd.concat(
            [
                *_platoon(t, skip=("m0", "m1", "m2", "m3", "m4")),
                _veh("c", t, x, lane, v),
            ],
            ignore_index=True,
        )
        e = _one(_measure(df))
        assert e["gap_outcome"] == "no_partner"
        assert math.isnan(e["gap_reach_m"])
        assert e["speed_1_outcome"] == "onset"

    def test_counts_and_outcome_vocabulary(self) -> None:
        df, _, _ = _base()
        recs = lane_change_gaps(df, [WIDE], mainline_lanes=MAIN, aux_lanes=AUX, dt_s=DT).records
        out = anticipation_events(df, recs, [WIDE], mainline_lanes=MAIN, dt_s=DT)
        assert out.counts["n_events"] == 1
        for d in definitions():
            assert sum(out.counts[f"outcome_{d}_{o}"] for o in OUTCOMES) == 1
            assert set(out.events[f"{d}_outcome"]) <= set(OUTCOMES)
        assert set(CENSOR_REASONS) < set(OUTCOMES)
        assert out.parameters["n_break_steps"] == 5
        assert out.parameters["n_bridge_steps"] == 5

    def test_step_must_be_a_multiple_of_dt(self) -> None:
        df, _, _ = _base()
        recs = lane_change_gaps(df, [WIDE], mainline_lanes=MAIN, aux_lanes=AUX, dt_s=DT).records
        with pytest.raises(ValueError, match="whole multiple"):
            anticipation_events(df, recs, [WIDE], mainline_lanes=MAIN, dt_s=DT, step_s=0.3)

    def test_names(self) -> None:
        assert definitions((1.0, 2.0)) == ("gap", "speed_1", "speed_2")
        assert speed_definition_name(0.5) == "speed_0p5"


# --- Kaplan-Meier, the bootstrap, the summaries and the rule ------------------------------------


class TestKaplanMeier:
    def test_no_censoring_is_the_empirical_distribution(self) -> None:
        d = np.arange(1.0, 11.0)
        q = km_quantiles(d, np.zeros(10, dtype=bool))
        assert q == {"p10": 1.0, "p25": 3.0, "p50": 5.0, "p75": 8.0, "p90": 9.0}

    def test_textbook_example(self) -> None:
        # Freireich et al. (1963) 6-MP arm, as tabulated by Kleinbaum & Klein (Survival
        # Analysis, 3rd ed., ch. 2): S(22) = 0.538, S(23) = 0.448, median 23 weeks
        times = [6, 6, 6, 6, 7, 9, 10, 10, 11, 13, 16, 17, 19, 20, 22, 23, 25, 32, 32, 34, 35]
        cens = [0, 0, 0, 1, 0, 1, 0, 1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 1, 1, 1, 1]
        tt, s, n_risk = km_curve(times, np.asarray(cens, dtype=bool))
        assert list(tt) == [6, 7, 10, 13, 16, 22, 23]
        assert list(n_risk) == [21, 17, 15, 12, 11, 7, 6]
        assert s[tt == 22][0] == pytest.approx(0.538, abs=1e-3)
        assert s[tt == 23][0] == pytest.approx(0.448, abs=1e-3)
        assert km_quantile(tt, s, 0.5) == 23.0
        assert km_quantile(tt, s, 0.75) is None  # S never falls to 0.25

    def test_all_censored_is_not_identified(self) -> None:
        assert km_quantiles([5.0, 6.0], [True, True])["p50"] is None
        boot = km_bootstrap([5.0, 6.0], [True, True], (0.5,), n_boot=20, seed=1)
        assert boot["p50"] == {"ci95": [None, None], "unidentified_share": 1.0}

    def test_recovers_a_planted_median_under_censoring(self) -> None:
        rng = make_rng(7)
        n = 600
        true = rng.lognormal(math.log(150.0), 0.8, n)  # median 150 m
        cens_at = rng.uniform(0.0, 500.0, n)  # independent censoring (fragment starts)
        obs = np.minimum(true, cens_at)
        cen = cens_at < true
        assert 0.25 < cen.mean() < 0.6
        planted = float(np.median(true))  # this sample's own median (138 m at seed 7)
        med = km_quantiles(obs, cen, (0.5,))["p50"]
        assert med is not None and abs(med - planted) < 0.05 * planted
        naive = float(np.median(obs[~cen]))
        assert naive < 0.85 * planted  # the complete-case median is biased low
        boot = km_bootstrap(obs, cen, (0.5,), n_boot=300, seed=11)
        lo, hi = boot["p50"]["ci95"]
        assert lo is not None and hi is not None and lo < planted < hi
        assert boot == km_bootstrap(obs, cen, (0.5,), n_boot=300, seed=11)


def _events(
    reach: np.ndarray,
    censored: np.ndarray,
    *,
    zone: str = "HH_on_BR_off_weave",
    v: float = 24.0,
) -> pd.DataFrame:
    n = reach.size
    ev = _empty_events(definitions()).reindex(range(n))
    ev["zone"] = zone
    ev["v"] = v
    ev["speed_class"] = "v>=20" if v >= 20.0 else "10<=v<20"
    ev["d_change_m"] = 100.0
    for d in definitions():
        ev[f"{d}_outcome"] = np.where(censored, "changer_track_start", "onset")
        ev[f"{d}_censored"] = censored
        ev[f"{d}_reach_m"] = reach
        ev[f"{d}_time_s"] = reach / v
        ev[f"{d}_onset_x_rel_m"] = 100.0 - reach
    for col in (
        "gap_onset_lag_dist_m",
        "gap_onset_lead_gap_m",
        "gap_onset_lag_gap_m",
        "gap_onset_lead_dv_ms",
        "gap_onset_lag_dv_ms",
    ):
        ev[col] = 1.0
    return ev


class TestSummariesAndRule:
    RULE = AdoptionRule(
        strata=(
            Stratum("primary", ("HH_on_BR_off_weave",), "v>=20"),
            Stratum("fallback", ("HH_on_BR_off_weave", "OH_acceleration_lane"), "v>=20"),
        )
    )

    def test_stratum_summary_fields(self) -> None:
        rng = make_rng(3)
        reach = rng.lognormal(math.log(80.0), 0.5, 200)
        cen = rng.uniform(size=200) < 0.2
        s = stratum_summary(_events(reach, cen), "gap", n_boot=50, seed=5)
        assert s["n"] == 200 and s["n_onset"] + s["n_censored"] == 200
        assert s["km_reach_m"]["p50"] is not None
        assert set(s["km_reach_m_boot"]) == {"p10", "p25", "p50", "p75", "p90"}
        assert s["share_onset_upstream"] is not None
        assert s["outcomes"]["onset"] == int((~cen).sum())

    def test_summaries_by_zone_and_class(self) -> None:
        rng = make_rng(4)
        ev = pd.concat(
            [
                _events(rng.uniform(10, 200, 50), np.zeros(50, dtype=bool)),
                _events(rng.uniform(10, 200, 30), np.zeros(30, dtype=bool), zone="OH", v=12.0),
            ],
            ignore_index=True,
        )
        rows = summarize_anticipation(ev, n_boot=10, seed=1)
        # 2 zones x (all + 3 classes) x 3 definitions
        assert len(rows) == 2 * 4 * 3
        assert {r["definition"] for r in rows} == set(definitions())
        pos = change_positions(ev)
        assert {r["zone"] for r in pos} == {"HH_on_BR_off_weave", "OH"}
        assert rows == summarize_anticipation(ev, n_boot=10, seed=1)

    def test_rule_adopts_the_primary_median_rounded(self) -> None:
        rng = make_rng(5)
        reach = rng.lognormal(math.log(163.0), 0.4, 400)
        res = propose(_events(reach, np.zeros(400, dtype=bool)), self.RULE, n_boot=200, seed=2)
        assert res["selected"] == "primary"
        med = res["strata"][0]["median_m"]
        assert res["proposed_value_m"] == 10.0 * round(med / 10.0)
        assert res["current_value_m"] == 120.0

    def test_rule_falls_back_then_refuses(self) -> None:
        rng = make_rng(6)
        few = _events(rng.lognormal(math.log(90.0), 0.4, 40), np.zeros(40, dtype=bool))
        oh = _events(
            rng.lognormal(math.log(90.0), 0.4, 300),
            np.zeros(300, dtype=bool),
            zone="OH_acceleration_lane",
        )
        res = propose(pd.concat([few, oh], ignore_index=True), self.RULE, n_boot=200, seed=2)
        assert res["strata"][0]["checks"]["min_n"] is False
        assert res["selected"] == "fallback"
        # heavily censored early: the median is never reached in either stratum
        cen = _events(np.full(300, 50.0), np.ones(300, dtype=bool))
        res2 = propose(cen, self.RULE, n_boot=50, seed=2)
        assert res2["selected"] is None and res2["proposed_value_m"] is None
        assert res2["strata"][0]["checks"]["median_identified"] is False

    def test_rule_refuses_a_wide_interval(self) -> None:
        rng = make_rng(8)
        reach = rng.lognormal(math.log(100.0), 2.5, 110)  # very dispersed, n just above 100
        res = propose(_events(reach, rng.uniform(size=110) < 0.5), self.RULE, n_boot=200, seed=3)
        prim = res["strata"][0]
        assert prim["checks"]["min_n"]
        assert prim["checks"]["ci_width"] is False
        assert prim["passed"] is False
