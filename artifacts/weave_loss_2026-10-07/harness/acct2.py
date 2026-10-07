"""Where the deficit sits at t = 1200 s: stock by place, weave minus ceiling, same seeds."""
import glob, json, sys
import numpy as np, pandas as pd
X0, XE = 829.07, 1134.09
def runs(root):
    return {json.load(open(m))["seed"]: m.rsplit("/", 1)[0] for m in glob.glob(f"{root}/*/*/meta.json")}
def one(d):
    meta = json.load(open(d + "/meta.json"))
    df = pd.read_parquet(d + "/trajectories.parquet", columns=["t","veh_id","x"])
    v = pd.read_parquet(d + "/vehicles.parquet")
    end = df[(df.x >= XE - 60) & (df.x < XE)].groupby("veh_id").t.min()
    last = df[df.t == df.t.max()]
    seen = set(df.veh_id.unique())
    ramp_ins_unseen = int(((v.origin != "mainline") & ~v.veh_id.isin(seen)).sum())
    on = next(r for r in meta["ramps"] if r["name"] == "th52")
    return {"crossed_end_120_1200": int((end >= 120).sum()),
            "mainline_upstream_x<829": int((last.x < X0).sum()),
            "in_section": int(((last.x >= X0) & (last.x < XE)).sum()),
            "on_ramp_(inserted,unseen)": ramp_ins_unseen,
            "ramp_not_inserted": on["n_planned"] - on["n_departed"],
            "mainline_not_inserted": meta["n_vehicles_planned"] - on["n_planned"] - (meta["n_vehicles_departed"] - on["n_departed"])}
W, C = runs(sys.argv[1]), runs(sys.argv[2])
rows = []
for s in sorted(W):
    w, c = one(W[s]), one(C[s])
    rows.append({"seed": s, **{k: w[k] - c[k] for k in w}})
t = pd.DataFrame(rows).set_index("seed")
pd.set_option("display.width", 200)
print("weave minus ceiling, vehicles (same seed, same planned departures)")
print(t.to_string()); print(t.mean().round(1).to_string())
