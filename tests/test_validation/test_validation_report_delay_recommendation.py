"""The client summary's recommendation measure (WP-105; docs/FRISCO_PROTOCOL.md §8.2, §8.4).

When every run of the baseline and of a strategy carries its demand ledger
(``journeys.parquet``), the recommendation line is stated on total delay
including waiting time, the protocol's tuning objective; otherwise it keeps
the mean travel time and its own name (the run sets written before WP-105).
Synthetic run sets of the client-summary tests, plus hand-made ledgers in
which the strategy's delay is lower than the baseline's in every seed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from tests.test_validation import test_validation_report_client_summary as cs
from validation.metrics import JOURNEY_COLUMNS, JOURNEYS_FILE
from validation.report import (
    DELAY_RECOMMENDATION_METRIC,
    RECOMMENDATION_METRIC,
    _discover_runs,
    _fill_metrics,
    _group_runs,
    _pct_interval,
    generate_report,
    recommendation_metric,
)

END_S = 1000.0


def _ledger(run: Path, time_in_system_s: float) -> None:
    """Three vehicles planned at 0 s, each arriving after ``time_in_system_s``."""
    rows = [
        {
            "veh_id": f"v{i}",
            "route": "main",
            "origin_ramp": -1,
            "depart_planned_s": 0.0,
            "route_length_m": 1000.0,
            "free_flow_s": 50.0,
            "inserted": True,
            "depart_s": 0.5,
            "insert_offset_m": 5.0,
            "arrived": True,
            "arrival_s": time_in_system_s + i,
            "distance_end_m": np.nan,
            "free_flow_covered_s": 50.0,
            "meter_ramp": -1,
            "meter_hold_start_s": np.nan,
            "meter_released": False,
            "meter_release_s": np.nan,
            "meter_wait_s": 0.0,
        }
        for i in range(3)
    ]
    pd.DataFrame(rows, columns=list(JOURNEY_COLUMNS)).to_parquet(run / JOURNEYS_FILE)
    meta = json.loads((run / "meta.json").read_text())
    meta["journeys"] = {"file": JOURNEYS_FILE, "end_s": END_S}
    (run / "meta.json").write_text(json.dumps(meta))


def _run_set(root: Path, *, ledger_on_strategy: bool = True) -> Path:
    cs._run_set(root)
    for seed in (1, 2, 3):
        _ledger(root / cs.BASE_HASH / str(seed), 200.0 + 10.0 * seed)
        if ledger_on_strategy:
            _ledger(root / cs.CTRL_HASH / str(seed), 150.0 + 5.0 * seed)
    return root


def _recommendation_lines(tmp_path: Path, **kw: bool) -> list[str]:
    root = _run_set(tmp_path / "runs", **kw)
    out = tmp_path / "report" / "report.md"
    generate_report(root, out, gate=cs._gate(True))
    summary = cs._section(out.read_text(), "## Client summary")
    return [ln for ln in summary.splitlines() if ln.startswith(("- On this model", "- No rec"))]


def test_a_run_set_with_ledgers_is_recommended_on_delay(tmp_path: Path) -> None:
    lines = _recommendation_lines(tmp_path)
    assert len(lines) == 1
    match = re.fullmatch(
        r"- On this model, strategy (.+) reduced total delay including waiting time by "
        r"(\d+\.\d)–(\d+\.\d) % \(95 % interval\) relative to doing nothing; this is a model "
        r"prediction\.",
        lines[0],
    )
    assert match is not None and match.group(1) == "follower_stopper @ 5% / 100%"
    groups = _group_runs(_discover_runs(tmp_path / "runs"))
    _fill_metrics(groups, None, None)
    assert recommendation_metric(groups[0], groups[1]) == DELAY_RECOMMENDATION_METRIC
    d, lo_pct, hi_pct = _pct_interval(groups[0], groups[1], DELAY_RECOMMENDATION_METRIC[0])
    assert d.method == "paired" and d.resolved and d.hi95 < 0.0
    assert (float(match.group(2)), float(match.group(3))) == (
        round(-hi_pct, 1),
        round(-lo_pct, 1),
    )


def test_without_ledgers_on_every_run_the_line_stays_on_mean_travel_time(
    tmp_path: Path,
) -> None:
    lines = _recommendation_lines(tmp_path, ledger_on_strategy=False)
    assert len(lines) == 1
    assert "mean travel time" in lines[0] and "delay" not in lines[0]
    groups = _group_runs(_discover_runs(tmp_path / "runs"))
    _fill_metrics(groups, None, None)
    assert recommendation_metric(groups[0], groups[1]) == RECOMMENDATION_METRIC
