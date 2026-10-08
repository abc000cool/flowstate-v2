"""scripts/driver_joint_grid.py: the Amendment-7 joint driver grid (docs/PRE_FRISCO_PROGRAM.md B6).

25 derived populations, mean ``a_max`` = measured mean + k · sd(a_max) and mean
``T`` = the base's 1.322 s + j · 0.25 · sd(T), sd from ``artifacts/idm_i24.json``'s
covariance. The T step is computed, not typed (0.130302 s; the plan quotes 0.130).
Lineage: (0, 0) is the base, (k, 0) Amendment 1's files, the other 20 are written
with their provenance in ``notes`` and ``artifacts/driver_joint_grid_i24.json``;
the committed files are exactly the derivation, and the package lineage reader
resolves every population that moves one mean.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import json
import math
import shutil
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

from flowstate_core.artifacts import IDMCalibration
from flowstate_core.config import FleetSpec
from flowstate_core.rng import make_rng

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
BASE = REPO_ROOT / "artifacts" / "idm_i24_capacity.json"
MEASURED = REPO_ROOT / "artifacts" / "idm_i24.json"
MANIFEST = REPO_ROOT / "artifacts" / "driver_joint_grid_i24.json"
KW = {"base_label": "base (sha256 x)", "measured_label": "measured (sha256 y)"}


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


jg = _load("driver_joint_grid")
dp = _load("derive_population")


def _pops() -> tuple[IDMCalibration, IDMCalibration]:
    return IDMCalibration.load(BASE), IDMCalibration.load(MEASURED)


def _derive(k: float, j: int, created_at: str = "2026-10-07T00:00:00Z") -> IDMCalibration:
    base, measured = _pops()
    return jg.derive_joint(base, measured, k=k, j=j, created_at=created_at, **KW)


def test_the_t_step_is_a_quarter_of_the_measured_sd_of_t() -> None:
    _, measured = _pops()
    i = list(measured.param_names).index("T")
    sd_t = math.sqrt(measured.cov[i][i])
    assert jg.t_step(measured) == 0.25 * sd_t
    assert jg.t_step(measured) == pytest.approx(0.130302, abs=5e-7)
    assert round(jg.t_step(measured), 3) == jg.T_STEP_QUOTED_S == 0.130
    # the step is the k grid's: 0.25 sd, k in steps of 0.25
    assert np.diff(jg.K_GRID).tolist() == [0.25] * 4
    assert round(IDMCalibration.load(BASE).mean["T"], 3) == jg.T_BASE_QUOTED_S


@pytest.mark.parametrize("k", [0.0, 0.25, 0.5, 0.75, 1.0])
@pytest.mark.parametrize("j", [-2, -1, 0, 1, 2])
def test_the_derivation_moves_a_max_and_t_and_keeps_the_rest(k: float, j: int) -> None:
    base, measured = _pops()
    d = _derive(k, j)
    sd_a = math.sqrt(measured.cov[2][2])
    sd_t = math.sqrt(measured.cov[1][1])
    assert d.mean["a_max"] == measured.mean["a_max"] + k * sd_a
    assert d.mean["T"] == base.mean["T"] + j * 0.25 * sd_t
    assert d.cov == base.cov
    assert {n: d.mean[n] for n in ("v0", "b", "s0")} == {n: base.mean[n] for n in ("v0", "b", "s0")}
    assert (d.source, d.data_hash, d.n_episodes_fit, d.per_episode_rmse_m) == (
        base.source,
        base.data_hash,
        base.n_episodes_fit,
        base.per_episode_rmse_m,
    )
    for text in (
        "Amendment 7",
        f"(k, j) = ({k:g}, {j:+d})",
        "base (sha256 x)",
        "measured (sha256 y)",
        "2026-10-07T00:00:00Z",
        f"{jg.t_step(measured):.6f}",
    ):
        assert text in d.notes
    # both means inside protocol §7.2's measured range (measured mean ± 1 sd)
    for p in ("a_max", "T"):
        lo, hi = jg.measured_range(measured, p)
        assert lo <= d.mean[p] <= hi


@pytest.mark.parametrize("k", [0.25, 0.5, 0.75, 1.0])
def test_the_j0_row_is_amendment_1s_derivation(k: float) -> None:
    base, measured = _pops()
    one = dp.derive_shifted(
        base, measured, param="a_max", k=k, created_at="t", base_label="b", measured_label="m"
    )
    assert jg.same_population(_derive(k, 0), one)
    assert jg.same_population(
        _derive(k, 0),
        IDMCalibration.load(REPO_ROOT / "artifacts" / dp.out_name("idm_i24_capacity_amax", k)),
    )
    assert jg.same_population(_derive(0.0, 0), base)


def test_the_measured_ranges_are_the_protocols() -> None:
    _, measured = _pops()
    assert jg.measured_range(measured, "T") == pytest.approx((0.990, 2.032), abs=5e-4)
    assert jg.measured_range(measured, "a_max") == pytest.approx((0.626, 1.483), abs=5e-4)


def test_refusals() -> None:
    base, measured = _pops()
    kw = {"created_at": "t", **KW}
    with pytest.raises(ValueError, match="outside the measured mean"):
        jg.derive_joint(base, measured, k=1.25, j=0, **kw)
    # 1.322 - 3 x 0.1303 = 0.931 s < 0.990 s: outside T's measured range
    with pytest.raises(ValueError, match="measured range"):
        jg.derive_joint(base, measured, k=0.0, j=-3, **kw)
    moved = base.model_copy(update={"mean": {**base.mean, "a_max": 1.2}})
    with pytest.raises(ValueError, match="k = 0 would not be the base"):
        jg.derive_joint(moved, measured, k=0.5, j=0, **kw)
    cov = [list(r) for r in base.cov]
    cov[1][1] += 0.01
    with pytest.raises(ValueError, match="covariance"):
        jg.derive_joint(base.model_copy(update={"cov": cov}), measured, k=0.5, j=1, **kw)
    with pytest.raises(ValueError, match="source or data hash"):
        jg.derive_joint(base.model_copy(update={"data_hash": "x"}), measured, k=0.5, j=1, **kw)


def test_naming_keeps_the_amendment_1_glob_to_one_shift_populations() -> None:
    """tests/test_validation/test_validation_uncertainty.py globs idm_i24_capacity_amax_k*.json
    and requires each to be one a_max shift of the base: no joint file may match it."""
    kw = {"base": str(BASE), "amax_dir": "artifacts", "out_dir": "artifacts"}
    assert jg.population_path(0.0, 0, **kw) == "artifacts/idm_i24_capacity.json"
    assert jg.population_path(0.5, 0, **kw) == "artifacts/idm_i24_capacity_amax_k0.5.json"
    assert jg.population_path(0.5, -1, **kw) == "artifacts/idm_i24_capacity_joint_k0.5_j-1.json"
    assert jg.population_path(0.0, 2, **kw) == "artifacts/idm_i24_capacity_joint_k0.0_j+2.json"
    for k in jg.K_GRID:
        for j in jg.J_GRID:
            name = Path(jg.population_path(k, j, **kw)).name
            assert fnmatch.fnmatch(name, "idm_i24_capacity_amax_k*.json") == (k > 0 and j == 0)


# --- the committed grid ----------------------------------------------------------------------


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def test_the_committed_manifest_lists_the_25_pairs_and_their_files() -> None:
    m = _manifest()
    assert m["schema"] == "flowstate.driver_joint_grid/1"
    pairs = {(p["k"], p["j"]): p for p in m["pairs"]}
    assert set(pairs) == {(k, j) for k in jg.K_GRID for j in jg.J_GRID}
    assert m["steps"]["T_step_s"] == jg.t_step(IDMCalibration.load(MEASURED))
    assert m["steps"]["T_step_derivation"]["matches_quote"] is True
    assert m["base"]["sha256"] == jg.file_sha256(BASE)
    assert m["measured"]["sha256"] == jg.file_sha256(MEASURED)
    written = 0
    for (k, j), p in pairs.items():
        path = REPO_ROOT / p["path"]
        assert jg.file_sha256(path) == p["sha256"], p["path"]
        stored = IDMCalibration.load(path)
        assert jg.same_population(stored, _derive(k, j)), p["path"]
        assert (stored.mean["a_max"], stored.mean["T"]) == (p["a_max_mean_ms2"], p["T_mean_s"])
        if j != 0:
            written += 1
            assert p["origin"] == "scripts/driver_joint_grid.py"
            notes = stored.notes
            assert f"artifacts/idm_i24_capacity.json (sha256 {jg.file_sha256(BASE)})" in notes
            assert f"artifacts/idm_i24.json (sha256 {jg.file_sha256(MEASURED)})" in notes
            assert f"(k, j) = ({k:g}, {j:+d})" in notes
    assert written == 20


def test_the_committed_manifest_reproduces() -> None:
    stored = _manifest()
    fresh = jg.build(
        BASE,
        MEASURED,
        amax_dir=REPO_ROOT / "artifacts",
        out_dir=REPO_ROOT / "artifacts",
        created_at=stored["created_at"],
        write=False,
    )
    assert jg.without_package_lineage(fresh) == jg.without_package_lineage(stored)
    assert jg.main(["--check"]) == 0


def test_every_population_that_moves_one_mean_resolves_to_the_measured_source() -> None:
    """validation.uncertainty.population_lineage (one mean per step): the k = 0 and j = 0 pairs
    resolve to artifacts/idm_i24.json, with protocol §7.2's ranges from it; the manifest
    records the reader's answer for every pair as of the derivation."""
    from validation.uncertainty import population_lineage

    m = _manifest()
    for p in m["pairs"]:
        if p["k"] == 0.0 or p["j"] == 0:
            found = population_lineage(p["path"])
            assert found is not None, p["path"]
            assert found.source.label.startswith("artifacts/idm_i24.json (sha256 ")
            assert p["package_lineage"]["source"] == "artifacts/idm_i24.json"
        else:
            assert p["package_lineage"] is None
            assert (
                "do not resolve this population" in IDMCalibration.load(REPO_ROOT / p["path"]).notes
            )
    assert "16 pairs with k > 0" in m["package_lineage_note"]


def test_main_writes_twenty_files_and_check_catches_a_changed_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "pops"
    manifest = tmp_path / "grid.json"
    common = ["--out-dir", str(out), "--manifest", str(manifest)]
    assert jg.main([*common, "--created-at", "2026-10-07T00:00:00Z"]) == 0
    assert len(list(out.glob("idm_i24_capacity_joint_k*_j*.json"))) == 20
    m = json.loads(manifest.read_text())
    assert m["created_at"] == "2026-10-07T00:00:00Z"
    assert {p["origin"] for p in m["pairs"] if p["j"] == 0} == {
        "base",
        "scripts/derive_population.py (Amendment 1)",
    }
    assert jg.main([*common, "--check"]) == 0
    victim = out / jg.joint_name(0.5, -1)
    shutil.copy(out / jg.joint_name(0.5, 1), victim)
    assert jg.main([*common, "--check"]) == 1
    assert "is not the derivation of pair (0.5, -1)" in capsys.readouterr().out


def test_drawing_drivers_from_a_joint_population_moves_both_means() -> None:
    """The runner's draw (microsim.vehicles._draw_from_calibration: ±3σ marginals and the
    0.4-s T floor) moves the drawn means with the population's. The T floor sits 1.3 sd below
    the j = -2 mean (1.06 s) and 1.8 sd below the base's, so the truncation lifts the low-T
    population's drawn mean more than the base's: the drawn shift of j = -2 is about three
    quarters of the nominal one. S1 screens the nominal mean driver, as §4 did."""
    from microsim.vehicles import draw_vehicle_params

    _, measured = _pops()
    n = 20000
    means = {}
    for key, path in (
        ("base", BASE),
        ("joint", REPO_ROOT / "artifacts" / jg.joint_name(1.0, -2)),
    ):
        draws = draw_vehicle_params(FleetSpec(idm_calibration=str(path)), n, make_rng(11))
        means[key] = {p: float(np.mean([d[p] for d in draws])) for p in ("T", "a_max", "v0")}
    sd_a = math.sqrt(measured.cov[2][2])
    nominal_t = -2 * jg.t_step(measured)
    assert means["joint"]["a_max"] - means["base"]["a_max"] == pytest.approx(sd_a, abs=0.04)
    drawn_t = means["joint"]["T"] - means["base"]["T"]
    assert drawn_t < 0.0 and 0.6 * abs(nominal_t) < abs(drawn_t) < 0.9 * abs(nominal_t)
    assert means["joint"]["v0"] == pytest.approx(means["base"]["v0"], rel=0.02)
