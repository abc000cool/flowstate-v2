"""Corridor onboarding over the API (WP-F): ``POST/GET /api/v1/corridors``.

The product path end to end on the synthetic freeway fixture from
``tests/test_microsim/test_microsim_geo.py``: three inputs (bounding box +
bearing, a detector export, the two boundary station ids) in, a calibrated
preset out, a run on it, and a report scored against the uploaded
observations. Everything below runs real ``netconvert`` and real SUMO on a
2.4 km fixture corridor; the only thing replaced is the Overpass download
(no test may touch the network, docs/CONTRACTS.md §8).
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.test_api.conftest import API_KEY, HEADERS, data_dir
from tests.test_microsim.test_microsim_geo import BBOX, LATS, LONS, fixture_osm

pytestmark = pytest.mark.integration

#: Two half-hour windows, so one clock hour of link flows exists for GEH.
WINDOW_S = 1800.0
DURATION_S = 3600.0

#: Stations on the eastbound carriageway: the fixture's nodes 1, 4 and 6,
#: which is x ≈ 0 m, 1190 m and 2002 m along the chain. The off-ramp leaves
#: at 800 m and the on-ramp joins at 1579 m, so each bracket holds exactly
#: one ramp and the observed change has somewhere to go.
STATIONS: list[dict[str, Any]] = [
    {"station": "SU", "label": "upstream", "lat": LATS[0], "lon": LONS[0], "lanes": 3},
    {"station": "SM", "label": "middle", "lat": LATS[3], "lon": LONS[3], "lanes": 3},
    {"station": "SD", "label": "downstream", "lat": LATS[5], "lon": LONS[5], "lanes": 3},
    # ~220 m south of the corridor: another road, and the projection must say
    # so rather than place it.
    {"station": "SOFF", "label": "frontage", "lat": LATS[2] - 0.0020, "lon": LONS[2], "lanes": 2},
]

#: Observed flows [veh/h]: 150 veh/h leaves at the off-ramp and 150 veh/h
#: joins at the on-ramp, so both ramps close their bracket exactly.
FLOWS = {"SU": 600.0, "SM": 450.0, "SD": 600.0, "SOFF": 200.0}
SPEEDS = {"SU": 28.0, "SM": 26.0, "SD": 24.0, "SOFF": 20.0}


def stations_csv() -> bytes:
    """The detector inventory upload (``station,label,lat,lon,lanes,kind``)."""
    lines = ["station,label,lat,lon,lanes,kind"]
    lines += [
        f"{s['station']},{s['label']},{s['lat']:.6f},{s['lon']:.6f},{s['lanes']},mainline"
        for s in STATIONS
    ]
    return ("\n".join(lines) + "\n").encode()


def detectors_csv(*, columns: dict[str, str] | None = None) -> bytes:
    """The tidy detector upload: two half-hour windows from 06:00 local."""
    header = ["timestamp", "station", "flow_veh_h", "occupancy_pct", "speed_ms", "lanes", "kind"]
    rows = []
    for window, clock in enumerate(("06:00:00", "06:30:00")):
        for station in FLOWS:
            lanes = next(s["lanes"] for s in STATIONS if s["station"] == station)
            rows.append(
                [
                    f"2026-09-15T{clock}-05:00",
                    station,
                    f"{FLOWS[station]:.1f}",
                    f"{10.0 + window:.1f}",
                    f"{SPEEDS[station]:.1f}",
                    str(lanes),
                    "mainline",
                ]
            )
    if columns:
        header = [columns.get(h, h) for h in header]
    buf = io.StringIO()
    buf.write(",".join(header) + "\n")
    for row in rows:
        buf.write(",".join(row) + "\n")
    return buf.getvalue().encode()


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Like the shared fixture, plus a throwaway presets directory.

    The onboarding job installs its corridor as a preset, so the presets
    directory must not be the repository's ``scenarios/``.
    """
    monkeypatch.setenv("FLOWSTATE_QUEUE", "inline")
    monkeypatch.setenv("FLOWSTATE_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("FLOWSTATE_DATA_DIR", str(data_dir(tmp_path)))
    monkeypatch.setenv("FLOWSTATE_SCENARIOS_DIR", str(tmp_path / "presets"))
    monkeypatch.setenv("FLOWSTATE_API_KEY", API_KEY)
    (tmp_path / "presets").mkdir(parents=True, exist_ok=True)
    from api.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def no_download(monkeypatch: pytest.MonkeyPatch) -> list[tuple[float, float, float, float]]:
    """Answer the extract stage from the hand-written fixture, never Overpass."""
    from api import onboarding_jobs

    seen: list[tuple[float, float, float, float]] = []

    def fake(bbox: tuple[float, float, float, float], dest: Path) -> Path:
        seen.append(bbox)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(fixture_osm())
        return dest

    monkeypatch.setattr(onboarding_jobs, "fetch_extract", fake)
    return seen


def post_corridor(client: TestClient, **overrides: Any) -> dict[str, Any]:
    """``POST /corridors`` with the fixture corridor's three inputs."""
    form: dict[str, Any] = {
        "name": overrides.pop("name", "fixture_corridor_eb"),
        "bbox": overrides.pop("bbox", " ".join(f"{v}" for v in BBOX)),
        "bearing_deg": overrides.pop("bearing_deg", "90"),
        "upstream_station": "SU",
        "downstream_station": "SD",
        "window_s": str(WINDOW_S),
        "t0_local": "06:00",
        "duration_s": str(DURATION_S),
        "warmup_s": "0",
        "source": "synthetic detectors",
    }
    form.update({k: v for k, v in overrides.items() if k not in ("detectors", "stations")})
    files = {
        "detectors": ("detectors.csv", overrides.get("detectors", detectors_csv()), "text/csv"),
        "stations": ("stations.csv", overrides.get("stations", stations_csv()), "text/csv"),
    }
    response = client.post("/api/v1/corridors", data=form, files=files, headers=HEADERS)
    assert response.status_code == 202, response.text
    return response.json()  # type: ignore[no-any-return]


@pytest.fixture()
def onboarded(client: TestClient, no_download: list[Any]) -> dict[str, Any]:
    """One finished onboarding of the fixture corridor (inline queue)."""
    body = post_corridor(client)
    got = client.get(f"/api/v1/corridors/{body['corridor_id']}", headers=HEADERS)
    assert got.status_code == 200, got.text
    payload: dict[str, Any] = got.json()
    assert payload["status"] == "done", payload["error"]
    return payload


class TestOnboardingHappyPath:
    def test_the_post_answers_queued_and_does_not_onboard_in_the_request(
        self, client: TestClient, no_download: list[Any]
    ) -> None:
        """202 carries the queued row, inline queue included.

        An onboarding run inside the request holds the server for its whole
        duration — seconds — and ``/healthz`` stops answering, which the
        dashboard reads as the API being offline while its own request is
        being served. The job is a background task after the response, so the
        202 body is the queued row and the work is visible only on the poll.
        """
        body = post_corridor(client, name="fixture_corridor_bg")
        assert body["status"] == "queued"
        assert body["scenario_id"] is None
        assert body["summary"] is None
        # and the job did run, once the response was out
        polled = client.get(f"/api/v1/corridors/{body['corridor_id']}", headers=HEADERS).json()
        assert polled["status"] == "done", polled["error"]
        assert polled["scenario_id"] is not None

    def test_the_job_reports_done_with_the_corridor_it_found(
        self, onboarded: dict[str, Any]
    ) -> None:
        assert onboarded["progress"] == {
            "stage": "done",
            "completed_stages": 5,
            "total_stages": 5,
        }
        summary = onboarded["summary"]
        assert summary["corridor"] == "fixture_corridor_eb"
        assert summary["chain_length_m"] == pytest.approx(2370.0, abs=60.0)
        assert summary["n_chain_edges"] == 3
        assert summary["n_ramps"] == 2
        assert [s["station"] for s in summary["stations_placed"]] == ["SU", "SM", "SD"]
        assert [s["station"] for s in summary["stations_rejected"]] == ["SOFF"]
        assert summary["stations_rejected"][0]["offset_m"] > 60.0
        assert summary["stations_without_chain_x"] == ["SOFF"]
        # The lane pre-flight: the fixture is 3 lanes throughout and every
        # mainline station's inventory says 3, so nothing disagrees. The
        # rejected station is not on this carriageway and is not compared.
        assert summary["lanes_compared"] == 3
        assert summary["lane_mismatches"] == []
        assert any("lanes vs inventory: 3 of 3" in line for line in summary["lines"])

    def test_demand_traces_to_the_stations(self, onboarded: dict[str, Any]) -> None:
        summary = onboarded["summary"]
        assert summary["inflow_peak_veh_h"] == pytest.approx(600.0)
        ramps = {r["kind"]: r for r in summary["ramps"]}
        # no ramp detector was uploaded, so both ramps close the balance
        assert {r["method"] for r in summary["ramps"]} == {"conservation"}
        assert ramps["on"]["peak"] == pytest.approx(150.0)  # veh/h joining
        assert ramps["off"]["peak"] == pytest.approx(150.0 / 600.0)  # fraction leaving
        assert summary["residuals"] == []
        assert summary["zeroed_ramps"] == []
        assert any("inflow from SU" in line for line in summary["lines"])

    def test_the_bundle_is_written(self, client: TestClient, onboarded: dict[str, Any]) -> None:
        root = Path(client.app.state.settings.results_dir) / onboarded["corridor_dir"]
        names = {p.name for p in root.iterdir()}
        assert {
            "scenario.yaml",
            "stations_x.csv",
            "observations.json",
            "demand.json",
            "extract.osm",
            "summary.txt",
        } <= names
        demand = json.loads((root / "demand.json").read_text())
        assert demand["schema"] == "flowstate.demand/1"
        assert demand["upstream_station"] == "SU"
        assert demand["downstream_station"] == "SD"
        assert demand["config_hash"] == onboarded["config_hash"]
        # the station table carries the chain positions, and says nothing for
        # the station the projection rejected
        table = (root / "stations_x.csv").read_text().splitlines()
        assert table[0].startswith("station,label,lat,lon,lanes,kind,x_m,offset_m")
        assert table[-1].split(",")[0] == "SOFF"
        assert table[-1].split(",")[6] == ""  # x_m

    def test_the_scenario_is_installed_as_a_preset(
        self, client: TestClient, onboarded: dict[str, Any]
    ) -> None:
        assert onboarded["preset_filename"] == "fixture_corridor_eb.yaml"
        presets = client.get("/api/v1/scenarios/preset", headers=HEADERS).json()
        names = {p["name"]: p for p in presets}
        assert "fixture_corridor_eb" in names
        assert names["fixture_corridor_eb"]["config_hash"] == onboarded["config_hash"]
        stored = client.get(f"/api/v1/scenarios/{onboarded['scenario_id']}", headers=HEADERS).json()
        assert stored["config_hash"] == onboarded["config_hash"]
        assert stored["config"]["network"]["kind"] == "osm"
        assert stored["config"]["sim"]["warmup_s"] == 0.0

    def test_the_extract_is_installed_under_the_corridor_name(
        self, client: TestClient, onboarded: dict[str, Any]
    ) -> None:
        settings = client.app.state.settings
        assert (Path(settings.data_dir) / "osm" / "fixture_corridor_eb.osm").is_file()
        # nothing half-written is left beside it
        assert not list((Path(settings.data_dir) / "osm").glob("*.part"))

    def test_a_second_corridor_of_the_same_name_is_refused(
        self, client: TestClient, onboarded: dict[str, Any], no_download: list[Any]
    ) -> None:
        response = client.post(
            "/api/v1/corridors",
            data={
                "name": "fixture_corridor_eb",
                "bbox": " ".join(f"{v}" for v in BBOX),
                "bearing_deg": "90",
                "upstream_station": "SU",
                "downstream_station": "SD",
            },
            files={
                "detectors": ("d.csv", detectors_csv(), "text/csv"),
                "stations": ("s.csv", stations_csv(), "text/csv"),
            },
            headers=HEADERS,
        )
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]


class TestListing:
    """``GET /corridors``: the history the dashboard offers as run/report targets."""

    def test_the_finished_corridor_is_listed_without_its_summary(
        self, client: TestClient, onboarded: dict[str, Any]
    ) -> None:
        listed = client.get("/api/v1/corridors", headers=HEADERS)
        assert listed.status_code == 200, listed.text
        (row,) = listed.json()
        # the fields a client needs to launch a run and score a report on it
        assert row["corridor_id"] == onboarded["corridor_id"]
        assert row["name"] == "fixture_corridor_eb"
        assert row["status"] == "done"
        assert row["scenario_id"] == onboarded["scenario_id"]
        assert row["observations_path"] == onboarded["observations_path"]
        assert row["config_hash"] == onboarded["config_hash"]
        # everything the detail row carries except the summary, which belongs
        # to the corridor being looked at, not to a listing of all of them
        assert "summary" not in row
        assert set(row) == set(onboarded) - {"summary"}

    def test_rows_are_newest_first_and_bounded_by_limit(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two onboardings that fail at the first stage: the listing is not
        about success, it is about which corridors this server has."""
        from api import onboarding_jobs

        def refuse(bbox: Any, dest: Path) -> Path:
            raise RuntimeError("no network in tests")

        monkeypatch.setattr(onboarding_jobs, "fetch_extract", refuse)
        first = post_corridor(client, name="fixture_list_one")
        second = post_corridor(client, name="fixture_list_two")

        rows = client.get("/api/v1/corridors", headers=HEADERS).json()
        assert [r["corridor_id"] for r in rows] == [
            second["corridor_id"],
            first["corridor_id"],
        ]
        assert [r["name"] for r in rows] == ["fixture_list_two", "fixture_list_one"]
        assert [r["status"] for r in rows] == ["failed", "failed"]
        # a failed onboarding offers nothing to run or report against, and
        # says so rather than being left out of the history
        assert rows[0]["scenario_id"] is None
        assert rows[0]["observations_path"] is None
        assert rows[0]["error_kind"] == "corridor_extract"

        newest = client.get("/api/v1/corridors?limit=1", headers=HEADERS).json()
        assert [r["corridor_id"] for r in newest] == [second["corridor_id"]]
        assert client.get("/api/v1/corridors?limit=0", headers=HEADERS).status_code == 422

    def test_an_empty_server_lists_nothing(self, client: TestClient) -> None:
        assert client.get("/api/v1/corridors", headers=HEADERS).json() == []


class TestRunAndReport:
    def test_the_corridor_runs_and_scores_against_its_observations(
        self, client: TestClient, onboarded: dict[str, Any]
    ) -> None:
        run = client.post(
            "/api/v1/runs",
            json={"scenario_id": onboarded["scenario_id"], "replicates": 1},
            headers=HEADERS,
        )
        assert run.status_code == 202, run.text
        run_body = run.json()
        assert run_body["status"] == "done", run_body["error"]
        assert run_body["scenario_id"] == onboarded["scenario_id"]
        assert not run_body["seeded"]  # onboarding seeds no perturbation

        report = client.post(
            "/api/v1/reports",
            json={
                "run_ids": [run_body["run_id"]],
                "title": "onboarded corridor",
                "observations_path": onboarded["observations_path"],
            },
            headers=HEADERS,
        )
        assert report.status_code == 202, report.text
        report_body = report.json()
        assert report_body["status"] == "done", report_body["error"]
        observed = report_body["observed"]
        assert observed["corridor"] == "fixture_corridor_eb"
        assert observed["n_stations"] == 3
        assert observed["n_link_hours"] > 0
        assert observed["n_speed_cells"] > 0

        markdown = client.get(
            f"/api/v1/reports/{report_body['report_id']}/markdown", headers=HEADERS
        ).text
        geh_row = next(
            line for line in markdown.splitlines() if line.startswith("| link_flows_geh ")
        )
        rmspe_row = next(
            line for line in markdown.splitlines() if line.startswith("| speeds_rmspe ")
        )
        assert "not evaluated" not in geh_row
        assert "not evaluated" not in rmspe_row


class TestRefusals:
    def test_a_bearing_with_no_chain_fails_with_a_plain_message(
        self, client: TestClient, no_download: list[Any]
    ) -> None:
        body = post_corridor(client, name="fixture_corridor_north", bearing_deg="0")
        payload = client.get(f"/api/v1/corridors/{body['corridor_id']}", headers=HEADERS).json()
        assert payload["status"] == "failed"
        assert payload["error_kind"] == "corridor_network"
        assert payload["progress"]["stage"] == "network"
        assert "heading" in payload["error"] or "bearing" in payload["error"]
        assert "Traceback" not in payload["error"]
        assert payload["scenario_id"] is None
        # nothing was installed for a corridor that was never built
        presets = client.get("/api/v1/scenarios/preset", headers=HEADERS).json()
        assert "fixture_corridor_north" not in {p["name"] for p in presets}

    def test_an_idm_calibration_outside_the_roots_is_refused(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/corridors",
            data={
                "name": "fixture_escape",
                "bbox": " ".join(f"{v}" for v in BBOX),
                "bearing_deg": "90",
                "upstream_station": "SU",
                "downstream_station": "SD",
                "idm_calibration": "/etc/passwd",
            },
            files={
                "detectors": ("d.csv", detectors_csv(), "text/csv"),
                "stations": ("s.csv", stations_csv(), "text/csv"),
            },
            headers=HEADERS,
        )
        assert response.status_code == 422
        (error,) = response.json()["detail"]
        assert error["type"] == "path_outside_roots"
        assert error["loc"] == ["body", "idm_calibration"]

    @pytest.mark.parametrize(
        ("field", "value", "needle"),
        [
            ("name", "../escape", "must match"),
            ("bbox", "44.9 -93.3 44.8 -93.2", "south < north"),
            ("bbox", "44.9 -93.3 45.0", "four numbers"),
            ("bearing_deg", "400", "bearing_deg"),
            ("warmup_s", "99999", "warmup_s"),
        ],
    )
    def test_malformed_form_fields_are_422(
        self, client: TestClient, field: str, value: str, needle: str
    ) -> None:
        form = {
            "name": "fixture_bad",
            "bbox": " ".join(f"{v}" for v in BBOX),
            "bearing_deg": "90",
            "upstream_station": "SU",
            "downstream_station": "SD",
            "duration_s": str(DURATION_S),
            field: value,
        }
        response = client.post(
            "/api/v1/corridors",
            data=form,
            files={
                "detectors": ("d.csv", detectors_csv(), "text/csv"),
                "stations": ("s.csv", stations_csv(), "text/csv"),
            },
            headers=HEADERS,
        )
        assert response.status_code == 422, response.text
        assert needle in json.dumps(response.json()["detail"])

    def test_a_column_map_renames_the_uploaded_columns(
        self, client: TestClient, no_download: list[Any]
    ) -> None:
        renamed = detectors_csv(columns={"timestamp": "ts", "flow_veh_h": "volume"})
        body = post_corridor(
            client,
            name="fixture_corridor_mapped",
            detectors=renamed,
            column_map=json.dumps({"timestamp": "ts", "flow": "volume"}),
        )
        payload = client.get(f"/api/v1/corridors/{body['corridor_id']}", headers=HEADERS).json()
        assert payload["status"] == "done", payload["error"]
        assert payload["summary"]["inflow_peak_veh_h"] == pytest.approx(600.0)

    def test_an_unknown_corridor_is_404(self, client: TestClient) -> None:
        assert client.get("/api/v1/corridors/cor_nope", headers=HEADERS).status_code == 404

    def test_a_name_with_a_trailing_newline_is_refused(self, client: TestClient) -> None:
        """`$` matches before a trailing newline; `fullmatch` does not."""
        response = client.post(
            "/api/v1/corridors",
            data={
                "name": "fixture_newline\n",
                "bbox": " ".join(f"{v}" for v in BBOX),
                "bearing_deg": "90",
                "upstream_station": "SU",
                "downstream_station": "SD",
            },
            files={
                "detectors": ("d.csv", detectors_csv(), "text/csv"),
                "stations": ("s.csv", stations_csv(), "text/csv"),
            },
            headers=HEADERS,
        )
        assert response.status_code == 422, response.text
        assert "must match" in json.dumps(response.json()["detail"])

    def test_a_name_whose_extract_already_exists_is_refused(self, client: TestClient) -> None:
        """A shipped extract is a scenario's map: onboarding never replaces it."""
        settings = client.app.state.settings
        shipped = Path(settings.data_dir) / "osm" / "i24_motion.osm"
        shipped.parent.mkdir(parents=True, exist_ok=True)
        shipped.write_text("<osm/>")
        response = client.post(
            "/api/v1/corridors",
            data={
                "name": "i24_motion",
                "bbox": " ".join(f"{v}" for v in BBOX),
                "bearing_deg": "90",
                "upstream_station": "SU",
                "downstream_station": "SD",
            },
            files={
                "detectors": ("d.csv", detectors_csv(), "text/csv"),
                "stations": ("s.csv", stations_csv(), "text/csv"),
            },
            headers=HEADERS,
        )
        assert response.status_code == 409, response.text
        assert "OSM extract" in response.json()["detail"]
        assert shipped.read_text() == "<osm/>"  # untouched


class TestUploadsAndRetries:
    """What the request leaves on disk, and what a failed job takes back."""

    def test_the_two_uploads_never_collide(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Client filenames do not name the stored files; a clash cannot happen.

        The two parts share one upload directory, so a station table the
        client happens to call ``stations_x.csv`` (the name the server used to
        derive for it from a detector file called ``x.csv``) must not land on
        the detector export.
        """
        from api import onboarding_jobs

        def refuse(bbox: Any, dest: Path) -> Path:
            raise RuntimeError("no network in tests")

        monkeypatch.setattr(onboarding_jobs, "fetch_extract", refuse)
        response = client.post(
            "/api/v1/corridors",
            data={
                "name": "fixture_upload_names",
                "bbox": " ".join(f"{v}" for v in BBOX),
                "bearing_deg": "90",
                "upstream_station": "SU",
                "downstream_station": "SD",
            },
            files={
                "detectors": ("stations_x.csv", detectors_csv(), "text/csv"),
                "stations": ("x.csv", stations_csv(), "text/csv"),
            },
            headers=HEADERS,
        )
        assert response.status_code == 202, response.text
        uploads = Path(client.app.state.settings.uploads_dir)
        stored = sorted(p for p in uploads.rglob("*") if p.is_file())
        assert [p.name for p in stored] == ["detectors.csv", "stations.csv"]
        assert stored[0].read_bytes() == detectors_csv()
        assert stored[1].read_bytes() == stations_csv()

    def test_a_failed_job_frees_its_name(self, client: TestClient, no_download: list[Any]) -> None:
        """The extract this job installed goes again, so the name is retryable."""
        settings = client.app.state.settings
        extract = Path(settings.data_dir) / "osm" / "fixture_retry.osm"
        preset = Path(settings.scenarios_dir) / "fixture_retry.yaml"

        body = post_corridor(client, name="fixture_retry", bearing_deg="0")
        payload = client.get(f"/api/v1/corridors/{body['corridor_id']}", headers=HEADERS).json()
        assert payload["status"] == "failed"
        assert payload["progress"]["stage"] == "network"  # the extract was installed first
        assert not extract.exists()
        assert not preset.exists()

        # the same name is accepted again and reaches the same real stage —
        # not refused as taken, and not failed on "already exists"
        again = post_corridor(client, name="fixture_retry", bearing_deg="0")
        retried = client.get(f"/api/v1/corridors/{again['corridor_id']}", headers=HEADERS).json()
        assert retried["progress"]["stage"] == "network"
        assert "already exists" not in retried["error"]
        assert not extract.exists()
