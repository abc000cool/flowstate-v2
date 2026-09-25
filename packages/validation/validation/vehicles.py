"""Per-vehicle run table: ``vehicles.parquet`` (docs/CONTRACTS.md §3, WP-69).

``microsim.runner.run_micro`` writes one row per vehicle that departed beside
``trajectories.parquet``, in ``veh_id`` order. The trajectories record
corridor edges only and carry no route, and ``meta.json["ramps"]`` holds run
totals, so this table is where a replicate says which vehicle was going where:
its planned route, origin (the mainline or an on-ramp) and destination (the
corridor's end or an off-ramp), when it departed, its first and last
trajectory row (its corridor entry and where it was last seen), whether it
arrived, and whether it was given up — a weaving section gave its exit up and
rerouted it through, or the lane-end give-up (WP-71) rerouted it to its lane's
own continuation — with the planned destination kept beside the one it drove
to.

Columns (:data:`VEHICLE_COLUMNS`):

* ``veh_id`` (str), ``route`` (str: ``"main"``, ``"on<k>"``,
  ``"main_off<j>"``, ``"on<k>_off<j>"``).
* ``origin`` (str): :data:`ORIGIN_MAINLINE` or the on-ramp's label
  (``RampSpec.name``, else its attach edge as compiled — the label
  ``meta.json["weave_sections"]`` uses); ``origin_ramp`` (int): its index in
  ``meta.json["ramps"]``, ``-1`` for the mainline.
* ``destination`` (str): the PLANNED destination, :data:`DESTINATION_CORRIDOR_END`
  or the off-ramp's label; ``destination_ramp`` (int): its index, ``-1`` for
  the corridor's end.
* ``depart_planned_s`` (float, s): the fleet plan's departure time;
  ``depart_s`` (float, s): SUMO's departure time (``vehicle.getDeparture``,
  on the step grid, at or after the planned one).
* ``entry_t_s``, ``entry_x_m``, ``entry_lane``: the vehicle's first
  trajectory row — its corridor entry at the trajectory cadence (a vehicle
  enters the trajectories when first seen on a corridor edge at a sampled
  step, so ``entry_t_s >= depart_s``); null when it has no row (an on-ramp
  vehicle still on the ramp when the run ended).
* ``last_t_s``, ``last_x_m``, ``last_lane``: its last trajectory row (null
  likewise). A track that ends before the run's end left the corridor by an
  exit or at the corridor's end.
* ``arrived`` (bool): the vehicle reached the end of its route (final route)
  and left the network before the run ended.
* ``gave_up`` (bool), ``gave_up_s`` (float, s, null unless given up): the
  vehicle was rerouted away from its planned destination at that step (the
  first, if twice). Either a weaving section counted the exiter as given up
  (``weave_sections[i].n_missed_exit``) and rerouted it to the corridor's
  end, or the lane-end give-up (``OSMNetwork.lane_end_giveup_m``, WP-71;
  ``meta.json["lane_end_giveups"]``) found it halted at the end of a lane its
  route did not continue on and rerouted it to that lane's own continuation.
* ``destination_final`` (str): the destination it drove to — ``destination``
  unless it gave up. For a weave give-up, and for an exiter the lane-end rule
  sent on from a through lane, :data:`DESTINATION_CORRIDOR_END`. For a
  vehicle the lane-end rule sent off an exit-only lane, the off-ramp's label.

The four integer columns come back as pandas' nullable ``Int32`` so a lane
stays an integer when some rows have none.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final

import pandas as pd

#: File name of the table in a replicate directory (``microsim.runner.VEHICLES_FILE``).
VEHICLES_FILE: Final[str] = "vehicles.parquet"

#: ``origin`` of a vehicle that entered on the mainline.
ORIGIN_MAINLINE: Final[str] = "mainline"

#: ``destination`` of a vehicle routed to the corridor's last edge; also the
#: ``destination_final`` of a weave give-up and of a lane-end give-up of an exit.
DESTINATION_CORRIDOR_END: Final[str] = "corridor_end"

#: Columns in file order (the writer's ``microsim.runner._VEHICLES_SCHEMA``).
VEHICLE_COLUMNS: Final[tuple[str, ...]] = (
    "veh_id",
    "route",
    "origin",
    "origin_ramp",
    "destination",
    "destination_ramp",
    "depart_planned_s",
    "depart_s",
    "entry_t_s",
    "entry_x_m",
    "entry_lane",
    "last_t_s",
    "last_x_m",
    "last_lane",
    "arrived",
    "gave_up",
    "gave_up_s",
    "destination_final",
)


def vehicles_path(run_dir: str | Path) -> Path:
    """Where a replicate's per-vehicle table is (it may not exist).

    Args:
        run_dir: Replicate directory (``runs/<config_hash>/<seed>/``).

    Returns:
        ``run_dir / VEHICLES_FILE``.
    """
    return Path(run_dir) / VEHICLES_FILE


def read_vehicles(run_dir: str | Path, columns: Sequence[str] | None = None) -> pd.DataFrame:
    """Read one replicate's per-vehicle table.

    Read through an open file object, as the trajectories are
    (``microsim.demand_adapter.read_trajectories``: a bare path makes pyarrow
    build a filesystem, which fails once libsumo's libarrow is loaded).

    Args:
        run_dir: Replicate directory.
        columns: Columns to read (default: all of :data:`VEHICLE_COLUMNS`).

    Returns:
        One row per departed vehicle, in ``veh_id`` order; the integer
        columns as nullable ``Int32``.

    Raises:
        FileNotFoundError: The directory holds no table — a micro run written
            before 2026-09-25 (WP-69), or a macro-tier run.
        ValueError: A requested column is not in the contract, or the file
            lacks one.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = vehicles_path(run_dir)
    if not path.is_file():
        raise FileNotFoundError(
            f"{path}: no per-vehicle table (micro runs written before 2026-09-25, WP-69, "
            "and macro-tier runs have none)"
        )
    wanted = list(VEHICLE_COLUMNS if columns is None else columns)
    unknown = sorted(set(wanted) - set(VEHICLE_COLUMNS))
    if unknown:
        raise ValueError(f"not {VEHICLES_FILE} columns: {unknown}")
    with open(path, "rb") as f:
        parquet = pq.ParquetFile(f)
        missing = sorted(set(wanted) - set(parquet.schema_arrow.names))
        if missing:
            raise ValueError(f"{path} lacks the columns {missing}")
        table = parquet.read(columns=wanted)
    frame: pd.DataFrame = table.to_pandas(types_mapper={pa.int32(): pd.Int32Dtype()}.get)
    return frame
