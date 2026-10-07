"""Batch R: the original OR fixture (BLOCK, seeds 1-20) with k = 0 / 0.5 / 1 populations. 2 processes.
Usage: uv run --no-sync python batch_r.py OUT.jsonl"""

from __future__ import annotations

import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "orig"))


def _job(p):
    import fixture_or as FO

    r = FO.run_or(p["fleet"], p["seed"], "BLOCK", tag=p["fleet"])
    r.pop("det", None)
    return r


def main():
    out = Path(sys.argv[1])
    jobs = [{"fleet": f, "seed": s} for f in ("i24", "i24k05", "i24dc") for s in range(1, 21)]
    t0 = time.time()
    with mp.get_context("spawn").Pool(2) as pool, out.open("w") as fh:
        for i, r in enumerate(pool.imap_unordered(_job, jobs), 1):
            fh.write(json.dumps(r, default=str) + "\n")
            fh.flush()
            if i % 10 == 0:
                print(f"{i}/{len(jobs)} {time.time() - t0:.0f}s", flush=True)
    print(f"done {len(jobs)} in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
