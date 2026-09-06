"""Config-hash policy v2 (docs/CONTRACTS.md §2): defaults excluded, versioned.

* A new optional field leaves the hash of every scenario that does not use
  it unchanged (explicit defaults hash like omitted ones).
* ``tests/golden/config_defaults.json`` pins the full default dump of a
  canonical config: a field default that drifts changes the physics of
  every scenario relying on it, so the snapshot must be regenerated
  together with a bump of ``CONFIG_HASH_VERSION`` and a CHANGELOG note.
* One hash is pinned outright so a policy change is visible.

Regenerate the snapshot (after bumping the version) with::

    uv run --no-sync python tests/test_flowstate_core/test_config_hash.py --regenerate
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from flowstate_core.config import (
    CONFIG_HASH_VERSION,
    ScenarioConfig,
    config_hash,
    config_hash_payload,
)

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "config_defaults.json"
PINNED_RING_HASH = "a226444c0145"  # scenarios/ring_sugiyama.yaml under policy v2


def _canonical() -> ScenarioConfig:
    return ScenarioConfig.model_validate(
        {
            "name": "canonical_defaults",
            "network": {"kind": "corridor", "length_m": 1000.0, "lanes": 2, "inflow": [[0.0, 0.3]]},
            "sim": {"duration_s": 60.0},
        }
    )


def _snapshot() -> dict:
    return {
        "hash_version": CONFIG_HASH_VERSION,
        "full_dump": _canonical().model_dump(mode="json"),
    }


def test_explicit_defaults_hash_like_omitted_ones():
    base = _canonical()
    explicit = base.model_copy(
        update={
            "closures": [],
            "perturbation": None,
            "fleet": base.fleet.model_copy(update={"heavy": None, "lc_strategic_ramp": None}),
        }
    )
    assert config_hash(explicit) == config_hash(base)
    payload = config_hash_payload(base)
    assert payload["hash_version"] == CONFIG_HASH_VERSION
    assert "closures" not in payload["config"]
    assert "heavy" not in payload["config"].get("fleet", {})
    assert payload["config"]["network"]["kind"] == "corridor"


def test_non_default_values_change_the_hash():
    base = _canonical()
    assert config_hash(base.model_copy(update={"seed": 43})) != config_hash(base)
    assert config_hash(base.model_copy(update={"tier": "macro"})) != config_hash(base)


def test_pinned_ring_hash():
    root = Path(__file__).resolve().parents[2]
    cfg = ScenarioConfig.from_yaml(root / "scenarios" / "ring_sugiyama.yaml")
    assert config_hash(cfg) == PINNED_RING_HASH, (
        "the hash policy or a ring default moved; bump CONFIG_HASH_VERSION, regenerate the "
        "goldens and this pin with a CHANGELOG note"
    )


def _existing_defaults_unchanged(golden: object, current: object, path: str = "") -> list[str]:
    """Key paths present in the golden whose current value differs (new keys
    are allowed: a new optional field is not a default change)."""
    diffs: list[str] = []
    if isinstance(golden, dict) and isinstance(current, dict):
        for key, val in golden.items():
            if key not in current:
                diffs.append(f"{path}/{key} (removed)")
            else:
                diffs += _existing_defaults_unchanged(val, current[key], f"{path}/{key}")
        return diffs
    if golden != current:
        diffs.append(f"{path}: {golden!r} -> {current!r}")
    return diffs


def test_defaults_snapshot_is_pinned():
    golden = json.loads(GOLDEN.read_text())
    current = _snapshot()
    diffs = _existing_defaults_unchanged(golden["full_dump"], current["full_dump"])
    assert not diffs, (
        "a field default changed: every scenario relying on it now runs different physics under "
        "an unchanged hash — bump CONFIG_HASH_VERSION, regenerate tests/golden/config_defaults.json "
        f"(--regenerate) and note it in the CHANGELOG: {diffs}"
    )
    assert current["hash_version"] == golden["hash_version"]


if __name__ == "__main__":
    if "--regenerate" in sys.argv:
        GOLDEN.write_text(json.dumps(_snapshot(), indent=2, sort_keys=True))
        print(f"wrote {GOLDEN}")
    else:
        print(__doc__)
