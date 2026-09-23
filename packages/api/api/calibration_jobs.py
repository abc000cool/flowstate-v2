"""Detector-observation jobs (WP-A): ``POST /api/v1/calibrations/demand``.

The third calibration kind next to ``fd`` and ``idm``. Its input is a tidy
detector CSV (the observations contract, one row per station × window) and its
output is the pair of artifacts a corridor scenario is built from:

``observations.json``
    ``flowstate.observations/1`` — the per-window observed flow, speed and
    occupancy profile of every station, aggregated over the dates in the
    upload, with its spread and per-station coverage
    (:class:`calibration.observations.Observations`).
``demand.json``
    ``flowstate.demand/1`` — the corridor inflow steps taken from the upstream
    station, plus any ramp profiles, in SI
    (:class:`calibration.demand.DemandArtifact`).

Both paths come back on ``GET /api/v1/calibrations/{id}`` under
``artifact_paths``; ``artifact_path`` (and the parsed ``artifact``) is the
observations file, because that is the one downstream comparisons read.

No fitting happens here — the job aggregates measurements and converts units.
It therefore fails loudly rather than filling a gap: a station with no
observed window in the analysed span, or an upstream station that is not in
the upload, is an error record, never an invented profile (CLAUDE.md §0.1).

The job lives in its own module so the WP-A surface is separable; ``api.jobs``
and ``api.main`` carry only the import and dispatch lines it needs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from api.jobs import (
    _DETECTOR_LOADER_KEYS,
    _OBSERVATIONS_KEYS,
    _calibration_error_text,
    _resolve,
    _subset,
)
from api.store import now_iso

if TYPE_CHECKING:  # pragma: no cover - typing only (pandas stays a worker import)
    import pandas as pd

    from calibration.observations import Observations

_log = logging.getLogger(__name__)

#: Artifact file names written into ``<results>/calibrations/<id>/``.
OBSERVATIONS_FILENAME = "observations.json"
DEMAND_FILENAME = "demand.json"

#: Defaults for the observation options a request leaves unset. The window and
#: the analysed span have no safe default in general, so these are the ones the
#: contract's own example uses and they are recorded on the artifact.
DEFAULT_WINDOW_S = 300.0
DEFAULT_T0_LOCAL = "06:00"


def demand_calibration_job(
    calibration_id: str, db_path: str | None = None, results_root: str | None = None
) -> None:
    """Build the observations + demand artifacts from an uploaded detector CSV.

    Params consumed (``api.schemas.CalibrationParams``): the detector-loader
    options ``column_map``, ``speed_unit``, ``occupancy_unit``,
    ``kind_default``; and the observation options ``window_s`` (default
    :data:`DEFAULT_WINDOW_S`), ``t0_local`` (default :data:`DEFAULT_T0_LOCAL`),
    ``duration_s`` (default: the whole span the upload covers from ``t0``),
    ``upstream_station`` (default: the mainline station with the smallest
    ``x_m``, else the first station), ``stations``, ``ramps``, ``corridor``
    and ``notes`` (recorded on the artifact's ``source``).

    Args:
        calibration_id: Store row id (also the RQ job id).
        db_path: Store path; defaults to the environment's settings.
        results_root: Results root; defaults to the environment's settings.

    Raises:
        KeyError: The calibration row does not exist.
    """
    store, results = _resolve(db_path, results_root)
    cal = store.get_calibration(calibration_id)
    if cal is None:
        raise KeyError(f"calibration {calibration_id!r} not found in store {store.db_path}")
    if not store.claim_calibration(calibration_id):
        _log.info(
            "calibration %s is %s, not claimable; duplicate delivery ignored",
            calibration_id,
            cal["status"],
        )
        return
    try:
        from calibration.demand import demand_from_observations_artifact
        from calibration.loaders.detector_csv import load_detector_csv
        from calibration.observations import Observations

        params: dict[str, Any] = cal["params"]
        data_path = Path(cal["data_path"])
        frame = load_detector_csv(data_path, **_subset(params, _DETECTOR_LOADER_KEYS))
        options = _subset(params, _OBSERVATIONS_KEYS)

        window_s = float(options.get("window_s") or frame.attrs.get("interval_s") or 0.0)
        if window_s <= 0.0:
            window_s = DEFAULT_WINDOW_S
        t0_local = str(options.get("t0_local") or DEFAULT_T0_LOCAL)
        duration_s = options.get("duration_s")
        if duration_s is None:
            duration_s = _span_from_frame(frame, window_s=window_s, t0_local=t0_local)
        stations = options.get("stations")
        source: dict[str, Any] = {
            "provider": cal["source"],
            "data_path": data_path.name,
            "fetched_at": now_iso(),
        }
        if options.get("notes"):
            source["notes"] = str(options["notes"])
        obs = Observations.from_frame(
            frame,
            stations,
            window_s=window_s,
            t0_local=t0_local,
            duration_s=float(duration_s),
            corridor=str(options.get("corridor") or cal["source"]),
            source=source,
        )
        observations_path = results / "calibrations" / calibration_id / OBSERVATIONS_FILENAME
        obs.to_json(observations_path)

        upstream = str(options.get("upstream_station") or _default_upstream(obs))
        demand = demand_from_observations_artifact(
            obs,
            upstream,
            ramps=list(options.get("ramps") or ()),
            observations_path=str(observations_path),
            step_s=window_s,
        )
        demand.to_json(observations_path.parent / DEMAND_FILENAME)
        store.set_calibration_status(calibration_id, "done", artifact_path=str(observations_path))
    except Exception as exc:
        _log.exception("demand calibration %s failed", calibration_id)
        store.set_calibration_status(calibration_id, "failed", error=_calibration_error_text(exc))


def _span_from_frame(frame: pd.DataFrame, *, window_s: float, t0_local: str) -> float:
    """Whole windows from ``t0_local`` to the last row of the upload [s].

    A request that does not state ``duration_s`` gets the span its own file
    covers — never more, so the artifact never carries windows the upload has
    no rows for.

    Raises:
        ValueError: No row of the upload sits at or after ``t0_local``.
    """
    from calibration.loaders.detector_csv import local_seconds
    from calibration.observations import parse_clock

    t0_s = parse_clock(t0_local)
    seconds = local_seconds(frame)
    after = seconds[seconds >= t0_s]
    if after.empty:
        raise ValueError(
            f"no row of the upload starts at or after t0_local {t0_local!r}; the analysed "
            f"span would be empty (pass t0_local matching the data, or duration_s)"
        )
    windows = int((float(after.max()) - t0_s) // window_s) + 1
    return windows * window_s


def _default_upstream(obs: Observations) -> str:
    """Station id used as the corridor boundary when the request names none.

    The most upstream mainline station — smallest ``x_m`` — else the first
    station of the artifact.

    Raises:
        ValueError: The artifact holds no station at all.
    """
    mainline = obs.mainline_stations()
    if mainline:
        return str(mainline[0].id)
    if obs.stations:
        return str(obs.stations[0].id)
    raise ValueError(
        "the upload produced no station series, so there is no boundary to take the "
        "corridor inflow from"
    )
