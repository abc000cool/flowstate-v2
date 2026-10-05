"""validation.uncertainty (WP-106, Frisco plan Stage 1 item 11) on synthetic inputs.

The Latin hypercube's stratification and determinism, the nested seeds, the
refusal of a source-less range, the default space of a scenario (every range
sourced, assumed ones flagged), ``apply`` (valid configs, distinct and
reproducible hashes, derived populations written once), the two-level
variance combination against a hand computation, the §8.5 robustness verdict
at its boundary, and the aggregate's JSON/markdown. Driver ranges from a
transfer-check report (WP-106b): observed intervals, fallbacks flagged
assumed, the cut to the measured range, a derived population, the refusals,
the truck share, every range's basis. No simulation runs; the only files read
are the small population artifacts written to ``tmp_path``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from scipy import stats

from flowstate_core.artifacts import IDMCalibration
from flowstate_core.config import ScenarioConfig, config_hash
from flowstate_core.rng import spawn_seeds
from validation import uncertainty as unc
from validation.uncertainty import (
    PARAMETER_KINDS,
    ParameterSpace,
    RunRecord,
    Sample,
    SampleValue,
    UncertainParameter,
    aggregate,
    apply,
    default_space,
    latin_hypercube,
    nested_summary,
    robustness,
    run_seeds,
    sample_space,
)

MEAN = {"v0": 32.0, "T": 1.3, "a_max": 1.0, "b": 1.7, "s0": 2.5}
SD = {"v0": 5.0, "T": 0.5, "a_max": 0.4, "b": 0.9, "s0": 0.7}


@pytest.fixture
def population(tmp_path: Path) -> Path:
    path = tmp_path / "idm_synthetic.json"
    IDMCalibration(
        created_at="2026-10-04T00:00:00Z",
        source="synthetic",
        data_hash="0" * 64,
        mean=MEAN,
        cov=np.diag([SD[k] ** 2 for k in ("v0", "T", "a_max", "b", "s0")]).tolist(),
        n_episodes_fit=100,
        n_episodes_holdout=40,
        holdout_gap_rmse_m=4.0,
    ).save(path)
    return path


def _osm_doc(fleet: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": "unc_test",
        "network": {
            "kind": "osm",
            "osm_file": "x.osm",
            "corridor_edges": ["a", "b"],
            "inflow": [[0.0, 0.5], [300.0, 0.6]],
            "ramps": [
                {"kind": "on", "edges": ["r1"], "attach_edge": "a", "inflow": [[0.0, 0.1]]},
                {"kind": "off", "edges": ["r2"], "attach_edge": "b", "exit_fraction": [[0.0, 0.2]]},
            ],
        },
        "fleet": fleet or {},
        "sim": {"duration_s": 60.0},
    }


HEAVY = {
    "fraction": 0.10,
    "length_m": 12.0,
    "emission_class": "HBEFA4/x",
    "v0": 28.0,
    "T": 1.9,
    "a_max": 0.6,
    "b": 1.6,
    "s0": 2.5,
}


def _cfg(population: Path, *, heavy: bool = False) -> ScenarioConfig:
    fleet: dict[str, Any] = {"idm_calibration": str(population)}
    if heavy:
        fleet["heavy"] = HEAVY
    return ScenarioConfig.model_validate(_osm_doc(fleet))


# --- constants mirror the calibration package (validation does not import it) ------------


def test_constants_agree_with_the_calibration_package() -> None:
    from calibration import transfer_check
    from calibration.conservation import DEFAULT_COUNT_ERROR
    from calibration.data_quality import QUALITY_SCHEMA

    assert unc.COUNT_ERROR == DEFAULT_COUNT_ERROR
    assert unc.DATA_QUALITY_SCHEMA == QUALITY_SCHEMA
    assert unc.MEASURED_RANGE_SIGMAS == transfer_check.MEASURED_RANGE_SIGMAS
    assert unc.HEAVY_SHARE_ASSUMED_HALF_WIDTH == transfer_check.HEAVY_SHARE_TOLERANCE
    assert unc.HEAVY_FRACTION_BOUNDS == transfer_check.HEAVY_SHARE_RANGE
    assert unc.TRANSFER_SCHEMA == transfer_check.TRANSFER_SCHEMA
    assert set(unc.KIND_MAPS_TO) == set(PARAMETER_KINDS)
    assert set(unc.BASIS_WORDS) == set(unc.RANGE_BASES)
    assert unc.ROBUST_SIGN_SHARE == 0.90
    assert (unc.PROTOCOL_MIN_SAMPLES, unc.PROTOCOL_MIN_SEEDS) == (10, 5)


# --- Latin hypercube ----------------------------------------------------------------------


@pytest.mark.parametrize(("n", "d", "seed"), [(10, 4, 1), (7, 3, 42), (1, 2, 5), (25, 1, 0)])
def test_each_stratum_of_each_parameter_is_hit_exactly_once(n: int, d: int, seed: int) -> None:
    u = latin_hypercube(n, d, seed)
    assert u.shape == (n, d)
    assert np.all((u >= 0.0) & (u < 1.0))
    for j in range(d):
        assert sorted(np.floor(u[:, j] * n).astype(int).tolist()) == list(range(n))


def test_the_hypercube_is_determined_by_its_seed() -> None:
    a = latin_hypercube(10, 3, 7)
    np.testing.assert_array_equal(a, latin_hypercube(10, 3, 7))
    assert not np.array_equal(a, latin_hypercube(10, 3, 8))


def test_latin_hypercube_refuses_empty() -> None:
    with pytest.raises(ValueError):
        latin_hypercube(0, 2, 1)


def test_samples_stratify_each_range(population: Path) -> None:
    space = default_space(_cfg(population))
    samples = sample_space(space, 10, 3)
    assert [s.sample_id for s in samples] == [f"s{i:02d}" for i in range(10)]
    for p in space.parameters:
        vals = [s.value_of(p.kind) for s in samples]
        assert all(v is not None and p.low <= v < p.high for v in vals)
        strata = [math.floor((v - p.low) / (p.high - p.low) * 10) for v in vals if v is not None]
        assert sorted(strata) == list(range(10))
    again = sample_space(space, 10, 3)
    assert [s.to_dict() for s in samples] == [s.to_dict() for s in again]
    assert Sample.from_dict(samples[0].to_dict()) == samples[0]


def test_sample_ids_widen_for_large_designs() -> None:
    assert unc.sample_id(3, 10) == "s03"
    assert unc.sample_id(3, 101) == "s003"


def test_run_seeds_nest_in_samples_and_are_stable() -> None:
    seeds = run_seeds(42, 3, 5)
    flat = [s for row in seeds for s in row]
    assert len(set(flat)) == 15
    assert seeds == run_seeds(42, 3, 5)
    assert [row[:2] for row in seeds] == run_seeds(42, 3, 2)
    assert run_seeds(42, 5, 5)[:3] == seeds
    assert not set(flat) & set(spawn_seeds(42, 20))  # never a sweep's evaluation seeds
    assert all(0 <= s < 2**63 for s in flat)


# --- parameters and their sources ------------------------------------------------------------


@pytest.mark.parametrize("source", ["", "   "])
def test_a_parameter_without_a_source_is_refused(source: str) -> None:
    with pytest.raises(ValueError, match="no source"):
        UncertainParameter("demand", "demand_scale", 0.95, 1.05, source)


@pytest.mark.parametrize(
    ("kind", "low", "high"),
    [
        ("demand_scale", 1.05, 0.95),
        ("demand_scale", 1.0, 1.0),
        ("t_scale", 0.0, 1.2),
        ("heavy_fraction", 0.4, 0.6),
        ("lane_change", 0.5, 1.5),
    ],
)
def test_bad_ranges_and_kinds_are_refused(kind: str, low: float, high: float) -> None:
    with pytest.raises(ValueError):
        UncertainParameter("x", kind, low, high, "stated")  # type: ignore[arg-type]


def test_a_space_holds_one_parameter_per_kind_and_a_new_range_needs_a_source() -> None:
    p = UncertainParameter("demand", "demand_scale", 0.95, 1.05, "count error", nominal=1.0)
    with pytest.raises(ValueError, match="one parameter per kind"):
        ParameterSpace((p, UncertainParameter("d2", "demand_scale", 0.9, 1.1, "x")))
    space = ParameterSpace((p,))
    with pytest.raises(ValueError, match="no source"):
        space.with_range("demand_scale", 0.9, 1.1, "")
    wider = space.with_range("demand_scale", 0.9, 1.1, "agency's stated count accuracy ±10 %")
    assert (wider.parameters[0].low, wider.parameters[0].nominal) == (0.9, 1.0)
    added = space.with_range("heavy_fraction", 0.05, 0.12, "classification counts", assumed=False)
    assert [q.kind for q in added.parameters] == ["demand_scale", "heavy_fraction"]
    assert ParameterSpace.from_dict(added.to_dict()) == added


def test_default_space_of_an_artifact_population(population: Path) -> None:
    space = default_space(_cfg(population))
    assert [p.kind for p in space.parameters] == ["demand_scale", "t_scale", "v0_scale"]
    d, t, v = space.parameters
    # no data-quality artifact: the ±5 % default, labelled assumed (protocol §8.5)
    assert (d.low, d.high, d.assumed, d.basis) == (0.95, 1.05, True, "count_error")
    assert "DEFAULT_COUNT_ERROR" in d.source and d.source.startswith("assumed")
    # T 1.3 ± 0.5 within 0.8–2.2 → 0.8–1.8; v0 32 ± 5 within 25–38 → 27–37
    assert t.low == pytest.approx(0.8 / 1.3) and t.high == pytest.approx(1.8 / 1.3)
    assert v.low == pytest.approx(27.0 / 32.0) and v.high == pytest.approx(37.0 / 32.0)
    assert "sha256" in t.source and "MEASURED_RANGE_SIGMAS" in t.source
    # without a transfer check the wide §7.2 range is used, and said to be assumed (WP-106b)
    for p in (t, v):
        assert p.assumed and p.basis == "measured_range"
        assert p.source.startswith("assumed — the wide transfer range")
        assert "not a calibration uncertainty" in p.source and "no transfer check" in p.source
    assert all(p.nominal == 1.0 for p in space.parameters)


def test_default_space_clips_to_the_calibration_range(tmp_path: Path) -> None:
    path = tmp_path / "wide.json"
    IDMCalibration(
        created_at="2026-10-04T00:00:00Z",
        source="synthetic",
        data_hash="0" * 64,
        mean=MEAN,
        cov=np.diag([10.0**2, 1.0**2, 0.4**2, 0.9**2, 0.7**2]).tolist(),
        n_episodes_fit=10,
        n_episodes_holdout=5,
        holdout_gap_rmse_m=4.0,
    ).save(path)
    t = default_space(_cfg(path), kinds=["t_scale"]).parameters[0]
    assert t.low == pytest.approx(0.8 / 1.3) and t.high == pytest.approx(2.2 / 1.3)


def test_heavy_share_is_assumed_unless_a_measured_interval_is_given(population: Path) -> None:
    cfg = _cfg(population, heavy=True)
    heavy = default_space(cfg).by_kind("heavy_fraction")
    assert heavy is not None and heavy.assumed
    assert (heavy.low, heavy.high, heavy.nominal) == pytest.approx((0.07, 0.13, 0.10))
    assert "assumed" in heavy.source
    measured = default_space(
        cfg, heavy_range=(0.08, 0.11), heavy_source="agency classification counts, 2026"
    ).by_kind("heavy_fraction")
    assert measured is not None and not measured.assumed
    assert (measured.low, measured.high) == (0.08, 0.11)
    with pytest.raises(ValueError, match="heavy_source"):
        default_space(cfg, heavy_range=(0.08, 0.11))
    assert default_space(_cfg(population)).by_kind("heavy_fraction") is None
    with pytest.raises(ValueError, match="no heavy population"):
        default_space(_cfg(population), kinds=["heavy_fraction"])


def test_a_scalar_fleet_is_flagged_assumed_and_a_ring_has_no_demand() -> None:
    ring = ScenarioConfig.model_validate(
        {
            "name": "ring",
            "network": {"kind": "ring", "circumference_m": 230.0, "n_vehicles": 22},
            "sim": {"duration_s": 60.0},
        }
    )
    space = default_space(ring)
    assert [p.kind for p in space.parameters] == ["t_scale", "v0_scale"]
    assert all(p.assumed and p.basis == "configured_spread" for p in space.parameters)
    with pytest.raises(ValueError, match="no inflow"):
        default_space(ring, kinds=["demand_scale"])


# --- applying a sample -------------------------------------------------------------------------


def _sample(**values: float) -> Sample:
    return Sample("s00", 0, tuple(SampleValue(k, k, v) for k, v in values.items()))  # type: ignore[arg-type]


def test_apply_scales_inflows_and_on_ramps_but_not_exit_shares(population: Path) -> None:
    base = _cfg(population)
    before = base.model_dump(mode="json")
    cfg = apply(_sample(demand_scale=1.04), base)
    assert cfg.network.inflow == [(0.0, pytest.approx(0.52)), (300.0, pytest.approx(0.624))]
    on, off = cfg.network.ramps  # type: ignore[union-attr]
    assert on.inflow == [(0.0, pytest.approx(0.104))]
    assert off.exit_fraction == [(0.0, 0.2)]
    assert base.model_dump(mode="json") == before  # the base is not modified
    assert config_hash(cfg) != config_hash(base)


def test_apply_derives_a_population_once_and_reproducibly(population: Path, tmp_path: Path) -> None:
    base = _cfg(population)
    pop_dir = tmp_path / "pops"
    s = _sample(t_scale=0.9, v0_scale=1.1)
    cfg = apply(s, base, population_dir=pop_dir, created_at="2026-10-04T00:00:00Z")
    path = Path(str(cfg.fleet.idm_calibration))
    assert path.parent == pop_dir and path.is_file()
    derived = IDMCalibration.load(path)
    assert derived.mean["T"] == pytest.approx(1.3 * 0.9)
    assert derived.mean["v0"] == pytest.approx(32.0 * 1.1)
    assert derived.mean["s0"] == MEAN["s0"]
    np.testing.assert_array_equal(derived.cov, IDMCalibration.load(population).cov)
    assert "never a calibration" in derived.notes
    text = path.read_text()
    again = apply(s, base, population_dir=pop_dir, created_at="2030-01-01T00:00:00Z")
    assert config_hash(again) == config_hash(cfg)
    assert path.read_text() == text  # checked, never rewritten
    other = apply(_sample(t_scale=0.95, v0_scale=1.1), base, population_dir=pop_dir)
    assert config_hash(other) != config_hash(cfg)
    with pytest.raises(ValueError, match="population_dir"):
        apply(s, base)


def test_apply_refuses_a_tampered_derived_population(population: Path, tmp_path: Path) -> None:
    base = _cfg(population)
    pop_dir = tmp_path / "pops"
    cfg = apply(_sample(t_scale=0.9), base, population_dir=pop_dir)
    path = Path(str(cfg.fleet.idm_calibration))
    raw = json.loads(path.read_text())
    raw["mean"]["T"] = 9.9
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="different population"):
        apply(_sample(t_scale=0.9), base, population_dir=pop_dir)


def test_apply_on_a_scalar_fleet_and_on_trucks() -> None:
    base = ScenarioConfig.model_validate(_osm_doc({"T": 1.4, "v0": 30.0, "heavy": HEAVY}))
    cfg = apply(_sample(t_scale=1.2, v0_scale=0.9, heavy_fraction=0.12), base)
    assert cfg.fleet.T == pytest.approx(1.68) and cfg.fleet.v0 == pytest.approx(27.0)
    assert cfg.fleet.heavy is not None and cfg.fleet.heavy.fraction == 0.12
    assert cfg.fleet.heavy.T == HEAVY["T"]  # the heavy population is untouched
    with pytest.raises(ValueError, match="no heavy population"):
        apply(_sample(heavy_fraction=0.1), ScenarioConfig.model_validate(_osm_doc()))


def test_sample_configs_are_valid_with_distinct_hashes(population: Path, tmp_path: Path) -> None:
    base = _cfg(population, heavy=True)
    space = default_space(base)
    samples = sample_space(space, 6, 11)
    hashes = unc.sample_hashes(samples, base, population_dir=tmp_path / "pops")
    assert len(set(hashes.values())) == 6
    assert hashes == unc.sample_hashes(samples, base, population_dir=tmp_path / "pops")


# --- the two-level variance --------------------------------------------------------------------


def test_nested_summary_matches_a_hand_computation() -> None:
    # Three samples of two seeds: means 2, 5, 9 → μ̂ = 16/3.
    s = nested_summary({"a": [1.0, 3.0], "b": [4.0, 6.0], "c": [7.0, 11.0]})
    mu = 16.0 / 3.0
    s2_between = ((2 - mu) ** 2 + (5 - mu) ** 2 + (9 - mu) ** 2) / 2.0  # = 37/3
    assert s2_between == pytest.approx(37.0 / 3.0)
    var_within = (2.0 + 2.0 + 8.0) / 3.0  # SS_within / Σ(K_i − 1) = 4
    var_between = s2_between - var_within / 2.0  # σ̂_s² = 31/3
    # Var(μ̂) = σ_s²/M + σ_e²/(M K) = S_B²/M = MS_B/(M K)
    var_mu = var_between / 3.0 + var_within / (3.0 * 2.0)
    ms_b = 2.0 * ((2 - mu) ** 2 + (5 - mu) ** 2 + (9 - mu) ** 2) / 2.0
    assert var_mu == pytest.approx(s2_between / 3.0) == pytest.approx(ms_b / 6.0)
    half = stats.t.ppf(0.975, 2) * math.sqrt(var_mu)
    assert s.mean == pytest.approx(mu)
    assert (s.lo95, s.hi95) == (pytest.approx(mu - half), pytest.approx(mu + half))
    assert s.sd_within == pytest.approx(2.0)
    assert s.sd_between == pytest.approx(math.sqrt(31.0 / 3.0))
    assert (s.p05, s.p95) == (pytest.approx(2.3), pytest.approx(8.6))
    assert (s.n_samples, s.n_runs, s.df) == (3, 6, 2)
    assert s.sample_means == {"a": 2.0, "b": 5.0, "c": 9.0}


def test_nested_summary_unbalanced_nan_and_degenerate() -> None:
    s = nested_summary({"a": [1.0, 3.0, float("nan")], "b": [5.0], "c": []})
    assert s.n_samples == 2 and s.n_runs == 3
    assert s.mean == pytest.approx(3.5)
    assert s.sd_within == pytest.approx(math.sqrt(2.0))
    # S_B² = 4.5; σ̂_s² = 4.5 − 2 · mean(1/2, 1/1) = 3
    assert s.sd_between == pytest.approx(math.sqrt(3.0))
    one = nested_summary({"a": [1.0, 2.0]})
    assert one.mean == 1.5 and one.lo95 is None and one.df is None and not one.resolved
    empty = nested_summary({"a": [float("nan")]})
    assert empty.mean is None and empty.n_samples == 0
    flat = nested_summary({"a": [1.0], "b": [1.0]})
    assert (flat.lo95, flat.hi95) == (1.0, 1.0) and flat.sd_within is None


# --- robustness (§8.5) ----------------------------------------------------------------------------


def _effects(n_pos: int, n_neg: int) -> dict[str, float]:
    vals = [1.0] * n_pos + [-1.0] * n_neg
    return {f"s{i:02d}": v for i, v in enumerate(vals)}


def test_robust_at_nine_of_ten_uncertain_at_eight() -> None:
    assert robustness(_effects(9, 1), 0.8, 10) == (9, 0.9, "robust", "increase")
    assert robustness(_effects(8, 2), 0.6, 10) == (8, 0.8, "uncertain", "increase")
    assert robustness(_effects(1, 9), -0.8, 10)[2:] == ("robust", "decrease")
    assert robustness(_effects(18, 2), 0.8, 20)[2] == "robust"
    assert robustness(_effects(26, 4), 0.7, 30)[2] == "uncertain"


def test_a_missing_sample_counts_against_robustness() -> None:
    assert robustness(_effects(9, 0), 1.0, 10)[2] == "robust"
    assert robustness(_effects(8, 0), 1.0, 10)[2] == "uncertain"


def test_zero_or_missing_pooled_effect_is_never_robust() -> None:
    assert robustness(_effects(5, 5), 0.0, 10) == (0, 0.0, "uncertain", "none")
    assert robustness({}, None, 10) == (0, None, "not_estimable", "not_estimable")


# --- aggregation ---------------------------------------------------------------------------------


def _records(effects: dict[str, float], *, seeds: int = 2) -> list[RunRecord]:
    out: list[RunRecord] = []
    for i, (sid, eff) in enumerate(effects.items()):
        for j in range(seeds):
            seed = 100 * i + j
            base_tt = 600.0 + 10.0 * i + j
            out.append(RunRecord("baseline", sid, seed, {"mean_tt_s": base_tt, "wave_count": 2}, 0))
            out.append(
                RunRecord("vsl", sid, seed, {"mean_tt_s": base_tt + eff, "wave_count": 2}, 0)
            )
    return out


def test_aggregate_pairs_by_sample_and_seed() -> None:
    effects = {f"s{i:02d}": -5.0 - i for i in range(9)} | {"s09": 3.0}
    records = _records(effects)
    # an arm run whose baseline is missing is not paired
    records.append(RunRecord("vsl", "s00", 999, {"mean_tt_s": 0.0, "wave_count": 2}, 0))
    res = aggregate(records, sample_ids=list(effects))
    e = res.effects["vsl"]["mean_tt_s"]
    expected = nested_summary({sid: [eff, eff] for sid, eff in effects.items()})
    assert e.effect.mean == pytest.approx(expected.mean)
    assert e.effect.lo95 == pytest.approx(expected.lo95)
    assert e.effect.sd_within == pytest.approx(0.0)
    assert (e.n_same_sign, e.n_samples_design, e.verdict) == (9, 10, "robust")
    base_mean = nested_summary(
        {f"s{i:02d}": [600.0 + 10.0 * i, 601.0 + 10.0 * i] for i in range(10)}
    ).mean
    assert base_mean is not None and e.pct_of_baseline == pytest.approx(
        100.0 * float(expected.mean or 0.0) / base_mean
    )
    flat = res.effects["vsl"]["wave_count"]
    assert flat.direction == "none" and flat.verdict == "uncertain"
    assert res.summaries["baseline"]["mean_tt_s"].n_runs == 20
    assert res.zero_collisions is True and res.meets_protocol_minimum is False  # 2 seeds
    assert res.headline == "mean_tt_s"


def test_aggregate_eight_of_ten_is_uncertain_and_collisions_are_reported() -> None:
    effects = {f"s{i:02d}": -5.0 for i in range(8)} | {"s08": 4.0, "s09": 4.0}
    records = _records(effects, seeds=5)
    records[1] = RunRecord(
        records[1].arm, records[1].sample_id, records[1].seed, records[1].metrics, 2
    )
    res = aggregate(records)
    assert res.effects["vsl"]["mean_tt_s"].verdict == "uncertain"
    assert res.meets_protocol_minimum is True
    doc = json.loads(res.to_json())
    assert doc["effects"]["vsl"]["mean_tt_s"]["protocol_verdict"] == "uncertain"
    md = res.to_markdown()
    assert "Rehearsal" not in md and "rehearsal" not in md
    assert "The direction held in 8 of 10 samples: uncertain (an effect is robust only" in md
    assert res.zero_collisions is False
    assert res.collisions["vsl"]["total"] == 2
    assert res.collisions["baseline"]["zero_collisions"] is True
    unrecorded = [RunRecord(r.arm, r.sample_id, r.seed, r.metrics, None) for r in _records(effects)]
    assert aggregate(unrecorded).zero_collisions is None


def test_aggregate_refuses_without_baseline_or_with_duplicates() -> None:
    with pytest.raises(ValueError, match="baseline"):
        aggregate([RunRecord("vsl", "s00", 1, {"mean_tt_s": 1.0})])
    with pytest.raises(ValueError, match="no run records"):
        aggregate([])
    dup = [RunRecord("baseline", "s00", 1, {"mean_tt_s": 1.0})] * 2
    with pytest.raises(ValueError, match="duplicate"):
        aggregate(dup)


def test_json_is_strict_and_the_markdown_speaks_plainly(population: Path) -> None:
    space = default_space(_cfg(population))
    samples = sample_space(space, 10, 1)
    effects = {s.sample_id: -5.0 - s.index for s in samples}
    records = _records(effects)
    records.append(RunRecord("baseline", "s00", 7, {"mean_tt_s": float("nan")}, 0))
    res = aggregate(
        records,
        sample_ids=[s.sample_id for s in samples],
        space=space,
        samples=samples,
        provenance={"scenario": "scenarios/x.yaml", "base_config_hash": "abc", "design_seed": 1},
        missing=[{"sample_id": "s01", "arm": "vsl", "seed": 5}],
    )
    doc = json.loads(res.to_json())  # allow_nan=False: no NaN reaches the file
    assert doc["schema"] == unc.SCHEMA
    assert list(doc)[:4] == ["schema", "provenance", "protocol", "space"]
    assert doc["protocol"]["meets_protocol_minimum"] is False
    assert doc["effects"]["vsl"]["mean_tt_s"]["verdict"] == "robust"
    assert doc["effects"]["vsl"]["mean_tt_s"]["protocol_verdict"] == "rehearsal"  # 2 seeds
    assert doc["effects"]["vsl"]["mean_tt_s"]["n_same_sign"] == 10
    assert len(doc["samples"]) == 10 and doc["missing_runs"][0]["seed"] == 5
    assert doc["space"]["parameters"][0]["maps_to"] == unc.KIND_MAPS_TO["demand_scale"]
    md = res.to_markdown()
    assert "Rehearsal, not a protocol result" in md
    assert (
        "Under the plausible range of driver behaviour and demand, vsl changed mean travel time"
        in md
    )
    assert "The direction held in 10 of 10 samples: robust — in a rehearsal" in md
    assert "not a protocol verdict" in md and "| robust (rehearsal) |" in md
    assert "DEFAULT_COUNT_ERROR" in md and "Zero collisions in every run." in md
    assert "1 expected run(s) missing" in md


def test_a_protocol_sized_design_states_its_verdict() -> None:
    effects = {f"s{i:02d}": -5.0 for i in range(9)} | {"s09": 4.0}
    res = aggregate(_records(effects, seeds=5))
    assert res.meets_protocol_minimum is True
    doc = json.loads(res.to_json())
    assert doc["effects"]["vsl"]["mean_tt_s"]["protocol_verdict"] == "robust"
    assert res.headline_sentence("vsl").endswith("The direction held in 9 of 10 samples: robust.")


# --- the measured source of a derived population (protocol §7.2) ---------------------------------


def _derive(source: Path, out: Path, *, t: float = 0.8, s0: float | None = None) -> Path:
    cal = IDMCalibration.load(source)
    mean = dict(cal.mean)
    mean["T"] *= t
    if s0 is not None:
        mean["s0"] = s0
    cal.model_copy(update={"mean": mean, "notes": "derived"}).save(out)
    return out


def _sidecar(derived: Path, source: Path, name: str | None = None) -> Path:
    path = derived.parent / (name or f"{derived.stem}.calibration.json")
    path.write_text(json.dumps({"source": str(source), "table": [], "T_scale": 0.8}))
    return path


def test_a_derived_population_spans_its_sources_measured_range(
    population: Path, tmp_path: Path
) -> None:
    derived = _derive(population, tmp_path / "idm_capacity.json")  # mean T 1.04
    _sidecar(derived, population)
    found = unc.source_population(str(derived))
    assert found is not None and found[0].means["T"] == pytest.approx(1.3)
    t = default_space(_cfg(derived), kinds=["t_scale"]).parameters[0]
    # the source's T 1.3 ± 0.5 within 0.8–2.2 = 0.8–1.8, as a factor on the configured 1.04
    assert (t.low, t.high) == (pytest.approx(0.8 / 1.04), pytest.approx(1.8 / 1.04))
    assert "source of the configured" in t.source and "§7.2" in t.source
    narrowed = default_space(_cfg(derived), kinds=["t_scale"], centre="configured").parameters[0]
    # 1.04 ± 0.5 = 0.54–1.54, inside the measured 0.8–1.8 → 0.8–1.54
    assert (narrowed.low, narrowed.high) == (pytest.approx(0.8 / 1.04), pytest.approx(1.54 / 1.04))
    assert "centre='configured'" in narrowed.source
    with pytest.raises(ValueError, match="centre"):
        default_space(_cfg(derived), centre="middle")  # type: ignore[arg-type]


def test_the_range_agrees_with_transfer_check(population: Path, tmp_path: Path) -> None:
    from calibration import transfer_check as tc

    derived = _derive(population, tmp_path / "idm_capacity.json", t=0.85)
    _sidecar(derived, population)
    expected = tc.measured_range(
        tc.population_from_artifact(derived), "T", 1.0, tc.population_from_artifact(population)
    )
    t = default_space(_cfg(derived), kinds=["t_scale"]).parameters[0]
    mean_t = 1.3 * 0.85
    assert (t.low * mean_t, t.high * mean_t) == (
        pytest.approx(expected[0]),
        pytest.approx(expected[1]),
    )


def test_a_sidecar_is_used_only_for_a_verified_derivation(population: Path, tmp_path: Path) -> None:
    other = _derive(population, tmp_path / "idm_other.json", s0=3.0)  # s0 differs: not a T scaling
    derived = _derive(population, tmp_path / "idm_capacity.json")
    _sidecar(derived, other)
    assert unc.source_population(str(derived)) is None
    t = default_space(_cfg(derived), kinds=["t_scale"]).parameters[0]
    assert "no sidecar names another source" in t.source
    # a sidecar of another name in the same directory naming the true source is found
    _sidecar(derived, population, name="idm_probe.calibration.json")
    found = unc.source_population(str(derived))
    assert found is not None and found[1].endswith("idm_probe.calibration.json")


def test_a_configured_mean_outside_the_measured_range_is_said(
    population: Path, tmp_path: Path
) -> None:
    derived = _derive(population, tmp_path / "idm_slow.json", t=1.6)  # 2.08 > 1.3 + 0.5
    _sidecar(derived, population)
    t = default_space(_cfg(derived), kinds=["t_scale"]).parameters[0]
    assert "the configured mean lies outside this range" in t.source
    assert t.high < 1.0


# --- driver ranges from the corridor's transfer check (WP-106b) -------------------------------


def _entry(knob: str, parameter: str, ref: float, spec: tuple[str, float, float]) -> dict[str, Any]:
    basis, lo, hi = spec
    if basis == "observed_interval":
        return {
            "knob": knob,
            "parameter": parameter,
            "reference_mean": ref,
            "low": lo / ref,
            "high": hi / ref,
            "parameter_low": lo,
            "parameter_high": hi,
            "basis": basis,
            "reason": f"synthetic interval reading for {knob}",
            "clipped": False,
            "measured_range": [0.5, 1.5],
            "observed_interval": [1.0, 2.0],
            "curve": "analytical",
        }
    return {
        "knob": knob,
        "parameter": parameter,
        "reference_mean": ref,
        "low": 0.5,
        "high": 1.5,
        "parameter_low": 0.5 * ref,
        "parameter_high": 1.5 * ref,
        "basis": "measured_range_fallback",
        "reason": "only a lower bound of the capacity was observed",
        "clipped": False,
        "measured_range": [0.5, 1.5],
        "observed_interval": None,
        "curve": None,
    }


def _transfer(
    checked: Path,
    *,
    t: tuple[str, float, float] = ("observed_interval", 1.1, 1.5),
    v0: tuple[str, float, float] = ("observed_interval", 30.0, 34.0),
    model: str = "IDM",
    heavy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A transfer-check report (schema flowstate.transfer_check/1) run on ``checked``."""
    cal = IDMCalibration.load(checked)
    return {
        "schema": "flowstate.transfer_check/1",
        "model": {
            "model": model,
            "population_sources": {
                "idm_calibration": str(checked),
                "idm_calibration_sha256": unc.file_sha256(checked),
            },
        },
        "observed": {"heavy": heavy or {"available": False}},
        "comparisons": [
            {"quantity": "truck_share"},
            {
                "quantity": "free_flow_speed",
                "uncertainty_range": _entry("v0_scale", "v0", cal.mean["v0"], v0),
            },
            {
                "quantity": "capacity_per_lane",
                "uncertainty_range": _entry("t_scale", "T", cal.mean["T"], t),
            },
        ],
    }


LABEL = "runs/x/transfer_check.json (sha256 0123456789ab)"


def test_transfer_ranges_are_the_observed_intervals(population: Path) -> None:
    space = default_space(_cfg(population), transfer=_transfer(population), transfer_label=LABEL)
    d, t, v = space.parameters
    assert d.basis == "count_error"
    # mean T 1.1–1.5 s and v0 30–34 m/s, inside the measured 0.8–1.8 / 27–37, as factors
    assert (t.low, t.high) == (pytest.approx(1.1 / 1.3), pytest.approx(1.5 / 1.3))
    assert (v.low, v.high) == (pytest.approx(30.0 / 32.0), pytest.approx(34.0 / 32.0))
    for p in (t, v):
        assert not p.assumed and p.basis == "observed_interval"
        assert p.source.startswith(f"observed — {LABEL}, run on this population")
        assert f"synthetic interval reading for {p.kind}" in p.source
        assert "cut to" not in p.source


def test_a_transfer_fallback_is_the_measured_range_flagged_assumed(population: Path) -> None:
    transfer = _transfer(population, t=("measured_range_fallback", 0.0, 0.0))
    t, v = default_space(
        _cfg(population), kinds=["t_scale", "v0_scale"], transfer=transfer, transfer_label=LABEL
    ).parameters
    assert t.assumed and t.basis == "measured_range_fallback"
    assert (t.low, t.high) == (pytest.approx(0.8 / 1.3), pytest.approx(1.8 / 1.3))
    assert "gave no observed-interval range: only a lower bound of the capacity" in t.source
    assert "not a calibration uncertainty" in t.source and "MEASURED_RANGE_SIGMAS" in t.source
    assert not v.assumed and v.basis == "observed_interval"


def test_a_transfer_range_is_cut_to_the_measured_range(population: Path) -> None:
    partly = _transfer(population, t=("observed_interval", 0.9, 1.6))
    wide = dict(partly)
    wide["comparisons"][2]["uncertainty_range"].update(parameter_low=0.6, parameter_high=1.6)
    t = default_space(_cfg(population), kinds=["t_scale"], transfer=wide).parameters[0]
    assert (t.low, t.high) == (pytest.approx(0.8 / 1.3), pytest.approx(1.6 / 1.3))
    assert not t.assumed and "cut to this population's measured range" in t.source
    assert "widened" not in t.source  # the configured 1.3 lies inside 0.6-1.6


def test_a_transfer_range_is_widened_to_include_the_configured_mean(population: Path) -> None:
    # §8.5: the observed-interval range, widened to the configured (calibrated) mean 1.3 s,
    # then clipped to the measured 0.8-1.8 s
    below = _transfer(population, t=("observed_interval", 0.6, 1.0))
    t = default_space(_cfg(population), kinds=["t_scale"], transfer=below).parameters[0]
    assert (t.low, t.high) == (pytest.approx(0.8 / 1.3), pytest.approx(1.0))
    assert not t.assumed and t.basis == "observed_interval"
    assert "widened to include the configured mean 1.3 s" in t.source
    assert "cut to this population's measured range" in t.source
    assert "the configured mean lies outside this range" not in t.source
    above = _transfer(population, t=("observed_interval", 1.5, 1.7))
    t = default_space(_cfg(population), kinds=["t_scale"], transfer=above).parameters[0]
    assert (t.low, t.high) == (pytest.approx(1.0), pytest.approx(1.7 / 1.3))
    assert "cut to" not in t.source


def test_any_basis_but_an_observed_interval_is_the_measured_range_assumed(
    population: Path,
) -> None:
    transfer = _transfer(population)
    entry = transfer["comparisons"][2]["uncertainty_range"]
    entry.update(
        basis="analytical_index_fallback",
        reason="the model's capacity per lane comes from the analytical equilibrium index",
    )
    t = default_space(_cfg(population), kinds=["t_scale"], transfer=transfer).parameters[0]
    assert t.assumed and t.basis == "measured_range_fallback"
    assert (t.low, t.high) == (pytest.approx(0.8 / 1.3), pytest.approx(1.8 / 1.3))
    assert "its range basis is analytical_index_fallback, not an observed interval" in t.source
    entry["basis"] = ""
    with pytest.raises(ValueError, match="with a stated basis"):
        default_space(_cfg(population), kinds=["t_scale"], transfer=transfer)


def test_an_interval_below_the_measured_range_is_widened_into_it(population: Path) -> None:
    # before §8.5's widening this range (0.5-0.7 s, all below the measured 0.8 s) was empty
    outside = _transfer(population, t=("observed_interval", 0.5, 0.7))
    t = default_space(_cfg(population), kinds=["t_scale"], transfer=outside).parameters[0]
    assert not t.assumed and t.basis == "observed_interval"
    assert (t.low, t.high) == (pytest.approx(0.8 / 1.3), pytest.approx(1.0))


def test_a_derived_population_carries_the_range_over_in_absolute_units(
    population: Path, tmp_path: Path
) -> None:
    derived = _derive(population, tmp_path / "idm_adjusted.json", t=0.8)  # mean T 1.04 s
    transfer = _transfer(population, t=("observed_interval", 1.0, 1.2))
    t = default_space(_cfg(derived), kinds=["t_scale"], transfer=transfer).parameters[0]
    assert (t.low, t.high) == (pytest.approx(1.0 / 1.04), pytest.approx(1.2 / 1.04))
    assert not t.assumed and "differs from in mean T / v0 alone" in t.source


def test_a_transfer_check_of_another_population_is_refused(
    population: Path, tmp_path: Path
) -> None:
    other = _derive(population, tmp_path / "idm_other.json", t=1.0, s0=3.0)
    with pytest.raises(ValueError, match="another driver population"):
        default_space(_cfg(other), transfer=_transfer(population))
    gone = _transfer(population)
    gone["model"]["population_sources"]["idm_calibration_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="missing or changed"):
        default_space(_cfg(other), transfer=gone)
    with pytest.raises(ValueError, match="EIDM"):
        default_space(_cfg(population), transfer=_transfer(population, model="EIDM"))
    scalar = ScenarioConfig.model_validate(_osm_doc({"T": 1.4, "v0": 30.0}))
    with pytest.raises(ValueError, match="artifact population"):
        default_space(scalar, transfer=_transfer(population))
    # a space without driver knobs does not need the population
    only_demand = default_space(_cfg(other), kinds=["demand_scale"], transfer=_transfer(population))
    assert [p.kind for p in only_demand.parameters] == ["demand_scale"]


def test_only_a_transfer_check_with_ranges_is_read(population: Path) -> None:
    with pytest.raises(ValueError, match="not a transfer-check report"):
        default_space(_cfg(population), transfer={"schema": "flowstate.uncertainty/1"})
    old = _transfer(population)
    del old["comparisons"][2]["uncertainty_range"]
    with pytest.raises(ValueError, match="predates WP-106b"):
        default_space(_cfg(population), transfer=old)
    wrong = _transfer(population)
    wrong["comparisons"][1]["uncertainty_range"]["knob"] = "t_scale"
    with pytest.raises(ValueError, match="not a v0_scale range"):
        default_space(_cfg(population), transfer=wrong)


def test_the_truck_share_interval_comes_from_the_transfer_check(population: Path) -> None:
    heavy = {
        "available": True,
        "share": 0.094,
        "interval": {"lo": 0.08, "hi": 0.11, "level": 0.95, "unit": "day", "n_units": 5},
        "definition": "FHWA classes 5-13",
    }
    cfg = _cfg(population, heavy=True)
    h = default_space(cfg, transfer=_transfer(population, heavy=heavy)).by_kind("heavy_fraction")
    assert h is not None and not h.assumed and h.basis == "classification_interval"
    assert (h.low, h.high) == (0.08, 0.11)
    assert "classification counts of the transfer check" in h.source
    assert "FHWA classes 5-13" in h.source
    stated = default_space(
        cfg,
        heavy_range=(0.05, 0.07),
        heavy_source="agency counts",
        transfer=_transfer(population, heavy=heavy),
    ).by_kind("heavy_fraction")
    assert stated is not None and (stated.low, stated.high) == (0.05, 0.07)  # stated wins
    flat = dict(heavy, interval={"lo": 0.12, "hi": 0.12, "level": 0.95, "unit": "day"})
    h = default_space(cfg, transfer=_transfer(population, heavy=flat)).by_kind("heavy_fraction")
    assert h is not None and h.assumed and h.basis == "assumed_tolerance"
    assert "single value" in h.source
    h = default_space(cfg, transfer=_transfer(population)).by_kind("heavy_fraction")
    assert h is not None and h.assumed and "had no classification counts" in h.source


def test_a_basis_is_validated_and_round_trips() -> None:
    with pytest.raises(ValueError, match="unknown basis"):
        UncertainParameter("d", "demand_scale", 0.9, 1.1, "x", basis="guess")  # type: ignore[arg-type]
    p = UncertainParameter("d", "demand_scale", 0.9, 1.1, "x", basis="count_error")
    assert UncertainParameter.from_dict(p.to_dict()) == p
    legacy = {k: v for k, v in p.to_dict().items() if k != "basis"}
    assert UncertainParameter.from_dict(legacy).basis is None
    space = ParameterSpace((p,)).with_range("demand_scale", 0.8, 1.2, "agency's count accuracy")
    assert space.parameters[0].basis == "stated"


def test_the_report_says_which_basis_each_range_has(population: Path) -> None:
    transfer = _transfer(population, t=("measured_range_fallback", 0.0, 0.0))
    space = default_space(_cfg(population), transfer=transfer, transfer_label=LABEL)
    samples = sample_space(space, 3, 1)
    records = _records({s.sample_id: -5.0 for s in samples})
    md = aggregate(records, space=space, samples=samples).to_markdown()
    assert "| Parameter | Range | Base value | Basis | Assumed? | Source |" in md
    assert "| observed 95 % interval (transfer check) | no |" in md
    assert "| §7.2 measured range; the transfer check gave no interval | assumed |" in md
    assert "| assumed detector count error (no data-quality artifact given) | assumed |" in md
    assert "- Driver ranges from the observed 95 % intervals (v0_scale)" in md
    assert "- Driver ranges flagged assumed (t_scale)" in md
    wide = default_space(_cfg(population))
    md = aggregate(records, space=wide, samples=sample_space(wide, 3, 1)).to_markdown()
    assert "§7.2 measured range; no transfer check given" in md
    assert "- Driver ranges flagged assumed (t_scale, v0_scale)" in md
    assert "Driver ranges from the observed" not in md


# --- review round B: paired seeds, the denominator, the count error, the headline -------------


def test_review_min_seeds_counts_the_seeds_paired_with_the_baseline() -> None:
    """A strategy arm run on one seed per sample is a rehearsal, whatever the baseline ran."""
    recs: list[RunRecord] = []
    sids = [f"s{i:02d}" for i in range(10)]
    for i, sid in enumerate(sids):
        for seed in range(5):
            recs.append(RunRecord("baseline", sid, 100 * i + seed, {"mean_tt_s": 300.0 + seed}, 0))
        recs.append(RunRecord("fs", sid, 100 * i, {"mean_tt_s": 290.0}, 0))  # one seed only
    res = aggregate(recs, sample_ids=sids)
    assert res.min_seeds_per_sample == 1 and not res.meets_protocol_minimum
    assert res.protocol_verdict(res.effects["fs"]["mean_tt_s"]) == "rehearsal"
    # an arm's seed without a baseline run on it is not paired either
    unpaired = [r for r in recs if not (r.arm == "baseline" and r.seed % 100 == 0)]
    for i, sid in enumerate(sids):
        unpaired += [RunRecord("fs", sid, 100 * i + k, {"mean_tt_s": 290.0}, 0) for k in (1, 2, 3)]
    res = aggregate(unpaired, sample_ids=sids)
    assert res.min_seeds_per_sample == 3  # fs ran 4 seeds a sample, 3 of them paired
    full = list(recs) + [
        RunRecord("fs", sid, 100 * i + k, {"mean_tt_s": 290.0}, 0)
        for i, sid in enumerate(sids)
        for k in (1, 2, 3, 4)
    ]
    assert aggregate(full, sample_ids=sids).meets_protocol_minimum


def test_the_robustness_denominator_is_every_sample_of_the_design() -> None:
    """Fails if the denominator becomes the samples that have an estimate (§8.5)."""
    sids = [f"s{i:02d}" for i in range(10)]
    recs: list[RunRecord] = []
    for i, sid in enumerate(sids):
        for seed in range(5):
            recs.append(RunRecord("baseline", sid, 100 * i + seed, {"mean_tt_s": 300.0}, 0))
            if i < 8:  # the arm has no estimate in s08 and s09
                recs.append(RunRecord("vsl", sid, 100 * i + seed, {"mean_tt_s": 290.0}, 0))
    e = aggregate(recs, sample_ids=sids).effects["vsl"]["mean_tt_s"]
    assert e.effect.n_samples == 8  # an estimate in 8 samples, all of one sign
    assert (e.n_same_sign, e.n_samples_design) == (8, 10)
    assert e.share_same_sign == pytest.approx(0.8) and e.verdict == "uncertain"


def test_the_count_error_is_read_from_the_data_quality_artifact(population: Path) -> None:
    raw = {"schema": unc.DATA_QUALITY_SCHEMA, "parameters": {"count_error": 0.08}}
    assert unc.data_quality_count_error(raw) == 0.08
    for bad, match in (
        ({"schema": "flowstate.observations/1"}, "not a data-quality artifact"),
        ({"schema": unc.DATA_QUALITY_SCHEMA, "parameters": {}}, "no parameters.count_error"),
        (
            {"schema": unc.DATA_QUALITY_SCHEMA, "parameters": {"count_error": 1.5}},
            r"not in \(0, 1\)",
        ),
    ):
        with pytest.raises(ValueError, match=match):
            unc.data_quality_count_error(bad)
    space = default_space(
        _cfg(population),
        kinds=["demand_scale"],
        count_error=0.08,
        count_error_source="runs/dq.json (sha256 0123456789ab)",
    )
    (d,) = space.parameters
    assert (d.low, d.high) == (pytest.approx(0.92), pytest.approx(1.08))
    assert not d.assumed and d.basis == "data_quality_count_error"
    assert "runs/dq.json (sha256 0123456789ab)" in d.source
    md = aggregate(_records({"s00": -1.0}), space=space).to_markdown()
    assert "| count error recorded in the data-quality artifact | no |" in md
    assert "the count error recorded in the study's data-quality artifact" in md


def test_the_headline_is_total_delay_including_waiting_when_recorded() -> None:
    from validation.metrics import WAITING_FIELDS

    assert unc.DEFAULT_HEADLINE == "total_delay_incl_waiting_veh_h"
    assert set(WAITING_FIELDS) <= set(unc.METRIC_LABELS)
    recs = [
        RunRecord(arm, "s00", seed, {"mean_tt_s": 100.0, "total_delay_incl_waiting_veh_h": d})
        for seed in range(2)
        for arm, d in (("baseline", 50.0 + seed), ("vsl", 40.0 + seed))
    ]
    res = aggregate(recs)
    assert res.headline == "total_delay_incl_waiting_veh_h" and res.headline_note == ""
    assert "changed total delay including waiting on ramps and to enter" in (
        res.headline_sentence("vsl")
    )
    md = res.to_markdown()
    assert "Total delay and travel time including time spent waiting" in md
    old = aggregate(_records({"s00": -1.0}))  # runs from before the demand ledger
    assert old.headline == "mean_tt_s"
    assert old.headline_note.startswith("These runs record no total delay including waiting")
    assert old.headline_note in old.to_markdown()
    assert json.loads(old.to_json())["headline_note"] == old.headline_note
    assert "These runs record no total delay including time spent waiting" in old.to_markdown()
