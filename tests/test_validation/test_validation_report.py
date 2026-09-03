"""End-to-end tests for validation.report on synthetic run directories:
report generation, macro-only refusal (CLAUDE.md §5.6), seeded labeling
(CLAUDE.md §0.2), baseline-versus-controller grouping with seed-paired /
Welch contrasts (CLAUDE.md §7.4, docs/CONTROLLER_COMPARISON.md), and the
optional PDF rendering."""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from scipy.stats import t as student_t

from validation.criteria import CriteriaProfile
from validation.report import ReportRefusedError, contrast, generate_report, group_label

SPEEDS = (20.0, 25.0, 30.0)
MID_SPEED = 25.0

BASE_HASH = "base00000001"
CTRL_HASH = "ctrl00000001"
BASELINE_AV: dict[str, Any] = {"penetration": 0.0, "compliance": 1.0, "controller": None}
FS_AV: dict[str, Any] = {"penetration": 0.05, "compliance": 1.0, "controller": "follower_stopper"}


def _traj(spread: float | None = None) -> pd.DataFrame:
    """Three constant-speed vehicles; ``spread`` sets speeds 25 ∓ spread, 25."""
    speeds = SPEEDS if spread is None else (MID_SPEED - spread, MID_SPEED, MID_SPEED + spread)
    frames = []
    t = np.arange(0.0, 100.0 + 0.25, 0.5)
    for i, v in enumerate(speeds):
        frames.append(
            pd.DataFrame(
                {
                    "t": t,
                    "veh_id": f"veh{i}",
                    "x": v * t,
                    "lane": np.zeros(len(t), dtype=np.int32),
                    "v": np.full(len(t), v),
                    "a": np.zeros(len(t)),
                    "is_av": False,
                    "complied": True,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def _write_run(
    run_dir: Path,
    seed: int,
    tier: str = "micro",
    seeded: bool = False,
    with_trajectories: bool = True,
    config_hash: str = "cafe01234567",
    av: dict[str, Any] | None = None,
    spread: float | None = None,
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    if with_trajectories:
        _traj(spread).to_parquet(run_dir / "trajectories.parquet")
    meta: dict[str, Any] = {
        "config_hash": config_hash,
        "seed": seed,
        "tier": tier,
        "seeded": seeded,
        "versions": {"eclipse-sumo": "1.27.1", "flowstate": "2.0.0-dev"},
        "wall_time_s": 2.5,
        "calibration_artifacts": [{"path": "artifacts/fd_pems_d7.json", "data_hash": "deadbeef"}],
    }
    if av is not None:
        meta["config"] = {"av": av}
    (run_dir / "meta.json").write_text(json.dumps(meta))
    return run_dir


@pytest.fixture()
def micro_run_set(tmp_path: Path) -> Path:
    root = tmp_path / "runs" / "cafe01234567"
    _write_run(root / "1", seed=1)
    _write_run(root / "2", seed=2)
    return tmp_path / "runs"


def _two_group_run_set(root: Path, ctrl_seeds: tuple[int, ...] = (1, 2, 3)) -> Path:
    """Baseline (spatial σ_v = 5 + 0.1·seed) vs FollowerStopper (3 + 0.05·seed).

    With three symmetric speeds ``25 ∓ d, 25`` the spatial σ_v equals ``d``
    exactly, so the controller-minus-baseline difference is
    ``−2 − 0.05·seed`` per seed: a known, seed-varying, negative contrast.
    """
    for seed in (1, 2, 3):
        _write_run(
            root / BASE_HASH / str(seed),
            seed=seed,
            config_hash=BASE_HASH,
            av=BASELINE_AV,
            spread=5.0 + 0.1 * seed,
        )
    for seed in ctrl_seeds:
        _write_run(
            root / CTRL_HASH / str(seed),
            seed=seed,
            config_hash=CTRL_HASH,
            av=FS_AV,
            spread=3.0 + 0.05 * seed,
        )
    return root


@pytest.fixture()
def two_group_run_set(tmp_path: Path) -> Path:
    return _two_group_run_set(tmp_path / "runs")


def _table_after(text: str, marker: str) -> dict[str, list[str]]:
    """Rows of the first markdown table following ``marker``, keyed by column 1."""
    start = text.index(marker)
    rows: dict[str, list[str]] = {}
    in_table = False
    for line in text[start:].splitlines()[1:]:
        if line.startswith("|"):
            in_table = True
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if set(cells[0]) <= {"-", ":"}:
                continue
            rows[cells[0]] = cells
        elif in_table:
            break
    return rows


class TestGenerateReport:
    def test_end_to_end_contents(self, micro_run_set: Path, tmp_path: Path):
        out = tmp_path / "report" / "report.md"
        result = generate_report(
            micro_run_set,
            out,
            geh_values=[1.0] * 18 + [9.0] * 2,
            rmspe_value=0.12,
            created_at="2026-08-29T00:00:00Z",
        )
        assert result == out
        text = out.read_text()
        # Provenance: config hash, seeds, versions from meta.
        assert "cafe01234567" in text
        assert "eclipse-sumo" in text and "1.27.1" in text
        # Calibration artifacts with data hash.
        assert "artifacts/fd_pems_d7.json" in text and "deadbeef" in text
        # Criteria table rows, honest pass/fail.
        assert "link_flows_geh" in text and "PASS" in text
        assert "n_seeds" in text and "FAIL" in text  # only 2 seeds < 20
        # Metric table with underpowered flags (2 replicates).
        assert "throughput_veh_h" in text
        assert "underpowered" in text.lower()
        # Unseeded set: no seeded banner.
        assert "SEEDED RUNS INCLUDED" not in text

    def test_single_group_is_labeled_baseline_without_contrast(
        self, micro_run_set: Path, tmp_path: Path
    ):
        """A run set with one configuration renders one group and no delta table."""
        out = tmp_path / "report.md"
        generate_report(micro_run_set, out)
        text = out.read_text()
        assert "### baseline (`cafe01234567`)" in text
        assert text.count("| Metric | Mean | Lower | Upper | n | Underpowered |") == 1
        assert "Controller minus baseline" not in text
        assert "Wave-speed criterion input" not in text  # single group: no extra note
        assert "replicate\ncriterion (n_seeds >= 20): FAIL" in text

    def test_speed_contour_figures_written(self, micro_run_set: Path, tmp_path: Path):
        out = tmp_path / "report" / "report.md"
        generate_report(micro_run_set, out)
        pngs = sorted(p.name for p in out.parent.glob("speed_contour_*.png"))
        assert len(pngs) == 2
        text = out.read_text()
        for name in pngs:
            assert name in text

    def test_template_body_has_no_free_text_numerals(self):
        """CLAUDE.md §7.4: every number must come from computed context."""
        import validation.report as report_mod

        template = (Path(report_mod.__file__).parent / "templates" / "report.md.j2").read_text()
        # Strip jinja expressions/statements; no digits may remain.
        body = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", template, flags=re.S)
        assert not re.search(r"\d", body), "template body contains free-text numerals"

    def test_seeded_run_labeled_prominently(self, tmp_path: Path):
        root = tmp_path / "runs"
        _write_run(root / "a", seed=1, seeded=True)
        _write_run(root / "b", seed=2, seeded=False)
        out = tmp_path / "report.md"
        generate_report(root, out)
        text = out.read_text()
        assert "SEEDED RUNS INCLUDED" in text
        assert "seeded=True" in text
        # The seeded replicate is excluded from the emergent wave-speed input.
        assert "1 seeded replicate(s) excluded" in text

    def test_macro_only_run_set_refused(self, tmp_path: Path):
        root = tmp_path / "runs"
        _write_run(root / "a", seed=1, tier="screening", with_trajectories=False)
        _write_run(root / "b", seed=2, tier="macro", with_trajectories=False)
        with pytest.raises(ReportRefusedError, match="screening"):
            generate_report(root, tmp_path / "report.md")
        assert not (tmp_path / "report.md").exists()

    def test_mixed_tiers_report_micro_only_metrics(self, tmp_path: Path):
        root = tmp_path / "runs"
        _write_run(root / "a", seed=1)
        _write_run(root / "b", seed=2, tier="screening", with_trajectories=False)
        out = tmp_path / "report.md"
        generate_report(root, out)
        text = out.read_text()
        # The screening run appears in provenance but yields no contour.
        assert "screening" in text
        assert len(list(tmp_path.glob("speed_contour_*.png"))) == 1

    def test_empty_run_set_rejected(self, tmp_path: Path):
        (tmp_path / "runs").mkdir()
        with pytest.raises(ValueError, match="no runs"):
            generate_report(tmp_path / "runs", tmp_path / "report.md")

    def test_duplicate_seed_in_one_configuration_rejected(self, tmp_path: Path):
        root = tmp_path / "runs"
        _write_run(root / "a", seed=1)
        _write_run(root / "b", seed=1)
        with pytest.raises(ValueError, match="seed 1 more than once"):
            generate_report(root, tmp_path / "report.md")

    def test_custom_profile_name_recorded(self, micro_run_set: Path, tmp_path: Path):
        out = tmp_path / "report.md"
        generate_report(micro_run_set, out, profile=CriteriaProfile(name="txdot_variant"))
        assert "txdot_variant" in out.read_text()


class TestGroupLabel:
    def test_labels(self):
        assert group_label({}) == "baseline"
        assert group_label({"config": {"av": BASELINE_AV}}) == "baseline"
        # A controller with zero penetration controls nothing.
        assert group_label({"config": {"av": {**FS_AV, "penetration": 0.0}}}) == "baseline"
        assert group_label({"config": {"av": FS_AV}}) == "follower_stopper @ 5% / 100%"
        noisy = {
            **FS_AV,
            "controller": "jad",
            "compliance": 0.8,
            "oracle": {"kind": "noisy", "delay_s": 30.0, "amplitude_noise_frac": 0.2},
        }
        assert group_label({"config": {"av": noisy}}) == (
            "jad @ 5% / 80% (noisy oracle, delay 30 s, noise 20%)"
        )
        assert group_label({"config": {"av": {**BASELINE_AV, "vsl": "vsl_threshold"}}}) == (
            "VSL vsl_threshold"
        )


class TestContrast:
    def test_paired_hand_computed(self):
        base = {"1": 10.0, "2": 12.0, "3": 14.0}
        other = {"1": 9.0, "2": 10.0, "3": 11.0}  # deltas -1, -2, -3
        d = contrast(base, other)
        assert d.method == "paired"
        assert d.n == 3
        assert d.mean == pytest.approx(-2.0)
        half = student_t.ppf(0.975, 2) * 1.0 / math.sqrt(3)
        assert d.lo95 == pytest.approx(-2.0 - half)
        assert d.hi95 == pytest.approx(-2.0 + half)
        assert d.resolved is False  # interval straddles zero
        assert d.pct_of_baseline == pytest.approx(-100.0 * 2.0 / 12.0)

    def test_paired_resolved_when_interval_excludes_zero(self):
        base = {"1": 10.0, "2": 12.0, "3": 14.0}
        other = {"1": 9.0, "2": 11.0, "3": 13.0}  # every delta exactly -1
        d = contrast(base, other)
        assert d.method == "paired"
        assert (d.mean, d.lo95, d.hi95) == pytest.approx((-1.0, -1.0, -1.0))
        assert d.resolved is True

    def test_paired_drops_nan_pairs(self):
        base = {"1": 10.0, "2": float("nan"), "3": 14.0}
        other = {"1": 9.0, "2": 11.0, "3": 13.0}
        d = contrast(base, other)
        assert d.method == "paired"
        assert d.n == 2
        assert d.mean == pytest.approx(-1.0)

    def test_welch_hand_computed(self):
        base = {"1": 10.0, "2": 12.0}  # mean 11, var 2
        other = {"5": 9.0, "6": 9.0}  # mean 9, var 0
        d = contrast(base, other)
        assert d.method == "welch"
        assert d.n == 2
        assert d.mean == pytest.approx(-2.0)
        se = math.sqrt(2.0 / 2 + 0.0)
        df = se**4 / ((2.0 / 2) ** 2 / 1)
        half = student_t.ppf(0.975, df) * se
        assert d.lo95 == pytest.approx(-2.0 - half)
        assert d.hi95 == pytest.approx(-2.0 + half)
        assert d.resolved is False

    def test_undefined_interval_is_never_resolved(self):
        d = contrast({"1": 10.0}, {"1": 5.0})
        assert d.method == "paired" and d.n == 1
        assert math.isnan(d.lo95) and math.isnan(d.hi95)
        assert d.resolved is False
        empty = contrast({"1": float("nan")}, {"1": float("nan")})
        assert empty.n == 0 and math.isnan(empty.mean) and empty.resolved is False


class TestBaselineVersusController:
    def test_group_tables_and_paired_delta(self, two_group_run_set: Path, tmp_path: Path):
        out = tmp_path / "report" / "report.md"
        generate_report(two_group_run_set, out)
        text = out.read_text()

        # One metric table per configuration group, baseline first.
        base_head = f"### baseline (`{BASE_HASH}`)"
        ctrl_head = f"### follower_stopper @ 5% / 100% (`{CTRL_HASH}`)"
        assert base_head in text and ctrl_head in text
        assert text.index(base_head) < text.index(ctrl_head)
        assert text.count("| Metric | Mean | Lower | Upper | n | Underpowered |") == 2
        base_rows = _table_after(text, base_head)
        ctrl_rows = _table_after(text, ctrl_head)
        # Spatial σ_v equals the spread: baseline mean 5.2, controller 3.1.
        assert float(base_rows["sigma_v_spatial_ms"][1]) == pytest.approx(5.2)
        assert float(ctrl_rows["sigma_v_spatial_ms"][1]) == pytest.approx(3.1)
        assert base_rows["sigma_v_spatial_ms"][4] == "3"
        # Per-group replicate criterion (3 < 20).
        assert text.count("criterion (n_seeds >= 20): FAIL") == 2

        # Delta table: seed-paired, negative, resolved.
        assert "### Controller minus baseline" in text
        delta_head = "#### follower_stopper @ 5% / 100% vs baseline — seed-paired"
        assert delta_head in text
        delta = _table_after(text, delta_head)
        row = delta["sigma_v_spatial_ms"]
        mean, lo, hi, pct, n, resolved = row[1:]
        assert float(mean) == pytest.approx(-2.1)
        assert float(lo) < float(hi) < 0.0
        assert float(pct) == pytest.approx(-100.0 * 2.1 / 5.2, rel=1e-2)
        assert n == "3" and resolved == "yes"
        # Identical throughput in both groups: zero delta, not resolved.
        thr = delta["throughput_veh_h"]
        assert float(thr[1]) == 0.0 and thr[6] == "no"
        # Metrics undefined in both groups stay undefined and unresolved.
        assert delta["fuel_ml_per_veh_km"][1] == "NaN" and delta["fuel_ml_per_veh_km"][6] == "no"

        # The criteria note names the inputs' provenance.
        assert "Wave-speed criterion input: mean over the 3 unseeded replicate(s)" in text
        assert f"group baseline (`{BASE_HASH}`)" in text
        assert "Contrasts compare" not in text  # limitations bullet wording below
        assert "Controller-minus-baseline contrasts compare configurations" in text

    def test_paired_delta_is_exactly_per_seed_difference(
        self, two_group_run_set: Path, tmp_path: Path
    ):
        """Pairing by seed: the interval is that of the per-seed differences."""
        out = tmp_path / "report.md"
        generate_report(two_group_run_set, out)
        delta = _table_after(out.read_text(), "vs baseline — seed-paired")
        deltas = np.array([-2.0 - 0.05 * s for s in (1, 2, 3)])
        half = student_t.ppf(0.975, 2) * deltas.std(ddof=1) / math.sqrt(3)
        row = delta["sigma_v_spatial_ms"]
        assert float(row[2]) == pytest.approx(deltas.mean() - half, abs=5e-4)
        assert float(row[3]) == pytest.approx(deltas.mean() + half, abs=5e-4)

    def test_welch_when_seed_sets_differ(self, tmp_path: Path):
        root = _two_group_run_set(tmp_path / "runs", ctrl_seeds=(1, 2, 4))
        out = tmp_path / "report.md"
        generate_report(root, out)
        text = out.read_text()
        assert "vs baseline — Welch unequal-variance (seed sets differ)" in text
        delta = _table_after(text, "vs baseline — Welch")
        row = delta["sigma_v_spatial_ms"]
        assert float(row[1]) < 0.0
        assert float(row[2]) < float(row[3]) < 0.0 and row[6] == "yes"
        # Matched seeds 1 and 2 are paired figures; seed 3 (baseline only) and
        # seed 4 (controller only) fall back to single contours.
        pairs = sorted(p.name for p in tmp_path.glob("speed_contour_pair_*.png"))
        singles = sorted(p.name for p in tmp_path.glob("speed_contour_0*.png"))
        assert pairs == ["speed_contour_pair_01_seed_1.png", "speed_contour_pair_01_seed_2.png"]
        assert singles == ["speed_contour_00_seed_4.png", "speed_contour_01_seed_3.png"]
        for name in pairs + singles:
            assert name in text

    def test_paired_contour_figures_per_matched_seed(self, two_group_run_set: Path, tmp_path: Path):
        out = tmp_path / "report" / "report.md"
        generate_report(two_group_run_set, out)
        pngs = sorted(p.name for p in out.parent.glob("speed_contour_*.png"))
        assert pngs == [f"speed_contour_pair_01_seed_{s}.png" for s in (1, 2, 3)]
        text = out.read_text()
        assert "baseline (left) vs follower_stopper @ 5% / 100% (right)" in text

    def test_two_baselines_yield_no_contrast(self, tmp_path: Path):
        root = tmp_path / "runs"
        _write_run(root / "a" / "1", seed=1, config_hash="aaaa00000000", av=BASELINE_AV)
        _write_run(root / "b" / "1", seed=1, config_hash="bbbb00000000", av=BASELINE_AV)
        out = tmp_path / "report.md"
        generate_report(root, out)
        text = out.read_text()
        assert "### baseline [aaaa00000000]" in text  # disambiguated labels
        assert "### baseline [bbbb00000000]" in text
        assert "Controller minus baseline" not in text
        assert "2 baseline configuration(s) found" in text


class TestPdf:
    def test_pdf_written_beside_markdown(self, two_group_run_set: Path, tmp_path: Path):
        out = tmp_path / "report" / "report.md"
        md, pdf = generate_report(two_group_run_set, out, pdf=True)
        assert md == out
        assert pdf == out.with_name("report.pdf")
        assert pdf.is_file() and pdf.stat().st_size > 0
        assert pdf.read_bytes()[:4] == b"%PDF"

    def test_pdf_missing_dependency_names_the_extra(
        self, micro_run_set: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setitem(sys.modules, "fpdf", None)  # makes `import fpdf` fail
        with pytest.raises(RuntimeError, match=r"validation\[pdf\]"):
            generate_report(micro_run_set, tmp_path / "report.md", pdf=True)
        # The markdown is still the product; only the PDF rendering failed.
        assert (tmp_path / "report.md").is_file()

    def test_markdown_parser_covers_report_constructs(self):
        from validation.report_pdf import parse_markdown

        blocks = parse_markdown(
            "# Title\n\n> **banner**\n\nGenerated: now\n\n| A | B |\n|---|---|\n"
            "| `x` | 1 |\n\n- one\n  continued\n- two\n\n![cap](fig.png)\n"
        )
        kinds = [(b.kind, b.level) for b in blocks]
        assert kinds == [
            ("heading", 1),
            ("quote", 0),
            ("paragraph", 0),
            ("table", 0),
            ("bullets", 0),
            ("image", 0),
        ]
        assert blocks[3].rows == [["A", "B"], ["x", "1"]]
        assert blocks[4].items == ["one continued", "two"]
        assert blocks[5].path == "fig.png" and blocks[5].text == "cap"
