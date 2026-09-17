"""Golden regressions for the macro (screening) tier (CLAUDE.md §9).

GOLDEN UPDATE RULE: a change to any ``tests/golden/macro_*.json`` must come
with a PR note explaining the physics or code change that moved the numbers.
Never edit a golden by hand — its values are produced by
:func:`macrosim.run_macro` + :func:`api.results.macro_metrics` (the
screening-tier reduction of :class:`validation.metrics.Metrics` from the
binned ``edges.parquet``), see ``tests/golden/README.md`` and
:func:`regenerate` below.

Cases (:data:`CASES`), both labeled ``seeded=True``:

``macro_corridor``
    A 10 km single-lane corridor with the ``corridor_10km`` demand steps and
    a 120 s capacity drop at 7 km (:class:`PerturbationSpec`) on the
    ``v1_legacy`` FD: the shock produces a backward-propagating queue, so the
    wave statistics are non-trivial. It pins the solver, the inflow boundary,
    the perturbation cap, the Edie-style field output and the metric
    reduction.

``macro_corridor_workzone``
    A temporary lane closure (:class:`LaneClosureSpec`) — the second path
    that caps interface flux, and the only one the perturbation case does not
    touch. It pins the single-pipe closure treatment of
    :func:`macrosim.run_macro` (the overlapped cells' interfaces capped at
    ``q_max × share of lanes left open``), the cells it selects, and the
    resulting shock.

    Its config is the ``closure_run`` config of
    ``tests/test_microsim/test_microsim_closures_heavy.py`` — 3 km, two
    lanes, a 20 % heavy population, the right lane closed for a 500 m span
    over 120–210 s, 300 sim-s, seed 11 — moved to ``tier: macro`` with two
    documented departures, both forced by the effective-single-pipe
    treatment (CLAUDE.md §10) and without which the case would pin nothing
    about closures:

    * **Inflow 0.5 → 1.4 veh/s.** The macro tier carries both lanes as one
      pipe whose capacity is 1.48 veh/s, so the micro case's 0.5 veh/s is
      34 % of capacity and stays below the closure's 50 % cap (0.74 veh/s):
      the cap never binds and the field is exactly free-flow (measured
      σ_v ≈ 1e-14 m/s, no waves). 1.4 veh/s is 95 % of capacity, so the
      closed lane is the binding constraint.
    * **Span 0.2–0.7 km → 2.0–2.5 km.** At 0.2 km the queue reaches the
      corridor entry within seconds and the front is pinned at the boundary
      (it is then recorded in the inflow-queue ledger, and no front speed is
      measurable). Placed at 2.0 km the queue has 2 km of corridor to
      propagate back through, and the detector measures the Rankine–Hugoniot
      shock of the capped interface (17.4 km/h upstream analytically for
      these two states; the detector reports backward speeds as positive
      magnitudes, and :func:`test_workzone_closure_binds_and_is_recorded`
      asserts the two agree — see the ``run`` block's closure counters and
      ``metrics.wave_speed_kmh`` in the golden).

    The heavy population is kept verbatim although the macro tier does not
    represent it (a calibrated fundamental diagram already embeds the vehicle
    mix): :func:`test_macro_tier_does_not_represent_heavy_vehicles` pins that
    the runner says so in ``meta.json`` instead of silently ignoring it.

Tolerance: relative 1e-9 on floats, exact on integers. The tier is pure
numpy/numba arithmetic with no RNG in the PDE (the seed only feeds AV
compliance draws, unused here), so the only admissible drift is
floating-point summation order across numpy/numba releases (~1e-15); 1e-9
leaves margin for that and nothing else.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from api.results import macro_metrics
from flowstate_core.artifacts import TriangularFD
from flowstate_core.config import (
    CorridorNetwork,
    PerturbationSpec,
    ScenarioConfig,
    SimSpec,
    config_hash,
)
from flowstate_core.units import ms_to_kmh
from macrosim.runner import run_macro

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden"
GOLDEN_SCHEMA_VERSION = 1

#: Relative tolerance on floating-point statistics (see module docstring).
REL_TOL = 1e-9

#: Integer statistics compared exactly.
EXACT_KEYS = frozenset({"wave_count", "n_cells", "n_closures", "n_closure_cells"})

#: Heavy-vehicle population of the work-zone case, verbatim from
#: ``tests/test_microsim/test_microsim_closures_heavy.py`` (a 20.5 m Euro-VI
#: tractor-trailer). Not represented on this tier — see the module docstring.
HEAVY_POPULATION: dict[str, Any] = {
    "fraction": 0.2,
    "length_m": 20.5,
    "emission_class": "HBEFA4/TT_AT_gt34-40t_Euro-VI_A-C",
    "v0": 28.0,
    "T": 1.8,
    "a_max": 0.5,
    "b": 1.5,
    "s0": 3.0,
}


@dataclasses.dataclass(frozen=True)
class GoldenCase:
    """One macro golden case.

    Attributes:
        factory: Builds the exact :class:`ScenarioConfig` the golden pins.
        scenario: Where the config comes from, for the golden file.
        description: One line for the golden file and ``tests/golden/README.md``.
    """

    factory: Callable[[], ScenarioConfig]
    scenario: str
    description: str


def golden_config() -> ScenarioConfig:
    """The ``macro_corridor`` scenario (its hash is recorded in the golden file)."""
    return ScenarioConfig(
        name="macro_corridor_golden",
        tier="macro",
        network=CorridorNetwork(
            length_m=10_000.0,
            lanes=1,
            inflow=[(0.0, 0.45), (120.0, 0.50), (1080.0, 0.40)],
        ),
        sim=SimSpec(duration_s=1200.0, step_length_s=0.5, output_hz=0.2),
        perturbation=PerturbationSpec(
            t_s=600.0, position_m=7000.0, duration_s=120.0, v_drop_ms=25.0
        ),
        seed=42,
        replicates=1,
    )


def workzone_config() -> ScenarioConfig:
    """The ``macro_corridor_workzone`` scenario: a lane closure on the macro tier.

    The micro closure case's config (``closure_run`` of
    ``tests/test_microsim/test_microsim_closures_heavy.py``) at ``tier:
    macro``; the module docstring states and justifies the two departures
    (inflow and span).
    """
    return ScenarioConfig.model_validate(
        {
            "name": "macro_corridor_workzone",
            "tier": "macro",
            "network": {"kind": "corridor", "length_m": 3000.0, "lanes": 2, "inflow": [[0.0, 1.4]]},
            "fleet": {"heavy": HEAVY_POPULATION},
            "sim": {"duration_s": 300.0},
            "seed": 11,
            "closures": [
                {
                    "label": "work zone",
                    "start_m": 2000.0,
                    "end_m": 2500.0,
                    "lanes": [0],
                    "t_start_s": 120.0,
                    "t_end_s": 210.0,
                }
            ],
        }
    )


#: Golden cases by name; the case's config is :func:`case_config`.
CASES: dict[str, GoldenCase] = {
    "macro_corridor": GoldenCase(
        golden_config,
        "golden_config() in tests/test_macrosim/test_macrosim_golden.py",
        "10 km single-lane corridor, corridor_10km demand steps, seeded 120 s "
        "capacity drop at 7 km (v1_legacy FD), 1200 sim-s, seed 42",
    ),
    "macro_corridor_workzone": GoldenCase(
        workzone_config,
        "workzone_config() in tests/test_macrosim/test_macrosim_golden.py",
        "3 km two-lane corridor, 1.4 veh/s, 20 % heavy population (not represented "
        "on this tier), right lane closed 2.0-2.5 km for 120-210 s (seeded=True), "
        "300 sim-s, seed 11",
    ),
}


def case_config(case: str) -> ScenarioConfig:
    """Scenario config for a golden case (the config whose hash the golden records).

    Args:
        case: Key of :data:`CASES`.

    Returns:
        The config built by the case's factory.
    """
    return CASES[case].factory()


def _json_float(value: float) -> float | None:
    """NaN → ``None`` so the golden file is strict JSON."""
    return None if isinstance(value, float) and math.isnan(value) else value


def summarize(run_dir: Path) -> dict[str, Any]:
    """Summary statistics of one completed macro run, in golden-file layout.

    The ``run`` block gains the four closure keys only when the config
    declares closures, so the closure-free golden's key set is unchanged.

    Args:
        run_dir: Run directory holding ``edges.parquet`` and ``meta.json``.

    Returns:
        ``config_hash``, ``seed``, ``tier``, ``seeded``, ``versions``,
        ``config`` (snapshot), ``run`` (grid, FD, ledger, clamp flag, closure
        counters) and ``metrics`` (screening
        :class:`validation.metrics.Metrics` as a dict).
    """
    meta = json.loads((run_dir / "meta.json").read_text())
    metrics = macro_metrics(run_dir)
    run: dict[str, Any] = {
        "n_cells": meta["grid"]["n_cells"],
        "dx_m": meta["grid"]["dx_m"],
        "dt_s": meta["grid"]["dt_s"],
        "fd_preset": meta["fd"]["preset"],
        "fd_v_f": meta["fd"]["v_f"],
        "fd_w": meta["fd"]["w"],
        "fd_rho_jam": meta["fd"]["rho_jam"],
        "clamped": meta["clamped"],
        "vehicles_in": meta["ledger"]["vehicles_in"],
        "vehicles_out": meta["ledger"]["vehicles_out"],
        "stored_veh": meta["ledger"]["stored_veh"],
    }
    if meta["closures"]:
        run["n_closures"] = len(meta["closures"])
        run["n_closure_cells"] = sum(len(c["cells"]) for c in meta["closures"])
        run["closure_capacity_share_open"] = min(
            float(c["capacity_share_open"]) for c in meta["closures"]
        )
        run["queue_veh"] = meta["ledger"]["queue_veh"]
    return {
        "config_hash": meta["config_hash"],
        "seed": meta["seed"],
        "tier": meta["tier"],
        "seeded": meta["seeded"],
        "versions": meta["versions"],
        "config": meta["config"],
        "run": run,
        "metrics": {k: _json_float(v) for k, v in dataclasses.asdict(metrics).items()},
    }


def _assert_stats_match(section: str, expected: dict[str, Any], actual: dict[str, Any]) -> None:
    """Compare one golden section: exact for ints/bools/str/None, ``REL_TOL`` for floats."""
    assert set(actual) == set(expected), f"{section}: key set changed"
    for key, exp in expected.items():
        act = actual[key]
        label = f"{section}.{key}"
        if exp is None or isinstance(exp, bool | str) or key in EXACT_KEYS:
            assert act == exp, f"{label}: golden {exp!r}, got {act!r}"
        else:
            assert act == pytest.approx(exp, rel=REL_TOL), (
                f"{label}: golden {exp!r}, got {act!r} (rel tol {REL_TOL:g})"
            )


def assert_matches_golden(golden: dict[str, Any], actual: dict[str, Any]) -> None:
    """Assert a fresh run summary reproduces the golden file.

    Args:
        golden: Parsed golden JSON.
        actual: Output of :func:`summarize` for the re-run.
    """
    assert golden["schema_version"] == GOLDEN_SCHEMA_VERSION
    assert actual["config_hash"] == golden["config_hash"], (
        "config hash changed: the case factory or the config schema no longer "
        "describes the golden experiment; regenerate per tests/golden/README.md "
        "with a PR note explaining the change"
    )
    for key in ("seed", "tier", "seeded"):
        assert actual[key] == golden[key], key
    _assert_stats_match("run", golden["run"], actual["run"])
    _assert_stats_match("metrics", golden["metrics"], actual["metrics"])


def regenerate(case: str, work_root: Path) -> Path:
    """Re-run a golden case and rewrite its golden file.

    Args:
        case: Key of :data:`CASES`.
        work_root: Scratch run-tree root for the artifacts.

    Returns:
        Path of the written golden file.
    """
    spec = CASES[case]
    cfg = case_config(case)
    run_dir = run_macro(cfg, cfg.seed, work_root)
    payload: dict[str, Any] = {
        "schema_version": GOLDEN_SCHEMA_VERSION,
        "case": case,
        "description": spec.description,
        "scenario": spec.scenario,
        "producer": "macrosim.run_macro (v1_legacy FD) + api.results.macro_metrics",
        "regenerate": (
            f"uv run --no-sync python tests/test_macrosim/test_macrosim_golden.py "
            f"--regenerate {case}"
        ),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tolerance": {"relative": REL_TOL, "exact": sorted(EXACT_KEYS)},
        **summarize(run_dir),
    }
    out = GOLDEN_DIR / f"{case}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n")
    return out


@pytest.mark.parametrize("case", sorted(CASES))
def test_golden_summary_reproduced(case: str, tmp_path: Path) -> None:
    """Fixed (config, seed) reproduces the stored screening statistics."""
    golden_path = GOLDEN_DIR / f"{case}.json"
    golden = json.loads(golden_path.read_text())
    cfg = case_config(case)
    assert config_hash(cfg) == golden["config_hash"], (
        f"{golden_path.name}: config hash {config_hash(cfg)} != golden "
        f"{golden['config_hash']} — the case factory or the config schema changed; "
        "regenerate per tests/golden/README.md with a PR note"
    )
    run_dir = run_macro(cfg, cfg.seed, tmp_path)
    actual = summarize(run_dir)
    assert actual["tier"] == "screening"  # CLAUDE.md §5.6 label
    assert actual["run"]["clamped"] is False
    assert_matches_golden(golden, actual)


def test_workzone_closure_binds_and_is_recorded(tmp_path: Path) -> None:
    """The work-zone case's closure actually caps flux, and meta.json says how.

    Guards the case against silently degenerating into the free-flow run it
    would be at the micro case's inflow (module docstring): the 50 % capacity
    cap must be below the demand, the capped cells must be the overlapped
    ones, the same corridor without the closure must stay wave-free, and the
    front the closure raises must travel upstream at the Rankine–Hugoniot
    speed of the two states it separates.
    """
    cfg = workzone_config()
    run_dir = run_macro(cfg, cfg.seed, tmp_path)
    meta = json.loads((run_dir / "meta.json").read_text())
    (closure,) = meta["closures"]
    assert closure["label"] == "work zone"
    assert closure["lanes"] == [0]
    assert closure["capacity_share_open"] == pytest.approx(0.5)  # 1 of 2 lanes open
    dx = meta["grid"]["dx_m"]
    assert closure["cells"] == [20, 21, 22, 23, 24]  # 2000-2500 m at dx = 100 m
    assert all(2000.0 <= (i + 0.5) * dx < 2500.0 for i in closure["cells"])
    fd = TriangularFD(v_f=meta["fd"]["v_f"], w=meta["fd"]["w"], rho_jam=meta["fd"]["rho_jam"])
    demand = cfg.network.inflow[0][1]
    q_closed = fd.q_max * closure["capacity_share_open"]
    assert q_closed < demand < fd.q_max, (
        "the closure cap must bind: demand must exceed the open lanes' capacity "
        "and stay below the unclosed pipe's capacity"
    )

    # The queue front is the shock between the arriving free-flow state and
    # the congested state discharging at the cap; the detector reports
    # backward speeds as positive magnitudes (validation.waves).
    rho_free = demand / fd.v_f
    rho_queue = fd.rho_jam - q_closed / -fd.w
    s_rh = (q_closed - demand) / (rho_queue - rho_free)  # m/s, negative = upstream
    metrics = macro_metrics(run_dir)
    assert metrics.wave_count == 1
    assert metrics.wave_speed_kmh == pytest.approx(ms_to_kmh(-s_rh), rel=0.1)

    # Counterfactual: the same demand without the closure is free-flow, so
    # the golden's wave statistics come from the closure and nothing else.
    free = cfg.model_copy(update={"closures": []})
    assert not free.seeded
    free_metrics = macro_metrics(run_macro(free, free.seed, tmp_path))
    assert free_metrics.wave_count == 0, "the wave must come from the closure, not the inflow"
    assert free_metrics.throughput_veh_h > metrics.throughput_veh_h


def test_macro_tier_does_not_represent_heavy_vehicles(tmp_path: Path) -> None:
    """A heavy population reaches meta.json as an explicit note, never silently."""
    cfg = workzone_config()
    assert cfg.fleet.heavy is not None
    meta = json.loads((run_macro(cfg, cfg.seed, tmp_path) / "meta.json").read_text())
    assert isinstance(meta["heavy_vehicles"], str)
    assert "not represented in the macro tier" in meta["heavy_vehicles"]


def main(argv: list[str] | None = None) -> int:
    """CLI: ``--regenerate [<case> ...]`` rewrites ``tests/golden/macro_*.json``."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--regenerate",
        nargs="*",
        metavar="CASE",
        required=True,
        help=f"golden case(s) to regenerate: {', '.join(sorted(CASES))}; "
        "no argument (or 'all') regenerates every case",
    )
    args = parser.parse_args(argv)
    cases = sorted(CASES) if args.regenerate in ([], ["all"]) else args.regenerate
    unknown = [c for c in cases if c not in CASES]
    if unknown:
        parser.error(f"unknown case(s) {unknown}; choose from {sorted(CASES)}")
    with tempfile.TemporaryDirectory(prefix="flowstate-golden-") as tmp:
        for case in cases:
            print(f"wrote {regenerate(case, Path(tmp))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
