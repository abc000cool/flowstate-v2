"""Delayed / noisy wave-detection oracle (CLAUDE.md §4.3, OracleSpec)."""

import numpy as np
import pytest

from microsim.runner import _apply_oracle_noise


class TestOracleNoise:
    def test_perfect_oracle_is_identity(self):
        bins = (30.0, 25.0, float("nan"), 5.0)
        out = _apply_oracle_noise(bins, 0.0, np.random.default_rng(0))
        assert out == bins

    def test_noise_stays_within_the_configured_band(self):
        rng = np.random.default_rng(42)
        bins = tuple(20.0 for _ in range(500))
        out = _apply_oracle_noise(bins, 0.2, rng)
        arr = np.asarray(out)
        assert arr.min() >= 20.0 * 0.8 - 1e-9
        assert arr.max() <= 20.0 * 1.2 + 1e-9
        # Actually perturbs, and is unbiased to within sampling error.
        assert arr.std() > 0.5
        assert abs(arr.mean() - 20.0) < 0.5

    def test_empty_bins_stay_nan(self):
        out = _apply_oracle_noise((float("nan"), 10.0), 0.2, np.random.default_rng(1))
        assert np.isnan(out[0])
        assert not np.isnan(out[1])

    def test_speeds_never_go_negative(self):
        out = _apply_oracle_noise((0.0, 0.1), 1.0, np.random.default_rng(3))
        assert all(v >= 0.0 for v in out)

    def test_seeded_and_reproducible(self):
        a = _apply_oracle_noise((20.0,) * 20, 0.2, np.random.default_rng(7))
        b = _apply_oracle_noise((20.0,) * 20, 0.2, np.random.default_rng(7))
        assert a == b


class TestOracleSpecConfig:
    def test_default_is_perfect(self):
        from flowstate_core import AVSpec

        assert AVSpec().oracle.kind == "perfect"
        assert AVSpec().oracle.delay_s == 0.0

    def test_noisy_requires_a_degradation(self):
        from flowstate_core import OracleSpec

        with pytest.raises(ValueError, match="nonzero"):
            OracleSpec(kind="noisy")

    def test_perfect_rejects_degradation(self):
        from flowstate_core import OracleSpec

        with pytest.raises(ValueError, match="cannot carry"):
            OracleSpec(kind="perfect", delay_s=10.0)

    def test_oracle_changes_the_config_hash(self):
        from flowstate_core import ScenarioConfig, config_hash

        base = dict(
            name="oracle_hash",
            network={"kind": "ring", "circumference_m": 230.0, "n_vehicles": 22},
            sim={"duration_s": 60.0},
            av={"penetration": 0.05, "controller": "jad"},
        )
        a = ScenarioConfig.model_validate(base)
        noisy = {**base, "av": {**base["av"], "oracle": {"kind": "noisy", "delay_s": 30.0}}}
        b = ScenarioConfig.model_validate(noisy)
        assert config_hash(a) != config_hash(b)


class TestStaleSnapshot:
    """Selection of the delayed oracle's view (runner._stale_snapshot)."""

    @staticmethod
    def _history(times):
        from collections import deque

        return deque((t, np.array([t * 10.0]), np.array([t])) for t in times)

    def test_no_delay_returns_current(self):
        from microsim.runner import _stale_snapshot

        cur = (np.array([1.0]), np.array([2.0]))
        got = _stale_snapshot(self._history([0.0, 1.0]), 5.0, 0.0, cur)
        assert got is cur

    def test_empty_history_returns_current(self):
        from collections import deque

        from microsim.runner import _stale_snapshot

        cur = (np.array([1.0]), np.array([2.0]))
        assert _stale_snapshot(deque(), 5.0, 30.0, cur) is cur

    def test_picks_most_recent_snapshot_at_or_before_target(self):
        from microsim.runner import _stale_snapshot

        hist = self._history([70.0, 80.0, 90.0, 100.0])
        _, v = _stale_snapshot(hist, 100.0, 20.0, (np.array([0.0]), np.array([0.0])))
        assert v[0] == 80.0  # t - delay = 80 exactly
        _, v = _stale_snapshot(hist, 100.0, 15.0, (np.array([0.0]), np.array([0.0])))
        assert v[0] == 80.0  # target 85 -> most recent at or before

    def test_falls_back_to_oldest_early_in_the_run(self):
        from microsim.runner import _stale_snapshot

        hist = self._history([0.0, 0.5, 1.0])
        _, v = _stale_snapshot(hist, 1.0, 30.0, (np.array([0.0]), np.array([9.0])))
        assert v[0] == 0.0  # oldest held, not the live state

    def test_returns_a_strictly_older_view_than_current(self):
        from microsim.runner import _stale_snapshot

        hist = self._history([60.0, 70.0, 80.0, 90.0])
        cur = (np.array([999.0]), np.array([999.0]))
        _, v = _stale_snapshot(hist, 90.0, 30.0, cur)
        assert v[0] == 60.0
        assert v[0] != 999.0


@pytest.mark.integration
@pytest.mark.slow
class TestDelayedOracleInSimulation:
    def test_delay_changes_jad_behaviour_on_a_waving_corridor(self, tmp_path):
        """A 30 s stale, 20%-noisy oracle must change JAD's commands.

        Uses the full `corridor_10km` scenario: JAD only acts once waves have
        actually formed, which needs the corridor's full length and duration.
        On short runs (or when the AV is stuck in a jam, where SUMO's safety
        layer clamps any command) the two oracles legitimately coincide, so a
        cheaper scenario cannot test this.
        """
        import json

        import pandas as pd

        from flowstate_core import ScenarioConfig
        from microsim.runner import run_micro

        repo = __import__("pathlib").Path(__file__).resolve().parents[2]
        base = ScenarioConfig.from_yaml(repo / "scenarios" / "corridor_10km.yaml")
        bj = base.model_dump(mode="json")
        bj["av"] = {**bj["av"], "penetration": 0.05, "compliance": 1.0, "controller": "jad"}

        perfect = run_micro(ScenarioConfig.model_validate(bj), 11, tmp_path / "perfect")
        nj = json.loads(json.dumps(bj))
        nj["av"]["oracle"] = {"kind": "noisy", "delay_s": 30.0, "amplitude_noise_frac": 0.2}
        delayed = run_micro(ScenarioConfig.model_validate(nj), 11, tmp_path / "delayed")

        def av_speed_sum(paths):
            with open(paths.trajectories, "rb") as fh:
                d = pd.read_parquet(fh)
            return float(d[d["is_av"]]["v"].sum())

        assert av_speed_sum(perfect) != pytest.approx(av_speed_sum(delayed), rel=1e-9)
