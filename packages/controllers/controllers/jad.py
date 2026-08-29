"""Jam-Absorption Driving (JAD) controller — slow-in / fast-out (CLAUDE.md §4.3).

Lineage: R. Nishi, A. Tomoeda, K. Shimura & K. Nishinari, "Theory of
jam-absorption driving", Transportation Research Part B 50:116–129 (2013), and
the He, Liu & Liu (2016, Transportation Research Part B) jam-absorption
strategy cited in CLAUDE.md §4.3/§13. The controller absorbs an approaching
stop-and-go wave by slowing early ("slow-in") so its density shadow meets the
wave front, then recovering gently ("fast-out") so as not to seed a secondary
wave.

Phase machine (encoded in ``memory`` as float codes, CLAUDE.md §4.3)::

    CRUISE (0) ── wave detected ──► SLOW_IN (1) ── at v_slow ──► HOLD (2)
       ▲                                │ recovery                 │ recovery
       │                                ▼                          ▼ or timeout
       └────────── at v_ref ────── FAST_OUT (3) ◄──────────────────┘

* **Detection** (perfect oracle variant, §4.3): a wave is present when any
  ``obs.downstream`` bin (mean speeds, nearest-first, bin width
  ``obs.downstream_dx``) within ``lookahead_m`` lies below ``v_wave_thresh``
  (default 40 km/h in SI). NaN bins (no vehicles) are skipped. The
  delayed/noisy oracle is the runner's responsibility (it degrades the
  ``downstream`` observation), keeping this function pure.
* **Slow-in**: the command ramps down at ≤ ``a_slow`` (default 1.0 m/s²)
  toward ``v_slow = β·v_trigger`` (β default 0.55, range 0.4–0.8).
* **Hold / intercept timing** (approximate — see derivation below): the hold
  ends on recovery of the nearest bins, or at the estimated intercept time as
  a ceiling.
* **Fast-out**: the command ramps up at ≤ ``a_out`` (default 1.5 m/s²,
  the §4.3 secondary-wave cap) back to ``v_ref``. Recovery means the
  triggering wave is gone: either no jammed bin remains anywhere within the
  lookahead, or the wave had already reached the nearest ``recover_bins``
  bins (tracked via ``memory["wave_near"]``) and those bins have cleared
  (CLAUDE.md §4.3 "local leader speed recovers"). Clear near bins alone are
  NOT recovery while the wave is still approaching from farther downstream.

Intercept-timing derivation (approximate)
-----------------------------------------
At trigger time ``t0`` the nearest jammed bin's near edge lies
``x_w = i_jam · downstream_dx`` ahead of the ego. The wave front is assumed to
propagate upstream at constant ``w_wave < 0`` (default −18 km/h in SI, inside
the empirical 14–22 km/h backward band, ``flowstate_core.constants.
WAVE_SPEED_BAND_KMH``). Approximating the ego speed by ``v_slow`` for the
whole approach (folding the short slow-in transient into the hold), ego and
front positions are ``x_e(t) = v_slow·(t − t0)`` and
``x_f(t) = x_w + w_wave·(t − t0)``; equating gives

    t_int = x_w / (v_slow − w_wave)   (> 0 since w_wave < 0),

and the hold ceiling is ``t0 + t_int``. This neglects the slow-in transient
distance and wave-speed variability; the full geometric derivation with
diagram belongs in ``docs/jad_derivation.md`` (later phase, CLAUDE.md §4.3).

The command is rate-limited every step from the previous command
(``a_slow`` down / ``a_out`` up), so commanded decel/accel limits hold at
every step for the runner-supplied ``dt``. Re-arming: a new wave triggers
SLOW_IN only from CRUISE, so one detection drives exactly one absorption
cycle; persistent congestion re-triggers after the cycle completes.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Final

from flowstate_core.controller_types import ControllerObs, Memory
from flowstate_core.units import kmh_to_ms

PHASE_CRUISE: Final[float] = 0.0
PHASE_SLOW_IN: Final[float] = 1.0
PHASE_HOLD: Final[float] = 2.0
PHASE_FAST_OUT: Final[float] = 3.0

JAD_PHASES: Final[tuple[float, ...]] = (PHASE_CRUISE, PHASE_SLOW_IN, PHASE_HOLD, PHASE_FAST_OUT)
"""Valid float phase codes stored in ``memory["phase"]``."""

JAD_DEFAULTS: Final[dict[str, float]] = {
    "beta": 0.55,  # v_slow = beta * v_trigger [-] (§4.3; range 0.4–0.8)
    "a_slow": 1.0,  # slow-in command decel limit [m/s²] (§4.3)
    "a_out": 1.5,  # fast-out command accel cap [m/s²] (§4.3 secondary-wave cap)
    "w_wave": -kmh_to_ms(18.0),  # assumed wave-front speed [m/s], negative = upstream
    "v_wave_thresh": kmh_to_ms(40.0),  # wave detection speed threshold [m/s] (§4.3)
    "lookahead_m": 2000.0,  # detection lookahead L [m] (§4.3 default 2 km)
    "recover_bins": 3.0,  # nearest bins that must clear for fast-out [-]
}
"""Literature/spec defaults (CLAUDE.md §4.3); calibrated in Phase 1."""

_MEM_PHASE: Final[str] = "phase"
_MEM_PHASE_T: Final[str] = "phase_entry_t"
_MEM_V_SLOW: Final[str] = "v_slow"
_MEM_T_INT_END: Final[str] = "t_int_end"
_MEM_PREV: Final[str] = "v_cmd_prev"
_MEM_WAVE_NEAR: Final[str] = "wave_near"


def _nearest_wave_bin(obs: ControllerObs, p: Mapping[str, float]) -> int | None:
    """Index of the nearest jammed downstream bin within the lookahead, else None."""
    if obs.downstream_dx <= 0.0:
        return None
    n_look = math.ceil(p["lookahead_m"] / obs.downstream_dx)
    for i, speed in enumerate(obs.downstream[:n_look]):
        if not math.isnan(speed) and speed < p["v_wave_thresh"]:
            return i
    return None


def _nearest_bins_recovered(obs: ControllerObs, p: Mapping[str, float]) -> bool:
    """True when the nearest ``recover_bins`` bins are all at/above threshold.

    NaN bins (no vehicles) count as recovered; an empty observation counts as
    recovered (nothing detectable ahead).
    """
    n = max(int(p["recover_bins"]), 1)
    for speed in obs.downstream[:n]:
        if not math.isnan(speed) and speed < p["v_wave_thresh"]:
            return False
    return True


def jad(obs: ControllerObs, params: Mapping[str, float], memory: Memory) -> tuple[float, Memory]:
    """Jam-Absorption Driving command (slow-in / fast-out phase machine).

    Args:
        obs: Vehicle observation (SI). Wave detection consumes
            ``obs.downstream`` (mean bin speeds, nearest-first) and
            ``obs.downstream_dx``; ``obs.v_ref`` is the cruise reference.
        params: Overrides of ``JAD_DEFAULTS``.
        memory: Phase state: ``"phase"`` (float code in ``JAD_PHASES``),
            ``"phase_entry_t"`` [s], ``"v_slow"`` [m/s], ``"t_int_end"`` [s],
            ``"v_cmd_prev"`` [m/s], ``"wave_near"`` (0.0/1.0 — the wave has
            reached the nearest bins). Empty dict ⇒ fresh CRUISE state with
            ``v_cmd_prev`` initialized to the current ego speed.

    Returns:
        ``(v_cmd [m/s], new_memory)``. The command changes by at most
        ``a_slow·dt`` downward and ``a_out·dt`` upward per step, and lies in
        ``[0, max(v_ref, v)]``.
    """
    p = {**JAD_DEFAULTS, **params}
    u = max(obs.v_ref, 0.0)
    mem = dict(memory)
    phase = mem.get(_MEM_PHASE, PHASE_CRUISE)
    prev = mem.get(_MEM_PREV, max(obs.v, 0.0))
    v_slow = mem.get(_MEM_V_SLOW, 0.0)

    wave_bin = _nearest_wave_bin(obs, p)
    near_clear = _nearest_bins_recovered(obs, p)
    if phase in (PHASE_SLOW_IN, PHASE_HOLD) and wave_bin is not None:
        if wave_bin < max(int(p["recover_bins"]), 1):
            mem[_MEM_WAVE_NEAR] = 1.0
    # Recovery: the triggering wave is gone from the lookahead entirely, or it
    # had reached the nearest bins and those have since cleared.
    recovered = wave_bin is None or (mem.get(_MEM_WAVE_NEAR, 0.0) >= 1.0 and near_clear)

    if phase == PHASE_CRUISE and wave_bin is not None:
        phase = PHASE_SLOW_IN
        v_slow = p["beta"] * max(obs.v, 0.0)
        x_w = wave_bin * obs.downstream_dx
        t_int = x_w / max(v_slow - p["w_wave"], 1e-9)
        mem[_MEM_V_SLOW] = v_slow
        mem[_MEM_T_INT_END] = obs.t + t_int
        mem[_MEM_PHASE_T] = obs.t
        mem[_MEM_WAVE_NEAR] = 1.0 if wave_bin < max(int(p["recover_bins"]), 1) else 0.0
    elif phase == PHASE_SLOW_IN:
        if recovered:
            phase = PHASE_FAST_OUT
            mem[_MEM_PHASE_T] = obs.t
        elif prev <= v_slow + 1e-9:
            phase = PHASE_HOLD
            mem[_MEM_PHASE_T] = obs.t
    elif phase == PHASE_HOLD:
        if recovered or obs.t >= mem.get(_MEM_T_INT_END, obs.t):
            phase = PHASE_FAST_OUT
            mem[_MEM_PHASE_T] = obs.t
    elif phase == PHASE_FAST_OUT:
        if prev >= u - 1e-9:
            phase = PHASE_CRUISE
            mem[_MEM_PHASE_T] = obs.t
            mem[_MEM_WAVE_NEAR] = 0.0

    target = v_slow if phase in (PHASE_SLOW_IN, PHASE_HOLD) else u

    # Per-step command rate limiting: ≤ a_slow down, ≤ a_out up.
    lo = prev - p["a_slow"] * obs.dt
    hi = prev + p["a_out"] * obs.dt
    v_cmd = max(min(max(target, lo), hi), 0.0)

    mem[_MEM_PHASE] = phase
    mem[_MEM_PREV] = v_cmd
    mem.setdefault(_MEM_PHASE_T, obs.t)
    return v_cmd, mem
