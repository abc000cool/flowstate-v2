"""Corridor onboarding jobs (WP-F): ``POST /api/v1/corridors``.

The product path of CLAUDE.md §3.2.4 as one asynchronous job: a bounding box,
a detector export and the two boundary station ids go in; a calibrated,
runnable corridor preset and the artifacts a report is scored against come
out. It is the same pipeline the two CLIs run —
``scripts/onboard_corridor.py`` (geometry) then ``scripts/corridor_demand.py``
(demand) — with no corridor-specific code in between, which is the point: a
traffic engineer with a bbox and a detector CSV should not need a Python
session.

Stages (``api.schemas.CORRIDOR_STAGES``), each recorded on the row so
``GET /corridors/{id}`` can report progress rather than a spinner:

``extract``
    The OSM extract for the bbox, filtered to motorway ways
    (:func:`fetch_extract`, the Overpass API). It is downloaded into the job's
    own directory and then installed, by an atomic rename, outside it
    (:func:`extract_path`: under ``FLOWSTATE_DATA_DIR/osm/`` when one is
    mounted, else the results root) because the installed scenario names it
    and the runner re-imports it on every replicate. The map moves; a
    scenario must not.
``network``
    :func:`microsim.scenarios.corridor_from_bbox`: mainline chain by bearing,
    lane profile, discovered ramps, and each station's position along the
    chain. Compiled with ramp guessing and the split audit's fixes applied
    unless the request's ``ramp_guessing`` / ``split_fixes`` say otherwise
    (2026-09-24); a connection patch the fixes need is installed beside the
    extract (``<name>.splits.con.xml``, :func:`split_patch_path`) and, like
    the extract, removed again when a later stage fails.
``observations``
    The uploaded tidy detector CSV
    (:func:`calibration.loaders.detector_csv.load_detector_csv`) aggregated
    into a ``flowstate.observations/1`` artifact.
``demand``
    :func:`calibration.onboarding.calibrate_scenario`: mainline inflow from
    the upstream station, ramp flows closing the station balance bracket by
    bracket, the downstream speed boundary, warm-up and the driver
    population.
``install``
    The bundle is written under ``<results>/corridors/<id>/``, the scenario is
    stored (so ``POST /runs`` takes its ``scenario_id``) and its YAML is
    installed — again by an atomic rename — into the presets directory so it
    is selectable beside the shipped corridors.

**A name is free again after a failure.** The preset and the extract are the
two files this job writes outside its own directory that ``POST /corridors``
refuses a name over (HTTP 409); the split patch beside the extract is the
third file. A job that fails removes the ones *it* installed, never one it
found, so a failed onboarding can be retried under its own name with no
operator cleanup. A crash hard enough to skip that cleanup (SIGKILL) leaves
them behind: delete ``scenarios/<name>.yaml`` and the ``osm/<name>.osm``
(with its ``osm/<name>.splits.con.xml``, if any) under the data root, then
retry.

**What this is not.** Onboarding is not validation. The job writes a corridor
whose demand is traceable to detectors and states, per ramp, whether the
number came from a detector or from conservation; whether the result
reproduces the corridor is what ``POST /reports`` with ``observations_path``
answers, from the run artifacts (CLAUDE.md §0.1, §7).

The job lives in its own module so the WP-F surface is separable; ``api.jobs``
and ``api.main`` carry only the import and dispatch lines it needs.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from api.jobs import _calibration_error_text, _resolve
from api.schemas import CORRIDOR_STAGES
from api.settings import Settings, load_settings
from api.store import Store
from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.units import veh_h_to_veh_s

if TYPE_CHECKING:  # pragma: no cover - typing only (SUMO stays a worker import)
    from calibration.onboarding import OnboardingResult
    from microsim.scenarios import CorridorBuild

_log = logging.getLogger(__name__)

#: Files written into ``<results>/corridors/<id>/``.
SCENARIO_FILENAME = "scenario.yaml"
STATIONS_FILENAME = "stations_x.csv"
OBSERVATIONS_FILENAME = "observations.json"
DEMAND_FILENAME = "demand.json"
EXTRACT_FILENAME = "extract.osm"
SUMMARY_FILENAME = "summary.txt"

#: Columns the station table gains when the corridor is built (the same ones
#: ``scripts/onboard_corridor.py`` writes back).
STATION_OUT_COLUMNS: tuple[str, ...] = ("x_m", "offset_m", "edge_id", "lane_pos_m")

#: Mainline demand handed to the geometry step [veh/h]. It never survives:
#: the ``demand`` stage replaces ``network.inflow`` with the upstream
#: station's observed profile before anything is hashed or stored. It exists
#: only because ``corridor_from_bbox`` builds a *runnable* scenario and a
#: runnable scenario needs some inflow.
PLACEHOLDER_INFLOW_VEH_H = 6000.0

#: Default replicates of an onboarded corridor — the reporting standard
#: (``validation.metrics.MIN_REPLICATES``); a run request may override it.
DEFAULT_REPLICATES = 20

#: Default scenario master seed.
DEFAULT_SEED = 42


def extract_path(name: str, settings: Settings, results: Path | None = None) -> Path:
    """Where a corridor's OSM extract is installed for the scenario to name.

    The extract is persisted outside the job's own directory — under
    ``FLOWSTATE_DATA_DIR/osm/`` when one is mounted, else the results root —
    because the installed scenario names it and the runner re-imports it on
    every replicate. It is therefore as much a uniqueness lock as the preset
    file, and ``POST /corridors`` refuses a name whose extract already exists.

    Args:
        name: Corridor name (also the preset and extract file name).
        settings: The service settings.
        results: Results root to fall back on; defaults to
            ``settings.results_dir``.

    Returns:
        The absolute path of ``<name>.osm``.
    """
    root = settings.data_dir or (results if results is not None else settings.results_dir)
    return root / "osm" / f"{name}.osm"


def split_patch_path(name: str, settings: Settings, results: Path | None = None) -> Path:
    """Where the split fixes' connection patch is written: beside the extract.

    ``<data root>/osm/<name>.splits.con.xml`` (:func:`extract_path`'s
    directory, the pattern of :func:`microsim.scenarios.default_split_patch_path`),
    so the scenario's ``patch_files`` entry lies inside the data roots the
    runner confines it to, exactly as ``osm_file`` does.
    """
    from microsim.scenarios import default_split_patch_path

    return default_split_patch_path(extract_path(name, settings, results))


def _install_atomic(source: Path, dest: Path) -> Path:
    """Put ``source``'s bytes at ``dest`` by a rename, never a partial write.

    A crash while a shared file is being written must not leave a truncated
    map or preset at the path a scenario names, so the copy lands on a
    temporary file beside the destination and is renamed into place —
    ``os.replace`` is atomic within a filesystem.

    Args:
        source: The file to install (the job's own copy).
        dest: Where it is installed (parents created).

    Returns:
        ``dest``.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".part")
    os.close(handle)
    tmp = Path(tmp_name)
    try:
        shutil.copyfile(source, tmp)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return dest


def fetch_extract(bbox: tuple[float, float, float, float], dest: Path) -> Path:
    """Download the corridor's OSM extract to ``dest`` (Overpass).

    A module-level function so a test can replace it: every other stage runs
    for real on a fixture extract, and nothing in the test suite may touch the
    network (docs/CONTRACTS.md §8).

    Args:
        bbox: ``(south, west, north, east)`` in WGS84 degrees.
        dest: Where the extract is written (parents created).

    Returns:
        ``dest``.

    Raises:
        RuntimeError: Overpass answered with an HTTP error status, or with
            something that is not XML. The failure record this job stores
            withholds third-party messages, but this one is raised inside
            ``microsim`` and carries only the download's HTTP status line, so
            the operator can tell a server outage (retry) from a bad bbox.
    """
    from microsim.networks import _download_bbox_overpass

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_download_bbox_overpass(bbox))
    return dest


def corridor_onboarding_job(
    corridor_id: str, db_path: str | None = None, results_root: str | None = None
) -> None:
    """Onboard a corridor from a bounding box and a detector export.

    Args:
        corridor_id: Store row id (also the RQ job id).
        db_path: Store path; defaults to the environment's settings.
        results_root: Results root; defaults to the environment's settings.

    Raises:
        KeyError: The corridor row does not exist.
    """
    store, results = _resolve(db_path, results_root)
    row = store.get_corridor(corridor_id)
    if row is None:
        raise KeyError(f"corridor {corridor_id!r} not found in store {store.db_path}")
    if not store.claim_corridor(corridor_id):
        _log.info(
            "corridor %s is %s, not claimable; duplicate delivery ignored",
            corridor_id,
            row["status"],
        )
        return

    settings = load_settings()
    corridor_dir = results / "corridors" / corridor_id
    installed: list[Path] = []
    try:
        _run_onboarding(store, settings, row, corridor_dir, results, installed)
    except Exception as exc:
        # Everything this job put outside its own directory goes again: a
        # failed onboarding must not leave the preset or the extract behind,
        # because both are uniqueness locks and the name would then be
        # permanently refused (and the leftovers are a half-built corridor).
        # Only files *this* job installed are removed — never one it found.
        for path in installed:
            path.unlink(missing_ok=True)
        # The stage on the row is the one that was running when it failed;
        # a failure before the first stage marker is reported as that stage.
        current = store.get_corridor(corridor_id) or {}
        stage = str(current.get("stage") or CORRIDOR_STAGES[0])
        _log.exception("corridor onboarding %s failed at stage %s", corridor_id, stage)
        store.set_corridor_status(
            corridor_id,
            "failed",
            stage=stage,
            corridor_dir=str(corridor_dir),
            error=_calibration_error_text(exc),
            error_kind=f"corridor_{stage}",
        )


def _run_onboarding(
    store: Store,
    settings: Settings,
    row: dict[str, Any],
    corridor_dir: Path,
    results: Path,
    installed: list[Path],
) -> None:
    """The five stages; every failure is the caller's ``except`` (see above).

    ``installed`` collects the paths this job writes *outside* ``corridor_dir``
    — the OSM extract and the preset — in the order they are written, so the
    caller can undo exactly those on a failure.
    """
    from calibration.loaders.detector_csv import load_detector_csv
    from calibration.observations import Observations
    from calibration.onboarding import calibrate_scenario
    from microsim.scenarios import corridor_from_bbox

    corridor_id = str(row["id"])
    name = str(row["name"])
    params: dict[str, Any] = row["params"]
    bbox = (
        float(params["bbox"][0]),
        float(params["bbox"][1]),
        float(params["bbox"][2]),
        float(params["bbox"][3]),
    )
    corridor_dir.mkdir(parents=True, exist_ok=True)
    preset_path = settings.scenarios_dir / f"{name}.yaml"
    osm_path = extract_path(name, settings, results)
    # ``POST /corridors`` already refused both; re-checked here because the
    # check there and this job are not one transaction (two concurrent
    # requests for one name both pass it — the loser fails right here).
    for target, what in ((preset_path, "preset"), (osm_path, "OSM extract")):
        if target.exists():
            raise ValueError(
                f"a {what} named {target.name!r} already exists; onboard this corridor "
                f"under another name rather than overwriting it"
            )

    # -- extract ----------------------------------------------------------
    store.set_corridor_stage(corridor_id, "extract")
    local_extract = corridor_dir / EXTRACT_FILENAME
    fetch_extract(bbox, local_extract)
    # The scenario names ``osm_path``, so the extract has to be there before
    # the network stage runs — but it lands by an atomic rename from the job's
    # own copy, and the caller removes it again if any later stage fails.
    _install_atomic(local_extract, osm_path)
    installed.append(osm_path)

    # -- network ----------------------------------------------------------
    store.set_corridor_stage(corridor_id, "network")
    station_rows = _read_station_rows(Path(row["stations_path"]))
    # Rows created before 2026-09-24 carry neither key: the defaults apply.
    split_fixes = bool(params.get("split_fixes", True))
    patch_path = split_patch_path(name, settings, results)
    if split_fixes:
        # Registered before the build: a fix that wrote the patch and then
        # failed on the re-import must not leave it beside the extract.
        installed.append(patch_path)
    build = corridor_from_bbox(
        name,
        bbox,
        float(params["bearing_deg"]),
        inflow=veh_h_to_veh_s(PLACEHOLDER_INFLOW_VEH_H),
        workdir=corridor_dir / "build",
        stations=station_rows,
        duration_s=float(params["duration_s"]),
        seed=int(params.get("seed") or DEFAULT_SEED),
        osm_file=osm_path,
        replicates=int(params.get("replicates") or DEFAULT_REPLICATES),
        ramp_guessing=bool(params.get("ramp_guessing", True)),
        split_fixes=split_fixes,
        split_patch_path=patch_path,
    )
    _write_station_table(corridor_dir / STATIONS_FILENAME, station_rows, build)

    # -- observations -----------------------------------------------------
    store.set_corridor_stage(corridor_id, "observations")
    frame = load_detector_csv(
        Path(row["detectors_path"]), column_map=params.get("column_map") or None
    )
    observations = Observations.from_frame(
        frame,
        [_observed_station_spec(r) for r in station_rows],
        window_s=float(params["window_s"]),
        t0_local=str(params["t0_local"]),
        duration_s=float(params["duration_s"]),
        corridor=name,
        source={
            "provider": str(params.get("source") or "detector CSV upload"),
            "data_path": Path(row["detectors_path"]).name,
            "bbox": list(bbox),
            "bearing_deg": float(params["bearing_deg"]),
        },
    )

    # -- demand -----------------------------------------------------------
    store.set_corridor_stage(corridor_id, "demand")
    result = calibrate_scenario(
        build.config.model_dump(mode="json"),
        observations=observations,
        stations_x={sid: point.x_m for sid, point in build.station_x.items()},
        net_path=build.net_path,
        upstream=str(params["upstream_station"]),
        downstream=str(params["downstream_station"]),
        idm_calibration=params.get("idm_calibration") or None,
        warmup_s=float(params["warmup_s"]),
    )

    # -- install ----------------------------------------------------------
    store.set_corridor_stage(corridor_id, "install")
    cfg = ScenarioConfig.model_validate(result.scenario)
    scenario_path = corridor_dir / SCENARIO_FILENAME
    cfg.to_yaml(scenario_path)
    observations_path = corridor_dir / OBSERVATIONS_FILENAME
    result.observations.to_json(observations_path)
    result.demand["scenario"] = str(scenario_path)
    result.demand["observations"] = str(observations_path)
    (corridor_dir / DEMAND_FILENAME).write_text(json.dumps(result.demand, indent=1))
    summary = _summary(name, build, result, station_rows)
    (corridor_dir / SUMMARY_FILENAME).write_text("\n".join(summary["lines"]) + "\n")

    _install_atomic(scenario_path, preset_path)
    installed.append(preset_path)
    scenario_id = store.create_scenario(cfg.name, cfg.model_dump(mode="json"), config_hash(cfg))

    store.set_corridor_status(
        corridor_id,
        "done",
        stage="done",
        scenario_id=scenario_id,
        corridor_dir=str(corridor_dir),
        observations_path=str(observations_path),
        config_hash=result.config_hash,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Station tables
# ---------------------------------------------------------------------------


def _read_station_rows(path: Path) -> list[dict[str, Any]]:
    """The station inventory CSV as row dicts (every column preserved).

    Empty cells are dropped rather than passed on as ``""``: the downstream
    readers coerce (``float(row["x_m"])``), and an empty string there is a
    parse error about a value the operator never supplied.

    Raises:
        ValueError: The file has no header, or lacks ``station``/``id``,
            ``lat`` and ``lon``.
    """
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("stations CSV is empty (no header row)")
        fields = set(reader.fieldnames)
        if {"lat", "lon"} - fields or not ({"station", "id"} & fields):
            raise ValueError(
                f"stations CSV needs 'station' (or 'id'), 'lat' and 'lon' columns; "
                f"found {reader.fieldnames}"
            )
        return [{k: v for k, v in row.items() if v not in (None, "")} for row in reader]


def _observed_station_spec(row: dict[str, Any]) -> dict[str, Any]:
    """One inventory row as an ``ObservedStation`` mapping.

    The corridor's own projection supplies ``x_m`` (the ``demand`` stage
    rewrites every station onto the chain), so an inventory ``x_m`` measured
    along someone else's reference is deliberately not carried over.
    """
    return {k: v for k, v in row.items() if k != "x_m"}


def _write_station_table(path: Path, rows: list[dict[str, Any]], build: CorridorBuild) -> None:
    """Write the inventory back with the corridor positions filled in.

    A station farther from the centreline than the acceptance threshold gets
    its offset and nothing else: it sits on another carriageway or another
    road, and giving it an ``x_m`` would invite a comparison that is wrong.
    """
    columns = list(rows[0]) if rows else ["station", "lat", "lon"]
    columns += [c for c in STATION_OUT_COLUMNS if c not in columns]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            station_id = str(row.get("station", row.get("id", ""))).strip()
            point = build.station_x.get(station_id) or build.stations_rejected.get(station_id)
            accepted = station_id in build.station_x
            out = dict(row)
            out["x_m"] = f"{point.x_m:.1f}" if point is not None and accepted else ""
            out["offset_m"] = f"{point.offset_m:.1f}" if point is not None else ""
            out["edge_id"] = point.edge_id if point is not None and accepted else ""
            out["lane_pos_m"] = f"{point.lane_pos:.1f}" if point is not None and accepted else ""
            writer.writerow(out)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _summary(
    name: str,
    build: CorridorBuild,
    result: OnboardingResult,
    stations: list[dict[str, Any]],
) -> dict[str, Any]:
    """What the onboarding found, in the shape of ``api.schemas.CorridorSummaryOut``.

    ``stations`` is the uploaded inventory: it carries the ``lanes`` column
    the compiled lane profile is checked against
    (:meth:`microsim.scenarios.CorridorBuild.lane_check`, the pre-flight of
    docs/ONBOARDING_MNDOT.md §7), which is reported rather than enforced —
    a lane disagreement is for the operator to judge, not for the job to
    fail on.
    """
    lane_mismatches = build.lane_check(stations)
    inflow_peak = max((v for _, v in result.demand["inflow_steps"]), default=0.0) * 3600.0
    ramps = []
    for record in result.demand["ramps"]:
        on_ramp = record["kind"] == "on"
        key = "inflow_steps" if on_ramp else "exit_fraction_steps"
        peak = max((v for _, v in record[key]), default=0.0) * (3600.0 if on_ramp else 1.0)
        ramps.append(
            {
                "name": record["name"],
                "kind": record["kind"],
                "x_m": record["x_m"],
                "method": record["method"],
                "peak": peak,
                "unit": "veh/h" if on_ramp else "frac",
                "station": record.get("station"),
            }
        )
    return {
        "corridor": name,
        "chain_length_m": build.length_m,
        "n_chain_edges": len(build.chain_edges),
        "lanes_profile": [[x0, x1, lanes] for x0, x1, lanes in build.lanes_profile],
        "n_ramps": len(build.ramps),
        "stations_placed": [
            {"station": sid, "x_m": p.x_m, "offset_m": p.offset_m}
            for sid, p in sorted(build.station_x.items(), key=lambda kv: kv[1].x_m)
        ],
        "stations_rejected": [
            {"station": sid, "x_m": p.x_m, "offset_m": p.offset_m}
            for sid, p in sorted(build.stations_rejected.items(), key=lambda kv: kv[1].offset_m)
        ],
        "stations_without_chain_x": list(result.stations_without_chain_x),
        "inflow_peak_veh_h": inflow_peak,
        "ramps": ramps,
        "residuals": list(result.residuals),
        "zeroed_ramps": list(result.zeroed_ramps),
        "unmatched_detectors": list(result.unmatched_detectors),
        "lanes_compared": build.lanes_compared(stations),
        "lane_mismatches": [
            {
                "station": m.station,
                "x_m": m.x_m,
                "compiled_lanes": m.compiled_lanes,
                "inventory_lanes": m.inventory_lanes,
                "hint": m.hint,
            }
            for m in lane_mismatches
        ],
        # The split audit (docs/ONBOARDING_MNDOT.md §9) beside the lane check:
        # the one the scenario compiles, the one the fixes were derived from
        # (when any were applied), and what was applied — never enforced.
        "split_audit": [f.as_dict() for f in build.split_audit],
        "split_audit_before_fixes": (
            None
            if build.split_audit_before_fixes is None
            else [f.as_dict() for f in build.split_audit_before_fixes]
        ),
        "ramp_guessing": build.ramp_guessing,
        "split_fixes": build.split_fixes,
        "split_fixes_applied": build.split_fixes_applied,
        "split_defects_remaining": len(build.split_defects()),
        "split_patch_file": (
            None if build.split_patch_file is None else str(build.split_patch_file)
        ),
        "applied": build.applied_line(),
        "lines": [build.summary(stations), *result.summary],
    }
