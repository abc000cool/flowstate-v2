"""Crossings (events.py output), waits, gore composition and the runner's command log, seeds 3-7."""
import glob, json, pickle, sys
import numpy as np, pandas as pd
S = sys.argv[1]
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
X0, XE = 829.07, 1134.09
def mvk(r):
    if r == "on0_off1": return "ramp->exit"
    if r.startswith("on0"): return "entrant"
    return "exiter" if r == "main_off1" else "through"
ev = pd.read_parquet(S + "/ev_base.parquet"); ev["xs"] = ev.x - X0
ev["kind"] = ev.mv + ":" + ev.from_L.astype(str) + ">" + ev.L.astype(str)
g = ev[ev.arm == "weave"].groupby("kind")
t = pd.DataFrame({"n_per_seed": g.size() / 5, "arrival_share": g.arrival.mean(), "xs_p10": g.xs.quantile(.1), "xs_p50": g.xs.median(), "xs_p90": g.xs.quantile(.9),
                  "v_p50": g.v.median(), "vlead_p50": g.v_lead.median(), "vfol_p50": g.v_fol.median(), "tglead_p10": g.tg_lead.quantile(.1), "tglead_p50": g.tg_lead.median(),
                  "tglag_p10": g.tg_lag.quantile(.1), "tglag_p50": g.tg_lag.median(), "glead_p50": g.g_lead.median(), "glag_p50": g.g_lag.median()})
print("## lane changes (weave, seeds 3-7): xs = metres past the section start; time gaps lead/changer speed, lag/follower speed"); print(t[t.n_per_seed >= 3].round(2).to_string())
rows, comp, cong_share = [], [], []
for m in sorted(glob.glob(S + "/w_base/th52_weave/*/*/meta.json")):
    d = m.rsplit("/", 1)[0]; seed = json.load(open(m))["seed"]
    df = pd.read_parquet(d + "/trajectories.parquet", columns=["t", "veh_id", "x", "lane", "v"])
    v = pd.read_parquet(d + "/vehicles.parquet").set_index("veh_id")
    df["L"] = np.where((df.x >= X0) & (df.x < XE), df.lane, df.lane + 1); df["mv"] = v.loc[df.veh_id, "route"].map(mvk).values
    first = df[(df.x >= X0) & (df.x < XE)].groupby("veh_id").t.min()
    up = df[(df.x >= 700) & (df.x < X0) & (df.L == 1)]; st = up.groupby((up.t // 60).astype(int)).v.mean()
    e = ev[(ev.arm == "weave") & (ev.seed == seed) & (((ev.mv == "exiter") & (ev.from_L == 1) & (ev.L == 0)) | ((ev.mv == "entrant") & (ev.from_L == 0) & (ev.L == 1)))].copy()
    e["wait"] = e.t - first.reindex(e.veh_id).values; e["congested"] = st.reindex((e.t // 60).astype(int)).values < 15; rows.append(e)
    cmin = set(st[st < 15].index); cong_share.append((seed, round(len([q for q in cmin if q >= 2]) / 18, 2)))
    w = df[((df.t // 60).astype(int)).isin(cmin)]
    for (a, b, name) in [(780, X0, "upstream 50 m"), (X0, 879, "section first 50 m"), (879, 1034, "section middle"), (1034, XE, "section last 100 m")]:
        z = w[(w.x >= a) & (w.x < b) & (w.L <= 2)]
        tt = z.groupby(["L", "mv"]).agg(n=("v", "size"), v=("v", "mean")).reset_index(); tt["zone"] = name; comp.append(tt)
E = pd.concat(rows); g = E.groupby(["mv", "congested"])
print("\n## crossing waits (s from first sample on the section to the change), by state (congested = lane-1 upstream minute mean < 15 m/s)")
print(pd.DataFrame({"n": g.size(), "within_0.5s": g.wait.apply(lambda q: (q <= 0.5).mean()), "wait_p50": g.wait.median(), "wait_p90": g.wait.quantile(.9), "wait_mean": g.wait.mean(), "v_p50": g.v.median()}).round(2).to_string())
T = pd.concat(comp).groupby(["zone", "L", "mv"]).agg(n=("n", "sum"), v=("v", "mean")).reset_index(); T["share_of_lane"] = T.n / T.groupby(["zone", "L"]).n.transform("sum")
print("\n## lane composition by vehicle-steps in congested minutes"); print(T.round(2).to_string(index=False)); print("congested share of minutes 2-19:", cong_share)
for name in ("cf_log", "cf_log_l300"):
    d = pickle.load(open(f"{S}/cmdlog_{name}.pkl", "rb")); L = pd.DataFrame(d["rows"], columns=d["columns"])
    L["run"] = (L.t.diff() < -100).cumsum(); L["seed"] = L.run.map(dict(enumerate([3, 4, 5, 6, 7])))
    A = L.sort_values("v_new").groupby(["seed", "t", "vid"]).first().reset_index(); A["dv"] = (A.v - A.v_new).clip(lower=0)
    bt = {}
    for m in glob.glob(f"{S}/arms_runs/{name}/*/*/meta.json"):
        s = json.load(open(m))["seed"]; df = pd.read_parquet(m.rsplit("/", 1)[0] + "/trajectories.parquet", columns=["t", "x", "lane", "v"])
        up = df[(df.x >= 700) & (df.x < X0) & (df.lane == 0)]; st = up.groupby((up.t // 30).astype(int)).v.mean(); b = st[(st < 15) & (st.index >= 2)]
        bt[s] = int(b.index.min()) * 30 if len(b) else 1200
    A["phase"] = np.where(A.t < A.seed.map(bt), "free", "congested")
    A["role"] = np.where(A.follower, "follower", "changer"); A["ck"] = np.where(A.target_lane == 1, np.where(A.road_changer.isin(["200", "210"]), "entrant on ramp", "entrant in section"), "exiter")
    B = A[A.a_cmd < 0]; gg = B.groupby(["phase", "role", "ck"])
    print(f"\n## runner speed commands that brake ({name}); breakdown t per seed {dict(sorted(bt.items()))}")
    print(pd.DataFrame({"steps": gg.size(), "a<=-1": gg.a_cmd.apply(lambda q: (q <= -1).sum()), "at_full_b": gg.apply(lambda q: (q.a_cmd <= -q.b + 1e-9).sum()), "speed_removed_ms": gg.dv.sum()}).round(0).to_string())
