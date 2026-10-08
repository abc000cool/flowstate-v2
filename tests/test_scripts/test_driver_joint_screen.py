"""scripts/driver_joint_screen.py: screen S1 of the Amendment-7 grid (docs/PRE_FRISCO_PROGRAM.md B6).

S1: the population's mean driver is string-unstable at its own capacity density
(``unstable_band``'s lower edge at or below the density of maximum equilibrium
flow, docs/DISCHARGE_CALIBRATION.md §4's measure).

Closed form. The equilibrium (and so the capacity density ρ*) does not depend on
``a_max``, and the IDM partials scale as f_s = a·S, f_v = −a·F, f_dv = √a·G with
S, F, G functions of the equilibrium alone (Treiber & Kesting 2013, ch. 15; the
derivation in ``validation.string_stability``). The criterion
f_v²/2 − f_v·f_dv − f_s = 0 is then a quadratic in √a:

    (F²/2)·a + F·G·√a − S = 0   ⇒   a_c = ((−G + √(G² + 2S)) / F)²

and a/a_c below 1 is unstable at ρ*, above 1 stable. A synthetic population with
``a_max = a_c`` has its band's lower edge exactly at ρ*; slightly below it passes
S1, slightly above it fails. These tests compute S, F, G from the IDM law
themselves, not from the module.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from flowstate_core.artifacts import IDMCalibration

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
BASE = REPO_ROOT / "artifacts" / "idm_i24_capacity.json"
GRID = REPO_ROOT / "artifacts" / "driver_joint_grid_i24.json"
SCREEN = REPO_ROOT / "artifacts" / "driver_joint_screen_i24.json"
STAGE = REPO_ROOT / "artifacts" / "driver_joint_2026-10-07" / "stage_p18_b6.sh.txt"
L = 5.0


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sc = _load("driver_joint_screen")


def _base_driver() -> dict[str, float]:
    return sc.mean_driver(IDMCalibration.load(BASE))


def _critical_a_max(p: dict[str, float], v: float, s: float) -> float:
    """a_c at the equilibrium (gap s, speed v): the module docstring's quadratic, from the IDM law."""
    v0, t_h, b, s0, delta = p["v0"], p["T"], p["b"], p["s0"], p["delta"]
    s_star = s0 + v * t_h
    big_s = 2.0 * s_star**2 / s**3  # f_s / a
    big_f = delta * v ** (delta - 1.0) / v0**delta + 2.0 * t_h * s_star / s**2  # -f_v / a
    big_g = s_star * v / (s**2 * math.sqrt(b))  # f_dv / sqrt(a)
    return ((-big_g + math.sqrt(big_g**2 + 2.0 * big_s)) / big_f) ** 2


def _a_c(p: dict[str, float]) -> tuple[float, float]:
    """``(a_c at the capacity density, the capacity density [veh/m])`` for ``p``'s other means."""
    _, v_star, rho_star = sc.capacity_point(p, L)
    return _critical_a_max(p, v_star, 1.0 / rho_star - L), rho_star


def test_the_capacity_point_meets_the_first_order_condition() -> None:
    """q = v/(s_e + L) is stationary at v*: s_e(v*) + L = v*·s_e'(v*), s_e' in closed form."""
    for t_h in (1.0616, 1.3222, 1.5828):
        p = {**_base_driver(), "T": t_h}
        q, v, rho = sc.capacity_point(p, L)
        v0, s0 = p["v0"], p["s0"]
        theta = 1.0 - (v / v0) ** 4
        s_e = (s0 + v * t_h) / math.sqrt(theta)
        ds_e = t_h / math.sqrt(theta) + 2.0 * (s0 + v * t_h) * v**3 / (v0**4 * theta**1.5)
        assert s_e + L == pytest.approx(v * ds_e, rel=1e-6)
        assert rho == pytest.approx(1.0 / (s_e + L), rel=1e-9)
        assert q == pytest.approx(v * rho, rel=1e-12)


def test_the_base_reproduces_the_committed_equilibrium_capacity() -> None:
    """artifacts/idm_i24_capacity_equilibrium.json: 1,985.5 veh/h/lane at 18.912 m/s (29.2 veh/km)."""
    row = sc.screen_driver(_base_driver())
    eq = json.loads((REPO_ROOT / "artifacts" / "idm_i24_capacity_equilibrium.json").read_text())
    rec = next(r for r in eq["populations"] if r["artifact"] == "artifacts/idm_i24_capacity.json")
    assert round(row["capacity_veh_h_lane"], 1) == rec["equilibrium_capacity_veh_h_lane"]
    assert round(row["v_at_capacity_ms"], 3) == rec["v_at_capacity_ms"]
    assert round(row["capacity_density_veh_km"], 1) == 29.2
    assert round(row["band_lo_veh_km"], 1) == 28.2  # docs/DISCHARGE_CALIBRATION.md §4
    assert row["passes"] is True


@pytest.mark.parametrize("t_h", [1.0616, 1.3222, 1.5828])
def test_at_the_critical_a_max_the_band_starts_at_the_capacity_density(t_h: float) -> None:
    p = {**_base_driver(), "T": t_h}
    a_c, rho_star = _a_c(p)
    edges = sc.band_edges({**p, "a_max": a_c}, L)
    assert edges["lo_refined"] is True
    assert edges["lo"] == pytest.approx(rho_star, rel=1e-6)
    # and the module's own margin vanishes there
    assert sc.stability_margin(rho_star, {**p, "a_max": a_c}, L) == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("t_h", [1.0616, 1.3222, 1.5828])
@pytest.mark.parametrize(("factor", "passes"), [(0.98, True), (1.02, False)])
def test_the_verdict_flips_at_the_closed_form_critical_a_max(
    t_h: float, factor: float, passes: bool
) -> None:
    p = {**_base_driver(), "T": t_h}
    a_c, _ = _a_c(p)
    row = sc.screen_driver({**p, "a_max": factor * a_c}, L)
    assert row["passes"] is passes
    assert row["unstable_at_capacity_density"] is passes
    margin = row["capacity_density_minus_band_lo_veh_km"]
    assert (margin >= 0.0) is passes


def test_a_driver_stable_at_every_density_fails() -> None:
    p = {**_base_driver(), "a_max": 5.0}
    row = sc.screen_driver(p, L)
    assert row["band_lo_veh_km"] is None and row["passes"] is False
    assert "stable everywhere" in row["reason"]


# --- end to end on synthetic populations -----------------------------------------------------


def _synthetic_grid(tmp: Path, a_max_by_k: dict[float, float]) -> Path:
    """A grid manifest over copies of the base with chosen mean a_max (j = 0 only)."""
    base = IDMCalibration.load(BASE)
    pairs = []
    for k, a in a_max_by_k.items():
        pop = base.model_copy(update={"mean": {**base.mean, "a_max": a}})
        path = tmp / f"pop_k{k}.json"
        pop.save(path)
        pairs.append(
            {
                "k": k,
                "j": 0,
                "a_max_mean_ms2": a,
                "T_mean_s": float(base.mean["T"]),
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    grid = tmp / "grid.json"
    grid.write_text(json.dumps({"schema": "flowstate.driver_joint_grid/1", "pairs": pairs}))
    return grid


def test_the_screen_and_the_stop_rule_on_synthetic_populations(tmp_path: Path) -> None:
    a_c, _ = _a_c(_base_driver())
    # k > 0 pair unstable at capacity: B6 goes on
    grid = _synthetic_grid(tmp_path, {0.0: 0.9 * a_c, 0.5: 0.95 * a_c, 1.0: 1.1 * a_c})
    art = sc.screen(grid, "2026-10-07T00:00:00Z")
    assert [r["passes"] for r in art["rows"]] == [True, True, False]
    assert art["stop_rule"]["fired"] is False
    assert art["stop_rule"]["pairs_passing_with_k_positive"] == [{"k": 0.5, "j": 0}]
    # only k = 0 unstable at capacity: the plan's Stop rule fires
    grid2 = _synthetic_grid(tmp_path / "b", {0.0: 0.9 * a_c, 0.5: 1.05 * a_c, 1.0: 1.1 * a_c})
    art2 = sc.screen(grid2, "t")
    assert [r["passes"] for r in art2["rows"]] == [True, False, False]
    assert art2["stop_rule"]["fired"] is True
    # --check-stop / --list-pass read the artifact
    out, out2 = tmp_path / "s.json", tmp_path / "s2.json"
    out.write_text(json.dumps(art))
    out2.write_text(json.dumps(art2))
    assert sc.main(["--out", str(out), "--check-stop"]) == 0
    assert sc.main(["--out", str(out2), "--check-stop"]) == sc.EXIT_STOP == 10


def test_the_screen_refuses_a_population_that_is_not_the_manifests(tmp_path: Path) -> None:
    grid = _synthetic_grid(tmp_path, {0.0: 1.0})
    (tmp_path / "pop_k0.0.json").write_text(
        (tmp_path / "pop_k0.0.json")
        .read_text()
        .replace('"schema_version": 1', '"schema_version":1')
    )
    with pytest.raises(ValueError, match="sha256"):
        sc.screen(grid, "t")


# --- the committed screen --------------------------------------------------------------------


def _close(a: object, b: object) -> bool:
    """Equal JSON values, floats to 1e-9 relative (platform libm differences)."""
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def test_the_committed_screen_reproduces() -> None:
    stored = json.loads(SCREEN.read_text())
    fresh = json.loads(json.dumps(sc.screen(GRID, stored["created_at"])))
    assert _close(fresh, stored)


def test_the_committed_screen_admits_k0_only_and_the_stop_rule_fires(
    capsys: pytest.CaptureFixture[str],
) -> None:
    art = json.loads(SCREEN.read_text())
    assert art["grid"]["sha256"] == hashlib.sha256(GRID.read_bytes()).hexdigest()
    rows = {(r["k"], r["j"]): r for r in art["rows"]}
    assert len(rows) == 25
    for (k, _j), r in rows.items():
        assert r["passes"] is (k == 0.0), (k, _j)
        assert r["passes"] is r["unstable_at_capacity_density"]
    # the nearest k > 0 miss: k = 0.25 at the lowest T, by more than half a veh/km
    near = rows[(0.25, -2)]["capacity_density_minus_band_lo_veh_km"]
    assert -1.0 < near < -0.5
    assert rows[(1.0, 2)]["band_lo_veh_km"] is None  # stable at every density
    assert art["stop_rule"]["fired"] is True
    assert sc.main(["--check-stop"]) == sc.EXIT_STOP
    capsys.readouterr()
    assert sc.main(["--list-pass"]) == 0
    listed = capsys.readouterr().out.split("\n")
    assert [line.split()[:2] for line in listed if line] == [
        ["0.0", str(j)] for j in (-2, -1, 0, 1, 2)
    ]


def _body(text: str, func: str) -> list[str]:
    """The lines of shell function ``func``'s body (up to its closing brace)."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{func}() {{"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return [line.strip() for line in lines[start + 1 : end] if line.strip()]


def test_the_stage_snippet_parses_and_runs_the_stop_guard_first() -> None:
    text = STAGE.read_text()
    r = subprocess.run(["bash", "-n"], input=text, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    for func in ("p18_steps", "p18b_steps"):
        assert _body(text, func)[0] == "p18_guard || return 1", func
    guard = _body(text, "p18_guard")
    assert guard[0] == "$RUN scripts/driver_joint_screen.py --check-stop"
    assert any('"$rc" -eq 10' in line and "return 1" in line for line in guard)
    assert sc.EXIT_STOP == 10
    # the scripts the snippet uses as built exist; the others are named as assumed or not built
    for script in (
        "scripts/driver_joint_screen.py",
        "scripts/driver_joint_grid.py",
        "scripts/calibrate_capacity.py",
        "scripts/i24_validate.py",
    ):
        assert script in text and (REPO_ROOT / script).is_file(), script
    header = text[: text.index("P18_ARM=")]
    assert "ASSUMED, being built by the B5 agent:" in header
    assert "scripts/fit_demand_level.py --corridor i24" in header
    for unbuilt in ("scripts/driver_joint_s2.py", "scripts/driver_joint_run.py"):
        assert unbuilt in header and not (REPO_ROOT / unbuilt).exists(), unbuilt


def test_the_grid_and_the_screen_agree_on_every_pair() -> None:
    grid = json.loads(GRID.read_text())
    art = json.loads(SCREEN.read_text())
    g = {(p["k"], p["j"]): p for p in grid["pairs"]}
    for r in art["rows"]:
        p = g[(r["k"], r["j"])]
        assert (r["population"], r["sha256"]) == (p["path"], p["sha256"])
        assert (r["a_max_mean_ms2"], r["T_mean_s"]) == (p["a_max_mean_ms2"], p["T_mean_s"])


def test_the_reproduction_block_reads_the_section_4_quotes() -> None:
    rep = json.loads(SCREEN.read_text())["reproduction"]
    on_grid = rep["computed_on_a_0.1_veh_km_grid"]
    quoted = copy.deepcopy(rep["quoted_veh_km"])
    for key in ("band_lo_k0", "band_lo_k1", "capacity_density"):
        assert on_grid[key] == quoted[key], key
    # §4's 32.5 at k = 0.5 is coarser than the refined edge's 0.1-veh/km reading
    assert rep["computed_veh_km"]["band_lo_k0.5"] == pytest.approx(32.31, abs=0.01)
