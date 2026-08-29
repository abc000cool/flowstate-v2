"""Controller registry (docs/CONTRACTS.md §1).

Maps registry names to pure controller functions and their literature-default
parameter dicts. Names are the ones scenario configs reference
(``AVSpec.controller`` / ``AVSpec.vsl``): ``"follower_stopper"``,
``"pi_saturation"``, ``"jad"`` (vehicle); ``"vsl_threshold"`` (segment).
Unknown names raise ``KeyError`` listing the available names.
"""

from __future__ import annotations

from typing import Final

from controllers.follower_stopper import FOLLOWER_STOPPER_DEFAULTS, follower_stopper
from controllers.jad import JAD_DEFAULTS, jad
from controllers.pi_saturation import PI_SATURATION_DEFAULTS, pi_saturation
from controllers.vsl import VSL_THRESHOLD_DEFAULTS, vsl_threshold
from flowstate_core.controller_types import SegmentControllerFn, VehicleControllerFn

ALL_VEHICLE_CONTROLLERS: Final[dict[str, VehicleControllerFn]] = {
    "follower_stopper": follower_stopper,
    "pi_saturation": pi_saturation,
    "jad": jad,
}
"""Vehicle (Lagrangian) controllers, keyed by registry name."""

ALL_SEGMENT_CONTROLLERS: Final[dict[str, SegmentControllerFn]] = {
    "vsl_threshold": vsl_threshold,
}
"""Segment (VSL) controllers, keyed by registry name."""

_DEFAULT_PARAMS: Final[dict[str, dict[str, float]]] = {
    "follower_stopper": FOLLOWER_STOPPER_DEFAULTS,
    "pi_saturation": PI_SATURATION_DEFAULTS,
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
        ``{"vehicle": (...), "segment": (...)}`` with names sorted.
    """
    return {
        "vehicle": tuple(sorted(ALL_VEHICLE_CONTROLLERS)),
        "segment": tuple(sorted(ALL_SEGMENT_CONTROLLERS)),
    }
