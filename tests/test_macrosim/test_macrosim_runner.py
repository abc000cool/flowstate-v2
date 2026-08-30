"""Tests for macrosim.runner.run_macro — contract outputs (docs/CONTRACTS.md §3)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from flowstate_core.config import (
    AVSpec,
    CorridorNetwork,
    OSMNetwork,
    PerturbationSpec,
    RingNetwork,
    ScenarioConfig,
    SimSpec,
    config_hash,
)
from macrosim.runner import run_macro


def _corridor_cfg(**overrides: Any) -> ScenarioConfig:
    base: dict[str, Any] = {
        "name": "corridor_macro_test",
        "tier": "macro",
        "network": CorridorNetwork(length_m=10_000.0, lanes=1, inflow=[(0.0, 0.5)]),
        "sim": SimSpec(duration_s=600.0, step_length_s=0.5, output_hz=0.2),
        "seed": 11,
        "replicates": 1,
    }
    base.update(overrides)
    return ScenarioConfig(**base)


def _ring_cfg(**overrides: Any) -> ScenarioConfig:
    base: dict[str, Any] = {
        "name": "ring_macro_test",
        "tier": "macro",
        "network": RingNetwork(circumference_m=5000.0, n_vehicles=100),
        "sim": SimSpec(duration_s=300.0, step_length_s=0.5, output_hz=0.5),
        "seed": 11,
        "replicates": 1,
    }
    base.update(overrides)
    return ScenarioConfig(**base)


def _read(run_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    edges = pd.read_parquet(run_dir / "edges.parquet")
    meta = json.loads((run_dir / "meta.json").read_text())
    return edges, meta


def test_contract_layout_and_meta(tmp_path: Path) -> None:
    """Run dir is <out>/<config_hash>/<seed>/ with edges.parquet + meta.json."""
    cfg = _corridor_cfg()
    run_dir = run_macro(cfg, seed=11, out_dir=tmp_path)
    assert run_dir == tmp_path / config_hash(cfg) / "11"
    edges, meta = _read(run_dir)

    assert list(edges.columns) == ["t_bin", "x_bin", "mean_speed", "density", "flow"]
    assert len(edges) > 0
    assert (edges["density"] >= 0).all()
    assert (edges["mean_speed"] >= 0).all()

    assert meta["tier"] == "screening"  # CLAUDE.md §5.6 — macro is never validation
    assert meta["seeded"] is False
    assert meta["config_hash"] == config_hash(cfg)
    assert meta["seed"] == 11
    assert meta["clamped"] is False
    assert meta["fuel_total_ml"] is None
    for key in ("python", "numpy", "numba", "flowstate_core", "macrosim"):
        assert key in meta["versions"]
    # Config snapshot must round-trip to the exact config that ran.
    assert ScenarioConfig.model_validate(meta["config"]) == cfg


def test_ledger_balances_in_meta(tmp_path: Path) -> None:
    """meta.json ledger satisfies inflow − outflow − storage = 0 (empty start)."""
    cfg = _corridor_cfg()
    _, meta = _read(run_macro(cfg, seed=1, out_dir=tmp_path))
    led = meta["ledger"]
    assert abs(led["vehicles_in"] - led["vehicles_out"] - led["stored_veh"]) < 1e-8


def test_perturbation_labels_seeded_and_creates_congestion(tmp_path: Path) -> None:
    """A PerturbationSpec run is labeled seeded=True and shows a local jam.

    The perturbation is a temporary local capacity/speed reduction (see
    macrosim.runner docstring) — density just upstream of it during the
    window must exceed the pre-perturbation level.
    """
    cfg = _corridor_cfg(
        network=CorridorNetwork(length_m=10_000.0, lanes=1, inflow=[(0.0, 0.6)]),
        sim=SimSpec(duration_s=900.0, step_length_s=0.5, output_hz=0.2),
        perturbation=PerturbationSpec(
            t_s=400.0, position_m=7000.0, duration_s=150.0, v_drop_ms=25.0
        ),
    )
    edges, meta = _read(run_macro(cfg, seed=5, out_dir=tmp_path))
    assert meta["seeded"] is True

    # Compare a window just upstream of the perturbation, after the corridor
    # has filled (the 0.6 veh/s inflow front reaches 7 km at t ≈ 252 s).
    near = (edges["x_bin"] > 6400.0) & (edges["x_bin"] < 7000.0)
    during = edges[near & (edges["t_bin"] > 440.0) & (edges["t_bin"] < 550.0)]
    before = edges[near & (edges["t_bin"] > 300.0) & (edges["t_bin"] < 390.0)]
    assert during["density"].mean() > 2.0 * before["density"].mean()
    assert during["mean_speed"].mean() < 0.5 * before["mean_speed"].mean()


def test_same_seed_reproduces_identical_output(tmp_path: Path) -> None:
    """Same config + seed ⇒ bit-identical edge data (CLAUDE.md §0.5)."""
    cfg = _ring_cfg(av=AVSpec(penetration=0.05, compliance=0.5))
    e1, _ = _read(run_macro(cfg, seed=21, out_dir=tmp_path / "a", v_star_ms=8.0))
    e2, _ = _read(run_macro(cfg, seed=21, out_dir=tmp_path / "b", v_star_ms=8.0))
    pd.testing.assert_frame_equal(e1, e2)


def test_fixed_v_star_av_perturbs_ring(tmp_path: Path) -> None:
    """With a fixed-v* moving bottleneck the uniform ring state breaks symmetry."""
    base = _ring_cfg()
    edges_base, meta_base = _read(run_macro(base, seed=3, out_dir=tmp_path / "base"))
    cfg = _ring_cfg(av=AVSpec(penetration=0.05, compliance=1.0))
    edges_av, meta_av = _read(run_macro(cfg, seed=3, out_dir=tmp_path / "av", v_star_ms=8.0))

    assert meta_base["av"]["n_avs"] == 0
    assert meta_av["av"]["n_avs"] == max(1, round(0.05 * 100))
    assert meta_av["av"]["n_complied"] == meta_av["av"]["n_avs"]

    t_last_base = edges_base["t_bin"].max()
    t_last_av = edges_av["t_bin"].max()
    std_base = edges_base[edges_base["t_bin"] == t_last_base]["density"].std()
    std_av = edges_av[edges_av["t_bin"] == t_last_av]["density"].std()
    assert std_base < 1e-12  # no-AV uniform ring stays uniform (LWR equilibrium)
    assert std_av > 1e-3


def test_controller_fallback_when_controllers_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the controllers package unimportable, v_star_ms is required and noted.

    The controllers package may be built concurrently with this one; the
    runner must degrade gracefully (lazy import). Blocking the import
    simulates the not-yet-built state deterministically.
    """
    monkeypatch.setitem(sys.modules, "controllers", None)  # forces ImportError
    cfg = _ring_cfg(av=AVSpec(penetration=0.05, controller="follower_stopper"))

    with pytest.raises(ValueError, match="v_star_ms"):
        run_macro(cfg, seed=2, out_dir=tmp_path / "x")

    _, meta = _read(run_macro(cfg, seed=2, out_dir=tmp_path / "y", v_star_ms=8.0))
    assert meta["av"]["controller_applied"] is False
    assert any("unavailable" in note for note in meta["notes"])


def test_controller_driven_av_when_registry_available(tmp_path: Path) -> None:
    """If the controllers registry resolves, the controller drives the AV."""
    try:
        from controllers import registry

        registry.get_vehicle_controller("follower_stopper")
        registry.default_params("follower_stopper")
    except Exception:
        pytest.skip("controllers registry not available yet (built concurrently)")

    cfg = _ring_cfg(av=AVSpec(penetration=0.05, compliance=1.0, controller="follower_stopper"))
    _, meta = _read(run_macro(cfg, seed=9, out_dir=tmp_path))
    assert meta["av"]["controller_applied"] is True
    assert meta["clamped"] is False


def test_multilane_corridor_scales_jam_storage(tmp_path: Path) -> None:
    """lanes > 1 runs as an effective single pipe with scaled ρ_jam (noted)."""
    cfg = _corridor_cfg(network=CorridorNetwork(length_m=10_000.0, lanes=2, inflow=[(0.0, 0.8)]))
    _, meta = _read(run_macro(cfg, seed=4, out_dir=tmp_path))
    _, meta1 = _read(run_macro(_corridor_cfg(), seed=4, out_dir=tmp_path / "l1"))
    assert meta["fd"]["rho_jam"] == pytest.approx(2 * meta1["fd"]["rho_jam"])
    assert any("single-pipe" in note for note in meta["notes"])


def test_osm_network_not_supported(tmp_path: Path) -> None:
    cfg = ScenarioConfig(
        name="osm_macro",
        tier="macro",
        network=OSMNetwork(osm_file="corridor.osm", corridor_edges=["e1"]),
        sim=SimSpec(duration_s=60.0),
    )
    with pytest.raises(NotImplementedError, match="ring and corridor"):
        run_macro(cfg, seed=1, out_dir=tmp_path)


def test_prescribed_v_star_trajectories_bind_and_differ_by_variant(tmp_path: Path) -> None:
    """Prescribed playback congests upstream and discriminates the variants.

    A slow prescribed trajectory (v* far below V_e at the ambient density)
    must (a) bind — recorded in the meta diagnostics, (b) raise upstream
    density relative to the free baseline, and (c) produce different fields
    under ``flux_cap`` vs ``capacity`` (the CLAUDE.md §5.5 comparison is only
    meaningful when the constraint distinguishes the variants).
    """
    import numpy as np

    from macrosim.bottleneck import VStarTrajectory

    cfg = _corridor_cfg(
        network=CorridorNetwork(length_m=5000.0, lanes=1, inflow=[(0.0, 0.45)]),
        sim=SimSpec(duration_s=400.0, step_length_s=0.5, output_hz=0.2),
    )
    traj = VStarTrajectory(
        t_s=np.array([150.0, 350.0]),
        x_m=np.array([2000.0, 2000.0 + 4.0 * 200.0]),
        v_ms=np.array([4.0, 4.0]),
    )
    base_dir = run_macro(cfg, seed=7, out_dir=tmp_path / "base")
    flux_dir = run_macro(
        cfg, seed=7, out_dir=tmp_path / "flux", prescribed_avs=[traj], bottleneck_variant="flux_cap"
    )
    cap_dir = run_macro(
        cfg,
        seed=7,
        out_dir=tmp_path / "cap",
        prescribed_avs=[traj],
        bottleneck_variant="capacity",
    )
    e_base, _ = _read(base_dir)
    e_flux, m_flux = _read(flux_dir)
    e_cap, m_cap = _read(cap_dir)

    pres = m_flux["av"]["prescribed"]
    assert pres["n_trajectories"] == 1
    assert pres["active_av_steps"] > 0
    assert pres["binding_fraction"] > 0.9  # v*=4 m/s << free-flow V_e
    assert m_flux["tier"] == "screening"
    assert m_cap["av"]["prescribed"]["n_trajectories"] == 1

    def shadow_density(e: pd.DataFrame) -> float:
        # The queue trails the moving AV inside its swept band (2000-2800 m).
        sel = e[(e.t_bin > 200.0) & (e.t_bin <= 350.0) & (e.x_bin > 2000.0) & (e.x_bin < 2800.0)]
        return float(sel.density.mean())

    def downstream_density(e: pd.DataFrame) -> float:
        sel = e[(e.t_bin > 200.0) & (e.t_bin <= 350.0) & (e.x_bin > 3000.0) & (e.x_bin < 4500.0)]
        return float(sel.density.mean())

    assert shadow_density(e_flux) > 1.5 * shadow_density(e_base)
    assert downstream_density(e_flux) < 0.9 * downstream_density(e_base)
    # The two variants must NOT be identical when the constraint binds.
    assert not np.allclose(e_flux.density.to_numpy(), e_cap.density.to_numpy())


def test_prescribed_avs_suppress_config_av_block(tmp_path: Path) -> None:
    """With prescribed trajectories the config's own AVs are not actuated."""
    import numpy as np

    from macrosim.bottleneck import VStarTrajectory

    cfg = _corridor_cfg(
        network=CorridorNetwork(length_m=2000.0, lanes=1, inflow=[(0.0, 0.3)]),
        sim=SimSpec(duration_s=60.0, step_length_s=0.5, output_hz=0.5),
        av=AVSpec(penetration=0.05, compliance=1.0, controller=None),
    )
    traj = VStarTrajectory(
        t_s=np.array([0.0, 60.0]),
        x_m=np.array([500.0, 800.0]),
        v_ms=np.array([5.0, 5.0]),
    )
    _, meta = _read(run_macro(cfg, seed=3, out_dir=tmp_path, v_star_ms=8.0, prescribed_avs=[traj]))
    assert meta["av"]["n_avs"] == 0
    assert meta["av"]["prescribed"]["n_trajectories"] == 1
    assert any("prescribed" in n for n in meta["notes"])


def test_runner_respects_cfl_for_small_cells(tmp_path: Path) -> None:
    """The runner shrinks dt below the config step when CFL requires it."""
    cfg = _corridor_cfg(
        network=CorridorNetwork(length_m=1000.0, lanes=1, inflow=[(0.0, 0.3)]),
        sim=SimSpec(duration_s=60.0, step_length_s=1.0, output_hz=1.0),
    )
    _, meta = _read(run_macro(cfg, seed=1, out_dir=tmp_path, dx_m=20.0))
    dt = meta["grid"]["dt_s"]
    dx = meta["grid"]["dx_m"]
    assert dt <= dx / meta["fd"]["v_f"] + 1e-12
