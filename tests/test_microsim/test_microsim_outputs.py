"""Micro-tier run-artifact tests: Edie binning, completion marker, pool robustness.

These cover the parts of ``microsim.runner`` that decide whether a run's
artifacts are *correct* (``edges.parquet`` weighting) and whether they may be
*consumed at all* (the ``meta.json`` completion marker), plus the failure
handling of the replicate pool. None of them needs SUMO: the Edie frame is a
pure function, the marker tests write fixture directories, and the pool tests
run picklable stand-in workers so a worker death, a worker exception and a
wedged worker can be provoked in seconds.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from flowstate_core.config import config_hash
from microsim.runner import (
    EDGES_DT_BIN_S,
    EDGES_DX_BIN_M,
    REPLICATE_TIMEOUT_FLOOR_S,
    _edie_edges_frame,
    _map_replicates,
    is_run_complete,
    replicate_timeout_s,
    require_complete_run,
)
from microsim.scenarios import load_scenario


def _uniform_traj(n_veh: int, dt_s: float, duration_s: float, length_m: float, v_ms: float):
    """``n_veh`` vehicles sampled every ``dt_s``, evenly spread over ``length_m``."""
    times = np.arange(0.0, duration_s, dt_s)
    rows: list[dict[str, float]] = []
    for k in range(n_veh):
        x0 = length_m * k / n_veh
        for t in times:
            rows.append({"t": float(t), "x": float((x0 + v_ms * t) % length_m), "v": v_ms})
    return pd.DataFrame(rows)


class TestEdieSampleCadence:
    """``density``/``flow`` must be weighted by the REALIZED sample interval.

    The audit found ``run_micro`` handing ``_edie_edges_frame`` the *nominal*
    ``1/sim.output_hz`` while trajectories are sampled on whole steps
    (``out_every·step_length_s``), which scaled every density and flow in
    ``edges.parquet`` by ``nominal/realized`` — a legal ``output_hz: 4`` at a
    0.5 s step halved the customer-facing density heatmap.
    """

    def test_density_and_flow_from_realized_interval(self) -> None:
        # One bin (15 s x 100 m): 5 vehicles on 100 m is exactly 0.05 veh/m,
        # ground truth independent of the sampling rate.
        n_veh, dt_s, v_ms = 5, 0.5, 10.0
        traj = _uniform_traj(n_veh, dt_s, EDGES_DT_BIN_S, EDGES_DX_BIN_M, v_ms)
        frame = _edie_edges_frame(traj, dt_s, EDGES_DT_BIN_S, EDGES_DX_BIN_M)
        assert len(frame) == 1
        row = frame.iloc[0]
        assert row.density == pytest.approx(n_veh / EDGES_DX_BIN_M)
        assert row.flow == pytest.approx(n_veh * v_ms / EDGES_DX_BIN_M)
        assert row.mean_speed == pytest.approx(v_ms)

    @pytest.mark.parametrize("nominal_over_realized", [0.5, 2.0 / 3.0, 2.0])
    def test_wrong_interval_scales_the_field(self, nominal_over_realized: float) -> None:
        """The defect's signature: a mis-stated interval is a pure scale factor."""
        n_veh, dt_s, v_ms = 5, 0.5, 10.0
        traj = _uniform_traj(n_veh, dt_s, EDGES_DT_BIN_S, EDGES_DX_BIN_M, v_ms)
        truth = _edie_edges_frame(traj, dt_s, EDGES_DT_BIN_S, EDGES_DX_BIN_M).iloc[0]
        wrong = _edie_edges_frame(
            traj, dt_s * nominal_over_realized, EDGES_DT_BIN_S, EDGES_DX_BIN_M
        ).iloc[0]
        assert wrong.density == pytest.approx(truth.density * nominal_over_realized)
        assert wrong.flow == pytest.approx(truth.flow * nominal_over_realized)
        # Speed is a TTD/TTS ratio, so it is unaffected — which is why the
        # defect stayed invisible in the speed contours.
        assert wrong.mean_speed == pytest.approx(truth.mean_speed)


class TestCompletionMarker:
    """``meta.json`` is the completion marker; nothing else vouches for a run."""

    def test_predicate(self, tmp_path: Path) -> None:
        assert not is_run_complete(tmp_path)
        (tmp_path / "meta.json").write_text("{}")
        assert is_run_complete(tmp_path)
        assert require_complete_run(tmp_path) == tmp_path

    def test_interrupted_run_is_refused_and_names_the_debris(self, tmp_path: Path) -> None:
        # What an interrupted replicate leaves: a footer-less parquet opened
        # at the start of the run and no marker.
        (tmp_path / "trajectories.parquet").write_bytes(b"PAR1\x00\x00truncated")
        with pytest.raises(FileNotFoundError) as excinfo:
            require_complete_run(tmp_path)
        msg = str(excinfo.value)
        assert "meta.json" in msg
        assert "trajectories.parquet" in msg
        assert str(tmp_path) in msg

    def test_never_run_directory_says_so(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="has not been run"):
            require_complete_run(tmp_path / "42")

    def test_run_micro_clears_a_stale_marker_before_writing_artifacts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed re-run must not leave the previous run's marker in place.

        Otherwise a stale ``meta.json`` vouches for a ``trajectories.parquet``
        the interrupted re-run truncated, and every resume sees a complete
        replicate that cannot be read.
        """
        from microsim import runner as runner_mod

        cfg = load_scenario("ring_sugiyama")
        run_dir = tmp_path / config_hash(cfg) / "42"
        run_dir.mkdir(parents=True)
        (run_dir / "meta.json").write_text(json.dumps({"seed": 42, "stale": True}))

        def _boom(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("network build failed")

        monkeypatch.setattr(runner_mod, "_build_network", _boom)
        with pytest.raises(RuntimeError, match="network build failed"):
            runner_mod.run_micro(cfg, 42, tmp_path)
        assert not is_run_complete(run_dir)


class TestReplicateTimeoutBudget:
    def test_floor_for_short_scenarios(self) -> None:
        cfg = load_scenario("ring_sugiyama").model_copy(deep=True)
        cfg.sim.duration_s = 30.0  # 30 s / 0.05 = 600 s, under the floor
        assert replicate_timeout_s(cfg) == REPLICATE_TIMEOUT_FLOOR_S

    def test_scales_with_duration(self) -> None:
        cfg = load_scenario("ring_sugiyama").model_copy(deep=True)
        cfg.sim.duration_s = 7800.0  # I-24 replica length
        # 20x slower than real time is still allowed (CLAUDE.md §3.4 targets 5x).
        assert replicate_timeout_s(cfg) == pytest.approx(7800.0 / 0.05)


_POOL_HELPERS = '''
"""Picklable stand-in replicate workers for the pool-robustness tests."""

import os
import signal
import time
from pathlib import Path


def echo(payload):
    seed, _victim, _pid_dir = payload
    return seed * 10


def die(payload):
    """SIGKILL the worker on the victim seed (an OOM kill, in miniature)."""
    seed, victim, _pid_dir = payload
    if seed == victim:
        os.kill(os.getpid(), signal.SIGKILL)
    time.sleep(0.2)
    return seed * 10


def boom(payload):
    seed, victim, _pid_dir = payload
    if seed == victim:
        raise ValueError(f"synthetic failure in seed {seed}")
    return seed * 10


def hang(payload):
    """Wedge the worker on the victim seed, recording its pid first."""
    seed, victim, pid_dir = payload
    if seed == victim:
        Path(pid_dir, f"{seed}.pid").write_text(str(os.getpid()))
        time.sleep(3600.0)
    return seed * 10
'''


@pytest.fixture(scope="module")
def pool_helpers(tmp_path_factory: pytest.TempPathFactory):
    """Import the stand-in workers from a module the spawn children can import."""
    root = tmp_path_factory.mktemp("pool_helpers")
    (root / "flowstate_pool_helpers.py").write_text(_POOL_HELPERS)
    sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    try:
        yield importlib.import_module("flowstate_pool_helpers")
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("flowstate_pool_helpers", None)


def _process_gone(pid: int) -> bool:
    """Whether ``pid`` is dead (or a zombie awaiting the executor's join)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:  # pragma: no cover - pid recycled by another user
        return True
    state = subprocess.run(
        ["ps", "-o", "state=", "-p", str(pid)], capture_output=True, text=True, check=False
    ).stdout.strip()
    return state.startswith("Z") or state == ""


class TestMapReplicates:
    """``multiprocessing.Pool.map`` hangs forever when a worker dies; this must not.

    ``Pool`` silently repopulates a killed worker and never completes or fails
    the task it held, so a 20-replicate run blocks until the machine is killed
    (the CLI and the inline queue have no backstop at all). Every failure mode
    below must instead surface in seconds, naming the seed.
    """

    def test_results_in_seed_order(self, pool_helpers, tmp_path: Path) -> None:
        seeds = [7, 3, 11]
        payloads = [(s, None, str(tmp_path)) for s in seeds]
        out = _map_replicates(pool_helpers.echo, payloads, seeds, n_procs=2, timeout_s=120.0)
        assert out == [70, 30, 110]

    def test_worker_death_fails_fast_and_names_the_seeds(
        self, pool_helpers, tmp_path: Path
    ) -> None:
        seeds = [1, 2]
        payloads = [(s, 1, str(tmp_path)) for s in seeds]
        t0 = time.monotonic()
        with pytest.raises(RuntimeError) as excinfo:
            _map_replicates(pool_helpers.die, payloads, seeds, n_procs=2, timeout_s=120.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 60.0, f"pool took {elapsed:.1f}s to report a dead worker"
        msg = str(excinfo.value)
        # The killed seed is named: the unfinished list is sorted, and seed 1
        # is unfinished whether or not its sibling completed first.
        assert "died" in msg and "seed(s) [1" in msg

    def test_replicate_exception_names_the_seed(self, pool_helpers, tmp_path: Path) -> None:
        seeds = [4, 5]
        payloads = [(s, 5, str(tmp_path)) for s in seeds]
        with pytest.raises(RuntimeError, match="seed=5"):
            _map_replicates(pool_helpers.boom, payloads, seeds, n_procs=2, timeout_s=120.0)

    def test_wedged_worker_times_out_and_is_killed(self, pool_helpers, tmp_path: Path) -> None:
        seeds = [8, 9]
        payloads = [(s, 9, str(tmp_path)) for s in seeds]
        t0 = time.monotonic()
        with pytest.raises(TimeoutError) as excinfo:
            _map_replicates(pool_helpers.hang, payloads, seeds, n_procs=2, timeout_s=3.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 60.0, f"bounded wait took {elapsed:.1f}s for a 3 s budget"
        assert "9" in str(excinfo.value)
        # The wedged worker (and the SUMO it would be running in-process) is
        # cleaned up, not left behind to hold memory on the machine.
        pid_file = tmp_path / "9.pid"
        assert pid_file.is_file(), "the hanging worker never started"
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 30.0
        while not _process_gone(pid) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert _process_gone(pid), f"worker pid {pid} survived the timeout"
