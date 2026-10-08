"""Amendment 3's range round (stage p24_i94_a3): the T.H.52 ramp-to-ramp share at u = 0, 0.5 and 1.

docs/FRISCO_PROTOCOL.md, "Adoption of Amendment 3 — 2026-10-07", items 1-7 (pre-registration P-A3 of
docs/DECISIONS_2026-10-07.md §A3.3); docs/A3_RANGE_ROUND.md. Everything here was fixed on 2026-10-07, before any run
that varies the share; nothing is re-thresholded and no share is ever chosen from the round.

usage (repository root; stage p24_i94_a3 runs ``check-copy``, ``lanes`` and ``evaluate`` on the VM,
artifacts/a3_range_2026-10-07/stage_p24_a3.sh.txt):
  corridor_a3.py expected --out artifacts/a3_range_2026-10-07/a3_expected.json
  corridor_a3.py check-copy --source scenarios/S.yaml --source-hash H --copy C.yaml [--u U] [--w2-off]
  corridor_a3.py lanes --label LABEL [--out artifacts/a3_lanes_LABEL.json]
  corridor_a3.py evaluate --out artifacts/a3_range.json

* ``expected``: item 3's expectations on the calibration-day inputs [computed, not run] — from the count data
  (``artifacts/demand_mndot_i94_wb_stpaul_cal.json``'s T.H.52 inflow and exit fraction, S790 in the calibration-day
  observations: ``s_w = P_w + u · (0.70 − P_w)`` clipped to ``v_OFF,w / v_ON,w``, ``v_OFF,w = P_w (v_F,w + v_ON,w)``,
  crossers ``v_RF + v_FR``), set beside the protocol's figures, which are never changed here; then the same shares as
  the plan builder meets them on the model's own volumes at the gore (the scenario's rates, windowed by free-flow
  arrival at the weave, the timing decision of docs/A3_RANGE_ROUND.md §2; and, for comparison, by departure).
  The free-flow times come from a netconvert compile of the F2 scenario's network (no simulation).
* ``check-copy``: a stage copy is its committed source with only its name, the T.H.52 block's ``ramp_to_ramp_share``
  (``{u: U}``; absent without ``--u``) and, with ``--w2-off``, W2's three switches at 0 on both weaves changed; the
  source must still hash to ``--source-hash`` (config-hash policy v4). Exit 0, or 1 with the reasons.
* ``lanes``: per-lane hourly flows at S790 and at the T.H.52 gore (1 m upstream of the section's end, the weave
  on-ramp's ``attach_end_x_m``) from the battery's one kept trajectory (its first seed, 6914975401685141156, the
  only ``trajectories.parquet`` the battery keeps; read on the VM).
* ``evaluate``: per family (F1 ``_dc_cal_w1b`` with W2 off, p10's arm A; F2 ``_dc_cal_w1b_w2``, the reference) and arm
  (u0, u05, u1) item 4's readings, the contrasts against u0 (paired t, 19 df) and item 5's rule at u1 against u0:
  material if (M1) S790 06:30-07:30 lower bound >= +100 veh/h with the mean realised demand at most 1 pp lower, or
  (M2) S790's or S97's GEH < 5 count differs by >= 5 of 20 seeds in any hour, or (M3) calibration-day C1 or C3
  (15 min), paired, has its interval wholly beyond +-2 pp, or (M4) a collision or a lock in one arm only; not
  material if none holds and the S790 interval lies within +-100 veh/h; otherwise inconclusive (no seeds are added).
  u05 shows shape only (contrasts, no verdict). Exit 3 after writing the readout when a problem is recorded.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: the repository holding this harness and the committed readers it imports
CODE = Path(__file__).resolve().parents[3]
#: the root the inputs are read from and run directories resolved against (the working tree on the VM; a test's
#: directory in the tests)
REPO = CODE

MNDOT = "mndot_i94_wb_stpaul"
#: The families of item 2. ``source_hash_v4``: the committed file's config hash under policy v4
#: (tests/golden/scenario_config_hashes.json); ``p10`` the label of p10's battery of the same physics.
FAMILIES: dict[str, dict[str, Any]] = {
    "F1": {
        "stem": f"{MNDOT}_weave_dc_cal_w1b",
        "name": f"{MNDOT}_weave_xlsfg_dc_cal_w1b",
        "source_hash_v4": "2dd495d173f4",
        "w2_off": True,
        "p10": f"{MNDOT}_weave_xlsfg_dc_cal_w1b",
        "what": "W1b on both weaves, W2's three switches at 0 (p10's arm A; Amendment 4 made W2 the default, so the "
        "committed file alone now runs W2)",
    },
    "F2": {
        "stem": f"{MNDOT}_weave_dc_cal_w1b_w2",
        "name": f"{MNDOT}_weave_xlsfg_dc_cal_w1b_w2",
        "source_hash_v4": "395a111cb991",
        "w2_off": False,
        "p10": f"{MNDOT}_weave_xlsfg_dc_cal_w1b_w2",
        "what": "W1b and W2 on both weaves (p10's arm B; the reference under Amendment 4)",
    },
}
ARMS: dict[str, float] = {"u0": 0.0, "u05": 0.5, "u1": 1.0}
COPY_DIR = "runs/p24_a3/scenarios"
TH52 = {"entrance": "on-ramp 769818012", "exit": "off-ramp 18207598", "attach": "51388891"}
RUTH = {"entrance": "on-ramp 745524613", "exit": "C-D split 18208090"}
W2_SWITCHES = ("weave_handback", "weave_close_leader", "weave_resolve_opposing")
STATIONS = ("S790", "S97")
PEAK = "06:30"
S_MAX = 0.70
P1A = "artifacts/p1_rehearsal_2026-10-04"
CAL_OBS = f"{P1A}/observations_calibration.json"
DEMAND = "artifacts/demand_mndot_i94_wb_stpaul_cal.json"
MASTER_SEED, N_SEEDS = 42, 20
KEPT_SEED = 6914975401685141156
GORE_OFFSET_M = 1.0
WINDOW_S = 300.0
T0_LOCAL_MIN = 5 * 60 + 30  # 05:30, the observations' t0_local
#: item 5's numbers, fixed in the protocol before any run
M1_LOWER_VEH_H = 100.0
M1_DEMAND_PP = 0.01
M2_SEEDS = 5
M3_PP = 0.02
NOT_MATERIAL_VEH_H = 100.0
W1B_DISCLOSED_SHARE = 0.01
RANGE_LABEL = "range over the T.H.52 ramp-to-ramp share [proportional, 0.70], stated assumption"
#: item 3's figures as the protocol states them (docs/FRISCO_PROTOCOL.md, adoption of Amendment 3, item 3, and item 1)
PROTOCOL_EXPECTED: dict[str, Any] = {
    "u0": {
        "s_0530_0550": 0.29,
        "s_0630_0730": 0.18,
        "crossers_0630_0730_veh_h": 1955,
        "clipped_windows": 0,
    },
    "u05": {
        "s_0530_0550": 0.50,
        "s_0630_0730": 0.44,
        "crossers_0630_0730_veh_h": 1282,
        "clipped_windows": 1,
    },
    "u1": {
        "s_0530_0550": 0.70,
        "s_0630_0730": 0.70,
        "crossers_0630_0730_veh_h": 609,
        "clipped_windows": 8,
    },
    "item1": {
        "lowest_ratio": 0.36,
        "lowest_ratio_clock": "07:50",
        "clipped_at_0p70": "07:30-08:10, eight windows",
    },
}


# --------------------------------------------------------------------------- small helpers


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def readers() -> tuple[Any, Any]:
    """``(corridor_w1b, corridor_w2)``: the committed readers of p9 and p10 (the front-row lock reader is W1b's)."""
    w1b = _load_module(
        "corridor_w1b",
        CODE / "artifacts" / "weave_loss_2026-10-07" / "w1b" / "harness" / "corridor_w1b.py",
    )
    w2 = _load_module(
        "corridor_w2",
        CODE / "artifacts" / "weave_collision_guards_2026-10-07" / "harness" / "corridor_w2.py",
    )
    return w1b, w2


def label_of(family: str, arm: str) -> str:
    return f"{FAMILIES[family]['name']}_a3{arm}"


def scenario_of(family: str, arm: str) -> str:
    """The scenario an arm runs: F2's u0 is the committed file, every other arm a copy written on the VM."""
    if family == "F2" and arm == "u0":
        return f"scenarios/{FAMILIES['F2']['stem']}.yaml"
    return f"{COPY_DIR}/{label_of(family, arm)}.yaml"


def clock(window: int) -> str:
    m = T0_LOCAL_MIN + 5 * window
    return f"{m // 60:02d}:{m % 60:02d}"


def seeds_of_record() -> list[int]:
    from flowstate_core.rng import spawn_seeds

    return [int(s) for s in spawn_seeds(MASTER_SEED, N_SEEDS)]


def load(rel: str) -> dict[str, Any] | None:
    p = REPO / rel
    return json.loads(p.read_text()) if p.is_file() else None


def paired(a: Sequence[float | None], b: Sequence[float | None]) -> dict[str, Any] | None:
    """``a - b`` seed by seed: mean and 95 % t-interval (n - 1 df; 19 at 20 seeds). None when a value is missing."""
    if len(a) != len(b) or any(x is None for x in a) or any(x is None for x in b) or not a:
        return None
    from validation.metrics import ci

    d = [float(x) - float(y) for x, y in zip(a, b, strict=True)]  # type: ignore[arg-type]
    c = ci(d)
    return {"mean": c.mean, "lo95": c.lo95, "hi95": c.hi95, "n": c.n, "df": c.n - 1}


def summary(values: Sequence[float | None]) -> dict[str, Any] | None:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    from validation.metrics import ci

    c = ci(vals)
    return {
        "mean": c.mean,
        "lo95": c.lo95,
        "hi95": c.hi95,
        "n": c.n,
        "min": min(vals),
        "max": max(vals),
    }


# --------------------------------------------------------------------------- expected (item 3)


def _steps(steps: Sequence[Sequence[float]]) -> list[float]:
    return [float(v) for _, v in steps]


def expected_from_counts(
    v_on: Sequence[float], p: Sequence[float], v_f: Sequence[float], s_max: float = S_MAX
) -> dict[str, Any]:
    """Item 3 on the count data: per 5-min window ``w`` the HCM split ``P_w``, ``v_OFF,w = P_w (v_F,w + v_ON,w)``,
    ``s_w = P_w + u (s_max − P_w)`` clipped to ``v_OFF,w / v_ON,w``, crossers ``v_ON (1 − s) + (v_OFF − s v_ON)``.

    Args:
        v_on: The entrance's volume per window [veh/h] (rnd_91040 through the demand's inflow steps).
        p: The paired exit's fraction per window (the conservation closure ``P_w``).
        v_f: The mainline volume per window [veh/h] (S790).
        s_max: The range's upper end.
    """
    n = len(v_on)
    assert len(p) == n == len(v_f), "one value per window"
    ratio = [p[w] * (v_f[w] + v_on[w]) / v_on[w] if v_on[w] > 0 else math.inf for w in range(n)]
    out: dict[str, Any] = {"windows": [], "arms": {}}
    for w in range(n):
        out["windows"].append(
            {
                "clock": clock(w),
                "v_on_veh_h": v_on[w],
                "v_f_veh_h": v_f[w],
                "p": p[w],
                "v_off_veh_h": p[w] * (v_f[w] + v_on[w]),
                "ratio_v_off_v_on": ratio[w],
            }
        )
    for arm, u in ARMS.items():
        s = [p[w] + u * (s_max - p[w]) for w in range(n)]
        clipped = [w for w in range(n) if s[w] > ratio[w]]
        sc = [min(s[w], ratio[w]) for w in range(n)]
        v_off = [p[w] * (v_f[w] + v_on[w]) for w in range(n)]
        cross = [v_on[w] * (1.0 - sc[w]) + (v_off[w] - sc[w] * v_on[w]) for w in range(n)]
        out["arms"][arm] = {
            "u": u,
            "s_0530_0550": sum(sc[0:4]) / 4,
            "s_0630_0730": sum(sc[12:24]) / 12,
            "crossers_0630_0730_veh_h": sum(cross[12:24]) / 12,
            "clipped_windows": len(clipped),
            "clipped_clocks": [clock(w) for w in clipped],
            "s_w": sc,
        }
    lowest = min(range(n), key=lambda w: ratio[w])
    out["lowest_ratio"] = ratio[lowest]
    out["lowest_ratio_clock"] = clock(lowest)
    out["windows_below_s_max"] = [clock(w) for w in range(n) if ratio[w] < s_max]
    return out


def against_protocol(counts: Mapping[str, Any]) -> dict[str, Any]:
    """Our figures rounded as the protocol rounds them (s to 0.01, crossers to 1 veh/h, ratio to 0.01)."""
    rows: dict[str, Any] = {}
    ok = True
    for arm, want in PROTOCOL_EXPECTED.items():
        if arm == "item1":
            continue
        got = counts["arms"][arm]
        mine = {
            "s_0530_0550": round(got["s_0530_0550"], 2),
            "s_0630_0730": round(got["s_0630_0730"], 2),
            "crossers_0630_0730_veh_h": round(got["crossers_0630_0730_veh_h"]),
            "clipped_windows": got["clipped_windows"],
        }
        same = {k: mine[k] == want[k] for k in want}
        ok = ok and all(same.values())
        rows[arm] = {"protocol": want, "reproduced": mine, "same": same}
    lo = {
        "lowest_ratio": round(counts["lowest_ratio"], 2),
        "lowest_ratio_clock": counts["lowest_ratio_clock"],
    }
    item1 = PROTOCOL_EXPECTED["item1"]
    same1 = {k: lo[k] == item1[k] for k in lo}
    eight = counts["arms"]["u1"]["clipped_clocks"]
    rows["item1"] = {
        "protocol": item1,
        "reproduced": {**lo, "clipped_at_0p70": eight},
        "same": {
            **same1,
            "clipped_at_0p70": len(eight) == 8 and eight[0] == "07:30" and eight[-1] == "08:10",
        },
    }
    ok = ok and all(rows["item1"]["same"].values())
    return {"all_same": ok, "rows": rows}


def _integral(rate: Sequence[float], t0: float, t1: float, horizon: float) -> float:
    """∫ rate dt over [t0, t1) of a 300-s step profile (veh/h per window), in vehicles; zero outside [0, horizon)."""
    a, b = max(t0, 0.0), min(t1, horizon)
    total = 0.0
    while a < b:
        w = int(a // WINDOW_S)
        end = min(b, (w + 1) * WINDOW_S)
        total += rate[w] / 3600.0 * (end - a)
        a = end
    return total


def expected_under_timing(
    cfg: Any,
    edge_geometry: Mapping[str, tuple[float, float]],
    corridor_edges: Sequence[str],
    v0: float,
    speed_factor: float,
    rule: str,
    s_max: float = S_MAX,
) -> dict[str, Any]:
    """The shares the plan builder meets on the model's own volumes at the gore, from the scenario's rates.

    Every flow is the scenario's (veh/h per 300-s step); a vehicle of origin ``o`` departing at ``t`` is bound
    for the T.H.52 exit with the probability the plan draws it with at ``t``, and passes the weave bound elsewhere
    with the probability it survives every exit up to and including it. ``rule`` ``"arrival"`` windows every
    origin by ``t + τ_o`` (the free-flow time at the population's mean v0 to the start of the entrance's attach
    edge; the entrants by their own ramp's), ``"departure"`` by ``t``. In window ``w`` (the gore's clock under
    "arrival"): ``N_w`` entrants, ``E_w`` vehicles of the pool bound for the exit (the clip ``E_w / N_w``),
    ``s_w = P_w + u (s_max − P_w)`` with ``P_w`` the exit fraction of window ``w``; crossers ``N_w (1 − s') +
    (E_w − s' N_w)`` [veh/h].
    """
    ramps = list(cfg.network.ramps)
    pos = {e: i for i, e in enumerate(corridor_edges)}
    k = next(i for i, r in enumerate(ramps) if r.name == TH52["entrance"])
    j = next(i for i, r in enumerate(ramps) if r.name == TH52["exit"])
    entry = pos[ramps[k].attach_edge]
    horizon = float(cfg.sim.duration_s)
    n_w = round(horizon / WINDOW_S)
    offs = sorted((pos[r.attach_edge], m) for m, r in enumerate(ramps) if r.kind == "off")
    origins = [-1] + [
        u for u, r in enumerate(ramps) if r.kind == "on" and (pos[r.attach_edge] < entry or u == k)
    ]

    def tau(o: int) -> float:
        start = 0 if o < 0 else pos[ramps[o].attach_edge]
        edges = (list(ramps[o].edges) if o >= 0 else []) + list(corridor_edges[start:entry])
        return sum(L / min(v0, speed_factor * lim) for L, lim in (edge_geometry[e] for e in edges))

    def frac(m: int, w: int) -> float:
        steps = ramps[m].exit_fraction
        return float(steps[min(w, len(steps) - 1)][1]) if steps else 0.0

    def rate(o: int) -> list[float]:
        steps = cfg.network.inflow if o < 0 else ramps[o].inflow
        return [float(steps[min(w, len(steps) - 1)][1]) * 3600.0 for w in range(n_w)]

    def probs(o: int, w: int) -> tuple[float, float]:
        """(bound for j, passes the weave bound elsewhere) for origin o departing in window w."""
        start = -1 if o < 0 else pos[ramps[o].attach_edge]
        alive, p_j = 1.0, 0.0
        for p_m, m in offs:
            if p_m < start:
                continue
            f = frac(m, w)
            if m == j:
                p_j = alive * f
                alive *= 1.0 - f
                break
            alive *= 1.0 - f
        return p_j, alive

    taus = {o: tau(o) for o in origins}
    shift = {o: (taus[o] if rule == "arrival" else 0.0) for o in origins}
    rates = {o: rate(o) for o in origins}
    gore: list[dict[str, float]] = []
    for w in range(n_w):
        t0, t1 = w * WINDOW_S, (w + 1) * WINDOW_S
        ent = exit_pool = 0.0
        for o in origins:
            # vehicles of origin o reaching window w: departed in [t0 - shift, t1 - shift)
            a, b = t0 - shift[o], t1 - shift[o]
            for dw in range(max(int(a // WINDOW_S), 0), min(math.ceil(b / WINDOW_S), n_w)):
                n = _integral(rates[o], max(a, dw * WINDOW_S), min(b, (dw + 1) * WINDOW_S), horizon)
                p_j, _ = probs(o, dw)
                if o == k:
                    ent += n
                    exit_pool += n * p_j
                else:
                    exit_pool += n * p_j
        gore.append({"n_entrants": ent, "exit_pool": exit_pool, "p": frac(j, w)})
    out: dict[str, Any] = {
        "rule": rule,
        "free_flow_s": {("corridor entry" if o < 0 else ramps[o].name): taus[o] for o in origins},
        "v0_ms": v0,
        "speed_factor": speed_factor,
        "arms": {},
    }
    for arm, u in ARMS.items():
        sc, clipped, cross = [], [], []
        for w, g in enumerate(gore):
            s = g["p"] + u * (s_max - g["p"])
            hi = g["exit_pool"] / g["n_entrants"] if g["n_entrants"] > 0 else math.inf
            if s > hi:
                clipped.append(clock(w))
            s2 = min(s, hi)
            sc.append(s2)
            cross.append(
                12.0 * (g["n_entrants"] * (1.0 - s2) + (g["exit_pool"] - s2 * g["n_entrants"]))
            )
        out["arms"][arm] = {
            "u": u,
            "s_0530_0550": sum(sc[0:4]) / 4,
            "s_0630_0730": sum(sc[12:24]) / 12,
            "crossers_0630_0730_veh_h": sum(cross[12:24]) / 12,
            "clipped_windows": len(clipped),
            "clipped_clocks": clipped,
            "s_w": sc,
        }
    out["windows"] = [
        {
            "clock": clock(w),
            "n_entrants_veh_h": 12 * g["n_entrants"],
            "exit_pool_veh_h": 12 * g["exit_pool"],
            "p": g["p"],
            "max_share": (g["exit_pool"] / g["n_entrants"]) if g["n_entrants"] > 0 else None,
        }
        for w, g in enumerate(gore)
    ]
    return out


def network_inputs(
    scenario: str,
) -> tuple[Any, dict[str, tuple[float, float]], list[str], float, float]:
    """The scenario (ramps resolved), its compiled net's edge geometry, corridor edges, mean v0 and speed factor.

    A netconvert compile only (microsim.runner._build_network), never a simulation.
    """
    import sumolib

    from flowstate_core.config import ScenarioConfig
    from microsim.runner import _build_network, _resolve_ramp_pieces
    from microsim.vehicles import load_idm_calibration

    cfg = ScenarioConfig.from_yaml(REPO / scenario)
    with tempfile.TemporaryDirectory() as tmp:
        bundle = _build_network(cfg, Path(tmp))
        cfg = _resolve_ramp_pieces(cfg, bundle)
        net = sumolib.net.readNet(str(bundle.net_path))
        geom: dict[str, tuple[float, float]] = {}
        edges = list(bundle.edge_ids) + [
            e for r in cfg.network.ramps if r.kind == "on" for e in r.edges
        ]
        for e in edges:
            lanes = net.getEdge(e).getLanes()
            geom[e] = (float(lanes[0].getLength()), max(float(lane.getSpeed()) for lane in lanes))
        corridor = list(bundle.edge_ids)
    v0 = (
        float(load_idm_calibration(cfg.fleet.idm_calibration).mean["v0"])
        if cfg.fleet.idm_calibration
        else float(cfg.fleet.v0)
    )
    return cfg, geom, corridor, v0, float(cfg.fleet.speed_factor)


def departure_exit_shift(departure: Mapping[str, Any], arm: str = "u1") -> dict[str, Any]:
    """What departure windows would do to the gore's exit flow (docs/A3_RANGE_ROUND.md §2) [computed].

    Under the departure rule the entrants of window ``w`` (at the gore about ``τ_k`` after departing) trade with
    corridor-entry vehicles that reach it ``τ_entry − τ_k`` later. Each of the window's ``x_w = (s'_w − P_w) N_w``
    swaps adds an exit trip at the gore in ``w`` and removes one in ``[w + Δ, w + 1 + Δ)``, ``Δ`` that lag in
    windows. Returns the net change of the gore's exit flow per 5-min window [veh/h] and per scored hour [veh];
    under the built arrival rule it is zero by construction (free flow).
    """
    ff = departure["free_flow_s"]
    lag = float(ff["corridor entry"]) - float(ff[TH52["entrance"]])
    s1 = departure["arms"][arm]["s_w"]
    rows = departure["windows"]
    n = len(rows)
    x = [(s1[w] - rows[w]["p"]) * rows[w]["n_entrants_veh_h"] / 12.0 for w in range(n)]
    net = [0.0] * (n + 4)
    for w in range(n):
        net[w] += x[w]
        a, b = w * WINDOW_S + lag, (w + 1) * WINDOW_S + lag
        for v in range(int(a // WINDOW_S), math.ceil(b / WINDOW_S)):
            lo, hi = max(a, v * WINDOW_S), min(b, (v + 1) * WINDOW_S)
            if hi > lo and v < len(net):
                net[v] -= x[w] * (hi - lo) / WINDOW_S
    per_window = [{"clock": clock(w), "net_exit_veh_h": 12.0 * net[w]} for w in range(n)]
    hours = {f"{clock(h)}": sum(net[h : h + 12]) for h in range(0, n, 12)}
    largest = sorted(per_window, key=lambda r: -abs(r["net_exit_veh_h"]))[:8]
    return {"arm": arm, "lag_s": lag, "swaps_per_window_mean": sum(x) / n, "largest_veh_h": largest,
            "hourly_net_veh": hours, "per_window": per_window}  # fmt: skip


def cmd_expected(out: Path) -> int:
    demand = load(DEMAND)
    obs = load(CAL_OBS)
    assert demand is not None and obs is not None, "the calibration-day inputs are tracked files"
    ramps = {r["name"]: r for r in demand["ramps"]}
    v_on = [v * 3600.0 for v in _steps(ramps[TH52["entrance"]]["inflow_steps"])]
    p = _steps(ramps[TH52["exit"]]["exit_fraction_steps"])
    v_f = [float(x) for x in obs["flows_veh_h"]["S790"]]
    counts = expected_from_counts(v_on, p, v_f)
    check = against_protocol(counts)
    scenario = f"scenarios/{FAMILIES['F2']['stem']}.yaml"
    cfg, geom, corridor, v0, sf = network_inputs(scenario)
    timing = {
        rule: expected_under_timing(cfg, geom, corridor, v0, sf, rule)
        for rule in ("arrival", "departure")
    }
    doc = {
        "schema": "flowstate.a3_expected/1",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "spec": "docs/FRISCO_PROTOCOL.md, adoption of Amendment 3, items 1 and 3; docs/A3_RANGE_ROUND.md §2 and §4",
        "label": "[computed, not run]",
        "inputs": {
            "demand": DEMAND,
            "observations": CAL_OBS,
            "s_max": S_MAX,
            "network_from": scenario,
        },
        "from_counts": {k: v for k, v in counts.items()},
        "against_protocol": check,
        "plan_on_model_volumes": {
            "note": "the shares the plan builder meets on the scenario's own volumes at the gore (rates, the "
            "population's mean v0); 'arrival' is the built timing rule, 'departure' the alternative it replaced",
            **timing,
        },
        "departure_rule_exit_shift": departure_exit_shift(timing["departure"]),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, default=float) + "\n")
    print(json.dumps(check, indent=1))
    for rule, t in timing.items():
        print(
            rule,
            {
                a: {
                    k: (round(v, 3) if isinstance(v, float) else v)
                    for k, v in x.items()
                    if k != "s_w"
                }
                for a, x in t["arms"].items()
            },
            {k: round(v, 1) for k, v in t["free_flow_s"].items()},
        )
    return 0 if check["all_same"] else 1


# --------------------------------------------------------------------------- check-copy


def _weave_blocks(doc: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        r["name"]: r["weave"]
        for r in doc["network"]["ramps"]
        if r.get("kind") == "on" and r.get("weave") is not None
    }


def check_copy(
    source: Path, copy: Path, source_hash: str, u: float | None, w2_off: bool
) -> tuple[list[str], str | None]:
    """``(refusals, the copy's config hash)``: the copy is the source but for its name, the share and the switches."""
    import yaml

    from flowstate_core.config import ScenarioConfig, config_hash

    why: list[str] = []
    try:
        src_cfg = ScenarioConfig.from_yaml(source)
        cpy_cfg = ScenarioConfig.from_yaml(copy)
    except Exception as exc:  # every refusal is reported
        return [f"does not validate: {exc}"], None
    if config_hash(src_cfg) != source_hash:
        why.append(f"{source} hashes {config_hash(src_cfg)} under policy v4, not {source_hash}")
    src = src_cfg.model_dump(mode="json")
    cpy = cpy_cfg.model_dump(mode="json")
    raw = yaml.safe_load(copy.read_text())
    th = _weave_blocks(cpy).get(TH52["entrance"])
    ruth = _weave_blocks(cpy).get(RUTH["entrance"])
    if th is None or ruth is None:
        return [*why, "the copy lacks a T.H.52 or a Ruth St weave block"], None
    share = th.get("ramp_to_ramp_share")
    if u is None:
        if share is not None:
            why.append(f"the T.H.52 block sets ramp_to_ramp_share {share}; u0 sets none")
    elif share != {"u": u, "s_max": S_MAX}:
        why.append(
            f"the T.H.52 block's ramp_to_ramp_share is {share}, not {{u: {u:g}}} (s_max {S_MAX})"
        )
    raw_th = next(r for r in raw["network"]["ramps"] if r.get("name") == TH52["entrance"])["weave"]
    if u is not None and raw_th.get("ramp_to_ramp_share") != {"u": u}:
        why.append(
            f"the copy writes {raw_th.get('ramp_to_ramp_share')}, not {{u: {u:g}}} (s_max left at its default)"
        )
    if "ramp_to_ramp_share" in ruth:
        why.append("the Ruth St block sets a share; Ruth St stays proportional (item 3)")
    for name, blk in _weave_blocks(cpy).items():
        want = 0.0 if w2_off else 1.0
        got = [blk["weave_params"].get(s) for s in W2_SWITCHES]
        if w2_off and got != [want] * 3:
            why.append(f"{name}: W2's switches {got}, not all 0")
    # everything else is the source's
    norm = json.loads(json.dumps(cpy))
    norm["name"] = src["name"]
    for r in norm["network"]["ramps"]:
        if r.get("kind") == "on" and r.get("weave") is not None:
            r["weave"].pop("ramp_to_ramp_share", None)
            if w2_off:
                src_blk = _weave_blocks(src)[r["name"]]["weave_params"]
                for s in W2_SWITCHES:
                    if s in src_blk:
                        r["weave"]["weave_params"][s] = src_blk[s]
                    else:
                        r["weave"]["weave_params"].pop(s, None)
    if json.dumps(norm, sort_keys=True) != json.dumps(src, sort_keys=True):
        diff = sorted(
            k
            for k in set(norm) | set(src)
            if json.dumps(norm.get(k), sort_keys=True) != json.dumps(src.get(k), sort_keys=True)
        )
        why.append(
            f"the copy differs from its source beyond its name, the share and the switches: {diff}"
        )
    return why, config_hash(cpy_cfg)


def cmd_check_copy(a: argparse.Namespace) -> int:
    why, h = check_copy(Path(a.source), Path(a.copy), a.source_hash, a.u, a.w2_off)
    if why:
        print(f"check-copy {a.copy}: REFUSED")
        for w in why:
            print("  " + w)
        return 1
    print(
        f"check-copy {a.copy}: ok; config hash {h} (policy v4); source {a.source} {a.source_hash}"
    )
    return 0


# --------------------------------------------------------------------------- lanes (item 4, the kept trajectory)


def lane_crossings(
    traj: Any, x_ref: float, hours: Sequence[tuple[str, float]]
) -> dict[str, dict[str, int]]:
    """Per scored hour and lane, the vehicles crossing ``x_ref`` (consecutive samples ``x < x_ref <= x'``; the
    crossing time interpolated, the lane the sample at or past ``x_ref``).

    Args:
        traj: A frame with ``t``, ``veh_id``, ``x``, ``lane``.
        x_ref: The cross-section [m, linear x].
        hours: ``(clock, start [s])`` of each scored hour.
    """
    import numpy as np

    df = traj.sort_values(["veh_id", "t"], kind="stable")
    x = df["x"].to_numpy(dtype=float)
    t = df["t"].to_numpy(dtype=float)
    lane = df["lane"].to_numpy()
    vid = df["veh_id"].to_numpy()
    same = np.zeros(len(df), dtype=bool)
    same[1:] = vid[1:] == vid[:-1]
    prev_x = np.empty_like(x)
    prev_x[0] = np.nan
    prev_x[1:] = x[:-1]
    prev_t = np.empty_like(t)
    prev_t[0] = np.nan
    prev_t[1:] = t[:-1]
    hit = same & (prev_x < x_ref) & (x >= x_ref)
    frac = (x_ref - prev_x[hit]) / (x[hit] - prev_x[hit])
    tc = prev_t[hit] + frac * (t[hit] - prev_t[hit])
    lanes = lane[hit]
    out: dict[str, dict[str, int]] = {}
    for name, start in hours:
        sel = (tc >= start) & (tc < start + 3600.0)
        vals, counts = np.unique(lanes[sel], return_counts=True)
        out[name] = {str(int(v)): int(c) for v, c in zip(vals, counts, strict=True)}
    return out


def cmd_lanes(label: str, out: Path | None) -> int:
    import pandas as pd
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    out = out or REPO / f"artifacts/a3_lanes_{label}.json"
    bat = load(f"artifacts/validation_{label}.json")
    if bat is None:
        print(f"lanes {label}: artifacts/validation_{label}.json is missing")
        return 3
    row = bat["per_seed"][0]
    run_dir = REPO / str(row["run_dir"])
    path = run_dir / "trajectories.parquet"
    rec: dict[str, Any] = {
        "schema": "flowstate.a3_lanes/1",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "label": label,
        "seed": int(row["seed"]),
        "run_dir": str(row["run_dir"]),
    }
    problems = []
    if int(row["seed"]) != KEPT_SEED:
        problems.append(f"the battery's first seed is {row['seed']}, not {KEPT_SEED}")
    if not path.is_file():
        problems.append(
            f"{path} is missing (the battery keeps its first seed's trajectory; read on the VM)"
        )
    if problems:
        rec["problems"] = problems
        out.write_text(json.dumps(rec, indent=1) + "\n")
        print(json.dumps(rec))
        return 3
    meta = json.loads((run_dir / "meta.json").read_text())
    ramp = next(r for r in meta["ramps"] if r["name"] == TH52["entrance"])
    s790 = next(h["x_ref_m"] for h in row["link_hours"] if h["station"] == "S790")
    hours = sorted(
        {
            (h["clock"], float(h["window_start_s"]))
            for h in row["link_hours"]
            if h["station"] == "S790"
        },
        key=lambda x: x[1],
    )
    gore = float(ramp["attach_end_x_m"]) - GORE_OFFSET_M
    xs = {"S790": float(s790), "gore": gore}
    lo, hi = min(xs.values()) - 200.0, max(xs.values()) + 200.0
    table = pq.read_table(path, columns=["t", "veh_id", "x", "lane"])
    mask = pc.and_(pc.greater_equal(table["x"], lo), pc.less_equal(table["x"], hi))
    frame = table.filter(mask).to_pandas()
    del table
    rec["x_ref_m"] = xs
    rec["gore_note"] = (
        f"{GORE_OFFSET_M:g} m upstream of the T.H.52 section's end ({TH52['entrance']} attach_end_x_m "
        f"{ramp['attach_end_x_m']}), the auxiliary lane being lane 0"
    )
    rec["hours"] = [h[0] for h in hours]
    rec["veh_h_by_lane"] = {k: lane_crossings(frame, x, hours) for k, x in xs.items()}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, indent=1) + "\n")
    print(json.dumps({k: rec[k] for k in ("label", "seed", "x_ref_m", "veh_h_by_lane")}))
    _ = pd  # pandas is the frame's type
    return 0


# --------------------------------------------------------------------------- evaluate (items 4-6)


def battery_problems(bat: Mapping[str, Any] | None, label: str, scenario: str) -> list[str]:
    if bat is None:
        return [f"{label}: artifacts/validation_{label}.json is missing"]
    p: list[str] = []
    if bat.get("scenario") != scenario:
        p.append(f"{label}: the battery ran {bat.get('scenario')!r}, not {scenario}")
    seeds = [int(r["seed"]) for r in bat.get("per_seed") or []]
    if seeds != seeds_of_record():
        p.append(
            f"{label}: not the 20 seeds of record, spawn_seeds({MASTER_SEED}, {N_SEEDS}), in order"
        )
    if (bat.get("observations") or {}).get("path") != CAL_OBS:
        p.append(
            f"{label}: scored against {(bat.get('observations') or {}).get('path')!r}, not {CAL_OBS}"
        )
    if (bat.get("criteria_profile") or {}).get("name") != "fhwa_tat3_2004":
        p.append(f"{label}: criteria profile is not fhwa_tat3_2004")
    return p


def station_hours(bat: Mapping[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """Per station of :data:`STATIONS` and scored hour: per-seed simulated flow and GEH, the observed flow."""
    out: dict[str, dict[str, dict[str, Any]]] = {s: {} for s in STATIONS}
    for r in bat["per_seed"]:
        for h in r["link_hours"]:
            if h["station"] in out:
                cell = out[h["station"]].setdefault(
                    h["clock"], {"sim": [], "geh": [], "obs": h["obs_veh_h"]}
                )
                cell["sim"].append(h["sim_veh_h"])
                cell["geh"].append(h["geh"])
    return out


def gate_rows(gate: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if gate is None:
        return None
    keep = ("C1", "C3", "C4", "C6")
    return {
        f"{r['check']} {r['day_set']}": {"value": r.get("value"), "status": r.get("status")}
        for r in gate.get("checks") or []
        if r.get("check") in keep
    }


def c1_c3_per_replicate(bat: Mapping[str, Any]) -> dict[str, Any] | None:
    """The gate's calibration-day C1 (GEH < 5 share, hours anchored at the study period's start) and C3 (15-min
    station-speed RMSPE) per replicate, re-scored from each replicate's stored files with the gate's own code
    (``validation.baseline_gate.score_day_set``): the gate artifact records C3 per replicate but not C1. None when
    a replicate's ``metrics.json`` or ``observed_scores.json`` is missing."""
    from validation.baseline_gate import score_day_set
    from validation.battery import load_replicate_analysis
    from validation.observed import ObservedCorridor

    dirs = [REPO / str(r["run_dir"]) for r in bat["per_seed"]]
    if not all(
        (d / "metrics.json").is_file() and (d / "observed_scores.json").is_file() for d in dirs
    ):
        return None
    scored_end = bat.get("scored_end_s")
    analyses = [
        load_replicate_analysis(d, None if scored_end is None else float(scored_end)) for d in dirs
    ]
    scored = ObservedCorridor.from_json(REPO / str(bat["observations"]["path"]))
    cal = ObservedCorridor.from_json(REPO / CAL_OBS)
    score = score_day_set(
        "calibration", cal, [a.scores for a in analyses], scored_against=scored, path=CAL_OBS
    )
    return {
        "seeds": [int(r["seed"]) for r in bat["per_seed"]],
        "c1": [float(v) for v in score.geh_fraction_per_replicate],
        "c3_900": [float(v) for v in score.rmspe_per_replicate.get(900.0, ())],
    }


def share_readings(bat: Mapping[str, Any]) -> dict[str, Any]:
    """The T.H.52 share per seed: the plan's record (set arms), the planned routes (journeys.parquet, every arm) and
    the departed entrants' destinations (vehicles.parquet)."""
    import pandas as pd

    rows = []
    for r in bat["per_seed"]:
        rd = REPO / str(r["run_dir"])
        rec: dict[str, Any] = {"seed": int(r["seed"])}
        meta_p = rd / "meta.json"
        if meta_p.is_file():
            meta = json.loads(meta_p.read_text())
            names = [x["name"] for x in meta["config"]["network"]["ramps"]]
            k, j = names.index(TH52["entrance"]), names.index(TH52["exit"])
            plan = next(
                (x for x in meta.get("ramp_to_ramp_shares") or [] if x["ramp"] == TH52["entrance"]),
                None,
            )
            if plan is not None:
                rec["plan"] = {
                    key: plan.get(key)
                    for key in (
                        "form",
                        "u",
                        "s_max",
                        "share_drawn",
                        "share_realized",
                        "n_entrants",
                        "n_ramp_to_ramp",
                        "n_swapped_to_exit",
                        "n_swapped_from_exit",
                        "n_clipped",
                        "clipped_windows_t0_s",
                        "free_flow_s",
                    )
                }
            jp = rd / "journeys.parquet"
            if jp.is_file():
                jr = pd.read_parquet(jp, columns=["route"])["route"]
                n_on = int(jr.str.match(rf"^on{k}(_|$)").sum())
                rec["planned_share"] = float((jr == f"on{k}_off{j}").sum() / n_on) if n_on else None
        vp = rd / "vehicles.parquet"
        if vp.is_file():
            v = pd.read_parquet(vp, columns=["origin", "destination"])
            ent = v[v.origin == TH52["entrance"]]
            rec["departed_share"] = (
                float((ent.destination == TH52["exit"]).mean()) if len(ent) else None
            )
            rec["departed_entrants"] = len(ent)
        rows.append(rec)
    clipped = [x["plan"]["n_clipped"] for x in rows if "plan" in x]
    return {
        "per_seed": rows,
        "planned_share": summary([x.get("planned_share") for x in rows]),
        "departed_share": summary([x.get("departed_share") for x in rows]),
        "clipped_windows": summary(clipped) if clipped else None,
    }


def weave_readings(bat: Mapping[str, Any], w2: Any) -> dict[str, Any]:
    """Given-up exits, W1b releases (share of the entrance's departures, against CW5b's 1 % as a disclosed cost)
    and the crossings (``n_changed_in``, ``n_changed_out``) per weave, pooled over the seeds."""
    pools: dict[str, dict[str, Any]] = {}
    for r in bat["per_seed"]:
        rd = REPO / str(r["run_dir"])
        if not (rd / "meta.json").is_file():
            continue
        meta = json.loads((rd / "meta.json").read_text())
        deps = {x["name"]: x.get("n_departed") for x in meta["ramps"]}
        for z in meta.get("weave_sections") or []:
            p = pools.setdefault(
                z["ramp"],
                {
                    "exit": z.get("exit"),
                    "n_seeds": 0,
                    "n_missed_exit": 0,
                    "n_entrant_took_exit": 0,
                    "entrance_departed": 0,
                    "n_changed_in": 0,
                    "n_changed_out": 0,
                    **{c: 0 for c in w2.W2_COUNTERS},
                },
            )
            p["n_seeds"] += 1
            for key in (
                "n_missed_exit",
                "n_entrant_took_exit",
                "n_changed_in",
                "n_changed_out",
                *w2.W2_COUNTERS,
            ):
                p[key] += int(z.get(key) or 0)
            p["entrance_departed"] += int(deps.get(z["ramp"]) or 0)
    for p in pools.values():
        p["w1b_release_share"] = (
            p["n_entrant_took_exit"] / p["entrance_departed"] if p["entrance_departed"] else None
        )
        p["w1b_release_above_1pct"] = (p["w1b_release_share"] or 0.0) > W1B_DISCLOSED_SHARE
    return pools


def gate_consistency(
    c1c3: Mapping[str, Any] | None, gate: Mapping[str, Any] | None
) -> dict[str, Any]:
    """The re-scored per-replicate C1 and C3 against the gate artifact: the C1 per-replicate mean must be the gate's
    ``pass_fraction_ci.mean`` and C3 its ``rmspe["900"].per_replicate`` (both on the calibration days)."""
    if c1c3 is None or gate is None:
        return {"consistent": None, "note": "nothing to compare"}
    cal = ((gate.get("day_sets") or {}).get("calibration")) or {}
    g_c1 = ((cal.get("geh") or {}).get("pass_fraction_ci") or {}).get("mean")
    g_c3 = ((cal.get("rmspe") or {}).get("900") or {}).get("per_replicate")
    c1 = [v for v in c1c3["c1"] if not math.isnan(v)]
    mean_c1 = sum(c1) / len(c1) if c1 else math.nan
    c1_ok = g_c1 is not None and abs(mean_c1 - float(g_c1)) < 1e-9
    c3_ok = (
        g_c3 is not None
        and len(g_c3) == len(c1c3["c3_900"])
        and all(
            (a is None and math.isnan(b)) or (a is not None and abs(float(a) - b) < 1e-9)
            for a, b in zip(g_c3, c1c3["c3_900"], strict=True)
        )
    )
    return {
        "consistent": bool(c1_ok and c3_ok),
        "c1_mean": mean_c1,
        "gate_c1_mean": g_c1,
        "c3_matches_gate": c3_ok,
    }


def arm_readings(family: str, arm: str, w1b: Any, w2: Any) -> dict[str, Any]:
    label = label_of(family, arm)
    scenario = scenario_of(family, arm)
    bat = load(f"artifacts/validation_{label}.json")
    gate = load(f"artifacts/baseline_gate_{label}.json")
    rec: dict[str, Any] = {
        "label": label,
        "scenario": scenario,
        "u": ARMS[arm],
        "problems": battery_problems(bat, label, scenario),
    }
    if bat is None:
        return rec
    if gate is None:
        rec["problems"].append(f"{label}: artifacts/baseline_gate_{label}.json is missing")
    elif gate.get("config_hash") != bat.get("config_hash"):
        rec["problems"].append(f"{label}: the gate's config_hash is not its battery's")
    rec["config_hash"] = bat.get("config_hash")
    rec["seeds"] = [int(r["seed"]) for r in bat["per_seed"]]
    rec["stations"] = station_hours(bat)
    rec["departed"] = [r["insertion"]["departed_fraction"] for r in bat["per_seed"]]
    rec["realised_demand"] = {
        "mean": (bat.get("insertion") or {}).get("mean_departed_fraction"),
        "min": (bat.get("insertion") or {}).get("min_departed_fraction"),
        "th52_departed": summary(
            [
                next(
                    (
                        x["fraction"]
                        for x in r["insertion"]["ramps"]
                        if x["name"] == TH52["entrance"]
                    ),
                    None,
                )
                for r in bat["per_seed"]
            ]
        ),
    }
    rec["gate"] = gate_rows(gate)
    rec["gate_c3_900_per_replicate"] = (
        (((gate or {}).get("day_sets") or {}).get("calibration") or {})
        .get("rmspe", {})
        .get("900", {})
        .get("per_replicate")
    )
    if (
        family == "F2"
        and arm == "u0"
        and bat.get("config_hash") != FAMILIES["F2"]["source_hash_v4"]
    ):
        rec["problems"].append(
            f"{label}: config_hash {bat.get('config_hash')}, not the committed file's "
            f"{FAMILIES['F2']['source_hash_v4']} (policy v4)"
        )
    try:
        rec["c1_c3"] = c1_c3_per_replicate(bat)
    except Exception as exc:  # recorded; the verdict is then undetermined
        rec["c1_c3"] = None
        rec["problems"].append(
            f"{label}: per-replicate C1/C3 not re-scored ({type(exc).__name__}: {exc})"
        )
    rec["c1_c3_against_gate"] = gate_consistency(rec["c1_c3"], gate)
    if rec["c1_c3_against_gate"].get("consistent") is False:
        rec["problems"].append(
            f"{label}: the re-scored per-replicate C1/C3 do not reproduce the gate's "
            f"({rec['c1_c3_against_gate']})"
        )
    rows = w1b.arm(REPO / f"artifacts/validation_{label}.json")
    ex = w2.extra(REPO / f"artifacts/validation_{label}.json")
    rec["collisions"] = {
        "total": sum(int(rows[s]["collisions"] or 0) for s in rows),
        "by_section": [ex[s].get("collisions_by_section") for s in rows],
    }
    if all(rows[s]["sections"] is not None for s in rows):
        rec["locks_front_row"] = [
            f"{s}:{z['ramp']}" for s in rows for z in rows[s]["sections"] if z["locked"]
        ]
    else:
        rec["locks_front_row"] = None
    rec["locks_battery"] = [s for s in rows if ex[s]["battery_locked"]]
    rec["weaves"] = weave_readings(bat, w2)
    rec["share"] = share_readings(bat)
    rec["lanes"] = load(f"artifacts/a3_lanes_{label}.json")
    return rec


def contrasts(arm: Mapping[str, Any], ref: Mapping[str, Any]) -> dict[str, Any]:
    """``arm - ref`` seed by seed (paired t, n - 1 df)."""
    out: dict[str, Any] = {"stations": {}}
    for st in STATIONS:
        out["stations"][st] = {}
        for hour, cell in arm["stations"][st].items():
            rcell = ref["stations"][st].get(hour)
            if rcell is None:
                continue
            out["stations"][st][hour] = {
                "flow_veh_h": paired(cell["sim"], rcell["sim"]),
                "geh_lt5_seeds": {
                    "arm": sum(g < 5 for g in cell["geh"]),
                    "ref": sum(g < 5 for g in rcell["geh"]),
                },
            }
    out["departed"] = paired(arm["departed"], ref["departed"])
    a, r = arm.get("c1_c3"), ref.get("c1_c3")
    out["c1_calibration"] = paired(a["c1"], r["c1"]) if a and r else None
    out["c3_calibration_900"] = paired(a["c3_900"], r["c3_900"]) if a and r else None
    return out


def materiality(
    u1: Mapping[str, Any], u0: Mapping[str, Any], con: Mapping[str, Any]
) -> dict[str, Any]:
    """Item 5 at u1 against u0 (never re-thresholded)."""
    s790 = (con["stations"].get("S790") or {}).get(PEAK, {}).get("flow_veh_h")
    dep = con.get("departed")
    missing = [
        n
        for n, v in (
            ("S790 06:30", s790),
            ("departed", dep),
            ("C1", con.get("c1_calibration")),
            ("C3", con.get("c3_calibration_900")),
        )
        if v is None
    ]
    if u1["locks_front_row"] is None or u0["locks_front_row"] is None:
        missing.append("front-row locks")
    if missing:
        return {"verdict": None, "undetermined": missing}
    assert s790 is not None and dep is not None
    m1 = s790["lo95"] >= M1_LOWER_VEH_H and dep["mean"] >= -M1_DEMAND_PP
    m2_rows = []
    for st in STATIONS:
        for hour, cell in con["stations"][st].items():
            d = cell["geh_lt5_seeds"]["arm"] - cell["geh_lt5_seeds"]["ref"]
            if abs(d) >= M2_SEEDS:
                m2_rows.append(
                    {
                        "station": st,
                        "hour": hour,
                        "u1": cell["geh_lt5_seeds"]["arm"],
                        "u0": cell["geh_lt5_seeds"]["ref"],
                    }
                )
    m2 = bool(m2_rows)

    def beyond(c: Mapping[str, Any]) -> bool:
        return c["lo95"] > M3_PP or c["hi95"] < -M3_PP

    m3_c1, m3_c3 = beyond(con["c1_calibration"]), beyond(con["c3_calibration_900"])
    one_arm = {
        "collisions": (u1["collisions"]["total"] > 0) != (u0["collisions"]["total"] > 0),
        "locks_front_row": bool(u1["locks_front_row"]) != bool(u0["locks_front_row"]),
        "locks_battery": bool(u1["locks_battery"]) != bool(u0["locks_battery"]),
    }
    m4 = any(one_arm.values())
    within = s790["lo95"] > -NOT_MATERIAL_VEH_H and s790["hi95"] < NOT_MATERIAL_VEH_H
    material = m1 or m2 or m3_c1 or m3_c3 or m4
    verdict = "material" if material else "not material" if within else "inconclusive"
    return {
        "verdict": verdict,
        "M1": {
            "holds": m1,
            "s790_0630_paired": s790,
            "departed_paired": dep,
            "rule": f"lower bound >= +{M1_LOWER_VEH_H:g} veh/h and mean realised demand >= -{M1_DEMAND_PP:g}",
        },
        "M2": {
            "holds": m2,
            "rows": m2_rows,
            "rule": f"|GEH < 5 count difference| >= {M2_SEEDS} of 20, S790 or S97, any hour",
        },
        "M3": {
            "holds": m3_c1 or m3_c3,
            "c1": con["c1_calibration"],
            "c3_900": con["c3_calibration_900"],
            "c1_beyond": m3_c1,
            "c3_beyond": m3_c3,
            "rule": f"paired interval wholly beyond +-{M3_PP:g}",
        },
        "M4": {"holds": m4, "one_arm_only": one_arm},
        "s790_within_100": within,
        "note": "no seeds are added; no share is chosen from the round",
    }


def p10_comparison(rec: Mapping[str, Any], family: str) -> dict[str, Any] | None:
    """The u0 arm against p10's battery of the same physics, seed by seed: every per-seed field p10 recorded but
    run_dir (F2: Amendment 4's re-run of p10's arm B, item (i); F1: p10's arm A). Every difference is reported."""
    p10 = load(f"artifacts/validation_{FAMILIES[family]['p10']}.json")
    bat = load(f"artifacts/validation_{rec['label']}.json")
    if p10 is None or bat is None:
        return None
    by = {int(r["seed"]): r for r in p10["per_seed"]}
    rows = []
    for r in bat["per_seed"]:
        q = by.get(int(r["seed"]))
        if q is None:
            rows.append({"seed": int(r["seed"]), "in_p10": False})
            continue
        diff = sorted(
            k
            for k in q
            if k != "run_dir"
            and json.dumps(r.get(k), sort_keys=True) != json.dumps(q.get(k), sort_keys=True)
        )
        rows.append(
            {
                "seed": int(r["seed"]),
                "in_p10": True,
                "fields_differing": diff,
                # fields today's battery writes and p10's did not (later code, e.g. weave_releases): listed, not compared
                "fields_new": sorted(k for k in r if k not in q),
                "departed": [
                    r["insertion"]["departed_fraction"],
                    q["insertion"]["departed_fraction"],
                ],
            }
        )
    return {
        "p10_battery": f"artifacts/validation_{FAMILIES[family]['p10']}.json",
        "p10_config_hash": p10.get("config_hash"),
        "identical_seeds": sum(1 for x in rows if x.get("in_p10") and not x["fields_differing"]),
        "departed_unequal": [
            x["seed"] for x in rows if x.get("in_p10") and x["departed"][0] != x["departed"][1]
        ],
        "per_seed": rows,
    }


def range_reading(u0: Mapping[str, Any], u1: Mapping[str, Any]) -> dict[str, Any]:
    """Adoption (c): every reading the T.H.52 section can move at u = 0 with its u = 1 value beside it."""

    def mean(cell: Mapping[str, Any] | None) -> float | None:
        return None if not cell else sum(cell["sim"]) / len(cell["sim"])

    rows: dict[str, Any] = {}
    for st in STATIONS:
        for hour in u0["stations"][st]:
            rows[f"{st} {hour} veh/h"] = [
                mean(u0["stations"][st].get(hour)),
                mean(u1["stations"][st].get(hour)),
            ]
    for key in (
        "C1 calibration",
        "C3 calibration",
        "C4 calibration",
        "C6 calibration",
        "C1 validation",
        "C3 validation",
        "C6 validation",
    ):
        rows[key] = [
            ((u0.get("gate") or {}).get(key) or {}).get("value"),
            ((u1.get("gate") or {}).get(key) or {}).get("value"),
        ]
    rows["collisions"] = [u0["collisions"]["total"], u1["collisions"]["total"]]
    rows["locks (front row)"] = [u0["locks_front_row"], u1["locks_front_row"]]
    rows["locks (battery)"] = [u0["locks_battery"], u1["locks_battery"]]
    return {"label": RANGE_LABEL, "u0_u1": rows}


def evaluate() -> dict[str, Any]:
    w1b, w2 = readers()
    doc: dict[str, Any] = {
        "schema": "flowstate.a3_range/1",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "spec": "docs/FRISCO_PROTOCOL.md, adoption of Amendment 3, items 1-7; docs/A3_RANGE_ROUND.md",
        "never": "no share is chosen from the round; u05 shows shape only; every result is reported",
        "families": {},
        "problems": [],
    }
    for family, fam in FAMILIES.items():
        arms = {arm: arm_readings(family, arm, w1b, w2) for arm in ARMS}
        out: dict[str, Any] = {
            "what": fam["what"],
            "arms": arms,
            "contrasts": {},
            "verdict_u1": None,
        }
        for arm in arms.values():
            doc["problems"] += arm["problems"]
        base = arms["u0"]
        for arm in ("u05", "u1"):
            if "stations" in arms[arm] and "stations" in base:
                out["contrasts"][arm] = contrasts(arms[arm], base)
        if "u1" in out["contrasts"] and not arms["u1"]["problems"] and not base["problems"]:
            out["verdict_u1"] = materiality(arms["u1"], base, out["contrasts"]["u1"])
            out["range"] = range_reading(base, arms["u1"])
        else:
            out["verdict_u1"] = {
                "verdict": None,
                "undetermined": ["u0 or u1 missing or with problems"],
            }
        if "stations" in base:
            out["u0_against_p10"] = p10_comparison(base, family)
        doc["families"][family] = out
    return doc


def cmd_evaluate(out: Path) -> int:
    doc = evaluate()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, default=float) + "\n")
    print(
        json.dumps({f: (x["verdict_u1"] or {}).get("verdict") for f, x in doc["families"].items()})
    )
    for p in doc["problems"]:
        print("problem:", p)
    return 3 if doc["problems"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="corridor_a3.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("expected")
    e.add_argument(
        "--out", type=Path, default=REPO / "artifacts/a3_range_2026-10-07/a3_expected.json"
    )
    c = sub.add_parser("check-copy")
    c.add_argument("--source", required=True)
    c.add_argument("--source-hash", required=True)
    c.add_argument("--copy", required=True)
    c.add_argument("--u", type=float, default=None)
    c.add_argument("--w2-off", action="store_true")
    ln = sub.add_parser("lanes")
    ln.add_argument("--label", required=True)
    ln.add_argument("--out", type=Path, default=None)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--out", type=Path, default=REPO / "artifacts/a3_range.json")
    a = ap.parse_args(argv)
    if a.cmd == "expected":
        return cmd_expected(a.out)
    if a.cmd == "check-copy":
        return cmd_check_copy(a)
    if a.cmd == "lanes":
        return cmd_lanes(a.label, a.out)
    return cmd_evaluate(a.out)


if __name__ == "__main__":
    raise SystemExit(main())
