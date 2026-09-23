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
from validation.waves import get_detector

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


#: Planted backward wave speed [km/h]; inside the acceptance band [14, 22].
PLANTED_WAVE_KMH = 16.0
_PLANTED_C_MS = PLANTED_WAVE_KMH / 3.6


def _wave_traj(
    t_end: float = 300.0,
    x_end: float = 1500.0,
    headway_s: float = 6.0,
    wavelength_m: float = 450.0,
    jam_frac: float = 0.3,
    v_free: float = 30.0,
    v_jam: float = 4.0,
) -> pd.DataFrame:
    """Trajectories whose speed field carries a planted backward wave.

    Vehicles are integrated forward through a speed field that is ``v_jam``
    inside a stripe of wavelength ``wavelength_m`` travelling upstream at
    :data:`PLANTED_WAVE_KMH` and ``v_free`` outside it, so the binned field
    the detectors see has a known front speed.
    """
    ts: list[float] = []
    xs: list[float] = []
    vs: list[float] = []
    ids: list[str] = []
    for k, t0 in enumerate(np.arange(0.0, t_end, headway_s)):
        t, x = float(t0), 0.0
        while t <= t_end and x <= x_end:
            phase = (x + _PLANTED_C_MS * t) % wavelength_m
            v = v_jam if phase < jam_frac * wavelength_m else v_free
            ts.append(t)
            xs.append(x)
            vs.append(v)
            ids.append(f"w{k}")
            x += v * 0.5
            t += 0.5
    return pd.DataFrame({"t": ts, "veh_id": ids, "x": xs, "v": vs})


def _free_traj(t_end: float = 300.0, x_end: float = 1500.0, headway_s: float = 6.0):
    """Uniform free flow: no front for any detector to find."""
    frames = []
    for k, t0 in enumerate(np.arange(0.0, t_end, headway_s)):
        t = np.arange(t0, t_end + 0.25, 0.5)
        x = 30.0 * (t - t0)
        keep = x <= x_end
        frames.append(pd.DataFrame({"t": t[keep], "veh_id": f"f{k}", "x": x[keep], "v": 30.0}))
    return pd.concat(frames, ignore_index=True)


def _write_traj_run(run_dir: Path, seed: int, traj: pd.DataFrame, config_hash: str) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    traj.to_parquet(run_dir / "trajectories.parquet")
    (run_dir / "meta.json").write_text(
        json.dumps(
            {
                "config_hash": config_hash,
                "seed": seed,
                "tier": "micro",
                "seeded": False,
                "versions": {"eclipse-sumo": "1.27.1"},
                "wall_time_s": 1.0,
                "config": {"av": BASELINE_AV},
            }
        )
    )
    return run_dir


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
        assert "replicate\ncriterion (n_seeds >= 20): FAIL" in text
        # A single-group report still states where the wave-speed value came
        # from: the printed number is never without its basis.
        assert re.search(
            r"Wave-speed criterion input: mean over \d+ of 2 unseeded replicate\(s\) "
            r"of group baseline \(`cafe01234567`\), measured with the stack detector",
            text,
        )
        # The replicate-criterion sentence stays a multi-group extra.
        assert "Replicate criterion input" not in text

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
        # Every section of the report is inside this one check, including the
        # strategy comparison, whose column headers and cells are numbers-in-
        # context and must therefore all arrive through the render context.
        for section in ("## Metrics", "### Controller minus baseline", "## Strategy comparison"):
            assert section in template
        # Strip jinja expressions/statements; no digits may remain.
        body = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", template, flags=re.S)
        assert not re.search(r"\d", body), "template body contains free-text numerals"
        # The comparison table's columns are named by the module, not the
        # template, so the two can never list different metrics.
        assert "comparison_headers" in template
        assert "| Configuration | " in template

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


class TestInsertionLine:
    """Provenance states how much of the configured demand actually ran."""

    @staticmethod
    def _add_insertion(run_dir: Path, planned: int, departed: int, arrived: int) -> None:
        meta = json.loads((run_dir / "meta.json").read_text())
        meta.update(
            {
                "n_vehicles_planned": planned,
                "n_vehicles_departed": departed,
                "n_vehicles_arrived": arrived,
                "ramps": [
                    {"name": "OH-ON", "kind": "on", "n_planned": 400, "n_departed": 40},
                    {"name": "BR-ON", "kind": "on", "n_planned": 400, "n_departed": 400},
                ],
            }
        )
        (run_dir / "meta.json").write_text(json.dumps(meta))

    def test_counters_are_rendered_with_the_starved_ramps(self, tmp_path: Path):
        root = tmp_path / "runs" / "cafe01234567"
        self._add_insertion(_write_run(root / "1", seed=1), 1000, 400, 300)
        self._add_insertion(_write_run(root / "2", seed=2), 1000, 600, 500)
        out = tmp_path / "report.md"
        generate_report(root.parent, out)
        line = next(ln for ln in out.read_text().splitlines() if ln.startswith("Insertion: "))
        # 2000 planned, 1000 departed, 800 arrived; mean fraction 0.5, worst 0.4.
        assert "2000 vehicles planned over 2 run(s)" in line
        assert "1000 departed" in line
        assert "800 arrived" in line
        assert "0.5" in line and "0.4" in line
        assert "backlog: 50 % of planned vehicles never departed" in line
        assert "OH-ON" in line and "BR-ON" not in line

    def test_runs_without_the_counters_get_no_line(self, micro_run_set: Path, tmp_path: Path):
        out = tmp_path / "report.md"
        generate_report(micro_run_set, out)
        assert "Insertion: " not in out.read_text()


class TestWaveSpeedCriterion:
    """The criterion must be scoreable: measured with the profile's detector."""

    def test_planted_wave_is_evaluated_and_passes(self, tmp_path: Path):
        root = tmp_path / "runs"
        for seed in (1, 2):
            _write_traj_run(root / "wave" / str(seed), seed, _wave_traj(), "wave00000001")
        out = tmp_path / "report.md"
        generate_report(root, out)
        text = out.read_text()
        row = _table_after(text, "## Acceptance criteria")["wave_speed"]
        value, _threshold, evaluated, result = row[1:]
        assert evaluated == "yes", "the profile's own detector must score its own row"
        assert result.startswith("PASS")
        assert float(value) == pytest.approx(PLANTED_WAVE_KMH, abs=1.5)
        assert "detector: stack" in result
        assert "measured with the stack detector on its own bins" in text

    def test_profile_detector_is_used_not_the_metrics_detector(self, tmp_path: Path):
        """A profile carrying a differently-binned detector still scores.

        ``WaveDetector.measure`` refuses a field binned by another recipe, so
        the field has to be rebuilt on the profile detector's own bins.
        """
        root = tmp_path / "runs"
        for seed in (1, 2):
            _write_traj_run(root / "wave" / str(seed), seed, _wave_traj(), "wave00000001")
        out = tmp_path / "report.md"
        generate_report(root, out, profile=CriteriaProfile(wave_detector=get_detector("stripe")))
        text = out.read_text()
        row = _table_after(text, "## Acceptance criteria")["wave_speed"]
        assert row[3] == "yes"
        assert "measured with the stripe detector" in text

    def test_replicates_without_a_front_are_named_not_hidden(self, tmp_path: Path):
        """The printed mean drops NaN replicates; the note must say so."""
        root = tmp_path / "runs"
        for seed in (1, 2):
            _write_traj_run(root / "mixed" / str(seed), seed, _wave_traj(), "mix000000001")
        for seed in (3, 4):
            _write_traj_run(root / "mixed" / str(seed), seed, _free_traj(), "mix000000001")
        out = tmp_path / "report.md"
        generate_report(root, out)
        text = out.read_text()
        assert "mean over 2 of 4 unseeded replicate(s)" in text
        assert "2 replicate(s) detected no backward front and are excluded from the mean" in text
        assert "underpowered, not a headline value" in text
        # The value is still the mean of the two that did resolve a front.
        row = _table_after(text, "## Acceptance criteria")["wave_speed"]
        assert float(row[1]) == pytest.approx(PLANTED_WAVE_KMH, abs=1.5)


class TestVersionProvenance:
    def test_every_run_version_listed_with_a_mismatch_warning(self, tmp_path: Path):
        root = tmp_path / "runs"
        _write_run(root / BASE_HASH / "1", seed=1, config_hash=BASE_HASH, av=BASELINE_AV)
        _write_run(root / BASE_HASH / "2", seed=2, config_hash=BASE_HASH, av=BASELINE_AV)
        ctrl = _write_run(root / CTRL_HASH / "1", seed=1, config_hash=CTRL_HASH, av=FS_AV)
        meta = json.loads((ctrl / "meta.json").read_text())
        meta["versions"] = {"eclipse-sumo": "1.19.0", "flowstate": "2.0.0-dev"}
        (ctrl / "meta.json").write_text(json.dumps(meta))
        out = tmp_path / "report.md"
        generate_report(root, out)
        text = out.read_text()
        # Both engine versions appear, each attributed to its runs.
        assert "`1.27.1` (" in text and "`1.19.0` (" in text
        assert f"{CTRL_HASH}/1" in text.split("### Calibration artifacts")[0]
        # A package every run agrees on stays a plain single value.
        assert "- flowstate: `2.0.0-dev`" in text
        # The warning appears in provenance and again over the contrast table.
        assert text.count("Version provenance is not uniform") == 2
        assert "not attributable to their configurations alone" in text

    def test_uniform_versions_carry_no_warning(self, two_group_run_set: Path, tmp_path: Path):
        out = tmp_path / "report.md"
        generate_report(two_group_run_set, out)
        text = out.read_text()
        assert "- eclipse-sumo: `1.27.1`" in text
        assert "Version provenance is not uniform" not in text

    def test_missing_version_block_is_reported(self, tmp_path: Path):
        root = tmp_path / "runs"
        _write_run(root / "1", seed=1)
        bare = _write_run(root / "2", seed=2)
        meta = json.loads((bare / "meta.json").read_text())
        del meta["versions"]
        (bare / "meta.json").write_text(json.dumps(meta))
        out = tmp_path / "report.md"
        generate_report(root, out)
        text = out.read_text()
        assert "no version metadata recorded for 2" in text


class TestMeasurementWindow:
    def test_warmup_is_stated_and_applied(self, tmp_path: Path):
        root = tmp_path / "runs"
        for seed in (1, 2):
            run = _write_run(root / "w" / str(seed), seed=seed)
            meta = json.loads((run / "meta.json").read_text())
            meta["config"] = {"av": BASELINE_AV, "sim": {"warmup_s": 40.0}}
            (run / "meta.json").write_text(json.dumps(meta))
        out = tmp_path / "report.md"
        generate_report(root, out, x_ref=1000.0)
        text = out.read_text()
        assert "warm-up per run, in seconds: 40" in text
        rows = _table_after(text, "### baseline")
        # The three constant-speed vehicles cross x = 1000 m at t = 33.3, 40
        # and 50 s: two of the three crossings fall inside the [40, 100] s
        # window, over which they are counted — the whole record would report
        # three crossings over 100 s instead.
        assert float(rows["throughput_veh_h"][1]) == pytest.approx(2.0 / 60.0 * 3600.0)

    def test_one_span_is_shared_across_groups(self, tmp_path: Path):
        """Groups that travel different distances share one exit bound."""
        root = tmp_path / "runs"
        for seed in (1, 2, 3):
            _write_run(
                root / BASE_HASH / str(seed), seed=seed, config_hash=BASE_HASH, av=BASELINE_AV
            )
            # The controlled group is slower: its own default span would be
            # shorter, and its travel times would not be comparable.
            slow = _write_run(
                root / CTRL_HASH / str(seed), seed=seed, config_hash=CTRL_HASH, av=FS_AV
            )
            traj = pd.read_parquet(slow / "trajectories.parquet")
            traj["x"] = traj["x"] * 0.5
            traj["v"] = traj["v"] * 0.5
            traj.to_parquet(slow / "trajectories.parquet")
        out = tmp_path / "report.md"
        generate_report(root, out)
        text = out.read_text()
        assert "one span for every group, derived from the reference group" in text
        # Baseline median furthest position: 2500 m (speeds 20/25/30 m/s).
        assert "measured over [0, 2500] m" in text
        base_rows = _table_after(text, f"### baseline (`{BASE_HASH}`)")
        ctrl_rows = _table_after(text, f"### follower_stopper @ 5% / 100% (`{CTRL_HASH}`)")
        # Half-speed vehicles reach at most 1500 m, so none completes the
        # shared span: the censoring is visible as a zero sample size, not
        # hidden in a travel time measured over a shorter corridor.
        assert float(base_rows["n_travel_time_veh"][1]) == 2.0
        assert float(ctrl_rows["n_travel_time_veh"][1]) == 0.0
        assert ctrl_rows["mean_tt_s"][1] == "NaN"


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

        # The criteria note names the inputs' provenance, including how many
        # of the unseeded replicates actually produced a reading.
        assert re.search(
            r"Wave-speed criterion input: mean over \d+ of 3 unseeded replicate\(s\)", text
        )
        assert f"group baseline (`{BASE_HASH}`)" in text
        assert "Replicate criterion input" in text
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
            # The strategy-comparison section: a level-2 heading and a table
            # whose cells carry an interval and a contrast. The PDF may not
            # reformat either of them — it renders the markdown, it does not
            # re-derive a number (§7.4).
            "\n## Strategy comparison\n\n| Configuration | Throughput [veh/h] |\n|---|---|\n"
            "| baseline (reference) | 1700 [1650, 1750] |\n"
            "| VSL vsl_threshold (Δ seed-paired) | 1780 [1740, 1820] · Δ 80 [40, 120] resolved |\n"
        )
        kinds = [(b.kind, b.level) for b in blocks]
        assert kinds == [
            ("heading", 1),
            ("quote", 0),
            ("paragraph", 0),
            ("table", 0),
            ("bullets", 0),
            ("image", 0),
            ("heading", 2),
            ("table", 0),
        ]
        assert blocks[3].rows == [["A", "B"], ["x", "1"]]
        assert blocks[4].items == ["one continued", "two"]
        assert blocks[5].path == "fig.png" and blocks[5].text == "cap"
        assert blocks[6].text == "Strategy comparison"
        assert blocks[7].rows == [
            ["Configuration", "Throughput [veh/h]"],
            ["baseline (reference)", "1700 [1650, 1750]"],
            ["VSL vsl_threshold (Δ seed-paired)", "1780 [1740, 1820] · Δ 80 [40, 120] resolved"],
        ]


class TestAggregationAndLabels:
    def test_speed_aggregation_rows_floor_and_shape(self):
        import numpy as np

        from validation.report import speed_aggregation_rows

        rng = np.random.default_rng(3)
        obs = 10.0 + rng.normal(0.0, 2.0, size=(24, 10))
        sim = obs + rng.normal(0.0, 1.0, size=(24, 10))
        rows = speed_aggregation_rows(obs, sim, 300.0)
        labels = [r["aggregation"] for r in rows]
        assert labels == ["5 min (criterion)", "15 min", "30 min", "60 min", "whole period"]
        native = float(rows[0]["rmspe"])
        assert float(rows[3]["rmspe"]) < native  # averaging shrinks the error
        assert rows[0]["floor"] and rows[1]["floor"] and rows[2]["floor"] == ""
        with pytest.raises(ValueError, match="share a 2-D shape"):
            speed_aggregation_rows(obs, sim[:, :5], 300.0)

    def test_group_labels_for_closures_heavy_managed_and_meters(self):
        from validation.report import BASELINE_LABEL, group_label

        base = {"config": {"av": {"penetration": 0.0}}}
        assert group_label(base) == BASELINE_LABEL
        closed = {"config": {"av": {}, "closures": [{"label": "work zone", "lanes": [0]}]}}
        assert group_label(closed) == "closure work zone"
        heavy = {"config": {"av": {}, "fleet": {"heavy": {"fraction": 0.092}}}}
        assert group_label(heavy) == "heavy 9.2%"
        hov = {"config": {"av": {}, "managed_lanes": [{"label": "", "lanes": [3]}]}}
        assert group_label(hov) == "managed lane lanes [3]"
        meter = {
            "config": {
                "av": {},
                "network": {
                    "ramps": [{"name": "OH", "meter": {"controller": "alinea"}, "merge": "zipper"}]
                },
            }
        }
        assert group_label(meter) == "ramp meter alinea on OH + merge zipper on OH"


class TestObservedDataBlock:
    """The observed-data provenance block and the two criteria rows it feeds."""

    @staticmethod
    def _provenance() -> Any:
        from validation.observed import ObservedProvenance

        return ObservedProvenance(
            path="artifacts/observations_test.json",
            corridor="test_corridor",
            provider="Test DOT archive",
            dates="20260915, 20260916",
            url="https://example.invalid/archive",
            aggregation="mean over dates per window",
            t0_local="06:00",
            window_s=300.0,
            n_stations=3,
            n_windows=24,
            n_windows_compared=22,
            flow_fraction=0.9861,
            speed_fraction=0.9861,
            n_link_hours=4,
            n_speed_cells=130,
            n_replicates=2,
        )

    def test_block_is_rendered_and_criteria_rows_are_evaluated(
        self, micro_run_set: Path, tmp_path: Path
    ):
        out = tmp_path / "report.md"
        generate_report(
            micro_run_set,
            out,
            geh_values=[1.0] * 9 + [12.0],  # 90% under 5 — above the 85% bound
            rmspe_value=0.11,
            observed=self._provenance(),
        )
        text = out.read_text()
        assert "### Observed data" in text
        assert "Test DOT archive" in text
        assert "20260915, 20260916" in text
        assert "artifacts/observations_test.json" in text
        assert "link-hour comparisons (pooled over replicates)" in text
        # Both criteria rows are scored, not "NOT EVALUATED".
        geh_row = next(line for line in text.splitlines() if line.startswith("| link_flows_geh"))
        assert "PASS" in geh_row
        rmspe_row = next(line for line in text.splitlines() if line.startswith("| speeds_rmspe"))
        assert "PASS" in rmspe_row
        assert "excluded from both" in text  # the limitations bullet

    def test_without_observations_the_block_is_absent_and_rows_not_evaluated(
        self, micro_run_set: Path, tmp_path: Path
    ):
        out = tmp_path / "report.md"
        generate_report(micro_run_set, out)
        text = out.read_text()
        assert "### Observed data" not in text
        geh_row = next(line for line in text.splitlines() if line.startswith("| link_flows_geh"))
        assert "NOT EVALUATED" in geh_row
        rmspe_row = next(line for line in text.splitlines() if line.startswith("| speeds_rmspe"))
        assert "NOT EVALUATED" in rmspe_row

    def test_empty_provenance_fields_are_dropped(self, micro_run_set: Path, tmp_path: Path):
        import dataclasses

        provenance = dataclasses.replace(self._provenance(), provider="", dates="", url="")
        out = tmp_path / "report.md"
        generate_report(micro_run_set, out, geh_values=[1.0], observed=provenance)
        text = out.read_text()
        assert "source provider" not in text
        assert "corridor | test_corridor" in text


class TestStrategyComparison:
    """The one table that puts every configuration side by side (§7.4).

    It introduces no statistic of its own: the means are the group
    aggregates already tabulated per configuration, the deltas the same
    ``contrast`` the per-metric contrast sections use. What it adds is the
    reading a deployment decision needs — VSL versus metering versus
    controlled vehicles, on the metrics that get quoted, on one page.
    """

    def test_rows_carry_means_and_paired_deltas_with_the_baseline_first(
        self, two_group_run_set: Path, tmp_path: Path
    ):
        from validation.report import COMPARISON_METRICS

        out = tmp_path / "report" / "report.md"
        generate_report(two_group_run_set, out)
        text = out.read_text()
        assert "## Strategy comparison" in text
        rows = _table_after(text, "## Strategy comparison")

        base_key = "baseline (reference)"
        ctrl_key = "follower_stopper @ 5% / 100% (Δ seed-paired)"
        assert list(rows) == ["Configuration", base_key, ctrl_key]  # baseline first
        assert rows["Configuration"][1:] == [header for _, header in COMPARISON_METRICS]
        assert [name for name, _ in COMPARISON_METRICS] == [
            "throughput_veh_h",
            "mean_tt_s",
            "sigma_v_temporal_ms",
            "fuel_ml_per_veh_km",
            "wave_count",
        ]

        # The reference row is means only — a group has no delta against itself.
        assert all("Δ" not in cell for cell in rows[base_key][1:])
        base_tt = re.fullmatch(r"(\S+) \[(\S+), (\S+)\]", rows[base_key][2])
        assert base_tt is not None

        # Every other cell is "mean [lo, hi] · Δ mean [lo, hi] <marker>".
        ctrl_tt = re.fullmatch(
            r"(\S+) \[(\S+), (\S+)\] · Δ (\S+) \[(\S+), (\S+)\] (resolved|unresolved)",
            rows[ctrl_key][2],
        )
        assert ctrl_tt is not None
        mean, lo, hi, d_mean, d_lo, d_hi = (float(g) for g in ctrl_tt.groups()[:6])
        marker = ctrl_tt.group(7)
        assert lo < mean < hi
        assert d_lo < d_mean < d_hi
        # The controller's travel time is longer by exactly the difference of
        # the two means, and the interval excludes zero.
        assert d_mean == pytest.approx(mean - float(base_tt.group(1)), abs=5e-3)
        assert d_lo > 0.0 and marker == "resolved"

        # A metric no replicate produced stays NaN in both halves of the cell.
        assert rows[ctrl_key][4].startswith("NaN [NaN, NaN] · Δ NaN")
        assert rows[ctrl_key][4].endswith("unresolved")

    def test_infrastructure_groups_are_named_by_their_strategy(self, tmp_path: Path):
        """A VSL deployment is a row of its own, labelled by what it deploys."""
        root = tmp_path / "runs"
        vsl_av = {**BASELINE_AV, "vsl": "vsl_threshold"}
        for seed in (1, 2):
            _write_run(
                root / BASE_HASH / str(seed),
                seed=seed,
                config_hash=BASE_HASH,
                av=BASELINE_AV,
                spread=5.0,
            )
            _write_run(
                root / "vsl000000001" / str(seed),
                seed=seed,
                config_hash="vsl000000001",
                av=vsl_av,
                spread=4.0,
            )
        out = tmp_path / "report.md"
        generate_report(root, out)
        rows = _table_after(out.read_text(), "## Strategy comparison")
        assert list(rows) == [
            "Configuration",
            "baseline (reference)",
            "VSL vsl_threshold (Δ seed-paired)",
        ]

    def test_single_configuration_has_no_comparison_table(
        self, micro_run_set: Path, tmp_path: Path
    ):
        """Nothing to compare: the section is omitted, not printed with one row."""
        out = tmp_path / "report.md"
        generate_report(micro_run_set, out)
        assert "## Strategy comparison" not in out.read_text()

    def test_without_a_single_baseline_the_table_carries_means_only(self, tmp_path: Path):
        """Two baselines: no unambiguous reference, so no Δ is invented."""
        root = tmp_path / "runs"
        _write_run(root / "a" / "1", seed=1, config_hash="aaaa00000000", av=BASELINE_AV)
        _write_run(root / "b" / "1", seed=1, config_hash="bbbb00000000", av=BASELINE_AV)
        out = tmp_path / "report.md"
        generate_report(root, out)
        text = out.read_text()
        rows = _table_after(text, "## Strategy comparison")
        assert len(rows) == 3  # header + two configurations
        assert all("Δ" not in cell for key in rows for cell in rows[key][1:])
        assert "no unambiguous reference to subtract" in text
