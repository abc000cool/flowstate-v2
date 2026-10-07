"""Controller registry (docs/CONTRACTS.md §1).

Maps registry names to pure controller functions and their literature-default
parameter dicts. Names are the ones scenario configs reference
(``AVSpec.controller`` / ``AVSpec.vsl``): ``"follower_stopper"``,
``"pi_saturation"``, ``"jad"`` (vehicle); ``"vsl_threshold"`` (segment).
Unknown names raise ``KeyError`` listing the available names. Each vehicle
controller also declares whether it reads the downstream speed bins
(:func:`reads_downstream`).
"""

from __future__ import annotations

from typing import Final

from controllers.follower_stopper import FOLLOWER_STOPPER_DEFAULTS, follower_stopper
from controllers.follower_stopper_capacity import (
    FOLLOWER_STOPPER_CAPACITY_DEFAULTS,
    follower_stopper_capacity,
)
from controllers.jad import JAD_DEFAULTS, jad
from controllers.pi_meanfrac import PI_MEANFRAC_DEFAULTS, pi_meanfrac
from controllers.pi_saturation import PI_SATURATION_DEFAULTS, pi_saturation
from controllers.ramp_meter import ALINEA_DEFAULTS, alinea
from controllers.vsl import VSL_THRESHOLD_DEFAULTS, vsl_threshold
from flowstate_core.controller_types import (
    RampMeterFn,
    SegmentControllerFn,
    VehicleControllerFn,
)

ALL_VEHICLE_CONTROLLERS: Final[dict[str, VehicleControllerFn]] = {
    "follower_stopper": follower_stopper,
    "follower_stopper_capacity": follower_stopper_capacity,
    "pi_saturation": pi_saturation,
    "pi_meanfrac": pi_meanfrac,
    "jad": jad,
}
"""Vehicle (Lagrangian) controllers, keyed by registry name."""

VEHICLE_CONTROLLER_READS_DOWNSTREAM: Final[dict[str, bool]] = {
    "follower_stopper": False,
    "follower_stopper_capacity": False,
    "pi_saturation": False,
    "pi_meanfrac": False,
    "jad": True,
}
"""Whether each vehicle controller reads ``ControllerObs.downstream``.

The downstream bins are the wave oracle of CLAUDE.md §4.3 (mean speeds ahead
of the vehicle, with the oracle's delay and noise). The micro runner builds
them only for a controller declared ``True`` here: they cost one pass over
every vehicle per AV and control step. Every other controller receives the
contract's empty default ``()``. Every registered vehicle controller is
declared (pinned by test), and a controller that reads the bins must be
declared ``True`` or it sees no downstream field.
"""

ALL_SEGMENT_CONTROLLERS: Final[dict[str, SegmentControllerFn]] = {
    "vsl_threshold": vsl_threshold,
}
"""Segment (VSL) controllers, keyed by registry name."""

ALL_RAMP_METERS: Final[dict[str, RampMeterFn]] = {
    "alinea": alinea,
}
"""Ramp-metering controllers (``RampMeterSpec.controller``), keyed by registry name."""

_DEFAULT_PARAMS: Final[dict[str, dict[str, float]]] = {
    "follower_stopper": FOLLOWER_STOPPER_DEFAULTS,
    "follower_stopper_capacity": FOLLOWER_STOPPER_CAPACITY_DEFAULTS,
    "alinea": ALINEA_DEFAULTS,
    "pi_saturation": PI_SATURATION_DEFAULTS,
    "pi_meanfrac": PI_MEANFRAC_DEFAULTS,
    "jad": JAD_DEFAULTS,
    "vsl_threshold": VSL_THRESHOLD_DEFAULTS,
}


def get_vehicle_controller(name: str) -> VehicleControllerFn:
    """Look up a vehicle controller by registry name.

    Args:
        name: Registry name, e.g. ``"follower_stopper"``.

    Returns:
        The pure controller function (docs/CONTRACTS.md §1).

    Raises:
        KeyError: Unknown name; the message lists the available names.
    """
    try:
        return ALL_VEHICLE_CONTROLLERS[name]
    except KeyError:
        raise KeyError(
            f"unknown vehicle controller {name!r}; available: {sorted(ALL_VEHICLE_CONTROLLERS)}"
        ) from None


def reads_downstream(name: str) -> bool:
    """Whether a vehicle controller reads the downstream speed bins.

    Args:
        name: Vehicle controller registry name, e.g. ``"jad"``.

    Returns:
        ``True`` when the controller reads ``ControllerObs.downstream`` (see
        :data:`VEHICLE_CONTROLLER_READS_DOWNSTREAM`).

    Raises:
        KeyError: Unknown or undeclared name; the message lists the declared names.
    """
    try:
        return VEHICLE_CONTROLLER_READS_DOWNSTREAM[name]
    except KeyError:
        raise KeyError(
            f"unknown vehicle controller {name!r}; declared: "
            f"{sorted(VEHICLE_CONTROLLER_READS_DOWNSTREAM)}"
        ) from None


def get_segment_controller(name: str) -> SegmentControllerFn:
    """Look up a segment (VSL) controller by registry name.

    Args:
        name: Registry name, e.g. ``"vsl_threshold"``.

    Returns:
        The pure segment controller function.

    Raises:
        KeyError: Unknown name; the message lists the available names.
    """
    try:
        return ALL_SEGMENT_CONTROLLERS[name]
    except KeyError:
        raise KeyError(
            f"unknown segment controller {name!r}; available: {sorted(ALL_SEGMENT_CONTROLLERS)}"
        ) from None


def get_ramp_meter(name: str) -> RampMeterFn:
    """Look up a ramp-metering controller by registry name.

    Raises:
        KeyError: Unknown name; the message lists the available names.
    """
    try:
        return ALL_RAMP_METERS[name]
    except KeyError:
        raise KeyError(
            f"unknown ramp meter {name!r}; available: {sorted(ALL_RAMP_METERS)}"
        ) from None


def default_params(name: str) -> dict[str, float]:
    """Literature-default parameters for any registered controller.

    Args:
        name: Vehicle or segment controller registry name.

    Returns:
        A fresh copy of the controller's default parameter dict (safe to
        mutate).

    Raises:
        KeyError: Unknown name; the message lists the available names.
    """
    try:
        return dict(_DEFAULT_PARAMS[name])
    except KeyError:
        raise KeyError(
            f"unknown controller {name!r}; available: {sorted(_DEFAULT_PARAMS)}"
        ) from None


def list_controllers() -> dict[str, tuple[str, ...]]:
    """All registered controller names, grouped by kind.

    Returns:
        ``{"vehicle": (...), "segment": (...), "ramp_meter": (...)}`` with names sorted.
    """
    return {
        "vehicle": tuple(sorted(ALL_VEHICLE_CONTROLLERS)),
        "segment": tuple(sorted(ALL_SEGMENT_CONTROLLERS)),
        "ramp_meter": tuple(sorted(ALL_RAMP_METERS)),
    }
