# FlowState v2

**Calibrated corridor digital twins for studying — and dissipating — stop-and-go
("phantom") traffic waves with sparse controlled vehicles and variable speed
limits.**

FlowState v2 is a two-tier traffic simulation and analysis platform:

- **Microscopic tier (primary):** Eclipse SUMO (1.27.1) with the Intelligent
  Driver Model. String instability is *emergent* — stop-and-go waves grow out
  of calibrated car-following dynamics, not hand-seeded shocks — reproducing
  the Sugiyama et al. (2008) ring-road experiment and the Stern et al. (2018)
  single-AV dampening result.
- **Macroscopic tier (screening):** the v1 LWR/CTM Godunov engine, ported with
  a full test battery (Riemann exactness, conservation, CFL guards) and
  repurposed for what first-order models are actually good at: fast parameter
  screening and, later, real-time state estimation. It makes no claims about
  phantom-jam formation.

> **On v1:** FlowState v1 studied controlled dissipation of *seeded* shocks in
> a first-order (LWR) model — a model class that is string-stable by
> construction and cannot form phantom jams. v2 studies *emergent* stop-and-go
> waves in a calibrated microscopic model. v1 remains available at
> [abc000cool/FlowState](https://github.com/abc000cool/FlowState) as motivated
> preliminary work.

## Quickstart

One command (Docker + Compose):

```sh
docker compose up -d --build
```

Then open <http://localhost:8000> — the dashboard is served at `/`, the API
under `/api/v1/...`, OpenAPI docs at `/docs`, health at `/healthz`. The stack
is API + RQ worker + Redis; all simulations run on the worker, never in a
request handler.

**API key:** every `/api/...` route requires an `X-API-Key` header. The
default is `dev-key-change-me` — fine locally, and exactly as unsafe as it
sounds anywhere else. Set your own before exposing the port:

```sh
FLOWSTATE_API_KEY=your-secret docker compose up -d
```

Smoke test:

```sh
curl -s http://localhost:8000/healthz
curl -s -H "X-API-Key: dev-key-change-me" http://localhost:8000/api/v1/scenarios/preset
```

### Dev path (no Docker)

Needs [uv](https://docs.astral.sh/uv/) and Python 3.12; SUMO 1.27.1 installs
from PyPI wheels as part of the sync.

```sh
uv sync                                  # workspace + dev tools
uv run pytest tests -q -m "not slow"     # fast test battery
uv run uvicorn api.main:app --reload     # API on :8000, inline job queue
```

The inline queue (`FLOWSTATE_QUEUE=inline`, the default outside Docker) runs
jobs synchronously in-process — good for small local runs, not for real
sweeps. For the frontend against a local API: `cd frontend && npm install &&
npm run dev` (Vite on :5173, CORS pre-configured).

## Status

Under active construction. Milestone tracker:

- [x] **M0** — monorepo + macro tier ported with test battery, CI green
- [x] **M1** — ring-road emergence + single-AV dampening reproduced in CI
- [x] **M2** — FD + IDM population calibration from public data
- [x] **M3** — full 540-run sweep with CIs; US-101 validation executed end-to-end (criteria honestly mixed: see docs/M3_US101_VALIDATION.md)
- [x] **M4** — FastAPI service + dashboard + Docker
- [ ] **M5** — hardening, docs, versioned release

## Non-negotiables

1. No unvalidated claims — every headline number reproduces from a seeded run.
2. Emergent, not seeded — seeded experiments are always labeled `seeded=True`.
3. Standard metrics only: throughput, travel time, σ_v, fuel/energy, wave
   count/speed/amplitude.
4. No consumer nav-app advisory features — this is simulation and decision
   support.
5. Reproducibility: explicit seeds, config hashes, golden regression tests.
6. Honest uncertainty: headline metrics are mean ± 95% CI over ≥ 20 replicates.

## License

Apache-2.0. See [LICENSE](LICENSE).
