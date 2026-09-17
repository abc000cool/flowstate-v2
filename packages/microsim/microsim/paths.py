"""Worker-side confinement of the filesystem paths a scenario config names.

``network.osm_file``, ``fleet.idm_calibration`` and
``fleet.heavy.idm_calibration`` are paths the process that *runs* the
simulation opens, so on a hosted service they are confined to an allow-list of
directories. The API is the first line of that defence: it refuses an
out-of-root path at ``POST /scenarios``, ``/runs`` and every ``/sweeps`` cell
with HTTP 422 (``api.main._confine_config_paths`` over
``api.settings.Settings.config_path_roots``). This module is the second: the
check the reader itself performs, so a config that reaches the worker by some
other route — an edited store row, a direct :func:`api.jobs.run_scenario_job`
call, a future endpoint that forgets the validator — still cannot make it read
``/etc/passwd``.

Two ways to restrict a reader:

* pass ``allowed_roots=`` to :func:`microsim.vehicles.resolve_calibration_path`,
  :func:`microsim.vehicles.load_idm_calibration` or
  :func:`microsim.networks.osm_import`;
* set :data:`ROOTS_ENV_VAR` — ``os.pathsep``-separated directories — in the
  environment, which every one of those functions consults when its argument
  is ``None``. This is how ``api.jobs.run_scenario_job`` restricts a run: the
  micro tier fans replicates out into a *spawn* process pool
  (:func:`microsim.runner.run_replicates`), and the environment is the one
  channel that reaches those children without threading an argument through
  every call in between.

Unrestricted is the library default: ``allowed_roots=None`` with the variable
unset means no check at all, which is what this repository's scripts and the
ad-hoc `uv run` analyses want. An explicitly empty ``allowed_roots=()``
refuses everything.

Containment is decided on the *resolved* path (``Path.resolve``: symlinks
followed, ``..`` collapsed, relative paths taken against the process's working
directory), so neither ``../..`` nor a symlink planted inside a root escapes
it. The refusal message names only the configured value and the roots — never
anything read from the file, and never the resolved target.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Final

#: Environment variable holding the ``os.pathsep``-separated roots every
#: reader in this package falls back to. Unset or empty ⇒ unrestricted.
#: ``api.jobs`` repeats this literal (it must not import SUMO to set it).
ROOTS_ENV_VAR: Final[str] = "FLOWSTATE_WORKER_PATH_ROOTS"


def env_path_roots() -> tuple[Path, ...] | None:
    """Resolved roots from :data:`ROOTS_ENV_VAR`, ``None`` when unset/empty."""
    raw = os.environ.get(ROOTS_ENV_VAR, "")
    parts = [p for p in raw.split(os.pathsep) if p.strip()]
    if not parts:
        return None
    return tuple(Path(p).resolve() for p in parts)


def effective_roots(allowed_roots: Iterable[str | Path] | None) -> tuple[Path, ...] | None:
    """The roots a reader must honour: the argument, else the environment.

    Args:
        allowed_roots: Caller-supplied directories, or ``None`` to fall back
            to :func:`env_path_roots`. An empty iterable is *not* ``None``:
            it allows nothing.

    Returns:
        Resolved roots, or ``None`` for "no confinement configured".
    """
    if allowed_roots is None:
        return env_path_roots()
    return tuple(Path(r).resolve() for r in allowed_roots)


def within_roots(resolved: Path, roots: tuple[Path, ...] | None) -> bool:
    """Whether an already-resolved path lies inside one of ``roots``."""
    if roots is None:
        return True
    return any(resolved.is_relative_to(root) for root in roots)


def outside_roots_error(
    value: str | os.PathLike[str], roots: tuple[Path, ...], *, field: str
) -> ValueError:
    """The refusal for a path outside ``roots`` (sanitized, ready to raise).

    Mirrors the API's 422 ``path_outside_roots`` wording. Carries the
    configured value and the allowed roots and nothing else: the file's bytes
    and the resolved target stay out of a string that a run's ``error`` field
    hands back to any API-key holder.
    """
    return ValueError(
        f"{field} {str(value)!r} is outside the allowed data roots {[str(r) for r in roots]}"
    )


def ensure_within_roots(
    value: str | os.PathLike[str],
    resolved: Path,
    roots: tuple[Path, ...] | None,
    *,
    field: str,
) -> None:
    """Raise :func:`outside_roots_error` unless ``resolved`` is inside ``roots``.

    Args:
        value: The configured path, quoted verbatim in the message.
        resolved: ``value`` after ``Path.resolve`` — what will actually be
            opened.
        roots: Result of :func:`effective_roots`; ``None`` checks nothing.
        field: Config field name for the message (e.g. ``network.osm_file``).

    Raises:
        ValueError: ``resolved`` is outside every root.
    """
    if within_roots(resolved, roots):
        return
    assert roots is not None  # within_roots is True for None
    raise outside_roots_error(value, roots, field=field)
