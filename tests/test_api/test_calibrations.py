"""Calibration endpoints wrapping the calibration package (CLAUDE.md §6)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from tests.test_api.conftest import HEADERS, data_dir

_V_F = 30.0
_W = -6.0
_RHO_JAM = 0.16


def _fd_csv_bytes(seed: int = 0) -> bytes:
    """Synthetic triangular flow-density scatter (v_f=30, w=-6, ρ_jam=0.16)."""
    rng = np.random.default_rng(seed)
    rho_free = rng.uniform(0.002, 0.024, size=150)
    q_free = _V_F * rho_free * rng.normal(1.0, 0.02, size=150)
    rho_cong = rng.uniform(0.035, 0.15, size=150)
    q_cong = -_W * (_RHO_JAM - rho_cong) * rng.normal(1.0, 0.02, size=150)
    df = pd.DataFrame(
        {
            "density_veh_m": np.concatenate([rho_free, rho_cong]),
            "flow_veh_s": np.concatenate([q_free, q_cong]),
        }
    )
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


_FD_PARAMS = {
    "seed": 0,
    "n_bootstrap": 25,
    "uncongested_max_density": 0.026,
}


def test_fd_calibration_multipart_upload_round_trip(client: TestClient) -> None:
    r = client.post(
        "/api/v1/calibrations/fd",
        files={"file": ("loops.csv", _fd_csv_bytes(), "text/csv")},
        data={"params": json.dumps(_FD_PARAMS), "source": "synthetic triangular scatter"},
        headers=HEADERS,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["kind"] == "fd"
    assert body["status"] == "done", body["error"]  # inline queue ran the fit
    assert body["artifact_path"] is not None

    got = client.get(f"/api/v1/calibrations/{body['calibration_id']}", headers=HEADERS)
    assert got.status_code == 200
    artifact = got.json()["artifact"]
    assert artifact is not None
    assert artifact["kind"] == "fd"
    assert artifact["source"] == "synthetic triangular scatter"
    fd = artifact["fd"]
    # Recovered parameters should sit near the generating truth.
    assert abs(fd["v_f"] - _V_F) / _V_F < 0.2
    assert abs(fd["rho_jam"] - _RHO_JAM) / _RHO_JAM < 0.2
    assert fd["w"] < 0
    assert "v_f" in fd["ci95"]  # bootstrap CIs recorded (§6.1)


def test_fd_calibration_server_data_path(client: TestClient, tmp_path: Path) -> None:
    csv_path = data_dir(tmp_path) / "loops.csv"  # inside the allow-listed data root
    csv_path.write_bytes(_fd_csv_bytes(seed=1))
    r = client.post(
        "/api/v1/calibrations/fd",
        data={"data_path": str(csv_path), "params": json.dumps(_FD_PARAMS)},
        headers=HEADERS,
    )
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "done", r.json()["error"]


def test_fd_calibration_bad_data_fails_honestly(client: TestClient) -> None:
    r = client.post(
        "/api/v1/calibrations/fd",
        files={"file": ("bad.csv", b"a,b\n1,2\n", "text/csv")},
        headers=HEADERS,
    )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "failed"
    assert body["error"] is not None and "density_veh_m" in body["error"]


def test_calibration_input_validation(client: TestClient, tmp_path: Path) -> None:
    # Exactly one of file/data_path.
    r = client.post("/api/v1/calibrations/fd", data={"source": "x"}, headers=HEADERS)
    assert r.status_code == 422
    csv_path = tmp_path / "d.csv"
    csv_path.write_bytes(b"a\n1\n")
    r = client.post(
        "/api/v1/calibrations/fd",
        files={"file": ("d.csv", b"a\n1\n", "text/csv")},
        data={"data_path": str(csv_path)},
        headers=HEADERS,
    )
    assert r.status_code == 422
    # data_path must exist; params must be a JSON object.
    r = client.post(
        "/api/v1/calibrations/fd", data={"data_path": "/nope/missing.csv"}, headers=HEADERS
    )
    assert r.status_code == 422
    r = client.post(
        "/api/v1/calibrations/fd",
        files={"file": ("d.csv", b"a\n1\n", "text/csv")},
        data={"params": "not json"},
        headers=HEADERS,
    )
    assert r.status_code == 422
    assert client.get("/api/v1/calibrations/cal_missing", headers=HEADERS).status_code == 404


def _idm_pairs_csv_bytes() -> bytes:
    """Four synthetic 40 s leader-follower episodes at 2 Hz.

    Followers cruise at or below the leader's 15 m/s so the integrated gap
    stays comfortably positive (episodes with implausible gaps are dropped by
    the episode validator).
    """
    rows = []
    t = np.arange(0.0, 40.0, 0.5)
    specs = [(14.0, 22.0), (14.5, 25.0), (15.0, 20.0), (14.2, 18.0)]
    for i, (v_f0, gap0) in enumerate(specs):
        v_leader = np.full_like(t, 15.0)
        v = v_f0 + 0.8 * np.sin(2.0 * np.pi * t / 20.0)
        gap = gap0 + np.cumsum(np.concatenate([[0.0], (v_leader[:-1] - v[:-1]) * 0.5]))
        rows.append(
            pd.DataFrame(
                {
                    "t": t,
                    "veh_id": f"f{i}",
                    "lane": 0,
                    "leader_id": f"l{i}",
                    "gap_m": gap,
                    "v": v,
                    "v_leader": v_leader,
                }
            )
        )
    buf = io.BytesIO()
    pd.concat(rows).to_csv(buf, index=False)
    return buf.getvalue()


def test_idm_calibration_round_trip(client: TestClient) -> None:
    params = {
        "seed": 3,
        "holdout_frac": 0.25,
        "trim_quantile": 1.0,
        "de_maxiter": 2,
        "de_popsize": 4,
    }
    r = client.post(
        "/api/v1/calibrations/idm",
        files={"file": ("pairs.csv", _idm_pairs_csv_bytes(), "text/csv")},
        data={"params": json.dumps(params), "source": "synthetic pairs"},
        headers=HEADERS,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["kind"] == "idm"
    assert body["status"] == "done", body["error"]
    artifact = body["artifact"]
    assert artifact["kind"] == "idm"
    assert set(artifact["mean"]) == {"v0", "T", "a_max", "b", "s0"}
    assert artifact["n_episodes_holdout"] == 1
    assert np.isfinite(artifact["holdout_gap_rmse_m"])


def test_idm_calibration_bad_columns_fails_honestly(client: TestClient) -> None:
    r = client.post(
        "/api/v1/calibrations/idm",
        files={"file": ("bad.csv", b"t,v\n0,1\n", "text/csv")},
        headers=HEADERS,
    )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "failed"
    assert body["error"] is not None and "missing columns" in body["error"]


# ---------------------------------------------------------------------------
# Bad uploads must say what is wrong with the file
# ---------------------------------------------------------------------------
#
# ``api.jobs._calibration_error_text`` withholds the message of any exception
# raised outside FlowState's packages, because a pandas/numpy parse message can
# quote the file's contents. The commonest bad uploads all used to raise out
# there, so the customer saw "IndexError raised in
# numpy.lib._function_base_impl" and could not tell an empty file from a
# mis-delimited or mis-typed one. Each shape is now diagnosed in the job and
# re-raised as a message this package authored — which therefore survives the
# filter — naming the column and the file row but never the cell value.


def _fd_failure(client: TestClient, payload: bytes, name: str = "loops.csv") -> str:
    r = client.post(
        "/api/v1/calibrations/fd",
        files={"file": (name, payload, "text/csv")},
        headers=HEADERS,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "failed"
    assert body["error"]
    # The chain may still carry a withheld third-party cause; what matters is
    # that its *first* line is a diagnosis the customer can act on.
    first = str(body["error"]).splitlines()[0]
    assert "message withheld" not in first, body["error"]
    return first


def _idm_failure(client: TestClient, payload: bytes, name: str = "pairs.csv") -> str:
    r = client.post(
        "/api/v1/calibrations/idm",
        files={"file": (name, payload, "text/csv")},
        headers=HEADERS,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "failed"
    assert body["error"]
    # The chain may still carry a withheld third-party cause; what matters is
    # that its *first* line is a diagnosis the customer can act on.
    first = str(body["error"]).splitlines()[0]
    assert "message withheld" not in first, body["error"]
    return first


def test_empty_upload_says_the_file_is_unreadable(client: TestClient) -> None:
    for error in (_fd_failure(client, b""), _idm_failure(client, b"")):
        assert "not a readable CSV" in error
        assert "empty, truncated" in error


def test_header_only_upload_says_there_are_no_rows(client: TestClient) -> None:
    fd = _fd_failure(client, b"density_veh_m,flow_veh_s\n")
    assert "0 data rows" in fd and "loops.csv" in fd
    idm = _idm_failure(client, b"t,veh_id,lane,leader_id,gap_m,v,v_leader\n")
    assert "0 data rows" in idm and "pairs.csv" in idm


def test_thousands_separators_name_the_column_and_row_not_the_value(client: TestClient) -> None:
    """A locale-formatted export is the customer's most likely mistake."""
    payload = b'density_veh_m,flow_veh_s\n0.01,0.3\n0.02,"1,234"\n'
    error = _fd_failure(client, payload)
    assert "'flow_veh_s' is not numeric" in error
    assert "file row 3" in error  # header + two data rows
    assert "thousands separators" in error
    assert "1,234" not in error  # the value itself is never echoed


def test_all_blank_numeric_column_is_named(client: TestClient) -> None:
    """An all-NA column has rows, so a row-count check alone would miss it."""
    error = _fd_failure(client, b"density_veh_m,flow_veh_s\n,\n,\n,\n")
    assert "'density_veh_m'" in error
    assert "no numeric value" in error


def test_an_unused_occupancy_column_is_not_type_checked(client: TestClient) -> None:
    """``occupancy`` reaches the fit only without an explicit density cut.

    Type-checking it regardless would refuse a perfectly fittable file over a
    column ``calibration.fd_fit`` never reads.
    """
    frame = pd.read_csv(io.BytesIO(_fd_csv_bytes()))
    # A locale decimal comma: a real string, not one of pandas' NA tokens.
    frame["occupancy"] = "0,5"  # junk, and with _FD_PARAMS the fit ignores it
    buf = io.BytesIO()
    frame.to_csv(buf, index=False)

    r = client.post(
        "/api/v1/calibrations/fd",
        files={"file": ("loops.csv", buf.getvalue(), "text/csv")},
        data={"params": json.dumps(_FD_PARAMS), "source": "junk occupancy column"},
        headers=HEADERS,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "done", body["error"]

    # Without the density cut the column does drive the split, so it is checked.
    buf2 = io.BytesIO()
    frame.to_csv(buf2, index=False)
    r = client.post(
        "/api/v1/calibrations/fd",
        files={"file": ("loops.csv", buf2.getvalue(), "text/csv")},
        data={"params": json.dumps({"seed": 0, "n_bootstrap": 0}), "source": "junk occupancy"},
        headers=HEADERS,
    )
    assert r.status_code == 202
    failed = r.json()
    assert failed["status"] == "failed"
    assert "'occupancy' is not numeric" in failed["error"]
    assert "0,5" not in failed["error"]  # the value itself is never echoed
