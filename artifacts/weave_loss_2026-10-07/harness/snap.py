"""snap.py RUN_DIR T [x0 x1]: every vehicle on lanes 0-3 around the section at time T."""
import sys
import numpy as np, pandas as pd
X0, XE = 829.07, 1134.09
d, T = sys.argv[1], float(sys.argv[2])
x0 = float(sys.argv[3]) if len(sys.argv) > 3 else 780; x1 = float(sys.argv[4]) if len(sys.argv) > 4 else 1180
df = pd.read_parquet(d + "/trajectories.parquet", columns=["t","veh_id","x","lane","v","a"])
v = pd.read_parquet(d + "/vehicles.parquet").set_index("veh_id")
df = df[df.t == T]
df["L"] = np.where((df.x >= X0) & (df.x < XE), df.lane, df.lane + 1)
df["r"] = v.loc[df.veh_id, "route"].values
df = df[(df.x >= x0) & (df.x < x1)].sort_values("x", ascending=False)
for L in (0, 1, 2, 3):
    g = df[df.L == L]
    print(f"L{L}: " + "  ".join(f"{r.veh_id[1:]}:{r.r.replace('main_off1','EX').replace('on0_off1','RR').replace('on0_off2','EN2').replace('on0','EN').replace('main_off2','TJ').replace('main','T')}@{r.x:.0f}/{r.v:.1f}" for r in g.itertuples()))
