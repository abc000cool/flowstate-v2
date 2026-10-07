"""Auto-generated validation report (CLAUDE.md §7.4) — a product feature.

``generate_report`` renders a markdown calibration/validation report from a
directory of run results: provenance (config hash, seeds, and the package
and SUMO versions of *every* run — a run set mixing engine versions is
listed value by value and carries a comparability warning, CLAUDE.md §9),
how much of the configured demand actually entered the network
(:func:`validation.battery.insertion_stats`, printed when the runs record
the counters — a run set that inserted a fraction of its plan simulated a
different scenario from the one it was asked for),
calibration artifacts used, the acceptance criteria table
(:mod:`validation.criteria`), metric tables with replicate confidence
intervals (:mod:`validation.metrics`), and speed-contour figures rendered
beside the report. Seeded-perturbation runs are labeled prominently
(CLAUDE.md §0.2). Macro-only run sets are refused (CLAUDE.md §5.6): the
screening tier cannot support validation claims.

A **Model integrity** section states what the runs' metadata records about
the simulation misbehaving (:func:`_integrity_context`): SUMO collisions —
count, per-run interval, rate per departed vehicles, the runs they occurred
in and a location table by lane (:func:`validation.battery.collision_summary`,
the reading the corridor battery artifact carries) — and the lane changes
each scripted merge or weaving section forced past SUMO's lane-change safety
checks (:func:`validation.battery.forced_change_summary`). A run set with
collisions also gets a first limitations bullet naming them and where they
happened; a run without the counter is "not recorded", never zero.

Zero collisions is also an acceptance criterion (2026-10-04, owner decision,
WP-98): the criteria table carries the ``no_collisions`` row of
:func:`validation.criteria.evaluate`, scored from every micro run's
``meta.json`` (PASS / FAIL / NOT RECORDED), and a run set that fails it opens
with a model-integrity banner under the title.

**Locks** (2026-10-07, docs/I94_COLLAPSE_DIAGNOSIS.md): the Model integrity
section also states which runs locked — vehicles standing with no discharge
past a point for at least :data:`validation.locks.LOCK_MIN_DURATION_S` while a
queue builds behind it (:func:`validation.locks.detect_run_locks` on each micro
run's ``edges.parquet`` and ``vehicles.parquet``, or the caller's
``locks_by_run``) — with a table of every lock (run, head, section, onset,
duration, trapped vehicles); the criteria table carries the ``no_locks`` row,
a run set with a lock opens with its own model-integrity banner and gets a
limitations bullet, and the client summary's confidence table says whether
the runs are free of locks. A run without either file is "not recorded",
never lock-free.

Every metric, figure and criterion describes the same measurement window:
each run's recorded period minus its configured warm-up
(:func:`validation.metrics.warmup_from_meta`) and, when the caller passes
``scored_end_s``, minus the cool-down after the study period's end
(docs/FRISCO_PROTOCOL.md §8.2). The wave-speed criterion is
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

A run set with more than one configuration also carries a **strategy
comparison**: one table, one row per group (baseline first), one column per
headline metric (:data:`COMPARISON_METRICS`), each cell the group's mean with
its replicate interval and — off the baseline row — the same contrast
(:func:`contrast`) with its interval and whether it is resolved. It answers
"which deployment, on the numbers that get quoted" on one page, where the
per-group sections answer it one configuration at a time; it introduces no
new statistic (:func:`_comparison_rows` re-reads the group aggregates and the
same contrasts) and is omitted for a single-configuration run set.

A **client summary** opens the report (docs/FRISCO_PROTOCOL.md §10): the
baseline gate's result first (:mod:`validation.baseline_gate`, an optional
``gate`` input — without one the summary says the gate was not evaluated and
that no strategy recommendation is made), each check in words with its
computed numbers, a "what we are confident about and what we are not" table
generated from the checks' statuses, the strategy results — only when the
gate passed, only as intervals, and a recommendation line only of the form
"on this model, strategy X reduced total delay including waiting time by A–B
% (95 % interval) relative to doing nothing; this is a model prediction",
made only when every run of the baseline and of the strategy carries that
measure (protocol §8.2, §8.4; otherwise the line says, in plain words, why no
recommendation is made) — the excluded detectors, the calibration/validation
day split, and the study's limits. When the gate failed the report says it
contains no strategy recommendations, and the strategy tables
(per-configuration metrics of the strategy arms, the contrasts and the
strategy comparison) are replaced by a "not delivered" statement. A gate
result that names no configuration (an empty ``config_hash``) cannot be tied
to the report's baseline and is treated as not evaluated.

**Fuel is a model estimate everywhere it appears** (Frisco plan Stage 1
item 13; protocol §8.6): SUMO computes it from its HBEFA emission classes and
nothing in this repository has validated it against measured fuel, so every
metric row, column header, note and summary line that carries a fuel figure
says so (:data:`FUEL_ESTIMATE_TEXT`, :func:`metric_label`).

Every number in the rendered report is a computed value passed into the
Jinja2 template (packaged at ``validation/templates/report.md.j2``); the
template body contains no free-text numerals (CLAUDE.md §7.4). The optional
PDF (``pdf=True``, :mod:`validation.report_pdf`) is a rendering of that same
markdown text, never a second source of numbers.
"""

from __future__ import annotations

import dataclasses
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

from validation.baseline_gate import (
    PROTOCOL_DOC,
    SPEED_AGGREGATION_S,
    CheckResult,
    GateResult,
    status_text,
)
from validation.battery import (
    STARVED_RAMP_FRACTION,
    STARVED_RAMP_MIN_PLANNED,
    aggregate_insertion,
    collision_counts,
    collision_summary,
    forced_change_summary,
    insertion_stats,
    records_insertion,
    weave_exit_summary,
)
from validation.criteria import CriteriaProfile, CriteriaResult, evaluate, zero_collisions
from validation.fields import SpeedField, speed_field
from validation.locks import (
    LOCK_MIN_DURATION_S,
    SHARE_CI_LEVEL,
    RunLocks,
    detect_run_locks,
    lock_summary,
)
from validation.metrics import (
    CI,
    CI_LEVEL,
    JOURNEYS_FILE,
    MIN_REPLICATES,
    WAITING_FIELDS,
    Metrics,
    WaitingMetrics,
    aggregate,
    compute_metrics,
    compute_waiting_metrics,
    default_travel_span,
    warmup_from_meta,
)
from validation.observed import DetectorWaveSpeed, ObservedProvenance
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

#: Seconds per minute (labels only).
_SECONDS_PER_MINUTE = 60.0

#: Metric fields that carry a fuel figure (labelled a model estimate wherever
#: they appear; Frisco plan Stage 1 item 13, docs/FRISCO_PROTOCOL.md §8.6).
FUEL_METRICS: frozenset[str] = frozenset({"fuel_ml_per_veh_km"})

#: The label every fuel figure carries.
FUEL_LABEL = "model estimate"

#: How fuel is described wherever the report names it in prose.
FUEL_ESTIMATE_TEXT = (
    "fuel (model estimate, SUMO HBEFA emission class; not validated against measured fuel)"
)

#: The note under the Metrics heading.
FUEL_NOTE = (
    f"Rows marked ({FUEL_LABEL}) carry {FUEL_ESTIMATE_TEXT}. SUMO computes fuel from its "
    "HBEFA emission classes; this study has measured no fuel to check it against "
    f"({PROTOCOL_DOC} section 8.6)."
)

#: The limitations bullet on fuel.
FUEL_LIMITATION = (
    f"Every fuel figure is a {FUEL_LABEL} (SUMO HBEFA emission class), not validated against "
    "measured fuel; differences in fuel between configurations are model predictions."
)

#: The only measure a client-summary recommendation line is stated on
#: (``validation.metrics.WaitingMetrics``, computed from the run's
#: ``journeys.parquet``): the protocol's tuning objective, total delay
#: including waiting time (docs/FRISCO_PROTOCOL.md §8.2, §8.4), so a strategy
#: that holds cars on ramps or off the road cannot be recommended for a
#: shorter trip of the cars it let through. A run set in which any run of the
#: baseline or of the strategy lacks it (runs written before the demand
#: ledger, WP-105) gets no recommendation line for that strategy — never one
#: on the travel time of the vehicles that happened to finish.
DELAY_RECOMMENDATION_METRIC = (
    "total_delay_incl_waiting_veh_h",
    "total delay including waiting time",
)

#: Throughput field a strategy may not lose (§8.4: a setting whose throughput
#: interval lies entirely below the baseline's cannot be selected).
THROUGHPUT_METRIC = "throughput_veh_h"

#: Columns of the client summary's strategy table: metric field → plain name.
CLIENT_STRATEGY_METRICS: tuple[tuple[str, str], ...] = (
    ("throughput_veh_h", "Throughput"),
    ("mean_tt_s", "Mean travel time"),
    ("sigma_v_temporal_ms", "Speed variation σ_v (temporal)"),
    ("fuel_ml_per_veh_km", f"Fuel ({FUEL_LABEL})"),
    ("wave_count", "Wave count"),
)

#: Columns placed first in the client strategy table when the baseline carries
#: the waiting metrics (protocol §8.2, §8.3): the recommendation measure and
#: the travel time that counts ramp and entry waiting. A strategy without them
#: shows "—" there.
CLIENT_WAITING_METRICS: tuple[tuple[str, str], ...] = (
    ("total_delay_incl_waiting_veh_h", "Total delay including waiting"),
    ("mean_tt_incl_waiting_s", "Mean travel time including waiting"),
)


def metric_label(name: str) -> str:
    """A metric's row label: fuel fields carry ``(model estimate)``."""
    return f"{name} ({FUEL_LABEL})" if name in FUEL_METRICS else name


class ReportRefusedError(RuntimeError):
    """Raised when a report is requested from macro-only (screening) runs.

    The macroscopic LWR/CTM tier is string-stable by construction and cannot
    support phantom-wave validation claims; the report generator therefore
    refuses run sets containing no microscopic runs (CLAUDE.md §5.6).
    """


@dataclass(frozen=True)
class _RunInfo:
    """One discovered run directory plus its parsed metadata.

    ``scored_end_s`` is the end of the scored period [s] the caller of
    :func:`generate_report` set (the study period's end before a cool-down,
    docs/FRISCO_PROTOCOL.md §8.2); ``None`` scores to the run's end.
    """

    path: Path
    meta: dict[str, Any]
    scored_end_s: float | None = None

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
    #: seed label → waiting metrics, for the runs that carry a demand ledger
    #: (``journeys.parquet``, WP-105); empty for runs written before it
    waiting: dict[str, WaitingMetrics] = dataclasses.field(default_factory=dict)

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
        if name in WAITING_FIELDS:
            return {seed: float(getattr(m, name)) for seed, m in self.waiting.items()}
        return {seed: float(getattr(m, name)) for seed, m in self.metrics.items()}

    @property
    def carries_waiting(self) -> bool:
        """Every run of the group carries the waiting metrics (WP-105)."""
        return bool(self.runs) and all(r.seed in self.waiting for r in self.runs)


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
        traj = _scored_rows(traj, r)
        lo, hi = default_travel_span(traj)
        if hi > lo:
            los.append(lo)
            his.append(hi)
    if not los:
        return None
    return min(los), float(np.median(np.asarray(his, dtype=np.float64)))


def _scored_rows(traj: Any, run: _RunInfo) -> Any:
    """The rows of ``traj`` in the run's scored window (warm-up and cool-down dropped).

    The window :func:`validation.metrics.compute_metrics` measures: ``t``
    from the run's warm-up to its scored end (``run.scored_end_s``, else the
    run's end). An empty selection keeps the whole frame, as the warm-up-only
    rule always did.
    """
    warm = run.warmup_s
    end = run.scored_end_s
    if warm <= 0.0 and end is None:
        return traj
    keep = traj["t"] >= warm
    if end is not None:
        keep &= traj["t"] < end
    windowed = traj.loc[keep]
    return traj if windowed.empty else windowed


def _run_key(path: str | Path) -> Path:
    """The key a caller-supplied per-run mapping is looked up by."""
    return Path(path).resolve()


def _fill_metrics(
    groups: list[_Group],
    x_ref: float | None,
    span: tuple[float, float] | None,
    precomputed: Mapping[Path, Metrics] | None = None,
) -> None:
    """Compute and aggregate every group's replicate metrics in place.

    A run whose directory is in ``precomputed`` (keyed by :func:`_run_key`)
    takes its metrics from there and its trajectory is not read; every other
    run is measured with :func:`validation.metrics.compute_metrics`.
    """
    stored = precomputed or {}
    for g in groups:
        g.metrics = {}
        for r in g.runs:
            known = stored.get(_run_key(r.path))
            g.metrics[r.seed] = (
                known
                if known is not None
                else compute_metrics(r.path, x_ref=x_ref, span=span, scored_end_s=r.scored_end_s)
            )
        g.agg = aggregate(list(g.metrics.values()))
        g.waiting = {
            r.seed: compute_waiting_metrics(r.path, scored_end_s=r.scored_end_s)
            for r in g.runs
            if (r.path / JOURNEYS_FILE).is_file() and isinstance(r.meta.get("journeys"), dict)
        }


def _fmt(value: float | None, digits: int = 4) -> str:
    """Format one computed number for the template ('—' for missing)."""
    if value is None:
        return "—"
    if isinstance(value, float) and math.isnan(value):
        return "NaN"
    return f"{value:.{digits}g}"


def _load_field(run: _RunInfo, dt_bin: float = 15.0, dx_bin: float = 75.0) -> SpeedField:
    """Speed field of one run's measurement window, binned as asked.

    The run's configured warm-up — and a cool-down after its scored end — is
    dropped (the same window :func:`validation.metrics.compute_metrics`
    measures, :func:`_scored_rows`), so the archived contours and the
    criterion reading describe the scored period. The bins are explicit
    because a :class:`validation.waves.WaveDetector` refuses a field binned
    differently from its own recipe.
    """
    import pandas as pd

    traj = pd.read_parquet(run.path / "trajectories.parquet", columns=["t", "x", "v"])
    return speed_field(_scored_rows(traj, run), dt_bin=dt_bin, dx_bin=dx_bin)


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
    groups: list[_Group],
    baseline: _Group | None,
    out_dir: Path,
    figure_runs: frozenset[Path] | None = None,
) -> list[dict[str, str]]:
    """Speed-contour figures: seed-matched pairs when a baseline exists.

    ``figure_runs`` (directories keyed by :func:`_run_key`) restricts the
    runs that get a contour; ``None`` renders every run. A seed-matched pair
    is rendered when the controlled run is selected (its baseline partner is
    read for the left panel whether or not it is selected itself).
    """

    def selected(runs: list[_RunInfo]) -> list[_RunInfo]:
        if figure_runs is None:
            return runs
        return [r for r in runs if _run_key(r.path) in figure_runs]

    figures: list[dict[str, str]] = []
    others = [g for g in groups if g is not baseline]
    if baseline is None or not others:
        index = 0
        for g in groups:
            label = None if len(groups) == 1 else g.label
            for r in selected(g.runs):
                name, caption = _render_contour(r, out_dir, index, label)
                figures.append({"path": name, "caption": caption})
                index += 1
        return figures

    base_by_seed = {r.seed: r for r in baseline.runs}
    matched: set[str] = set()
    single_index = 0
    for pair_index, g in enumerate(others, start=1):
        for r in selected(g.runs):
            base_run = base_by_seed.get(r.seed)
            if base_run is None:
                name, caption = _render_contour(r, out_dir, single_index, g.label)
                single_index += 1
            else:
                matched.add(r.seed)
                name, caption = _render_contour_pair(base_run, r, g.label, out_dir, pair_index)
            figures.append({"path": name, "caption": caption})
    for r in selected(baseline.runs):
        if r.seed not in matched:
            name, caption = _render_contour(r, out_dir, single_index, baseline.label)
            single_index += 1
            figures.append({"path": name, "caption": caption})
    return figures


def speed_aggregation_rows(
    obs: Sequence[Sequence[float]],
    sim: Sequence[Sequence[float]],
    window_s: float | None,
    *,
    criterion_aggregation_s: float | None = None,
) -> list[dict[str, str]]:
    """The speed criterion at coarser time aggregation, with the floor.

    Rows: RMSPE of the simulated (replicate-mean) field against the observed
    field at the native window and at 3, 6 and 12 windows and the whole
    period (segments kept), plus the observed field against its own
    3-window moving average at the native window and at 3 windows — the
    resolution below which one recorded day does not repeat itself, i.e. the
    floor an ensemble mean can reach (docs/I24_VALIDATION.md §0.5).

    Blocks start at the matrices' first row — the first analysed window, the
    study period's start — and a trailing partial block is left out. Within
    a block both sides are averaged over the same windows: those where both
    are measured and the observation is not zero (the cells the RMSPE can
    compare), so a window missing on one side never weights the other side's
    mean; the whole-period row likewise. The observed floor is a property of
    the observed field alone and averages every observed window.

    Args:
        obs: Observed segment speeds ``[window][segment]`` [m/s]; NaN = empty.
        sim: Simulated matrix on the same bins.
        window_s: Window length [s] for the row labels (``None`` = "window").
        criterion_aggregation_s: The aggregation [s] the criterion is scored
            at, whose row is labelled ``(criterion)`` (added when it is not
            among the standard rows); ``None`` (the default) labels the native
            window, the criteria profile's RMSPE row. The baseline gate passes
            C3's 15 minutes (``validation.baseline_gate.SPEED_AGGREGATION_S``).
            When it is not a whole number of windows (observations on 10-,
            20-, 30- or 60-minute windows against C3's 15 minutes) the
            criterion cannot be formed on these windows: no row is labelled
            ``(criterion)``, and a last row says why (``rmspe`` "not formed",
            the reason under ``note``); the other rows are unchanged.

    Returns:
        Table rows (``aggregation``, ``rmspe``, ``floor``; ``note`` on the
        not-formed criterion row only) as strings.

    Raises:
        ValueError: The matrices differ in shape or are not 2-D, or a
            criterion aggregation is given without ``window_s``.
    """
    import warnings

    import numpy as np

    from validation.metrics import rmspe

    o = np.asarray(obs, dtype=np.float64)
    s = np.asarray(sim, dtype=np.float64)
    if o.shape != s.shape or o.ndim != 2:
        raise ValueError(
            f"segment-speed matrices must share a 2-D shape, got {o.shape} vs {s.shape}"
        )
    criterion_k: int | None = 1
    not_formed: dict[str, str] | None = None
    if criterion_aggregation_s is not None:
        if not window_s:
            raise ValueError("criterion_aggregation_s needs the window length window_s")
        ratio = criterion_aggregation_s / window_s
        criterion_k = round(ratio)
        if criterion_k < 1 or abs(ratio - criterion_k) > 1e-9:
            # the criterion cannot be formed on these windows: the row is
            # skipped with a note, the diagnostic rows stand (a caller scoring
            # the criterion itself reports it as not evaluated)
            criterion_k = None
            not_formed = {
                "aggregation": f"{criterion_aggregation_s / 60.0:g} min (criterion)",
                "rmspe": "not formed",
                "floor": "",
                "note": (
                    f"criterion aggregation {criterion_aggregation_s:g} s is not a whole "
                    f"number of {window_s:g} s windows"
                ),
            }
    # Both sides on the cells the RMSPE compares (joint mask), as C3 does.
    joint = np.isfinite(s) & np.isfinite(o) & (o != 0.0)
    s_j = np.where(joint, s, np.nan)
    o_j = np.where(joint, o, np.nan)

    def agg(f: np.ndarray, k: int) -> np.ndarray:
        n = f.shape[0] // k * k
        if n == 0:
            return f
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # an all-NaN block is NaN
            return np.asarray(np.nanmean(f[:n].reshape(-1, k, f.shape[1]), axis=1))

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
    standard = {1, 3, 6, 12} if criterion_k is None else {1, 3, 6, 12, criterion_k}
    for k in sorted(standard):
        if k > n_win:
            continue
        rows.append(
            {
                "aggregation": label(k) + (" (criterion)" if k == criterion_k else ""),
                "rmspe": _fmt(err(agg(s_j, k), agg(o_j, k))),
                "floor": _fmt(floor(k)) if k <= 3 else "",
            }
        )
    whole = max(n_win, 1)
    rows.append(
        {
            "aggregation": "whole period",
            "rmspe": _fmt(err(agg(s_j, whole), agg(o_j, whole))),
            "floor": "",
        }
    )
    if not_formed is not None:
        rows.append(not_formed)
    return rows


def _wave_speed_context_line(wave: DetectorWaveSpeed, band_kmh: tuple[float, float]) -> str:
    """The observed block's detector wave-speed line — context, not a criterion.

    A corridor's own recurrent wave speed is what the profile's band
    (:data:`flowstate_core.constants.WAVE_SPEED_BAND_KMH`) is a generic stand-in
    for, so printing the two side by side tells a reviewer whether the band is
    the right target for *this* corridor. It scores nothing: the wave-speed
    criterion stays a statement about the simulated field (CLAUDE.md §7.1).

    The median is a median over a handful of station pairs, so the artifact's
    leave-one-date-out range — the same estimate re-run without each date in
    turn — is printed beside it whenever the artifact carries one, together
    with the fewest pairs any of those subsets kept. A headline that moves by
    3 km/h when one morning is dropped is not a 0.1 km/h number, and the line
    has to say so where the number is read.

    Args:
        wave: The estimate read from the observations artifact.
        band_kmh: The active profile's acceptance band [km/h].

    Returns:
        The line, with every number formatted from the arguments.
    """
    lo, hi = band_kmh
    band = f"the model's band is {_fmt(lo, 3)}–{_fmt(hi, 3)} km/h"
    if wave.n_used <= 0:
        detail = f" ({wave.rejections})" if wave.rejections else ""
        return f"not estimated from {wave.n_pairs} station pairs{detail}; {band}"
    q25, q75 = wave.iqr_kmh
    loo = ""
    if wave.has_loo:
        loo = (
            f"leave-one-date-out {_fmt(wave.loo_median_min_kmh, 3)}–"
            f"{_fmt(wave.loo_median_max_kmh, 3)} km/h over {wave.loo_n_dates} dates "
            f"(fewest {wave.loo_pairs_min} pairs); "
        )
    return (
        f"median {_fmt(wave.median_kmh, 3)} km/h (IQR {_fmt(q25, 3)}–{_fmt(q75, 3)}) from "
        f"{wave.n_used} of {wave.n_pairs} station pairs; {loo}{band}"
    )


def _observed_rows(
    observed: ObservedProvenance, band_kmh: tuple[float, float]
) -> list[dict[str, str]]:
    """Provenance rows for the observed side of a comparison.

    Every value is taken from the artifact or from the scoring; rows the
    artifact left empty (a source that states no provider, dates or URL) are
    omitted rather than printed as blanks. Coverage is reported as the share
    of the mainline station-window grid that actually carries a measurement,
    on both series separately — a corridor whose speeds are dense and whose
    flows are sparse is a different comparison from one that is dense in
    both, and one pooled number would hide that (CLAUDE.md §7.4).

    A station the run does not reach is excluded from both comparisons rather
    than scored against a simulated flow of zero, so the count of excluded
    stations is printed too; the ``comparison not formed`` row appears only
    when the artifact supported no comparison at all.
    """
    rows: list[tuple[str, str]] = [
        ("artifact", observed.path),
        ("corridor", observed.corridor),
        ("source provider", observed.provider),
        ("source dates", observed.dates),
        ("source url", observed.url),
        ("aggregation", observed.aggregation),
        ("local clock time of simulation t = 0", observed.t0_local),
        ("observation window [s]", _fmt(observed.window_s)),
        ("mainline stations compared", str(observed.n_stations)),
        ("observation windows", str(observed.n_windows)),
        ("windows inside the measurement window", str(observed.n_windows_compared)),
        ("station-windows with an observed flow [fraction]", _fmt(observed.flow_fraction)),
        ("station-windows with an observed speed [fraction]", _fmt(observed.speed_fraction)),
        ("link-hour comparisons (pooled over replicates)", str(observed.n_link_hours)),
        ("speed cells compared (pooled over replicates)", str(observed.n_speed_cells)),
        ("replicates scored against the observations", str(observed.n_replicates)),
        (
            "stations excluded (outside the simulated span)",
            f"{observed.n_stations_outside_span}"
            + (f" — {observed.stations_outside_span}" if observed.stations_outside_span else ""),
        ),
        ("comparison not formed", observed.note),
    ]
    if observed.wave_speed is not None:
        rows.append(
            (
                "detector-estimated backward wave speed (context, not a criterion)",
                _wave_speed_context_line(observed.wave_speed, band_kmh),
            )
        )
    return [{"name": name, "detail": detail} for name, detail in rows if detail]


#: What the observed-data block means; prose only, no numbers (those are rows).
OBSERVED_NOTE = (
    "The link-flow and segment-speed criteria are scored against this observed "
    "artifact: hourly volumes formed from the fully observed windows of each hour at "
    "every mainline station, and mean speeds per station segment (each station owns "
    "the span to the midpoints with its neighbours). Simulation time zero is the "
    "artifact's local start time; the run's warm-up, and any window the run does not "
    "cover to its end, are excluded, and a station-window the detector did not measure "
    "is skipped, never imputed — the coverage rows say how much of the grid was "
    "compared. A station whose cross-section lies outside the simulated position span "
    "is excluded from both comparisons rather than scored against a simulated flow of "
    "zero; the rows say how many were. When the artifact carries a "
    "detector-estimated backward wave speed, it is printed here as context for the "
    "band the simulated wave speed is scored against — it is a property of the "
    "corridor, not a target, and no criterion is evaluated from it."
)


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
    ends = sorted({r.scored_end_s for r in micro_runs if r.scored_end_s is not None})
    if span is None:
        span_text = (
            "each replicate's own default span (smallest observed position to the "
            "median per-vehicle furthest position)"
        )
    else:
        span_text = f"[{span[0]:g}, {span[1]:g}] m"
        if span_is_shared:
            span_text += " — one span for every group, derived from the reference group"
    note = (
        f"Measurement window: each run's configured warm-up is discarded from every "
        f"metric (warm-up per run, in seconds: {warm_text}). Travel times keep whole "
        f"journeys that begin inside the window and are measured over {span_text}. "
        f"Fuel per vehicle-km, a {FUEL_LABEL} (SUMO HBEFA emission class; not validated "
        "against measured fuel), remains a whole-run ratio unless the run records a "
        "post-warm-up fuel total."
    )
    if ends:
        end_text = ", ".join(f"{e:g}" for e in ends)
        note += (
            f" Scored period: the window ends at the study period's end ({end_text} s); the "
            "cool-down simulated after it lets the study period's vehicles finish and is not "
            "scored — throughput, speed variation, VMT/VHT, the wave field and the contours "
            "stop at that end, only departures planned before it enter the measures including "
            "waiting, and the travel times of journeys begun before it are followed into the "
            "cool-down; a post-warm-up fuel total cannot be cut there and stays a ratio over "
            f"the post-warm-up run ({PROTOCOL_DOC} section 8.2)."
        )
    return note


def _insertion_note(micro_runs: list[_RunInfo]) -> str | None:
    """One sentence on how much of the demand the run set actually inserted.

    A run whose vehicles never departed is not a congested corridor but a
    different scenario: the queue waits outside the network, so throughput
    is the insertion rate and every flow metric below describes demand that
    was never applied. The counters come from each run's ``meta.json``
    (:func:`validation.battery.insertion_stats`) — the same reading the
    corridor battery prints per replicate, so the report and the battery
    cannot disagree.

    Args:
        micro_runs: The run set's microscopic runs.

    Returns:
        The sentence, or None when no run recorded insertion counters (older
        runs and hand-written fixtures): silence, not a zero.
    """
    summary = aggregate_insertion(
        [insertion_stats(r.meta) for r in micro_runs if records_insertion(r.meta)]
    )
    if summary is None:
        return None
    arrived = (
        "arrival not recorded"
        if summary.mean_arrived is None
        else (
            f"{summary.mean_arrived:.1f} arrived per run "
            f"(over {summary.n_with_arrived} of {summary.n_runs})"
        )
    )
    text = (
        f"Insertion: {summary.planned} vehicles planned over {summary.n_runs} run(s), "
        f"{summary.departed} departed ({_fmt(summary.mean_departed_fraction, 3)} of plan on "
        f"average, lowest {_fmt(summary.min_departed_fraction, 3)}), {arrived}; "
        f"verdict: {summary.verdict}."
    )
    if summary.starved_ramps:
        text += (
            " Starved on-ramps (a ramp of at least "
            f"{STARVED_RAMP_MIN_PLANNED} planned vehicles delivering under "
            f"{_fmt(STARVED_RAMP_FRACTION, 2)} of them): "
            f"{', '.join(summary.starved_ramps)}."
        )
    return text


def _weave_exit_notes(micro_runs: list[_RunInfo]) -> list[str]:
    """One sentence per weaving section on the exits its runs gave up.

    An exit-bound vehicle halted at the gore's end is rerouted through
    (``meta.json["weave_sections"][i]["n_missed_exit"]``, docs/CONTRACTS.md
    §2): it is missing from the exit's link flow and present on every
    mainline link downstream, which the GEH rows below cannot tell from a
    demand error. The counters are pooled by
    :func:`validation.battery.weave_exit_summary`, the same reading the
    corridor battery prints beside its insertion line.

    Args:
        micro_runs: The run set's microscopic runs.

    Returns:
        One sentence per section in the runs' section order; empty when no
        run lists weaving sections (silence, not a zero).
    """
    summary = weave_exit_summary([r.meta for r in micro_runs])
    threshold = f"{_fmt(_PERCENT * float(summary['threshold_share']), 3)} % threshold"
    notes: list[str] = []
    for section in summary["sections"]:
        missed = section["missed_exit"]
        share = float(missed["share"])
        share_text = (
            f"{_fmt(_PERCENT * share, 3)} %" if math.isfinite(share) else "no exiter reached"
        )
        exit_name = f" (exit {section['exit']})" if section["exit"] else ""
        state = f"above the {threshold}" if section["flagged"] else f"within the {threshold}"
        notes.append(
            f"Weave exits at {section['ramp']}{exit_name}: {missed['n']} of "
            f"{section['reached']} reached exiters ({share_text}) were given up at the "
            f"gore's end and rerouted through over {section['n_runs']} run(s), {state}; "
            "the exit's link flow is short by that count and every mainline link "
            "downstream carries it."
        )
    return notes


#: Collision locations the limitations bullet names before pointing at the
#: Model integrity table for the rest (the table lists every one).
LIMITATION_MAX_LOCATIONS = 5


def _position_text(row: Mapping[str, Any]) -> str:
    """``lo–hi`` [m] of one collision location row ('—' when none was logged)."""
    lo, hi = row.get("pos_m_min"), row.get("pos_m_max")
    if lo is None or hi is None:
        return _fmt(None)
    if lo == hi:
        return f"{float(lo):.1f}"
    return f"{float(lo):.1f}–{float(hi):.1f}"


def _count_text(value: object) -> str:
    """A vehicle count for the template ('—' when not recorded)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _fmt(None)
    return str(int(value))


def _lock_context(locks: Mapping[str, Any] | None) -> dict[str, Any]:
    """The Model integrity section's lock line, table, banner and limitations.

    Args:
        locks: :func:`validation.locks.lock_summary` over the micro runs
            (labelled by their directory under the run set), or None when no
            run is recorded.

    Returns:
        ``{lock_line, lock_rows, lock_note, lock_banner, lock_limitations}``;
        every number in them is formatted from the summary.
    """
    minutes = _fmt(LOCK_MIN_DURATION_S / _SECONDS_PER_MINUTE, 3)
    if locks is None:
        return {
            "lock_line": (
                "not recorded — no run in this set has the files the lock detector reads "
                "(edges.parquet, vehicles.parquet)."
            ),
            "lock_rows": [],
            "lock_note": None,
            "lock_banner": None,
            "lock_limitations": [
                "No run in this set has the files the lock detector reads, so a simulation "
                "free of permanent standstills is not established."
            ],
        }
    share = locks["share_locked"]
    n_locked, n_recorded = int(locks["n_runs_locked"]), int(locks["n_runs_recorded"])
    not_recorded = [str(n) for n in locks["runs_not_recorded"]]
    line = (
        f"{n_locked} of {n_recorded} run(s) locked ({_fmt(_PERCENT * float(share['value']), 3)} %; "
        f"{_fmt(SHARE_CI_LEVEL * _PERCENT, 3)} % Clopper–Pearson interval "
        f"{_fmt(_PERCENT * float(share['lo95']), 3)}–{_fmt(_PERCENT * float(share['hi95']), 3)} %). "
        f"A lock is a queue standing with no discharge past a point for at least {minutes} "
        "min"
    )
    if not_recorded:
        line += f"; not recorded for {len(not_recorded)} run(s) ({', '.join(not_recorded)})"
    locked_runs = {str(run["run"]) for run in locks["runs_locked"]}
    partial = [
        str(n) for n in locks.get("runs_partially_recorded") or [] if str(n) not in locked_runs
    ]
    if partial:
        line += (
            f"; no lock established for {len(partial)} run(s) ({', '.join(partial)}): read at "
            "the run's end only (vehicles.parquet, no edges.parquet), which cannot see a lock "
            "released before the end, so the count is a lower bound"
        )
    seeded_rows = list(locks.get("seeded_standstills") or [])
    if seeded_rows:
        line += (
            f". {len(seeded_rows)} standstill(s) of {minutes} min or more at a seeded disturbance "
            "are not counted as locks: they are imposed (seeded=True), not a model defect ("
            + "; ".join(
                f"{row['run']} at {row['seeded_by']}, x {float(row['x_m']):.0f} m from "
                f"{float(row['onset_s']):.0f} s"
                for row in seeded_rows
            )
            + ")"
        )
    line += "."
    rows: list[dict[str, str]] = []
    for run in locks["runs_locked"]:
        for lock in run["locks"]:
            onset = float(lock["onset_s"])
            rows.append(
                {
                    "run": str(run["run"]),
                    "x": f"{float(lock['x_m']):.1f}",
                    "section": str(lock["section"])
                    + ("" if lock["kind"] is None else f" ({lock['kind']})"),
                    "onset": ("≤ " if lock["onset_is_upper_bound"] else "") + f"{onset:.0f}",
                    "duration": (
                        "≥ " if lock["persists_to_end"] or lock["onset_is_upper_bound"] else ""
                    )
                    + _fmt(float(lock["duration_s"]) / _SECONDS_PER_MINUTE, 3),
                    "end": "yes" if lock["persists_to_end"] else "no",
                    "trapped": _count_text(lock["n_trapped_in_network"]),
                    "never": _count_text(lock["n_never_departed_upstream"]),
                }
            )
    note = (
        "Onset is when the head started standing (simulation time); ≤ marks an upper bound "
        "read at the run's end, ≥ a duration cut short by the run's end. Trapped: vehicles "
        "still in the network at the end, upstream of the head and bound past it. Never "
        "departed: vehicles of origins at or upstream of the head that never entered the "
        "network, any ordinary backlog of those origins included."
        if rows
        else None
    )
    banner: str | None = None
    limitations: list[str] = []
    if n_locked > 0:
        banner = (
            f"MODEL INTEGRITY FAILURE — {n_locked} of {n_recorded} run(s) locked (a permanent "
            "standstill); the no_locks acceptance criterion fails. Every metric of those runs "
            "includes the vehicles trapped behind the lock: see Model integrity and Limitations."
        )
        places = "; ".join(
            f"{run['run']} at "
            + ", ".join(
                f"{lk['section']} from {('≤ ' if lk['onset_is_upper_bound'] else '')}"
                f"{float(lk['onset_s']):.0f} s"
                for lk in run["locks"]
            )
            for run in locks["runs_locked"]
        )
        limitations.append(
            f"This run set contains {n_locked} locked run(s) of {n_recorded} ({places}). A "
            "lock is a model defect, not a traffic outcome: the vehicles trapped behind it never "
            "finish, so the travel times of those runs are censored and their flows past the lock "
            "fall to zero, and every metric and interval above includes them; see Model "
            "integrity."
        )
    if not_recorded:
        limitations.append(
            f"Locks were not recorded for {len(not_recorded)} of {locks['n_runs']} run(s) "
            f"({', '.join(not_recorded)}); a simulation free of permanent standstills is not "
            "established for them."
        )
    if partial:
        limitations.append(
            f"Locks were read at the run's end only for {len(partial)} of {locks['n_runs']} "
            f"run(s) ({', '.join(partial)}; vehicles.parquet without edges.parquet). That reader "
            "cannot see a lock released before the end, nor one whose last crossing vehicles were "
            "still in the network near the end, so where it found none a simulation free of "
            "permanent standstills is not established."
        )
    return {
        "lock_line": line,
        "lock_rows": rows,
        "lock_note": note,
        "lock_banner": banner,
        "lock_limitations": limitations,
    }


def _integrity_context(
    micro_runs: list[_RunInfo], run_set: Path, locks: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The Model integrity section and its limitations bullets.

    Collisions are pooled by :func:`validation.battery.collision_summary`
    over every micro run's ``meta.json`` — the same reading the corridor
    battery artifact's ``collisions`` block carries — with each run labelled
    by its directory under the run set, and forced lane changes by
    :func:`validation.battery.forced_change_summary`. A run set whose
    metadata carries no collision counter is "not recorded", never zero,
    and says so in the limitations too; a run set with collisions gets a
    limitations bullet naming the count, the runs and the lanes.

    Locks come from ``locks`` (:func:`_lock_context`): their line, table,
    banner and limitations bullets follow the collisions'.

    Args:
        micro_runs: The run set's microscopic runs.
        run_set: The run-set root (run labels are paths relative to it).
        locks: :func:`validation.locks.lock_summary` over the micro runs, or
            None when no run is recorded.

    Returns:
        ``{collision_line, collision_runs, forced_lines, locations,
        location_note, limitations, banner, lock_line, lock_rows, lock_note,
        lock_banner}`` for the template; every number in them is formatted
        from the summaries. ``banner`` is the model-integrity failure line
        under the title when any run records a collision (the
        ``no_collisions`` criterion fails), else None; ``lock_banner`` the
        same for a locked run (``no_locks``).
    """
    lock = _lock_context(locks)
    lock_limitations = lock.pop("lock_limitations")
    names = [str(r.path.relative_to(run_set)) for r in micro_runs]
    metas = [r.meta for r in micro_runs]
    summary = collision_summary(metas, labels=names)
    forced_lines: list[str] = []
    for row in forced_change_summary(metas):
        head = f"Forced lane changes at {row['ramp']} ({row['model']}): "
        if row["n_runs"] == 0:
            forced_lines.append(head + "not recorded (no run records the model's counters).")
            continue
        forced_lines.append(
            head + f"{row['n_forced']} of {row['n_changed']} completed change(s) over "
            f"{row['n_runs']} run(s) were made under SUMO's forced lane-change mode, in which "
            "SUMO refuses a change only on an overlap — the path around its own lane-change "
            "safety checks."
        )

    if summary is None:
        return {
            "collision_line": (
                "not recorded — no run in this set carries the collision counter "
                "(meta.json n_collisions; runs written before it existed)."
            ),
            "collision_runs": "",
            "forced_lines": forced_lines,
            "locations": [],
            "location_note": None,
            "limitations": [
                "No run in this set records a collision count, so a collision-free "
                "simulation is not established.",
                *lock_limitations,
            ],
            "banner": None,
            **lock,
        }

    total = int(summary["total"])
    not_recorded = [str(n) for n in summary["runs_not_recorded"]]
    per = summary["per_run"]
    line = f"{total} over {summary['n_runs_recorded']} run(s) that record the counter"
    if not_recorded:
        line += f", not recorded for {len(not_recorded)} run(s) ({', '.join(not_recorded)})"
    line += (
        f"; per run {_fmt(per['mean'])} [{_fmt(per['lo95'])}, {_fmt(per['hi95'])}] "
        f"(two-sided {_fmt(CI_LEVEL * _PERCENT, 3)} % t-interval, n = {per['n']}"
        + (", underpowered" if per["underpowered"] else "")
        + ")"
    )
    rate = summary["rate"]
    if rate["n_runs"] == 0:
        line += "; no rate: no run that records collisions records its departed vehicles"
    elif not math.isfinite(float(rate["value"])):
        line += "; no rate: no vehicle departed"
    else:
        line += (
            f"; {_fmt(float(rate['value']), 3)} per {int(rate['per_vehicles']):,} departed "
            f"vehicles ({rate['n_collisions']} over {rate['n_departed']} departed in "
            f"{rate['n_runs']} run(s), whole runs including warm-up)"
        )
    line += "."
    runs_text = ", ".join(f"{w['run']} ({w['n']})" for w in summary["runs_with_collisions"])
    rows = summary["locations"]
    unlocated = total - int(summary["n_logged"])
    location_note = (
        f"The table places the {summary['n_logged']} collision(s) the runs' metadata logs; "
        f"{unlocated} more are counted but not located (a run's meta.json lists only its "
        "first events)."
        if unlocated > 0
        else None
    )

    limitations: list[str] = []
    banner: str | None = None
    if total > 0:
        banner = (
            f"MODEL INTEGRITY FAILURE — {total} SUMO collision(s) in "
            f"{summary['n_runs_with_collisions']} of {summary['n_runs_recorded']} run(s); "
            "the no_collisions acceptance criterion fails. A collision is a model "
            "defect, not a traffic outcome: see Model integrity and Limitations."
        )
        shown = rows[:LIMITATION_MAX_LOCATIONS]
        places = [
            f"lane `{r['lane']}` (edge `{r['edge']}`"
            + (f", {_position_text(r)} m" if r.get("pos_m_min") is not None else "")
            + f"): {r['n']}"
            for r in shown
        ]
        if len(rows) > len(shown):
            places.append(f"{len(rows) - len(shown)} more lane(s) in the Model integrity table")
        if unlocated > 0:
            places.append(f"{unlocated} not located")
        where = "; ".join(places) if places else "no logged location"
        limitations.append(
            f"This run set contains {total} SUMO collision(s) in "
            f"{summary['n_runs_with_collisions']} of {summary['n_runs_recorded']} run(s) "
            f"({runs_text}), at {where}. A collision is a model defect, not a traffic "
            "outcome, and every metric of those runs includes the vehicles involved; see "
            "Model integrity."
        )
    if not_recorded:
        limitations.append(
            f"Collisions were not recorded for {len(not_recorded)} of {summary['n_runs']} "
            f"run(s) ({', '.join(not_recorded)}); a collision-free simulation is not "
            "established for them."
        )
    return {
        "collision_line": line,
        "collision_runs": runs_text,
        "forced_lines": forced_lines,
        "locations": [
            {
                "lane": str(r["lane"]),
                "edge": str(r["edge"]),
                "n": str(r["n"]),
                "pos": _position_text(r),
                "runs": ", ".join(str(x) for x in r["runs"]),
            }
            for r in rows
        ],
        "location_note": location_note,
        "limitations": [*limitations, *lock_limitations],
        "banner": banner,
        **lock,
    }


def _criteria_rows(results: list[CriteriaResult]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for c in results:
        rows.append(
            {
                "name": c.name,
                "value": _fmt(c.value),
                "threshold": c.threshold,
                "evaluated": "yes" if c.evaluated else "no",
                # A row the run set could not evaluate (or whose input some run
                # did not record) is neither a pass nor a fail; naming it FAIL
                # would let a reader count it as evidence.
                "result": c.status + (f" — {c.detail}" if c.detail else ""),
            }
        )
    return rows


def _metric_rows(agg: Mapping[str, CI]) -> list[dict[str, str]]:
    return [
        {
            "name": metric_label(name),
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
                "is_baseline": g.is_baseline,
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
                    "name": metric_label(name),
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


#: Columns of the strategy-comparison table: metric field → column header.
#: The five headline metrics of CLAUDE.md §0.3 (throughput, travel time,
#: σ_v, energy, waves) — one representative each, so the table fits a page
#: and no reader picks the flattering variant of a metric from a wide grid.
#: The per-group Metrics sections carry every field.
COMPARISON_METRICS: tuple[tuple[str, str], ...] = (
    ("throughput_veh_h", "Throughput [veh/h]"),
    ("mean_tt_s", "Mean travel time [s]"),
    ("sigma_v_temporal_ms", "σ_v temporal [m/s]"),
    ("fuel_ml_per_veh_km", f"Fuel, {FUEL_LABEL} [ml/veh·km]"),
    ("wave_count", "Wave count"),
)

#: Marker printed for a contrast whose interval excludes / includes zero.
RESOLVED_MARK = "resolved"
UNRESOLVED_MARK = "unresolved"


def _ci_text(ci: CI | None) -> str:
    """``mean [lo, hi]`` of one aggregate, or ``—`` when the group has none."""
    if ci is None:
        return _fmt(None)
    return f"{_fmt(ci.mean)} [{_fmt(ci.lo95)}, {_fmt(ci.hi95)}]"


def _delta_text(d: DeltaCI) -> str:
    """``Δ mean [lo, hi] resolved|unresolved`` of one contrast."""
    mark = RESOLVED_MARK if d.resolved else UNRESOLVED_MARK
    return f"Δ {_fmt(d.mean)} [{_fmt(d.lo95)}, {_fmt(d.hi95)}] {mark}"


def _comparison_rows(groups: list[_Group], baseline: _Group | None) -> list[dict[str, Any]]:
    """One row per configuration for the strategy-comparison table.

    Each cell of a row carries that group's mean with its replicate interval
    and, off the baseline row, the group-minus-baseline contrast
    (:func:`contrast` — seed-paired or Welch, exactly as the per-group
    contrast tables compute it) with its interval and resolution. Nothing is
    re-derived here: the means come from the group aggregates and the deltas
    from the same function the contrast tables use, so the summary table can
    never disagree with the sections above it.

    Args:
        groups: Every configuration group, baseline first
            (:func:`_group_runs` sorts them).
        baseline: The single uncontrolled group, or ``None`` when the run set
            has no baseline or more than one — the rows then carry means
            only, since there is no unambiguous reference to subtract.

    Returns:
        One dict per group with its label, config hash, baseline flag, the
        contrast method (empty on the baseline row and when there is none)
        and one cell string per :data:`COMPARISON_METRICS` column.
    """
    rows: list[dict[str, Any]] = []
    for g in groups:
        cells: list[str] = []
        methods: set[str] = set()
        for name, _ in COMPARISON_METRICS:
            text = _ci_text(g.agg.get(name))
            if baseline is not None and g is not baseline:
                d = contrast(baseline.values(name), g.values(name))
                methods.add(d.method)
                text = f"{text} · {_delta_text(d)}"
            cells.append(text)
        method_text = ""
        if methods:
            method_text = "seed-paired" if methods == {"paired"} else "Welch"
        rows.append(
            {
                "label": g.label,
                "config_hash": g.config_hash,
                "is_baseline": g is baseline,
                "method_text": method_text,
                "cells": cells,
            }
        )
    return rows


#: What the strategy sections say instead of their tables when the gate failed.
STRATEGY_WITHHELD = (
    "Not delivered: the model did not reproduce the corridor (the baseline gate failed, "
    f"{PROTOCOL_DOC} section 6). Strategy runs are kept for internal learning only; their "
    "numbers are not findings and are not shown."
)

#: The speed-contour section's line when strategy results are withheld.
CONTOURS_WITHHELD = (
    "Contours of the strategy configurations are not shown: the model did not reproduce the "
    "corridor (the baseline gate failed), so they are not findings. The baseline's contours "
    "follow."
)

#: Confidence-table answers.
CONFIDENT_YES = "yes"
CONFIDENT_NO = "no"
CONFIDENT_NA = "not applicable"
CONFIDENT_UNKNOWN = "not established"
CONFIDENT_RANGES = "only as ranges"


def _gate_checks(gate: GateResult, name: str) -> list[CheckResult]:
    """Every result of one check (both day sets, in gate order)."""
    return [c for c in gate.checks if c.check == name]


def _confidence_row(
    statement: str, gate: GateResult | None, name: str, *, allow_na: bool = False
) -> dict[str, str]:
    """One confidence row from one check's statuses (all day sets must pass)."""
    if gate is None:
        return {
            "statement": statement,
            "confident": CONFIDENT_UNKNOWN,
            "basis": "the baseline gate was not evaluated for this run set",
        }
    found = _gate_checks(gate, name)
    basis = " ".join(c.plain for c in found) or "the gate result carries no such check"
    if not found:
        answer = CONFIDENT_UNKNOWN
    elif all(c.status == "pass" for c in found):
        answer = CONFIDENT_YES
    elif allow_na and all(c.status == "not_applicable" for c in found):
        answer = CONFIDENT_NA
    else:
        answer = CONFIDENT_NO
    return {"statement": statement, "confident": answer, "basis": basis}


def _pct_interval(baseline: _Group, group: _Group, name: str) -> tuple[DeltaCI, float, float]:
    """The contrast of one metric and its interval as percent of the baseline mean."""
    d = contrast(baseline.values(name), group.values(name))
    finite = [v for v in baseline.values(name).values() if math.isfinite(v)]
    base_mean = float(np.mean(finite)) if finite else math.nan
    if not math.isfinite(base_mean) or base_mean == 0.0:
        return d, math.nan, math.nan
    return d, _PERCENT * d.lo95 / base_mean, _PERCENT * d.hi95 / base_mean


def _signed(value: float) -> str:
    return f"{value:+.1f}" if math.isfinite(value) else _fmt(None)


def recommendation_metric(baseline: _Group, group: _Group) -> tuple[str, str] | None:
    """The ``(field, name)`` a strategy's recommendation line is stated on.

    :data:`DELAY_RECOMMENDATION_METRIC` when every run of both groups carries
    the waiting metrics, else None: no recommendation is made (module
    docstring).
    """
    if baseline.carries_waiting and group.carries_waiting:
        return DELAY_RECOMMENDATION_METRIC
    return None


def _waiting_missing(baseline: _Group, group: _Group) -> str:
    """Which side lacks the delay measure, in plain words (empty when neither)."""
    lacking = [
        f"{len([r for r in g.runs if r.seed not in g.waiting])} of {len(g.runs)} run(s) of "
        f"{'the baseline' if g is baseline else group.label}"
        for g in (baseline, group)
        if not g.carries_waiting
    ]
    return " and ".join(lacking)


def _recommendation(baseline: _Group, group: _Group, ci_pct: str) -> str:
    """The client summary's line for one strategy (module docstring)."""
    flag = zero_collisions(collision_counts([r.meta for r in group.runs]))
    if flag is False:
        return (
            f"No recommendation for {group.label}: its runs recorded SUMO collisions, which "
            f"disqualify a setting ({PROTOCOL_DOC} section 8.4)."
        )
    if flag is None:
        return (
            f"No recommendation for {group.label}: its runs do not all record the collision "
            "counter, so a collision-free setting is not established."
        )
    chosen = recommendation_metric(baseline, group)
    if chosen is None:
        return (
            f"No recommendation for {group.label}: {_waiting_missing(baseline, group)} record "
            "no total delay including waiting time (no demand ledger, journeys.parquet), so "
            "it is not shown that the strategy does not simply hold vehicles on ramps or off "
            "the road; a recommendation rests on that measure only "
            f"({PROTOCOL_DOC} sections 8.2 and 8.4)."
        )
    field_name, measure = chosen
    unmeasured = [
        g.label
        for g in (baseline, group)
        if not all(math.isfinite(v) for v in g.values(field_name).values())
    ]
    if unmeasured:
        return (
            f"No recommendation for {group.label}: {measure} could not be computed for every "
            f"run of {' and '.join(unmeasured)} (a vehicle without its route geometry), so the "
            "effect on it is not established."
        )
    thr, _, _ = _pct_interval(baseline, group, THROUGHPUT_METRIC)
    if thr.resolved and thr.hi95 < 0.0:
        return (
            f"No recommendation for {group.label}: its throughput interval lies entirely "
            f"below the baseline's ({PROTOCOL_DOC} section 8.4)."
        )
    d, lo_pct, hi_pct = _pct_interval(baseline, group, field_name)
    if d.resolved and d.hi95 < 0.0 and math.isfinite(lo_pct) and math.isfinite(hi_pct):
        return (
            f"On this model, strategy {group.label} reduced {measure} by {-hi_pct:.1f}–"
            f"{-lo_pct:.1f} % ({ci_pct} % interval) relative to doing nothing; this is a "
            "model prediction."
        )
    return (
        f"No recommendation for {group.label}: its {measure} changed by {_signed(lo_pct)} to "
        f"{_signed(hi_pct)} % ({ci_pct} % interval) relative to doing nothing, which does not "
        "show a reduction."
    )


def _lock_confidence(locks: Mapping[str, Any] | None) -> dict[str, str]:
    """The client summary's confidence row on locks (from the run set, not the gate)."""
    statement = "No permanent standstill (gridlock) in any run"
    if locks is None:
        return {
            "statement": statement,
            "confident": CONFIDENT_UNKNOWN,
            "basis": "no run in this set has the files the lock detector reads",
        }
    n_locked, n_recorded = int(locks["n_runs_locked"]), int(locks["n_runs_recorded"])
    minutes = _fmt(LOCK_MIN_DURATION_S / _SECONDS_PER_MINUTE, 3)
    if n_locked > 0:
        places = ", ".join(f"{row['section']} ({row['n_runs']})" for row in locks["by_section"])
        return {
            "statement": statement,
            "confident": CONFIDENT_NO,
            "basis": f"{n_locked} of {n_recorded} run(s) locked, at {places}: a queue stood with "
            f"no discharge for {minutes} min or more and its vehicles never finished (see Model "
            "integrity)",
        }
    if locks["runs_not_recorded"]:
        return {
            "statement": statement,
            "confident": CONFIDENT_UNKNOWN,
            "basis": f"no lock in {n_recorded} recorded run(s), but "
            f"{len(locks['runs_not_recorded'])} run(s) carry no lock record",
        }
    partial = list(locks.get("runs_partially_recorded") or [])  # none locked: n_locked is 0 here
    if partial:
        # the run-end reader alone never establishes "no lock" (review 2026-10-07, finding 4)
        return {
            "statement": statement,
            "confident": CONFIDENT_UNKNOWN,
            "basis": f"no lock found in {n_recorded} run(s), but {len(partial)} of them were "
            "read at the run's end only, which cannot see a lock released before the end",
        }
    return {
        "statement": statement,
        "confident": CONFIDENT_YES,
        "basis": f"no run of {n_recorded} had a queue standing with no discharge for {minutes} "
        "min or more",
    }


def _client_summary(
    gate: GateResult | None,
    groups: list[_Group],
    baseline: _Group | None,
    observed: ObservedProvenance | None,
    locks: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The client summary section's context (module docstring).

    Every sentence is assembled from the gate's computed checks and the run
    set's computed contrasts; nothing here is typed by a caller.

    Args:
        gate: The baseline gate, or None when it was not evaluated.
        groups: Every configuration group, baseline first.
        baseline: The single baseline group, or None.
        observed: The observed-data provenance, if any.
        locks: :func:`validation.locks.lock_summary` over the run set's micro
            runs (None: not recorded); its confidence row follows the gate's
            collision row.

    Returns:
        ``{gate_line, check_lines, confidence, strategy_statement,
        strategy_headers, strategy_rows, recommendations, data_lines, limits,
        withheld}`` for the template; ``withheld`` is the "not delivered"
        sentence when the gate failed, else None.
    """
    ci_pct = _fmt(CI_LEVEL * _PERCENT, 3)
    arms = [g for g in groups if g is not baseline and not g.is_baseline]
    unattributed = gate is not None and not str(gate.config_hash or "").strip()
    if unattributed:
        # A gate that names no configuration cannot be checked against this
        # report's baseline (module docstring): it is not evaluated here.
        gate = None
    mismatch = ""
    if gate is not None and gate.config_hash and baseline is not None:
        if gate.config_hash != baseline.config_hash:
            mismatch = (
                f"The baseline gate was evaluated on configuration `{gate.config_hash}`; this "
                f"report's baseline is `{baseline.config_hash}`, so the gate does not apply to "
                "it and no strategy recommendation is made."
            )
    allowed = gate is not None and gate.passed and not mismatch and baseline is not None
    withheld = STRATEGY_WITHHELD if gate is not None and not gate.passed else None

    if gate is None:
        gate_line = (
            (
                "Baseline gate: NOT EVALUATED. The supplied gate result names no configuration "
                "(its config_hash is empty), so it cannot be tied to this report's baseline "
                "and is treated as not evaluated: the model has not been shown to reproduce "
                f"the corridor under the study protocol ({PROTOCOL_DOC} section 6), and this "
                "report makes no strategy recommendation."
            )
            if unattributed
            else (
                "Baseline gate: NOT EVALUATED. No baseline gate result was supplied with this "
                "run set, so the model has not been shown to reproduce the corridor under the "
                f"study protocol ({PROTOCOL_DOC} section 6), and this report makes no strategy "
                "recommendation."
            )
        )
        check_lines: list[str] = []
    else:
        gate_line = gate.headline()
        check_lines = [
            f"{c.check} {status_text(c.status)}"
            + ("" if c.gating else " (reported, not part of the gate)")
            + f": {c.plain} Source: {c.label}."
            for c in gate.checks
        ]
    if mismatch:
        check_lines.append(mismatch)

    minutes = f"{SPEED_AGGREGATION_S / _SECONDS_PER_MINUTE:g}"
    confidence = [
        _confidence_row("Traffic counts at the detectors (link flows, C1)", gate, "C1"),
        _confidence_row(f"Speeds at the detectors, {minutes}-minute averages (C3)", gate, "C3"),
        _confidence_row("Where and when the slowdowns form (bottlenecks, C6)", gate, "C6"),
        _confidence_row("Stop-and-go wave speed (C4)", gate, "C4", allow_na=True),
        _confidence_row("No simulated collisions (C5)", gate, "C5"),
        _lock_confidence(locks),
        {
            "statement": FUEL_ESTIMATE_TEXT[:1].upper() + FUEL_ESTIMATE_TEXT[1:],
            "confident": CONFIDENT_NO,
            "basis": "fuel is not validated: no measured fuel was compared, so every fuel "
            f"figure is a {FUEL_LABEL} ({PROTOCOL_DOC} section 8.6)",
        },
    ]
    if gate is None:
        effect = (
            CONFIDENT_NO,
            "the baseline gate was not evaluated, so no strategy result is a finding",
        )
    elif not gate.passed:
        effect = (CONFIDENT_NO, "not delivered: the model did not reproduce the corridor")
    elif mismatch:
        effect = (CONFIDENT_NO, "the gate was evaluated on another configuration")
    elif arms and baseline is None:
        effect = (CONFIDENT_NO, "no single baseline group to compare against")
    elif not arms:
        effect = (CONFIDENT_NA, "this run set has no strategy arm")
    else:
        effect = (
            CONFIDENT_RANGES,
            f"model predictions with {ci_pct} % intervals over the replicates, relative to "
            "doing nothing; not measurements",
        )
    confidence.append(
        {"statement": "Effects of the strategies", "confident": effect[0], "basis": effect[1]}
    )
    confidence.append(
        {
            "statement": "Robustness of strategy effects to driver and demand uncertainty",
            "confident": CONFIDENT_NO,
            "basis": f"not evaluated in this report ({PROTOCOL_DOC} section 8.5)",
        }
    )
    waiting_everywhere = (
        baseline is not None and baseline.carries_waiting and all(g.carries_waiting for g in arms)
    )
    # Every run of every group carries the ledger, but the run set has no
    # single baseline group (e.g. a do-nothing battery whose merge models give
    # its configuration a non-baseline label): the waiting time IS counted, and
    # the missing recommendation is the baseline's absence, not the ledger's.
    waiting_without_baseline = (
        baseline is None and bool(groups) and all(g.carries_waiting for g in groups)
    )
    if arms:
        if waiting_everywhere:
            waiting_row = (
                CONFIDENT_YES,
                "counted: every run records total delay and travel time including waiting "
                f"({PROTOCOL_DOC} section 8.2), the only measure a recommendation is stated on",
            )
        elif waiting_without_baseline:
            waiting_row = (
                CONFIDENT_YES,
                "counted: every run records total delay and travel time including waiting "
                f"({PROTOCOL_DOC} section 8.2); no strategy recommendation is made because the "
                "run set has no single baseline (do-nothing) group to compare against",
            )
        else:
            waiting_row = (
                CONFIDENT_NO,
                "not recorded on every run (no demand ledger), so no strategy "
                f"recommendation is made ({PROTOCOL_DOC} section 8.2)",
            )
        confidence.append(
            {
                "statement": "Waiting time on ramps and before entering the network",
                "confident": waiting_row[0],
                "basis": waiting_row[1],
            }
        )

    table_metrics = (
        (*CLIENT_WAITING_METRICS, *CLIENT_STRATEGY_METRICS)
        if baseline is not None
        and baseline.carries_waiting
        and any(g.carries_waiting for g in arms)
        else CLIENT_STRATEGY_METRICS
    )
    headers = [f"{name}, change [%]" for _, name in table_metrics]
    strategy_rows: list[dict[str, Any]] = []
    recommendations: list[str] = []
    if gate is not None and not gate.passed:
        statement = (
            "This report contains no strategy recommendations: the model did not reproduce "
            "the corridor (the baseline gate failed), so strategy results are not delivered "
            "as findings."
        )
    elif gate is None:
        statement = (
            "This report contains no strategy recommendations: the baseline gate was not "
            "evaluated. Any strategy table below is model output, not a finding."
        )
    elif mismatch:
        statement = "This report contains no strategy recommendations: " + mismatch
    elif arms and baseline is None:
        statement = (
            "This report contains no strategy recommendations: the run set has no single "
            "baseline group to state the strategies' effects against."
        )
    elif not arms:
        statement = "The baseline gate passed; this run set has no strategy arm to compare."
    else:
        statement = (
            "The baseline gate passed. The strategy effects below are model predictions on "
            f"this corridor model, each the {ci_pct} % interval of the change relative to doing "
            "nothing, as a percentage of the baseline mean; they are not measurements."
        )
    if allowed and baseline is not None:
        for g in arms:
            cells = []
            for name, _ in table_metrics:
                _, lo_pct, hi_pct = _pct_interval(baseline, g, name)
                finite = math.isfinite(lo_pct) and math.isfinite(hi_pct)
                cells.append(f"{_signed(lo_pct)} to {_signed(hi_pct)}" if finite else _fmt(None))
            strategy_rows.append({"label": g.label, "cells": cells})
            recommendations.append(_recommendation(baseline, g, ci_pct))

    data_lines: list[str] = []
    split = gate.split if gate is not None else None
    if split:
        cal = ", ".join(str(d) for d in split.get("calibration_dates") or []) or "none"
        val = ", ".join(str(d) for d in split.get("validation_dates") or []) or "none"
        line = (
            f"Calibration days: {cal}. Validation days: {val}. Drawn by the protocol's seeded "
            f"split (seed {split.get('seed')}, {PROTOCOL_DOC} section 3)."
        )
        if split.get("underpowered"):
            line += f" The validation is underpowered: {split.get('underpowered_reason')}."
        data_lines.append(line)
    else:
        data_lines.append(
            "Calibration and validation days: not stated (no day split accompanies this report)."
        )
    if gate is not None:
        if gate.excluded_detectors:
            data_lines.extend(
                f"Excluded detector {name}: {why}"
                for name, why in sorted(gate.excluded_detectors.items())
            )
        else:
            data_lines.append("Excluded detectors: none recorded in the observations.")
    else:
        data_lines.append("Excluded detectors: not stated (no baseline gate result).")
    if observed is not None:
        data_lines.append(
            f"Observed data: {observed.path or observed.corridor}"
            + (f", dates {observed.dates}" if observed.dates else "")
            + f"; {observed.aggregation}."
        )
    data_lines.append(
        "Ramp volumes estimated from mainline differences, and every split assumption behind "
        "them, are listed in the study's ramp-estimation artifact, not in this run set "
        f"({PROTOCOL_DOC} section 2.3)."
    )
    if arms:
        data_lines.append(
            "Strategy assumptions: each strategy arm's penetration, compliance and settings "
            "are as its label states; they are assumptions, not measured behaviour."
        )

    limits = [
        "Single corridor: the results describe this corridor, period and day set; transfer "
        "to other corridors is not established.",
        "Model-form uncertainty: the car-following and lane-change models' own assumptions are "
        "not captured by the seed-to-seed intervals.",
        FUEL_LIMITATION,
        "Compliance and strategy assumptions: strategy results hold only for the settings "
        "simulated, under the configured driver population and demand.",
        "Results are reported as they came out, including failures.",
    ]
    return {
        "gate_line": gate_line,
        "check_lines": check_lines,
        "confidence": confidence,
        "strategy_statement": statement,
        "strategy_headers": headers,
        "strategy_rows": strategy_rows,
        "recommendations": recommendations,
        "data_lines": data_lines,
        "limits": limits,
        "withheld": withheld,
    }


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
    segment_speeds_obs: Sequence[Sequence[float]] | None = ...,
    segment_speeds_sim: Sequence[Sequence[float]] | None = ...,
    segment_window_s: float | None = ...,
    observed: ObservedProvenance | None = ...,
    metrics_by_run: Mapping[str | Path, Metrics] | None = ...,
    wave_readings_by_run: Mapping[str | Path, float] | None = ...,
    figure_runs: Sequence[str | Path] | None = ...,
    gate: GateResult | None = ...,
    scored_end_s: float | None = ...,
    locks_by_run: Mapping[str | Path, RunLocks] | None = ...,
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
    segment_speeds_obs: Sequence[Sequence[float]] | None = ...,
    segment_speeds_sim: Sequence[Sequence[float]] | None = ...,
    segment_window_s: float | None = ...,
    observed: ObservedProvenance | None = ...,
    metrics_by_run: Mapping[str | Path, Metrics] | None = ...,
    wave_readings_by_run: Mapping[str | Path, float] | None = ...,
    figure_runs: Sequence[str | Path] | None = ...,
    gate: GateResult | None = ...,
    scored_end_s: float | None = ...,
    locks_by_run: Mapping[str | Path, RunLocks] | None = ...,
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
    observed: ObservedProvenance | None = None,
    metrics_by_run: Mapping[str | Path, Metrics] | None = None,
    wave_readings_by_run: Mapping[str | Path, float] | None = None,
    figure_runs: Sequence[str | Path] | None = None,
    gate: GateResult | None = None,
    scored_end_s: float | None = None,
    locks_by_run: Mapping[str | Path, RunLocks] | None = None,
) -> Path | tuple[Path, Path]:
    """Generate a markdown (optionally PDF) validation report for a run set.

    Discovers every run directory (containing ``meta.json``) under
    ``run_set_dir``, groups the microscopic runs by ``config_hash``,
    computes and aggregates metrics per group, evaluates the acceptance
    criteria, renders speed-contour figures beside the report, and writes
    the markdown report. With a baseline group and at least one controlled
    group the report also carries a controller-minus-baseline contrast table
    (:func:`contrast`) and seed-matched contour pairs. The Model integrity
    section (collisions and forced lane changes, :func:`_integrity_context`)
    is read from the micro runs' ``meta.json`` alone. See the module
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
        observed: Provenance of the observations ``geh_values`` and
            ``rmspe_value`` were computed against
            (:func:`validation.observed.pool_scores`); adds the
            **Observed data** block naming the source, its window grid, its
            coverage and how many comparisons were formed. ``None`` omits the
            block — and, with ``geh_values``/``rmspe_value`` also ``None``,
            leaves the two criteria rows honestly *not evaluated* for want of
            an input. Supplied but with those two ``None`` (no comparable
            window was formed), the rows stay unevaluated and say *that*
            instead.
        metrics_by_run: Per-replicate metrics the caller already computed,
            keyed by run directory (any spelling of the path; resolved
            before lookup). A run found here is not re-measured and its
            trajectory is not read; every other run is measured with
            :func:`validation.metrics.compute_metrics` and the ``x_ref`` /
            ``span`` above. The caller vouches that the stored metrics were
            computed with those same arguments (``scripts/corridor_battery.py``
            records them in each replicate's ``metrics.json``).
        wave_readings_by_run: The profile detector's per-replicate backward
            wave speed [km/h] (NaN = no front), keyed likewise; a run found
            here is not re-binned for the wave-speed criterion row. The
            reading must come from ``profile.wave_detector`` on the run's
            measurement window (:func:`validation.battery.replicate_wave_speed_kmh`
            is that measurement).
        figure_runs: Run directories that get a speed-contour figure;
            ``None`` renders one for every run. With the two mappings above
            covering every run, the report then reads only the listed runs'
            trajectories — the corridor battery lists its first seed, the
            one it keeps after pruning.
        gate: The no-strategy configuration's baseline gate
            (:func:`validation.baseline_gate.evaluate_gate`), which opens the
            report's client summary. ``None`` (the default) makes the summary
            say the gate was not evaluated and that no strategy
            recommendation is made; a failed gate also replaces the strategy
            tables with a "not delivered" statement.
        scored_end_s: End of the scored period [s] (simulation time) — the
            study period's end when the runs continue past it with a
            cool-down (docs/FRISCO_PROTOCOL.md §8.2). Every measured run is
            then scored on ``[warm-up, scored_end_s)``: its metrics
            (:func:`validation.metrics.compute_metrics`), its measures
            including waiting (only departures planned before it;
            :func:`validation.metrics.compute_waiting_metrics`), its wave
            field and its contours, and the measurement note says so.
            ``None`` (the default) scores to each run's end. The caller
            vouches that ``metrics_by_run`` and ``wave_readings_by_run`` were
            measured on the same window.
        locks_by_run: Each run's locks when the caller already detected them
            (:func:`validation.locks.detect_run_locks`), keyed like
            ``metrics_by_run``; every other micro run is read from its own
            ``edges.parquet`` and ``vehicles.parquet``. Locks are detected
            on the whole run, whatever ``scored_end_s``.

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
    if scored_end_s is not None:
        runs = [dataclasses.replace(r, scored_end_s=float(scored_end_s)) for r in runs]
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
    _fill_metrics(
        groups,
        x_ref,
        measure_span,
        {_run_key(k): v for k, v in (metrics_by_run or {}).items()},
    )

    # Wave-speed criterion: emergent means unseeded (CLAUDE.md §0.2, §7.1),
    # measured with the profile's own detector on that detector's own bins.
    # `evaluate` refuses (does not score) a value from another recipe, so
    # reusing the metrics reading would leave the row unevaluatable.
    det = p.wave_detector
    unseeded_runs = [r for r in reference.runs if not r.seeded]
    n_seeded_excluded = len(reference.runs) - len(unseeded_runs)
    known_readings = {_run_key(k): float(v) for k, v in (wave_readings_by_run or {}).items()}
    readings = [
        known_readings[_run_key(r.path)]
        if _run_key(r.path) in known_readings
        else det.measure(_load_field(r, dt_bin=det.dt_bin_s, dx_bin=det.dx_bin_m)).speed_kmh
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
    known_locks = {_run_key(k): v for k, v in (locks_by_run or {}).items()}
    lock_records: list[RunLocks] = [
        known_locks.get(_run_key(r.path)) or detect_run_locks(r.path, meta=r.meta)
        for r in micro_runs
    ]
    locks = lock_summary(
        lock_records, labels=[str(r.path.relative_to(run_set)) for r in micro_runs]
    )
    criteria_results = evaluate(
        p,
        geh_values=geh_values,
        rmspe_value=rmspe_value,
        wave_speed_kmh=wave_speed,
        ring_emergence=ring_emergence,
        ring_dampening=ring_dampening,
        n_seeds=len(set(smallest.seeds)),
        wave_detector=det,
        # An artifact that yielded no comparable window is still an artifact:
        # the two rows stay unevaluated, but they may not report the operator's
        # upload as missing (the observed-data block holds the reason).
        observations_supplied=observed is not None,
        # Model integrity (WP-98): every micro run of the set, every group.
        collision_counts=collision_counts([r.meta for r in micro_runs]),
        lock_records=lock_records,
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

    # An empty matrix (no analysis window fully inside the run, or no observed
    # cell) is "nothing to compare", not a malformed input: the RMSPE row stays
    # NOT EVALUATED and the observed-data block states the zero coverage.
    aggregation_rows = (
        speed_aggregation_rows(segment_speeds_obs, segment_speeds_sim, segment_window_s)
        if segment_speeds_obs is not None
        and segment_speeds_sim is not None
        and np.size(segment_speeds_obs) > 0
        and np.size(segment_speeds_sim) > 0
        else None
    )
    client = _client_summary(gate, groups, baseline, observed, locks)
    # A failed gate withholds the strategy configurations' results, their
    # contour panels included: only the baseline's contours are rendered.
    withheld = client["withheld"] is not None and baseline is not None
    figures = _render_figures(
        [baseline] if withheld and baseline is not None else groups,
        baseline,
        out.parent,
        None if figure_runs is None else frozenset(_run_key(p) for p in figure_runs),
    )

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
        client=client,
        strategy_withheld=client["withheld"],
        contours_withheld=CONTOURS_WITHHELD if withheld and len(groups) > 1 else None,
        fuel_note=FUEL_NOTE,
        fuel_limitation=FUEL_LIMITATION,
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
        insertion_note=_insertion_note(micro_runs),
        weave_exit_notes=_weave_exit_notes(micro_runs),
        integrity=_integrity_context(micro_runs, run_set, locks),
        calibrations=calibrations,
        observed=(
            _observed_rows(observed, p.wave_speed_band_kmh) if observed is not None else None
        ),
        observed_note=OBSERVED_NOTE,
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
        # One table across configurations; a single-configuration run set has
        # nothing to compare, so the section is omitted rather than printed
        # with one row.
        comparison=_comparison_rows(groups, baseline) if len(groups) > 1 else None,
        comparison_headers=[header for _, header in COMPARISON_METRICS],
        resolved_mark=RESOLVED_MARK,
        unresolved_mark=UNRESOLVED_MARK,
        comparison_note=(
            None
            if baseline is not None
            else (
                "No Δ column: this run set has no single baseline group, so there is "
                "no unambiguous reference to subtract. The cells are group means only."
            )
        ),
        figures=figures,
    )
    out.write_text(rendered)
    if pdf:
        return out, _render_pdf(out)
    return out
