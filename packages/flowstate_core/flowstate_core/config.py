"""Scenario configuration schema (docs/CONTRACTS.md §2).

Pydantic-validated, YAML round-trippable, hashable. A ``ScenarioConfig`` plus a
seed fully determines a run; ``config_hash`` is recorded in every output
artifact so results always trace back to an exact configuration
(CLAUDE.md §0.5).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from flowstate_core.constants import HETEROGENEITY_FRAC_DEFAULT, IDM_DEFAULTS

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
    "courtesy": 0.0,
}
"""Defaults of :attr:`RampSpec.merge_params` for the ``scripted`` merge."""
SCRIPTED_MERGE_KEYS = frozenset(SCRIPTED_MERGE_DEFAULTS)

WEAVE_DEFAULTS: dict[str, float | None] = {
    **SCRIPTED_MERGE_DEFAULTS,
    "exit_accept_gap_s": 0.6,
    "vacate_ahead_m": 500.0,
    "vacate_max_veh_h": 0.0,
    "vacate_no_follower_braking": 0.0,
    "pair_release_s": 2.0,
    "exit_giveup_m": 5.0,
    "exit_giveup_patience_s": 0.0,
    "exit_abreast_patience_s": 0.0,
    # 0 since 2026-09-24 (block 3, WP-56): VM M, the four-hour I-94 battery
    # under this rule (runner 585e588), locks one of its 20 seeds — see the
    # key's paragraph in the docstring below. Not a fitted value.
    "exiter_yields": 0.0,
    "entrant_yields": 0.0,
    "exiter_yields_halting": 0.0,
    "exiter_yield_lead_s": 0.0,
    # 2026-09-24 (block 3, WP-57): the entrant's entry-speed anticipation on
    # the ramp's last metres, measured on the fixture grid and left off —
    # see the key's paragraph in the docstring below. A switch, not a fitted
    # value.
    "entry_speed_bound": 0.0,
    # 2026-09-24 (block 3, WP-58): the bounded hold — how long a chosen
    # gap's follower may be held for a changer that has stopped closing on
    # the gap, measured on the fixture grid and left off — see the key's
    # paragraph in the docstring below. A positive value is a time in
    # seconds (2 s, ``pair_release_s``'s two reaction times, is the measured
    # form); not a fitted value.
    "hold_release_s": 0.0,
    # 2026-09-24 (block 3, WP-60): the gated anticipation — an approaching
    # entrant's gap follower held only once the entrant arrives no later than
    # the follower can open the gap at its own b; see the key's paragraph in
    # the docstring below. A switch, not a fitted value.
    "anticipation_gate": 0.0,
    # 2026-09-24 (block 3, WP-62): exiters asked, inside the vacate window,
    # into the lane that feeds section lane 1 — the vacate rule's mirror for
    # the exit movement; see the key's paragraph in the docstring below. A
    # switch, not a fitted value.
    "exit_prepare": 0.0,
    # 2026-09-25 (block 3, WP-64): the swap — an entrant in the auxiliary lane
    # and an exiter beside it in section lane 1 that block each other change
    # in one step, each into the lane the other leaves; see the key's
    # paragraph in the docstring below. A switch, not a fitted value.
    "swap_pairs": 0.0,
    # WP-67 (2026-09-25, block 3): the crossings spread along the section —
    # a switch; the onset positions are derived from the section's geometry
    # (its unforced length less one cooperative gap opening), not fitted
    "spread_crossings": 0.0,
    # WP-70 (2026-09-25, block 3): the ramp's outlet — an exiter holds no
    # entrant that still owes its change out of the auxiliary lane while that
    # entrant is on the ramp or within the stretch the entrants need to leave
    # it; a switch, not a fitted value: the stretch is derived from the two
    # movements' crossing distributions at the defaults
    # (microsim.runner.WEAVE_OUTLET_ENTRANT_M, WEAVE_OUTLET_EXIT_RESERVE_M)
    "ramp_outlet": 0.0,
    # WP-73 (2026-09-25, block 3): the exit priority from where the exiter's
    # own lane-end braking begins — a switch, not a fitted value: the onset is
    # the stop term of the SUMO car-following model driving the vehicle, at its
    # own drawn parameters and current speed
    # (microsim.runner._weave_brake_onset_m; EIDM: vT + v^2/(2 sqrt(ab)))
    "exit_priority_onset": 0.0,
    # WP-75 (2026-09-25, block 3): the ramp anticipation spares the exiters —
    # an approaching entrant's gap follower that is itself bound for the
    # paired exit is not held; a switch, not a fitted value
    "anticipation_spares_exiters": 0.0,
    # WP-80 (2026-09-25, block 3): the follower-side time gap of each
    # movement, apart from the leader side's (``accept_gap_s`` /
    # ``exit_accept_gap_s``). None = unset: the follower side reads the
    # leader side's key, as every run before WP-80 did — not a fitted value.
    # The calibrated per-side values are a proposal in
    # artifacts/i24_critical_gaps.json (``proposal``, ``lead_side_only`` /
    # ``lag_side_only``), not defaults.
    "accept_lag_gap_s": None,
    "exit_accept_lag_gap_s": None,
}
"""Defaults of :attr:`WeaveSpec.weave_params`: the ``scripted`` merge's keys
(applied to the entering movement, ``courtesy`` to both movements) plus
``exit_accept_gap_s``, the time gap the exiting movement accepts,
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
for. Not a fitted value. ``vacate_no_follower_braking`` (same date): ``1``
selects the re-derived form of the rule — a vehicle is asked only on a step
when the target-lane gap it is in accepts it without the follower braking
(the weave's time gaps plus the follower's IDM desired gap at its current
speed), under mode 768 (no speed adaptation), re-evaluated every step; ``0``
(the default) keeps the third derivation's form (asked once, mode 512, SUMO
adapting the vehicle's speed and informing the follower). The re-derived
form was measured and not made the default: at the corridor's demand the
candidates reach the window far slower than the target lane, no gap accepts
them, and the section's own lock returns (docs/WEAVE_MODEL_PLAN.md, dated
section). Both keys are hash-neutral unless set. ``pair_release_s`` (2026-09-24, block 3, fifth
derivation): how long an entering and an exiting vehicle may stand within one
vehicle length of each other in section lanes 0 and 1, both below the creep
speed (``microsim.runner.SCRIPTED_MERGE_CREEP_MS``), before the pair is
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
lane end. ``exit_giveup_patience_s`` (2026-09-24, block 3, WP-52, the
bounded give-up patience): the longest a halted exiter within ``exit_giveup_m``
whose request is refused waits before it is given up, while the refusal is a
transient of its auxiliary-lane follower still braking towards the gap — the
follower reported this step is the one reported last step, its speed is
still falling by more than ``microsim.runner.WEAVE_GIVEUP_DECEL_TOL_MS2`` per
step and it has not come to rest (``microsim.runner._weave_giveup_patient``).
The wait ends, and the exit is given up, the first step the follower's speed
is no longer falling, it is at rest, no follower is reported, or the bound
is reached — the bound being this value or the follower's own braking time
to rest at its ``b`` from its speed on the first refused step, ``v_F / b_F``,
whichever is shorter. The wait is counted in ``n_giveup_waited``
(vehicle-steps). The default is **0** — give up on the first refused step,
the exit-side derivation's behaviour — because the rule was measured and
found not to help (docs/WEAVE_MODEL_PLAN.md, dated section): on the 30
fixture runs of the speed-aware acceptance's grid the give-ups read 44 at 0
against 48 at 10 s, because the give-up at the gore's end is not the braking
transient the rule waits for — of the 44, 34 are a lane-0 vehicle overlapping
the halted exiter and the 6 followers that were braking towards the gap were
caught by the cooperation's hold inside their own brake distance at ``b``, so
they slid alongside whatever the wait. A positive value is a measured option,
never a lock (the bound and the deceleration condition end every wait; 1–16
vehicle-steps per run at 10 s). Not a fitted value. ``exit_abreast_patience_s``
(2026-09-24, block 3, WP-53, the abreast state): the longest a halted exiter
within ``exit_giveup_m`` whose request is refused waits for the
auxiliary-lane vehicle *beside* it (a negative reported gap on either side)
to clear its front — while that vehicle is not a driven entrant of the
section (halted at the end of its lane beside the exiter, the crossing pair
at the lane ends: nothing local resolves it and the exiter's reroute is
what frees both), is moving, and would clear the exiter's leader side at its
current speed within the budget left of this value since the first refused
step (``microsim.runner._weave_giveup_abreast``, the distance from
``_weave_abreast_clear_m``); the budget is shared with
``exit_giveup_patience_s`` and never renewed, and the wait is counted in
``n_giveup_waited`` like the other. The default is **0** — give up on the
first refused step — because the rule was measured on the same 29-run
fixture grid as WP-52 and found not to help (docs/WEAVE_MODEL_PLAN.md, dated
section): at 10 s it rescues the exiters it was written for (the vehicle
sliding past, 10 → 1 of the give-ups) and the give-ups still read 46 against
44, the waited exiter meeting the next follower inside its brake distance,
while the wait holds lane 1 at the gore (T.H.52 at capacity, seed 4: the
entrance 386 of 466 against 401); of the 44 give-ups, 24 are a driven
entrant halted beside the exiter, which no wait moves. 5 and 20 s read as
10 s. A positive value is a measured option, never a lock (the clearing
condition and the bound end every wait; at most 15 vehicle-steps per run
at 10 s). Not a fitted value. ``exiter_yields`` (2026-09-24, block 3,
WP-54, the crossing pair): ``1`` has an exit-bound changer
inside its forced zone stop behind a driven entrant halted at the end of
the auxiliary lane ahead of it — driven towards a virtual leader one
entrant ``minGap`` behind the entrant's rear, the exit priority's hold with
the roles exchanged, only while the stop is feasible at its own ``b``
(``microsim.runner._weave_yield_at_ends``) — so the entrant changes ahead
of it and the auxiliary lane it blocked moves again. The per-pair trace of
the 24 crossing-pair give-ups on the fixture grid showed that such an
entrant is not freed by the give-up of the exiter beside it: it stays,
refused into lane 1 by every lane-1 vehicle arriving inside its brake
distance, and the next exiters halt beside it and are given up in turn
(one entrant, three give-ups, on ``weave_th52.osm`` at the corridor's
demand, seed 5). Measured on the same 29-run grid as WP-52 and WP-53
(docs/WEAVE_MODEL_PLAN.md, dated section): give-ups 44 → 39, exits 5,988
→ 6,015 of 6,131 → 6,142 reached, the entrances 5,944 → 5,973, pair
releases 218 → 169, no lock, no collision, the T.H.52 rows and the golden
unchanged; from the whole section instead of the zone it read worse (46
given up, the T.H.52 capacity fixture 4 / 5 given up at seeds 3 / 4
against 1 / 1). **The default is 0 since 2026-09-24 (block 3, WP-56)**:
on the four-hour I-94 corridor (VM M, the 20-seed battery under runner
585e588, ``artifacts/mndot_rounds/weave_2026-09-24/battery_corrected_inputs_exiter_yields_585e588.json``)
the rule locks one seed — 6904272788004776631 departs 0.356 of its
demand against 0.852 without the rule (VM K), with 18,259 exiter-yield
vehicle-steps against 770–3,500 on the other seeds and every on-ramp
starved — while the other 19 seeds read 0.843–0.899 (battery 0.836
departed, speed RMSPE 0.716, GEH 0.077, 19 collisions against VM K's 15,
given-up exits 0.6 / 0.9 % against 0.8 / 1.0 %, VM K's 0.743 seed at
0.865). A rule that locks a seed of the flagship corridor cannot ship on
until it is bounded; ``1`` switches it on. Hash-neutral unless set; a
switch, not a fitted value. ``entrant_yields`` (same package): ``1`` has a moving
driven entrant beside an exiter whose forced change is due fall behind the
exiter's rear at its own ``b`` (the same virtual leader, the roles as the
priority has them) while it can still come to rest behind where the
exiter's rear will be at the latest, the lane end. The default is **0**:
the per-pair trace found the bound met in 2 of the 24 pairs (by 1.4 m) —
at the due moment the entrant beside the exiter is halted at its lane
end already, abreast at speed parity with both braking for their lane
ends at more than its ``b``, or closing from behind already held at
``-b`` — and on the grid it bound on 21 vehicle-steps, five pairs in
five runs (give-ups 44 → 36 with 5,987 exits; two of the five pairs
resolve as derived, the rest is the sequence moving), returned nothing on top of the exiter's yield (39 → 39, nine
fewer exits, 37 fewer entrants) and, asked from the exiter's zone entry,
locked the Ruth St module at the 271 m window (lane 1 at 0.0 m/s for
seven minutes). Hash-neutral unless set; a switch, not a fitted value.
``exiter_yields_halting`` (2026-09-24, block 3, WP-55, the forming pair):
``1`` brings the exiter's yield forward from "the entrant ahead is halted"
to "the entrant ahead will halt at its lane end before the exiter reaches
the gore" — the entrant committed to its lane end (its brake distance at
its own ``b`` reaches it) and there first at the two speeds
(``microsim.runner._weave_halting_first``) — the exiter then stopping one
entrant ``minGap`` behind where the entrant's rear will rest, one length
short of the lane end, under the same feasibility bound at its own ``b``.
The default is **0**: measured on the same 29-run grid as WP-52..54
(docs/WEAVE_MODEL_PLAN.md, dated section) it read worse — give-ups 39 → 43,
the entrances 5,973 → 5,958, exits 6,015 → 6,017 of 6,142 → 6,139 reached,
binding on 53 more vehicle-steps in five runs, on entrants committed only
through a small drawn ``b`` (0.65–1.22 m/s² at 10–15 m/s) that changed or
halted regardless — and the give-ups it was written for have no move at
the exiter's ``b`` inside the zone (12 of the 39: the stop needed 41–165 m
against 60–69 m offered). Hash-neutral unless set; a switch, not a fitted
value. ``exiter_yield_lead_s`` (2026-09-24, block 3, WP-56, the
brake-scaled zone): a positive value has the exiter's yield
(``exiter_yields``) asked *outside* the fixed forced zone as well — from
the step at which the exiter is within this many seconds of travel of the
last point at which it can still stop at its own ``b`` behind the halted
entrant's rest point, ``room − v²/(2·b) ≤ v · lead_s``
(``microsim.runner._weave_yield_early``); for an entrant halted at the
lane end that is a yield zone of ``max(force_within_m, v²/(2·b) + v ·
lead_s + len_E + s0_E + s0_X)`` upstream of the gore, scaled to the
exiter's own brake distance where the fixed 80 m is blind to it (the
corridor fleet draws ``b`` down to 0.53 m/s²; WP-55 counted 12 give-ups
that needed 41–165 m against the 60–69 m the zone offers); the entrant
must be halted within the fixed zone of its own lane end (the crossing
pair, not the queue at the section start). The forced change, the exit
priority and the give-up keep the fixed zone. Bounded: outside the fixed
zone an exiter the rule has held below the creep speed for longer than
``pair_release_s`` lapses and is not asked again while that entrant
stands ahead of it; nobody else is commanded through the rule; it is
re-evaluated every step and the lapse clears once no halted entrant is
ahead. Inert unless ``exiter_yields`` is set. The default is **0** (off,
hash-neutral unless set): measured at 0.5–3 s on the same 29-run grid as
WP-52..55 with the exiter's yield on (docs/WEAVE_MODEL_PLAN.md, dated
section) it binds outside the zone on 16 vehicle-steps at 1 s, six
exiters in four runs, where the cooperation already brakes the exiter at
``−b`` towards the halted entrant as the follower of its gap — give-ups
39 → 39, exits 6,015 → 6,019, the entrances equal — and its one
mechanism row swings from the grid's best to its worst reading as the
lead goes 0.5 → 3 s on the same two vehicles; the 12 give-ups it was
written for have no feasible stop on any section step. Without the
lane-end condition it re-rolled the T.H.52 rows from single steps
(35–42 given up, 17–53 fewer entrants). A positive value is one human
reaction time or a few (Treiber & Kesting 2013, ch. 12, the figure
``pair_release_s`` doubles); not a fitted value. ``entry_speed_bound``
(2026-09-24, block 3, WP-57, the entrant's entry speed): ``1`` asks an
entering vehicle on the on-ramp within ``lookahead_m`` of the section to
enter no faster than the speed from which it can still halt at its own
comfortable deceleration with its front one ``minGap`` short of the
auxiliary lane's end — ``v ≤ √(2·b·(D − s0))`` with ``D`` the distance
from its front to the gore, the constant-``b`` braking curve that ends at
the lane end (``microsim.runner._weave_entry_speed_bound``,
``_weave_entry_bound``) — as a one-step speed ceiling below its own
car-following (:func:`microsim.runner._weave_command`, clipped at ``−b``,
SUMO's safety check on), re-evaluated every step on the ramp only and
never on the section, so that it does not arrive on a short auxiliary
lane at a speed from which no stop at ``b`` exists and form the crossing
pair at the lane ends by momentum (WP-55 counted 5 of the 12 entrants
heading such a pair as unable to stop within the 136 m Ruth St lane at
their ``b``, having entered at 15.7–19.4 m/s with ``b`` 0.59–0.99 m/s²).
Bounded by construction: the ceiling on the ramp is never below
``√(2·b·(L_S − s0))`` (11.9 m/s at the corridor fleet's smallest ``b`` on
Ruth St, 17.9 m/s on T.H.52), nobody else is commanded through the rule,
and the ramp throttle and the entrance demand are untouched. The
vehicle-steps on which it binds are counted in ``n_entry_bounded``. The
default is **0** (off, hash-neutral unless set): measured on the same
29-run fixture grid as WP-52..56 (docs/WEAVE_MODEL_PLAN.md, dated section
WP-57) it binds on 1,383 vehicle-steps, 187 entrants in the seven Ruth St
corridor-fleet runs (the fleet defaults' ``b`` never falls under the curve
on either fixture, so the T.H.52 rows and the golden are byte-identical),
takes the bound entrants from 21.5 to 18.0 m/s at the section start and
the Ruth St entrants that cannot stop within their lane from 40 to 27 of
≈ 590 — and reads worse: give-ups 44 → 54 (crossing pairs 24 → 31 on 16 →
17 halted entrants), 5,988 → 5,995 exits, the entrances 5,944 → 5,967,
forced changes deferred 5,439 → 6,644, no lock, no collision; the one row
it helps (Ruth St corridor fleet at the exit peak, seed 3: 9 → 5 given up,
lane 1 at the gore 2.8 → 6.1 m/s) is paid for on seeds 4 and 5 (1 → 12, 4
→ 8), where the entrant it slows below lane 1's speed is held by the
cooperation and the chains form behind entrants that *can* stop within
the lane at their ``b``. The form that removes the fast arrivals almost
entirely (the bound from the ramp's start, harness only: 40 → 5 of the
Ruth St entrants unable to stop) leaves 27 crossing pairs on 16 entrants
— the pair does not form by momentum. A switch, not a fitted value.
``hold_release_s`` (2026-09-24, block 3, WP-58, the bounded hold): a
positive value is the longest a chosen gap's follower may be held at IDM
towards the changer (``microsim.runner._weave_cooperate``) once the
changer has stopped closing on the gap — its projected arrival at the
gap, the remaining deficit to the leader-side gap the acceptance asks
over the rate it closed that deficit at during the last step, no longer
advancing step over step — while the follower's side of the gap is
already open by the acceptance's terms (the time gap, the follower
absorbing the changer within its ``b``, the changer outside the
follower's brake gap). On the first step the stall has lasted longer
than the bound the hold is dropped: the follower is not commanded, it is
blocked for that changer for one bound (no chain: a released follower is
not asked again for the same changer within the bound) and the changer
re-chooses its gap with the follower excluded — the next gap behind, into
which it drops once the follower has passed
(``microsim.runner._weave_hold_release``). Never dropped while the
changer is inside the follower's brake gap towards it (the speed-aware
guard's follower side: that braking is the hold's productive part), and
never for a pair standing below the creep speed — that pair is
``pair_release_s``'s, released after two reaction times with the partner
forcing its change. A time and not a distance bound because the hold's
cost is the follower's speed deficit integrated over time, a driver's
patience is in seconds, and at low speed — where the hold locks (VM M's
seed at a standstill) — a bound on the changer's travel never fires
(the distance form was measured beside it). Each drop is counted in
``n_hold_releases``. The default is **0** (off, hash-neutral unless set):
the hold trace at the default on the 29-run fixture grid
(docs/WEAVE_MODEL_PLAN.md, dated section WP-58) reads 5,248 of 17,810
changer–follower episodes stalled for more than 2 s, carrying 133,011 of
the 206,358 held changer-steps with a target on the follower (53,085 of
them past the 2 s mark), and 36 of the 40 longest stalls (22.5–43 s) are
entrants still on the ramp, within ``lookahead_m`` of the section at 3–10
m/s, holding a lane-1 follower at their speed — but every form of the
release reads worse than the default: at 2 s (``pair_release_s``'s figure) 8,330 holds
dropped, give-ups 44 → 74, exits 5,988 → 5,897 of 6,131 → 6,084 reached,
the entrances 5,944 → 5,830, lane-1 minutes at or below 5 m/s 14 → 31,
forced changes deferred 5,439 → 9,407, pair releases 218 → 458, and
T.H.52 at capacity, seed 5, near a lock (288 of 466 departed against 373,
lane 1 at the section start 0.4 m/s in nine minutes, 257 pair releases);
at 1 s 70 given up, at 4 s 59, on the section only 59 (74 unfinished),
without the exit priority 80, as a 40 m distance bound 66, with the
strict stall reading (the deficit not shrinking at all) 64 — and every
form fails the no-lock pin of the T.H.52 capacity fixture
(``test_th52_weave_at_capacity_does_not_lock``) at seed 4 or 5 or both,
which the default passes at seeds 3–5; with the exiter's yield on 66 and
63 given up against the yield's own 39, the pin failing at seed 5 and 4. The mechanism: a changer
released from a gap at speed parity is passed by the through platoon one
held follower at a time and reaches the section, or the lane end, with no
gap, so the forced changes, the deferrals and the pairs at the lane ends
grow. No collision in any form; the default is byte-identical to the
grid before the key. A positive value is two human reaction times
(Treiber & Kesting 2013, ch. 12) at 2 s; not a fitted value.
``anticipation_gate`` (2026-09-24, block 3, WP-60, the gated anticipation):
``1`` holds the follower of an approaching entrant's chosen gap (the ramp
anticipation of ``microsim.runner._weave_cooperate``) only from the step on
which the entrant's time to the section start, its distance over its speed
floored at the creep speed, is no longer than the time the follower needs to
open the gap at its own ``b`` — the positive root ``t_open = (Δv + √(Δv² +
2·b·D))/b`` of ``b·τ²/2 − Δv·τ − D = 0``, with ``Δv`` the follower's closing
speed on the entrant's projection and ``D = s0 + accept_gap_s · v_F − s_F``
the deficit to the acceptance's time gap; no hold while the discriminant is
negative, the follower able to shed its closing speed at ``b`` and keep the
gap (``microsim.runner._weave_coop_gate``) — and from then on while it stays
the chosen follower. The gap choice and the entrant's easing are untouched;
the withheld commands that would have bound are counted in
``n_anticipation_gated``. The default is **0** (off, hash-neutral unless
set): on the same 29-run fixture grid as WP-52..58 (docs/WEAVE_MODEL_PLAN.md,
dated section WP-60) it removes 88 % of the held steps on ramp entrants
(109,904 → 13,142) and reads worse on every criterion — give-ups 44 → 58,
exits 5,988 → 5,782, the entrances 5,944 → 5,415, lane-1 minutes at or below
5 m/s 14 → 31, forced changes deferred 5,439 → 10,549, pair releases 218 →
680, the T.H.52 capacity fixture 379 / 315 / 343 of 466 departed (395 / 401
/ 373 at the default) with lane 1 at the section start at or below 5 m/s in
6 / 14 / 9 minutes (2 / 5 / 4) and its no-lock pin broken at seeds 4 and 5;
no collision. The entrants reach the section without their gap: the early
hold is the positioning they arrive with. A switch, not a fitted value.
``exit_prepare`` (2026-09-24, block 3, WP-62, the exiters' early move): ``1``
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
the request living to the section start, or, with
``vacate_no_follower_braking``, one lane per accepting step under mode 768
with the changer's brake gap on the (slower) target lane's leader; bounded by
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
1); no collision. A switch, not a fitted value. ``swap_pairs`` (2026-09-25,
block 3, WP-64, the swap): ``1`` has a driven entrant in the auxiliary lane and
a driven exiter beside it in section lane 1 that block each other — each the
other's nearest vehicle across, and at least one of the two changes refused by
the acceptance because of the other — exchange lanes in one step, both changes
under mode 256 as an accepted change is, when the two clear the forced guard
against each other (at speed parity one ``minGap`` of each between bumpers),
each change is accepted against every other target-lane neighbour with the
partner removed, and no lane-2 vehicle could enter lane 1 beside the entrant in
the same step (``microsim.runner._weave_swap_step``; SUMO 1.27.1 executes the
two changes of such a pair in one step, front vehicle first, probed). Nobody
else is commanded, nothing is held; the pairs commanded are counted in
``n_swaps``. The default is **0** (off, hash-neutral unless set): on the
corridor section test's fixture (``tests/fixtures/weave_th52_corridor.osm``,
seeds 3 / 4 / 5; docs/WEAVE_MODEL_PLAN.md, dated section WP-64) it exchanges 44
/ 45 / 44 pairs, every one completed in the step, no collision, and no
criterion improves — the T.H.52 entrance 360 / 365 / 339 of 407 against 368 /
360 / 350, the exit end's lanes at or below 20 m/s in 11 / 12 / 13 of 16
windows against 10 / 11 / 13 — because 72–82 % of the blocked pair-steps are
refused on the offset (overlapping or nearer than the forced guard, a median
15–22 m into the section at 5–6 m/s, the two within about 1 m/s of each other),
where no exchange is possible until one of them drops back, and the form that
commands that drop reads worse; on the 29-run fixture grid the entrances fall 5,944 → 5,903 and the T.H.52
capacity fixture's no-lock pin fails at seeds 3 and 5 (3 and 6 exits missed).
A switch, not a fitted value. ``spread_crossings`` (2026-09-25, block 3,
WP-67, the crossings spread): ``1`` withholds each crossing — an entrant's
out of the auxiliary lane, an exiter's into it — until the vehicle reaches
its place in the spread, ``frac(n · φ)`` (the golden ratio's conjugate, by
the order the vehicles are taken) of the stretch in which waiting costs it
nothing: the section less the larger of the forced zone and the distance
from which its own IDM brakes for the end of its lane, less one cooperative
gap opening, all at its current speed (``microsim.runner._weave_spread_length``;
0 in free flow on the 305 m T.H.52 section, 212 m at 5 m/s). A vehicle whose
crossing is withheld is taken under the weave's lane-change mode on the step
before it can reach the section, so SUMO's own model cannot make the change
in the step it arrives (``_weave_handover_step``); an entrant whose crossing
is withheld is not anticipated on the ramp; and under the rule an entrant's
accepted change is never commanded beside an opposing entry into lane 1
(WP-64's guard). Withheld vehicle-steps are counted in ``n_spread_withheld``.
The default is **0** (off, hash-neutral unless set): on the corridor section
test's fixture (``tests/fixtures/weave_th52_corridor.osm``, seeds 3 / 4 / 5;
docs/WEAVE_MODEL_PLAN.md, dated section WP-67) it moves the crossings out of
the section's first 50 m (entrants 8 / 23 / 8 % there against 75 / 77 / 78 %,
exiters 7 / 16 / 7 % against 55 / 50 / 49 %) and the entry's lanes 0 and 1
hold 1.64 / 1.69 / 1.75 lanes' worth against 1.13 / 1.14 / 1.18, but the
section breaks down in its second half instead, the exit end's lanes read at
or below 20 m/s in 12 / 12 / 13 of 16 windows against 10 / 11 / 13, and
11 / 21 / 14 driven vehicles are unfinished against 1 / 6 / 8; on the
29-run fixture grid the entrances rise 5,944 → 6,060 but exits fall
5,988 → 5,860, unfinished rise 45 → 208 and the T.H.52 capacity fixture's
no-lock pin fails at seed 4 (lane 1 at the section start 2.0 m/s). A switch,
not a fitted value. ``ramp_outlet`` (2026-09-25, block 3, WP-70, the ramp's
outlet): ``1`` has an exit-bound changer's gap choice pass over every vehicle
on the on-ramp or in the auxiliary lane short of the stretch the ramp's
vehicles need to leave it, so that no exiter holds a vehicle in the ramp's
only outlet; the exiter still takes a gap the acceptance finds open there,
SUMO's own changes are untouched and the exit priority's hold is exempt. The
stretch (``microsim.runner._weave_outlet_length``) is 51.1 m, within which a
share q* = 0.773 of the entrants have left the auxiliary lane at the defaults,
capped so that an exiter keeps 173.8 m before the forced zone, within which
the same share of the exiters have entered it — the minimax split of the
section's unforced length between the two movements' needs on the corridor
section test's fixture; none on a section shorter than 253.8 m. The
exiter-steps on which the gap chosen without the rule has such a vehicle as
its follower are counted in ``n_outlet_spared``. The default is **0** (off,
hash-neutral unless set): on the corridor section test's fixture
(``tests/fixtures/weave_th52_corridor.osm``, seeds 3 / 4 / 5;
docs/WEAVE_MODEL_PLAN.md, dated section WP-70) the T.H.52 entrance departs
401 / 386 / 403 of 407 against 368 / 360 / 350 and the mainline 1,187 /
1,181 / 1,171 of 1,196 against 1,140 / 1,157 / 1,149, and the entry breaks
down later, but the exit end's lanes read at or below 20 m/s in 12 / 12 / 13
of 16 windows against 10 / 11 / 13 (seeds 3–12: the entrance 3,418 → 3,749,
those windows 123 → 126); on the 29-run fixture grid exits rise 5,988 →
6,035 and the entrances 5,944 → 6,055, but give-ups read 44 → 45, unfinished
45 → 70, and the T.H.52 capacity fixture's no-lock pin fails at seed 5 (3
exits missed); no collision. A switch, not a fitted value.
``exit_priority_onset`` (2026-09-25, block 3, WP-73, the exit priority from
where the braking begins): ``1`` gives an exiter still owing its change the
exit priority (``microsim.runner._weave_choose_gap``: the gap behind a vehicle
beside it is a candidate, the gap's follower holds one ``minGap`` farther
back, the commitment is kept) from the step on which its distance to the gore
is within the onset of its own model's braking for the end of its lane, at
its own drawn parameters and speed, latched — instead of from 4 s
(``force_after_s``) into the 80 m forced zone. The onset
(``microsim.runner._weave_brake_onset_m``) is the stop term of SUMO's
car-following model driving the vehicle (``FleetSpec.model``): for the EIDM
the IIDM's ``s* = vT + v²/(2√(ab))`` with no ``minGap`` (170 m at 20 m/s at
the corridor fleet's means), for the IDM that over ``√(1 − (v/v0)⁴)`` (226 m),
both read from SUMO 1.27.1's source and checked with ``vehicle.getStopSpeed``.
The forced change keeps its zone, and with ``ramp_outlet`` set the onset
priority's hold passes over the outlet's vehicles. Exiter-steps with the onset
priority before the zone's are counted in ``n_onset_priority``. The default is
**0** (off, hash-neutral unless set): on the corridor section test's fixture
(seeds 3 / 4 / 5, with ``ramp_outlet``; docs/WEAVE_MODEL_PLAN.md, dated
section WP-73) the exiters' crossings into the auxiliary lane in [51, 305) m
in minutes 1–4 are made at a median 17.0 / 16.7 / 12.2 m/s against 15.7 /
17.0 / 11.9 — the exiters reach the section's second half at a median 18.1 /
18.4 / 13.3 m/s, and even a priority from the section start leaves those
crossings at 17.1 / 14.6 / 11.9 m/s — the exit end's lanes read at or below
20 m/s in 11 / 10 / 13 of 16 windows against 12 / 12 / 13 (lanes 0 and 1:
79 of 80 over seeds 3–12 against 80), and T.H.52 departs 359 of 407 at seed
5; on the 29-run fixture grid exits fall 6,035 → 5,965, the T.H.52 capacity
fixture's no-lock pin fails at seeds 3 and 4 (at seeds 4 and 5 with the key
alone), and the key locks the Ruth St section at seed 5, where the outlet is
inert (lane 1 at the gore's end at 0.0 m/s from minute 16); no collision. A
switch, not a fitted value. ``anticipation_spares_exiters`` (2026-09-25,
block 3, WP-75, the exiters on the approach): ``1`` withholds the ramp
anticipation's hold (``microsim.runner._weave_cooperate``, an entrant still on
the ramp) on a gap follower that is itself bound for the paired exit — an
exiter held so that an entrant can enter lane 1 in front of it must itself
cross into the lane the entrant leaves; the gap choice, the commitment and the
entrant's easing are kept, and the section's own cooperation is untouched. The
gap behind the exiter is no alternative (the entrant is in one gap only, and
on the corridor section test's fixture the exiters behind an approaching
entrant are slower than it: passed over in the gap choice they leave it no gap
at all). Withheld holds that would have bound are counted in
``n_anticipation_exiter_spared``. The default is **0** (off, hash-neutral
unless set): on the corridor section test's fixture
(``tests/fixtures/weave_th52_corridor.osm``, seeds 3 / 4 / 5, with
``ramp_outlet``; docs/WEAVE_MODEL_PLAN.md, dated section WP-75) the exiters
reach the section start at a median 20.3 / 21.4 / 16.3 m/s against 18.5 /
18.0 / 16.2 in minutes 1–4, but the hold reappears in the section as the
section entrants' own, the entrants cross later, T.H.52 departs 377 / 345 /
319 of 407 against 407 / 391 / 382, lanes 0 and 1 of the last 60 m still read
at or below 20 m/s in every window, and the section locks at seed 5 (lanes 0
and 1 at 0.0–0.2 m/s from minute 13); seeds 3–12: T.H.52 3,750 → 3,464 (3,374 →
2,963 with the key alone). On the 29-run fixture grid give-ups rise 44 → 64
(45 → 59 with ``ramp_outlet``), the entrances fall 5,944 → 5,544 (6,055 →
5,897), and the T.H.52 capacity fixture's no-lock pin fails at seeds 4 and 5
(at all three with ``ramp_outlet``); no collision. A switch, not a fitted
value. ``accept_lag_gap_s`` and ``exit_accept_lag_gap_s`` (2026-09-25,
block 3, WP-80, the leader and follower gaps apart): the follower-side
time gap of the entering and of the exiting movement. Until WP-80 each
movement had one time gap, ``accept_gap_s`` / ``exit_accept_gap_s``,
applied to both sides of every test; the I-24 MOTION critical gaps (VM Z,
``artifacts/i24_critical_gaps.json``) read the two sides differently for
both movements, and WP-79 measured the one-value compromise failing on the
exit side. Now the existing key governs the leader side and the new key
the follower side, everywhere the movement's time gap enters: the
acceptance's ``s0 + A · v_F``, the forced guard's closing-speed bound on
the follower side (the guard is part of every accepted change, so a guard
on one value would re-impose the leader side on the follower side), the
cooperation's and the anticipation's gap targets for the gap's follower,
the swap's follower-side checks, the spread length's gap opening, and the
follower side of the vacate / early-move gap check that borrows the
entering key (``microsim.runner._weave_lag_gap_s``). **``None``, the
default, is unset: the follower side reads the leader side's key**, so a
run that sets neither key is byte-identical to one before WP-80 and the
config hash moves only when a key is set. Not fitted values: the
calibrated per-side values are a proposal in the artifact's ``proposal``
block (``lead_side_only`` / ``lag_side_only``), measured on the fixtures
in docs/WEAVE_MODEL_PLAN.md (dated section WP-80) and not made defaults:
with all four (entering 0.0 / 0.778 s, exiting 2.584 / 0.721 s) the 29-run
fixture grid's give-ups rise 44 → 134 and the T.H.52 capacity fixture's
no-lock pin fails at all three seeds, driven by the exiting leader side;
the entering pair alone holds the grid's totals (47 given up) and fails
the pin at seed 4."""
WEAVE_KEYS = frozenset(WEAVE_DEFAULTS)


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
    """Overrides of :data:`WEAVE_DEFAULTS`; unknown keys are rejected."""

    @model_validator(mode="after")
    def _check_params(self) -> Self:
        unknown = set(self.weave_params) - WEAVE_KEYS
        if unknown:
            raise ValueError(f"unknown weave_params keys: {sorted(unknown)}")
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
    merge: Literal["lane_change", "acceleration_lane", "zipper", "scripted", "weave"] = (
        "lane_change"
    )
    """How an on-ramp's acceleration lane hands its traffic to the mainline
    (micro tier, 2026-09-06). ``lane_change`` (default): the lane dead-ends
    and ramp vehicles change lanes under the lane-change model — on the I-24
    replica this locked the merge into a right-lane crawl under every
    parameter tried (docs/I24_VALIDATION.md §0.5). ``acceleration_lane``:
    SUMO's lane attribute of that name on the attach edge's rightmost lane
    (vehicles do not brake for the lane end). ``zipper``: the attach edge's
    rightmost lane is connected into the next corridor edge's rightmost lane
    alongside the mainline lane and the junction becomes a zipper, so ramp
    and mainline traffic interleave at the lane end instead of negotiating
    lane changes. Both need the acceleration lane to dead-end at the attach
    edge's end node (checked at run time) and are applied as netconvert
    patches recorded in ``meta.json``. ``scripted`` (2026-09-16): the network
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
    ``meta.json["weave_sections"]``."""
    merge_params: dict[str, float] = Field(default_factory=dict)
    """Tuning of the ``scripted`` merge (ignored by the other models).
    Keys and defaults: ``accept_gap_s`` 0.6 (time gap accepted to the mainline
    leader and follower, on top of the vehicle's ``s0``), ``force_after_s``
    4.0, ``force_within_m`` 80.0, ``change_duration_s`` 2.0, ``lookahead_m``
    120.0 (distance over which the mainline lane's speed is matched),
    ``courtesy`` 0.0 (m/s; when > 0 the mainline follower that blocks an
    otherwise acceptable gap is asked to hold its desired speed this far below
    the ramp vehicle's until the gap opens — courtesy yielding)."""
    weave: WeaveSpec | None = None
    """The weaving section this on-ramp opens (:class:`WeaveSpec`); required
    by, and only allowed with, ``merge="weave"``. Hash-neutral when unset."""

    @model_validator(mode="after")
    def _check_kind(self) -> Self:
        unknown = set(self.merge_params) - SCRIPTED_MERGE_KEYS
        if unknown:
            raise ValueError(f"unknown merge_params keys: {sorted(unknown)}")
        if self.merge_params and self.merge != "scripted":
            raise ValueError("merge_params apply to merge='scripted' only")
        if self.kind == "off" and self.weave is not None:
            raise ValueError("a weave is opened by an on-ramp; an off-ramp cannot carry weave")
        if self.kind == "on" and self.merge == "weave" and self.weave is None:
            raise ValueError("merge='weave' needs a weave block naming its exit_ramp")
        if self.weave is not None and self.merge != "weave":
            raise ValueError("a weave block applies to merge='weave' only")
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


CONFIG_HASH_VERSION: Final[int] = 2
"""Version of the hashing policy (docs/CONTRACTS.md §2). Bump it whenever a
field DEFAULT changes (a default change is a physics change and must move
every hash) — `tests/test_flowstate_core/test_config_hash.py` pins the
defaults snapshot and fails when one drifts without a bump."""


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


def config_hash(cfg: ScenarioConfig) -> str:
    """12-hex-char sha256 of the canonical JSON form of
    :func:`config_hash_payload` (sorted keys, no whitespace)."""
    canonical = json.dumps(config_hash_payload(cfg), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]
