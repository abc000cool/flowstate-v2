"""FHWA-style acceptance criteria (CLAUDE.md §7.1) as data, not prose.

Encodes the acceptance table as a :class:`CriteriaProfile` (default profile:
GEH < 5 for at least 85% of link-hour comparisons and RMSPE ≤ 15% per the
usage of the FHWA Traffic Analysis Toolbox Vol. III, FHWA-HOP-18-036, 2019,
and UK DMRB practice; emergent backward wave speed inside the empirical
14-22 km/h band from ``flowstate_core.constants``; ring-benchmark booleans;
at least 20 replicate seeds per CLAUDE.md §0.6) and evaluates supplied
measurements into honest pass/fail rows — no rounding games, missing inputs
are reported as not evaluated and failing.

Note:
    The FHWA volume describes GEH usage rather than prescribing one wording;
    the exact threshold text should be re-verified against FHWA-HOP-18-036
    when a state-DOT variant profile is added (CLAUDE.md §7.1).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from flowstate_core.constants import WAVE_SPEED_BAND_KMH


@dataclass(frozen=True)
class CriteriaProfile:
    """One named set of acceptance thresholds (CLAUDE.md §7.1 table).

    Attributes:
        name: Profile identifier (recorded in reports).
        geh_threshold: Per-comparison GEH acceptance bound (strict ``<``).
        geh_pass_fraction: Minimum fraction of link-hour comparisons that
            must satisfy the GEH bound (inclusive ``>=``).
        rmspe_max: Maximum segment-speed RMSPE as a fraction (inclusive).
        wave_speed_band_kmh: Acceptance band for the emergent backward
            wave-front speed magnitude [km/h], inclusive on both ends.
        min_seeds: Minimum replicate count for headline reporting.
        require_ring_emergence: Include the Sugiyama ring emergence check.
        require_ring_dampening: Include the Stern single-AV dampening check.
    """

    name: str = "fhwa_default"
    geh_threshold: float = 5.0
    geh_pass_fraction: float = 0.85
    rmspe_max: float = 0.15
    wave_speed_band_kmh: tuple[float, float] = field(default=WAVE_SPEED_BAND_KMH)
    min_seeds: int = 20
    require_ring_emergence: bool = True
    require_ring_dampening: bool = True


@dataclass(frozen=True)
class CriteriaResult:
    """One evaluated acceptance-criterion row.

    Attributes:
        name: Criterion identifier.
        value: Measured value, or ``None`` when the input was not supplied.
        threshold: Human-readable threshold description.
        passed: Honest pass/fail; always ``False`` when not evaluated.
        evaluated: Whether an input was available to evaluate.
        detail: Optional explanatory note.
    """

    name: str
    value: float | None
    threshold: str
    passed: bool
    evaluated: bool
    detail: str = ""


def _not_evaluated(name: str, threshold: str) -> CriteriaResult:
    return CriteriaResult(
        name=name,
        value=None,
        threshold=threshold,
        passed=False,
        evaluated=False,
        detail="not evaluated: input not supplied",
    )


def evaluate(
    profile: CriteriaProfile | None = None,
    *,
    geh_values: Sequence[float] | None = None,
    rmspe_value: float | None = None,
    wave_speed_kmh: float | None = None,
    ring_emergence: bool | None = None,
    ring_dampening: bool | None = None,
    n_seeds: int | None = None,
) -> list[CriteriaResult]:
    """Evaluate acceptance criteria against measured values.

    Every check in the profile yields exactly one row. A check whose input
    is ``None`` (or NaN) is reported with ``evaluated=False`` (respectively
    a failing NaN value) rather than silently dropped — an unevaluated
    criterion is never a pass (CLAUDE.md §0.1).

    Args:
        profile: Threshold profile; ``None`` uses the default FHWA-style
            :class:`CriteriaProfile`.
        geh_values: Per-link-hour GEH statistics (from
            :func:`validation.metrics.geh` on hourly flows).
        rmspe_value: Segment-speed RMSPE as a fraction.
        wave_speed_kmh: Emergent backward wave-front speed magnitude [km/h]
            (positive; e.g. ``Metrics.wave_speed_kmh``).
        ring_emergence: Whether the Sugiyama ring emergence benchmark passed.
        ring_dampening: Whether the single-AV dampening benchmark passed.
        n_seeds: Number of replicate seeds behind the reported metrics.

    Returns:
        One :class:`CriteriaResult` per profile check, in table order.
    """
    p = profile if profile is not None else CriteriaProfile()
    rows: list[CriteriaResult] = []

    geh_threshold_text = (
        f"GEH < {p.geh_threshold:g} for >= {p.geh_pass_fraction:.0%} of link-hour comparisons"
    )
    if geh_values is None:
        rows.append(_not_evaluated("link_flows_geh", geh_threshold_text))
    elif len(geh_values) == 0:
        rows.append(
            CriteriaResult(
                name="link_flows_geh",
                value=math.nan,
                threshold=geh_threshold_text,
                passed=False,
                evaluated=True,
                detail="no comparisons supplied",
            )
        )
    else:
        frac = sum(1 for g in geh_values if g < p.geh_threshold) / len(geh_values)
        rows.append(
            CriteriaResult(
                name="link_flows_geh",
                value=frac,
                threshold=geh_threshold_text,
                passed=frac >= p.geh_pass_fraction,
                evaluated=True,
                detail=f"fraction of comparisons with GEH < {p.geh_threshold:g}",
            )
        )

    rmspe_text = f"segment-speed RMSPE <= {p.rmspe_max:.0%}"
    if rmspe_value is None:
        rows.append(_not_evaluated("speeds_rmspe", rmspe_text))
    else:
        rows.append(
            CriteriaResult(
                name="speeds_rmspe",
                value=rmspe_value,
                threshold=rmspe_text,
                passed=bool(rmspe_value <= p.rmspe_max),
                evaluated=True,
            )
        )

    lo, hi = p.wave_speed_band_kmh
    wave_text = f"backward wave speed in [{lo:g}, {hi:g}] km/h (emergent, unseeded)"
    if wave_speed_kmh is None:
        rows.append(_not_evaluated("wave_speed", wave_text))
    else:
        in_band = bool(lo <= wave_speed_kmh <= hi)  # NaN compares False
        detail = "" if math.isfinite(wave_speed_kmh) else "no backward wave detected"
        rows.append(
            CriteriaResult(
                name="wave_speed",
                value=wave_speed_kmh,
                threshold=wave_text,
                passed=in_band,
                evaluated=True,
                detail=detail,
            )
        )

    if p.require_ring_emergence:
        text = "Sugiyama ring emergence benchmark reproduced"
        if ring_emergence is None:
            rows.append(_not_evaluated("ring_emergence", text))
        else:
            rows.append(
                CriteriaResult(
                    name="ring_emergence",
                    value=float(ring_emergence),
                    threshold=text,
                    passed=bool(ring_emergence),
                    evaluated=True,
                )
            )

    if p.require_ring_dampening:
        text = "Stern single-AV dampening benchmark reproduced"
        if ring_dampening is None:
            rows.append(_not_evaluated("ring_dampening", text))
        else:
            rows.append(
                CriteriaResult(
                    name="ring_dampening",
                    value=float(ring_dampening),
                    threshold=text,
                    passed=bool(ring_dampening),
                    evaluated=True,
                )
            )

    seeds_text = f"n_seeds >= {p.min_seeds}"
    if n_seeds is None:
        rows.append(_not_evaluated("n_seeds", seeds_text))
    else:
        rows.append(
            CriteriaResult(
                name="n_seeds",
                value=float(n_seeds),
                threshold=seeds_text,
                passed=n_seeds >= p.min_seeds,
                evaluated=True,
            )
        )
    return rows
