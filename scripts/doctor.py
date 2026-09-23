"""Preflight check: can this machine run FlowState, and if not, what is wrong?

One command a first-time tester runs before anything else (docs/QUICKSTART.md)::

    uv run --no-sync python scripts/doctor.py

It prints one row per check with ``PASS`` / ``WARN`` / ``FAIL`` and, for every
row that is not ``PASS``, a single-line fix. The exit code is 1 when any check
FAILs (a ``WARN`` is a degraded but working machine: no Redis, a small disk, a
missing optional data file), so it drops straight into a shell script or CI
step.

What it checks, and why each one is here:

* **python** — the workspace pins ``>=3.12,<3.13``; another interpreter
  resolves a different dependency set.
* **settings** — ``api.settings.load_settings()`` must succeed; a mistyped
  ``FLOWSTATE_QUEUE`` refuses to build the service later, in a worse place.
* **sumo_packages / sumo_version** — ``eclipse-sumo``, ``libsumo``, ``traci``
  and ``sumolib`` must import and must all be the pinned version. Goldens are
  per-SUMO-version (CLAUDE.md §9), so a different wheel silently moves every
  number.
* **pyarrow** — 24.0.0 is excluded by the workspace pins; it is the release
  whose bundled Arrow collides with libsumo's.
* **netconvert** — the OSM import path (``osm_generic``, CLAUDE.md §3.2.4)
  shells out to it through ``sumolib.checkBinary``.
* **redis_server** — only the ``redis`` queue needs it; the inline queue does
  not, hence WARN.
* **results_root / data_roots** — the service writes every artifact, the
  SQLite store and every upload under these; unwritable means every job fails.
* **scenarios / scenario_files / osm_extracts** — every shipped preset must
  parse, and the artifacts and OSM extracts they name must exist or those
  presets cannot run (WARN: a fresh clone without the local data payloads
  still runs the ring and the synthetic corridor).
* **disk / memory** — headroom for run artifacts and for SUMO processes.
* **smoke** — 10 simulated seconds of ``ring_sugiyama`` through
  ``microsim.runner.run_micro`` into a temporary directory, reporting
  steps/second: the end-to-end proof that the engine actually runs here.

``--json`` prints the same results as one JSON object (stdout is kept clean:
libsumo prints a pyarrow banner on import, which this script redirects to
stderr). ``--no-sim`` skips the smoke run.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.metadata as importlib_metadata
import json
import os
import platform
import re
import shutil
import sys
import tempfile
import time
import tomllib
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

REPO_ROOT = Path(__file__).resolve().parent.parent

#: SUMO distributions that must all carry the pinned version.
SUMO_DISTRIBUTIONS: tuple[str, ...] = ("eclipse-sumo", "libsumo", "traci", "sumolib")

#: Fallbacks used when the workspace pins cannot be read from disk.
FALLBACK_SUMO_PIN = "1.27.1"
FALLBACK_PYARROW_EXCLUDED = "24.0.0"

#: Where the pins live: the micro tier declares SUMO and pyarrow.
PIN_PYPROJECT = REPO_ROOT / "packages" / "microsim" / "pyproject.toml"
LOCKFILE = REPO_ROOT / "uv.lock"

#: WARN thresholds for the resource checks.
MIN_FREE_DISK_GB = 5.0
MIN_TOTAL_MEMORY_GB = 8.0

#: Smoke-run size: 10 simulated seconds of the 230 m ring.
SMOKE_SCENARIO = REPO_ROOT / "scenarios" / "ring_sugiyama.yaml"
SMOKE_DURATION_S = 10.0

Status = Literal["PASS", "WARN", "FAIL"]

_GB = float(1024**3)


@dataclass(frozen=True)
class Check:
    """One preflight result.

    Attributes:
        name: Stable identifier, also the table's first column.
        status: ``PASS``, ``WARN`` or ``FAIL``.
        detail: What was found, on one line.
        fix: What to do about it, on one line. Printed under the row whenever
            the status is not ``PASS``, and always present in ``--json``.
    """

    name: str
    status: Status
    detail: str
    fix: str = ""


# ---------------------------------------------------------------------------
# Small helpers (monkeypatched by tests/test_scripts/test_doctor.py)
# ---------------------------------------------------------------------------


def _dist_version(name: str) -> str | None:
    """Installed version of a distribution, or ``None`` when it is absent.

    Args:
        name: Distribution name as it appears on PyPI (``eclipse-sumo``).

    Returns:
        The version string, or ``None`` when the distribution is not installed.
    """
    try:
        return importlib_metadata.version(name)
    except importlib_metadata.PackageNotFoundError:
        return None


def _netconvert_path() -> str | None:
    """Path to the ``netconvert`` binary, from the wheel or from ``PATH``.

    Returns:
        An existing executable path, or ``None`` when SUMO's binary cannot be
        located at all.
    """
    candidate: str | None = None
    try:
        import sumolib

        candidate = str(sumolib.checkBinary("netconvert"))
    except Exception:  # pragma: no cover - only when sumolib itself is broken
        candidate = None
    if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
        return candidate
    return shutil.which("netconvert")


def _total_memory_bytes() -> int | None:
    """Total physical memory in bytes, or ``None`` when it cannot be read."""
    try:
        return int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, ValueError, OSError):  # pragma: no cover - exotic platforms
        return None


def _requirement_specs(dist: str) -> str | None:
    """The workspace's requirement string for ``dist``.

    Reads ``packages/microsim/pyproject.toml`` (where SUMO and pyarrow are
    declared) so the pins this script enforces are the repository's own.

    Args:
        dist: Distribution name, e.g. ``"eclipse-sumo"``.

    Returns:
        The specifier part of the requirement (``"==1.27.1"``), or ``None``
        when the file or the requirement is missing.
    """
    try:
        data = tomllib.loads(PIN_PYPROJECT.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    deps = data.get("project", {}).get("dependencies", [])
    for raw in deps:
        if not isinstance(raw, str):
            continue
        match = re.match(r"^\s*([A-Za-z0-9._-]+)\s*(.*)$", raw)
        if match and match.group(1).lower().replace("_", "-") == dist.lower():
            return match.group(2).strip()
    return None


def _sumo_pin() -> tuple[str, str]:
    """The pinned SUMO version and where it was read from.

    Returns:
        ``(version, source)``; ``source`` is the file the pin came from, or
        ``"fallback"`` when neither the pyproject nor the lockfile could be
        read.
    """
    spec = _requirement_specs("eclipse-sumo")
    if spec and spec.startswith("=="):
        return spec[2:].strip(), str(PIN_PYPROJECT.relative_to(REPO_ROOT))
    try:
        lock = LOCKFILE.read_text()
    except OSError:
        return FALLBACK_SUMO_PIN, "fallback"
    match = re.search(r'name = "eclipse-sumo"\nversion = "([^"]+)"', lock)
    if match:
        return match.group(1), str(LOCKFILE.relative_to(REPO_ROOT))
    return FALLBACK_SUMO_PIN, "fallback"


def _pyarrow_excluded() -> str:
    """The pyarrow version the workspace excludes (``!=`` in the pins)."""
    spec = _requirement_specs("pyarrow") or ""
    match = re.search(r"!=\s*([0-9][0-9A-Za-z.*+-]*)", spec)
    return match.group(1) if match else FALLBACK_PYARROW_EXCLUDED


@contextlib.contextmanager
def _stdout_to_stderr() -> Iterator[None]:
    """Send anything printed by an import or a run to stderr.

    ``import libsumo`` prints a pyarrow-version banner on stdout, which would
    corrupt ``--json``. The report itself is written to the real stdout after
    the checks have finished.
    """
    saved = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = saved


def _writable(directory: Path) -> str | None:
    """Create ``directory`` if needed and prove it is writable.

    Args:
        directory: Directory to test.

    Returns:
        ``None`` when the directory exists (or was created) and a file could
        be written and removed inside it; otherwise a one-line reason.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"cannot create: {exc.strerror or exc}"
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".doctor-", delete=True):
            pass
    except OSError as exc:
        return f"not writable: {exc.strerror or exc}"
    return None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_python(info: tuple[int, int] | None = None) -> Check:
    """Interpreter is CPython 3.12 (the workspace pin ``>=3.12,<3.13``).

    Args:
        info: ``(major, minor)`` to judge instead of the running interpreter's
            (tests pass an explicit pair).
    """
    major, minor = info if info is not None else (sys.version_info.major, sys.version_info.minor)
    detail = f"{major}.{minor} ({platform.python_implementation()}, {platform.platform()})"
    if (major, minor) == (3, 12):
        return Check("python", "PASS", detail, "")
    return Check(
        "python",
        "FAIL",
        detail,
        "the workspace requires >=3.12,<3.13: `uv python install 3.12` then re-sync",
    )


def check_settings(settings: Any) -> Check:
    """Service settings resolved from the environment.

    Args:
        settings: The loaded ``api.settings.Settings``, or ``None`` when
            loading raised (the reason is then in ``detail``).
    """
    if settings is None:
        return Check(
            "settings",
            "FAIL",
            "api.settings.load_settings() raised (see the line above)",
            "fix the FLOWSTATE_* environment variables named in the error (docs/DEPLOYMENT.md §2)",
        )
    return Check(
        "settings",
        "PASS",
        f"queue={settings.queue_kind} results={settings.results_dir} "
        f"scenarios={settings.scenarios_dir}",
        "",
    )


def check_sumo_packages() -> Check:
    """``sumolib``, ``traci`` and ``libsumo`` import in this interpreter."""
    failed: list[str] = []
    with _stdout_to_stderr():
        for module in ("sumolib", "traci", "libsumo"):
            try:
                __import__(module)
            except Exception as exc:
                failed.append(f"{module} ({type(exc).__name__})")
    if failed:
        return Check(
            "sumo_packages",
            "FAIL",
            "not importable: " + ", ".join(failed),
            "`uv sync --all-packages --dev` installs eclipse-sumo/libsumo/traci/sumolib wheels",
        )
    return Check("sumo_packages", "PASS", "sumolib, traci, libsumo import", "")


def check_sumo_version() -> Check:
    """Every SUMO distribution carries the version the workspace pins."""
    pin, source = _sumo_pin()
    found = {name: _dist_version(name) for name in SUMO_DISTRIBUTIONS}
    missing = [name for name, version in found.items() if version is None]
    wrong = [f"{name}={version}" for name, version in found.items() if version not in (None, pin)]
    detail = f"pin {pin} (from {source}); " + ", ".join(
        f"{name}={version or 'absent'}" for name, version in found.items()
    )
    if missing or wrong:
        return Check(
            "sumo_version",
            "FAIL",
            detail,
            f"goldens are per-SUMO-version (CLAUDE.md §9): install the pinned {pin} "
            f"wheels with `uv sync --all-packages --dev`",
        )
    return Check("sumo_version", "PASS", detail, "")


def check_pyarrow(version: str | None = None) -> Check:
    """pyarrow is installed and is not the excluded release.

    Args:
        version: Version to judge instead of the installed one (tests pass the
            excluded release).
    """
    excluded = _pyarrow_excluded()
    found = version if version is not None else _dist_version("pyarrow")
    if found is None:
        return Check(
            "pyarrow",
            "FAIL",
            "not installed",
            "`uv sync --all-packages --dev` (trajectories and metrics are Parquet)",
        )
    if found == excluded:
        return Check(
            "pyarrow",
            "FAIL",
            f"{found} is excluded by the workspace pins (!={excluded})",
            f"install any pyarrow other than {excluded} — its bundled Arrow collides with "
            f"libsumo's and every Parquet write dies mid-run",
        )
    return Check("pyarrow", "PASS", f"{found} (excluded: {excluded})", "")


def check_netconvert() -> Check:
    """SUMO's ``netconvert`` binary is callable (the OSM import path)."""
    with _stdout_to_stderr():
        path = _netconvert_path()
    if path is None:
        return Check(
            "netconvert",
            "FAIL",
            "not found in the eclipse-sumo wheel or on PATH",
            "`uv sync --all-packages --dev` reinstalls the eclipse-sumo wheel that ships it",
        )
    return Check("netconvert", "PASS", path, "")


def check_redis_server(queue_kind: str | None = None) -> Check:
    """``redis-server`` on PATH — needed only by the ``redis`` queue.

    Args:
        queue_kind: The configured queue, used only to word the message.
    """
    path = shutil.which("redis-server")
    if path is not None:
        return Check("redis_server", "PASS", path, "")
    configured = f"FLOWSTATE_QUEUE={queue_kind}" if queue_kind else "the configured queue"
    return Check(
        "redis_server",
        "WARN",
        f"not on PATH ({configured})",
        "only the redis queue needs it: run with FLOWSTATE_QUEUE=inline, or use "
        "`docker compose up` which brings its own Redis",
    )


def check_results_root(results_dir: Path) -> Check:
    """The results root exists (or can be created) and is writable."""
    problem = _writable(results_dir)
    if problem is None:
        return Check("results_root", "PASS", f"{results_dir} writable", "")
    return Check(
        "results_root",
        "FAIL",
        f"{results_dir}: {problem}",
        "point FLOWSTATE_RESULTS_DIR at a writable directory (every run artifact, "
        "upload and metadata.db lives under it)",
    )


def check_data_roots(roots: Sequence[Path]) -> Check:
    """Every allow-listed data root is writable (uploads, results, data dir)."""
    problems = [f"{root} ({problem})" for root in roots if (problem := _writable(root)) is not None]
    listed = ", ".join(str(root) for root in roots) or "none"
    if problems:
        return Check(
            "data_roots",
            "FAIL",
            "; ".join(problems),
            "these are the only roots a scenario or calibration may read from "
            "(FLOWSTATE_RESULTS_DIR, FLOWSTATE_DATA_DIR); make them writable",
        )
    return Check("data_roots", "PASS", listed, "")


def check_scenarios(scenarios_dir: Path) -> tuple[Check, Check]:
    """Every shipped preset parses, and the files it names exist.

    Args:
        scenarios_dir: Directory of preset YAMLs (``settings.scenarios_dir``).

    Returns:
        ``(scenarios, scenario_files)`` — the parse result and the
        referenced-file result. A preset that does not parse is a FAIL (the
        repository is broken); a preset whose OSM extract or calibration
        artifact is absent is a WARN (that one preset cannot run; the ring and
        the synthetic corridor still can).
    """
    from flowstate_core.config import ScenarioConfig

    paths = sorted(scenarios_dir.glob("*.yaml"))
    if not paths:
        broken_dir = Check(
            "scenarios",
            "FAIL",
            f"no *.yaml under {scenarios_dir}",
            "point FLOWSTATE_SCENARIOS_DIR at the repository's scenarios/ directory",
        )
        return broken_dir, Check("scenario_files", "WARN", "no presets to check", "see above")

    broken: list[str] = []
    missing: list[str] = []
    n_refs = 0
    for path in paths:
        try:
            cfg = ScenarioConfig.from_yaml(path)
        except Exception as exc:
            broken.append(f"{path.name} ({type(exc).__name__})")
            continue
        for ref in _referenced_files(cfg):
            n_refs += 1
            if not _resolve_ref(ref).is_file():
                missing.append(f"{path.name} -> {ref}")

    if broken:
        parse = Check(
            "scenarios",
            "FAIL",
            f"{len(paths) - len(broken)}/{len(paths)} parse; broken: " + ", ".join(broken),
            'run `uv run --no-sync python -c "from flowstate_core.config import ScenarioConfig; '
            "ScenarioConfig.from_yaml('<file>')\"` for the full validation error",
        )
    else:
        parse = Check("scenarios", "PASS", f"{len(paths)}/{len(paths)} parse", "")

    if missing:
        files = Check(
            "scenario_files",
            "WARN",
            f"{len(missing)}/{n_refs} referenced files absent: " + ", ".join(missing),
            "those presets cannot run until the artifact or OSM extract is present "
            "(data/ payloads are not in the repository); ring_sugiyama and corridor_10km "
            "need none",
        )
    else:
        files = Check("scenario_files", "PASS", f"{n_refs}/{n_refs} referenced files present", "")
    return parse, files


def _referenced_files(cfg: Any) -> list[str]:
    """Server-side files a scenario config names (OSM extract, calibrations)."""
    refs: list[str] = []
    osm_file = getattr(cfg.network, "osm_file", None)
    if osm_file:
        refs.append(str(osm_file))
    if cfg.fleet.idm_calibration:
        refs.append(str(cfg.fleet.idm_calibration))
    heavy = cfg.fleet.heavy
    if heavy is not None and heavy.idm_calibration:
        refs.append(str(heavy.idm_calibration))
    if cfg.fd_calibration:
        refs.append(str(cfg.fd_calibration))
    return refs


def _resolve_ref(ref: str) -> Path:
    """Resolve a scenario file reference as the runner does (cwd, else repo)."""
    path = Path(ref)
    if path.is_file():
        return path
    return REPO_ROOT / ref


def check_osm_extracts(osm_dir: Path | None = None) -> Check:
    """OSM extracts are present for the corridor presets."""
    directory = osm_dir if osm_dir is not None else REPO_ROOT / "data" / "osm"
    extracts = sorted(directory.glob("*.osm")) if directory.is_dir() else []
    if extracts:
        return Check(
            "osm_extracts",
            "PASS",
            f"{len(extracts)} under {directory}: " + ", ".join(p.name for p in extracts[:4]),
            "",
        )
    return Check(
        "osm_extracts",
        "WARN",
        f"none under {directory}",
        "only the OSM-imported corridors need them: fetch one with "
        "`uv run --no-sync python scripts/onboard_corridor.py --bbox ... --download`",
    )


def check_disk(path: Path) -> Check:
    """Free space at the results root."""
    try:
        usage = shutil.disk_usage(path if path.exists() else path.parent)
    except OSError as exc:
        return Check("disk", "WARN", f"unreadable: {exc}", "check the results root's filesystem")
    free_gb = usage.free / _GB
    detail = f"{free_gb:.1f} GB free at {path}"
    if free_gb < MIN_FREE_DISK_GB:
        return Check(
            "disk",
            "WARN",
            detail,
            f"under {MIN_FREE_DISK_GB:g} GB: a 20-seed micro run writes hundreds of MB of "
            f"trajectories — free space or move FLOWSTATE_RESULTS_DIR",
        )
    return Check("disk", "PASS", detail, "")


def check_memory() -> Check:
    """Total physical memory (SUMO replicates run one process per core)."""
    total = _total_memory_bytes()
    if total is None:
        return Check("memory", "WARN", "unreadable", "check memory manually; ~1 GB per replicate")
    total_gb = total / _GB
    detail = f"{total_gb:.1f} GB total"
    if total_gb < MIN_TOTAL_MEMORY_GB:
        return Check(
            "memory",
            "WARN",
            detail,
            f"under {MIN_TOTAL_MEMORY_GB:g} GB: keep replicates small (one SUMO process per "
            f"core, ~1 GB per worker) or run sweeps on a bigger host",
        )
    return Check("memory", "PASS", detail, "")


def run_smoke(
    scenario: Path | None = None, duration_s: float = SMOKE_DURATION_S, seed: int = 42
) -> Check:
    """Run a few simulated seconds of the ring through the real engine.

    Args:
        scenario: Scenario YAML (default: the repository's
            ``scenarios/ring_sugiyama.yaml``).
        duration_s: Simulated duration to override the scenario's with.
        seed: Replicate seed.

    Returns:
        A ``smoke`` check reporting simulation steps per wall-clock second and
        the real-time factor, or FAIL with the exception type and message.
    """
    path = scenario if scenario is not None else SMOKE_SCENARIO
    if not path.is_file():
        return Check(
            "smoke",
            "FAIL",
            f"{path} not found",
            "run this from the repository root (the smoke uses scenarios/ring_sugiyama.yaml)",
        )
    label = path.name
    try:
        with _stdout_to_stderr():
            from flowstate_core.config import ScenarioConfig
            from microsim.runner import run_micro

            cfg = ScenarioConfig.from_yaml(path)
            cfg = cfg.model_copy(
                update={"sim": cfg.sim.model_copy(update={"duration_s": float(duration_s)})}
            )
            steps = round(cfg.sim.duration_s / cfg.sim.step_length_s)
            with tempfile.TemporaryDirectory(prefix="flowstate-doctor-") as tmp:
                t0 = time.perf_counter()
                paths = run_micro(cfg, seed=seed, out_dir=tmp)
                wall = time.perf_counter() - t0
                meta = json.loads(Path(paths.meta).read_text())
    except Exception as exc:
        return Check(
            "smoke",
            "FAIL",
            f"{label}: {type(exc).__name__}: {exc}",
            "fix the FAILs above first; if they all pass, run "
            "`uv run --no-sync pytest tests/test_microsim -q` for the full engine battery",
        )
    rtf = meta.get("realtime_factor")
    steps_per_s = steps / wall if wall > 0 else float("inf")
    rtf_text = f"{rtf:.0f}x real time" if isinstance(rtf, int | float) else "real-time factor n/a"
    return Check(
        "smoke",
        "PASS",
        f"{cfg.name} {cfg.sim.duration_s:g} sim-s in {wall:.2f} s "
        f"({steps_per_s:.0f} steps/s, {rtf_text})",
        "",
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def run_checks(*, with_smoke: bool = True) -> list[Check]:
    """Run every check in table order.

    Args:
        with_smoke: Include the ring smoke run (``--no-sim`` turns it off).

    Returns:
        The checks, in the order they are printed.
    """
    checks: list[Check] = [check_python()]

    settings: Any = None
    try:
        from api.settings import load_settings

        settings = load_settings()
    except Exception as exc:
        print(f"settings error: {type(exc).__name__}: {exc}", file=sys.stderr)
    checks.append(check_settings(settings))

    checks.append(check_sumo_packages())
    checks.append(check_sumo_version())
    checks.append(check_pyarrow())
    checks.append(check_netconvert())
    checks.append(check_redis_server(getattr(settings, "queue_kind", None)))

    if settings is not None:
        checks.append(check_results_root(Path(settings.results_dir)))
        checks.append(check_data_roots([Path(p) for p in settings.data_roots]))
        checks.extend(check_scenarios(Path(settings.scenarios_dir)))
        checks.append(check_osm_extracts())
        checks.append(check_disk(Path(settings.results_dir)))
    else:
        checks.extend(check_scenarios(REPO_ROOT / "scenarios"))
        checks.append(check_osm_extracts())
        checks.append(check_disk(REPO_ROOT))
    checks.append(check_memory())

    if with_smoke:
        checks.append(run_smoke())
    return checks


def format_table(checks: Sequence[Check]) -> str:
    """Render the checks as the printed table plus a fix line per problem.

    Args:
        checks: Results in print order.

    Returns:
        The full report text (no trailing newline).
    """
    name_width = max((len(c.name) for c in checks), default=4)
    lines = [f"{'CHECK'.ljust(name_width)}  STATUS  DETAIL"]
    for check in checks:
        lines.append(f"{check.name.ljust(name_width)}  {check.status:<6}  {check.detail}")
        if check.status != "PASS" and check.fix:
            lines.append(f"{' ' * name_width}          fix: {check.fix}")
    counts = summarize(checks)
    lines.append("")
    lines.append(
        f"{counts['PASS']} pass, {counts['WARN']} warn, {counts['FAIL']} fail — "
        + (
            "this machine can run FlowState (docs/QUICKSTART.md)"
            if counts["FAIL"] == 0
            else "fix the FAIL rows above, then re-run this script"
        )
    )
    return "\n".join(lines)


def summarize(checks: Sequence[Check]) -> dict[str, int]:
    """Count checks by status.

    Args:
        checks: Results to count.

    Returns:
        Counts keyed by ``"PASS"``, ``"WARN"`` and ``"FAIL"``.
    """
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for check in checks:
        counts[check.status] += 1
    return counts


def to_json(checks: Sequence[Check]) -> str:
    """Render the checks as one machine-readable JSON object.

    Args:
        checks: Results in print order.

    Returns:
        Indented JSON with ``ok``, ``counts`` and the per-check list.
    """
    counts = summarize(checks)
    payload: dict[str, Any] = {
        "ok": counts["FAIL"] == 0,
        "counts": counts,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "checks": [asdict(check) for check in checks],
    }
    return json.dumps(payload, indent=2)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Arguments without the program name (defaults to ``sys.argv``).

    Returns:
        1 when any check FAILed, else 0.
    """
    parser = argparse.ArgumentParser(
        prog="doctor.py",
        description="FlowState preflight: what works on this machine, and how to fix what does not.",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output on stdout")
    parser.add_argument("--no-sim", action="store_true", help="skip the 10-second ring smoke run")
    args = parser.parse_args(argv)

    checks = run_checks(with_smoke=not args.no_sim)
    print(to_json(checks) if args.json else format_table(checks))
    return 1 if summarize(checks)["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
