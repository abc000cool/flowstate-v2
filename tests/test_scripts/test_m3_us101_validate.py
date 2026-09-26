"""Tests for the wave criterion row of ``scripts/m3_us101_validate.py`` (WP-89).

The US-101 driver scores the ``wave_speed`` criteria row the way
``scripts/i24_validate.py`` does: with the criteria profile's own detector,
measured per replicate on the 640 m site-clipped field binned by that
detector, and named to ``validation.criteria.evaluate``. The standard 40 km/h
readings stay in the results as labelled diagnostics. Before this, the row
carried the standard reading under the profile detector's label with "caller
did not state which detector produced the value" (docs/PAPER_DRAFT.md
Appendix C item 18).

Synthetic ``sim``/``obs`` dicts and synthetic trajectory grids only: no
simulation, no NGSIM data.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from flowstate_core.units import kmh_to_ms
from validation.fields import speed_field

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


def _load() -> ModuleType:
    """Import ``scripts/m3_us101_validate.py`` by path (``--import-mode=importlib`` safe)."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "m3_us101_validate", SCRIPTS / "m3_us101_validate.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m3 = _load()

N_SEC, N_WIN = len(m3.SECTIONS_M), m3.N_WINDOWS
STANDARD_KMH = 6.06
"""The standard 40 km/h detector's diagnostic reading in the synthetic ``sim``
(the value the committed artifact's row carried)."""


def _obs() -> dict:
    counts = np.full((N_SEC, N_WIN), 100.0)
    return {
        "sections_m": list(m3.SECTIONS_M),
        "n_windows": N_WIN,
        "counts": counts.tolist(),
        "hourly_flows_veh_h": (counts * 3600.0 / m3.WINDOW_S).tolist(),
        "segment_speeds_ms": np.full((N_WIN, m3.N_SEGMENTS), 5.0).tolist(),
        "waves": {"count": 1, "mean_backward_speed_kmh": None},
        "waves_stripe": {"count": 3, "mean_backward_speed_kmh": 9.5},
        "waves_stripe_params": {
            "v_jam_thresh_kmh": m3.STRIPE_THRESH_KMH,
            "dt_bin_s": m3.STRIPE_DT_BIN_S,
            "dx_bin_m": m3.STRIPE_DX_BIN_M,
        },
        "waves_criterion": {
            "detector": m3.CRITERION_DETECTOR.name,
            "mean_backward_speed_kmh": None,
        },
        "boundary_source": "synthetic",
    }


def _reading(speed_kmh: float | None) -> dict:
    """One replicate's criterion-detector summary (the keys the aggregate reads)."""
    return {
        "detector": m3.CRITERION_DETECTOR.name,
        "count": 0,
        "backward_speeds_kmh": [] if speed_kmh is None else [speed_kmh],
        "mean_backward_speed_kmh": speed_kmh,
    }


def _sim(readings: list[float | None]) -> dict:
    """A ``micro_arm``-shaped block whose criterion value comes from ``readings``."""
    criterion = m3._aggregate_criterion_waves([_reading(r) for r in readings])
    counts = np.full((N_SEC, N_WIN), 100.0)
    return {
        "seeds": list(range(len(readings))),
        "config_hash": "synthetic",
        "counts_mean": counts.tolist(),
        "hourly_flows_veh_h_mean": (counts * 3600.0 / m3.WINDOW_S).tolist(),
        "segment_speeds_ms_mean": np.full((N_WIN, m3.N_SEGMENTS), 5.5).tolist(),
        "waves_per_replicate": [],
        "wave_count_mean": 2.15,
        "mean_backward_speed_kmh": STANDARD_KMH,
        "n_replicates_with_backward_waves": len(readings),
        "waves_stripe_per_replicate": [],
        "stripe_mean_backward_speed_kmh": 9.0,
        "n_replicates_with_stripe_backward_waves": len(readings),
        "criterion_detector": m3.CRITERION_DETECTOR.name,
        "criterion_wave_speed_kmh": criterion["mean_backward_speed_kmh"],
        "criterion_waves": criterion,
        "metrics_ci": {},
    }


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(name="us101_replica", network=SimpleNamespace(boundary=None))


def _wave_row(results: dict) -> dict:
    rows = [r for r in results["criteria"] if r["name"] == "wave_speed"]
    assert len(rows) == 1
    return rows[0]


class TestWaveCriterionRow:
    def test_row_is_the_criterion_detector_reading_and_names_it(self) -> None:
        sim = _sim([15.0, 17.0, None])
        results = m3.build_results("no_boundary", _cfg(), sim, _obs(), replicates=3)
        row = _wave_row(results)
        # Mean over the replicates with a backward reading, not the standard value.
        assert row["value"] == pytest.approx(16.0)
        assert row["evaluated"] is True
        assert row["passed"] is True
        assert m3.CRITERION_DETECTOR.describe() in row["detail"]
        assert "caller did not state" not in row["detail"]
        assert results["criteria_profile"] == m3.PROFILE.name
        waves = results["waves"]
        assert waves["criterion_detector"] == m3.CRITERION_DETECTOR.name
        assert waves["criterion_wave_speed_kmh"] == pytest.approx(16.0)
        assert waves["n_replicates_with_criterion_backward_waves"] == 2
        # The standard-detector statistics stay, as diagnostics.
        assert waves["simulated_mean_backward_speed_kmh"] == STANDARD_KMH
        assert "diagnostic" in waves["diagnostic_note"]
        assert set(waves["diagnostic_detectors"]) == {"standard", "stripe"}
        assert m3.RESULTS_SCHEMA_KEYS <= set(results)

    def test_no_backward_front_is_a_failing_nan_row(self) -> None:
        sim = _sim([None] * 20)
        assert sim["criterion_wave_speed_kmh"] is None
        results = m3.build_results("no_boundary", _cfg(), sim, _obs(), replicates=20)
        row = _wave_row(results)
        assert math.isnan(row["value"])
        assert row["evaluated"] is True
        assert row["passed"] is False
        assert "no backward wave detected" in row["detail"]
        assert m3.CRITERION_DETECTOR.describe() in row["detail"]
        assert "caller did not state" not in row["detail"]
        # The standard reading is not substituted for the missing criterion value.
        assert results["waves"]["simulated_mean_backward_speed_kmh"] == STANDARD_KMH
        assert results["waves"]["n_replicates_with_criterion_backward_waves"] == 0

    def test_a_standard_reading_is_named_and_not_scored(self) -> None:
        if m3.CRITERION_DETECTOR.name == "standard":
            pytest.skip("the profile's detector is the standard one")
        stated = _sim([STANDARD_KMH])
        stated["criterion_detector"] = "standard"
        legacy = {
            k: v
            for k, v in _sim([STANDARD_KMH]).items()
            if k not in {"criterion_detector", "criterion_wave_speed_kmh", "criterion_waves"}
        }
        for sim in (stated, legacy):
            row = _wave_row(m3.build_results("no_boundary", _cfg(), sim, _obs(), replicates=1))
            assert row["evaluated"] is False
            assert row["passed"] is False
            assert row["value"] == pytest.approx(STANDARD_KMH)
            assert "standard: jam = v < 40 km/h" in row["detail"]
            assert m3.CRITERION_DETECTOR.describe() in row["detail"]
            assert "caller did not state" not in row["detail"]

    def test_written_results_pass_verify_schema(self, tmp_path: Path) -> None:
        results = m3.build_results("no_boundary", _cfg(), _sim([None, 12.0]), _obs(), 2)
        path = tmp_path / "results_no_boundary.json"
        path.write_text(json.dumps(m3._json_safe(results), allow_nan=False))
        m3.verify_schema(path)


def _grid_traj(c_kmh: float, junk_kmh: float | None = None, seed: int = 3) -> pd.DataFrame:
    """1 Hz x 5 m samples over x in [-150, 800) m: aperiodic backward stripes
    (5 km/h in 30 km/h, 80 m wide) travelling at ``c_kmh`` on the site; with
    ``junk_kmh``, other stripes on free flow outside the 640 m site."""
    t = np.arange(0.0, 900.0, 1.0)
    x = np.arange(-150.0, 800.0, 5.0)
    tt, xx = np.meshgrid(t, x, indexing="ij")

    def stripes(c: float, s: int, width: float) -> np.ndarray:
        xi = xx + kmh_to_ms(c) * tt
        lead = np.cumsum(np.random.default_rng(s).uniform(180.0, 420.0, 200)) + xi.min() - 100.0
        idx = np.searchsorted(lead, xi, side="right") - 1
        return (idx >= 0) & (xi - lead[np.clip(idx, 0, None)] < width)

    v = np.where(stripes(c_kmh, seed, 80.0), kmh_to_ms(5.0), kmh_to_ms(30.0))
    if junk_kmh is not None:
        outside = (xx < 0.0) | (xx >= m3.SITE_LENGTH_M)
        junk = np.where(stripes(junk_kmh, seed + 1, 150.0), 0.0, kmh_to_ms(60.0))
        v = np.where(outside, junk, v)
    return pd.DataFrame({"t": tt.ravel(), "x": xx.ravel(), "v": v.ravel()})


class TestCriterionReading:
    def test_site_clipped_on_the_detectors_own_bins(self) -> None:
        det = m3.CRITERION_DETECTOR
        traj = _grid_traj(12.0, junk_kmh=35.0)
        summary = m3._criterion_wave_summary(traj)
        site = traj[(traj["x"] >= 0.0) & (traj["x"] < m3.SITE_LENGTH_M)]
        direct = det.measure(speed_field(site, dt_bin=det.dt_bin_s, dx_bin=det.dx_bin_m))
        assert summary["detector"] == det.name
        assert summary["detector_description"] == det.describe()
        assert math.isfinite(direct.speed_kmh)
        assert summary["mean_backward_speed_kmh"] == pytest.approx(direct.speed_kmh)
        # Samples outside the site do not reach the reading ...
        clean = m3._criterion_wave_summary(_grid_traj(12.0))
        assert clean["mean_backward_speed_kmh"] == pytest.approx(direct.speed_kmh)
        # ... although they would change it without the clip.
        unclipped = det.measure(speed_field(traj, dt_bin=det.dt_bin_s, dx_bin=det.dx_bin_m))
        assert not math.isclose(unclipped.speed_kmh, direct.speed_kmh, abs_tol=0.05)

    def test_flat_field_has_no_reading_and_aggregates_to_none(self) -> None:
        flat = _grid_traj(12.0).assign(v=20.0)
        summary = m3._criterion_wave_summary(flat)
        assert summary["mean_backward_speed_kmh"] is None
        assert summary["backward_speeds_kmh"] == []
        agg = m3._aggregate_criterion_waves([summary, summary])
        assert agg["mean_backward_speed_kmh"] is None
        assert agg["n_replicates_with_backward_waves"] == 0
        assert agg["n_replicates"] == 2
        striped = m3._criterion_wave_summary(_grid_traj(12.0))
        agg = m3._aggregate_criterion_waves([summary, striped])
        assert agg["mean_backward_speed_kmh"] == pytest.approx(striped["mean_backward_speed_kmh"])
        assert agg["n_replicates_with_backward_waves"] == 1
