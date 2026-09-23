"""Infrastructure strategies: the config patch shared by the CLI and the API.

A *strategy* is the infrastructure side of a sweep axis, orthogonal to the
Lagrangian axis (AV penetration × compliance × controller): what the road
operator deploys, rather than what the controlled vehicles do.

``none``
    The scenario as calibrated.
``vsl``
    Variable speed limits: ``av.vsl = "vsl_threshold"``
    (:func:`controllers.vsl.vsl_threshold`, CLAUDE.md §4.4), applied per
    gantry segment in both tiers.
``alinea``
    Every on-ramp metered by ALINEA (:func:`controllers.ramp_meter.alinea`)
    at the corridor's per-lane critical density.
``vsl+alinea``
    Both.

The patch is a plain dict operation on a serialized
:class:`flowstate_core.config.ScenarioConfig` (``model_dump(mode="json")``),
so ``scripts/corridor_sweep.py`` and ``POST /api/v1/sweeps`` build
*identical* cell configs — and therefore identical ``config_hash`` values and
run trees — from one implementation rather than two copies that can drift.
It is idempotent, additive (``none`` never strips a strategy the scenario
already carries) and never fabricates a metering target: ALINEA's critical
density has no default anywhere in the stack (CLAUDE.md §0.1), so the caller
supplies it from the corridor's ``FDCalibration``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Literal, get_args

Strategy = Literal["none", "vsl", "alinea", "vsl+alinea"]
"""The infrastructure axis of a sweep (module docstring)."""

STRATEGIES: Final[tuple[Strategy, ...]] = get_args(Strategy)
"""Every strategy name, in the order the sweeps fan them out."""

VSL_CONTROLLER: Final[str] = "vsl_threshold"
""":attr:`flowstate_core.config.AVSpec.vsl` written by the ``vsl`` strategies."""

RAMP_METER_CONTROLLER: Final[str] = "alinea"
""":attr:`flowstate_core.config.RampMeterSpec.controller` written by ``alinea``."""


class StrategyError(ValueError):
    """A strategy cannot be applied to this scenario.

    Raised for an unknown strategy name, a missing ALINEA target density, or
    an ALINEA strategy on a network with no on-ramp to meter. A subclass of
    ``ValueError`` so existing callers that catch ``ValueError`` keep
    working; the API answers 422 with the message.
    """


def on_ramps(cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The on-ramp entries of a serialized scenario config, in config order.

    Args:
        cfg: A serialized :class:`flowstate_core.config.ScenarioConfig`.

    Returns:
        The ``network.ramps`` entries with ``kind == "on"`` — the dicts
        themselves, so a caller can patch them in place. Empty for a network
        that carries no ramps (ring and corridor networks never do).
    """
    network = cfg.get("network")
    ramps = network.get("ramps") if isinstance(network, dict) else None
    if not isinstance(ramps, list):
        return []
    return [r for r in ramps if isinstance(r, dict) and str(r.get("kind")) == "on"]


def needs_target(strategy: str) -> bool:
    """Whether ``strategy`` requires an ALINEA target density."""
    return RAMP_METER_CONTROLLER in strategy


def apply_strategy(
    cfg: dict[str, Any], strategy: str, rho_target_veh_km: float | None = None
) -> None:
    """Patch a serialized scenario config in place for one strategy.

    Idempotent and additive: applying a strategy twice yields the same
    config, and ``none`` leaves the scenario exactly as calibrated (it does
    *not* remove a VSL or a meter the scenario itself configures).

    Args:
        cfg: Serialized :class:`flowstate_core.config.ScenarioConfig`,
            modified in place.
        strategy: One of :data:`STRATEGIES`.
        rho_target_veh_km: Per-lane critical density [veh/km] written into
            every meter's ``params`` for the ALINEA strategies — from the
            corridor's ``FDCalibration`` (``fd.rho_c``, converted with
            :func:`flowstate_core.units.veh_m_to_veh_km`). Required for
            those strategies, ignored by the others.

    Raises:
        StrategyError: Unknown strategy, an ALINEA strategy without
            ``rho_target_veh_km``, or an ALINEA strategy on a network with no
            on-ramp.
    """
    if strategy not in STRATEGIES:
        raise StrategyError(f"unknown strategy {strategy!r}; choose from {', '.join(STRATEGIES)}")
    if "vsl" in strategy:
        av = cfg.setdefault("av", {})
        av["vsl"] = VSL_CONTROLLER
        av.setdefault("vsl_params", {})
    if needs_target(strategy):
        if rho_target_veh_km is None:
            raise StrategyError(
                "the alinea strategy needs rho_target_veh_km, the corridor's per-lane "
                "critical density [veh/km]; there is no default target"
            )
        ramps = on_ramps(cfg)
        if not ramps:
            raise StrategyError(
                "the alinea strategy needs at least one on-ramp in network.ramps; "
                "this scenario has none to meter"
            )
        for ramp in ramps:
            ramp["meter"] = {
                "controller": RAMP_METER_CONTROLLER,
                "params": {"rho_target_veh_km": float(rho_target_veh_km)},
            }
