"""Fit one demand scale factor for the I-24 replica on observed speeds.

Why a third demand arm. The instrument tracks about half of vehicle-time at
the peak, so the tracked counts are a lower bound on demand (arm 1) and the
coverage-corrected counts (arm 2) are an estimate whose peak, 1,840 veh/h
per lane at the entry, sits at the fitted fundamental diagram's capacity
lower bound: the replica cannot insert it and queues from the first window
(docs/I24_VALIDATION.md §3, docs/I24_DATA.md). Speeds, unlike counts, are
robust to coverage. This script therefore calibrates a single scale factor
``s`` applied to the tracked mainline and on-ramp inflows (exit fractions
and the measured boundary unchanged) by minimising the segment-speed RMSPE
over the FIRST HOUR of the study period (06:30–07:30 CST, windows 0–11), and
reports the SECOND hour (07:30–08:30, windows 12–23) as an out-of-sample
check. Standard demand calibration (FHWA Vol. III), done out-of-sample so a
pass on the second hour is not a fit.

Method: two rounds of a parallel grid (coarse, then refined around the best),
one seeded replicate per scale (the first replicate seed of the scenario),
ties broken toward the smaller scale. Nothing else is tuned.

**Insertion constraint and flow objective (opt-in, 2026-10-07).** The speed
objective cannot see vehicles held off the network: under stronger drivers the
refits chose scales that left 7–8 % of the planned vehicles uninserted
(docs/FRISCO_PROTOCOL.md, Result of Amendment 2). ``--min-inserted F`` chooses
the best-objective scale among those whose inserted fraction (departed /
planned, the fit's single seed) is at least ``F``; when none is, it chooses the
scale with the highest inserted fraction and flags ``constraint_unmet`` in the
artifact, the scenario header and the console. The refine round is centred on
the constrained coarse choice. ``--objective geh`` selects on link flows
instead of speeds (CLAUDE.md §6.3): the most (section, 5-min window) bins of
the fit hour under GEH 5 against the criterion row's observed table
(``hourly_flows_veh_h_recommended`` of ``artifacts/i24_validation_observed.json``),
ties to the smaller mean GEH, then the smaller scale. With either option every
scale's GEH reading is reported beside its RMSPE, and the artifact gains a
``selection`` block (the rule, every scale's inserted fraction and readings,
and the reason for the choice); without them the fit and its artifact are
exactly as before.

``--analyze-artifact PATH`` (repeatable) re-reads a saved fit's per-scale
numbers and reports what the rule would have chosen for each ``--min-inserted``
given, without simulating. It is an analysis of recorded runs, not a
calibration: a constrained fit centres its refine round elsewhere, and the
scales it would have run that the saved grid lacks are listed
(``procedure.refine_missing``).

Outputs ``artifacts/demand_scale_i24.json`` and, with ``--write-scenario``,
``scenarios/i24_replica_speedcal.yaml``.

Run: ``uv run --no-sync python scripts/i24_fit_demand_scale.py --procs 7 --write-scenario``

Re-analysis: ``uv run --no-sync python scripts/i24_fit_demand_scale.py
--analyze-artifact artifacts/demand_scale_i24_flow_dc.json --min-inserted 0.97
--min-inserted 0.98``
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from i24_validate import (
    SECTIONS_M,
    WINDOW_S,
    _inputs,
    _segment_speeds,
    _sim_frame,
    _span,
    crossings_per_window,
    section_lane_crossings,
)

from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from microsim.runner import _versions, run_micro
from validation.metrics import geh, rmspe

REPO = Path(__file__).resolve().parents[1]
TRACKED_YAML = REPO / "scenarios" / "i24_replica.yaml"
CORRECTED_YAML = REPO / "scenarios" / "i24_replica_corrected.yaml"
BASES = {"tracked": TRACKED_YAML, "corrected": CORRECTED_YAML}
OBSERVED = REPO / "artifacts" / "i24_validation_observed.json"
OUT = REPO / "artifacts" / "demand_scale_i24.json"
SCENARIO_OUT = REPO / "scenarios" / "i24_replica_speedcal.yaml"
FLEET_ARTIFACT = "artifacts/idm_i24_capacity.json"  # FHWA step 1 output (docs/I24_CAPACITY.md)

COARSE = {
    "tracked": (1.0, 1.15, 1.3, 1.45, 1.6, 1.75, 1.9),
    # coverage-shaped profile (per-window 1/coverage) with a fitted level
    "corrected": (0.6, 0.7, 0.8, 0.9, 1.0, 1.1),
}
REFINE_STEP = 0.025
REFINE_HALF_WIDTH = 3  # ± 3 steps around the coarse optimum
TRAIN_WINDOWS = range(0, 12)
TEST_WINDOWS = range(12, 24)

OBJECTIVES = ("speed_rmspe", "geh")
OBJECTIVE_TEXT = {
    # the speed text is the one every fit artifact has carried
    "speed_rmspe": "segment-speed RMSPE, windows 0-11 (06:30-07:30 CST); windows 12-23 held out",
    "geh": (
        "link-flow GEH, windows 0-11 (06:30-07:30 CST); windows 12-23 held out: the most "
        "(section, 5-min window) bins under GEH 5, ties to the smaller mean GEH, then the smaller "
        "scale; hourly flows are the run's 5-min crossings x 12 at the six sections, against the "
        "observed crossings divided by the recommended coverage (the criterion row's table, "
        "artifacts/i24_validation_observed.json hourly_flows_veh_h_recommended)"
    ),
}
GEH_OBSERVED_KEY = "hourly_flows_veh_h_recommended"
INSERTED_DEFINITION = (
    "n_vehicles_departed / n_vehicles_planned of the fit's single seed (the battery's "
    "demand_realized_fraction is the same ratio averaged over its replicates)"
)
ANALYSIS_LABEL = (
    "re-analysis of a saved fit's recorded per-scale numbers; no simulation was run; not a "
    "calibration and not an adopted scale"
)


SCENARIO_NAME = "i24_replica_speedcal"


def scaled_config(
    scale: float,
    fleet_artifact: str = FLEET_ARTIFACT,
    base: str = "tracked",
    base_yaml: Path | None = None,
    name: str = SCENARIO_NAME,
) -> dict[str, Any]:
    """The base scenario dict with mainline and on-ramp inflows × scale and the step-1 fleet."""
    raw = yaml.safe_load((base_yaml or BASES[base]).read_text())
    raw["fleet"]["idm_calibration"] = fleet_artifact
    net = raw["network"]
    net["inflow"] = [[t, round(q * scale, 6)] for t, q in net["inflow"]]
    for ramp in net.get("ramps", []):
        if ramp.get("kind") == "on" and ramp.get("inflow"):
            ramp["inflow"] = [[t, round(q * scale, 6)] for t, q in ramp["inflow"]]
    raw["name"] = name
    return raw


def _rmspe_windows(sim: np.ndarray, obs: np.ndarray, windows: range) -> float:
    s = sim[list(windows)].ravel()
    o = obs[list(windows)].ravel()
    ok = np.isfinite(s) & np.isfinite(o)
    return float(rmspe(s[ok], o[ok]))


def _job(args: tuple[Any, ...]) -> dict[str, Any]:
    """One fit run: simulate ``scaled_config`` on one seed and read it as the battery reads a replicate.

    ``args`` is ``(scale, seed, fleet_artifact, base, base_yaml, name)``; a seventh item
    ``True`` (scripts/fit_demand_level.py ``--section-lanes observed``) adds the run's
    ``lane_crossings`` (``i24_validate.section_lane_crossings``, the battery's per-lane
    reader) last. Without it the run's record is exactly as before.
    """
    scale, seed, fleet_artifact, base, base_yaml_s, name = args[:6]
    with_lanes = len(args) > 6 and bool(args[6])
    base_yaml = Path(base_yaml_s) if base_yaml_s else None
    geo = _inputs()["geometry"]
    a, b = geo["sim_x_of_data_x"]["a"], geo["sim_x_of_data_x"]["b"]
    _span_lo, span_hi = _span()
    obs = np.array(json.loads(OBSERVED.read_text())["segment_speeds_ms"], dtype=float)
    n_win = obs.shape[0]
    cfg = ScenarioConfig.model_validate(scaled_config(scale, fleet_artifact, base, base_yaml, name))
    with tempfile.TemporaryDirectory() as td:
        t0 = time.perf_counter()
        paths = run_micro(cfg, seed, Path(td))
        meta = json.loads(paths.meta.read_text())
        df = _sim_frame(paths.run_dir, a, b)
        seg = _segment_speeds(df, span_hi, n_win)
        counts = [
            [int(c) for c in crossings_per_window(df, s, 0.0, n_win * WINDOW_S)] for s in SECTIONS_M
        ]
        lanes = (
            section_lane_crossings(df, SECTIONS_M, 0.0, n_win * WINDOW_S) if with_lanes else None
        )
        wall = time.perf_counter() - t0
    row = {
        "scale": scale,
        "seed": seed,
        "config_hash": meta["config_hash"],
        "inserted_fraction": round(meta["n_vehicles_departed"] / meta["n_vehicles_planned"], 4),
        "rmspe_train": round(_rmspe_windows(seg, obs, TRAIN_WINDOWS), 4),
        "rmspe_test": round(_rmspe_windows(seg, obs, TEST_WINDOWS), 4),
        "rmspe_all": round(_rmspe_windows(seg, obs, range(n_win)), 4),
        "segment_speeds_ms": np.round(seg, 3).tolist(),
        "counts_per_window": counts,
        "wall_s": round(wall, 1),
    }
    if with_lanes:
        row["lane_crossings"] = lanes
    return row


def _run_jobs(jobs: Sequence[tuple[Any, ...]], procs: int) -> list[dict]:
    """Run one grid round's simulations in a spawn pool, in job order."""
    ctx = mp.get_context("spawn")
    with ctx.Pool(min(procs, len(jobs))) as pool:
        return pool.map(_job, jobs)


def _json_default(o: Any) -> Any:
    """numpy scalars/arrays → plain Python for json.dumps."""
    if hasattr(o, "tolist"):
        return o.tolist()
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def _best(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return min(rows, key=lambda r: (r["rmspe_train"], r["scale"]))


def refine_scales(centre: float) -> list[float]:
    """The refine round's scales around ``centre`` (± REFINE_HALF_WIDTH steps, positive only)."""
    return [
        round(centre + k * REFINE_STEP, 3)
        for k in range(-REFINE_HALF_WIDTH, REFINE_HALF_WIDTH + 1)
        if k != 0 and centre + k * REFINE_STEP > 0.0
    ]


# --- the insertion constraint and the flow objective ----------------------------------------


def observed_hourly_flows(path: Path = OBSERVED) -> np.ndarray:
    """The criterion row's observed hourly flows, (section, 5-min window) [veh/h]."""
    return np.asarray(json.loads(path.read_text())[GEH_OBSERVED_KEY], dtype=float)


def geh_reading(
    counts_per_window: Sequence[Sequence[float]], obs_hourly: np.ndarray, windows: Sequence[int]
) -> dict[str, Any]:
    """GEH of one run's link flows against an observed table over ``windows``.

    Each 5-min crossing count (``counts_per_window[section][window]``, as
    :func:`_job` records it) is scaled ×12 to an hourly flow and compared bin
    by bin with ``obs_hourly`` (veh/h, same shape), as scripts/i24_validate.py
    scores a battery's replicate mean. Non-finite bins are skipped.

    Returns:
        ``n_bins``, ``n_under_5`` (bins with GEH < 5), ``under_5`` (their share)
        and ``mean`` (mean GEH); the last two are None when no bin is finite.
    """
    sim = np.asarray(counts_per_window, dtype=float) * (3600.0 / WINDOW_S)
    obs = np.asarray(obs_hourly, dtype=float)
    if sim.shape != obs.shape:
        raise ValueError(f"counts shape {sim.shape} != observed shape {obs.shape}")
    cols = list(windows)
    s, o = sim[:, cols].ravel(), obs[:, cols].ravel()
    ok = np.isfinite(s) & np.isfinite(o)
    vals = [geh(float(m), float(c)) for m, c in zip(s[ok], o[ok], strict=True)]
    n_under = sum(1 for v in vals if v < 5.0)
    return {
        "n_bins": len(vals),
        "n_under_5": n_under,
        "under_5": round(n_under / len(vals), 4) if vals else None,
        "mean": round(float(np.mean(vals)), 3) if vals else None,
    }


def add_geh(rows: list[dict[str, Any]], obs_hourly: np.ndarray) -> None:
    """Attach ``geh`` readings (train / test / all windows) to every row lacking one."""
    n_win = np.asarray(obs_hourly).shape[1]
    splits = {"train": TRAIN_WINDOWS, "test": TEST_WINDOWS, "all": range(n_win)}
    for r in rows:
        if "geh" not in r:
            r["geh"] = {
                k: geh_reading(r["counts_per_window"], obs_hourly, w) for k, w in splits.items()
            }


def objective_key(row: dict[str, Any], objective: str) -> tuple[float, ...]:
    """Sort key of a grid row under ``objective`` (smaller is better; ends with the scale)."""
    if objective == "speed_rmspe":
        return (row["rmspe_train"], row["scale"])
    if objective == "geh":
        g = row.get("geh", {}).get("train")
        if g is None or g["mean"] is None:
            raise ValueError(f"scale {row['scale']}: no GEH reading for the fit windows")
        return (-g["n_under_5"], g["mean"], row["scale"])
    raise ValueError(f"unknown objective {objective!r}; choose from {OBJECTIVES}")


def _check_min_inserted(min_inserted: float | None) -> None:
    if min_inserted is not None and not 0.0 < min_inserted <= 1.0:
        raise ValueError(f"min_inserted must be in (0, 1], got {min_inserted}")


def scale_summary(row: dict[str, Any], min_inserted: float | None = None) -> dict[str, Any]:
    """One scale's readings as a selection records them."""
    out: dict[str, Any] = {
        "scale": row["scale"],
        "inserted_fraction": row["inserted_fraction"],
        "meets_min_inserted": None
        if min_inserted is None
        else bool(row["inserted_fraction"] >= min_inserted),
        "rmspe_train": row["rmspe_train"],
        "rmspe_test": row["rmspe_test"],
    }
    if "geh" in row:
        out["geh_under_5_train"] = row["geh"]["train"]["under_5"]
        out["geh_under_5_test"] = row["geh"]["test"]["under_5"]
        out["geh_mean_train"] = row["geh"]["train"]["mean"]
    return out


def choose(
    rows: list[dict[str, Any]],
    objective: str = "speed_rmspe",
    min_inserted: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The chosen grid row and the record of why.

    Without ``min_inserted`` this is the fitter's historical rule (for the
    speed objective, :func:`_best`). With it, the best-objective row among
    those whose ``inserted_fraction`` is at least ``min_inserted``; if none
    is, the row with the highest inserted fraction (ties: better objective,
    then smaller scale), flagged ``constraint_unmet``.
    """
    if not rows:
        raise ValueError("no grid rows to choose from")
    _check_min_inserted(min_inserted)
    objective_key(rows[0], objective)  # fails early on an unknown or unreadable objective

    def key(r: dict[str, Any]) -> tuple[float, ...]:
        return objective_key(r, objective)

    n = len(rows)
    unconstrained = min(rows, key=key)
    qualifying = (
        list(rows)
        if min_inserted is None
        else [r for r in rows if r["inserted_fraction"] >= min_inserted]
    )
    unmet = False
    if min_inserted is None:
        chosen = unconstrained
        reason = f"best {objective} over all {n} scales (no insertion constraint)"
    elif qualifying:
        chosen = min(qualifying, key=key)
        reason = (
            f"best {objective} among the {len(qualifying)} of {n} scales whose inserted fraction "
            f"is >= {min_inserted:g}"
        )
        if chosen is unconstrained:
            reason += "; the unconstrained best qualifies"
        else:
            reason += (
                f"; the unconstrained best, s = {unconstrained['scale']:.3f}, inserts "
                f"{unconstrained['inserted_fraction']:.4f}"
            )
    else:
        chosen = min(rows, key=lambda r: (-r["inserted_fraction"], *key(r)))
        unmet = True
        reason = (
            f"CONSTRAINT UNMET: no scale inserts >= {min_inserted:g} (highest "
            f"{chosen['inserted_fraction']:.4f}, s = {chosen['scale']:.3f}); chose the scale with "
            "the highest inserted fraction"
        )
    selection = {
        "objective": objective,
        "objective_definition": OBJECTIVE_TEXT[objective],
        "min_inserted": min_inserted,
        "inserted_fraction_definition": INSERTED_DEFINITION,
        "constraint_unmet": unmet,
        "chosen_scale": chosen["scale"],
        "unconstrained_best_scale": unconstrained["scale"],
        "qualifying_scales": None
        if min_inserted is None
        else sorted(r["scale"] for r in qualifying),
        "reason": reason,
        "per_scale": [
            scale_summary(r, min_inserted) for r in sorted(rows, key=lambda r: r["scale"])
        ],
    }
    return chosen, selection


def analyze_fit(
    art: dict[str, Any],
    thresholds: Sequence[float | None],
    objective: str = "speed_rmspe",
    obs_hourly: np.ndarray | None = None,
) -> dict[str, Any]:
    """What ``choose`` picks on a saved fit's recorded rows, per threshold (no simulation).

    Two readings per threshold. ``chosen``: the rule over every recorded row.
    ``procedure``: the fit a constrained run would have made — the coarse
    round (identical runs: same seed, same configurations), the rule there,
    the refine scales around that choice, and the rule over the coarse plus
    the refine rows the saved grid holds; ``refine_missing`` lists the refine
    scales it lacks (their readings are unknown, so ``complete`` is False and
    the reading is a lower bound on what such a run would have explored).
    ``recorded_choice_reproduced`` checks the machinery: the unconstrained
    rule on the same rows must return the saved ``best``.

    ``obs_hourly``: the observed hourly flows for the GEH readings (required
    for ``objective="geh"``; rows must carry ``counts_per_window``).
    """
    rows = [dict(r) for r in art["grid"]]
    if obs_hourly is not None and all("counts_per_window" in r for r in rows):
        add_geh(rows, obs_hourly)
    elif objective == "geh":
        raise ValueError("the GEH objective needs observed flows and per-row counts_per_window")
    by_scale = {round(float(r["scale"]), 3): r for r in rows}
    recorded = by_scale[round(float(art["best"]["scale"]), 3)]
    legacy_rule, _ = choose(rows, "speed_rmspe", None)
    coarse_scales = [round(s, 3) for s in COARSE.get(art.get("base", ""), ())]
    coarse = [by_scale[s] for s in coarse_scales if s in by_scale]
    coarse_complete = bool(coarse_scales) and len(coarse) == len(coarse_scales)
    out: dict[str, Any] = {
        "label": ANALYSIS_LABEL,
        "base_scenario": art.get("base_scenario"),
        "fleet_artifact": art.get("fleet_artifact"),
        "seed": art.get("seed"),
        "source_objective": art.get("objective"),
        "objective": objective,
        "recorded_choice": scale_summary(recorded),
        "recorded_choice_reproduced": legacy_rule["scale"] == recorded["scale"],
        "per_scale": [scale_summary(r) for r in sorted(rows, key=lambda r: r["scale"])],
        "by_threshold": [],
    }
    for f in thresholds:
        chosen, sel = choose(rows, objective, f)
        procedure: dict[str, Any] | None = None
        if coarse_complete:
            c_choice, _ = choose(coarse, objective, f)
            refine = refine_scales(c_choice["scale"])
            present = [s for s in refine if s in by_scale]
            missing = [s for s in refine if s not in by_scale]
            p_choice, p_sel = choose(coarse + [by_scale[s] for s in present], objective, f)
            procedure = {
                "coarse_choice_scale": c_choice["scale"],
                "refine_scales": refine,
                "refine_missing": missing,
                "complete": not missing,
                "chosen_scale": p_choice["scale"],
                "constraint_unmet": p_sel["constraint_unmet"],
            }
        out["by_threshold"].append(
            {
                "min_inserted": f,
                "chosen": scale_summary(chosen, f),
                "constraint_unmet": sel["constraint_unmet"],
                "differs_from_recorded": chosen["scale"] != recorded["scale"],
                "qualifying_scales": sel["qualifying_scales"],
                "reason": sel["reason"],
                "procedure": procedure,
            }
        )
    return out


def _fmt(v: float | None, spec: str) -> str:
    return "—" if v is None else format(v, spec)


def _print_analysis(path: Path, res: dict[str, Any]) -> None:
    rc = res["recorded_choice"]
    print(f"{_rel(path)} — {res['label']}")
    print(
        f"  recorded: s={rc['scale']:.3f} inserted={rc['inserted_fraction']:.4f} "
        f"rmspe train={rc['rmspe_train']:.3f} test={rc['rmspe_test']:.3f} "
        f"geh<5 train={_fmt(rc.get('geh_under_5_train'), '.3f')} "
        f"(reproduced by the unconstrained rule: {res['recorded_choice_reproduced']})"
    )
    for b in res["by_threshold"]:
        c = b["chosen"]
        p = b["procedure"]
        proc = (
            "procedure: n/a (coarse grid not recorded)"
            if p is None
            else f"procedure: coarse {p['coarse_choice_scale']:.3f} -> {p['chosen_scale']:.3f}"
            + ("" if p["complete"] else f", refine scales not run: {p['refine_missing']}")
        )
        print(
            f"  min_inserted={_fmt(b['min_inserted'], 'g')}: s={c['scale']:.3f} "
            f"inserted={c['inserted_fraction']:.4f} rmspe train={c['rmspe_train']:.3f} "
            f"test={c['rmspe_test']:.3f} geh<5 train={_fmt(c.get('geh_under_5_train'), '.3f')} "
            f"test={_fmt(c.get('geh_under_5_test'), '.3f')}"
            f"{' CONSTRAINT UNMET' if b['constraint_unmet'] else ''} | {proc}"
        )


def _min_inserted_arg(text: str) -> float:
    f = float(text)
    if not 0.0 < f <= 1.0:
        raise argparse.ArgumentTypeError(f"must be in (0, 1], got {text}")
    return f


def analyze_main(args: argparse.Namespace) -> list[dict[str, Any]]:
    """``--analyze-artifact``: print (and with ``--analysis-out`` write) the re-analysis."""
    thresholds: list[float | None] = list(args.min_inserted or [None])
    objective = args.objective or "speed_rmspe"
    obs_hourly = observed_hourly_flows() if OBSERVED.is_file() else None
    results = []
    for path in args.analyze_artifact:
        res = analyze_fit(json.loads(path.read_text()), thresholds, objective, obs_hourly)
        res["artifact"] = _rel(path)
        _print_analysis(path, res)
        results.append(res)
    if args.analysis_out is not None:
        args.analysis_out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "label": ANALYSIS_LABEL,
            "observed_flows": f"{_rel(OBSERVED)} {GEH_OBSERVED_KEY}"
            if obs_hourly is not None
            else None,
            "analyses": results,
        }
        args.analysis_out.write_text(json.dumps(payload, indent=1, default=_json_default))
        print(f"-> {args.analysis_out}")
    return results


def _print_rows(rows: list[dict[str, Any]], selecting: bool) -> None:
    for r in sorted(rows, key=lambda r: r["scale"]):
        line = f"  s={r['scale']:.3f} inserted={r['inserted_fraction']:.3f} rmspe train={r['rmspe_train']:.3f} test={r['rmspe_test']:.3f}"
        if selecting:
            g = r["geh"]
            line += f" geh<5 train={_fmt(g['train']['under_5'], '.3f')} test={_fmt(g['test']['under_5'], '.3f')}"
        print(line)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--write-scenario", action="store_true")
    ap.add_argument("--coarse-only", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--fleet-artifact", default=FLEET_ARTIFACT)
    ap.add_argument("--base", choices=tuple(BASES), default="tracked")
    ap.add_argument(
        "--base-yaml", type=Path, default=None, help="scenario file to scale (overrides --base)"
    )
    ap.add_argument("--scenario-out", type=Path, default=SCENARIO_OUT)
    ap.add_argument("--name", default=SCENARIO_NAME, help="name of the written scenario")
    ap.add_argument(
        "--from-artifact",
        type=Path,
        default=None,
        help="write --scenario-out from this saved fit artifact (its best scale, fleet and "
        "base) without simulating; the scenario is a deterministic function of the artifact",
    )
    ap.add_argument(
        "--min-inserted",
        type=_min_inserted_arg,
        action="append",
        default=None,
        metavar="F",
        help="insertion constraint: choose the best-objective scale among those whose inserted "
        "fraction (departed / planned, the fit's seed) is >= F; if none is, the scale with the "
        "highest inserted fraction, flagged constraint_unmet. Default: none (the historical rule). "
        "Once for a fit; repeatable with --analyze-artifact",
    )
    ap.add_argument(
        "--objective",
        choices=OBJECTIVES,
        default=None,
        help="selection objective (default speed_rmspe). geh: most fit-hour (section, window) "
        "bins under GEH 5 against the criterion row's observed flows. Passing --objective or "
        "--min-inserted also reports every scale's GEH and writes the artifact's selection block",
    )
    ap.add_argument(
        "--analyze-artifact",
        type=Path,
        action="append",
        default=None,
        metavar="PATH",
        help="re-read a saved fit artifact's per-scale numbers and report what the rule would "
        "choose for each --min-inserted, without simulating (repeatable)",
    )
    ap.add_argument(
        "--analysis-out", type=Path, default=None, help="write the --analyze-artifact JSON here"
    )
    args = ap.parse_args(argv)
    if args.analyze_artifact:
        analyze_main(args)
        return
    if args.analysis_out is not None:
        ap.error("--analysis-out needs --analyze-artifact")
    if args.min_inserted is not None and len(args.min_inserted) > 1:
        ap.error("a fit takes one --min-inserted")
    min_inserted = args.min_inserted[0] if args.min_inserted else None
    selecting = min_inserted is not None or args.objective is not None
    objective = args.objective or "speed_rmspe"
    fleet = args.fleet_artifact
    base_name = args.base
    base_yaml = args.base_yaml.resolve() if args.base_yaml is not None else None
    if args.from_artifact is not None:
        art = json.loads(args.from_artifact.read_text())
        base_name = art["base"]
        base_yaml = (REPO / art["base_scenario"]).resolve() if art.get("base_scenario") else None
        write_scenario(
            art["best"], art["fleet_artifact"], base_name, base_yaml, args, art.get("selection")
        )
        return
    if args.out == OUT and base_name != "tracked":
        args.out = OUT.with_name(f"demand_scale_i24_{base_name}.json")
    base = ScenarioConfig.model_validate(scaled_config(1.0, fleet, base_name, base_yaml, args.name))
    seed = spawn_seeds(base.seed, base.replicates)[0]
    obs_hourly = observed_hourly_flows() if selecting else None

    def pick(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not selecting:
            return _best(rows)
        assert obs_hourly is not None
        add_geh(rows, obs_hourly)
        return choose(rows, objective, min_inserted)[0]

    def jobs(scales: Sequence[float]) -> list[tuple[float, int, str, str, str | None, str]]:
        return [
            (s, seed, fleet, base_name, str(base_yaml) if base_yaml else None, args.name)
            for s in scales
        ]

    rows: list[dict[str, Any]] = []
    rows += _run_jobs(jobs(COARSE[base_name]), args.procs)
    best = pick(rows)
    _print_rows(rows, selecting)
    coarse_choice = best["scale"]
    if not args.coarse_only:
        fine = refine_scales(best["scale"])
        rows += _run_jobs(jobs(fine), args.procs)
        best = pick(rows)
        _print_rows(rows, selecting)
    print(
        f"best scale {best['scale']:.3f}: rmspe train {best['rmspe_train']:.3f}, test {best['rmspe_test']:.3f}, inserted {best['inserted_fraction']:.3f}"
    )
    selection: dict[str, Any] | None = None
    if selecting:
        best, selection = choose(rows, objective, min_inserted)
        selection["coarse_choice_scale"] = coarse_choice
        selection["refine_centre_scale"] = None if args.coarse_only else coarse_choice
        print(f"selection: {selection['reason']}")
        if selection["constraint_unmet"]:
            print(
                f"WARNING: insertion constraint unmet (>= {min_inserted:g}); the chosen scale "
                "holds vehicles off the network",
                file=sys.stderr,
            )
    result = {
        "schema_version": 1,
        "versions": _versions(),
        "base": base_name,
        "base_scenario": str((base_yaml or BASES[base_name]).relative_to(REPO))
        if (base_yaml or BASES[base_name]).is_relative_to(REPO)
        else str(base_yaml or BASES[base_name]),
        "fleet_artifact": fleet,
        "objective": OBJECTIVE_TEXT[objective],
        "seed": seed,
        "grid": sorted(rows, key=lambda r: r["scale"]),
        "best": {
            k: best[k]
            for k in (
                "scale",
                "config_hash",
                "inserted_fraction",
                "rmspe_train",
                "rmspe_test",
                "rmspe_all",
            )
        },
    }
    if selection is not None:
        result["best"]["geh"] = best["geh"]
        result["best"]["constraint_unmet"] = selection["constraint_unmet"]
        result["selection"] = selection
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1, default=_json_default))
    print(f"-> {args.out}")
    if args.write_scenario:
        write_scenario(best, fleet, base_name, base_yaml, args, selection)


def write_scenario(
    best: dict[str, Any],
    fleet: str,
    base_name: str,
    base_yaml: Path | None,
    args: argparse.Namespace,
    selection: dict[str, Any] | None = None,
) -> None:
    """Write the scaled scenario for ``best`` (``--scenario-out``, ``--name``).

    ``selection`` (a fit's ``selection`` block, present when the fit ran with
    ``--min-inserted`` or ``--objective``) names the objective and adds the
    reason for the choice to the header; without it the header is the
    historical one.
    """
    raw = scaled_config(best["scale"], fleet, base_name, base_yaml, args.name)
    cfg = ScenarioConfig.model_validate(raw)
    on_what = "observed segment speeds"
    if selection is not None and selection.get("objective") == "geh":
        on_what = "observed link flows (GEH)"
    header = (
        f"# {args.name} — the {base_name}-demand replica with mainline and on-ramp\n"
        f"# inflows multiplied by s = {best['scale']:.3f}, fitted by scripts/i24_fit_demand_scale.py\n"
        f"# on {on_what} over 06:30-07:30 CST only (windows 0-11); 07:30-08:30\n"
        f"# is held out. Fleet: {fleet} (capacity-calibrated, docs/I24_CAPACITY.md).\n"
        "# Exit fractions and the measured boundary are unchanged. See\n"
        f"# {_rel(args.from_artifact or args.out)} (rmspe train {best['rmspe_train']:.3f}, "
        f"test {best['rmspe_test']:.3f}).\n"
    )
    if selection is not None:
        header += f"# Selection: {selection['reason']}.\n"
    header += f"# config hash {config_hash(cfg)}; seeded=False.\n"
    args.scenario_out.write_text(header + yaml.safe_dump(raw, sort_keys=False))
    print(f"-> {args.scenario_out} ({config_hash(cfg)})")


def _rel(path: Path) -> str:
    path = Path(path).resolve()
    return str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)


if __name__ == "__main__":
    main()
