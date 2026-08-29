"""Microsim test config: parquet-IO shim for the libsumo/pyarrow arrow clash.

libsumo 1.27.1 bundles libarrow 24 while the workspace venv carries pyarrow 25
(libsumo itself warns about this at import). Once ``import libsumo`` has
loaded its bundled dylib, ANY construction of ``pyarrow.fs.LocalFileSystem``
in the same process — which pyarrow performs internally whenever a parquet
function receives a bare path — fails with ``ArrowKeyError: Attempted to
register factory for scheme 'file' ...``, and retrying never recovers.
``microsim.runner`` therefore writes its own artifacts through open file
objects (see ``runner._write_parquet``), which bypasses filesystem resolution
entirely and is unaffected.

Other packages' tests (e.g. ``tests/test_validation``) write parquet via
``DataFrame.to_parquet(path)``; they pass standalone but would fail when
pytest runs the whole ``tests/`` tree in one process and a microsim test has
already loaded libsumo. This conftest — imported at collection time, before
any libsumo import — wraps the three path-accepting ``pyarrow.parquet``
entry points so a ``str``/``Path`` destination is opened as a file object
first. Behavior is otherwise identical, and the shim is only active in test
sessions that collect ``tests/test_microsim``.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pyarrow.parquet as pq

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
