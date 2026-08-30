"""Environment-driven service settings.

Every knob is an environment variable so the same image runs the API, the
worker, and the tests (ADR-3: thin service layer, Docker deploy):

- ``FLOWSTATE_RESULTS_DIR`` — results root (default ``./runs``). Run
  artifacts, uploads, calibration artifacts and reports live under it; the
  SQLite metadata database is ``<results>/metadata.db``.
- ``FLOWSTATE_API_KEY`` — the single API key (default ``dev-key-change-me``;
  real auth is a Phase 4 concern, CLAUDE.md §8).
- ``FLOWSTATE_QUEUE`` — ``inline`` (synchronous, tests and small local runs)
  or ``redis`` (RQ; the production mode). Default ``inline``.
- ``FLOWSTATE_REDIS_URL`` — Redis URL for the RQ backend
  (default ``redis://localhost:6379/0``).
- ``FLOWSTATE_SCENARIOS_DIR`` — preset scenario YAML directory (default: the
  repo's ``scenarios/``).
- ``FLOWSTATE_FRONTEND_DIST`` — built frontend directory served at ``/`` when
  it exists (default: the repo's ``frontend/dist``).

Settings are read at :func:`load_settings` call time (never at import time)
so tests can point the service at temporary directories via ``monkeypatch``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Repo root when running from the source tree (packages/api/api/ -> root).
_REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_API_KEY = "dev-key-change-me"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
QUEUE_KINDS = ("inline", "redis")


@dataclass(frozen=True)
class Settings:
    """Resolved service configuration."""

    results_dir: Path
    api_key: str
    queue_kind: str
    redis_url: str
    scenarios_dir: Path
    frontend_dist: Path

    @property
    def db_path(self) -> Path:
        """SQLite metadata database (metadata only — payloads stay on disk)."""
        return self.results_dir / "metadata.db"

    @property
    def runs_dir(self) -> Path:
        """Per-run artifact roots: ``<runs_dir>/<run_id>/<config_hash>/<seed>/``."""
        return self.results_dir / "runs"

    @property
    def uploads_dir(self) -> Path:
        """Uploaded calibration data files."""
        return self.results_dir / "uploads"

    @property
    def calibrations_dir(self) -> Path:
        """Saved calibration artifacts (JSON, docs/CONTRACTS.md §5)."""
        return self.results_dir / "calibrations"

    @property
    def reports_dir(self) -> Path:
        """Generated report bundles (markdown + figures)."""
        return self.results_dir / "reports"


def load_settings() -> Settings:
    """Read settings from the environment (call-time, not import-time)."""
    queue_kind = os.environ.get("FLOWSTATE_QUEUE", "inline").strip().lower()
    if queue_kind not in QUEUE_KINDS:
        raise ValueError(f"FLOWSTATE_QUEUE must be one of {QUEUE_KINDS}, got {queue_kind!r}")
    return Settings(
        results_dir=Path(os.environ.get("FLOWSTATE_RESULTS_DIR", "./runs")).resolve(),
        api_key=os.environ.get("FLOWSTATE_API_KEY", DEFAULT_API_KEY),
        queue_kind=queue_kind,
        redis_url=os.environ.get("FLOWSTATE_REDIS_URL", DEFAULT_REDIS_URL),
        scenarios_dir=Path(
            os.environ.get("FLOWSTATE_SCENARIOS_DIR", str(_REPO_ROOT / "scenarios"))
        ).resolve(),
        frontend_dist=Path(
            os.environ.get("FLOWSTATE_FRONTEND_DIST", str(_REPO_ROOT / "frontend" / "dist"))
        ).resolve(),
    )
