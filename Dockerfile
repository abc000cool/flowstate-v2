# syntax=docker/dockerfile:1
# FlowState v2 — one image for both the API (uvicorn) and the RQ worker
# (`python -m api.worker`, see docker-compose.yml). Stage 1 builds the
# Vite/React frontend; stage 2 installs the uv workspace and serves the API
# with the built frontend mounted at / (single-origin deploy — API routes
# live under /api/v1/... so statics and API never collide).

# --- Stage 1: frontend build ------------------------------------------------
FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/ ./
RUN npm ci && npm run build

# --- Stage 2: python runtime ------------------------------------------------
FROM python:3.12-bookworm AS runtime

# libsumo's native extension (and the sumo binaries) link against libGL even
# headless; python:3.12-bookworm doesn't ship it (verified via ldd — libGL.so.1
# is the only missing shared object).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
WORKDIR /app

# Workspace manifests + lock first (layer caching), then the package sources
# (installed editable from /app/packages by uv, so they must be present).
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY packages/ packages/
RUN uv sync --all-packages --no-dev --frozen

# Repo data the service reads at runtime: preset scenarios (GET
# /api/v1/scenarios/preset) and the contract docs.
COPY scenarios/ scenarios/
COPY docs/ docs/
COPY CLAUDE.md ./

# Built frontend, served at / by the API (api.settings: FLOWSTATE_FRONTEND_DIST
# defaults to /app/frontend/dist).
COPY --from=frontend /build/frontend/dist frontend/dist

# Results root (Parquet/JSON payloads + SQLite metadata) — mount a volume here.
# NUMBA_CACHE_DIR: numba's on-disk JIT cache needs a writable directory; the
# package source tree under /app is not writable at runtime.
ENV PATH="/app/.venv/bin:$PATH" \
    FLOWSTATE_RESULTS_DIR=/data/runs \
    NUMBA_CACHE_DIR=/tmp/numba
VOLUME /data/runs

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
