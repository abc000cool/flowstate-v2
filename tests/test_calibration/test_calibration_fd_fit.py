"""Synthetic-truth recovery tests for the triangular FD fit (calibration.fd_fit).

A known triangular FD generates a flow-density scatter with realistic
asymmetric congested-branch noise (points scatter *below* the equilibrium
bound — the reason §6.1 prescribes an upper-quantile congested fit) and only
a partial congested branch. The fit must recover v_f within 5%, w and rho_jam
within 15%, with bootstrap CIs bracketing the truth.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from calibration.fd_fit import (
    DROP_REASONS,
    FDBounds,
    check_fd_plausible,
    fit_triangular_fd,
    quantile_line_fit,
)
from flowstate_core.artifacts import FDCalibration, TriangularFD
from flowstate_core.rng import make_rng
from flowstate_core.units import kmh_to_ms, veh_km_to_veh_m

TRUE_FD = TriangularFD(
    v_f=kmh_to_ms(100.0),
    w=-kmh_to_ms(18.0),
    rho_jam=veh_km_to_veh_m(150.0),
)


def _synthetic_scatter(seed: int, n_free: int = 250, n_cong: int = 200) -> pd.DataFrame:
    """Flow-density scatter from TRUE_FD with only a partial congested branch."""
    rng = make_rng(seed)
    rho_c = TRUE_FD.rho_c
    # Free-flow branch: multiplicative noise around q = v_f * rho.
    rho_free = rng.uniform(0.1 * rho_c, 0.8 * rho_c, n_free)
    q_free = TRUE_FD.v_f * rho_free * (1.0 + rng.normal(0.0, 0.02, n_free))
    # Congested branch, PARTIAL (up to 0.6 rho_jam only): scatter mostly below
    # the equilibrium line (non-equilibrium states) -> exponential downward
    # noise plus small symmetric noise.
    rho_cong = rng.uniform(1.15 * rho_c, 0.6 * TRUE_FD.rho_jam, n_cong)
    q_line = -TRUE_FD.w * (TRUE_FD.rho_jam - rho_cong)
    q_cong = q_line - rng.exponential(0.01, n_cong) + rng.normal(0.0, 0.003, n_cong)
    df = pd.DataFrame(
        {
            "density_veh_m": np.concatenate([rho_free, rho_cong]),
            "flow_veh_s": np.concatenate([q_free, q_cong]),
        }
    )
    # Consistent synthetic occupancy (g = 7 m) so the occupancy path works too.
    df["occupancy"] = df["density_veh_m"] * 7.0
    return df


class TestQuantileLineFit:
    def test_exact_on_noiseless_line(self) -> None:
        x = np.linspace(0.0, 10.0, 50)
        y = 3.0 - 0.5 * x
        a, b = quantile_line_fit(x, y, tau=0.9)
        assert a == pytest.approx(3.0, abs=1e-6)
        assert b == pytest.approx(-0.5, abs=1e-6)

    def test_upper_quantile_ignores_downward_outliers(self) -> None:
        rng = make_rng(11)
        x = np.linspace(0.0, 10.0, 200)
        y = 2.0 + 1.0 * x - rng.exponential(1.0, 200)
        a, b = quantile_line_fit(x, y, tau=0.9)
        assert b == pytest.approx(1.0, abs=0.1)
        assert a == pytest.approx(2.0, abs=0.5)

    def test_bad_tau_raises(self) -> None:
        with pytest.raises(ValueError, match="tau"):
            quantile_line_fit(np.arange(3.0), np.arange(3.0), tau=1.0)


class TestFitTriangularFD:
    def test_synthetic_truth_recovery(self) -> None:
        df = _synthetic_scatter(seed=7)
        cal = fit_triangular_fd(
            df,
            created_at="2026-08-29T00:00:00+00:00",
            source="synthetic triangular FD, seed 7",
            uncongested_max_density=0.85 * TRUE_FD.rho_c,
            seed=13,
        )
        fd = cal.fd
        assert fd.v_f == pytest.approx(TRUE_FD.v_f, rel=0.05)
        assert fd.w == pytest.approx(TRUE_FD.w, rel=0.15)
        assert fd.rho_jam == pytest.approx(TRUE_FD.rho_jam, rel=0.15)
        assert cal.r2_freeflow > 0.98
        assert cal.n_observations == len(df)
        assert cal.congested_quantile == 0.9
        # Bootstrap 95% CIs must bracket the truth (honest uncertainty, §0.6).
        for key, truth in (("v_f", TRUE_FD.v_f), ("w", TRUE_FD.w), ("rho_jam", TRUE_FD.rho_jam)):
            lo, hi = fd.ci95[key]
            assert lo <= truth <= hi, f"{key}: truth {truth} outside CI ({lo}, {hi})"
            assert lo < hi
        # Derived-quantity CIs are recorded too.
        assert "q_max" in fd.ci95 and "rho_c" in fd.ci95

    def test_occupancy_threshold_path(self) -> None:
        df = _synthetic_scatter(seed=21)
        cal = fit_triangular_fd(
            df,
            created_at="2026-08-29T00:00:00+00:00",
            source="synthetic, occupancy split",
            uncongested_max_occupancy=0.85 * TRUE_FD.rho_c * 7.0,
            n_bootstrap=0,
            seed=1,
        )
        assert cal.fd.v_f == pytest.approx(TRUE_FD.v_f, rel=0.05)
        assert cal.fd.rho_jam == pytest.approx(TRUE_FD.rho_jam, rel=0.15)
        assert cal.fd.ci95 == {}  # bootstrap disabled

    def test_bootstrap_is_seeded_and_reproducible(self) -> None:
        df = _synthetic_scatter(seed=7)
        kwargs = dict(
            created_at="2026-08-29T00:00:00+00:00",
            source="repro",
            uncongested_max_density=0.85 * TRUE_FD.rho_c,
            n_bootstrap=50,
        )
        a = fit_triangular_fd(df, seed=99, **kwargs)
        b = fit_triangular_fd(df, seed=99, **kwargs)
        assert a.fd.ci95 == b.fd.ci95
        assert a.data_hash == b.data_hash

    def test_artifact_round_trip(self, tmp_path) -> None:
        df = _synthetic_scatter(seed=7)
        cal = fit_triangular_fd(
            df,
            created_at="2026-08-29T00:00:00+00:00",
            source="round trip",
            uncongested_max_density=0.85 * TRUE_FD.rho_c,
            n_bootstrap=0,
        )
        path = tmp_path / "fd.json"
        cal.save(path)
        loaded = type(cal).load(path)
        assert loaded.fd.v_f == cal.fd.v_f
        assert loaded.congested_quantile == 0.9

    def test_missing_columns_raise(self) -> None:
        with pytest.raises(ValueError, match="missing column"):
            fit_triangular_fd(
                pd.DataFrame({"flow_veh_s": [1.0]}),
                created_at="t",
                source="s",
            )

    def test_too_few_congested_points_raise(self) -> None:
        rng = make_rng(3)
        rho = rng.uniform(0.001, 0.8 * TRUE_FD.rho_c, 100)
        df = pd.DataFrame({"density_veh_m": rho, "flow_veh_s": TRUE_FD.v_f * rho})
        with pytest.raises(ValueError, match="congested points"):
            fit_triangular_fd(
                df,
                created_at="t",
                source="s",
                uncongested_max_density=0.85 * TRUE_FD.rho_c,
                n_bootstrap=0,
            )


class TestPlausibilityGate:
    """The fitted diagram is range-checked before the artifact exists.

    A mis-declared aggregation interval or occupancy unit rescales a whole
    column; the fit absorbs it with an unchanged R² and tight bootstrap CIs,
    so the scale check is the only thing that can refuse it.
    """

    def test_default_bounds_accept_the_repo_fits(self) -> None:
        # artifacts/fd_i24.json, artifacts/fd_us101.json, the v1_legacy preset
        # and the micro FD of artifacts/run_summaries/m3_fluxcap/results.json
        # must all pass the gate they are the reference for.
        for v_f, w, rho_jam in (
            (18.66484100349359, -4.46709656155156, 0.137221125962174),  # fd_i24
            (15.949386478778385, -4.055011654596175, 0.18016067416514253),  # fd_us101
            (kmh_to_ms(100.0), -kmh_to_ms(20.0), veh_km_to_veh_m(160.0)),  # v1_legacy
            # artifacts/run_summaries/m3_fluxcap/results.json — an FD fitted
            # from simulated micro bins, the non-detector caller of the fit.
            (24.212185923729795, -3.474993992589088, 0.19486619852821568),
            (TRUE_FD.v_f, TRUE_FD.w, TRUE_FD.rho_jam),
        ):
            check_fd_plausible(TriangularFD(v_f=v_f, w=w, rho_jam=rho_jam))

    @pytest.mark.parametrize(
        ("fd", "must_name"),
        [
            (TriangularFD(v_f=333.5, w=-5.0, rho_jam=0.15), "v_f"),
            (TriangularFD(v_f=27.8, w=-5.0, rho_jam=234.784), "rho_jam"),
            (TriangularFD(v_f=27.8, w=-0.016, rho_jam=0.15), "w"),
            (TriangularFD(v_f=27.8, w=-9.0, rho_jam=0.29), "q_max"),
        ],
    )
    def test_out_of_range_parameter_refused_by_name(self, fd: TriangularFD, must_name: str) -> None:
        with pytest.raises(ValueError, match="implausible fitted fundamental diagram") as exc:
            check_fd_plausible(fd)
        assert must_name in str(exc.value)

    def test_message_reports_customer_units_and_the_likely_cause(self) -> None:
        with pytest.raises(ValueError) as exc:
            check_fd_plausible(TriangularFD(v_f=27.8, w=-5.0, rho_jam=234.784))
        msg = str(exc.value)
        assert "234784 veh/km" in msg  # the offending value, in veh/km
        assert "occupancy_unit=" in msg  # the likely cause
        assert "bounds=" in msg  # the documented escape hatch

    def test_hourly_counts_read_as_interval_counts_are_refused(self) -> None:
        # Flow uploaded as veh/h with the 5-min interval default: every flow
        # is 12x too large, which scales v_f, w and q_max but leaves R^2
        # bit-identical to the clean fit.
        df = _synthetic_scatter(seed=7)
        df["flow_veh_s"] = df["flow_veh_s"] * 12.0
        with pytest.raises(ValueError, match="implausible fitted fundamental diagram") as exc:
            fit_triangular_fd(
                df,
                created_at="t",
                source="hourly counts, 5-min interval",
                uncongested_max_density=0.85 * TRUE_FD.rho_c,
                n_bootstrap=0,
            )
        msg = str(exc.value)
        assert "v_f=" in msg and "interval_s=" in msg

    def test_percent_occupancy_read_as_fraction_is_refused(self) -> None:
        # Density 100x too large: rho_jam lands at ~15,000 veh/km while
        # q_max still looks textbook-normal.
        df = _synthetic_scatter(seed=7)
        df["density_veh_m"] = df["density_veh_m"] * 100.0
        with pytest.raises(ValueError, match="implausible fitted fundamental diagram") as exc:
            fit_triangular_fd(
                df,
                created_at="t",
                source="percent occupancy read as fraction",
                uncongested_max_density=0.85 * TRUE_FD.rho_c * 100.0,
                n_bootstrap=0,
            )
        assert "rho_jam=" in str(exc.value)

    def test_bounds_override_allows_a_deliberate_out_of_range_fit(self) -> None:
        # scripts/m3_fluxcap_compare.py fits an FD from simulated micro data;
        # an unconditional raise would make such callers unrunnable.
        df = _synthetic_scatter(seed=7)
        df["flow_veh_s"] = df["flow_veh_s"] * 12.0
        wide = FDBounds(v_f_ms=(5.0, 500.0), w_abs_ms=(2.0, 100.0), q_max_veh_s=(0.1, 20.0))
        cal = fit_triangular_fd(
            df,
            created_at="t",
            source="screening",
            uncongested_max_density=0.85 * TRUE_FD.rho_c,
            n_bootstrap=0,
            bounds=wide,
        )
        assert cal.fd.v_f == pytest.approx(12.0 * TRUE_FD.v_f, rel=0.05)
        # bounds=None disables the gate entirely.
        assert (
            fit_triangular_fd(
                df,
                created_at="t",
                source="screening",
                uncongested_max_density=0.85 * TRUE_FD.rho_c,
                n_bootstrap=0,
                bounds=None,
            ).fd.v_f
            == cal.fd.v_f
        )


class TestNonPhysicalRows:
    """Sentinel and impossible rows are dropped, counted and reported."""

    def test_negative_flow_sentinels_no_longer_bias_v_f(self) -> None:
        df = _synthetic_scatter(seed=7)
        n_bad = 20
        df.loc[: n_bad - 1, "flow_veh_s"] = -1.0 / 300.0  # Flow = -1 per 5-min
        cal = fit_triangular_fd(
            df,
            created_at="t",
            source="detector sentinels",
            uncongested_max_density=0.85 * TRUE_FD.rho_c,
            n_bootstrap=0,
        )
        assert cal.fd.v_f == pytest.approx(TRUE_FD.v_f, rel=0.05)
        assert cal.dropped_rows == {"negative": n_bad}
        assert cal.n_rows_input == len(df)
        assert cal.n_observations == len(df) - n_bad
        assert f"{n_bad} dropped as non-physical" in cal.notes

    def test_every_drop_reason_is_counted_separately(self) -> None:
        df = _synthetic_scatter(seed=7)
        df["occupancy"] = df["density_veh_m"] * 7.0
        df.loc[0:1, "flow_veh_s"] = np.nan  # 2 non-finite
        df.loc[2:4, "density_veh_m"] = -0.01  # 3 negative
        df.loc[5:9, "occupancy"] = 1.4  # 5 occupancy > 100%
        df.loc[10:13, ["density_veh_m", "flow_veh_s"]] = [0.0, 0.3]  # 4 rho=0, q>0
        cal = fit_triangular_fd(
            df,
            created_at="t",
            source="mixed junk",
            uncongested_max_occupancy=0.85 * TRUE_FD.rho_c * 7.0,
            n_bootstrap=0,
        )
        assert cal.dropped_rows == {
            "non_finite": 2,
            "negative": 3,
            "occupancy_above_100pct": 5,
            "zero_density_positive_flow": 4,
        }
        assert cal.n_observations == len(df) - 14
        assert sum(cal.dropped_rows.values()) == cal.n_rows_input - cal.n_observations
        # The artifact contract names DROP_REASONS as the key set (and its
        # order): keep the two from drifting apart.
        assert tuple(cal.dropped_rows) == DROP_REASONS

    def test_occupancy_column_untouched_when_a_density_cut_is_given(self) -> None:
        # The occupancy column does not affect the fit when an explicit
        # density cut is passed, so a useless occupancy column must not cost
        # rows (it also must not be carried through the bootstrap payloads).
        df = _synthetic_scatter(seed=7)
        df["occupancy"] = np.nan
        cal = fit_triangular_fd(
            df,
            created_at="t",
            source="unused occupancy column",
            uncongested_max_density=0.85 * TRUE_FD.rho_c,
            n_bootstrap=0,
        )
        assert cal.dropped_rows == {}
        assert cal.n_observations == len(df)

    def test_mostly_junk_file_is_refused(self) -> None:
        df = _synthetic_scatter(seed=7)
        df.loc[: int(0.3 * len(df)), "flow_veh_s"] = -1.0
        with pytest.raises(ValueError, match="non-physical"):
            fit_triangular_fd(
                df,
                created_at="t",
                source="broken month",
                uncongested_max_density=0.85 * TRUE_FD.rho_c,
                n_bootstrap=0,
            )

    def test_input_ranges_are_recorded(self) -> None:
        df = _synthetic_scatter(seed=7)
        cal = fit_triangular_fd(
            df,
            created_at="t",
            source="ranges",
            uncongested_max_density=0.85 * TRUE_FD.rho_c,
            n_bootstrap=0,
        )
        assert cal.input_ranges["density_veh_m_max"] == pytest.approx(df["density_veh_m"].max())
        assert cal.input_ranges["flow_veh_s_min"] == pytest.approx(df["flow_veh_s"].min())
        assert cal.branch_counts["free"] > 0 and cal.branch_counts["congested"] > 0


class TestRowCap:
    """The fit is capped at a seeded, recorded subsample of the input rows."""

    def test_cap_subsamples_deterministically_and_records_it(self) -> None:
        df = _synthetic_scatter(seed=7)
        kwargs = dict(
            created_at="t",
            source="capped",
            uncongested_max_density=0.85 * TRUE_FD.rho_c,
            n_bootstrap=0,
            max_fit_rows=200,
        )
        a = fit_triangular_fd(df, seed=5, **kwargs)
        b = fit_triangular_fd(df, seed=5, **kwargs)
        c = fit_triangular_fd(df, seed=6, **kwargs)
        assert a.n_observations == 200
        assert a.n_rows_input == len(df)
        assert "subsampled from 450 usable (seeded, seed 5)" in a.notes
        assert a.fd.v_f == b.fd.v_f and a.fd.rho_jam == b.fd.rho_jam
        assert a.fd.v_f != c.fd.v_f  # a different seed draws different rows
        # Still the same diagram, within the noise of a 44% subsample.
        assert a.fd.v_f == pytest.approx(TRUE_FD.v_f, rel=0.05)
        assert a.fd.rho_jam == pytest.approx(TRUE_FD.rho_jam, rel=0.15)

    def test_uncapped_fit_is_unchanged_by_the_cap_machinery(self) -> None:
        # The cap must not perturb the bootstrap stream when it does not fire.
        df = _synthetic_scatter(seed=7)
        kwargs = dict(
            created_at="t",
            source="uncapped",
            uncongested_max_density=0.85 * TRUE_FD.rho_c,
            n_bootstrap=30,
            seed=3,
        )
        capped = fit_triangular_fd(df, max_fit_rows=10_000, **kwargs)
        uncapped = fit_triangular_fd(df, max_fit_rows=None, **kwargs)
        assert capped.fd.ci95 == uncapped.fd.ci95
        assert capped.n_observations == uncapped.n_observations == len(df)
        assert "subsampled" not in capped.notes

    def test_cap_below_the_branch_minimum_is_refused(self) -> None:
        with pytest.raises(ValueError, match="max_fit_rows"):
            fit_triangular_fd(
                _synthetic_scatter(seed=7),
                created_at="t",
                source="s",
                max_fit_rows=5,
                n_bootstrap=0,
            )

    def test_parallel_bootstrap_matches_the_serial_one(self) -> None:
        # Resamples are now generated lazily and refitted in bounded batches;
        # the batching must not change the draw order.
        df = _synthetic_scatter(seed=7)
        kwargs = dict(
            created_at="t",
            source="parallel",
            uncongested_max_density=0.85 * TRUE_FD.rho_c,
            n_bootstrap=24,
            seed=4,
        )
        serial = fit_triangular_fd(df, **kwargs)
        parallel = fit_triangular_fd(df, n_procs=2, **kwargs)
        assert serial.fd.ci95 == parallel.fd.ci95


class TestArtifactCompatibility:
    def test_schema_1_json_still_loads(self, tmp_path) -> None:
        # Artifacts written before the input-provenance fields existed
        # (artifacts/fd_i24.json, artifacts/fd_us101.json) must keep loading.
        legacy = {
            "schema_version": 1,
            "created_at": "2026-08-30T00:02:19Z",
            "source": "NGSIM US-101",
            "data_hash": "8578f475",
            "kind": "fd",
            "fd": {"v_f": 15.94, "w": -4.05, "rho_jam": 0.18, "ci95": {}},
            "n_observations": 3001,
            "r2_freeflow": 0.8369,
            "congested_quantile": 0.9,
            "notes": "bootstrap: 200/200 resamples usable (seed 42).",
        }
        path = tmp_path / "fd_legacy.json"
        path.write_text(json.dumps(legacy))
        cal = FDCalibration.load(path)
        assert cal.schema_version == 1  # preserved, not rewritten
        assert cal.n_observations == 3001
        assert cal.n_rows_input == 0  # "not recorded", not "no rows"
        assert cal.dropped_rows == {} and cal.input_ranges == {}

    def test_new_artifact_declares_schema_2(self) -> None:
        cal = fit_triangular_fd(
            _synthetic_scatter(seed=7),
            created_at="t",
            source="s",
            uncongested_max_density=0.85 * TRUE_FD.rho_c,
            n_bootstrap=0,
        )
        assert cal.schema_version == 2
