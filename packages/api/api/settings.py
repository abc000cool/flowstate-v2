"""Environment-driven service settings.

Every knob is an environment variable so the same image runs the API, the
worker, and the tests (ADR-3: thin service layer, Docker deploy):

- ``FLOWSTATE_RESULTS_DIR`` — results root (default ``./runs``). Run
  artifacts, uploads, calibration artifacts and reports live under it; the
  SQLite metadata database is ``<results>/metadata.db``.
- ``FLOWSTATE_API_KEY`` — the single API key (default ``dev-key-change-me``;
  real auth is a Phase 4 concern, CLAUDE.md §8). The default is published in
  this repository, so :func:`check_api_key_not_default` refuses to build the
  app with it under the Redis (deployed) queue.
- ``FLOWSTATE_QUEUE`` — ``inline`` (synchronous, tests and small local runs)
  or ``redis`` (RQ; the production mode). Default ``inline``.
- ``FLOWSTATE_REDIS_URL`` — Redis URL for the RQ backend
  (default ``redis://localhost:6379/0``).
- ``FLOWSTATE_SCENARIOS_DIR`` — preset scenario YAML directory (default: the
  repo's ``scenarios/``).
- ``FLOWSTATE_DATA_DIR`` — optional extra root of server-side data files that
  ``POST /api/v1/calibrations/{kind}`` may read via ``data_path`` (unset ⇒ only
  the results root, which contains uploads, is readable).
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


class InsecureDefaultKeyError(RuntimeError):
    """Raised when a deployed service would boot on the published dev key."""


@dataclass(frozen=True)
class Settings:
    """Resolved service configuration."""

    results_dir: Path
    api_key: str
    queue_kind: str
    redis_url: str
    scenarios_dir: Path
    frontend_dist: Path
    data_dir: Path | None = None
    """Optional extra allow-listed root for calibration ``data_path`` reads."""

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

    @property
    def data_roots(self) -> tuple[Path, ...]:
        """Roots a calibration ``data_path`` may point inside (resolved).

        Uploads land under the results root, so both are listed explicitly
        even though the first contains the second; ``FLOWSTATE_DATA_DIR``
        adds the operator's own mounted dataset directory when set. Anything
        outside these roots is refused with HTTP 422 — a ``data_path`` is a
        server-side path, and without an allow-list it reads any file the
        worker can see.
        """
        roots = [self.uploads_dir.resolve(), self.results_dir.resolve()]
        if self.data_dir is not None:
            roots.append(self.data_dir.resolve())
        return tuple(roots)


def check_api_key_not_default(settings: Settings) -> None:
    """Refuse the published default key on a deployed (Redis-queue) service.

    ``FLOWSTATE_QUEUE=redis`` means API and workers are separate processes —
    i.e. a real deployment, not a one-off local run — and
    :data:`DEFAULT_API_KEY` is printed in this repository's README, so leaving
    it in place is equivalent to no auth at all.

    Raises:
        InsecureDefaultKeyError: When the deployed service still holds the
            default key.
    """
    if settings.queue_kind == "redis" and settings.api_key == DEFAULT_API_KEY:
        raise InsecureDefaultKeyError(
            f"refusing to start: FLOWSTATE_API_KEY is still the published default "
            f"{DEFAULT_API_KEY!r} while FLOWSTATE_QUEUE=redis (a deployed service). "
            f"Set FLOWSTATE_API_KEY to a secret of your own before starting the API."
        )


def load_settings() -> Settings:
    """Read settings from the environment (call-time, not import-time)."""
    queue_kind = os.environ.get("FLOWSTATE_QUEUE", "inline").strip().lower()
    if queue_kind not in QUEUE_KINDS:
        raise ValueError(f"FLOWSTATE_QUEUE must be one of {QUEUE_KINDS}, got {queue_kind!r}")
    raw_data_dir = os.environ.get("FLOWSTATE_DATA_DIR", "").strip()
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
        data_dir=Path(raw_data_dir).resolve() if raw_data_dir else None,
    )
