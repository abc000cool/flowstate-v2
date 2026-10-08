"""scripts/i24_build_replica.py's C7b options on synthetic inputs (docs/I24_CONSISTENCY_C7B.md).

``--demand-coverage recommended`` (the corrected arm's inflows over the coverage the validator's
link-flow target divides by) and ``--insertion-shift-s`` (the mainline steps after the first moved
earlier by the free-flow time from the entry to the count section). The I-24 MOTION table, the OSM
network and SUMO are never touched: ``main()`` runs with the stand-ins of
``test_i24_build_replica_ramp_through`` (synthetic crossings, a linear geometry with the count section
at sim x 2,452.5 m, a fleet whose mean v0 is 30 m/s) in a temporary repository root, plus a synthetic
``artifacts/i24_coverage.json``. Pinned:

* with every C7b option at its default the builder writes what it wrote at the commit before C7b, byte
  for byte (and ``--coverage-estimator`` keeps its arithmetic);
* ``--demand-coverage recommended`` divides the corrected arm's mainline and on-ramp inflows by the
  recommended coverage, the arithmetic the C7b harness derives arms with, records it and refuses a
  coverage artifact of another recording, a non-default ``--coverage-estimator`` and a missing fleet;
* ``--insertion-shift-s`` moves only the mainline step times of both arms, by ``distance / v0`` to
  0.1 s (``free_flow``) or by the given value, and records it;
* each writes a new family: the suffix token is ``rc`` + ``c`` + ``s``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
import yaml

from tests.test_scripts import test_i24_build_replica_ramp_through as rt

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
HARNESS = REPO_ROOT / "artifacts" / "i24_consistency_2026-10-07" / "harness" / "corridor_c7b.py"
#: The builder's last commit before C7b: every default must write what it wrote.
PRE_C7B_COMMIT = "32e061485483474cf801653e95bb4faa3b2cc46f"

br = rt.br
#: The stand-ins' geometry: sim x of data x = 200 m, and the stand-in fleet's mean v0.
COUNT_SECTION_SIM_X = 2256.5 + 0.98 * 200.0
V0 = 30.0
#: Recommended coverage per 15-min window, every one above the stand-in's equilibrium one.
RECOMMENDED = [0.60 + 0.012 * k for k in range(8)]


def _harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("c7b_harness_for_builder_tests", HARNESS)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _coverage_artifact(data_hash: str = rt.DATA_HASH) -> dict[str, Any]:
    windows = [
        {"t_lo_s": br.T_STUDY_LO_S + 900.0 * k, "pooled": {"recommended_filled": c}}
        for k, c in enumerate(RECOMMENDED)
    ]
    return {"data_hash": data_hash, "created_at": "2026-09-03T00:00:00Z", "windows": windows}


def _run(
    mod: ModuleType,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    *,
    count_check: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
    fleet: bool = True,
) -> dict[str, bytes]:
    """``rt._run`` with the coverage artifact (and optionally no fleet) installed first."""
    rt._install(mod, root, monkeypatch)
    if not fleet:
        (root / mod.FLEET_ARTIFACT).unlink()
    if count_check is not None:
        (root / "artifacts" / "i24_count_consistency.json").write_text(json.dumps(count_check))
    if coverage is not None:
        (root / "artifacts" / "i24_coverage.json").write_text(json.dumps(coverage))
    monkeypatch.setattr(sys, "argv", ["i24_build_replica.py", *argv])
    mod.main()
    suffix = "_" + argv[argv.index("--suffix") + 1] if "--suffix" in argv else ""
    paths = [
        f"scenarios/i24_replica{suffix}.yaml",
        f"artifacts/demand_i24{suffix}.json",
        f"artifacts/i24_replica_inputs{suffix}.json",
    ]
    if fleet:
        paths.insert(1, f"scenarios/i24_replica{suffix}_corrected.yaml")
    return {p: (root / p).read_bytes() for p in paths}


RC = ["--ramp-through-traffic", "exclude"]
DC = ["--demand-coverage", "recommended"]
SHIFT = ["--insertion-shift-s", "free_flow"]


def _family(code: str) -> list[str]:
    return ["--suffix", f"flow_{code}", *RC]


def _docs(out: dict[str, bytes], code: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        yaml.safe_load(out[f"scenarios/i24_replica_flow_{code}.yaml"]),
        yaml.safe_load(out[f"scenarios/i24_replica_flow_{code}_corrected.yaml"]),
    )


def _masked(doc: dict[str, Any], *, times: bool = False, values: bool = False) -> dict[str, Any]:
    """The document without its name and, optionally, without the inflows' times or values."""
    d = json.loads(json.dumps(doc))
    d.pop("name")
    streams = [d["network"]["inflow"]] + [
        r["inflow"] for r in d["network"]["ramps"] if r["kind"] == "on"
    ]
    for s in streams:
        for step in s:
            if times:
                step[0] = None
            if values:
                step[1] = None
    return d


# --- defaults ------------------------------------------------------------------------------------


def test_the_defaults_match_the_builder_before_c7b_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipped where the commit is not in the clone (a shallow checkout); move the pin if the default
    output is ever changed on purpose."""
    try:
        res = subprocess.run(
            ["git", "show", f"{PRE_C7B_COMMIT}:scripts/i24_build_replica.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        pytest.skip("git is not available")
    if res.returncode != 0:
        pytest.skip(f"commit {PRE_C7B_COMMIT[:12]} is not in this clone")
    old = ModuleType("flowstate_i24_build_replica_pre_c7b")
    old.__file__ = str(SCRIPTS / "i24_build_replica.py")
    exec(compile(res.stdout, "i24_build_replica.py@pre-C7b", "exec"), old.__dict__)
    assert not hasattr(old, "shifted_steps")
    cases: list[tuple[list[str], dict[str, Any]]] = [
        ([], {}),
        (["--suffix", "flow", "--osm", "corrected", "--lc-strategic-ramp", "1"], {}),
        (_family("rc"), {"count_check": rt._artifact()}),
        (
            ["--suffix", "rec", "--coverage-estimator", "recommended"],
            {"coverage": _coverage_artifact()},
        ),
    ]
    for i, (argv, extra) in enumerate(cases):
        before = _run(old, tmp_path / f"old{i}", monkeypatch, argv, **extra)
        after = _run(
            br,
            tmp_path / f"new{i}",
            monkeypatch,
            [*argv, "--demand-coverage", "equilibrium"],
            **extra,
        )
        assert after == before, argv


# --- --demand-coverage recommended ---------------------------------------------------------------


def test_demand_coverage_divides_the_corrected_inflows_by_the_recommended_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _harness()
    base = _run(br, tmp_path / "rc", monkeypatch, _family("rc"), count_check=rt._artifact())
    out = _run(
        br,
        tmp_path / "rcc",
        monkeypatch,
        [*_family("rcc"), *DC],
        count_check=rt._artifact(),
        coverage=_coverage_artifact(),
    )
    raw_b, cor_b = _docs(base, "rc")
    raw, cor = _docs(out, "rcc")
    # the tracked arm is untouched; the corrected arm differs only in its inflow values
    assert _masked(raw) == _masked(raw_b)
    assert _masked(cor, values=True) == _masked(cor_b, values=True)
    assert [q for _, q in cor["network"]["inflow"]] == h.mainline_rates(
        rt.MAIN.tolist(), RECOMMENDED
    )
    for spec in cor["network"]["ramps"]:
        if spec["kind"] == "on":
            net = (rt.COUNTS[spec["name"]] - rt.PRIOR[spec["name"]]).tolist()
            assert [q for _, q in spec["inflow"]] == h.ramp_rates(net, RECOMMENDED)
    # the equilibrium family divides by the stand-in's equilibrium coverage, the same arithmetic
    assert [q for _, q in cor_b["network"]["inflow"]] == h.mainline_rates(
        rt.MAIN.tolist(), rt.COVERAGE
    )
    inputs = json.loads(out["artifacts/i24_replica_inputs_flow_rcc.json"])
    rows = inputs["coverage"]["rows"]
    assert [r["coverage_used"] for r in rows] == RECOMMENDED
    assert [r["coverage_equilibrium"] for r in rows] == rt.COVERAGE
    assert inputs["coverage"]["source"]["estimator"] == "recommended_filled"
    assert "recommended_filled" in inputs["coverage"]["method"]
    block = inputs["demand_coverage"]
    sha = hashlib.sha256(
        (tmp_path / "rcc" / "artifacts" / "i24_coverage.json").read_bytes()
    ).hexdigest()
    assert block["mode"] == "recommended" and block["artifact_sha256"] == sha
    assert block["artifact_data_hash"] == rt.DATA_HASH
    assert [w["c_used"] for w in block["per_window"]] == RECOMMENDED
    assert list(inputs)[-1] == "demand_coverage"  # added last; the earlier keys keep their places
    head = rt._header(out["scenarios/i24_replica_flow_rcc_corrected.yaml"])
    assert "C7b COVERAGE-CONSISTENT DEMAND" in head and "apparent tracking coverage" not in head
    assert "C7b" not in rt._header(out["scenarios/i24_replica_flow_rcc.yaml"])


@pytest.mark.parametrize(
    ("argv", "extra", "message"),
    [
        ([*_family("rc"), *DC], {}, "ending in 'rcc'"),
        (["--suffix", "flow_rcc", *DC], {}, "ending in 'c'"),
        (
            [*_family("rcc"), *DC, "--coverage-estimator", "gap_mixture"],
            {},
            "leave --coverage-estimator at its default",
        ),
        (
            [*_family("rcc"), *DC],
            {"coverage": _coverage_artifact("cd" * 32)},
            "made from data hash cdcdcdcdcdcd",
        ),
        ([*_family("rcc"), *DC, "--allow-missing-fleet"], {"fleet": False}, "needs"),
    ],
)
def test_demand_coverage_refuses_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    extra: dict[str, Any],
    message: str,
) -> None:
    kw: dict[str, Any] = {"count_check": rt._artifact(), "coverage": _coverage_artifact(), **extra}
    with pytest.raises(SystemExit, match=message):
        _run(br, tmp_path, monkeypatch, argv, **kw)
    assert not list((tmp_path / "scenarios").iterdir())


# --- --insertion-shift-s -------------------------------------------------------------------------


def test_free_flow_shift_moves_only_the_mainline_step_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = _harness()
    base = _run(br, tmp_path / "rc", monkeypatch, _family("rc"), count_check=rt._artifact())
    out = _run(
        br, tmp_path / "rcs", monkeypatch, [*_family("rcs"), *SHIFT], count_check=rt._artifact()
    )
    shift = br.free_flow_shift_s(COUNT_SECTION_SIM_X, V0)
    assert shift == round(COUNT_SECTION_SIM_X / V0, 1)
    for new, old in zip(_docs(out, "rcs"), _docs(base, "rc"), strict=True):
        times = [t for t, _ in old["network"]["inflow"]]
        assert [t for t, _ in new["network"]["inflow"]] == h.shifted_times(times, shift)
        assert new["network"]["inflow"][0][0] == 0.0
        assert [t for t, _ in new["network"]["inflow"]][1] == round(900.0 - shift, 6)
        # values, on-ramps, exit fractions and boundary unchanged
        assert [q for _, q in new["network"]["inflow"]] == [q for _, q in old["network"]["inflow"]]
        assert new["network"]["ramps"] == old["network"]["ramps"]
        assert new["network"]["boundary"] == old["network"]["boundary"]
        assert _masked(new, times=True) == _masked(old, times=True)
    inputs = json.loads(out["artifacts/i24_replica_inputs_flow_rcs.json"])
    block = inputs["insertion_shift"]
    assert block["mode"] == "free_flow" and block["shift_s"] == shift
    assert block["distance_m"] == pytest.approx(COUNT_SECTION_SIM_X, rel=1e-12)
    assert block["v0_ms"] == V0 and block["count_x_m"] == 200.0
    assert (
        block["fleet_artifact_sha256"]
        == hashlib.sha256((tmp_path / "rcs" / br.FLEET_ARTIFACT).read_bytes()).hexdigest()
    )
    assert block["inflow_step_times_sim"] == [
        t
        for t, _ in yaml.safe_load(out["scenarios/i24_replica_flow_rcs.yaml"])["network"]["inflow"]
    ]
    assert [t for t, _ in inputs["mainline"]["inflow_steps_sim"]] == block["inflow_step_times_sim"]
    assert list(inputs)[-1] == "insertion_shift"
    demand = json.loads(out["artifacts/demand_i24_flow_rcs.json"])
    assert [s[0] for s in demand["steps"]] == block["inflow_step_times_sim"]
    assert f"every step after the first starts {shift:g} s earlier" in demand["source"]
    for f in (
        "scenarios/i24_replica_flow_rcs.yaml",
        "scenarios/i24_replica_flow_rcs_corrected.yaml",
    ):
        head = rt._header(out[f])
        assert "C7b INSERTION SHIFT" in head and f"start {shift:g} s before" in head


def test_an_explicit_shift_is_applied_and_labelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _run(
        br,
        tmp_path,
        monkeypatch,
        [*_family("rcs"), "--insertion-shift-s", "60"],
        count_check=rt._artifact(),
    )
    raw, _ = _docs(out, "rcs")
    assert [t for t, _ in raw["network"]["inflow"]][:3] == [0.0, 840.0, 1140.0]
    block = json.loads(out["artifacts/i24_replica_inputs_flow_rcs.json"])["insertion_shift"]
    assert block["mode"] == "explicit" and block["shift_s"] == 60.0
    assert "given explicitly" in rt._header(out["scenarios/i24_replica_flow_rcs.yaml"])


def test_both_corrections_compose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kw: dict[str, Any] = {"count_check": rt._artifact(), "coverage": _coverage_artifact()}
    rcc = _run(br, tmp_path / "c", monkeypatch, [*_family("rcc"), *DC], **kw)
    rcs = _run(br, tmp_path / "s", monkeypatch, [*_family("rcs"), *SHIFT], **kw)
    both = _run(br, tmp_path / "cs", monkeypatch, [*_family("rccs"), *DC, *SHIFT], **kw)
    _, cor_c = _docs(rcc, "rcc")
    _, cor_s = _docs(rcs, "rcs")
    _, cor_cs = _docs(both, "rccs")
    net = cor_cs["network"]
    assert [q for _, q in net["inflow"]] == [q for _, q in cor_c["network"]["inflow"]]
    assert [t for t, _ in net["inflow"]] == [t for t, _ in cor_s["network"]["inflow"]]
    assert net["ramps"] == cor_c["network"]["ramps"]
    inputs = json.loads(both["artifacts/i24_replica_inputs_flow_rccs.json"])
    assert list(inputs)[-2:] == ["demand_coverage", "insertion_shift"]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        ([*_family("rc"), *SHIFT], "ending in 'rcs'"),
        ([*_family("rcs"), *DC, *SHIFT], "ending in 'rccs'"),
        (["--suffix", "flow_rcs", *SHIFT], "ending in 's'"),
        ([*_family("rcs"), "--insertion-shift-s", "0"], r"must lie in \(0, 300\) s"),
        ([*_family("rcs"), "--insertion-shift-s", "300"], r"must lie in \(0, 300\) s"),
        ([*_family("rcs"), "--insertion-shift-s", "-5"], r"must lie in \(0, 300\) s"),
        ([*_family("rcs"), "--insertion-shift-s", "soon"], "a number of seconds or 'free_flow'"),
        (
            [*_family("rcs"), *SHIFT, "--allow-missing-fleet"],
            "needs the fleet's mean v0",
        ),
    ],
)
def test_the_shift_refuses_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], message: str
) -> None:
    fleet = "--allow-missing-fleet" not in argv
    with pytest.raises(SystemExit, match=message):
        _run(br, tmp_path, monkeypatch, argv, count_check=rt._artifact(), fleet=fleet)
    assert not list((tmp_path / "scenarios").iterdir())


# --- helpers ---------------------------------------------------------------------------------------


def test_family_code_and_the_shift_helpers() -> None:
    assert br.family_code(exclude=True, demand_coverage=False, shift=False) == "rc"
    assert br.family_code(exclude=True, demand_coverage=True, shift=True) == "rccs"
    assert br.family_code(exclude=False, demand_coverage=False, shift=True) == "s"
    assert br.family_code(exclude=False, demand_coverage=False, shift=False) == ""
    assert br.free_flow_shift_s(2452.5043879248387, 32.39960289263415) == 75.7
    steps = [(0.0, 1.0), (900.0, 2.0), (1200.0, 3.0)]
    assert br.shifted_steps(steps, 75.7) == [(0.0, 1.0), (824.3, 2.0), (1124.3, 3.0)]
    for bad in (0.0, 300.0, float("nan")):
        with pytest.raises(ValueError, match="insertion shift"):
            br.shifted_steps(steps, bad)
    with pytest.raises(ValueError, match="not positive"):
        br.free_flow_shift_s(0.0, 30.0)
    with pytest.raises(ValueError, match="not positive"):
        br.free_flow_shift_s(100.0, 0.0)
    assert np.isclose(br.free_flow_shift_s(COUNT_SECTION_SIM_X, V0), 81.8)
