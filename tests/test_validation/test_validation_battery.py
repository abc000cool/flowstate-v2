"""Unit tests for ``validation.battery.insertion_stats`` and its aggregate.

The insertion counters are the cheapest evidence that a run simulated the
demand it was configured with; these tests pin the three verdicts the
corridor battery and the report print — ok, backlog, starved ramps — and the
conventions around them (off-ramps ignored, small ramps not judged, missing
counters refused rather than reported as zero).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from validation.battery import (
    HEALTHY_DEPARTED_FRACTION,
    NO_PLAN_VERDICT,
    OK_VERDICT,
    SCORE_MEMORY_FRACTION,
    SCORE_WORKER_BASE_BYTES,
    SCORE_WORKER_BYTES_PER_ROW,
    STARVED_RAMP_MIN_PLANNED,
    InsertionStats,
    aggregate_insertion,
    available_memory_bytes,
    insertion_stats,
    records_insertion,
    score_pool_size,
    score_worker_bytes,
    trajectory_rows,
)

PLANNED = 1000


def _meta(
    planned: int = PLANNED,
    departed: int = PLANNED,
    arrived: int | None = 900,
    ramps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A meta.json fragment carrying only what insertion_stats reads."""
    meta: dict[str, Any] = {
        "n_vehicles_planned": planned,
        "n_vehicles_departed": departed,
    }
    if arrived is not None:
        meta["n_vehicles_arrived"] = arrived
    if ramps is not None:
        meta["ramps"] = ramps
    return meta


def _ramp(
    name: str, kind: str, planned: int, departed: int, index: int | None = None
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "n_planned": planned,
        "n_departed": departed,
    }
    if index is not None:
        entry["index"] = index
    return entry


class TestInsertionStats:
    def test_a_run_that_inserted_its_plan_is_ok(self) -> None:
        stats = insertion_stats(_meta())
        assert stats.verdict == OK_VERDICT
        assert (stats.planned, stats.departed, stats.arrived) == (PLANNED, PLANNED, 900)
        assert stats.departed_fraction == pytest.approx(1.0)
        assert stats.ramps == () and stats.starved_ramps == ()

    def test_the_healthy_threshold_is_inclusive(self) -> None:
        departed = round(HEALTHY_DEPARTED_FRACTION * PLANNED)
        assert insertion_stats(_meta(departed=departed)).verdict == OK_VERDICT
        assert insertion_stats(_meta(departed=departed - 1)).verdict.startswith("backlog:")

    def test_a_backlog_names_the_share_that_never_departed(self) -> None:
        stats = insertion_stats(_meta(departed=400))
        assert stats.departed_fraction == pytest.approx(0.4)
        assert stats.verdict == "backlog: 60 % of planned vehicles never departed"

    def test_starved_on_ramps_are_named(self) -> None:
        stats = insertion_stats(
            _meta(
                ramps=[
                    _ramp("OH-ON", "on", 4 * STARVED_RAMP_MIN_PLANNED, STARVED_RAMP_MIN_PLANNED),
                    _ramp(
                        "BR-ON", "on", 4 * STARVED_RAMP_MIN_PLANNED, 4 * STARVED_RAMP_MIN_PLANNED
                    ),
                    # Too few planned to judge, and an off-ramp, which carries
                    # no insertion at all.
                    _ramp("TINY-ON", "on", STARVED_RAMP_MIN_PLANNED - 1, 0),
                    _ramp("OH-OFF", "off", 4 * STARVED_RAMP_MIN_PLANNED, 0),
                ]
            )
        )
        assert [r.name for r in stats.ramps] == ["OH-ON", "BR-ON", "TINY-ON"]
        assert stats.starved_ramps == ("OH-ON",)
        assert stats.verdict == "starved ramps: OH-ON"
        assert stats.ramps[0].fraction == pytest.approx(0.25)
        assert stats.ramps[1].starved is False

    def test_both_problems_are_reported_together(self) -> None:
        stats = insertion_stats(
            _meta(
                departed=400,
                ramps=[_ramp("OH-ON", "on", 4 * STARVED_RAMP_MIN_PLANNED, 0)],
            )
        )
        assert stats.verdict == (
            "backlog: 60 % of planned vehicles never departed; starved ramps: OH-ON"
        )

    def test_an_empty_plan_is_not_called_a_backlog(self) -> None:
        stats = insertion_stats(_meta(planned=0, departed=0, arrived=0))
        assert math.isnan(stats.departed_fraction)
        assert stats.verdict == NO_PLAN_VERDICT

    def test_metadata_without_the_counters_is_refused(self) -> None:
        assert records_insertion({"seed": 1}) is False
        assert records_insertion(_meta()) is True
        with pytest.raises(ValueError, match="insertion counters"):
            insertion_stats({"seed": 1})

    def test_a_missing_arrived_counter_stays_missing(self) -> None:
        """A zero would read as "nothing completed the corridor" — gridlock."""
        stats = insertion_stats(_meta(arrived=None))
        assert stats.arrived is None
        assert json.loads(json.dumps(stats.to_dict()))["arrived"] is None
        assert stats.verdict == OK_VERDICT  # the counter says nothing about insertion

    def test_an_unnamed_ramp_is_labelled_by_its_scenario_index(self) -> None:
        """``meta["ramps"][k]["index"]`` is what the label must resolve against.

        Counting on-ramps only made the second on-ramp "ramp 1" although the
        scenario's ramps[1] is the off-ramp in front of it.
        """
        big = 4 * STARVED_RAMP_MIN_PLANNED
        stats = insertion_stats(
            _meta(
                ramps=[
                    _ramp("", "on", big, 0, index=0),
                    _ramp("OH-OFF", "off", big, 0, index=1),
                    _ramp("", "on", big, 0, index=2),
                ]
            )
        )
        assert [r.name for r in stats.ramps] == ["ramp 0", "ramp 2"]
        assert stats.starved_ramps == ("ramp 0", "ramp 2")

    def test_an_unnamed_ramp_without_an_index_falls_back_to_its_position(self) -> None:
        stats = insertion_stats(
            _meta(ramps=[_ramp("OH-OFF", "off", 10, 0), _ramp("", "on", 10, 0)])
        )
        assert [r.name for r in stats.ramps] == ["ramp 1"]

    def test_to_dict_is_json_serialisable(self) -> None:
        stats = insertion_stats(_meta(ramps=[_ramp("OH-ON", "on", 200, 10)]))
        payload = json.loads(json.dumps(stats.to_dict()))
        assert payload["verdict"] == stats.verdict
        assert payload["ramps"][0]["starved"] is True


class TestAggregateInsertion:
    def test_no_runs_says_nothing(self) -> None:
        assert aggregate_insertion([]) is None

    def test_pooled_counts_mean_and_worst_replicate(self) -> None:
        stats = [insertion_stats(_meta(departed=d)) for d in (1000, 800, 600)]
        summary = aggregate_insertion(stats)
        assert summary is not None
        assert summary.n_runs == 3
        assert (summary.planned, summary.departed) == (3000, 2400)
        assert summary.mean_arrived == pytest.approx(900.0) and summary.n_with_arrived == 3
        assert summary.mean_departed_fraction == pytest.approx(0.8)
        assert summary.min_departed_fraction == pytest.approx(0.6)
        assert summary.verdict == "backlog: 20 % of planned vehicles never departed"

    def test_starved_ramps_are_unioned_in_first_seen_order(self) -> None:
        big = 4 * STARVED_RAMP_MIN_PLANNED
        stats = [
            insertion_stats(
                _meta(ramps=[_ramp("OH-ON", "on", big, 0), _ramp("BR-ON", "on", big, big)])
            ),
            insertion_stats(
                _meta(ramps=[_ramp("OH-ON", "on", big, 0), _ramp("BR-ON", "on", big, 0)])
            ),
        ]
        summary = aggregate_insertion(stats)
        assert summary is not None
        assert summary.starved_ramps == ("OH-ON", "BR-ON")
        assert summary.verdict == "starved ramps: OH-ON, BR-ON"

    def test_an_undefined_fraction_contributes_nothing(self) -> None:
        summary = aggregate_insertion(
            [insertion_stats(_meta()), insertion_stats(_meta(planned=0, departed=0, arrived=0))]
        )
        assert summary is not None
        assert summary.mean_departed_fraction == pytest.approx(1.0)
        assert summary.min_departed_fraction == pytest.approx(1.0)
        assert summary.verdict == OK_VERDICT

    def test_arrived_is_a_mean_over_the_runs_that_recorded_it(self) -> None:
        """Summing a subset beside two full sums would read as vanished vehicles."""
        summary = aggregate_insertion(
            [
                insertion_stats(_meta(arrived=900)),
                insertion_stats(_meta(arrived=None)),
                insertion_stats(_meta(arrived=700)),
            ]
        )
        assert summary is not None
        assert summary.n_runs == 3 and summary.n_with_arrived == 2
        assert summary.mean_arrived == pytest.approx(800.0)
        assert json.loads(json.dumps(summary.to_dict()))["mean_arrived"] == pytest.approx(800.0)

    def test_no_run_recorded_arrival_says_so(self) -> None:
        summary = aggregate_insertion([insertion_stats(_meta(arrived=None))])
        assert summary is not None
        assert summary.mean_arrived is None and summary.n_with_arrived == 0

    def test_stats_are_frozen(self) -> None:
        stats = insertion_stats(_meta())
        assert isinstance(stats, InsertionStats)
        with pytest.raises(AttributeError):
            stats.departed = 0  # type: ignore[misc]


class TestScorePoolSize:
    """The scoring pool is sized by memory, from the parquet footer (no SUMO).

    A scoring worker's peak RSS grows with the replicate's row count
    (``SCORE_WORKER_BYTES_PER_ROW``, measured); six workers on a 4-hour
    corridor would exceed a 125 GB machine, so the default pool is capped by
    ``MemAvailable``.
    """

    ROWS = 1000

    @pytest.fixture
    def run_dir(self, tmp_path: Path) -> Path:
        import numpy as np
        import pyarrow as pa
        import pyarrow.parquet as pq

        d = tmp_path / "abc" / "1"
        d.mkdir(parents=True)
        table = pa.table(
            {
                "t": np.arange(self.ROWS, dtype=np.float64),
                "veh_id": ["v0"] * self.ROWS,
                "x": np.zeros(self.ROWS),
                "v": np.zeros(self.ROWS),
            }
        )
        with open(d / "trajectories.parquet", "wb") as f:
            pq.write_table(table, f)
        return d

    def test_rows_come_from_the_footer(self, run_dir: Path) -> None:
        assert trajectory_rows(run_dir) == self.ROWS
        assert trajectory_rows(run_dir.parent / "pruned") is None

    def test_estimate_is_base_plus_rows(self) -> None:
        assert score_worker_bytes(0) == SCORE_WORKER_BASE_BYTES
        assert score_worker_bytes(self.ROWS) == (
            SCORE_WORKER_BASE_BYTES + SCORE_WORKER_BYTES_PER_ROW * self.ROWS
        )

    def test_pool_is_capped_by_available_memory(self, run_dir: Path) -> None:
        per_worker = score_worker_bytes(self.ROWS)
        # Room for exactly two workers inside the planning fraction.
        available = int(2 * per_worker / SCORE_MEMORY_FRACTION) + 1
        assert score_pool_size(6, [run_dir], available_bytes=available) == (2, per_worker)
        # Plenty of memory: the CPU-side limit stands.
        assert score_pool_size(6, [run_dir], available_bytes=10**12) == (6, per_worker)
        # Not even one worker fits: one runs anyway (the caller's choice to score at all).
        assert score_pool_size(6, [run_dir], available_bytes=1) == (1, per_worker)

    def test_largest_replicate_sets_the_estimate(self, run_dir: Path, tmp_path: Path) -> None:
        import numpy as np
        import pyarrow as pa
        import pyarrow.parquet as pq

        bigger = tmp_path / "abc" / "2"
        bigger.mkdir()
        n = 4 * self.ROWS
        with open(bigger / "trajectories.parquet", "wb") as f:
            pq.write_table(pa.table({"t": np.zeros(n)}), f)
        pruned = tmp_path / "abc" / "3"
        pruned.mkdir()
        _, per_worker = score_pool_size(6, [run_dir, bigger, pruned], available_bytes=10**12)
        assert per_worker == score_worker_bytes(n)

    def test_without_trajectories_or_meminfo_there_is_no_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import validation.battery as battery

        assert score_pool_size(4, [tmp_path], available_bytes=1) == (4, None)
        monkeypatch.setattr(battery, "_MEMINFO_PATH", tmp_path / "absent")
        assert available_memory_bytes() is None

    def test_meminfo_is_parsed_in_kib(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import validation.battery as battery

        meminfo = tmp_path / "meminfo"
        meminfo.write_text("MemTotal:       131072000 kB\nMemAvailable:   122070312 kB\n")
        monkeypatch.setattr(battery, "_MEMINFO_PATH", meminfo)
        assert available_memory_bytes() == 122070312 * 1024


class TestReadScoringFrame:
    """``read_scoring_frame`` equals ``read_trajectories`` with ``veh_id``
    factorized (``sort=True``) on every file layout the reader can meet, and
    the batched read hands the same numbers to the metrics as the file read."""

    @staticmethod
    def _frame(n_veh: int, samples: int = 100, headway_s: float = 1.0):
        """Time-major rows: ``n_veh`` vehicles of ``samples`` rows each."""
        import numpy as np
        import pandas as pd

        offsets = 0.5 * np.arange(samples)
        t = (headway_s * np.arange(n_veh)[:, None] + offsets[None, :]).ravel()
        veh = np.repeat([f"veh_{k}" for k in range(n_veh)], samples)
        rng = np.random.default_rng(0)
        v = rng.uniform(5.0, 30.0, t.size)
        x = np.tile(offsets, n_veh) * np.repeat(rng.uniform(10.0, 30.0, n_veh), samples)
        frame = pd.DataFrame({"t": t, "veh_id": veh, "x": x, "v": v})
        return frame.sort_values("t", kind="stable").reset_index(drop=True)

    @staticmethod
    def _write(run_dir: Path, frame, *, row_group_size: int | None = None, dictionary=False):
        import pyarrow as pa
        import pyarrow.parquet as pq

        run_dir.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(frame, preserve_index=False)
        if dictionary:
            i = table.schema.get_field_index("veh_id")
            table = table.set_column(i, "veh_id", table.column("veh_id").dictionary_encode())
        with open(run_dir / "trajectories.parquet", "wb") as f:
            pq.write_table(table, f, row_group_size=row_group_size)
        (run_dir / "meta.json").write_text(
            json.dumps({"config": {"sim": {"warmup_s": 20.0, "duration_s": 1000.0}}})
        )
        return run_dir

    @staticmethod
    def _assert_same(run_dir: Path) -> None:
        import numpy as np
        import pandas as pd

        from validation.battery import read_scoring_frame, read_trajectories

        got = read_scoring_frame(run_dir)
        ref = read_trajectories(run_dir)
        assert list(got.columns) == list(ref.columns) == ["t", "veh_id", "x", "v"]
        assert got["veh_id"].dtype == np.intp
        for col in ("t", "x", "v"):
            assert got[col].dtype == np.float64
            assert np.array_equal(got[col].to_numpy(), ref[col].to_numpy(), equal_nan=True)
        codes, _ = pd.factorize(ref["veh_id"].astype("str"), sort=True)
        assert np.array_equal(got["veh_id"].to_numpy(), codes)

    def test_batches_split_vehicles_and_the_last_batch_is_short(self, tmp_path: Path) -> None:
        from validation.battery import SCORING_READ_BATCH_ROWS

        frame = self._frame(n_veh=3000)  # 300k rows: two batches, the second short
        assert SCORING_READ_BATCH_ROWS < len(frame) < 2 * SCORING_READ_BATCH_ROWS
        assert len(frame) % SCORING_READ_BATCH_ROWS != 0
        self._assert_same(self._write(tmp_path / "split", frame, row_group_size=100_000))

    def test_extra_and_reordered_columns_and_nan_positions(self, tmp_path: Path) -> None:
        import numpy as np

        frame = self._frame(n_veh=20)
        frame.loc[[3, 40, 41], "x"] = np.nan
        frame.loc[[7, 8], "v"] = np.nan
        frame["lane"] = np.zeros(len(frame), dtype=np.int32)
        frame = frame[["lane", "v", "x", "veh_id", "t"]]
        self._assert_same(self._write(tmp_path / "wide", frame))

    def test_dictionary_encoded_ids_get_the_string_order(self, tmp_path: Path) -> None:
        frame = self._frame(n_veh=20)
        self._assert_same(self._write(tmp_path / "dict", frame, dictionary=True))

    def test_empty_and_single_row_files(self, tmp_path: Path) -> None:
        frame = self._frame(n_veh=2)
        self._assert_same(self._write(tmp_path / "empty", frame.iloc[:0]))
        self._assert_same(self._write(tmp_path / "one", frame.iloc[:1]))

    def test_a_null_id_is_refused(self, tmp_path: Path) -> None:
        from validation.battery import read_scoring_frame

        frame = self._frame(n_veh=5)
        frame.loc[[2, 9], "veh_id"] = None
        with pytest.raises(ValueError, match="null veh_id"):
            read_scoring_frame(self._write(tmp_path / "null", frame))

    def test_the_frame_yields_the_file_reads_metrics(self, tmp_path: Path) -> None:
        import dataclasses

        from validation.battery import read_scoring_frame, replicate_wave_speed_kmh
        from validation.criteria import CRITERIA_PROFILES
        from validation.metrics import compute_metrics

        frame = self._frame(n_veh=60)
        # Two duplicated (veh_id, t) rows exercise the tie rule on the codes.
        frame = frame.iloc[[*range(len(frame)), 5, 700]].reset_index(drop=True)
        run_dir = self._write(tmp_path / "run", frame, row_group_size=1000)
        handed = read_scoring_frame(run_dir)
        via_file = dataclasses.asdict(compute_metrics(run_dir, x_ref=300.0))
        via_frame = dataclasses.asdict(compute_metrics(run_dir, x_ref=300.0, trajectories=handed))
        # JSON text: byte identity, NaN included.
        assert json.dumps(via_frame) == json.dumps(via_file)
        detector = next(iter(CRITERIA_PROFILES.values())).wave_detector
        assert json.dumps(replicate_wave_speed_kmh(run_dir, detector, trajectories=handed)) == (
            json.dumps(replicate_wave_speed_kmh(run_dir, detector))
        )
