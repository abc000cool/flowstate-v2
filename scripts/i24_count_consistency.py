"""Count-consistency check of the I-24 MOTION westbound section counts.

docs/I24_DISCHARGE_DIAGNOSIS.md §6 and §8.4: at the recommended coverage the
validator's observed section flows (``scripts/i24_validate.py::observed_side``,
``artifacts/i24_validation_observed.json``) lose about 617 veh/h between data
x 2,200 and 5,400 m, while the recording's own ramp-lane counts (the counts
``scripts/i24_build_replica.py`` builds the model's ramps from) carry a net
exit of only about 235 (the model 304). The diagnosis reads the difference as
coverage that varies from section to section, hidden by the one pooled
coverage factor every section is divided by, which would make the peak
sections' 6,626 / 6,639 veh/h too high. This driver measures it. It is a cloud
stage: it reads the processed westbound table (about 1 GB, 43 M rows), which
the laptop must not.

For every 5-min window of 06:30–08:30 CST it computes, from the recording only:

* **Section flows** at the validator's six sections (data x 200, 1,000,
  2,200, 3,200, 4,800, 5,400 m): crossings of the mainline lanes 1–4, each
  fragment counted once per section, at its first crossing, in the lane of
  the later sample. The validator's rule (``crossings_per_window``, which does
  not de-duplicate a fragment that oscillates across a section) is counted
  beside it and must reproduce ``counts_tracked`` of the committed observed
  artifact. Also per lane, and the crossings of the auxiliary band (lanes 5–9)
  and the median shoulder (lane 0) at the same sections.
* **Ramp flows** at the builder's ramp count sections (``RAMPS`` of
  ``scripts/i24_build_replica.py``: crossings of lanes 5–9), with the number
  whose fragment was in lanes 1–4 before the crossing (through traffic in the
  ramp lane, e.g. Bell Road exiters already in the weave lane at the Hickory
  Hollow on-ramp count) and the number that returns to lanes 1–4 after it.
  Both are lower bounds: fragments are short and the history is read within
  the loaded chunk only.
* **Storage**: tracked lanes-1–4 vehicles between consecutive sections at
  every window boundary (mean over the 0.2-s slots within ±10 s of the last
  slot before the boundary), so that a window's balance
  ``Q(b) − Q(a) − (on − off) + ΔN/Δt`` is zero for conserved, fully tracked
  traffic.
* **Section coverage**, per lane and pooled, per 15-min window, with the
  estimator behind the recommended coverage (``scripts/i24_coverage.py``:
  the geometric-gamma mixture on 2-s snapshot spacings, eq. G of
  ``calibration.coverage``, the section-crossing coverage eq. S, floored by
  the FD capacity bound), in a 500-m cell centred on each section — the
  quantity the one pooled factor stands in for. Spacings are those of
  followers inside the cell to their next tracked leader wherever it is (the
  rows are loaded ``s_max`` beyond the last cell, and a follower with no
  tracked leader within ``s_max`` enters as a censored spacing), so every
  cell is sampled the same way.
* **Conservation residuals** for each adjacent section pair and for the spans
  2,200→5,400, 3,200→5,400 and 200→5,400:
  ``R = Q(b) − Q(a) − (on − off) [+ ΔN/Δt]``, zero when the counts conserve.
  Variants: tracked (raw); every term divided by the pooled recommended
  coverage of the window (the correction behind 6,626 / 6,639 — "pooled");
  each section divided by its own measured coverage with the ramps at the
  pooled coverage ("section"); with the ramp counts as counted and net of the
  flagged through traffic ("rampadj"). Means over the windows with 95 %
  circular-block (15 min) and i.i.d. bootstrap intervals.
* **The pre-registered reading** of docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.1,
  applied mechanically (:func:`verdict`): whether the peak sections' targets
  are inconsistent with the downstream counts, the targets that would replace
  them, and the model's GEH against them
  (``artifacts/i24_validation_dc_refit.json``).

Writes ``artifacts/i24_count_consistency.json`` with provenance (the data
hash, the table's sha256, the code commit). Memory: one 15-min chunk at a time
(about 3 M rows with its 60-s lead pad; on the order of 1–1.5 GB peak,
estimated), the speed field is never built.

Run (VM):  ``uv run --no-sync python scripts/i24_count_consistency.py``
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from i24_build_replica import RAMPS, T_STUDY_HI_S, T_STUDY_LO_S, WINDOW_S
from i24_coverage import MIN_N_PER_CLASS, SNAPSHOT_DT_S, SPEED_EDGES_KMH, V_MAX_KMH
from i24_critical_gaps import _peak_rss_mb, git_dirty
from i24_data import REPO_ROOT, SAMPLE_DT_S, WB_DIR, clock
from i24_lane_change_gaps import git_head

from calibration.coverage import (
    DEFAULT_S_MAX_M,
    combine_with_bound,
    coverage_capacity_bound,
    coverage_gap_mixture,
    coverage_section_crossings,
)
from calibration.loaders.i24motion import I24_CITATION, load_i24_parquet, sha256_file
from flowstate_core.rng import make_rng
from flowstate_core.units import kmh_to_ms, ms_to_kmh
from validation.metrics import geh

OUT = REPO_ROOT / "artifacts" / "i24_count_consistency.json"
COVERAGE_ARTIFACT = REPO_ROOT / "artifacts" / "i24_coverage.json"
FD_ARTIFACT = REPO_ROOT / "artifacts" / "fd_i24.json"
OBSERVED_ARTIFACT = REPO_ROOT / "artifacts" / "i24_validation_observed.json"
INPUTS_ARTIFACT = REPO_ROOT / "artifacts" / "i24_replica_inputs.json"
MODEL_ARTIFACT = REPO_ROOT / "artifacts" / "i24_validation_dc_refit.json"

#: The validator's count sections (``scripts/i24_validate.py::SECTIONS_M``).
SECTIONS_M: tuple[float, ...] = (200.0, 1000.0, 2200.0, 3200.0, 4800.0, 5400.0)
#: Spans read beyond the adjacent pairs; the first is the one the rules read.
SPANS_M: tuple[tuple[float, float], ...] = ((2200.0, 5400.0), (3200.0, 5400.0), (200.0, 5400.0))
PRIMARY_SPAN_M = (2200.0, 5400.0)
PEAK_SECTIONS_M: tuple[float, ...] = (2200.0, 3200.0)
FAR_END_SECTION_M = 5400.0
#: The along-road profile: crossings and Edie flow every 100 m (cells ±50 m).
PROFILE_X_M: tuple[float, ...] = tuple(float(x) for x in range(100, 5501, 100))
PROFILE_HALF_M = 50.0

MAIN_LANES = (1, 4)
AUX_LANES = (5, 9)
SHOULDER_LANE = 0
CELL_HALF_M = 250.0
PAD_S = 60.0
"""Lead pad per chunk [s]: the validator's own (it loads from 06:29 for 06:30)."""
STORAGE_HALF_SLOTS = 50
"""Storage slab half-width in 0.2-s slots (±10 s)."""
COLUMNS = ["t", "veh_id", "x", "lane", "v"]

N_BOOT = 2000
BLOCK_WINDOWS = 3
SEED = 20261007
EXPLAINED_SHARE = 0.5
"""A correction explains the non-conservation when it leaves at most this share of it."""
MATERIAL_VEH_H = 100.0
"""Smallest primary-span residual read as non-conservation [veh/h]: about 1.5 % of the peak
flow, a third of the 2,200 m target-side term in question (≈ 290–380 veh/h) and a quarter of the
GEH-5 margin on 2-h flows near 6,000 veh/h (≈ 390)."""
GEH_LIMIT = 5.0

OUTCOMES: dict[str, str] = {
    "consistent": (
        "the recording's section counts conserve with its ramp counts under the pooled "
        "correction (the residual's 95 % interval includes 0, or it is below the material "
        "floor): the peak sections' targets stand as consistent, and their shortfall is read "
        "against the model"
    ),
    "inconsistent_section_coverage": (
        "the counts do not conserve under one pooled coverage, and the measured section coverage "
        "explains at least half of it: the peak sections' targets are inconsistent with the "
        "downstream counts and are restated at the section-specific coverage"
    ),
    "ramp_counts_contaminated": (
        "the counts do not conserve, the section coverage does not explain it, and removing the "
        "through traffic flagged in the ramp lanes does: the section targets stand, the ramp counts "
        "(and the model's ramp inputs built from them) are at fault"
    ),
    "inconsistent_section_coverage_and_ramp_contamination": (
        "neither correction alone explains half of the non-conservation, both together do: the "
        "targets are restated at the section-specific coverage and the ramp inputs are flagged"
    ),
    "unexplained": (
        "the counts do not conserve and nothing measured here explains at least half of it: the "
        "peak targets can be neither confirmed nor restated from this recording (external counts "
        "needed); no discharge calibration on the peak sections"
    ),
    "not_evaluable": "the primary span's pooled residual could not be formed",
}


# --------------------------------------------------------------------------
# Layout and period
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Ramp:
    """A ramp counted in the auxiliary band at ``count_x_m`` (sign: on +, off −)."""

    name: str
    kind: str
    count_x_m: float

    @property
    def sign(self) -> int:
        return 1 if self.kind == "on" else -1


@dataclass(frozen=True)
class Layout:
    """Sections, ramps and lane bands of the check (data x, lane indices)."""

    sections_m: tuple[float, ...]
    ramps: tuple[Ramp, ...]
    spans_m: tuple[tuple[float, float], ...] = ()
    primary_span_m: tuple[float, float] | None = None
    peak_sections_m: tuple[float, ...] = ()
    far_end_m: float | None = None
    cell_half_m: float = CELL_HALF_M
    main_lanes: tuple[int, int] = MAIN_LANES
    aux_lanes: tuple[int, int] = AUX_LANES
    profile_x_m: tuple[float, ...] = ()
    profile_half_m: float = PROFILE_HALF_M
    s_max_m: float = DEFAULT_S_MAX_M

    def __post_init__(self) -> None:
        if list(self.sections_m) != sorted(self.sections_m) or len(self.sections_m) < 2:
            raise ValueError("sections_m must be at least two increasing positions")
        for r in self.ramps:
            if r.kind not in ("on", "off"):
                raise ValueError(f"ramp {r.name!r}: kind must be 'on' or 'off'")
        for a, b in self.spans_m:
            if a not in self.sections_m or b not in self.sections_m or a >= b:
                raise ValueError(f"span ({a}, {b}) must join two sections, upstream first")

    @property
    def main_lane_list(self) -> list[int]:
        return list(range(self.main_lanes[0], self.main_lanes[1] + 1))

    def pairs(self) -> list[tuple[float, float, str]]:
        """Adjacent section pairs, then the spans: ``(from, to, kind)``."""
        out = [
            (a, b, "adjacent") for a, b in zip(self.sections_m, self.sections_m[1:], strict=False)
        ]
        return out + [(a, b, "span") for a, b in self.spans_m]

    def ramps_between(self, a: float, b: float) -> list[int]:
        """Indices of the ramps counted in ``(a, b]``."""
        return [i for i, r in enumerate(self.ramps) if a < r.count_x_m <= b]

    def load_x_range(self) -> tuple[float, float]:
        """Rows to load: every cell, ramp and profile section, plus ``s_max`` downstream."""
        lows = [x - self.cell_half_m for x in self.sections_m]
        highs = [x + self.cell_half_m for x in self.sections_m]
        lows += [x - self.profile_half_m for x in self.profile_x_m]
        highs += [x + self.profile_half_m for x in self.profile_x_m]
        lows += [r.count_x_m for r in self.ramps]
        highs += [r.count_x_m for r in self.ramps]
        return min(lows) - 50.0, max(highs) + self.s_max_m + 50.0


def i24_layout(cell_half_m: float = CELL_HALF_M) -> Layout:
    """The validator's sections and the builder's ramps."""
    return Layout(
        sections_m=SECTIONS_M,
        ramps=tuple(Ramp(str(r["name"]), str(r["kind"]), float(r["count_x_m"])) for r in RAMPS),
        spans_m=SPANS_M,
        primary_span_m=PRIMARY_SPAN_M,
        peak_sections_m=PEAK_SECTIONS_M,
        far_end_m=FAR_END_SECTION_M,
        cell_half_m=cell_half_m,
        profile_x_m=PROFILE_X_M,
    )


@dataclass(frozen=True)
class Period:
    """Study period, its windows, and the chunking (one chunk per coverage window)."""

    t_lo_s: float = T_STUDY_LO_S
    t_hi_s: float = T_STUDY_HI_S
    window_s: float = WINDOW_S
    coverage_window_s: float = 900.0
    pad_s: float = PAD_S
    storage_half_slots: int = STORAGE_HALF_SLOTS
    sample_dt_s: float = SAMPLE_DT_S

    def __post_init__(self) -> None:
        span = self.t_hi_s - self.t_lo_s
        per = self.coverage_window_s / self.window_s
        if span <= 0 or abs(per - round(per)) > 1e-9 or per < 1:
            raise ValueError("coverage_window_s must be a whole number of windows")
        n = span / self.coverage_window_s
        if abs(n - round(n)) > 1e-9:
            raise ValueError("the period must be a whole number of coverage windows")
        if self.pad_s < (self.storage_half_slots + 1) * self.sample_dt_s:
            raise ValueError("pad_s must cover the storage slab")

    @property
    def n_windows(self) -> int:
        return round((self.t_hi_s - self.t_lo_s) / self.window_s)

    @property
    def windows_per_chunk(self) -> int:
        return round(self.coverage_window_s / self.window_s)

    @property
    def n_chunks(self) -> int:
        return round((self.t_hi_s - self.t_lo_s) / self.coverage_window_s)

    @property
    def post_pad_s(self) -> float:
        """Rows loaded past a chunk's end: the final boundary's storage slab."""
        return (self.storage_half_slots + 1) * self.sample_dt_s


# --------------------------------------------------------------------------
# Counting primitives
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Steps:
    """Consecutive rows of one fragment within a lane band (row indices and positions)."""

    cur: np.ndarray
    x_prev: np.ndarray
    x_cur: np.ndarray
    veh_cur: np.ndarray


def group_steps(veh: np.ndarray, x: np.ndarray, mask: np.ndarray) -> Steps:
    """Consecutive rows of one fragment within the rows selected by ``mask``.

    The frame is sorted by ``(veh, t)``. A step joins two rows of the same
    fragment that are consecutive *among the selected rows* (the validator
    counts on a frame filtered to lanes 1–4, so a fragment's steps skip its
    samples in other lanes in exactly this way). ``cur`` indexes the later row
    in the sorted frame.
    """
    idx = np.flatnonzero(mask)
    if idx.size < 2:
        e = np.zeros(0, dtype=np.int64)
        return Steps(cur=e, x_prev=np.zeros(0), x_cur=np.zeros(0), veh_cur=e)
    same = veh[idx[1:]] == veh[idx[:-1]]
    prev, cur = idx[:-1][same], idx[1:][same]
    return Steps(cur=cur, x_prev=x[prev], x_cur=x[cur], veh_cur=veh[cur])


def section_crossings(steps: Steps, x_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Crossings of ``x_s`` (``x_prev < x_s <= x_cur``) over the given steps.

    Returns:
        ``(every, first)``: the later-sample row of every crossing (the
        validator's and builder's rule) and of each fragment's first crossing
        only (the rule used here: a fragment counts once per section). Rows
        are in ``(veh, t)`` order, so a fragment's crossings are contiguous and
        its first is the earliest.
    """
    hit = (steps.x_prev < x_s) & (steps.x_cur >= x_s)
    every = steps.cur[hit]
    if every.size == 0:
        return every, every
    hv = steps.veh_cur[hit]
    first = np.ones(every.size, dtype=bool)
    first[1:] = hv[1:] != hv[:-1]
    return every, every[first]


def mainline_history(veh: np.ndarray, main: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per row: was the fragment in the mainline band before it / will it be after it.

    The frame is sorted by ``(veh, t)``; only the loaded rows are seen.
    """
    n = veh.size
    if n == 0:
        return np.zeros(0, dtype=bool), np.zeros(0, dtype=bool)
    m = main.astype(np.int64)
    start = np.ones(n, dtype=bool)
    start[1:] = veh[1:] != veh[:-1]
    seg = np.cumsum(start) - 1
    cs = np.cumsum(m)
    base = (cs - m)[start]  # main rows before each fragment's first row
    incl = cs - base[seg]  # main rows of the fragment up to and including this row
    total = np.bincount(seg, weights=m).astype(np.int64)
    return (incl - m) > 0, (total[seg] - incl) > 0


def snapshot_pairs(
    slot: np.ndarray, x: np.ndarray, v: np.ndarray, *, s_max: float, x_edge: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Snapshot spacings of one lane with each pair's follower position.

    As ``calibration.coverage.snapshot_spacings`` (front-to-front spacing of
    consecutive tracked vehicles at one slot, mean pair speed), plus the
    follower's position, and a censored spacing (``s_max + 1``) for the most
    downstream vehicle of a slot when the loaded rows reach at least ``s_max``
    beyond it (its next tracked leader, if any, is farther than ``s_max``).

    Returns:
        ``(spacing, pair_speed, follower_x)``.
    """
    if slot.size == 0:
        z = np.zeros(0)
        return z, z, z
    order = np.lexsort((x, slot))
    slot, x, v = slot[order], x[order], v[order]
    same = slot[1:] == slot[:-1]
    sp = (x[1:] - x[:-1])[same]
    spd = 0.5 * (v[1:] + v[:-1])[same]
    fx = x[:-1][same]
    last = np.ones(slot.size, dtype=bool)
    last[:-1] = ~same
    keep = (x_edge - x[last]) >= s_max
    return (
        np.concatenate([sp, np.full(int(keep.sum()), s_max + 1.0)]),
        np.concatenate([spd, v[last][keep]]),
        np.concatenate([fx, x[last][keep]]),
    )


def _effective(n: np.ndarray, c: np.ndarray) -> float:
    """Coverage of a summed count, ``Σ N / Σ (N / c)`` over lanes with a finite ``c``."""
    ok = np.isfinite(c) & (c > 0) & (n > 0)
    return float(n[ok].sum() / (n[ok] / c[ok]).sum()) if ok.any() else math.nan


def _num(v: float) -> float | None:
    return float(v) if v is not None and math.isfinite(float(v)) else None


def _mean(a: np.ndarray) -> float:
    """Mean of the finite entries; NaN (without a warning) when there are none."""
    a = np.asarray(a, dtype=np.float64)
    ok = np.isfinite(a)
    return float(a[ok].mean()) if ok.any() else math.nan


# --------------------------------------------------------------------------
# Chunk loop
# --------------------------------------------------------------------------


@dataclass
class Tally:
    """Tracked accumulators over the global 5-min windows, filled chunk by chunk."""

    counts: dict[str, np.ndarray]
    lane_counts: np.ndarray
    ramp: dict[str, np.ndarray]
    edie_vd: np.ndarray
    edie_tt: np.ndarray
    storage: np.ndarray
    profile: dict[str, np.ndarray]
    coverage: list[dict[str, Any]] = field(default_factory=list)
    n_rows: int = 0
    wall_s: dict[str, float] = field(
        default_factory=lambda: {"read": 0.0, "count": 0.0, "fit": 0.0}
    )

    @classmethod
    def empty(cls, layout: Layout, period: Period) -> Tally:
        n_sec, n_win, n_ramp = len(layout.sections_m), period.n_windows, len(layout.ramps)
        n_lanes, n_prof = len(layout.main_lane_list), len(layout.profile_x_m)
        groups = ("main", "main_rule", "aux", "aux_rule", "shoulder", "shoulder_rule")
        return cls(
            counts={g: np.zeros((n_sec, n_win), dtype=np.int64) for g in groups},
            lane_counts=np.zeros((n_sec, n_lanes, n_win), dtype=np.int64),
            ramp={
                k: np.zeros((n_ramp, n_win), dtype=np.int64)
                for k in ("count", "count_rule", "prior_main", "later_main")
            },
            edie_vd=np.zeros((n_sec, n_lanes, n_win)),
            edie_tt=np.zeros((n_sec, n_lanes, n_win)),
            storage=np.full((n_sec - 1, n_win + 1), math.nan),
            profile={
                "main": np.zeros(n_prof, dtype=np.int64),
                "aux": np.zeros(n_prof, dtype=np.int64),
                "main_vd": np.zeros(n_prof),
            },
        )


def _codes(ids: pd.Series) -> np.ndarray:
    arr = ids.to_numpy()
    if arr.dtype.kind in "iu":
        return arr.astype(np.int64)
    return pd.factorize(arr, sort=False)[0].astype(np.int64)


def process_chunk(
    df: pd.DataFrame,
    k: int,
    layout: Layout,
    period: Period,
    tally: Tally,
    *,
    q_cap_fd_veh_s: float,
) -> None:
    """Add chunk ``k`` (one coverage window plus its pads) to ``tally``.

    ``df`` holds every row with ``t`` in ``[lo − pad, hi + post_pad)``, lanes
    0 to ``aux_lanes[1]`` and ``x`` in :meth:`Layout.load_x_range`. Crossings
    are attributed to the window of their later sample and counted only when
    it lies in ``[lo, hi)``, so a crossing is counted in exactly one chunk.
    """
    t_c = time.perf_counter()
    lo = period.t_lo_s + k * period.coverage_window_s
    hi = lo + period.coverage_window_s
    n_win, per = period.n_windows, period.windows_per_chunk
    wins = slice(k * per, (k + 1) * per)
    lanes = layout.main_lane_list
    n_lanes, n_sec = len(lanes), len(layout.sections_m)
    dt = period.sample_dt_s
    tally.n_rows += len(df)
    veh = _codes(df["veh_id"])
    t = df["t"].to_numpy(dtype=np.float64)
    order = np.lexsort((t, veh))
    veh, t = veh[order], t[order]
    x = df["x"].to_numpy(dtype=np.float64)[order]
    lane = df["lane"].to_numpy(dtype=np.int64)[order]
    v = df["v"].to_numpy(dtype=np.float64)[order]
    slot = np.rint(t / dt).astype(np.int64)
    in_chunk = (t >= lo) & (t < hi)
    main = (lane >= layout.main_lanes[0]) & (lane <= layout.main_lanes[1])
    aux = (lane >= layout.aux_lanes[0]) & (lane <= layout.aux_lanes[1])
    shoulder = lane == SHOULDER_LANE
    prior_main, later_main = mainline_history(veh, main)

    def binned(cur: np.ndarray) -> np.ndarray:
        sel = cur[in_chunk[cur]]
        w = ((t[sel] - period.t_lo_s) // period.window_s).astype(np.int64)
        return np.bincount(w, minlength=n_win)[:n_win]

    for name, mask in (("main", main), ("aux", aux), ("shoulder", shoulder)):
        steps = group_steps(veh, x, mask)
        for i, x_s in enumerate(layout.sections_m):
            every, first = section_crossings(steps, x_s)
            tally.counts[name][i] += binned(first)
            tally.counts[f"{name}_rule"][i] += binned(every)
            if name == "main":
                for j, ln in enumerate(lanes):
                    tally.lane_counts[i, j] += binned(first[lane[first] == ln])
        if name in ("main", "aux"):
            for p, x_p in enumerate(layout.profile_x_m):
                _, first = section_crossings(steps, x_p)
                tally.profile[name][p] += int(in_chunk[first].sum())
        if name == "aux":
            for r_i, r in enumerate(layout.ramps):
                every, first = section_crossings(steps, r.count_x_m)
                tally.ramp["count"][r_i] += binned(first)
                tally.ramp["count_rule"][r_i] += binned(every)
                tally.ramp["prior_main"][r_i] += binned(first[prior_main[first]])
                tally.ramp["later_main"][r_i] += binned(first[later_main[first]])
        del steps

    # Edie vehicle-distance and vehicle-time per section cell, lane and window (lanes 1-4).
    sel = in_chunk & main
    ts, xs, ls, vs = t[sel], x[sel], lane[sel] - layout.main_lanes[0], v[sel]
    w_loc = ((ts - period.t_lo_s) // period.window_s).astype(np.int64)
    h = layout.cell_half_m
    for i, x_s in enumerate(layout.sections_m):
        c = (xs >= x_s - h) & (xs < x_s + h)
        key = w_loc[c] * n_lanes + ls[c]
        vd = np.bincount(key, weights=vs[c] * dt, minlength=n_win * n_lanes)
        tt = np.bincount(key, minlength=n_win * n_lanes) * dt
        tally.edie_vd[i] += vd[: n_win * n_lanes].reshape(n_win, n_lanes).T
        tally.edie_tt[i] += tt[: n_win * n_lanes].reshape(n_win, n_lanes).T
    if layout.profile_x_m:
        step = 2.0 * layout.profile_half_m
        p0 = layout.profile_x_m[0] - layout.profile_half_m
        cell = np.floor((xs - p0) / step).astype(np.int64)
        ok = (cell >= 0) & (cell < len(layout.profile_x_m))
        tally.profile["main_vd"] += np.bincount(
            cell[ok], weights=vs[ok] * dt, minlength=len(layout.profile_x_m)
        )[: len(layout.profile_x_m)]

    # Storage: tracked lanes-1-4 vehicles between consecutive sections at each boundary.
    sec = np.asarray(layout.sections_m)
    xm, sm = x[main], slot[main]
    interval = np.searchsorted(sec, xm, side="right") - 1
    valid = (interval >= 0) & (interval < n_sec - 1)
    m = period.storage_half_slots
    bounds = list(range(k * per, (k + 1) * per)) + ([n_win] if k == period.n_chunks - 1 else [])
    for b in bounds:
        big_b = round((period.t_lo_s + b * period.window_s) / dt)
        slab = valid & (sm >= big_b - 1 - m) & (sm <= big_b - 1 + m)
        cnt = np.bincount(interval[slab], minlength=n_sec - 1)[: n_sec - 1]
        tally.storage[:, b] = cnt / (2 * m + 1)
    tally.wall_s["count"] += time.perf_counter() - t_c

    # Section coverage per lane (mixture in the cell, eq. S, FD capacity floor), then pooled.
    t_f = time.perf_counter()
    snap_step = round(SNAPSHOT_DT_S / dt)
    snap = sel & (slot % snap_step == 0)
    x_edge = layout.load_x_range()[1]
    speed_edges = [kmh_to_ms(e) for e in SPEED_EDGES_KMH]
    area = 2.0 * h * period.coverage_window_s
    per_lane: list[list[dict[str, Any]]] = [[] for _ in layout.sections_m]
    for j, ln in enumerate(lanes):
        s_sel = snap & (lane == ln)
        sp, spd, fx = snapshot_pairs(
            slot[s_sel], x[s_sel], v[s_sel], s_max=layout.s_max_m, x_edge=x_edge
        )
        for i, x_s in enumerate(layout.sections_m):
            c = (fx >= x_s - h) & (fx < x_s + h)
            res = coverage_gap_mixture(
                sp[c],
                spd[c],
                speed_edges_ms=speed_edges,
                min_n=MIN_N_PER_CLASS,
                v_max_ms=kmh_to_ms(V_MAX_KMH),
                s_max=layout.s_max_m,
            )
            n_cross = int(tally.lane_counts[i, j, wins].sum())
            q_edie = float(tally.edie_vd[i, j, wins].sum()) / area
            rho = float(tally.edie_tt[i, j, wins].sum()) / area
            c_sec = coverage_section_crossings(n_cross, period.coverage_window_s, q_edie, res.c)
            bound = coverage_capacity_bound(n_cross / period.coverage_window_s, q_cap_fd_veh_s)
            per_lane[i].append(
                {
                    "lane": ln,
                    "n_crossings": n_cross,
                    "q_edie_veh_h": q_edie * 3600.0,
                    "rho_edie_veh_km": rho * 1000.0,
                    "v_edie_kmh": ms_to_kmh(q_edie / rho) if rho > 0 else math.nan,
                    "crossing_to_edie": (
                        n_cross / (period.coverage_window_s * q_edie) if q_edie > 0 else math.nan
                    ),
                    "n_spacings": int(c.sum()),
                    "n_spacings_used": int(res.n_used),
                    "c_vehicle_time": res.c,
                    "c_vehicle_time_class_range": [res.c_min, res.c_max],
                    "c_section": c_sec,
                    "capacity_bound_fd": bound,
                    "c_recommended": (
                        combine_with_bound(c_sec, bound) if math.isfinite(c_sec) else math.nan
                    ),
                }
            )
    for i, x_s in enumerate(layout.sections_m):
        rows = per_lane[i]
        n = np.array([r["n_crossings"] for r in rows], dtype=float)
        rho = np.array([r["rho_edie_veh_km"] for r in rows])
        cvt = np.array([r["c_vehicle_time"] for r in rows], dtype=float)
        ok = np.isfinite(cvt) & (rho > 0)
        c_sec = _effective(n, np.array([r["c_section"] for r in rows], dtype=float))
        bound = _effective(n, np.array([r["capacity_bound_fd"] for r in rows], dtype=float))
        q_tot = sum(r["q_edie_veh_h"] for r in rows)
        tally.coverage.append(
            {
                "k": k,
                "t_lo_s": lo,
                "window": clock(lo),
                "section_m": x_s,
                "lanes": rows,
                "pooled": {
                    "n_crossings": int(n.sum()),
                    "crossing_to_edie": (
                        n.sum() * 3600.0 / (period.coverage_window_s * q_tot)
                        if q_tot > 0
                        else math.nan
                    ),
                    "c_vehicle_time": (
                        float(np.sum(cvt[ok] * rho[ok]) / np.sum(rho[ok])) if ok.any() else math.nan
                    ),
                    "c_section": c_sec,
                    "capacity_bound_fd": bound,
                    "c_recommended": (
                        combine_with_bound(c_sec, bound) if math.isfinite(c_sec) else math.nan
                    ),
                },
            }
        )
    tally.wall_s["fit"] += time.perf_counter() - t_f


def run_check(
    read_chunk: Callable[[float, float], pd.DataFrame],
    layout: Layout,
    period: Period,
    *,
    q_cap_fd_veh_s: float,
    log: Callable[[str], None] | None = None,
) -> Tally:
    """Read the period chunk by chunk (one coverage window each) and tally it."""
    tally = Tally.empty(layout, period)
    for k in range(period.n_chunks):
        lo = period.t_lo_s + k * period.coverage_window_s
        t_r = time.perf_counter()
        df = read_chunk(lo - period.pad_s, lo + period.coverage_window_s + period.post_pad_s)
        tally.wall_s["read"] += time.perf_counter() - t_r
        process_chunk(df, k, layout, period, tally, q_cap_fd_veh_s=q_cap_fd_veh_s)
        if log is not None:
            log(f"chunk {clock(lo)}: {len(df):,} rows  [{sum(tally.wall_s.values()):.0f} s]")
        del df
    return tally


def make_reader(wb_dir: Path, layout: Layout) -> Callable[[float, float], pd.DataFrame]:
    """A chunk reader on the processed table (filters pushed down to Parquet)."""

    def read(t_lo: float, t_hi: float) -> pd.DataFrame:
        return load_i24_parquet(
            wb_dir,
            t_range_s=(t_lo, t_hi),
            x_range_m=layout.load_x_range(),
            lanes=(SHOULDER_LANE, layout.aux_lanes[1]),
            columns=COLUMNS,
        )

    return read


def frame_reader(df: pd.DataFrame, layout: Layout) -> Callable[[float, float], pd.DataFrame]:
    """The same reader over an in-memory frame (tests)."""
    x_lo, x_hi = layout.load_x_range()

    def read(t_lo: float, t_hi: float) -> pd.DataFrame:
        m = (
            (df["t"] >= t_lo)
            & (df["t"] < t_hi)
            & (df["x"] >= x_lo)
            & (df["x"] < x_hi)
            & (df["lane"] >= SHOULDER_LANE)
            & (df["lane"] <= layout.aux_lanes[1])
        )
        return df.loc[m, COLUMNS].reset_index(drop=True)

    return read


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------


def bootstrap_mean(
    values: Sequence[float] | np.ndarray,
    *,
    n_boot: int = N_BOOT,
    block: int = BLOCK_WINDOWS,
    seed: int = SEED,
    level: float = 0.95,
) -> dict[str, Any]:
    """Mean over windows with circular-block and i.i.d. percentile intervals.

    Windows with a non-finite value are dropped first (the block bootstrap
    then runs on the remaining sequence). Fewer than three finite windows give
    no interval.
    """
    vals = np.asarray(values, dtype=np.float64)
    vals = vals[np.isfinite(vals)]
    n = int(vals.size)
    out: dict[str, Any] = {
        "mean": float(vals.mean()) if n else math.nan,
        "n_windows": n,
        "ci_block": None,
        "ci_iid": None,
        "block_windows": block,
        "n_boot": n_boot,
        "seed": seed,
    }
    if n < 3 or n_boot < 1:
        return out
    q = [100.0 * (1.0 - level) / 2.0, 100.0 * (1.0 + level) / 2.0]
    b = max(1, min(block, n))
    n_blocks = math.ceil(n / b)
    starts = make_rng(seed).integers(0, n, size=(n_boot, n_blocks))
    idx = ((starts[:, :, None] + np.arange(b)[None, None, :]) % n).reshape(n_boot, -1)[:, :n]
    out["ci_block"] = [float(v) for v in np.percentile(vals[idx].mean(axis=1), q)]
    iid = make_rng(seed + 1).integers(0, n, size=(n_boot, n))
    out["ci_iid"] = [float(v) for v in np.percentile(vals[iid].mean(axis=1), q)]
    return out


def _fill(a: np.ndarray) -> np.ndarray:
    """Nearest finite value along the last axis (forward, then backward fill)."""
    return pd.DataFrame(np.atleast_2d(a)).T.ffill().bfill().T.to_numpy().reshape(a.shape)


def coverage_matrices(tally: Tally, layout: Layout, period: Period) -> dict[str, np.ndarray]:
    """Pooled section coverage per (section, coverage window), raw and filled."""
    n_sec, n_k = len(layout.sections_m), period.n_chunks
    rec = np.full((n_sec, n_k), math.nan)
    cvt = np.full((n_sec, n_k), math.nan)
    pos = {x: i for i, x in enumerate(layout.sections_m)}
    for row in tally.coverage:
        i = pos[row["section_m"]]
        rec[i, row["k"]] = row["pooled"]["c_recommended"]
        cvt[i, row["k"]] = row["pooled"]["c_vehicle_time"]
    return {"rec": rec, "rec_filled": _fill(rec), "cvt": cvt, "cvt_filled": _fill(cvt)}


def analyze(
    tally: Tally,
    layout: Layout,
    period: Period,
    c_pool: np.ndarray,
    *,
    model: dict[str, Any] | None = None,
    landmarks: dict[str, float] | None = None,
    n_boot: int = N_BOOT,
    block: int = BLOCK_WINDOWS,
    seed: int = SEED,
) -> dict[str, Any]:
    """Flows, coverage, residuals, intervals and the pre-registered reading."""
    f = 3600.0 / period.window_s
    per = period.windows_per_chunk
    secs = list(layout.sections_m)
    pos = {x: i for i, x in enumerate(secs)}
    c_pool = np.asarray(c_pool, dtype=np.float64)
    cov = coverage_matrices(tally, layout, period)
    c_sec_w = np.repeat(cov["rec_filled"], per, axis=1)
    c_vt_w = np.repeat(cov["cvt_filled"], per, axis=1)
    q_raw = tally.counts["main"] * f
    q = {"tracked": q_raw, "pooled": q_raw / c_pool[None, :], "section": q_raw / c_sec_w}
    sign = np.array([r.sign for r in layout.ramps], dtype=float)
    adj_cut = np.array(
        [
            tally.ramp["prior_main"][i] if r.kind == "on" else tally.ramp["later_main"][i]
            for i, r in enumerate(layout.ramps)
        ]
    ).reshape(len(layout.ramps), period.n_windows)
    ramp_raw = tally.ramp["count"] * f
    ramp_adj = (tally.ramp["count"] - adj_cut) * f

    def summ(a: np.ndarray) -> dict[str, Any]:
        return bootstrap_mean(a, n_boot=n_boot, block=block, seed=seed)

    def pw(a: np.ndarray) -> list[float | None]:
        return [_num(round(float(v), 1)) for v in a]

    sections: list[dict[str, Any]] = []
    h = layout.cell_half_m
    for i, x_s in enumerate(secs):
        rows = [r for r in tally.coverage if r["section_m"] == x_s]
        lanes_tab = []
        for j, ln in enumerate(layout.main_lane_list):
            lr = [r["lanes"][j] for r in rows]
            n = np.array([r_["n_crossings"] for r_ in lr], dtype=float)
            lanes_tab.append(
                {
                    "lane": ln,
                    "flow_tracked_veh_h": float(
                        tally.lane_counts[i, j].sum() * f / period.n_windows
                    ),
                    "c_vehicle_time_per_window": [_num(r_["c_vehicle_time"]) for r_ in lr],
                    "c_recommended_per_window": [_num(r_["c_recommended"]) for r_ in lr],
                    "crossing_to_edie_per_window": [_num(r_["crossing_to_edie"]) for r_ in lr],
                    "c_recommended_period": _effective(
                        n, np.array([r_["c_recommended"] for r_ in lr], dtype=float)
                    ),
                    "n_spacings_used": int(sum(r_["n_spacings_used"] for r_ in lr)),
                }
            )
        n_k = np.array([r["pooled"]["n_crossings"] for r in rows], dtype=float)
        sections.append(
            {
                "x_m": x_s,
                "cell_m": [x_s - h, x_s + h],
                "landmarks_in_cell": {
                    k_: round(v_, 1)
                    for k_, v_ in (landmarks or {}).items()
                    if x_s - h <= v_ < x_s + h
                },
                "flow_veh_h": {name: summ(a[i]) for name, a in q.items()},
                "flow_veh_h_per_window": {name: pw(a[i]) for name, a in q.items()},
                "aux_band_tracked_veh_h": float(tally.counts["aux"][i].mean() * f),
                "shoulder_tracked_veh_h": float(tally.counts["shoulder"][i].mean() * f),
                "recrossings": int(
                    tally.counts["main_rule"][i].sum() - tally.counts["main"][i].sum()
                ),
                "coverage": {
                    "c_recommended_per_coverage_window": [_num(v) for v in cov["rec"][i]],
                    "c_vehicle_time_per_coverage_window": [_num(v) for v in cov["cvt"][i]],
                    "crossing_to_edie_per_coverage_window": [
                        _num(r["pooled"]["crossing_to_edie"]) for r in rows
                    ],
                    "c_recommended_period": _effective(n_k, cov["rec"][i]),
                    "n_coverage_windows_filled": int(np.sum(~np.isfinite(cov["rec"][i]))),
                    "per_lane": lanes_tab,
                },
            }
        )

    pairs: list[dict[str, Any]] = []
    primary: dict[str, dict[str, Any]] | None = None
    implied: dict[str, dict[str, Any]] = {}
    for a, b, kind in layout.pairs():
        ia, ib = pos[a], pos[b]
        r_idx = layout.ramps_between(a, b)
        net_raw = (sign[r_idx, None] * ramp_raw[r_idx]).sum(axis=0)
        net_adj = (sign[r_idx, None] * ramp_adj[r_idx]).sum(axis=0)
        s_raw = np.diff(tally.storage[ia:ib].sum(axis=0)) * f
        c_pair = 0.5 * (c_vt_w[ia] + c_vt_w[ib])
        d_raw = q_raw[ib] - q_raw[ia]
        d_sec = q["section"][ib] - q["section"][ia]
        variants = {
            "raw": d_raw - net_raw,
            "raw_storage": d_raw - net_raw + s_raw,
            "pooled": (d_raw - net_raw) / c_pool,
            "pooled_storage": (d_raw - net_raw + s_raw) / c_pool,
            "pooled_storage_rampadj": (d_raw - net_adj + s_raw) / c_pool,
            "section": d_sec - net_raw / c_pool,
            "section_storage": d_sec - net_raw / c_pool + s_raw / c_pair,
            "section_storage_rampadj": d_sec - net_adj / c_pool + s_raw / c_pair,
        }
        res = {name: summ(val) for name, val in variants.items()}
        pairs.append(
            {
                "from_m": a,
                "to_m": b,
                "kind": kind,
                "ramps": [
                    {"name": layout.ramps[r].name, "kind": layout.ramps[r].kind} for r in r_idx
                ],
                "terms_tracked_veh_h_mean": {
                    "section_change": float(d_raw.mean()),
                    "ramps_net_on_minus_off": float(net_raw.mean()),
                    "ramps_net_flagged_through_traffic_removed": float(net_adj.mean()),
                    "storage_rate": _mean(s_raw),
                },
                "residual_veh_h": res,
                "residual_veh_h_per_window": {name: pw(val) for name, val in variants.items()},
            }
        )
        if kind == "span" and b == layout.far_end_m:
            implied[f"{a:g}"] = {
                name: {
                    "flow_from_far_end_and_ramps_veh_h": _mean(q[corr][ia]) + res[name]["mean"],
                    "target_veh_h": _mean(q[corr][ia]),
                }
                for name, corr in (("pooled_storage", "pooled"), ("section_storage", "section"))
                if math.isfinite(res[name]["mean"])
            }
        if layout.primary_span_m is not None and (a, b) == tuple(layout.primary_span_m):
            primary = res

    ramps = []
    for r_i, r in enumerate(layout.ramps):
        ramps.append(
            {
                "name": r.name,
                "kind": r.kind,
                "count_x_m": r.count_x_m,
                "tracked_veh_h": float(tally.ramp["count"][r_i].mean() * f),
                "pooled_veh_h": float(np.mean(tally.ramp["count"][r_i] * f / c_pool)),
                "prior_mainline_tracked_veh_h": float(tally.ramp["prior_main"][r_i].mean() * f),
                "later_mainline_tracked_veh_h": float(tally.ramp["later_main"][r_i].mean() * f),
                "counts_per_window": tally.ramp["count"][r_i].tolist(),
                "prior_mainline_per_window": tally.ramp["prior_main"][r_i].tolist(),
                "later_mainline_per_window": tally.ramp["later_main"][r_i].tolist(),
            }
        )

    diffs = []
    if layout.far_end_m is not None:
        i_far = pos[layout.far_end_m]
        for x_p in layout.peak_sections_m:
            d = cov["rec"][pos[x_p]] - cov["rec"][i_far]
            diffs.append(
                {
                    "upstream_m": x_p,
                    "downstream_m": layout.far_end_m,
                    "per_coverage_window": [_num(v) for v in d],
                    **bootstrap_mean(d, n_boot=n_boot, block=1, seed=seed),
                }
            )

    profile = None
    if layout.profile_x_m:
        span_s = period.t_hi_s - period.t_lo_s
        q_edie = tally.profile["main_vd"] / (2.0 * layout.profile_half_m * span_s) * 3600.0
        q_main = tally.profile["main"] * 3600.0 / span_s
        profile = {
            "x_m": list(layout.profile_x_m),
            "main_crossings_tracked_veh_h": q_main.round(1).tolist(),
            "aux_crossings_tracked_veh_h": (tally.profile["aux"] * 3600.0 / span_s)
            .round(1)
            .tolist(),
            "main_edie_flow_tracked_veh_h": q_edie.round(1).tolist(),
            "crossing_to_edie": [
                _num(round(a_ / b_, 4)) if b_ > 0 else None
                for a_, b_ in zip(q_main, q_edie, strict=True)
            ],
        }

    targets = {
        name: {x_s: _mean(q[name][pos[x_s]]) for x_s in secs} for name in ("pooled", "section")
    }
    return {
        "sections": sections,
        "ramps": ramps,
        "pairs": pairs,
        "coverage_difference_peak_minus_far_end": diffs,
        "conservation_implied_peak_flows": implied,
        "profile": profile,
        "verdict": verdict(primary, targets, model, layout),
    }


def verdict(
    primary: dict[str, dict[str, Any]] | None,
    targets: dict[str, dict[float, float]],
    model: dict[str, Any] | None,
    layout: Layout,
) -> dict[str, Any]:
    """The pre-registered reading (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.1), mechanically.

    On the primary span (2,200 → 5,400 m), storage-adjusted residuals:
    ``Rp`` pooled, ``Rs`` section-specific coverage, ``Ra`` pooled with the
    flagged ramp-lane through traffic removed, ``Rsa`` both. The counts do
    not conserve when ``Rp``'s 95 % block interval excludes 0 and ``|Rp|`` is
    at least ``MATERIAL_VEH_H``. A correction
    explains it when it leaves at most ``EXPLAINED_SHARE`` of ``|Rp|``. The
    targets adopted are the section-specific ones when section coverage
    explains it (alone or together with the ramps), else the pooled ones.
    The model's 2-h section flows are compared with the adopted targets by
    GEH on 2-h hourly flows (the convention of the diagnosis's thresholds,
    not the criteria row's per-window link-hours).
    """

    def mean(name: str) -> float:
        if primary is None or name not in primary:
            return math.nan
        return float(primary[name]["mean"])

    rp, rs, ra, rsa = (
        mean("pooled_storage"),
        mean("section_storage"),
        mean("pooled_storage_rampadj"),
        mean("section_storage_rampadj"),
    )
    ci = None if primary is None else primary.get("pooled_storage", {}).get("ci_block")
    nonconservation = (
        ci is not None
        and (ci[0] > 0.0 or ci[1] < 0.0)
        and math.isfinite(rp)
        and abs(rp) >= MATERIAL_VEH_H
    )

    def explains(r: float) -> bool:
        return nonconservation and math.isfinite(r) and abs(r) <= EXPLAINED_SHARE * abs(rp)

    by_section, by_ramps = explains(rs), explains(ra)
    by_both = not by_section and not by_ramps and explains(rsa)
    if not math.isfinite(rp) or ci is None:
        outcome = "not_evaluable"
    elif not nonconservation:
        outcome = "consistent"
    elif by_section:
        outcome = "inconsistent_section_coverage"
    elif by_ramps:
        outcome = "ramp_counts_contaminated"
    elif by_both:
        outcome = "inconsistent_section_coverage_and_ramp_contamination"
    else:
        outcome = "unexplained"
    adopted = "section" if outcome.startswith("inconsistent_section") else "pooled"
    rows = []
    model_fault = None
    if model is not None:
        flows = {float(k_): float(v_) for k_, v_ in model["flows_veh_h"].items()}
        check = [*layout.peak_sections_m, *([layout.far_end_m] if layout.far_end_m else [])]
        for x_s in check:
            tgt, mod = targets[adopted].get(x_s, math.nan), flows.get(x_s, math.nan)
            g = geh(mod, tgt) if math.isfinite(tgt) and math.isfinite(mod) else math.nan
            rows.append(
                {
                    "x_m": x_s,
                    "target_veh_h": tgt,
                    "original_target_veh_h": targets["pooled"].get(x_s, math.nan),
                    "model_veh_h": mod,
                    "shortfall_veh_h": tgt - mod,
                    "geh_2h": g,
                    "geh_pass": bool(math.isfinite(g) and g < GEH_LIMIT),
                    "peak": x_s in layout.peak_sections_m,
                }
            )
        peak = [r for r in rows if r["peak"] and math.isfinite(r["geh_2h"])]
        if peak and outcome not in ("unexplained", "not_evaluable"):
            model_fault = any(not r["geh_pass"] and r["shortfall_veh_h"] > 0 for r in peak)
    return {
        "rule": "docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.1, fixed 2026-10-07 before any run",
        "primary_span_m": list(layout.primary_span_m) if layout.primary_span_m else None,
        "explained_share": EXPLAINED_SHARE,
        "material_veh_h": MATERIAL_VEH_H,
        "residuals_veh_h": {
            "pooled_storage": rp,
            "section_storage": rs,
            "pooled_storage_rampadj": ra,
            "section_storage_rampadj": rsa,
        },
        "pooled_ci_block": ci,
        "nonconservation": nonconservation,
        "explained_by_section_coverage": by_section,
        "explained_by_ramp_through_traffic": by_ramps,
        "explained_only_by_both": by_both,
        "outcome": outcome,
        "reading": OUTCOMES[outcome],
        "targets_adopted": adopted,
        "model": None
        if model is None
        else {k_: v_ for k_, v_ in model.items() if k_ != "flows_veh_h"},
        "model_vs_targets": rows,
        "peak_shortfall_is_the_models": model_fault,
    }


# --------------------------------------------------------------------------
# Inputs, checks, provenance
# --------------------------------------------------------------------------


def recommended_coverage(cov: dict[str, Any], period: Period) -> np.ndarray:
    """Per-5-min pooled recommended coverage, as ``scripts/i24_validate.py`` derives it."""
    win_s = float(cov["parameters"]["window_s"])
    rows = sorted(cov["windows"], key=lambda w: float(w["t_lo_s"]))
    out = np.empty(period.n_windows)
    for i in range(period.n_windows):
        t = period.t_lo_s + i * period.window_s
        row = next((w for w in rows if float(w["t_lo_s"]) <= t < float(w["t_lo_s"]) + win_s), None)
        if row is None:
            raise ValueError(f"no coverage window contains t={t}")
        out[i] = float(row["pooled"]["recommended_filled"])
    return out


def load_model(path: Path) -> dict[str, Any] | None:
    """2-h mean simulated section flows of a committed validation artifact."""
    if not path.is_file():
        return None
    art = json.loads(path.read_text())
    secs = [float(s) for s in art["observed"]["sections_m"]]
    flows = np.asarray(art["simulated"]["hourly_flows_veh_h_mean"], dtype=float).mean(axis=1)
    return {
        "artifact": rel(path),
        "scenario": art.get("scenario"),
        "arm": art.get("arm"),
        "config_hash": art.get("config_hash"),
        "replicates": art.get("replicates"),
        "flows_veh_h": {f"{s:g}": float(v) for s, v in zip(secs, flows, strict=True)},
    }


def ramp_landmarks(path: Path) -> dict[str, float]:
    """Ramp landmarks on the data axis (chain → data x), as scripts/i24_lanechange_observed.py."""
    if not path.is_file():
        return {}
    geo = json.loads(path.read_text())["geometry"]
    x0, scale = float(geo["data_x0_chain_m"]), float(geo["chain_m_per_data_m"])
    return {k: (float(c) - x0) / scale for k, c in geo["ramp_landmarks_chain_m"].items()}


def consistency_checks(
    tally: Tally,
    layout: Layout,
    period: Period,
    c_pool: np.ndarray,
    data_hash: str,
    *,
    observed: dict[str, Any] | None,
    inputs: dict[str, Any] | None,
    coverage: dict[str, Any],
) -> dict[str, Any]:
    """Do the counts reproduce the committed observed side and the builder's ramp counts?"""
    period_list = [period.t_lo_s, period.t_hi_s]
    out: dict[str, Any] = {}
    if observed is None:
        out["validator_counts"] = {"compared": False, "reason": "observed artifact missing"}
    elif (
        observed.get("data_hash") != data_hash
        or [float(v) for v in observed["t_range_s"]] != period_list
    ):
        out["validator_counts"] = {"compared": False, "reason": "data hash or period differs"}
    else:
        ref = np.asarray(observed["counts_tracked"], dtype=np.int64)
        mine = tally.counts["main_rule"]
        same_shape = ref.shape == mine.shape and list(observed["sections_m"]) == list(
            layout.sections_m
        )
        diff = np.abs(ref - mine) if same_shape else None
        out["validator_counts"] = {
            "compared": bool(same_shape),
            "equal": bool(same_shape and diff is not None and int(diff.max()) == 0),
            "max_abs_diff": None if diff is None else int(diff.max()),
            "n_bins_differing": None if diff is None else int((diff > 0).sum()),
            "rec_coverage_equal_4dp": bool(
                np.allclose(
                    np.round(c_pool, 4), np.asarray(observed["coverage_recommended_per_window"])
                )
            ),
        }
    if inputs is None:
        out["builder_ramp_counts"] = {"compared": False, "reason": "inputs artifact missing"}
    elif (
        inputs.get("data_hash") != data_hash
        or [
            float(inputs["study_period"]["t_lo_s"]),
            float(inputs["study_period"]["t_hi_s"]),
        ]
        != period_list
    ):
        out["builder_ramp_counts"] = {"compared": False, "reason": "data hash or period differs"}
    else:
        by_name = {r["name"]: r for r in inputs["ramps"]}
        rows = []
        for r_i, r in enumerate(layout.ramps):
            ref = np.asarray(by_name[r.name]["ramp_lane_crossings"], dtype=np.int64)
            d = np.abs(ref - tally.ramp["count_rule"][r_i])
            rows.append({"name": r.name, "equal": int(d.max()) == 0, "max_abs_diff": int(d.max())})
        out["builder_ramp_counts"] = {"compared": True, "ramps": rows}
    win_s = float(coverage["parameters"]["window_s"])
    art_rows = {float(w["t_lo_s"]): w["pooled"] for w in coverage["windows"]}
    if layout.sections_m[0] == float(coverage["parameters"]["count_section_x_m"]):
        first = [r for r in tally.coverage if r["section_m"] == layout.sections_m[0]]
        mine = [r["pooled"]["c_recommended"] for r in first]
        ref = [art_rows.get(float(r["t_lo_s"]), {}).get("recommended") for r in first]
        out["first_section_coverage_vs_artifact"] = {
            "note": (
                f"this check's cell [{layout.sections_m[0] - layout.cell_half_m:g}, "
                f"{layout.sections_m[0] + layout.cell_half_m:g}) m with censored terminal spacings "
                f"against the artifact's local stretch {coverage['parameters']['local_x_range_m']} m "
                f"({win_s:g} s windows)"
            ),
            "this_check": [_num(v) for v in mine],
            "artifact_recommended": [_num(v) if v is not None else None for v in ref],
        }
    return out


def reproduces(checks: dict[str, Any]) -> bool | None:
    """True when every comparison made reproduces the committed counts exactly.

    False voids the run (docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.1: its counts would not be the
    targets' counts); None when nothing could be compared (another data hash or period).
    """
    made: list[bool] = []
    vc = checks.get("validator_counts", {})
    if vc.get("compared"):
        made += [bool(vc.get("equal")), bool(vc.get("rec_coverage_equal_4dp"))]
    rc = checks.get("builder_ramp_counts", {})
    if rc.get("compared"):
        made += [bool(r["equal"]) for r in rc.get("ramps", [])]
    return all(made) if made else None


def rel(path: Path) -> str:
    """``path`` relative to the repository when it lies inside it."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def json_safe(obj: Any) -> Any:
    """Non-finite floats → ``None``; numpy scalars → Python; float keys → strings."""
    if isinstance(obj, dict):
        return {(f"{k:g}" if isinstance(k, float) else k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [json_safe(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return json_safe(obj.tolist())
    if isinstance(obj, np.bool_ | bool):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating | float):
        return float(obj) if math.isfinite(float(obj)) else None
    return obj


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--wb-dir", default=str(WB_DIR), help="the processed westbound table's dir")
    ap.add_argument("--out", default=str(OUT), help=f"artifact path (default {rel(OUT)})")
    ap.add_argument(
        "--t-range",
        nargs=2,
        type=float,
        default=[T_STUDY_LO_S, T_STUDY_HI_S],
        metavar=("LO", "HI"),
        help="study period [s after 06:00 CST] (default 06:30-08:30, the validator's)",
    )
    ap.add_argument("--cell-half-m", type=float, default=CELL_HALF_M)
    ap.add_argument("--coverage-artifact", default=str(COVERAGE_ARTIFACT))
    ap.add_argument("--fd-artifact", default=str(FD_ARTIFACT))
    ap.add_argument("--observed-artifact", default=str(OBSERVED_ARTIFACT))
    ap.add_argument("--inputs-artifact", default=str(INPUTS_ARTIFACT))
    ap.add_argument("--model-artifact", default=str(MODEL_ARTIFACT))
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    ap.add_argument("--block-windows", type=int, default=BLOCK_WINDOWS)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args(argv)

    wb_dir = Path(args.wb_dir)
    table = wb_dir / "trajectories.parquet"
    if not table.is_file():
        raise SystemExit(
            f"{table} missing: the I-24 table is cloud-only (launch the VM with --data-set i24)"
        )
    t_start = time.perf_counter()
    meta = json.loads((wb_dir / "meta.json").read_text())
    data_hash = str(meta.get("data_hash"))
    layout = i24_layout(cell_half_m=args.cell_half_m)
    period = Period(t_lo_s=args.t_range[0], t_hi_s=args.t_range[1])
    cov_art = json.loads(Path(args.coverage_artifact).read_text())
    fd = json.loads(Path(args.fd_artifact).read_text())
    q_cap = float(fd["fd"]["ci95"]["q_max"][1])
    c_pool = recommended_coverage(cov_art, period)
    model = load_model(Path(args.model_artifact))
    obs_path, inp_path = Path(args.observed_artifact), Path(args.inputs_artifact)
    observed = json.loads(obs_path.read_text()) if obs_path.is_file() else None
    inputs = json.loads(inp_path.read_text()) if inp_path.is_file() else None

    print(f"count consistency: {table} ({clock(period.t_lo_s)}-{clock(period.t_hi_s)})", flush=True)
    tally = run_check(
        make_reader(wb_dir, layout),
        layout,
        period,
        q_cap_fd_veh_s=q_cap,
        log=lambda s: print(s, flush=True),
    )
    result = analyze(
        tally,
        layout,
        period,
        c_pool,
        model=model,
        landmarks=ramp_landmarks(inp_path),
        n_boot=args.n_boot,
        block=args.block_windows,
        seed=args.seed,
    )
    checks = consistency_checks(
        tally, layout, period, c_pool, data_hash, observed=observed, inputs=inputs, coverage=cov_art
    )
    checks["reproduces_committed_counts"] = reproduces(checks)
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "kind": "observed",
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "scripts/i24_count_consistency.py",
        "code": git_head(),
        "code_dirty": git_dirty(),
        "data_hash": data_hash,
        "table_sha256": sha256_file(table),
        "data": rel(wb_dir),
        "citation": I24_CITATION,
        "spec": "docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.1",
        "question": (
            "Do the recording's section counts conserve with its own ramp counts between the "
            "validator's sections, and does the section-to-section coverage explain where they do not?"
        ),
        "time_origin": "t = seconds after 06:00:00 CST, 30 Nov 2022",
        "x_axis": "data x [m], front bumper, 0 at MM 62.7, westbound",
        "parameters": {
            "period_s": [period.t_lo_s, period.t_hi_s],
            "period_cst": f"{clock(period.t_lo_s)}-{clock(period.t_hi_s)}",
            "window_s": period.window_s,
            "coverage_window_s": period.coverage_window_s,
            "pad_s": period.pad_s,
            "storage_slab_s": (2 * period.storage_half_slots + 1) * period.sample_dt_s,
            "sections_m": list(layout.sections_m),
            "spans_m": [list(s) for s in layout.spans_m],
            "primary_span_m": list(PRIMARY_SPAN_M),
            "ramps": [
                {"name": r.name, "kind": r.kind, "count_x_m": r.count_x_m} for r in layout.ramps
            ],
            "main_lanes": list(layout.main_lanes),
            "aux_lanes": list(layout.aux_lanes),
            "cell_half_m": layout.cell_half_m,
            "load_x_range_m": list(layout.load_x_range()),
            "snapshot_dt_s": SNAPSHOT_DT_S,
            "speed_edges_kmh": list(SPEED_EDGES_KMH),
            "v_max_kmh": V_MAX_KMH,
            "min_n_per_class": MIN_N_PER_CLASS,
            "s_max_m": layout.s_max_m,
            "q_cap_fd_veh_h_lane": q_cap * 3600.0,
            "coverage_artifact": {
                "path": rel(Path(args.coverage_artifact)),
                "created_at": cov_art.get("created_at"),
                "data_hash": cov_art.get("data_hash"),
                "rule": cov_art.get("recommendation", {}).get("rule"),
            },
            "pooled_coverage_per_window": c_pool.round(4).tolist(),
            "bootstrap": {
                "n_boot": args.n_boot,
                "block_windows": args.block_windows,
                "seed": args.seed,
            },
        },
        "definitions": {
            "count": "lanes-1-4 crossings (x_prev < x_s <= x_cur, consecutive samples of a fragment among the lanes-1-4 rows), each fragment once per section at its first crossing, in the window of the later sample",
            "count_rule": "the validator's / builder's crossings_per_window rule (no de-duplication); checks compare it with the committed artifacts",
            "ramp": "crossings of lanes 5-9 at the builder's count section; prior/later mainline = the fragment has a lanes-1-4 sample before / after the crossing within the loaded chunk (lower bounds)",
            "storage": "tracked lanes-1-4 vehicles between the two sections, mean over the slots within the slab centred on the last slot before each window boundary; storage rate = ΔN / window",
            "residual": "Q(b) − Q(a) − (on − off) [+ storage rate], veh/h; 0 when the counts conserve",
            "pooled": "every term divided by the window's pooled recommended coverage (the correction of hourly_flows_veh_h_recommended)",
            "section": "each section divided by its own pooled c_recommended (per 15-min window, nearest window filled), ramps by the pooled recommended coverage, storage by the mean vehicle-time coverage of the two end cells",
            "rampadj": "on-ramp counts minus prior-mainline, off-ramp counts minus later-mainline crossings",
            "c_recommended": "max(eq. S section coverage, FD capacity bound) per lane, pooled as Σ N / Σ (N / c): the recommended estimator of artifacts/i24_coverage.json applied at each section's cell",
            "intervals": "95 % percentile bootstrap over windows: circular blocks of block_windows (primary) and i.i.d.",
        },
        "checks": checks,
        **result,
        "coverage_by_window": tally.coverage,
        "counts": {
            "rows_read": tally.n_rows,
            "wall_s": {k: round(v, 1) for k, v in tally.wall_s.items()}
            | {"total": round(time.perf_counter() - t_start, 1)},
            "peak_rss_mb": round(_peak_rss_mb(), 1),
        },
        "limits": [
            "Coverage-corrected values carry the coverage estimator's own error, which the window bootstrap does not include.",
            "Section cells overlapping a ramp zone (1,000 m in the Old Hickory acceleration lane, 4,800 m in the Hickory Hollow-Bell Road weave) hold lanes-1-4 flow that changes along the cell; a centred cell is right to first order only.",
            "Ramp-lane coverage cannot be estimated from the data (artifacts/i24_coverage_lane5.json); ramps are corrected by the pooled mainline coverage, as the builder does.",
            "The through-traffic flags read the fragment's history inside the loaded chunk only; they are lower bounds.",
            "One day (30 Nov 2022), one direction.",
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(json_safe(artifact), indent=1, allow_nan=False))

    v = result["verdict"]
    print(f"\nchecks: {json.dumps(json_safe(checks))[:400]}")
    for p in result["pairs"]:
        r = p["residual_veh_h"]
        print(
            f"{p['from_m']:>6.0f} -> {p['to_m']:<6.0f} {p['kind']:8s} "
            + "  ".join(
                f"{k}={r[k]['mean']:+.0f}"
                for k in (
                    "raw_storage",
                    "pooled_storage",
                    "section_storage",
                    "pooled_storage_rampadj",
                )
            )
        )
    if checks["reproduces_committed_counts"] is False:
        print("\nVOID: the counts do not reproduce the committed observed side or ramp inputs")
    print(f"\noutcome: {v['outcome']} — {v['reading']}")
    for row in v["model_vs_targets"]:
        print(
            f"  {row['x_m']:.0f} m: target {row['target_veh_h']:.0f} (was {row['original_target_veh_h']:.0f}), "
            f"model {row['model_veh_h']:.0f}, GEH {row['geh_2h']:.2f}"
        )
    print(f"-> {out}  ({time.perf_counter() - t_start:.0f} s, peak {_peak_rss_mb():.0f} MB)")


if __name__ == "__main__":
    main()
