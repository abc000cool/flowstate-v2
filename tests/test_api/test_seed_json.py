"""Replicate seeds leave the API as decimal strings, exactly.

Seeds are 64-bit (:func:`flowstate_core.rng.spawn_seeds` draws them below
``2**63``), and a JSON number above ``2**53`` does not survive a browser's
``JSON.parse``: 6914975401685141156 — the first replicate seed of master seed
42 — reads back as 6914975401685141000. The dashboard then showed a seed the
run never used and asked ``/heatmap?seed=`` for it, which is a 404. Every
response field holding a seed is a decimal string (``api.schemas.Seed``), and
a seed given back as that string selects exactly that replicate.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from api.schemas import (
    CalibrationParams,
    HeatmapOut,
    MergeDiagnosticsOut,
    ProgressOut,
    ReplicateMetricsOut,
    RunOut,
)
from flowstate_core.rng import spawn_seeds
from tests.test_api.conftest import HEADERS, macro_corridor_config, post_run, post_scenario

#: ``spawn_seeds(42, 3)[0]``: above 2**53, so a JSON number would round it.
BIG_SEED = 6914975401685141156
#: What ``JSON.parse`` makes of BIG_SEED as a number (the nearest double).
ROUNDED = int(float(BIG_SEED))


def test_the_seed_under_test_is_one_a_json_number_cannot_carry() -> None:
    assert spawn_seeds(42, 3)[0] == BIG_SEED
    assert BIG_SEED > 2**53
    assert ROUNDED != BIG_SEED


def test_every_seed_field_serializes_as_an_exact_decimal_string() -> None:
    run = RunOut(
        run_id="run_x",
        scenario_id=None,
        sweep_id=None,
        status="done",
        tier="micro",
        config_hash="h",
        seeded=False,
        progress=ProgressOut(completed_replicates=1, total_replicates=1),
        seeds=[BIG_SEED, 7],
        created_at="t",
    )
    assert '"seeds":["6914975401685141156","7"]' in run.model_dump_json()
    # Python-mode dumps keep the int (internal callers compare ints)
    assert run.model_dump()["seeds"] == [BIG_SEED, 7]

    rep = ReplicateMetricsOut(seed=BIG_SEED, metrics={})
    assert json.loads(rep.model_dump_json())["seed"] == "6914975401685141156"
    diag = MergeDiagnosticsOut(seed=BIG_SEED)
    assert json.loads(diag.model_dump_json())["seed"] == "6914975401685141156"
    heat = HeatmapOut(
        run_id="run_x",
        config_hash="h",
        seed=BIG_SEED,
        field="speed",
        tier="micro",
        t_bins=[],
        x_bins=[],
        values=[],
    )
    assert '"seed":"6914975401685141156"' in heat.model_dump_json()


def test_a_seed_is_accepted_as_an_int_or_a_decimal_string() -> None:
    for given in (BIG_SEED, "6914975401685141156"):
        assert CalibrationParams.model_validate({"seed": given}).seed == BIG_SEED
        assert CalibrationParams.model_validate_json(json.dumps({"seed": given})).seed == BIG_SEED
    # and a string seed round-trips through a response model unchanged
    back = RunOut.model_validate_json(
        RunOut(
            run_id="r",
            scenario_id=None,
            sweep_id=None,
            status="done",
            tier="macro",
            config_hash="h",
            seeded=False,
            progress=ProgressOut(completed_replicates=0, total_replicates=1),
            seeds=[BIG_SEED],
            created_at="t",
        ).model_dump_json()
    )
    assert back.seeds == [BIG_SEED]


def test_a_64_bit_seed_round_trips_byte_exactly_through_the_api(client: TestClient) -> None:
    scenario = post_scenario(client, macro_corridor_config(seed=42))
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "done", run["error"]
    rid = run["run_id"]
    expected = [str(s) for s in spawn_seeds(42, 3)]
    assert expected[0] == "6914975401685141156"

    # the run, read and listed: the digits on the wire are the seeds the run used
    r = client.get(f"/api/v1/runs/{rid}", headers=HEADERS)
    assert '"seeds":["6914975401685141156","134183728835869882","2378473973028931053"]' in r.text
    assert r.json()["seeds"] == expected
    listing = client.get("/api/v1/runs", headers=HEADERS)
    assert '"seeds":["6914975401685141156",' in listing.text

    # the metrics name each replicate by the same string (in directory order)
    m = client.get(f"/api/v1/runs/{rid}/metrics", headers=HEADERS)
    assert m.status_code == 200, m.text
    assert sorted(rep["seed"] for rep in m.json()["replicates"]) == sorted(expected)
    assert '"seed":"6914975401685141156"' in m.text

    # the heatmap's default replicate is the first seed, named exactly
    h = client.get(f"/api/v1/runs/{rid}/heatmap?field=speed", headers=HEADERS)
    assert h.status_code == 200, h.text
    assert '"seed":"6914975401685141156"' in h.text

    # the string handed back selects exactly that replicate ...
    for seed in expected:
        h = client.get(f"/api/v1/runs/{rid}/heatmap?field=speed&seed={seed}", headers=HEADERS)
        assert h.status_code == 200, h.text
        assert f'"seed":"{seed}"' in h.text

    # ... and the rounded number a browser would have made of it does not
    h = client.get(f"/api/v1/runs/{rid}/heatmap?field=speed&seed={ROUNDED}", headers=HEADERS)
    assert h.status_code == 404


def test_openapi_publishes_the_heatmap_seed_as_the_decimal_string_runs_list(
    client: TestClient,
) -> None:
    # A client generated from /openapi.json must get the same type for the
    # seed it sends as for the seeds it reads (``RunOut.seeds: string[]``);
    # an ``integer`` here made it write ``Number(run.seeds[0])`` and round
    # 6914975401685141156 into a 404.
    spec = client.get("/openapi.json").json()
    params = spec["paths"]["/api/v1/runs/{run_id}/heatmap"]["get"]["parameters"]
    (seed,) = [p for p in params if p["name"] == "seed"]
    assert seed["in"] == "query"
    assert seed["required"] is False
    assert seed["schema"]["anyOf"] == [
        {"type": "string", "pattern": "^[0-9]+$"},
        {"type": "null"},
    ]
    seeds = spec["components"]["schemas"]["RunOut"]["properties"]["seeds"]
    assert seeds["items"]["type"] == "string"

    # and no other query or path parameter carries a seed as a number
    for path, ops in spec["paths"].items():
        for op in ops.values():
            for p in op.get("parameters", []):
                if "seed" in p["name"].lower():
                    assert "integer" not in json.dumps(p["schema"]), (path, p)


def test_the_heatmap_seed_parses_exactly_and_refuses_anything_but_digits(
    client: TestClient,
) -> None:
    scenario = post_scenario(client, macro_corridor_config(seed=42))
    run = post_run(client, scenario["scenario_id"])
    assert run["status"] == "done", run["error"]
    rid = run["run_id"]
    url = f"/api/v1/runs/{rid}/heatmap?field=speed"

    # omitted: the first seed; given: exactly that int, not its double
    assert client.get(url, headers=HEADERS).json()["seed"] == str(BIG_SEED)
    h = client.get(f"{url}&seed={BIG_SEED}", headers=HEADERS)
    assert h.status_code == 200, h.text
    assert h.json()["seed"] == str(BIG_SEED)
    # digits that name no replicate are a 404, not a 422
    assert client.get(f"{url}&seed=999999", headers=HEADERS).status_code == 404

    # lax int parsing would take these ("5_0" as 50); the published pattern does not
    for bad in ("abc", "", "-1", "+5", "5.0", "5_0", "%205", "0x10"):
        r = client.get(f"{url}&seed={bad}", headers=HEADERS)
        assert r.status_code == 422, (bad, r.status_code, r.text)
