"""Manual calibration CLI (CLAUDE.md §6).

Small argparse front end for running calibrations by hand:

    python -m calibration.cli fd  --pems-csv data/pems_d7.csv --out artifacts/fd.json
    python -m calibration.cli idm --ngsim-csv data/ngsim_i80.csv --out artifacts/idm.json

Both subcommands write versioned artifacts (``flowstate_core.artifacts``)
with full provenance; the artifact timestamp is taken from the wall clock at
invocation (the CLI *is* the caller that supplies ``created_at``).
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from calibration.fd_fit import fit_triangular_fd
from calibration.idm_fit import fit_population
from calibration.loaders.ngsim import load_ngsim_episodes
from calibration.loaders.pems import load_pems_station_csv


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m calibration.cli",
        description="FlowState calibration runs (FD from PeMS, IDM from NGSIM).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fd = sub.add_parser("fd", help="Fit a triangular fundamental diagram from a PeMS CSV.")
    fd.add_argument("--pems-csv", required=True, help="PeMS 5-min station CSV path.")
    fd.add_argument("--out", required=True, help="Output FDCalibration JSON path.")
    fd.add_argument("--source", default="", help="Provenance note (station/date range).")
    fd.add_argument("--seed", type=int, default=0, help="Bootstrap RNG seed.")
    fd.add_argument("--g-length", type=float, default=7.0, help="Effective vehicle length g [m].")
    fd.add_argument("--n-bootstrap", type=int, default=200, help="Bootstrap resamples for the CIs.")

    idm = sub.add_parser("idm", help="Fit the IDM population from an NGSIM trajectory CSV.")
    idm.add_argument("--ngsim-csv", required=True, help="NGSIM trajectory CSV path.")
    idm.add_argument("--out", required=True, help="Output IDMCalibration JSON path.")
    idm.add_argument("--source", default="", help="Provenance note (dataset/period).")
    idm.add_argument("--seed", type=int, default=0, help="Master RNG seed.")
    idm.add_argument("--downsample", type=int, default=5, help="Keep every k-th 10 Hz frame.")
    idm.add_argument(
        "--min-duration-s", type=float, default=30.0, help="Minimum episode duration [s]."
    )
    idm.add_argument("--max-episodes", type=int, default=0, help="Cap on episodes used (0 = all).")
    idm.add_argument("--de-maxiter", type=int, default=60, help="DE generations per episode.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = _build_parser().parse_args(argv)
    created_at = datetime.now(UTC).isoformat()

    if args.command == "fd":
        df = load_pems_station_csv(args.pems_csv, g_effective_length_m=args.g_length)
        artifact = fit_triangular_fd(
            df,
            created_at=created_at,
            source=args.source or f"PeMS CSV {args.pems_csv}",
            seed=args.seed,
            n_bootstrap=args.n_bootstrap,
        )
        artifact.save(args.out)
        fd = artifact.fd
        print(
            f"FD fit: v_f={fd.v_f:.2f} m/s, w={fd.w:.2f} m/s, "
            f"rho_jam={fd.rho_jam:.4f} veh/m (n={artifact.n_observations}, "
            f"R2_free={artifact.r2_freeflow:.3f}) -> {args.out}"
        )
        return 0

    if args.command == "idm":
        episodes = load_ngsim_episodes(
            args.ngsim_csv, downsample=args.downsample, min_duration_s=args.min_duration_s
        )
        if args.max_episodes > 0:
            episodes = episodes[: args.max_episodes]
        if len(episodes) < 2:
            print(f"error: only {len(episodes)} usable episodes", file=sys.stderr)
            return 2
        artifact = fit_population(
            episodes,
            seed=args.seed,
            created_at=created_at,
            source=args.source or f"NGSIM CSV {args.ngsim_csv}",
            de_maxiter=args.de_maxiter,
        )
        artifact.save(args.out)
        print(
            f"IDM fit: {len(episodes)} episodes, mean="
            + ", ".join(f"{k}={v:.3f}" for k, v in artifact.mean.items())
            + f", holdout gap RMSE={artifact.holdout_gap_rmse_m:.2f} m -> {args.out}"
        )
        return 0

    return 2  # pragma: no cover — argparse enforces the choices


if __name__ == "__main__":
    raise SystemExit(main())
