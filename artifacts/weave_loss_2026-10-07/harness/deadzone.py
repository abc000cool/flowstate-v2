"""The overlap dead zone of the weave's gap choice, measured on trajectories.

A changer still owing its crossing (an exit-bound vehicle in section lane 1,
an entrant in section lane 0) is in the dead zone in a step when some
target-lane vehicle's front lies in (x_c - len_c, x_c]: that vehicle is
neither a leader the gap choice accepts (its front is behind the changer's)
nor a follower (it overlaps the changer), so no gap involving it is a
candidate (runner._weave_choose_gap, priority off).
"""
import glob, json, sys
import numpy as np, pandas as pd
X0, XE, LEN = 829.07, 1134.09, 5.0

def kind(r):
    if r in ("main_off1", "on0_off1"): return "exit"
    if r.startswith("on0"): return "entrant"
    return "through"

def one(d, t_from=0.0):
    df = pd.read_parquet(d + "/trajectories.parquet", columns=["t","veh_id","x","lane","v"])
    v = pd.read_parquet(d + "/vehicles.parquet").set_index("veh_id")
    df["L"] = np.where((df.x >= X0) & (df.x < XE), df.lane, df.lane + 1)
    df["k"] = v.loc[df.veh_id, "route"].map(kind).values
    sec = df[(df.x >= X0) & (df.x < XE) & (df.t >= t_from)]
    ch = sec[((sec.k == "exit") & (sec.L == 1)) | ((sec.k == "entrant") & (sec.L == 0))].copy()
    ch["tgt"] = np.where(ch.k == "exit", 0, 1)
    tl = sec[["t","x","L","veh_id"]].rename(columns={"x":"xo","veh_id":"vo","L":"tgt"})
    m = ch.merge(tl, on=["t","tgt"])
    m = m[(m.vo != m.veh_id) & (m.xo > m.x - LEN) & (m.xo <= m.x)]
    dz = m[["t","veh_id"]].drop_duplicates()
    dz_time = dz.groupby("veh_id").size() * 0.5
    # outcomes per changer
    per = ch.groupby("veh_id").agg(k=("k","first"), t0=("t","min"), t1=("t","max"), x_last=("x","max"), v_min=("v","min"))
    halted = ch[(ch.x > XE - 40) & (ch.v < 0.5)].groupby("veh_id").size() * 0.5
    per["dz_s"] = dz_time.reindex(per.index).fillna(0.0)
    per["halt_end_s"] = halted.reindex(per.index).fillna(0.0)
    per["changer_steps"] = ch.groupby("veh_id").size()
    return per, len(dz), len(ch)

if __name__ == "__main__":
    pd.set_option("display.width", 220)
    for label, root in zip(sys.argv[1::2], sys.argv[2::2]):
        allp = []
        for mfile in sorted(glob.glob(f"{root}/*/*/meta.json")):
            seed = json.load(open(mfile))["seed"]
            per, ndz, nch = one(mfile.rsplit("/",1)[0])
            per["seed"] = seed; allp.append(per)
            print(label, seed, "changer-steps", nch, "dead-zone steps", ndz, f"({ndz/nch:.1%})")
        P = pd.concat(allp)
        P["dz_ge2"] = P.dz_s >= 2.0
        P["halted"] = P.halt_end_s > 0
        print(P.groupby(["k","dz_ge2"]).agg(n=("halted","size"), halted=("halted","sum"), halted_share=("halted","mean"), dz_mean=("dz_s","mean"), halt_s=("halt_end_s","sum")).round(3).to_string())
        print("halted changers with dz>=2s share of all halted:", round(P[P.halted].dz_ge2.mean(),3), " halted-steps share:", round(P[P.dz_ge2].halt_end_s.sum()/P.halt_end_s.sum(),3))
