"""Minute-by-minute: lane speeds just upstream of / at the section start, section-end flow."""
import glob, sys
import numpy as np, pandas as pd
X_SEC0, X_END = 829.07, 1134.09
def one(d):
    df = pd.read_parquet(d + "/trajectories.parquet", columns=["t", "veh_id", "x", "lane", "v"])
    sec = (df.x >= X_SEC0) & (df.x < X_END)
    df["L"] = np.where(sec, df.lane, df.lane + 1)
    df["m"] = (df.t // 60).astype(int)
    up = df[(df.x >= 700) & (df.x < X_SEC0)]
    st = df[(df.x >= X_SEC0) & (df.x < X_SEC0 + 60)]
    a = up.groupby(["m", "L"]).v.mean().unstack().add_prefix("up_L")
    b = st.groupby(["m", "L"]).v.mean().unstack().add_prefix("st_L")
    end = df[(df.x >= X_END - 60) & (df.x < X_END)].groupby("veh_id").t.min()
    q = (end // 60).astype(int).value_counts().sort_index() * 60
    return pd.concat([a, b, q.rename("q_end_vph")], axis=1)
if __name__ == "__main__":
    pd.set_option("display.width", 220)
    for d in sys.argv[1:]:
        print("==", d.split("/")[-3:])
        print(one(d).round(1).to_string())
