"""Auto-generated validation report (CLAUDE.md §7.4) — a product feature.

``generate_report`` renders a markdown calibration/validation report from a
directory of run results: provenance (config hash, seeds, package and SUMO
versions from run metadata), calibration artifacts used, the acceptance
criteria table (:mod:`validation.criteria`), metric tables with replicate
confidence intervals (:mod:`validation.metrics`), and speed-contour figures
rendered beside the report. Seeded-perturbation runs are labeled prominently
(CLAUDE.md §0.2). Macro-only run sets are refused (CLAUDE.md §5.6): the
screening tier cannot support validation claims.

Every number in the rendered report is a computed value passed into the
Jinja2 template (packaged at ``validation/templates/report.md.j2``); the
template body contains no free-text numerals (CLAUDE.md §7.4).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from validation.criteria import CriteriaProfile, CriteriaResult, evaluate
from validation.fields import speed_field
from validation.metrics import CI_LEVEL, MIN_REPLICATES, aggregate, compute_metrics

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_NAME = "report.md.j2"

#: meta.json tier values that mark macroscopic screening runs (contract §3).
MACRO_TIERS = frozenset({"macro", "screening"})


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


def _fmt(value: float | None, digits: int = 4) -> str:
    """Format one computed number for the template ('—' for missing)."""
    if value is None:
        return "—"
    if isinstance(value, float) and math.isnan(value):
        return "NaN"
    return f"{value:.{digits}g}"


def _render_contour(run: _RunInfo, out_dir: Path, index: int) -> tuple[str, str]:
    """Render one speed-contour PNG beside the report; return (path, caption)."""
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    traj = pd.read_parquet(run.path / "trajectories.parquet")
    field = speed_field(traj)
    seed = run.meta.get("seed", "unknown")
    seeded = bool(run.meta.get("seeded", False))
    name = f"speed_contour_{index:02d}_seed_{seed}.png"
    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    mesh = ax.pcolormesh(field.x_edges, field.t_edges, field.mean_speed, shading="flat")
    fig.colorbar(mesh, ax=ax, label="mean speed [m/s]")
    ax.set_xlabel("position x [m]")
    ax.set_ylabel("time t [s]")
    title = f"Speed field — seed {seed}"
    if seeded:
        title += " (seeded=True)"
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out_dir / name, dpi=150)
    plt.close(fig)
    caption = f"Space-time mean-speed contour, seed {seed}" + (
        ", seeded perturbation" if seeded else ""
    )
    return name, caption


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
) -> Path:
    """Generate a markdown validation report for a set of runs.

    Discovers every run directory (containing ``meta.json``) under
    ``run_set_dir``, computes and aggregates metrics for the microscopic
    runs, evaluates the acceptance criteria, renders speed-contour figures
    beside the report, and writes the markdown report. See the module
    docstring for content guarantees.

    Args:
        run_set_dir: Root directory holding run directories (contract §3
            layout ``runs/<config_hash>/<seed>/``).
        out_path: Destination of the markdown report; figures are written
            into its parent directory.
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

    Returns:
        Path to the written markdown report.

    Raises:
        ReportRefusedError: If every discovered run is macroscopic
            (``tier`` in ``{"macro", "screening"}``) — CLAUDE.md §5.6.
        ValueError: If no runs are found under ``run_set_dir``.
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

    metrics_list = [compute_metrics(r.path, x_ref=x_ref, span=span) for r in micro_runs]
    agg = aggregate(metrics_list)

    wave_ci = agg["wave_speed_kmh"]
    criteria_results = evaluate(
        profile,
        geh_values=geh_values,
        rmspe_value=rmspe_value,
        wave_speed_kmh=wave_ci.mean,
        ring_emergence=ring_emergence,
        ring_dampening=ring_dampening,
        n_seeds=len(micro_runs),
    )

    figures = []
    for i, r in enumerate(micro_runs):
        fig_name, caption = _render_contour(r, out.parent, i)
        figures.append({"path": fig_name, "caption": caption})

    seeded_any = any(bool(r.meta.get("seeded", False)) for r in runs)
    run_rows = [
        {
            "name": str(r.path.relative_to(run_set)),
            "config_hash": str(r.meta.get("config_hash", "unknown")),
            "seed": str(r.meta.get("seed", "unknown")),
            "tier": str(r.meta.get("tier", "unknown")),
            "seeded": "seeded=True" if r.meta.get("seeded", False) else "seeded=False",
            "wall_time_s": _fmt(r.meta.get("wall_time_s")),
        }
        for r in runs
    ]
    versions_raw = micro_runs[0].meta.get("versions", {})
    versions = sorted(versions_raw.items()) if isinstance(versions_raw, dict) else []

    calibrations: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for r in runs:
        for entry in r.meta.get("calibration_artifacts", []) or []:
            if isinstance(entry, dict):
                key = (str(entry.get("path", "unknown")), str(entry.get("data_hash", "unknown")))
                if key not in seen:
                    seen.add(key)
                    calibrations.append({"path": key[0], "data_hash": key[1]})

    metric_rows = [
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

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
    )
    p = profile if profile is not None else CriteriaProfile()
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
        seeds_joined=", ".join(str(r.meta.get("seed", "unknown")) for r in micro_runs),
        runs=run_rows,
        versions=versions,
        calibrations=calibrations,
        criteria=_criteria_rows(criteria_results),
        ci_level_pct=_fmt(CI_LEVEL * 100.0, 3),
        min_replicates=str(MIN_REPLICATES),
        metric_rows=metric_rows,
        figures=figures,
    )
    out.write_text(rendered)
    return out
