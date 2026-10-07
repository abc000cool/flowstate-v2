"""Counterfactual arms (scratch monkeypatches of the HEAD snapshot's runner; never package edits).

usage: cfarm.py CF NAME SEEDS [arm.py options]   CF in {h1, h2, h1h2}
  h1: the overlap dead zone removed for non-partner leaders (see prereg.md)
  h2: an entrant halted at the auxiliary lane's end takes the exit (mirror of exit_giveup_m)
"""
import math, runpy, sys
from pathlib import Path
S = Path(__file__).resolve().parent
cf = sys.argv[1]
sys.argv = [str(S / "arm.py")] + sys.argv[2:]
sys.path.insert(0, str(S / "head" / "packages" / "microsim"))
from microsim import runner as R

CUR = {"ws": None}
COUNT = {"h1_beside_candidates": 0, "h1_beside_chosen": 0, "h2_took_exit": 0}
orig_step = R._weave_step
orig_choose = R._weave_choose_gap

RX = {}      # relaxed vehicle -> state
LAST = {}    # entrant -> last (road, lane) off internal lanes
ENTRANT = {}
SUCC = {}
TAU_R, RESTORE, FLOOR_FRAC = 7.5, 4.0, 0.5
COUNT.update({"h3_granted_entrant": 0, "h3_granted_follower": 0, "h3_restored_lc": 0, "h3_restored_exp": 0, "h3_arrival_crossings": 0, "h3_runner_crossings": 0})

def _succ(mod, where):
    if where not in SUCC:
        try:
            SUCC[where] = {(l[0].rsplit("_", 1)[0], int(l[0].rsplit("_", 1)[1])) for l in mod.lane.getLinks(f"{where[0]}_{where[1]}")}
        except Exception:
            SUCC[where] = set()
    return SUCC[where]

def _set_tau(mod, ws, vid, tau):
    mod.vehicle.setTau(vid, tau)
    p = ws["veh_params"].get(vid)
    if p is not None:
        p["T"] = tau

def _grant(mod, ws, vid, gap_net, v, t, where, role):
    rx = RX.get(vid)
    t_own = rx["t_own"] if rx else float(mod.vehicle.getTau(vid))
    step_s = float(ws["step_s"])
    floor = max(step_s, FLOOR_FRAC * t_own)
    if v <= 1e-6:
        return
    t0 = min(max(gap_net / v, floor), max(t_own, floor))
    if t0 >= t_own - 1e-12:
        return
    if rx is not None and not t0 < rx["tau_set"]:
        return
    RX[vid] = {"t_own": t_own, "t0": t0, "g_s": t, "tau_set": t0, "where": where}
    pv = ws["veh_params"].get(vid)
    if pv is not None:
        pv["T0"] = t_own
    _set_tau(mod, ws, vid, t0)
    COUNT["h3_granted_" + role] += 1

def relax(mod, tc, ws, results, t):
    step_s = float(ws["step_s"])
    for vid in sorted(RX):
        rx = RX[vid]; res = results.get(vid)
        if res is None:
            del RX[vid]; continue
        road = str(res[tc.VAR_ROAD_ID]); now = (road, int(res[tc.VAR_LANE_INDEX]))
        if not road.startswith(":"):
            if now != rx["where"] and now not in _succ(mod, rx["where"]):
                _set_tau(mod, ws, vid, rx["t_own"]); del RX[vid]; COUNT["h3_restored_lc"] += 1; continue
            rx["where"] = now
        el = t - rx["g_s"]
        if el >= RESTORE * TAU_R:
            _set_tau(mod, ws, vid, rx["t_own"]); del RX[vid]; COUNT["h3_restored_exp"] += 1; continue
        floor = max(step_s, FLOOR_FRAC * rx["t_own"])
        tau = rx["t_own"] - (rx["t_own"] - rx["t0"]) * math.exp(-el / TAU_R)
        tau = min(max(tau, floor), max(rx["t_own"], rx["t0"]))
        if abs(tau - rx["tau_set"]) > 1e-12:
            _set_tau(mod, ws, vid, tau); rx["tau_set"] = tau
    sec = set(ws["edges"]); ramp = set(ws["ramp_edges"])
    for vid, res in results.items():
        road = str(res[tc.VAR_ROAD_ID])
        if road.startswith(":"):
            continue
        ent = ENTRANT.get(vid)
        if ent is None:
            ent = ENTRANT[vid] = mod.vehicle.getRouteID(vid).startswith("on") and vid not in ws["exiting_ids"]
        if not ent:
            continue
        lane = int(res[tc.VAR_LANE_INDEX]); prev = LAST.get(vid); LAST[vid] = (road, lane)
        if road not in sec or lane < 1 or prev is None:
            continue
        arrival = prev[0] in ramp
        if not (arrival or (prev[0] in sec and prev[1] == 0)):
            continue
        COUNT["h3_arrival_crossings" if arrival else "h3_runner_crossings"] += 1
        v = float(res[tc.VAR_SPEED])
        lead = mod.vehicle.getLeader(vid, 250.0)
        if lead is not None and lead[0]:
            _grant(mod, ws, vid, float(lead[1]), v, t, (road, lane), "entrant")
        fol = mod.vehicle.getFollower(vid, 250.0)
        if fol is not None and fol[0]:
            fres = results.get(fol[0])
            if fres is not None:
                fwhere = (str(fres[tc.VAR_ROAD_ID]), int(fres[tc.VAR_LANE_INDEX]))
                _grant(mod, ws, fol[0], float(fol[1]), float(fres[tc.VAR_SPEED]), t, fwhere, "follower")

def step(mod, tc, ws, results, t, *a, **k):
    CUR["ws"] = ws
    ws["_t"] = t
    if "h3" in cf:
        relax(mod, tc, ws, results, t)
    orig_step(mod, tc, ws, results, t, *a, **k)
    if "h2" in cf:
        prm = ws["params"]
        exit_target = sorted(ws["exit_edges"])[-1]
        for vid, st in list(ws["veh"].items()):
            if st["dir"] <= 0 or vid not in results:
                continue
            res = results[vid]
            road = res[tc.VAR_ROAD_ID]
            if road not in ws["edge_index"] or int(res[tc.VAR_LANE_INDEX]) != 0:
                continue
            remaining = ws["lane_len_m"][road] - float(res[tc.VAR_LANEPOSITION]) + ws["beyond_m"][road]
            if remaining > prm["exit_giveup_m"] or float(res[tc.VAR_SPEED]) >= R.HALTING_SPEED_MS:
                continue
            if st["mode"] == R.LC_MODE_SCRIPTED_FORCE and st["requested_s"] == t:
                continue  # a change was requested this step
            mod.vehicle.changeTarget(vid, exit_target)
            mod.vehicle.setLaneChangeMode(vid, st["lc_mode_orig"])
            del ws["veh"][vid]
            ws.setdefault("h2_took_exit", []).append(vid)
            COUNT["h2_took_exit"] += 1

def REL(s_bb, v, p):
    """T at which the bumper gap s_bb is the equilibrium (the measured model's relaxed T)."""
    t_own = p.get("T0", p["T"])
    floor = max(0.5, 0.5 * t_own)
    if v <= 1e-6:
        return t_own
    return min(max((s_bb - p["s0"]) / v, floor), max(t_own, floor))

def choose(vid, x_c, v_c, p_c, v0_c, lane_list, x_of, v_of, p_of, v0_of, lookahead_m, committed, priority=False, relaxed_T=None):
    if "h5" in cf and relaxed_T is None:
        relaxed_T = REL
    if "h1" not in cf or priority or relaxed_T is not None:
        return orig_choose(vid, x_c, v_c, p_c, v0_c, lane_list, x_of, v_of, p_of, v0_of, lookahead_m, committed, priority, relaxed_T)
    ws = CUR["ws"]
    partners = set(ws["veh"]) | set(ws["pre"]) if ws is not None else set()
    # the original, with one change: a leader beside the changer (front behind the
    # changer's front, ahead of its rear) that is not a driven changer is admissible
    best = None
    n = len(lane_list)
    for i in range(n + 1):
        l_id = lane_list[i][1] if i < n else None
        f_id = lane_list[i - 1][1] if i > 0 else None
        if f_id is not None:
            p_f = p_of[f_id]
            s_f = x_c - p_c["len"] - x_of[f_id]
            dist = x_c - x_of[f_id]
            if s_f <= 0.0 or dist > lookahead_m:
                continue
            a_f = R._idm_accel(v_of[f_id], v0_of[f_id], s_f, v_of[f_id] - v_c, p_f["T"], p_f["a"], p_f["b"], p_f["s0"])
            open_ = a_f >= -p_f["b"]
        else:
            a_f, dist, open_ = math.inf, 0.0, True
        if l_id is not None:
            if x_of[l_id] < x_c or (x_of[l_id] == x_c and l_id < vid):
                beside = x_of[l_id] > x_c - p_c["len"]
                if cf == "h1eq" or not (beside and l_id not in partners):
                    continue
                COUNT["h1_beside_candidates"] += 1
            s_l = x_of[l_id] - p_of[l_id]["len"] - x_c
            a_c = R._idm_accel(v_c, v0_c, s_l, v_c - v_of[l_id], p_c["T"], p_c["a"], p_c["b"], p_c["s0"])
        else:
            a_c = math.inf
        enterable = open_ and a_c >= -p_c["b"]
        if f_id is not None and f_id == committed and open_:
            return l_id, f_id, a_f, a_c
        key = (enterable, open_, -dist)
        if best is None or key > best[0]:
            best = (key, l_id, f_id, a_f, a_c)
    if best is None:
        return None, None, math.inf, math.inf
    if best[1] is not None and x_of[best[1]] < x_c:
        COUNT["h1_beside_chosen"] += 1
    return best[1], best[2], best[3], best[4]

LOG = []
orig_coop = R._weave_cooperate
orig_cmd = R._weave_command
def coop_w(mod, tc, ws, results, lanes, x_of, v_of, p_of, v0_of, coop, vid, target_lane, *a, **k):
    CUR["changer"] = (vid, target_lane, x_of.get(vid), str(results[vid][tc.VAR_ROAD_ID]) if vid in results else "")
    CUR["t"] = ws.get("_t")
    return orig_coop(mod, tc, ws, results, lanes, x_of, v_of, p_of, v0_of, coop, vid, target_lane, *a, **k)
def cmd_w(mod, coop, vid, v, v0, p, a_target, step_s, follower=True):
    before = coop.get(vid)
    orig_cmd(mod, coop, vid, v, v0, p, a_target, step_s, follower)
    after = coop.get(vid)
    if after is not None and after is not before:
        ch = CUR.get("changer")
        LOG.append((CUR.get("t"), vid, v, after[0], max(a_target, -p["b"]), p["b"], bool(follower), ch[0], ch[1], ch[2], ch[3]))
if "log" in cf:
    R._weave_cooperate = coop_w
    R._weave_command = cmd_w
if "h5" in cf:
    import inspect, textwrap
    src = textwrap.dedent(inspect.getsource(orig_step))
    old = """            a_i = _idm_accel(
                v_foll,
                v0_i,
                g_foll + p_i["s0"],
                v_foll - v_ego,
                p_i["T"],"""
    assert src.count(old) == 1, "absorption check not found"
    src = src.replace(old, old.replace('p_i["T"],', '_H5_REL(g_foll + p_i["s0"], v_foll, p_i),'))
    R.__dict__["_H5_REL"] = REL
    exec(compile(src, "<h5 _weave_step>", "exec"), R.__dict__)
    orig_step = R.__dict__["_weave_step"]
R._weave_step = step
R._weave_choose_gap = choose
import atexit, json
atexit.register(lambda: print("CF_COUNTS", json.dumps(COUNT), flush=True))
def _dump():
    if LOG:
        import pickle
        pickle.dump({"columns": ["t","vid","v","v_new","a_cmd","b","follower","changer","target_lane","x_changer","road_changer"], "rows": LOG}, open(str(S / f"cmdlog_{sys.argv[1]}.pkl"), "wb"))
runpy.run_path(str(S / "arm.py"), run_name="__main__")
_dump()
