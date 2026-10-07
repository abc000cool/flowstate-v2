"""Generic controller × strategy sweep for any corridor scenario, with a paired analysis.

Cells are the product of ``--penetration`` × ``--compliance`` × ``--controllers``
× ``--strategies`` plus the ``baseline`` cell (no AVs, no strategy) and one
infrastructure-only cell per strategy (no AVs). Every cell runs the same seed
list, so per-seed deltas against the baseline are paired. Each run stores
``metrics.json`` (``validation.metrics.compute_metrics`` on the analysed span,
warm-up discarded per the run's config) and drops its trajectories unless
``--keep-trajectories``. The summary (``--summary``) carries per-cell marginal
95 % t-CIs, paired deltas versus baseline with 95 % CIs, every config hash and
the metric arguments — the only source for numbers quoted in documents. When a
run directory also holds its ``meta.json`` (the pipeline archives it beside
``metrics.json``), each cell carries a ``diagnostics`` block: per ramp meter
(keyed by ramp) the seed mean, 95 % t-interval and n of ``n_released``,
``n_passed_unstoppable`` and the share ``n_passed_unstoppable / (n_released +
n_passed_unstoppable)``; per weaving section (keyed by on-ramp) the same for
its counters and mean wait. Runs without ``meta.json`` contribute nothing and
``n_runs_with_meta`` says how many did.

Zero collisions is a pass/fail requirement of every run set (2026-10-04,
owner decision, WP-98). Each cell carries ``collisions`` (:func:`collision_block`:
the total over the runs whose ``meta.json`` records ``n_collisions``, which
runs had any, which do not record it) and ``zero_collisions`` (true only when
every run of the cell records zero, false on any collision, null when not
recorded — a run without ``meta.json`` or without the counter); the summary
carries the same flag over every run of every complete cell.

Waiting counts (WP-105, docs/FRISCO_PROTOCOL.md §8.2): each run's
``metrics.json`` also carries ``validation.metrics.WaitingMetrics`` — travel
time and total delay from each vehicle's PLANNED departure, so time in the
insertion backlog and behind ramp meters counts — computed from the run's
``journeys.parquet`` (kept beside ``meta.json``), and :data:`FIELDS` carries
them into every cell's intervals and paired deltas. ``--comparison PATH``
writes the protocol §8.3 strategy comparison table
(``validation.strategy_compare``: the fixed measure set, paired by seed against
the baseline cell); it refuses — the sweep exits non-zero with the reason —
when any run of any cell lacks any measure of the set (a tree written before
WP-105, a run without a collision count).

Strategies:
  ``none``    the scenario as calibrated;
  ``vsl``     ``av.vsl = "vsl_threshold"`` (threshold ladder, per-edge gantries);
  ``alinea``  every on-ramp metered by ALINEA with ``--rho-target-veh-km``
              (per-lane critical density, e.g. from the corridor's FD artifact);
  ``vsl+alinea`` both.

Cool-down (docs/FRISCO_PROTOCOL.md §8.2): ``--scored-end-s T`` scores a
scenario that runs on past its study period. Both measure sets stop at
simulation time ``T`` (throughput, σ_v, VMT/VHT and waves over the study
period; the waiting measures over the departures planned before ``T``, their
clocks running on to the run's end), and ``metrics_args`` in the manifest
records it. Without the option every run is scored to its end, as before.
The value is checked against the scenario's warm-up and length before
anything is simulated (:func:`scored_end_problem`).

Resumable: a run whose ``metrics.json`` exists under its cell/config-hash/seed
directory is skipped. Each run's ``metrics.json`` records the metric arguments
it was scored with (``metrics_args``: ``x_ref``, ``span``, ``scored_end_s``,
null = the run's end; 2026-10-07), and a resume whose arguments differ from
the stored ``MANIFEST.json``'s or from a stored run's is refused
(:func:`resume_conflict`): runs scored on two windows are never mixed. A run
stored before the record existed is "unknown", refused only when the launch
sets a scored end. ``--analyze-only`` rebuilds the summary from stored files.

Example::

    uv run --no-sync python scripts/corridor_sweep.py --scenario scenarios/X.yaml \\
        --penetration 0.05 0.10 0.20 --compliance 1.0 --controllers follower_stopper \\
        --strategies none vsl alinea --rho-target-veh-km 19.9 --x-ref 11027 --span 1110 11027 \\
        --replicates 20 --procs 30 --out runs/X_sweep --summary artifacts/sweep_X_summary.json
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import shutil
import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from flowstate_core.strategies import STRATEGIES, apply_strategy, needs_target

FIELDS = (
    "throughput_veh_h",
    "sigma_v_temporal_ms",
    "sigma_v_spatial_ms",
    "mean_tt_s",
    "p90_tt_s",
    "fuel_ml_per_veh_km",
    "wave_count",
    "wave_speed_kmh",
    "wave_amplitude_ms",
    # WP-105: travel time and delay including waiting (validation.metrics.WaitingMetrics)
    "mean_tt_incl_waiting_s",
    "p90_tt_incl_waiting_s",
    "total_delay_incl_waiting_veh_h",
    "insertion_delay_veh_h",
    "meter_wait_veh_h",
    "n_censored",
)


#: Key of a run's ``metrics.json`` that records its metric arguments (module docstring).
RUN_METRICS_ARGS_KEY = "metrics_args"


def metrics_args_record(metrics_args: Mapping[str, Any]) -> dict[str, Any]:
    """The canonical record of a run's metric arguments.

    ``x_ref`` and ``span`` as floats, ``scored_end_s`` a float or None (an
    absent key is None: scored to the run's end), so a manifest written
    before the scored end existed compares equal to a launch without it.
    """
    x_ref = metrics_args.get("x_ref")
    span = metrics_args.get("span")
    end = metrics_args.get("scored_end_s")
    return {
        "x_ref": None if x_ref is None else float(x_ref),
        "span": None if span is None else [float(v) for v in span],
        "scored_end_s": None if end is None else float(end),
    }


def resume_conflict(
    root: Path, runs: Iterable[tuple[str, str, int]], metrics_args: Mapping[str, Any]
) -> str | None:
    """Why the stored tree under ``root`` cannot be resumed with ``metrics_args``, or None.

    Only a tree with a stored run among ``runs`` is checked (nothing else
    would be reused). Refused: the stored ``MANIFEST.json`` records other
    metric arguments; a stored run's ``metrics.json`` records other ones; a
    stored run records none (written before the record existed, so its scored
    end is unknown) while the launch sets a scored end.

    Args:
        root: Run tree root (``<root>/<cell>/<config hash>/<seed>/``).
        runs: ``(cell, config hash, seed)`` of the runs the launch would reuse.
        metrics_args: The launch's metric arguments.

    Returns:
        The refusal (it names a fresh ``--out``), or None.
    """
    want = metrics_args_record(metrics_args)
    stored: list[Path] = []
    for cell, chash, seed in runs:
        p = root / cell / chash / str(seed) / "metrics.json"
        if p.is_file():
            stored.append(p)
    if not stored:
        return None
    advice = (
        "runs scored on different windows cannot be mixed: use a fresh --out, or rerun with "
        "the recorded values"
    )
    manifest = root / "MANIFEST.json"
    if manifest.is_file():
        recorded = json.loads(manifest.read_text()).get("metrics_args")
        if isinstance(recorded, Mapping) and metrics_args_record(recorded) != want:
            return (
                f"{manifest} records metrics_args {metrics_args_record(recorded)}; this launch "
                f"asks for {want} and {len(stored)} stored run(s) would be reused; {advice}"
            )
    unknown: list[Path] = []
    for p in stored:
        rec = json.loads(p.read_text()).get(RUN_METRICS_ARGS_KEY)
        if not isinstance(rec, Mapping):
            unknown.append(p)
        elif metrics_args_record(rec) != want:
            return (
                f"{p} was scored with metrics_args {metrics_args_record(rec)}; this launch asks "
                f"for {want}; {advice}"
            )
    if unknown and want["scored_end_s"] is not None:
        return (
            f"{len(unknown)} stored run(s) (e.g. {unknown[0]}) do not record the window they "
            f"were scored on (written before 2026-10-07), and this launch sets scored_end_s "
            f"{want['scored_end_s']:g}; {advice}"
        )
    return None


def scored_end_problem(config: Mapping[str, Any], scored_end_s: float | None) -> str | None:
    """Why ``scored_end_s`` cannot score runs of ``config`` (checked before simulating), or None.

    The scored end must lie after the warm-up and no later than the run's
    end, the bounds :func:`validation.battery.measurement_window` (and the
    metrics) apply after a run; checked up front, a mistyped value costs
    nothing instead of every run's simulation.

    Args:
        config: A serialized scenario (``ScenarioConfig.model_dump``).
        scored_end_s: ``--scored-end-s``; None scores to the run's end.

    Returns:
        The refusal, or None.
    """
    if scored_end_s is None:
        return None
    from validation.battery import measurement_window

    try:
        measurement_window({"config": dict(config)}, scored_end_s)
    except ValueError as exc:
        return f"--scored-end-s {scored_end_s:g}: {exc}; nothing was simulated"
    return None


def cell_config(
    base: dict[str, Any],
    pen: float,
    comp: float,
    controller: str | None,
    strategy: str,
    rho_target_veh_km: float | None,
) -> dict[str, Any]:
    cfg = json.loads(json.dumps(base))
    cfg["av"]["penetration"] = pen
    cfg["av"]["compliance"] = comp
    cfg["av"]["controller"] = controller if pen > 0.0 else None
    cfg["av"]["controller_params"] = {}
    apply_strategy(cfg, strategy, rho_target_veh_km)
    return cfg


def _worker(
    payload: tuple[str, dict[str, Any], int, dict[str, Any], str, bool],
) -> tuple[str, int, bool, str]:
    cell_name, cfg_json, seed, metrics_args, root, keep = payload
    try:
        from flowstate_core.config import ScenarioConfig
        from microsim.runner import run_micro
        from validation.metrics import compute_metrics, compute_waiting_metrics

        paths = run_micro(ScenarioConfig.model_validate(cfg_json), seed, Path(root) / cell_name)
        m = compute_metrics(paths.run_dir, **metrics_args)
        # the waiting measures (WP-105) beside the standard ones, same window
        w = compute_waiting_metrics(paths.run_dir, scored_end_s=metrics_args.get("scored_end_s"))
        record = {**asdict(m), **asdict(w)}
        # the window it was scored on, so a resume can refuse to mix windows
        record[RUN_METRICS_ARGS_KEY] = metrics_args_record(metrics_args)
        (paths.run_dir / "metrics.json").write_text(json.dumps(record, indent=2))
        if not keep:
            paths.trajectories.unlink(missing_ok=True)
            paths.edges.unlink(missing_ok=True)
            shutil.rmtree(paths.run_dir / "net", ignore_errors=True)
        return cell_name, seed, True, ""
    except Exception as exc:  # reported, never raised: one failed seed must not kill the pool
        return cell_name, seed, False, f"{type(exc).__name__}: {exc}"


def _done(root: Path, cell_name: str, chash: str, seed: int) -> bool:
    """A run counts as done when its ``metrics.json`` exists.

    ``meta.json`` is not required: it only feeds the optional diagnostics
    block, and archives before 2026-09-24 carried ``metrics.json`` alone —
    requiring both made a resumed sweep redo every stored run (the 2026-09-24
    resume of 112 stored runs started all 120 over).
    """
    return (root / cell_name / chash / str(seed) / "metrics.json").is_file()


#: ``meta.json["ramp_meters"][i]`` counters aggregated per ramp meter.
METER_COUNTERS = ("n_released", "n_passed_unstoppable")
#: ``meta.json["weave_sections"][i]`` fields aggregated per weaving section
#: (the runner's exact keys, docs/CONTRACTS.md §2 weaving sections). After
#: ``wait_s_mean`` come the follower-cooperation counters (2026-09-24, block
#: 3), the vacate counters (third derivation), the pair releases (fifth), the
#: exits given up (exit side), the re-derived vacate rule's skipped vehicles
#: and requests (block 3) and the give-ups deferred by the bounded patiences
#: (WP-52, the braking follower; WP-53, the vehicle beside the exiter), the
#: two yields at the lane ends (WP-54), the entrant's entry-speed bound
#: (WP-57), the bounded hold (WP-58), the gated anticipation (WP-60), the
#: exiters' early move (WP-62), the swap (WP-64), the crossings spread
#: (WP-67), the ramp's outlet (WP-70), the exit priority from the braking
#: onset (WP-73), the anticipation sparing the exiters (WP-75) and the
#: opposing-entry guard's deferrals (WP-92); a meta written before a counter
#: contributes nothing to its interval (``n`` = 0).
WEAVE_FIELDS = (
    "n_entered",
    "n_exited",
    "n_reached_section_exiting",
    "n_forced",
    "n_forced_deferred",
    "n_unfinished",
    "wait_s_mean",
    "n_cooperations",
    "mean_follower_decel_ms2",
    "n_changer_eased",
    "n_vacated",
    "n_vacate_refused",
    "n_pair_releases",
    "n_missed_exit",
    "n_vacate_skipped_no_gap",
    "n_vacate_requests",
    "n_giveup_waited",
    "n_exiter_yields",
    "n_entrant_yields",
    "n_entry_bounded",
    "n_hold_releases",
    "n_anticipation_gated",
    "n_exit_prepared",
    "n_swaps",
    "n_spread_withheld",
    "n_outlet_spared",
    "n_onset_priority",
    "n_anticipation_exiter_spared",
    "n_opposing_deferred",
)


def _ci(vals: list[float]) -> dict[str, Any]:
    """Mean with its 95 % t-interval over the finite values (n = 1 gives a nan half-width)."""
    import numpy as np
    from scipy import stats

    arr = np.asarray(vals, dtype=float)
    arr = arr[np.isfinite(arr)]
    n = len(arr)
    if n == 0:
        return {"mean": None, "lo95": None, "hi95": None, "n": 0}
    mean = float(arr.mean())
    half = (
        float(stats.t.ppf(0.975, n - 1) * arr.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan")
    )
    return {
        "mean": mean,
        "lo95": mean - half,
        "hi95": mean + half,
        "n": n,
        "underpowered": n < 20,
    }


def _num(value: Any) -> float:
    """A meta counter as a float; ``None`` (a section with no wait, or a counter the meta
    predates) becomes nan and is skipped."""
    return float("nan") if value is None else float(value)


def diagnostics_block(metas: list[dict[str, Any]]) -> dict[str, Any]:
    """The ``diagnostics`` entry of one cell from the ``meta.json`` of its runs.

    ``ramp_meters`` is keyed by ramp name and carries, per counter of
    :data:`METER_COUNTERS`, the seed mean with its 95 % t-interval and n
    (:func:`_ci`), plus ``share_passed_unstoppable`` = ``n_passed_unstoppable /
    (n_released + n_passed_unstoppable)`` per seed (a seed on which the meter
    saw no vehicle contributes nothing to the share). ``weave_sections`` is
    keyed by on-ramp and carries :data:`WEAVE_FIELDS` the same way. A ramp
    absent from one seed's meta is aggregated over the seeds that have it.

    Args:
        metas: Parsed ``meta.json`` of the cell's runs that have one.

    Returns:
        ``{"n_runs_with_meta", "ramp_meters", "weave_sections"}``; the two
        maps are empty when no run has a meta or the metas carry no such block.
    """
    meter_vals: dict[str, dict[str, list[float]]] = {}
    meter_ctl: dict[str, str | None] = {}
    for meta in metas:
        for ms in meta.get("ramp_meters") or []:
            name = str(ms.get("ramp"))
            store = meter_vals.setdefault(name, {k: [] for k in (*METER_COUNTERS, "share")})
            meter_ctl.setdefault(name, ms.get("controller"))
            released = _num(ms.get("n_released"))
            passed = _num(ms.get("n_passed_unstoppable"))
            store["n_released"].append(released)
            store["n_passed_unstoppable"].append(passed)
            total = released + passed
            store["share"].append(passed / total if total > 0 else float("nan"))
    weave_vals: dict[str, dict[str, list[float]]] = {}
    for meta in metas:
        for ws in meta.get("weave_sections") or []:
            name = str(ws.get("ramp"))
            store = weave_vals.setdefault(name, {k: [] for k in WEAVE_FIELDS})
            for k in WEAVE_FIELDS:
                store[k].append(_num(ws.get(k)))
    return {
        "n_runs_with_meta": len(metas),
        "ramp_meters": {
            name: {
                "controller": meter_ctl[name],
                **{k: _ci(vals[k]) for k in METER_COUNTERS},
                "share_passed_unstoppable": _ci(vals["share"]),
            }
            for name, vals in sorted(meter_vals.items())
        },
        "weave_sections": {
            name: {k: _ci(vals[k]) for k in WEAVE_FIELDS}
            for name, vals in sorted(weave_vals.items())
        },
    }


def collision_block(counts: dict[int, int | None]) -> dict[str, Any]:
    """The ``collisions`` entry of one cell from its runs' collision counts.

    Args:
        counts: Per seed with a ``metrics.json``: its ``meta.json``
            ``n_collisions`` (``validation.battery.collision_count``), or None
            when the run has no ``meta.json`` or the meta no counter.

    Returns:
        ``{"n_runs", "n_runs_recorded", "total", "runs_with_collisions":
        [{"seed", "n"}], "runs_not_recorded": [seed]}``; ``total`` is the sum
        over the recorded runs and null when none records the counter (not
        recorded is not zero).
    """
    recorded = {s: c for s, c in counts.items() if c is not None}
    return {
        "n_runs": len(counts),
        "n_runs_recorded": len(recorded),
        "total": sum(recorded.values()) if recorded else None,
        "runs_with_collisions": [{"seed": s, "n": c} for s, c in recorded.items() if c > 0],
        "runs_not_recorded": [s for s, c in counts.items() if c is None],
    }


def collision_line(summary: dict[str, Any]) -> str:
    """One console line for the sweep's ``zero_collisions`` requirement."""
    flag = summary.get("zero_collisions")
    cells = summary["cells"]
    n_runs = sum(e["collisions"]["n_runs"] for e in cells.values())
    if flag is True:
        return f"collisions: PASS — zero in all {n_runs} run(s)"
    failing = [
        f"{cell} ({e['collisions']['total']})"
        for cell, e in cells.items()
        if e["zero_collisions"] is False
    ]
    missing = sum(len(e["collisions"]["runs_not_recorded"]) for e in cells.values())
    parts = []
    if failing:
        parts.append("collisions in " + ", ".join(failing))
    if missing:
        parts.append(f"not recorded for {missing} of {n_runs} run(s)")
    status = "FAIL" if flag is False else "NOT RECORDED"
    return f"collisions: {status} — " + "; ".join(parts or ["no run"])


def _fmt_ci(c: dict[str, Any], digits: int = 1, scale: float = 1.0) -> str:
    """``mean [lo, hi] (n)`` of one :func:`_ci` entry for the console; ``—`` when empty."""
    if c["n"] == 0:
        return "—"
    return (
        f"{c['mean'] * scale:.{digits}f} [{c['lo95'] * scale:.{digits}f}, "
        f"{c['hi95'] * scale:.{digits}f}] (n={c['n']})"
    )


def print_diagnostics(summary: dict[str, Any]) -> None:
    """Print each cell's ramp-meter and weaving-section diagnostics, when any run had a meta."""
    for cell, entry in summary["cells"].items():
        diag = entry.get("diagnostics")
        if not diag or diag["n_runs_with_meta"] == 0:
            continue
        n_seeds = entry["aggregate"][FIELDS[0]]["n"]
        print(f"  diagnostics {cell} (meta in {diag['n_runs_with_meta']}/{n_seeds} runs):")
        for ramp, m in diag["ramp_meters"].items():
            print(
                f"    meter {ramp} ({m['controller']}): released {_fmt_ci(m['n_released'])}, "
                f"passed unstoppable {_fmt_ci(m['n_passed_unstoppable'])}, "
                f"share {_fmt_ci(m['share_passed_unstoppable'], 2, 100.0)} %"
            )
        for ramp, w in diag["weave_sections"].items():
            print(
                f"    weave {ramp}: entered {_fmt_ci(w['n_entered'])}, "
                f"exited {_fmt_ci(w['n_exited'])}, "
                f"reached (exiting) {_fmt_ci(w['n_reached_section_exiting'])}, "
                f"forced {_fmt_ci(w['n_forced'])}, deferred {_fmt_ci(w['n_forced_deferred'])}, "
                f"unfinished {_fmt_ci(w['n_unfinished'])}, wait {_fmt_ci(w['wait_s_mean'])} s, "
                f"cooperations {_fmt_ci(w['n_cooperations'])}, "
                f"follower decel {_fmt_ci(w['mean_follower_decel_ms2'], 2)} m/s², "
                f"changer easings {_fmt_ci(w['n_changer_eased'])}, "
                f"through vacated {_fmt_ci(w['n_vacated'])}, "
                f"vacate refused {_fmt_ci(w['n_vacate_refused'])}, "
                f"vacate skipped (no gap) {_fmt_ci(w['n_vacate_skipped_no_gap'])}, "
                f"vacate requests {_fmt_ci(w['n_vacate_requests'])}, "
                f"pair releases {_fmt_ci(w['n_pair_releases'])}, "
                f"exits given up {_fmt_ci(w['n_missed_exit'])}"
            )


def run_records(
    root: Path, cells: dict[str, str], seeds: list[int]
) -> dict[str, dict[int, dict[str, Any]]]:
    """Per cell and seed, the stored run's record for the comparison table.

    The record is the run's ``metrics.json`` (every field) plus
    ``n_collisions`` from its ``meta.json`` (``validation.battery.collision_count``;
    None when the run has no meta or the meta no counter). A run without
    ``metrics.json`` contributes nothing.

    Args:
        root: Run tree root (``<root>/<cell>/<config hash>/<seed>/``).
        cells: Cell name → config hash.
        seeds: The seeds to read.

    Returns:
        Cell → seed → record.
    """
    from validation.battery import collision_count

    out: dict[str, dict[int, dict[str, Any]]] = {}
    for cell, chash in cells.items():
        out[cell] = {}
        for seed in seeds:
            p = root / cell / chash / str(seed) / "metrics.json"
            if not p.is_file():
                continue
            record: dict[str, Any] = json.loads(p.read_text())
            meta_path = p.with_name("meta.json")
            record["n_collisions"] = (
                collision_count(json.loads(meta_path.read_text())) if meta_path.is_file() else None
            )
            out[cell][int(seed)] = record
    return out


def write_comparison(
    records: dict[str, dict[int, dict[str, Any]]], out: Path, *, baseline: str = "baseline"
) -> dict[str, Any]:
    """Build the protocol §8.3 comparison table and write it as JSON and Markdown.

    Args:
        records: :func:`run_records` of the cells to compare (baseline among them).
        out: JSON path; the Markdown goes beside it (``.md``).
        baseline: The do-nothing cell.

    Returns:
        The table as a dict.

    Raises:
        validation.strategy_compare.ComparisonRefusedError: A cell lacks a
            measure of the fixed set, or the seed sets differ.
    """
    from validation.strategy_compare import build_comparison_table, render_markdown

    table = build_comparison_table(records, baseline=baseline)
    payload = table.to_dict()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    out.with_suffix(".md").write_text(render_markdown(table))
    return payload


def analyze(root: Path, summary_path: Path, *, allow_partial: bool) -> dict[str, Any]:
    import numpy as np
    from scipy import stats

    from validation.battery import collision_count
    from validation.criteria import zero_collisions

    manifest = json.loads((root / "MANIFEST.json").read_text())
    seeds = [int(s) for s in manifest["seeds"]]
    per_cell: dict[str, dict[int, dict[str, float]]] = {}
    metas: dict[str, list[dict[str, Any]]] = {}
    collisions: dict[str, dict[int, int | None]] = {}
    missing: list[tuple[str, int]] = []
    for cell, chash in manifest["cells"].items():
        per_cell[cell] = {}
        metas[cell] = []
        collisions[cell] = {}
        for seed in seeds:
            p = root / cell / chash / str(seed) / "metrics.json"
            if not p.is_file():
                missing.append((cell, seed))
                continue
            m = json.loads(p.read_text())
            per_cell[cell][seed] = {f: float(m[f]) for f in FIELDS if f in m and m[f] is not None}
            meta_path = p.with_name("meta.json")
            collisions[cell][seed] = None
            if meta_path.is_file():
                meta = json.loads(meta_path.read_text())
                metas[cell].append(meta)
                collisions[cell][seed] = collision_count(meta)
    incomplete = sorted({c for c, _ in missing})
    if missing and not allow_partial:
        raise SystemExit(
            f"{len(missing)} runs lack metrics.json, e.g. {missing[:3]}; pass --allow-partial"
        )
    if "baseline" in incomplete:
        raise SystemExit("baseline cell incomplete; nothing to pair against")
    per_cell = {c: v for c, v in per_cell.items() if c not in incomplete}

    base = per_cell["baseline"]
    cells_out: dict[str, Any] = {}
    for cell, by_seed in per_cell.items():
        agg = {
            f: _ci([by_seed[s][f] for s in seeds if s in by_seed and f in by_seed[s]])
            for f in FIELDS
        }
        entry: dict[str, Any] = {
            "aggregate": agg,
            "grid": manifest["grid"][cell],
            "config_hash": manifest["cells"][cell],
        }
        if cell != "baseline":
            deltas: dict[str, Any] = {}
            for f in FIELDS:
                pairs = [
                    (by_seed[s][f], base[s][f])
                    for s in seeds
                    if s in by_seed
                    and s in base
                    and f in by_seed[s]
                    and f in base[s]
                    and np.isfinite(by_seed[s][f])
                    and np.isfinite(base[s][f])
                ]
                if len(pairs) < 2:
                    continue
                d = np.asarray([a - b for a, b in pairs], dtype=float)
                n = len(d)
                mean = float(d.mean())
                half = float(stats.t.ppf(0.975, n - 1) * d.std(ddof=1) / np.sqrt(n))
                base_mean = float(np.mean([b for _, b in pairs]))
                deltas[f] = {
                    "mean": mean,
                    "lo95": mean - half,
                    "hi95": mean + half,
                    "n": n,
                    "pct_of_baseline": (100.0 * mean / base_mean) if base_mean else None,
                    "resolved": bool((mean - half) > 0 or (mean + half) < 0),
                }
            entry["vs_baseline_paired"] = deltas
        entry["diagnostics"] = diagnostics_block(metas[cell])
        # Model integrity (WP-98): the cell's collisions and its pass/fail flag.
        entry["collisions"] = collision_block(collisions[cell])
        entry["zero_collisions"] = zero_collisions(list(collisions[cell].values()))
        cells_out[cell] = entry
    summary = {
        "experiment": manifest["experiment"],
        "scenario": manifest["scenario"],
        "base_config_hash": manifest["base_config_hash"],
        "grid_spec": manifest["grid_spec"],
        "rho_target_veh_km": manifest.get("rho_target_veh_km"),
        "metrics_args": manifest["metrics_args"],
        "n_seeds": len(seeds),
        "seeds": seeds,
        "incomplete_cells": incomplete,
        "cells": cells_out,
        # over every run of every complete cell: true / false / null
        "zero_collisions": zero_collisions(
            [c for cell in cells_out for c in collisions[cell].values()]
        ),
    }
    (root / "analysis.json").write_text(json.dumps(summary, indent=2))
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--scenario", required=True, type=Path)
    ap.add_argument("--penetration", type=float, nargs="*", default=[0.05, 0.10, 0.20])
    ap.add_argument("--compliance", type=float, nargs="*", default=[1.0])
    ap.add_argument("--controllers", nargs="*", default=["follower_stopper"])
    ap.add_argument("--strategies", nargs="*", default=["none"], choices=STRATEGIES)
    ap.add_argument(
        "--rho-target-veh-km", type=float, default=None, help="ALINEA per-lane target density"
    )
    ap.add_argument("--x-ref", type=float, required=True, help="throughput cross-section [m]")
    ap.add_argument(
        "--span", type=float, nargs=2, required=True, metavar=("LO", "HI"), help="analysed span [m]"
    )
    ap.add_argument(
        "--scored-end-s",
        type=float,
        default=None,
        metavar="T",
        help="end of the scored period [s, simulation time], the study period's end before a "
        "cool-down (docs/FRISCO_PROTOCOL.md section 8.2); default: score to the run's end",
    )
    ap.add_argument("--replicates", type=int, default=20)
    ap.add_argument("--procs", type=int, default=8)
    ap.add_argument("--out", required=True, type=Path, help="run tree root")
    ap.add_argument("--summary", required=True, type=Path)
    ap.add_argument("--keep-trajectories", action="store_true")
    ap.add_argument("--analyze-only", action="store_true")
    ap.add_argument("--allow-partial", action="store_true")
    ap.add_argument(
        "--comparison",
        type=Path,
        default=None,
        help="also write the protocol §8.3 strategy comparison table (JSON, .md beside it); "
        "refuses when a run lacks a measure of the fixed set",
    )
    args = ap.parse_args()

    from flowstate_core.config import ScenarioConfig, config_hash
    from flowstate_core.rng import spawn_seeds

    root: Path = args.out
    if args.analyze_only:
        s = analyze(root, args.summary, allow_partial=args.allow_partial)
        print(f"analysed {len(s['cells'])} cells; incomplete {s['incomplete_cells']}")
        print(collision_line(s))
        print_diagnostics(s)
        _comparison_or_exit(args.comparison, root, s)
        return

    if any(needs_target(s) for s in args.strategies) and args.rho_target_veh_km is None:
        raise SystemExit(
            "--rho-target-veh-km is required for the alinea strategies (the corridor's "
            "per-lane critical density, e.g. rho_c of its FD artifact); there is no default"
        )
    base_cfg = ScenarioConfig.from_yaml(args.scenario)
    base_json = json.loads(base_cfg.model_dump_json())
    problem = scored_end_problem(base_json, args.scored_end_s)
    if problem is not None:
        raise SystemExit(problem)
    seeds = spawn_seeds(base_cfg.seed, args.replicates)
    metrics_args: dict[str, Any] = {
        "x_ref": float(args.x_ref),
        "span": (float(args.span[0]), float(args.span[1])),
    }
    if args.scored_end_s is not None:
        metrics_args["scored_end_s"] = float(args.scored_end_s)

    grid: dict[str, dict[str, Any]] = {
        "baseline": {"penetration": 0.0, "compliance": 1.0, "controller": None, "strategy": "none"}
    }
    for strategy in args.strategies:
        if strategy != "none":
            grid[f"strategy_{strategy}"] = {
                "penetration": 0.0,
                "compliance": 1.0,
                "controller": None,
                "strategy": strategy,
            }
    for strategy in args.strategies:
        for controller in args.controllers:
            for pen in args.penetration:
                if pen <= 0.0:
                    continue
                for comp in args.compliance:
                    name = f"{controller}_p{pen:.2f}_c{comp:.2f}_{strategy}"
                    grid[name] = {
                        "penetration": pen,
                        "compliance": comp,
                        "controller": controller,
                        "strategy": strategy,
                    }

    hashes: dict[str, str] = {}
    configs: dict[str, dict[str, Any]] = {}
    for name, g in grid.items():
        cfg_json = cell_config(
            base_json,
            g["penetration"],
            g["compliance"],
            g["controller"],
            g["strategy"],
            args.rho_target_veh_km,
        )
        hashes[name] = config_hash(ScenarioConfig.model_validate(cfg_json))
        configs[name] = cfg_json
    conflict = resume_conflict(
        root, [(name, hashes[name], s) for name in grid for s in seeds], metrics_args
    )
    if conflict is not None:
        raise SystemExit(f"refusing to resume {root}: {conflict}")
    root.mkdir(parents=True, exist_ok=True)
    (root / "MANIFEST.json").write_text(
        json.dumps(
            {
                "experiment": root.name,
                "scenario": str(args.scenario),
                "base_config_hash": config_hash(base_cfg),
                "grid_spec": {
                    "penetration": args.penetration,
                    "compliance": args.compliance,
                    "controllers": args.controllers,
                    "strategies": args.strategies,
                },
                "rho_target_veh_km": args.rho_target_veh_km,
                "metrics_args": metrics_args,
                "grid": grid,
                "cells": hashes,
                "seeds": seeds,
            },
            indent=2,
        )
    )

    pending = [
        (name, configs[name], s, metrics_args, str(root), args.keep_trajectories)
        for name in grid
        for s in seeds
        if not _done(root, name, hashes[name], s)
    ]
    print(
        f"{len(grid)} cells × {len(seeds)} seeds = {len(grid) * len(seeds)} runs; {len(pending)} pending",
        flush=True,
    )
    t0 = time.perf_counter()
    n_fail = 0
    if pending:
        with mp.get_context("spawn").Pool(min(args.procs, len(pending))) as pool:
            for i, (cell, seed, ok, err) in enumerate(
                pool.imap_unordered(_worker, pending), start=1
            ):
                if not ok:
                    n_fail += 1
                    print(f"  FAIL {cell} seed={seed}: {err}", flush=True)
                if i % 10 == 0 or i == len(pending):
                    print(f"  {i}/{len(pending)} ({time.perf_counter() - t0:.0f} s)", flush=True)
    print(f"runs done in {time.perf_counter() - t0:.0f} s; {n_fail} failed", flush=True)
    s = analyze(root, args.summary, allow_partial=True)
    print(f"summary → {args.summary}; incomplete cells: {s['incomplete_cells']}")
    print(collision_line(s))
    print_diagnostics(s)
    _comparison_or_exit(args.comparison, root, s)


def _comparison_or_exit(path: Path | None, root: Path, summary: dict[str, Any]) -> None:
    """``--comparison``: write the table over the complete cells, or exit with the refusal."""
    if path is None:
        return
    from validation.strategy_compare import ComparisonRefusedError

    manifest = json.loads((root / "MANIFEST.json").read_text())
    cells = {c: h for c, h in manifest["cells"].items() if c in summary["cells"]}
    try:
        write_comparison(run_records(root, cells, [int(s) for s in manifest["seeds"]]), path)
    except ComparisonRefusedError as exc:
        raise SystemExit(str(exc)) from None
    print(f"comparison → {path} (+ {path.with_suffix('.md').name})")


if __name__ == "__main__":
    main()
