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
from typing import Any

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
    ObservedWaveSpeed,
    detector_wave_speed,
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


def _wave_series(lag_bins: int, *, seed: int, noise_ms: float = 0.4) -> list[float | None]:
    """Free flow with the planted jams, delayed by ``lag_bins``, plus noise."""
    rng = np.random.default_rng(seed)
    values = np.full(N_BINS, FREE_FLOW_MS) + rng.normal(0.0, noise_ms, N_BINS)
    for start in EVENT_STARTS:
        lo = start + lag_bins
        values[lo : lo + EVENT_BINS] = JAM_MS + rng.normal(0.0, noise_ms, EVENT_BINS)
    return [float(v) for v in values]


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
