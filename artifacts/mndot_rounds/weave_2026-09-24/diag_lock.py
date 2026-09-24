import sys, json, glob, numpy as np, pyarrow.parquet as pq
run = sys.argv[1]
pf = pq.ParquetFile(f"{run}/trajectories.parquet"); print("schema", pf.schema_arrow.names, "rows", pf.metadata.num_rows, flush=True)
cols = [c for c in ("t","x","v","lane","edge") if c in pf.schema_arrow.names]
DX, DT = 100.0, 300.0
sums = {}; cnt = {}
for i in range(pf.num_row_groups):
    tb = pf.read_row_group(i, columns=["t","x","v"]); t = tb["t"].to_numpy(); x = tb["x"].to_numpy(); v = tb["v"].to_numpy()
    xb = (x // DX).astype(int); tbn = (t // DT).astype(int)
    key = tbn * 100000 + xb
    u, inv = np.unique(key, return_inverse=True)
    s = np.bincount(inv, weights=v); c = np.bincount(inv)
    for k, sv, cv in zip(u, s, c):
        sums[k] = sums.get(k, 0.0) + sv; cnt[k] = cnt.get(k, 0) + cv
keys = sorted(sums)
grid = {}
for k in keys:
    if cnt[k] >= 20: grid.setdefault(k // 100000, {})[k % 100000] = sums[k] / cnt[k]
print("first standstill bins (mean v < 2 m/s, >=20 samples) per 5-min window:", flush=True)
for tb in sorted(grid):
    stopped = sorted(xb for xb, mv in grid[tb].items() if mv < 2.0)
    if stopped:
        print(f"  t={tb*DT/60:5.0f} min: x bins stopped = {stopped[0]*DX/1000:.1f}-{stopped[-1]*DX/1000:.1f} km ({len(stopped)} bins); min-speed bin at {min(grid[tb], key=grid[tb].get)*DX/1000:.1f} km", flush=True)
