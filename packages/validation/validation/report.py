"""Auto-generated validation report (CLAUDE.md §7.4) — a product feature.

``generate_report`` renders a markdown calibration/validation report from a
directory of run results: provenance (config hash, seeds, and the package
and SUMO versions of *every* run — a run set mixing engine versions is
listed value by value and carries a comparability warning, CLAUDE.md §9),
calibration artifacts used, the acceptance criteria table
(:mod:`validation.criteria`), metric tables with replicate confidence
intervals (:mod:`validation.metrics`), and speed-contour figures rendered
beside the report. Seeded-perturbation runs are labeled prominently
(CLAUDE.md §0.2). Macro-only run sets are refused (CLAUDE.md §5.6): the
screening tier cannot support validation claims.

Every metric, figure and criterion describes the same measurement window:
each run's recorded period minus its configured warm-up
(:func:`validation.metrics.warmup_from_meta`). The wave-speed criterion is
measured with the active profile's own detector on fields binned at that
detector's bins — reusing the metrics module's ``standard``-detector reading
would make the row unscoreable, since :func:`validation.criteria.evaluate`
refuses a value produced by another recipe.

Microscopic runs are grouped by ``config_hash`` — one configuration per group,
labeled from its ``config.av`` block (``baseline`` for uncontrolled fleets,
otherwise controller, penetration and compliance) — and every group gets its
own metric table and replicate check. When a baseline group and at least one
controlled group are present, the report adds a **controller minus baseline**
contrast per metric (:func:`contrast`): seed-paired when both groups ran the
same seed set (common random numbers), Welch's unequal-variance interval
otherwise, with a ``resolved`` flag when the confidence interval excludes
zero — the conventions of docs/CONTROLLER_COMPARISON.md. Speed contours are
then rendered as baseline-versus-controller pairs per matched seed.

Every number in the rendered report is a computed value passed into the
Jinja2 template (packaged at ``validation/templates/report.md.j2``); the
template body contains no free-text numerals (CLAUDE.md §7.4). The optional
PDF (``pdf=True``, :mod:`validation.report_pdf`) is a rendering of that same
markdown text, never a second source of numbers.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, overload

import matplotlib
import numpy as np
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from scipy.stats import t as student_t

from validation.criteria import CriteriaProfile, CriteriaResult, evaluate
from validation.fields import SpeedField, speed_field
from validation.metrics import (
    CI,
    CI_LEVEL,
    MIN_REPLICATES,
    Metrics,
    aggregate,
    compute_metrics,
    default_travel_span,
    warmup_from_meta,
)
from validation.waves import WaveDetector, get_detector

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_NAME = "report.md.j2"

#: meta.json tier values that mark macroscopic screening runs (contract §3).
MACRO_TIERS = frozenset({"macro", "screening"})

#: Group label for configurations with no controlled vehicles.
BASELINE_LABEL = "baseline"

#: Dimensionless fraction → percent (not an SI unit conversion; kept in one
#: place so no bare ``* 100`` appears in the rendering code).
_PERCENT = 100.0


class ReportRefusedError(RuntimeError):
    """Raised when a report is requested from macro-only (screening) runs.

    The macroscopic LWR/CTM tier is string-stable by construction and cannot
    support phantom-wave validation claims; the report generator therefore
    refuses run sets containing no microscopic runs (CLAUDE.md §5.6).
    """


@dataclass(frozen=True)
class _RunInfo:
    """One discovered run directory plus its parsed metadata."""

    path: Path
    meta: dict[str, Any]

    @property
    def is_macro(self) -> bool:
        return str(self.meta.get("tier", "")) in MACRO_TIERS

    @property
    def seed(self) -> str:
        return str(self.meta.get("seed", "unknown"))

    @property
    def seeded(self) -> bool:
        return bool(self.meta.get("seeded", False))

    @property
    def config_hash(self) -> str:
        return str(self.meta.get("config_hash", "unknown"))

    @property
    def warmup_s(self) -> float:
        """The run's configured metrics warm-up [s] (discarded from metrics)."""
        return warmup_from_meta(self.meta)


@dataclass(frozen=True)
class DeltaCI:
    """One controller-minus-baseline contrast with its replicate interval.

    Attributes:
        mean: Mean difference (other minus baseline) in the metric's unit.
        lo95: Lower two-sided :data:`validation.metrics.CI_LEVEL` bound
            (NaN when fewer than two contributing values).
        hi95: Upper bound, likewise.
        n: Number of contributing values — matched seed pairs when
            ``method == "paired"``, the smaller group size under Welch.
        method: ``"paired"`` when both groups share exactly the same seed
            set (common random numbers, per-seed differences), ``"welch"``
            otherwise (unequal-variance two-sample interval with the
            Welch–Satterthwaite degrees of freedom).
        resolved: True when the interval excludes zero — the difference is
            statistically distinguishable from no effect at
            :data:`validation.metrics.CI_LEVEL`. Never true for an
            undefined interval.
        pct_of_baseline: ``mean`` as a percentage of the baseline mean
            (NaN when the baseline mean is zero or undefined).
    """

    mean: float
    lo95: float
    hi95: float
    n: int
    method: Literal["paired", "welch"]
    resolved: bool
    pct_of_baseline: float


def contrast(baseline: Mapping[str, float], other: Mapping[str, float]) -> DeltaCI:
    """Difference ``other − baseline`` of one metric with a replicate CI.

    Both arguments map a seed label to that replicate's metric value; NaN
    values (metric undefined for that run) are dropped. When the two seed
    sets are identical the runs are common-random-number replicates and the
    interval is the t-interval over the per-seed differences (the paired
    convention of docs/CONTROLLER_COMPARISON.md, tighter than the marginal
    intervals). Otherwise it is Welch's unequal-variance interval on the
    difference of means. With fewer than two contributing values (pairs or
    per-group values) the bounds are NaN and ``resolved`` is False.

    Args:
        baseline: Seed label → metric value for the baseline group.
        other: Seed label → metric value for the compared group.

    Returns:
        A :class:`DeltaCI`.
    """
    q = 0.5 + CI_LEVEL / 2.0
    base_finite = {k: float(v) for k, v in baseline.items() if math.isfinite(float(v))}
    other_finite = {k: float(v) for k, v in other.items() if math.isfinite(float(v))}
    base_mean = float(np.mean(list(base_finite.values()))) if base_finite else math.nan

    method: Literal["paired", "welch"]
    if set(baseline) == set(other):
        method = "paired"
        deltas = np.asarray(
            [other_finite[k] - base_finite[k] for k in base_finite if k in other_finite],
            dtype=np.float64,
        )
        n = int(deltas.size)
        if n == 0:
            return DeltaCI(math.nan, math.nan, math.nan, 0, method, False, math.nan)
        mean = float(deltas.mean())
        if n == 1:
            half = math.nan
        else:
            half = float(student_t.ppf(q, n - 1) * deltas.std(ddof=1) / math.sqrt(n))
    else:
        method = "welch"
        a = np.asarray(list(base_finite.values()), dtype=np.float64)
        b = np.asarray(list(other_finite.values()), dtype=np.float64)
        n = int(min(a.size, b.size))
        if a.size == 0 or b.size == 0:
            return DeltaCI(math.nan, math.nan, math.nan, n, method, False, math.nan)
        mean = float(b.mean() - a.mean())
        if a.size < 2 or b.size < 2:
            half = math.nan
        else:
            va = float(a.var(ddof=1)) / a.size
            vb = float(b.var(ddof=1)) / b.size
            se = math.sqrt(va + vb)
            if se == 0.0:
                half = 0.0
            else:
                df = se**4 / (va**2 / (a.size - 1) + vb**2 / (b.size - 1))
                half = float(student_t.ppf(q, df) * se)

    lo, hi = mean - half, mean + half
    resolved = bool(math.isfinite(lo) and math.isfinite(hi) and (lo > 0.0 or hi < 0.0))
    pct = _PERCENT * mean / base_mean if math.isfinite(base_mean) and base_mean != 0 else math.nan
    return DeltaCI(mean, lo, hi, n, method, resolved, pct)


@dataclass
class _Group:
    """Micro runs sharing one ``config_hash`` and their aggregated metrics."""

    config_hash: str
    label: str
    runs: list[_RunInfo]
    metrics: dict[str, Metrics]  # seed label → metrics, discovery order
    agg: dict[str, CI]

    @property
    def is_baseline(self) -> bool:
        return self.label.startswith(BASELINE_LABEL)

    @property
    def seeds(self) -> list[str]:
        return [r.seed for r in self.runs]

    @property
    def seeded_any(self) -> bool:
        return any(r.seeded for r in self.runs)

    def values(self, name: str) -> dict[str, float]:
        return {seed: float(getattr(m, name)) for seed, m in self.metrics.items()}


def group_label(meta: Mapping[str, Any]) -> str:
    """Human-readable configuration label from a run's ``config.av`` block.

    ``baseline`` when no controlled vehicles act (penetration zero or no
    vehicle controller, and no VSL); otherwise
    ``"<controller> @ <penetration>% / <compliance>%"``, with the oracle
    named when it is not the perfect default and ``"VSL <name>"`` appended
    when a segment controller is configured. A run whose metadata carries no
    config block is labeled ``baseline`` (the schema defaults are an
    uncontrolled fleet).

    Args:
        meta: Parsed ``meta.json`` of one run (docs/CONTRACTS.md §3).

    Returns:
        The group label.
    """
    config = meta.get("config")
    av = config.get("av") if isinstance(config, dict) else None
    if not isinstance(av, dict):
        return BASELINE_LABEL
    penetration = float(av.get("penetration", 0.0) or 0.0)
    compliance = float(av.get("compliance", 1.0))
    controller = av.get("controller")
    vsl = av.get("vsl")
    parts: list[str] = []
    if controller is not None and penetration > 0.0:
        text = f"{controller} @ {_PERCENT * penetration:g}% / {_PERCENT * compliance:g}%"
        oracle = av.get("oracle")
        if isinstance(oracle, dict) and str(oracle.get("kind", "perfect")) != "perfect":
            text += (
                f" ({oracle.get('kind')} oracle, delay {float(oracle.get('delay_s', 0.0)):g} s,"
                f" noise {_PERCENT * float(oracle.get('amplitude_noise_frac', 0.0)):g}%)"
            )
        parts.append(text)
    if vsl is not None:
        parts.append(f"VSL {vsl}")
    closures = config.get("closures") if isinstance(config, dict) else None
    if isinstance(closures, list) and closures:
        for c in closures:
            if isinstance(c, dict):
                text = str(c.get("label") or "").strip() or (
                    f"lanes {c.get('lanes')} at {float(c.get('start_m', 0.0)):g}-"
                    f"{float(c.get('end_m', 0.0)):g} m"
                )
                parts.append(f"closure {text}")
    managed = config.get("managed_lanes") if isinstance(config, dict) else None
    if isinstance(managed, list) and managed:
        for m in managed:
            if isinstance(m, dict):
                text = str(m.get("label") or "").strip() or f"lanes {m.get('lanes')}"
                parts.append(f"managed lane {text}")
    fleet = config.get("fleet") if isinstance(config, dict) else None
    heavy = fleet.get("heavy") if isinstance(fleet, dict) else None
    if isinstance(heavy, dict) and float(heavy.get("fraction", 0.0) or 0.0) > 0.0:
        parts.append(f"heavy {_PERCENT * float(heavy['fraction']):g}%")
    network = config.get("network") if isinstance(config, dict) else None
    ramps = network.get("ramps") if isinstance(network, dict) else None
    if isinstance(ramps, list):
        for r in ramps:
            if isinstance(r, dict) and isinstance(r.get("meter"), dict):
                parts.append(
                    f"ramp meter {r['meter'].get('controller', '?')} on {r.get('name') or r.get('attach_edge')}"
                )
            if isinstance(r, dict) and str(r.get("merge", "lane_change")) != "lane_change":
                parts.append(f"merge {r.get('merge')} on {r.get('name') or r.get('attach_edge')}")
    return " + ".join(parts) if parts else BASELINE_LABEL


def _discover_runs(run_set_dir: Path) -> list[_RunInfo]:
    """Find run directories (anything holding a meta.json) under a root."""
    metas = sorted(run_set_dir.rglob("meta.json"))
    runs: list[_RunInfo] = []
    for meta_path in metas:
        raw = json.loads(meta_path.read_text())
        if not isinstance(raw, dict):
            raise ValueError(f"{meta_path}: expected a JSON object")
        runs.append(_RunInfo(path=meta_path.parent, meta=raw))
    if not runs:
        raise ValueError(f"no runs found under {run_set_dir} (no meta.json anywhere)")
    return runs


def _group_runs(micro_runs: list[_RunInfo]) -> list[_Group]:
    """Group micro runs by config hash; baseline groups first, metrics unfilled.

    Grouping is metadata-only so the run set's shared measurement span can be
    derived from the reference group before any metric is computed
    (:func:`_fill_metrics`).

    Raises:
        ValueError: If one configuration holds the same seed twice — a
            duplicated replicate would inflate ``n`` and narrow every CI.
    """
    by_hash: dict[str, list[_RunInfo]] = {}
    for r in micro_runs:
        by_hash.setdefault(r.config_hash, []).append(r)

    groups: list[_Group] = []
    for chash, runs in by_hash.items():
        seen: set[str] = set()
        for r in runs:
            if r.seed in seen:
                raise ValueError(
                    f"configuration {chash} holds seed {r.seed} more than once "
                    f"({r.path}); duplicate replicates would inflate n"
                )
            seen.add(r.seed)
        groups.append(
            _Group(
                config_hash=chash,
                label=group_label(runs[0].meta),
                runs=runs,
                metrics={},
                agg={},
            )
        )

    # Disambiguate identical labels (e.g. same controller, different params).
    counts: dict[str, int] = {}
    for g in groups:
        counts[g.label] = counts.get(g.label, 0) + 1
    for g in groups:
        if counts[g.label] > 1:
            g.label = f"{g.label} [{g.config_hash}]"

    groups.sort(key=lambda g: 0 if g.is_baseline else 1)  # stable: keeps discovery order
    return groups


def _shared_span(reference: _Group) -> tuple[float, float] | None:
    """One travel-time span [m] for the whole run set, from the reference group.

    Each replicate's own default span would be derived from its own
    trajectory file, so a congested group would be measured over a shorter
    corridor than the baseline and the controller-minus-baseline travel-time
    contrast would compare different distances. The reference group fixes one
    span for every group: the smallest observed entry position and the median
    of the replicates' own :func:`validation.metrics.default_travel_span`
    exit bounds. ``None`` when no replicate yields a usable span (each
    replicate then falls back to its own default).
    """
    import pandas as pd

    los: list[float] = []
    his: list[float] = []
    for r in reference.runs:
        path = r.path / "trajectories.parquet"
        if not path.is_file():
            continue
        traj = pd.read_parquet(path, columns=["t", "veh_id", "x"])
        if traj.empty:
            continue
        warm = r.warmup_s
        if warm > 0.0:
            windowed = traj.loc[traj["t"] >= warm]
            if not windowed.empty:
                traj = windowed
        lo, hi = default_travel_span(traj)
        if hi > lo:
            los.append(lo)
            his.append(hi)
    if not los:
        return None
    return min(los), float(np.median(np.asarray(his, dtype=np.float64)))


def _fill_metrics(
    groups: list[_Group], x_ref: float | None, span: tuple[float, float] | None
) -> None:
    """Compute and aggregate every group's replicate metrics in place."""
    for g in groups:
        g.metrics = {r.seed: compute_metrics(r.path, x_ref=x_ref, span=span) for r in g.runs}
        g.agg = aggregate(list(g.metrics.values()))


def _fmt(value: float | None, digits: int = 4) -> str:
    """Format one computed number for the template ('—' for missing)."""
    if value is None:
        return "—"
    if isinstance(value, float) and math.isnan(value):
        return "NaN"
    return f"{value:.{digits}g}"


def _load_field(run: _RunInfo, dt_bin: float = 15.0, dx_bin: float = 75.0) -> SpeedField:
    """Speed field of one run's measurement window, binned as asked.

    The run's configured warm-up is dropped (the same window
    :func:`validation.metrics.compute_metrics` measures), so the archived
    contours and the criterion reading describe the scored period. The bins
    are explicit because a :class:`validation.waves.WaveDetector` refuses a
    field binned differently from its own recipe.
    """
    import pandas as pd

    traj = pd.read_parquet(run.path / "trajectories.parquet", columns=["t", "x", "v"])
    warm = run.warmup_s
    if warm > 0.0:
        windowed = traj.loc[traj["t"] >= warm]
        if not windowed.empty:
            traj = windowed
    return speed_field(traj, dt_bin=dt_bin, dx_bin=dx_bin)


def _render_contour(
    run: _RunInfo, out_dir: Path, index: int, label: str | None = None
) -> tuple[str, str]:
    """Render one speed-contour PNG beside the report; return (path, caption)."""
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    field = _load_field(run)
    seed = run.seed
    name = f"speed_contour_{index:02d}_seed_{seed}.png"
    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    mesh = ax.pcolormesh(field.x_edges, field.t_edges, field.mean_speed, shading="flat")
    fig.colorbar(mesh, ax=ax, label="mean speed [m/s]")
    ax.set_xlabel("position x [m]")
    ax.set_ylabel("time t [s]")
    title = f"Speed field — seed {seed}"
    if label is not None:
        title = f"{label} — seed {seed}"
    if run.seeded:
        title += " (seeded=True)"
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out_dir / name, dpi=150)
    plt.close(fig)
    caption = f"Space-time mean-speed contour, seed {seed}"
    if label is not None:
        caption += f", {label}"
    if run.seeded:
        caption += ", seeded perturbation"
    return name, caption


def _render_contour_pair(
    base: _RunInfo, other: _RunInfo, other_label: str, out_dir: Path, index: int
) -> tuple[str, str]:
    """Render baseline (left) vs controller (right) contours for one seed."""
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    seed = other.seed
    fields = (_load_field(base), _load_field(other))
    stacked = np.concatenate([f.mean_speed.ravel() for f in fields])
    finite = stacked[np.isfinite(stacked)]
    vmin = float(finite.min()) if finite.size else 0.0
    vmax = float(finite.max()) if finite.size else 1.0
    name = f"speed_contour_pair_{index:02d}_seed_{seed}.png"
    fig, axes = plt.subplots(1, 2, figsize=(12.0, 4.0), sharey=True, layout="constrained")
    titles = (BASELINE_LABEL, other_label)
    meshes = []
    for ax, field, run, title in zip(axes, fields, (base, other), titles, strict=True):
        meshes.append(
            ax.pcolormesh(
                field.x_edges,
                field.t_edges,
                field.mean_speed,
                shading="flat",
                vmin=vmin,
                vmax=vmax,
            )
        )
        ax.set_xlabel("position x [m]")
        ax.set_title(f"{title} — seed {seed}" + (" (seeded=True)" if run.seeded else ""))
    axes[0].set_ylabel("time t [s]")
    fig.colorbar(meshes[-1], ax=list(axes), label="mean speed [m/s]")
    fig.savefig(out_dir / name, dpi=150)
    plt.close(fig)
    caption = (
        f"Space-time mean-speed contours, seed {seed}: {BASELINE_LABEL} (left) vs "
        f"{other_label} (right)"
    )
    if base.seeded or other.seeded:
        caption += ", seeded perturbation"
    return name, caption


def _render_figures(
    groups: list[_Group], baseline: _Group | None, out_dir: Path
) -> list[dict[str, str]]:
    """Speed-contour figures: seed-matched pairs when a baseline exists."""
    figures: list[dict[str, str]] = []
    others = [g for g in groups if g is not baseline]
    if baseline is None or not others:
        index = 0
        for g in groups:
            label = None if len(groups) == 1 else g.label
            for r in g.runs:
                name, caption = _render_contour(r, out_dir, index, label)
                figures.append({"path": name, "caption": caption})
                index += 1
        return figures

    base_by_seed = {r.seed: r for r in baseline.runs}
    matched: set[str] = set()
    single_index = 0
    for pair_index, g in enumerate(others, start=1):
        for r in g.runs:
            base_run = base_by_seed.get(r.seed)
            if base_run is None:
                name, caption = _render_contour(r, out_dir, single_index, g.label)
                single_index += 1
            else:
                matched.add(r.seed)
                name, caption = _render_contour_pair(base_run, r, g.label, out_dir, pair_index)
            figures.append({"path": name, "caption": caption})
    for r in baseline.runs:
        if r.seed not in matched:
            name, caption = _render_contour(r, out_dir, single_index, baseline.label)
            single_index += 1
            figures.append({"path": name, "caption": caption})
    return figures


def speed_aggregation_rows(
    obs: Sequence[Sequence[float]],
    sim: Sequence[Sequence[float]],
    window_s: float | None,
) -> list[dict[str, str]]:
    """The speed criterion at coarser time aggregation, with the floor.

    Rows: RMSPE of the simulated (replicate-mean) field against the observed
    field at the native window and at 3, 6 and 12 windows and the whole
    period (segments kept), plus the observed field against its own
    3-window moving average at the native window and at 3 windows — the
    resolution below which one recorded day does not repeat itself, i.e. the
    floor an ensemble mean can reach (docs/I24_VALIDATION.md §0.5).

    Args:
        obs: Observed segment speeds ``[window][segment]`` [m/s]; NaN = empty.
        sim: Simulated matrix on the same bins.
        window_s: Window length [s] for the row labels (``None`` = "window").

    Returns:
        Table rows (``aggregation``, ``rmspe``, ``floor``) as strings.
    """
    import numpy as np

    from validation.metrics import rmspe

    o = np.asarray(obs, dtype=np.float64)
    s = np.asarray(sim, dtype=np.float64)
    if o.shape != s.shape or o.ndim != 2:
        raise ValueError(
            f"segment-speed matrices must share a 2-D shape, got {o.shape} vs {s.shape}"
        )

    def agg(f: np.ndarray, k: int) -> np.ndarray:
        n = f.shape[0] // k * k
        if n == 0:
            return f
        return np.nanmean(f[:n].reshape(-1, k, f.shape[1]), axis=1)

    def err(a: np.ndarray, b: np.ndarray) -> float:
        ok = np.isfinite(a) & np.isfinite(b) & (b != 0.0)
        return float(rmspe(a[ok], b[ok])) if ok.any() else float("nan")

    def floor(k: int) -> float:
        f = agg(o, k)
        if f.shape[0] < 3:
            return float("nan")
        pad = np.pad(f, ((1, 1), (0, 0)), mode="edge")
        smooth = (pad[:-2] + pad[1:-1] + pad[2:]) / 3.0
        return err(smooth, f)

    n_win = o.shape[0]

    def label(k: int) -> str:
        return f"{k * window_s / 60.0:g} min" if window_s else f"{k} windows"

    rows: list[dict[str, str]] = []
    for k in (1, 3, 6, 12):
        if k > n_win:
            continue
        rows.append(
            {
                "aggregation": label(k) + (" (criterion)" if k == 1 else ""),
                "rmspe": _fmt(err(agg(s, k), agg(o, k))),
                "floor": _fmt(floor(k)) if k <= 3 else "",
            }
        )
    rows.append(
        {
            "aggregation": "whole period",
            "rmspe": _fmt(err(np.nanmean(s, axis=0)[None, :], np.nanmean(o, axis=0)[None, :])),
            "floor": "",
        }
    )
    return rows


def _wave_criterion_note(
    *,
    reference: _Group,
    detector: WaveDetector,
    n_readings: int,
    n_finite: int,
    n_seeded_excluded: int,
) -> str:
    """Provenance sentence for the wave-speed criterion's input value.

    States the detector, the reference group, and — because replicates in
    which no backward front is found contribute nothing — how many of the
    unseeded replicates the printed mean actually rests on. A bare
    "mean over the N unseeded replicates" would overstate the sample
    whenever any replicate yields no reading (the common case for the
    ``stack`` detector on a low-contrast field).
    """
    if n_readings == 0:
        text = f"not evaluated — group {reference.label} has no unseeded replicate"
    else:
        text = (
            f"mean over {n_finite} of {n_readings} unseeded replicate(s) of group "
            f"{reference.label} (`{reference.config_hash}`), measured with the "
            f"{detector.name} detector on its own bins"
        )
        missing = n_readings - n_finite
        if missing:
            text += (
                f"; {missing} replicate(s) detected no backward front and are "
                "excluded from the mean"
            )
        if 0 < n_finite < MIN_REPLICATES:
            text += (
                f"; fewer than {MIN_REPLICATES} contributing replicate(s) — "
                "underpowered, not a headline value"
            )
    if n_seeded_excluded:
        text += f"; {n_seeded_excluded} seeded replicate(s) excluded"
    return f"Wave-speed criterion input: {text}."


def _version_context(
    micro_runs: list[_RunInfo], run_set: Path
) -> tuple[list[dict[str, str]], str | None]:
    """Package-version rows for the whole run set, plus a mismatch warning.

    Every micro run's ``versions`` block is scanned, not just the first: a
    run set assembled from runs made weeks apart (``POST /reports`` takes an
    arbitrary run-id list) can mix engine versions, and results are not
    comparable across SUMO versions (CLAUDE.md §9). Each distinct value is
    rendered with the runs that carry it.

    Returns:
        ``(rows, warning)`` — ``rows`` are ``{"name", "detail"}`` per
        package, ``warning`` is None when every run agrees and records a
        version block.
    """
    per_key: dict[str, dict[str, list[str]]] = {}
    missing: list[str] = []
    for r in micro_runs:
        name = str(r.path.relative_to(run_set))
        raw = r.meta.get("versions")
        if not isinstance(raw, dict) or not raw:
            missing.append(name)
            continue
        for package, value in raw.items():
            per_key.setdefault(str(package), {}).setdefault(str(value), []).append(name)

    rows: list[dict[str, str]] = []
    for package, values in sorted(per_key.items()):
        if len(values) == 1:
            detail = f"`{next(iter(values))}`"
        else:
            detail = ", ".join(
                f"`{value}` ({', '.join(runs)})" for value, runs in sorted(values.items())
            )
        rows.append({"name": package, "detail": detail})

    split = [package for package, values in per_key.items() if len(values) > 1]
    if not split and not missing:
        return rows, None
    parts: list[str] = []
    if split:
        parts.append(f"runs differ in {', '.join(sorted(split))}")
    if missing:
        parts.append(f"no version metadata recorded for {', '.join(missing)}")
    warning = (
        "Version provenance is not uniform across this run set ("
        + "; ".join(parts)
        + "). Results are pinned per engine version (CLAUDE.md §9), so differences "
        "between groups are not attributable to their configurations alone."
    )
    return rows, warning


def _measurement_note(
    micro_runs: list[_RunInfo], span: tuple[float, float] | None, span_is_shared: bool
) -> str:
    """One sentence stating the window and span every metric was measured on."""
    warmups = sorted({r.warmup_s for r in micro_runs})
    warm_text = ", ".join(f"{w:g}" for w in warmups)
    if span is None:
        span_text = (
            "each replicate's own default span (smallest observed position to the "
            "median per-vehicle furthest position)"
        )
    else:
        span_text = f"[{span[0]:g}, {span[1]:g}] m"
        if span_is_shared:
            span_text += " — one span for every group, derived from the reference group"
    return (
        f"Measurement window: each run's configured warm-up is discarded from every "
        f"metric (warm-up per run, in seconds: {warm_text}). Travel times keep whole "
        f"journeys that begin inside the window and are measured over {span_text}. "
        "Fuel per vehicle-km remains a whole-run ratio unless the run records a "
        "post-warm-up fuel total."
    )


def _criteria_rows(results: list[CriteriaResult]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for c in results:
        rows.append(
            {
                "name": c.name,
                "value": _fmt(c.value),
                "threshold": c.threshold,
                "evaluated": "yes" if c.evaluated else "no",
                "result": ("PASS" if c.passed else "FAIL") + (f" — {c.detail}" if c.detail else ""),
            }
        )
    return rows


def _metric_rows(agg: Mapping[str, CI]) -> list[dict[str, str]]:
    return [
        {
            "name": name,
            "mean": _fmt(ci.mean),
            "lo": _fmt(ci.lo95),
            "hi": _fmt(ci.hi95),
            "n": str(ci.n),
            "underpowered": "yes" if ci.underpowered else "no",
        }
        for name, ci in agg.items()
    ]


def _replicate_row(profile: CriteriaProfile, n_seeds: int) -> CriteriaResult:
    """The replicate-count criterion row for one group."""
    rows = [c for c in evaluate(profile, n_seeds=n_seeds) if c.name == "n_seeds"]
    if len(rows) != 1:
        raise RuntimeError("criteria profile yielded no n_seeds row")
    return rows[0]


def _group_context(groups: list[_Group], profile: CriteriaProfile) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for g in groups:
        rep = _replicate_row(profile, len(set(g.seeds)))
        out.append(
            {
                "label": g.label,
                "config_hash": g.config_hash,
                "n_seeds": str(len(set(g.seeds))),
                "seeds_joined": ", ".join(g.seeds),
                "replicate_threshold": rep.threshold,
                "replicate_result": "PASS" if rep.passed else "FAIL",
                "seeded": g.seeded_any,
                "metric_rows": _metric_rows(g.agg),
            }
        )
    return out


def _delta_context(groups: list[_Group], baseline: _Group) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for g in groups:
        if g is baseline:
            continue
        rows: list[dict[str, str]] = []
        methods: set[str] = set()
        for name in baseline.agg:
            d = contrast(baseline.values(name), g.values(name))
            methods.add(d.method)
            rows.append(
                {
                    "name": name,
                    "mean": _fmt(d.mean),
                    "lo": _fmt(d.lo95),
                    "hi": _fmt(d.hi95),
                    "pct": _fmt(d.pct_of_baseline, 3),
                    "n": str(d.n),
                    "resolved": "yes" if d.resolved else "no",
                }
            )
        method = methods.pop() if len(methods) == 1 else "paired"
        method_text = (
            "seed-paired (common random numbers)"
            if method == "paired"
            else "Welch unequal-variance (seed sets differ)"
        )
        out.append({"label": g.label, "method_text": method_text, "rows": rows})
    return out


def _render_pdf(markdown_path: Path) -> Path:
    from validation.report_pdf import render_pdf

    return render_pdf(markdown_path, markdown_path.with_name("report.pdf"))


@overload
def generate_report(
    run_set_dir: str | Path,
    out_path: str | Path,
    *,
    profile: CriteriaProfile | None = ...,
    geh_values: list[float] | None = ...,
    rmspe_value: float | None = ...,
    ring_emergence: bool | None = ...,
    ring_dampening: bool | None = ...,
    title: str = ...,
    created_at: str | None = ...,
    x_ref: float | None = ...,
    span: tuple[float, float] | None = ...,
    pdf: Literal[False] = ...,
) -> Path: ...


@overload
def generate_report(
    run_set_dir: str | Path,
    out_path: str | Path,
    *,
    profile: CriteriaProfile | None = ...,
    geh_values: list[float] | None = ...,
    rmspe_value: float | None = ...,
    ring_emergence: bool | None = ...,
    ring_dampening: bool | None = ...,
    title: str = ...,
    created_at: str | None = ...,
    x_ref: float | None = ...,
    span: tuple[float, float] | None = ...,
    pdf: Literal[True],
) -> tuple[Path, Path]: ...


def generate_report(
    run_set_dir: str | Path,
    out_path: str | Path,
    *,
    profile: CriteriaProfile | None = None,
    geh_values: list[float] | None = None,
    rmspe_value: float | None = None,
    ring_emergence: bool | None = None,
    ring_dampening: bool | None = None,
    title: str = "FlowState calibration & validation report",
    created_at: str | None = None,
    x_ref: float | None = None,
    span: tuple[float, float] | None = None,
    pdf: bool = False,
    segment_speeds_obs: Sequence[Sequence[float]] | None = None,
    segment_speeds_sim: Sequence[Sequence[float]] | None = None,
    segment_window_s: float | None = None,
) -> Path | tuple[Path, Path]:
    """Generate a markdown (optionally PDF) validation report for a run set.

    Discovers every run directory (containing ``meta.json``) under
    ``run_set_dir``, groups the microscopic runs by ``config_hash``,
    computes and aggregates metrics per group, evaluates the acceptance
    criteria, renders speed-contour figures beside the report, and writes
    the markdown report. With a baseline group and at least one controlled
    group the report also carries a controller-minus-baseline contrast table
    (:func:`contrast`) and seed-matched contour pairs. See the module
    docstring for content guarantees.

    The wave-speed criterion is fed by the unseeded replicates of the
    reference group (the baseline when exactly one exists, else the first
    group), each measured with the profile's own
    :class:`validation.waves.WaveDetector` on a field binned at that
    detector's bins; the printed value is the mean of the replicates that
    yielded a reading, and the note under the table says how many of them
    there were. The replicate criterion is fed by the smallest group's
    distinct seed count, and additionally per group in each metrics section.

    Args:
        run_set_dir: Root directory holding run directories (contract §3
            layout ``runs/<config_hash>/<seed>/``).
        out_path: Destination of the markdown report; figures (and the PDF)
            are written into its parent directory.
        profile: Acceptance-criteria profile; ``None`` uses the FHWA-style
            default.
        geh_values: Optional per-link-hour GEH statistics vs observed counts.
        rmspe_value: Optional segment-speed RMSPE (fraction) vs observations.
        ring_emergence: Optional ring emergence benchmark outcome.
        ring_dampening: Optional single-AV dampening benchmark outcome.
        title: Report title.
        created_at: Optional ISO timestamp, caller-supplied (never
            auto-generated, for reproducibility); omitted when ``None``.
        x_ref: Optional throughput cross-section [m] forwarded to
            :func:`validation.metrics.compute_metrics`.
        span: Optional travel-time measurement span [m], forwarded likewise.
            ``None`` derives one span for the whole run set from the
            reference group (:func:`_shared_span`) so that every group's
            travel time — and the controller-minus-baseline contrast — is
            measured over the same distance.
        pdf: Also render the markdown to ``report.pdf`` beside it via
            :mod:`validation.report_pdf` (needs the ``validation[pdf]``
            extra, fpdf2).
        segment_speeds_obs: Optional observed segment-speed matrix
            ``[window][segment]`` [m/s] behind ``rmspe_value``; with
            ``segment_speeds_sim`` it adds the speed criterion by time
            aggregation and the observed field's own repeatability floor
            (:func:`speed_aggregation_rows`).
        segment_speeds_sim: The simulated (replicate-mean) matrix on the same
            bins.
        segment_window_s: The matrices' window length [s] (labels the rows).

    Returns:
        Path to the written markdown report; with ``pdf=True`` the tuple
        ``(markdown_path, pdf_path)``.

    Raises:
        ReportRefusedError: If every discovered run is macroscopic
            (``tier`` in ``{"macro", "screening"}``) — CLAUDE.md §5.6.
        ValueError: If no runs are found under ``run_set_dir``, or one
            configuration holds the same seed twice.
        RuntimeError: If ``pdf=True`` and fpdf2 is not installed.
    """
    run_set = Path(run_set_dir)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    runs = _discover_runs(run_set)
    micro_runs = [r for r in runs if not r.is_macro]
    if not micro_runs:
        raise ReportRefusedError(
            "all runs are macroscopic screening-tier results; the screening tier "
            "cannot support validation claims, so no report is generated "
            "(CLAUDE.md §5.6)"
        )

    p = profile if profile is not None else CriteriaProfile()
    groups = _group_runs(micro_runs)
    baselines = [g for g in groups if g.is_baseline]
    baseline = baselines[0] if len(baselines) == 1 else None
    reference = baseline if baseline is not None else groups[0]
    # One travel-time span for every group (see _shared_span) unless the
    # caller fixed one; each replicate's own default would measure the
    # groups over different distances.
    measure_span = span if span is not None else _shared_span(reference)
    _fill_metrics(groups, x_ref, measure_span)

    # Wave-speed criterion: emergent means unseeded (CLAUDE.md §0.2, §7.1),
    # measured with the profile's own detector on that detector's own bins.
    # `evaluate` refuses (does not score) a value from another recipe, so
    # reusing the metrics reading would leave the row unevaluatable.
    det = p.wave_detector
    unseeded_runs = [r for r in reference.runs if not r.seeded]
    n_seeded_excluded = len(reference.runs) - len(unseeded_runs)
    readings = [
        det.measure(_load_field(r, dt_bin=det.dt_bin_s, dx_bin=det.dx_bin_m)).speed_kmh
        for r in unseeded_runs
    ]
    finite = [v for v in readings if math.isfinite(v)]
    wave_speed: float | None
    if not readings:
        wave_speed = None
    elif finite:
        # Replicates with no detected front contribute nothing to the mean
        # (as validation.metrics.aggregate drops them); the note says how many.
        wave_speed = float(np.mean(finite))
    else:
        wave_speed = math.nan
    smallest = min(groups, key=lambda g: len(set(g.seeds)))
    criteria_results = evaluate(
        p,
        geh_values=geh_values,
        rmspe_value=rmspe_value,
        wave_speed_kmh=wave_speed,
        ring_emergence=ring_emergence,
        ring_dampening=ring_dampening,
        n_seeds=len(set(smallest.seeds)),
        wave_detector=det,
    )
    criteria_note = _wave_criterion_note(
        reference=reference,
        detector=det,
        n_readings=len(readings),
        n_finite=len(finite),
        n_seeded_excluded=n_seeded_excluded,
    )
    if len(groups) > 1 or n_seeded_excluded:
        criteria_note += (
            f" Replicate criterion input: the smallest group ({smallest.label}, n = "
            f"{len(set(smallest.seeds))} distinct seeds); each group's own replicate "
            "check is under Metrics."
        )

    aggregation_rows = (
        speed_aggregation_rows(segment_speeds_obs, segment_speeds_sim, segment_window_s)
        if segment_speeds_obs is not None and segment_speeds_sim is not None
        else None
    )
    figures = _render_figures(groups, baseline, out.parent)

    seeded_any = any(r.seeded for r in runs)
    run_rows = [
        {
            "name": str(r.path.relative_to(run_set)),
            "config_hash": r.config_hash,
            "seed": r.seed,
            "tier": str(r.meta.get("tier", "unknown")),
            "seeded": "seeded=True" if r.seeded else "seeded=False",
            "wall_time_s": _fmt(r.meta.get("wall_time_s")),
        }
        for r in runs
    ]
    versions, versions_warning = _version_context(micro_runs, run_set)

    calibrations: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for r in runs:
        entries = list(r.meta.get("calibration_artifacts", []) or [])
        # Micro runs record a single fleet IDMCalibration under
        # `fleet_calibration` (docs/CONTRACTS.md §2) — same provenance shape.
        fleet_cal = r.meta.get("fleet_calibration")
        if isinstance(fleet_cal, dict):
            entries.append(fleet_cal)
        for entry in entries:
            if isinstance(entry, dict):
                key = (str(entry.get("path", "unknown")), str(entry.get("data_hash", "unknown")))
                if key not in seen:
                    seen.add(key)
                    calibrations.append({"path": key[0], "data_hash": key[1]})

    deltas = _delta_context(groups, baseline) if baseline is not None else []
    delta_note: str | None = None
    if not deltas and len(groups) > 1:
        delta_note = (
            f"No controller-minus-baseline contrast: {len(baselines)} baseline "
            "configuration(s) found; a contrast needs exactly one baseline group."
        )

    seeds_seen: list[str] = []
    for r in micro_runs:
        if r.seed not in seeds_seen:
            seeds_seen.append(r.seed)

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
    )
    rendered = env.get_template(_TEMPLATE_NAME).render(
        title=title,
        created_at=created_at,
        seeded_any=seeded_any,
        seeded_banner=(
            "SEEDED RUNS INCLUDED — one or more runs carry seeded=True: their waves "
            "were injected, not emergent (CLAUDE.md §0.2)."
        ),
        seeded_flag_text="seeded=True",
        profile_name=p.name,
        seeds_joined=", ".join(seeds_seen),
        runs=run_rows,
        versions=versions,
        versions_warning=versions_warning,
        measurement_note=_measurement_note(micro_runs, measure_span, span is None),
        calibrations=calibrations,
        criteria=_criteria_rows(criteria_results),
        criteria_note=criteria_note,
        wave_row_note=(
            f"The wave_speed_kmh row is the metrics detector's diagnostic reading "
            f"({get_detector('standard').name}); the acceptance criterion above is "
            f"measured separately with the profile's {det.name} detector on its own "
            "bins, so the two values can differ."
        ),
        aggregation=aggregation_rows,
        ci_level_pct=_fmt(CI_LEVEL * _PERCENT, 3),
        min_replicates=str(MIN_REPLICATES),
        groups=_group_context(groups, p),
        deltas=deltas,
        delta_note=delta_note,
        figures=figures,
    )
    out.write_text(rendered)
    if pdf:
        return out, _render_pdf(out)
    return out
