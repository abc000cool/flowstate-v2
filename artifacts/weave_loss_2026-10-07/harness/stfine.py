import sys, numpy as np, pandas as pd
X0, XE = 829.07, 1134.09
d, L, t0, t1 = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
df = pd.read_parquet(d + "/trajectories.parquet", columns=["t","veh_id","x","lane","v"])
df["L"] = np.where((df.x >= X0) & (df.x < XE), df.lane, df.lane + 1)
w = df[(df.L == L) & (df.t >= t0) & (df.t < t1) & (df.x >= 600) & (df.x < 1200)]
xb = pd.cut(w.x, [600,650,700,750,800,829.07,855,880,905,930,955,980,1005,1030,1055,1080,1105,1134.09,1200])
tab = w.groupby([(w.t // 10 * 10).astype(int), xb], observed=False).v.mean().unstack()
tab.columns = [f"{int(c.left)}" for c in tab.columns]
pd.set_option("display.width", 250)
print(f"lane {L} mean speed, rows = 10 s, cols = x bin start"); print(tab.round(0).to_string())
