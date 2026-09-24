# Hosted tester (Cloud Run) — 2026-09-24

A single hosted instance of the API + dashboard for outside testers, so a
first run needs no install. It is a tester convenience, not a product tier:
one request at a time per instance, two instances at most, and a simulation
runs inside the request (the inline queue), so anything longer than the
60-minute request timeout fails — the ring benchmark and short corridor
smokes are what it is for.

**Deploy / redeploy:** `FLOWSTATE_API_KEY=<key> scripts/gcp/deploy_cloud_run.sh`
(builds the image with Cloud Build from a clean export of `HEAD`; the key is
never committed — it lives only in the service's environment and with the
owner). First deployed 2026-09-23 23:28 CDT (revision 00001); revision 00002
at 23:39 CDT added the `/health` alias (below); revision 00003 (2026-09-24 00:34 CDT) carried the split-audit and merge-diagnostics panels, 00004 (01:29 CDT) the onboarding opt-outs, 00005 (10:03 CDT) the block-3 dashboard changes, 00006 (15:44 CDT) the demand-integrity panel and the weave counters — each deployed with `scripts/gcp/deploy_cloud_run.sh` from the pushed `HEAD`.

**Service (project `project-357fa2a7-490c-4a4b-a71`, `us-west1`):**

| setting | value |
|---|---|
| service / URL | `flowstate-tester`, `https://flowstate-tester-826556907769.us-west1.run.app` |
| auth | public invoker; every `/api/…` route needs `X-API-Key` (the owner hands the key to testers) |
| resources | 2 vCPU, 4 GiB, startup CPU boost, gen2 |
| concurrency | 1 request per instance, max 2 instances, scale to zero |
| request timeout | 3,600 s |
| queue | `FLOWSTATE_QUEUE=inline` (no Redis; a run executes in the request) |
| results | Cloud Storage bucket `flowstate-tester-results` mounted at `/mnt/results` (`FLOWSTATE_RESULTS_DIR`) |
| uploads | `FLOWSTATE_MAX_UPLOAD_MB=64` |

**Verified 2026-09-23 23:42 CDT:** `/health` → `{"status":"ok","store":"ok","queue":"ok","queue_kind":"inline"}`;
`POST /api/v1/scenarios` with `scenarios/ring_sugiyama.yaml` → a scenario id;
`POST /api/v1/runs` with two replicates → `status: done` in the same request;
`GET /api/v1/runs/{id}/metrics` returns the two seeds' metrics (marked
`underpowered`, as any run under 20 seeds is).

**`/healthz` on Cloud Run:** Google's front end answers `/healthz` on a
`run.app` URL with its own 404 before the container sees the request (observed
on both revisions, every variant of the path). The server therefore also
serves the probe at `/health`, and the dashboard polls that path
(`frontend/src/api/client.ts`); local deployments keep `/healthz` too.

**Cost guard:** scale-to-zero, two instances, and a 64 MB upload cap; there is
no per-user quota — the API key is the only gate, so rotate it
(`deploy_cloud_run.sh` with a new key) if it leaks. The results bucket grows
with every run; empty it when the tester period ends.

**Not hosted:** sweeps of twenty seeds on a corridor (hours of CPU; the
cloud VM pipeline `scripts/gcp/launch_i24_pipeline.sh` is for that), Redis
queues, per-user accounts.
