"""Re-score an archived I-24 battery's locks into a sidecar (no simulation, no re-run).

The I-24 batteries written before 2026-10-07 (``scripts/i24_validate.py``)
carry no lock records, so their ``no_locks`` criteria row reads NOT RECORDED.
Their replicates' files survive in the stage archives
(``~/.flowstate-block/archives/<stage>_final.tgz``, run tree
``runs/i24_validation/<label>/<config hash>/<seed>/``). This script reads
them with the corridor batteries' reader and writes what the battery would
have recorded, beside the committed battery artifact, which it never
modifies:

1. reads the committed battery artifact (``artifacts/i24_validation_<label>.json``)
   for its seeds, their order and its config hash (read-only);
2. streams the archive and extracts, for those seeds only, ``meta.json``,
   ``edges.parquet`` and ``vehicles.parquet`` (never ``trajectories.parquet``
   or any other member) into a temporary directory under ``--scratch``,
   deleted on exit;
3. runs :func:`validation.locks.detect_run_locks` on each replicate (the
   space-time reader on ``edges.parquet``, the run-end reader on
   ``vehicles.parquet``; a replicate with neither is not recorded, never
   unlocked);
4. writes the sidecar ``artifacts/i24_locks_<label>.json`` with the keys
   ``scripts/i24_validate.py`` now writes into a battery artifact —
   ``simulated.locks_per_replicate`` (one :class:`validation.locks.RunLocks`
   record per seed, seed order), ``locks``
   (:func:`validation.locks.lock_summary` labelled by seed) and
   ``zero_locks`` (:func:`validation.battery.lock_free`) — the re-scored
   ``no_locks`` criteria row beside the committed one, and provenance (the
   battery artifact's and the archive's sha256, the files read per seed,
   the scorer's commit and its files' sha256).

The sidecar also carries ``standing_per_replicate``, a diagnostic and not a
criterion: per replicate, how many 15 s × 100 m bins of ``edges.parquet``
meet the space-time reader's own standing test (its default thresholds) and
the longest interval any one cell stood without a break, against the lock
duration — how close a replicate that broke down came to a lock.

Thresholds are :data:`validation.locks.DEFAULT_PARAMS`, never tuned here.

Usage (repo root)::

    uv run --no-sync python scripts/i24_rescore_locks.py \\
        --archive ~/.flowstate-block/archives/p14_final.tgz --label p14_b1b2 \\
        --scratch /private/tmp/<scratch dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from validation.battery import lock_free
from validation.criteria import NO_LOCKS, evaluate, get_profile
from validation.locks import (
    DEFAULT_PARAMS,
    RunLocks,
    _runs,
    _standing_field,
    detect_run_locks,
    lock_summary,
    read_edges,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "flowstate.i24_locks/1"

#: The only members extracted from an archive (the lock reader's inputs).
FILES = ("meta.json", "edges.parquet", "vehicles.parquet")

#: The run tree root inside an archive (``runs/i24_validation<_family>``).
RUNS_ROOT = "runs/i24_validation"

#: The criteria profile of every I-24 battery (``scripts/i24_validate.PROFILE``).
PROFILE = get_profile("fhwa_default")

#: The files whose sha256 the sidecar records (the scorer and the reader).
CODE_PATHS = (
    "scripts/i24_rescore_locks.py",
    "packages/validation/validation/locks.py",
    "packages/validation/validation/criteria.py",
    "packages/validation/validation/battery.py",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def member_pattern(runs_root: str, label: str, config_hash: str) -> re.Pattern[str]:
    """The archive member names this script extracts: ``<root>/<label>/<hash>/<seed>/<file>``.

    ``<file>`` is one of :data:`FILES`; anything else (``trajectories.parquet``,
    ``journeys.parquet``, other labels or hashes) does not match.
    """
    files = "|".join(re.escape(f) for f in FILES)
    return re.compile(
        rf"^(?:\./)?{re.escape(runs_root)}/{re.escape(label)}/{re.escape(config_hash)}"
        rf"/(?P<seed>\d+)/(?P<file>{files})$"
    )


def extract_replicates(
    archive: Path, pattern: re.Pattern[str], seeds: Sequence[int], dest: Path
) -> dict[int, list[str]]:
    """Stream ``archive`` and copy the matching members of ``seeds`` to ``dest/<seed>/<file>``.

    Only regular files are copied (a hard-link entry repeating a member is
    skipped); destination paths are built from the pattern's groups, never
    from the member name, so nothing lands outside ``dest``.

    Returns:
        The files extracted per seed (every seed listed, possibly empty).
    """
    wanted = {int(s) for s in seeds}
    found: dict[int, list[str]] = {int(s): [] for s in seeds}
    with tarfile.open(archive, "r|*") as tar:
        for member in tar:
            match = pattern.match(member.name)
            if match is None or not member.isfile():
                continue
            seed, name = int(match["seed"]), match["file"]
            if seed not in wanted or name in found[seed]:
                continue
            src = tar.extractfile(member)
            if src is None:
                continue
            out = dest / str(seed) / name
            out.parent.mkdir(parents=True, exist_ok=True)
            with src, open(out, "wb") as f:
                shutil.copyfileobj(src, f)
            found[seed].append(name)
    return {s: sorted(names) for s, names in found.items()}


def score_replicates(root: Path, seeds: Sequence[int]) -> list[RunLocks]:
    """Each seed's :class:`validation.locks.RunLocks` from ``root/<seed>/`` (seed order)."""
    return [detect_run_locks(root / str(seed)) for seed in seeds]


def standing_diagnostic(run_dir: Path) -> dict[str, Any] | None:
    """How close a replicate came to a lock (module docstring); None without ``edges.parquet``.

    Uses the space-time reader's standing test with :data:`DEFAULT_PARAMS`
    (a cell stands when its density is at least ``min_density_veh_m`` and its
    flow at most ``max_flow_veh_s``); a cell becomes locked once it stands
    ``min_duration_s`` without a break.

    Returns:
        ``{n_standing_bins, n_bins, longest_standing_s, longest_x_lo_m,
        longest_t_start_s, lock_duration_s}`` (the place and start of the
        longest standing interval null when no bin stands).
    """
    edges = read_edges(run_dir)
    if edges is None or not len(edges):
        return None
    fld = _standing_field(edges, DEFAULT_PARAMS)
    longest, x_lo, t0 = 0.0, None, None
    for i in range(fld.standing.shape[1]):
        for k0, k1 in _runs(fld.standing[:, i]):
            span = float(fld.t_hi[k1] - fld.t_lo[k0])
            if span > longest:
                longest, x_lo, t0 = span, float(fld.x_lo[i]), float(fld.t_lo[k0])
    return {
        "n_standing_bins": int(fld.standing.sum()),
        "n_bins": int(fld.standing.size),
        "longest_standing_s": longest,
        "longest_x_lo_m": x_lo,
        "longest_t_start_s": t0,
        "lock_duration_s": DEFAULT_PARAMS.min_duration_s,
    }


def _code() -> dict[str, Any]:
    """The scorer's commit, whether its files differ from it, and their sha256."""

    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    status = git("status", "--porcelain", "--", *CODE_PATHS)
    return {
        "commit": git("rev-parse", "--short", "HEAD"),
        "dirty": None if status is None else bool(status),
        "sha256": {p: _sha256(REPO_ROOT / p) for p in CODE_PATHS if (REPO_ROOT / p).is_file()},
    }


def _rel(path: Path) -> str:
    p = path.resolve()
    return str(p.relative_to(REPO_ROOT)) if p.is_relative_to(REPO_ROOT) else p.name


def _json_safe(obj: object) -> object:
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def build_sidecar(
    *,
    label: str,
    battery: Mapping[str, Any],
    battery_path: Path,
    archive: Path,
    run_tree: str,
    files: Mapping[int, list[str]],
    records: Sequence[RunLocks],
    standing: Sequence[dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """The sidecar (module docstring, step 4)."""
    seeds = [int(s) for s in battery["seeds"]]
    row = next(r for r in evaluate(PROFILE, lock_records=records) if r.name == NO_LOCKS)
    committed = next((r for r in battery.get("criteria") or [] if r.get("name") == NO_LOCKS), None)
    return {
        "schema": SCHEMA,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "label": label,
        "scenario": battery.get("scenario"),
        "config_hash": battery.get("config_hash"),
        "seeds": seeds,
        "battery_artifact": {
            "path": _rel(battery_path),
            "sha256": _sha256(battery_path),
            "no_locks_row": committed,
        },
        "source": {
            "archive": archive.name,
            "archive_sha256": _sha256(archive),
            "run_tree": run_tree,
            "files_read": {str(s): list(files.get(s, [])) for s in seeds},
            "never_read": "trajectories.parquet and every other member of the archive",
        },
        "code": _code(),
        "simulated": {"locks_per_replicate": [r.to_dict() for r in records]},
        "locks": lock_summary(records, labels=seeds),
        "zero_locks": lock_free(records),
        "criteria": [asdict(row)],
        "standing_per_replicate": (
            None if standing is None else [None if d is None else dict(d) for d in standing]
        ),
        "notes": [
            "Re-scored from the archived run tree without simulating: each replicate's "
            "meta.json, edges.parquet and vehicles.parquet (where archived) read by "
            "validation.locks.detect_run_locks with its default thresholds; the committed "
            "battery artifact is not modified.",
            "simulated.locks_per_replicate, locks and zero_locks are the keys "
            "scripts/i24_validate.py writes into a battery artifact since 2026-10-07; criteria "
            "holds the no_locks row those records score, battery_artifact.no_locks_row the "
            "committed row.",
            "A replicate with edges.parquet is completely recorded; one with vehicles.parquet "
            "alone is partially recorded (no lock found there is not 'no lock'); one with "
            "neither is not recorded.",
            "standing_per_replicate is a diagnostic, not a criterion: per seed (seed order), "
            "the bins of edges.parquet meeting the space-time reader's standing test at its "
            "default thresholds and the longest interval one cell stood without a break, "
            "against the lock duration; null without edges.parquet.",
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--archive", type=Path, required=True, help="the stage archive (.tgz)")
    ap.add_argument("--label", required=True, help="the battery's label (its run tree's name)")
    ap.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="the committed battery artifact (default artifacts/i24_validation_<label>.json)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="the sidecar (default artifacts/i24_locks_<label>.json)",
    )
    ap.add_argument(
        "--scratch",
        type=Path,
        default=None,
        help="where the temporary extraction directory is made (default the system temp dir)",
    )
    ap.add_argument("--runs-root", default=RUNS_ROOT, help="the run tree root in the archive")
    args = ap.parse_args(argv)
    battery_path = args.artifact or REPO_ROOT / "artifacts" / f"i24_validation_{args.label}.json"
    out_path = args.out or REPO_ROOT / "artifacts" / f"i24_locks_{args.label}.json"
    if not args.archive.is_file():
        ap.error(f"--archive {args.archive}: no such file")
    if not battery_path.is_file():
        ap.error(f"{battery_path}: no battery artifact (its seeds and config hash are needed)")
    battery = json.loads(battery_path.read_text())
    seeds = [int(s) for s in battery["seeds"]]
    config_hash = str(battery["config_hash"])
    run_tree = f"{args.runs_root}/{args.label}/{config_hash}"
    if args.scratch is not None:
        args.scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.scratch, prefix="i24_locks_") as tmp:
        root = Path(tmp)
        files = extract_replicates(
            args.archive, member_pattern(args.runs_root, args.label, config_hash), seeds, root
        )
        if not any(files.values()):
            ap.error(f"{args.archive.name}: no replicate files under {run_tree}/")
        records = score_replicates(root, seeds)
        standing = [standing_diagnostic(root / str(seed)) for seed in seeds]
    sidecar = build_sidecar(
        label=args.label,
        battery=battery,
        battery_path=battery_path,
        archive=args.archive,
        run_tree=run_tree,
        files=files,
        records=records,
        standing=standing,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(_json_safe(sidecar), indent=2, allow_nan=False) + "\n")
    row = sidecar["criteria"][0]
    locks = sidecar["locks"]
    print(
        f"{args.label} ({config_hash}): "
        + (
            "not recorded"
            if locks is None
            else f"{locks['n_runs_locked']} of {locks['n_runs_recorded']} recorded "
            f"replicate(s) locked; {len(locks['runs_not_recorded'])} not recorded, "
            f"{len(locks['runs_partially_recorded'])} partially recorded"
        ),
        flush=True,
    )
    for run in [] if locks is None else locks["runs_locked"]:
        print(
            f"    {run['run']}: "
            + "; ".join(
                f"{lk['section']} from {lk['onset_s']:.0f} s for {lk['duration_s'] / 60:.1f} min"
                for lk in run["locks"]
            ),
            flush=True,
        )
    worst = max(
        ((s, d) for s, d in zip(seeds, standing, strict=True) if d is not None),
        key=lambda sd: sd[1]["longest_standing_s"],
        default=None,
    )
    if worst is not None and worst[1]["longest_standing_s"] <= 0.0:
        print("    no cell met the standing test in any replicate", flush=True)
    elif worst is not None:
        print(
            f"    longest standstill of any cell: {worst[1]['longest_standing_s']:.0f} s "
            f"(seed {worst[0]}; a lock needs {DEFAULT_PARAMS.min_duration_s:.0f} s)",
            flush=True,
        )
    print(f"    {row['name']:<18} {_status(row):<14} {row['value']}  -> {_rel(out_path)}")
    return 0


def _status(row: Mapping[str, Any]) -> str:
    if not row["evaluated"]:
        return "NOT RECORDED"
    return "PASS" if row["passed"] else "FAIL"


if __name__ == "__main__":
    raise SystemExit(main())
