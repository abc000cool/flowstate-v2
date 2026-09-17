"""Environment-driven service settings.

Every knob is an environment variable so the same image runs the API, the
worker, and the tests (ADR-3: thin service layer, Docker deploy):

- ``FLOWSTATE_RESULTS_DIR`` — results root (default ``./runs``). Run
  artifacts, uploads, calibration artifacts and reports live under it; the
  SQLite metadata database is ``<results>/metadata.db``.
- ``FLOWSTATE_API_KEY`` — the primary API key (default ``dev-key-change-me``;
  real auth is a Phase 4 concern, CLAUDE.md §8). The default is published in
  this repository, so :func:`check_api_key_not_default` refuses to build the
  app with it under the Redis (deployed) queue.
- ``FLOWSTATE_API_KEYS`` — optional comma-separated list of *additional*
  accepted keys (whitespace trimmed, empty entries dropped), so a key can be
  rotated without a window in which every client is locked out: add the new
  key, redeploy, move the clients, drop the old key. Every accepted key is in
  :attr:`Settings.api_keys`, and the default-key refusal above applies to all
  of them.
- ``FLOWSTATE_QUEUE`` — ``inline`` (synchronous, tests and small local runs)
  or ``redis`` (RQ; the production mode). Default ``inline``.
- ``FLOWSTATE_REDIS_URL`` — Redis URL for the RQ backend
  (default ``redis://localhost:6379/0``).
- ``FLOWSTATE_SCENARIOS_DIR`` — preset scenario YAML directory (default: the
  repo's ``scenarios/``).
- ``FLOWSTATE_DATA_DIR`` — optional extra root of server-side data files that
  ``POST /api/v1/calibrations/{kind}`` may read via ``data_path`` and that
  scenario configs may name in their file fields (unset ⇒ only the results
  root, which contains uploads, plus the repo's ``artifacts/`` and ``data/``
  directories for scenario configs — see :attr:`Settings.config_path_roots`).
- ``FLOWSTATE_MAX_BODY_MB`` — ceiling on a JSON/YAML request body (scenario
  configs, run/sweep/report requests); larger bodies are refused with HTTP
  413 (default 8 MB), from the declared ``Content-Length`` where there is one
  and otherwise from a running byte count as the body streams in
  (``api.main.BodyCapMiddleware``).
- ``FLOWSTATE_MAX_UPLOAD_MB`` — ceiling on one uploaded calibration data
  file (default 200 MB, HTTP 413 above it); the multipart request as a whole
  is capped at this plus ``FLOWSTATE_MAX_BODY_MB`` for its framing.
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
#: The same depth ``microsim.vehicles.resolve_calibration_path`` uses for its
#: repo-relative fallback, so a relative scenario path resolves to the file
#: the worker will read.
REPO_ROOT = Path(__file__).resolve().parents[3]
_REPO_ROOT = REPO_ROOT

#: Default ceilings for request bodies and uploads [MB]; see the module
#: docstring for the environment overrides.
DEFAULT_MAX_BODY_MB = 8
DEFAULT_MAX_UPLOAD_MB = 200
_MB = 1024 * 1024

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
    """The primary API key — ``FLOWSTATE_API_KEY`` when set, else the first
    ``FLOWSTATE_API_KEYS`` entry, else :data:`DEFAULT_API_KEY`."""
    queue_kind: str
    redis_url: str
    scenarios_dir: Path
    frontend_dist: Path
    data_dir: Path | None = None
    """Optional extra allow-listed root for calibration ``data_path`` reads
    and scenario config file fields."""
    max_body_bytes: int = DEFAULT_MAX_BODY_MB * _MB
    """Largest JSON/YAML request body accepted on ``/api/`` (HTTP 413 above)."""
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_MB * _MB
    """Largest calibration data upload accepted (HTTP 413 above)."""
    api_keys: tuple[str, ...] = ()
    """Every accepted ``X-API-Key`` value, :attr:`api_key` first.

    Defaults to ``(api_key,)`` so a hand-built :class:`Settings` still
    authenticates its own key; :func:`load_settings` fills it from
    ``FLOWSTATE_API_KEY`` plus ``FLOWSTATE_API_KEYS``.
    """

    def __post_init__(self) -> None:
        if not self.api_keys:
            object.__setattr__(self, "api_keys", (self.api_key,))

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

    @property
    def config_path_roots(self) -> tuple[Path, ...]:
        """Roots a scenario config's file fields may point inside (resolved).

        ``network.osm_file``, ``fleet.idm_calibration`` and
        ``fleet.heavy.idm_calibration`` name files on the *worker's*
        filesystem, so they are confined like a calibration ``data_path``:
        :attr:`data_roots` plus the repository's ``artifacts/`` and ``data/``
        directories, which the shipped ``scenarios/*.yaml`` presets reference
        by repo-relative path (``artifacts/idm_i24.json``,
        ``data/osm/i24_motion.osm``). Anything else is refused with HTTP 422.
        """
        return (
            *self.data_roots,
            (REPO_ROOT / "artifacts").resolve(),
            (REPO_ROOT / "data").resolve(),
        )


def check_api_key_not_default(settings: Settings) -> None:
    """Refuse the published default key on a deployed (Redis-queue) service.

    ``FLOWSTATE_QUEUE=redis`` means API and workers are separate processes —
    i.e. a real deployment, not a one-off local run — and
    :data:`DEFAULT_API_KEY` is printed in this repository's README, so leaving
    it in place is equivalent to no auth at all. Every key in
    :attr:`Settings.api_keys` is checked: a rotation list that still carries
    the default accepts the published key just as surely as a single one.

    Raises:
        InsecureDefaultKeyError: When the deployed service still accepts the
            default key.
    """
    if settings.queue_kind == "redis" and DEFAULT_API_KEY in settings.api_keys:
        raise InsecureDefaultKeyError(
            f"refusing to start: an accepted API key is still the published default "
            f"{DEFAULT_API_KEY!r} while FLOWSTATE_QUEUE=redis (a deployed service). "
            f"Set FLOWSTATE_API_KEY (or FLOWSTATE_API_KEYS) to secrets of your own "
            f"before starting the API."
        )


def load_settings() -> Settings:
    """Read settings from the environment (call-time, not import-time)."""
    queue_kind = os.environ.get("FLOWSTATE_QUEUE", "inline").strip().lower()
    if queue_kind not in QUEUE_KINDS:
        raise ValueError(f"FLOWSTATE_QUEUE must be one of {QUEUE_KINDS}, got {queue_kind!r}")
    raw_data_dir = os.environ.get("FLOWSTATE_DATA_DIR", "").strip()
    max_body_mb = _positive_int_env("FLOWSTATE_MAX_BODY_MB", DEFAULT_MAX_BODY_MB)
    max_upload_mb = _positive_int_env("FLOWSTATE_MAX_UPLOAD_MB", DEFAULT_MAX_UPLOAD_MB)
    api_keys = _api_keys_env()
    return Settings(
        results_dir=Path(os.environ.get("FLOWSTATE_RESULTS_DIR", "./runs")).resolve(),
        api_key=api_keys[0],
        api_keys=api_keys,
        queue_kind=queue_kind,
        redis_url=os.environ.get("FLOWSTATE_REDIS_URL", DEFAULT_REDIS_URL),
        scenarios_dir=Path(
            os.environ.get("FLOWSTATE_SCENARIOS_DIR", str(_REPO_ROOT / "scenarios"))
        ).resolve(),
        frontend_dist=Path(
            os.environ.get("FLOWSTATE_FRONTEND_DIST", str(_REPO_ROOT / "frontend" / "dist"))
        ).resolve(),
        data_dir=Path(raw_data_dir).resolve() if raw_data_dir else None,
        max_body_bytes=max_body_mb * _MB,
        max_upload_bytes=max_upload_mb * _MB,
    )


def _api_keys_env() -> tuple[str, ...]:
    """Every accepted API key from the environment, the primary one first.

    ``FLOWSTATE_API_KEY`` takes precedence — it stays the key the operator
    hands out and the one :attr:`Settings.api_key` reports — and
    ``FLOWSTATE_API_KEYS`` adds the rest of a rotation window
    (comma-separated, whitespace trimmed, empty entries dropped, duplicates
    collapsed). A blank or whitespace-only value counts as unset, so neither
    variable can silently make the empty string a valid key; with both unset
    the published :data:`DEFAULT_API_KEY` is the only key, which
    :func:`check_api_key_not_default` refuses on a deployed service.
    """
    candidates = [
        os.environ.get("FLOWSTATE_API_KEY", ""),
        *os.environ.get("FLOWSTATE_API_KEYS", "").split(","),
    ]
    keys: list[str] = []
    for raw in candidates:
        key = raw.strip()
        if key and key not in keys:
            keys.append(key)
    return tuple(keys) or (DEFAULT_API_KEY,)


def _positive_int_env(name: str, default: int) -> int:
    """A positive integer from the environment, ``default`` when unset.

    Raises:
        ValueError: When the variable is set but is not a positive integer.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}")
    return value
