"""ROADMAP §1.4 — validate the I-24 replica against the I-24 MOTION day.

Runs ``i24_replica`` (demand as tracked) and ``i24_replica_corrected``
(demand divided by the apparent tracking coverage, docs/I24_DATA.md §4) with
20 seeded replicates each and compares them with the westbound recording over
the study period (06:30–08:30 CST) on the measured span (data x ∈ [0, 5492) m,
MM 62.7 → Bell Road). Same structure as ``scripts/m3_us101_validate.py``:

* **Link flows** — fragment crossings at six high-coverage sections (data
  x = 200, 1000, 2200, 3200, 4800, 5400 m; the 400 m and 2400 m coverage
  holes are avoided) × 24 five-minute windows, ×12 to hourly volumes, GEH per
  bin against the replicate-mean simulated crossings. The observed counts are
  the tracked counts (lower bounds); the corrected arm is additionally scored
  against counts divided by the per-window coverage factor — both tables are
  written, both labeled.
* **Segment speeds** — RMSPE of replicate-mean simulated mean sampled speed
  against observed, 10 × 549 m segments × 24 windows (speeds are
  coverage-robust; one observed side serves both arms).
* **Waves** — every recipe in ``validation.waves.WAVE_DETECTORS`` (standard
  40 km/h on 15 s × 75 m bins, the 25 km/h / 10 s × 50 m stripe variant of
  the M3 analysis, relative 0.5 × p90, and the slant-stack estimate) on
  fields of the measured span, identically on both sides; the criteria row
  is measured with the profile's ``wave_detector`` and names it. This is the
  test of docs/WAVE_SPEED_DIAGNOSIS.md's prediction that a long, congested
  corridor lets the calibrated fleet reach the 14–22 km/h band.
* **Criteria** — ``validation.criteria.evaluate`` (GEH / RMSPE / wave speed /
  ring emergence / ring dampening / n_seeds) and replicate metrics with 95% t
  CIs. The ring rows are evaluated for real: ``--ring-seeds N`` (default 20)
  runs ``ring_sugiyama`` as shipped and with one FollowerStopper vehicle for
  ``spawn_seeds(<ring scenario seed>, N)`` through
  ``validation.ring_benchmark.evaluate_ring_benchmark`` — the CI gate's
  checks (tests/test_microsim/test_microsim_ring_gate.py) applied to every
  seed — and both arms' JSON carry the ``ring`` block. ``--ring-seeds 0``
  skips it (the rows then report not evaluated, failing, per CLAUDE.md §0.1);
  ``--ring-only`` evaluates the ring, writes
  ``runs/i24_validation/ring/ring_benchmark.json`` and exits.

Coordinates: sim x = a + b · data x (``artifacts/i24_replica_inputs.json``
``geometry.sim_x_of_data_x``), sim t = data t − 1800 + 600.

Outputs: ``runs/i24_validation/<arm>/`` run trees and
``artifacts/i24_validation_<arm>.json`` (schema of the M3 results files plus
the coverage tables). The scenario YAMLs used are copied next to them.

**An explicit scenario file** (``--scenario PATH --label L``, 2026-10-06, for
scenarios no family builds, e.g. the Amendment-1 calibrated
``scenarios/i24_replica_flow_speedcal_dc.yaml``): the same battery, observed
side and criteria rows, the run tree under ``runs/i24_validation<_family>/L/``
and the artifact ``artifacts/i24_validation<_family>_L.json``. ``L`` may not be
an arm's name (no committed arm artifact can be overwritten). Additively, the
artifact records the scenario file (path, sha256), every replicate's SUMO
collision count (``simulated.n_collisions_per_replicate``, the ``collisions``
block of ``validation.battery.collision_summary`` and ``zero_collisions``) and
scores the ``no_collisions`` criteria row from them; the committed observed
side (``artifacts/i24_validation_observed.json``) is not rewritten. The arms'
behaviour and outputs are unchanged.

**Locks** (2026-10-07, every battery, arms and explicit scenarios alike):
each replicate's permanent standstills are detected in the analysis worker
by :func:`validation.locks.detect_run_locks` — the corridor batteries'
reader — from the replicate's ``meta.json``, ``edges.parquet`` (the
space-time reader) and ``vehicles.parquet`` (the run-end reader), never its
trajectories. Additively, the artifact records each replicate's
:class:`validation.locks.RunLocks` record in seed order
(``simulated.locks_per_replicate``, the record the corridor battery keeps in
``per_seed[i].locks``) and, last, the corridor batteries' ``locks`` block
(:func:`validation.locks.lock_summary` labelled by seed) and ``zero_locks``
flag (:func:`validation.battery.lock_free`); the records score the
``no_locks`` criteria row (PASS / FAIL instead of NOT RECORDED), and
``--criteria-only`` re-scores it from the stored records. Every other key
and value is unchanged. Artifacts written before carry none of the three
keys, and their row stays NOT RECORDED (``scripts/i24_rescore_locks.py``
re-scores an archived run tree into a sidecar).

**Other mornings** (2026-10-07, docs/PRE_FRISCO_PROGRAM.md C9; additions only,
no other output changes). ``--observed-only PATH`` builds the observed side of
the active I-24 day (``I24_DAY_DIR`` / ``I24_T0_UNIX``, ``scripts/i24_data.py``;
the committed day without them) with that day's ``--coverage-artifact``, with
a ``day`` block naming the recording, and exits (:func:`day_observed_side`).
``--criteria-only --label L --rescore-observed DATE=PATH [...] --rescore-out
OUT`` (or one ``--arms A``) scores a stored battery's simulated side, the model
unchanged, against one such side or the mean of several per 5-min window
(docs/FRISCO_PROTOCOL.md §3.5): the three GEH tables, the RMSPE block and the
criteria rows exactly as the battery forms them, plus the protocol's C1, C3,
C4 and C5 read on that side, reported only (:func:`rescore_document`). The
battery artifact is never rewritten.

**Lane sets** (2026-10-07, docs/I24_CONSISTENCY_C7B.md §3; opt-in, the
defaults write what they wrote before). The observed count reads lanes 1-4;
the simulated count reads every lane of the section's edge, so at 1,000 m and
4,800 m (5-lane edges) it also counts the auxiliary lane. ``--lane-crossings``
records, per replicate and section, the crossings by lane (the SUMO lane of
the later sample; their sum is the all-lane count exactly) and by lane
transition (``simulated.lane_crossings``, :func:`lane_crossing_block`), and adds
``geh.lane_set``, the three tables with the simulated count on the observed
lane set (the four highest SUMO indices of the section's edge), beside the
row. ``--section-lanes observed`` scores the criteria row on that table
(``geh.primary`` ``recommended_lane_set``; ``--criteria-only`` keeps it).

Usage (repo root)::

    uv run --no-sync python scripts/i24_validate.py --replicates 2 --arms tracked   # smoke
    uv run --no-sync python scripts/i24_validate.py --procs 8                        # full
    uv run --no-sync python scripts/i24_validate.py --ring-only --ring-seeds 3       # ring rows
    uv run --no-sync python scripts/i24_validate.py --scenario scenarios/X.yaml --label x --procs 30
    I24_DAY_DIR=data/i24motion/processed/i24_wb_20221129 I24_T0_UNIX=1669723200 \
        uv run --no-sync python scripts/i24_validate.py --observed-only OBS.json \
        --coverage-artifact COVERAGE.json
    uv run --no-sync python scripts/i24_validate.py --criteria-only --label dc_refit_rc \
        --ring-seeds 0 --rescore-observed 2022-11-29=OBS.json --rescore-out OUT.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_build_replica import T_STUDY_HI_S, T_STUDY_LO_S, WARMUP_S, WINDOW_S, crossings_per_window
from i24_data import REPO_ROOT, active_day, clock, data_hash, load_mainline

from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from microsim.runner import _versions, run_replicates
from microsim.scenarios import load_scenario
from validation.baseline_gate import (
    GATE_WAVE_DETECTOR,
    SPEED_AGGREGATION_S,
    WAVE_MIN_FRONT_REPLICATE_SHARE,
)
from validation.battery import (
    collision_counts,
    collision_free,
    collision_summary,
    load_meta,
    lock_free,
)
from validation.criteria import CriteriaResult, evaluate, get_profile
from validation.fields import speed_field
from validation.locks import RunLocks, detect_run_locks, lock_summary
from validation.metrics import aggregate, ci, compute_metrics, geh, rmspe
from validation.report import speed_aggregation_rows
from validation.waves import WAVE_DETECTORS, WaveDetector, get_detector

OUT_ROOT = REPO_ROOT / "runs" / "i24_validation"
INPUTS = REPO_ROOT / "artifacts" / "i24_replica_inputs.json"

SECTIONS_M = (200.0, 1000.0, 2200.0, 3200.0, 4800.0, 5400.0)
N_SEGMENTS = 10
PROFILE = get_profile("fhwa_default")
CRITERION_DETECTOR = PROFILE.wave_detector
STANDARD = WAVE_DETECTORS["standard"]
STRIPE = WAVE_DETECTORS["stripe"]
OBSERVED_CACHE_VERSION = 3  # v3: + hourly flows at the recommended coverage
COVERAGE_ARTIFACT = REPO_ROOT / "artifacts" / "i24_coverage.json"

#: Lanes the observed count reads: 1-4 (``scripts/i24_data.MAINLINE_LANES``), the
#: four leftmost. In SUMO's numbering (0 = rightmost; the runner's acceleration and
#: weave lanes are lane 0 of their edge) they are the four highest indices of the
#: section's edge (docs/I24_CONSISTENCY_C7B.md §3).
OBSERVED_LANE_COUNT = 4
#: ``--section-lanes``: the link-flow row on every lane of the section's edge (the
#: battery as before) or on the observed lane set.
SECTION_LANES = ("all", "observed")
#: ``geh.primary`` of a battery scored on the observed lane set (``--section-lanes observed``).
LANE_SET_PRIMARY = "recommended_lane_set"
LANE_CROSSINGS_DEFINITION = (
    "per section and replicate, the crossings of crossings_per_window (consecutive samples of "
    "a vehicle with x_prev < x_s <= x_cur, in the window of the later sample) split by the "
    "SUMO lane index of the later sample (by_lane[lane][window]; its sum over lanes is the "
    "all-lane count exactly) and counted by (earlier sample's lane, later sample's lane) over "
    "the study period (transitions[prev][cur]); a section's n_lanes is one more than the "
    "largest lane index any replicate crossed it in; its lane_set (the observed lanes 1-4) is "
    "the four highest indices; a replicate's lane-set count is its by_lane summed over the "
    "lane set; into_set / out_of_set count the crossings whose two samples lie on either side "
    "of the lane set's edge (the only crossings a filter-then-cross count, the recording's "
    "rule, can count differently)"
)

ARMS = {
    "tracked": "i24_replica",
    "corrected": "i24_replica_corrected",
    "speedcal": "i24_replica_speedcal",  # FHWA step-2 demand scale (docs/I24_CAPACITY.md)
    "ramps": "i24_replica_speedcal_ramps",  # step 3: ramps, boundary, gap acceptance (§6.1)
    "speedcal_heavy": "i24_replica_speedcal_heavy",  # the fitted level with the heavy share
}
FAMILY = ""
"""Scenario family suffix (``--family``): scenario ``i24_replica<_family>...``,
artifacts ``i24_validation<_family>_<arm>.json``, runs under
``runs/i24_validation<_family>/``. Empty = the canonical family."""


def scenario_name(arm: str) -> str:
    """Scenario name of an arm in the active family."""
    base = ARMS[arm]
    return base.replace("i24_replica", f"i24_replica{FAMILY}", 1) if FAMILY else base


def artifact_path(arm: str) -> Path:
    """Validation artifact path of an arm (or an explicit scenario's label) in the active family."""
    return REPO_ROOT / "artifacts" / f"i24_validation{FAMILY}_{arm}.json"


def label_error(label: str) -> str | None:
    """Why ``label`` cannot name an explicit scenario's battery, else None.

    A label names the run tree and the artifact (:func:`artifact_path`), so an
    arm's name is refused: it would overwrite that arm's committed artifact.
    """
    if not re.fullmatch(r"[a-z0-9][a-z0-9_]*", label):
        return f"--label {label!r}: lower-case letters, digits and '_' only"
    if label in ARMS or label in ("ring", "all", "both"):
        return f"--label {label!r} is an arm's (or reserved) name; choose another"
    return None


def _inputs() -> dict:
    return json.loads(INPUTS.read_text())


def _span() -> tuple[float, float]:
    lo, hi = _inputs()["geometry"]["measured_span_data_x_m"]
    return float(lo), float(hi)


def _segment_speeds(traj: pd.DataFrame, span_hi: float, n_win: int) -> np.ndarray:
    """Mean sampled speed per (window, segment) on the measured span; NaN if empty."""
    seg_m = span_hi / N_SEGMENTS
    out = np.full((n_win, N_SEGMENTS), np.nan)
    t = traj["t"].to_numpy(dtype=np.float64)
    x = traj["x"].to_numpy(dtype=np.float64)
    v = traj["v"].to_numpy(dtype=np.float64)
    ok = (t >= 0.0) & (t < n_win * WINDOW_S) & (x >= 0.0) & (x < span_hi)
    wi = (t[ok] // WINDOW_S).astype(np.int64)
    si = np.minimum((x[ok] // seg_m).astype(np.int64), N_SEGMENTS - 1)
    sums = np.zeros((n_win, N_SEGMENTS))
    cnts = np.zeros((n_win, N_SEGMENTS))
    np.add.at(sums, (wi, si), v[ok])
    np.add.at(cnts, (wi, si), 1.0)
    np.divide(sums, cnts, out=out, where=cnts > 0)
    return out


def _wave_summary(traj: pd.DataFrame, span_hi: float, detector: WaveDetector) -> dict:
    """One detector's reading of the site-clipped field (its own bins)."""
    clipped = traj[(traj["x"] >= 0.0) & (traj["x"] < span_hi)]
    field = speed_field(clipped, dt_bin=detector.dt_bin_s, dx_bin=detector.dx_bin_m)
    m = detector.measure(field)
    bw = list(m.backward_speeds_kmh)
    amplitudes = (
        [round(w.amplitude_ms, 2) for w in detector.detect(field).waves]
        if detector.method != "stack"
        else []
    )
    return {
        "detector": detector.name,
        "detector_description": detector.describe(),
        "count": m.n_components,
        "n_backward": m.n_backward,
        "backward_speeds_kmh": [round(v, 2) for v in bw],
        "amplitudes_ms": amplitudes,
        "mean_backward_speed_kmh": m.speed_kmh if math.isfinite(m.speed_kmh) else None,
        "median_backward_speed_kmh": float(np.median(bw)) if bw else None,
        "frac_backward_in_band": m.in_band_fraction() if bw else None,
        "threshold_kmh": m.threshold_kmh if math.isfinite(m.threshold_kmh) else None,
        "stack_contrast": m.contrast if math.isfinite(m.contrast) else None,
        "note": m.note,
    }


def _wave_summaries(traj: pd.DataFrame, span_hi: float) -> dict[str, dict]:
    """Every registered detector's reading, keyed by detector name."""
    return {name: _wave_summary(traj, span_hi, d) for name, d in WAVE_DETECTORS.items()}


def _recommended_coverage(
    n_win: int, t_lo: float, artifact: Path = COVERAGE_ARTIFACT
) -> tuple[np.ndarray, str]:
    """Per-5-min-window tracking coverage from ``artifacts/i24_coverage.json``.

    The artifact's recommended estimator (``section_gap_mixture`` floored by
    the FD capacity bound; chosen on its synthetic validation, needs no
    car-following model) gives one value per 15-min window; each 5-min
    window inherits the 15-min window containing its start. ``artifact``
    names another morning's coverage artifact (``scripts/i24_coverage.py``
    run on that day; :func:`day_observed_side`).
    """
    cov = json.loads(artifact.read_text())
    win_s = float(cov["parameters"]["window_s"])
    rows = sorted(cov["windows"], key=lambda w: float(w["t_lo_s"]))
    out = np.empty(n_win)
    for i in range(n_win):
        t = t_lo + i * WINDOW_S
        row = next((w for w in rows if float(w["t_lo_s"]) <= t < float(w["t_lo_s"]) + win_s), None)
        if row is None:
            raise ValueError(f"no coverage window contains t={t}")
        out[i] = float(row["pooled"]["recommended_filled"])
    src = (
        f"{_rel_repo(artifact)}: {cov['recommendation']['rule']} "
        f"(pooled.recommended_filled per {win_s:g} s window)"
    )
    return out, src


def observed_side(cache_path: Path) -> dict:
    """Observed comparison tables on the study period / measured span (cached)."""
    dh = data_hash()
    if cache_path.is_file():
        cached = json.loads(cache_path.read_text())
        if cached.get("data_hash") == dh and cached.get("cache_version") == OBSERVED_CACHE_VERSION:
            print(f"observed side: cache hit ({cache_path})", flush=True)
            return cached
    obs = build_observed()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(_json_safe(obs), indent=2, allow_nan=False))
    return obs


def build_observed(
    coverage_artifact: Path = COVERAGE_ARTIFACT,
    apparent: tuple[np.ndarray, str] | None = None,
) -> dict:
    """The observed tables of the active I-24 day (``scripts/i24_data.py``), uncached.

    Args:
        coverage_artifact: The day's coverage artifact (its recommended
            estimator scales the counts of the criteria row's table).
        apparent: The apparent coverage per 5-min window behind the
            ``corrected`` table and its source; default the committed replica
            inputs' (the 30 Nov builder's equilibrium factors). Another day
            passes its own (:func:`apparent_coverage`), which is then recorded
            under ``coverage_factor_source``.
    """
    dh = data_hash()
    print("observed side: building from the I-24 Parquet ...", flush=True)
    t0 = time.perf_counter()
    inputs = _inputs()
    span_lo, span_hi = _span()
    t_lo, t_hi = T_STUDY_LO_S, T_STUDY_HI_S
    n_win = round((t_hi - t_lo) / WINDOW_S)
    df = load_mainline(
        t_range_s=(t_lo - 60.0, t_hi + 60.0),
        x_range_m=(span_lo - 200.0, span_hi + 200.0),
        columns=["t", "veh_id", "x", "v"],
    )
    counts = np.array([crossings_per_window(df, s, t_lo, t_hi) for s in SECTIONS_M])
    if apparent is None:
        cov_rows = inputs["coverage"]["rows"]
        cov_win = int(inputs["coverage"]["window_s"] // WINDOW_S)
        factors = np.array(
            [cov_rows[min(i // cov_win, len(cov_rows) - 1)]["coverage_used"] for i in range(n_win)]
        )
    else:
        factors = np.asarray(apparent[0], dtype=np.float64)
    rec, rec_src = _recommended_coverage(n_win, t_lo, coverage_artifact)
    study = df[(df["t"] >= t_lo) & (df["t"] < t_hi)].copy()
    study["t"] = study["t"] - t_lo
    seg = _segment_speeds(study, span_hi, n_win)
    waves_by_detector = _wave_summaries(study[["t", "x", "v"]], span_hi)
    obs = {
        "cache_version": OBSERVED_CACHE_VERSION,
        "data_hash": dh,
        "period": f"{clock(t_lo)}-{clock(t_hi)} CST",
        "t_range_s": [t_lo, t_hi],
        "span_data_x_m": [span_lo, span_hi],
        "sections_m": list(SECTIONS_M),
        "window_s": WINDOW_S,
        "n_windows": n_win,
        "n_segments": N_SEGMENTS,
        "segment_m": span_hi / N_SEGMENTS,
        "counts_tracked": counts.tolist(),
        "hourly_flows_veh_h_tracked": (counts * 3600.0 / WINDOW_S).tolist(),
        "coverage_factor_per_window": factors.round(4).tolist(),
        "hourly_flows_veh_h_corrected": (counts * 3600.0 / WINDOW_S / factors[None, :])
        .round(1)
        .tolist(),
        "coverage_recommended_per_window": rec.round(4).tolist(),
        "coverage_recommended_source": rec_src,
        "hourly_flows_veh_h_recommended": (counts * 3600.0 / WINDOW_S / rec[None, :])
        .round(1)
        .tolist(),
        "segment_speeds_ms": seg.tolist(),
        "waves": waves_by_detector[STANDARD.name],
        "waves_stripe": waves_by_detector[STRIPE.name],
        "waves_stripe_params": {
            "v_jam_thresh_kmh": STRIPE.v_jam_thresh_ms * 3.6,
            "dt_bin_s": STRIPE.dt_bin_s,
            "dx_bin_m": STRIPE.dx_bin_m,
        },
        "waves_by_detector": waves_by_detector,
        "n_fragments": int(study["veh_id"].nunique()),
        "wall_s": round(time.perf_counter() - t0, 1),
    }
    if apparent is not None:
        obs["coverage_factor_source"] = apparent[1]
    return obs


def apparent_coverage(coverage_artifact: Path, n_win: int, t_lo: float) -> tuple[np.ndarray, str]:
    """Another day's apparent coverage per 5-min window, from its coverage artifact.

    The artifact's ``equilibrium_pooled`` (the method of
    ``scripts/i24_build_replica.py::coverage_factors``, which built the committed
    day's ``corrected`` table) per 15-min window of the study period, clipped
    to [0.001, 1], undefined (free-flow) windows filled from the nearest
    defined one (forward, then backward), as the builder fills them; each
    5-min window inherits its 15-min window. Only the bounding ``corrected``
    table reads it; the criteria row reads the recommended estimator.

    Raises:
        ValueError: No window of the study period has a defined value.
    """
    cov = json.loads(coverage_artifact.read_text())
    win_s = float(cov["parameters"]["window_s"])
    t_hi = t_lo + n_win * WINDOW_S
    rows = sorted(
        (w for w in cov["windows"] if t_lo <= float(w["t_lo_s"]) < t_hi),
        key=lambda w: float(w["t_lo_s"]),
    )
    vals = []
    for w in rows:
        v = w["pooled"].get("equilibrium_pooled")
        ok = v is not None and math.isfinite(float(v))
        vals.append(min(max(float(v), 1e-3), 1.0) if ok else math.nan)
    filled = pd.Series(vals, dtype=float).ffill().bfill()
    if filled.isna().any() or not rows:
        raise ValueError(
            f"{_rel_repo(coverage_artifact)}: no study-period window has a defined equilibrium_pooled"
        )
    out = np.empty(n_win)
    for i in range(n_win):
        t = t_lo + i * WINDOW_S
        k = next(
            j for j, w in enumerate(rows) if float(w["t_lo_s"]) <= t < float(w["t_lo_s"]) + win_s
        )
        out[i] = float(filled.iloc[k])
    src = (
        f"{_rel_repo(coverage_artifact)}: pooled.equilibrium_pooled per {win_s:g} s window, clipped "
        "to [0.001, 1], undefined windows filled from the nearest (the rule of "
        "scripts/i24_build_replica.py::coverage_factors)"
    )
    return out, src


def day_observed_side(coverage_artifact: Path) -> dict:
    """The observed side of the active day for ``--observed-only`` (docs/PRE_FRISCO_PROGRAM.md C9).

    The battery's tables (:func:`build_observed`), on the day named by
    ``I24_DAY_DIR`` / ``I24_T0_UNIX`` (the committed day without them), scaled by
    that day's coverage artifact. The committed day keeps the replica inputs'
    apparent coverage, so its arrays reproduce ``artifacts/i24_validation_observed.json``;
    another day takes its own (:func:`apparent_coverage`). A ``day`` block,
    last, names the recording and the coverage artifact.
    """
    day = active_day()
    cov = json.loads(coverage_artifact.read_text())
    if cov.get("data_hash") != data_hash():
        raise ValueError(
            f"{_rel_repo(coverage_artifact)} was computed on data {str(cov.get('data_hash'))[:12]}, "
            f"not the active day's {data_hash()[:12]} ({day.date})"
        )
    unscaled = [
        w.get("window")
        for w in cov["windows"]
        if T_STUDY_LO_S <= float(w["t_lo_s"]) < T_STUDY_HI_S
        and w["pooled"].get("recommended_filled") is None
    ]
    if unscaled:
        raise ValueError(
            f"{_rel_repo(coverage_artifact)} has no recommended coverage for {unscaled} (no window "
            "with a section estimate): this morning's counts cannot be scaled (C9 stop rule: replace it)"
        )
    n_win = round((T_STUDY_HI_S - T_STUDY_LO_S) / WINDOW_S)
    apparent = None if day.is_default else apparent_coverage(coverage_artifact, n_win, T_STUDY_LO_S)
    obs = build_observed(coverage_artifact, apparent)
    obs["day"] = {
        "date": day.date,
        "source": day.source_label(),
        "day_dir": _rel_repo(day.day_dir),
        "t0_unix": day.t0_unix,
        "coverage_artifact": {
            "path": _rel_repo(coverage_artifact),
            "sha256": hashlib.sha256(coverage_artifact.read_bytes()).hexdigest(),
        },
        "apparent_coverage": (
            "the committed replica inputs' (artifacts/i24_replica_inputs.json coverage.rows)"
            if apparent is None
            else apparent[1]
        ),
    }
    return obs


def section_lane_crossings(
    df: pd.DataFrame, sections_m: Sequence[float], t_lo: float, t_hi: float
) -> list[dict[str, list[list[int]]]]:
    """Each section's crossings by lane (``LANE_CROSSINGS_DEFINITION``), one sort for all.

    The crossing rule is :func:`crossings_per_window`'s exactly, so a section's
    ``by_lane`` summed over lanes is that function's count window for window.

    Args:
        df: One replicate's frame with ``t, veh_id, x, lane`` (study time, data x).
        sections_m: Sections, data x [m].
        t_lo: Start of the counted period [s].
        t_hi: End of the counted period [s].

    Returns:
        Per section: ``by_lane`` (``[lane][window]``, lanes ``0 .. n - 1`` with ``n``
        one more than the largest index crossed in) and ``transitions``
        (``[earlier sample's lane][later sample's lane]`` over the period, square,
        as large as the largest index of either sample).
    """
    df = df.sort_values(["veh_id", "t"], kind="stable")
    vid = df["veh_id"].to_numpy()
    same = vid[1:] == vid[:-1]
    x = df["x"].to_numpy()
    t = df["t"].to_numpy()
    lane = df["lane"].to_numpy().astype(np.int64)
    x_prev, x_cur, t_cur = x[:-1][same], x[1:][same], t[1:][same]
    l_prev, l_cur = lane[:-1][same], lane[1:][same]
    n_win = round((t_hi - t_lo) / WINDOW_S)
    out = []
    for x_s in sections_m:
        hit = (x_prev < x_s) & (x_cur >= x_s) & (t_cur >= t_lo) & (t_cur < t_hi)
        w = ((t_cur[hit] - t_lo) // WINDOW_S).astype(np.int64)
        lc, lp = l_cur[hit], l_prev[hit]
        if lc.size and min(int(lc.min()), int(lp.min())) < 0:
            raise ValueError(f"section {x_s}: a negative lane index")
        n_lanes = int(lc.max()) + 1 if lc.size else 0
        n_trans = max(n_lanes, int(lp.max()) + 1 if lp.size else 0)
        by_lane = np.zeros((n_lanes, n_win), dtype=np.int64)
        np.add.at(by_lane, (lc, w), 1)
        trans = np.zeros((n_trans, n_trans), dtype=np.int64)
        np.add.at(trans, (lp, lc), 1)
        out.append({"by_lane": by_lane.tolist(), "transitions": trans.tolist()})
    return out


def lane_set(n_lanes: int) -> list[int]:
    """The SUMO lane indices of the observed lanes 1-4 on an edge of ``n_lanes`` lanes."""
    return list(range(max(n_lanes - OBSERVED_LANE_COUNT, 0), n_lanes))


def lane_crossing_block(
    per_replicate: Sequence[Sequence[Mapping[str, Any]]],
    counts_per_replicate: Sequence[Any],
    sections_m: Sequence[float],
    n_win: int,
) -> dict[str, Any]:
    """The battery's ``simulated.lane_crossings`` block (``--lane-crossings``).

    Args:
        per_replicate: Per replicate, :func:`section_lane_crossings`' output.
        counts_per_replicate: The battery's all-lane counts ``[replicate][section][window]``.
        sections_m: Sections, data x [m].
        n_win: Windows of the study period.
    """
    n_rep = len(per_replicate)
    sections = []
    lane_set_counts = np.zeros((n_rep, len(sections_m), n_win), dtype=np.int64)
    sums_equal = True
    for si, x in enumerate(sections_m):
        per = [rep[si] for rep in per_replicate]
        n_lanes_rep = [len(p["by_lane"]) for p in per]
        n_lanes = max(n_lanes_rep, default=0)
        keep = lane_set(n_lanes)
        into = out = aux = 0
        for ri, p in enumerate(per):
            by_lane = np.asarray(p["by_lane"], dtype=np.int64).reshape(-1, n_win)
            if not np.array_equal(
                by_lane.sum(axis=0), np.asarray(counts_per_replicate[ri][si], dtype=np.int64)
            ):
                sums_equal = False
            lane_set_counts[ri, si] = by_lane[[k for k in keep if k < len(by_lane)]].sum(axis=0)
            aux += int(by_lane[[k for k in range(len(by_lane)) if k not in keep]].sum())
            trans = np.asarray(p["transitions"], dtype=np.int64)
            for prev in range(len(trans)):
                for cur in range(len(trans)):
                    if (prev in keep) != (cur in keep):
                        if cur in keep:
                            into += int(trans[prev][cur])
                        else:
                            out += int(trans[prev][cur])
        sections.append(
            {
                "section_m": float(x),
                "n_lanes": n_lanes,
                "n_lanes_per_replicate": n_lanes_rep,
                "lane_set": keep,
                "crossings_outside_lane_set_total": aux,
                "into_set_total": into,
                "out_of_set_total": out,
            }
        )
    mean = lane_set_counts.mean(axis=0) if n_rep else np.zeros((len(sections_m), n_win))
    return {
        "definition": LANE_CROSSINGS_DEFINITION,
        "lane_numbering": "SUMO lane index on the section's edge, 0 = rightmost (the runner's "
        "acceleration and weave lanes are lane 0 of their edge)",
        "observed_lanes": f"1-{OBSERVED_LANE_COUNT} (scripts/i24_data.MAINLINE_LANES), the four "
        "leftmost: the lane_set",
        "sections": sections,
        "sums_equal_counts_per_replicate": sums_equal,
        "per_replicate": [list(rep) for rep in per_replicate],
        "counts_per_replicate_lane_set": lane_set_counts.tolist(),
        "counts_mean_lane_set": mean.tolist(),
        "hourly_flows_veh_h_mean_lane_set": (mean * 3600.0 / WINDOW_S).tolist(),
    }


def _sim_frame(run_dir: Path, a: float, b: float) -> pd.DataFrame:
    """One replicate's trajectories in observed coordinates (data x, study t)."""
    df = pd.read_parquet(run_dir / "trajectories.parquet")
    df["x"] = (df["x"] - a) / b
    df["t"] = df["t"] - WARMUP_S
    return df[df["t"] >= 0.0]


def replicate_locks(run_dir: Path, meta: Mapping[str, Any]) -> dict[str, Any]:
    """One replicate's lock record (:meth:`validation.locks.RunLocks.to_dict`).

    The corridor batteries' reader, :func:`validation.locks.detect_run_locks`,
    on the replicate's ``edges.parquet`` and ``vehicles.parquet`` with its
    parsed ``meta.json``; a replicate with neither table is not recorded
    (``locked`` null), never unlocked.
    """
    return detect_run_locks(run_dir, meta=meta).to_dict()


def stored_lock_records(sim: Mapping[str, Any]) -> list[RunLocks | None] | None:
    """The per-replicate lock records an artifact's ``simulated`` block stores.

    Args:
        sim: The ``simulated`` block (:func:`micro_arm`'s result).

    Returns:
        One :class:`validation.locks.RunLocks` per replicate in seed order
        (None for an entry stored as null), or None when the block predates
        ``locks_per_replicate`` (the ``no_locks`` row then reads NOT RECORDED).
    """
    stored = sim.get("locks_per_replicate")
    if not isinstance(stored, list):
        return None
    return [RunLocks.from_dict(r) if isinstance(r, dict) else None for r in stored]


def add_lock_blocks(
    results: dict[str, Any], lock_records: Sequence[RunLocks | None] | None, seeds: Sequence[int]
) -> None:
    """Append the corridor batteries' ``locks`` and ``zero_locks`` keys to an artifact.

    ``locks`` is :func:`validation.locks.lock_summary` labelled by seed (null
    when no replicate is recorded) and ``zero_locks``
    :func:`validation.battery.lock_free` (true only when every replicate is
    completely recorded and none locked, false on any lock, null otherwise);
    both null without records. Added last, so every existing key keeps its
    place and value.
    """
    results["locks"] = (
        None if lock_records is None else lock_summary(lock_records, labels=list(seeds))
    )
    results["zero_locks"] = None if lock_records is None else lock_free(lock_records)


def lock_console_line(results: Mapping[str, Any]) -> str:
    """The console line for an artifact's ``locks`` block (beside the collisions line).

    The corridor battery's ``locks`` line (``scripts/corridor_battery.lock_line``)
    plus the locked seeds.
    """
    locks = results.get("locks")
    text = "    locks              "
    if locks is None:
        return text + "not recorded (no replicate has edges.parquet or vehicles.parquet)"
    share = locks["share_locked"]
    text += (
        f"{locks['n_runs_locked']} of {locks['n_runs_recorded']} replicate(s) locked "
        f"({100.0 * share['value']:.0f} %, 95 % CI {100.0 * share['lo95']:.0f}–"
        f"{100.0 * share['hi95']:.0f} %)"
    )
    if locks["runs_not_recorded"]:
        text += f"; not recorded for {len(locks['runs_not_recorded'])} replicate(s)"
    locked = {row["run"] for row in locks["runs_locked"]}
    partial = [r for r in locks.get("runs_partially_recorded") or [] if r not in locked]
    if partial:
        text += (
            f"; no lock established for {len(partial)} replicate(s) read at the run's end only "
            "(no edges.parquet)"
        )
    seeded = locks.get("seeded_standstills") or []
    if seeded:
        text += f"; {len(seeded)} seeded standstill(s) not counted"
    places = [
        f"{row['section']} ({row['n_runs']}, onset {row['onset_s_min']:.0f}"
        + (f"–{row['onset_s_max']:.0f}" if row["onset_s_max"] != row["onset_s_min"] else "")
        + " s)"
        for row in locks["by_section"]
    ]
    if places:
        text += "; at " + ", ".join(places)
    if locks["runs_locked"]:
        text += "; seeds " + ", ".join(str(row["run"]) for row in locks["runs_locked"])
    return text


def _analyze_replicate(payload: tuple[Any, ...]) -> dict:
    """Per-replicate comparison tables + metrics (process-pool worker).

    ``payload`` is ``(run_dir, a, b, span_lo, span_hi, n_win)``; a seventh item
    ``True`` (``--lane-crossings``) adds the replicate's ``lane_crossings``
    (:func:`section_lane_crossings`) last.
    """
    run_dir_s, a, b, span_lo, span_hi, n_win = payload[:6]
    with_lanes = len(payload) > 6 and bool(payload[6])
    from dataclasses import asdict as _asdict

    run_dir = Path(run_dir_s)
    meta = json.loads((run_dir / "meta.json").read_text())
    df = _sim_frame(run_dir, a, b)
    out = {
        # the replicate's locks, read from its meta.json, edges.parquet and
        # vehicles.parquet (never the trajectories; module docstring)
        "locks": replicate_locks(run_dir, meta),
        "realized": meta["n_vehicles_departed"] / meta["n_vehicles_planned"],
        "ramps": meta.get("ramps"),
        "counts": np.array(
            [crossings_per_window(df, s, 0.0, n_win * WINDOW_S) for s in SECTIONS_M]
        ).tolist(),
        "seg": _segment_speeds(df, span_hi, n_win).tolist(),
        "waves_by_detector": _wave_summaries(df[["t", "x", "v"]], span_hi),
        "metrics": _asdict(
            compute_metrics(run_dir, x_ref=a + b * 2200.0, span=(a + b * span_lo, a + b * span_hi))
        ),
    }
    if with_lanes:
        out["lane_crossings"] = section_lane_crossings(df, SECTIONS_M, 0.0, n_win * WINDOW_S)
    return out


def _existing_replicates(cfg: ScenarioConfig, out_root: Path, seeds: list[int]) -> list | None:
    """RunPaths for replicates already on disk (all seeds complete), else None."""
    from microsim.runner import RunPaths

    root = out_root / config_hash(cfg)
    paths = []
    for seed in seeds:
        d = root / str(seed)
        if not (d / "meta.json").is_file() or not (d / "trajectories.parquet").is_file():
            return None
        paths.append(
            RunPaths(
                run_dir=d,
                trajectories=d / "trajectories.parquet",
                edges=d / "edges.parquet",
                meta=d / "meta.json",
            )
        )
    return paths


def micro_arm(
    cfg: ScenarioConfig,
    n_replicates: int,
    out_root: Path,
    procs: int,
    obs: dict,
    analysis_procs: int = 6,
    reuse_runs: bool = False,
    lane_crossings: bool = False,
) -> dict:
    """Run one arm's replicates (or reuse complete ones on disk), then analyse them.

    ``lane_crossings`` (``--lane-crossings``) adds ``lane_crossings``
    (:func:`lane_crossing_block`) last; every other key is the same either way.
    """
    from validation.metrics import Metrics

    inputs = _inputs()
    a, b = inputs["geometry"]["sim_x_of_data_x"]["a"], inputs["geometry"]["sim_x_of_data_x"]["b"]
    span_lo, span_hi = _span()
    n_win = obs["n_windows"]
    cfg = cfg.model_copy(update={"replicates": n_replicates})
    seeds = spawn_seeds(cfg.seed, n_replicates)
    t0 = time.perf_counter()
    paths = _existing_replicates(cfg, out_root, seeds) if reuse_runs else None
    if paths is None:
        paths = run_replicates(cfg, out_root, n_procs=min(procs, n_replicates))
    else:
        print(f"  reusing {len(paths)} complete replicates under {out_root}", flush=True)
    wall = time.perf_counter() - t0
    payloads: list[tuple[Any, ...]] = [
        (str(p.run_dir), a, b, span_lo, span_hi, n_win, *((True,) if lane_crossings else ()))
        for p in paths
    ]
    if analysis_procs > 1:
        import multiprocessing as mp

        with mp.get_context("spawn").Pool(min(analysis_procs, len(payloads))) as pool:
            analyses = pool.map(_analyze_replicate, payloads)
    else:
        analyses = [_analyze_replicate(pl) for pl in payloads]
    counts = [np.asarray(r["counts"]) for r in analyses]
    seg_speeds = [np.asarray(r["seg"]) for r in analyses]
    waves_by_detector = {
        name: [r["waves_by_detector"][name] for r in analyses] for name in WAVE_DETECTORS
    }
    waves = waves_by_detector[STANDARD.name]
    waves_stripe = waves_by_detector[STRIPE.name]
    metrics_list = [Metrics(**r["metrics"]) for r in analyses]
    realized = [r["realized"] for r in analyses]
    ramps = [r["ramps"] for r in analyses]
    print(
        f"  analysis of {len(paths)} replicates done ({time.perf_counter() - t0 - wall:.0f} s)",
        flush=True,
    )
    counts_arr = np.asarray(counts, dtype=np.float64)
    seg_arr = np.asarray(seg_speeds, dtype=np.float64)
    wave_speed_by_detector = {
        name: _aggregate_wave_summaries(name, summaries)
        for name, summaries in waves_by_detector.items()
    }
    criterion = wave_speed_by_detector[CRITERION_DETECTOR.name]
    bw = [w["mean_backward_speed_kmh"] for w in waves if w["mean_backward_speed_kmh"] is not None]
    bws = [
        w["mean_backward_speed_kmh"]
        for w in waves_stripe
        if w["mean_backward_speed_kmh"] is not None
    ]
    metrics_ci = {
        name: {
            "mean": ci.mean,
            "lo95": ci.lo95,
            "hi95": ci.hi95,
            "n": ci.n,
            "underpowered": ci.underpowered,
        }
        for name, ci in aggregate(metrics_list).items()
    }
    sim = {
        "config_hash": config_hash(cfg),
        "seeds": seeds,
        "run_dirs": [str(p.run_dir) for p in paths],
        "wall_s": round(wall, 1),
        "demand_realized_fraction": [round(f, 4) for f in realized],
        "ramps_per_replicate": ramps,
        "counts_mean": counts_arr.mean(axis=0).tolist(),
        "hourly_flows_veh_h_mean": (counts_arr.mean(axis=0) * 3600.0 / WINDOW_S).tolist(),
        "counts_per_replicate": [c.tolist() for c in counts],
        "segment_speeds_ms_mean": np.nanmean(seg_arr, axis=0).tolist(),
        "segment_speeds_ms_per_replicate": [s.tolist() for s in seg_speeds],
        "waves_per_replicate": waves,
        "wave_count_mean": float(np.mean([w["count"] for w in waves])),
        "mean_backward_speed_kmh": float(np.mean(bw)) if bw else None,
        "n_replicates_with_backward_waves": len(bw),
        "all_backward_speeds_kmh": [v for w in waves for v in w["backward_speeds_kmh"]],
        "waves_stripe_per_replicate": waves_stripe,
        "stripe_mean_backward_speed_kmh": float(np.mean(bws)) if bws else None,
        "n_replicates_with_stripe_backward_waves": len(bws),
        "wave_speed_by_detector": wave_speed_by_detector,
        "criterion_detector": CRITERION_DETECTOR.name,
        "criterion_wave_speed_kmh": criterion["mean_backward_speed_kmh"],
        "metrics_ci": metrics_ci,
        # each replicate's RunLocks record in seed order (module docstring, "Locks")
        "locks_per_replicate": [r["locks"] for r in analyses],
    }
    if lane_crossings:  # module docstring, "Lane sets"
        sim["lane_crossings"] = lane_crossing_block(
            [r["lane_crossings"] for r in analyses], sim["counts_per_replicate"], SECTIONS_M, n_win
        )
    return sim


def _aggregate_wave_summaries(name: str, summaries: list[dict]) -> dict:
    """Replicate aggregate of one detector's per-replicate readings."""
    bw = [
        w["mean_backward_speed_kmh"] for w in summaries if w["mean_backward_speed_kmh"] is not None
    ]
    return {
        "detector": name,
        "detector_description": WAVE_DETECTORS[name].describe(),
        "mean_backward_speed_kmh": float(np.mean(bw)) if bw else None,
        "median_backward_speed_kmh": float(np.median(bw)) if bw else None,
        "n_replicates_with_backward_waves": len(bw),
        "wave_count_mean": float(np.mean([w["count"] for w in summaries])),
        "all_backward_speeds_kmh": [v for w in summaries for v in w["backward_speeds_kmh"]],
        "per_replicate": summaries,
    }


def _json_safe(obj: object) -> object:
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, np.floating | np.integer):
        return _json_safe(obj.item())
    return obj


GEH_PRIMARY_RULE = (
    "every arm's link-flow row is scored against the tracked crossings divided by the "
    "coverage artifact's recommended estimator (artifacts/i24_coverage.json, "
    "section_gap_mixture floored by the FD capacity bound, chosen on synthetic validation "
    "and independent of the car-following model); the tracked and apparent-coverage tables "
    "are reported as the lower and upper bounds"
)


def _rmspe_block(value: float, n_bins: int, sim: dict, obs_seg: np.ndarray) -> dict:
    """RMSPE of the replicate mean plus, when per-replicate fields exist, the
    single-realisation numbers: each replicate against the observed day and
    each replicate against the mean of the other replicates (the model's own
    realisation-to-ensemble distance, a floor for a 5-min-bin comparison of
    one recorded day with an ensemble mean)."""
    block: dict = {"value": float(value), "n_bins": int(n_bins)}
    per = sim.get("segment_speeds_ms_per_replicate")
    if not per:
        return block
    arr = np.asarray(per, dtype=np.float64)
    vs_obs, loo = [], []
    for i in range(arr.shape[0]):
        si = arr[i]
        ok = np.isfinite(si) & np.isfinite(obs_seg) & (obs_seg != 0.0)
        vs_obs.append(float(rmspe(si[ok], obs_seg[ok])) if ok.any() else float("nan"))
        if arr.shape[0] < 2:
            continue  # the leave-one-out floor needs at least two replicates
        others = np.nanmean(np.delete(arr, i, axis=0), axis=0)
        ok2 = np.isfinite(si) & np.isfinite(others) & (others != 0.0)
        loo.append(float(rmspe(si[ok2], others[ok2])) if ok2.any() else float("nan"))
    block.update(
        {
            "per_replicate_vs_observed": [round(v, 4) for v in vs_obs],
            "per_replicate_vs_observed_mean": round(float(np.mean(vs_obs)), 4),
            "leave_one_out_floor": [round(v, 4) for v in loo],
            "leave_one_out_floor_mean": round(float(np.nanmean(loo)), 4) if loo else None,
            "definition": "value: replicate-mean field vs observed; per_replicate_vs_observed: one seed vs observed; leave_one_out_floor: one seed vs the mean of the other seeds",
        }
    )
    return block


def _geh_table(sim_hourly: np.ndarray, obs_hourly: np.ndarray) -> dict:
    vals = [
        round(geh(float(m), float(c)), 3)
        for m, c in zip(sim_hourly.ravel(), obs_hourly.ravel(), strict=True)
    ]
    return {
        "values": vals,
        "fraction_under_5": round(sum(1 for g in vals if g < 5.0) / len(vals), 4),
        "n_bins": len(vals),
    }


def _ring_worker(payload: tuple[list[int], str]) -> dict:
    """Ring benchmark in a child process (keeps libsumo out of the parent)."""
    from validation.ring_benchmark import evaluate_ring_benchmark

    seeds, out = payload
    return evaluate_ring_benchmark(seeds, Path(out)).to_dict()


def ring_benchmark_block(n_seeds: int, out_dir: Path) -> dict:
    """Evaluate the §7.1 ring rows on ``n_seeds`` seeds of ``ring_sugiyama``.

    Seeds are ``spawn_seeds(<ring scenario seed>, n_seeds)`` (docs/CONTRACTS.md
    §6). Runs in a spawned process so the parent never loads libsumo (the
    parquet-path clash documented on ``microsim.runner._write_parquet``).
    Writes ``out_dir/ring_benchmark.json`` and returns the same dict.
    """
    import multiprocessing as mp

    from validation.ring_benchmark import RING_SCENARIO

    ring_cfg = load_scenario(RING_SCENARIO)
    seeds = spawn_seeds(ring_cfg.seed, n_seeds)
    t0 = time.perf_counter()
    with mp.get_context("spawn").Pool(1) as pool:
        ring = pool.apply(_ring_worker, ((seeds, str(out_dir)),))
    ring["wall_s"] = round(time.perf_counter() - t0, 1)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ring_benchmark.json").write_text(
        json.dumps(_json_safe(ring), indent=2, allow_nan=False)
    )
    return ring


SWEEP_SUMMARY = REPO_ROOT / "artifacts" / "i24_sweep_summary.json"


def _sweep_grid() -> list[tuple[float, float]] | None:
    """(penetration, compliance) cells published with >= 20 seeds, else None.

    Feeds the CLAUDE.md §7.1 sensitivity row from
    ``artifacts/i24_sweep_summary.json`` (``scripts/i24_penetration_analyze.py``)
    when the flagship sweep has been analysed; the row stays "not evaluated"
    otherwise.
    """
    if not SWEEP_SUMMARY.is_file():
        return None
    d = json.loads(SWEEP_SUMMARY.read_text())
    if int(d.get("n_seeds", 0)) < 20:
        return None
    incomplete = set(d.get("incomplete_cells") or [])
    cells = []
    for pen in d.get("penetrations", []):
        for comp in d.get("compliances", []):
            name = f"fs_p{float(pen):.2f}_c{float(comp):.2f}"
            if name in d.get("cells", {}) and name not in incomplete:
                cells.append((float(pen), float(comp)))
    return cells or None


def build_results(
    arm: str,
    cfg: ScenarioConfig,
    sim: dict,
    obs: dict,
    replicates: int,
    ring: dict | None = None,
    *,
    scenario: str | None = None,
    demand_arm: str | None = None,
    collisions: list[int | None] | None = None,
    lock_records: Sequence[RunLocks | None] | None = None,
    section_lanes: str = "all",
) -> dict:
    """The arm's artifact. ``scenario`` / ``demand_arm`` / ``collisions`` serve an
    explicit scenario (``--scenario``): its name, its demand text and its runs'
    collision counts for the ``no_collisions`` row (None: not recorded, as for
    every arm). ``lock_records`` (one :class:`validation.locks.RunLocks` per
    replicate, :func:`stored_lock_records`) scores the ``no_locks`` row (None:
    not recorded); :func:`add_lock_blocks` writes the blocks that go with it.
    A ``sim`` with ``lane_crossings`` (``--lane-crossings``) adds ``geh.lane_set``,
    the three tables on the observed lane set, last in the block; with
    ``section_lanes="observed"`` the criteria row is scored on it
    (:data:`LANE_SET_PRIMARY`; module docstring, "Lane sets")."""
    if section_lanes not in SECTION_LANES:
        raise ValueError(f"section_lanes {section_lanes!r} is not one of {SECTION_LANES}")
    if section_lanes == "observed" and "lane_crossings" not in sim:
        raise ValueError("section_lanes='observed' needs the battery's lane crossings")
    sim_hourly = np.asarray(sim["hourly_flows_veh_h_mean"], dtype=np.float64)
    geh_tracked = _geh_table(sim_hourly, np.asarray(obs["hourly_flows_veh_h_tracked"]))
    geh_corrected = _geh_table(sim_hourly, np.asarray(obs["hourly_flows_veh_h_corrected"]))
    geh_recommended = _geh_table(sim_hourly, np.asarray(obs["hourly_flows_veh_h_recommended"]))
    # The criterion row scores every arm against the best available estimate
    # of the observed flow — the tracked crossings divided by the coverage
    # artifact's recommended estimator (needs no car-following model, unlike
    # the apparent coverage that shapes the corrected arm's demand). The
    # tracked (lower-bound) and apparent-coverage tables are kept as bounds.
    geh_primary = geh_recommended
    geh_lane_set = lane_set_geh(sim, obs) if "lane_crossings" in sim else None
    if section_lanes == "observed":
        assert geh_lane_set is not None
        geh_primary = geh_lane_set["vs_recommended_coverage_counts"]

    obs_seg = np.asarray(obs["segment_speeds_ms"], dtype=np.float64)
    sim_seg = np.asarray(sim["segment_speeds_ms_mean"], dtype=np.float64)
    both = np.isfinite(obs_seg) & np.isfinite(sim_seg) & (obs_seg != 0.0)
    rmspe_value = rmspe(sim_seg[both], obs_seg[both])
    rmspe_block = _rmspe_block(rmspe_value, int(both.sum()), sim, obs_seg)

    criteria_rows = evaluate(
        PROFILE,
        geh_values=geh_primary["values"],
        rmspe_value=rmspe_value,
        wave_speed_kmh=(
            sim["criterion_wave_speed_kmh"]
            if sim["criterion_wave_speed_kmh"] is not None
            else math.nan
        ),
        wave_detector=CRITERION_DETECTOR,
        ring_emergence=None if ring is None else bool(ring["emergence"]["passed"]),
        ring_dampening=None if ring is None else bool(ring["dampening"]["passed"]),
        n_seeds=replicates,
        sweep_grid=_sweep_grid(),
        collision_counts=collisions,
        lock_records=lock_records,
    )
    inputs = _inputs()
    ring_note = (
        "Ring benchmark rows evaluated by validation.ring_benchmark (the CI gate's checks on "
        f"{ring['emergence']['n_seeds']} seeded replicates; pass = every replicate passes); "
        "see the 'ring' block."
        if ring is not None
        else "Ring benchmark rows not evaluated in this run (--ring-seeds 0); reported as failing per CLAUDE.md §0.1."
    )
    results = {
        "schema_version": 6,
        "criteria_profile": PROFILE.name,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scenario": scenario if scenario is not None else scenario_name(arm),
        "arm": arm,
        "replicates": replicates,
        "seeds": sim["seeds"],
        "config_hash": sim["config_hash"],
        "versions": _versions(),
        "boundary": {
            "kind": "speed_schedule on the exit edge (data x >= 5492 m)",
            "source": f"observed mean mainline speed in data x {inputs['boundary']['x_range_m']} per {inputs['boundary']['window_s']:g} s",
            "v_limit_min_ms": inputs["boundary"]["v_min_ms"],
            "v_limit_max_ms": inputs["boundary"]["v_max_ms"],
            "framing": "measured downstream boundary condition per FHWA Traffic Analysis Toolbox Vol. III (FHWA-HOP-18-036, 2019); data-derived (seeded=False)",
        },
        "ramps": [r["name"] for r in inputs["ramps"]],
        "demand_arm": demand_arm
        if demand_arm is not None
        else (
            "as tracked (lower bound at the instrument's coverage)"
            if arm == "tracked"
            else "divided by the apparent tracking coverage per 15-min window (docs/I24_DATA.md §4)"
        ),
        "observed": obs,
        "simulated": sim,
        "geh": {
            "primary": "recommended",
            "primary_rule": GEH_PRIMARY_RULE,
            "vs_tracked_counts": geh_tracked,
            "vs_coverage_corrected_counts": geh_corrected,
            "vs_recommended_coverage_counts": geh_recommended,
            "bins": "6 sections x 24 five-min windows, hourly-equivalent volumes (x12)",
        },
        "rmspe": rmspe_block,
        "waves": {
            "observed": obs["waves"],
            "observed_stripe": obs["waves_stripe"],
            "stripe_params": obs["waves_stripe_params"],
            "simulated_mean_backward_speed_kmh": sim["mean_backward_speed_kmh"],
            "simulated_wave_count_mean": sim["wave_count_mean"],
            "n_replicates_with_backward_waves": sim["n_replicates_with_backward_waves"],
            "simulated_stripe_mean_backward_speed_kmh": sim["stripe_mean_backward_speed_kmh"],
            "n_replicates_with_stripe_backward_waves": sim[
                "n_replicates_with_stripe_backward_waves"
            ],
            "criterion_detector": CRITERION_DETECTOR.name,
            "criterion_detector_description": CRITERION_DETECTOR.describe(),
            "criterion_wave_speed_kmh": sim["criterion_wave_speed_kmh"],
            "by_detector": {
                name: {
                    "detector_description": WAVE_DETECTORS[name].describe(),
                    "observed": obs["waves_by_detector"][name],
                    "simulated": sim["wave_speed_by_detector"][name],
                }
                for name in WAVE_DETECTORS
            },
            "field_bins": f"each detector's own bins (see by_detector) on data x in [0, {obs['span_data_x_m'][1]:.0f}) m, both sides",
            "prediction_under_test": "docs/WAVE_SPEED_DIAGNOSIS.md: on a long, congested corridor the calibrated fleet's emergent backward waves fall in the 14-22 km/h band",
        },
        "criteria": [asdict(r) for r in criteria_rows],
        "ring": ring,
        "metrics_ci": sim["metrics_ci"],
        "notes": [
            "Observed side: I-24 MOTION westbound fragments, mainline lanes 1-4, 06:30-08:30 CST, data x in [0, 5492) m; counts are fragment crossings (lower bounds at tracking coverage), speeds are coverage-robust.",
            "Six GEH sections chosen for coverage (holes at 400 m and 2400 m avoided); the observed count at every section is still biased low by 35-50% in the peak.",
            "Three GEH tables are reported for every arm (tracked counts = lower bound; counts / apparent coverage; counts / recommended coverage); the criteria row uses the recommended-coverage table: "
            + GEH_PRIMARY_RULE,
            ring_note,
            (
                "The sensitivity_grid criterion row is fed from artifacts/i24_sweep_summary.json "
                "(scripts/i24_penetration_sweep.py -> i24_penetration_analyze.py)."
                if _sweep_grid()
                else "The sensitivity_grid criterion row is not evaluated: no analysed penetration x "
                "compliance sweep artifact (artifacts/i24_sweep_summary.json) is present."
            ),
            "compute_metrics runs on the measured span only (travel time over the span, throughput at data x = 2200 m); its wave metrics use the standard 40 km/h detector on the same site-clipped field, not the criteria row's detector.",
            f"The wave_speed criteria row is measured with the {PROFILE.name!r} profile's wave_detector ({CRITERION_DETECTOR.name}); the 'waves' block keeps the standard-detector keys of schema 4 and adds every registered detector under 'by_detector'.",
        ],
    }
    if geh_lane_set is not None:  # --lane-crossings only: additions, last in their blocks
        results["geh"]["lane_set"] = geh_lane_set
        if section_lanes == "observed":
            results["geh"]["primary"] = LANE_SET_PRIMARY
            results["geh"]["primary_rule"] = GEH_PRIMARY_RULE + "; " + LANE_SET_RULE
        results["notes"].append(
            "Per-lane section crossings recorded (--lane-crossings): simulated.lane_crossings "
            "and geh.lane_set (the three tables on the observed lane set). The criteria row is "
            + (
                "scored on the observed lane set (--section-lanes observed)."
                if section_lanes == "observed"
                else "scored on every lane of the section's edge, as before; geh.lane_set is "
                "reported beside it."
            )
        )
    return results


#: How ``geh.lane_set`` counts the simulated side (docs/I24_CONSISTENCY_C7B.md §3).
LANE_SET_RULE = (
    "the simulated count at every section reads the observed lane set (lanes 1-4: the four "
    "highest SUMO lane indices of the section's edge, simulated.lane_crossings), so the "
    "auxiliary lane of the 5-lane edges at 1,000 m (the Old Hickory acceleration lane) and "
    "4,800 m (the Hickory Hollow-Bell Road weave lane) is no longer counted on the simulated "
    "side only"
)


def lane_set_geh(sim: Mapping[str, Any], obs: Mapping[str, Any]) -> dict[str, Any]:
    """``geh.lane_set``: the battery's three GEH tables with the simulated count on the lane set."""
    hourly = np.asarray(sim["lane_crossings"]["hourly_flows_veh_h_mean_lane_set"], dtype=np.float64)
    return {
        "rule": LANE_SET_RULE,
        "vs_tracked_counts": _geh_table(hourly, np.asarray(obs["hourly_flows_veh_h_tracked"])),
        "vs_coverage_corrected_counts": _geh_table(
            hourly, np.asarray(obs["hourly_flows_veh_h_corrected"])
        ),
        "vs_recommended_coverage_counts": _geh_table(
            hourly, np.asarray(obs["hourly_flows_veh_h_recommended"])
        ),
        "bins": "6 sections x 24 five-min windows, hourly-equivalent volumes (x12)",
    }


def _criteria_rows(
    d: Mapping[str, Any], *, geh_values: Sequence[float], rmspe_value: float
) -> list[CriteriaResult]:
    """The criteria rows of a stored battery ``d`` with these GEH values and this RMSPE.

    Everything else comes from the battery as stored: the criterion wave speed
    (the standard detector's, flagged, when the artifact predates the
    profile's), the ring rows, the replicate count, the published sweep grid,
    the collision counts (explicit-scenario batteries) and the lock records
    (batteries run since 2026-10-07).
    """
    waves = d["waves"]
    wave_speed = waves.get("criterion_wave_speed_kmh")
    detector = CRITERION_DETECTOR
    if wave_speed is None:
        wave_speed = waves.get("simulated_mean_backward_speed_kmh")
        detector = get_detector("standard")
    ring = d.get("ring")
    return evaluate(
        PROFILE,
        geh_values=geh_values,
        rmspe_value=rmspe_value,
        wave_speed_kmh=wave_speed if wave_speed is not None else math.nan,
        wave_detector=detector,
        ring_emergence=None if not ring else bool(ring["emergence"]["passed"]),
        ring_dampening=None if not ring else bool(ring["dampening"]["passed"]),
        n_seeds=int(d["replicates"]),
        sweep_grid=_sweep_grid(),
        # recorded by explicit-scenario batteries only; absent (not recorded) for every arm
        collision_counts=(d.get("simulated") or {}).get("n_collisions_per_replicate"),
        # recorded by every battery run since 2026-10-07; absent (not recorded) before
        lock_records=stored_lock_records(d.get("simulated") or {}),
    )


def refresh_criteria(arm: str, ring_block: dict | None = None) -> Path:
    """Re-evaluate the criteria rows of an existing arm artifact (no simulation).

    Uses the values the artifact already carries (primary GEH bins, RMSPE, the
    criterion wave speed, ring rows, replicate count) with the current profile
    and the published sweep grid, rewrites ``criteria`` and the note, and
    returns the path. Rows whose inputs are absent stay not evaluated, except
    the ring rows when ``ring_block`` (a fresh ``ring_benchmark_block`` run,
    ``--criteria-only --ring-seeds N``) is given: the ring benchmark does not
    depend on the arm, so it is stored on the artifact and the rows scored.
    """
    path = artifact_path(arm)
    d = json.loads(path.read_text())
    if ring_block is not None:
        d["ring"] = ring_block
    geh = d["geh"]
    obs_d = d["observed"]
    if "hourly_flows_veh_h_recommended" not in obs_d:
        rec, rec_src = _recommended_coverage(int(obs_d["n_windows"]), float(obs_d["t_range_s"][0]))
        counts = np.asarray(obs_d["counts_tracked"], dtype=np.float64)
        obs_d["coverage_recommended_per_window"] = rec.round(4).tolist()
        obs_d["coverage_recommended_source"] = rec_src
        obs_d["hourly_flows_veh_h_recommended"] = (
            (counts * 3600.0 / WINDOW_S / rec[None, :]).round(1).tolist()
        )
    if "vs_recommended_coverage_counts" not in geh:
        sim_hourly = np.asarray(d["simulated"]["hourly_flows_veh_h_mean"], dtype=np.float64)
        geh["vs_recommended_coverage_counts"] = _geh_table(
            sim_hourly, np.asarray(obs_d["hourly_flows_veh_h_recommended"])
        )
    # a battery scored on the observed lane set (--section-lanes observed) keeps that row
    lane_set_primary = geh.get("primary") == LANE_SET_PRIMARY and "lane_set" in geh
    geh["primary"] = LANE_SET_PRIMARY if lane_set_primary else "recommended"
    geh["primary_rule"] = (
        GEH_PRIMARY_RULE + "; " + LANE_SET_RULE if lane_set_primary else GEH_PRIMARY_RULE
    )
    d["notes"] = [
        n for n in d.get("notes", []) if not n.startswith("Both GEH tables are reported")
    ] + [
        "Three GEH tables are reported (tracked = lower bound; counts / apparent coverage; counts / recommended coverage); the criteria row uses the recommended-coverage table: "
        + GEH_PRIMARY_RULE
    ]
    primary = (
        geh["lane_set"]["vs_recommended_coverage_counts"]
        if lane_set_primary
        else geh["vs_recommended_coverage_counts"]
    )
    if "leave_one_out_floor" not in d["rmspe"]:
        obs_seg = np.asarray(obs_d["segment_speeds_ms"], dtype=np.float64)
        d["rmspe"] = _rmspe_block(
            d["rmspe"]["value"], d["rmspe"]["n_bins"], d["simulated"], obs_seg
        )
    rows = _criteria_rows(d, geh_values=primary["values"], rmspe_value=d["rmspe"]["value"])
    d["criteria"] = [_json_safe(asdict(r)) for r in rows]
    d.setdefault("notes", []).append(
        f"criteria rows re-evaluated by scripts/i24_validate.py --criteria-only on "
        f"{datetime.now(UTC).isoformat(timespec='seconds')} with the current profile"
        f" ({'sweep grid present' if _sweep_grid() else 'no sweep grid'})"
    )
    path.write_text(json.dumps(_json_safe(d), indent=2, allow_nan=False))
    return path


# --- re-scoring a battery against other mornings (docs/PRE_FRISCO_PROGRAM.md C9) ----------------

RESCORE_SCHEMA = "flowstate.i24_rescore/1"
#: The observed side's grid; a battery is re-scored only against a side on the same one.
OBSERVED_GRID_KEYS = (
    "t_range_s",
    "span_data_x_m",
    "sections_m",
    "window_s",
    "n_windows",
    "n_segments",
)
#: The observed tables averaged over days (protocol §3.5: "the mean of the validation days").
_MEANED_TABLES = (
    "counts_tracked",
    "hourly_flows_veh_h_tracked",
    "hourly_flows_veh_h_corrected",
    "hourly_flows_veh_h_recommended",
)


def grid_mismatch(a: Mapping[str, Any], b: Mapping[str, Any]) -> str | None:
    """How two observed sides' grids differ (:data:`OBSERVED_GRID_KEYS`), or None."""
    for key in OBSERVED_GRID_KEYS:
        if not np.allclose(np.asarray(a[key], dtype=float), np.asarray(b[key], dtype=float)):
            return f"{key}: {a[key]} against {b[key]}"
    return None


def mean_observed(sides: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One observed side from several days: each table's mean per (section or segment, window).

    Flows are averaged after each day's own coverage scaling (a day's
    recommended table is its counts over its coverage); segment speeds are
    averaged over the days that measured the cell. One side is returned whole
    (a copy, its wave readings included); a mean carries no wave readings (each
    day's are in the re-score's ``observed.inputs``).

    Raises:
        ValueError: No side, or two sides on different grids.
    """
    if not sides:
        raise ValueError("no observed side to average")
    first = sides[0]
    for other in sides[1:]:
        why = grid_mismatch(first, other)
        if why is not None:
            raise ValueError(f"observed sides on different grids ({why})")
    if len(sides) == 1:
        return json.loads(json.dumps(first))
    out: dict[str, Any] = {k: first[k] for k in ("period", *OBSERVED_GRID_KEYS, "segment_m")}
    for key in _MEANED_TABLES:
        out[key] = np.mean([np.asarray(s[key], dtype=np.float64) for s in sides], axis=0).tolist()
    seg = np.stack([np.asarray(s["segment_speeds_ms"], dtype=np.float64) for s in sides])
    n = np.sum(np.isfinite(seg), axis=0)
    total = np.nansum(seg, axis=0)
    out["segment_speeds_ms"] = np.where(n > 0, total / np.maximum(n, 1), np.nan).tolist()
    out["n_days"] = len(sides)
    return out


def _block_rmspe(sim: np.ndarray, obs: np.ndarray, k: int) -> float:
    """RMSPE of two ``[window][segment]`` fields after averaging blocks of ``k`` windows.

    ``validation.baseline_gate.aggregated_rmspe``'s rule on arrays: blocks
    anchored at the first window (the study period's start), a trailing
    partial block left out, and within a block both sides averaged over the
    cells where both are measured and the observation is not zero.
    """
    joint = np.isfinite(sim) & np.isfinite(obs) & (obs != 0.0)
    n_blocks = sim.shape[0] // k
    s_blk = np.full((n_blocks, sim.shape[1]), np.nan)
    o_blk = np.full((n_blocks, sim.shape[1]), np.nan)
    for b in range(n_blocks):
        sl = slice(b * k, (b + 1) * k)
        cnt = joint[sl].sum(axis=0)
        s_sum = np.where(joint[sl], sim[sl], 0.0).sum(axis=0)
        o_sum = np.where(joint[sl], obs[sl], 0.0).sum(axis=0)
        s_blk[b] = np.where(cnt > 0, s_sum / np.maximum(cnt, 1), np.nan)
        o_blk[b] = np.where(cnt > 0, o_sum / np.maximum(cnt, 1), np.nan)
    ok = np.isfinite(s_blk) & np.isfinite(o_blk) & (o_blk != 0.0)
    return float(rmspe(s_blk[ok], o_blk[ok])) if ok.any() else math.nan


def _ci_block(values: Sequence[float]) -> dict[str, Any]:
    interval = ci(values)
    return {
        "mean": interval.mean,
        "lo95": interval.lo95,
        "hi95": interval.hi95,
        "n": interval.n,
        "underpowered": interval.underpowered,
    }


def gate_readings(
    battery: Mapping[str, Any], obs: Mapping[str, Any], rows: Sequence[CriteriaResult]
) -> dict[str, Any]:
    """Protocol checks C1, C3, C4 and C5 (docs/FRISCO_PROTOCOL.md §4) read on one observed side.

    Reported only (docs/PRE_FRISCO_PROGRAM.md C9); none gates here.

    * **C1** — GEH < 5 on at least 85 % of station-hour comparisons
      (``fhwa_default``): each section's simulated volume per hour (the
      replicate-mean crossings summed over the hour's 5-min windows) against
      the observed volume (the recommended-coverage flows summed likewise),
      hours anchored at the study period's start; a trailing partial hour is
      left out. The battery's own row (5-min windows ×12) is in ``criteria``.
    * **C3** — segment-speed RMSPE at 15 minutes
      (``validation.baseline_gate.SPEED_AGGREGATION_S``): per replicate
      (:func:`_block_rmspe`), the gating value the mean over the replicates
      with its 95 % t interval, at most the profile's ``rmspe_max``; the
      replicate-mean field by aggregation beside it
      (``validation.report.speed_aggregation_rows``).
    * **C4** — the battery's ``wave_speed`` row (profile detector) passes and
      a backward front is found in at least
      ``validation.baseline_gate.WAVE_MIN_FRONT_REPLICATE_SHARE`` of the
      replicates; the replicates' speeds with their 95 % interval, and the
      observed side's reading of the same detector when it has one. The
      simulated side does not depend on the day.
    * **C5** — the battery's ``no_collisions`` row, also day-independent.
    """
    sim = battery["simulated"]
    window_s = float(obs["window_s"])
    n_win = int(obs["n_windows"])
    per_hour = round(3600.0 / window_s)
    n_hours = n_win // per_hour
    counts_mean = np.asarray(sim["counts_mean"], dtype=np.float64)
    obs_rec = np.asarray(obs["hourly_flows_veh_h_recommended"], dtype=np.float64)
    hours = []
    for si, x in enumerate(obs["sections_m"]):
        for h in range(n_hours):
            sl = slice(h * per_hour, (h + 1) * per_hour)
            m = float(counts_mean[si, sl].sum())
            c = float((obs_rec[si, sl] * window_s / 3600.0).sum())
            hours.append({"section_m": x, "hour": h, "sim_veh": m, "obs_veh": c, "geh": geh(m, c)})
    c1_row = next(
        r
        for r in evaluate(PROFILE, geh_values=[h["geh"] for h in hours])
        if r.name == "link_flows_geh"
    )
    obs_seg = np.asarray(obs["segment_speeds_ms"], dtype=np.float64)
    k = round(SPEED_AGGREGATION_S / window_s)
    per_rep = [
        _block_rmspe(np.asarray(f, dtype=np.float64), obs_seg, k)
        for f in sim["segment_speeds_ms_per_replicate"]
    ]
    c3 = _ci_block(per_rep)
    rmspe_max = PROFILE.rmspe_max
    c3_pass = rmspe_max is not None and math.isfinite(c3["mean"]) and c3["mean"] <= rmspe_max
    stack = sim["wave_speed_by_detector"][GATE_WAVE_DETECTOR]
    n_rep = int(battery["replicates"])
    share = int(stack["n_replicates_with_backward_waves"]) / n_rep if n_rep else math.nan
    speeds = [
        math.nan if r["mean_backward_speed_kmh"] is None else float(r["mean_backward_speed_kmh"])
        for r in stack["per_replicate"]
    ]
    wave_row = next(r for r in rows if r.name == "wave_speed")
    collision_row = next(r for r in rows if r.name == "no_collisions")
    observed_wave = (obs.get("waves_by_detector") or {}).get(GATE_WAVE_DETECTOR)
    return {
        "reported_only": True,
        "C1": {
            "check": "GEH < 5 on >= 85 % of station-hour comparisons (fhwa_default), hours anchored at the study period's start",
            "passed": bool(c1_row.passed),
            "value": c1_row.value,
            "n_comparisons": len(hours),
            "n_windows_left_out": n_win - n_hours * per_hour,
            "comparisons": hours,
        },
        "C3": {
            "check": f"segment-speed RMSPE at {SPEED_AGGREGATION_S / 60.0:g} min, mean over replicates <= {rmspe_max}",
            "passed": bool(c3_pass),
            "per_replicate": per_rep,
            "replicates": c3,
            "replicate_mean_field_by_aggregation": speed_aggregation_rows(
                obs_seg,
                np.asarray(sim["segment_speeds_ms_mean"], dtype=np.float64),
                window_s,
                criterion_aggregation_s=SPEED_AGGREGATION_S,
            ),
        },
        "C4": {
            "check": f"the wave_speed row ({GATE_WAVE_DETECTOR}) passes and a backward front in >= {WAVE_MIN_FRONT_REPLICATE_SHARE:.0%} of the replicates",
            "passed": bool(wave_row.passed) and share >= WAVE_MIN_FRONT_REPLICATE_SHARE,
            "wave_speed_row_passed": bool(wave_row.passed),
            "wave_speed_kmh": wave_row.value,
            "front_share": share,
            "replicate_speeds_kmh": _ci_block(speeds),
            "observed_speed_kmh": None
            if observed_wave is None
            else observed_wave.get("mean_backward_speed_kmh"),
            "note": "the simulated side is the battery's and does not depend on the day; the "
            "observed reading is the side's own (a mean of days has none: see observed.inputs)",
        },
        "C5": {
            "check": "zero SUMO collisions in every run (no_collisions)",
            "passed": bool(collision_row.passed),
            "evaluated": bool(collision_row.evaluated),
            "value": collision_row.value,
            "note": "the battery's row; it does not depend on the day",
        },
    }


def rescore(battery: Mapping[str, Any], obs: Mapping[str, Any]) -> dict[str, Any]:
    """A stored battery's simulated side scored against another observed side.

    The model is unchanged (docs/FRISCO_PROTOCOL.md §3.5): the replicate-mean
    hourly flows, segment speeds and per-replicate fields, the wave, ring,
    collision and lock inputs are the battery's; only the observed tables
    change. The three GEH tables, the RMSPE block and the criteria rows are
    formed exactly as :func:`build_results` and :func:`refresh_criteria` form
    them, so re-scoring a battery against its own observed side reproduces
    its stored values; :func:`gate_readings` adds the protocol's C1/C3/C4/C5.

    Raises:
        ValueError: The observed side is on another grid than the battery's.
    """
    why = grid_mismatch(battery["observed"], obs)
    if why is not None:
        raise ValueError(f"the observed side is not on the battery's grid ({why})")
    if (battery.get("geh") or {}).get("primary") == LANE_SET_PRIMARY:
        raise ValueError(
            "the battery's link-flow row is scored on the observed lane set (--section-lanes "
            "observed); re-scoring it against another side is not implemented"
        )
    sim = battery["simulated"]
    sim_hourly = np.asarray(sim["hourly_flows_veh_h_mean"], dtype=np.float64)
    geh_block = {
        "primary": "recommended",
        "primary_rule": GEH_PRIMARY_RULE,
        "vs_tracked_counts": _geh_table(sim_hourly, np.asarray(obs["hourly_flows_veh_h_tracked"])),
        "vs_coverage_corrected_counts": _geh_table(
            sim_hourly, np.asarray(obs["hourly_flows_veh_h_corrected"])
        ),
        "vs_recommended_coverage_counts": _geh_table(
            sim_hourly, np.asarray(obs["hourly_flows_veh_h_recommended"])
        ),
        "bins": "6 sections x 24 five-min windows, hourly-equivalent volumes (x12)",
    }
    obs_seg = np.asarray(obs["segment_speeds_ms"], dtype=np.float64)
    sim_seg = np.asarray(sim["segment_speeds_ms_mean"], dtype=np.float64)
    both = np.isfinite(obs_seg) & np.isfinite(sim_seg) & (obs_seg != 0.0)
    rmspe_value = rmspe(sim_seg[both], obs_seg[both])
    rows = _criteria_rows(
        battery,
        geh_values=geh_block["vs_recommended_coverage_counts"]["values"],
        rmspe_value=rmspe_value,
    )
    return {
        "geh": geh_block,
        "rmspe": _rmspe_block(rmspe_value, int(both.sum()), sim, obs_seg),
        "criteria": [_json_safe(asdict(r)) for r in rows],
        "gate_checks": gate_readings(battery, obs, rows),
    }


def parse_dated_paths(items: Sequence[str]) -> list[tuple[str, Path]]:
    """``DATE=PATH`` items → ``(YYYY-MM-DD, path)``; a relative path is the repository's.

    Raises:
        ValueError: An item without ``=``, a bad date, or a date given twice.
    """
    out: list[tuple[str, Path]] = []
    for item in items:
        day, sep, path = str(item).partition("=")
        if not sep or not path:
            raise ValueError(f"{item!r}: expected DATE=PATH")
        d = datetime.strptime(day.strip(), "%Y-%m-%d").date().isoformat()
        if d in {o[0] for o in out}:
            raise ValueError(f"date {d} given twice")
        p = Path(path)
        out.append((d, p if p.is_absolute() else REPO_ROOT / p))
    return out


def rescore_document(
    battery_path: Path, observed: Sequence[tuple[str, Path]], label: str | None = None
) -> dict[str, Any]:
    """The ``--rescore-observed`` artifact: one battery against one day or the mean of several."""
    battery = json.loads(battery_path.read_text())
    sides = [json.loads(path.read_text()) for _, path in observed]
    obs = mean_observed(sides)
    dates = [d for d, _ in observed]
    inputs = []
    for (d, path), side in zip(observed, sides, strict=True):
        by_det = side.get("waves_by_detector") or {}
        inputs.append(
            {
                "date": d,
                "path": _rel_repo(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "data_hash": side.get("data_hash"),
                "source": (side.get("day") or {}).get("source"),
                "coverage_recommended_source": side.get("coverage_recommended_source"),
                "observed_wave_speed_kmh": {
                    name: (by_det.get(name) or {}).get("mean_backward_speed_kmh")
                    for name in WAVE_DETECTORS
                },
            }
        )
    scored = rescore(battery, obs)
    return {
        "schema": RESCORE_SCHEMA,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": "scripts/i24_validate.py --criteria-only --rescore-observed",
        "label": label or (dates[0] if len(dates) == 1 else "mean of " + ", ".join(dates)),
        "rule": (
            "the battery's simulated side, unchanged (no simulation), scored against the observed "
            "side below, which is one day or the mean of several per 5-min window "
            "(docs/FRISCO_PROTOCOL.md section 3.5; docs/PRE_FRISCO_PROGRAM.md C9)"
        ),
        "battery": {
            "path": _rel_repo(battery_path),
            "sha256": hashlib.sha256(battery_path.read_bytes()).hexdigest(),
            "scenario": battery.get("scenario"),
            "arm": battery.get("arm"),
            "config_hash": battery.get("config_hash"),
            "replicates": battery.get("replicates"),
            "seeds": battery.get("seeds"),
            "criteria_as_stored": battery.get("criteria"),
        },
        "observed": {
            "dates": dates,
            "aggregation": "one day"
            if len(sides) == 1
            else f"mean over {len(sides)} days per 5-min window (flows after each day's coverage scaling)",
            "inputs": inputs,
            "tables": obs,
        },
        **scored,
        "notes": [
            "Observed sides are tracked fragment crossings scaled by each day's recommended coverage "
            "(the criteria row's table) and coverage-robust segment speeds, on the battery's sections, "
            "windows and segments.",
            "The wave_speed, ring, n_seeds, sensitivity_grid, no_collisions and no_locks rows read the "
            "battery's stored inputs and do not depend on the day.",
            "gate_checks are reported only (docs/PRE_FRISCO_PROGRAM.md C9).",
        ],
    }


def _print_arm(
    arm: str, results: dict[str, Any], sim: dict[str, Any], obs: dict[str, Any], replicates: int
) -> None:
    """The console summary of one arm's (or explicit scenario's) battery."""
    g = results["geh"]
    print(
        f"[{arm}] GEH<5 vs tracked {g['vs_tracked_counts']['fraction_under_5']:.0%}, vs corrected "
        f"{g['vs_coverage_corrected_counts']['fraction_under_5']:.0%}, vs recommended "
        f"{g['vs_recommended_coverage_counts']['fraction_under_5']:.0%} | RMSPE {results['rmspe']['value']:.1%} | "
        f"sim backward wave [{CRITERION_DETECTOR.name}] {sim['criterion_wave_speed_kmh']} km/h "
        f"(standard {sim['mean_backward_speed_kmh']}, {sim['n_replicates_with_backward_waves']}/{replicates} reps) | "
        f"obs [{CRITERION_DETECTOR.name}] {obs['waves_by_detector'][CRITERION_DETECTOR.name]['mean_backward_speed_kmh']} km/h "
        f"(standard {obs['waves']['mean_backward_speed_kmh']}, stripe {obs['waves_stripe']['mean_backward_speed_kmh']}) | "
        f"wall {sim['wall_s']} s",
        flush=True,
    )
    for row in results["criteria"]:
        print(
            f"    {row['name']:18s} {'PASS' if row['passed'] else 'FAIL'}  {row['value']}  ({row['threshold']})"
        )


def _rel_repo(path: Path) -> str:
    p = path.resolve()
    return str(p.relative_to(REPO_ROOT)) if p.is_relative_to(REPO_ROOT) else str(p)


def explicit_battery(
    scenario: Path,
    label: str,
    replicates: int,
    procs: int,
    obs: dict[str, Any],
    ring: dict[str, Any] | None,
    analysis_procs: int,
    reuse_runs: bool,
    lane_crossings: bool = False,
    section_lanes: str = "all",
) -> dict[str, Any]:
    """The battery of an explicit scenario file (module docstring); writes its artifact."""
    cfg = load_scenario(scenario)
    print(
        f"scenario {_rel_repo(scenario)} as {label!r} ({cfg.name}, config {config_hash(cfg)}): "
        f"{replicates} replicates ...",
        flush=True,
    )
    sim = micro_arm(
        cfg,
        replicates,
        OUT_ROOT / label,
        procs,
        obs,
        analysis_procs=analysis_procs,
        reuse_runs=reuse_runs,
        lane_crossings=lane_crossings or section_lanes == "observed",
    )
    metas = [load_meta(d) for d in sim["run_dirs"]]
    sim["n_collisions_per_replicate"] = collision_counts(metas)
    results = build_results(
        label,
        cfg,
        sim,
        obs,
        replicates,
        ring,
        scenario=cfg.name,
        demand_arm=f"as written in the scenario file {_rel_repo(scenario)} (its header says how)",
        collisions=sim["n_collisions_per_replicate"],
        lock_records=stored_lock_records(sim),
        section_lanes=section_lanes,
    )
    results["scenario_file"] = {
        "path": _rel_repo(scenario),
        "sha256": hashlib.sha256(scenario.read_bytes()).hexdigest(),
    }
    results["collisions"] = collision_summary(metas, labels=sim["seeds"])
    results["zero_collisions"] = collision_free(metas)
    results["notes"].append(
        f"Explicit scenario file (--scenario {_rel_repo(scenario)} --label {label}); every "
        "replicate's SUMO collision count (meta.json n_collisions) is recorded in "
        "simulated.n_collisions_per_replicate, pooled in 'collisions' and scored in the "
        "no_collisions row."
    )
    add_lock_blocks(results, stored_lock_records(sim), sim["seeds"])
    out_path = artifact_path(label)
    out_path.write_text(json.dumps(_json_safe(results), indent=2, allow_nan=False))
    shutil.copy(scenario, OUT_ROOT / f"{cfg.name}.yaml")
    _print_arm(label, results, sim, obs, replicates)
    col = results["collisions"]
    print(
        "    collisions         "
        + (
            "not recorded"
            if col is None
            else f"{col['total']} over {col['n_runs_recorded']} replicate(s), "
            f"{col['n_runs_with_collisions']} with any"
        )
        + f" -> {_rel_repo(out_path)}",
        flush=True,
    )
    print(lock_console_line(results), flush=True)
    return results


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--replicates", type=int, default=20)
    ap.add_argument("--procs", type=int, default=int(os.environ.get("I24_PROCS", "8")))
    ap.add_argument(
        "--arms",
        choices=("all", "both", "tracked", "corrected", "speedcal", "ramps", "speedcal_heavy"),
        default=None,
        help="'both' = tracked + corrected (the pre-2026-09-03 pair); 'all' (the default) adds "
        "speedcal",
    )
    ap.add_argument("--analysis-procs", type=int, default=6)
    ap.add_argument(
        "--criteria-only",
        action="store_true",
        help="re-evaluate the criteria rows of the existing arm artifacts (no simulation) and exit",
    )
    ap.add_argument(
        "--reuse-runs",
        action="store_true",
        help="analyse complete replicates already under runs/i24_validation instead of re-simulating",
    )
    ap.add_argument(
        "--ring-seeds",
        type=int,
        default=20,
        help="seeds for the ring emergence/dampening rows (0 = not evaluated)",
    )
    ap.add_argument(
        "--ring-only",
        action="store_true",
        help="evaluate the ring benchmark, write runs/i24_validation/ring/ring_benchmark.json, exit",
    )
    ap.add_argument(
        "--family",
        default="",
        help="scenario family suffix written by scripts/i24_build_replica.py --suffix; arms "
        "then read scenarios/i24_replica_<family>*.yaml and write "
        "artifacts/i24_validation_<family>_<arm>.json under runs/i24_validation_<family>/",
    )
    ap.add_argument(
        "--scenario",
        type=Path,
        default=None,
        help="validate this scenario file (with --label) instead of the family's arms",
    )
    ap.add_argument(
        "--label",
        default=None,
        help="the explicit scenario's name for its run tree and artifact "
        "(artifacts/i24_validation[_<family>]_<label>.json); not an arm's name",
    )
    ap.add_argument(
        "--observed-only",
        type=Path,
        default=None,
        metavar="PATH",
        help="build the observed side of the active I-24 day (I24_DAY_DIR / I24_T0_UNIX, "
        "scripts/i24_data.py; default the committed day), write it to PATH and exit: no "
        "simulation, no ring, no battery",
    )
    ap.add_argument(
        "--coverage-artifact",
        type=Path,
        default=None,
        help="with --observed-only: that day's coverage artifact (scripts/i24_coverage.py on the "
        "day; default artifacts/i24_coverage.json)",
    )
    ap.add_argument(
        "--rescore-observed",
        nargs="+",
        default=None,
        metavar="DATE=PATH",
        help="with --criteria-only and one battery (--label or one --arms): score its stored "
        "simulated side against these observed sides (--observed-only files), one day or the "
        "mean of several, into --rescore-out; the battery artifact is not rewritten",
    )
    ap.add_argument("--rescore-out", type=Path, default=None, help="the re-score's JSON")
    ap.add_argument(
        "--rescore-label", default=None, help="the re-score's label (default: its dates)"
    )
    ap.add_argument(
        "--lane-crossings",
        action="store_true",
        help="record each replicate's section crossings by lane (simulated.lane_crossings) and "
        "report the link-flow tables on the observed lane set beside the row (geh.lane_set); "
        "the criteria row is unchanged (docs/I24_CONSISTENCY_C7B.md §3)",
    )
    ap.add_argument(
        "--section-lanes",
        choices=SECTION_LANES,
        default="all",
        help="the simulated count of the link-flow criteria row: every lane of the section's "
        "edge ('all', the default, as before) or the observed lane set ('observed': lanes 1-4, "
        "the four highest SUMO indices; implies --lane-crossings)",
    )
    args = ap.parse_args(argv)
    lane_options = args.lane_crossings or args.section_lanes != "all"
    if lane_options and (args.criteria_only or args.observed_only is not None or args.ring_only):
        ap.error(
            "--lane-crossings and --section-lanes are read from the trajectories: they go with a "
            "battery, not with --criteria-only, --observed-only or --ring-only"
        )

    def repo_path(path: Path) -> Path:
        return path if path.is_absolute() else REPO_ROOT / path

    if args.observed_only is not None:
        if (
            args.criteria_only
            or args.scenario is not None
            or args.label is not None
            or args.arms is not None
            or args.rescore_observed is not None
        ):
            ap.error(
                "--observed-only builds one observed side and exits; it takes no battery option"
            )
        coverage = repo_path(args.coverage_artifact or COVERAGE_ARTIFACT)
        if not coverage.is_file():
            ap.error(f"--coverage-artifact {coverage}: no such file")
        try:
            obs = day_observed_side(coverage)
        except ValueError as exc:
            ap.error(f"--observed-only: {exc}")
        out = repo_path(args.observed_only)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(_json_safe(obs), indent=2, allow_nan=False))
        rec = np.asarray(obs["hourly_flows_veh_h_recommended"], dtype=np.float64)
        print(
            f"observed side of {obs['day']['date']} ({obs['day']['source']}): "
            f"{int(np.sum(obs['counts_tracked']))} tracked crossings at {len(obs['sections_m'])} "
            f"sections, mean recommended-coverage flow {rec.mean():.0f} veh/h -> {_rel_repo(out)}",
            flush=True,
        )
        return
    if args.coverage_artifact is not None:
        ap.error("--coverage-artifact goes with --observed-only")
    if args.rescore_observed is not None:
        if not args.criteria_only:
            ap.error("--rescore-observed re-scores a stored battery: give --criteria-only")
        if args.rescore_out is None:
            ap.error(
                "--rescore-observed needs --rescore-out (the battery artifact is never rewritten)"
            )
        if args.scenario is not None or (args.label is None) == (args.arms is None):
            ap.error("--rescore-observed takes exactly one battery: --label L or --arms <one arm>")
        if args.arms in ("all", "both"):
            ap.error("--rescore-observed takes exactly one battery: --arms <one arm>, not all/both")
    elif args.rescore_out is not None or args.rescore_label is not None:
        ap.error("--rescore-out and --rescore-label go with --rescore-observed")
    explicit = args.label is not None or args.scenario is not None
    if explicit:
        if args.label is None:
            ap.error("--scenario needs --label (it names the run tree and the artifact)")
        if args.scenario is None and not args.criteria_only:
            ap.error("--label needs --scenario (or --criteria-only, to re-score its artifact)")
        if args.arms is not None:
            ap.error("--arms and --scenario/--label are exclusive")
        if args.scenario is not None and not args.scenario.is_file():
            ap.error(f"--scenario {args.scenario}: no such file")
        err = label_error(args.label)
        if err is not None:
            ap.error(err)
    arms_choice = args.arms or "all"
    global FAMILY, OUT_ROOT
    if args.family:
        FAMILY = f"_{args.family}"
        OUT_ROOT = REPO_ROOT / "runs" / f"i24_validation{FAMILY}"
    if args.rescore_observed is not None:
        try:
            observed = parse_dated_paths(args.rescore_observed)
        except ValueError as exc:
            ap.error(f"--rescore-observed: {exc}")
        for day, path in observed:
            if not path.is_file():
                ap.error(f"--rescore-observed {day}: no file at {path}")
        battery_path = artifact_path(args.label if args.label is not None else args.arms)
        if not battery_path.is_file():
            ap.error(f"no battery artifact at {_rel_repo(battery_path)}")
        out = repo_path(args.rescore_out)
        if out.resolve() == battery_path.resolve():
            ap.error("--rescore-out may not be the battery artifact")
        try:
            doc = rescore_document(battery_path, observed, args.rescore_label)
        except ValueError as exc:
            ap.error(f"--rescore-observed: {exc}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(_json_safe(doc), indent=2, allow_nan=False))
        print(f"[{_rel_repo(battery_path)}] re-scored against {doc['label']}:", flush=True)
        for r in doc["criteria"]:
            print(
                f"    {r['name']:<18} {'PASS' if r['passed'] else 'FAIL':<5} {r.get('value')}",
                flush=True,
            )
        for name, chk in doc["gate_checks"].items():
            if isinstance(chk, dict):
                print(
                    f"    gate {name:<13} {'PASS' if chk['passed'] else 'FAIL'} (reported only)",
                    flush=True,
                )
        print(f"-> {_rel_repo(out)}", flush=True)
        return
    t0 = time.perf_counter()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    ring: dict | None = None
    if args.ring_seeds > 0:
        print(f"ring benchmark: {args.ring_seeds} seeds ...", flush=True)
        ring = ring_benchmark_block(args.ring_seeds, OUT_ROOT / "ring")
        for arm_name in ("emergence", "dampening"):
            blk = ring[arm_name]
            print(
                f"  ring {arm_name:10s} {'PASS' if blk['passed'] else 'FAIL'}  "
                f"{blk['n_pass']}/{blk['n_seeds']} seeds  (wall {ring['wall_s']} s)",
                flush=True,
            )
    if args.ring_only:
        print(f"done in {time.perf_counter() - t0:.0f} s -> {OUT_ROOT / 'ring'}")
        return
    obs = observed_side(OUT_ROOT / "observed_i24.json")
    if not explicit:  # an explicit scenario's battery leaves the committed observed side alone
        shutil.copy(
            OUT_ROOT / "observed_i24.json", REPO_ROOT / "artifacts" / "i24_validation_observed.json"
        )
    arms = [
        a
        for a in ARMS
        if not explicit
        and (
            arms_choice == a
            or (
                arms_choice == "all"
                and (
                    a not in ("ramps", "speedcal_heavy")
                    or (REPO_ROOT / "scenarios" / f"{scenario_name(a)}.yaml").is_file()
                )
            )
            or (arms_choice == "both" and a in ("tracked", "corrected"))
        )
    ]
    if args.criteria_only:
        for arm in [args.label] if explicit else arms:
            art = artifact_path(arm)
            if not art.is_file():
                print(f"[{arm}] no artifact at {art}; skipped", flush=True)
                continue
            refresh_criteria(arm, ring)
            rows = json.loads(art.read_text())["criteria"]
            print(f"[{arm}] criteria re-evaluated:", flush=True)
            for r in rows:
                print(
                    f"    {r['name']:<18} {'PASS' if r['passed'] else 'FAIL':<5} {r.get('value')}",
                    flush=True,
                )
        return
    if explicit:
        explicit_battery(
            args.scenario,
            args.label,
            args.replicates,
            args.procs,
            obs,
            ring,
            args.analysis_procs,
            args.reuse_runs,
            lane_crossings=args.lane_crossings,
            section_lanes=args.section_lanes,
        )
    for arm in arms:
        cfg = load_scenario(scenario_name(arm))
        print(
            f"arm {arm!r} ({scenario_name(arm)}, config {config_hash(cfg)}): {args.replicates} replicates ...",
            flush=True,
        )
        sim = micro_arm(
            cfg,
            args.replicates,
            OUT_ROOT / arm,
            args.procs,
            obs,
            analysis_procs=args.analysis_procs,
            reuse_runs=args.reuse_runs,
            lane_crossings=lane_options,
        )
        lock_records = stored_lock_records(sim)
        results = build_results(
            arm,
            cfg,
            sim,
            obs,
            args.replicates,
            ring,
            lock_records=lock_records,
            section_lanes=args.section_lanes,
        )
        add_lock_blocks(results, lock_records, sim["seeds"])
        out_path = artifact_path(arm)
        out_path.write_text(json.dumps(_json_safe(results), indent=2, allow_nan=False))
        shutil.copy(
            REPO_ROOT / "scenarios" / f"{scenario_name(arm)}.yaml",
            OUT_ROOT / f"{scenario_name(arm)}.yaml",
        )
        _print_arm(arm, results, sim, obs, args.replicates)
        print(lock_console_line(results), flush=True)
    print(f"done in {time.perf_counter() - t0:.0f} s -> {OUT_ROOT}")


if __name__ == "__main__":
    main()
