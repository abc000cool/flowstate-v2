"""scripts/derive_population.py: the Amendment-1 populations (mean a_max shifted by k measured sd).

The four committed artifacts ``artifacts/idm_i24_capacity_amax_k{0.25,0.5,0.75,1.0}.json``
must be exactly the derivation: ``artifacts/idm_i24_capacity.json`` with mean
``a_max`` = the measured population's mean + k × sqrt(cov[a_max, a_max]) of
``artifacts/idm_i24.json`` (≈ 1.055 + k × 0.428), the covariance and every other
mean unchanged; the provenance in ``notes``. Drawing drivers through
``microsim.vehicles`` from a derived population moves the drivers' mean a_max
by k sd (less the truncation at the 0.2 m/s² floor, which lifts the k = 0
mean slightly). Both corridors' fleets run the base population.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
import yaml

from flowstate_core.artifacts import IDMCalibration
from flowstate_core.config import FleetSpec
from flowstate_core.rng import make_rng

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
BASE = REPO_ROOT / "artifacts" / "idm_i24_capacity.json"
MEASURED = REPO_ROOT / "artifacts" / "idm_i24.json"
KS = (0.25, 0.5, 0.75, 1.0)


def _load() -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "derive_population", SCRIPTS / "derive_population.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


dp = _load()


def _derived(k: float) -> IDMCalibration:
    return dp.derive_shifted(
        IDMCalibration.load(BASE),
        IDMCalibration.load(MEASURED),
        param="a_max",
        k=k,
        base_label="base (sha256 x)",
        measured_label="measured (sha256 y)",
        created_at="2026-10-06T00:00:00Z",
    )


def test_both_corridors_run_the_base_population() -> None:
    for scenario in ("i24_replica_flow_speedcal.yaml", "mndot_i94_wb_stpaul_weave_slice.yaml"):
        raw = yaml.safe_load((REPO_ROOT / "scenarios" / scenario).read_text())
        assert raw["fleet"]["idm_calibration"] == "artifacts/idm_i24_capacity.json"


def test_the_measured_spread_is_the_amendments() -> None:
    measured = IDMCalibration.load(MEASURED)
    sd = dp.measured_sd(measured, "a_max")
    assert sd == pytest.approx(0.4284, abs=1e-4)
    assert measured.mean["a_max"] == pytest.approx(1.055, abs=1e-3)


@pytest.mark.parametrize("k", KS)
def test_the_derivation_shifts_one_mean_and_keeps_the_rest(k: float) -> None:
    base, measured = IDMCalibration.load(BASE), IDMCalibration.load(MEASURED)
    d = _derived(k)
    sd = math.sqrt(measured.cov[2][2])
    assert d.mean["a_max"] == pytest.approx(measured.mean["a_max"] + k * sd, rel=1e-15)
    assert d.cov == base.cov
    assert {n: d.mean[n] for n in ("v0", "T", "b", "s0")} == {
        n: base.mean[n] for n in ("v0", "T", "b", "s0")
    }
    assert (d.source, d.data_hash, d.n_episodes_fit) == (
        base.source,
        base.data_hash,
        base.n_episodes_fit,
    )
    for text in (
        "Amendment 1",
        f"{k:g} x {sd:.6f}",
        "base (sha256 x)",
        "measured (sha256 y)",
        "2026-10-06",
    ):
        assert text in d.notes


@pytest.mark.parametrize("k", KS)
def test_the_committed_artifacts_are_the_derivation(k: float) -> None:
    path = REPO_ROOT / "artifacts" / dp.out_name("idm_i24_capacity_amax", k)
    assert path.name == f"idm_i24_capacity_amax_k{float(k)}.json"
    stored = IDMCalibration.load(path)
    fresh = _derived(k)
    assert stored.mean == fresh.mean and stored.cov == fresh.cov
    assert (
        "idm_i24_capacity.json (sha256 " in stored.notes and "idm_i24.json (sha256 " in stored.notes
    )


def test_refusals() -> None:
    base, measured = IDMCalibration.load(BASE), IDMCalibration.load(MEASURED)
    kw = {"base_label": "b", "measured_label": "m", "created_at": "t"}
    with pytest.raises(ValueError, match="outside the measured mean"):
        dp.derive_shifted(base, measured, param="a_max", k=1.5, **kw)
    with pytest.raises(ValueError, match="unknown parameter"):
        dp.derive_shifted(base, measured, param="delta", k=0.5, **kw)
    moved = base.model_copy(update={"mean": {**base.mean, "a_max": 1.2}})
    with pytest.raises(ValueError, match="k = 0 would not be the base"):
        dp.derive_shifted(moved, measured, param="a_max", k=0.5, **kw)
    cov = [list(r) for r in base.cov]
    cov[0][0] += 1.0
    with pytest.raises(ValueError, match="covariance"):
        dp.derive_shifted(
            base.model_copy(update={"cov": cov}), measured, param="a_max", k=0.5, **kw
        )
    # s0 + 1 sd of the measured population leaves CLAUDE.md §3.1's 1.0-3.0 m range
    with pytest.raises(ValueError, match="calibration range"):
        dp.derive_shifted(base, measured, param="s0", k=1.0, **kw)


def test_main_writes_the_files_and_skips_k0(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = dp.main(
        [
            "--k",
            "0",
            "0.5",
            "--out-dir",
            str(tmp_path),
            "--stem",
            "pop",
            "--created-at",
            "2026-10-06T00:00:00Z",
        ]
    )
    assert code == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pop_k0.5.json"]
    assert "k = 0 is the base itself" in capsys.readouterr().out
    assert IDMCalibration.load(tmp_path / "pop_k0.5.json").created_at == "2026-10-06T00:00:00Z"


def test_drawing_drivers_from_a_derived_population_shifts_their_mean() -> None:
    from microsim.vehicles import draw_vehicle_params

    n = 20000
    means = {}
    for k in (0.0, 1.0):
        path = (
            BASE if k == 0.0 else REPO_ROOT / "artifacts" / dp.out_name("idm_i24_capacity_amax", k)
        )
        fleet = FleetSpec(idm_calibration=str(path))
        draws = draw_vehicle_params(fleet, n, make_rng(11))
        means[k] = {
            p: float(np.mean([d[p] for d in draws])) for p in ("v0", "T", "a_max", "b", "s0")
        }
    sd = math.sqrt(IDMCalibration.load(MEASURED).cov[2][2])
    nominal = IDMCalibration.load(MEASURED).mean["a_max"]
    # the drawn means sit at the nominal ones, the k = 0 one lifted slightly by the 0.2 m/s² floor
    assert means[1.0]["a_max"] == pytest.approx(nominal + sd, abs=0.015)
    assert means[0.0]["a_max"] == pytest.approx(nominal, abs=0.03)
    assert means[1.0]["a_max"] - means[0.0]["a_max"] == pytest.approx(sd, abs=0.04)
    # the other parameters' drawn means do not move (same seed; correlated draws, small tolerance)
    for p in ("v0", "T", "b", "s0"):
        assert means[1.0][p] == pytest.approx(means[0.0][p], rel=0.02)
