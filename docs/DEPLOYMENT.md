# Deployment runbook

How to run the FlowState service for a pilot: one host, Docker Compose, one
shared API key. Everything here is what the code does today (`docker-compose.yml`,
`packages/api/api/settings.py`, `packages/api/api/worker.py`); nothing is
aspirational. For the API surface see `docs/CONTRACTS.md`; for the load test
that sized it see `docs/M5_LOAD_TEST.md`.

## 1. What runs

| Service | Image | Role |
|---|---|---|
| `redis` | `redis:7-alpine`, append-only, volume `redis-data` | the job queue (state, not a cache) |
| `api` | this repository's Dockerfile | FastAPI on `:8000`, dashboard at `/`, OpenAPI at `/docs`, health at `/healthz` |
| `worker` | same image, `python -m api.worker` | every simulation, calibration and report; `init: true`, 6 h stop grace |

Both containers run as the non-root user `flowstate` (uid 10001) and share the
`flowstate-runs` volume at `/data/runs`: per-run artifacts under `runs/`,
uploads under `uploads/`, calibrations, reports, and the SQLite metadata
store `metadata.db`. The published port is bound to `127.0.0.1` until you
change it.

## 2. Environment

| Variable | Default | Meaning |
|---|---|---|
| `FLOWSTATE_API_KEY` | `dev-key-change-me` (refused under the Redis queue) | the primary shared key; every `/api/...` route needs it in `X-API-Key` |
| `FLOWSTATE_API_KEYS` | unset | extra accepted keys, comma-separated (whitespace trimmed, empty entries dropped) — the rotation window below; the default key is refused here too |
| `FLOWSTATE_QUEUE` | `inline` outside Compose, `redis` inside | `redis` = jobs run on the worker; `inline` = synchronous, for tests and small local runs only |
| `FLOWSTATE_REDIS_URL` | `redis://redis:6379/0` | queue location |
| `FLOWSTATE_RESULTS_DIR` | `./runs` (Compose: `/data/runs`) | results root: runs, uploads, calibrations, reports, `metadata.db` |
| `FLOWSTATE_SCENARIOS_DIR` | the repository's `scenarios/` | presets served by `GET /scenarios/preset` |
| `FLOWSTATE_DATA_DIR` | unset | an extra root from which scenario configs may reference OSM files and calibration artifacts |
| `FLOWSTATE_FRONTEND_DIST` | the built dashboard in the image | static files served at `/` |
| `FLOWSTATE_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | comma-separated browser origins allowed to call `/api/...` cross-origin (scheme + host + optional port, no trailing slash). Needed only when the dashboard is served from somewhere other than this API — a dashboard on the API's own `/` is same-origin. `*` allows any origin (demos only); a single `-` allows none |
| `FLOWSTATE_MAX_BODY_MB` | 8 | request-body cap on `/api/` (HTTP 413) |
| `FLOWSTATE_MAX_UPLOAD_MB` | 200 | calibration upload cap (HTTP 413) |
| `FLOWSTATE_WORKER_PATH_ROOTS` | set per job by the worker | worker-side copy of the allowed roots (`os.pathsep`-separated absolute paths) that `microsim` enforces when it reads calibration artifacts and OSM files; the API's 422 is the primary control, this is defence in depth; unset means unrestricted, which is the library default for scripts |

Both caps hold whether or not the client declares a `Content-Length`: a
declared length over the cap is refused before the body is read, and a chunked
body is counted as it streams and cut off at the cap (`POST
/api/v1/calibrations/{kind}` gets the upload cap plus the body cap for its
multipart framing; everything else gets the body cap). Nothing is enqueued for
a request that is refused.

Scenario configs may only reference files under the results root, the uploads
directory, `FLOWSTATE_DATA_DIR`, or the repository's `artifacts/` and `data/`;
anything else is refused with 422 (`path_outside_roots`). Put customer OSM
extracts and calibration artifacts under `FLOWSTATE_DATA_DIR`.

The image ships the repository's `artifacts/` and `data/osm/` at `/app`, so
every preset in the gallery runs out of the box — 14 of the 17 name an
`artifacts/...` calibration and 12 also name a `data/osm/...` extract, and
containment is a pure path test, so a missing file is accepted at
`POST /scenarios` and only fails in the worker. The rest of `data/` (local
PeMS/I-24 payloads) is excluded from the build context.

## 3. First start

```sh
FLOWSTATE_API_KEY="$(openssl rand -hex 24)" docker compose up -d --build
curl -s http://127.0.0.1:8000/healthz
curl -s -H "X-API-Key: $FLOWSTATE_API_KEY" http://127.0.0.1:8000/api/v1/scenarios/preset
```

Keep the key in a `.env` file next to `docker-compose.yml` (`FLOWSTATE_API_KEY=...`)
rather than in the shell history. Open the dashboard, paste the key into its
Settings drawer once (it is stored in the browser's `localStorage`).

To publish the port, swap the `127.0.0.1:8000:8000` mapping for `8000:8000`
in `docker-compose.yml` and put TLS in front (a reverse proxy; the API speaks
plain HTTP). The API will not start under the Redis queue on the built-in
default key.

## 4. Sizing

The M5 load test ran 10 concurrent macro sweeps plus a SUMO sweep on one host
with two workers and kept `/healthz` under 12 ms p95. Per-replicate SUMO wall
time scales with vehicle count and duration (CLAUDE.md §3.4 targets 5x real
time for the 10 km corridor at 20 sim-min on laptop-class cores). One run job
executes its replicates in a process pool of `min(CPU count, replicates)`, so
a worker uses every core it can see. Plan one CPU core per concurrent
replicate and about 1 GB RAM per worker; 20-seed sweeps of the I-24 replica
are a cloud job (`scripts/gcp/`), not a pilot-host job.

## 5. Operations

**Health.** `GET /healthz` answers 200 when the store and the queue are
reachable, 503 otherwise. The Compose healthchecks mark a container unhealthy
for `docker compose ps`; `restart: unless-stopped` restarts a container that
exits, not one that is merely unhealthy, so alert on `/healthz`.

**Backups.** Everything durable is the `flowstate-runs` volume (artifacts and
`metadata.db`) plus `redis-data` (queued jobs). Snapshot the runs volume with
the worker stopped or accept that a run in flight is incomplete in the copy:

```sh
docker compose stop worker
docker run --rm -v flowstate-runs:/data -v "$PWD":/backup alpine tar czf /backup/flowstate-runs.tgz -C /data .
docker compose start worker
```

**Key rotation.** The API accepts every key in `FLOWSTATE_API_KEY` plus the
comma-separated `FLOWSTATE_API_KEYS`, so a key is replaced without locking
anyone out:

1. Add the new key beside the old one in `.env`, e.g.
   `FLOWSTATE_API_KEY=<old>` and `FLOWSTATE_API_KEYS=<new>` (the primary key
   first, then the list; duplicates collapse). `docker-compose.yml` forwards
   `FLOWSTATE_API_KEY` only, so add one line to the `api` service's
   `environment:` block the first time you rotate:
   `FLOWSTATE_API_KEYS: ${FLOWSTATE_API_KEYS:-}` (an unset or empty value
   changes nothing).
2. `docker compose up -d` — the api container restarts and now accepts both.
3. Move the clients: paste the new key into every dashboard's Settings drawer
   and update any scripted `X-API-Key` headers.
4. Remove the old key (`FLOWSTATE_API_KEY=<new>`, drop `FLOWSTATE_API_KEYS`)
   and `docker compose up -d` again. The old key is refused from that restart.

Rotating in one step — changing `FLOWSTATE_API_KEY` alone — still works and is
still a lock-out until every client has the new key. The startup refusal of
the published default key applies to every entry in the list, so the default
can never be smuggled in as a second accepted key.

**Upgrading.** `git pull && docker compose up -d --build`. A `docker compose
down` waits up to 6 h for the worker's job in flight; to abandon that job
deliberately, `docker compose kill worker` first. Golden results are pinned to
SUMO 1.27.1; the image pins the same wheel.

**Job recovery.** Rows are `queued` / `running` / `done` / `failed` in the
store, and the RQ job id equals the row id. A job that dies without reporting
(work horse killed, worker SIGKILLed on a redeploy, Redis restarted) leaves a
row that says `running`; reconciliation repairs it at worker start, on RQ's
maintenance pass, and once at API start: `running` rows whose job is gone are
marked `failed` with the reason, `queued` rows whose job vanished are enqueued
again under the same id, and a `failed` row whose RQ job is in the failed
registry can be re-run with `rq requeue` against the same Redis
(`docker compose exec worker rq requeue --url redis://redis:6379/0 --queue
flowstate <row id>`), which starts it from a clean row. A sweep
re-run dispatches only the cells still `queued`; it never creates duplicate
child runs. A report re-run clears its staging tree first, so it neither
trips over the previous attempt's hard links nor folds that attempt's
leftovers into the new bundle.

Reconciliation confirms liveness with a fresh read of the started-job
registry before it fails a row, so a cell a worker picks up *while* another
worker's maintenance pass is running is left alone rather than marked "worker
died" mid-simulation.

**Reading failures.** `GET /runs/{id}`, `/sweeps/{id}`, `/reports/{id}` carry
`error` as an exception chain — one `Type: message` line per cause, with no
traceback frames, no server *source* paths, no file contents and no pydantic
input values; the worker log has the traceback. A path under the results root
(or one the caller itself supplied) can appear in a message, exactly as it
does in `report_path` and `data_path` on the success path: the path-confinement
diagnostic names the refused path and the roots it was checked against, which
is the whole value of that message. Calibration failures go further and
withhold any message raised outside FlowState's own packages, since a parse
error can quote the uploaded file; the common bad-upload shapes (empty,
header-only, non-numeric column) are diagnosed by the job itself and name the
column and file row, never the cell value.

**What an API report is.** `POST /reports` takes a `profile` naming the
acceptance-criteria thresholds to score against (`GET /api/v1/criteria` lists
them with their `source`: the FlowState default, the 2004 FHWA toolbox table,
ODOT's 2011 VISSIM protocol, TxDOT TSAP ch. 13), and the chosen profile is
recorded on the report row and printed in the bundle. Only the thresholds come
from the request. The measurements are computed from the staged run artifacts,
and the criteria whose evidence is observed field data the service does not
hold — `link_flows_geh` (observed link counts), `speeds_rmspe` (an observed
segment-speed field), `ring_emergence` / `ring_dampening` (the two benchmark
runs) and `sensitivity_grid` (the realized penetration × compliance cells) —
come back **not evaluated**; an unevaluated row counts as failing, by the rule
in `validation.criteria`. Numbers typed into a request body would be exactly
the free-text report values CLAUDE.md §7.4 forbids, so the API does not accept
them. Until those inputs can be supplied from observed data, an API report is
a run-set metrics bundle with the profile's thresholds stated — not a
signed-off calibration/validation acceptance deliverable. The flagship
corridor reports under `docs/reports/` are produced by scripts that compute
those inputs from observed data.

**Request ids.** Every response carries `X-Request-Id` — the client's own
value when it is at most 64 characters of `[A-Za-z0-9._-]`, otherwise a uuid4
— and an unhandled error answers `{"detail": "internal error; request id
<id>"}` with no traceback. Grep the api log for that id to find the request:
each one logs a line on the `api.access` logger at INFO, e.g.

```
INFO api.access POST /api/v1/runs 202 31.4ms request_id=2f1c…
```

**Logs.** `docker compose logs -f api worker`. The API logs one `api.access`
line per request (above) and an `api` traceback for anything that 500s. Those
go to stderr through a handler the app installs on the `api` logger at
startup, because `uvicorn` configures only its own loggers and would drop an
INFO record; a process that has configured logging itself keeps its own
setup. RQ logs each job's start and outcome at INFO, and reconciliation logs
every row it repaired at WARNING; SUMO's own warnings are suppressed, and
collisions are counted in every run's `meta.json` (`n_collisions`, with the
first events under `collisions`).

## 6. Without Docker

```sh
uv run --no-sync uvicorn api.main:app --port 8000
```

runs the API with the inline queue: jobs execute synchronously in the request
process, which is fine for a demo and wrong for a pilot (no concurrency, a
long sweep blocks the server). Use Compose for anything customer-facing.
