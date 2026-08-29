"""End-to-end tests for validation.report on synthetic run directories:
report generation, macro-only refusal (CLAUDE.md §5.6), and seeded labeling
(CLAUDE.md §0.2)."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from validation.criteria import CriteriaProfile
from validation.report import ReportRefusedError, generate_report

SPEEDS = (20.0, 25.0, 30.0)


def _traj() -> pd.DataFrame:
    frames = []
    t = np.arange(0.0, 100.0 + 0.25, 0.5)
    for i, v in enumerate(SPEEDS):
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
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    if with_trajectories:
        _traj().to_parquet(run_dir / "trajectories.parquet")
    meta = {
        "config_hash": "cafe01234567",
        "seed": seed,
        "tier": tier,
        "seeded": seeded,
        "versions": {"eclipse-sumo": "1.27.1", "flowstate": "2.0.0-dev"},
        "wall_time_s": 2.5,
        "calibration_artifacts": [{"path": "artifacts/fd_pems_d7.json", "data_hash": "deadbeef"}],
    }
    (run_dir / "meta.json").write_text(json.dumps(meta))
    return run_dir


@pytest.fixture()
def micro_run_set(tmp_path: Path) -> Path:
    root = tmp_path / "runs" / "cafe01234567"
    _write_run(root / "1", seed=1)
    _write_run(root / "2", seed=2)
    return tmp_path / "runs"


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
        import re

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

    def test_custom_profile_name_recorded(self, micro_run_set: Path, tmp_path: Path):
        out = tmp_path / "report.md"
        generate_report(micro_run_set, out, profile=CriteriaProfile(name="txdot_variant"))
        assert "txdot_variant" in out.read_text()
