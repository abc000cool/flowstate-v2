"""Stranding at the section end and abreast crossing pairs, per run."""
import glob, json, sys
import numpy as np, pandas as pd
X0, XE = 829.07, 1134.09

def kind(r):
    if r in ("main_off1", "on0_off1"): return "exit"
    if r.startswith("on0"): return "entrant"
    return "through"

def one(d):
    df = pd.read_parquet(d + "/trajectories.parquet", columns=["t","veh_id","x","lane","v"])
    v = pd.read_parquet(d + "/vehicles.parquet").set_index("veh_id")
    df["L"] = np.where((df.x >= X0) & (df.x < XE), df.lane, df.lane + 1)
    df["k"] = v.loc[df.veh_id, "route"].map(kind).values
    sec = df[(df.x >= X0) & (df.x < XE)]
    # front vehicle of lane 0 each step
    l0 = sec[sec.L == 0]
    front0 = l0.loc[l0.groupby("t").x.idxmax()]
    strand0 = front0[(front0.x > XE - 15) & (front0.v < 0.5) & (front0.k == "entrant")]
    # front vehicle of lane 1 halted near the end and exit-bound
    l1 = sec[sec.L == 1]
    halted1 = l1[(l1.x > XE - 40) & (l1.v < 0.5) & (l1.k == "exit")]
    # exit discharge: exit-bound vehicles leaving the section in lane 0 (last sample on the section)
    last = df.groupby("veh_id").last()
    ex = last[(last.k == "exit") & (last.x > XE - 20) & (last.x < XE) & (last.L == 0)]
    # abreast crossing pairs: entrant in L0 and exit-bound vehicle in L1 within 5 m, both in section
    pairs = 0.0
    a = sec[(sec.L == 0) & (sec.k == "entrant")][["t","x","veh_id"]]
    b = sec[(sec.L == 1) & (sec.k == "exit")][["t","x","veh_id"]]
    m = a.merge(b, on="t", suffixes=("_e","_x"))
    m = m[(m.x_e - m.x_x).abs() < 5.0]
    pair_steps = m.groupby(["veh_id_e","veh_id_x"]).size()
    # strand episodes (contiguous runs of the same stranded entrant)
    eps = strand0.groupby("veh_id").t.agg(["min","max","count"])
    meta = json.load(open(d + "/meta.json"))
    ws = meta["weave_sections"][0]
    return dict(seed=meta["seed"],
                strand_s=len(strand0) * 0.5, strand_s_after120=float((strand0.t >= 120).sum() * 0.5),
                n_strand_eps=len(eps), longest_strand_s=float(eps["count"].max() * 0.5) if len(eps) else 0.0,
                halted_exiter_L1_end_s=float(l1[(l1.x > XE-40)&(l1.v<0.5)&(l1.k=='exit')].t.nunique()*0.5),
                n_abreast_pairs_ge5s=int((pair_steps >= 10).sum()), abreast_pair_steps=int(pair_steps.sum()),
                strand_first_t=float(strand0.t.min()) if len(strand0) else None,
                missed_exit=ws["n_missed_exit"], forced=ws["n_forced"], forced_deferred=ws["n_forced_deferred"],
                pair_releases=ws["n_pair_releases"], eased=ws["n_changer_eased"], coop=ws["n_cooperations"],
                wait_in=ws["wait_in_s_mean"], wait_out=ws["wait_out_s_mean"], unfinished=ws["n_unfinished"])

if __name__ == "__main__":
    pd.set_option("display.width", 250)
    for label, root in zip(sys.argv[1::2], sys.argv[2::2]):
        rows = [one(m.rsplit("/",1)[0]) for m in sorted(glob.glob(f"{root}/*/*/meta.json"))]
        t = pd.DataFrame(rows).sort_values("seed")
        print("==", label); print(t.round(2).to_string(index=False))
