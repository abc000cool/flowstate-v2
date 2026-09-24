import sys, numpy as np, pyarrow.parquet as pq
run = sys.argv[1]; pf = pq.ParquetFile(f"{run}/trajectories.parquet")
T0, T1, DX, DT = 0.0, 1500.0, 50.0, 60.0
acc = {}
for i in range(pf.num_row_groups):
    tb = pf.read_row_group(i, columns=["t","x","v","lane"]); t = tb["t"].to_numpy(); m = (t >= T0) & (t < T1)
    if not m.any(): continue
    x = tb["x"].to_numpy()[m]; v = tb["v"].to_numpy()[m]; ln = tb["lane"].to_numpy()[m]; t = t[m]
    sel = (x > 9000) & (x < 11500)
    key = ((t[sel] // DT).astype(int) * 1000 + (x[sel] // DX).astype(int)) * 10 + ln[sel].astype(int)
    u, inv = np.unique(key, return_inverse=True); s = np.bincount(inv, weights=v[sel]); c = np.bincount(inv)
    for k, sv, cv in zip(u, s, c): a = acc.setdefault(int(k), [0.0, 0]); a[0] += sv; a[1] += cv
rows = sorted((k // 10000, (k // 10) % 1000, k % 10, a[0]/a[1], a[1]) for k, a in acc.items() if a[1] >= 10)
first = None
for tb, xb, ln, mv, n in rows:
    if mv < 2.0:
        print(f"t={tb:3d} min  x={xb*DX/1000:.2f} km lane {ln}: mean v {mv:.1f} m/s (n={n})")
        first = first or tb
        if tb > first + 3: break
