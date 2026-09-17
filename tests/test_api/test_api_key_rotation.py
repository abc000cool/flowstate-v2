"""API-key rotation: ``FLOWSTATE_API_KEYS`` beside ``FLOWSTATE_API_KEY``.

With one key there is no way to replace it without a window in which either
the old key still works (it was never removed) or every client is locked out
(it was). The service therefore accepts every key in ``Settings.api_keys``:
the primary ``FLOWSTATE_API_KEY`` plus the comma-separated
``FLOWSTATE_API_KEYS`` list, so an operator adds the new key, redeploys,
moves the clients, then drops the old one (docs/DEPLOYMENT.md §5).

The published default key is refused under the Redis queue wherever it
appears in that list, not just as the primary key.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.settings import DEFAULT_API_KEY, InsecureDefaultKeyError, load_settings

OLD_KEY = "old-key-0000"
NEW_KEY = "new-key-1111"


@pytest.fixture(autouse=True)
def _clean_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither key variable is inherited from the developer's environment."""
    monkeypatch.delenv("FLOWSTATE_API_KEY", raising=False)
    monkeypatch.delenv("FLOWSTATE_API_KEYS", raising=False)


@contextmanager
def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> Iterator[TestClient]:
    """A client over a fresh app built with ``env`` set (inline queue)."""
    monkeypatch.setenv("FLOWSTATE_QUEUE", "inline")
    monkeypatch.setenv("FLOWSTATE_RESULTS_DIR", str(tmp_path / "results"))
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    from api.main import create_app

    with TestClient(create_app()) as c:
        yield c


def _status(client: TestClient, key: str) -> int:
    return client.get("/api/v1/scenarios", headers={"X-API-Key": key}).status_code


# ---------------------------------------------------------------------------
# Settings parsing
# ---------------------------------------------------------------------------


def test_single_key_variable_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWSTATE_API_KEY", OLD_KEY)
    settings = load_settings()
    assert settings.api_key == OLD_KEY
    assert settings.api_keys == (OLD_KEY,)


def test_list_variable_alone_sets_every_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWSTATE_API_KEYS", f"{OLD_KEY},{NEW_KEY}")
    settings = load_settings()
    assert settings.api_keys == (OLD_KEY, NEW_KEY)
    assert settings.api_key == OLD_KEY  # the first listed key is the primary


def test_primary_key_takes_precedence_and_the_list_adds_to_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FLOWSTATE_API_KEY", NEW_KEY)
    monkeypatch.setenv("FLOWSTATE_API_KEYS", f"{OLD_KEY},{NEW_KEY}")
    settings = load_settings()
    assert settings.api_key == NEW_KEY
    assert settings.api_keys == (NEW_KEY, OLD_KEY)  # primary first, duplicate collapsed


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (f"  {OLD_KEY} , {NEW_KEY}  ", (OLD_KEY, NEW_KEY)),
        (f"{OLD_KEY},,{NEW_KEY},", (OLD_KEY, NEW_KEY)),
        (f"\t{OLD_KEY}\n", (OLD_KEY,)),
        (" , ", (DEFAULT_API_KEY,)),  # nothing usable ⇒ the default key alone
    ],
)
def test_list_entries_are_trimmed_and_empties_dropped(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: tuple[str, ...]
) -> None:
    monkeypatch.setenv("FLOWSTATE_API_KEYS", value)
    assert load_settings().api_keys == expected


def test_blank_primary_key_is_not_a_usable_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty FLOWSTATE_API_KEY must not authenticate an empty header."""
    monkeypatch.setenv("FLOWSTATE_API_KEY", "   ")
    monkeypatch.setenv("FLOWSTATE_API_KEYS", NEW_KEY)
    assert load_settings().api_keys == (NEW_KEY,)


def test_no_key_variable_is_still_the_default_key(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings()
    assert settings.api_key == DEFAULT_API_KEY
    assert settings.api_keys == (DEFAULT_API_KEY,)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def test_every_listed_key_authenticates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The rotation window: old and new key both work, nothing else does."""
    with _client(tmp_path, monkeypatch, FLOWSTATE_API_KEYS=f"{OLD_KEY},{NEW_KEY}") as client:
        assert _status(client, OLD_KEY) == 200
        assert _status(client, NEW_KEY) == 200
        assert _status(client, "not-a-key") == 401
        assert _status(client, "") == 401
        assert client.get("/api/v1/scenarios").status_code == 401


def test_primary_and_list_keys_both_authenticate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client(
        tmp_path, monkeypatch, FLOWSTATE_API_KEY=OLD_KEY, FLOWSTATE_API_KEYS=NEW_KEY
    ) as client:
        assert client.app.state.settings.api_keys == (OLD_KEY, NEW_KEY)  # type: ignore[attr-defined]
        assert _status(client, OLD_KEY) == 200
        assert _status(client, NEW_KEY) == 200
        assert _status(client, f"{OLD_KEY},{NEW_KEY}") == 401  # the list is not itself a key


def test_a_removed_key_stops_working(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End of the rotation: the old key is dropped and is refused at once."""
    with _client(tmp_path, monkeypatch, FLOWSTATE_API_KEYS=NEW_KEY) as client:
        assert _status(client, NEW_KEY) == 200
        assert _status(client, OLD_KEY) == 401


# ---------------------------------------------------------------------------
# Default-key refusal
# ---------------------------------------------------------------------------


def test_default_key_in_the_rotation_list_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hiding the published key behind a real one is still the published key."""
    monkeypatch.setenv("FLOWSTATE_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("FLOWSTATE_QUEUE", "redis")
    monkeypatch.setenv("FLOWSTATE_API_KEY", NEW_KEY)
    monkeypatch.setenv("FLOWSTATE_API_KEYS", f"{OLD_KEY},{DEFAULT_API_KEY}")
    from api.main import create_app

    with pytest.raises(InsecureDefaultKeyError) as excinfo:
        create_app()
    message = str(excinfo.value)
    assert DEFAULT_API_KEY in message
    assert "FLOWSTATE_API_KEY" in message


def test_two_operator_keys_start_under_the_redis_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal is about the default key, not about having a list."""
    monkeypatch.setenv("FLOWSTATE_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("FLOWSTATE_QUEUE", "redis")
    monkeypatch.setenv("FLOWSTATE_API_KEYS", f"{OLD_KEY},{NEW_KEY}")
    from api.main import create_app

    app = create_app()
    assert app.state.settings.api_keys == (OLD_KEY, NEW_KEY)
