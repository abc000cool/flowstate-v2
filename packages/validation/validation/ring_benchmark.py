"""Sugiyama ring emergence and Stern single-AV dampening as evaluable checks.

CLAUDE.md §3.2.1 makes the ``ring_sugiyama`` benchmark a permanent CI gate
(``tests/test_microsim/test_microsim_ring_gate.py``) and §7.1 lists it as an
acceptance criterion of the corridor validation battery. This module holds
the gate's checks as pure functions on trajectory arrays so that the battery
(``scripts/i24_validate.py``) can *evaluate* the ring rows on seeded runs
instead of reporting them as not evaluated. The thresholds are the gate's,
copied here with their documented provenance — they are not re-derived and
must be kept identical to the test file:

* **Emergence** (Sugiyama et al. 2008, New J. Phys. 10:033001): the run is
  unseeded with no AVs; the spatial speed std σ_v (std across vehicles per
  output slice) averaged over the final :data:`TAIL_S` exceeds
  :data:`SIGMA_V_MIN_MS`; some vehicle drops below
  :data:`V_MIN_AFTER_WARMUP_MS` after :data:`WARMUP_S`; and the jam location
  (per-slice argmin speed, unwrapped around the ring) drifts backward with a
  Theil–Sen slope inside :data:`DRIFT_BAND_KMH`. The empirical stop-and-go
  band is 14–22 km/h backward (``flowstate_core.constants``); the assertion
  band is wider because on a 230 m ring the wave is still growing and the
  argmin tracker hops between wave segments.
* **Dampening** (Stern et al. 2018, Transp. Res. C 89:205–221): the same
  seed with one compliant FollowerStopper vehicle (1 of 22) reduces σ_v to at
  most :data:`DAMPENING_SIGMA_FACTOR` of the baseline, raises the tail
  minimum speed by more than :data:`MIN_SPEED_RAISE_MS`, and the ring keeps
  moving (tail mean speed above :data:`MEAN_TAIL_SPEED_MIN_MS`).

Over several seeds the benchmark is "reproduced" when **every** seeded
replicate passes; no extra pass-rate threshold is introduced. Per-seed
values and replicate CIs are reported alongside so the margin is visible.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.stats import t as student_t
from scipy.stats import theilslopes

from flowstate_core.constants import RING_CIRCUMFERENCE_M
from flowstate_core.units import ms_to_kmh
from validation.metrics import CI, CI_LEVEL

FloatArray = NDArray[np.float64]

#: Analysis warm-up [s] before the deep-slowdown check (ring gate).
WARMUP_S = 180.0
#: Final window [s] over which σ_v, drift and tail speeds are evaluated.
TAIL_S = 300.0
#: σ_v (spatial, tail mean) must exceed this for sustained waves [m/s].
SIGMA_V_MIN_MS = 1.5
#: Some vehicle must fall below this after the warm-up [m/s] (a real jam).
V_MIN_AFTER_WARMUP_MS = 3.0
#: Jam-drift acceptance band [km/h]; negative = backward (see module doc).
DRIFT_BAND_KMH: tuple[float, float] = (-25.0, -5.0)
#: Damped σ_v must be at most this fraction of the baseline σ_v.
DAMPENING_SIGMA_FACTOR = 0.75
#: Damped tail minimum speed must exceed the baseline's by more than this [m/s].
MIN_SPEED_RAISE_MS = 0.5
#: Damped tail mean speed must stay above this [m/s] (dampening ≠ stopping).
MEAN_TAIL_SPEED_MIN_MS = 1.0
#: Penetration giving one AV of 22 (``round(0.045 · 22) = 1``, Stern-style).
SINGLE_AV_PENETRATION = 0.045
#: Controller of the dampening arm.
DAMPENING_CONTROLLER = "follower_stopper"
#: Scenario name of the benchmark.
RING_SCENARIO = "ring_sugiyama"


@dataclass(frozen=True)
class RingSlices:
    """Per-output-slice arrays of one ring run.

    Attributes:
        t: Slice times [s], ascending, shape ``[nt]``.
        v: Speeds [m/s], shape ``[nt, n_vehicles]`` (NaN where absent).
        x: Wrapped ring positions [m], same shape.
    """

    t: FloatArray
    v: FloatArray
    x: FloatArray


def ring_slices(trajectories: pd.DataFrame) -> RingSlices:
    """Pivot a ring trajectory frame into per-slice speed and position arrays.

    Args:
        trajectories: Rows with ``t``, ``veh_id``, ``x`` (wrapped) and ``v``.

    Returns:
        :class:`RingSlices`.

    Raises:
        ValueError: On missing columns or an empty frame.
    """
    for col in ("t", "veh_id", "x", "v"):
        if col not in trajectories.columns:
            raise ValueError(f"trajectories missing column {col!r}")
    if trajectories.empty:
        raise ValueError("trajectories holds no rows")
    piv_v = trajectories.pivot_table(index="t", columns="veh_id", values="v")
    piv_x = trajectories.pivot_table(index="t", columns="veh_id", values="x")
    return RingSlices(
        t=np.asarray(piv_v.index.to_numpy(), dtype=np.float64),
        v=np.asarray(piv_v.to_numpy(), dtype=np.float64),
        x=np.asarray(piv_x.to_numpy(), dtype=np.float64),
    )


def sigma_v_tail(slices: RingSlices, tail_s: float = TAIL_S) -> float:
    """Mean over the last ``tail_s`` of the across-vehicle speed std [m/s].

    Population std (``ddof=0``) per slice, as in the ring gate; NaN entries
    (absent vehicles) are ignored.
    """
    last = slices.t > slices.t.max() - tail_s
    return float(np.nanmean(np.nanstd(slices.v[last], axis=1)))


def min_speed_after(slices: RingSlices, t0: float) -> float:
    """Minimum speed of any vehicle at slices with ``t > t0`` [m/s]."""
    return float(np.nanmin(slices.v[slices.t > t0]))


def mean_speed_tail(slices: RingSlices, tail_s: float = TAIL_S) -> float:
    """Mean speed over all vehicles and the last ``tail_s`` [m/s]."""
    last = slices.t > slices.t.max() - tail_s
    return float(np.nanmean(slices.v[last]))


def jam_drift_kmh(
    slices: RingSlices,
    circumference_m: float = RING_CIRCUMFERENCE_M,
    tail_s: float = TAIL_S,
) -> float:
    """Theil–Sen slope [km/h] of the unwrapped jam (argmin-speed) location.

    Negative means the jam moves backward (upstream) around the ring.

    Args:
        slices: Ring slices.
        circumference_m: Ring circumference [m] for unwrapping.
        tail_s: Final window [s] fitted.

    Returns:
        Drift speed [km/h], negative = backward.
    """
    last = slices.t > slices.t.max() - tail_s
    argmin = np.nanargmin(slices.v, axis=1)
    jam_x = slices.x[np.arange(len(slices.t)), argmin][last]
    unwrapped = [float(jam_x[0])]
    half = circumference_m / 2.0
    for x in jam_x[1:]:
        d = (float(x) - unwrapped[-1] + half) % circumference_m - half
        unwrapped.append(unwrapped[-1] + d)
    slope = float(theilslopes(np.asarray(unwrapped), slices.t[last])[0])
    return ms_to_kmh(slope)


@dataclass(frozen=True)
class EmergenceChecks:
    """Emergence-arm measurements and their pass/fail (ring gate checks).

    Attributes:
        unseeded: ``meta.seeded`` is False and no perturbation is configured.
        no_avs: The run has no AV-tagged vehicles.
        sigma_v_ms: Tail spatial σ_v [m/s].
        v_min_after_warmup_ms: Minimum speed after :data:`WARMUP_S` [m/s].
        drift_kmh: Jam drift [km/h], negative = backward.
        sigma_v_ok: ``sigma_v_ms > SIGMA_V_MIN_MS``.
        deep_slowdown_ok: ``v_min_after_warmup_ms < V_MIN_AFTER_WARMUP_MS``.
        backward_ok: ``drift_kmh < 0`` and inside :data:`DRIFT_BAND_KMH`.
        passed: All of the above.
    """

    unseeded: bool
    no_avs: bool
    sigma_v_ms: float
    v_min_after_warmup_ms: float
    drift_kmh: float
    sigma_v_ok: bool
    deep_slowdown_ok: bool
    backward_ok: bool
    passed: bool


@dataclass(frozen=True)
class DampeningChecks:
    """Dampening-arm measurements and their pass/fail (ring gate checks).

    Attributes:
        single_compliant_av: Exactly one AV, complied, running
            :data:`DAMPENING_CONTROLLER`.
        sigma_v_baseline_ms: Baseline tail σ_v [m/s].
        sigma_v_damped_ms: Damped tail σ_v [m/s].
        reduction_frac: ``1 − σ_damped / σ_baseline``.
        v_min_tail_baseline_ms: Baseline minimum speed over the tail [m/s].
        v_min_tail_damped_ms: Damped minimum speed over the tail [m/s].
        mean_speed_tail_damped_ms: Damped mean speed over the tail [m/s].
        sigma_v_ok: ``σ_damped <= DAMPENING_SIGMA_FACTOR · σ_baseline``.
        min_speed_ok: Tail minimum raised by more than
            :data:`MIN_SPEED_RAISE_MS`.
        still_flows_ok: Tail mean speed above :data:`MEAN_TAIL_SPEED_MIN_MS`.
        passed: All of the above.
    """

    single_compliant_av: bool
    sigma_v_baseline_ms: float
    sigma_v_damped_ms: float
    reduction_frac: float
    v_min_tail_baseline_ms: float
    v_min_tail_damped_ms: float
    mean_speed_tail_damped_ms: float
    sigma_v_ok: bool
    min_speed_ok: bool
    still_flows_ok: bool
    passed: bool


def emergence_checks(slices: RingSlices, meta: Mapping[str, Any]) -> EmergenceChecks:
    """Apply the ring gate's emergence checks to one baseline run.

    Args:
        slices: Baseline run slices (:func:`ring_slices`).
        meta: The run's ``meta.json`` (keys ``seeded``, ``config``,
            ``av_ids``).

    Returns:
        :class:`EmergenceChecks`.
    """
    cfg = meta.get("config", {})
    unseeded = meta.get("seeded") is False and cfg.get("perturbation") is None
    no_avs = len(meta.get("av_ids", [1])) == 0
    sigma = sigma_v_tail(slices)
    v_min = min_speed_after(slices, WARMUP_S)
    drift = jam_drift_kmh(slices)
    sigma_ok = bool(sigma > SIGMA_V_MIN_MS)
    deep_ok = bool(v_min < V_MIN_AFTER_WARMUP_MS)
    backward_ok = bool(drift < 0.0 and DRIFT_BAND_KMH[0] <= drift <= DRIFT_BAND_KMH[1])
    return EmergenceChecks(
        unseeded=bool(unseeded),
        no_avs=bool(no_avs),
        sigma_v_ms=sigma,
        v_min_after_warmup_ms=v_min,
        drift_kmh=drift,
        sigma_v_ok=sigma_ok,
        deep_slowdown_ok=deep_ok,
        backward_ok=backward_ok,
        passed=bool(unseeded and no_avs and sigma_ok and deep_ok and backward_ok),
    )


def dampening_checks(
    baseline: RingSlices, damped: RingSlices, meta_damped: Mapping[str, Any]
) -> DampeningChecks:
    """Apply the ring gate's dampening checks to a baseline/damped pair.

    Args:
        baseline: Baseline run slices.
        damped: Same seed with one compliant FollowerStopper vehicle.
        meta_damped: The damped run's ``meta.json`` (keys ``av_ids``,
            ``complied_ids``, ``controller``).

    Returns:
        :class:`DampeningChecks`.
    """
    av_ids = list(meta_damped.get("av_ids", []))
    single = (
        len(av_ids) == 1
        and list(meta_damped.get("complied_ids", [])) == av_ids
        and meta_damped.get("controller") == DAMPENING_CONTROLLER
    )
    sigma_b = sigma_v_tail(baseline)
    sigma_d = sigma_v_tail(damped)
    min_b = min_speed_after(baseline, baseline.t.max() - TAIL_S)
    min_d = min_speed_after(damped, damped.t.max() - TAIL_S)
    mean_d = mean_speed_tail(damped)
    sigma_ok = bool(sigma_d <= DAMPENING_SIGMA_FACTOR * sigma_b)
    min_ok = bool(min_d > min_b + MIN_SPEED_RAISE_MS)
    flows_ok = bool(mean_d > MEAN_TAIL_SPEED_MIN_MS)
    return DampeningChecks(
        single_compliant_av=bool(single),
        sigma_v_baseline_ms=sigma_b,
        sigma_v_damped_ms=sigma_d,
        reduction_frac=1.0 - sigma_d / max(sigma_b, 1e-12),
        v_min_tail_baseline_ms=min_b,
        v_min_tail_damped_ms=min_d,
        mean_speed_tail_damped_ms=mean_d,
        sigma_v_ok=sigma_ok,
        min_speed_ok=min_ok,
        still_flows_ok=flows_ok,
        passed=bool(single and sigma_ok and min_ok and flows_ok),
    )


@dataclass(frozen=True)
class RingSeedResult:
    """Both arms of the benchmark for one seed."""

    seed: int
    emergence: EmergenceChecks
    dampening: DampeningChecks
    run_dir_baseline: str
    run_dir_damped: str


@dataclass(frozen=True)
class RingBenchmarkResult:
    """Ring benchmark over a seed set (CLAUDE.md §7.1 ring rows).

    Attributes:
        scenario: Scenario name.
        seeds: Replicate seeds, in order.
        config_hash_baseline: Config hash of the emergence arm.
        config_hash_damped: Config hash of the dampening arm.
        per_seed: One :class:`RingSeedResult` per seed.
        emergence_passed: True iff every seed's emergence checks passed.
        dampening_passed: True iff every seed's dampening checks passed.
    """

    scenario: str
    seeds: tuple[int, ...]
    config_hash_baseline: str
    config_hash_damped: str
    per_seed: tuple[RingSeedResult, ...]
    emergence_passed: bool
    dampening_passed: bool

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready summary: thresholds, per-seed rows, pass counts, CIs.

        The layout is ``{"emergence": {...}, "dampening": {...}, ...}`` as
        consumed by ``scripts/i24_validate.py``.
        """
        n = len(self.per_seed)
        emergence = {
            "passed": self.emergence_passed,
            "n_pass": sum(1 for r in self.per_seed if r.emergence.passed),
            "n_seeds": n,
            "rule": "every seeded replicate passes the ring-gate emergence checks",
            "sigma_v_ms": _ci_dict([r.emergence.sigma_v_ms for r in self.per_seed]),
            "drift_kmh": _ci_dict([r.emergence.drift_kmh for r in self.per_seed]),
            "v_min_after_warmup_ms": _ci_dict(
                [r.emergence.v_min_after_warmup_ms for r in self.per_seed]
            ),
        }
        dampening = {
            "passed": self.dampening_passed,
            "n_pass": sum(1 for r in self.per_seed if r.dampening.passed),
            "n_seeds": n,
            "rule": "every seeded replicate passes the ring-gate dampening checks",
            "sigma_v_damped_ms": _ci_dict([r.dampening.sigma_v_damped_ms for r in self.per_seed]),
            "reduction_frac": _ci_dict([r.dampening.reduction_frac for r in self.per_seed]),
            "v_min_tail_damped_ms": _ci_dict(
                [r.dampening.v_min_tail_damped_ms for r in self.per_seed]
            ),
        }
        return {
            "scenario": self.scenario,
            "seeds": list(self.seeds),
            "config_hash_baseline": self.config_hash_baseline,
            "config_hash_damped": self.config_hash_damped,
            "thresholds": ring_thresholds(),
            "emergence": emergence,
            "dampening": dampening,
            "per_seed": [asdict(r) for r in self.per_seed],
        }


def ring_thresholds() -> dict[str, Any]:
    """The gate thresholds applied here, for the results record."""
    return {
        "warmup_s": WARMUP_S,
        "tail_s": TAIL_S,
        "sigma_v_min_ms": SIGMA_V_MIN_MS,
        "v_min_after_warmup_ms": V_MIN_AFTER_WARMUP_MS,
        "drift_band_kmh": list(DRIFT_BAND_KMH),
        "dampening_sigma_factor": DAMPENING_SIGMA_FACTOR,
        "min_speed_raise_ms": MIN_SPEED_RAISE_MS,
        "mean_tail_speed_min_ms": MEAN_TAIL_SPEED_MIN_MS,
        "single_av_penetration": SINGLE_AV_PENETRATION,
        "dampening_controller": DAMPENING_CONTROLLER,
        "provenance": "tests/test_microsim/test_microsim_ring_gate.py (CLAUDE.md §3.2.1)",
    }


def _ci_dict(values: Sequence[float]) -> dict[str, Any]:
    ci = replicate_ci(values)
    return {
        "mean": ci.mean,
        "lo95": ci.lo95,
        "hi95": ci.hi95,
        "n": ci.n,
        "underpowered": ci.underpowered,
        "min": float(np.nanmin(values)) if len(values) else math.nan,
        "max": float(np.nanmax(values)) if len(values) else math.nan,
    }


def replicate_ci(values: Sequence[float]) -> CI:
    """Two-sided t-distribution :class:`~validation.metrics.CI` over seeds.

    Same construction as :func:`validation.metrics.aggregate` (NaN dropped;
    bounds NaN for ``n == 1``; everything NaN for ``n == 0``).
    """
    arr = np.asarray(values, dtype=np.float64)
    finite = arr[np.isfinite(arr)]
    n = int(finite.size)
    if n == 0:
        return CI(math.nan, math.nan, math.nan, 0)
    mean = float(finite.mean())
    if n == 1:
        return CI(mean, math.nan, math.nan, 1)
    half = float(student_t.ppf(0.5 + CI_LEVEL / 2.0, n - 1) * finite.std(ddof=1) / math.sqrt(n))
    return CI(mean, mean - half, mean + half, n)


RunFn = Callable[[Any, int, Path], Any]
"""``(cfg, seed, out_dir) → RunPaths``-like object with ``.run_dir``,
``.trajectories`` and ``.meta`` paths (``microsim.run_micro``)."""

LoadFn = Callable[[str], Any]
"""``scenario name → ScenarioConfig`` (``microsim.load_scenario``)."""


def _read_parquet(path: Path) -> pd.DataFrame:
    # Through a file object: a bare path makes pyarrow build a
    # LocalFileSystem, which fails once libsumo's libarrow is loaded.
    with open(path, "rb") as f:
        return pd.read_parquet(f, columns=["t", "veh_id", "x", "v"])


def evaluate_ring_benchmark(
    seeds: Sequence[int],
    out_dir: str | Path,
    *,
    scenario: str = RING_SCENARIO,
    run_fn: RunFn | None = None,
    load_fn: LoadFn | None = None,
) -> RingBenchmarkResult:
    """Run the ring benchmark for ``seeds`` and apply the gate's checks.

    For every seed the scenario is run as shipped (emergence arm) and again
    with ``av.penetration = SINGLE_AV_PENETRATION``, ``av.compliance = 1``
    and ``av.controller = DAMPENING_CONTROLLER`` (dampening arm), exactly as
    the CI gate does. Runs land under ``out_dir/<config_hash>/<seed>/``.

    Args:
        seeds: Replicate seeds (e.g. ``spawn_seeds(cfg.seed, n)``).
        out_dir: Run-tree root.
        scenario: Scenario name (default the Sugiyama ring).
        run_fn: Runner; ``None`` uses ``microsim.run_micro`` (imported
            lazily — this package does not depend on SUMO).
        load_fn: Scenario loader; ``None`` uses ``microsim.load_scenario``.

    Returns:
        :class:`RingBenchmarkResult`.

    Raises:
        ValueError: If ``seeds`` is empty.
        ImportError: If ``microsim`` is unavailable and no runner is given.
    """
    if len(seeds) == 0:
        raise ValueError("evaluate_ring_benchmark needs at least one seed")
    if run_fn is None or load_fn is None:
        try:
            from microsim import load_scenario, run_micro
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ImportError(
                "evaluate_ring_benchmark needs the 'microsim' package (the SUMO tier) "
                "unless run_fn and load_fn are supplied"
            ) from exc
        run_fn = run_fn or run_micro
        load_fn = load_fn or load_scenario
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cfg = load_fn(scenario)
    cfg_damped = cfg.model_copy(deep=True)
    cfg_damped.av.penetration = SINGLE_AV_PENETRATION
    cfg_damped.av.compliance = 1.0
    cfg_damped.av.controller = DAMPENING_CONTROLLER

    per_seed: list[RingSeedResult] = []
    hash_b = ""
    hash_d = ""
    for seed in seeds:
        paths_b = run_fn(cfg, int(seed), out)
        paths_d = run_fn(cfg_damped, int(seed), out)
        meta_b = json.loads(Path(paths_b.meta).read_text())
        meta_d = json.loads(Path(paths_d.meta).read_text())
        hash_b = str(meta_b.get("config_hash", hash_b))
        hash_d = str(meta_d.get("config_hash", hash_d))
        slices_b = ring_slices(_read_parquet(Path(paths_b.trajectories)))
        slices_d = ring_slices(_read_parquet(Path(paths_d.trajectories)))
        per_seed.append(
            RingSeedResult(
                seed=int(seed),
                emergence=emergence_checks(slices_b, meta_b),
                dampening=dampening_checks(slices_b, slices_d, meta_d),
                run_dir_baseline=str(paths_b.run_dir),
                run_dir_damped=str(paths_d.run_dir),
            )
        )
    return RingBenchmarkResult(
        scenario=scenario,
        seeds=tuple(int(s) for s in seeds),
        config_hash_baseline=hash_b,
        config_hash_damped=hash_d,
        per_seed=tuple(per_seed),
        emergence_passed=all(r.emergence.passed for r in per_seed),
        dampening_passed=all(r.dampening.passed for r in per_seed),
    )
