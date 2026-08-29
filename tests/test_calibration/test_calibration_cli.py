"""CLI smoke tests (calibration.cli) — in-process, no network."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from calibration.cli import main
from calibration.loaders.pems import MPH_TO_MS
from flowstate_core.artifacts import FDCalibration, TriangularFD
from flowstate_core.rng import make_rng
from flowstate_core.units import kmh_to_ms, veh_km_to_veh_m

FIXTURES = Path(__file__).parent / "fixtures"

TRUE_FD = TriangularFD(
    v_f=kmh_to_ms(100.0),
    w=-kmh_to_ms(18.0),
    rho_jam=veh_km_to_veh_m(150.0),
)


def _write_pems_like_csv(path: Path, seed: int) -> None:
    """Synthetic PeMS-format CSV consistent with TRUE_FD (g = 7 m)."""
    rng = make_rng(seed)
    rho_c = TRUE_FD.rho_c
    rho_free = rng.uniform(0.1 * rho_c, 0.8 * rho_c, 150)
    q_free = TRUE_FD.v_f * rho_free * (1.0 + rng.normal(0.0, 0.02, 150))
    rho_cong = rng.uniform(1.15 * rho_c, 0.6 * TRUE_FD.rho_jam, 120)
    q_cong = -TRUE_FD.w * (TRUE_FD.rho_jam - rho_cong) - rng.exponential(0.01, 120)
    rho = np.concatenate([rho_free, rho_cong])
    q = np.concatenate([q_free, q_cong])
    speed_mph = (q / rho) / MPH_TO_MS
    pd.DataFrame(
        {
            "Timestamp": [f"03/01/2024 05:{i % 60:02d}:00" for i in range(rho.shape[0])],
            "Station": 717490,
            "District": 7,
            "Flow": q * 300.0,  # veh per 5-min interval
            "Occupancy": rho * 7.0,  # fraction, g = 7 m
            "Speed": speed_mph,
        }
    ).to_csv(path, index=False)


class TestCliFd:
    def test_fd_subcommand_writes_artifact(self, tmp_path: Path, capsys) -> None:
        csv = tmp_path / "pems_like.csv"
        _write_pems_like_csv(csv, seed=17)
        out = tmp_path / "fd.json"
        code = main(
            [
                "fd",
                "--pems-csv",
                str(csv),
                "--out",
                str(out),
                "--seed",
                "3",
                "--n-bootstrap",
                "0",
            ]
        )
        assert code == 0
        assert "FD fit" in capsys.readouterr().out
        artifact = FDCalibration.load(out)
        assert artifact.fd.v_f == pytest.approx(TRUE_FD.v_f, rel=0.05)
        assert artifact.fd.rho_jam == pytest.approx(TRUE_FD.rho_jam, rel=0.15)
        assert artifact.created_at  # CLI supplies the timestamp


class TestCliIdm:
    def test_idm_subcommand_errors_without_episodes(self, tmp_path: Path, capsys) -> None:
        # The tiny fixture has no >= 30 s episode, so the CLI must refuse
        # rather than fit an unusable population (CLAUDE.md §0.1).
        out = tmp_path / "idm.json"
        code = main(
            [
                "idm",
                "--ngsim-csv",
                str(FIXTURES / "ngsim_tiny.csv"),
                "--out",
                str(out),
                "--downsample",
                "1",
            ]
        )
        assert code == 2
        assert "usable episodes" in capsys.readouterr().err
        assert not out.exists()
