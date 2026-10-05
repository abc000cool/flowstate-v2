"""The report's client summary (docs/FRISCO_PROTOCOL.md §10) and fuel labelling.

Synthetic run sets (the report tests' constant-speed trajectories): a
baseline and a controlled group whose seed-paired mean travel time is shorter
than the baseline's. The summary must open the report with the gate result;
say "not evaluated" and recommend nothing without a gate (or with a gate that
names no configuration); recommend nothing and withhold the strategy tables
when the gate failed; and, only when it passed, state each strategy's effect
as an interval — the recommendation line itself needs total delay including
waiting on every run (these run sets carry no demand ledger, so it says why
there is none; tests/test_validation/test_validation_report_delay_recommendation.py
has the ledgers). Every line that mentions fuel calls it a model estimate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests.test_validation import test_validation_report as base
from validation.baseline_gate import CheckResult, GateResult
from validation.report import (
    CLIENT_STRATEGY_METRICS,
    CONTOURS_WITHHELD,
    FUEL_ESTIMATE_TEXT,
    STRATEGY_WITHHELD,
    _pct_interval,
    generate_report,
)

BASE_HASH = base.BASE_HASH
CTRL_HASH = base.CTRL_HASH


def _run_set(root: Path, *, collisions: int | None = 0) -> Path:
    """Baseline spread 3 + 0.05·seed, controller 5 + 0.1·seed.

    With speeds ``25 ∓ d, 25`` only the vehicles at 25 and 25 + d complete
    the shared span, so the larger spread — the controller — has the shorter
    mean travel time in every seed: a resolved, seed-varying reduction.
    """
    for seed in (1, 2, 3):
        for chash, av, spread in (
            (BASE_HASH, base.BASELINE_AV, 3.0 + 0.05 * seed),
            (CTRL_HASH, base.FS_AV, 5.0 + 0.1 * seed),
        ):
            run = base._write_run(
                root / chash / str(seed), seed=seed, config_hash=chash, av=av, spread=spread
            )
            meta = json.loads((run / "meta.json").read_text())
            if collisions is not None:
                meta["n_collisions"] = collisions if chash == CTRL_HASH else 0
            (run / "meta.json").write_text(json.dumps(meta))
    return root


def _check(check: str, day_set: str, status: str, plain: str, gating: bool = True) -> CheckResult:
    return CheckResult(
        check=check,
        day_set=day_set,
        status=status,  # type: ignore[arg-type]
        gating=gating,
        value=0.5,
        target="target",
        shortfall=None if status == "pass" else 0.35,
        plain=plain,
        label="[federal] label",
    )


def _gate(passed: bool, config_hash: str = BASE_HASH) -> GateResult:
    c1 = "pass" if passed else "fail"
    checks = (
        _check("C1", "calibration", c1, "Link flows, calibration days: GEH < 5 on 50.0 %."),
        _check("C1", "validation", "pass", "Link flows, validation days: fine."),
        _check("C2", "calibration", "fail", "Texas: short.", gating=False),
        _check("C3", "calibration", "pass", "Speeds, calibration days: fine."),
        _check("C3", "validation", "pass", "Speeds, validation days: fine."),
        _check("C4", "calibration", "not_applicable", "Wave speed: not applicable."),
        _check("C5", "all runs", "pass", "Collisions: none."),
        _check("C6", "calibration", "pass", "Bottlenecks: fine."),
        _check("C6", "validation", "pass", "Bottlenecks: fine."),
    )
    return GateResult(
        passed=passed,
        checks=checks,
        reasons=() if passed else (checks[0].plain,),
        day_sets={},
        n_replicates=20,
        config_hash=config_hash,
        scenario="synthetic",
        split={
            "seed": 20261004,
            "calibration_dates": ["2026-09-01", "2026-09-08"],
            "validation_dates": ["2026-09-02"],
            "underpowered": True,
            "underpowered_reason": "2 calibration day(s), fewer than 5",
        },
        excluded_detectors={"3240": "lane-3 loop chatters"},
    )


def _report(tmp_path: Path, gate: GateResult | None, **kw: Any) -> str:
    root = _run_set(tmp_path / "runs", **kw)
    out = tmp_path / "report" / "report.md"
    generate_report(root, out, gate=gate)
    return out.read_text()


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start : len(text) if end < 0 else end]


class TestFuelLabel:
    @pytest.mark.parametrize("passed", [None, True, False])
    def test_every_line_that_mentions_fuel_calls_it_a_model_estimate(
        self, tmp_path: Path, passed: bool | None
    ) -> None:
        text = _report(tmp_path, None if passed is None else _gate(passed))
        lines = [ln for ln in text.splitlines() if "fuel" in ln.lower()]
        assert lines  # the report does carry fuel figures
        for line in lines:
            assert "model estimate" in line, line
        assert FUEL_ESTIMATE_TEXT[1:] in text
        assert "| fuel_ml_per_veh_km (model estimate) |" in text
        assert "Fuel, model estimate [ml/veh·km]" in text or passed is False


class TestClientSummary:
    def test_it_opens_the_report(self, tmp_path: Path) -> None:
        text = _report(tmp_path, None)
        assert text.index("## Client summary") < text.index("## Provenance")
        summary = _section(text, "## Client summary")
        for heading in (
            "### What we are confident about, and what we are not",
            "### Strategy results",
            "### Data, days and assumptions",
            "### Limits of this study",
        ):
            assert heading in summary

    def test_without_a_gate_it_is_not_evaluated_and_recommends_nothing(
        self, tmp_path: Path
    ) -> None:
        text = _report(tmp_path, None)
        summary = _section(text, "## Client summary")
        assert "Baseline gate: NOT EVALUATED" in summary
        assert "This report contains no strategy recommendations" in summary
        assert "On this model, strategy" not in text
        assert "| Traffic counts at the detectors (link flows, C1) | not established |" in summary
        # without a gate the existing strategy tables stay, marked as model output
        assert "## Strategy comparison" in text and STRATEGY_WITHHELD not in text

    def test_a_failed_gate_withholds_every_strategy_table(self, tmp_path: Path) -> None:
        text = _report(tmp_path, _gate(False))
        summary = _section(text, "## Client summary")
        assert summary.index("Baseline gate FAILED") < summary.index("### What we are confident")
        assert "C1 FAIL: Link flows, calibration days: GEH < 5 on 50.0 %." in summary
        assert "C2 FAIL (reported, not part of the gate)" in summary
        assert (
            "This report contains no strategy recommendations: the model did not reproduce "
            "the corridor" in summary
        )
        assert "On this model, strategy" not in text
        assert "| Effects of the strategies | no | not delivered" in summary
        assert STRATEGY_WITHHELD in _section(text, "## Strategy comparison")
        assert "| Configuration |" not in text
        assert "#### follower_stopper" not in text
        ctrl = text[text.index(f"### follower_stopper @ 5% / 100% (`{CTRL_HASH}`)") :]
        ctrl = ctrl[: ctrl.index("### Controller minus baseline")]
        assert STRATEGY_WITHHELD in ctrl and "| Metric |" not in ctrl
        base_part = text[text.index(f"### baseline (`{BASE_HASH}`)") :]
        assert "| Metric | Mean |" in base_part  # the baseline itself is reported
        # no strategy contour panel either: only the baseline's contours
        contours = _section(text, "## Speed contours")
        assert CONTOURS_WITHHELD in contours
        assert "speed_contour_pair" not in contours and "follower_stopper" not in contours
        assert contours.count("](speed_contour_") == 3
        # the split, exclusions and limits are stated whatever the outcome
        assert "Calibration days: 2026-09-01, 2026-09-08. Validation days: 2026-09-02." in summary
        assert "The validation is underpowered: 2 calibration day(s), fewer than 5." in summary
        assert "Excluded detector 3240: lane-3 loop chatters" in summary
        limits = _section(text, "### Limits of this study")
        for word in ("Single corridor", "Model-form uncertainty", "model estimate", "Compliance"):
            assert word in limits

    def test_a_passed_gate_states_ranges_and_without_delay_recommends_nothing(
        self, tmp_path: Path
    ) -> None:
        text = _report(tmp_path, _gate(True))
        summary = _section(text, "## Client summary")
        assert "Baseline gate PASSED" in summary
        assert "| Effects of the strategies | only as ranges |" in summary
        assert (
            "| Strategy | "
            + " | ".join(f"{name}, change [%]" for _, name in CLIENT_STRATEGY_METRICS)
            in summary
        )
        # the table states the seed-paired contrast as a share of the baseline mean
        from validation.report import _discover_runs, _fill_metrics, _group_runs

        groups = _group_runs(_discover_runs(tmp_path / "runs"))
        _fill_metrics(groups, None, None)
        d, lo_pct, hi_pct = _pct_interval(groups[0], groups[1], "mean_tt_s")
        assert d.method == "paired" and d.resolved and d.hi95 < 0.0
        row = next(ln for ln in summary.splitlines() if ln.startswith("| follower_stopper"))
        assert f"| {lo_pct:+.1f} to {hi_pct:+.1f} |" in row
        # review: the arm "wins" on the travel time of the vehicles that finished, but
        # no run carries total delay including waiting, so no recommendation is made
        assert "On this model, strategy" not in text
        lines = [ln for ln in summary.splitlines() if ln.startswith("- No recommendation")]
        assert lines == [
            "- No recommendation for follower_stopper @ 5% / 100%: 3 of 3 run(s) of the "
            "baseline and 3 of 3 run(s) of follower_stopper @ 5% / 100% record no total delay "
            "including waiting time (no demand ledger, journeys.parquet), so it is not shown "
            "that the strategy does not simply hold vehicles on ramps or off the road; a "
            "recommendation rests on that measure only (docs/FRISCO_PROTOCOL.md sections 8.2 "
            "and 8.4)."
        ]
        assert "| Waiting time on ramps and before entering the network | no |" in summary
        # the strategy tables are reported as usual
        assert STRATEGY_WITHHELD not in text and "| Configuration |" in text
        assert CONTOURS_WITHHELD not in text and "speed_contour_pair" in text
        assert "C1 PASS: Link flows, calibration days: GEH < 5 on 50.0 %. Source: [federal]" in text

    def test_a_strategy_with_collisions_gets_no_recommendation(self, tmp_path: Path) -> None:
        text = _report(tmp_path, _gate(True), collisions=1)
        assert "On this model, strategy" not in text
        assert "No recommendation for follower_stopper @ 5% / 100%: its runs recorded SUMO " in text

    def test_a_gate_from_another_configuration_does_not_apply(self, tmp_path: Path) -> None:
        text = _report(tmp_path, _gate(True, config_hash="0123456789ab"))
        assert "On this model, strategy" not in text
        assert "the gate does not apply to it and no strategy recommendation is made" in text

    @pytest.mark.parametrize("passed", [True, False])
    def test_a_gate_without_a_configuration_hash_is_not_evaluated(
        self, tmp_path: Path, passed: bool
    ) -> None:
        text = _report(tmp_path, _gate(passed, config_hash=""))
        summary = _section(text, "## Client summary")
        assert summary.split("\n")[2].startswith(
            "Baseline gate: NOT EVALUATED. The supplied gate result names no configuration"
        )
        assert "Baseline gate PASSED" not in text and "Baseline gate FAILED" not in text
        assert "This report contains no strategy recommendations: the baseline gate was not" in (
            summary
        )
        assert "On this model, strategy" not in text
        assert STRATEGY_WITHHELD not in text  # not evaluated: tables stay, as model output


def test_template_has_the_section_and_no_numerals() -> None:
    import validation.report as report_mod

    template = (Path(report_mod.__file__).parent / "templates" / "report.md.j2").read_text()
    for section in (
        "## Client summary",
        "### What we are confident about, and what we are not",
        "### Limits of this study",
    ):
        assert section in template
    body = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", template, flags=re.S)
    assert not re.search(r"\d", body)
