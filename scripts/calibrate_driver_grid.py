"""The Amendment-1 driver calibration grid: mean ``a_max`` × ``lc_keep_right``, run, scored, chosen.

docs/FRISCO_PROTOCOL.md Amendment 1 (2026-10-06, fixed before any run that
uses it) lets two driver settings be calibrated on every corridor: the
population's mean maximum acceleration ``a_max`` at the measured mean + k·sd,
k ∈ {0, 0.25, 0.5, 0.75, 1.0} (derived populations,
``scripts/derive_population.py``), and SUMO's keep-right eagerness
``lc_keep_right`` ∈ {0, 0.1, 0.25, 0.5, 1.0}. This script builds every pair as
a variant of the corridor's reference scenario (only ``fleet.idm_calibration``
and ``fleet.lc_keep_right`` change; the name is kept, so the pair (0, 0) IS the
reference, same config hash), runs it, reads the two targets from each run,
and applies the amendment's selection rule (``validation.driver_calibration``)
to write ``artifacts/driver_calibration_<corridor>.json``: the grid table, the
rule, the chosen pair and the provenance. It chooses nothing by hand.

Corridors (:data:`SPECS`):

* ``i24`` — ``scenarios/i24_replica_flow_speedcal.yaml`` (the base of the
  phase-2 probes), one seed (``spawn_seeds(42, 20)[0]``), its one recorded
  morning (no holdout; stated in the artifact). Lane use: shares of
  vehicle-time by lane, lanes 1–4 numbered from the left (the auxiliary lane 5
  left out and the four renormalised), data x 0–5,500 m (the measured span in
  ``artifacts/i24_lane_profile.json``'s 250-m bins), 06:30–08:30 CST (sim
  t 600–7,800 s) — the 2026-09 measurement, whose recorded shares are
  30.33 / 24.16 / 20.06 / 25.46 %, quoted as 30/24/20/26 % (largest-remainder
  rounding; the script checks the quote). The Old Hickory merge area alone
  (data x 750–2,000 m) is reported beside it as a diagnostic, not scored.
  Discharge: the 2-h mean flow crossing data x 2,200 and 3,200 m (the
  validation's peak sections, ``scripts/i24_validate.py`` /
  ``scripts/i24_merge_experiment.py`` readings, ``crossings_per_window``)
  against 6,626 / 6,639 veh/h (``artifacts/i24_validation_observed.json``,
  recommended coverage).
* ``i94`` — the 35-minute slice ``scenarios/mndot_i94_wb_stpaul_weave_slice.yaml``
  under the corridor's reference configuration (``xlsfg``: weave
  ``exit_prepare`` 1, ``lane_end_giveup_m`` 7.5, ``force_guard`` on the two
  scripted merges — pipeline stage ``p2_gate_b``'s recipe, :func:`xlsfg_variant`),
  two seeds (``spawn_seeds(42, 2)``). The slice's t = 0 is 07:00 local; its
  scored window is t 300–2,100 s = 07:05–07:35. Lane use: per-lane detector
  shares of crossings at the mainline stations of the committed selection on
  the five calibration days (``artifacts/p1_rehearsal_2026-10-04/day_split.json``),
  built by ``--build-observed-lanes`` from the per-lane 30-s cache
  (``calibration.loaders.mndot_lanes``); IRIS lane 1 is the rightmost lane, so
  detector lane n is SUMO lane n − 1. A station is compared where the simulated
  cross-section has the station's lane count and lies at least 25 m from a
  lane-count change; otherwise it is listed as not compared, with the reason.
  Discharge: S97's mean flow over the scored windows (each vehicle counted
  once at its first sample at or past the station,
  ``scripts/merge_model_selfcheck.py station_flows``) against the calibration
  days' mean over the same clock windows
  (``artifacts/p1_rehearsal_2026-10-04/observations_calibration.json``).

Errors: lane use, the RMSE in percentage points over the compared lanes (all
stations' lanes pooled on I-94; seeds pooled by summing counts); discharge,
the mean over the corridor's sections of ``|q_sim − q_obs| / q_obs`` with
``q_sim`` the seed mean. The rule and how "improves" is read are in
``validation.driver_calibration`` (strictly smaller error, no noise band).

Runs: the existing runner (``microsim.runner.run_micro``) in a spawn process
pool, ``--procs`` capped by memory (``--mem-per-run-gb``: 9 GB for an I-24 run,
4 GB for the slice) against the machine's available memory. Resumable: a run
whose ``readings.json`` exists under ``<out>/<pair>/<config hash>/<seed>/`` is
skipped; each run keeps ``meta.json`` and ``readings.json`` and drops its
trajectories (``--keep-trajectories`` keeps them). The corridor grids refuse to
run on a machine with less than 24 GB (the laptop rule: the owner's laptop has 16;
``--allow-small-machine`` overrides) — they are cloud stages
(``scripts/gcp/pipeline_i24.sh`` stage 22).

Run (VM, from the repository root)::

    uv run --no-sync python scripts/calibrate_driver_grid.py --corridor i24 --plan-only
    uv run --no-sync python scripts/calibrate_driver_grid.py --corridor i24 --procs 12 \\
        --out runs/p3/grid_i24 --artifact artifacts/driver_calibration_i24.json
    uv run --no-sync python scripts/calibrate_driver_grid.py --corridor i94 \\
        --build-observed-lanes artifacts/driver_calibration_i94_observed_lanes.json \\
        --lanes-from-cache data/mndot/cache --metro-config data/mndot/config/metro_config.xml.gz \\
        --day-split artifacts/p1_rehearsal_2026-10-04/day_split.json \\
        --quality runs/p3/dq_lanes/data_quality.json --exclude-detectors 3240 --allow-fetch
    uv run --no-sync python scripts/calibrate_driver_grid.py --corridor i94 --procs 16 \\
        --out runs/p3/grid_i94 --artifact artifacts/driver_calibration_i94.json
    uv run --no-sync python scripts/calibrate_driver_grid.py --corridor i94 --analyze-only \\
        --out runs/p3/grid_i94 --artifact artifacts/driver_calibration_i94.json
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from derive_population import measured_sd, out_name

from flowstate_core.artifacts import IDMCalibration
from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from validation.driver_calibration import (
    CURRENT,
    DISCHARGE_ERROR_TEXT,
    IMPROVES_TEXT,
    K_GRID,
    KEEP_RIGHT_GRID,
    RULE_TEXT,
    GridScore,
    discharge_error,
    grid_pairs,
    is_amendment_grid,
    select_pair,
)
from validation.lane_use import (
    LaneSegment,
    crossing_lane_counts,
    distance_to_lane_change,
    lanes_at,
    largest_remainder_percent,
    left_numbered_lane,
    share_rmse_pp,
    shares,
    sumo_lane_of_right_number,
    vehicle_time_lane_counts,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

SCHEMA = "flowstate.driver_calibration/1"
OBSERVED_LANES_SCHEMA = "flowstate.observed_lane_shares/1"
PROTOCOL = "docs/FRISCO_PROTOCOL.md Amendment 1 (2026-10-06)"

READINGS = "readings.json"
MANIFEST = "MANIFEST.json"
LANES = "LANES.json"

MEASURED_POPULATION = "artifacts/idm_i24.json"
POPULATION_BASE = "artifacts/idm_i24_capacity.json"
POPULATION_STEM = "idm_i24_capacity_amax"
SHIFTED_PARAM = "a_max"

#: The corridor grids refuse to run below this much physical memory [GB] (the laptop rule).
LARGE_MACHINE_GB = 24.0
#: Share of the available memory the pool may plan for.
MEMORY_HEADROOM = 0.85

#: The paths whose uncommitted changes make the artifact's ``code_dirty`` true
#: (tests/test_scripts/test_code_dirty.py convention).
CODE_PATHS = (
    "scripts/calibrate_driver_grid.py",
    "scripts/derive_population.py",
    "scripts/merge_model_selfcheck.py",
    "scripts/i24_build_replica.py",
    "packages/validation",
    "packages/microsim",
    "packages/calibration",
    "packages/flowstate_core",
    "pyproject.toml",
    "uv.lock",
)

# --- I-24: the 2026-09 measurements this grid is scored against -----------------------------
I24_LANE_PROFILE = "artifacts/i24_lane_profile.json"
I24_OBSERVED = "artifacts/i24_validation_observed.json"
I24_INPUTS = "artifacts/i24_replica_inputs.json"
#: Lane-use segment [data x, m]: the measured span in the lane profile's 250-m bins.
I24_SPAN_DATA_X_M = (0.0, 5500.0)
#: The Old Hickory merge area (acceleration lane 751–1,899 m), reported, not scored.
I24_MERGE_AREA_DATA_X_M = (750.0, 2000.0)
#: Left-numbered mainline lanes scored (5, the auxiliary lane, is left out).
I24_LANES = (1, 2, 3, 4)
#: Every left-numbered lane read from a run (4 mainline + the auxiliary lane).
I24_ALL_LANES = (1, 2, 3, 4, 5)
#: The study window relative to the end of the 600-s warm-up: 06:30–08:30 CST.
I24_STUDY_S = (0.0, 7200.0)
I24_WARMUP_S = 600.0
I24_SECTIONS_M = (2200.0, 3200.0)
I24_WINDOW_S = 300.0
#: The amendment's quotes, checked against what the files give.
I24_QUOTED_SHARES_PCT = (30, 24, 20, 26)
I24_QUOTED_DISCHARGE_VEH_H = {"2200": 6626, "3200": 6639}


@dataclass(frozen=True)
class CorridorSpec:
    """One corridor's grid: its reference, seeds, memory and measurements.

    Attributes:
        key: Corridor key (``i24``, ``i94`` or a test's own).
        base_scenario: Scenario YAML the reference is built from.
        transform: ``None`` or ``"xlsfg"`` (:func:`xlsfg_variant`).
        n_seeds: Seeds per pair (``spawn_seeds(scenario seed, n)``).
        mem_gb: Memory one run needs [GB].
        mode: ``"i24"`` (vehicle-time lane shares, I-24 peak sections) or
            ``"detectors"`` (crossing lane shares at detector stations,
            discharge at named stations).
        guard_small_machine: Refuse to run below :data:`LARGE_MACHINE_GB`.
        population_base: The population the reference runs (k = 0).
        population_dir: Where the derived populations live.
        population_stem: Their file stem (``<stem>_k<k>.json``).
        measured_population: The measured source population (sd, origin).
        observations: Detector observations for the discharge (detectors mode).
        clock_offset_s: The scenario's t = 0 in the observations' clock
            (seconds after their ``t0_local``).
        discharge_stations: Stations whose flow is the discharge.
        discharge_upstream: ``(discharge station, its upstream station)``
            pairs: the observed queue and active-bottleneck flags of each
            scored window are reported beside the discharge (not used to
            select windows: the amendment fixes the slice's window).
        observed_lanes: Observed lane shares (``--build-observed-lanes``).
        min_clearance_m: Least distance [m] from a compared station to a
            simulated lane-count change.
        notes: Lines copied into the artifact.
    """

    key: str
    base_scenario: str
    transform: str | None
    n_seeds: int
    mem_gb: float
    mode: str
    guard_small_machine: bool = True
    population_base: str = POPULATION_BASE
    population_dir: str = "artifacts"
    population_stem: str = POPULATION_STEM
    measured_population: str = MEASURED_POPULATION
    observations: str | None = None
    clock_offset_s: float = 0.0
    discharge_stations: tuple[str, ...] = ()
    discharge_upstream: tuple[tuple[str, str], ...] = ()
    observed_lanes: str | None = None
    min_clearance_m: float = 25.0
    notes: tuple[str, ...] = field(default_factory=tuple)


SPECS: dict[str, CorridorSpec] = {
    "i24": CorridorSpec(
        key="i24",
        base_scenario="scenarios/i24_replica_flow_speedcal.yaml",
        transform=None,
        n_seeds=1,
        mem_gb=9.0,
        mode="i24",
        notes=(
            "I-24 calibrates on its one recorded morning (I-24 MOTION, 30 Nov 2022, "
            "06:30-08:30 CST), which has no holdout: the chosen pair is not validated on "
            "other data (docs/FRISCO_PROTOCOL.md Amendment 1).",
            "Lane-use segment: data x 0-5,500 m (the measured span), the segment whose "
            "observed vehicle-time shares are the amendment's 30/24/20/26 % (the 2026-09 "
            "calibration's measurement); the Old Hickory merge area alone (750-2,000 m) "
            "gives 28.0/23.4/19.0/29.6 % and is reported as a diagnostic, not scored.",
            "One seed per pair (the amendment's design): differences between pairs include "
            "seed-level noise the grid does not estimate.",
        ),
    ),
    "i94": CorridorSpec(
        key="i94",
        base_scenario="scenarios/mndot_i94_wb_stpaul_weave_slice.yaml",
        transform="xlsfg",
        n_seeds=2,
        mem_gb=4.0,
        mode="detectors",
        observations="artifacts/p1_rehearsal_2026-10-04/observations_calibration.json",
        clock_offset_s=5400.0,
        discharge_stations=("S97",),
        discharge_upstream=(("S97", "S790"),),
        observed_lanes="artifacts/driver_calibration_i94_observed_lanes.json",
        notes=(
            "I-94 calibrates on the five calibration days of the committed split "
            "(artifacts/p1_rehearsal_2026-10-04/day_split.json); the 35-minute slice "
            "(07:00-07:35 local, scored 07:05-07:35) is scored, the validation days are not "
            "used.",
            "Lane use: per-lane detector shares of crossings at the selected mainline "
            "stations; IRIS lane 1 is the rightmost lane (SUMO lane 0). Stations whose "
            "simulated cross-section has another lane count (netconvert's guessed ramp "
            "lanes) or whose observed cross-section is incomplete are not compared and are "
            "listed with the reason.",
            "Two seeds per pair (the amendment's design): differences between pairs include "
            "seed-level noise the grid does not estimate.",
        ),
    ),
}


# --- small helpers -------------------------------------------------------------------------


def _path(p: str | Path) -> Path:
    """Repository-relative paths resolve against the repository root."""
    q = Path(p)
    return q if q.is_absolute() else REPO_ROOT / q


def _rel(p: str | Path) -> str:
    try:
        return str(_path(p).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


def file_sha256(path: str | Path) -> str:
    """Hex sha256 of a file's bytes."""
    return hashlib.sha256(_path(path).read_bytes()).hexdigest()


def git_head() -> str:
    """The code's commit (``"<sha> <subject>"``), or ``"unknown"``."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H %s"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def git_dirty() -> bool | None:
    """Whether :data:`CODE_PATHS` had uncommitted changes (None without git)."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no", "--", *CODE_PATHS],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    """Atomic JSON write (``.part`` then rename); NaN is refused."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(payload, indent=1, allow_nan=False) + "\n")
    part.replace(path)


def _finite_or_none(v: float | None) -> float | None:
    return None if v is None or not math.isfinite(v) else float(v)


# --- the scenario variants ------------------------------------------------------------------


def xlsfg_variant(raw: dict[str, Any]) -> dict[str, Any]:
    """The I-94 corridor's reference configuration on a scenario dict (stage p2_gate_b's recipe).

    The shell recipe of ``scripts/gcp/pipeline_i24.sh`` stages ``p1_tune`` /
    ``p1b_strategies`` / ``p2_gate_b``, as data: the name gets ``_xlsfg``;
    every weaving section with empty ``weave_params`` gets ``{exit_prepare:
    1.0}``; the network gets ``lane_end_giveup_m: 7.5``; every scripted merge
    with empty ``merge_params`` gets ``{force_guard: 1.0}``, and there must be
    exactly two (McKnight Rd 178547099 and Hudson Rd 18207436), as the stage
    checks.

    Raises:
        ValueError: Not an OSM network, or not exactly two scripted merges set.
    """
    out = copy.deepcopy(raw)
    net = out["network"]
    if net.get("kind") != "osm":
        raise ValueError("xlsfg_variant: the reference configuration is an OSM corridor's")
    out["name"] = f"{raw['name']}_xlsfg"
    for ramp in net.get("ramps") or ():
        weave = ramp.get("weave")
        if isinstance(weave, dict) and weave.get("weave_params") == {}:
            weave["weave_params"] = {"exit_prepare": 1.0}
    net["lane_end_giveup_m"] = 7.5
    n_scripted = 0
    for ramp in net.get("ramps") or ():
        if ramp.get("merge") == "scripted" and ramp.get("merge_params") == {}:
            ramp["merge_params"] = {"force_guard": 1.0}
            n_scripted += 1
    if n_scripted != 2:
        raise ValueError(f"xlsfg_variant: expected 2 scripted merges to guard, found {n_scripted}")
    return out


def reference_raw(spec: CorridorSpec) -> dict[str, Any]:
    """The reference scenario as a dict (the base scenario, transformed).

    Raises:
        ValueError: An unknown transform, or a fleet that does not run
            ``spec.population_base``.
    """
    raw: dict[str, Any] = yaml.safe_load(_path(spec.base_scenario).read_text())
    if spec.transform == "xlsfg":
        raw = xlsfg_variant(raw)
    elif spec.transform is not None:
        raise ValueError(f"unknown transform {spec.transform!r}")
    fleet = raw.get("fleet") or {}
    if fleet.get("idm_calibration") != spec.population_base:
        raise ValueError(
            f"{spec.base_scenario}: the fleet runs {fleet.get('idm_calibration')!r}, not "
            f"{spec.population_base!r} — the grid's populations are derived from that one"
        )
    return raw


def population_for(spec: CorridorSpec, k: float) -> str:
    """The population a pair with shift ``k`` runs (the base itself at k = 0)."""
    if float(k) == 0.0:
        return spec.population_base
    return str(Path(spec.population_dir) / out_name(spec.population_stem, k))


def pair_name(k: float, kr: float) -> str:
    """Run-tree directory of a pair: ``k<k>_kr<lc_keep_right>``."""
    return f"k{float(k)}_kr{float(kr)}"


def pair_config(ref: dict[str, Any], population: str, kr: float) -> dict[str, Any]:
    """The reference with the pair's population and keep-right; nothing else changes."""
    raw = copy.deepcopy(ref)
    raw["fleet"]["idm_calibration"] = population
    raw["fleet"]["lc_keep_right"] = float(kr)
    return raw


def check_populations(
    spec: CorridorSpec, ks: list[float]
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Every pair population, verified to be the Amendment-1 derivation.

    Returns:
        ``({str(k): {k, path, sha256, a_max_mean}}, the measured source's record)``.

    Raises:
        FileNotFoundError: A derived population is missing.
        ValueError: A population is not the base with mean ``a_max`` shifted by
            k measured sd (covariance and every other mean equal).
    """
    measured = IDMCalibration.load(_path(spec.measured_population))
    base = IDMCalibration.load(_path(spec.population_base))
    sd = measured_sd(measured, SHIFTED_PARAM)
    m0 = float(measured.mean[SHIFTED_PARAM])
    out: dict[str, dict[str, Any]] = {}
    for k in ks:
        p = population_for(spec, k)
        if not _path(p).is_file():
            raise FileNotFoundError(
                f"{p} is missing: run scripts/derive_population.py (Amendment-1 populations)"
            )
        pop = IDMCalibration.load(_path(p))
        expect = m0 + float(k) * sd
        if not math.isclose(float(pop.mean[SHIFTED_PARAM]), expect, rel_tol=1e-12, abs_tol=1e-15):
            raise ValueError(f"{p}: mean {SHIFTED_PARAM} {pop.mean[SHIFTED_PARAM]} != {expect}")
        others = [n for n in base.param_names if n != SHIFTED_PARAM]
        if pop.cov != base.cov or any(pop.mean[n] != base.mean[n] for n in others):
            raise ValueError(f"{p}: covariance or another mean differs from {spec.population_base}")
        out[str(float(k))] = {
            "k": float(k),
            "path": _rel(p),
            "sha256": file_sha256(p),
            "a_max_mean": float(pop.mean[SHIFTED_PARAM]),
        }
    measured_rec = {
        "path": _rel(spec.measured_population),
        "sha256": file_sha256(spec.measured_population),
        "a_max_mean": m0,
        "a_max_sd": sd,
        "formula": "mean a_max = measured mean + k x sqrt(cov[a_max, a_max]) of the measured population",
    }
    return out, measured_rec


@dataclass(frozen=True)
class GridPlan:
    """The pairs, their configs and hashes, and the seeds."""

    spec: CorridorSpec
    reference: dict[str, Any]
    reference_hash: str
    pairs: list[tuple[float, float]]
    names: list[str]
    configs: dict[str, dict[str, Any]]
    hashes: dict[str, str]
    seeds: list[int]

    @property
    def n_runs(self) -> int:
        return len(self.pairs) * len(self.seeds)

    def manifest_pairs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": n,
                "k": p[0],
                "lc_keep_right": p[1],
                "config_hash": self.hashes[n],
                "population": self.configs[n]["fleet"]["idm_calibration"],
            }
            for n, p in zip(self.names, self.pairs, strict=True)
        ]


def build_plan(
    spec: CorridorSpec,
    k_grid: tuple[float, ...] | list[float] = K_GRID,
    keep_right_grid: tuple[float, ...] | list[float] = KEEP_RIGHT_GRID,
) -> GridPlan:
    """Every pair's scenario and config hash, and the seeds (validates every config)."""
    ref = reference_raw(spec)
    ref_cfg = ScenarioConfig.model_validate(ref)
    pairs = grid_pairs(list(k_grid), list(keep_right_grid))
    if CURRENT not in pairs:
        raise ValueError(f"the grid must contain the current setting {CURRENT}")
    names, configs, hashes = [], {}, {}
    for k, kr in pairs:
        name = pair_name(k, kr)
        raw = pair_config(ref, population_for(spec, k), kr)
        names.append(name)
        configs[name] = raw
        hashes[name] = config_hash(ScenarioConfig.model_validate(raw))
    if hashes[pair_name(*CURRENT)] != config_hash(ref_cfg):
        raise AssertionError("the pair (0, 0) must be the reference scenario itself")
    return GridPlan(
        spec=spec,
        reference=ref,
        reference_hash=config_hash(ref_cfg),
        pairs=pairs,
        names=names,
        configs=configs,
        hashes=hashes,
        seeds=spawn_seeds(int(ref_cfg.seed), spec.n_seeds),
    )


# --- memory --------------------------------------------------------------------------------


def memory_gb() -> tuple[float | None, float | None]:
    """``(total, available)`` physical memory [GB]; ``None`` where unknown."""
    try:
        info: dict[str, float] = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            info[key] = float(rest.split()[0]) / 1024.0**2  # kB -> GB
        return info.get("MemTotal"), info.get("MemAvailable")
    except (OSError, ValueError, IndexError):
        pass
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024.0**3
        return float(total), None
    except (ValueError, OSError, AttributeError):
        return None, None


def procs_for(
    requested: int, n_pending: int, mem_per_run_gb: float, available_gb: float | None
) -> int:
    """Pool size: ``--procs``, the pending runs, and what the available memory holds."""
    cap = max(1, int(requested))
    if available_gb is not None and mem_per_run_gb > 0:
        cap = min(cap, max(1, int(available_gb * MEMORY_HEADROOM // mem_per_run_gb)))
    return max(1, min(cap, max(1, n_pending)))


# --- lane geometry -------------------------------------------------------------------------


def lane_table(cfg: ScenarioConfig, workdir: Path) -> list[dict[str, Any]]:
    """The simulated corridor's lane count by linear x, built the runner's way.

    ``microsim.runner._build_network`` (the scenario's network block only — the
    grid never changes it), then each corridor edge's lane count from the
    compiled net, at the bundle's offsets (the trajectories' ``x``).
    """
    import sumolib

    from microsim.runner import _build_network

    bundle = _build_network(cfg, workdir)
    net = sumolib.net.readNet(str(bundle.net_path))
    return [
        {
            "edge": str(eid),
            "x_lo": float(off),
            "x_hi": float(off + length),
            "n_lanes": int(net.getEdge(eid).getLaneNumber()),
        }
        for eid, off, length in zip(
            bundle.edge_ids, bundle.offsets, bundle.edge_lengths, strict=True
        )
        if length > 0
    ]


def segments_of(table: list[dict[str, Any]]) -> list[LaneSegment]:
    return [LaneSegment(float(r["x_lo"]), float(r["x_hi"]), int(r["n_lanes"])) for r in table]


# --- observed sides ------------------------------------------------------------------------


def _profile_counts(
    rows: list[dict[str, Any]], bin_m: float, lo: float, hi: float
) -> dict[int, float]:
    """Vehicle-time by lane over bins inside ``[lo, hi)`` (``share × n`` of each bin)."""
    if (lo / bin_m) % 1 or (hi / bin_m) % 1:
        raise ValueError(f"segment [{lo}, {hi}) is not on the profile's {bin_m:g}-m bins")
    tot = dict.fromkeys(I24_LANES, 0.0)
    for r in rows:
        if lo <= float(r["x_lo_m"]) and float(r["x_lo_m"]) + bin_m <= hi:
            for lane in I24_LANES:
                tot[lane] += float(r["share"][str(lane)]) * float(r["n"])
    return tot


def i24_geometry() -> tuple[float, float]:
    """``sim x = a + b · data x`` (``artifacts/i24_replica_inputs.json``)."""
    g = json.loads(_path(I24_INPUTS).read_text())["geometry"]["sim_x_of_data_x"]
    return float(g["a"]), float(g["b"])


def observed_i24() -> dict[str, Any]:
    """The I-24 targets from committed artifacts (module docstring)."""
    prof = json.loads(_path(I24_LANE_PROFILE).read_text())
    rows = prof["observed"]["rows"]
    bin_m = float(prof["bin_m"])
    span = shares(_profile_counts(rows, bin_m, *I24_SPAN_DATA_X_M))
    merge = shares(_profile_counts(rows, bin_m, *I24_MERGE_AREA_DATA_X_M))
    quoted = largest_remainder_percent([span[lane] for lane in I24_LANES])
    obs = json.loads(_path(I24_OBSERVED).read_text())
    sections = [float(s) for s in obs["sections_m"]]
    rec = np.asarray(obs["hourly_flows_veh_h_recommended"], dtype=float)
    q = {f"{s:g}": float(np.mean(rec[sections.index(s)])) for s in I24_SECTIONS_M}
    q_quote = {k: round(v) for k, v in q.items()}
    return {
        "lane_use": {
            "observable": "share of vehicle-time by lane (trajectory samples: the recording's "
            "5 Hz, a run's 2 Hz output)",
            "segment_data_x_m": list(I24_SPAN_DATA_X_M),
            "window": "06:30-08:30 CST (sim t 600-7,800 s)",
            "lanes": "1-4 numbered from the left (1 = leftmost, 4 = rightmost mainline); the "
            "auxiliary lane 5 left out and the four renormalised",
            "shares": {str(lane): span[lane] for lane in I24_LANES},
            "shares_quoted_pct": quoted,
            "matches_amendment_quote": tuple(quoted) == I24_QUOTED_SHARES_PCT,
            "merge_area_diagnostic": {
                "segment_data_x_m": list(I24_MERGE_AREA_DATA_X_M),
                "shares": {str(lane): merge[lane] for lane in I24_LANES},
            },
            "source": {
                "path": I24_LANE_PROFILE,
                "sha256": file_sha256(I24_LANE_PROFILE),
                "rows": "observed.rows, 250-m bins with x_lo in the segment, share x n",
            },
        },
        "discharge": {
            "observable": "2-h mean flow across each section: crossings per 5-min window "
            "(scripts/i24_build_replica.crossings_per_window, as scripts/i24_validate.py and "
            "scripts/i24_merge_experiment.py count them), averaged over the 24 windows",
            "sections_data_x_m": list(I24_SECTIONS_M),
            "window": "06:30-08:30 CST",
            "flows_veh_h": q,
            "matches_amendment_quote": q_quote == I24_QUOTED_DISCHARGE_VEH_H,
            "error": DISCHARGE_ERROR_TEXT,
            "source": {
                "path": I24_OBSERVED,
                "sha256": file_sha256(I24_OBSERVED),
                "field": "hourly_flows_veh_h_recommended (coverage-corrected)",
            },
        },
    }


def _clock(seconds: float) -> str:
    s = round(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}"


def _parse_clock(text: str) -> float:
    h, m = text.split(":")[:2]
    return 3600.0 * int(h) + 60.0 * int(m)


def detector_window(spec: CorridorSpec, cfg: ScenarioConfig) -> dict[str, Any]:
    """The scored window in simulated time and in the observations' local clock."""
    obs = json.loads(_path(str(spec.observations)).read_text())
    t0 = _parse_clock(str(obs["t0_local"]))
    w = float(obs["window_s"])
    lo, hi = float(cfg.sim.warmup_s), float(cfg.sim.duration_s)
    first = math.ceil(lo / w - 1e-9)
    last = int(hi // w)
    return {
        "sim_t_s": [lo, hi],
        "local_start_s": t0 + spec.clock_offset_s + lo,
        "local_end_s": t0 + spec.clock_offset_s + hi,
        "local": f"{_clock(t0 + spec.clock_offset_s + lo)}-{_clock(t0 + spec.clock_offset_s + hi)}",
        "window_s": w,
        "sim_windows": list(range(first, last)),
        "obs_windows": [round((spec.clock_offset_s + k * w) / w) for k in range(first, last)],
    }


def observed_detectors(
    spec: CorridorSpec,
    cfg: ScenarioConfig,
    segments: list[LaneSegment] | None,
    observed_lanes: Path,
) -> dict[str, Any]:
    """The detector corridor's targets: lane shares (built earlier) and discharge.

    Each usable observed station is compared when the simulated lane count at
    its ``x`` equals its own and it lies ``min_clearance_m`` or more from a
    lane-count change (``segments`` known), else listed with the reason.

    Raises:
        ValueError: The observed lane shares were built for another window or
            have the wrong schema.
    """
    win = detector_window(spec, cfg)
    ol = json.loads(observed_lanes.read_text())
    if ol.get("schema") != OBSERVED_LANES_SCHEMA:
        raise ValueError(f"{observed_lanes}: not a {OBSERVED_LANES_SCHEMA} file")
    if [ol["window"]["local_start_s"], ol["window"]["local_end_s"]] != [
        win["local_start_s"],
        win["local_end_s"],
    ]:
        raise ValueError(
            f"{observed_lanes}: built for {ol['window']['local']}, the grid scores {win['local']}"
        )
    compared, not_compared = [], []
    for st in ol["stations"]:
        reason = None if st["usable"] else f"observed: {st['reason']}"
        n_sim = None
        if reason is None and segments is not None:
            n_at = lanes_at([float(st["x_m"])], segments)[0]
            n_sim = None if not math.isfinite(n_at) else int(n_at)
            clearance = distance_to_lane_change(float(st["x_m"]), segments)
            if n_sim != int(st["lanes"]):
                reason = f"simulated cross-section has {n_sim} lanes, the station {st['lanes']}"
            elif clearance < spec.min_clearance_m:
                reason = f"{clearance:.0f} m from a simulated lane-count change"
        rec = {"id": st["id"], "x_m": st["x_m"], "lanes": st["lanes"], "sim_lanes": n_sim}
        if reason is None:
            compared.append({**rec, "shares": st["shares"]})
        else:
            not_compared.append({**rec, "reason": reason})
    from calibration.transfer_check import BOTTLENECK_SPEED_DIFFERENCE_MS, BREAKDOWN_SPEED_MS

    obs = json.loads(_path(str(spec.observations)).read_text())
    upstream = dict(spec.discharge_upstream)
    disc: dict[str, Any] = {}
    for sid in spec.discharge_stations:
        flows = obs["flows_veh_h"][sid]
        speeds = obs.get("speeds_ms", {})
        windows = []
        for k_sim, k_obs in zip(win["sim_windows"], win["obs_windows"], strict=True):
            row: dict[str, Any] = {
                "sim_window": k_sim,
                "obs_window": k_obs,
                "obs_veh_h": None if flows[k_obs] is None else float(flows[k_obs]),
            }
            up = upstream.get(sid)
            if up is not None and sid in speeds and up in speeds:
                v_dn, v_up = speeds[sid][k_obs], speeds[up][k_obs]
                row["obs_speed_ms"] = {sid: v_dn, up: v_up}
                if v_dn is not None and v_up is not None:
                    row["upstream_queued"] = bool(v_up < BREAKDOWN_SPEED_MS)
                    row["active_bottleneck_window"] = bool(
                        v_up < BREAKDOWN_SPEED_MS and v_dn - v_up >= BOTTLENECK_SPEED_DIFFERENCE_MS
                    )
            windows.append(row)
        used = [w["obs_veh_h"] for w in windows if w["obs_veh_h"] is not None]
        disc[sid] = {
            "windows": windows,
            "flow_veh_h": float(np.mean(used)) if used else None,
            "upstream_station": upstream.get(sid),
            "note": (
                "per window on the calibration-day mean: upstream_queued = the upstream station "
                "below 40 mph; active_bottleneck_window = also the discharge station at least "
                "20 mph faster (docs/FRISCO_PROTOCOL.md section 5's per-window condition, without "
                "its 5-of-7 persistence); reported, not used to pick windows"
            ),
        }
    return {
        "lane_use": {
            "observable": "share of crossings by lane at each station (each vehicle once, in the "
            "lane of its first crossing; detector counts on the observed side)",
            "lanes": "right-numbered (IRIS: 1 = rightmost); detector lane n = SUMO lane n - 1",
            "window": win["local"]
            + " local (sim t "
            + "-".join(f"{v:g}" for v in win["sim_t_s"])
            + " s)",
            "stations_compared": compared,
            "stations_not_compared": not_compared,
            "rmse": "over every (station, lane) of the compared stations, pooled",
            "source": {
                "path": _rel(observed_lanes),
                "sha256": file_sha256(observed_lanes),
                "dates": ol.get("dates"),
            },
        },
        "discharge": {
            "observable": "mean flow over the scored windows, each vehicle counted once at its "
            "first sample at or past the station (scripts/merge_model_selfcheck.py station_flows)",
            "stations": disc,
            "window": win["local"],
            "error": DISCHARGE_ERROR_TEXT,
            "source": {
                "path": _rel(str(spec.observations)),
                "sha256": file_sha256(str(spec.observations)),
            },
        },
        "window": win,
    }


def build_observed_lanes(
    spec: CorridorSpec,
    cfg: ScenarioConfig,
    *,
    cache_dir: Path,
    metro_config: Path,
    day_split: Path,
    quality: Path | None,
    exclude_detectors: list[str],
    allow_fetch: bool,
    mndot_corridor: str | None = None,
    reverse_lane_order: list[str] | None = None,
    remap_reversed: bool = False,
) -> dict[str, Any]:
    """Observed per-lane shares at the selected mainline stations, calibration days.

    Per station: its live mainline and auxiliary detectors grouped by IRIS lane
    number (1 = rightmost); the lanes must be exactly 1..``lanes`` of the
    station, else it is not usable. The per-lane 30-s archive
    (``calibration.loaders.mndot_lanes.lane_frame``, 5-min windows) for the
    split's calibration dates, quality-masked with the data-quality report when
    given (``calibration.data_quality.mask_frame``: an excluded detector-day
    drops, a suspect one sets its named windows aside); per date and window a
    lane's flow is the mean of its detectors with a reading, and the
    cross-section counts only when every lane has one. Shares are of the summed
    flows over the counted date-windows.

    Lane order (docs/I94_LANE_SHARES.md §3): the IRIS labels are used as they
    are unless a station is named in ``reverse_lane_order`` (a reviewer's
    correction, as the 3240 exclusion is) or ``remap_reversed`` is set and the
    data-quality report found the station's labels reversed with strong
    evidence (``lane_order`` verdict ``reversed``); such a station's IRIS lane k
    is read as lane ``lanes + 1 - k``. Both are opt-in: the grid's targets were
    fixed in advance, so a remap is a recorded correction, never a silent one.
    Every station records the report's lane-order verdict and whether its
    labels were remapped, and the artifact lists stations the report flagged
    that were not remapped.

    Raises:
        ValueError: ``remap_reversed`` without a quality report, or a station
            in ``reverse_lane_order`` that is not in the selection.
    """
    from calibration.conservation import normalize_date
    from calibration.data_quality import QualityVerdicts, mask_frame
    from calibration.detector_inputs import frame_sha256
    from calibration.loaders.detector_csv import local_seconds
    from calibration.loaders.mndot import MetroConfig
    from calibration.loaders.mndot_lanes import lane_frame, offline_fetch

    win = detector_window(spec, cfg)
    split = json.loads(day_split.read_text())
    dates = [normalize_date(d) for d in split["calibration_dates"]]
    station_ids = list(split["selected_stations"])
    obs = json.loads(_path(str(spec.observations)).read_text())
    x_of = {s["id"]: float(s["x_m"]) for s in obs["stations"]}
    config = MetroConfig.load(metro_config)
    corr = config.corridor(mndot_corridor or str(obs["corridor"]))
    lane_of: dict[str, int] = {}
    for node in corr.nodes:
        for d in node.detectors:
            lane_of[d.name] = int(d.lane)
    reviewer = sorted({str(x) for x in (reverse_lane_order or [])})
    if unknown := [x for x in reviewer if x not in station_ids]:
        raise ValueError(f"reverse_lane_order names {unknown}, not stations of the selection")
    if remap_reversed and quality is None:
        raise ValueError("remap_reversed needs the data-quality report (quality)")
    verdicts = QualityVerdicts.from_json(quality) if quality is not None else None
    from_report = (
        set(verdicts.lanes_reversed) if (verdicts is not None and remap_reversed) else set()
    )
    frame = lane_frame(
        config,
        corr.name,
        station_ids,
        [d.replace("-", "") for d in dates],
        window_s=float(win["window_s"]),
        cache_dir=cache_dir,
        session=None if allow_fetch else offline_fetch,
        include_ramps=False,
        exclude_detectors=exclude_detectors,
    )
    quality_rec: dict[str, Any] | None = None
    if verdicts is not None:
        fm = mask_frame(
            frame,
            verdicts,
            dates=dates,
            start_s=win["local_start_s"],
            end_s=win["local_end_s"],
            stations=station_ids,
        )
        frame = fm.frame
        quality_rec = fm.record()
    secs = local_seconds(frame).to_numpy(dtype=float)
    w = float(win["window_s"])
    inside = (secs >= win["local_start_s"] - 1e-6) & (secs + w <= win["local_end_s"] + 1e-6)
    frame = frame[inside].copy()
    excluded = set(exclude_detectors)
    stations_out = []
    not_remapped: list[str] = []
    for sid in station_ids:
        st = corr.station(sid)
        dets = [d for d in st.detectors if d not in excluded]
        numbers = sorted({lane_of[d] for d in dets})
        expected = list(range(1, int(st.lanes) + 1))
        check = (verdicts.lane_order.get(sid) or {}) if verdicts is not None else {}
        flip = (sid in reviewer or sid in from_report) and numbers == expected
        n_lanes = int(st.lanes)

        def lane_no(det: str, flip: bool = flip, n_lanes: int = n_lanes) -> int:
            return n_lanes + 1 - lane_of[det] if flip else lane_of[det]

        if check.get("verdict") in ("reversed", "uncertain") and not flip:
            not_remapped.append(f"{sid} ({check.get('verdict')})")
        rec: dict[str, Any] = {
            "id": sid,
            "x_m": x_of.get(sid),
            "lanes": n_lanes,
            "detectors_by_lane": {
                str(n): sorted(d for d in dets if lane_no(d) == n)
                for n in sorted({lane_no(d) for d in dets})
            },
            "excluded_detectors": sorted(d for d in st.detectors if d in excluded),
            "lane_order": {
                "iris_labels_reversed": flip,
                "by": (
                    "reviewer (--reverse-lane-order)"
                    if flip and sid in reviewer
                    else "data-quality report (lane_order: reversed; --remap-reversed-lanes)"
                    if flip
                    else None
                ),
                "quality_check": check.get("verdict"),
            },
        }
        reason = None
        if sid not in x_of:
            reason = "no position on the simulated chain in the observations"
        elif numbers != expected:
            reason = f"detector lanes {numbers} do not cover lanes {expected}"
        counted = 0
        totals: dict[str, float] = {}
        if reason is None:
            sub = frame[frame["station"] == sid].copy()
            sub["lane_no"] = sub["lane"].map(lane_no)
            per = sub.groupby(["timestamp", "lane_no"])["flow_veh_h"].mean().unstack("lane_no")
            per = per.reindex(columns=expected)
            ok = per.notna().all(axis=1)
            counted = int(ok.sum())
            if counted == 0:
                reason = "no date-window with a reading in every lane"
            else:
                totals = {str(n): float(per.loc[ok, n].sum()) for n in expected}
        rec.update(
            {
                "usable": reason is None,
                "reason": reason,
                "n_date_windows": counted,
                "n_date_windows_possible": len(dates) * len(win["obs_windows"]),
                "mean_flow_veh_h_by_lane": (
                    {n: v / counted for n, v in totals.items()} if counted else {}
                ),
                "shares": shares(totals) if reason is None else {},
            }
        )
        stations_out.append(rec)
    return {
        "schema": OBSERVED_LANES_SCHEMA,
        "corridor": corr.name,
        "lane_numbering": "IRIS: 1 = rightmost lane; SUMO lane = IRIS lane - 1",
        "lane_order": {
            "reversed_by_reviewer": reviewer,
            "remap_reversed": bool(remap_reversed),
            "reversed_by_report": sorted(from_report),
            "flagged_not_remapped": not_remapped,
            "rule": "a station whose labels are reversed is read as lane k -> lanes + 1 - k; "
            "only stations named by the reviewer, or found reversed by the data-quality report "
            "when remap_reversed is set (docs/I94_LANE_SHARES.md section 3)",
        },
        "window": {
            "local": win["local"],
            "local_start_s": win["local_start_s"],
            "local_end_s": win["local_end_s"],
            "window_s": w,
        },
        "dates": dates,
        "stations": stations_out,
        "quality": quality_rec,
        "provenance": {
            "script": "scripts/calibrate_driver_grid.py --build-observed-lanes",
            "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "code": git_head(),
            "code_dirty": git_dirty(),
            "cache_dir": str(cache_dir),
            "allow_fetch": bool(allow_fetch),
            "metro_config": str(metro_config),
            "metro_config_sha256": file_sha256(metro_config),
            "metro_config_time_stamp": config.time_stamp,
            "day_split": str(day_split),
            "day_split_sha256": file_sha256(day_split),
            "observations": _rel(str(spec.observations)),
            "exclude_detectors": sorted(excluded),
            "reverse_lane_order": reviewer,
            "remap_reversed_lanes": bool(remap_reversed),
            "n_rows": len(frame),
            "frame_sha256": frame_sha256(frame),
        },
    }


# --- per-run readings ----------------------------------------------------------------------


def _read_trajectories(run_dir: Path) -> dict[str, np.ndarray]:
    """``t, x, lane`` and integer vehicle codes from a run's trajectories.

    Read through an open file object: a bare path makes pyarrow construct a
    ``LocalFileSystem``, which fails once libsumo's bundled libarrow is loaded
    in the process (``microsim.demand_adapter.read_trajectories``) — and the
    worker reads right after its run.
    """
    import pyarrow.parquet as pq

    with open(run_dir / "trajectories.parquet", "rb") as handle:
        tbl = pq.read_table(handle, columns=["t", "veh_id", "x", "lane"])
    codes = (
        tbl.column("veh_id")
        .combine_chunks()
        .dictionary_encode()
        .indices.to_numpy(zero_copy_only=False)
        .astype(np.int64)
    )
    return {
        "veh": codes,
        "t": tbl.column("t").to_numpy().astype(np.float64),
        "x": tbl.column("x").to_numpy().astype(np.float64),
        "lane": tbl.column("lane").to_numpy().astype(np.int64),
    }


def readings_i24(run_dir: Path, ctx: dict[str, Any]) -> dict[str, Any]:
    """Vehicle-time by lane (span and merge area) and the peak sections' 2-h flows."""
    import pandas as pd
    from i24_build_replica import crossings_per_window

    tr = _read_trajectories(run_dir)
    a, b = float(ctx["a"]), float(ctx["b"])
    xd = (tr["x"] - a) / b
    td = tr["t"] - float(ctx["warmup_s"])
    n = lanes_at(tr["x"], segments_of(ctx["lanes"]))
    # a sample whose lane index does not exist on the edge its x falls on (x exactly at an edge
    # end, where the next edge's count applies) is left out, as scripts/i24_lane_profile.py's
    # lane filter leaves it out; the count is reported
    ok = np.isfinite(n) & (tr["lane"] >= 0) & (tr["lane"] < np.nan_to_num(n, nan=0.0))
    left = left_numbered_lane(tr["lane"][ok], n[ok])
    study = (float(ctx["study_s"][0]), float(ctx["study_s"][1]))
    counts = {
        name: {
            str(k): v
            for k, v in vehicle_time_lane_counts(
                xd[ok],
                td[ok],
                left,
                x_range=(float(rng[0]), float(rng[1])),
                t_range=study,
                lanes=I24_ALL_LANES,
            ).items()
        }
        for name, rng in (("span", ctx["span"]), ("merge_area", ctx["merge_area"]))
    }
    keep = td >= 0.0
    df = pd.DataFrame({"veh_id": tr["veh"][keep], "t": td[keep], "x": xd[keep]})
    flows = {
        f"{s:g}": float(crossings_per_window(df, s, *study).mean() * 3600.0 / I24_WINDOW_S)
        for s in ctx["sections"]
    }
    return {
        "lane_time_counts": counts,
        "discharge_veh_h": flows,
        "n_samples_unmapped": int(np.count_nonzero(~ok)),
    }


def readings_detectors(run_dir: Path, ctx: dict[str, Any]) -> dict[str, Any]:
    """Crossings by SUMO lane at every observed station, and the discharge stations' windows."""
    import merge_model_selfcheck as mms

    tr = _read_trajectories(run_dir)
    xs = [float(s["x_m"]) for s in ctx["stations"]]
    t_range = (float(ctx["t_range"][0]), float(ctx["t_range"][1]))
    counts = crossing_lane_counts(tr["veh"], tr["t"], tr["x"], tr["lane"], xs, t_range=t_range)
    by_station = {
        s["id"]: {str(lane): c for lane, c in sorted(counts[float(s["x_m"])].items())}
        for s in ctx["stations"]
    }
    disc = {}
    for sid in ctx["discharge_stations"]:
        res = mms.station_flows(
            [run_dir], Path(ctx["observations"]), sid, float(ctx["clock_offset_s"])
        )
        row = res["runs"][0]
        disc[sid] = {
            "x_m": res["x_m"],
            "window_s": res["window_s"],
            "sim_veh_h": row["sim_veh_h"],
            "obs_veh_h": row["obs_veh_h"],
        }
    return {"crossings_by_station": by_station, "discharge_windows": disc}


def run_summary(meta: dict[str, Any]) -> dict[str, Any]:
    """Seed, hash, departures and collisions of one run's ``meta.json``."""
    planned = int(meta.get("n_vehicles_planned") or 0)
    departed = int(meta.get("n_vehicles_departed") or 0)
    return {
        "seed": int(meta["seed"]),
        "config_hash": meta["config_hash"],
        "n_vehicles_planned": planned,
        "n_vehicles_departed": departed,
        "departed_share": departed / planned if planned else None,
        "n_collisions": meta.get("n_collisions"),
        "wall_time_s": meta.get("wall_time_s"),
        "versions": meta.get("versions"),
    }


def _worker(
    payload: tuple[str, dict[str, Any], int, str, bool, dict[str, Any]],
) -> tuple[str, int, bool, str]:
    name, cfg_json, seed, root, keep, ctx = payload
    try:
        from microsim.runner import run_micro

        paths = run_micro(ScenarioConfig.model_validate(cfg_json), seed, Path(root) / name)
        reader = readings_i24 if ctx["mode"] == "i24" else readings_detectors
        rd = reader(paths.run_dir, ctx)
        rd["run"] = run_summary(json.loads(paths.meta.read_text()))
        _write_json(paths.run_dir / READINGS, rd)
        if not keep:
            paths.trajectories.unlink(missing_ok=True)
            paths.edges.unlink(missing_ok=True)
            shutil.rmtree(paths.run_dir / "net", ignore_errors=True)
        return name, seed, True, ""
    except Exception as exc:  # reported, never raised: one failed run must not kill the pool
        return name, seed, False, f"{type(exc).__name__}: {exc}"


# --- the grid ------------------------------------------------------------------------------


def _manifest(
    plan: GridPlan, populations: dict[str, Any], measured: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema": "flowstate.driver_grid_manifest/1",
        "corridor": plan.spec.key,
        "spec": asdict(plan.spec),
        "reference_scenario": plan.spec.base_scenario,
        "reference_transform": plan.spec.transform,
        "reference_config_hash": plan.reference_hash,
        "grid": {
            "k": sorted({p[0] for p in plan.pairs}),
            "lc_keep_right": sorted({p[1] for p in plan.pairs}),
            "is_amendment_grid": is_amendment_grid(plan.pairs),
        },
        "pairs": plan.manifest_pairs(),
        "seeds": plan.seeds,
        "populations": populations,
        "measured_population": measured,
    }


def _context(
    spec: CorridorSpec, plan: GridPlan, lanes: list[dict[str, Any]], observed_lanes: Path | None
) -> dict[str, Any]:
    cfg = ScenarioConfig.model_validate(plan.reference)
    if spec.mode == "i24":
        a, b = i24_geometry()
        if (float(cfg.sim.warmup_s), float(cfg.sim.duration_s) - float(cfg.sim.warmup_s)) != (
            I24_WARMUP_S,
            I24_STUDY_S[1],
        ):
            raise ValueError("the I-24 reference's warm-up / study window is not 600 s / 7,200 s")
        return {
            "mode": "i24",
            "lanes": lanes,
            "a": a,
            "b": b,
            "warmup_s": I24_WARMUP_S,
            "study_s": list(I24_STUDY_S),
            "span": list(I24_SPAN_DATA_X_M),
            "merge_area": list(I24_MERGE_AREA_DATA_X_M),
            "sections": list(I24_SECTIONS_M),
        }
    assert observed_lanes is not None
    ol = json.loads(observed_lanes.read_text())
    stations = [
        {"id": s["id"], "x_m": float(s["x_m"])} for s in ol["stations"] if s["x_m"] is not None
    ]
    return {
        "mode": "detectors",
        "stations": stations,
        "discharge_stations": list(spec.discharge_stations),
        "observations": str(_path(str(spec.observations))),
        "clock_offset_s": spec.clock_offset_s,
        "t_range": [float(cfg.sim.warmup_s), float(cfg.sim.duration_s)],
    }


def _observed_lanes_path(spec: CorridorSpec, override: Path | None) -> Path | None:
    if spec.mode != "detectors":
        return None
    p = (
        override
        if override is not None
        else (_path(spec.observed_lanes) if spec.observed_lanes else None)
    )
    if p is None or not p.is_file():
        raise FileNotFoundError(
            f"observed lane shares not found ({p}): build them first with --build-observed-lanes"
        )
    return p


def run_grid(
    spec: CorridorSpec,
    plan: GridPlan,
    root: Path,
    *,
    procs: int,
    mem_per_run_gb: float,
    keep_trajectories: bool = False,
    observed_lanes: Path | None = None,
) -> tuple[int, int]:
    """Run every pending (pair, seed); returns ``(n_run, n_failed)``."""
    populations, measured = check_populations(spec, sorted({p[0] for p in plan.pairs}))
    ol_path = _observed_lanes_path(spec, observed_lanes)
    root.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(plan, populations, measured)
    mpath = root / MANIFEST
    if mpath.is_file():
        old = json.loads(mpath.read_text())
        if old["pairs"] != manifest["pairs"] or old["seeds"] != manifest["seeds"]:
            raise ValueError(
                f"{mpath} holds another grid (pairs, hashes or seeds); use another --out"
            )
    else:
        manifest["created_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        _write_json(mpath, manifest)
    lpath = root / LANES
    if lpath.is_file():
        lanes = json.loads(lpath.read_text())["lanes"]
    else:
        with tempfile.TemporaryDirectory(prefix="driver_grid_net_") as td:
            lanes = lane_table(ScenarioConfig.model_validate(plan.reference), Path(td))
        _write_json(lpath, {"reference_config_hash": plan.reference_hash, "lanes": lanes})
    ctx = _context(spec, plan, lanes, ol_path)
    pending = [
        (name, plan.configs[name], s, str(root), keep_trajectories, ctx)
        for name in plan.names
        for s in plan.seeds
        if not (root / name / plan.hashes[name] / str(s) / READINGS).is_file()
    ]
    total, avail = memory_gb()
    n_procs = procs_for(procs, len(pending), mem_per_run_gb, avail)
    print(
        f"{len(plan.pairs)} pairs x {len(plan.seeds)} seed(s) = {plan.n_runs} runs; "
        f"{len(pending)} pending; pool {n_procs} (requested {procs}, {mem_per_run_gb:g} GB/run, "
        f"available {avail if avail is None else round(avail, 1)} GB of "
        f"{total if total is None else round(total, 1)} GB)",
        flush=True,
    )
    n_fail = 0
    t0 = time.perf_counter()
    if pending:
        # one run per worker process: an I-24 run holds ~9 GB, returned to the system on exit
        with mp.get_context("spawn").Pool(n_procs, maxtasksperchild=1) as pool:
            for i, (name, seed, ok, err) in enumerate(pool.imap_unordered(_worker, pending), 1):
                if not ok:
                    n_fail += 1
                    print(f"  FAIL {name} seed={seed}: {err}", flush=True)
                print(
                    f"  {i}/{len(pending)} {name} seed={seed} ({time.perf_counter() - t0:.0f} s)",
                    flush=True,
                )
    return len(pending), n_fail


def _collect(root: Path, pair: dict[str, Any], seeds: list[int]) -> list[dict[str, Any]]:
    out = []
    for s in seeds:
        p = root / pair["name"] / pair["config_hash"] / str(s) / READINGS
        if p.is_file():
            out.append(json.loads(p.read_text()))
    return out


def _score_i24(reads: list[dict[str, Any]], observed: dict[str, Any]) -> dict[str, Any]:
    span = {
        lane: float(sum(r["lane_time_counts"]["span"][str(lane)] for r in reads))
        for lane in I24_LANES
    }
    merge = {
        lane: float(sum(r["lane_time_counts"]["merge_area"][str(lane)] for r in reads))
        for lane in I24_LANES
    }
    sim_span = {str(k): v for k, v in shares(span).items()}
    sim_merge = {str(k): v for k, v in shares(merge).items()}
    obs_lu = observed["lane_use"]
    q_sim = {
        s: float(np.mean([r["discharge_veh_h"][s] for r in reads]))
        for s in observed["discharge"]["flows_veh_h"]
    }
    return {
        "lane_shares": sim_span,
        "lane_rmse_pp": share_rmse_pp(sim_span, obs_lu["shares"]),
        "merge_area_diagnostic": {
            "lane_shares": sim_merge,
            "lane_rmse_pp": share_rmse_pp(sim_merge, obs_lu["merge_area_diagnostic"]["shares"]),
        },
        "discharge_veh_h": q_sim,
        "discharge_error": discharge_error(q_sim, observed["discharge"]["flows_veh_h"]),
    }


def _score_detectors(reads: list[dict[str, Any]], observed: dict[str, Any]) -> dict[str, Any]:
    sim_sh: dict[str, float] = {}
    obs_sh: dict[str, float] = {}
    per_station: dict[str, Any] = {}
    for st in observed["lane_use"]["stations_compared"]:
        lanes = list(range(1, int(st["lanes"]) + 1))
        counts = {
            n: float(
                sum(
                    r["crossings_by_station"][st["id"]].get(str(sumo_lane_of_right_number(n)), 0)
                    for r in reads
                )
            )
            for n in lanes
        }
        sh = shares(counts)
        per_station[st["id"]] = {
            "counts": {str(n): counts[n] for n in lanes},
            "shares": {str(n): sh[n] for n in lanes},
        }
        for n in lanes:
            sim_sh[f"{st['id']}:{n}"] = sh[n]
            obs_sh[f"{st['id']}:{n}"] = float(st["shares"][str(n)])
    # A discharge station with no observed flow in any scored window has no target: it is left out
    # with the reason, as an incomplete lane-use station is; with none left the pair is unscored
    # (ValueError, which analyze records as score_error: the grid is incomplete, no pair chosen).
    stations: dict[str, list[int]] = {}
    not_scored: list[dict[str, str]] = []
    for sid, d in observed["discharge"]["stations"].items():
        idx = [w["sim_window"] for w in d["windows"] if w["obs_veh_h"] is not None]
        if d["flow_veh_h"] is None or not idx:
            not_scored.append({"id": sid, "reason": "observed: no flow in any scored window"})
        else:
            stations[sid] = idx
    if not stations:
        raise ValueError(
            "no discharge station has an observed flow in a scored window: "
            + ", ".join(s["id"] for s in not_scored)
        )
    by_seed: dict[str, list[float]] = {
        sid: [
            float(np.mean([r["discharge_windows"][sid]["sim_veh_h"][k] for k in idx]))
            for r in reads
        ]
        for sid, idx in stations.items()
    }
    q_sim = {sid: float(np.mean(v)) for sid, v in by_seed.items()}
    q_obs = {sid: float(observed["discharge"]["stations"][sid]["flow_veh_h"]) for sid in stations}
    return {
        "lane_shares_by_station": per_station,
        "lane_rmse_pp": share_rmse_pp(sim_sh, obs_sh),
        "discharge_veh_h": q_sim,
        "discharge_veh_h_by_seed": by_seed,
        "discharge_stations_not_scored": not_scored,
        "discharge_error": discharge_error(q_sim, q_obs),
    }


def analyze(
    spec: CorridorSpec,
    root: Path,
    artifact: Path,
    *,
    observed_lanes: Path | None = None,
    argv: list[str] | None = None,
) -> dict[str, Any]:
    """Score every pair from ``<root>`` and apply the rule; writes ``artifact``."""
    manifest = json.loads((root / MANIFEST).read_text())
    if manifest["corridor"] != spec.key:
        raise ValueError(f"{root} holds the {manifest['corridor']} grid, not {spec.key}")
    lanes = json.loads((root / LANES).read_text())["lanes"]
    segs = segments_of(lanes)
    ref_cfg = ScenarioConfig.model_validate(reference_raw(spec))
    fresh = build_plan(spec, manifest["grid"]["k"], manifest["grid"]["lc_keep_right"])
    if fresh.manifest_pairs() != manifest["pairs"]:
        raise ValueError(
            f"{root / MANIFEST} was written for other pair configs than this code and these "
            "scenarios build (a config hash or population differs): analyse it with the code "
            "that ran it, or rerun the grid under another --out"
        )
    if spec.mode == "i24":
        observed = observed_i24()
    else:
        ol = _observed_lanes_path(spec, observed_lanes)
        assert ol is not None
        observed = observed_detectors(spec, ref_cfg, segs, ol)
    seeds = [int(s) for s in manifest["seeds"]]
    rows, scores = [], []
    for pair in manifest["pairs"]:
        reads = _collect(root, pair, seeds)
        complete = len(reads) == len(seeds)
        row: dict[str, Any] = {
            "name": pair["name"],
            "k": pair["k"],
            "lc_keep_right": pair["lc_keep_right"],
            "config_hash": pair["config_hash"],
            "population": manifest["populations"][str(float(pair["k"]))],
            "complete": complete,
            "n_seeds_read": len(reads),
            "runs": [r["run"] for r in reads],
        }
        coll = [r["run"]["n_collisions"] for r in reads]
        row["n_collisions"] = None if any(c is None for c in coll) or not coll else int(sum(coll))
        dep = [r["run"]["departed_share"] for r in reads if r["run"]["departed_share"] is not None]
        row["departed_share_mean"] = float(np.mean(dep)) if dep else None
        row["departed_share_min"] = float(np.min(dep)) if dep else None
        rmse = derr = None
        if complete:
            try:
                sc = (
                    _score_i24(reads, observed)
                    if spec.mode == "i24"
                    else _score_detectors(reads, observed)
                )
                row.update(sc)
                rmse, derr = sc["lane_rmse_pp"], sc["discharge_error"]
            except (ValueError, KeyError, ZeroDivisionError) as exc:
                row["score_error"] = f"{type(exc).__name__}: {exc}"
        scores.append(
            GridScore(
                float(pair["k"]),
                float(pair["lc_keep_right"]),
                _finite_or_none(rmse),
                _finite_or_none(derr),
            )
        )
        rows.append(row)
    complete_grid = all(r["complete"] for r in rows) and all(s.scored for s in scores)
    selection = select_pair(scores).to_dict() if complete_grid else None
    notes = list(spec.notes)
    if not is_amendment_grid(
        [(float(p["k"]), float(p["lc_keep_right"])) for p in manifest["pairs"]]
    ):
        notes.append("NOT the Amendment-1 grid: a test or a partial grid, never a calibration.")
    if not complete_grid:
        notes.append("The grid is incomplete (a run is missing or unscored): no pair is chosen.")
    if any(r["n_collisions"] for r in rows):
        notes.append(
            "Collisions were recorded in some pairs (column n_collisions): the rule does not "
            "exclude them (Amendment 1 does not), but the acceptance gate requires zero (C5)."
        )
    out = {
        "schema": SCHEMA,
        "corridor": spec.key,
        "protocol": PROTOCOL,
        "rule": RULE_TEXT,
        "improves_reading": IMPROVES_TEXT,
        "reference": {
            "scenario": spec.base_scenario,
            "transform": spec.transform,
            "config_hash": manifest["reference_config_hash"],
            "current_setting": {"k": CURRENT[0], "lc_keep_right": CURRENT[1]},
        },
        "grid": manifest["grid"],
        "seeds": seeds,
        "populations": manifest["populations"],
        "measured_population": manifest["measured_population"],
        "targets": observed,
        "pairs": rows,
        "complete": complete_grid,
        "selection": selection,
        "notes": notes,
        "provenance": {
            "script": "scripts/calibrate_driver_grid.py",
            "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "code": git_head(),
            "code_dirty": git_dirty(),
            "argv": list(sys.argv[1:] if argv is None else argv),
            "run_root": str(root),
            "manifest_sha256": file_sha256(root / MANIFEST),
            "lanes_sha256": file_sha256(root / LANES),
            "run_versions": sorted(
                {json.dumps(run.get("versions"), sort_keys=True) for r in rows for run in r["runs"]}
            ),
        },
    }
    _write_json(artifact, out)
    return out


# --- command line --------------------------------------------------------------------------


def print_plan(plan: GridPlan, mem_per_run_gb: float, procs: int) -> None:
    """``--plan-only``: what would run (nothing written)."""
    spec = plan.spec
    cfg = ScenarioConfig.model_validate(plan.reference)
    total, avail = memory_gb()
    sim_h = plan.n_runs * float(cfg.sim.duration_s) / 3600.0
    print(
        f"corridor {spec.key}: reference {spec.base_scenario} (transform {spec.transform or 'none'}), "
        f"config hash {plan.reference_hash}"
    )
    ks = sorted({p[0] for p in plan.pairs})
    krs = sorted({p[1] for p in plan.pairs})
    print(
        f"grid: k {ks} x lc_keep_right {krs} = {len(plan.pairs)} pairs"
        + ("" if is_amendment_grid(plan.pairs) else " (NOT the Amendment-1 grid)")
    )
    print(f"seeds {plan.seeds} -> {plan.n_runs} runs, {sim_h:.1f} simulated hours")
    print(
        f"memory: {mem_per_run_gb:g} GB per run; this machine {total if total is None else round(total, 1)} GB "
        f"(available {avail if avail is None else round(avail, 1)}): pool "
        f"{procs_for(procs, plan.n_runs, mem_per_run_gb, avail)} of --procs {procs}"
    )
    print(f"{'pair':<14} {'k':>5} {'kr':>5}  population{'':<36} config_hash")
    for n, (k, kr) in zip(plan.names, plan.pairs, strict=True):
        print(
            f"{n:<14} {k:>5g} {kr:>5g}  {plan.configs[n]['fleet']['idm_calibration']:<46} {plan.hashes[n]}"
        )
    print("nothing written (--plan-only)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python scripts/calibrate_driver_grid.py",
        description=(__doc__ or "").split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--corridor", required=True, choices=sorted(SPECS))
    ap.add_argument(
        "--out", type=Path, default=None, help="run tree (default runs/driver_grid_<corridor>)"
    )
    ap.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="default artifacts/driver_calibration_<corridor>.json",
    )
    ap.add_argument("--procs", type=int, default=8)
    ap.add_argument("--mem-per-run-gb", type=float, default=None)
    ap.add_argument("--keep-trajectories", action="store_true")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--plan-only", action="store_true")
    mode.add_argument("--analyze-only", action="store_true")
    mode.add_argument(
        "--build-observed-lanes",
        type=Path,
        default=None,
        metavar="OUT",
        help="detector corridors: build the observed lane shares from the per-lane cache",
    )
    ap.add_argument("--allow-small-machine", action="store_true", help="override the 24-GB guard")
    ap.add_argument(
        "--observed-lanes",
        type=Path,
        default=None,
        help="observed lane shares (detector corridors)",
    )
    obs = ap.add_argument_group("--build-observed-lanes inputs")
    obs.add_argument("--lanes-from-cache", type=Path, default=Path("data/mndot/cache"))
    obs.add_argument(
        "--metro-config", type=Path, default=Path("data/mndot/config/metro_config.xml.gz")
    )
    obs.add_argument(
        "--day-split", type=Path, default=Path("artifacts/p1_rehearsal_2026-10-04/day_split.json")
    )
    obs.add_argument("--quality", type=Path, default=None, help="per-lane data-quality report")
    obs.add_argument("--exclude-detectors", default="", help="comma-separated detectors not read")
    obs.add_argument(
        "--allow-fetch", action="store_true", help="download detector-days missing from the cache"
    )
    obs.add_argument("--mndot-corridor", default=None, help="default: the observations' corridor")
    obs.add_argument(
        "--reverse-lane-order",
        default="",
        help="comma-separated stations whose IRIS lane labels are read reversed (a reviewer's "
        "correction, e.g. S791: docs/I94_LANE_SHARES.md section 3)",
    )
    obs.add_argument(
        "--remap-reversed-lanes",
        action="store_true",
        help="also read reversed every station the --quality report's lane-order check found "
        "reversed with strong evidence (off by default: the grid's targets were fixed in advance)",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    """Plan, build the observed lanes, run and analyse; returns the exit status."""
    args = build_parser().parse_args(argv)
    spec = SPECS[args.corridor]
    root = args.out or REPO_ROOT / "runs" / f"driver_grid_{spec.key}"
    artifact = args.artifact or REPO_ROOT / "artifacts" / f"driver_calibration_{spec.key}.json"
    mem = float(args.mem_per_run_gb if args.mem_per_run_gb is not None else spec.mem_gb)
    if args.build_observed_lanes is not None:
        if spec.mode != "detectors":
            print(
                f"--build-observed-lanes is for detector corridors; {spec.key} is not one",
                file=sys.stderr,
            )
            return 2
        cfg = ScenarioConfig.model_validate(reference_raw(spec))
        payload = build_observed_lanes(
            spec,
            cfg,
            cache_dir=args.lanes_from_cache,
            metro_config=args.metro_config,
            day_split=args.day_split,
            quality=args.quality,
            exclude_detectors=[d.strip() for d in args.exclude_detectors.split(",") if d.strip()],
            allow_fetch=bool(args.allow_fetch),
            mndot_corridor=args.mndot_corridor,
            reverse_lane_order=[x.strip() for x in args.reverse_lane_order.split(",") if x.strip()],
            remap_reversed=bool(args.remap_reversed_lanes),
        )
        _write_json(args.build_observed_lanes, payload)
        for st in payload["stations"]:
            sh = " / ".join(f"{100 * v:.1f}" for v in st["shares"].values()) or "-"
            print(
                f"  {st['id']:<6} lanes {st['lanes']} usable {st['usable']} shares (lane 1 = right) {sh}"
                + (f"  ({st['reason']})" if st["reason"] else "")
            )
        for flagged in payload["lane_order"]["flagged_not_remapped"]:
            print(
                f"  NOTE: the data-quality report flags the lane order of {flagged}; its labels "
                "were used as IRIS gives them (--reverse-lane-order / --remap-reversed-lanes)"
            )
        print(f"-> {args.build_observed_lanes}")
        return 0
    if args.analyze_only:
        res = analyze(spec, root, artifact, observed_lanes=args.observed_lanes, argv=argv)
        _print_result(res, artifact)
        return 0
    plan = build_plan(spec)
    if args.plan_only:
        print_plan(plan, mem, args.procs)
        return 0
    total, _ = memory_gb()
    if (
        spec.guard_small_machine
        and not args.allow_small_machine
        and (total is None or total < LARGE_MACHINE_GB)
    ):
        print(
            f"refused: the {spec.key} grid runs corridor simulations ({mem:g} GB each) and this "
            f"machine has {total if total is None else round(total, 1)} GB — a cloud stage "
            f"(scripts/gcp/pipeline_i24.sh stage 22); --allow-small-machine overrides",
            file=sys.stderr,
        )
        return 2
    n_run, n_fail = run_grid(
        spec,
        plan,
        root,
        procs=args.procs,
        mem_per_run_gb=mem,
        keep_trajectories=args.keep_trajectories,
        observed_lanes=args.observed_lanes,
    )
    print(f"{n_run} runs, {n_fail} failed", flush=True)
    res = analyze(spec, root, artifact, observed_lanes=args.observed_lanes, argv=argv)
    _print_result(res, artifact)
    return 0 if n_fail == 0 else 1


def _print_result(res: dict[str, Any], artifact: Path) -> None:
    print(
        f"{'pair':<14} {'lane RMSE pp':>12} {'disch. err':>10} {'collisions':>10} {'departed':>9}"
    )
    for r in res["pairs"]:
        rm = r.get("lane_rmse_pp")
        de = r.get("discharge_error")
        dep = r.get("departed_share_mean")
        print(
            f"{r['name']:<14} {'-' if rm is None else f'{rm:.3f}':>12} "
            f"{'-' if de is None else f'{de:.4f}':>10} {r['n_collisions']!s:>10} "
            f"{'-' if dep is None else f'{dep:.3f}':>9}"
        )
    sel = res["selection"]
    print("selection: " + ("none (grid incomplete)" if sel is None else sel["explanation"]))
    print(f"-> {artifact}")


if __name__ == "__main__":
    raise SystemExit(main())
