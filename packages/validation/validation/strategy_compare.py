"""Fair strategy comparison table (docs/FRISCO_PROTOCOL.md §8.1, §8.3; WP-105).

Every strategy arm and the do-nothing baseline are compared on one **fixed
set of measures** (:data:`COMPARISON_MEASURES`, protocol §8.3), seed by seed
against the baseline (protocol §8.1: the same demand, seeds and scoring window
for every arm). The set was fixed before any strategy result was seen
(2026-10-04): throughput at the reference section, mean and 90th-percentile
travel time *including waiting*, total delay *including waiting* (protocol
§8.2: time on ramps behind a meter and in the insertion backlog counts —
``validation.metrics.WaitingMetrics``), the temporal and spatial speed
variation σ_v, the wave count and amplitude, SUMO collisions, and fuel —
labelled a model estimate (SUMO HBEFA4 emission classes, protocol §8.6).

The builder **refuses** (:class:`ComparisonRefusedError`) rather than
compare arms that do not carry every measure: a table missing the waiting
measures is exactly the comparison that flatters a strategy holding cars off
the road. A value that is present but undefined (NaN — e.g. a wave amplitude
when no wave formed) is not missing; it contributes nothing to its interval
and the interval's ``n`` says so. A collision count must be recorded for every
run (not recorded is not zero, ``validation.criteria.zero_collisions``).

Intervals are two-sided t-intervals at ``validation.metrics.CI_LEVEL``
(:func:`validation.metrics.ci`): the marginal interval of each arm's
replicate values, and the interval of the per-seed paired differences
``arm − baseline`` (common random numbers). Fewer than
``validation.metrics.MIN_REPLICATES`` seeds marks the table underpowered: it
may be read as a rehearsal, never quoted as a headline result (CLAUDE.md §0.6).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from validation.metrics import CI, MIN_REPLICATES, ci


@dataclass(frozen=True)
class Measure:
    """One measure of the comparison table.

    Attributes:
        key: The record key (a ``metrics.json`` field, or ``n_collisions``).
        label: Plain-language name, as printed.
        unit: Unit, as printed.
        lower_is_better: Direction of improvement (throughput: higher is
            better); informational, the table states differences only.
    """

    key: str
    label: str
    unit: str
    lower_is_better: bool = True


#: Record key of the per-run SUMO collision count (``meta.json["n_collisions"]``).
COLLISIONS_KEY: Final[str] = "n_collisions"

#: The label fuel carries wherever it appears (protocol §8.6).
FUEL_MODEL_ESTIMATE: Final[str] = "model estimate (SUMO HBEFA4), not measured"

#: The fixed measure set of protocol §8.3, in table order.
COMPARISON_MEASURES: Final[tuple[Measure, ...]] = (
    Measure("throughput_veh_h", "Throughput at the reference section", "veh/h", False),
    Measure("mean_tt_incl_waiting_s", "Mean travel time including waiting", "s"),
    Measure("p90_tt_incl_waiting_s", "90th-percentile travel time including waiting", "s"),
    Measure("total_delay_incl_waiting_veh_h", "Total delay including waiting", "veh·h"),
    Measure("sigma_v_temporal_ms", "Speed variation σ_v (temporal)", "m/s"),
    Measure("sigma_v_spatial_ms", "Speed variation σ_v (spatial)", "m/s"),
    Measure("wave_count", "Wave count", "waves"),
    Measure("wave_amplitude_ms", "Wave amplitude", "m/s"),
    Measure(COLLISIONS_KEY, "SUMO collisions", "events"),
    Measure("fuel_ml_per_veh_km", f"Fuel, {FUEL_MODEL_ESTIMATE}", "ml/veh·km"),
)

#: Keys every run of every arm must carry.
REQUIRED_KEYS: Final[tuple[str, ...]] = tuple(m.key for m in COMPARISON_MEASURES)

#: Missing values listed verbatim in a refusal (the count is exact).
_REFUSAL_EXAMPLES: Final[int] = 8

#: Percent per unit fraction.
_PERCENT: Final[float] = 100.0

Record = Mapping[str, Any]
"""One run's values: ``metrics.json`` fields plus :data:`COLLISIONS_KEY`."""


class ComparisonRefusedError(ValueError):
    """The arms cannot be compared fairly (module docstring); the message says why."""


@dataclass(frozen=True)
class PairedDelta:
    """``arm − baseline`` for one measure over seed-matched pairs.

    Attributes:
        mean: Mean paired difference.
        lo95: Lower interval bound (NaN with fewer than two pairs).
        hi95: Upper interval bound, likewise.
        n: Pairs with both values finite.
        resolved: True when the interval excludes zero.
        pct_of_baseline: ``mean`` as a percentage of the baseline's mean over
            the same seeds (NaN when that mean is zero or undefined).
    """

    mean: float
    lo95: float
    hi95: float
    n: int
    resolved: bool
    pct_of_baseline: float


@dataclass(frozen=True)
class ArmRow:
    """One arm of the table.

    Attributes:
        arm: Arm name.
        marginal: Measure key → replicate interval of the arm's values.
        paired: Measure key → difference against the baseline; empty for the
            baseline itself.
        collisions_total: Collisions summed over the arm's runs.
        zero_collisions: True when every run of the arm records zero.
    """

    arm: str
    marginal: dict[str, CI]
    paired: dict[str, PairedDelta]
    collisions_total: int
    zero_collisions: bool


@dataclass(frozen=True)
class ComparisonTable:
    """The comparison (module docstring).

    Attributes:
        baseline: Name of the baseline arm.
        seeds: The seed list every arm shares, ascending.
        measures: The measures, in table order.
        rows: Baseline first, then the other arms in the order given.
        underpowered: Fewer than ``MIN_REPLICATES`` seeds.
    """

    baseline: str
    seeds: tuple[int, ...]
    measures: tuple[Measure, ...]
    rows: tuple[ArmRow, ...]
    underpowered: bool

    def row(self, arm: str) -> ArmRow:
        """The row of one arm.

        Raises:
            KeyError: Unknown arm.
        """
        for r in self.rows:
            if r.arm == arm:
                return r
        raise KeyError(arm)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form (NaN → None)."""
        return {
            "baseline": self.baseline,
            "seeds": list(self.seeds),
            "n_seeds": len(self.seeds),
            "underpowered": self.underpowered,
            "measures": [
                {
                    "key": m.key,
                    "label": m.label,
                    "unit": m.unit,
                    "lower_is_better": m.lower_is_better,
                }
                for m in self.measures
            ],
            "arms": {
                r.arm: {
                    "marginal": {k: _ci_dict(v) for k, v in r.marginal.items()},
                    "vs_baseline_paired": {k: _delta_dict(v) for k, v in r.paired.items()},
                    "collisions_total": r.collisions_total,
                    "zero_collisions": r.zero_collisions,
                }
                for r in self.rows
            },
        }


def _num(x: float) -> float | None:
    return float(x) if math.isfinite(x) else None


def _ci_dict(c: CI) -> dict[str, Any]:
    return {"mean": _num(c.mean), "lo95": _num(c.lo95), "hi95": _num(c.hi95), "n": c.n}


def _delta_dict(d: PairedDelta) -> dict[str, Any]:
    return {
        "mean": _num(d.mean),
        "lo95": _num(d.lo95),
        "hi95": _num(d.hi95),
        "n": d.n,
        "resolved": d.resolved,
        "pct_of_baseline": _num(d.pct_of_baseline),
    }


def _value(record: Record, key: str) -> float | None:
    """A record's value of one key: None when absent, None or not a number."""
    v = record.get(key)
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def paired_delta(baseline: Mapping[int, float], other: Mapping[int, float]) -> PairedDelta:
    """``other − baseline`` over the seeds both carry with finite values.

    Args:
        baseline: Seed → baseline value.
        other: Seed → the arm's value.

    Returns:
        The :class:`PairedDelta` (t-interval of the differences).
    """
    seeds = sorted(s for s in baseline if s in other)
    pairs = [
        (other[s], baseline[s])
        for s in seeds
        if math.isfinite(other[s]) and math.isfinite(baseline[s])
    ]
    c = ci([a - b for a, b in pairs])
    base_mean = math.fsum(b for _, b in pairs) / len(pairs) if pairs else math.nan
    resolved = bool(math.isfinite(c.lo95) and math.isfinite(c.hi95) and (c.lo95 > 0 or c.hi95 < 0))
    pct = _PERCENT * c.mean / base_mean if math.isfinite(base_mean) and base_mean != 0 else math.nan
    return PairedDelta(c.mean, c.lo95, c.hi95, c.n, resolved, pct)


def build_comparison_table(
    arms: Mapping[str, Mapping[int, Record]],
    *,
    baseline: str = "baseline",
    measures: Sequence[Measure] = COMPARISON_MEASURES,
) -> ComparisonTable:
    """The strategy comparison table, or a refusal.

    Args:
        arms: Arm name → seed → that run's record (``metrics.json`` fields
            with :data:`COLLISIONS_KEY` from its ``meta.json``; None when not
            recorded). The baseline must be among them.
        baseline: Name of the do-nothing arm.
        measures: The measure set; the protocol's is the default and a
            caller should not narrow it.

    Returns:
        The :class:`ComparisonTable`.

    Raises:
        ComparisonRefusedError: No baseline arm; an arm without runs; an arm
            whose seed set differs from the baseline's (not a paired
            comparison); or any run of any arm lacking any measure — key
            absent, None, or not a number (the message lists examples and the
            exact count).
    """
    if baseline not in arms:
        raise ComparisonRefusedError(
            f"refusing the strategy comparison: no baseline arm {baseline!r} among {sorted(arms)}"
        )
    base_seeds = sorted(int(s) for s in arms[baseline])
    problems: list[str] = []
    for arm, runs in arms.items():
        seeds = sorted(int(s) for s in runs)
        if not seeds:
            problems.append(f"arm {arm!r} has no runs")
        elif seeds != base_seeds:
            only_arm = sorted(set(seeds) - set(base_seeds))
            only_base = sorted(set(base_seeds) - set(seeds))
            problems.append(
                f"arm {arm!r} does not share the baseline's seeds (only in the arm: "
                f"{only_arm}; only in the baseline: {only_base})"
            )
    if problems:
        raise ComparisonRefusedError(
            "refusing the strategy comparison: every arm must run the baseline's seeds "
            "(docs/FRISCO_PROTOCOL.md §8.1) — " + "; ".join(problems)
        )
    missing: list[tuple[str, int, str]] = [
        (arm, int(seed), m.key)
        for arm, runs in arms.items()
        for seed, record in sorted(runs.items())
        for m in measures
        if _value(record, m.key) is None
    ]
    if missing:
        examples = "; ".join(f"{a} seed {s}: {k}" for a, s, k in missing[:_REFUSAL_EXAMPLES])
        keys = sorted({k for _, _, k in missing})
        raise ComparisonRefusedError(
            f"refusing the strategy comparison: {len(missing)} value(s) of the fixed measure "
            f"set (docs/FRISCO_PROTOCOL.md §8.3) are missing — measures {keys}; e.g. "
            f"{examples}. Runs written before the waiting measures existed (WP-105) or "
            "without a recorded collision count cannot be compared; re-run them."
        )

    def series(arm: str, key: str) -> dict[int, float]:
        out: dict[int, float] = {}
        for seed, record in arms[arm].items():
            v = _value(record, key)
            assert v is not None  # checked above
            out[int(seed)] = v
        return out

    order = [baseline, *[a for a in arms if a != baseline]]
    rows: list[ArmRow] = []
    for arm in order:
        marginal = {m.key: ci([series(arm, m.key)[s] for s in base_seeds]) for m in measures}
        paired = (
            {}
            if arm == baseline
            else {
                m.key: paired_delta(series(baseline, m.key), series(arm, m.key)) for m in measures
            }
        )
        counts = (
            series(arm, COLLISIONS_KEY) if any(m.key == COLLISIONS_KEY for m in measures) else {}
        )
        total = int(math.fsum(counts.values()))
        rows.append(ArmRow(arm, marginal, paired, total, total == 0))
    return ComparisonTable(
        baseline=baseline,
        seeds=tuple(base_seeds),
        measures=tuple(measures),
        rows=tuple(rows),
        underpowered=len(base_seeds) < MIN_REPLICATES,
    )


def _fmt(x: float) -> str:
    if not math.isfinite(x):
        return "—"
    a = abs(x)
    if a >= 1000:
        return f"{x:,.0f}"
    if a >= 100:
        return f"{x:.0f}"
    if a >= 10:
        return f"{x:.1f}"
    return f"{x:.2f}"


def _cell(c: CI) -> str:
    if c.n == 0:
        return "—"
    if c.n == 1 or not math.isfinite(c.lo95):
        return f"{_fmt(c.mean)} (n={c.n})"
    return f"{_fmt(c.mean)} [{_fmt(c.lo95)}, {_fmt(c.hi95)}]"


def _delta_cell(d: PairedDelta) -> str:
    if d.n == 0:
        return "—"
    sign = "+" if d.mean > 0 else ""
    text = f"{sign}{_fmt(d.mean)}"
    if d.n > 1 and math.isfinite(d.lo95):
        text += f" [{_fmt(d.lo95)}, {_fmt(d.hi95)}]"
    else:
        text += f" (n={d.n})"
    return text + (" *" if d.resolved else "")


def render_markdown(table: ComparisonTable) -> str:
    """The table as Markdown: arm means with intervals, then paired differences.

    Args:
        table: A built table.

    Returns:
        Markdown text (two tables and a note).
    """
    head = "| Measure | " + " | ".join(r.arm for r in table.rows) + " |"
    rule = "|---|" + "---|" * len(table.rows)
    lines = [
        f"Seeds: {len(table.seeds)} shared by every arm"
        + (" — UNDERPOWERED (a rehearsal, not a headline result)" if table.underpowered else "")
        + ".",
        "",
        "Means with 95 % intervals over seeds:",
        "",
        head,
        rule,
    ]
    for m in table.measures:
        cells = [_cell(r.marginal[m.key]) for r in table.rows]
        lines.append(f"| {m.label} [{m.unit}] | " + " | ".join(cells) + " |")
    others = [r for r in table.rows if r.arm != table.baseline]
    if others:
        lines += [
            "",
            f"Paired differences against {table.baseline} (same seeds), 95 % intervals; "
            "* marks an interval that excludes zero:",
            "",
            "| Measure | " + " | ".join(r.arm for r in others) + " |",
            "|---|" + "---|" * len(others),
        ]
        for m in table.measures:
            cells = [_delta_cell(r.paired[m.key]) for r in others]
            lines.append(f"| {m.label} [{m.unit}] | " + " | ".join(cells) + " |")
    lines += [
        "",
        "Travel time and delay include the time spent waiting to enter the road and "
        "held at ramp meters (docs/FRISCO_PROTOCOL.md §8.2). Fuel is a "
        f"{FUEL_MODEL_ESTIMATE}.",
    ]
    return "\n".join(lines) + "\n"
