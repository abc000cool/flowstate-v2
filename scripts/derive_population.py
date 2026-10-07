"""Derived driver populations with one mean shifted by k measured standard deviations.

docs/FRISCO_PROTOCOL.md Amendment 1, item 1: the driver population's mean
maximum acceleration ``a_max`` may be calibrated inside mean ± 1 sd of the
measured source population (``artifacts/idm_i24.json``: 1.055 ± 0.43 m/s²), as
a derived population — mean shifted, covariance unchanged — on the grid
mean + k·sd, k ∈ {0, 0.25, 0.5, 0.75, 1.0}.

The derivation (:func:`derive_shifted`), the convention of
``scripts/calibrate_capacity.py`` (mean T scaled) and
``validation.uncertainty.derived_population`` (mean T / v0 scaled): a copy of
the base population with one mean changed,

    mean[p] = measured.mean[p] + k · sqrt(measured.cov[p, p]),

every other mean, the covariance, ``source``, ``data_hash`` and the episode
statistics kept from the base, and the provenance — both inputs' paths and
sha256, k, sd, the formula and the date — in ``notes``. The base is the
population the corridors run (``artifacts/idm_i24_capacity.json``: the
measured population with its mean T scaled for capacity, used by
``scenarios/i24_replica_flow_speedcal.yaml`` and
``scenarios/mndot_i94_wb_stpaul_weave_slice.yaml``); its mean of the shifted
parameter must equal the measured one, so k = 0 is the base itself and needs
no file. The new mean must lie inside the measured mean ± 1 sd and CLAUDE.md
§3.1's calibration range, or the derivation is refused.

No ``*.calibration.json`` sidecar is written: ``calibration.transfer_check``
and ``validation.uncertainty.source_population`` read every sidecar in
``artifacts/`` as a capacity-calibration record of a T-scaled population, and
this is neither.

Run (from the repository root; writes the four Amendment-1 populations)::

    uv run --no-sync python scripts/derive_population.py
    uv run --no-sync python scripts/derive_population.py --param a_max --k 0.25 0.5 0.75 1.0 \\
        --base artifacts/idm_i24_capacity.json --measured artifacts/idm_i24.json \\
        --out-dir artifacts --stem idm_i24_capacity_amax
"""

from __future__ import annotations

import argparse
import hashlib
import math
from datetime import UTC, datetime
from pathlib import Path

from flowstate_core.artifacts import IDMCalibration
from flowstate_core.constants import IDM_RANGES

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_BASE = "artifacts/idm_i24_capacity.json"
DEFAULT_MEASURED = "artifacts/idm_i24.json"
DEFAULT_PARAM = "a_max"
DEFAULT_K: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0)
DEFAULT_STEM = "idm_i24_capacity_amax"
#: Amendment 1: the shift stays inside the measured mean ± this many sd.
MAX_ABS_K = 1.0


def file_sha256(path: Path) -> str:
    """Hex sha256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    """``path`` relative to the repository when inside it, else as given."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def measured_sd(measured: IDMCalibration, param: str) -> float:
    """``sqrt(cov[p, p])`` of the measured population — the spread of its drivers."""
    i = list(measured.param_names).index(param)
    var = float(measured.cov[i][i])
    if not var > 0.0:
        raise ValueError(f"the measured population's variance of {param} is {var}, not > 0")
    return math.sqrt(var)


def out_name(stem: str, k: float) -> str:
    """``<stem>_k<k>.json``, ``k`` as Python prints a float (0.25, 0.5, 0.75, 1.0)."""
    return f"{stem}_k{float(k)}.json"


def derive_shifted(
    base: IDMCalibration,
    measured: IDMCalibration,
    *,
    param: str,
    k: float,
    base_label: str,
    measured_label: str,
    created_at: str,
) -> IDMCalibration:
    """``base`` with ``mean[param] = measured.mean[param] + k · sd`` (module docstring).

    Args:
        base: The population the corridors run.
        measured: The measured source population (the sd and the origin).
        param: One of the IDM parameters (``a_max``).
        k: Shift in measured standard deviations, ``|k| <= 1``.
        base_label: The base's path and sha256, for the notes.
        measured_label: The measured population's path and sha256.
        created_at: ISO-8601 timestamp of the derivation.

    Returns:
        The derived :class:`IDMCalibration`.

    Raises:
        ValueError: An unknown parameter, ``|k| > 1``, a base whose mean of
            ``param`` differs from the measured one, a base whose covariance
            differs from the measured one, or a new mean outside CLAUDE.md
            §3.1's calibration range.
    """
    names = list(base.param_names)
    if param not in names or list(measured.param_names) != names:
        raise ValueError(f"unknown parameter {param!r} or different parameter orders")
    if not -MAX_ABS_K <= k <= MAX_ABS_K:
        raise ValueError(f"k = {k} lies outside the measured mean ± {MAX_ABS_K:g} sd")
    m0 = float(measured.mean[param])
    if not math.isclose(float(base.mean[param]), m0, rel_tol=1e-12, abs_tol=0.0):
        raise ValueError(
            f"the base's mean {param} {base.mean[param]} differs from the measured "
            f"{m0}: k = 0 would not be the base"
        )
    if base.cov != measured.cov:
        raise ValueError("the base's covariance differs from the measured population's")
    sd = measured_sd(measured, param)
    new = m0 + k * sd
    lo, hi = IDM_RANGES[param]
    if not lo <= new <= hi:
        raise ValueError(
            f"mean {param} {new:.4f} lies outside CLAUDE.md §3.1's calibration range {lo}-{hi}"
        )
    mean = dict(base.mean)
    mean[param] = new
    note = (
        f"Derived from {base_label} by scripts/derive_population.py "
        f"(docs/FRISCO_PROTOCOL.md Amendment 1): population mean {param} = measured mean + k x sd "
        f"= {m0:.6f} + {k:g} x {sd:.6f} = {new:.6f}, sd = sqrt(cov[{param},{param}]) of the "
        f"measured source population {measured_label}; covariance and every other mean "
        f"unchanged (the base's). Derived {created_at}. A candidate of the Amendment-1 grid, "
        f"not a calibrated population until docs/DISCHARGE_CALIBRATION.md section 3 records "
        f"the choice. Base notes: {base.notes}"
    )
    return base.model_copy(update={"mean": mean, "notes": note, "created_at": created_at})


def main(argv: list[str] | None = None) -> int:
    """Write the derived populations; returns the exit status."""
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--base", type=Path, default=REPO_ROOT / DEFAULT_BASE)
    ap.add_argument("--measured", type=Path, default=REPO_ROOT / DEFAULT_MEASURED)
    ap.add_argument("--param", default=DEFAULT_PARAM)
    ap.add_argument("--k", type=float, nargs="+", default=list(DEFAULT_K))
    ap.add_argument("--out-dir", type=Path, default=REPO_ROOT / "artifacts")
    ap.add_argument("--stem", default=DEFAULT_STEM)
    ap.add_argument(
        "--created-at",
        default=None,
        help="ISO-8601 timestamp recorded in the artifacts (default: now, UTC)",
    )
    args = ap.parse_args(argv)
    created = args.created_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = IDMCalibration.load(args.base)
    measured = IDMCalibration.load(args.measured)
    base_label = f"{rel(args.base)} (sha256 {file_sha256(args.base)})"
    measured_label = f"{rel(args.measured)} (sha256 {file_sha256(args.measured)})"
    sd = measured_sd(measured, args.param)
    for k in args.k:
        if k == 0.0:
            print(f"k = 0 is the base itself ({rel(args.base)}); nothing written")
            continue
        derived = derive_shifted(
            base,
            measured,
            param=args.param,
            k=k,
            base_label=base_label,
            measured_label=measured_label,
            created_at=created,
        )
        target = args.out_dir / out_name(args.stem, k)
        derived.save(target)
        print(
            f"k = {k:g}: mean {args.param} {derived.mean[args.param]:.6f} "
            f"(measured {measured.mean[args.param]:.6f} + {k:g} x {sd:.6f}) -> {rel(target)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
