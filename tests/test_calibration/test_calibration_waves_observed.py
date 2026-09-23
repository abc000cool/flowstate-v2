"""Observed backward wave speed from detector series (``calibration.waves_observed``).

Every fixture here is synthetic and seeded: a planted backward wave whose speed
is known by construction, a station pair with nothing in common, and a JSON
cache written into ``tmp_path`` so the loader helper is exercised without a
network (CLAUDE.md §9).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pytest

from calibration.loaders.mndot import (
    MPH_TO_MS,
    SAMPLE_INTERVAL_S,
    SAMPLES_PER_DAY,
    MetroConfig,
    station_speed_series,
)
from calibration.waves_observed import (
    MIN_PEAK_CORRELATION,
    MIN_PEAK_LAG_BINS,
    ObservedWaveSpeed,
    _barriers,
    _detrend,
    _lag_correlations,
    concatenate_dates,
    detector_wave_speed,
    leave_one_date_out,
    summary_line,
)
from flowstate_core.constants import WAVE_SPEED_BAND_KMH
from flowstate_core.units import kmh_to_ms

FIXTURE = Path(__file__).parent / "fixtures" / "mndot_metro_config_tiny.xml"

DT_S = SAMPLE_INTERVAL_S
FREE_FLOW_MS = 25.0
JAM_MS = 4.0
EVENT_BINS = 6  # three minutes below the jam threshold
EVENT_STARTS = (60, 160, 260, 360)
N_BINS = 480  # four hours of 30-second bins

#: The planted wave: 18 km/h backward over a 750 m spacing is exactly five
#: 30-second bins of lag, so the estimator's integer peak lands on the truth
#: and the test measures the method, not the grid.
PLANTED_KMH = 18.0
DX_M = 750.0
PLANTED_LAG_BINS = round(DX_M / kmh_to_ms(PLANTED_KMH) / DT_S)


def _wave_series(
    lag_bins: int,
    *,
    seed: int,
    noise_ms: float = 0.4,
    starts: tuple[int, ...] = EVENT_STARTS,
    n_bins: int = N_BINS,
    event_bins: int = EVENT_BINS,
) -> list[float | None]:
    """Free flow with the planted jams, delayed by ``lag_bins``, plus noise."""
    rng = np.random.default_rng(seed)
    values = np.full(n_bins, FREE_FLOW_MS) + rng.normal(0.0, noise_ms, n_bins)
    for start in starts:
        lo = start + lag_bins
        values[lo : lo + event_bins] = JAM_MS + rng.normal(0.0, noise_ms, event_bins)
    return [float(v) for v in values]


def _free_flow(*, seed: int, noise_ms: float = 0.4, n_bins: int = N_BINS) -> list[float | None]:
    """A morning that never congests."""
    rng = np.random.default_rng(seed)
    return [float(v) for v in np.full(n_bins, FREE_FLOW_MS) + rng.normal(0.0, noise_ms, n_bins)]


@pytest.fixture(scope="module")
def planted() -> ObservedWaveSpeed:
    """Two stations 750 m apart with an 18 km/h backward wave between them."""
    series: dict[str, Any] = {
        "UP": _wave_series(PLANTED_LAG_BINS, seed=1),
        "DOWN": _wave_series(0, seed=2),
    }
    return detector_wave_speed(series, {"UP": 0.0, "DOWN": DX_M}, dt_s=DT_S)


class TestPlantedWave:
    def test_speed_is_recovered_within_ten_percent(self, planted: ObservedWaveSpeed) -> None:
        assert planted.n_pairs == 1
        assert planted.n_used == 1
        pair = planted.pairs[0]
        assert pair.downstream == "DOWN" and pair.upstream == "UP"
        assert pair.used and pair.reason == ""
        assert pair.dx_m == pytest.approx(DX_M)
        assert pair.lag_bins == PLANTED_LAG_BINS
        assert pair.lag_s == pytest.approx(PLANTED_LAG_BINS * DT_S, abs=0.5 * DT_S)
        assert pair.speed_kmh == pytest.approx(PLANTED_KMH, rel=0.10)
        assert pair.correlation > MIN_PEAK_CORRELATION
        assert pair.n_events == len(EVENT_STARTS)

    def test_summary_is_the_median_of_the_used_pairs(self, planted: ObservedWaveSpeed) -> None:
        assert planted.median_kmh == pytest.approx(PLANTED_KMH, rel=0.10)
        q25, q75 = planted.iqr_kmh
        assert q25 == pytest.approx(planted.median_kmh) == pytest.approx(q75)
        assert planted.band_kmh == WAVE_SPEED_BAND_KMH
        assert planted.rejected == ()

    def test_the_line_states_the_band_it_is_compared_with(self, planted: ObservedWaveSpeed) -> None:
        line = summary_line(planted)
        assert "median 18" in line
        assert "from 1 of 1 station pairs" in line
        assert "the model's band is 14–22 km/h" in line

    def test_a_forward_ordered_pair_is_not_read_backwards(self) -> None:
        """Swapping the positions makes the wave arrive upstream *first*.

        The same two series with the positions exchanged describe a
        disturbance moving downstream; the peak lag is then negative and the
        pair is rejected rather than reported as a backward wave.
        """
        series: dict[str, Any] = {
            "UP": _wave_series(PLANTED_LAG_BINS, seed=1),
            "DOWN": _wave_series(0, seed=2),
        }
        result = detector_wave_speed(series, {"UP": DX_M, "DOWN": 0.0}, dt_s=DT_S)
        assert result.n_used == 0
        assert math.isnan(result.median_kmh)
        assert result.pairs[0].reason.startswith("peak lag is not positive")

    def test_artifact_round_trip_keeps_every_number(self, planted: ObservedWaveSpeed) -> None:
        payload = json.loads(json.dumps(planted.to_dict(), allow_nan=False))
        assert payload["median_kmh"] == pytest.approx(planted.median_kmh)
        assert payload["n_pairs"] == 1 and payload["n_used"] == 1
        assert payload["band_kmh"] == [14.0, 22.0]
        assert payload["dt_s"] == DT_S
        assert payload["pairs"][0]["used"] is True
        assert payload["rejected"] == {}


class TestRejection:
    def test_an_uncorrelated_pair_is_rejected_not_reported(self) -> None:
        rng = np.random.default_rng(7)
        series: dict[str, Any] = {
            "UP": [float(v) for v in rng.normal(FREE_FLOW_MS, 3.0, N_BINS)],
            "DOWN": _wave_series(0, seed=3),
        }
        result = detector_wave_speed(series, {"UP": 0.0, "DOWN": DX_M}, dt_s=DT_S)
        pair = result.pairs[0]
        assert not pair.used
        assert pair.reason == "peak correlation below the acceptance floor"
        assert pair.correlation < MIN_PEAK_CORRELATION
        assert result.n_used == 0
        assert math.isnan(result.median_kmh) and math.isnan(result.iqr_kmh[0])
        assert result.rejection_counts() == {pair.reason: 1}
        assert "not estimated from 1 station pair" in summary_line(result)

    def test_too_few_congested_episodes_is_a_rejection(self) -> None:
        """A corridor that never congests yields no wave speed, not a zero."""
        series: dict[str, Any] = {
            "UP": [FREE_FLOW_MS] * N_BINS,
            "DOWN": [FREE_FLOW_MS] * N_BINS,
        }
        result = detector_wave_speed(series, {"UP": 0.0, "DOWN": DX_M}, dt_s=DT_S)
        assert result.n_used == 0
        assert result.pairs[0].reason.startswith("fewer than min_events")
        assert result.pairs[0].n_events == 0

    def test_a_lag_on_the_search_bound_is_rejected(self) -> None:
        """A wave slower than the search range peaks at the bound; unusable."""
        series: dict[str, Any] = {
            "UP": _wave_series(10, seed=1),
            "DOWN": _wave_series(0, seed=2),
        }
        result = detector_wave_speed(
            series, {"UP": 0.0, "DOWN": DX_M}, dt_s=DT_S, max_lag_s=10 * DT_S
        )
        assert result.n_used == 0
        assert result.pairs[0].reason == "peak lag sits on the search bound"

    def test_a_station_without_a_position_takes_part_in_no_pair(self) -> None:
        series: dict[str, Any] = {
            "UP": _wave_series(PLANTED_LAG_BINS, seed=1),
            "MID": _wave_series(2, seed=4),
            "DOWN": _wave_series(0, seed=2),
        }
        result = detector_wave_speed(series, {"UP": 0.0, "DOWN": DX_M}, dt_s=DT_S)
        assert [(p.upstream, p.downstream) for p in result.pairs] == [("UP", "DOWN")]

    def test_a_shared_envelope_alone_is_not_a_wave(self) -> None:
        """Both stations congesting together must not read as a fast wave.

        Without the detrending step the shared morning-peak envelope
        correlates at zero lag (or at whatever lag the envelope's own shape
        prefers); with it, the pair carries no oscillation to time and is
        rejected. The corridor's slow common trend is not a moving jam.
        """
        rng = np.random.default_rng(11)
        envelope = np.full(N_BINS, FREE_FLOW_MS)
        envelope[120:300] = JAM_MS
        series: dict[str, Any] = {
            "UP": [float(v) for v in envelope + rng.normal(0.0, 0.4, N_BINS)],
            "DOWN": [float(v) for v in envelope + rng.normal(0.0, 0.4, N_BINS)],
        }
        result = detector_wave_speed(series, {"UP": 0.0, "DOWN": DX_M}, dt_s=DT_S, min_events=1)
        assert result.n_used == 0

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"dt_s": 0.0}, "dt_s"),
            ({"dt_s": 30.0, "max_lag_s": 10.0}, "max_lag_s"),
            ({"dt_s": 30.0, "min_events": 0}, "min_events"),
            ({"dt_s": 30.0, "detrend_s": -1.0}, "detrend_s"),
        ],
    )
    def test_bad_parameters_are_refused(self, kwargs: dict[str, float], match: str) -> None:
        with pytest.raises(ValueError, match=match):
            detector_wave_speed({"A": [1.0]}, {"A": 0.0}, **kwargs)  # type: ignore[arg-type]

    def test_series_of_differing_lengths_are_refused(self) -> None:
        with pytest.raises(ValueError, match="one clock"):
            detector_wave_speed({"A": [1.0, 2.0], "B": [1.0]}, {"A": 0.0, "B": 10.0}, dt_s=DT_S)


class TestStationSpeedSeries:
    """The loader helper that builds the 30-s series from the JSON cache."""

    @staticmethod
    def _cache(root: Path, date: str, detector: str, values: list[float | None]) -> None:
        path = root / date / f"{detector}.speed.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values))

    def test_lane_mean_zero_drop_and_date_concatenation(self, tmp_path: Path) -> None:
        config = MetroConfig.load(FIXTURE)
        station = config.corridor("I-94 WB").station("S2104")
        assert station.detectors == ("9063", "9064", "9065")
        lanes: dict[str, list[float | None]] = {
            # bin 0: 60 and 50 mph, one lane missing -> mean of the two present
            # bin 1: one lane reports 0 mph -> no measurement, mean of the rest
            # bin 2: nothing at all -> None
            "9063": [60.0, 40.0, None] + [55.0] * (SAMPLES_PER_DAY - 3),
            "9064": [50.0, 0.0, None] + [55.0] * (SAMPLES_PER_DAY - 3),
            "9065": [None, 50.0, None] + [55.0] * (SAMPLES_PER_DAY - 3),
        }
        for name, values in lanes.items():
            for date in ("20260901", "20260902"):
                self._cache(tmp_path, date, name, values)

        series = station_speed_series(
            config,
            "I-94 WB",
            ["S2104"],
            ["20260901", "20260902"],
            duration_s=3.0 * SAMPLE_INTERVAL_S,
            cache_dir=tmp_path,
            gap_s=2.0 * SAMPLE_INTERVAL_S,
        )
        got = series["S2104"]
        assert len(got) == 3 + 2 + 3  # day, gap, day
        assert got[0] == pytest.approx(55.0 * MPH_TO_MS)
        assert got[1] == pytest.approx(45.0 * MPH_TO_MS)
        assert got[2] is None
        assert got[3:5] == [None, None]  # the separator between the dates
        assert got[5] == pytest.approx(55.0 * MPH_TO_MS)

    def test_the_span_is_taken_from_the_local_clock(self, tmp_path: Path) -> None:
        config = MetroConfig.load(FIXTURE)
        values: list[float | None] = [float(i % 70) for i in range(SAMPLES_PER_DAY)]
        for name in ("9063", "9064", "9065"):
            self._cache(tmp_path, "20260901", name, values)
        series = station_speed_series(
            config,
            "I-94 WB",
            ["S2104"],
            ["20260901"],
            t0_s=3600.0,  # 01:00 local = bin 120
            duration_s=60.0,
            cache_dir=tmp_path,
        )
        assert series["S2104"] == [
            pytest.approx(float(120 % 70) * MPH_TO_MS),
            pytest.approx(float(121 % 70) * MPH_TO_MS),
        ]

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"dates": []}, "at least one date"),
            ({"t0_s": 17.0}, "t0_s"),
            ({"duration_s": 172800.0}, "one local day"),
        ],
    )
    def test_bad_spans_are_refused(
        self, tmp_path: Path, kwargs: dict[str, Any], match: str
    ) -> None:
        config = MetroConfig.load(FIXTURE)
        call: dict[str, Any] = {"dates": ["20260901"], "cache_dir": tmp_path}
        call.update(kwargs)
        dates = call.pop("dates")
        with pytest.raises(ValueError, match=match):
            station_speed_series(config, "I-94 WB", ["S2104"], dates, **call)


class TestResolutionFloor:
    """Lags too short for a 30-second grid to resolve are rejected."""

    def test_a_one_bin_lag_is_below_the_resolution_of_the_grid(self) -> None:
        """``dx / (0.5 · dt)`` is the grid's limit, not a wave speed.

        A one-bin peak carries a sub-bin offset the parabola clamps to ±½
        bin, so the implied speed spans a factor of three. The pair is
        rejected with its lag on the record rather than reported.
        """
        series: dict[str, Any] = {
            "UP": _wave_series(1, seed=1),
            "DOWN": _wave_series(0, seed=2),
        }
        result = detector_wave_speed(series, {"UP": 0.0, "DOWN": DX_M}, dt_s=DT_S)
        pair = result.pairs[0]
        assert pair.lag_bins == 1 < MIN_PEAK_LAG_BINS
        assert not pair.used and math.isnan(pair.speed_kmh)
        assert pair.reason.startswith("lag below resolution")
        # the correlation is excellent: it is the resolution, not the fit,
        # that makes the number unusable
        assert pair.correlation > MIN_PEAK_CORRELATION
        assert result.n_used == 0 and math.isnan(result.median_kmh)

    def test_the_floor_is_two_bins(self) -> None:
        series: dict[str, Any] = {
            "UP": _wave_series(MIN_PEAK_LAG_BINS, seed=1),
            "DOWN": _wave_series(0, seed=2),
        }
        result = detector_wave_speed(series, {"UP": 0.0, "DOWN": DX_M}, dt_s=DT_S)
        assert result.pairs[0].lag_bins == MIN_PEAK_LAG_BINS
        assert result.pairs[0].used


class TestRejectionOrder:
    def test_a_negative_peak_on_the_bound_is_labelled_not_backward(self) -> None:
        """What the data said comes before what the search window did.

        A peak at −max_lag is first of all a disturbance that reached the
        *upstream* station earlier — downstream propagation. Reporting it as
        "peak lag sits on the search bound" would blame the search window for
        a direction the data chose, and would invite widening the window.
        """
        series: dict[str, Any] = {
            "UP": _wave_series(0, seed=1),
            "DOWN": _wave_series(10, seed=2),
        }
        result = detector_wave_speed(
            series, {"UP": 0.0, "DOWN": DX_M}, dt_s=DT_S, max_lag_s=10 * DT_S
        )
        pair = result.pairs[0]
        assert pair.lag_bins == -10  # exactly on the bound, and negative
        assert pair.reason == "peak lag is not positive (no backward propagation)"


class TestEventCounting:
    def test_min_events_counts_runs_not_days(self) -> None:
        """Three episodes of one morning satisfy ``min_events=3``.

        The parameter is a floor on *congested episodes*, which is what the
        rejection reason says; it is no guarantee that several dates
        contributed. That question is answered by
        :func:`leave_one_date_out`, not by this counter.
        """
        starts = (60, 160, 260)
        series: dict[str, Any] = {
            "UP": _wave_series(PLANTED_LAG_BINS, seed=1, starts=starts),
            "DOWN": _wave_series(0, seed=2, starts=starts),
        }
        result = detector_wave_speed(
            series, {"UP": 0.0, "DOWN": DX_M}, dt_s=DT_S, min_events=len(starts)
        )
        pair = result.pairs[0]
        assert pair.n_events == len(starts)  # one day, three runs
        assert pair.used and pair.reason == ""
        # one run short of the floor is a rejection, whatever the day count
        strict = detector_wave_speed(
            series, {"UP": 0.0, "DOWN": DX_M}, dt_s=DT_S, min_events=len(starts) + 1
        )
        assert strict.pairs[0].reason.startswith("fewer than min_events")


class TestDetrendWindow:
    """A centred mean that is not centred leaves the trend in the residual."""

    def test_a_ramp_detrends_to_zero_only_where_the_window_is_two_sided(self) -> None:
        ramp = np.arange(50.0)
        residual = _detrend(ramp, 11, np.zeros(50, dtype=np.bool_))
        half = 11 // 2
        # the interior: the mean of a straight line is the line
        assert residual[half:-half] == pytest.approx(0.0, abs=1e-12)
        # the ends: the window runs off the data, so there is no residual —
        # a one-sided mean would have left the ramp's slope behind
        assert np.isnan(residual[:half]).all()
        assert np.isnan(residual[-half:]).all()

    def test_a_window_reaching_into_a_separator_has_no_residual_either(self) -> None:
        ramp = np.concatenate([np.arange(30.0), np.full(10, math.nan), np.arange(30.0)])
        barrier = _barriers(ramp, ramp, 10)
        assert barrier[30:40].all() and not barrier[:30].any()
        residual = _detrend(ramp, 11, barrier)
        half = 11 // 2
        # the last samples of the first date are as one-sided as the first
        # samples of the whole series
        assert np.isnan(residual[30 - half : 30]).all()
        assert np.isnan(residual[40 : 40 + half]).all()
        assert residual[half : 30 - half] == pytest.approx(0.0, abs=1e-12)

    def test_the_step_is_the_identity_below_two_bins(self) -> None:
        values = np.array([1.0, 5.0, 2.0])
        assert _detrend(values, 1, np.zeros(3, dtype=np.bool_)) == pytest.approx(values)


class TestDateSeparator:
    """No correlation pair may span the NaN run between two dates."""

    #: A short synthetic day, so that one day plus the separator (230 bins)
    #: is inside a two-hour lag search (240 bins at 30 s).
    DAY_BINS = 200
    GAP_BINS = 30
    STARTS = (25, 47, 88, 109, 151, 177)

    def _two_days(self) -> dict[str, Any]:
        """Day 1 carries the only wave; day 2's upstream repeats day 1's downstream.

        The repeat is the worst case on purpose: at a lag of exactly one day
        plus the separator the two series line up perfectly, so an estimator
        that pairs across the separator prefers that alignment to anything
        inside a day. A real archive only needs a partial repeat to do the
        same — every weekday morning of a corridor looks like the last one.
        """
        kwargs: dict[str, Any] = {"n_bins": self.DAY_BINS, "event_bins": 4}
        day1_down = _wave_series(0, seed=2, starts=self.STARTS, **kwargs)
        day1_up = _free_flow(seed=7, n_bins=self.DAY_BINS)
        day2_down = _free_flow(seed=13, n_bins=self.DAY_BINS)
        gap: list[float | None] = [None] * self.GAP_BINS
        return {
            "UP": [*day1_up, *gap, *day1_down],
            "DOWN": [*day1_down, *gap, *day2_down],
        }

    def test_a_lag_longer_than_the_separator_pairs_two_mornings(self) -> None:
        """The defect, stated: ``max_lag_s`` alone does not stop it."""
        result = detector_wave_speed(
            self._two_days(), {"UP": 0.0, "DOWN": DX_M}, dt_s=DT_S, max_lag_s=7200.0
        )
        pair = result.pairs[0]
        assert pair.lag_bins == self.DAY_BINS + self.GAP_BINS  # one day later
        assert pair.correlation == pytest.approx(1.0)
        assert pair.used and pair.speed_kmh < 1.0  # 0.4 km/h is not a jam wave

    def test_the_separator_width_removes_those_pairs(self) -> None:
        result = detector_wave_speed(
            self._two_days(),
            {"UP": 0.0, "DOWN": DX_M},
            dt_s=DT_S,
            max_lag_s=7200.0,
            gap_s=self.GAP_BINS * DT_S,
        )
        pair = result.pairs[0]
        assert result.gap_s == self.GAP_BINS * DT_S
        assert pair.lag_bins != self.DAY_BINS + self.GAP_BINS
        assert pair.correlation < 1.0
        # what is left rests on a handful of samples at a long lag — the
        # method's own limit at a two-hour search window, and it is reported
        # with that count rather than hidden
        assert pair.n_samples < 0.25 * self.DAY_BINS

    def test_no_lag_crossing_a_barrier_keeps_a_single_pair(self) -> None:
        series = self._two_days()
        down = np.asarray(series["DOWN"], dtype=np.float64)
        up = np.asarray(series["UP"], dtype=np.float64)
        max_lag = 240
        barrier = _barriers(up, down, self.GAP_BINS)
        assert barrier.sum() == self.GAP_BINS
        analysed = np.ones(down.size, dtype=np.bool_)
        _, counts = _lag_correlations(down, up, analysed, max_lag, barrier)
        # a lag as long as a whole date cannot fit inside one: every pair at
        # such a lag would have to cross the separator, so none survives
        too_long = [lag for lag in range(-max_lag, max_lag + 1) if abs(lag) >= self.DAY_BINS]
        assert counts[[lag + max_lag for lag in too_long]].max() == 0
        # without the barrier they are the best-populated lags of all
        open_counts = _lag_correlations(down, up, analysed, max_lag, np.zeros_like(barrier))[1]
        assert open_counts[max_lag + self.DAY_BINS + self.GAP_BINS] > 100
        # and the short lags, which never cross, are unaffected by either
        assert counts[max_lag + 5] == open_counts[max_lag + 5] > 0

    def test_without_a_separator_width_there_are_no_barriers(self) -> None:
        values = np.full(10, math.nan)
        assert not _barriers(values, values, 0).any()


class TestLeaveOneDateOut:
    """How much the corridor median rests on any one date."""

    def _dates(self) -> dict[str, dict[str, Any]]:
        """Three mornings over three stations, each with the same planted wave."""
        return {
            f"2026090{i}": {
                "UP": _wave_series(2 * PLANTED_LAG_BINS, seed=10 * i + 1),
                "MID": _wave_series(PLANTED_LAG_BINS, seed=10 * i + 2),
                "DOWN": _wave_series(0, seed=10 * i + 3),
            }
            for i in (1, 2, 3)
        }

    #: UP–MID is 750 m (18 km/h at five bins), MID–DOWN is 500 m (12 km/h).
    POSITIONS: ClassVar[dict[str, float]] = {"UP": 0.0, "MID": DX_M, "DOWN": DX_M + 500.0}

    def test_the_range_of_medians_and_the_fewest_pairs_are_reported(self) -> None:
        by_date = self._dates()
        loo = leave_one_date_out(by_date, self.POSITIONS, dt_s=DT_S, gap_s=60.0 * DT_S)
        assert loo.dates == tuple(by_date)
        assert len(loo.medians_kmh) == len(loo.n_used) == 3
        # both pairs survive every subset here, so the range is narrow — and
        # it is the range that was computed, not an assumption
        assert loo.n_used == (2, 2, 2)
        assert loo.pairs_min == 2
        assert loo.median_min_kmh <= loo.median_max_kmh
        assert loo.median_min_kmh == pytest.approx(15.0, rel=0.10)
        assert loo.median_max_kmh == pytest.approx(15.0, rel=0.10)

    def test_an_estimate_resting_on_one_date_says_so(self) -> None:
        """Two free-flowing mornings and one congested one.

        Leaving the congested date out leaves the pair with no episode at
        all, so its median is NaN and the fewest-pairs count is zero — the
        honest reading of a headline that one morning produced.
        """
        by_date: dict[str, dict[str, Any]] = {
            "20260901": {"UP": _free_flow(seed=21), "DOWN": _free_flow(seed=22)},
            "20260902": {"UP": _free_flow(seed=23), "DOWN": _free_flow(seed=24)},
            "20260903": {
                "UP": _wave_series(PLANTED_LAG_BINS, seed=1),
                "DOWN": _wave_series(0, seed=2),
            },
        }
        loo = leave_one_date_out(by_date, {"UP": 0.0, "DOWN": DX_M}, dt_s=DT_S, gap_s=60.0 * DT_S)
        assert loo.n_used == (1, 1, 0)
        assert loo.pairs_min == 0
        assert math.isnan(loo.medians_kmh[2])
        assert loo.median_min_kmh == pytest.approx(PLANTED_KMH, rel=0.10)

    def test_the_artifact_carries_the_three_numbers_at_the_top_level(self) -> None:
        by_date = self._dates()
        gap_s = 60.0 * DT_S
        loo = leave_one_date_out(by_date, self.POSITIONS, dt_s=DT_S, gap_s=gap_s)
        result = detector_wave_speed(
            concatenate_dates(by_date, gap_bins=round(gap_s / DT_S)),
            self.POSITIONS,
            dt_s=DT_S,
            gap_s=gap_s,
            loo=loo,
        )
        payload = json.loads(json.dumps(result.to_dict(), allow_nan=False))
        assert payload["loo_median_min_kmh"] == pytest.approx(loo.median_min_kmh)
        assert payload["loo_median_max_kmh"] == pytest.approx(loo.median_max_kmh)
        assert payload["loo_pairs_min"] == loo.pairs_min
        assert payload["gap_s"] == gap_s
        block = payload["leave_one_date_out"]
        assert block["n_dates"] == 3
        assert [row["date"] for row in block["by_omitted_date"]] == list(by_date)
        assert "leave-one-date-out" in summary_line(result)

    def test_an_estimate_without_one_carries_no_loo_keys(self) -> None:
        payload = detector_wave_speed(
            {"UP": _wave_series(PLANTED_LAG_BINS, seed=1), "DOWN": _wave_series(0, seed=2)},
            {"UP": 0.0, "DOWN": DX_M},
            dt_s=DT_S,
        ).to_dict()
        assert "loo_median_min_kmh" not in payload
        assert "leave_one_date_out" not in payload

    def test_a_single_date_cannot_be_left_out(self) -> None:
        with pytest.raises(ValueError, match="at least two dates"):
            leave_one_date_out({"20260901": {"UP": _free_flow(seed=1)}}, {"UP": 0.0}, dt_s=DT_S)


class TestConcatenateDates:
    def test_the_dates_are_laid_end_to_end_with_the_separator(self) -> None:
        by_date: dict[str, dict[str, Any]] = {
            "d1": {"A": [1.0, 2.0], "B": [3.0, 4.0]},
            "d2": {"A": [5.0, 6.0], "B": [7.0, 8.0]},
        }
        joined = concatenate_dates(by_date, gap_bins=3)
        assert list(joined) == ["A", "B"]
        assert joined["A"] == [1.0, 2.0, None, None, None, 5.0, 6.0]
        assert joined["B"] == [3.0, 4.0, None, None, None, 7.0, 8.0]

    def test_a_station_a_date_never_reported_is_filled_not_dropped(self) -> None:
        by_date: dict[str, dict[str, Any]] = {
            "d1": {"A": [1.0, 2.0], "B": [3.0, 4.0]},
            "d2": {"A": [5.0, 6.0]},
        }
        joined = concatenate_dates(by_date, gap_bins=1)
        assert joined["B"] == [3.0, 4.0, None, None, None]
        assert len(joined["A"]) == len(joined["B"])  # one clock for every station

    def test_a_date_whose_stations_disagree_on_length_is_refused(self) -> None:
        with pytest.raises(ValueError, match="one clock"):
            concatenate_dates({"d1": {"A": [1.0], "B": [1.0, 2.0]}}, gap_bins=0)
        with pytest.raises(ValueError, match="gap_bins"):
            concatenate_dates({"d1": {"A": [1.0]}}, gap_bins=-1)
