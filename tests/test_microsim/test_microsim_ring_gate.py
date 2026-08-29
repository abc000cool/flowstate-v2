"""THE CREDIBILITY GATE: ring emergence + single-AV dampening (CLAUDE.md §3.2.1).

Reproduces, with **no seeded perturbation**:

* Sugiyama et al. (2008), New J. Phys. 10:033001 — stop-and-go waves emerge
  spontaneously on a 230 m / 22-vehicle ring and propagate backward.
* Stern et al. (2018), Transp. Res. C 89:205–221 — a single FollowerStopper
  vehicle (1 of 22 ≈ 4.5% penetration) dampens the waves.

Assertion bands (documented):

* σ_v — the spatial speed std (std across vehicles per output slice),
  averaged over the final 300 s — must exceed 1.5 m/s sustained. Measured
  ≈ 2.4 m/s at the scenario seed with the tuned ``ring_sugiyama.yaml``
  (T = 1.2 s; see the YAML's tuning note).
* Some vehicle must drop below 3 m/s after the 180 s warmup (full stop
  events actually occur: measured min = 0).
* The jam location (per-slice argmin speed, unwrapped around the ring)
  drifts BACKWARD: Theil–Sen slope negative and within [−25, −5] km/h.
  The empirical stop-and-go band is 14–22 km/h backward
  (``flowstate_core.constants.WAVE_SPEED_BAND_KMH``); the assertion band is
  widened to [−25, −5] because on a 230 m ring the wave is still growing and
  the argmin tracker hops between wave segments. Measured ≈ −14.4 km/h.
* Dampening: same seed, 1 compliant FollowerStopper AV ⇒ σ_v reduced by at
  least 25% (measured: ≈ 100% — the ring equalizes) and the post-warmup
  minimum speed raised (measured 0 → ≈ 2 m/s).

This file is CI-critical and runs in a couple of seconds of wall time
(~600 sim-s each run at >300× real time) — integration, NOT slow.
"""

import json

import numpy as np
import pandas as pd
import pytest
from scipy.stats import theilslopes

from microsim import load_scenario, run_micro

pytestmark = pytest.mark.integration

RING_C = 230.0
WARMUP_S = 180.0
TAIL_S = 300.0

#: Documented jam-drift acceptance band [km/h] (see module docstring).
DRIFT_BAND_KMH = (-25.0, -5.0)


def _slice_arrays(paths):
    """Per-output-slice (times, speeds[nt, nveh], wrapped x[nt, nveh])."""
    df = pd.read_parquet(paths.trajectories)
    piv_v = df.pivot_table(index="t", columns="veh_id", values="v")
    piv_x = df.pivot_table(index="t", columns="veh_id", values="x")
    return piv_v.index.to_numpy(), piv_v.to_numpy(), piv_x.to_numpy()


def _sigma_v_last(ts, speeds, tail_s=TAIL_S):
    """Mean over the last ``tail_s`` of the spatial (across-vehicle) speed std."""
    last = ts > ts.max() - tail_s
    return float(np.nanstd(speeds[last], axis=1).mean())


def _min_speed_after(ts, speeds, t0):
    return float(np.nanmin(speeds[ts > t0]))


def _jam_drift_kmh(ts, speeds, xs, tail_s=TAIL_S):
    """Theil–Sen slope [km/h] of the unwrapped jam (argmin-speed) location."""
    last = ts > ts.max() - tail_s
    jam_x = xs[np.arange(len(ts)), np.nanargmin(speeds, axis=1)][last]
    unwrapped = [float(jam_x[0])]
    for x in jam_x[1:]:
        d = (float(x) - unwrapped[-1] + RING_C / 2.0) % RING_C - RING_C / 2.0
        unwrapped.append(unwrapped[-1] + d)
    slope, *_ = theilslopes(np.asarray(unwrapped), ts[last])
    return float(slope) * 3.6


@pytest.fixture(scope="module")
def ring_cfg():
    return load_scenario("ring_sugiyama")


@pytest.fixture(scope="module")
def baseline(ring_cfg, tmp_path_factory):
    """The emergence run: scenario as shipped, NO perturbation, NO AVs."""
    return run_micro(ring_cfg, ring_cfg.seed, tmp_path_factory.mktemp("ring_baseline"))


@pytest.fixture(scope="module")
def damped(ring_cfg, tmp_path_factory):
    """Same ring + seed with 1 of 22 vehicles a compliant FollowerStopper."""
    cfg = ring_cfg.model_copy(deep=True)
    cfg.av.penetration = 0.045  # round(0.045 · 22) = 1 AV (Stern-style)
    cfg.av.compliance = 1.0
    cfg.av.controller = "follower_stopper"
    return run_micro(cfg, ring_cfg.seed, tmp_path_factory.mktemp("ring_damped"))


class TestEmergence:
    """Stop-and-go MUST emerge without seeding — the §3.2.1 acceptance test."""

    def test_run_is_unseeded(self, baseline):
        meta = json.loads(baseline.meta.read_text())
        assert meta["seeded"] is False  # emergent, not seeded (§0.2)
        assert meta["config"]["perturbation"] is None
        assert meta["av_ids"] == []

    def test_sigma_v_sustained_above_threshold(self, baseline):
        ts, speeds, _ = _slice_arrays(baseline)
        sigma_v = _sigma_v_last(ts, speeds)
        assert sigma_v > 1.5, f"no sustained waves: sigma_v(last 300 s) = {sigma_v:.2f} m/s"

    def test_deep_slowdown_occurs(self, baseline):
        ts, speeds, _ = _slice_arrays(baseline)
        v_min = _min_speed_after(ts, speeds, WARMUP_S)
        assert v_min < 3.0, f"min speed after warmup = {v_min:.2f} m/s (no jam)"

    def test_jam_drifts_backward_within_band(self, baseline):
        ts, speeds, xs = _slice_arrays(baseline)
        drift = _jam_drift_kmh(ts, speeds, xs)
        assert drift < 0.0, f"jam drifts forward ({drift:.1f} km/h)"
        assert DRIFT_BAND_KMH[0] <= drift <= DRIFT_BAND_KMH[1], (
            f"jam drift {drift:.1f} km/h outside documented band {DRIFT_BAND_KMH}"
        )


class TestDampening:
    """One FollowerStopper of 22 measurably calms the ring (Stern et al. 2018)."""

    def test_single_compliant_av(self, damped):
        meta = json.loads(damped.meta.read_text())
        assert len(meta["av_ids"]) == 1
        assert meta["complied_ids"] == meta["av_ids"]
        assert meta["controller"] == "follower_stopper"

    def test_sigma_v_reduced_at_least_25_percent(self, baseline, damped):
        ts_b, v_b, _ = _slice_arrays(baseline)
        ts_d, v_d, _ = _slice_arrays(damped)
        sigma_b = _sigma_v_last(ts_b, v_b)
        sigma_d = _sigma_v_last(ts_d, v_d)
        assert sigma_d <= 0.75 * sigma_b, (
            f"sigma_v {sigma_b:.2f} -> {sigma_d:.2f} m/s: reduction "
            f"{100 * (1 - sigma_d / max(sigma_b, 1e-12)):.0f}% < 25%"
        )

    def test_min_speed_raised(self, baseline, damped):
        ts_b, v_b, _ = _slice_arrays(baseline)
        ts_d, v_d, _ = _slice_arrays(damped)
        min_b = _min_speed_after(ts_b, v_b, ts_b.max() - TAIL_S)
        min_d = _min_speed_after(ts_d, v_d, ts_d.max() - TAIL_S)
        assert min_d > min_b + 0.5, f"min speed {min_b:.2f} -> {min_d:.2f} m/s"

    def test_traffic_still_flows(self, damped):
        ts, speeds, _ = _slice_arrays(damped)
        last = ts > ts.max() - TAIL_S
        assert float(np.nanmean(speeds[last])) > 1.0  # dampening ≠ stopping the ring
