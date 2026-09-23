"""Tests for ``scripts/doctor.py`` — the first-run preflight (docs/QUICKSTART.md).

The point of the doctor is that a tester who has never seen this repository
learns, in one command, whether the machine can run it and what to do when it
cannot. So the tests cover both halves: the happy path (every check reports,
exit 0) and the failure paths with the environment broken on purpose — a
missing binary, the excluded pyarrow release, a scenario that does not parse,
an unwritable results root, a bad ``FLOWSTATE_*`` value. The engine smoke run
is a real SUMO simulation and carries the repository's ``integration`` marker,
like the rest of ``tests/test_microsim``.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCTOR_PATH = REPO_ROOT / "scripts" / "doctor.py"


def _load_doctor() -> ModuleType:
    """Import ``scripts/doctor.py`` by path (``scripts/`` is not a package).

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location("flowstate_doctor", DOCTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: ``@dataclass`` resolves its annotations
    # through ``sys.modules[cls.__module__]`` (the documented importlib
    # recipe). No ``sys.path`` is touched — CLAUDE.md §12.7.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


doctor = _load_doctor()

#: Every check the table must carry when settings resolve (order-independent).
EXPECTED_CHECKS = {
    "python",
    "settings",
    "sumo_packages",
    "sumo_version",
    "pyarrow",
    "netconvert",
    "redis_server",
    "results_root",
    "data_roots",
    "scenarios",
    "scenario_files",
    "osm_extracts",
    "disk",
    "memory",
}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Point the service settings at a temporary results root.

    Keeps the doctor's writability probes out of the repository's ``runs/``
    and makes the environment deterministic whatever the developer has
    exported.

    Yields:
        The temporary results root.
    """
    for name in (
        "FLOWSTATE_QUEUE",
        "FLOWSTATE_DATA_DIR",
        "FLOWSTATE_SCENARIOS_DIR",
        "FLOWSTATE_API_KEY",
        "FLOWSTATE_API_KEYS",
    ):
        monkeypatch.delenv(name, raising=False)
    results = tmp_path / "runs"
    monkeypatch.setenv("FLOWSTATE_RESULTS_DIR", str(results))
    yield results


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_no_sim_run_reports_every_check_and_exits_zero(
    clean_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--no-sim`` prints the full table, skips the simulation, exits 0."""
    code = doctor.main(["--no-sim"])
    out = capsys.readouterr().out

    assert code == 0, out
    assert "CHECK" in out and "STATUS" in out
    for name in EXPECTED_CHECKS:
        assert name in out
    assert "smoke" not in out
    assert " fail — " in out or "0 fail" in out


def test_json_output_is_machine_readable(
    clean_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--json`` writes one parseable object on stdout, banners on stderr."""
    code = doctor.main(["--json", "--no-sim"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["ok"] is True
    assert payload["counts"]["FAIL"] == 0
    names = {entry["name"] for entry in payload["checks"]}
    assert EXPECTED_CHECKS <= names
    assert all({"name", "status", "detail", "fix"} <= set(entry) for entry in payload["checks"])
    assert all(entry["status"] in {"PASS", "WARN", "FAIL"} for entry in payload["checks"])


def test_pins_are_read_from_the_repository() -> None:
    """The enforced SUMO pin comes from the workspace, not from a constant."""
    pin, source = doctor._sumo_pin()
    assert pin == "1.27.1"
    assert source.endswith("pyproject.toml")
    assert doctor._pyarrow_excluded() == "24.0.0"


def test_format_table_prints_a_fix_only_for_problems() -> None:
    """Every non-PASS row carries its one-line fix; PASS rows stay quiet."""
    table = doctor.format_table(
        [
            doctor.Check("good", "PASS", "fine", "never shown"),
            doctor.Check("soft", "WARN", "degraded", "do the warn thing"),
            doctor.Check("hard", "FAIL", "broken", "do the fail thing"),
        ]
    )
    assert "never shown" not in table
    assert "fix: do the warn thing" in table
    assert "fix: do the fail thing" in table
    assert "1 pass, 1 warn, 1 fail" in table


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_missing_netconvert_is_a_failure(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No SUMO binary ⇒ FAIL, a fix naming the sync, exit 1."""
    monkeypatch.setattr(doctor, "_netconvert_path", lambda: None)

    code = doctor.main(["--no-sim"])
    out = capsys.readouterr().out

    assert code == 1
    assert doctor.check_netconvert().status == "FAIL"
    assert "netconvert" in out and "uv sync" in out


def test_excluded_pyarrow_version_is_a_failure(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """pyarrow 24.0.0 (the libsumo/Arrow clash) fails with the reason."""
    real = doctor._dist_version
    monkeypatch.setattr(
        doctor, "_dist_version", lambda name: "24.0.0" if name == "pyarrow" else real(name)
    )

    check = doctor.check_pyarrow()
    assert check.status == "FAIL"
    assert "24.0.0" in check.detail and "libsumo" in check.fix

    assert doctor.main(["--no-sim"]) == 1
    assert "24.0.0 is excluded" in capsys.readouterr().out


def test_absent_pyarrow_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine with no pyarrow at all cannot write a trajectory."""
    monkeypatch.setattr(doctor, "_dist_version", lambda name: None)
    check = doctor.check_pyarrow()
    assert check.status == "FAIL"
    assert check.detail == "not installed"


def test_wrong_sumo_version_is_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unpinned SUMO wheel moves every golden, so it is a hard failure."""
    monkeypatch.setattr(
        doctor, "_dist_version", lambda name: "1.26.0" if name == "libsumo" else "1.27.1"
    )
    check = doctor.check_sumo_version()
    assert check.status == "FAIL"
    assert "libsumo=1.26.0" in check.detail
    assert "1.27.1" in check.fix


def test_missing_redis_server_only_warns(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The inline queue needs no Redis, so its absence must not fail a run."""
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)

    check = doctor.check_redis_server("inline")
    assert check.status == "WARN"
    assert "FLOWSTATE_QUEUE=inline" in check.fix or "inline" in check.fix

    assert doctor.main(["--no-sim"]) == 0
    assert "redis_server" in capsys.readouterr().out


def test_unparseable_scenario_is_a_failure_naming_the_file(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A preset that does not validate fails, and the row names it."""
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    shutil.copy(REPO_ROOT / "scenarios" / "ring_sugiyama.yaml", scenarios / "ring_sugiyama.yaml")
    (scenarios / "broken.yaml").write_text("name: broken\ntier: micro\nnetwork: {kind: ring}\n")
    monkeypatch.setenv("FLOWSTATE_SCENARIOS_DIR", str(scenarios))

    parse, files = doctor.check_scenarios(scenarios)
    assert parse.status == "FAIL"
    assert "broken.yaml" in parse.detail
    assert "1/2 parse" in parse.detail
    assert files.status == "PASS"

    assert doctor.main(["--no-sim"]) == 1


def test_missing_referenced_artifact_warns(tmp_path: Path) -> None:
    """A preset whose artifact is absent warns — other presets still run."""
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    text = (REPO_ROOT / "scenarios" / "ring_sugiyama.yaml").read_text()
    (scenarios / "ring_missing_fd.yaml").write_text(
        text + "fd_calibration: artifacts/does_not_exist.json\n"
    )

    parse, files = doctor.check_scenarios(scenarios)
    assert parse.status == "PASS"
    assert files.status == "WARN"
    assert "does_not_exist.json" in files.detail


def test_empty_scenarios_directory_is_a_failure(tmp_path: Path) -> None:
    """Pointing FLOWSTATE_SCENARIOS_DIR at the wrong place is diagnosable."""
    parse, files = doctor.check_scenarios(tmp_path)
    assert parse.status == "FAIL"
    assert "FLOWSTATE_SCENARIOS_DIR" in parse.fix
    assert files.status == "WARN"


def test_uncreatable_results_root_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A results root that cannot be created fails with the OS reason."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    results = blocker / "runs"

    check = doctor.check_results_root(results)
    assert check.status == "FAIL"
    assert "cannot create" in check.detail
    assert "FLOWSTATE_RESULTS_DIR" in check.fix

    monkeypatch.setenv("FLOWSTATE_RESULTS_DIR", str(results))
    assert doctor.main(["--no-sim"]) == 1
    assert "results_root" in capsys.readouterr().out


def test_bad_queue_setting_fails_the_settings_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``load_settings`` raising is reported as a row, not as a traceback."""
    monkeypatch.setenv("FLOWSTATE_RESULTS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("FLOWSTATE_QUEUE", "rabbit")

    code = doctor.main(["--no-sim"])
    captured = capsys.readouterr()

    assert code == 1
    assert "settings" in captured.out
    assert "FLOWSTATE_" in captured.out
    assert "Traceback" not in captured.out
    assert "FLOWSTATE_QUEUE" in captured.err


def test_low_disk_and_low_memory_only_warn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Resource shortfalls are advisory: they slow a sweep, not a first run."""
    usage = SimpleNamespace(total=100 * 1024**3, used=99 * 1024**3, free=1 * 1024**3)
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage)
    disk = doctor.check_disk(tmp_path)
    assert disk.status == "WARN"
    assert "1.0 GB free" in disk.detail

    monkeypatch.setattr(doctor, "_total_memory_bytes", lambda: 4 * 1024**3)
    memory = doctor.check_memory()
    assert memory.status == "WARN"
    assert "4.0 GB total" in memory.detail


def test_missing_osm_extracts_only_warn(tmp_path: Path) -> None:
    """The ring and the synthetic corridor need no OSM extract."""
    check = doctor.check_osm_extracts(tmp_path / "osm")
    assert check.status == "WARN"
    assert "onboard_corridor.py" in check.fix


def test_missing_scenario_file_makes_the_smoke_fail_cleanly(tmp_path: Path) -> None:
    """No engine is started when the scenario is not there."""
    check = doctor.run_smoke(scenario=tmp_path / "absent.yaml")
    assert check.status == "FAIL"
    assert "not found" in check.detail


# ---------------------------------------------------------------------------
# The real thing (runs SUMO)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_smoke_runs_the_ring_through_sumo() -> None:
    """10 simulated seconds of ``ring_sugiyama`` really run here (~0.2 s)."""
    check = doctor.run_smoke(duration_s=10.0)
    assert check.status == "PASS", check.detail
    assert "ring_sugiyama" in check.detail
    assert "steps/s" in check.detail
