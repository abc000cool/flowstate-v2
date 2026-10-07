"""Hard-braking steps per replicate of one I-24 battery (amendment B1, criterion A1; VM only).

usage (repository root, on the pipeline VM, BEFORE the battery's trajectories are pruned):
  hard_braking.py --artifact artifacts/i24_validation_<label>.json --out runs/i24_validation/<label>/hard_braking.json

A1 of docs/I24_DISCHARGE_DIAGNOSIS.md §8.3 compares the vehicle-steps below −8.9 m/s² of the B1 arm with
its reference. Only the trajectories carry them: meta.json counts collisions, not
decelerations; vehicles.parquet holds first and last samples; the battery artifact holds flows, speeds and
waves. The trajectories sample every step (``sim.output_hz`` 2 at the 0.5-s step: ``out_every`` = 1) with
SUMO's acceleration of that step in ``a``, on the corridor's edges (the ramps' own edges are not
recorded; the same for both arms). This reducer reads only the columns ``t``, ``x`` and ``a`` of each
replicate's ``trajectories.parquet`` in record batches (never the whole table in memory) and writes the
counts below −4.5, −7 and −8.9 m/s², over the whole run and over the study window (sim t ≥ 600 s), with
the time and position of every step below −8.9 m/s². It runs on the VM only (the laptop never reads
trajectories); ``corridor_b1.py evaluate`` reads its output, and the archive carries it.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[3]
THRESHOLDS = (-4.5, -7.0, -8.9)
EMERGENCY = -8.9
STUDY_T0_S = 600.0


def resolve(run_dir: str) -> Path:
    """A battery's run_dir (absolute on the VM that wrote it) under this checkout."""
    p = Path(run_dir)
    if p.is_dir():
        return p
    parts = p.parts
    if "runs" in parts:
        return REPO.joinpath(*parts[parts.index("runs") :])
    return p


def count(traj: Path) -> dict:
    """Counts of one replicate's trajectory file, streamed in record batches."""
    # an open handle, as validation.vehicles reads: a path makes pyarrow build a
    # LocalFileSystem, which re-registers the 'file' scheme and fails inside the
    # full test session (ArrowKeyError, gate of 2026-10-07)
    with traj.open("rb") as handle:
        pf = pq.ParquetFile(handle)
        n_rows = 0
        whole = dict.fromkeys(THRESHOLDS, 0)
        study = dict.fromkeys(THRESHOLDS, 0)
        events: list[list[float]] = []
        for batch in pf.iter_batches(columns=["t", "x", "a"], batch_size=1 << 20):
            t = batch.column("t").to_numpy(zero_copy_only=False)
            x = batch.column("x").to_numpy(zero_copy_only=False)
            a = batch.column("a").to_numpy(zero_copy_only=False)
            n_rows += len(a)
            in_study = t >= STUDY_T0_S
            for thr in THRESHOLDS:
                below = a < thr
                whole[thr] += int(below.sum())
                study[thr] += int((below & in_study).sum())
            hard = np.flatnonzero(a < EMERGENCY)
            events += [[float(t[i]), float(x[i]), float(a[i])] for i in hard]
    return {
        "n_vehicle_steps": n_rows,
        "below_ms2": {str(k): v for k, v in whole.items()},
        "below_ms2_study_window": {str(k): v for k, v in study.items()},
        "emergency_steps_t_x_a": events,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--artifact", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    art = json.loads(args.artifact.read_text())
    sim = art["simulated"]
    per_seed = {}
    for seed, rd in zip(sim["seeds"], sim["run_dirs"], strict=True):
        run_dir = resolve(rd)
        traj = run_dir / "trajectories.parquet"
        per_seed[str(seed)] = count(traj) if traj.is_file() else {"missing": str(traj)}
        meta = (
            json.loads((run_dir / "meta.json").read_text())
            if (run_dir / "meta.json").is_file()
            else {}
        )
        step = (meta.get("config") or {}).get("sim", {}).get("step_length_s")
        hz = meta.get("output_hz_realized")
        # every step is sampled only when the realized rate is 1 / step
        per_seed[str(seed)]["every_step_sampled"] = (
            None if step is None or hz is None else abs(float(hz) * float(step) - 1.0) < 1e-9
        )
        print(f"{seed}: {per_seed[str(seed)].get('below_ms2', 'missing')}", flush=True)
    doc = {
        "schema": "flowstate.b1_hard_braking/1",
        "artifact": str(args.artifact),
        "label": art.get("arm"),
        "config_hash": art.get("config_hash"),
        "definition": "vehicle-steps of trajectories.parquet (every 0.5-s step, corridor edges) with a below "
        "each threshold [m/s^2]; study window = sim t >= 600 s",
        "per_seed": per_seed,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1) + "\n")


if __name__ == "__main__":
    main()
