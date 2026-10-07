"""A veto of one section captured as another section's "original" mode (review 2026-10-07, third regression review).

With ``weave_resolve_opposing`` on (and always under the measured merge
model), a section's opposing-entry resolution clears an undriven vehicle's
model bits for one step (1621 -> 1536) and records the real mode in its
``opp_veto``, restored at the start of its next step only if the vehicle is
still under the vetoed mode. A downstream section stepped later in the same
step whose vacate window (or exit-prepare window, or zone) covers the
upstream section's lanes read 1536 as the vehicle's own mode and held it
under 512; the restore found 512 and dropped the record; the hold handed
1536 back at its end, and the vehicle had lost its strategic, cooperative,
speed-gain and keep-right changes for the rest of the run.

``microsim.runner._lc_mode_owned`` now consumes a pending veto of any
section (``ws["opp_veto_all"]``, wired by ``_weave_share_vetoes`` in
``run_micro``) and returns the recorded mode; each section keeps its own
``opp_veto`` for its restore. Fake-TraCI harness of
``test_microsim_merge_managed_meter`` (no SUMO).
"""

from __future__ import annotations

from flowstate_core.config import SCRIPTED_MERGE_DEFAULTS
from microsim.runner import (
    LC_MODE_MODEL_BITS,
    LC_MODE_SCRIPTED_SAFE,
    _lc_mode_owned,
    _measured_handover_step,
    _scripted_merge_step,
    _weave_share_vetoes,
    _weave_step,
)
from tests.test_microsim.test_microsim_merge_managed_meter import (
    _GuardVehicle,
    _res,
    _tc,
    _weave_state,
    _WeaveMod,
)

DEFAULT_MODE = 1621
VETOED_MODE = DEFAULT_MODE & ~LC_MODE_MODEL_BITS  # 1536


class _ReadCountingVehicle(_GuardVehicle):
    """The fake vehicle module, counting ``getLaneChangeMode`` reads per vehicle."""

    def __init__(self, speeds: dict[str, float]) -> None:
        super().__init__(speeds)
        self.mode_reads: dict[str, int] = {}

    def getLaneChangeMode(self, vid):
        self.mode_reads[vid] = self.mode_reads.get(vid, 0) + 1
        return super().getLaneChangeMode(vid)

    def getSpeedFactor(self, vid):
        return 1.0


def _lc_writes(veh: _GuardVehicle, vid: str) -> list[int]:
    return [c[2] for c in veh.calls if c[0] == "lc" and c[1] == vid]


def _two_sections(spec: tuple[int, int], **down) -> tuple[dict, dict]:
    """``(U, D)``: upstream section U (edges a, b) and downstream D (c, d) whose window is on U's edge a.

    Both with ``weave_resolve_opposing``. U's section lanes are a's lane
    indexes; D's window asks along ``spec`` = (lane feeding D's section
    lane 1, lane feeding its lane 2) on a, 200 m before D's start: in the
    500 m default window.
    """
    ws_u = _weave_state(weave_resolve_opposing=1.0)
    ws_d = _weave_state(weave_resolve_opposing=1.0, **down)
    ws_d.update(
        {
            "off_index": 2,
            "edges": ["c", "d"],
            "edge_index": {"c": 0, "d": 1},
            "exit_only": {"c": True, "d": True},
            "lane_len_m": {"c": 100.0, "d": 100.0},
            "beyond_m": {"c": 100.0, "d": 0.0},
            "exiting_ids": frozenset(),
            "lane_map": {(e, k): k for e in ("c", "d") for k in range(3)},
            "x_offset": {"a": 0.0, "b": 100.0, "c": 200.0, "d": 300.0},
            "vacate_lanes": {"a": spec},
        }
    )
    _weave_share_vetoes([ws_u, ws_d])
    return ws_u, ws_d


def _vetoed_by_upstream(ws_u: dict, ws_d: dict, veh: _ReadCountingVehicle) -> _WeaveMod:
    """Step t = 0, U then D (``run_micro``'s upstream-first order) up to U's veto.

    Entrant n on U's lane 0 at 55 m asks into lane 1; p, an undriven through
    vehicle on lane 2 at 60 m with its model bits set, could enter lane 1
    from the other side in the same step: U vetoes p for the step.
    """
    mod = _WeaveMod(veh)
    res = {"n": _res("a", 0, 55.0, 10.0), "p": _res("a", 2, 60.0, 10.0)}
    _weave_step(mod, _tc, ws_u, res, 0.0)
    assert [c for c in veh.calls if c[0] == "change"] == [("change", "n", 1, 0.5)]
    assert veh.lc_modes["p"] == VETOED_MODE and ws_u["opp_veto"] == {"p": DEFAULT_MODE}
    assert ws_u["n_opposing_vetoed"] == 1
    return mod


class TestVetoCapturedByAnotherSection:
    def test_downstream_vacate_hold_hands_back_the_real_mode(self):
        """D's vacate window covers U's lane 2 (the lane feeding D's section
        lane 1): in the step U vetoes p, D asks p to vacate. D's hold takes
        p's recorded mode (1621), not the vetoed one, consuming U's record
        with no TraCI read; U's restore next step finds nothing to do; D
        hands 1621 back when p is seen in the target lane."""
        ws_u, ws_d = _two_sections((2, 3))
        veh = _ReadCountingVehicle({"n": 10.0, "p": 10.0})
        mod = _vetoed_by_upstream(ws_u, ws_d, veh)
        reads_u = veh.mode_reads["p"]  # U's resolution read p's mode, then vetoed it
        res = {"n": _res("a", 0, 55.0, 10.0), "p": _res("a", 2, 60.0, 10.0)}
        _weave_step(mod, _tc, ws_d, res, 0.0)
        assert ws_d["n_vacate_requests"] == 1
        assert ws_d["vacate"]["p"]["lc_mode_orig"] == DEFAULT_MODE
        assert veh.lc_modes["p"] == LC_MODE_SCRIPTED_SAFE
        assert ws_u["opp_veto"] == {} and ws_d["opp_veto"] == {}  # consumed, once
        assert veh.mode_reads["p"] == reads_u  # no read: the recorded mode was taken
        # t = 0.5: n has entered lane 1, p is in D's target lane (a's lane 3)
        res = {"n": _res("a", 1, 60.0, 10.0), "p": _res("a", 3, 65.0, 10.0)}
        _weave_step(mod, _tc, ws_u, res, 0.5)
        assert _lc_writes(veh, "p") == [VETOED_MODE, LC_MODE_SCRIPTED_SAFE]  # no restore by U
        _weave_step(mod, _tc, ws_d, res, 0.5)
        assert ws_d["n_vacated"] == 1 and ws_d["vacate"] == {}
        assert veh.lc_modes["p"] == DEFAULT_MODE
        assert _lc_writes(veh, "p") == [VETOED_MODE, LC_MODE_SCRIPTED_SAFE, DEFAULT_MODE]
        assert veh.mode_reads["p"] == reads_u

    def test_downstream_exit_prepare_hold_hands_back_the_real_mode(self):
        """The same through D's exiters' early move: p is bound for D's exit,
        one lane left of the lane feeding D's section lane 1 on U's edge a,
        and is asked into it in the step U vetoes it."""
        ws_u, ws_d = _two_sections((1, 2), exit_prepare=1.0)
        ws_d["exiting_ids"] = frozenset({"p"})
        ws_d["vacate_exempt_ids"] = frozenset({"p", "n"})
        veh = _ReadCountingVehicle({"n": 10.0, "p": 10.0})
        mod = _vetoed_by_upstream(ws_u, ws_d, veh)
        reads_u = veh.mode_reads["p"]
        res = {"n": _res("a", 0, 55.0, 10.0), "p": _res("a", 2, 60.0, 10.0)}
        _weave_step(mod, _tc, ws_d, res, 0.0)
        assert ws_d["n_exit_prepare_requests"] == 1
        assert ws_d["prep"]["p"]["lc_mode_orig"] == DEFAULT_MODE
        assert ws_u["opp_veto"] == {} and veh.mode_reads["p"] == reads_u
        res = {"n": _res("a", 1, 60.0, 10.0), "p": _res("a", 1, 65.0, 10.0)}
        _weave_step(mod, _tc, ws_u, res, 0.5)
        _weave_step(mod, _tc, ws_d, res, 0.5)
        assert ws_d["n_exit_prepared"] == 1 and ws_d["prep"] == {}
        assert _lc_writes(veh, "p") == [VETOED_MODE, LC_MODE_SCRIPTED_SAFE, DEFAULT_MODE]

    def test_a_later_section_does_not_restore_an_earlier_sections_veto(self):
        """Each section keeps its own ``opp_veto`` for its restore: D, stepped
        after U in the step of U's veto with no rule of its own touching p,
        leaves the veto in place for the SUMO step; U restores it at the
        start of its next step, as with one section."""
        ws_u, ws_d = _two_sections((2, 3), vacate_ahead_m=0.0)
        veh = _ReadCountingVehicle({"n": 10.0, "p": 10.0})
        mod = _vetoed_by_upstream(ws_u, ws_d, veh)
        ws_d["opp_veto"]["q"] = DEFAULT_MODE  # D's own veto of last step, restored at D's start
        res = {"n": _res("a", 0, 55.0, 10.0), "p": _res("a", 2, 60.0, 10.0)}
        _weave_step(mod, _tc, ws_d, res, 0.0)
        assert veh.lc_modes["p"] == VETOED_MODE and ws_u["opp_veto"] == {"p": DEFAULT_MODE}
        res = {"n": _res("a", 1, 60.0, 10.0), "p": _res("a", 2, 65.0, 10.0)}
        _weave_step(mod, _tc, ws_u, res, 0.5)
        assert veh.lc_modes["p"] == DEFAULT_MODE and ws_u["opp_veto"] == {}
        assert _lc_writes(veh, "p") == [VETOED_MODE, DEFAULT_MODE]

    def test_a_section_taking_a_vetoed_vehicle_under_control(self):
        """The capture of ``_weave_step`` itself: p, vetoed by U this step,
        is an entrant on D's lane 0 (owing a change there): D takes it under
        control with the recorded mode."""
        ws_u, ws_d = _two_sections((2, 3))
        veh = _ReadCountingVehicle({"p": 10.0})
        veh.lc_modes["p"] = VETOED_MODE
        ws_u["opp_veto"]["p"] = DEFAULT_MODE
        _weave_step(_WeaveMod(veh), _tc, ws_d, {"p": _res("c", 0, 50.0, 10.0)}, 0.0)
        assert ws_d["veh"]["p"]["lc_mode_orig"] == DEFAULT_MODE
        assert ws_u["opp_veto"] == {} and veh.mode_reads.get("p", 0) == 0

    def test_scripted_merge_stepped_first_takes_the_recorded_mode(self):
        """``run_micro`` steps the scripted merges before every section, so a
        vehicle U vetoed in step t that has crossed onto a scripted merge's
        acceleration lane (lane 0 of its attach edge ``s``, here fed by U's
        lane 2) is read by ``_scripted_merge_step`` in t + dt before U's
        restore: the merge holds it with the recorded mode, U's restore has
        nothing left to do, and the merge hands 1621 back when p leaves the
        lane."""
        ws_u, ws_d = _two_sections((2, 3))
        ss = {
            "ramp": "on2",
            "edge": "s",
            "lane_len_m": 250.0,
            "target_lane": "s_1",
            "params": {**SCRIPTED_MERGE_DEFAULTS},
            "veh": {},
            "n_entered": 0,
            "n_changed": 0,
            "n_forced": 0,
            "n_forced_deferred": 0,
            "step_s": 0.5,
            "waits_s": [],
        }
        _weave_share_vetoes([ws_u, ws_d], [ss])
        assert ss["opp_veto_all"] is ws_u["opp_veto_all"]
        veh = _ReadCountingVehicle({"n": 10.0, "p": 10.0})
        mod = _vetoed_by_upstream(ws_u, ws_d, veh)
        reads_u = veh.mode_reads["p"]
        # t = 0.5 in run_micro's order: the scripted merges, then the sections
        res = {"n": _res("a", 1, 60.0, 10.0), "p": _res("s", 0, 5.0, 10.0)}
        _scripted_merge_step(mod, _tc, ss, res, 0.5)
        assert ss["veh"]["p"]["lc_mode_orig"] == DEFAULT_MODE and ss["n_entered"] == 1
        assert ws_u["opp_veto"] == {} and veh.mode_reads["p"] == reads_u
        _weave_step(mod, _tc, ws_u, res, 0.5)
        _weave_step(mod, _tc, ws_d, res, 0.5)
        assert _lc_writes(veh, "p") == [VETOED_MODE, LC_MODE_SCRIPTED_SAFE]  # no restore by U
        # t = 1.0: p has merged into lane 1 of s: the merge hands back
        res = {"n": _res("a", 1, 65.0, 10.0), "p": _res("s", 1, 10.0, 10.0)}
        _scripted_merge_step(mod, _tc, ss, res, 1.0)
        assert ss["n_changed"] == 1 and ss["veh"] == {}
        assert _lc_writes(veh, "p") == [VETOED_MODE, LC_MODE_SCRIPTED_SAFE, DEFAULT_MODE]
        assert veh.lc_modes["p"] == DEFAULT_MODE

    def test_measured_hand_over_takes_the_recorded_mode(self):
        """The measured model's hand-over (``_measured_handover_step``): an
        entrant on the ramp within two steps' travel of the zone, vetoed this
        step by another section, is taken under 512 with its recorded mode,
        and gets that mode back when it leaves the window undriven."""
        ws_u = _weave_state(weave_resolve_opposing=1.0)
        ws_m = {
            "mm": {
                "run": {"veh_params": {}, "av_ids": frozenset()},
                "reach": {"z": {0: frozenset(), 1: frozenset({"after"})}},
                "route_by_id": {},
                "target_of_route": {"main": "after"},
            },
            "edges": ["z"],
            "edge_index": {"z": 0},
            "x_offset": {"r": -50.0, "z": 0.0},
            "lane_map": {("r", 0): 0, ("z", 0): 0, ("z", 1): 1},
            "ramp_edges": frozenset({"r"}),
            "exiting_ids": frozenset(),
            "gave_up": set(),
            "veh": {},
            "handover": {},
            "n_handovers": 0,
            "opp_veto": {},
            "step_s": 0.5,
        }
        _weave_share_vetoes([ws_u, ws_m])
        veh = _ReadCountingVehicle({"p": 10.0})
        veh.lc_modes["p"] = VETOED_MODE
        ws_u["opp_veto"]["p"] = DEFAULT_MODE
        mod = _WeaveMod(veh)
        res = {"p": _res("r", 0, 47.0, 10.0)}
        _measured_handover_step(mod, _tc, ws_m, res, {"p": -3.0}, {"p": 10.0}, {})
        assert ws_m["handover"] == {"p": DEFAULT_MODE} and ws_m["n_handovers"] == 1
        assert veh.lc_modes["p"] == LC_MODE_SCRIPTED_SAFE
        assert ws_u["opp_veto"] == {} and veh.mode_reads.get("p", 0) == 0
        _measured_handover_step(mod, _tc, ws_m, res, {}, {}, {})
        assert veh.lc_modes["p"] == DEFAULT_MODE and ws_m["handover"] == {}


class TestLcModeOwned:
    def test_no_veto_pending_reads_the_live_mode_once(self):
        """The default path: no veto pending anywhere, one
        ``getLaneChangeMode`` read and its value, the vetoes untouched;
        likewise on a state never wired (no ``opp_veto_all``)."""
        ws_u, ws_d = _two_sections((2, 3))
        ws_u["opp_veto"]["other"] = DEFAULT_MODE
        veh = _ReadCountingVehicle({})
        veh.lc_modes["p"] = 1109
        mod = _WeaveMod(veh)
        assert _lc_mode_owned(mod, ws_d, "p") == 1109
        assert veh.mode_reads == {"p": 1}
        assert ws_u["opp_veto"] == {"other": DEFAULT_MODE} and ws_d["opp_veto"] == {}
        assert veh.calls == []
        bare = _weave_state()
        del bare["opp_veto"]
        assert _lc_mode_owned(mod, bare, "p") == 1109
        assert veh.mode_reads == {"p": 2}

    def test_a_pending_veto_is_consumed_once_with_no_read(self):
        ws_u, ws_d = _two_sections((2, 3))
        ws_u["opp_veto"]["p"] = DEFAULT_MODE
        veh = _ReadCountingVehicle({})
        veh.lc_modes["p"] = VETOED_MODE
        mod = _WeaveMod(veh)
        assert _lc_mode_owned(mod, ws_d, "p") == DEFAULT_MODE
        assert ws_u["opp_veto"] == {} and veh.mode_reads == {}
        # consumed: a second capture reads the live mode
        assert _lc_mode_owned(mod, ws_u, "p") == VETOED_MODE
        assert veh.mode_reads == {"p": 1}

    def test_share_vetoes_lists_every_states_own_dict(self):
        ws_u, ws_d = _two_sections((2, 3))
        assert ws_u["opp_veto_all"] is ws_d["opp_veto_all"]
        assert [id(v) for v in ws_u["opp_veto_all"]] == [id(ws_u["opp_veto"]), id(ws_d["opp_veto"])]
        assert ws_u["opp_veto"] is not ws_d["opp_veto"]
