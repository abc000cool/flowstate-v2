"""Cross-section flows by lane and movement (veh/h over t 300-1200 s)."""
import glob, json, sys
import numpy as np, pandas as pd
X_SEC0, X_END = 829.07, 1134.09

def mv(route):
    if route.startswith("on0"):
        return "ent_exit" if route == "on0_off1" else "entrant"
    return "exiter" if route == "main_off1" else "through"

def crossings(d, xs, t0=300, t1=1200):
    df = pd.read_parquet(d + "/trajectories.parquet", columns=["t", "veh_id", "x", "lane", "v"])
    v = pd.read_parquet(d + "/vehicles.parquet").set_index("veh_id")
    df = df.sort_values(["veh_id", "t"])
    rows = []
    for x in xs:
        xmin = df.groupby("veh_id").x.min()
        a = df[df.x >= x].groupby("veh_id").first()
        a = a[xmin.loc[a.index] < x]  # crossed x from upstream (ramp vehicles appear at the gore)
        a = a[(a.t >= t0) & (a.t < t1)]
        sec = (x >= X_SEC0) & (x < X_END)
        a["L"] = a.lane if sec else a.lane + 1
        a["mv"] = v.loc[a.index, "route"].map(mv)
        for (L, m), n in a.groupby(["L", "mv"]).size().items():
            rows.append(dict(x=x, L=int(L), mv=m, vph=n * 3600 / (t1 - t0), v=float(a[(a.L == L) & (a.mv == m)].v.mean())))
    return pd.DataFrame(rows)

if __name__ == "__main__":
    xs = [500.0, 800.0, 840.0, 900.0, 1000.0, 1120.0]
    pd.set_option("display.width", 220)
    for label, root in zip(sys.argv[1::2], sys.argv[2::2]):
        acc = []
        for m in sorted(glob.glob(f"{root}/*/*/meta.json")):
            acc.append(crossings(m.rsplit("/", 1)[0], xs))
        allr = pd.concat(acc)
        n = len(acc)
        tab = allr.groupby(["x", "L", "mv"]).vph.sum().div(n).unstack(fill_value=0).round(0)
        tab["total"] = tab.sum(axis=1)
        print("==", label, "flow veh/h by cross-section x, section lane L, movement (mean of", n, "seeds)")
        print(tab.to_string())
        print(allr.groupby("x").vph.sum().div(n).round(0).to_string())
