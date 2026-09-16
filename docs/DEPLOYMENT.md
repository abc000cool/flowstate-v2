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
| `FLOWSTATE_API_KEY` | `dev-key-change-me` (refused under the Redis queue) | the one shared key; every `/api/...` route needs it in `X-API-Key` |
| `FLOWSTATE_QUEUE` | `inline` outside Compose, `redis` inside | `redis` = jobs run on the worker; `inline` = synchronous, for tests and small local runs only |
| `FLOWSTATE_REDIS_URL` | `redis://redis:6379/0` | queue location |
| `FLOWSTATE_RESULTS_DIR` | `./runs` (Compose: `/data/runs`) | results root: runs, uploads, calibrations, reports, `metadata.db` |
| `FLOWSTATE_SCENARIOS_DIR` | the repository's `scenarios/` | presets served by `GET /scenarios/preset` |
| `FLOWSTATE_DATA_DIR` | unset | an extra root from which scenario configs may reference OSM files and calibration artifacts |
| `FLOWSTATE_FRONTEND_DIST` | the built dashboard in the image | static files served at `/` |
| `FLOWSTATE_MAX_BODY_MB` | 8 | request-body cap on `/api/` (HTTP 413) |
| `FLOWSTATE_MAX_UPLOAD_MB` | 200 | calibration upload cap (HTTP 413) |

Scenario configs may only reference files under the results root, the uploads
directory, `FLOWSTATE_DATA_DIR`, or the repository's `artifacts/` and `data/`;
anything else is refused with 422 (`path_outside_roots`). Put customer OSM
extracts and calibration artifacts under `FLOWSTATE_DATA_DIR`.

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

**Key rotation.** Change `FLOWSTATE_API_KEY` in `.env`, `docker compose up -d`
(api restarts), then paste the new key into every dashboard's Settings drawer.

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
child runs.

**Reading failures.** `GET /runs/{id}`, `/sweeps/{id}`, `/reports/{id}` carry
`error` as an exception chain (type and message per link, no frames, no
server paths); the worker log has the traceback.

**Logs.** `docker compose logs -f api worker`. RQ logs each job's start and
outcome at INFO, and reconciliation logs every row it repaired at WARNING;
SUMO's own warnings are suppressed, and collisions are counted in every run's
`meta.json` (`n_collisions`, with the first events under `collisions`).

## 6. Without Docker

```sh
uv run --no-sync uvicorn api.main:app --port 8000
```

runs the API with the inline queue: jobs execute synchronously in the request
process, which is fine for a demo and wrong for a pilot (no concurrency, a
long sweep blocks the server). Use Compose for anything customer-facing.
