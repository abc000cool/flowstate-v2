"""Space-time speed by lane (section numbering: 0 = auxiliary, 1..3 through, right to left)."""
import glob, json, sys
import numpy as np, pandas as pd
X_SEC0, X_END = 829.07, 1134.09

def load(d):
    df = pd.read_parquet(d + "/trajectories.parquet", columns=["t", "veh_id", "x", "lane", "v", "a"])
    sec = (df.x >= X_SEC0) & (df.x < X_END)
    df["L"] = np.where(sec, df.lane, df.lane + 1)
    return df

def table(roots, t0=300, t1=1200, bins=None):
    bins = bins or [0, 300, 500, 600, 700, 750, 800, 829.07, 860, 900, 950, 1000, 1050, 1100, 1134.09, 1200, 1355, 1600, 1880]
    out = {}
    for label, root in roots:
        acc = []
        for m in sorted(glob.glob(f"{root}/*/*/meta.json")):
            d = m.rsplit("/", 1)[0]
            df = load(d)
            df = df[(df.t >= t0) & (df.t < t1)]
            df["xb"] = pd.cut(df.x, bins)
            acc.append(df.groupby(["xb", "L"], observed=True).v.mean().unstack())
        out[label] = sum(acc) / len(acc)
    return out

if __name__ == "__main__":
    roots = list(zip(sys.argv[1::2], sys.argv[2::2]))
    pd.set_option("display.width", 200)
    for label, tab in table(roots).items():
        print("==", label, "mean speed [m/s], t 300-1200 s, seeds pooled (mean of seed means)")
        print(tab.round(1).to_string())
