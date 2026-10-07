"""Lane-change events in and around the T.H.52 section, with gaps at the change."""
import glob, json, sys
import numpy as np, pandas as pd
X_SEC0, X_END = 829.07, 1134.09
LEN = 5.0

def mv(route):
    if route.startswith("on0"):
        return "ent_exit" if route == "on0_off1" else "entrant"
    return "exiter" if route == "main_off1" else ("through" if route == "main" else "through_j")

def load(d):
    df = pd.read_parquet(d + "/trajectories.parquet", columns=["t", "veh_id", "x", "lane", "v", "a"])
    sec = (df.x >= X_SEC0) & (df.x < X_END)
    df["L"] = np.where(sec, df.lane, df.lane + 1)
    v = pd.read_parquet(d + "/vehicles.parquet").set_index("veh_id")
    df["mv"] = v.loc[df.veh_id, "route"].map(mv).values
    return df.sort_values(["veh_id", "t"]).reset_index(drop=True), v

def events(df):
    prev_L = df.groupby("veh_id").L.shift()
    prev_x = df.groupby("veh_id").x.shift()
    ch = df[(prev_L.notna()) & (df.L != prev_L)].copy()
    ch["from_L"] = prev_L[ch.index].astype(int)
    ch["dir"] = np.sign(ch.L - ch.from_L).astype(int)
    first = df.groupby("veh_id").first()
    # arrival-step changes of ramp vehicles: first seen on the section above lane 0
    arr = first[(first.mv.isin(["entrant"])) & (first.L >= 1) & (first.x < X_SEC0 + 30)].copy()
    arr["from_L"] = 0; arr["dir"] = 1; arr["arrival"] = True
    arr = arr.reset_index()
    ch["arrival"] = False
    # exiter arrival step: first sample on 102 already in lane 0 (seen before at x < 829 in lane >= 1)
    ev = pd.concat([ch, arr[ch.columns.intersection(arr.columns)]], ignore_index=True)
    # exiter changes into lane 0 within one step of reaching the section are 'arrival' too
    ev.loc[(ev.mv == "exiter") & (ev.L == 0) & (ev.x < X_SEC0 + 15), "arrival"] = True
    return ev

def gaps(df, ev):
    by_t = {t: g for t, g in df.groupby("t")}
    lead, lag, vl, vf = [], [], [], []
    for _, e in ev.iterrows():
        g = by_t[e.t]
        g = g[(g.L == e.L) & (g.veh_id != e.veh_id)]
        ahead = g[g.x > e.x]; behind = g[g.x < e.x]
        if len(ahead):
            r = ahead.loc[ahead.x.idxmin()]; lead.append(r.x - LEN - e.x); vl.append(r.v)
        else:
            lead.append(np.inf); vl.append(np.nan)
        if len(behind):
            r = behind.loc[behind.x.idxmax()]; lag.append(e.x - LEN - r.x); vf.append(r.v)
        else:
            lag.append(np.inf); vf.append(np.nan)
    ev = ev.copy()
    ev["g_lead"], ev["g_lag"], ev["v_lead"], ev["v_fol"] = lead, lag, vl, vf
    ev["tg_lead"] = ev.g_lead / ev.v.clip(lower=0.1)
    ev["tg_lag"] = ev.g_lag / ev.v_fol.clip(lower=0.1)
    return ev

def run(d):
    df, v = load(d)
    ev = events(df)
    ev = ev[(ev.x >= X_SEC0 - 600) & (ev.x < X_END + 50)]
    return gaps(df, ev)

if __name__ == "__main__":
    out = sys.argv[1]
    acc = []
    for label, root in zip(sys.argv[2::2], sys.argv[3::2]):
        for m in sorted(glob.glob(f"{root}/*/*/meta.json")):
            meta = json.load(open(m))
            e = run(m.rsplit("/", 1)[0]); e["seed"] = meta["seed"]; e["arm"] = label
            acc.append(e)
    pd.concat(acc).to_parquet(out)
    print("wrote", out)
