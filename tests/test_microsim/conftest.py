"""The E12 platform recorder, wired for every test of ``tests/test_microsim``.

Off unless ``FLOWSTATE_RECORD_STATE`` names a file
(``tests/test_microsim/_platform_record.py`` has the record's form and why no
test file calls it; docs/E12_PLATFORM_TESTS.md has the runs). The parquet-IO
shim that once lived here is in ``tests/conftest.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.test_microsim._platform_record import TestRecord, recording

_CALL_REPORT = pytest.StashKey[Any]()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    """Keep each test's call-phase report on the item, for the recorder's outcome."""
    report = yield
    if report.when == "call":
        item.stash[_CALL_REPORT] = report
    return report


@pytest.fixture(autouse=True)
def _platform_state_record(request: pytest.FixtureRequest) -> Iterator[TestRecord | None]:
    """Record the test's runs, states and outcome when ``FLOWSTATE_RECORD_STATE`` is set."""
    module = getattr(request.node, "module", None)
    with recording(
        request.node.nodeid, module, lambda: request.node.stash.get(_CALL_REPORT, None)
    ) as rec:
        yield rec
