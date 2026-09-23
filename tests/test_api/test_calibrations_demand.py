"""Detector-observation calibrations over the API (WP-A).

``POST /api/v1/calibrations/demand`` turns an uploaded tidy detector CSV into
the observations + demand artifact pair, and ``loader: "detector_csv"`` lets
the FD fit read the same frame. Both run through the inline queue, like the
existing FD/IDM tests.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from tests.test_api.conftest import HEADERS

_V_F = 30.0
_W = -6.0
_RHO_JAM = 0.16


def _detector_csv_bytes(
    *,
    n_windows: int = 12,
    columns: dict[str, str] | None = None,
    flows: dict[str, float] | None = None,
) -> bytes:
    """One morning hour of 5-minute windows at two stations plus a ramp."""
    flows = flows or {"S1": 1800.0, "S2": 2160.0, "R1": 360.0}
    meta = {"S1": (0.0, 3, "mainline"), "S2": (1000.0, 3, "mainline"), "R1": (500.0, 1, "on_ramp")}
    rows = []
    for window in range(n_windows):
        minute = window * 5
        stamp = f"2026-09-15T{6 + minute // 60:02d}:{minute % 60:02d}:00-05:00"
        for station, flow in flows.items():
            x_m, lanes, kind = meta[station]
            rows.append(
                {
                    "timestamp": stamp,
                    "station": station,
                    "flow_veh_h": flow,
                    "occupancy_pct": 10.0,
                    "speed_ms": 25.0,
                    "lanes": lanes,
                    "kind": kind,
                    "x_m": x_m,
                }
            )
    frame = pd.DataFrame(rows)
    if columns:
        frame = frame.rename(columns=columns)
    buf = io.BytesIO()
    frame.to_csv(buf, index=False)
    return buf.getvalue()


def _post_demand(
    client: TestClient, payload: bytes, params: dict[str, object]
) -> dict[str, object]:
    response = client.post(
        "/api/v1/calibrations/demand",
        files={"file": ("detectors.csv", payload, "text/csv")},
        data={"params": json.dumps(params), "source": "MnDOT RTMC Mayfly API"},
        headers=HEADERS,
    )
    assert response.status_code == 202, response.text
    return response.json()  # type: ignore[no-any-return]


def test_demand_calibration_writes_both_artifacts(client: TestClient) -> None:
    body = _post_demand(
        client,
        _detector_csv_bytes(),
        {
            "window_s": 300,
            "t0_local": "06:00",
            "duration_s": 3600,
            "upstream_station": "S1",
            "corridor": "mndot_i94_wb",
            "ramps": [{"name": "measured on", "kind": "on", "x_m": 500.0, "station": "R1"}],
        },
    )
    assert body["kind"] == "demand"
    assert body["status"] == "done", body["error"]

    got = client.get(f"/api/v1/calibrations/{body['calibration_id']}", headers=HEADERS)
    assert got.status_code == 200
    payload = got.json()
    paths = payload["artifact_paths"]
    assert set(paths) == {"observations", "demand"}
    assert paths["observations"] == payload["artifact_path"]
    assert Path(paths["demand"]).name == "demand.json"

    observations = payload["artifact"]
    assert observations["schema"] == "flowstate.observations/1"
    assert observations["corridor"] == "mndot_i94_wb"
    assert observations["n_windows"] == 12
    assert observations["window_s"] == 300.0
    assert observations["flows_veh_h"]["S1"] == [1800.0] * 12
    assert observations["quality"]["S1"] == {"fraction_valid": 1.0, "n_dates": 1.0}
    assert observations["source"]["provider"] == "MnDOT RTMC Mayfly API"

    demand = json.loads(Path(paths["demand"]).read_text())
    assert demand["schema"] == "flowstate.demand/1"
    assert demand["upstream_station"] == "S1"
    # 1800 veh/h at the boundary = 0.5 veh/s, in SI, from t=0.
    assert demand["inflow_steps"][0] == [0.0, 0.5]
    assert len(demand["inflow_steps"]) == 12
    (ramp,) = demand["ramps"]
    assert ramp["method"] == "detector"
    assert ramp["inflow_steps"][0] == [0.0, 0.1]  # 360 veh/h
    assert demand["observations"] == paths["observations"]


def test_demand_defaults_infer_the_span_and_the_boundary(client: TestClient) -> None:
    body = _post_demand(client, _detector_csv_bytes(), {})
    assert body["status"] == "done", body["error"]
    observations = client.get(
        f"/api/v1/calibrations/{body['calibration_id']}", headers=HEADERS
    ).json()["artifact"]
    # Default t0_local is 06:00 and the span is what the upload covers.
    assert observations["t0_local"] == "06:00"
    assert observations["n_windows"] == 12
    demand = json.loads(
        Path(
            client.get(f"/api/v1/calibrations/{body['calibration_id']}", headers=HEADERS).json()[
                "artifact_paths"
            ]["demand"]
        ).read_text()
    )
    # The most upstream mainline station (smallest x_m) is the boundary.
    assert demand["upstream_station"] == "S1"


def test_column_map_reaches_the_loader(client: TestClient) -> None:
    payload = _detector_csv_bytes(
        columns={"timestamp": "when", "station": "det", "flow_veh_h": "volume"}
    )
    body = _post_demand(
        client,
        payload,
        {
            "column_map": {"timestamp": "when", "station": "det", "flow": "volume"},
            "window_s": 300,
            "t0_local": "06:00",
            "duration_s": 3600,
            "upstream_station": "S1",
        },
    )
    assert body["status"] == "done", body["error"]
    observations = client.get(
        f"/api/v1/calibrations/{body['calibration_id']}", headers=HEADERS
    ).json()["artifact"]
    assert observations["flows_veh_h"]["S1"] == [1800.0] * 12


def test_without_the_column_map_the_failure_names_the_column(client: TestClient) -> None:
    payload = _detector_csv_bytes(
        columns={"timestamp": "when", "station": "det", "flow_veh_h": "volume"}
    )
    body = _post_demand(client, payload, {"window_s": 300, "t0_local": "06:00"})
    assert body["status"] == "failed"
    assert "timestamp" in (body["error"] or "")


def test_unknown_upstream_station_fails_honestly(client: TestClient) -> None:
    body = _post_demand(
        client,
        _detector_csv_bytes(),
        {"window_s": 300, "t0_local": "06:00", "duration_s": 3600, "upstream_station": "S9"},
    )
    assert body["status"] == "failed"
    assert "S9" in (body["error"] or "")


def test_unknown_param_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/v1/calibrations/demand",
        files={"file": ("d.csv", _detector_csv_bytes(), "text/csv")},
        data={"params": json.dumps({"windows": 300})},
        headers=HEADERS,
    )
    assert response.status_code == 422


def _fd_detector_csv_bytes(seed: int = 0) -> bytes:
    """A single-lane triangular scatter in the tidy detector shape."""
    rng = np.random.default_rng(seed)
    rho_free = rng.uniform(0.002, 0.024, size=150)
    q_free = _V_F * rho_free * rng.normal(1.0, 0.02, size=150)
    rho_cong = rng.uniform(0.035, 0.15, size=150)
    q_cong = -_W * (_RHO_JAM - rho_cong) * rng.normal(1.0, 0.02, size=150)
    rho = np.concatenate([rho_free, rho_cong])
    q = np.concatenate([q_free, q_cong])
    stamps = pd.date_range("2026-09-15T00:00:00-05:00", periods=len(q), freq="5min")
    frame = pd.DataFrame(
        {
            "timestamp": [t.isoformat() for t in stamps],
            "station": "S1",
            "flow_veh_h": q * 3600.0,
            "occupancy_pct": rho * 7.0 * 100.0,
            "speed_ms": q / rho,
            "lanes": 1,
            "kind": "mainline",
        }
    )
    buf = io.BytesIO()
    frame.to_csv(buf, index=False)
    return buf.getvalue()


def test_fd_fit_from_a_detector_csv(client: TestClient) -> None:
    response = client.post(
        "/api/v1/calibrations/fd",
        files={"file": ("detectors.csv", _fd_detector_csv_bytes(), "text/csv")},
        data={
            "params": json.dumps(
                {
                    "loader": "detector_csv",
                    "seed": 0,
                    "n_bootstrap": 25,
                    "uncongested_max_density": 0.026,
                }
            )
        },
        headers=HEADERS,
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "done", body["error"]
    artifact = client.get(f"/api/v1/calibrations/{body['calibration_id']}", headers=HEADERS).json()[
        "artifact"
    ]
    fd = artifact["fd"]
    assert abs(fd["v_f"] - _V_F) / _V_F < 0.2
    assert abs(fd["rho_jam"] - _RHO_JAM) / _RHO_JAM < 0.2
    assert fd["w"] < 0
