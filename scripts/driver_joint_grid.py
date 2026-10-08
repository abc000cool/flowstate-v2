"""The Amendment-7 joint driver grid: mean ``a_max`` × mean ``T``, 25 derived populations.

docs/PRE_FRISCO_PROGRAM.md B6 (proposed docs/FRISCO_PROTOCOL.md Amendment 7,
approved by the coordinator on 2026-10-07, 21:16 CDT) moves two means of one
driver population together, on the grid

    mean a_max = measured mean + k · sd(a_max),   k ∈ {0, 0.25, 0.5, 0.75, 1}
    mean T     = 1.322 s + j · 0.25 · sd(T),      j ∈ {−2, −1, 0, 1, 2}

with ``sd(p) = sqrt(cov[p, p])`` of the measured source population
(``artifacts/idm_i24.json``) and 1.322 s the mean T of the population the
corridors run (``artifacts/idm_i24_capacity.json``: the measured population with
its mean T scaled for capacity). The T step is the k grid's step, 0.25 sd,
computed here from the covariance and recorded (0.130302 s; the plan quotes
0.130).

The derivation (:func:`derive_joint`) is ``scripts/derive_population.py``'s with
two means changed instead of one: a copy of the base with

    mean[a_max] = measured.mean[a_max] + k · sd(a_max)
    mean[T]     = base.mean[T]         + j · 0.25 · sd(T)

every other mean, the covariance, ``source``, ``data_hash`` and the episode
statistics kept from the base, and the provenance — both inputs' paths and
sha256, (k, j), both steps, the formulas and the date — in ``notes``. Both new
means must lie inside the measured mean ± 1 sd (protocol §7.2's measured range)
and CLAUDE.md §3.1's calibration range, or the derivation is refused.

Which file each pair runs (:func:`population_path`):

* (0, 0) is the base itself, ``artifacts/idm_i24_capacity.json``;
* (k, 0), k > 0, is Amendment 1's population ``artifacts/idm_i24_capacity_amax_k<k>.json``
  (``scripts/derive_population.py``), checked here to equal the derivation — so
  (1, 0) on the I-24 arm is that arm itself, same config hash;
* every other pair is written here as
  ``artifacts/idm_i24_capacity_joint_k<k>_j<±j>.json`` (20 files). The stem is
  not ``idm_i24_capacity_amax_k*``: that glob is Amendment 1's one-shift family
  (tests/test_validation/test_validation_uncertainty.py).

Lineage. The 25 pairs, their files, sha256 and means are recorded in
``artifacts/driver_joint_grid_i24.json`` together with what
``validation.uncertainty.population_lineage`` (mirrored by
``calibration.transfer_check.measured_source``) returns for each file. That
reader accepts one mean moved per step through a sidecar-recorded base: it
resolves the k = 0 pairs (only mean T moves from the source) and the j = 0
pairs to ``artifacts/idm_i24.json``, and does **not** resolve the 16 pairs with
k > 0 and j ≠ 0 (two means move), which it would read as their own measured
population. Their lineage is their ``notes`` and the manifest; the reader is
not changed here (packages/ is out of this script's scope).

No ``*.calibration.json`` sidecar is written (as in ``scripts/derive_population.py``:
the capacity-sidecar readers would take it for a T-scaled capacity record).

Run (from the repository root; local, closed form, no simulation)::

    uv run --no-sync python scripts/driver_joint_grid.py
    uv run --no-sync python scripts/driver_joint_grid.py --check   # verify, write nothing
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from derive_population import file_sha256, measured_sd, out_name

from flowstate_core.artifacts import IDMCalibration
from flowstate_core.constants import IDM_RANGES

REPO_ROOT = Path(__file__).resolve().parents[1]

SCHEMA = "flowstate.driver_joint_grid/1"
PROTOCOL = (
    "docs/PRE_FRISCO_PROGRAM.md B6; docs/FRISCO_PROTOCOL.md Amendment 7 (proposed; approved "
    "by the coordinator 2026-10-07 21:16 CDT)"
)

DEFAULT_BASE = "artifacts/idm_i24_capacity.json"
DEFAULT_MEASURED = "artifacts/idm_i24.json"
DEFAULT_MANIFEST = "artifacts/driver_joint_grid_i24.json"
#: Amendment 1's one-shift populations (scripts/derive_population.py), the j = 0 row.
AMAX_STEM = "idm_i24_capacity_amax"
#: This grid's two-shift populations.
JOINT_STEM = "idm_i24_capacity_joint"

K_GRID: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)
"""Amendment 7 (= Amendment 1's k grid): mean a_max = measured mean + k · sd(a_max)."""
J_GRID: tuple[int, ...] = (-2, -1, 0, 1, 2)
"""Amendment 7: mean T = the base's mean T + j · T step."""
T_STEP_SD = 0.25
"""The T step in measured standard deviations: "0.25 sd of T, the k grid's step"."""
T_STEP_QUOTED_S = 0.130
"""The plan's quote of the step (docs/PRE_FRISCO_PROGRAM.md B6), checked to 3 decimals."""
T_BASE_QUOTED_S = 1.322
"""The plan's quote of the base's mean T, checked to 3 decimals."""
#: Protocol §7.2: a knob's measured range is the measured mean ± this many sd.
MEASURED_RANGE_SD = 1.0
SHIFTED = ("a_max", "T")


def rel(path: str | Path) -> str:
    """``path`` relative to the repository when inside it, else as given."""
    p = Path(path)
    try:
        return str((p if p.is_absolute() else REPO_ROOT / p).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _abs(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def t_step(measured: IDMCalibration) -> float:
    """``0.25 · sqrt(cov[T, T])`` of the measured population [s]."""
    return T_STEP_SD * measured_sd(measured, "T")


def measured_range(measured: IDMCalibration, param: str) -> tuple[float, float]:
    """Protocol §7.2's measured range of ``param``: mean ± 1 sd, inside CLAUDE.md §3.1's range."""
    m, sd = float(measured.mean[param]), measured_sd(measured, param)
    lo, hi = IDM_RANGES[param]
    return max(m - MEASURED_RANGE_SD * sd, lo), min(m + MEASURED_RANGE_SD * sd, hi)


def joint_name(k: float, j: int) -> str:
    """``idm_i24_capacity_joint_k<k>_j<±j>.json`` (k as Python prints a float)."""
    return f"{JOINT_STEM}_k{float(k)}_j{int(j):+d}.json"


def population_path(k: float, j: int, *, base: str, amax_dir: str, out_dir: str) -> str:
    """The file a grid pair runs (module docstring): the base, Amendment 1's, or this grid's."""
    if float(k) == 0.0 and int(j) == 0:
        return rel(base)
    if int(j) == 0:
        return rel(Path(amax_dir) / out_name(AMAX_STEM, k))
    return rel(Path(out_dir) / joint_name(k, j))


def check_inputs(base: IDMCalibration, measured: IDMCalibration) -> None:
    """The base must be the measured population with only mean T moved (its capacity scaling).

    Raises:
        ValueError: Different parameter orders, covariance, source or data hash,
            or a base mean other than T that differs from the measured one.
    """
    if list(base.param_names) != list(measured.param_names):
        raise ValueError("the base and the measured population list different parameters")
    if base.cov != measured.cov:
        raise ValueError("the base's covariance differs from the measured population's")
    if (base.source, base.data_hash) != (measured.source, measured.data_hash):
        raise ValueError("the base's source or data hash differs from the measured population's")
    for p in base.param_names:
        if p != "T" and not math.isclose(
            float(base.mean[p]), float(measured.mean[p]), rel_tol=1e-12, abs_tol=0.0
        ):
            raise ValueError(
                f"the base's mean {p} {base.mean[p]} differs from the measured "
                f"{measured.mean[p]}: k = 0 would not be the base"
            )


def joint_means(
    base: IDMCalibration, measured: IDMCalibration, k: float, j: int
) -> dict[str, float]:
    """The pair's two shifted means, by the module docstring's formulas."""
    a_max = float(measured.mean["a_max"]) + float(k) * measured_sd(measured, "a_max")
    t_mean = float(base.mean["T"]) + int(j) * t_step(measured)
    return {"a_max": a_max, "T": t_mean}


def _lineage_sentence(k: float, j: int) -> str:
    if float(k) != 0.0 and int(j) != 0:
        return (
            "Two means move from the base, so validation.uncertainty.population_lineage and "
            "calibration.transfer_check.measured_source (one mean per step, through "
            "sidecar-recorded bases) do not resolve this population to its measured source; its "
            "lineage is this note and artifacts/driver_joint_grid_i24.json."
        )
    return (
        "Only mean T moves from the measured source, so validation.uncertainty.population_lineage "
        "resolves this population to artifacts/idm_i24.json."
    )


def derive_joint(
    base: IDMCalibration,
    measured: IDMCalibration,
    *,
    k: float,
    j: int,
    base_label: str,
    measured_label: str,
    created_at: str,
) -> IDMCalibration:
    """``base`` with mean ``a_max`` and mean ``T`` moved to grid pair (k, j) (module docstring).

    Args:
        base: The population the corridors run (k = 0, j = 0).
        measured: The measured source population (the standard deviations, the
            origin of ``a_max`` and the measured ranges).
        k: ``a_max`` shift in measured standard deviations, ``|k| <= 1``.
        j: ``T`` shift in steps of 0.25 measured sd.
        base_label: The base's path and sha256, for the notes.
        measured_label: The measured population's path and sha256.
        created_at: ISO-8601 timestamp of the derivation.

    Returns:
        The derived :class:`IDMCalibration`.

    Raises:
        ValueError: Inputs that :func:`check_inputs` refuses, ``|k| > 1``, or a
            new mean outside protocol §7.2's measured range or CLAUDE.md §3.1's
            calibration range.
    """
    check_inputs(base, measured)
    if not -MEASURED_RANGE_SD <= float(k) <= MEASURED_RANGE_SD:
        raise ValueError(f"k = {k} lies outside the measured mean ± {MEASURED_RANGE_SD:g} sd")
    if int(j) != j:
        raise ValueError(f"j = {j} is not an integer step")
    new = joint_means(base, measured, k, int(j))
    for p in SHIFTED:
        lo, hi = measured_range(measured, p)
        if not lo <= new[p] <= hi:
            raise ValueError(
                f"mean {p} {new[p]:.4f} lies outside the measured range {lo:.4f}-{hi:.4f} "
                "(protocol §7.2: measured mean ± 1 sd, inside CLAUDE.md §3.1's range)"
            )
    sd_a, sd_t, step = measured_sd(measured, "a_max"), measured_sd(measured, "T"), t_step(measured)
    m_a, t0 = float(measured.mean["a_max"]), float(base.mean["T"])
    note = (
        f"Derived from {base_label} by scripts/driver_joint_grid.py ({PROTOCOL}): grid pair "
        f"(k, j) = ({float(k):g}, {int(j):+d}); population mean a_max = measured mean + k x "
        f"sd(a_max) = {m_a:.6f} + {float(k):g} x {sd_a:.6f} = {new['a_max']:.6f}; mean T = the "
        f"base's mean T + j x 0.25 x sd(T) = {t0:.6f} + ({int(j):+d}) x 0.25 x {sd_t:.6f} = "
        f"{new['T']:.6f}; sd = sqrt(cov[p,p]) of the measured source population "
        f"{measured_label}; covariance and every other mean unchanged (the base's). Derived "
        f"{created_at}. {_lineage_sentence(k, int(j))} A candidate of the Amendment-7 grid (T "
        f"step {step:.6f} s), not a calibrated population until docs/DISCHARGE_CALIBRATION.md "
        f"section 6 records a choice. Base notes: {base.notes}"
    )
    mean = dict(base.mean)
    mean.update(new)
    return base.model_copy(update={"mean": mean, "notes": note, "created_at": created_at})


def same_population(a: IDMCalibration, b: IDMCalibration) -> bool:
    """Equal parameter names, means and covariance (bit for bit), source and data hash."""
    return (
        list(a.param_names) == list(b.param_names)
        and dict(a.mean) == dict(b.mean)
        and a.cov == b.cov
        and (a.source, a.data_hash) == (b.source, b.data_hash)
    )


def package_lineage(path: str) -> dict[str, Any] | None:
    """What ``validation.uncertainty.population_lineage`` returns for ``path`` (None: unresolved)."""
    from validation.uncertainty import population_lineage

    found = population_lineage(path)
    if found is None:
        return None
    return {
        "source": found.source.label.split(" (", 1)[0],
        "source_label": found.source.label,
        "how": found.words(),
    }


def without_package_lineage(manifest: dict[str, Any]) -> dict[str, Any]:
    """The manifest without the record of what the package lineage reader returned.

    That record describes ``packages/validation`` at derivation time, not the
    derivation; ``--check`` and the tests compare everything else.
    """
    out = {k: v for k, v in manifest.items() if k != "package_lineage_note"}
    out["pairs"] = [
        {k: v for k, v in p.items() if k != "package_lineage"} for p in manifest["pairs"]
    ]
    return out


def build(
    base_path: str | Path,
    measured_path: str | Path,
    *,
    amax_dir: str | Path,
    out_dir: str | Path,
    created_at: str,
    write: bool,
) -> dict[str, Any]:
    """Derive the 25 populations, write the 20 new ones (``write``), return the manifest.

    With ``write`` false nothing is written and every pair's file must already
    hold the derivation (``--check``).

    Raises:
        FileNotFoundError: An Amendment-1 population (j = 0) or, without
            ``write``, one of this grid's files is missing.
        ValueError: A file that exists but is not the derivation.
    """
    base_p, meas_p = _abs(base_path), _abs(measured_path)
    base, measured = IDMCalibration.load(base_p), IDMCalibration.load(meas_p)
    check_inputs(base, measured)
    base_label = f"{rel(base_p)} (sha256 {file_sha256(base_p)})"
    measured_label = f"{rel(meas_p)} (sha256 {file_sha256(meas_p)})"
    cov_tt = float(
        measured.cov[list(measured.param_names).index("T")][list(measured.param_names).index("T")]
    )
    step = t_step(measured)
    pairs: list[dict[str, Any]] = []
    for k in K_GRID:
        for j in J_GRID:
            path = population_path(
                k, j, base=str(base_p), amax_dir=str(amax_dir), out_dir=str(out_dir)
            )
            target = _abs(path)
            fresh = derive_joint(
                base,
                measured,
                k=k,
                j=j,
                base_label=base_label,
                measured_label=measured_label,
                created_at=created_at,
            )
            if j == 0:
                origin = "base" if k == 0.0 else "scripts/derive_population.py (Amendment 1)"
                if not target.is_file():
                    raise FileNotFoundError(
                        f"{path} is missing: run scripts/derive_population.py (Amendment 1)"
                    )
                if not same_population(IDMCalibration.load(target), fresh):
                    raise ValueError(f"{path} is not the derivation of pair ({k:g}, {j:+d})")
            else:
                origin = "scripts/driver_joint_grid.py"
                if write:
                    fresh.save(target)
                elif not target.is_file():
                    raise FileNotFoundError(f"{path} is missing: run without --check")
                elif not same_population(IDMCalibration.load(target), fresh):
                    raise ValueError(f"{path} is not the derivation of pair ({k:g}, {j:+d})")
            moved = [p for p in SHIFTED if fresh.mean[p] != base.mean[p]]
            pairs.append(
                {
                    "k": float(k),
                    "j": int(j),
                    "a_max_mean_ms2": float(fresh.mean["a_max"]),
                    "T_mean_s": float(fresh.mean["T"]),
                    "path": path,
                    "sha256": file_sha256(target),
                    "origin": origin,
                    "means_moved_from_base": moved,
                    "package_lineage": package_lineage(path),
                }
            )
    lo_a, hi_a = measured_range(measured, "a_max")
    lo_t, hi_t = measured_range(measured, "T")
    n_unresolved = sum(p["package_lineage"] is None for p in pairs)
    return {
        "schema": SCHEMA,
        "protocol": PROTOCOL,
        "created_at": created_at,
        "script": "scripts/driver_joint_grid.py",
        "base": {
            "path": rel(base_p),
            "sha256": file_sha256(base_p),
            "mean_T_s": float(base.mean["T"]),
            "mean_a_max_ms2": float(base.mean["a_max"]),
        },
        "measured": {
            "path": rel(meas_p),
            "sha256": file_sha256(meas_p),
            "mean": {p: float(measured.mean[p]) for p in SHIFTED},
            "sd": {p: measured_sd(measured, p) for p in SHIFTED},
            "measured_range": {"a_max": [lo_a, hi_a], "T": [lo_t, hi_t]},
            "range_rule": "measured mean ± 1 sd (sqrt of the covariance diagonal), inside "
            "CLAUDE.md §3.1's calibration range (docs/FRISCO_PROTOCOL.md §7.2)",
        },
        "steps": {
            "a_max_per_unit_k_ms2": measured_sd(measured, "a_max"),
            "a_max_formula": "mean a_max = measured mean + k x sqrt(cov[a_max, a_max]) of the "
            "measured population",
            "T_step_s": step,
            "T_step_derivation": {
                "cov_T_T_s2": cov_tt,
                "sd_T_s": measured_sd(measured, "T"),
                "fraction_of_sd": T_STEP_SD,
                "value_s": step,
                "quoted_in_plan_s": T_STEP_QUOTED_S,
                "matches_quote": round(step, 3) == T_STEP_QUOTED_S,
                "base_T_quoted_in_plan_s": T_BASE_QUOTED_S,
                "base_T_matches_quote": round(float(base.mean["T"]), 3) == T_BASE_QUOTED_S,
                "source": "the measured population's covariance (the base carries the same one)",
            },
            "T_formula": "mean T = the base's mean T + j x 0.25 x sqrt(cov[T, T]) of the "
            "measured population",
        },
        "k_grid": list(K_GRID),
        "j_grid": list(J_GRID),
        "naming": {
            "(0, 0)": rel(base_p),
            "(k, 0), k > 0": f"<amax dir>/{AMAX_STEM}_k<k>.json (scripts/derive_population.py, "
            "Amendment 1; checked equal to the derivation)",
            "(k, j), j != 0": f"<out dir>/{JOINT_STEM}_k<k>_j<+j|-j>.json (written here)",
        },
        "pairs": pairs,
        "package_lineage_note": (
            "validation.uncertainty.population_lineage (mirrored by "
            "calibration.transfer_check.measured_source) as of this derivation: it accepts one "
            "mean moved per step through a sidecar-recorded base, so it resolves the k = 0 and "
            f"j = 0 pairs to {rel(meas_p)} and leaves the {n_unresolved} pairs with k > 0 and "
            "j != 0 unresolved (null here): read through it, their protocol §7.2 measured ranges "
            "would be centred on their own shifted means. Their lineage is their notes and this "
            "file. Fixing the reader is a change to packages/validation and "
            "packages/calibration, not made here."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    """Derive and write the grid (or ``--check`` it); returns the exit status."""
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--base", type=Path, default=REPO_ROOT / DEFAULT_BASE)
    ap.add_argument("--measured", type=Path, default=REPO_ROOT / DEFAULT_MEASURED)
    ap.add_argument(
        "--amax-dir",
        type=Path,
        default=REPO_ROOT / "artifacts",
        help="where Amendment 1's idm_i24_capacity_amax_k<k>.json live (the j = 0 row)",
    )
    ap.add_argument("--out-dir", type=Path, default=REPO_ROOT / "artifacts")
    ap.add_argument("--manifest", type=Path, default=REPO_ROOT / DEFAULT_MANIFEST)
    ap.add_argument(
        "--created-at",
        default=None,
        help="ISO-8601 timestamp recorded in the artifacts (default: now, UTC)",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="write nothing: every file must hold the derivation and the manifest must match",
    )
    args = ap.parse_args(argv)
    if args.check:
        stored = json.loads(args.manifest.read_text())
        try:
            fresh = build(
                args.base,
                args.measured,
                amax_dir=args.amax_dir,
                out_dir=args.out_dir,
                created_at=stored["created_at"],
                write=False,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"CHECK FAILED: {exc}")
            return 1
        if without_package_lineage(fresh) != without_package_lineage(stored):
            print(f"CHECK FAILED: {rel(args.manifest)} differs from the derivation")
            return 1
        print(f"ok: {len(fresh['pairs'])} pairs, {rel(args.manifest)} reproduces")
        if fresh != stored:
            print(
                "note: validation.uncertainty.population_lineage now reads some pairs differently "
                "from the manifest's record (package_lineage); re-run without --check to refresh it"
            )
        return 0
    created = args.created_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = build(
        args.base,
        args.measured,
        amax_dir=args.amax_dir,
        out_dir=args.out_dir,
        created_at=created,
        write=True,
    )
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=1, allow_nan=False) + "\n")
    st = manifest["steps"]["T_step_derivation"]
    print(
        f"T step = {T_STEP_SD:g} x sqrt({st['cov_T_T_s2']:.10f}) = {T_STEP_SD:g} x "
        f"{st['sd_T_s']:.6f} = {st['value_s']:.6f} s (plan quotes {T_STEP_QUOTED_S:.3f})"
    )
    for p in manifest["pairs"]:
        lin = "resolves" if p["package_lineage"] else "UNRESOLVED by population_lineage"
        print(
            f"(k, j) = ({p['k']:g}, {p['j']:+d}): a_max {p['a_max_mean_ms2']:.4f}, "
            f"T {p['T_mean_s']:.4f} -> {p['path']} [{p['origin']}; {lin}]"
        )
    print(f"-> {rel(args.manifest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
