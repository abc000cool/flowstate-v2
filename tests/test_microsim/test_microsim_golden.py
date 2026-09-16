"""Golden regressions for the micro tier (CLAUDE.md §9).

GOLDEN UPDATE RULE: a change to any file in ``tests/golden/`` must come with a
PR note explaining the physics or code change that moved the numbers (and,
for a SUMO bump, the new pinned version). Never edit a golden by hand — every
value in it is produced by running the pinned engine through
:func:`microsim.run_micro` + :func:`validation.metrics.compute_metrics`, see
``tests/golden/README.md`` and :func:`regenerate` below.

Each golden stores summary statistics only (never trajectories): the
:class:`validation.metrics.Metrics` fields plus the run's vehicle, collision
and feature counters and fuel total, together with the config snapshot, its
hash, the seed, and the package versions that produced it. The tests re-run
the same seeded config and compare:

* config hash — exact (a mismatch means the scenario YAML or the config
  schema changed; the golden then describes a different experiment);
* ``eclipse-sumo`` / ``libsumo`` versions — exact (goldens are per SUMO
  version, CLAUDE.md §9);
* integer counts (vehicles, collisions, heavy / HOV draws, meter releases,
  scripted merges, waves) — exact;
* floating-point statistics — relative tolerance :data:`REL_TOL`.

Cases (:data:`CASES`) are the two versioned scenarios (the ring and the §9
two-minute corridor smoke run), the versioned work-zone corridor, and inline
configs that exercise the engine features the pilot scenarios depend on but
no versioned scenario covers: a multi-step downstream boundary schedule, a
lane closure with a heavy-vehicle population, a managed (HOV) lane window,
and the on-ramp merge models (zipper patch, scripted merge, ALINEA metering)
on the checked-in interchange fixture ``tests/fixtures/merge.osm``. The OSM
path is written REPO-RELATIVE because it is part of the hashed config: an
absolute path would put the machine that generated the golden into its
config hash. The tests and the regenerate CLI therefore run from the
repository root (:data:`REPO_ROOT`).

Why 1e-6 and not exact: SUMO with a fixed seed and step length is
deterministic per version, and the run artifacts are byte-identical across
repeats (``test_microsim_determinism.py``), so the physics is reproduced
exactly. The summary statistics, however, pass through pandas/numpy
reductions (groupby standard deviations, trapezoid sums, percentile
interpolation) whose floating-point summation order may change between
numpy/pandas releases at the ~1e-15 level. 1e-6 is a million times looser
than that and a million times tighter than any physics change of interest.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from flowstate_core.config import ScenarioConfig, config_hash
from microsim import RunPaths, load_scenario, run_micro
from validation.metrics import compute_metrics

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = REPO_ROOT / "tests" / "golden"
GOLDEN_SCHEMA_VERSION = 1

#: Interchange fixture of the merge cases, repo-relative (module docstring).
MERGE_OSM = "tests/fixtures/merge.osm"

#: Relative tolerance on floating-point summary statistics (see module docstring).
REL_TOL = 1e-6

#: Integer statistics compared exactly.
EXACT_KEYS = frozenset(
    {
        "wave_count",
        "n_vehicles_planned",
        "n_vehicles_departed",
        "n_collisions",
        "n_heavy",
        "n_hov",
        "n_meter_releases",
        "n_scripted_merged",
        "n_scripted_forced",
    }
)

#: Heavy-vehicle population of the closure case: the explicit-parameter form
#: of ``HeavyVehicleSpec`` used by ``test_microsim_closures_heavy.py`` (a
#: 20.5 m Euro-VI tractor-trailer with slower, longer-headway car-following).
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

#: ALINEA meter of the metering case (``RampMeterSpec``), as in
#: ``test_microsim_merge_managed_meter.py``.
ALINEA_METER: dict[str, Any] = {
    "controller": "alinea",
    "params": {"rho_target_veh_km": 25.0},
    "interval_s": 30.0,
    "stop_line_m": 40.0,
    "rate_min_veh_h": 240.0,
    "rate_max_veh_h": 600.0,
}


@dataclasses.dataclass(frozen=True)
class GoldenCase:
    """One golden case: how to build its config, and how the file describes it.

    Attributes:
        factory: Builds the exact :class:`ScenarioConfig` the golden pins.
        description: One line for the golden file and ``tests/golden/README.md``.
        scenario: Versioned scenario YAML name, or ``None`` for an inline config.
        overrides: ``"block.field": value`` overrides applied to the YAML
            (recorded in the golden; empty for inline configs).
    """

    factory: Callable[[], ScenarioConfig]
    description: str
    scenario: str | None = None
    overrides: dict[str, float] = dataclasses.field(default_factory=dict)


def _versioned(scenario: str, overrides: dict[str, float]) -> Callable[[], ScenarioConfig]:
    """Factory for a versioned scenario YAML plus dotted overrides."""

    def factory() -> ScenarioConfig:
        cfg = load_scenario(scenario).model_copy(deep=True)
        for dotted, value in overrides.items():
            block, field = dotted.split(".", 1)
            setattr(getattr(cfg, block), field, value)
        return cfg

    return factory


def _corridor_boundary_schedule() -> ScenarioConfig:
    """Single lane, three-step exit-edge speed schedule 15 → 2 → 12 m/s.

    Pins the in-loop advance of a multi-step ``BoundarySpec`` (every I-24
    replica scenario posts one); the behavioural check of the same run is
    ``test_microsim_runner_smoke.py``.
    """
    return ScenarioConfig.model_validate(
        {
            "name": "corridor_boundary_schedule",
            "network": {
                "kind": "corridor",
                "length_m": 1000.0,
                "lanes": 1,
                "inflow": [[0.0, 0.35]],
                "boundary": {
                    "steps": [[0.0, 15.0], [120.0, 2.0], [180.0, 12.0]],
                    "exit_buffer_m": 150.0,
                },
            },
            "sim": {"duration_s": 240.0},
            "seed": 42,
        }
    )


def _corridor_workzone_heavy() -> ScenarioConfig:
    """Two lanes, 20 % heavy population, right lane closed 0.2–0.7 km for 120–210 s.

    The ``closure_run`` config of ``test_microsim_closures_heavy.py``: pins
    the closure lane-permission logic (``seeded=True``) and the heavy vType
    population draw together.
    """
    return ScenarioConfig.model_validate(
        {
            "name": "corridor_workzone_heavy",
            "network": {"kind": "corridor", "length_m": 3000.0, "lanes": 2, "inflow": [[0.0, 0.5]]},
            "fleet": {"heavy": HEAVY_POPULATION},
            "sim": {"duration_s": 300.0},
            "seed": 11,
            "closures": [
                {
                    "label": "work zone",
                    "start_m": 200.0,
                    "end_m": 700.0,
                    "lanes": [0],
                    "t_start_s": 120.0,
                    "t_end_s": 210.0,
                }
            ],
        }
    )


def _hov_corridor() -> ScenarioConfig:
    """Two lanes, 30 % HOV-eligible fleet, left lane managed 0.2–0.9 km for 120–210 s.

    The ``TestManagedLanes`` config of ``test_microsim_merge_managed_meter.py``:
    pins the managed-lane permission window and the ``hov_fraction`` draw.
    """
    return ScenarioConfig.model_validate(
        {
            "name": "hov_corridor",
            "network": {"kind": "corridor", "length_m": 3000.0, "lanes": 2, "inflow": [[0.0, 0.5]]},
            "fleet": {"hov_fraction": 0.3},
            "sim": {"duration_s": 300.0},
            "seed": 5,
            "managed_lanes": [
                {
                    "label": "HOV left lane",
                    "start_m": 200.0,
                    "end_m": 900.0,
                    "lanes": [1],
                    "t_start_s": 120.0,
                    "t_end_s": 210.0,
                }
            ],
        }
    )


def _merge_config(
    name: str, merge: str, meter: dict[str, Any] | None = None, duration_s: float = 200.0
) -> ScenarioConfig:
    """OSM interchange (``MERGE_OSM``) with one on-ramp under the given merge model.

    The ``_merge_scenario`` config of ``test_microsim_merge_managed_meter.py``
    on the checked-in copy of its fixture: mainline 0.6 veh/s on edges
    100–103, ramp 0.25 veh/s on edge 200 joining the auxiliary lane of 102.
    """
    on_ramp: dict[str, Any] = {
        "kind": "on",
        "name": "test on-ramp",
        "edges": ["200"],
        "attach_edge": "102",
        "inflow": [[0.0, 0.25]],
        "merge": merge,
    }
    if meter is not None:
        on_ramp["meter"] = meter
    return ScenarioConfig.model_validate(
        {
            "name": name,
            "network": {
                "kind": "osm",
                "osm_file": MERGE_OSM,
                "corridor_edges": ["100", "101", "102", "103"],
                "inflow": [[0.0, 0.6]],
                "ramps": [on_ramp],
            },
            "sim": {"duration_s": duration_s},
            "seed": 3,
        }
    )


#: Golden cases by name; the case's config is :func:`case_config`.
CASES: dict[str, GoldenCase] = {
    "ring_sugiyama": GoldenCase(
        _versioned("ring_sugiyama", {}),
        "scenarios/ring_sugiyama.yaml as is (600 sim-s, seed 42)",
        scenario="ring_sugiyama",
    ),
    # The CLAUDE.md §9 "corridor_10km 2-min smoke run": the versioned scenario
    # with only its duration shortened to two simulated minutes.
    "corridor_10km_smoke": GoldenCase(
        _versioned("corridor_10km", {"sim.duration_s": 120.0}),
        "scenarios/corridor_10km.yaml with sim.duration_s = 120 (the §9 2-min smoke run), seed 42",
        scenario="corridor_10km",
        overrides={"sim.duration_s": 120.0},
    ),
    # The versioned work zone, run long enough for its 300–900 s closure to
    # be active for the second half (a 120 s run would never apply it).
    "corridor_10km_workzone": GoldenCase(
        _versioned("corridor_10km_workzone", {"sim.duration_s": 600.0}),
        "scenarios/corridor_10km_workzone.yaml with sim.duration_s = 600 "
        "(right-lane closure 6.0–6.5 km active from 300 s), seed 42",
        scenario="corridor_10km_workzone",
        overrides={"sim.duration_s": 600.0},
    ),
    "corridor_boundary_schedule": GoldenCase(
        _corridor_boundary_schedule,
        "1 km single-lane corridor, 0.35 veh/s, exit-edge speed schedule "
        "15 m/s → 2 m/s at 120 s → 12 m/s at 180 s, 240 sim-s, seed 42",
    ),
    "corridor_workzone_heavy": GoldenCase(
        _corridor_workzone_heavy,
        "3 km two-lane corridor, 0.5 veh/s, 20 % heavy population, right lane "
        "closed 0.2–0.7 km for 120–210 s (seeded=True), 300 sim-s, seed 11",
    ),
    "hov_corridor": GoldenCase(
        _hov_corridor,
        "3 km two-lane corridor, 0.5 veh/s, 30 % HOV-eligible, left lane managed "
        "0.2–0.9 km for 120–210 s, 300 sim-s, seed 5",
    ),
    "merge_zipper": GoldenCase(
        lambda: _merge_config("merge_zipper", "zipper"),
        "tests/fixtures/merge.osm interchange, mainline 0.6 veh/s + on-ramp 0.25 veh/s, "
        "zipper merge (netconvert patch), 200 sim-s, seed 3",
    ),
    "merge_scripted": GoldenCase(
        lambda: _merge_config("merge_scripted", "scripted", duration_s=300.0),
        "tests/fixtures/merge.osm interchange, mainline 0.6 veh/s + on-ramp 0.25 veh/s, "
        "scripted merge (runner-driven acceleration lane), 300 sim-s, seed 3",
    ),
    "merge_meter_alinea": GoldenCase(
        lambda: _merge_config("merge_meter_alinea", "lane_change", ALINEA_METER, 240.0),
        "tests/fixtures/merge.osm interchange, mainline 0.6 veh/s + on-ramp 0.25 veh/s "
        "under an ALINEA ramp meter (240–600 veh/h, 30 s interval), 240 sim-s, seed 3",
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


def summarize(paths: RunPaths) -> dict[str, Any]:
    """Summary statistics of one completed micro run, in golden-file layout.

    Args:
        paths: Artifact paths of the run.

    Returns:
        ``config_hash``, ``seed``, ``sumo_seed``, ``tier``, ``seeded``,
        ``versions``, ``config`` (snapshot), ``run`` (vehicle, collision and
        feature counters + fuel total) and ``metrics``
        (:class:`validation.metrics.Metrics` as a dict).
    """
    meta = json.loads(paths.meta.read_text())
    metrics = compute_metrics(paths.run_dir)
    return {
        "config_hash": meta["config_hash"],
        "seed": meta["seed"],
        "sumo_seed": meta["sumo_seed"],
        "tier": meta["tier"],
        "seeded": meta["seeded"],
        "versions": meta["versions"],
        "config": meta["config"],
        "run": {
            "n_vehicles_planned": meta["n_vehicles_planned"],
            "n_vehicles_departed": meta["n_vehicles_departed"],
            "n_collisions": meta["n_collisions"],
            "n_heavy": meta["n_heavy"],
            "n_hov": meta["n_hov"],
            "n_meter_releases": sum(m["n_released"] for m in meta["ramp_meters"]),
            "n_scripted_merged": sum(s["n_changed"] for s in meta["scripted_merges"]),
            "n_scripted_forced": sum(s["n_forced"] for s in meta["scripted_merges"]),
            "fuel_total_ml": _json_float(meta["fuel_total_ml"]),
        },
        "metrics": {k: _json_float(v) for k, v in dataclasses.asdict(metrics).items()},
    }


def _assert_stats_match(section: str, expected: dict[str, Any], actual: dict[str, Any]) -> None:
    """Compare one golden section: exact for ints/None, ``REL_TOL`` for floats."""
    assert set(actual) == set(expected), f"{section}: key set changed"
    for key, exp in expected.items():
        act = actual[key]
        label = f"{section}.{key}"
        if exp is None:
            assert act is None, f"{label}: golden NaN, got {act!r}"
        elif key in EXACT_KEYS:
            assert act == exp, f"{label}: golden {exp!r}, got {act!r}"
        else:
            assert act == pytest.approx(exp, rel=REL_TOL), (
                f"{label}: golden {exp!r}, got {act!r} (rel tol {REL_TOL:g})"
            )


def assert_matches_golden(golden: dict[str, Any], actual: dict[str, Any]) -> None:
    """Assert a fresh run summary reproduces a golden file.

    Args:
        golden: Parsed golden JSON.
        actual: Output of :func:`summarize` for the re-run.
    """
    assert golden["schema_version"] == GOLDEN_SCHEMA_VERSION
    for dist in ("eclipse-sumo", "libsumo"):
        assert actual["versions"][dist] == golden["versions"][dist], (
            f"{dist} {actual['versions'][dist]} differs from the golden's "
            f"{golden['versions'][dist]}: goldens are per SUMO version (CLAUDE.md §9); "
            "regenerate per tests/golden/README.md and explain the bump in the PR note"
        )
    assert actual["config_hash"] == golden["config_hash"], (
        "config hash changed: the scenario YAML or the config schema no longer "
        "describes the golden experiment; regenerate per tests/golden/README.md "
        "with a PR note explaining the change"
    )
    for key in ("seed", "sumo_seed", "tier", "seeded"):
        assert actual[key] == golden[key], key
    _assert_stats_match("run", golden["run"], actual["run"])
    _assert_stats_match("metrics", golden["metrics"], actual["metrics"])


def regenerate(case: str, work_root: Path) -> Path:
    """Re-run a golden case on the pinned engine and rewrite its golden file.

    Must run with the repository root as the working directory (the merge
    cases name their OSM fixture repo-relative); :func:`main` does so.

    Args:
        case: Key of :data:`CASES`.
        work_root: Scratch run-tree root for the SUMO artifacts.

    Returns:
        Path of the written golden file.
    """
    spec = CASES[case]
    cfg = case_config(case)
    paths = run_micro(cfg, cfg.seed, work_root)
    payload: dict[str, Any] = {
        "schema_version": GOLDEN_SCHEMA_VERSION,
        "case": case,
        "description": spec.description,
        "scenario": spec.scenario,
        "overrides": spec.overrides,
        "producer": "microsim.run_micro + validation.metrics.compute_metrics",
        "regenerate": (
            f"uv run --no-sync python tests/test_microsim/test_microsim_golden.py "
            f"--regenerate {case}"
        ),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tolerance": {"relative": REL_TOL, "exact": sorted(EXACT_KEYS)},
        **summarize(paths),
    }
    out = GOLDEN_DIR / f"{case}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n")
    return out


def test_merge_fixture_is_checked_in_and_repo_relative() -> None:
    """The merge cases' OSM path must resolve from the repo root and never be absolute.

    An absolute path would enter the hashed config snapshot and make the
    golden's config hash machine-specific.
    """
    assert not Path(MERGE_OSM).is_absolute()
    assert (REPO_ROOT / MERGE_OSM).is_file(), f"missing fixture {MERGE_OSM}"
    for case in ("merge_zipper", "merge_scripted", "merge_meter_alinea"):
        assert getattr(case_config(case).network, "osm_file", None) == MERGE_OSM


@pytest.mark.parametrize("case", sorted(CASES))
def test_golden_summary_reproduced(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fixed (config, seed) reproduces the stored summary statistics."""
    monkeypatch.chdir(REPO_ROOT)  # the merge cases name their OSM fixture repo-relative
    golden_path = GOLDEN_DIR / f"{case}.json"
    golden = json.loads(golden_path.read_text())
    cfg = case_config(case)
    assert config_hash(cfg) == golden["config_hash"], (
        f"{golden_path.name}: config hash {config_hash(cfg)} != golden "
        f"{golden['config_hash']} — scenario YAML or config schema changed; "
        "regenerate per tests/golden/README.md with a PR note"
    )
    paths = run_micro(cfg, cfg.seed, tmp_path)
    actual = summarize(paths)
    assert actual["run"]["n_collisions"] == 0, (
        f"{case}: {actual['run']['n_collisions']} SUMO collision(s) in a golden run"
    )
    assert_matches_golden(golden, actual)


def main(argv: list[str] | None = None) -> int:
    """CLI: ``--regenerate <case> [<case> ...]`` (or ``all``)."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--regenerate",
        nargs="+",
        metavar="CASE",
        required=True,
        help=f"golden case(s) to regenerate: {', '.join(sorted(CASES))} or 'all'",
    )
    args = parser.parse_args(argv)
    cases = sorted(CASES) if args.regenerate == ["all"] else args.regenerate
    unknown = [c for c in cases if c not in CASES]
    if unknown:
        parser.error(f"unknown case(s) {unknown}; choose from {sorted(CASES)}")
    os.chdir(REPO_ROOT)  # the merge cases name their OSM fixture repo-relative
    with tempfile.TemporaryDirectory(prefix="flowstate-golden-") as tmp:
        for case in cases:
            out = regenerate(case, Path(tmp))
            print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
