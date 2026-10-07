"""The I-94 6th Street left-exit probe: the slice as built against the slice with the exit fixed.

docs/I94_LANE_SHARES.md §4 found that netconvert's ramp guessing compiles the
I-94 WB 6th Street LEFT exit (edge ``45782590``) with a guessed lane on the
right and the left lane leading only to the exit, so every through lane moves
one place left at 9.30-9.55 km, while OSM draws the left lane as an option
lane and the mainline keeping its three lanes. ``microsim.split_audit`` now
reports it (``through_lane_exit_only``) and
``scenarios/mndot_i94_wb_stpaul_weave_slice_netfix.yaml`` compiles the OSM
layout (``--ramps.unset 1001426896,45782590``). This probe asks, on the cloud,
how much of the Amendment-1 grid's I-94 lane-share error the network defect
explains (§6.3 of the note, written before any run):

* **Runs** — the 35-minute slice under the corridor's reference configuration
  (``xlsfg``, :func:`calibrate_driver_grid.xlsfg_variant`), two networks
  (``as_built``: the committed slice; ``netfix``: the variant) × two driver
  settings (``k0.0_kr0.0``, the grid's reference, and ``k1.0_kr0.1``, the
  pair the Amendment-1 rule chose) × four seeds (``spawn_seeds(42, 4)``, whose
  first two are the grid's) = 16 runs of about 4 GB, built exactly as the grid
  builds its pairs (:func:`calibrate_driver_grid.pair_config`; the as-built
  configs carry the grid's own config hashes).
* **Readings** — the grid's reader (:func:`calibrate_driver_grid.readings_detectors`):
  crossings by SUMO lane at every observed station and S97's 5-minute flows,
  with each run's collision count and departures.
* **Scores** — the grid's scorer (:func:`calibrate_driver_grid._score_detectors`)
  against the committed observed lane shares and S97 discharge, four ways:
  each network's own compared stations (lane count matching, 25 m from a
  change, per network) and the stations both networks compare, each with the
  targets as committed (IRIS lane order) and with S791's labels reversed (the
  data-quality report's ``lane_order`` check, docs/I94_LANE_SHARES.md §3; the
  correction to a target fixed in advance is the owner's decision, so both
  are reported). When the observed-lanes artifact already stores S791 in
  corrected order (``calibrate_driver_grid.py --build-observed-lanes
  --reverse-lane-order S791``, or ``--remap-reversed-lanes``; its stations
  record ``lane_order.iris_labels_reversed``), the targets are not reversed a
  second time (:func:`already_corrected`): both score sets are then the
  corrected ones, and the artifact says so. Shares at every station by IRIS
  lane (lane 1 = rightmost = SUMO lane 0) are listed, compared or not.
* **Expectations** — the note's §6.3 statements, evaluated mechanically.
* **Reproduction** — the as-built runs at the grid's two seeds are compared
  with the committed grid readings (``artifacts/p3_driver_grid_2026-10-07``):
  identical readings say the code path is unchanged since the grid.

A probe: four seeds, one corridor slice, no calibration, no validation claim.
Corridor runs are refused on a machine below 24 GB (the laptop rule;
``--allow-small-machine`` overrides): this is cloud stage
``p5_i94_netfix_probe`` of ``scripts/gcp/pipeline_i24.sh``.

Run (VM, repository root)::

    uv run --no-sync python scripts/i94_netfix_probe.py --plan-only
    uv run --no-sync python scripts/i94_netfix_probe.py --procs 16 \\
        --out runs/p5/i94_netfix_probe --artifact artifacts/i94_netfix_probe.json
    uv run --no-sync python scripts/i94_netfix_probe.py --analyze-only \\
        --out runs/p5/i94_netfix_probe --artifact artifacts/i94_netfix_probe.json
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import math
import multiprocessing as mp
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import calibrate_driver_grid as g

from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from validation.lane_use import lanes_at, shares

SCHEMA = "flowstate.i94_netfix_probe/1"
NOTE = "docs/I94_LANE_SHARES.md"

#: The two networks: the committed slice and its 6th Street fix.
NETWORKS: dict[str, str] = {
    "as_built": "scenarios/mndot_i94_wb_stpaul_weave_slice.yaml",
    "netfix": "scenarios/mndot_i94_wb_stpaul_weave_slice_netfix.yaml",
}
#: ``(k, lc_keep_right)``: the grid's reference and the pair the Amendment-1 rule chose.
PAIRS: tuple[tuple[float, float], ...] = ((0.0, 0.0), (1.0, 0.1))
N_SEEDS = 4
#: Stations whose IRIS labels the lane-order check reads reversed (docs/I94_LANE_SHARES.md §3).
REVERSED_STATIONS: tuple[str, ...] = ("S791",)
#: The committed grid's run tree (the reproduction check reads its readings).
GRID_ROOT = "artifacts/p3_driver_grid_2026-10-07/grid_i94"
#: The grid's smallest S791-corrected pooled RMSE [pp] (docs/I94_LANE_SHARES.md §2).
GRID_MIN_CORRECTED_RMSE_PP = 4.97
#: The netconvert option the fix changes (the only network difference).
FIX_OPTION = "--ramps.unset"
#: The paths whose uncommitted changes make the artifact's ``code_dirty`` true.
CODE_PATHS = (*g.CODE_PATHS, "scripts/i94_netfix_probe.py")


@dataclasses.dataclass(frozen=True)
class ProbePlan:
    """Every run's config and hash, by network and pair, and the seeds."""

    specs: dict[str, g.CorridorSpec]
    references: dict[str, dict[str, Any]]
    configs: dict[str, dict[str, dict[str, Any]]]
    hashes: dict[str, dict[str, str]]
    pairs: tuple[tuple[float, float], ...]
    seeds: list[int]

    @property
    def names(self) -> list[str]:
        """Pair names in :data:`PAIRS` order."""
        return [g.pair_name(k, kr) for k, kr in self.pairs]

    @property
    def n_runs(self) -> int:
        return len(self.specs) * len(self.pairs) * len(self.seeds)


def _strip_network_fix(raw: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(raw)
    out.pop("name", None)
    out["network"].pop("netconvert_extra", None)
    return out


def build_probe(
    networks: dict[str, str] | None = None,
    pairs: tuple[tuple[float, float], ...] = PAIRS,
    n_seeds: int = N_SEEDS,
) -> ProbePlan:
    """The probe's configs: each network's reference (``xlsfg``) with each pair's drivers.

    Raises:
        ValueError: The networks differ in anything but their name and
            ``netconvert_extra``, or run different seeds.
    """
    networks = dict(NETWORKS if networks is None else networks)
    base = g.SPECS["i94"]
    specs: dict[str, g.CorridorSpec] = {}
    references: dict[str, dict[str, Any]] = {}
    configs: dict[str, dict[str, dict[str, Any]]] = {}
    hashes: dict[str, dict[str, str]] = {}
    seeds: list[int] | None = None
    for net, path in networks.items():
        spec = dataclasses.replace(base, base_scenario=path)
        ref = g.reference_raw(spec)
        net_seeds = spawn_seeds(int(ScenarioConfig.model_validate(ref).seed), n_seeds)
        if seeds is not None and net_seeds != seeds:
            raise ValueError(f"{path} runs other seeds than the other network")
        seeds = net_seeds
        specs[net], references[net] = spec, ref
        configs[net], hashes[net] = {}, {}
        for k, kr in pairs:
            raw = g.pair_config(ref, g.population_for(spec, k), kr)
            configs[net][g.pair_name(k, kr)] = raw
            hashes[net][g.pair_name(k, kr)] = config_hash(ScenarioConfig.model_validate(raw))
    stripped = {_json_key(_strip_network_fix(r)) for r in references.values()}
    if len(stripped) != 1:
        raise ValueError("the networks must differ only in their name and netconvert_extra")
    assert seeds is not None
    return ProbePlan(specs, references, configs, hashes, tuple(pairs), seeds)


def _json_key(raw: dict[str, Any]) -> str:
    return json.dumps(raw, sort_keys=True, default=str)


def _ramps_unset(raw: dict[str, Any]) -> str | None:
    extra = [str(a) for a in raw["network"].get("netconvert_extra") or []]
    for i, a in enumerate(extra):
        if a == FIX_OPTION and i + 1 < len(extra):
            return extra[i + 1]
        if a.startswith(FIX_OPTION + "="):
            return a.partition("=")[2]
    return None


def _context(plan: ProbePlan, observed_lanes: Path) -> dict[str, Any]:
    """The grid's detector-mode reading context (the same for both networks)."""
    spec = plan.specs["as_built"] if "as_built" in plan.specs else next(iter(plan.specs.values()))
    cfg = ScenarioConfig.model_validate(next(iter(plan.references.values())))
    ol = json.loads(observed_lanes.read_text())
    return {
        "mode": "detectors",
        "stations": [
            {"id": s["id"], "x_m": float(s["x_m"])} for s in ol["stations"] if s["x_m"] is not None
        ],
        "discharge_stations": list(spec.discharge_stations),
        "observations": str(g._path(str(spec.observations))),
        "clock_offset_s": spec.clock_offset_s,
        "t_range": [float(cfg.sim.warmup_s), float(cfg.sim.duration_s)],
    }


def _run_dir(root: Path, net: str, name: str, h: str, seed: int) -> Path:
    return root / net / name / h / str(seed)


def lanes_for(plan: ProbePlan, root: Path) -> dict[str, list[dict[str, Any]]]:
    """Each network's compiled lane table (``<root>/<network>/LANES.json``, built once)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for net, ref in plan.references.items():
        path = root / net / g.LANES
        ref_hash = config_hash(ScenarioConfig.model_validate(ref))
        if path.is_file():
            out[net] = json.loads(path.read_text())["lanes"]
            continue
        with tempfile.TemporaryDirectory(prefix="netfix_probe_net_") as td:
            out[net] = g.lane_table(ScenarioConfig.model_validate(ref), Path(td))
        g._write_json(path, {"reference_config_hash": ref_hash, "lanes": out[net]})
    return out


def run_probe(
    plan: ProbePlan,
    root: Path,
    *,
    procs: int,
    mem_per_run_gb: float,
    keep_trajectories: bool = False,
) -> tuple[int, int]:
    """Run every pending (network, pair, seed); returns ``(n_run, n_failed)``."""
    spec = plan.specs["as_built"]
    g.check_populations(spec, sorted({k for k, _ in plan.pairs}))
    ol_path = g._observed_lanes_path(spec, None)
    assert ol_path is not None
    root.mkdir(parents=True, exist_ok=True)
    lanes_for(plan, root)
    ctx = _context(plan, ol_path)
    pending = [
        (f"{net}/{name}", plan.configs[net][name], s, str(root), keep_trajectories, ctx)
        for net in plan.specs
        for name in plan.names
        for s in plan.seeds
        if not (_run_dir(root, net, name, plan.hashes[net][name], s) / g.READINGS).is_file()
    ]
    total, avail = g.memory_gb()
    n_procs = g.procs_for(procs, len(pending), mem_per_run_gb, avail)
    print(
        f"{plan.n_runs} runs; {len(pending)} pending; pool {n_procs} (requested {procs}, "
        f"{mem_per_run_gb:g} GB/run, available {avail if avail is None else round(avail, 1)} GB of "
        f"{total if total is None else round(total, 1)} GB)",
        flush=True,
    )
    n_fail = 0
    t0 = time.perf_counter()
    if pending:
        with mp.get_context("spawn").Pool(n_procs, maxtasksperchild=1) as pool:
            for i, (name, seed, ok, err) in enumerate(pool.imap_unordered(g._worker, pending), 1):
                if not ok:
                    n_fail += 1
                    print(f"  FAIL {name} seed={seed}: {err}", flush=True)
                print(
                    f"  {i}/{len(pending)} {name} seed={seed} ({time.perf_counter() - t0:.0f} s)",
                    flush=True,
                )
    return len(pending), n_fail


# --- analysis ------------------------------------------------------------------------------


def reverse_station_shares(observed: dict[str, Any], stations: tuple[str, ...]) -> dict[str, Any]:
    """The targets with the named stations' observed lane shares read in reverse (lane k ↔ n+1-k)."""
    out = copy.deepcopy(observed)
    for st in out["lane_use"]["stations_compared"]:
        if st["id"] in stations:
            n = int(st["lanes"])
            st["shares"] = {str(k): st["shares"][str(n + 1 - k)] for k in range(1, n + 1)}
    return out


def already_corrected(observed_lanes: dict[str, Any], stations: tuple[str, ...]) -> tuple[str, ...]:
    """The ``stations`` whose shares the observed-lanes artifact already stores in reverse.

    ``calibrate_driver_grid.build_observed_lanes`` remaps a station named by
    ``--reverse-lane-order`` (top-level ``lane_order.reversed_by_reviewer``)
    or found reversed by the data-quality report under
    ``--remap-reversed-lanes``, and marks it ``lane_order.iris_labels_reversed``;
    such a station must not be reversed again.
    """
    order = observed_lanes.get("lane_order") or {}
    done = {str(x) for x in order.get("reversed_by_reviewer") or ()}
    for st in observed_lanes.get("stations") or ():
        if (st.get("lane_order") or {}).get("iris_labels_reversed"):
            done.add(str(st.get("id")))
    return tuple(sid for sid in stations if sid in done)


def corrected_variants(
    variants: dict[str, dict[str, Any]], to_reverse: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    """``variants`` plus a ``<key>_s791_reversed`` copy of each with ``to_reverse`` reversed.

    ``to_reverse`` excludes the stations the targets already store corrected
    (:func:`already_corrected`), so a correction is never applied twice.
    """
    return variants | {
        f"{k}_s791_reversed": reverse_station_shares(v, to_reverse) for k, v in variants.items()
    }


def restrict_compared(observed: dict[str, Any], ids: set[str]) -> dict[str, Any]:
    """The targets with only the compared stations in ``ids``."""
    out = copy.deepcopy(observed)
    out["lane_use"]["stations_compared"] = [
        s for s in out["lane_use"]["stations_compared"] if s["id"] in ids
    ]
    return out


def station_shares(
    reads: list[dict[str, Any]], ctx_stations: list[dict[str, Any]], segs: list[Any]
) -> dict[str, Any]:
    """Seed-summed crossings at every observed station by IRIS lane (SUMO lane + 1), with shares."""
    out: dict[str, Any] = {}
    for st in ctx_stations:
        sid = st["id"]
        counts: dict[int, float] = {}
        for r in reads:
            for lane, c in r["crossings_by_station"].get(sid, {}).items():
                counts[int(lane) + 1] = counts.get(int(lane) + 1, 0.0) + float(c)
        n_sim = lanes_at([float(st["x_m"])], segs)[0]
        sh = shares(counts) if sum(counts.values()) > 0 else {}
        out[sid] = {
            "x_m": st["x_m"],
            "sim_lanes": None if not math.isfinite(n_sim) else int(n_sim),
            "counts_by_iris_lane": {str(k): counts[k] for k in sorted(counts)},
            "shares_by_iris_lane": {str(k): sh[k] for k in sorted(sh)},
        }
    return out


def _reproduction(plan: ProbePlan, root: Path, grid_root: Path) -> dict[str, dict[str, str]]:
    """The as-built readings at the grid's seeds against the committed grid readings."""
    out: dict[str, dict[str, str]] = {}
    for name in plan.names:
        h = plan.hashes["as_built"][name]
        row: dict[str, str] = {}
        for s in plan.seeds:
            committed = grid_root / name / h / str(s) / g.READINGS
            ours = _run_dir(root, "as_built", name, h, s) / g.READINGS
            if not committed.is_file():
                continue
            if not ours.is_file():
                row[str(s)] = "not run"
                continue
            a, b = json.loads(committed.read_text()), json.loads(ours.read_text())
            same = (
                a["crossings_by_station"] == b["crossings_by_station"]
                and a["discharge_windows"] == b["discharge_windows"]
            )
            row[str(s)] = "identical" if same else "differs"
        out[name] = row
    return out


def _band_distance(x: float, lo: float, hi: float) -> float:
    return 0.0 if lo <= x <= hi else min(abs(x - lo), abs(x - hi))


def expectations(rows: dict[str, dict[str, Any]], names: list[str]) -> list[dict[str, Any]]:
    """docs/I94_LANE_SHARES.md §6.3's expected results if §4 is right, evaluated per pair."""
    out: list[dict[str, Any]] = []

    def share(net: str, name: str, sid: str, lane: int) -> float | None:
        st = rows.get(net, {}).get(name, {}).get("stations", {}).get(sid)
        if not st:
            return None
        v = st["shares_by_iris_lane"].get(str(lane))
        return None if v is None else float(v)

    for name in names:
        for sid in ("S791", "S97"):
            a, b = share("as_built", name, sid, 3), share("netfix", name, sid, 3)
            out.append(
                {
                    "pair": name,
                    "statement": f"{sid}'s simulated left-lane share falls toward 40 %",
                    "as_built": a,
                    "netfix": b,
                    "met": None if a is None or b is None else abs(b - 0.40) < abs(a - 0.40),
                }
            )
        for sid in ("S1948", "S1070"):
            a, b = share("as_built", name, sid, 5), share("netfix", name, sid, 5)
            out.append(
                {
                    "pair": name,
                    "statement": f"{sid}'s simulated lane-5 share recovers toward 27-32 %",
                    "as_built": a,
                    "netfix": b,
                    "met": None
                    if a is None or b is None
                    else _band_distance(b, 0.27, 0.32) < _band_distance(a, 0.27, 0.32),
                }
            )
        rm = rows.get("netfix", {}).get(name, {}).get("scores", {}).get("own_s791_reversed", {})
        val = rm.get("lane_rmse_pp")
        out.append(
            {
                "pair": name,
                "statement": "the S791-corrected pooled lane RMSE on the fixed network falls "
                f"below the grid's minimum {GRID_MIN_CORRECTED_RMSE_PP} pp",
                "netfix": val,
                "met": None if val is None else float(val) < GRID_MIN_CORRECTED_RMSE_PP,
            }
        )
    return out


def analyze_probe(
    plan: ProbePlan,
    root: Path,
    artifact: Path,
    *,
    grid_root: Path | None = None,
    argv: list[str] | None = None,
) -> dict[str, Any]:
    """Score every (network, pair) from ``<root>`` and write ``artifact``."""
    spec = plan.specs["as_built"]
    ol_path = g._observed_lanes_path(spec, None)
    assert ol_path is not None
    lanes = {net: json.loads((root / net / g.LANES).read_text())["lanes"] for net in plan.specs}
    ctx = _context(plan, ol_path)
    # a station the observed-lanes artifact already stores corrected is not reversed again
    pre_corrected = already_corrected(json.loads(ol_path.read_text()), REVERSED_STATIONS)
    to_reverse = tuple(sid for sid in REVERSED_STATIONS if sid not in pre_corrected)
    observed: dict[str, dict[str, Any]] = {}
    for net, ref in plan.references.items():
        cfg = ScenarioConfig.model_validate(ref)
        observed[net] = g.observed_detectors(
            plan.specs[net], cfg, g.segments_of(lanes[net]), ol_path
        )
    compared = {
        net: {s["id"] for s in o["lane_use"]["stations_compared"]} for net, o in observed.items()
    }
    common = set.intersection(*compared.values()) if compared else set()
    rows: dict[str, dict[str, Any]] = {}
    for net in plan.specs:
        segs = g.segments_of(lanes[net])
        rows[net] = {}
        for name in plan.names:
            h = plan.hashes[net][name]
            reads = [
                json.loads(p.read_text())
                for s in plan.seeds
                if (p := _run_dir(root, net, name, h, s) / g.READINGS).is_file()
            ]
            complete = len(reads) == len(plan.seeds)
            coll = [r["run"].get("n_collisions") for r in reads]
            dep = [r["run"]["departed_share"] for r in reads if r["run"].get("departed_share")]
            row: dict[str, Any] = {
                "config_hash": h,
                "complete": complete,
                "n_seeds_read": len(reads),
                "runs": [r["run"] for r in reads],
                "n_collisions": None
                if not coll or any(c is None for c in coll)
                else int(sum(coll)),
                "departed_share_mean": float(np.mean(dep)) if dep else None,
                "stations": station_shares(reads, ctx["stations"], segs),
            }
            if complete:
                variants = {
                    "own": observed[net],
                    "common": restrict_compared(observed[net], common),
                }
                variants = corrected_variants(variants, to_reverse)
                scores: dict[str, dict[str, Any]] = {}
                for key, obs in variants.items():
                    try:
                        sc = g._score_detectors(reads, obs)
                    except (KeyError, ValueError, ZeroDivisionError) as exc:
                        scores[key] = {"score_error": f"{type(exc).__name__}: {exc}"}
                        continue
                    scores[key] = {
                        "stations": [s["id"] for s in obs["lane_use"]["stations_compared"]],
                        "lane_rmse_pp": sc["lane_rmse_pp"],
                        "lane_shares_by_station": sc["lane_shares_by_station"],
                        "discharge_veh_h": sc["discharge_veh_h"],
                        "discharge_veh_h_by_seed": sc["discharge_veh_h_by_seed"],
                        "discharge_error": sc["discharge_error"],
                    }
                row["scores"] = scores
            rows[net][name] = row
    notes = [
        "A diagnostic probe of the network defect of docs/I94_LANE_SHARES.md section 4: four "
        "seeds, the 35-minute slice, no calibration and no validation claim; seeded=False.",
        "Scores 'own' use each network's own compared stations; 'common' the stations both "
        "compare (like for like). '_s791_reversed' reads S791's observed lane shares in reverse "
        "(the data-quality lane_order check); the targets as committed are the unsuffixed scores. "
        "Correcting a target fixed in advance is the owner's decision (note section 2).",
        "Station positions are the observed stations' x on the as-built chain; the fixed "
        "network's chain is 2.3 m shorter downstream of 45782590 (junction geometry), far "
        "below the 12.5 m between two 2-Hz samples at 25 m/s.",
    ]
    if pre_corrected:
        notes.append(
            f"The observed-lanes artifact already stores {', '.join(pre_corrected)} in corrected "
            "lane order (lane_order.iris_labels_reversed): the unsuffixed scores are the "
            "corrected ones, and '_s791_reversed' does not reverse "
            f"{', '.join(pre_corrected)} a second time."
        )
    if any(r.get("n_collisions") for net in rows.values() for r in net.values()):
        notes.append("Collisions were recorded (column n_collisions): see the runs.")
    out = {
        "schema": SCHEMA,
        "note": NOTE,
        "question": "how much of the I-94 grid's lane-share error is the 6th Street left exit's "
        "lane mapping (docs/I94_LANE_SHARES.md sections 4 and 6.3)",
        "networks": {
            net: {
                "scenario": plan.specs[net].base_scenario,
                "transform": plan.specs[net].transform,
                "reference_config_hash": config_hash(ScenarioConfig.model_validate(ref)),
                "ramps_unset": _ramps_unset(ref),
                "stations_compared": sorted(compared[net]),
                "stations_not_compared": observed[net]["lane_use"]["stations_not_compared"],
                "lanes": lanes[net],
            }
            for net, ref in plan.references.items()
        },
        "pairs": [
            {"name": g.pair_name(k, kr), "k": k, "lc_keep_right": kr} for k, kr in plan.pairs
        ],
        "seeds": plan.seeds,
        "stations_compared_by_both": sorted(common),
        "targets": {
            "lane_use_source": observed["as_built"]["lane_use"]["source"],
            "lane_use_window": observed["as_built"]["lane_use"]["window"],
            "discharge": observed["as_built"]["discharge"],
            "reversed_for_the_corrected_scores": list(to_reverse),
            # additive: only when the targets already carry the correction
            **({"already_corrected_in_targets": list(pre_corrected)} if pre_corrected else {}),
        },
        "results": rows,
        "expectations": expectations(rows, plan.names),
        "reproduces_grid": _reproduction(plan, root, grid_root or g._path(GRID_ROOT)),
        "complete": all(r["complete"] for net in rows.values() for r in net.values()),
        "notes": notes,
        "provenance": {
            "script": "scripts/i94_netfix_probe.py",
            "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "code": g.git_head(),
            "code_dirty": _git_dirty(),
            "argv": list(sys.argv[1:] if argv is None else argv),
            "run_root": str(root),
            "observed_lanes": g._rel(ol_path),
            "observed_lanes_sha256": g.file_sha256(ol_path),
        },
    }
    g._write_json(artifact, out)
    return out


def _git_dirty() -> bool | None:
    """Whether :data:`CODE_PATHS` had uncommitted changes (None without git)."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no", "--", *CODE_PATHS],
            cwd=g.REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


# --- command line --------------------------------------------------------------------------


def print_plan(plan: ProbePlan) -> None:
    """``--plan-only``: what would run (nothing written)."""
    print(
        f"{len(plan.specs)} networks x {len(plan.pairs)} pairs x {len(plan.seeds)} seeds = "
        f"{plan.n_runs} runs (seeds {plan.seeds})"
    )
    for net, spec in plan.specs.items():
        print(
            f"{net}: {spec.base_scenario} (xlsfg), --ramps.unset {_ramps_unset(plan.references[net])}"
        )
        for name in plan.names:
            print(f"  {name:<12} {plan.hashes[net][name]}")
    print("nothing written (--plan-only)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python scripts/i94_netfix_probe.py",
        description=(__doc__ or "").split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--out", type=Path, default=Path("runs/p5/i94_netfix_probe"))
    ap.add_argument("--artifact", type=Path, default=Path("artifacts/i94_netfix_probe.json"))
    ap.add_argument("--procs", type=int, default=16)
    ap.add_argument("--mem-per-run-gb", type=float, default=g.SPECS["i94"].mem_gb)
    ap.add_argument("--keep-trajectories", action="store_true")
    ap.add_argument("--allow-small-machine", action="store_true", help="override the 24-GB guard")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--plan-only", action="store_true")
    mode.add_argument("--analyze-only", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    """Plan, run and analyse; returns the exit status."""
    args = build_parser().parse_args(argv)
    plan = build_probe()
    root = args.out if args.out.is_absolute() else g.REPO_ROOT / args.out
    artifact = args.artifact if args.artifact.is_absolute() else g.REPO_ROOT / args.artifact
    if args.plan_only:
        print_plan(plan)
        return 0
    if not args.analyze_only:
        total, _ = g.memory_gb()
        if not args.allow_small_machine and (total is None or total < g.LARGE_MACHINE_GB):
            print(
                f"refused: the probe runs corridor simulations ({args.mem_per_run_gb:g} GB each) "
                f"and this machine has {total if total is None else round(total, 1)} GB — cloud "
                "stage p5_i94_netfix_probe (scripts/gcp/pipeline_i24.sh); --allow-small-machine "
                "overrides",
                file=sys.stderr,
            )
            return 2
        n_run, n_fail = run_probe(
            plan,
            root,
            procs=args.procs,
            mem_per_run_gb=args.mem_per_run_gb,
            keep_trajectories=args.keep_trajectories,
        )
        print(f"{n_run} runs, {n_fail} failed", flush=True)
        if n_fail:
            print("some runs failed; the artifact is written incomplete", flush=True)
    res = analyze_probe(plan, root, artifact, argv=argv)
    for net, by_pair in res["results"].items():
        for name, row in by_pair.items():
            sc = row.get("scores", {})
            parts = [
                f"{key} {sc[key]['lane_rmse_pp']:.2f} pp"
                for key in ("own", "own_s791_reversed", "common", "common_s791_reversed")
                if key in sc and "lane_rmse_pp" in sc[key]
            ]
            disch = sc.get("own", {}).get("discharge_veh_h", {})
            print(
                f"{net:<9} {name:<12} collisions {row['n_collisions']} | "
                + " | ".join(parts)
                + (f" | S97 {disch.get('S97', float('nan')):,.0f} veh/h" if disch else "")
            )
    print(f"-> {artifact}")
    return 0 if res["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
