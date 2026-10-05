"""The corridor battery carries the waiting measures (WP-109; docs/FRISCO_PROTOCOL.md §8.2).

The 2026-10-04 Minnesota rehearsal battery's runs each wrote a demand ledger
(``journeys.parquet``), but the battery stored only the standard metrics per
replicate, and its report said waiting was "not recorded on every run (no
demand ledger)". Two things are checked here, without SUMO (the battery's
end-to-end SUMO test, ``test_validation_corridor_battery.py``, checks the
same on a real one-replicate run):

* each replicate's stored ``metrics.json`` and the battery artifact carry
  :class:`validation.metrics.WaitingMetrics` additively — absent (``null``),
  never 0, for a run without a ledger; every existing key and value is
  unchanged;
* the report's confidence row names the right cause: with a ledger on every
  run but no group labelled ``baseline`` (the rehearsal's do-nothing
  configuration carries its merge models in its label), the waiting time is
  counted, and the missing recommendation is the baseline's absence.
"""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path
from typing import Any

import pytest

from tests.test_validation import test_validation_corridor_battery_collisions as col
from tests.test_validation import test_validation_report_client_summary as cs
from tests.test_validation import test_validation_report_delay_recommendation as dr
from tests.test_validation.test_validation_waiting import T_END, T_LO, hand_table
from validation.battery import (
    METRICS_FILE,
    SCORES_FILE,
    load_replicate_analysis,
    replicate_waiting,
    waiting_from_json,
    waiting_summary,
)
from validation.metrics import (
    JOURNEYS_FILE,
    WAITING_FIELDS,
    Metrics,
    WaitingMetrics,
    compute_waiting_metrics,
    waiting_metrics,
)
from validation.observed import ObservedScores
from validation.report import generate_report


def _replicate(
    root: Path, *, ledger: bool = True, meta_block: bool = True, stored: Any = "absent"
) -> Path:
    """A scored, pruned replicate: meta, metrics.json, scores; ledger optional.

    ``stored`` is the ``waiting`` value written into ``metrics.json``
    (``"absent"``: a file scored before the key existed).
    """
    run = root
    run.mkdir(parents=True)
    meta: dict[str, Any] = {
        "config": {"sim": {"warmup_s": T_LO}},
        "n_vehicles_planned": 6,
        "n_vehicles_departed": 4,
    }
    if meta_block:
        meta["journeys"] = {"file": JOURNEYS_FILE, "end_s": T_END}
    (run / "meta.json").write_text(json.dumps(meta))
    if ledger:
        hand_table().to_parquet(run / JOURNEYS_FILE)
    payload: dict[str, Any] = {
        "metrics": {
            f.name: (3 if f.name in ("wave_count", "n_travel_time_veh") else 10.0)
            for f in dataclasses.fields(Metrics)
        },
        "criterion_wave_speed_kmh": None,
        "criterion_detector": "profile",
        "x_ref_m": 0.0,
        "span_m": [0.0, 0.0],
        "insertion": {},
    }
    if stored != "absent":
        payload["waiting"] = stored
    (run / METRICS_FILE).write_text(json.dumps(payload))
    scores = ObservedScores(
        geh_values=(4.0,),
        n_link_hours=1,
        rmspe=0.1,
        n_speed_cells=1,
        segment_speeds_sim=((25.0,),),
        segment_speeds_obs=((25.0,),),
        windows=(0,),
    )
    (run / SCORES_FILE).write_text(json.dumps(scores.to_dict()))
    return run


EXPECTED = waiting_metrics(hand_table(), t_lo=T_LO, t_end=T_END)


class TestReplicateWaiting:
    def test_a_run_with_its_ledger_is_measured(self, tmp_path: Path) -> None:
        run = _replicate(tmp_path / "r")
        assert replicate_waiting(run) == EXPECTED == compute_waiting_metrics(run)

    def test_a_run_without_the_ledger_is_absent_never_zero(self, tmp_path: Path) -> None:
        assert replicate_waiting(_replicate(tmp_path / "a", ledger=False)) is None
        # the file without the meta block (a ledger the run did not vouch for)
        assert replicate_waiting(_replicate(tmp_path / "b", meta_block=False)) is None


class TestStoredMetrics:
    def test_a_stored_block_is_read_back_null_as_nan(self, tmp_path: Path) -> None:
        stored = dataclasses.asdict(EXPECTED) | {"mean_tt_incl_waiting_s": None}
        run = _replicate(tmp_path / "r", ledger=False, stored=stored)
        w = load_replicate_analysis(run).waiting
        assert w is not None and math.isnan(w.mean_tt_incl_waiting_s)
        assert w.total_delay_incl_waiting_veh_h == EXPECTED.total_delay_incl_waiting_veh_h
        assert w.n_censored == EXPECTED.n_censored

    def test_a_stored_null_stays_absent(self, tmp_path: Path) -> None:
        # stored null wins even when a ledger has appeared since
        assert load_replicate_analysis(_replicate(tmp_path / "r", stored=None)).waiting is None

    def test_a_file_scored_before_the_key_is_measured_from_the_ledger(self, tmp_path: Path) -> None:
        assert load_replicate_analysis(_replicate(tmp_path / "with")).waiting == EXPECTED
        assert (
            load_replicate_analysis(_replicate(tmp_path / "without", ledger=False)).waiting is None
        )

    def test_waiting_from_json(self) -> None:
        assert waiting_from_json(None) is None
        assert waiting_from_json(dataclasses.asdict(EXPECTED)) == EXPECTED


class TestSummary:
    def test_intervals_over_the_recorded_seeds_and_the_missing_ones_named(self) -> None:
        other = dataclasses.replace(
            EXPECTED, total_delay_incl_waiting_veh_h=EXPECTED.total_delay_incl_waiting_veh_h + 0.1
        )
        block = waiting_summary([EXPECTED, None, other], labels=[11, 12, 13])
        assert block is not None
        assert block["n_runs"] == 3 and block["n_runs_recorded"] == 2
        assert block["runs_not_recorded"] == [12]
        assert set(block["ci"]) == set(WAITING_FIELDS)
        delay = block["ci"]["total_delay_incl_waiting_veh_h"]
        assert delay["n"] == 2 and delay["underpowered"] is True
        assert delay["mean"] == pytest.approx(EXPECTED.total_delay_incl_waiting_veh_h + 0.05)
        assert "never counted as zero" in block["definition"]

    def test_no_ledger_anywhere_is_null(self) -> None:
        assert waiting_summary([None, None]) is None
        assert waiting_summary([]) is None


def _build(tmp_path: Path, waiting: list[WaitingMetrics | None] | None) -> dict[str, Any]:
    """``build_artifact`` as the collision tests call it, plus ``waiting_list``.

    ``None`` is the call without the argument (``col._build``, which also
    writes the scenario file both calls read).
    """
    artifact = col._build(tmp_path, col._metas())
    if waiting is None:
        return artifact
    battery = col._load_script()
    kwargs = _build_kwargs(tmp_path, battery)
    return dict(battery.build_artifact(**kwargs, metas=col._metas(), waiting_list=waiting))


def _build_kwargs(tmp_path: Path, battery: Any) -> dict[str, Any]:
    from tests.test_validation.test_validation_observed import table_payload, table_scores
    from validation.battery import insertion_stats, weave_exit_summary
    from validation.observed import ObservedCorridor

    metrics = Metrics(
        **{
            f.name: (3 if f.name in ("wave_count", "n_travel_time_veh") else 10.0)
            for f in dataclasses.fields(Metrics)
        }
    )
    n = len(col.SEEDS)
    return {
        "scenario": "table_corridor",
        "cfg": battery.load_scenario(tmp_path / "table_corridor.yaml"),
        "profile": battery.get_profile("fhwa_default"),
        "seeds": list(col.SEEDS),
        "dirs": [Path("runs") / str(seed) for seed in col.SEEDS],
        "metrics_list": [metrics] * n,
        "scores_list": [table_scores()] * n,
        "wave_speeds": [math.nan] * n,
        "insertion_list": [insertion_stats({"n_vehicles_planned": 7, "n_vehicles_departed": 7})]
        * n,
        "weave_exits": weave_exit_summary([{}] * n),
        "observed": ObservedCorridor.from_dict(table_payload()),
        "observations_path": "artifacts/observations_table.json",
        "criteria_rows": [],
        "ring": None,
        "x_offset_m": 50.0,
        "wall_s": 1.0,
    }


class TestArtifact:
    def test_per_seed_rows_and_the_pooled_block(self, tmp_path: Path) -> None:
        artifact = col._strict(_build(tmp_path, [EXPECTED, None, EXPECTED]))
        rows = [row["waiting"] for row in artifact["per_seed"]]
        assert rows[1] is None
        assert rows[0] == json.loads(json.dumps(dataclasses.asdict(EXPECTED)))
        block = artifact["waiting"]
        assert block["n_runs_recorded"] == 2 and block["runs_not_recorded"] == [102]
        keys = list(artifact)
        assert keys.index("waiting") == keys.index("metrics_ci") + 1

    def test_the_keys_are_additive_and_every_existing_value_is_unchanged(
        self, tmp_path: Path
    ) -> None:
        without = col._strict(_build(tmp_path / "none", None))
        absent = col._strict(_build(tmp_path / "absent", [None, None, None]))
        present = col._strict(_build(tmp_path / "present", [EXPECTED] * 3))
        assert without["waiting"] is None and absent["waiting"] is None
        assert present["waiting"]["n_runs_recorded"] == 3
        for key in set(without) - {"per_seed", "waiting"}:
            reference = json.dumps(without[key], allow_nan=False)
            assert json.dumps(absent[key], allow_nan=False) == reference, key
            assert json.dumps(present[key], allow_nan=False) == reference, key
        for a, b, c in zip(
            without["per_seed"], absent["per_seed"], present["per_seed"], strict=True
        ):
            assert a["waiting"] is None and b["waiting"] is None and c["waiting"] is not None
            for key in set(a) - {"waiting"}:
                assert b[key] == a[key] and c[key] == a[key], key
        assert present["schema"] == "flowstate.corridor_validation/1"


def test_battery_console_line(tmp_path: Path) -> None:
    battery = col._load_script()
    assert "not recorded" in battery.waiting_line(None)
    block = waiting_summary([EXPECTED, None])
    line = battery.waiting_line(block)
    assert "over 1 of 2 replicate(s); not recorded for 1" in line


def test_the_report_row_names_the_missing_baseline_not_the_ledger(tmp_path: Path) -> None:
    """Every run carries its ledger; no group is labelled ``baseline``."""
    root = dr._run_set(tmp_path / "runs")
    for seed in (1, 2, 3):
        meta_path = root / cs.BASE_HASH / str(seed) / "meta.json"
        meta = json.loads(meta_path.read_text())
        # the rehearsal's do-nothing battery: merge models in the label
        meta["config"]["network"] = {
            "kind": "osm",
            "ramps": [{"kind": "on", "name": "r1", "attach_edge": "e1", "merge": "weave"}],
        }
        meta_path.write_text(json.dumps(meta))
    out = tmp_path / "report" / "report.md"
    generate_report(root, out, gate=None)
    summary = cs._section(out.read_text(), "## Client summary")
    row = next(
        ln
        for ln in summary.splitlines()
        if ln.startswith("| Waiting time on ramps and before entering the network |")
    )
    assert "| yes | counted" in row
    assert "no single baseline (do-nothing) group" in row
    assert "no demand ledger" not in row


def test_the_report_row_still_says_no_ledger_when_one_is_missing(tmp_path: Path) -> None:
    root = dr._run_set(tmp_path / "runs", ledger_on_strategy=False)
    out = tmp_path / "report" / "report.md"
    generate_report(root, out, gate=None)
    summary = cs._section(out.read_text(), "## Client summary")
    assert (
        "| Waiting time on ramps and before entering the network | no | not recorded on every "
        "run (no demand ledger)" in summary
    )
