"""E12's recorder (``tests/test_microsim/_platform_record.py``) and divergence probe
(``scripts/platform_probe.py``); docs/E12_PLATFORM_TESTS.md.

Unit tests of the record's pieces, one pytest subprocess that records two real
fixture tests, a 30-s probe run of the Ruth St fixture (its digests, its file
and its purity: a probed run's files equal an unprobed run's), the comparison
on synthetic probe documents, and the workflow's trigger and test list.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest
import yaml

from tests.test_microsim import _platform_record as pr

REPO = Path(__file__).resolve().parents[2]
PROBE_PATH = REPO / "scripts" / "platform_probe.py"
TEST_LIST = REPO / "tests" / "test_microsim" / "e12_platform_tests.txt"
WORKFLOW = REPO / ".github" / "workflows" / "platform_tests.yml"


def _probe_module() -> ModuleType:
    if "platform_probe" in sys.modules:
        return sys.modules["platform_probe"]
    spec = importlib.util.spec_from_file_location("platform_probe", PROBE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["platform_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


def _fake_run(root: Path, *, v_shift: float = 0.0) -> SimpleNamespace:
    """A run directory with a ``meta.json`` and a three-step ``trajectories.parquet``."""
    root.mkdir(parents=True, exist_ok=True)
    meta = {
        "config_hash": "abc",
        "seed": 4,
        "sumo_seed": 4,
        "n_vehicles_planned": 3,
        "n_vehicles_departed": 3,
        "n_vehicles_arrived": 1,
        "n_collisions": 0,
        "collisions": [],
        "ramps": [{"name": "th52", "n_planned": 2, "n_departed": 1, "kind": "on"}],
        "weave_sections": [
            {"ramp": "th52", "n_missed_exit": 1, "wait_s_mean": None, "params": {"x": 1.0}}
        ],
    }
    (root / "meta.json").write_text(json.dumps(meta))
    traj = pd.DataFrame(
        {
            "t": [0.5, 0.5, 60.5, 61.0],
            "veh_id": ["v1", "v0", "v0", "v0"],
            "x": [10.0, 3.0, 30.0, 31.0],
            "lane": np.array([1, 0, 0, 0], dtype=np.int32),
            "v": [20.0, 19.0, 18.0 + v_shift, 17.0],
            "a": [0.0, 0.0, 0.0, 0.0],
        }
    )
    traj.to_parquet(root / "trajectories.parquet", index=False)
    return SimpleNamespace(
        run_dir=root, meta=root / "meta.json", trajectories=root / "trajectories.parquet"
    )


class TestRecorderPieces:
    def test_jsonable(self):
        state = {
            "windows": pd.Series([4.3, np.float64(5.5)], index=[2, 3]),
            "pair": (np.int64(401), 466),
            (1, "a"): float("nan"),
            "inf": float("-inf"),
            "arr": np.array([1.5, 2.0]),
        }
        out = pr.jsonable(state)
        assert out == {
            "windows": {"2": 4.3, "3": 5.5},
            "pair": [401, 466],
            "1/a": "nan",
            "inf": "-inf",
            "arr": [1.5, 2.0],
        }
        json.dumps(out, allow_nan=False)  # strict JSON

    @pytest.mark.parametrize(
        ("report", "outcome"),
        [
            (None, "not_run"),
            (SimpleNamespace(passed=True, skipped=False, failed=False), "passed"),
            (SimpleNamespace(passed=True, skipped=False, failed=False, wasxfail="r"), "xpassed"),
            (SimpleNamespace(passed=False, skipped=True, failed=False, wasxfail="r"), "xfailed"),
            (SimpleNamespace(passed=False, skipped=True, failed=False), "skipped"),
            (
                SimpleNamespace(
                    passed=False, skipped=False, failed=True, longrepr="[XPASS(strict)] r"
                ),
                "xpassed_strict",
            ),
            (SimpleNamespace(passed=False, skipped=False, failed=True, longrepr="E"), "failed"),
        ],
    )
    def test_outcome_of(self, report, outcome):
        assert pr.outcome_of(report) == outcome

    def test_trajectory_digests_are_exact_and_per_window(self, tmp_path):
        a = pr.trajectory_digests(_fake_run(tmp_path / "a").trajectories)
        b = pr.trajectory_digests(_fake_run(tmp_path / "b").trajectories)
        assert a == b and a["rows"] == 4 and set(a["by_window"]) == {"0", "1"}
        # one ulp of one speed in minute 1: that window and the whole digest move, minute 0 not
        c = pr.trajectory_digests(_fake_run(tmp_path / "c", v_shift=np.spacing(18.0)).trajectories)
        assert c["by_window"]["0"] == a["by_window"]["0"]
        assert c["by_window"]["1"] != a["by_window"]["1"] and c["sha256"] != a["sha256"]
        # row order within a step does not matter (rows are taken in (t, veh_id) order)
        df = pd.read_parquet(tmp_path / "a" / "trajectories.parquet").iloc[::-1]
        df.to_parquet(tmp_path / "rev.parquet", index=False)
        assert pr.trajectory_digests(tmp_path / "rev.parquet") == a

    def test_run_numbers(self, tmp_path):
        out = pr.run_numbers(_fake_run(tmp_path / "r"))
        assert out["n_vehicles_departed"] == 3 and out["n_collisions"] == 0
        assert out["ramps"] == [{"name": "th52", "n_planned": 2, "n_departed": 1}]
        assert out["weave_sections"] == [{"ramp": "th52", "n_missed_exit": 1, "wait_s_mean": None}]
        assert out["measured_merges"] == [] and out["trajectories"]["rows"] == 4

    def test_off_touches_nothing(self, tmp_path, monkeypatch):
        monkeypatch.delenv(pr.RECORD_ENV, raising=False)
        mod = SimpleNamespace(run_micro=lambda *a: None, _th52_lane1_windows=lambda *a: None)
        before = dict(vars(mod))
        with pr.recording("t::x", mod, lambda: None) as rec:
            assert rec is None and vars(mod) == before
        assert vars(mod) == before and not list(tmp_path.iterdir())

    def test_on_records_runs_states_and_outcome(self, tmp_path, monkeypatch):
        out = tmp_path / "rec" / "state.jsonl"
        monkeypatch.setenv(pr.RECORD_ENV, str(out))
        run = _fake_run(tmp_path / "run")

        def run_micro(cfg, seed, out_dir):
            return run

        def _th52_lane1_windows(paths, meta, last_60m=False):
            return pd.Series([4.4, 6.0], index=[2, 3]), {"entrance_departed": (393, 466)}

        def unrelated():
            return 1

        mod = SimpleNamespace(
            run_micro=run_micro, _th52_lane1_windows=_th52_lane1_windows, unrelated=unrelated
        )
        report = SimpleNamespace(passed=True, skipped=False, failed=False, wasxfail="r")
        with pr.recording("tests/x.py::test_y[on-4-0]", mod, lambda: report):
            assert mod.run_micro is not run_micro and mod.unrelated is unrelated
            paths = mod.run_micro(None, 4, tmp_path)
            _windows, state = mod._th52_lane1_windows(paths, {}, last_60m=True)
            state["exit_share"] = (296, 304)  # completed after the call, as exit_side does
        assert mod.run_micro is run_micro and mod._th52_lane1_windows is _th52_lane1_windows
        (rec,) = pr.read_records(out)
        assert rec["test_id"] == "tests/x.py::test_y[on-4-0]" and rec["outcome"] == "xpassed"
        assert rec["platform"] and rec["machine"] and rec["sumo_version"] == "1.27.1"
        assert rec["runs"][0]["weave_sections"][0]["n_missed_exit"] == 1
        assert rec["states"] == [
            {
                "helper": "_th52_lane1_windows",
                "value": [
                    {"2": 4.4, "3": 6.0},
                    {"entrance_departed": [393, 466], "exit_share": [296, 304]},
                ],
            }
        ]

    def test_state_helpers_are_defined_where_the_registry_says(self):
        from tests.test_microsim import test_microsim_merge_managed_meter as mmt
        from tests.test_microsim import test_microsim_merge_measured as mm
        from tests.test_microsim import test_microsim_th61_lane_end as th61
        from tests.test_microsim import test_microsim_weave_short_section as ws

        where = {
            "lane1_last60m_windows": ws,
            "th61_lane_end_state": th61,
            "_th52_lane1_windows": mmt,
            "_th52_corridor_state": mmt,
            "_lane_speed_windows": mmt,
            "_th52_state": mm,
            "_th52_lane1_first60m_windows": mm,
        }
        assert set(where) == set(pr.STATE_HELPERS)
        for name, mod in where.items():
            assert callable(getattr(mod, name, None)), name


@pytest.mark.integration
def test_the_recorder_records_real_fixture_tests(tmp_path):
    """A pytest subprocess with the recorder on: a 10-s Ruth St run (no state helper)
    and the T.H.52 capacity test (``_th52_lane1_windows``). The outcome of the
    second is platform-sensitive and not pinned here; only that it is recorded."""
    out = tmp_path / "state.jsonl"
    ids = [
        "tests/test_microsim/test_microsim_weave_short_section.py::TestShortSectionRule::"
        "test_meta_flags_the_ruth_section_short",
        "tests/test_microsim/test_microsim_merge_managed_meter.py::TestWeaveRun::"
        "test_th52_weave_at_capacity_flows",
    ]
    env = {**os.environ, pr.RECORD_ENV: str(out)}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *ids, "-q", "-p", "no:cacheprovider", "-rxX"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    recs = {r["test_id"]: r for r in pr.read_records(out)}
    assert set(recs) == set(ids), proc.stdout[-2000:]
    short, cap = recs[ids[0]], recs[ids[1]]
    assert short["outcome"] == "passed" and short["states"] == []
    assert short["runs"][0]["seed"] == 3 and short["runs"][0]["trajectories"]["rows"] > 0
    assert cap["outcome"] in {"xfailed", "xpassed_strict"}
    (run,) = cap["runs"]
    assert run["seed"] == 3 and run["weave_sections"][0]["ramp"] == "th52"
    (state,) = cap["states"]
    assert state["helper"] == "_th52_lane1_windows"
    windows, st = state["value"]
    assert len(windows) == 18 and set(st) >= {"lane1_first60m_by_minute", "entrance_departed"}


class TestProbe:
    @pytest.mark.integration
    def test_short_run_its_file_and_purity(self, tmp_path):
        """A 30-s probe of ``ruth_exit_peak_s5``: one digest per step and channel, the
        written file reads back as the document, and the probed run (step listener on,
        ``record_commands`` on) writes the same trajectories, edges and vehicles as
        ``run_micro`` alone."""
        pp = _probe_module()
        from flowstate_core.config import ScenarioConfig
        from microsim import run_micro

        fx = pp.FIXTURES["ruth_exit_peak_s5"]
        doc = pp.probe(fx, tmp_path / "probed", duration_s=30.0, dump_at=[20.0])
        assert doc["n_steps"] == 60 and doc["step_s"] == 0.5 and doc["seed"] == 5
        steps = doc["steps"]
        assert set(steps) == set(pp.CHANNELS) and all(len(v) == 60 for v in steps.values())
        assert all(h is None or len(h) == pp.HASH_HEX for v in steps.values() for h in v)
        assert sum(h is not None for h in steps["sumo"]) > 50
        assert sum(h is not None for h in steps["commands"]) > 0 and doc["commands_rows"] > 0
        assert doc["config"]["osm_file"] == "tests/fixtures/weave_ruth.osm"
        dump = doc["dumps"]["20.0"]
        assert dump["sumo"] and dump["traj"] and len(dump["sumo"]) >= len(dump["traj"])
        path = tmp_path / "doc.json"
        pp.write_probe(doc, path)
        assert json.loads(path.read_text()) == json.loads(json.dumps(doc))

        raw = fx.build().model_dump(mode="json")
        raw["sim"]["duration_s"] = 30.0
        plain = run_micro(ScenarioConfig.model_validate(raw), fx.seed, tmp_path / "plain")
        (probed_dir,) = (tmp_path / "probed").glob("*/5")
        for name in ("trajectories.parquet", "edges.parquet", "vehicles.parquet"):
            assert (probed_dir / name).read_bytes() == (plain.run_dir / name).read_bytes(), name
        assert (probed_dir / "weave_commands.parquet").is_file()
        assert not (plain.run_dir / "weave_commands.parquet").exists()
        # the listener is gone after the run
        import libsumo

        assert not libsumo._stepManager._stepListeners

    def test_step_digests(self, tmp_path):
        pp = _probe_module()
        base = pp.sumo_step_digest(["a", "b"], ["e_0", "e_1"], [1.0, 2.0], [3.0, 4.0])
        assert base == pp.sumo_step_digest(["a", "b"], ["e_0", "e_1"], [1.0, 2.0], [3.0, 4.0])
        assert base != pp.sumo_step_digest(
            ["a", "b"], ["e_0", "e_1"], [1.0, 2.0], [3.0, np.nextafter(4.0, 5.0)]
        )
        assert base != pp.sumo_step_digest(["a", "b"], ["e_0", "e_0"], [1.0, 2.0], [3.0, 4.0])
        traj = _fake_run(tmp_path / "a").trajectories
        d = pp.traj_step_digests(traj)
        assert set(d) == {0.5, 60.5, 61.0}
        df = pd.read_parquet(traj).iloc[::-1]
        df.to_parquet(tmp_path / "rev.parquet", index=False)
        assert pp.traj_step_digests(tmp_path / "rev.parquet") == d
        cmd = pd.DataFrame(
            {
                "t": [1.0, 1.0, 1.5],
                "veh_id": ["v1", "v2", "v1"],
                "section": ["th52"] * 3,
                "rule": ["cooperate", "ease", "cooperate"],
                "lane_from": np.array([0, 1, 0], dtype=np.int32),
                "lane_to": np.array([-1, -1, -1], dtype=np.int32),
                "v_cmd_ms": [10.0, float("nan"), 9.5],
                "lc_mode_set": np.array([-1, -1, -1], dtype=np.int32),
                "x_m": [100.0, 120.0, float("nan")],
            }
        )
        cmd.to_parquet(tmp_path / "c.parquet", index=False)
        dc = pp.command_step_digests(tmp_path / "c.parquet")
        assert set(dc) == {1.0, 1.5}
        cmd.iloc[[1, 0, 2]].to_parquet(tmp_path / "c2.parquet", index=False)
        swapped = pp.command_step_digests(tmp_path / "c2.parquet")
        assert swapped[1.0] != dc[1.0] and swapped[1.5] == dc[1.5]  # decision order counts
        assert pp.command_step_digests(tmp_path / "missing.parquet") == {}


def _doc(sumo, traj, commands, **over):
    base = {
        "fixture": "ruth_exit_peak_s5",
        "test_id": "t",
        "seed": 5,
        "step_s": 0.5,
        "n_steps": len(sumo),
        "hash_hex_chars": 12,
        "config": {"portable_sha256": "p", "osm_sha256": "o"},
        "platform": {"system": "darwin", "machine": "arm64"},
        "summary": {},
        "steps": {"sumo": sumo, "traj": traj, "commands": commands},
    }
    return {**base, **over}


class TestCompare:
    S: ClassVar[list[str | None]] = ["s0", "s1", "s2", "s3", "s4", "s5"]
    T: ClassVar[list[str | None]] = ["t0", "t1", "t2", "t3", "t4", "t5"]
    C: ClassVar[list[str | None]] = [None, "c1", None, "c3", "c4", "c5"]

    def _with(self, seq, i, value="zz"):
        out = list(seq)
        out[i] = value
        return out

    def test_identical(self):
        res = _probe_module().compare(_doc(self.S, self.T, self.C), _doc(self.S, self.T, self.C))
        assert res["verdict"] == "identical" and res["first_step"] is None

    def test_state_first(self):
        b = _doc(self._with(self.S, 2), self._with(self.T, 3), self._with(self.C, 4))
        res = _probe_module().compare(_doc(self.S, self.T, self.C), b)
        assert res["verdict"] == "state_first" and res["first_step"] == 2
        assert res["first_t_s"] == 1.5 and res["differing_at_first"] == ["sumo"]
        assert res["channels"]["commands"]["first_step"] == 4
        assert res["channels"]["sumo"]["n_differing"] == 1

    def test_commands_first(self):
        b = _doc(self._with(self.S, 4), self.T, self._with(self.C, 3))
        res = _probe_module().compare(_doc(self.S, self.T, self.C), b)
        assert res["verdict"] == "commands_first" and res["first_step"] == 3
        assert res["channels"]["traj"]["first_step"] is None

    def test_same_step(self):
        b = _doc(self.S, self._with(self.T, 1), self._with(self.C, 1))
        res = _probe_module().compare(_doc(self.S, self.T, self.C), b)
        assert res["verdict"] == "same_step" and res["differing_at_first"] == ["traj", "commands"]

    def test_incomparable(self):
        b = _doc(self.S, self.T, self.C, fixture="th52_capacity_on_s4")
        res = _probe_module().compare(_doc(self.S, self.T, self.C), b)
        assert res["verdict"] == "incomparable" and not res["comparable"]
        c = _doc(self.S, self.T, self.C, config={"portable_sha256": "q", "osm_sha256": "o"})
        assert _probe_module().compare(_doc(self.S, self.T, self.C), c)["comparable"] is False

    def test_cli_exit_codes(self, tmp_path, capsys):
        pp = _probe_module()
        a = _doc(self.S, self.T, self.C)
        paths = {}
        for name, doc in {
            "a": a,
            "same": a,
            "parted": _doc(self._with(self.S, 5), self.T, self.C),
            "other": _doc(self.S, self.T, self.C, seed=4),
        }.items():
            paths[name] = tmp_path / f"{name}.json"
            paths[name].write_text(json.dumps(doc))
        assert pp.main(["--compare", str(paths["a"]), str(paths["same"])]) == 0
        assert pp.main(["--compare", str(paths["a"]), str(paths["parted"])]) == 1
        out = capsys.readouterr().out
        assert "first differing step: 5 (t = 3.0 s), channels: sumo" in out
        assert "verdict: state_first" in out
        assert pp.main(["--compare", str(paths["a"]), str(paths["other"])]) == 2


class TestWorkflowAndList:
    def test_workflow_is_manual_only_and_runs_the_list(self):
        wf = yaml.safe_load(WORKFLOW.read_text())
        triggers = wf.get("on", wf.get(True))
        assert set(triggers) == {"workflow_dispatch"}
        text = WORKFLOW.read_text()
        assert "tests/test_microsim/e12_platform_tests.txt" in text
        assert "FLOWSTATE_RECORD_STATE" in text and "-rxX" in text
        assert "scripts/platform_probe.py" in text and "upload-artifact" in text
        steps = wf["jobs"]["platform"]["steps"]
        setup = next(s for s in steps if s.get("uses", "").startswith("astral-sh/setup-uv"))
        ci = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text())
        ci_setup = next(
            s for s in ci["jobs"]["test"]["steps"] if s.get("uses", "").startswith("astral-sh")
        )
        assert setup == ci_setup  # same uv action, Python and cache setting as ci.yml
        sync = next(s for s in steps if s.get("name") == "Sync workspace")
        assert sync["run"] == "uv sync --all-packages --dev"

    @pytest.mark.integration
    def test_list_collects_and_holds_every_probed_fixture(self):
        selectors = [ln for ln in TEST_LIST.read_text().splitlines() if ln.strip()]
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "-p",
                "no:cacheprovider",
                *selectors,
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
        collected = {ln.strip() for ln in proc.stdout.splitlines() if "::" in ln}
        for sel in selectors:
            assert any(c == sel or c.startswith(sel + "[") for c in collected), sel
        for fx in _probe_module().FIXTURES.values():
            assert fx.test_id in collected, fx.test_id


class TestCompareRecords:
    @staticmethod
    def _rec(test_id, outcome, n_missed=1, minute1="bbb", lane_min=4.4):
        return {
            "test_id": test_id,
            "outcome": outcome,
            "platform": "linux",
            "runs": [
                {
                    "n_collisions": 0,
                    "weave_sections": [{"ramp": "th52", "n_missed_exit": n_missed}],
                    "trajectories": {
                        "rows": 4,
                        "sha256": f"whole-{minute1}",
                        "by_window": {"0": "aaa", "1": minute1, "2": minute1},
                    },
                }
            ],
            "states": [{"helper": "_th52_lane1_windows", "value": [{"2": lane_min}, {}]}],
        }

    def test_rows_and_cli(self, tmp_path, capsys):
        pp = _probe_module()
        a = [self._rec("t::x", "xpassed"), self._rec("t::y", "passed")]
        b = [
            self._rec("t::x", "xfailed", n_missed=7, minute1="ccc", lane_min=1.9),
            self._rec("t::y", "passed"),
        ]
        paths = {}
        for name, recs in {"a": a, "b": b, "a2": a}.items():
            paths[name] = tmp_path / f"{name}.jsonl"
            paths[name].write_text("".join(json.dumps(r) + "\n" for r in recs))
        rows = {
            r["test_id"]: r
            for r in pp.compare_records(pp.read_records(paths["a"]), pp.read_records(paths["b"]))
        }
        x, y = rows["t::x"], rows["t::y"]
        assert (x["outcome_a"], x["outcome_b"], x["trajectories"]) == ("xpassed", "xfailed", [1])
        assert x["differing"] == {
            "runs.0.weave_sections.0.n_missed_exit": (1, 7),
            "states.0.value.0.2": (4.4, 1.9),
        }
        assert y["trajectories"] == ["identical"] and y["differing"] == {}
        assert pp.main(["--compare-records", str(paths["a"]), str(paths["a2"])]) == 0
        assert pp.main(["--compare-records", str(paths["a"]), str(paths["b"])]) == 1
        out = capsys.readouterr().out
        assert "t::x: xpassed / xfailed (OUTCOME DIFFERS); trajectories: part from minute 1" in out
        assert "runs.0.weave_sections.0.n_missed_exit: 1 -> 7" in out

    @pytest.mark.parametrize(
        ("outcome_a", "where_a", "outcome_b", "where_b", "relation"),
        [
            ("xfailed", ("linux", "x86_64"), "xfailed", ("darwin", "arm64"), "same"),
            # a per-platform strict mark: passes on macOS, xfails on Linux (E12 step 3)
            ("passed", ("darwin", "arm64"), "xfailed", ("linux", "x86_64"), "per_platform"),
            # step 1's non-strict xpass against the same platform's unmarked pass
            ("xpassed", ("darwin", "arm64"), "passed", ("darwin", "arm64"), "same_result"),
            ("xpassed", ("darwin", "arm64"), "xfailed", ("linux", "x86_64"), "per_platform"),
            # on one platform a different result is non-determinism
            ("passed", ("linux", "x86_64"), "xfailed", ("linux", "x86_64"), "differs"),
            ("passed", ("linux", "x86_64"), "xfailed", ("linux", "aarch64"), "per_platform"),
            # an outcome that fails its run never reads as expected
            ("passed", ("darwin", "arm64"), "failed", ("linux", "x86_64"), "differs"),
            ("xfailed", ("darwin", "arm64"), "xpassed_strict", ("linux", "x86_64"), "differs"),
            ("passed", ("darwin", "arm64"), "xpassed_strict", ("darwin", "arm64"), "differs"),
            ("passed", ("darwin", "arm64"), "skipped", ("linux", "x86_64"), "differs"),
        ],
    )
    def test_outcome_relation(self, outcome_a, where_a, outcome_b, where_b, relation):
        pp = _probe_module()
        ra = {"outcome": outcome_a, "platform": where_a[0], "machine": where_a[1]}
        rb = {"outcome": outcome_b, "platform": where_b[0], "machine": where_b[1]}
        assert pp.outcome_relation(ra, rb) == relation
        assert pp.outcome_relation(rb, ra) == relation

    def test_per_platform_rows_and_report(self, tmp_path, capsys):
        pp = _probe_module()
        a = {**self._rec("t::x", "passed"), "platform": "darwin", "machine": "arm64"}
        b = {
            **self._rec("t::x", "xfailed", n_missed=3),
            "platform": "linux",
            "machine": "x86_64",
        }
        y_a = {**self._rec("t::y", "xpassed"), "platform": "darwin", "machine": "arm64"}
        y_b = {**self._rec("t::y", "passed"), "platform": "linux", "machine": "x86_64"}
        paths = {}
        for name, recs in {"a": [a, y_a], "b": [b, y_b]}.items():
            paths[name] = tmp_path / f"{name}.jsonl"
            paths[name].write_text("".join(json.dumps(r) + "\n" for r in recs))
        rows = {
            r["test_id"]: r
            for r in pp.compare_records(pp.read_records(paths["a"]), pp.read_records(paths["b"]))
        }
        assert rows["t::x"]["outcome_relation"] == "per_platform"
        assert rows["t::y"]["outcome_relation"] == "same_result"
        assert pp.main(["--compare-records", str(paths["a"]), str(paths["b"])]) == 1
        out = capsys.readouterr().out
        assert "t::x: passed / xfailed (differs by platform, each as its own marks allow)" in out
        assert "t::y: xpassed / passed (same result, different marks)" in out
        assert "OUTCOME DIFFERS)" not in out
        assert out.rstrip().endswith(
            "outcomes: 0 same outcome; 1 same result, different marks; "
            "1 differs by platform, each as its own marks allow; 0 OUTCOME DIFFERS"
        )
