"""ROADMAP §1.1 — stream the I-24 MOTION INCEPTION export into Parquet.

The 30 Nov 2022 morning run (``data/i24motion/6386d89efb3ff533c12df167__post10.zip``,
5.8 GB zip → one 19.5 GB JSON array of 816,694 trajectory-fragment documents)
is never extracted to disk: ``calibration.loaders.i24motion.convert_i24_to_parquet``
decompresses from the zip, decodes one document at a time, keeps one
carriageway, decimates 25 Hz → 5 Hz after computing speed at the native rate,
and writes row-grouped Parquet plus a per-fragment table and ``meta.json``.

Outputs (gitignored, under ``data/i24motion/processed/<name>/``):

* ``trajectories.parquet`` — ``t, veh_id, x, y, lane, v, length, cls``
  (SI; ``x`` = front bumper, travel oriented, 0 at MM 62.7 for westbound;
  ``t`` = seconds since the origin recorded in ``meta.json``)
* ``vehicles.parquet`` — one row per fragment
* ``meta.json`` — provenance (sha256 of the zip), parameters, counts, the
  fragment-duration histogram

Run (repo root)::

    uv run --no-sync python scripts/i24_extract.py                 # westbound, 5 Hz
    uv run --no-sync python scripts/i24_extract.py --direction 1   # eastbound
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from calibration.loaders.i24motion import I24_MM_RANGE, convert_i24_to_parquet

REPO_ROOT = Path(__file__).resolve().parents[1]
I24_DIR = REPO_ROOT / "data" / "i24motion"
DEFAULT_SOURCE = I24_DIR / "6386d89efb3ff533c12df167__post10.zip"

#: ``t = 0`` for the INCEPTION run: 2022-11-30 06:00:00 CST (UTC-6). The first
#: document starts at 05:59:59.9, so an hour-floored auto origin would land on
#: 05:00; pinning it keeps ``t`` = seconds after 06:00 in every artifact.
INCEPTION_T0_UNIX = 1669809600.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--direction", type=int, choices=(-1, 1), default=-1)
    ap.add_argument("--downsample", type=int, default=5, help="keep every k-th 25 Hz slot")
    ap.add_argument(
        "--name", default=None, help="output folder name under data/i24motion/processed"
    )
    ap.add_argument(
        "--t-origin",
        type=float,
        default=INCEPTION_T0_UNIX,
        help="Unix time mapped to t = 0 (default 2022-11-30 06:00:00 CST)",
    )
    ap.add_argument(
        "--t-range",
        type=float,
        nargs=2,
        default=None,
        metavar=("T_LO", "T_HI"),
        help="seconds relative to --t-origin (default: whole run)",
    )
    args = ap.parse_args()

    name = args.name or f"i24_{'wb' if args.direction < 0 else 'eb'}_20221130"
    out_dir = I24_DIR / "processed" / name
    t0 = time.perf_counter()

    def progress(n_read: int, n_kept: int) -> None:
        if n_read % 100_000 == 0:
            print(
                f"  {n_read:>8d} read, {n_kept:>8d} kept ({time.perf_counter() - t0:.0f} s)",
                flush=True,
            )

    print(
        f"streaming {args.source.name} -> {out_dir} (direction {args.direction}, /{args.downsample})"
    )
    summary = convert_i24_to_parquet(
        args.source,
        out_dir,
        direction=args.direction,
        t_origin_unix=args.t_origin,
        downsample=args.downsample,
        mm_range=I24_MM_RANGE,
        t_range_s=tuple(args.t_range) if args.t_range else None,
        progress=progress,
    )
    wall = time.perf_counter() - t0
    print(f"done in {wall:.0f} s")
    print(
        f"  documents: {summary.n_docs_read} read, {summary.n_docs_direction} in direction, "
        f"{summary.n_docs_kept} kept; {summary.n_docs_ge_30s} last >= 30 s"
    )
    print(f"  rows written: {summary.n_rows} (from {summary.n_samples_native} native samples)")
    print(
        f"  t in [{summary.t_min_s:.1f}, {summary.t_max_s:.1f}] s from origin {summary.t_origin_unix:.0f}"
    )
    print(f"  x in [{summary.x_min_m:.1f}, {summary.x_max_m:.1f}] m")
    print(f"  classes: {json.dumps(summary.class_counts)}")
    print(f"  duration hist {summary.duration_hist_edges_s}: {summary.duration_hist}")
    sizes = {p.name: p.stat().st_size / 1e6 for p in out_dir.iterdir()}
    print("  files [MB]: " + ", ".join(f"{k} {v:.1f}" for k, v in sorted(sizes.items())))


if __name__ == "__main__":
    main()
