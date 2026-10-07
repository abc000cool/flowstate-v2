"""trace.py RUN_DIR VEH T0 T1: a vehicle's state, its same-lane leader and both neighbours."""
import sys
import numpy as np, pandas as pd, re
X0, XE = 829.07, 1134.09
d, vid, t0, t1 = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
df = pd.read_parquet(d + "/trajectories.parquet", columns=["t","veh_id","x","lane","v","a"])
v = pd.read_parquet(d + "/vehicles.parquet").set_index("veh_id")
df["L"] = np.where((df.x >= X0) & (df.x < XE), df.lane, df.lane + 1)
df["r"] = v.loc[df.veh_id, "route"].values
rou = open(d + "/net/demand.rou.xml").read()
def vt(veh):
    m = re.search(r'<vehicle id="%s" type="([^"]+)"' % veh, rou)
    if not m: return {}
    t = re.search(r'<vType id="%s" ([^>]*)/>' % m.group(1), rou).group(1)
    return dict(re.findall(r'(\w+)="([^"]*)"', t))
p = vt(vid); print(vid, v.loc[vid, "route"], {k: p.get(k) for k in ("accel","decel","tau","minGap","maxSpeed","lcStrategic")})
by_t = dict(tuple(df[(df.t >= t0) & (df.t <= t1)].groupby("t")))
rows = []
for t, g in sorted(by_t.items()):
    me = g[g.veh_id == vid]
    if me.empty: continue
    me = me.iloc[0]
    row = dict(t=t, x=round(me.x,1), L=int(me.L), v=round(me.v,2), a=round(me.a,2))
    for side, dL in (("same",0),("R",-1),("Lft",1)):
        o = g[(g.L == me.L + dL) & (g.veh_id != vid)]
        ah = o[o.x > me.x]; bh = o[o.x <= me.x]
        if len(ah):
            l = ah.loc[ah.x.idxmin()]; row[side+"_lead"] = f"{l.veh_id}({l.r[:6]}) g={l.x-5-me.x:.1f} v={l.v:.1f} a={l.a:.1f}"
        if side != "same" and len(bh):
            f = bh.loc[bh.x.idxmax()]; row[side+"_fol"] = f"{f.veh_id}({f.r[:6]}) g={me.x-5-f.x:.1f} v={f.v:.1f}"
    rows.append(row)
pd.set_option("display.width", 300); pd.set_option("display.max_colwidth", 60); pd.set_option("display.max_rows", 500)
print(pd.DataFrame(rows).to_string(index=False))
