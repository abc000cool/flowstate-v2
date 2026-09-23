"""MnDOT metadata parsing and 30-second aggregation (``calibration.loaders.mndot``).

The fixture is a handful of r_nodes copied from one corridor of the public
``metro_config.xml`` (I-94 WB, Co Rd 71 → Co Rd 19), including a station whose
detectors are all decommissioned and the Entrance/Exit nodes between them.
Nothing here touches the network: the archive fetcher takes an injected
session and every test passes one.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from calibration.loaders.mndot import (
    MAYFLY_BASE_URL,
    SAMPLES_PER_DAY,
    MetroConfig,
    aggregate_day,
    fetch_detector_day,
    mayfly_url,
    samples_per_window,
    station_frame,
    stations_table,
)
from calibration.loaders.pems import MPH_TO_MS

FIXTURE = Path(__file__).parent / "fixtures" / "mndot_metro_config_tiny.xml"


@pytest.fixture(scope="module")
def config() -> MetroConfig:
    return MetroConfig.load(FIXTURE)


class TestMetroConfig:
    def test_corridor_stations_and_chain_distances(self, config: MetroConfig) -> None:
        corridor = config.corridor("I-94 WB")
        assert corridor.route == "I-94" and corridor.direction == "WB"
        ids = [s.id for s in corridor.stations]
        assert ids == ["S2104", "S1361", "S2105", "S2106", "S1058", "S2107", "S1059", "S2108"]
        # x_m is cumulative along the node chain: 0 at the first node, strictly
        # increasing, and of the order of the real spacing (~0.8 km apart).
        xs = [s.x_m for s in corridor.stations]
        assert xs[0] == 0.0
        assert all(b > a for a, b in itertools.pairwise(xs))
        assert 700.0 < xs[1] < 900.0

    def test_mainline_detectors_exclude_abandoned_ones(self, config: MetroConfig) -> None:
        corridor = config.corridor("I-94 WB")
        assert corridor.station("S2104").detectors == ("9063", "9064", "9065")
        # Every detector of S1058 carries abandoned='t' in the configuration.
        assert corridor.station("S1058").detectors == ()

    def test_station_metadata_is_si(self, config: MetroConfig) -> None:
        station = config.corridor("I-94 WB").station("S2105")
        assert station.lanes == 3
        assert station.label == "Manning Ave"
        assert station.speed_limit_ms == pytest.approx(70.0 * MPH_TO_MS)

    def test_ramp_nodes_carry_their_detectors_by_category(self, config: MetroConfig) -> None:
        corridor = config.corridor("I-94 WB")
        by_node = {r.node: r for r in corridor.ramps}
        entrance = by_node["rnd_88855"]
        assert entrance.kind == "on_ramp"
        assert entrance.detectors["Merge"] == ("5104",)
        assert entrance.detectors["Queue"] == ("7755", "7756")
        assert entrance.flow_detectors == ("5104",)
        exit_node = by_node["rnd_88851"]
        assert exit_node.kind == "off_ramp"
        assert exit_node.flow_detectors == ("5100",)

    def test_station_span_and_ramps_between(self, config: MetroConfig) -> None:
        corridor = config.corridor("I-94 WB")
        span = corridor.station_span("S1361", "S2107")
        assert [s.id for s in span] == ["S1361", "S2105", "S2106", "S1058", "S2107"]
        ramps = corridor.ramps_between(span[0].x_m, span[-1].x_m)
        assert [r.node for r in ramps] == ["rnd_88851", "rnd_88855"]
        with pytest.raises(ValueError, match="precedes"):
            corridor.station_span("S2107", "S1361")
        with pytest.raises(KeyError, match="S9999"):
            corridor.station_span("S9999", "S2107")

    def test_unknown_corridor_lists_near_matches(self, config: MetroConfig) -> None:
        with pytest.raises(KeyError, match="I-94 WB"):
            config.corridor("I-94 EB")

    def test_stations_table_shape(self, config: MetroConfig) -> None:
        corridor = config.corridor("I-94 WB")
        table = stations_table(corridor.stations[:2], corridor.ramps[:1])
        assert list(table.columns) == [
            "station",
            "label",
            "lat",
            "lon",
            "x_m",
            "lanes",
            "kind",
            "speed_limit_ms",
            "detectors",
        ]
        assert table["station"].tolist() == ["S2104", "S1361", "rnd_88851"]
        assert table["kind"].tolist() == ["mainline", "mainline", "off_ramp"]
        assert table["detectors"][0] == "9063|9064|9065"


class TestAggregation:
    def test_samples_per_window(self) -> None:
        assert samples_per_window(300.0) == 10
        with pytest.raises(ValueError, match="multiple"):
            samples_per_window(70.0)

    def test_validity_threshold_and_two_stage_scaling(self) -> None:
        # Two detectors, two 5-minute windows (10 samples each).
        # Window 0: A complete (10 x 2 veh), B only 5 of 10 -> 15/20 = 75%
        #           present, below the 80% rule -> the window is not reported.
        # Window 1: A complete (10 x 2 veh), B 8 of 10 (x 4 veh) -> 18/20 = 90%
        #           -> valid. A contributes 20 veh, B 32 x 10/8 = 40 veh,
        #           total 60 veh in 300 s = 720 veh/h.
        counts = {
            "A": [2.0] * 10 + [2.0] * 10,
            "B": [3.0] * 5 + [None] * 5 + [4.0] * 8 + [None] * 2,
        }
        occupancy = {"A": [10.0] * 20, "B": [None] * 10 + [20.0] * 8 + [None] * 2}
        speeds = {"A": [60.0] * 20, "B": [None] * 20}
        day = aggregate_day(counts, occupancy, speeds, window_s=300.0)
        assert len(day) == 2
        assert day["fraction_valid"].tolist() == [0.75, 0.9]
        assert day["flow_veh_h"].isna()[0]
        assert day["occupancy_pct"].isna()[0]
        assert day["flow_veh_h"][1] == pytest.approx(720.0)
        assert day["speed_ms"][1] == pytest.approx(60.0 * MPH_TO_MS)
        # Occupancy is the mean over every present sample of every detector:
        # (10 x 10 + 8 x 20) / 18.
        assert day["occupancy_pct"][1] == pytest.approx((10 * 10.0 + 8 * 20.0) / 18.0)

    def test_a_silent_lane_is_scaled_up_by_the_station_stage(self) -> None:
        # Five lanes, one silent: 40/50 = 80% present, exactly at the rule.
        # Four lanes carry 10 veh each in the window; the station total is
        # scaled by 5/4 -> 50 veh in 300 s = 600 veh/h.
        counts = {name: [1.0] * 10 for name in "ABCD"}
        counts["E"] = [None] * 10
        day = aggregate_day(counts, {}, {}, window_s=300.0)
        assert day["fraction_valid"][0] == pytest.approx(0.8)
        assert day["flow_veh_h"][0] == pytest.approx(600.0)

    def test_zero_speed_is_not_a_measurement(self) -> None:
        counts = {"A": [1.0] * 10}
        speeds = {"A": [0.0] * 5 + [50.0] * 5}
        day = aggregate_day(counts, {}, speeds, window_s=300.0)
        assert day["speed_ms"][0] == pytest.approx(50.0 * MPH_TO_MS)
        blank = aggregate_day(counts, {}, {"A": [0.0] * 10}, window_s=300.0)
        assert blank["speed_ms"].isna()[0]

    def test_missing_detectors_count_against_the_rule(self) -> None:
        # Three lanes expected, one series delivered: 10/30 samples present.
        day = aggregate_day({"A": [1.0] * 10}, {}, {}, window_s=300.0, n_detectors=3)
        assert day["fraction_valid"][0] == pytest.approx(1 / 3)
        assert day["flow_veh_h"].isna()[0]

    def test_a_station_with_no_series_still_lays_out_the_day(self) -> None:
        day = aggregate_day({}, {}, {}, window_s=300.0, n_detectors=3)
        assert len(day) == SAMPLES_PER_DAY // 10
        assert day["flow_veh_h"].isna().all()


class TestFetching:
    def test_url_construction(self) -> None:
        url = mayfly_url("9063", "20260915", "counts")
        assert url.startswith(f"{MAYFLY_BASE_URL}/counts?")
        assert "district=metro" in url and "year=2026" in url
        assert "date=20260915" in url and "detector=9063" in url
        with pytest.raises(ValueError, match="endpoint"):
            mayfly_url("9063", "20260915", "volume")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="YYYYMMDD"):
            mayfly_url("9063", "2026-09-15", "counts")

    def test_cache_is_written_and_reused(self, tmp_path: Path) -> None:
        calls: list[str] = []

        def session(url: str) -> bytes:
            calls.append(url)
            return json.dumps([1, None, 2]).encode()

        first = fetch_detector_day(
            "9063", "20260915", "counts", cache_dir=tmp_path, session=session
        )
        assert first == [1.0, None, 2.0]
        cached = tmp_path / "20260915" / "9063.counts.json"
        assert json.loads(cached.read_text()) == [1, None, 2]
        again = fetch_detector_day(
            "9063", "20260915", "counts", cache_dir=tmp_path, session=session
        )
        assert again == first
        assert len(calls) == 1  # the second call was answered from the cache

    def test_absent_detector_day_is_an_empty_series_and_is_cached(self, tmp_path: Path) -> None:
        calls: list[str] = []

        def session(url: str) -> None:
            calls.append(url)
            return None  # the fetcher's spelling of HTTP 404

        assert (
            fetch_detector_day("X", "20260915", "speed", cache_dir=tmp_path, session=session) == []
        )
        assert (
            fetch_detector_day("X", "20260915", "speed", cache_dir=tmp_path, session=session) == []
        )
        assert len(calls) == 1

    def test_non_list_payload_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="expected a JSON list"):
            fetch_detector_day(
                "9063",
                "20260915",
                "counts",
                cache_dir=tmp_path,
                session=lambda url: b'{"error": "nope"}',
            )


class TestStationFrame:
    def test_frame_shape_units_and_ramp_rows(self, config: MetroConfig, tmp_path: Path) -> None:
        def session(url: str) -> bytes:
            if "/counts?" in url:
                return json.dumps([2] * SAMPLES_PER_DAY).encode()
            if "/speed?" in url:
                return json.dumps([60] * SAMPLES_PER_DAY).encode()
            return json.dumps([10.0] * SAMPLES_PER_DAY).encode()

        frame = station_frame(
            config,
            "I-94 WB",
            ["S2104", "S1361", "S2105", "S2106"],
            ["20260915"],
            window_s=300.0,
            cache_dir=tmp_path,
            session=session,
            max_workers=2,
        )
        assert frame.attrs["interval_s"] == 300.0
        stations = set(frame["station"])
        # Four stations plus the one off-ramp node inside their span.
        assert stations == {"S2104", "S1361", "S2105", "S2106", "rnd_88851"}
        assert len(frame) == 5 * (86400 // 300)
        mainline = frame[frame["station"] == "S2104"]
        # Three lane detectors x 10 samples x 2 veh = 60 veh per 5 min.
        assert mainline["flow_veh_h"].iloc[0] == pytest.approx(720.0)
        assert mainline["speed_ms"].iloc[0] == pytest.approx(60.0 * MPH_TO_MS)
        assert mainline["occupancy_pct"].iloc[0] == pytest.approx(10.0)
        assert mainline["lanes"].iloc[0] == 3
        assert mainline["x_m"].iloc[0] == 0.0
        ramp = frame[frame["station"] == "rnd_88851"]
        assert (ramp["kind"] == "off_ramp").all()
        # One detector x 10 samples x 2 veh = 20 veh per 5 min.
        assert ramp["flow_veh_h"].iloc[0] == pytest.approx(240.0)
        # Timestamps run from local midnight on a 5-minute grid.
        first = mainline["timestamp"].iloc[0]
        assert (first.hour, first.minute) == (0, 0)

    def test_a_station_without_live_detectors_is_all_nan(
        self, config: MetroConfig, tmp_path: Path
    ) -> None:
        frame = station_frame(
            config,
            "I-94 WB",
            ["S1058"],
            ["20260915"],
            cache_dir=tmp_path,
            session=lambda url: json.dumps([2] * SAMPLES_PER_DAY).encode(),
            include_ramps=False,
        )
        assert set(frame["station"]) == {"S1058"}
        assert frame["flow_veh_h"].isna().all()

    def test_unknown_station_is_refused(self, config: MetroConfig, tmp_path: Path) -> None:
        with pytest.raises(KeyError, match="S9999"):
            station_frame(
                config,
                "I-94 WB",
                ["S9999"],
                ["20260915"],
                cache_dir=tmp_path,
                session=lambda url: b"[]",
            )
