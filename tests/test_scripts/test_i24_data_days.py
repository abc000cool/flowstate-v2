"""``scripts/i24_data.py`` takes a day directory and its t-origin (docs/PRE_FRISCO_PROGRAM.md C9).

No I-24 data is read (the laptop rule): every day here is a tiny synthetic
westbound export — a JSON array of fragment documents in the INCEPTION schema,
streamed into a processed directory by the loader's own
``convert_i24_to_parquet`` exactly as ``scripts/i24_extract.py`` does on the VM
(no zip is involved). Pinned:

* **byte-identical default**: without ``I24_DAY_DIR`` / ``I24_T0_UNIX`` the
  module's names are the committed day's, and ``meta``, ``data_hash``,
  ``clock``, ``load_mainline`` and ``load_vehicles`` return, byte for byte,
  what the code before the parameterisation (frozen below as ``LEGACY``)
  returns on the same directory; the committed coverage artifact's ``source``
  starts with the default label, so ``scripts/i24_coverage.py`` writes the
  same string;
* another day through the environment reaches a consumer's
  ``from i24_data import WB_DIR`` unchanged, and one variable alone is refused;
* the origin of a date is 06:00 CST (30 Nov 2022 → the extraction's default),
  daylight-time dates are refused, and ``check_recording`` refuses a
  directory that holds another date's morning.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd
import pytest

from calibration.loaders.i24motion import FT_PER_MILE, I24_MM_RANGE, convert_i24_to_parquet
from calibration.loaders.ngsim import FEET_TO_M

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
X_REF_FT = I24_MM_RANGE[1] * FT_PER_MILE
T0_20221130 = 1669809600.0
T0_20221129 = 1669723200.0


def load_script(name: str) -> ModuleType:
    """A script module, imported once (``scripts/`` on ``sys.path`` for its siblings)."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


d = load_script("i24_data")

# --- synthetic mornings -----------------------------------------------------------


def fragment(
    oid: str,
    *,
    t0_unix: float,
    t_start_s: float,
    n: int,
    x0_m: float,
    v_ms: float,
    lane: int,
    length_m: float = 5.0,
    cls: int = 0,
) -> dict[str, Any]:
    """One westbound fragment document at 5 Hz (front bumper ``x0 + v·t``, data x)."""
    k = np.arange(n)
    ts = t0_unix + t_start_s + 0.2 * k
    x_back = x0_m + v_ms * 0.2 * k - length_m
    return {
        "_id": {"$oid": oid},
        "timestamp": ts.tolist(),
        "x_position": (X_REF_FT - x_back / FEET_TO_M).tolist(),
        "y_position": [12.0 * lane + 6.0] * n,
        "length": length_m / FEET_TO_M,
        "width": 6.0,
        "height": 5.0,
        "direction": -1,
        "coarse_vehicle_class": cls,
        "first_timestamp": float(ts[0]),
        "last_timestamp": float(ts[-1]),
    }


#: A crossing vehicle: 12.5 m/s from x = −148.75 m, so every 5 Hz sample sits 1.25 m off a
#: multiple of 2.5 m and no sample lies on a section (all sections are multiples of 2.5 m).
SPEED_MS = 12.5
X0_M = -148.75
#: Samples from x0 past the last section (5400 m) with room to spare.
N_CROSSING = 2320


def crossing_vehicles(
    t0_unix: float, starts_s: Iterable[float], *, prefix: str = "a"
) -> list[dict[str, Any]]:
    """Vehicles crossing every section, one per start time, lanes 1–4 in turn."""
    return [
        fragment(
            f"{prefix}{i:023x}",
            t0_unix=t0_unix,
            t_start_s=t,
            n=N_CROSSING,
            x0_m=X0_M,
            v_ms=SPEED_MS,
            lane=1 + i % 4,
        )
        for i, t in enumerate(starts_s)
    ]


def sentinels(t0_unix: float) -> list[dict[str, Any]]:
    """Two short slow fragments at 06:00 and 09:59:50 upstream of every section (the span)."""
    return [
        fragment("e" * 24, t0_unix=t0_unix, t_start_s=0.0, n=11, x0_m=20.0, v_ms=2.0, lane=1),
        fragment("f" * 24, t0_unix=t0_unix, t_start_s=14390.0, n=50, x0_m=20.0, v_ms=2.0, lane=2),
    ]


def make_day(root: Path, name: str, t0_unix: float, docs: list[dict[str, Any]]) -> Path:
    """Write the export and convert it with origin ``t0_unix`` as ``scripts/i24_extract.py
    --t-origin`` does; returns the day dir."""
    root.mkdir(parents=True, exist_ok=True)
    export = root / f"{name}.json"
    export.write_text(json.dumps(docs))
    out = root / "processed" / name
    convert_i24_to_parquet(export, out, direction=-1, t_origin_unix=t0_unix)
    return out


def coverage_doc(
    data_hash: str, recommended: float = 0.5, equilibrium: float | None = 0.4
) -> dict[str, Any]:
    """A coverage artifact's fields the consumers read: 16 windows of 15 min over 06:00–10:00."""
    return {
        "data_hash": data_hash,
        "parameters": {"window_s": 900.0},
        "recommendation": {"rule": "max(section_gap_mixture, capacity_bound_fd)"},
        "windows": [
            {
                "t_lo_s": 900.0 * i,
                "pooled": {
                    "recommended_filled": recommended,
                    "equilibrium_pooled": equilibrium if 2 <= i < 10 else None,
                },
            }
            for i in range(16)
        ],
    }


# --- the code before the parameterisation, frozen --------------------------------

LEGACY = """
import json
from calibration.loaders.i24motion import I24_MAINLINE_LANES, load_i24_parquet, load_i24_vehicles

WB_DIR = None
T0_UNIX = 1669809600.0
MAINLINE_LANES = I24_MAINLINE_LANES


def meta():
    m = json.loads((WB_DIR / "meta.json").read_text())
    if abs(float(m["t_origin_unix"]) - T0_UNIX) > 1e-6:
        raise ValueError(f"unexpected time origin {m['t_origin_unix']} (expected {T0_UNIX})")
    return m


def data_hash():
    return str(meta()["data_hash"])


def clock(t_s):
    h = 6 + int(t_s // 3600)
    m = int((t_s % 3600) // 60)
    return f"{h:02d}:{m:02d}"


def load_mainline(t_range_s=None, x_range_m=None, columns=None):
    return load_i24_parquet(
        WB_DIR, t_range_s=t_range_s, x_range_m=x_range_m, lanes=MAINLINE_LANES, columns=columns
    )


def load_vehicles():
    return load_i24_vehicles(WB_DIR)
"""


def legacy_module(day_dir: Path) -> ModuleType:
    module = ModuleType("i24_data_legacy")
    exec(compile(LEGACY, "i24_data_legacy", "exec"), module.__dict__)
    module.WB_DIR = day_dir
    return module


@pytest.fixture(scope="module")
def committed_like_day(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A synthetic day in the committed day's place: its directory name and its origin."""
    root = tmp_path_factory.mktemp("committed_like")
    docs = sentinels(T0_20221130) + crossing_vehicles(T0_20221130, [1800.0, 1900.0, 4000.0])
    return make_day(root, "i24_wb_20221130", T0_20221130, docs)


@pytest.fixture(scope="module")
def other_day(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("other")
    docs = sentinels(T0_20221129) + crossing_vehicles(T0_20221129, [2000.0, 5000.0])
    return make_day(root, "synthetic_20221129", T0_20221129, docs)


# --- tests --------------------------------------------------------------------------


def test_the_default_day_is_the_committed_one() -> None:
    assert d.DEFAULT_DAY_DIR == REPO_ROOT / "data" / "i24motion" / "processed" / "i24_wb_20221130"
    if not (os.environ.get(d.DAY_DIR_ENV) or os.environ.get(d.T0_ENV)):
        assert d.WB_DIR == d.DEFAULT_DAY_DIR
        assert d.T0_UNIX == 1669809600.0
        assert d.active_day().is_default
        assert d.active_day().date == "2022-11-30"
        assert d.source_label() == d.DEFAULT_SOURCE_LABEL
    # the label scripts/i24_coverage.py now writes is the committed artifact's, byte for byte
    committed = json.loads((REPO_ROOT / "artifacts" / "i24_coverage.json").read_text())
    assert committed["source"].startswith(d.DEFAULT_SOURCE_LABEL + ", mainline lanes 1-4")
    extract = load_script("i24_extract")
    assert extract.INCEPTION_T0_UNIX == d.DEFAULT_T0_UNIX == d.t0_unix_for_date("2022-11-30")


@pytest.mark.parametrize(
    "t_s",
    [0.0, 0.2, 59.9, 60.0, 1800.0, 3599.8, 3600.0, 9000.0, 14399.8, 14400.0, -0.2, -60.0, 70000.0],
)
def test_the_clock_is_the_legacy_clock_on_the_default_origin(t_s: float) -> None:
    legacy = legacy_module(Path("unused"))
    assert d.I24Day(d.DEFAULT_DAY_DIR, d.DEFAULT_T0_UNIX).clock(t_s) == legacy.clock(t_s)


def test_every_function_is_byte_identical_on_the_committed_day(
    committed_like_day: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = legacy_module(committed_like_day)
    monkeypatch.setattr(d, "WB_DIR", committed_like_day)
    monkeypatch.setattr(d, "T0_UNIX", d.DEFAULT_T0_UNIX)
    assert json.dumps(d.meta(), sort_keys=False) == json.dumps(legacy.meta(), sort_keys=False)
    assert d.data_hash() == legacy.data_hash()
    for t in (0.0, 1800.0, 5432.1, 14400.0):
        assert d.clock(t) == legacy.clock(t)
    for kwargs in (
        {},
        {"t_range_s": (1790.0, 2500.0)},
        {"x_range_m": (100.0, 1200.0), "columns": ["t", "veh_id", "x", "v"]},
        {"t_range_s": (1800.0, 9000.0), "x_range_m": (-200.0, 5692.5), "columns": ["t", "x"]},
    ):
        new, old = d.load_mainline(**kwargs), legacy.load_mainline(**kwargs)
        pd.testing.assert_frame_equal(new, old)
        assert new.to_csv().encode() == old.to_csv().encode()
    assert d.load_vehicles().to_csv().encode() == legacy.load_vehicles().to_csv().encode()
    # the origin check raises the same message
    monkeypatch.setattr(d, "T0_UNIX", d.DEFAULT_T0_UNIX + 1.0)
    legacy.T0_UNIX = d.DEFAULT_T0_UNIX + 1.0
    with pytest.raises(ValueError) as new_err:
        d.meta()
    with pytest.raises(ValueError) as old_err:
        legacy.meta()
    assert str(new_err.value) == str(old_err.value)


def test_another_day_through_the_environment_reaches_every_importer(other_day: Path) -> None:
    code = (
        "import json, sys; sys.path.insert(0, 'scripts'); "
        "from i24_data import WB_DIR, T0_UNIX, clock, data_hash, source_label, load_mainline; "
        "print(json.dumps([str(WB_DIR), T0_UNIX, clock(1800), data_hash(), source_label(), "
        "len(load_mainline())]))"
    )
    env = {**os.environ, "I24_DAY_DIR": str(other_day), "I24_T0_UNIX": "1669723200"}
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, env=env, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    wb_dir, t0, clk, dh, label, n_rows = json.loads(out.stdout.strip().splitlines()[-1])
    meta = json.loads((other_day / "meta.json").read_text())
    assert (wb_dir, t0, clk, dh) == (str(other_day), T0_20221129, "06:30", meta["data_hash"])
    assert label == "I-24 MOTION INCEPTION v1.x, 29 Nov 2022 westbound (synthetic_20221129)"
    assert n_rows > 0
    # one variable alone is refused
    env = {k: v for k, v in os.environ.items() if k not in ("I24_DAY_DIR", "I24_T0_UNIX")}
    env["I24_DAY_DIR"] = str(other_day)
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, env=env, capture_output=True, text=True
    )
    assert out.returncode != 0 and "set both I24_DAY_DIR and I24_T0_UNIX" in out.stderr


def test_day_from_env_resolves_relative_directories() -> None:
    day = d.day_from_env({"I24_DAY_DIR": "data/x", "I24_T0_UNIX": "1669723200"})
    assert day == d.I24Day(REPO_ROOT / "data" / "x", T0_20221129)
    assert d.day_from_env({}) == d.I24Day(d.DEFAULT_DAY_DIR, d.DEFAULT_T0_UNIX)
    with pytest.raises(ValueError, match="set both"):
        d.day_from_env({"I24_T0_UNIX": "1"})


def test_the_origin_is_six_o_clock_central_standard_time() -> None:
    assert d.t0_unix_for_date("2022-11-29") == T0_20221129
    assert d.t0_unix_for_date("2022-12-01") == T0_20221130 + 86400.0
    assert d.I24Day(Path("x"), T0_20221129).date == "2022-11-29"
    # 6 Nov 2022 (the first Sunday of November) is standard time at 06:00; the day before is not
    assert d.t0_unix_for_date("2022-11-06") == T0_20221130 - 24 * 86400.0
    with pytest.raises(ValueError, match="daylight time"):
        d.t0_unix_for_date("2022-11-05")
    with pytest.raises(ValueError, match="daylight time"):
        d.t0_unix_for_date("2023-03-12")
    assert d.t0_unix_for_date("2023-03-11") > 0


def test_check_recording_accepts_its_morning_and_refuses_another_date(
    other_day: Path, tmp_path: Path
) -> None:
    good = d.I24Day(other_day, T0_20221129)
    assert good.check_recording("2022-11-29") == []
    assert good.check_recording("2022-11-30") == [
        "the origin 1669723200 is 2022-11-29, not 2022-11-30"
    ]
    # the 30 Nov recording converted with 29 Nov's origin: every sample a day late
    docs = sentinels(T0_20221130) + crossing_vehicles(T0_20221130, [2000.0])
    shifted = make_day(tmp_path, "shifted", T0_20221129, docs)
    problems = d.I24Day(shifted, T0_20221129).check_recording("2022-11-29")
    assert any("first sample at t = 86400 s" in p for p in problems)
    # a morning that stops before 08:31 does not cover the study period
    short = make_day(
        tmp_path,
        "short",
        T0_20221129,
        sentinels(T0_20221129)[:1] + crossing_vehicles(T0_20221129, [2000.0]),
    )
    assert any(
        "study period is not covered" in p for p in d.I24Day(short, T0_20221129).check_recording()
    )
    # the CLI
    ok = d.main(["check", "2022-11-29", str(other_day)])
    bad = d.main(["check", "2022-11-29", str(shifted)])
    assert (ok, bad) == (0, 1)
    assert d.main(["t0", "2022-11-30"]) == 0
    assert d.main(["t0", "2022-07-01"]) == 2
