"""Coverage thinning: a complete trajectory table made to look like a partially tracked one (WP-91).

I-24 MOTION tracks about 0.5–0.65 of the peak vehicle-time (docs/I24_DATA.md
§4), as fragments: every document is a piece of one vehicle's track, median
9.9 s long, with a new tracker id (docs/I24_DATA.md §2). Under that coverage
only some lane-change quantities are bounds (docs/WEAVE_MODEL_PLAN.md,
"corrections from the review of the day's analysis code", table (a)): space
gaps, lead time gaps, the leader side's ``ratio_eq`` and the refusals of the
lead time term. The others (lag time gaps, the acceptance's overall refusal
share, fitted critical gaps, ``ratio_pop``) have only expected directions.
NGSIM US-101 is complete in coverage. Thinning it to I-24-like coverage and
measuring again tells, empirically, how far and which way that coverage moves
each quantity (docs/PAPER_DRAFT.md §3.4 makes the same point for flows and
waves).

Two thinning models, each seeded (``flowstate_core.rng.make_rng``) and each a
pure function of the input frame, the fraction and the seed (the row order of
the input does not matter: vehicles are visited in sorted-id order).

* :func:`thin_vehicles` (**vehicle-level**): keep ``round(F · n)`` of the
  ``n`` vehicle ids, drawn without replacement, with their whole tracks and
  their own ids. A vehicle is either tracked throughout or never, so only the
  neighbour a tracked vehicle sees changes (the nearest *tracked* one).
* :func:`thin_fragments` (**fragment-level**, closer to I-24): every vehicle's
  time axis is cut by an alternating renewal process into *tracked* spells
  (fragments) and *untracked* spells. Fragment durations are log-normal with
  I-24's committed median (9.9 s) and a scale set by I-24's committed share of
  fragments lasting 30 s or more (62,784 of 576,511, 10.9 %):
  ``σ = ln(30 / 9.9) / Φ⁻¹(1 − 0.1089) = 0.900``. That distribution reproduces
  the committed p90 (31.4 s; the model gives 31.36 s) and every bin of the
  committed duration histogram within 0.018 (:data:`I24_FRAGMENT_HISTOGRAM`;
  a test checks it). Untracked spells are log-normal with the same ``σ``
  (an assumption: I-24 publishes no statistic of them) and the mean that
  makes the tracked share ``F``: ``E[off] = E[on] · (1 − F) / F``. At
  ``F = 1`` the untracked spells have zero length: the track is only cut into
  fragments. The process starts ``burn_in_s`` before each vehicle's first
  sample (default the larger of 300 s and 20 mean cycles), so the state at
  the first sample is close to the process's stationary state and the
  expected kept fraction is ``F``. A row is kept when its time lies in a
  tracked spell ``[start, end)``. Each kept fragment gets a new tracker id,
  ``id_prefix`` plus a number drawn by a seeded permutation, as I-24's
  documents carry unrelated ids, so an extraction that follows a vehicle
  across a fragment switch must do it by position (the stitching of
  ``calibration.lane_change_relaxation``, WP-78's 2 m rule).

**What the thinning keeps and what it does not.** Rows are only removed; no
row is invented or altered except ``veh_id`` (fragment-level). The kept
vehicle-time fraction is reported as achieved (kept rows over input rows; on
a table sampled on one shared grid, rows are vehicle-time). Neither model
reproduces I-24's losses as they are: I-24's are correlated in space (holes at
camera boundaries and under overpasses, docs/I24_DATA.md §4) and by lane
(lane 1 tracked at 0.70–0.76, interior lane 3 at 0.40–0.54, "Tracking coverage
revisited"), and I-24 also has duplicate fragments (1.4–1.9 % of spacings) and
position noise. Both models lose tracking independently of position, lane and
neighbours; on a short site (US-101: 640 m) fragments are also truncated by
the ends of each track.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.special import ndtri

THINNING_MODELS: Final[tuple[str, ...]] = ("vehicle", "fragment")
"""The thinning models (:func:`thin_frame`)."""

I24_FRAGMENT_MEDIAN_S: Final[float] = 9.9
"""Median I-24 MOTION fragment duration [s] (docs/I24_DATA.md §2, westbound 30 Nov 2022)."""

I24_FRAGMENT_P90_S: Final[float] = 31.4
"""90th percentile of the I-24 MOTION fragment duration [s] (docs/I24_DATA.md §2)."""

I24_FRAGMENT_HISTOGRAM: Final[tuple[tuple[float, float, int], ...]] = (
    (0.0, 5.0, 137_211),
    (5.0, 10.0, 151_513),
    (10.0, 20.0, 161_446),
    (20.0, 30.0, 63_557),
    (30.0, 60.0, 49_293),
    (60.0, 120.0, 11_915),
    (120.0, 300.0, 1_567),
    (300.0, math.inf, 9),
)
"""The committed I-24 MOTION fragment-duration histogram ``(lo_s, hi_s, count)``
(docs/I24_DATA.md §2): 576,511 fragments, 62,784 (10.9 %) lasting 30 s or more."""

I24_FRAGMENT_SHARE_GE_30S: Final[float] = sum(
    c for lo, _, c in I24_FRAGMENT_HISTOGRAM if lo >= 30.0
) / sum(c for _, _, c in I24_FRAGMENT_HISTOGRAM)
"""Share of I-24 MOTION fragments lasting 30 s or more (62,784 / 576,511 = 0.1089)."""


def lognormal_sigma(median_s: float, threshold_s: float, share_above: float) -> float:
    """The log-normal scale ``σ`` with the given median and share above ``threshold_s``.

    ``P(D ≥ threshold) = 1 − Φ((ln threshold − ln median) / σ)``, solved for ``σ``.

    Raises:
        ValueError: Unless ``0 < median < threshold`` and ``0 < share_above < 0.5``.
    """
    if not (0.0 < median_s < threshold_s) or not (0.0 < share_above < 0.5):
        raise ValueError("need 0 < median_s < threshold_s and 0 < share_above < 0.5")
    return math.log(threshold_s / median_s) / float(ndtri(1.0 - share_above))


I24_FRAGMENT_SIGMA: Final[float] = lognormal_sigma(
    I24_FRAGMENT_MEDIAN_S, 30.0, I24_FRAGMENT_SHARE_GE_30S
)
"""Log-normal scale of the fragment durations (0.8996): I-24's median and ≥ 30 s share."""

DEFAULT_MIN_BURN_IN_S: Final[float] = 300.0
"""Smallest burn-in of the renewal process before a vehicle's first sample [s]."""

BURN_IN_CYCLES: Final[float] = 20.0
"""Default burn-in in mean (tracked + untracked) cycles."""


@dataclass
class ThinnedFrame:
    """A thinned trajectory frame and what was kept.

    Attributes:
        frame: The kept rows of the input, in input order with a fresh
            ``RangeIndex``; every column as in the input except ``veh_id``,
            which fragment-level thinning replaces by the fragment's tracker id.
        source_rows: Position in the input of each row of ``frame``.
        fragments: One row per kept fragment (fragment-level) or kept vehicle
            (vehicle-level): ``fragment_id`` (the id in ``frame``),
            ``source_id`` (the input's ``veh_id``), ``t_start``, ``t_end``
            (first and last kept sample [s]), ``duration_s`` (``t_end −
            t_start``), ``n_rows``, ``at_track_start`` / ``at_track_end``
            (the fragment holds the vehicle's first / last input sample: it is
            truncated by the track, not by the renewal process).
        kept_time_fraction: Kept rows over input rows (vehicle-time on one
            shared sampling grid).
        counts: ``n_rows``, ``n_rows_kept``, ``n_vehicles``,
            ``n_vehicles_kept`` (with at least one kept row), ``n_fragments``.
        parameters: The model and its parameters, for artifacts.
    """

    frame: pd.DataFrame
    source_rows: NDArray[np.int64]
    fragments: pd.DataFrame
    kept_time_fraction: float
    counts: dict[str, int]
    parameters: dict[str, Any] = field(default_factory=dict)


def _check(df: pd.DataFrame, fraction: float) -> None:
    missing = {"t", "veh_id"} - set(df.columns)
    if missing:
        raise ValueError(f"df is missing columns: {sorted(missing)}")
    if not (0.0 < float(fraction) <= 1.0) or not math.isfinite(float(fraction)):
        raise ValueError(f"fraction must lie in (0, 1], got {fraction}")


def _vehicles(
    df: pd.DataFrame,
) -> tuple[NDArray[np.int64], NDArray[np.object_], NDArray[np.float64]]:
    """Vehicle codes in sorted-id order, the sorted labels, and the times."""
    codes, labels = pd.factorize(df["veh_id"].astype(str), sort=True)
    return (
        np.asarray(codes, dtype=np.int64),
        np.asarray(labels, dtype=object),
        df["t"].to_numpy(dtype=np.float64),
    )


def _fragment_table(
    keep_rows: NDArray[np.int64],
    frag_of_row: NDArray[np.int64],
    new_ids: NDArray[np.object_],
    source_ids: NDArray[np.object_],
    t: NDArray[np.float64],
    first_row: NDArray[np.bool_],
    last_row: NDArray[np.bool_],
) -> pd.DataFrame:
    """One row per fragment from the kept rows and each row's fragment number."""
    cols = [
        "fragment_id",
        "source_id",
        "t_start",
        "t_end",
        "duration_s",
        "n_rows",
        "at_track_start",
        "at_track_end",
    ]
    if keep_rows.size == 0:
        return pd.DataFrame({c: pd.Series(dtype=object) for c in cols})
    frag = frag_of_row[keep_rows]
    tk = t[keep_rows]
    n_frag = int(new_ids.size)
    t_start = np.full(n_frag, np.inf)
    t_end = np.full(n_frag, -np.inf)
    np.minimum.at(t_start, frag, tk)
    np.maximum.at(t_end, frag, tk)
    n_rows = np.bincount(frag, minlength=n_frag).astype(np.int64)
    at_start = np.zeros(n_frag, dtype=bool)
    at_end = np.zeros(n_frag, dtype=bool)
    np.logical_or.at(at_start, frag, first_row[keep_rows])
    np.logical_or.at(at_end, frag, last_row[keep_rows])
    return pd.DataFrame(
        {
            "fragment_id": new_ids,
            "source_id": source_ids,
            "t_start": t_start,
            "t_end": t_end,
            "duration_s": t_end - t_start,
            "n_rows": n_rows,
            "at_track_start": at_start,
            "at_track_end": at_end,
        }
    )


def _track_ends(
    codes: NDArray[np.int64], t: NDArray[np.float64], n_veh: int
) -> tuple[NDArray[np.bool_], NDArray[np.bool_], NDArray[np.float64], NDArray[np.float64]]:
    """Per row: is it its vehicle's first / last sample; per vehicle: first and last time."""
    t_min = np.full(n_veh, np.inf)
    t_max = np.full(n_veh, -np.inf)
    np.minimum.at(t_min, codes, t)
    np.maximum.at(t_max, codes, t)
    return t == t_min[codes], t == t_max[codes], t_min, t_max


def thin_vehicles(df: pd.DataFrame, fraction: float, *, seed: int) -> ThinnedFrame:
    """Vehicle-level thinning: keep ``round(fraction · n)`` whole vehicles (module docstring).

    Args:
        df: A trajectory frame with at least ``t`` [s] and ``veh_id``.
        fraction: Share of vehicle ids kept, in (0, 1].
        seed: RNG seed.

    Returns:
        :class:`ThinnedFrame` (ids unchanged; one ``fragments`` row per kept vehicle).

    Raises:
        ValueError: On missing columns or a fraction outside (0, 1].
    """
    from flowstate_core.rng import make_rng

    _check(df, fraction)
    codes, labels, t = _vehicles(df)
    n_veh = int(labels.size)
    n_keep = min(n_veh, max(1, round(fraction * n_veh))) if n_veh else 0
    chosen = np.zeros(0, dtype=np.int64)
    if n_keep:
        chosen = np.asarray(
            make_rng(seed).choice(n_veh, size=n_keep, replace=False), dtype=np.int64
        )
    kept_veh = np.zeros(n_veh, dtype=bool)
    kept_veh[chosen] = True
    keep_rows = np.flatnonzero(kept_veh[codes]) if codes.size else np.zeros(0, dtype=np.int64)
    first, last, _, _ = _track_ends(codes, t, n_veh)
    # fragment number = the vehicle's rank among the kept vehicles
    rank = np.cumsum(kept_veh) - 1
    frag_of_row = np.where(kept_veh[codes], rank[codes], -1) if codes.size else codes
    kept_labels = labels[kept_veh]
    # the fragment id is the id as it stands in the frame (not its string form)
    first_pos = np.zeros(n_veh, dtype=np.int64)
    first_pos[codes[::-1]] = np.arange(codes.size - 1, -1, -1)
    kept_ids = df["veh_id"].to_numpy(dtype=object)[first_pos[kept_veh]]
    fragments = _fragment_table(
        keep_rows.astype(np.int64), frag_of_row, kept_ids, kept_labels, t, first, last
    )
    frame = df.iloc[keep_rows].reset_index(drop=True)
    n_rows = len(df)
    return ThinnedFrame(
        frame=frame,
        source_rows=keep_rows.astype(np.int64),
        fragments=fragments,
        kept_time_fraction=float(keep_rows.size / n_rows) if n_rows else math.nan,
        counts={
            "n_rows": n_rows,
            "n_rows_kept": int(keep_rows.size),
            "n_vehicles": n_veh,
            "n_vehicles_kept": int(n_keep),
            "n_fragments": int(n_keep),
        },
        parameters={"model": "vehicle", "fraction": float(fraction), "seed": int(seed)},
    )


def _lognormal_mu(mean: float, sigma: float) -> float:
    """``μ`` of a log-normal with the given mean and ``σ``."""
    return math.log(mean) - 0.5 * sigma * sigma


def thin_fragments(
    df: pd.DataFrame,
    fraction: float,
    *,
    seed: int,
    fragment_median_s: float = I24_FRAGMENT_MEDIAN_S,
    fragment_sigma: float = I24_FRAGMENT_SIGMA,
    gap_sigma: float | None = None,
    burn_in_s: float | None = None,
    id_prefix: str = "frag-",
) -> ThinnedFrame:
    """Fragment-level thinning: tracked and untracked spells per vehicle (module docstring).

    Args:
        df: A trajectory frame with at least ``t`` [s] and ``veh_id``.
        fraction: Expected share of each vehicle's time that is tracked, in (0, 1].
        seed: RNG seed.
        fragment_median_s: Median tracked-spell duration [s] (I-24's 9.9 s).
        fragment_sigma: Log-normal scale of the tracked spells (I-24's 0.900).
        gap_sigma: Log-normal scale of the untracked spells; None = ``fragment_sigma``.
        burn_in_s: How long before each vehicle's first sample the process
            starts [s]; None = the larger of 300 s and 20 mean cycles.
        id_prefix: Prefix of the new tracker ids (``<prefix><number>``).

    Returns:
        :class:`ThinnedFrame` with new ids.

    Raises:
        ValueError: On missing columns, a fraction outside (0, 1] or a
            non-positive median or scale.
    """
    from flowstate_core.rng import make_rng

    _check(df, fraction)
    if not fragment_median_s > 0.0 or not fragment_sigma > 0.0:
        raise ValueError("fragment_median_s and fragment_sigma must be positive")
    g_sigma = fragment_sigma if gap_sigma is None else float(gap_sigma)
    if not g_sigma > 0.0:
        raise ValueError("gap_sigma must be positive")
    f = float(fraction)
    mu_on = math.log(fragment_median_s)
    mean_on = math.exp(mu_on + 0.5 * fragment_sigma**2)
    mean_off = mean_on * (1.0 - f) / f
    mu_off = _lognormal_mu(mean_off, g_sigma) if mean_off > 0.0 else -math.inf
    cycle = mean_on + mean_off
    burn = max(DEFAULT_MIN_BURN_IN_S, BURN_IN_CYCLES * cycle) if burn_in_s is None else burn_in_s
    if burn < 0.0:
        raise ValueError("burn_in_s must be >= 0")

    codes, labels, t = _vehicles(df)
    n_veh = int(labels.size)
    first, last, t_min, t_max = _track_ends(codes, t, n_veh)
    rng = make_rng(seed)
    order = np.lexsort((t, codes)) if codes.size else codes
    bounds = np.searchsorted(codes[order], np.arange(n_veh + 1)) if codes.size else codes
    frag_local = np.full(t.size, -1, dtype=np.int64)
    for v in range(n_veh):
        rows = order[bounds[v] : bounds[v + 1]]
        t0 = float(t_min[v]) - burn
        span = float(t_max[v]) - t0
        on_parts: list[NDArray[np.float64]] = []
        off_parts: list[NDArray[np.float64]] = []
        total = 0.0
        while total <= span:
            k = max(8, math.ceil(1.5 * (span - total) / cycle) + 4)
            on = rng.lognormal(mu_on, fragment_sigma, k)
            off = rng.lognormal(mu_off, g_sigma, k) if mean_off > 0.0 else np.zeros(k)
            on_parts.append(on)
            off_parts.append(off)
            total += float(on.sum() + off.sum())
        on_all = np.concatenate(on_parts)
        off_all = np.concatenate(off_parts)
        # edges: start_0, end_0, start_1, end_1, ...; spell i is tracked on [start_i, end_i)
        steps = np.empty(2 * on_all.size)
        steps[0::2] = on_all
        steps[1::2] = off_all
        edges = t0 + np.concatenate(([0.0], np.cumsum(steps)[:-1]))
        idx = np.searchsorted(edges, t[rows], side="right") - 1
        tracked = (idx >= 0) & (idx % 2 == 0)
        frag_local[rows] = np.where(tracked, idx // 2, -1)
    keep = frag_local >= 0
    keep_rows = np.flatnonzero(keep).astype(np.int64)
    # number the fragments: (vehicle, spell) pairs that hold a kept row
    pair = (
        codes[keep_rows].astype(np.int64) * (int(frag_local.max(initial=0)) + 1)
        + frag_local[keep_rows]
    )
    uniq, inv = np.unique(pair, return_inverse=True)
    n_frag = int(uniq.size)
    width = max(len(str(max(n_frag - 1, 0))), 1)
    perm = rng.permutation(n_frag) if n_frag else np.zeros(0, dtype=np.int64)
    new_ids = np.array([f"{id_prefix}{int(i):0{width}d}" for i in perm], dtype=object)
    frag_of_row = np.full(t.size, -1, dtype=np.int64)
    frag_of_row[keep_rows] = inv.astype(np.int64)
    src_of_frag = np.empty(n_frag, dtype=object)
    if n_frag:
        src_of_frag[inv] = labels[codes[keep_rows]]
    fragments = _fragment_table(keep_rows, frag_of_row, new_ids, src_of_frag, t, first, last)
    frame = df.iloc[keep_rows].reset_index(drop=True)
    if n_frag:
        frame["veh_id"] = new_ids[inv]
    n_rows = len(df)
    return ThinnedFrame(
        frame=frame,
        source_rows=keep_rows,
        fragments=fragments,
        kept_time_fraction=float(keep_rows.size / n_rows) if n_rows else math.nan,
        counts={
            "n_rows": n_rows,
            "n_rows_kept": int(keep_rows.size),
            "n_vehicles": n_veh,
            "n_vehicles_kept": int(np.unique(codes[keep_rows]).size),
            "n_fragments": n_frag,
        },
        parameters={
            "model": "fragment",
            "fraction": f,
            "seed": int(seed),
            "fragment_median_s": fragment_median_s,
            "fragment_sigma": fragment_sigma,
            "fragment_mean_s": mean_on,
            "gap_sigma": g_sigma,
            "gap_mean_s": mean_off,
            "burn_in_s": burn,
            "id_prefix": id_prefix,
        },
    )


def thin_frame(
    df: pd.DataFrame,
    model: str,
    fraction: float,
    *,
    seed: int,
    options: Mapping[str, Any] | None = None,
) -> ThinnedFrame:
    """Dispatch to :func:`thin_vehicles` or :func:`thin_fragments`.

    Args:
        df: The trajectory frame.
        model: One of :data:`THINNING_MODELS`.
        fraction: The kept fraction, in (0, 1].
        seed: RNG seed.
        options: Keyword arguments of :func:`thin_fragments` (fragment model only).

    Raises:
        ValueError: On an unknown model, or options given to the vehicle model.
    """
    if model == "vehicle":
        if options:
            raise ValueError("the vehicle model takes no options")
        return thin_vehicles(df, fraction, seed=seed)
    if model == "fragment":
        return thin_fragments(df, fraction, seed=seed, **dict(options or {}))
    raise ValueError(f"model must be one of {THINNING_MODELS}, got {model!r}")


def lognormal_bin_shares(
    edges: tuple[tuple[float, float, int], ...] = I24_FRAGMENT_HISTOGRAM,
    *,
    median_s: float = I24_FRAGMENT_MEDIAN_S,
    sigma: float = I24_FRAGMENT_SIGMA,
) -> list[tuple[float, float]]:
    """``(observed share, model share)`` per histogram bin, for the fragment model's check."""
    from scipy.special import ndtr

    total = sum(c for _, _, c in edges)

    def cdf(x: float) -> float:
        if x <= 0.0:
            return 0.0
        if math.isinf(x):
            return 1.0
        return float(ndtr((math.log(x) - math.log(median_s)) / sigma))

    return [(c / total, cdf(hi) - cdf(lo)) for lo, hi, c in edges]
