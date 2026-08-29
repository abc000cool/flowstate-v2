"""Tests for validation.fields: binned speed field + Edie density/flow."""

import numpy as np
import pandas as pd
import pytest

from validation.fields import density_field, flow_field, speed_field


def _constant_speed_traj(v: float = 15.0, t_end: float = 30.0, dt: float = 0.5) -> pd.DataFrame:
    """One vehicle at constant speed v from x=0, sampled every dt seconds."""
    t = np.arange(0.0, t_end + dt / 2, dt)
    return pd.DataFrame({"t": t, "veh_id": "v0", "x": v * t, "v": np.full_like(t, v)})


class TestSpeedField:
    def test_constant_speed_bins_and_nan(self):
        traj = _constant_speed_traj(v=15.0, t_end=30.0)
        field = speed_field(traj, dt_bin=15.0, dx_bin=75.0)
        # Grid: t in [0, 30] -> 2 bins; x in [0, 450] -> 6 bins.
        assert field.t_edges.tolist() == [0.0, 15.0, 30.0]
        assert len(field.x_edges) == 7
        assert field.mean_speed.shape == (2, 6)
        # Every sampled bin holds exactly the constant speed.
        sampled = ~np.isnan(field.mean_speed)
        assert sampled.any()
        assert np.allclose(field.mean_speed[sampled], 15.0)
        # Bin far off the trajectory diagonal is empty -> NaN: at t<15 s the
        # vehicle never reaches x in [375, 450).
        assert np.isnan(field.mean_speed[0, 5])
        # And the diagonal bins visited in each time row are non-NaN.
        assert not np.isnan(field.mean_speed[0, 0])
        assert not np.isnan(field.mean_speed[1, 5])

    def test_two_vehicles_bin_mean(self):
        t = np.array([0.0, 0.0])
        traj = pd.DataFrame({"t": t, "veh_id": ["a", "b"], "x": [10.0, 20.0], "v": [10.0, 20.0]})
        field = speed_field(traj, dt_bin=15.0, dx_bin=75.0)
        assert field.mean_speed.shape == (1, 1)
        assert field.mean_speed[0, 0] == pytest.approx(15.0)

    def test_rejects_bad_inputs(self):
        traj = _constant_speed_traj()
        with pytest.raises(ValueError, match="bin sizes"):
            speed_field(traj, dt_bin=0.0)
        with pytest.raises(ValueError, match="missing required"):
            speed_field(traj.drop(columns=["v"]))
        with pytest.raises(ValueError, match="no usable"):
            speed_field(traj.iloc[:0])


class TestEdieFields:
    """Edie (1963) generalized definitions: density = time/area, flow = distance/area."""

    def test_single_vehicle_hand_values(self):
        v = 15.0
        traj = _constant_speed_traj(v=v, t_end=30.0, dt=0.5)
        dens = density_field(traj, dt_bin=15.0, dx_bin=75.0)
        flow = flow_field(traj, dt_bin=15.0, dx_bin=75.0)
        area = 15.0 * 75.0
        # First bin (t<15, x<75): vehicle inside for x/v = 5 s -> 10 samples
        # of 0.5 s. Edie: density = 5 s / area, flow = 75 m / area.
        assert dens.density[0, 0] == pytest.approx(5.0 / area)
        assert flow.flow[0, 0] == pytest.approx(75.0 / area)
        # q = rho * v must hold on every occupied bin by construction.
        occupied = dens.density > 0
        assert np.allclose(flow.flow[occupied] / dens.density[occupied], v)
        # Empty bins carry zero density/flow (not NaN) under Edie's definition.
        assert dens.density[0, 5] == 0.0
        assert flow.flow[0, 5] == 0.0

    def test_explicit_sample_dt_matches_inferred(self):
        traj = _constant_speed_traj(v=15.0)
        inferred = density_field(traj, dt_bin=15.0, dx_bin=75.0)
        explicit = density_field(traj, dt_bin=15.0, dx_bin=75.0, sample_dt=0.5)
        assert np.allclose(inferred.density, explicit.density)

    def test_infer_requires_veh_id_or_sample_dt(self):
        traj = _constant_speed_traj().drop(columns=["veh_id"])
        with pytest.raises(ValueError, match="veh_id"):
            density_field(traj)
        # With sample_dt supplied, veh_id is not needed.
        dens = density_field(traj, sample_dt=0.5)
        assert dens.density.sum() > 0

    def test_infer_rejects_single_sample_vehicles_only(self):
        traj = pd.DataFrame({"t": [0.0], "veh_id": ["a"], "x": [0.0], "v": [10.0]})
        with pytest.raises(ValueError, match="cannot infer"):
            flow_field(traj)
