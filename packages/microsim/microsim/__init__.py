"""FlowState v2 microscopic tier: SUMO+IDM scenario builder and runner.

The primary physics engine (CLAUDE.md ADR-1/§3): programmatic SUMO networks,
heterogeneous IDM/EIDM fleets, libsumo stepping with pure-function controller
dispatch, and contract-compliant Parquet/JSON run artifacts.
"""

from microsim.gym_backend import MicrosimBackend
from microsim.networks import NetBundle, corridor, osm_import, ring
from microsim.runner import RunPaths, fuel_mg_to_ml, run_micro, run_replicates
from microsim.scenarios import load_scenario, resolve_scenario, run_scenario
from microsim.vehicles import (
    FleetPlan,
    build_corridor_plan,
    build_ring_plan,
    corridor_departures,
    draw_vehicle_params,
    load_idm_calibration,
    resolve_calibration_path,
    tag_avs,
    write_corridor_routes,
    write_ring_routes,
)

__all__ = [
    "FleetPlan",
    "MicrosimBackend",
    "NetBundle",
    "RunPaths",
    "build_corridor_plan",
    "build_ring_plan",
    "corridor",
    "corridor_departures",
    "draw_vehicle_params",
    "fuel_mg_to_ml",
    "load_idm_calibration",
    "load_scenario",
    "osm_import",
    "resolve_calibration_path",
    "resolve_scenario",
    "ring",
    "run_micro",
    "run_replicates",
    "run_scenario",
    "tag_avs",
    "write_corridor_routes",
    "write_ring_routes",
]

__version__ = "2.0.0"
