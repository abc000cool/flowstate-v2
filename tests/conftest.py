"""Shared test configuration.

Besides the headless matplotlib backend, this installs the parquet-IO shim
for the libsumo/pyarrow Arrow clash, session-wide.

libsumo 1.27.1's ``_libsumo.so`` links its own ``libarrow.2400`` while the
workspace venv carries pyarrow 25 with ``libarrow.2500`` (libsumo warns about
this at import). Once ``import libsumo`` has loaded its bundled dylib, ANY
construction of ``pyarrow.fs.LocalFileSystem`` in the same process — which
pyarrow performs internally whenever a parquet function receives a bare path —
fails with ``ArrowKeyError: Attempted to register factory for scheme 'file'
but that scheme is already registered``, whichever library loaded first, and
retrying never recovers. Product code therefore writes and reads its own
parquet through open file objects (``microsim.runner._write_parquet``,
``microsim.demand_adapter.read_trajectories``), which bypasses filesystem
resolution entirely.

Tests, however, write fixtures with ``DataFrame.to_parquet(path)``, and
libsumo is loaded in-process by tests in several directories: ``tests/
test_microsim``, ``tests/test_scripts/test_doctor.py`` (``run_checks`` imports
libsumo for its ``sumo_packages`` row; ``run_smoke`` runs the ring), and
``tests/test_integration``. This shim used to live in
``tests/test_microsim/conftest.py`` and so was installed only when that
directory was collected: the whole tree passed, each directory alone passed,
but ``pytest tests/test_scripts tests/test_validation`` failed with ~50
``ArrowKeyError`` occurrences once a doctor test had loaded libsumo. A root
conftest is imported at collection time before any test module, for every
selection under ``tests/``, so the shim now covers every session.

The shim wraps the three path-accepting ``pyarrow.parquet`` entry points so a
``str``/``Path`` destination is opened as a file object first (pandas goes
through the same module attributes). Behavior is otherwise identical.
"""

from __future__ import annotations

import functools
from pathlib import Path

import matplotlib
import pyarrow.parquet as pq

matplotlib.use("Agg")

_orig_write_table = pq.write_table
_orig_read_table = pq.read_table
_orig_read_schema = pq.read_schema


def _is_local_path(where: object) -> bool:
    return isinstance(where, str | Path)


@functools.wraps(_orig_write_table)
def _write_table(table, where, *args, **kwargs):
    if _is_local_path(where) and kwargs.get("filesystem") is None:
        kwargs.pop("filesystem", None)
        with open(where, "wb") as f:
            return _orig_write_table(table, f, *args, **kwargs)
    return _orig_write_table(table, where, *args, **kwargs)


@functools.wraps(_orig_read_table)
def _read_table(source, *args, **kwargs):
    if _is_local_path(source) and Path(source).is_file() and kwargs.get("filesystem") is None:
        kwargs.pop("filesystem", None)
        with open(source, "rb") as f:
            return _orig_read_table(f, *args, **kwargs)
    return _orig_read_table(source, *args, **kwargs)


@functools.wraps(_orig_read_schema)
def _read_schema(where, *args, **kwargs):
    if _is_local_path(where) and Path(where).is_file():
        with open(where, "rb") as f:
            return _orig_read_schema(f, *args, **kwargs)
    return _orig_read_schema(where, *args, **kwargs)


pq.write_table = _write_table
pq.read_table = _read_table
pq.read_schema = _read_schema
