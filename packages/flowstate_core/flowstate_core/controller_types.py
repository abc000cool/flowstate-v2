"""Controller interface contract (docs/CONTRACTS.md §1).

Controllers are pure functions ``(obs, params, memory) -> (command, memory)``
shared verbatim between the microscopic and macroscopic tiers. They perform no
I/O, hold no global state, and use no randomness; all persistent state lives in
the JSON-serializable ``memory`` dict they thread through.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

Memory = dict[str, float]


@dataclass(frozen=True)
class ControllerObs:
    """Observation for a vehicle-level (Lagrangian) controller. SI units."""

    t: float
    """Simulation time [s]."""
    dt: float
    """Control interval [s]."""
    v: float
    """Ego speed [m/s]."""
    gap: float
    """Bumper-to-bumper gap to leader [m]; ``math.inf`` if no leader."""
    v_leader: float
    """Leader speed [m/s]; ``math.nan`` if no leader."""
    v_ref: float
    """Reference speed U [m/s] — rolling platoon mean, supplied by the runner."""
    downstream: tuple[float, ...] = field(default=())
    """Mean speeds of downstream bins [m/s], nearest bin first."""
    downstream_dx: float = 100.0
    """Spatial width of each ``downstream`` bin [m]."""


@dataclass(frozen=True)
class SegmentObs:
    """Observation for a segment-level (VSL) controller. SI units."""

    t: float
    dt: float
    seg_speed: tuple[float, ...]
    """Mean speed per segment [m/s], ordered upstream → downstream."""
    seg_density: tuple[float, ...]
    """Vehicle density per segment [veh/m], same ordering."""


VehicleControllerFn = Callable[[ControllerObs, Mapping[str, float], Memory], tuple[float, Memory]]
"""Returns (v_cmd [m/s], new_memory)."""

SegmentControllerFn = Callable[
    [SegmentObs, Mapping[str, float], Memory], tuple[tuple[float, ...], Memory]
]
"""Returns (speed limit per segment [m/s], new_memory)."""
