"""Scenario configuration schema (docs/CONTRACTS.md §2).

Pydantic-validated, YAML round-trippable, hashable. A ``ScenarioConfig`` plus a
seed fully determines a run; ``config_hash`` is recorded in every output
artifact so results always trace back to an exact configuration
(CLAUDE.md §0.5).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from flowstate_core.constants import (
    HETEROGENEITY_FRAC_DEFAULT,
    IDM_DEFAULTS,
    SPEED_FACTOR_BOUNDS,
    SPEED_FACTOR_DEFAULT,
)

#: Ceiling on ``ScenarioConfig.replicates``. Generous next to the ≥ 20 seeds a
#: headline claim needs (CLAUDE.md §0.6) and next to every scenario shipped
#: here (1–20), while keeping one config from queueing unbounded simulation
#: work. The API caps a *request* lower still (``api.schemas.MAX_REPLICATES``).
MAX_REPLICATES = 500


class RingNetwork(BaseModel):
    """Single-lane closed ring (Sugiyama benchmark geometry)."""

    kind: Literal["ring"] = "ring"
    circumference_m: float = Field(gt=0)
    n_vehicles: int = Field(gt=0)


class BoundarySpec(BaseModel):
    """Measured downstream boundary condition (docs/CONTRACTS.md §2).

    A time-varying speed schedule imposed at the corridor's downstream
    boundary, OUTSIDE the measured span: the micro runner appends an
    exit-buffer edge after the corridor proper and applies each step via
    ``edge.setMaxSpeed``, so congestion that originates downstream of the
    modeled section spills back into it exactly as a measured boundary
    would force. Imposing measured boundary conditions (demands, speeds,
    or bottleneck states taken from field data at the model limits) is
    standard microsimulation calibration practice — FHWA Traffic Analysis
    Toolbox Vol. III (FHWA-HOP-18-036, 2019) — and is required whenever the
    observed congestion enters the modeled section from outside it (e.g.
    the NGSIM US-101 site, docs/M2_RESULTS.md §7.3). A schedule derived
    from observations is a calibration input, not a seeded perturbation:
    it does NOT set ``seeded=True`` (the waves inside the span remain
    emergent), but its provenance must be recorded wherever it is used.
    """

    kind: Literal["speed_schedule"] = "speed_schedule"
    steps: list[tuple[float, float]] = Field(min_length=1)
    """Piecewise-constant (t_s [s, sim time], v_limit [m/s]) steps,
    time-ordered; each limit holds until the next step (the last holds to
    the end of the run)."""
    exit_buffer_m: float = Field(default=200.0, gt=0)
    """Length of the appended exit-buffer edge the limit applies to [m]."""

    @model_validator(mode="after")
    def _check_steps(self) -> Self:
        times = [t for t, _ in self.steps]
        if times != sorted(times):
            raise ValueError("boundary steps must be ordered by t_s")
        if any(v <= 0 for _, v in self.steps):
            raise ValueError("boundary speed limits must be > 0")
        return self


def _check_lane_shares(
    shares: list[float] | None, lanes: int | None, name: str = "entry_lane_shares"
) -> None:
    """Validate a per-lane share list (left to right; None = round-robin).

    Args:
        shares: The list to check, or ``None`` (always valid).
        lanes: Expected length (the network's lane count), or ``None`` when the
            lane count is only known at run time (OSM imports).
        name: Field name used in the error messages.
    """
    if shares is None:
        return
    if len(shares) < 2:
        raise ValueError(f"{name} needs at least two lanes")
    if any(s < 0.0 for s in shares) or sum(shares) <= 0.0:
        raise ValueError(f"{name} must be non-negative with a positive sum")
    if lanes is not None and len(shares) != lanes:
        raise ValueError(f"{name} has {len(shares)} entries for {lanes} lanes")


class CorridorNetwork(BaseModel):
    """Straight corridor with an upstream inflow boundary."""

    kind: Literal["corridor"] = "corridor"
    length_m: float = Field(gt=0)
    lanes: int = Field(ge=1, le=8, default=1)
    inflow: list[tuple[float, float]] = Field(min_length=1)
    """Piecewise-constant (t_start [s], inflow [veh/s]) steps, time-ordered."""
    boundary: BoundarySpec | None = None
    """Optional measured downstream boundary condition (speed schedule on an
    exit-buffer edge outside the measured span); None ⇒ free outflow."""
    entry_lane_shares: list[float] | None = None
    """Measured share of mainline entries per lane, LEFT to RIGHT (normalised
    at use; length = lane count). ``None`` (default) inserts round-robin
    across lanes. An upstream boundary carries a lane distribution as much as
    a flow: on I-24 the right lane holds 17% of vehicle-time at the entry
    against 34% in the left lane (an off-ramp has just drained it), and a
    replica that feeds a quarter of the flow into the lane the next on-ramp
    merges into queues that lane 1.5 km upstream of the gore
    (docs/I24_VALIDATION.md §0.5). Drawn per vehicle from the run's RNG."""

    @model_validator(mode="after")
    def _check_inflow(self) -> Self:
        times = [t for t, _ in self.inflow]
        if times != sorted(times):
            raise ValueError("inflow steps must be ordered by t_start")
        if any(q < 0 for _, q in self.inflow):
            raise ValueError("inflow must be >= 0")
        _check_lane_shares(self.entry_lane_shares, self.lanes)
        return self


class RampMeterSpec(BaseModel):
    """Ramp metering on an on-ramp (micro tier, 2026-09-06).

    A virtual signal at ``stop_line_m`` before the end of the ramp's last
    edge: every ramp vehicle stops there and the meter releases the first
    waiting vehicle whenever the metered headway ``3600 / rate`` has elapsed
    since the last release (one vehicle per green, the usual US practice).
    The rate comes from a pure controller in the ``controllers`` registry
    (``"alinea"``: Papageorgiou et al. 1991, integral feedback on the
    density measured on the corridor edge downstream of the merge every
    ``interval_s``), clipped to ``[rate_min_veh_h, rate_max_veh_h]``.
    ``params`` must carry the controller's target (for ALINEA the critical
    density ``rho_target_veh_km`` of the calibrated diagram); there is no
    built-in target. Releases and rates are recorded in ``meta.json``.

    Stop placement (2026-09-23, docs/LESSONS.md row 31): the stop is assigned
    once per vehicle, at its first step on any ramp edge (normally on entering
    ``edges[0]``), always at ``stop_line_m`` before the end of ``edges[-1]``.
    A vehicle already within its braking distance of the line
    (``v² / (2 b) + v · Δt``, ``b`` = the vehicle's comfortable deceleration)
    is not stopped: it passes the meter uncontrolled and is counted in
    ``meta.json["ramp_meters"][i]["n_passed_unstoppable"]``, as is any stop
    SUMO refuses as "too close to brake". A nonzero count means the line is
    too close to where vehicles enter the ramp for them all to be metered.
    """

    controller: Literal["alinea"] = "alinea"
    params: dict[str, float] = Field(default_factory=dict)
    interval_s: float = Field(default=30.0, gt=0.0)
    stop_line_m: float = Field(default=30.0, gt=0.0)
    rate_min_veh_h: float = Field(default=240.0, gt=0.0)
    rate_max_veh_h: float = Field(default=1800.0, gt=0.0)
    rate_init_veh_h: float | None = None
    """Initial rate; ``None`` starts at ``rate_max_veh_h``."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.rate_max_veh_h <= self.rate_min_veh_h:
            raise ValueError("rate_max_veh_h must exceed rate_min_veh_h")
        return self


SCRIPTED_MERGE_DEFAULTS: dict[str, float] = {
    "accept_gap_s": 0.6,
    "force_after_s": 4.0,
    "force_within_m": 80.0,
    "change_duration_s": 2.0,
    "lookahead_m": 120.0,
    # The forced change's brake-gap guard (2026-09-26, block 3, WP-93), on by
    # default since 2026-10-04 (owner decision, WP-98; config-hash policy
    # version 3); 0 reproduces the unguarded forced change of every scripted
    # merge before release 2.6 — see merge_params' docstring. A switch, not a
    # fitted value.
    "force_guard": 1.0,
}
"""Defaults of :attr:`RampSpec.merge_params` for the ``scripted`` merge."""
SCRIPTED_MERGE_KEYS = frozenset(SCRIPTED_MERGE_DEFAULTS)

WEAVE_DEFAULTS: dict[str, float] = {
    # the scripted merge's keys but its forced-change guard: the weave's
    # forced changes are always under its own (microsim.runner._weave_force_gap_ok)
    **{k: v for k, v in SCRIPTED_MERGE_DEFAULTS.items() if k != "force_guard"},
    "exit_accept_gap_s": 0.6,
    "vacate_ahead_m": 500.0,
    "vacate_max_veh_h": 0.0,
    "pair_release_s": 2.0,
    "exit_giveup_m": 5.0,
    # 2026-09-24 (block 3, WP-62): exiters asked, inside the vacate window,
    # into the lane that feeds section lane 1 — the vacate rule's mirror for
    # the exit movement; see the key's paragraph in the docstring below. A
    # switch, not a fitted value.
    "exit_prepare": 0.0,
}
"""Defaults of :attr:`WeaveSpec.weave_params`: the ``scripted`` merge's keys
but ``force_guard`` (applied to the entering movement; the weave's forced
changes are always under its own guard, ``microsim.runner._weave_force_gap_ok``)
plus ``exit_accept_gap_s``, the time gap the exiting movement accepts,
``vacate_ahead_m`` (2026-09-24, block 3, third derivation): how far upstream
of the section start a through vehicle in the weave lane is asked, once, to
move one lane left — the "through traffic keep left" signage and driver
anticipation of a weave — under SUMO's own safety check
(``microsim.runner._weave_vacate_step``). The window is measured along the
corridor chain from the section start across as many upstream edges as it
reaches (2026-09-24, block 3, the cross-edge window; until then it was
truncated to the edge before the section, and its default was the HCM 7th
ed. ch. 13 weaving segment's 500-ft upstream influence area, 150 m). The
default is now **500 m**: the influence area is where the weave's own gap
search binds, but a through driver moves left for a weave where the advance
signage tells him to — the MUTCD (2009 ed., §2E.33, Advance Guide Signs)
places the nearest advance guide sign of a freeway exit 1/2 mile (≈ 800 m)
ahead of it — so the request should be made where the through lane still
moves, not in the last 230 m between an entrance's merge and the gore.
500 m is a stated engineering choice between the two distances, not a
fitted value; both ends are in the sensitivity table (300 / 500 / 800 m,
docs/WEAVE_MODEL_PLAN.md, dated section). ``0`` disables the rule.
``vacate_max_veh_h`` (2026-09-24, block 3, the rule
re-derived): the most through vehicles the rule may ask into the target
lane per hour, counted over the last 60 s; a positive value is the bound,
``0`` (the default) uses the target lane's spare capacity — one IDM lane's
capacity at the fleet defaults, ``microsim.runner.VACATE_LANE_CAPACITY_VEH_H``
= 2,050 veh/h, less the flow that lane carried into the vacate window over
the same 60 s — so the rule cannot push more into the lane than it has room
for. Not a fitted value; hash-neutral unless set. ``pair_release_s``
(2026-09-24, block 3, fifth derivation): how long an entering and an exiting
vehicle may stand within one vehicle length of each other in section lanes 0
and 1, both below the creep speed (``microsim.runner.SCRIPTED_MERGE_CREEP_MS``), before the pair is
released — the one farther from the section end yields for a step
(``microsim.runner._weave_pair_release``). The default, 2 s, is two human
reaction times of about 1 s (Treiber & Kesting 2013, ch. 12, the human driver
model's reaction time; the IDM itself has none): a pair standing longer than
the time in which each could have reacted to the other once is not resolving
by itself. Not a fitted value. ``exit_giveup_m`` (2026-09-24, block 3, the
exit-side derivation): an exit-bound vehicle still owing its change into the
auxiliary lane that has come to a halt (below SUMO's halting speed, 0.1 m/s)
with no more than this much of the section ahead of its front, and no change
to request that step, has missed the exit — it is rerouted through
(``vehicle.changeTarget`` to the corridor's last edge, its paired exit
dropped), handed back and counted in ``n_missed_exit``, instead of being
held by SUMO at the end of a lane its route does not continue on, where it
stops the through lane behind it and the auxiliary lane beside it (the I-94
WB standstill at the T.H.52 gore's end, ``microsim.runner._weave_step``). One
still rolling there may yet drop in and is left to. The default, 5 m, is one
vehicle length: a driver halted within its own length of the gore's nose is
not going to cross the taper; not a fitted value. ``0`` gives up only at the
lane end. ``exit_prepare`` (2026-09-24, block 3, WP-62, the exiters' early move): ``1``
asks each vehicle bound for the paired exit that is inside the vacate window
(``vacate_ahead_m``, measured along the chain as for the vacate rule) in a
lane *left* of the one feeding section lane 1 to move into that lane — the
vacate rule's mirror for the exit movement: section lane 0 begins at the
entrance's gore and leads only to the exit, so section lane 1 is the one lane
from which a single change reaches it, and an exiter arriving in section lane
``k`` owes ``k`` changes through lanes the entering movement crosses the
other way (the HCM 7th ed. ch. 13 ramp weave counts one change per exiter,
from the lane next to the auxiliary lane). Under the vacate rule's own terms
(``microsim.runner._weave_exit_prepare_step``): asked once under mode 512 with
the request living to the section start; bounded by
``vacate_max_veh_h`` or the target lane's spare capacity; nobody else
commanded; never against the vehicle's route. Where an exiter and a through
vehicle held by the vacate rule stand abreast, each asking into the other's
lane, the vacate request goes first (SUMO does not order such a pair: on the
fixture grid they stood for up to 82 s). The exiters moved into the lane before
the section are counted in ``n_exit_prepared``. The default is **0** (off,
hash-neutral unless set): measured on the corridor section test's fixture
(``tests/fixtures/weave_th52_corridor.osm``, seeds 3 / 4 / 5;
docs/WEAVE_MODEL_PLAN.md, dated section WP-62) it moves 71 / 95 / 95 exiters
and no criterion improves — the T.H.52 entrance 371 / 355 / 337 of 407 against
368 / 360 / 350, the exit end's lanes at or below 20 m/s in 11 / 10 / 14 of 16
windows against 10 / 11 / 13, the mainline 1,105 and 1,116 of 1,196 at seeds 4
and 5 (1,137 asked), the exit end's lane 0 1,044 / 1,080 / 1,089 veh/h against
1,035 / 1,104 / 1,122 — because in free flow SUMO's own strategic change
(``lcStrategic`` 5) has already put the exiters in that lane, and once it
queues the move finds no gap (16 / 50 / 54 of the asked reach the section
still left of it) or joins the queue; on the 29-run fixture grid of WP-52..60
the entrances fall 5,944 → 5,847, the give-ups read 44 → 45, and the T.H.52
capacity fixture's no-lock pin fails at seed 5 (3 exits missed against at most
1); no collision. A switch, not a fitted value.
The weave's other switches, all off or unset by default, were deleted on
2026-10-06 (:data:`REMOVED_WEAVE_KEYS`; docs/WEAVE_MODEL_PLAN.md has their
derivations and measurements; reproduce results made with them with release
2.5.0). Keys with no default are in :data:`WEAVE_OPTIONAL_KEYS`."""

#: ``weave_params`` keys with no default: unset is off. Kept out of
#: :data:`WEAVE_DEFAULTS` so that a run that does not set one has exactly the
#: physics, ``meta.json`` and pinned default snapshot
#: (``tests/golden/config_defaults.json``) it had before the key existed.
#:
#: ``entrant_giveup_m`` (2026-10-07, amendment W1 of
#: docs/WEAVE_LOSS_DIAGNOSIS.md §6.2, opt-in): the entering mirror of
#: ``exit_giveup_m``. An entrant still owing its change from the auxiliary
#: lane into section lane 1 that has come to a halt (below SUMO's halting
#: speed, 0.1 m/s) with no more than this much of the section ahead of its
#: front, and no accepted or guard-passing forced change that step, takes the
#: paired exit: it is rerouted (``vehicle.changeTarget``) to the off-ramp's
#: last edge, handed back and counted in ``n_missed`` and
#: ``n_entrant_took_exit`` (``microsim.runner._weave_step``), instead of being
#: held at the end of the exit-only lane, where it stops every exit-bound
#: vehicle behind it. The pre-registered opt-in value is 5 m (one vehicle
#: length, as ``exit_giveup_m``). Unset or ``0`` is off. Not a fitted value;
#: not set by any committed scenario (adoption is a separate decision).
#:
#: ``entrant_giveup_dwell_s`` (2026-10-07, amendment W1b of
#: docs/WEAVE_LOSS_DIAGNOSIS.md §10, opt-in): the entering give-up waits for a
#: dwell. With it set, an entrant takes the paired exit only once it has stood
#: halted (below 0.1 m/s) within ``entrant_giveup_m`` of the auxiliary lane's
#: end without a break for at least this long — the clock starts on its first
#: halted step there and resets on any step it is at or above the halting speed
#: or farther back — and W1's own condition holds that step (no accepted or
#: guard-passing forced change). Meant for the permanent lock at a weaving gore
#: (docs/I94_COLLAPSE_DIAGNOSIS.md), not the ordinary stands that clear by
#: themselves (at most 50.5 s on the T.H.52 section test's reference). The
#: pre-registered opt-in value is 60 s. Needs ``entrant_giveup_m`` > 0 (refused
#: otherwise); unset or ``0`` leaves W1's immediate give-up. Not a fitted
#: value; not set by any committed scenario.
#:
#: The three switches of amendment W2, the weave collision guards
#: (2026-10-07, docs/I94_CAL_COLLISIONS.md §13, opt-in; :data:`WEAVE_W2_SWITCHES`;
#: ``1`` on, ``0`` or unset off, any other value refused):
#:
#: ``weave_handback``: before each one-step weave speed target (the changer's
#: easing, the chosen follower's cooperation, the ramp anticipation) the target
#: is withheld for the step when the vehicle's own model must brake harder than
#: a commanded vehicle can (``microsim.runner._handback_needed``, the AV path's
#: ``AVSpec.emergency_handback`` test): under SUMO 1.27.1's default speed mode a
#: TraCI speed target caps braking at ``decel`` (WP-95). Counted in
#: ``n_handback_skips``.
#:
#: ``weave_close_leader``: the weave's own-acceleration estimate
#: (``microsim.runner._weave_command``) reads a leader closer than the
#: vehicle's ``minGap`` as a leader at that bumper gap, not as a free road (the
#: AV path's ``AVSpec.observe_close_leader``, WP-96). Counted in
#: ``n_close_leader_withheld``.
#:
#: ``weave_resolve_opposing``: the section's change requests of a step are
#: resolved with ``microsim.merge_model.resolve_opposing`` before they execute
#: (two entries into one lane from both sides in one step: the loser waits a
#: step; WP-92, as the measured merge model always does). Counted in
#: ``n_opposing_deferred`` and ``n_opposing_vetoed``.
#:
#: Not fitted values; not set by any committed scenario.
WEAVE_W2_SWITCHES: Final[frozenset[str]] = frozenset(
    {"weave_handback", "weave_close_leader", "weave_resolve_opposing"}
)
WEAVE_OPTIONAL_KEYS: Final[frozenset[str]] = (
    frozenset({"entrant_giveup_m", "entrant_giveup_dwell_s"}) | WEAVE_W2_SWITCHES
)
WEAVE_KEYS = frozenset(WEAVE_DEFAULTS) | WEAVE_OPTIONAL_KEYS

#: The merge switches deleted on 2026-10-06 (docs/MERGE_MODEL.md, amendment
#: A4): each was off or unset by default, set by no committed scenario or
#: reference pipeline stage, and recorded as a negative result. Their
#: derivations and measurements stay in docs/WEAVE_MODEL_PLAN.md and the
#: CHANGELOG; results made with them are reproduced with release 2.5.0. A
#: config that still sets one is refused with a message naming it
#: (:func:`_removed_message`) rather than run under other physics.
#: ``courtesy`` came to the weave with the scripted merge's keys, and
#: ``force_guard`` here is the weave's pinned, never-read copy (the scripted
#: merge's stays).
REMOVED_WEAVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "accept_lag_gap_s",
        "exit_accept_lag_gap_s",
        "vacate_no_follower_braking",
        "exit_giveup_patience_s",
        "exit_abreast_patience_s",
        "exiter_yields",
        "entrant_yields",
        "exiter_yields_halting",
        "exiter_yield_lead_s",
        "entry_speed_bound",
        "hold_release_s",
        "anticipation_gate",
        "swap_pairs",
        "spread_crossings",
        "ramp_outlet",
        "exit_priority_onset",
        "anticipation_spares_exiters",
        "opposing_entry_guard",
        "courtesy",
        "force_guard",
    }
)
#: The scripted merge's key deleted on 2026-10-06 (:data:`REMOVED_WEAVE_KEYS`).
REMOVED_SCRIPTED_MERGE_KEYS: Final[frozenset[str]] = frozenset({"courtesy"})
#: ``RampSpec.merge`` values deleted on 2026-10-06: ``acceleration_lane``
#: (SUMO's lane attribute on the attach edge's lane 0), unused and inert.
REMOVED_MERGE_MODELS: Final[frozenset[str]] = frozenset({"acceleration_lane"})


def _removed_message(what: str, names: Iterable[str]) -> str:
    """The refusal of a setting deleted on 2026-10-06, naming it."""
    return (
        f"{what} {sorted(names)} removed on 2026-10-06 (dead merge switches, "
        "docs/MERGE_MODEL.md amendment A4; derivations in docs/WEAVE_MODEL_PLAN.md); "
        "reproduce results made with them with release 2.5.0"
    )


class WeaveSpec(BaseModel):
    """A weaving section run by the runner's two-sided gap acceptance (2026-09-23).

    HCM 7th ed. ch. 13: an entrance followed closely by an exit joined by an
    auxiliary lane, over which the entering stream (aux lane → mainline) and
    the exiting stream (mainline → aux lane) cross. Carried by an on-ramp
    whose ``merge`` is ``"weave"``; the auxiliary lane keeps its connection to
    the exit (no termination, no netconvert patch) and
    ``microsim.runner._weave_step`` drives both movements. docs/CONTRACTS.md
    §2, docs/WEAVE_MODEL_PLAN.md §2(A).
    """

    exit_ramp: str = Field(min_length=1)
    """``name`` of the paired off-ramp; it must attach to the same corridor
    edge as the on-ramp (schema check) and be reached along lane 0 of the
    compiled network (run-time check, ``microsim.networks.weave_sections``)."""
    length_m: float | None = Field(default=None, gt=0.0)
    """Short length L_S between the gores [m] as measured in the field;
    ``None`` = measured from the compiled network. Recorded in
    ``meta.json["weave_sections"]`` beside the measured length."""
    weave_params: dict[str, float] = Field(default_factory=dict)
    """Overrides of :data:`WEAVE_DEFAULTS`, plus the keys of
    :data:`WEAVE_OPTIONAL_KEYS` (no default; unset is off); unknown keys are
    rejected, and a key of :data:`REMOVED_WEAVE_KEYS` is refused by name."""
    ramp_to_ramp_share: float | None = Field(default=None, ge=0.0, le=1.0)
    """Share of this entrance's vehicles that leave at the paired exit
    (``v_RR / v_ON``, the ramp-to-ramp movement; 2026-10-07,
    docs/TH52_CROSSING_SHARE.md). ``None`` (the default) keeps the plan's
    proportional split: every vehicle reaching the exit draws its
    ``exit_fraction`` alike (HCM 7th ed. ch. 13's simple weaving-volume
    estimate). When set, ``microsim.vehicles.build_corridor_plan`` swaps
    destinations, in each 300-s window of departure, between entrants and the
    vehicles that reach the weave on the mainline (those of the corridor entry
    and of every on-ramp attached upstream of the entrance) bound for the
    paired exit (or, below the drawn share, the other way), so the entrance's
    realized share is this value while the volume of every leg and every exit
    is the plan's; no random number is drawn, so every other draw of the run
    is unchanged. A window is refused when its swap pool's exit volume (the
    window's entrants and those mainline vehicles bound for the exit: every
    vehicle of the window bound for it but those of an entrance attached at or
    after this one's edge) is smaller than the ramp-to-ramp volume the share
    asks for. An unmeasured demand input carried for sensitivity and
    uncertainty (docs/FRISCO_PROTOCOL.md Amendment 3, proposed), never a
    merge-model parameter, so it is allowed with ``merge="measured"`` too.
    Not set by any committed scenario. Hash-neutral and absent from
    ``model_dump`` (so from ``meta.json["config"]`` and YAML) when unset
    (:meth:`_serialize`); the realized share is recorded in
    ``meta.json["ramp_to_ramp_shares"]`` when set."""

    # No return annotation on purpose: pydantic builds a model's serialization
    # JSON schema from its serializer's return annotation and keeps the model's
    # own schema without one.
    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler):  # type: ignore[no-untyped-def]
        """Drop ``ramp_to_ramp_share`` from every dump while it is unset (None).

        So a weave block dumps (``model_dump``, ``meta.json["config"]``, YAML)
        exactly as before the field existed (2026-10-07). Written as a wrap
        serializer, not ``Field(exclude_if=...)``, because ``exclude_if``
        needs pydantic >= 2.12 while the packages declare ``pydantic>=2.10``:
        on 2.10 and 2.11 that keyword is a deprecated extra (it lands in the
        JSON schema, which then cannot be generated) and nothing is excluded.
        The optional ``weave_params`` keys (:data:`WEAVE_OPTIONAL_KEYS`) need
        no rule: they are dict keys, present only when set.
        """
        data = handler(self)
        if self.ramp_to_ramp_share is None and isinstance(data, dict):
            data.pop("ramp_to_ramp_share", None)
        return data

    @model_validator(mode="after")
    def _check_params(self) -> Self:
        removed = set(self.weave_params) & REMOVED_WEAVE_KEYS
        if removed:
            raise ValueError(_removed_message("weave_params keys", removed))
        unknown = set(self.weave_params) - WEAVE_KEYS
        if unknown:
            raise ValueError(f"unknown weave_params keys: {sorted(unknown)}")
        if self.weave_params.get("entrant_giveup_m", 0.0) < 0.0:
            raise ValueError("weave_params entrant_giveup_m must be >= 0 (0 or unset = off)")
        dwell = self.weave_params.get("entrant_giveup_dwell_s", 0.0)
        if dwell < 0.0:
            raise ValueError("weave_params entrant_giveup_dwell_s must be >= 0 (0 or unset = off)")
        if dwell > 0.0 and self.weave_params.get("entrant_giveup_m", 0.0) <= 0.0:
            raise ValueError(
                "weave_params entrant_giveup_dwell_s needs entrant_giveup_m > 0 "
                "(the dwell delays the entering give-up, amendment W1b)"
            )
        for key in sorted(WEAVE_W2_SWITCHES & set(self.weave_params)):
            if self.weave_params[key] not in (0.0, 1.0):
                raise ValueError(
                    f"weave_params {key} is a switch: 1 on, 0 or unset off "
                    "(amendment W2, docs/I94_CAL_COLLISIONS.md §13)"
                )
        return self


class RampSpec(BaseModel):
    """One on- or off-ramp attached to an OSM corridor (docs/CONTRACTS.md §2).

    Real corridors exchange traffic with interchanges; a mainline-only
    replica cannot conserve flow along the corridor (the US-101 replica's
    missing merge was a documented structural failure,
    docs/M3_US101_VALIDATION.md §6). A ramp is a chain of network edges
    (OSM ``motorway_link`` ways) joined to one corridor edge:

    * ``kind="on"`` — ``edges`` end at the junction where the ramp joins
      ``attach_edge``; vehicles are inserted at the start of ``edges[0]``
      following ``inflow`` (time-ordered ``(t_start [s], veh/s)`` steps,
      same convention as the corridor inflow) and then drive the corridor.
    * ``kind="off"`` — ``edges`` start at the junction where the ramp leaves
      ``attach_edge`` (the last corridor edge a diverging vehicle drives);
      every vehicle passing that point exits with probability
      ``exit_fraction`` (time-ordered ``(t_start [s], fraction)`` steps,
      looked up at the vehicle's departure time), drawn once per vehicle
      from the run's RNG, and leaves the network at the end of ``edges[-1]``.

    Ramp vehicles draw fleet parameters, AV tags and compliance exactly like
    mainline vehicles. Positions on ramp edges are not part of the corridor's
    linear ``x`` and are not recorded in ``trajectories.parquet``; a ramp
    vehicle appears in the outputs from the moment it is on a corridor edge.
    """

    kind: Literal["on", "off"]
    edges: list[str] = Field(min_length=1)
    """Ramp edge ids in driving order."""
    attach_edge: str = Field(min_length=1)
    """Corridor edge the ramp joins (on) or leaves from (off)."""
    inflow: list[tuple[float, float]] = Field(default_factory=list)
    """On-ramp demand steps ``(t_start [s], veh/s)``; empty for off-ramps."""
    exit_fraction: list[tuple[float, float]] = Field(default_factory=list)
    """Off-ramp diverge steps ``(t_start [s], fraction in [0, 1])``; empty
    for on-ramps."""
    name: str = ""
    """Optional label (e.g. the interchange) recorded in run metadata."""
    meter: RampMeterSpec | None = None
    """Ramp metering on this on-ramp (:class:`RampMeterSpec`); ``None`` =
    unmetered. Off-ramps cannot carry a meter."""
    merge_visibility_m: float | None = Field(default=None, gt=0.0)
    """Zipper merges only: SUMO connection ``visibility`` [m], the distance
    upstream of the zipper junction from which the ramp lane and the mainline
    lane it merges with consider each other and interleave (SUMO's default
    100 m). ``None`` keeps the default. Set to the acceleration lane's length
    to let the ramp feed the mainline over the lane instead of at its end
    (docs/I24_VALIDATION.md §0.7)."""
    merge: Literal["lane_change", "zipper", "scripted", "weave", "measured"] = "lane_change"
    """How an on-ramp's acceleration lane hands its traffic to the mainline
    (micro tier, 2026-09-06). ``lane_change`` (default): the lane dead-ends
    and ramp vehicles change lanes under the lane-change model — on the I-24
    replica this locked the merge into a right-lane crawl under every
    parameter tried (docs/I24_VALIDATION.md §0.5). ``zipper``: the attach
    edge's rightmost lane is connected into the next corridor edge's
    rightmost lane alongside the mainline lane and the junction becomes a
    zipper, so ramp and mainline traffic interleave at the lane end instead
    of negotiating lane changes; it needs the acceleration lane to dead-end
    at the attach edge's end node (checked at run time) and is applied as
    netconvert patches recorded in ``meta.json``. ``acceleration_lane``
    (SUMO's lane attribute, unused and inert) was removed on 2026-10-06
    (:data:`REMOVED_MERGE_MODELS`; reproduce with release 2.5.0).
    ``scripted`` (2026-09-16): the network
    is the ``lane_change`` one, but every vehicle on the acceleration lane is
    driven by the runner's gap-acceptance merge instead of SUMO's lane-change
    model — it matches the speed of the mainline lane it is entering, takes
    the first gap that clears ``merge_params["accept_gap_s"]`` on both sides,
    and after ``merge_params["force_after_s"]`` of waiting inside the last
    ``merge_params["force_within_m"]`` of the lane it forces the change (the
    mainline follower yields, SUMO still refusing collisions). Recorded per
    ramp in ``meta.json["scripted_merges"]``. ``weave`` (2026-09-23): the
    entrance is the upstream end of a weaving section whose auxiliary lane
    also feeds the off-ramp named in :attr:`weave` — lane 0 is left connected
    to the exit and the runner drives both crossing movements (entering
    vehicles change left, exiting vehicles change right) with two-sided gap
    acceptance (:class:`WeaveSpec`). Recorded per section in
    ``meta.json["weave_sections"]``. ``measured`` (2026-10-05,
    docs/MERGE_MODEL.md): the measured merge model — the runner drives every
    mandatory lane change inside the zone on per-driver critical gaps drawn
    from the measured fits, a speed ceiling matched to the chosen gap and a
    post-crossing headway relaxation (``microsim.merge_model``,
    ``microsim.runner._measured_step``). Without a :attr:`weave` block the
    zone is the acceleration lane (terminated as for ``scripted``); with one
    it is the weaving section to the named exit (paired and validated as for
    ``weave``; ``weave_params`` must stay empty: the model's constants are
    fixed, never tuned per corridor). Its parameters come from
    ``artifacts/merge_model_params.json`` (:attr:`OSMNetwork.merge_model_set`
    names the set). Recorded per zone in ``meta.json["measured_merges"]``
    and per run in ``meta.json["measured_merge_model"]``. Hash-neutral: the
    value exists only where a scenario sets it."""
    merge_params: dict[str, float] = Field(default_factory=dict)
    """Tuning of the ``scripted`` merge (ignored by the other models).
    Keys and defaults: ``accept_gap_s`` 0.6 (time gap accepted to the mainline
    leader and follower, on top of the vehicle's ``s0``), ``force_after_s``
    4.0, ``force_within_m`` 80.0, ``change_duration_s`` 2.0, ``lookahead_m``
    120.0 (distance over which the mainline lane's speed is matched),
    ``force_guard`` 1.0 (on since 2026-10-04; see below). ``courtesy`` was
    removed on 2026-10-06 (:data:`REMOVED_SCRIPTED_MERGE_KEYS`; reproduce with
    release 2.5.0).

    ``force_guard`` (2026-09-26, block 3, WP-93; docs/WEAVE_MODEL_PLAN.md,
    dated section). Added off (0) on 2026-09-26; **on (1) by default since
    2026-10-04** by owner decision (WP-98: zero collisions is a pass/fail
    requirement of every run set), which bumped the config-hash policy to
    version 3 (docs/CONTRACTS.md §2). ``0`` reproduces the unguarded forced
    change, the path of every scripted-merge run before release 2.6.
    Without it a vehicle due to force is put under
    ``laneChangeMode`` 256 for the rest of its time on the lane, and every
    open request then executes at any gap SUMO does not read as an overlap:
    a follower's front clear of its own ``minGap`` behind the changer,
    whatever the follower's closing speed (SUMO 1.27.1,
    ``MSLaneChanger::checkChange`` and ``MSVehicle::Influencer::
    influenceChangeDecision``). The changer has slowed for the lane's end, so
    such a change can land 2–8 m in front of a follower 10–16 m/s faster,
    which then brakes at 9 m/s² and hits it: all 13 collisions on the McKnight
    Rd fixture, and the signature of the 14 of the I-94 WB reference battery
    on the two scripted merges' lanes (VM AF: lane 1, 217–238 m, a ramp
    vehicle hit by one from upstream). With ``force_guard`` > 0 the
    vehicle due to force is under mode 256 only in a step in which
    ``microsim.runner._scripted_force_gap_ok`` passes — each target-lane gap,
    less the distance the pair closes in one step, at least the brake gap of
    the party behind at its own ``b`` — and under mode 512 (SUMO's own gap
    check) otherwise; its request stays open as before, so SUMO's cooperation
    towards it continues. Vehicle-steps refused are counted in
    ``meta.json["scripted_merges"][i]["n_forced_deferred"]``. Measured on the
    fixture (WP-93: no collision in 15 runs) and on the I-94 WB corridor
    battery with the key set on its two scripted ramps (VM AG, 2026-09-26:
    collisions 15 → 0 over 20 paired seeds, no resolved change in departures,
    RMSPE or GEH; docs/ONBOARDING_MNDOT.md §11). The weave has no such key
    (:data:`WEAVE_DEFAULTS`): its forced changes are always under its own
    guard (``_weave_force_gap_ok``)."""
    weave: WeaveSpec | None = None
    """The weaving section this on-ramp opens (:class:`WeaveSpec`); required
    by, and only allowed with, ``merge="weave"``. Hash-neutral when unset."""

    @field_validator("merge", mode="before")
    @classmethod
    def _check_removed_merge(cls, value: Any) -> Any:
        if isinstance(value, str) and value in REMOVED_MERGE_MODELS:
            raise ValueError(_removed_message("merge models", [value]))
        return value

    @model_validator(mode="after")
    def _check_kind(self) -> Self:
        removed = set(self.merge_params) & REMOVED_SCRIPTED_MERGE_KEYS
        if removed:
            raise ValueError(_removed_message("merge_params keys", removed))
        unknown = set(self.merge_params) - SCRIPTED_MERGE_KEYS
        if unknown:
            raise ValueError(f"unknown merge_params keys: {sorted(unknown)}")
        if self.merge_params and self.merge != "scripted":
            raise ValueError("merge_params apply to merge='scripted' only")
        if self.kind == "off" and self.weave is not None:
            raise ValueError("a weave is opened by an on-ramp; an off-ramp cannot carry weave")
        if self.kind == "on" and self.merge == "weave" and self.weave is None:
            raise ValueError("merge='weave' needs a weave block naming its exit_ramp")
        if self.weave is not None and self.merge not in ("weave", "measured"):
            raise ValueError(
                "a weave block applies to merge='weave' only (or to merge='measured', "
                "where it names the weaving section's exit)"
            )
        if self.merge == "measured" and self.weave is not None and self.weave.weave_params:
            raise ValueError(
                "weave_params apply to merge='weave' only: the measured model's constants "
                "are fixed (docs/MERGE_MODEL.md §2)"
            )
        if self.kind == "on":
            if not self.inflow:
                raise ValueError("an on-ramp needs a non-empty inflow")
            if self.exit_fraction:
                raise ValueError("an on-ramp cannot carry exit_fraction")
            times = [t for t, _ in self.inflow]
            if times != sorted(times) or any(q < 0 for _, q in self.inflow):
                raise ValueError("on-ramp inflow must be time-ordered and >= 0")
        else:
            if self.meter is not None:
                raise ValueError("an off-ramp cannot carry a meter")
            if self.merge != "lane_change":
                raise ValueError("merge models apply to on-ramps only")
            if self.merge_params:
                raise ValueError("merge_params apply to on-ramps only")
            if not self.exit_fraction:
                raise ValueError("an off-ramp needs a non-empty exit_fraction")
            if self.inflow:
                raise ValueError("an off-ramp cannot carry inflow")
            times = [t for t, _ in self.exit_fraction]
            if times != sorted(times):
                raise ValueError("exit_fraction steps must be time-ordered")
            if any(not 0.0 <= f <= 1.0 for _, f in self.exit_fraction):
                raise ValueError("exit_fraction values must lie in [0, 1]")
        return self

    # Collector–distributor pairing (2026-09-24, additive; hash-neutral when unset).
    cd_road: bool = False
    """This ramp is one end of a collector–distributor road: an ``off`` ramp
    that is the split where the C-D road leaves the corridor, or an ``on``
    ramp that is its re-entry. Both ends carry the same :attr:`cd_pair`, so
    the demand step can treat them as one road whose flow leaves and returns
    (``calibration.onboarding``) instead of an exit and an unrelated
    entrance. The runner treats each end exactly like any ramp of its kind."""
    cd_pair: str = ""
    """Identifier shared by the two ends of one C-D road (the id of the first
    link edge of the split); empty unless :attr:`cd_road`."""


class OSMNetwork(BaseModel):
    """Corridor imported from OpenStreetMap (the 'any city' path, §3.2.4)."""

    kind: Literal["osm"] = "osm"
    osm_file: str | None = None
    bbox: tuple[float, float, float, float] | None = None
    """(south, west, north, east) in WGS84 degrees."""
    corridor_edges: list[str] = Field(default_factory=list)
    """SUMO edge ids forming the analysis corridor after import/pruning."""
    inflow: list[tuple[float, float]] = Field(default_factory=list)
    boundary: BoundarySpec | None = None
    """Optional measured downstream boundary condition. On an OSM corridor
    the schedule is applied to the LAST edge of ``corridor_edges``, which
    therefore plays the exit-buffer role (its ``exit_buffer_m`` is ignored;
    the edge has its real length) and lies outside the measured span."""
    ramps: list[RampSpec] = Field(default_factory=list)
    """Interchange ramps exchanging traffic with the corridor (see
    :class:`RampSpec`); each ``attach_edge`` must be in ``corridor_edges``."""
    entry_lane_shares: list[float] | None = None
    """Measured share of mainline entries per lane, LEFT to RIGHT, on the
    first corridor edge (its lane count comes from the map and is checked at
    run time); ``None`` = round-robin. See :class:`CorridorNetwork`."""
    netconvert_extra: list[str] = Field(default_factory=list)
    """Extra ``netconvert`` options appended verbatim at every import of this
    network (before the pruning flags). The onboarding path uses
    ``["--ramps.guess", "--ramps.no-split", "--ramps.ramp-length", "250"]``
    when the map lacks acceleration lanes: OSM often tags a motorway as three
    lanes straight through a merge, and a link that joins lane 0 at a plain
    junction starves under SUMO's yielding (observed 2026-09-23 on I-94 WB:
    four on-ramps delivered 4-6 % of their demand). ``--ramps.no-split`` keeps
    the attach edge's id, so ``corridor_edges`` and station positions stay
    valid. Recorded in the config hash whenever set."""
    patch_files: list[str] = Field(default_factory=list)
    """Plain-XML ``netconvert`` patches (``*.nod.xml`` / ``*.edg.xml`` /
    ``*.con.xml``, paths relative to the working directory, inside the
    allowed data roots) loaded at every import of this network, before the
    merge-model patches the runner adds. They are the place for map
    corrections that netconvert cannot be talked into with options: an
    explicit connection list for an edge replaces every connection netconvert
    computed for it. First use (2026-09-24, I-94 WB St. Paul): netconvert put
    a right-hand exit on the leftmost lanes at two splits, trapping through
    traffic there. Recorded in the config hash whenever set."""
    internal_links: bool = False
    """Compile the network with SUMO's internal junction lanes (netconvert
    without ``--no-internal-links``). Off by default (every existing import
    is lane-to-lane at the node); on, junction movements — including a
    zipper merge (``RampSpec.merge``) — are resolved along internal lanes,
    which is where SUMO computes zipper interleaving. Vehicles are not
    recorded while on an internal lane (a few metres per junction)."""
    # 2026-10-05 (docs/MERGE_MODEL.md §2): the measured merge model's
    # pre-registered parameter sets; hash-neutral at its default
    merge_model_set: Literal["central", "us101_gaps", "delta_zero", "tau_r_low", "tau_r_high"] = (
        "central"
    )
    """Parameter set of the measured merge model (``RampSpec.merge =
    "measured"``; 2026-10-05, docs/MERGE_MODEL.md §2): ``central`` (the
    default) or one of the pre-registered sensitivity arms — ``us101_gaps``
    (US-101's complete-coverage critical gaps), ``delta_zero`` (no speed
    offset), ``tau_r_low`` / ``tau_r_high`` (the relaxation time constant at
    its interval's ends). The sets are read from
    ``artifacts/merge_model_params.json`` (``scripts/merge_model_params.py``).
    A set other than ``central`` needs a ``measured`` ramp. Hash-neutral at
    its default."""
    # WP-71 (2026-09-25, block 3): a distance, 0 = off; hash-neutral unless
    # set; not a fitted value (the derivation is in the docstring below)
    lane_end_giveup_m: float = Field(default=0.0, ge=0.0, le=50.0)
    """The lane-end give-up at every diverge of the corridor (2026-09-25,
    block 3, WP-71; ``microsim.runner._lane_end_step``); ``0`` (the default)
    is off. A positive value is the distance from a lane's end within which a
    vehicle counts as held there. A diverge is a corridor edge whose lanes do
    not all lead to the same edges (exit-only lanes beside through lanes).
    Each step, a vehicle on such an edge is given up to its lane's own
    continuation when all of these hold:

    * it is halted (below ``microsim.runner.HALTING_SPEED_MS``);
    * it is the front vehicle of its lane, within this distance of the end;
    * its route's next edge is not reached from its lane;
    * SUMO's lane-change model reports the change toward its route blocked
      this step.

    The give-up is a route change (``vehicle.changeTarget``), never a lane
    change: the vehicle drives on in the lane it is in.

    * An exiter held at the end of a through lane gives up its exit: it is
      rerouted to the corridor's last edge, as a weaving section's give-up
      is (``n_missed_exit``).
    * A vehicle bound elsewhere held at the end of an exit-only lane takes
      the exit: it is rerouted to the off-ramp's last edge, as a driver
      trapped in an exit lane does.

    Both are counted per diverge in ``meta.json["lane_end_giveups"]`` and
    marked in ``vehicles.parquet`` (``gave_up``, ``gave_up_s``,
    ``destination_final``). The weaving sections' edges are not acted on
    (they keep their own rule), nor is a vehicle a weaving section or a
    scripted merge is commanding. Derived from VM T (docs/ONBOARDING_MNDOT.md
    §11), the I-94 WB lock at the T.H.61 → 18207912 gore: an exiter held at
    the end of through lane 2, and T.H.61 entrants bound on held at the end
    of the exit-only lanes; each needs the other's lane.

    The value measured (docs/WEAVE_MODEL_PLAN.md, WP-71) is 7.5 m, one
    vehicle's room: ``microsim.vehicles.VEHICLE_LENGTH_M`` (5 m) plus the
    corridor population's mean minimum gap (2.53 m,
    ``artifacts/idm_i24_capacity.json``), rounded to 0.5 m. SUMO stops a
    vehicle that has run out of lane at the end itself. If the vehicle at
    the end of the target lane must come in ahead of it, SUMO stops it one
    such room back (VM T's T.H.61 entrant stood 5.3 m from the end, behind
    the exiter at 0.1 m). Farther back a halted vehicle is waiting for a gap
    with road still to use (the fixture's halted wrong-lane lane fronts:
    median 15 m). Not a fitted value; hash-neutral unless set."""

    @model_validator(mode="after")
    def _check_source(self) -> Self:
        if self.osm_file is None and self.bbox is None:
            raise ValueError("OSMNetwork needs osm_file or bbox")
        _check_lane_shares(self.entry_lane_shares, None)
        if self.boundary is not None and len(self.corridor_edges) < 2:
            raise ValueError(
                "an OSM boundary needs corridor_edges with at least two edges "
                "(the last one hosts the boundary, outside the measured span)"
            )
        corridor = set(self.corridor_edges)
        if self.merge_model_set != "central" and not any(
            r.kind == "on" and r.merge == "measured" for r in self.ramps
        ):
            raise ValueError(
                f"merge_model_set {self.merge_model_set!r} applies to merge='measured' only: "
                "no ramp of this network uses it"
            )
        for ramp in self.ramps:
            if ramp.attach_edge not in corridor:
                raise ValueError(f"ramp attach_edge {ramp.attach_edge!r} is not in corridor_edges")
            if corridor & set(ramp.edges):
                raise ValueError(f"ramp edges {ramp.edges} overlap corridor_edges")
        for ramp in self.ramps:
            if ramp.weave is None:
                continue
            label = ramp.name or ramp.attach_edge
            exits = [r for r in self.ramps if r.kind == "off" and r.name == ramp.weave.exit_ramp]
            if len(exits) != 1:
                raise ValueError(
                    f"ramp {label}: weave exit_ramp {ramp.weave.exit_ramp!r} must name exactly "
                    f"one off-ramp of this network (found {len(exits)})"
                )
            if exits[0].attach_edge != ramp.attach_edge:
                raise ValueError(
                    f"ramp {label}: weave exit_ramp {ramp.weave.exit_ramp!r} leaves from "
                    f"{exits[0].attach_edge!r}, not from the on-ramp's attach edge "
                    f"{ramp.attach_edge!r}"
                )
        return self

    @model_validator(mode="after")
    def _check_osm_inflow(self) -> Self:
        times = [t for t, _ in self.inflow]
        if times != sorted(times):
            raise ValueError("inflow steps must be ordered by t_start")
        if any(q < 0 for _, q in self.inflow):
            raise ValueError("inflow must be >= 0")
        return self


Network = Annotated[RingNetwork | CorridorNetwork | OSMNetwork, Field(discriminator="kind")]


class HeavyVehicleSpec(BaseModel):
    """Heavy vehicles (trucks) as a share of the human fleet.

    A heavy vehicle is a second IDM population with its own length, SUMO
    vehicle class and emission class. Its parameters must come from a
    calibration artifact or be given explicitly — there are no built-in
    truck defaults, because none would carry provenance (CLAUDE.md §0.1,
    §12.6). On I-24 the recording's semi and truck classes are fitted from
    the same day (``artifacts/idm_i24_heavy.json``, docs/I24_DATA.md).
    Heavy vehicles are never tagged as controlled vehicles.
    """

    fraction: float = Field(ge=0.0, le=0.5)
    """Share of departures (ring: of vehicles) drawn as heavy, Bernoulli per
    vehicle from the run's RNG after every existing draw, so a fleet without
    this block reproduces its previous draws exactly."""
    length_m: float = Field(gt=5.0, le=30.0)
    emission_class: str = Field(min_length=1)
    """SUMO emission class written on the heavy vTypes (e.g. an HBEFA4 heavy
    duty class); the passenger class stays the fleet default."""
    vclass: Literal["truck", "trailer"] = "truck"
    """SUMO ``vClass`` of the heavy vTypes (lane permissions, closures)."""
    idm_calibration: str | None = None
    """IDMCalibration artifact for the heavy population (overrides the scalar
    means below, as for the passenger fleet)."""
    v0: float | None = Field(default=None, gt=0)
    T: float | None = Field(default=None, gt=0)
    a_max: float | None = Field(default=None, gt=0)
    b: float | None = Field(default=None, gt=0)
    s0: float | None = Field(default=None, gt=0)
    heterogeneity_frac: float = Field(default=HETEROGENEITY_FRAC_DEFAULT, ge=0, le=0.3)
    lane_shares: list[float] | None = None
    """Departure-lane distribution of the heavy population, LEFT to RIGHT like
    ``entry_lane_shares`` (normalised at use; length = the entry edge's lane
    count, checked against the network's lanes here and against the compiled
    map at run time). ``None`` (default) leaves heavy vehicles wherever the
    fleet's own lane scheme put them, i.e. spread over lanes like the light
    fleet — the uniform placement every heavy arm has used so far.

    Heavy traffic is not uniform across lanes: in the I-24 recording heavy
    fragments are 1.4 / 6.5 / 19.0 / 14.4% of lanes 1–4 against 9.2% corridor
    wide (``artifacts/i24_heavy_by_lane.json``, docs/MERGE_ROUND6_PLAN.md §2.4
    addendum), so a uniform share puts trucks in the HOV lane and takes them
    out of the two right lanes. Convert those per-lane fractions into this
    distribution with ``microsim.vehicles.heavy_lane_shares_from_artifact``.
    Drawn per heavy vehicle from a stream of the run's seed that is
    independent of every other draw, so setting this field moves the heavy
    vehicles' lanes and nothing else."""

    @model_validator(mode="after")
    def _check_population(self) -> Self:
        _check_lane_shares(self.lane_shares, None, name="heavy.lane_shares")
        scalars = (self.v0, self.T, self.a_max, self.b, self.s0)
        if self.idm_calibration is None and any(v is None for v in scalars):
            raise ValueError(
                "HeavyVehicleSpec needs idm_calibration or all five IDM means "
                "(v0, T, a_max, b, s0): heavy vehicles carry no built-in defaults"
            )
        return self


class FleetSpec(BaseModel):
    """Human-driver fleet: car-following model + population parameters."""

    model: Literal["IDM", "EIDM"] = "IDM"
    v0: float = Field(default=IDM_DEFAULTS["v0"], gt=0)
    T: float = Field(default=IDM_DEFAULTS["T"], gt=0)
    a_max: float = Field(default=IDM_DEFAULTS["a_max"], gt=0)
    b: float = Field(default=IDM_DEFAULTS["b"], gt=0)
    s0: float = Field(default=IDM_DEFAULTS["s0"], gt=0)
    delta: float = Field(default=IDM_DEFAULTS["delta"], gt=0)
    heterogeneity_frac: float = Field(default=HETEROGENEITY_FRAC_DEFAULT, ge=0, le=0.3)
    """σ of the per-vehicle truncated-normal draw, as a fraction of each mean."""
    idm_calibration: str | None = None
    """Path to an IDMCalibration artifact; when set, population stats override
    the scalar fields above and outputs record the artifact's data_hash."""
    lc_strategic: float = Field(default=1.0, ge=0.0)
    """SUMO ``lcStrategic``: eagerness for route-required (strategic) lane
    changes, written on every vType when it differs from SUMO's default 1.0.
    Needed on corridors with off-ramps: with the default, exiting vehicles
    that are still in an inner lane at the diverge stop at the edge end and
    wait for a gap, which creates a spurious fixed bottleneck (measured on the
    I-24 replica and on the ramp fixture, docs/I24_VALIDATION.md); larger
    values make them move over earlier. It does not touch car-following."""
    lc_strategic_ramp: float | None = Field(default=None, ge=0.0)
    """SUMO ``lcStrategic`` for vehicles that enter from an on-ramp; ``None``
    (default) means the same value as ``lc_strategic``. One eagerness serves
    two opposite needs on a corridor with both ramp kinds: vehicles bound for
    an off-ramp must reach the deceleration pocket early (a large value),
    while vehicles entering from an acceleration lane should use its whole
    length before merging (SUMO's default 1.0). With a single elevated value
    the I-24 replica's ramp traffic merged at the gore at 9–13 km/h and the
    right lane crawled 1.5 km upstream of it (docs/I24_VALIDATION.md §0.5).
    Written on the ramp-origin vTypes only when it differs from 1.0."""
    lc_keep_right: float = Field(default=1.0, ge=0.0)
    """SUMO ``lcKeepRight``: eagerness to obey a keep-right rule, written on
    every vType when it differs from SUMO's default 1.0. US freeways have no
    keep-right obligation and the I-24 data shows all four lanes carrying
    similar vehicle-time (30/24/20/26%, left to right) at similar speeds;
    with the default, SUMO piles the replica's traffic into the two right
    lanes (merge-zone crawl at 23-27 km/h while the left lanes run free —
    docs/I24_VALIDATION.md). 0 disables the rule. Car-following untouched."""
    lc_cooperative: float = Field(default=1.0, ge=0.0, le=1.0)
    """SUMO ``lcCooperative``: willingness of mainline drivers to slow for a
    neighbour's lane change (0 = never, 1 = SUMO default). Written on every
    vType when it differs from 1.0. Governs how readily gaps open at merges;
    exposed for the Old Hickory merge calibration (docs/I24_CAPACITY.md §6)."""
    lc_assertive: float = Field(default=1.0, gt=0.0)
    """SUMO ``lcAssertive``: the minimum gap a lane-changing vehicle accepts is
    divided by this factor (>1 = accepts smaller gaps). Written on every vType
    when it differs from SUMO's default 1.0."""
    lc_speed_gain: float = Field(default=1.0, ge=0.0)
    """SUMO ``lcSpeedGain``: eagerness for speed-gain (tactical) lane changes.
    Written on every vType when it differs from SUMO's default 1.0."""
    heavy: HeavyVehicleSpec | None = None
    """Heavy-vehicle share and population (:class:`HeavyVehicleSpec`);
    ``None`` = passenger cars only, as before."""
    jm_timegap_minor_s: float | None = Field(default=None, gt=0.0)
    """SUMO junction-model ``jmTimegapMinor`` [s] written on every vType when
    set: the minimum time gap a vehicle accepts when entering a junction
    ahead of a prioritised (or, at a zipper, an interleaving) vehicle; SUMO's
    default is 1.0 s. Exposed for the zipper merge model (``RampSpec.merge``),
    whose merged-lane throughput it sets (docs/I24_VALIDATION.md §0.5 (j))."""
    jm_ignore_foe_prob: float | None = Field(default=None, ge=0.0, le=1.0)
    """SUMO junction-model ``jmIgnoreFoeProb`` written on every vType when
    set: the probability that a vehicle entering a junction ignores a foe
    slower than ``jmIgnoreFoeSpeed`` (SUMO defaults 0 and 0). Exposed as the
    last vehicle-side lever for a zipper merge's admittance, after the
    junction gap and the internal lanes proved inert (docs/I24_VALIDATION.md)."""
    lc_sublane: float | None = Field(default=None, ge=0.0)
    """Sublane model (``SimSpec.lateral_resolution_m``) eagerness for lateral
    moves within a lane, SUMO ``lcSublane`` (default 1.0); written when set."""
    lc_pushy: float | None = Field(default=None, ge=0.0, le=1.0)
    """Sublane model: willingness to encroach laterally on neighbours to
    open a gap, SUMO ``lcPushy`` (0 = never, SUMO's default; 1 = full);
    written when set. The lever for merging over a lane's length."""
    lc_impatience: float | None = Field(default=None, ge=-1.0, le=1.0)
    """Sublane model: dynamic impatience that lets a vehicle accept smaller
    gaps the longer it has wanted to change lanes, SUMO ``lcImpatience``
    (default 0.0); written when set. SUMO 1.27.1's lane-discrete model
    (LC2013, used when ``SimSpec.lateral_resolution_m`` is unset) does not
    read it: its parameter interface rejects the key, and the corridor section
    fixture writes byte-identical trajectories with it at 1.0
    (docs/WEAVE_MODEL_PLAN.md, 2026-09-25, WP-82)."""
    lc_accel_lat: float | None = Field(default=None, gt=0.0)
    """Sublane model: maximum lateral acceleration [m/s²], SUMO
    ``lcAccelLat`` (default 1.0); written when set."""
    max_speed_lat: float | None = Field(default=None, gt=0.0)
    """Sublane model: maximum lateral speed [m/s], SUMO ``maxSpeedLat``
    (default 1.0); written when set."""
    min_gap_lat: float | None = Field(default=None, ge=0.0)
    """Sublane model: desired lateral gap to neighbours [m], SUMO
    ``minGapLat`` (default 0.6); written when set."""
    lat_alignment: Literal["left", "right", "center", "compact", "nice", "arbitrary"] | None = None
    """Sublane model: preferred lateral alignment within the lane, SUMO
    ``latAlignment`` (default ``center``); written when set."""
    lc_overtake_right: float | None = Field(default=None, ge=0.0, le=1.0)
    """Probability of overtaking on the right, SUMO ``lcOvertakeRight``
    (default 0: never, the European rule). US freeways allow it; written
    when set."""
    hov_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    """Share of passenger vehicles eligible for managed (HOV) lanes, drawn
    per vehicle from the run's RNG after every other draw (0 = none, so
    fleets without managed lanes reproduce their draws exactly); eligible
    vehicles carry SUMO ``vClass="hov"``. A measured or assumed occupancy
    share — record its source in the scenario header."""
    speed_factor: float = Field(
        default=SPEED_FACTOR_DEFAULT, ge=SPEED_FACTOR_BOUNDS[0], le=SPEED_FACTOR_BOUNDS[1]
    )
    """SUMO ``speedFactor`` of every passenger vehicle (2026-10-04, WP-109):
    its desired free-flow speed on a lane is ``min(maxSpeed, speedFactor ×
    lane limit)`` — "the product of road speed limit and the individual
    speed factor gives the desired free flow driving speed" (SUMO
    documentation, *Definition of Vehicles, Vehicle Types, and Routes*,
    section "Speed Distributions"; measured on SUMO 1.27.1: a vType with
    ``speedFactor="1.2"`` cruises at 24.0 m/s on a 20 m/s lane). The default
    1.0 caps every driver at the posted limit, as every run before WP-109
    did; a larger value is the corridor-wide setting the driver-settings
    check (``calibration.transfer_check``) recommends when drivers in light
    traffic drive faster than the limit. Heavy vehicles (``heavy``) keep
    1.0: their speeds are not what a loop's light-traffic mean measures, and
    trucks are often governed. The bounds are SUMO's default cut-offs for an
    individual factor (``normc(1, 0.1, 0.2, 2)``). SUMO keeps four decimals
    of a vehicle's factor (``speedFactor="1.123456"`` runs as 1.1235,
    measured), so FlowState rounds to four as well. Written on the vTypes
    only when it differs from 1.0 or ``speed_dev`` is set, so route files of
    a default fleet are byte-identical. The downstream boundary schedule
    (:class:`BoundarySpec`) is posted divided by this factor so that its
    measured speeds stay the speeds driven; a VSL posting, like any posted
    limit, is exceeded by the factor."""
    speed_dev: float = Field(default=0.0, ge=0.0, le=0.5)
    """Spread of the passenger vehicles' individual speed factors, SUMO
    ``speedDev`` (WP-109): each passenger vehicle's factor is drawn from the
    normal distribution of mean ``speed_factor`` and this deviation, cut at
    SUMO's default bounds 0.2 and 2 — the distribution SUMO documents for
    ``speedFactor="<mean>" speedDev="<dev>"`` — but drawn by FlowState's
    seeded generator (a stream of the run's seed independent of every other
    draw, ``microsim.vehicles.draw_speed_factors``), rounded to SUMO's four
    decimals and written as that vehicle's own ``speedFactor`` with
    ``speedDev="0"``: per-vehicle heterogeneity comes from our RNG
    (CLAUDE.md §0.5), and each vehicle's factor is known before SUMO starts
    (the demand ledger's free-flow times use it). 0 (default) gives every
    passenger vehicle exactly ``speed_factor``. Loop data do not identify
    it: five-minute means average many vehicles."""


FLEET_SETTINGS_FIELDS: Final[tuple[str, ...]] = (
    "model",
    "heterogeneity_frac",
    "lc_strategic",
    "lc_strategic_ramp",
    "lc_keep_right",
    "idm_calibration",
)
"""The :class:`FleetSpec` fields a demand record states (``fleet_settings``,
docs/CONTRACTS.md, onboarding): the car-following model and its population
draw, the three lane-change eagernesses a corridor sets deliberately, and the
population artifact. A fleet block silently reset to the builder's defaults
(the I-94 regeneration of 2026-09-24, docs/ONBOARDING_MNDOT.md §11) shows up
in the artifact through these, not only in the scenario file."""


def fleet_settings(fleet: FleetSpec | Mapping[str, Any]) -> dict[str, Any]:
    """The :data:`FLEET_SETTINGS_FIELDS` of a fleet block, JSON-ready.

    Args:
        fleet: A :class:`FleetSpec`, or a fleet mapping as a scenario file
            carries it (validated here, so absent fields read as their
            defaults).

    Returns:
        Field name → value, in :data:`FLEET_SETTINGS_FIELDS` order.
    """
    spec = fleet if isinstance(fleet, FleetSpec) else FleetSpec.model_validate(dict(fleet))
    dumped = spec.model_dump(mode="json")
    return {name: dumped[name] for name in FLEET_SETTINGS_FIELDS}


def fleet_non_defaults(fleet: FleetSpec) -> dict[str, Any]:
    """The fields of ``fleet`` that differ from :class:`FleetSpec`'s defaults.

    The same omission rule as the config hash (policy v2,
    :func:`config_hash_payload`): a field at its default is absent.

    Returns:
        Field name → value (JSON-ready), in field order; empty for a default
        fleet.
    """
    return fleet.model_dump(mode="json", exclude_defaults=True)


class OracleSpec(BaseModel):
    """Downstream wave-detection oracle realism (CLAUDE.md §4.3).

    JAD's detection stage reads the downstream speed field. A *perfect* oracle
    sees the field instantaneously and exactly; real detection is late and
    noisy. This block makes the oracle swappable so that, as §4.3 requires,
    every headline JAD result is also reported under a degraded oracle.

    ``delay_s`` makes the controller observe the field as it was ``delay_s``
    ago (its own position stays current — it is the *traffic state* that is
    stale, as with loop-detector or probe latency). ``amplitude_noise_frac``
    multiplies each observed bin speed by ``1 + U(-f, +f)`` drawn per bin per
    control step from the run's seeded RNG. Defaults are a perfect oracle, so
    existing configs are unaffected.
    """

    kind: Literal["perfect", "noisy"] = "perfect"
    delay_s: float = Field(default=0.0, ge=0.0, le=120.0)
    """Detection latency [s]; §4.3 names 10–60 s as the realistic range."""
    amplitude_noise_frac: float = Field(default=0.0, ge=0.0, le=1.0)
    """Multiplicative speed error per bin; §4.3 names ±20% (0.2)."""

    @model_validator(mode="after")
    def _check_kind(self) -> Self:
        if self.kind == "perfect" and (self.delay_s > 0.0 or self.amplitude_noise_frac > 0.0):
            raise ValueError("kind='perfect' cannot carry delay_s or amplitude_noise_frac")
        if self.kind == "noisy" and self.delay_s == 0.0 and self.amplitude_noise_frac == 0.0:
            raise ValueError("kind='noisy' needs a nonzero delay_s or amplitude_noise_frac")
        return self


class AVSpec(BaseModel):
    """Controlled-vehicle deployment."""

    penetration: float = Field(default=0.0, ge=0.0, le=0.3)
    compliance: float = Field(default=1.0, ge=0.1, le=1.0)
    controller: str | None = None
    """Vehicle controller registry name; None ⇒ AVs drive as humans."""
    controller_params: dict[str, float] = Field(default_factory=dict)
    vsl: str | None = None
    """Segment controller registry name (VSL); None ⇒ no VSL."""
    vsl_params: dict[str, float] = Field(default_factory=dict)
    oracle: OracleSpec = Field(default_factory=OracleSpec)
    """Wave-detection oracle realism for downstream-reading controllers (JAD)."""
    emergency_handback: bool = True
    """Hand a commanded vehicle back to its car-following model for any step
    in which the model must brake harder than a command can (2026-09-26,
    WP-95; docs/I24_STRATEGIES.md, dated section). Added off by default
    (hash-neutral when off) on 2026-09-26; **on by default since 2026-10-04**
    by owner decision (WP-98; config-hash policy version 3, docs/CONTRACTS.md
    §2). ``False`` reproduces the pre-2.6 command path, the one every
    vehicle-controller result through release 2.5.0 ran under (WP-95: under
    it the I-24 strategy sweep's FollowerStopper cells recorded 311
    collisions, none with the handback).

    Every compliant AV is driven by ``vehicle.setSpeed`` under SUMO's default
    speed mode 31 (CLAUDE.md §3.3). In SUMO 1.27.1 a ``setSpeed`` target is
    held until ``setSpeed(-1)`` (``libsumo/Vehicle.cpp`` 1924–1938), and in
    every step ``MSVehicle::processTraCISpeedControl`` (``MSVehicle.cpp``
    4014–4043) replaces the model's own next speed with it, clamped by
    ``Influencer::influenceSpeed`` (493–520): first down to the safe speed
    (bit 0), then *up* to ``minNextSpeed(v)`` (bit 2). The second clamp
    wins, so a commanded vehicle never brakes harder than
    ``max(decel, min(emergencyDecel, 1.5))`` for SUMO's IDM
    (``MSCFModel_IDM.cpp`` 80–89) or ``decel`` for EIDM, while its model
    alone (``MSCFModel::finalizeSpeed``, ``MSCFModel.cpp`` 199–265) and every
    human may brake up to ``emergencyDecel`` (9 m/s² for a passenger car).
    When the leader brakes harder than that, or a vehicle enters the lane
    close ahead, the AV runs into it.

    With ``True``, in every step and for every AV holding a command, the
    runner asks the model for its follow speed behind the current leader
    (``vehicle.getFollowSpeed``); when it lies below the lowest speed the
    command can reach this step (``v`` less that deceleration times the step
    length) the command is withdrawn (``setSpeed(-1)``) and the model brakes
    with its own authority, and the held command is re-applied in the first
    step in which it can again brake enough. Without such a step the
    runner's TraCI writes, and so the run, are those of ``False``.
    Counted in ``meta.json["av_emergency_handback"]``; no effect without a
    ``controller``."""
    release_off_corridor: bool = True
    """Release a compliant AV's command (``setSpeed(-1)``) once it has left
    the controlled corridor (2026-09-26, WP-96; docs/I24_STRATEGIES.md, dated
    section). Added off by default (hash-neutral when off) on 2026-09-26;
    **on by default since 2026-10-04** by owner decision (WP-98; config-hash
    policy version 3). ``False`` reproduces the pre-2.6 behaviour.

    The dispatch commands a compliant AV only on a corridor edge, but a
    ``setSpeed`` target is held until ``setSpeed(-1)`` (SUMO 1.27.1,
    ``libsumo/Vehicle.cpp`` 1924–1938). With ``False`` an AV that leaves by
    an off-ramp drives the ramp at its last command, under the command's
    deceleration bound (``emergency_handback``), until it arrives; a last
    command of 0 stops it there for the rest of the run. With ``True``, in
    every step, each AV holding a controller command that is on an edge
    outside the corridor (not an internal junction edge, and not held by a
    scripted merge or weaving section at that moment) is released to its
    car-following model and not commanded again unless it re-enters the
    corridor. Counted in ``meta.json["av_off_corridor"]``; no effect without
    a ``controller``, or on a network whose every edge is a corridor edge (a
    ring, a generated corridor, an OSM import without off-ramps)."""
    observe_close_leader: bool = True
    """Report the leader to the controller when the bumper gap is below the
    AV's own ``s0`` (2026-09-26, WP-96; docs/I24_STRATEGIES.md, dated
    section). Added off by default (hash-neutral when off) on 2026-09-26;
    **on by default since 2026-10-04** by owner decision (WP-98; config-hash
    policy version 3). ``False`` reproduces the pre-2.6 behaviour.

    ``vehicle.getLeader`` returns the gap net of the ego's ``minGap`` (the
    drawn ``s0``). With ``False`` a negative value is read as "no leader"
    (``gap = inf``, ``v_leader = nan``), so at a bumper gap below ``s0`` the
    controller is told the road is free: FollowerStopper and its capacity
    variant command ``U``, PI with saturation blends toward ``U + v_catch``,
    where the true gap commands 0 or the leader's speed. With ``True`` the
    leader is reported with its bumper-to-bumper gap, floored at 0 m. JAD
    and the superseded ``pi_meanfrac`` do not read the leader. Counted in
    ``meta.json["av_close_leader"]``; no effect without a ``controller``."""


class SimSpec(BaseModel):
    """Time discretization and output cadence."""

    duration_s: float = Field(gt=0)
    step_length_s: float = Field(default=0.5, gt=0, le=1.0)
    action_step_s: float = Field(default=0.5, gt=0)
    warmup_s: float = Field(default=0.0, ge=0)
    """Discarded from metrics; still simulated and recorded."""
    output_hz: float = Field(default=2.0, gt=0, le=10.0)
    lateral_resolution_m: float | None = Field(default=None, gt=0.0, le=4.0)
    """SUMO ``--lateral-resolution`` [m]: ``None`` (default) keeps the
    lane-discrete LC2013 lane-change model; a value switches SUMO to the
    sublane model (LC_SL2015), in which vehicles occupy continuous lateral
    positions and merge gradually instead of jumping lanes when a whole-lane
    gap exists. Exposed for on-ramp merges, where the lane-discrete model
    produced a self-sustaining crawl on the I-24 replica (right lane at
    6–9 km/h upstream of the gore under every lane-change parameter tried,
    docs/I24_VALIDATION.md §0.5). Car-following is untouched; runs are slower."""


class PerturbationSpec(BaseModel):
    """A seeded shock. Presence of this block ⇒ seeded=True everywhere."""

    t_s: float = Field(ge=0)
    position_m: float = Field(ge=0)
    duration_s: float = Field(gt=0)
    v_drop_ms: float = Field(gt=0)
    """Commanded slowdown magnitude below prevailing speed [m/s]."""


class LaneClosureSpec(BaseModel):
    """A temporary lane closure (work zone, incident) on the corridor.

    Micro tier: for ``t ∈ [t_start_s, t_end_s)`` the listed lanes of every
    corridor edge overlapping ``[start_m, end_m)`` (linear x along the
    corridor) refuse all vehicle classes, so traffic changes lanes ahead of
    the closure through the ordinary strategic lane-change logic and any
    vehicle caught on a closed lane leaves it; the original permissions are
    restored at ``t_end_s``. Macro tier (single pipe): the flux through the
    interfaces of the overlapped cells is capped at the fundamental
    diagram's capacity times the share of lanes left open. A closure is an
    imposed disturbance, so the run is labelled ``seeded=True`` like a
    seeded perturbation (CLAUDE.md §0.2): waves at a closure are not the
    emergent phenomenon.
    """

    start_m: float = Field(ge=0.0)
    end_m: float = Field(gt=0.0)
    lanes: list[int] = Field(min_length=1)
    """SUMO lane indices closed (0 = rightmost) on each overlapped edge; an
    index beyond an edge's lane count is skipped there and recorded."""
    t_start_s: float = Field(ge=0.0)
    t_end_s: float = Field(gt=0.0)
    label: str = ""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.end_m <= self.start_m:
            raise ValueError("closure end_m must exceed start_m")
        if self.t_end_s <= self.t_start_s:
            raise ValueError("closure t_end_s must exceed t_start_s")
        if any(i < 0 for i in self.lanes) or len(set(self.lanes)) != len(self.lanes):
            raise ValueError("closure lanes must be distinct non-negative lane indices")
        return self


class ManagedLaneSpec(BaseModel):
    """A managed (HOV) lane: for the window only eligible vehicles may use it.

    Micro tier: the listed lanes of every corridor edge overlapping
    ``[start_m, end_m)`` (measured like a closure, from the start of the
    analysis corridor) admit only SUMO class ``hov`` for ``[t_start_s,
    t_end_s)`` and get their permissions back afterwards; eligible vehicles
    are the share ``FleetSpec.hov_fraction`` of passenger vehicles, drawn per
    vehicle and written with ``vClass="hov"`` (heavy vehicles are never
    eligible). Outside the window every vehicle may use the lane. A managed
    lane is a standing rule of the road, not a disturbance: it does NOT set
    ``seeded=True``. The macro tier does not represent it (meta says so).
    """

    start_m: float = Field(ge=0.0)
    end_m: float = Field(gt=0.0)
    lanes: list[int] = Field(min_length=1)
    """SUMO lane indices (0 = rightmost); on a four-lane freeway the left
    lane is index 3."""
    t_start_s: float = Field(ge=0.0)
    t_end_s: float = Field(gt=0.0)
    label: str = ""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.end_m <= self.start_m:
            raise ValueError("managed lane end_m must exceed start_m")
        if self.t_end_s <= self.t_start_s:
            raise ValueError("managed lane t_end_s must exceed t_start_s")
        if any(i < 0 for i in self.lanes) or len(set(self.lanes)) != len(self.lanes):
            raise ValueError("managed lanes must be distinct non-negative lane indices")
        return self


class MacroOptions(BaseModel):
    """Macro-tier (CTM screening) solver options — CLAUDE.md §5.

    Two knobs of the screening tier that are model choices rather than
    physics of the corridor, so they are carried on the scenario (and in the
    config hash when set) instead of being runner-call arguments no artifact
    records: the CTM cell length and which discretization of the moving
    bottleneck the AV cells use. The micro tier has neither a cell grid nor a
    moving flux constraint and ignores the block (it says so in its
    ``meta.json`` notes rather than dropping it silently).

    Attributes:
        dx_m: Target cell length [m]; the grid uses
            ``n_cells = max(10, round(length / dx_m))``, so the realized
            ``Δx`` recorded in ``meta.json["grid"]`` can differ slightly.
            The CFL step is derived from it (``macrosim.ctm.cfl_max_dt``).
        bottleneck_variant: Discretization of an AV's moving bottleneck —
            ``"flux_cap"`` (the v1 constraint ``F ← min(F, ρ·v*)``, the
            discrete analog of the Delle Monache–Goatin (2014) moving flux
            constraint) or ``"capacity"`` (``F ← min(F, α·q_max(v*))``).
            CLAUDE.md §5.5 asks for both to be reachable and compared
            against micro-tier ground truth.
    """

    model_config = ConfigDict(extra="forbid")

    dx_m: float = Field(default=100.0, gt=0.0, le=2000.0)
    bottleneck_variant: Literal["flux_cap", "capacity"] = "flux_cap"


class ScenarioConfig(BaseModel):
    """A complete, hashable scenario description."""

    name: str = Field(min_length=1)
    tier: Literal["micro", "macro"] = "micro"
    network: Network
    fleet: FleetSpec = Field(default_factory=FleetSpec)
    av: AVSpec = Field(default_factory=AVSpec)
    sim: SimSpec
    fd_calibration: str | None = None
    """Path to an ``FDCalibration`` artifact (as given, else resolved against
    the repository root — the same rule as ``FleetSpec.idm_calibration``, and
    confined to the API's allow-listed data roots). When set, the macro tier
    runs on that fitted triangular diagram instead of the uncalibrated
    ``v1_legacy`` preset (CLAUDE.md §5.1: FD parameters are calibrated
    per-corridor inputs, not constants) and ``meta.json["fd"]`` names the
    artifact. The micro tier has no fundamental diagram and does not use it;
    the field is deliberately tier-independent so the same scenario can be
    run on both tiers (the micro runner notes that it was ignored)."""
    macro: MacroOptions | None = None
    """Macro-tier solver options (:class:`MacroOptions`); ``None`` keeps the
    runner defaults (Δx = 100 m, ``flux_cap``)."""
    perturbation: PerturbationSpec | None = None
    closures: list[LaneClosureSpec] = Field(default_factory=list)
    """Temporary lane closures (:class:`LaneClosureSpec`); any entry labels
    the run ``seeded=True``."""
    managed_lanes: list[ManagedLaneSpec] = Field(default_factory=list)
    """Managed (HOV) lane rules (:class:`ManagedLaneSpec`); eligibility comes
    from ``FleetSpec.hov_fraction``."""
    seed: int = 42
    replicates: int = Field(default=20, ge=1, le=MAX_REPLICATES)
    """Seeded replicates per run. The ≥ 20 floor for headline claims is
    CLAUDE.md §0.6; the ``MAX_REPLICATES`` ceiling is a resource guard — a
    config is a request to execute this many simulations, and the largest
    study in this repository (the 540-run M3 sweep) uses 20 per cell."""

    @model_validator(mode="after")
    def _check_heavy_lane_shares(self) -> Self:
        """``HeavyVehicleSpec.lane_shares`` must match the entry lane count.

        Checked here and not on the fleet, which cannot see the network: a
        corridor states its lane count, an OSM import inherits it from the map
        (checked at run time by the route writer), and a ring has no entry.
        """
        heavy = self.fleet.heavy
        shares = None if heavy is None else heavy.lane_shares
        if shares is None:
            return self
        net = self.network
        if isinstance(net, RingNetwork):
            raise ValueError(
                "heavy.lane_shares needs a corridor or OSM network (a ring has no entry)"
            )
        if isinstance(net, CorridorNetwork) and len(shares) != net.lanes:
            raise ValueError(f"heavy.lane_shares has {len(shares)} entries for {net.lanes} lanes")
        entry = net.entry_lane_shares
        if entry is not None and len(shares) != len(entry):
            raise ValueError(
                f"heavy.lane_shares has {len(shares)} entries against "
                f"{len(entry)} entry_lane_shares"
            )
        return self

    @property
    def seeded(self) -> bool:
        """True when results come from an imposed disturbance — a seeded shock
        or a lane closure — and must be labeled as such (CLAUDE.md §0.2)."""
        return self.perturbation is not None or bool(self.closures)

    @classmethod
    def from_yaml(cls, path: str | Path) -> ScenarioConfig:
        raw = yaml.safe_load(Path(path).read_text())
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: expected a mapping at top level")
        return cls.model_validate(raw)

    def to_yaml(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
        )


CONFIG_HASH_VERSION: Final[int] = 3
"""Version of the hashing policy (docs/CONTRACTS.md §2). Bump it whenever a
field DEFAULT changes (a default change is a physics change and must move
every hash) — `tests/test_flowstate_core/test_config_hash.py` pins the
defaults snapshot and fails when one drifts without a bump.

History: 1 — sha256 of the full dump (before 2026-09-06); 2 — defaults
excluded (2026-09-06); 3 — the same payload rule, bumped on 2026-10-04 for
the default changes of WP-98 (``AVSpec.emergency_handback``,
``release_off_corridor``, ``observe_close_leader`` and the scripted merge's
``force_guard`` turned on)."""


def config_hash_payload(cfg: ScenarioConfig) -> dict[str, Any]:
    """The object that is hashed: the policy version plus the config with
    every field at its default omitted (policy v2, 2026-09-06).

    Omitting defaults means a new optional field leaves the hash of every
    scenario that does not use it unchanged, so schema growth no longer
    invalidates goldens, sweeps and run trees; the network's ``kind`` is
    kept explicitly since it is a default-valued discriminator.
    """
    dumped = cfg.model_dump(mode="json", exclude_defaults=True)
    network = dict(dumped.get("network") or {})
    network["kind"] = cfg.network.kind
    dumped["network"] = network
    return {"hash_version": CONFIG_HASH_VERSION, "config": dumped}


def _digest(payload: Mapping[str, Any]) -> str:
    """12 hex chars of the sha256 of ``payload``'s canonical JSON form."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def config_hash(cfg: ScenarioConfig) -> str:
    """12-hex-char sha256 of the canonical JSON form of
    :func:`config_hash_payload` (sorted keys, no whitespace)."""
    return _digest(config_hash_payload(cfg))


#: The ``AVSpec`` fields whose default policy v3 changed (2026-10-04, WP-98),
#: with their policy-v2 default. ``SCRIPTED_MERGE_DEFAULTS["force_guard"]``
#: changed too, but it lives outside the model: a ``merge_params`` block is
#: hashed as written under both policies.
V2_AV_DEFAULTS: Final[Mapping[str, bool]] = {
    "emergency_handback": False,
    "release_off_corridor": False,
    "observe_close_leader": False,
}


def config_hash_v2(document: Mapping[str, Any]) -> str:
    """The policy-v2 hash (2026-09-06 to 2026-10-03) of a scenario document.

    For provenance checks against a record written before 2026-10-04 — a fit
    artifact's ``base_config_hash``, a battery's ``config_hash``, a scenario
    header — which quotes a version-2 hash (docs/CONTRACTS.md §2). The
    document (a parsed scenario YAML or the dict a script builds) is read as
    v2 read it: an ``av`` key of :data:`V2_AV_DEFAULTS` it does not set takes
    its v2 default (False), and the payload omits the keys at that default.
    Every other field is hashed exactly as :func:`config_hash` hashes it
    (policy v3 kept v2's payload rule). A document produced by a full
    ``model_dump`` of a v3 configuration states the keys explicitly (True by
    default) and so does not reproduce its v2 hash. Never write this hash
    into a new record.

    Args:
        document: The scenario as a mapping (``ScenarioConfig`` input).

    Returns:
        The 12-hex-char version-2 hash.
    """
    doc = dict(document)
    av = dict(doc.get("av") or {})
    for key, v2_default in V2_AV_DEFAULTS.items():
        av.setdefault(key, v2_default)
    doc["av"] = av
    cfg = ScenarioConfig.model_validate(doc)
    payload = config_hash_payload(cfg)
    av_dump = dict(payload["config"].get("av") or {})
    for key, v2_default in V2_AV_DEFAULTS.items():
        av_dump.pop(key, None)
        value = getattr(cfg.av, key)
        if value != v2_default:
            av_dump[key] = value
    if av_dump:
        payload["config"]["av"] = av_dump
    else:
        payload["config"].pop("av", None)
    return _digest({"hash_version": 2, "config": payload["config"]})
