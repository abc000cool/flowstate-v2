# Quickstart — machine check to first report in ten minutes

For someone who has just been handed this repository: a traffic engineer with
a laptop and no context. At the end you will have checked that your machine
can run the engine, run a real (small) SUMO simulation through the service,
and downloaded a generated report bundle. No Docker, no cloud, no data
download.

**Ten minutes is dominated by the one-time install.** Every command below was
run on the machine this page was written on (Apple silicon, macOS 26, 16 GB) —
the measured wall-clock is in the last column of each step. The only exceptions
are the clone and `uv sync` in step 1 (that machine was already cloned and
synced) and the dashboard's `npm` commands in step 6; all three are marked
below.

| Step | What it does | Measured here |
|---|---|---|
| 1. Install | `uv sync --all-packages --dev` | ~2–5 min on a fresh machine (not re-run here) |
| 2. Doctor | `scripts/doctor.py` — 15 checks incl. a real ring run | 1.9 s |
| 3. API | `uvicorn api.main:app`, inline queue | 0.9 s to `/healthz` |
| 4. First run | preset → scenario → 2-replicate ring run | 1.6 s |
| 5. Report | metrics + report bundle + markdown | 0.7 s |
| 6. Dashboard | `npm install && npm run dev` | documented, not run here |

---

## 1. Clone and install (once)

```sh
git clone https://github.com/abc000cool/flowstate-v2.git
cd flowstate-v2
uv sync --all-packages --dev
```

Needs [uv](https://docs.astral.sh/uv/) and Python 3.12 (`uv python install 3.12`
if you do not have it). The sync installs the workspace packages *and* SUMO
1.27.1 from PyPI wheels — there is nothing else to install, no system SUMO, no
`SUMO_HOME`.

`uv sync` is the only command on this page that writes to the environment.
Everything afterwards runs with `uv run --no-sync`, which uses the environment
as it is and never re-resolves it.

## 2. Check the machine (`scripts/doctor.py`)

```sh
uv run --no-sync python scripts/doctor.py
```

One row per check, `PASS` / `WARN` / `FAIL`, a one-line fix under every row
that is not `PASS`, and **exit code 1 if anything FAILs**. A `WARN` is a
machine that works with a caveat (no Redis, a small disk, a data file you do
not have yet). On the machine this page was written on — `<repo>` stands in
for the repository path:

```
CHECK           STATUS  DETAIL
python          PASS    3.12 (CPython, macOS-26.5-arm64-arm-64bit)
settings        PASS    queue=inline results=<repo>/runs scenarios=<repo>/scenarios
sumo_packages   PASS    sumolib, traci, libsumo import
sumo_version    PASS    pin 1.27.1 (from packages/microsim/pyproject.toml); eclipse-sumo=1.27.1, libsumo=1.27.1, traci=1.27.1, sumolib=1.27.1
pyarrow         PASS    25.0.1 (excluded: 24.0.0)
netconvert      PASS    <repo>/.venv/lib/python3.12/site-packages/sumo/bin/netconvert
redis_server    PASS    /opt/homebrew/bin/redis-server
results_root    PASS    <repo>/runs writable
data_roots      PASS    <repo>/runs/uploads, <repo>/runs
scenarios       PASS    22/22 parse
scenario_files  PASS    38/38 referenced files present
osm_extracts    PASS    5 under <repo>/data/osm: i24_motion.osm, i24_motion_corrected.osm, i24_nashville.osm, mndot_i94_wb_stpaul.osm (the doctor lists the first four)
disk            PASS    31.6 GB free at <repo>/runs
memory          PASS    16.0 GB total
smoke           PASS    ring_sugiyama 10 sim-s in 0.21 s (93 steps/s, 47x real time)

15 pass, 0 warn, 0 fail — this machine can run FlowState (docs/QUICKSTART.md)
```

The last row is the one that matters: `smoke` runs 10 simulated seconds of the
230 m Sugiyama ring through `microsim.runner.run_micro` into a temporary
directory. If it passes, SUMO, libsumo, the Parquet writer and the scenario
schema all work together here.

Two flags:

```sh
uv run --no-sync python scripts/doctor.py --no-sim     # skip the simulation (0.5 s)
uv run --no-sync python scripts/doctor.py --json       # machine-readable, for CI
```

`--json` prints one object with `ok`, `counts` and every check's `name`,
`status`, `detail` and `fix`; stdout stays clean (libsumo's pyarrow banner is
sent to stderr), so `| jq` works.

## 3. Start the API

```sh
FLOWSTATE_QUEUE=inline uv run --no-sync uvicorn api.main:app --port 8000
```

`FLOWSTATE_QUEUE=inline` (the default outside Docker) runs jobs synchronously
in the API process: no Redis, no worker, no concurrency. That is right for
this walkthrough and wrong for a pilot — for anything customer-facing use the
Compose stack in [DEPLOYMENT.md](DEPLOYMENT.md). Artifacts and the SQLite
metadata store land under `./runs` (override with `FLOWSTATE_RESULTS_DIR=...`;
this page's numbers were produced with it pointed at a scratch directory).

In a second terminal:

```sh
curl -s http://127.0.0.1:8000/healthz
# {"status":"ok","store":"ok","queue":"ok","queue_kind":"inline"}
```

Every `/api/...` route needs the key in an `X-API-Key` header. Under the
inline queue the built-in default is accepted, so export it once:

```sh
export KEY=dev-key-change-me
```

That key is published in this repository and is therefore not a secret. A
deployed service (`FLOWSTATE_QUEUE=redis`) **refuses to start** on it and says
so; set `FLOWSTATE_API_KEY` to your own value there.

Interactive API docs: <http://127.0.0.1:8000/docs>.

## 4. First run: the ring benchmark

The repository's `scenarios/*.yaml` are offered as presets (0.3 s):

```sh
curl -s -H "X-API-Key: $KEY" http://127.0.0.1:8000/api/v1/scenarios/preset \
  | jq -r '.[] | .filename + "  " + .name'
```

Store one of them as a scenario — `POST /scenarios` takes the config as JSON
or YAML (0.3 s):

```sh
curl -s -H "X-API-Key: $KEY" http://127.0.0.1:8000/api/v1/scenarios/preset \
  | jq '.[] | select(.filename == "ring_sugiyama.yaml") | .config' \
  | curl -s -X POST http://127.0.0.1:8000/api/v1/scenarios \
      -H "X-API-Key: $KEY" -H 'Content-Type: application/json' --data-binary @- \
  | jq '{scenario_id, name, config_hash}'
# {"scenario_id": "scn_785240552ea8", "name": "ring_sugiyama", "config_hash": "a226444c0145"}
```

Or, equivalently, post the YAML file itself:

```sh
curl -s -X POST http://127.0.0.1:8000/api/v1/scenarios -H "X-API-Key: $KEY" \
  -H 'Content-Type: application/x-yaml' --data-binary @scenarios/ring_sugiyama.yaml
```

Run it with **2 replicates** (about a second here; the doctor's 10-second smoke above reported 47× real time on this laptop while other work ran — the §3.4 performance targets are measured by the perf workflow, not by the doctor):

```sh
export SCN=scn_785240552ea8          # the scenario_id you just got back
curl -s -X POST http://127.0.0.1:8000/api/v1/runs -H "X-API-Key: $KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"scenario_id\": \"$SCN\", \"replicates\": 2}" \
  | jq '{run_id, status, tier, seeds}'
```

> **2 replicates is a smoke test, not a result.** Headline numbers in this
> project are means with 95% confidence intervals over **at least 20 seeds**
> (CLAUDE.md §0.6). The service does not hide this: the metrics below come
> back with `underpowered: true`, and the report's `n_seeds` criterion
> **fails**. Drop the `replicates` field to use the scenario's own 20.

Poll until it is done (with the inline queue the `POST` only returns once the
work is finished, so this is already `done`; with the Redis queue you poll):

```sh
export RUN=run_bc59dd115c9b       # the run_id from the response above
curl -s -H "X-API-Key: $KEY" "http://127.0.0.1:8000/api/v1/runs/$RUN" \
  | jq '{status, progress, error}'
# {"status": "done", "progress": {"completed_replicates": 2, "total_replicates": 2}, "error": null}
```

Metrics, with per-seed values and t-distribution CIs (10 ms — they are
computed once by the job and cached):

```sh
curl -s -H "X-API-Key: $KEY" "http://127.0.0.1:8000/api/v1/runs/$RUN/metrics" \
  | jq '{underpowered, throughput: .aggregate.throughput_veh_h}'
# {"underpowered": true,
#  "throughput": {"mean": 728.57, "lo95": 619.66, "hi95": 837.48, "n": 2, "underpowered": true}}
```

The space-time speed field the dashboard draws is
`GET /runs/$RUN/heatmap?field=speed`.

## 5. The report

```sh
curl -s -X POST http://127.0.0.1:8000/api/v1/reports -H "X-API-Key: $KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"run_ids\": [\"$RUN\"], \"title\": \"Ring smoke\"}" \
  | jq '{report_id, status, error, report_path}'

export REP=rpt_f300f6d65607       # the report_id from the response above
curl -s -H "X-API-Key: $KEY" "http://127.0.0.1:8000/api/v1/reports/$REP/markdown" -o report.md
```

0.6 s for the bundle, 8 ms for the download; `…/pdf` and `…/archive` serve the
same bundle in the other two shapes. What you get is provenance (config hash,
every seed, package versions, calibration artifacts), an acceptance-criteria
table scored against a profile from `GET /api/v1/criteria`
(`fhwa_default`, `fhwa_tat3_2004`, `odot_vissim_2011`, `txdot_tsap_ch13`), the
metric tables with CIs, speed contours and a limitations section.

Read the criteria table of *this* report as the honest output it is:

```
| n_seeds        | 2   | n_seeds >= 20            | yes | FAIL          |
| link_flows_geh | —   | GEH < 5 for >= 85% ...   | no  | NOT EVALUATED |
```

Criteria whose evidence is observed field data the service does not hold
(link counts, an observed speed field, the ring benchmarks, the sensitivity
grid) come back **NOT EVALUATED** — a third status beside PASS and FAIL; a
report with such rows does not claim validation, and the API never accepts a
number typed into a request body (CLAUDE.md §7.4). An API report is a run-set metrics bundle with the profile's
thresholds stated; the signed-off corridor reports under `docs/reports/` are
produced by scripts that compute the observed side from data.

Stop the API with `Ctrl-C` when you are done.

## 6. The dashboard (documented here, not run)

The React dashboard is a separate Vite app that talks to the API you just
started. From `frontend/`, as [frontend/README.md](../frontend/README.md)
documents:

```sh
cd frontend
npm install --cache .npm-cache     # the repo's documented invocation; npm ci also works
npm run dev                        # Vite on :5173, proxies /api and /healthz to :8000
```

**These two commands were not run while writing this page** (no Node build on
the authoring machine); they are reproduced from `frontend/README.md`, which
is the maintained source for them. Open <http://localhost:5173>, then paste
your API key into the Settings drawer in the left rail once — it is stored in
the browser's `localStorage`, not in the app. If the API is unreachable the
dashboard falls back to demo data and says so in an amber banner.

A production dashboard is served by the API itself at `/` (same origin, no
CORS, built into the Docker image).

### From the dashboard

Steps 2–5 above also exist as a panel: **First run** in the left rail (and at
the top of Scenarios while the server holds no runs yet) walks the same path —
connect, store the `ring_sugiyama` preset, launch the 2-replicate smoke run,
watch it finish, open its metrics and heatmap, then generate the report and
download the markdown. Each step reports `done` / `current` / `blocked` from
what the API actually answered, with one line of why when it is blocked; a
step is never ticked off demo data, so while the API is unreachable every step
that would write says so and the buttons stay disabled. The 2-replicate note
is the same one as above: it is a smoke test, and a headline number needs at
least 20 seeds.

## 7. The Docker path

For a pilot — Redis queue, a real worker, concurrency, a published port —
`docker compose up -d --build` and then [DEPLOYMENT.md](DEPLOYMENT.md), which
covers environment variables, sizing, backups, key rotation and job recovery.
The inline queue used above is explicitly not for that.

---

## When it fails

Symptoms in the order you would hit them. The `doctor` rows are what
`scripts/doctor.py` prints; the HTTP rows are what the API returns.

| Symptom | Cause | Fix |
|---|---|---|
| `doctor: python FAIL` | interpreter is not 3.12 | `uv python install 3.12`, then `uv sync --all-packages --dev` |
| `doctor: settings FAIL` (reason on stderr) | a `FLOWSTATE_*` variable is invalid — e.g. `FLOWSTATE_QUEUE` that is neither `inline` nor `redis` | fix the variable named in the message ([DEPLOYMENT.md](DEPLOYMENT.md) §2) |
| `doctor: sumo_packages FAIL` | `libsumo`/`traci`/`sumolib` not importable | `uv sync --all-packages --dev`; do not install SUMO by hand |
| `doctor: sumo_version FAIL` | a SUMO wheel that is not the pinned 1.27.1 | goldens are per-SUMO-version (CLAUDE.md §9): reinstall the pin; never bump it casually |
| `doctor: pyarrow FAIL (24.0.0)` | pyarrow 24.0.0's bundled Arrow collides with libsumo's; Parquet writes die mid-run | install any other pyarrow (the workspace pins `>=18.0,!=24.0.0`) |
| `doctor: netconvert FAIL` | the `eclipse-sumo` wheel's binaries are missing | `uv sync --all-packages --dev`; only the OSM-import path needs it |
| `doctor: redis_server WARN` | no Redis on this machine | nothing to do for this page — the inline queue needs none; Compose brings its own |
| `doctor: results_root / data_roots FAIL` | `FLOWSTATE_RESULTS_DIR` is not creatable or not writable | point it at a writable directory; every artifact, upload and `metadata.db` lives under it |
| `doctor: scenarios FAIL` | a preset YAML does not validate | run `ScenarioConfig.from_yaml('<file>')` for the full pydantic error; if you did not edit it, the checkout is damaged |
| `doctor: scenario_files WARN` | a preset names an OSM extract or calibration artifact that is not on this machine | that preset alone cannot run; `ring_sugiyama` and `corridor_10km` need no files |
| `doctor: disk / memory WARN` | under 5 GB free or under 8 GB RAM | fine for this page; run 20-seed sweeps of a real corridor elsewhere |
| `{"detail":"invalid or missing X-API-Key"}` (401) | no header, or the wrong key | send `-H "X-API-Key: $KEY"`; under the inline queue the default is `dev-key-change-me` |
| API refuses to start: "an accepted API key is still the published default" | `FLOWSTATE_QUEUE=redis` with the published default key | set `FLOWSTATE_API_KEY` to your own secret (deployed services only) |
| `{"detail":"scenario 'scn_…' not found"}` (404) | the `scenario_id` was never stored, or the results root changed under you | re-`POST /scenarios`; the store lives in `FLOWSTATE_RESULTS_DIR/metadata.db` |
| 422 with `"type": "path_outside_roots"` | a scenario names a file outside the allowed roots (results root, uploads, `FLOWSTATE_DATA_DIR`, the repo's `artifacts/` and `data/`) | move the file under one of those roots, or set `FLOWSTATE_DATA_DIR` |
| 422 "unparseable config body" / a pydantic error list | the posted YAML/JSON is not a valid `ScenarioConfig` | the error names the field; the presets are working examples |
| 413 "request body exceeds the limit" | body over `FLOWSTATE_MAX_BODY_MB` (8), upload over `FLOWSTATE_MAX_UPLOAD_MB` (200) | raise the variable or send less |
| 422 "run set mixes tiers: …" on `POST /reports` | the run set holds macroscopic (screening) runs beside micro ones | request the report from the micro-tier runs alone (CLAUDE.md §5.6) |
| Report row `status: failed`, `error_kind: "report_refused"` | the report generator refused the set — e.g. macro-only runs, which may never produce a validation report | run the scenario on the micro tier and report from that |
| Run row `status: failed` with an `error` chain (no `error_kind`) | the simulation itself failed — a missing artifact or OSM file, a bad network, a SUMO error | the chain's last line is the cause; the server log has the traceback. A missing file usually means `doctor: scenario_files` warned about it |
| Corridor row `status: failed`, `error_kind: "corridor_<stage>"` | corridor onboarding failed at that stage — one of `extract`, `network`, `observations`, `demand`, `install` | the stage names the input to look at (the detector CSV, the bbox, the OSM extract) |
| `POST /runs` never returns | the inline queue runs the whole job inside the request — a big scenario × many replicates can take minutes | use small replicate counts locally; use the Compose stack (`FLOWSTATE_QUEUE=redis`) for real sweeps |
| `ERROR: [Errno 48] error while attempting to bind on address ('127.0.0.1', 8000): … address already in use` | something else holds :8000 | start with `--port 8001`, or stop the other process |
| Dashboard shows the amber "API offline — showing demo data" banner | the API is not running, or the dashboard's origin is not in `FLOWSTATE_CORS_ORIGINS` | start the API; for a dashboard on another host/port add its origin ([DEPLOYMENT.md](DEPLOYMENT.md) §2) |
| Dashboard 401s on every call | the key in the Settings drawer is not the key the API is running | paste the right key; it is one shared deployment key, not a login |

## Where to go next

* [README.md](../README.md) — what the project claims, with the evidence, and
  what it does not
* [DEPLOYMENT.md](DEPLOYMENT.md) — running it for a pilot
* [CONTRACTS.md](CONTRACTS.md) — the interfaces (scenario schema, controller
  signature, run-artifact layout, metric definitions)
* [QA.md](QA.md) — the questions a reviewer asks, each pointing at its evidence
* `uv run --no-sync pytest tests -q -m "not slow"` — the fast battery,
  including the ring emergence + dampening gate
