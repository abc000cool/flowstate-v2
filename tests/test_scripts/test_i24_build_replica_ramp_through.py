"""scripts/i24_build_replica.py --ramp-through-traffic (amendment B2) on synthetic inputs.

docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.3. The I-24 MOTION table, the OSM network
and SUMO are never touched: ``main()`` runs with its loaders, geometry,
coverage and boundary replaced by small synthetic stand-ins (module
attributes, patched per test) and writes into a temporary repository root.
Synthetic crossings: each vehicle is two samples straddling its count section
inside one 5-min window, so the builder's ``crossings_per_window`` returns the
planted counts exactly. The two tracked artifacts read at the end
(``artifacts/i24_count_consistency.json``, ``artifacts/i24_replica_inputs.json``)
are small JSON files, not trajectories.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from flowstate_core.config import ScenarioConfig, config_hash

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
#: The builder's last commit before amendment B2: ``keep`` must write what it wrote.
PRE_B2_COMMIT = "f439db5f02aaade043d810d5d59ffdf329ac78f2"


def _load() -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "flowstate_i24_build_replica", SCRIPTS / "i24_build_replica.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


br = _load()

DATA_HASH = "ab" * 32
N_WIN = 24
T_LO, WIN = br.T_STUDY_LO_S, br.WINDOW_S
W = np.arange(N_WIN)
MAIN = 30 + W % 4
#: Per ramp (builder order): counted crossings, then the two flag series. The
#: on-ramps' and off-ramps' flags differ in every window, so a test can tell
#: which series was subtracted.
COUNTS = {
    "Old Hickory Blvd on-ramp": 20 + W % 5,
    "Hickory Hollow Pkwy off-ramp": 10 + W % 3,
    "Hickory Hollow Pkwy on-ramp": 15 + W % 4,
    "Bell Road off-ramp (collector road)": 8 + W % 2,
}
PRIOR = {
    "Old Hickory Blvd on-ramp": W % 4,
    "Hickory Hollow Pkwy off-ramp": np.full(N_WIN, 7),
    "Hickory Hollow Pkwy on-ramp": 1 + W % 3,
    "Bell Road off-ramp (collector road)": np.full(N_WIN, 6),
}
LATER = {
    "Old Hickory Blvd on-ramp": np.full(N_WIN, 9),
    "Hickory Hollow Pkwy off-ramp": W % 3,
    "Hickory Hollow Pkwy on-ramp": np.full(N_WIN, 11),
    "Bell Road off-ramp (collector road)": W % 2 + 1,
}
COVERAGE = [0.50 + 0.01 * k for k in range(8)]


def _crossings(counts: np.ndarray, tag: str, x0: float, x1: float) -> pd.DataFrame:
    """Two samples per vehicle, at ``x0`` then ``x1``, inside its window (one crossing of
    every section in ``(x0, x1]``)."""
    rows = []
    for w, n in enumerate(counts):
        for k in range(int(n)):
            t = T_LO + w * WIN + 1.0 + k * (WIN - 2.0) / max(int(n), 1)
            rows += [(t - 0.2, f"{tag}-{w}-{k}", x0), (t, f"{tag}-{w}-{k}", x1)]
    return pd.DataFrame(rows, columns=["t", "veh_id", "x"])


def _main_frame() -> pd.DataFrame:
    # one jump over every mainline section (200, 3200, 4800 m): counted at each
    return _crossings(MAIN, "m", 0.0, 6000.0)


def _ramp_frame() -> pd.DataFrame:
    parts = [
        _crossings(COUNTS[r["name"]], f"r{i}", r["count_x_m"] - 1.0, r["count_x_m"] + 1.0)
        for i, r in enumerate(br.RAMPS)
    ]
    return pd.concat(parts, ignore_index=True)


class _FakeGeo:
    def __init__(self, edges: tuple[str, ...]) -> None:
        self.edge_ids = list(edges)
        self.offsets = [1000.0 * i for i in range(len(edges))]
        self.edge_lengths = [1000.0] * len(edges)
        self.chain_pos_at_mm_upstream = 2256.5
        self.slope_m_per_mile = 1609.344 * 0.98
        self.residual_rms_m = 3.0
        self.mm_chain_pos = {62.0: 3000.0, 61.0: 4577.0}
        self.ramp_chain_pos = {"Old Hickory Blvd on-ramp": 3187.5}

    def chain_pos_of_data_x(self, x: float) -> float:
        return self.chain_pos_at_mm_upstream + 0.98 * x

    def data_x_of_chain_pos(self, c: float) -> float:
        return (c - self.chain_pos_at_mm_upstream) / 0.98


def _artifact(**edits: Any) -> dict[str, Any]:
    """A count-consistency artifact consistent with the synthetic recording."""
    art: dict[str, Any] = {
        "kind": "observed",
        "created_at": "2026-10-07T14:45:19Z",
        "code": "synthetic",
        "data_hash": DATA_HASH,
        "parameters": {"period_s": [T_LO, br.T_STUDY_HI_S], "window_s": WIN},
        "checks": {"reproduces_committed_counts": True},
        "ramps": [
            {
                "name": r["name"],
                "kind": r["kind"],
                "count_x_m": r["count_x_m"],
                "counts_per_window": COUNTS[r["name"]].tolist(),
                "prior_mainline_per_window": PRIOR[r["name"]].tolist(),
                "later_mainline_per_window": LATER[r["name"]].tolist(),
            }
            for r in br.RAMPS
        ],
    }
    art.update(edits)
    return art


def _install(mod: ModuleType, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point ``mod.main()`` at ``root`` and replace every data, map and SUMO access."""
    for d in ("artifacts", "scenarios", "data/osm"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / mod.FLEET_ARTIFACT).write_text(json.dumps({"mean": {"v0": 30.0, "T": 1.3, "s0": 2.0}}))
    main_df, ramp_df = _main_frame(), _ramp_frame()
    net = SimpleNamespace(getEdge=lambda e: SimpleNamespace(getLaneNumber=lambda: 4))
    patches = {
        "REPO_ROOT": root,
        "OSM_FILE": root / "data" / "osm" / "i24_motion.osm",
        "CORRECTED_OSM_FILE": root / "data" / "osm" / "i24_motion_corrected.osm",
        "osm_import": lambda **kw: SimpleNamespace(net_path=root / "net.net.xml"),
        "sumolib": SimpleNamespace(net=SimpleNamespace(readNet=lambda p: net)),
        "read_projection": lambda p: None,
        "check_projection": lambda n, proj, osm: 0.123,
        "chain_geometry": lambda n, proj: _FakeGeo(mod.CORRIDOR_EDGES),
        "load_mainline": lambda **kw: main_df.copy(),
        "load_i24_parquet": lambda *a, **kw: ramp_df.copy(),
        "boundary_schedule": lambda t_lo, t_hi: [
            (t_lo + 30.0 * i, 12.0 + i % 5) for i in range(round((t_hi - t_lo) / 30.0))
        ],
        "coverage_factors": lambda t_lo, t_hi, span, idm: [
            {"t_lo_s": t_lo + 900.0 * k, "window": f"w{k}", "coverage": c, "coverage_used": c}
            for k, c in enumerate(COVERAGE)
        ],
        "data_hash": lambda: DATA_HASH,
        "subprocess": SimpleNamespace(
            run=lambda *a, **kw: SimpleNamespace(stdout="2026-10-07T12:00:00Z\n")
        ),
    }
    for name, value in patches.items():
        monkeypatch.setattr(mod, name, value)


def _run(
    mod: ModuleType,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    artifact: dict[str, Any] | None = None,
) -> dict[str, bytes]:
    """Run ``mod.main()`` with ``argv`` under ``root``; the four output files' bytes."""
    _install(mod, root, monkeypatch)
    if artifact is not None:
        (root / "artifacts" / "i24_count_consistency.json").write_text(json.dumps(artifact))
    monkeypatch.setattr(sys, "argv", ["i24_build_replica.py", *argv])
    mod.main()
    suffix = ""
    if "--suffix" in argv:
        suffix = "_" + argv[argv.index("--suffix") + 1]
    paths = (
        f"scenarios/i24_replica{suffix}.yaml",
        f"scenarios/i24_replica{suffix}_corrected.yaml",
        f"artifacts/demand_i24{suffix}.json",
        f"artifacts/i24_replica_inputs{suffix}.json",
    )
    return {p: (root / p).read_bytes() for p in paths}


def _header(text: bytes) -> str:
    lines = text.decode().splitlines(keepends=True)
    return "".join(ln for ln in lines if ln.startswith("#"))


def _values(series: list[list[float]]) -> list[float]:
    return [v for _, v in series]


EXCLUDE = ["--suffix", "flow_rc", "--ramp-through-traffic", "exclude"]


# --- keep is the builder as before ---------------------------------------------


def test_keep_is_the_default_byte_for_byte_and_never_reads_the_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = _run(br, tmp_path / "a", monkeypatch, [])
    keep = _run(
        br,
        tmp_path / "b",
        monkeypatch,
        ["--ramp-through-traffic", "keep", "--count-consistency", "missing/nowhere.json"],
    )
    assert default == keep
    inputs = json.loads(default["artifacts/i24_replica_inputs.json"])
    assert "ramp_through_traffic" not in inputs
    assert all("ramp_lane_crossings_corrected" not in r for r in inputs["ramps"])
    assert "AMENDMENT B2" not in _header(default["scenarios/i24_replica.yaml"])


def test_keep_matches_the_pre_b2_builder_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``keep`` writes exactly what the builder at PRE_B2_COMMIT wrote (same stand-ins).

    The old builder runs against today's packages, so the comparison isolates
    the builder's own change. Skipped where the commit is not in the clone (a
    shallow CI checkout). Move the pin if the default output is ever changed on
    purpose.
    """
    try:
        res = subprocess.run(
            ["git", "show", f"{PRE_B2_COMMIT}:scripts/i24_build_replica.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        pytest.skip("git is not available")
    if res.returncode != 0:
        pytest.skip(f"commit {PRE_B2_COMMIT[:12]} is not in this clone")
    old = ModuleType("flowstate_i24_build_replica_pre_b2")
    old.__file__ = str(SCRIPTS / "i24_build_replica.py")
    exec(compile(res.stdout, "i24_build_replica.py@pre-B2", "exec"), old.__dict__)
    assert not hasattr(old, "ramp_through_corrections")
    for argv in ([], ["--suffix", "flow", "--osm", "corrected", "--lc-strategic-ramp", "1"]):
        before = _run(old, tmp_path / "old", monkeypatch, argv)
        after = _run(br, tmp_path / "new", monkeypatch, [*argv, "--ramp-through-traffic", "keep"])
        assert after == before


# --- exclude ---------------------------------------------------------------------


def test_exclude_subtracts_the_flagged_series_of_each_ramp_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _run(br, tmp_path, monkeypatch, EXCLUDE, artifact=_artifact())
    raw = yaml.safe_load(out["scenarios/i24_replica_flow_rc.yaml"])
    cor = yaml.safe_load(out["scenarios/i24_replica_flow_rc_corrected.yaml"])
    for i, r in enumerate(br.RAMPS):
        name = r["name"]
        spec, spec_c = raw["network"]["ramps"][i], cor["network"]["ramps"][i]
        assert spec["name"] == name
        if r["kind"] == "on":
            net = COUNTS[name] - PRIOR[name]
            assert _values(spec["inflow"]) == [round(float(c / WIN), 6) for c in net]
            # coverage scaling comes after the subtraction (15-min windows: 3 per factor)
            assert _values(spec_c["inflow"]) == [
                round(round(float(c / WIN), 6) / COVERAGE[w // 3], 6) for w, c in enumerate(net)
            ]
            assert net.tolist() != (COUNTS[name] - LATER[name]).tolist()
        else:
            net = COUNTS[name] - LATER[name]
            assert _values(spec["exit_fraction"]) == [
                round(float(c / m), 6) for c, m in zip(net, MAIN, strict=True)
            ]
            assert spec_c["exit_fraction"] == spec["exit_fraction"]
            assert net.tolist() != (COUNTS[name] - PRIOR[name]).tolist()


def test_exclude_changes_the_config_only_through_the_ramp_demand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keep = _run(br, tmp_path / "keep", monkeypatch, ["--suffix", "flow_rc"])
    exc = _run(br, tmp_path / "exc", monkeypatch, EXCLUDE, artifact=_artifact())
    for f in ("scenarios/i24_replica_flow_rc.yaml", "scenarios/i24_replica_flow_rc_corrected.yaml"):
        a, b = yaml.safe_load(keep[f]), yaml.safe_load(exc[f])
        assert config_hash(ScenarioConfig.model_validate(a)) != config_hash(
            ScenarioConfig.model_validate(b)
        )
        for doc in (a, b):
            for spec in doc["network"]["ramps"]:
                key = "inflow" if spec["kind"] == "on" else "exit_fraction"
                spec[key] = [t for t, _ in spec[key]]  # the time grid stays; values may differ
        assert a == b
        assert a["network"]["inflow"] == b["network"]["inflow"]
    assert exc["artifacts/demand_i24_flow_rc.json"] == keep["artifacts/demand_i24_flow_rc.json"]


def test_exclude_records_its_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    art = _artifact()
    keep = _run(br, tmp_path / "keep", monkeypatch, ["--suffix", "flow_rc"])
    out = _run(br, tmp_path / "exc", monkeypatch, EXCLUDE, artifact=art)
    sha = hashlib.sha256(
        (tmp_path / "exc" / "artifacts" / "i24_count_consistency.json").read_bytes()
    ).hexdigest()
    inputs = json.loads(out["artifacts/i24_replica_inputs_flow_rc.json"])
    block = inputs["ramp_through_traffic"]
    assert block["mode"] == "exclude"
    assert block["artifact"] == "artifacts/i24_count_consistency.json"
    assert block["artifact_data_hash"] == DATA_HASH
    assert block["artifact_sha256"] == sha
    assert block["artifact_created_at"] == art["created_at"]
    assert "§8.4.3" in block["amendment"]
    assert [r["name"] for r in block["ramps"]] == [r["name"] for r in br.RAMPS]
    for r, rec in zip(block["ramps"], inputs["ramps"], strict=True):
        series = PRIOR if r["kind"] == "on" else LATER
        counted, flagged = COUNTS[r["name"]], series[r["name"]]
        assert r["flagged_series"] == br.THROUGH_TRAFFIC_SERIES[r["kind"]]
        assert r["counted"] == int(counted.sum())
        assert r["flagged"] == int(flagged.sum())
        assert r["corrected"] == int((counted - flagged).sum())
        assert r["share_2h"] == round(int(flagged.sum()) / int(counted.sum()), 4)
        assert r["windows_clipped"] == 0
        # the record keeps the recording's counts and adds the corrected ones
        assert rec["ramp_lane_crossings"] == counted.tolist()
        assert rec["ramp_lane_crossings_corrected"] == (counted - flagged).tolist()
    for f in ("scenarios/i24_replica_flow_rc.yaml", "scenarios/i24_replica_flow_rc_corrected.yaml"):
        head = _header(out[f])
        b2 = br.ramp_through_header(block, "artifacts/i24_replica_inputs_flow_rc.json")
        assert b2 in head
        assert "--ramp-through-traffic exclude" in head
        assert "artifacts/i24_count_consistency.json" in head
        assert DATA_HASH[:12] in head and sha[:12] in head
        for r in block["ramps"]:
            assert f"{r['name']}: counted {r['counted']}, flagged {r['flagged']}" in head
        # nothing else in the header moved
        assert head.replace(b2, "") == _header(keep[f])


def test_counts_are_clipped_at_zero() -> None:
    art = _artifact()
    oh = art["ramps"][0]
    oh["prior_mainline_per_window"][5] = oh["counts_per_window"][5] + 4
    counted = {r["name"]: COUNTS[r["name"]] for r in br.RAMPS}
    out = br.ramp_through_corrections(art, counted)
    r = out["Old Hickory Blvd on-ramp"]
    assert r["corrected_per_window"][5] == 0
    assert min(r["corrected_per_window"]) >= 0
    assert r["windows_clipped"] == 1
    assert r["removed"] == r["counted"] - r["corrected"] == r["flagged"] - 4
    assert all(v["windows_clipped"] == 0 for k, v in out.items() if k != r["name"])
    assert "1 window(s) clipped at 0" in br.ramp_through_header(
        {
            "artifact": "a.json",
            "artifact_data_hash": DATA_HASH,
            "artifact_sha256": "cd" * 32,
            "ramps": list(out.values()),
        },
        "x.json",
    )


# --- refusals ----------------------------------------------------------------------


def _swap_first_two(art: dict[str, Any]) -> None:
    art["ramps"][0], art["ramps"][1] = art["ramps"][1], art["ramps"][0]


MISMATCHES = {
    "data_hash": (lambda a: a.update(data_hash="cd" * 32), "data hash"),
    "period": (lambda a: a["parameters"].update(period_s=[T_LO, 8100.0]), "period"),
    "window": (lambda a: a["parameters"].update(window_s=900.0), "window"),
    "ramp_name": (lambda a: a["ramps"][2].update(name="Hickory Hollow on"), "ramps"),
    "count_x": (lambda a: a["ramps"][3].update(count_x_m=5000.0), "ramps"),
    "kind": (lambda a: a["ramps"][1].update(kind="on"), "ramps"),
    "order": (_swap_first_two, "ramps"),
    "missing_ramp": (lambda a: a["ramps"].pop(), "ramps"),
    "short_series": (
        lambda a: a["ramps"][0].update(prior_mainline_per_window=[1] * 23),
        "prior_mainline_per_window has 23 windows",
    ),
    "void_run": (
        lambda a: a["checks"].update(reproduces_committed_counts=False),
        "reproduces_committed_counts",
    ),
}


@pytest.mark.parametrize("case", sorted(MISMATCHES))
def test_a_mismatched_artifact_is_refused(case: str) -> None:
    mutate, message = MISMATCHES[case]
    art = _artifact()
    br.check_count_consistency(art, data_hash=DATA_HASH)  # the unmutated one passes
    mutate(art)
    with pytest.raises(ValueError, match=message):
        br.check_count_consistency(art, data_hash=DATA_HASH)


def test_counts_that_are_not_the_builders_are_refused() -> None:
    art = _artifact()
    art["ramps"][2]["counts_per_window"][7] += 1
    counted = {r["name"]: COUNTS[r["name"]] for r in br.RAMPS}
    with pytest.raises(ValueError, match="Hickory Hollow Pkwy on-ramp: the artifact's counts_per"):
        br.ramp_through_corrections(art, counted)


@pytest.mark.parametrize(
    ("argv", "artifact", "message"),
    [
        (EXCLUDE, _artifact(data_hash="cd" * 32), "does not match this builder: data hash"),
        (EXCLUDE, None, "is missing"),
        (["--suffix", "flow", "--ramp-through-traffic", "exclude"], _artifact(), "ending in 'rc'"),
        (["--ramp-through-traffic", "exclude"], _artifact(), "ending in 'rc'"),
    ],
)
def test_the_cli_refuses_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    artifact: dict[str, Any] | None,
    message: str,
) -> None:
    with pytest.raises(SystemExit, match=message):
        _run(br, tmp_path, monkeypatch, argv, artifact=artifact)
    assert not list((tmp_path / "scenarios").iterdir())


def test_the_cli_refuses_counts_that_differ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    art = _artifact()
    art["ramps"][0]["counts_per_window"][0] -= 1
    with pytest.raises(SystemExit, match="counts_per_window differ from the builder's crossings"):
        _run(br, tmp_path, monkeypatch, EXCLUDE, artifact=art)
    assert not list((tmp_path / "scenarios").iterdir())


# --- the committed artifact ------------------------------------------------------


def test_the_committed_count_check_fits_the_builder_and_reproduces_its_table() -> None:
    """The tracked artifact passes the check and gives docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.2's shares.

    The builder's counts are taken from the committed ``i24_replica_inputs.json``
    (``ramp_lane_crossings``), which the check reproduced bin for bin.
    """
    art = json.loads((REPO_ROOT / "artifacts" / "i24_count_consistency.json").read_text())
    inputs = json.loads((REPO_ROOT / "artifacts" / "i24_replica_inputs.json").read_text())
    br.check_count_consistency(art, data_hash=inputs["data_hash"])
    counted = {r["name"]: np.asarray(r["ramp_lane_crossings"]) for r in inputs["ramps"]}
    out = br.ramp_through_corrections(art, counted)
    table = {  # counted / flagged veh/h (tracked) and the share, §8.4.2
        "Old Hickory Blvd on-ramp": (664.5, 93.5, 0.14),
        "Hickory Hollow Pkwy off-ramp": (370.0, 8.0, 0.02),
        "Hickory Hollow Pkwy on-ramp": (431.0, 152.5, 0.35),
        "Bell Road off-ramp (collector road)": (203.0, 12.0, 0.06),
    }
    for name, (cnt, flag, share) in table.items():
        r = out[name]
        assert r["counted_veh_h"] == cnt and r["flagged_veh_h"] == flag
        assert round(r["share_2h"], 2) == share
        assert r["windows_clipped"] == 0
        assert r["corrected"] == r["counted"] - r["flagged"]
