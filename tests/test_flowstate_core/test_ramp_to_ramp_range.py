"""The per-window form of ``WeaveSpec.ramp_to_ramp_share`` (``RampToRampRange``, Amendment 3, 2026-10-07).

docs/FRISCO_PROTOCOL.md, adoption of Amendment 3, item 1; docs/A3_RANGE_ROUND.md; docs/CONTRACTS.md, "Weave
ramp-to-ramp share". The form is a mapping under the same key as the single share, so:

* unset it is hash-neutral and absent from every dump: every committed scenario hashes as pinned under policy v4
  (tests/golden/scenario_config_hashes.json) and dumps as before, and the policy version is unchanged;
* set, the hash moves with ``u`` and with a non-default ``s_max``; an explicit default ``s_max`` hashes like an
  omitted one (policy v2's rule); ``model_dump`` (``meta.json["config"]``, YAML) states both and round-trips;
* it can never be set together with the single share (one key), and anything but ``{u, s_max}`` in range is refused.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from flowstate_core.config import (
    CONFIG_HASH_VERSION,
    RAMP_TO_RAMP_S_MAX_DEFAULT,
    RampToRampRange,
    ScenarioConfig,
    WeaveSpec,
    config_hash,
    config_hash_payload,
)

ROOT = Path(__file__).resolve().parents[2]
PINNED = json.loads((ROOT / "tests" / "golden" / "scenario_config_hashes.json").read_text())
TH52 = "on-ramp 769818012"
F2 = "scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2.yaml"


def _raw(rel: str = F2) -> dict:
    return ScenarioConfig.from_yaml(ROOT / rel).model_dump(mode="json")


def _with(raw: dict, share: object, name: str = TH52) -> ScenarioConfig:
    doc = json.loads(json.dumps(raw))
    next(r for r in doc["network"]["ramps"] if r.get("name") == name)["weave"][
        "ramp_to_ramp_share"
    ] = share
    return ScenarioConfig.model_validate(doc)


def test_policy_version_is_unchanged() -> None:
    assert CONFIG_HASH_VERSION == 4
    assert RAMP_TO_RAMP_S_MAX_DEFAULT == 0.70


def test_committed_weave_scenarios_hash_as_pinned_with_the_key_present_but_unset() -> None:
    n = 0
    for rel, pinned in PINNED["policy_v4"].items():
        cfg = ScenarioConfig.from_yaml(ROOT / rel)
        assert config_hash(cfg) == pinned, rel
        raw = cfg.model_dump(mode="json")
        weaves = [r for r in raw["network"].get("ramps") or [] if r.get("weave")]
        if not weaves:
            continue
        n += 1
        assert "ramp_to_ramp_share" not in json.dumps(raw), rel
        assert "ramp_to_ramp_share" not in json.dumps(config_hash_payload(cfg)), rel
        explicit = json.loads(json.dumps(raw))
        for r in explicit["network"]["ramps"]:
            if r.get("weave"):
                r["weave"]["ramp_to_ramp_share"] = None
        cfg2 = ScenarioConfig.model_validate(explicit)
        assert config_hash(cfg2) == pinned, rel
        assert cfg2.model_dump(mode="json") == raw, rel
    assert n >= 10


def test_set_it_moves_the_hash_and_states_both_values() -> None:
    raw = _raw()
    base = config_hash(ScenarioConfig.model_validate(raw))
    assert base == PINNED["policy_v4"][F2]
    hashes = {u: config_hash(_with(raw, {"u": u})) for u in (0.0, 0.5, 1.0)}
    assert len(set(hashes.values())) == 3 and base not in hashes.values()
    # an explicit default s_max hashes like an omitted one; another s_max moves the hash
    assert config_hash(_with(raw, {"u": 0.5, "s_max": 0.70})) == hashes[0.5]
    assert config_hash(_with(raw, {"u": 0.5, "s_max": 0.6})) != hashes[0.5]
    cfg = _with(raw, {"u": 0.5})
    th52 = next(r for r in cfg.model_dump(mode="json")["network"]["ramps"] if r["name"] == TH52)
    assert th52["weave"]["ramp_to_ramp_share"] == {"u": 0.5, "s_max": 0.7}
    payload = next(
        r for r in config_hash_payload(cfg)["config"]["network"]["ramps"] if r.get("name") == TH52
    )
    assert payload["weave"]["ramp_to_ramp_share"] == {"u": 0.5}
    # YAML and JSON round trips
    assert ScenarioConfig.model_validate(cfg.model_dump(mode="json")) == cfg
    assert (
        ScenarioConfig.model_validate(yaml.safe_load(yaml.safe_dump(cfg.model_dump(mode="json"))))
        == cfg
    )
    spec = next(r for r in cfg.network.ramps if r.name == TH52).weave
    assert spec is not None and isinstance(spec.ramp_to_ramp_share, RampToRampRange)


def test_the_single_share_is_unchanged_and_one_key_holds_one_form() -> None:
    raw = _raw()
    single = _with(raw, 0.5)
    spec = next(r for r in single.network.ramps if r.name == TH52).weave
    assert spec is not None and spec.ramp_to_ramp_share == 0.5
    assert isinstance(spec.ramp_to_ramp_share, float)
    assert config_hash(single) != config_hash(_with(raw, {"u": 0.5}))
    # an integer share is still the single share, as before
    assert WeaveSpec(exit_ramp="B", ramp_to_ramp_share=1).ramp_to_ramp_share == 1.0
    # the forms are one key: a mapping that also names a single share is refused, never merged
    for both in ({"u": 0.5, "share": 0.3}, {"u": 0.5, "ramp_to_ramp_share": 0.3}):
        with pytest.raises(ValidationError, match="ramp_to_ramp_share"):
            WeaveSpec(exit_ramp="B", ramp_to_ramp_share=both)
    schema = WeaveSpec.model_json_schema(mode="serialization")
    assert "ramp_to_ramp_share_u" not in schema["properties"]
    assert schema == WeaveSpec.model_json_schema(mode="validation")


@pytest.mark.parametrize(
    "bad",
    [
        {"u": -0.1},
        {"u": 1.01},
        {"u": 0.5, "s_max": 0.0},
        {"u": 0.5, "s_max": 1.2},
        {"s_max": 0.7},
        {"u": 0.5, "smax": 0.7},
        {},
    ],
)
def test_out_of_range_or_unknown_is_refused(bad: dict) -> None:
    with pytest.raises(ValidationError, match="ramp_to_ramp_share"):
        WeaveSpec(exit_ramp="B", ramp_to_ramp_share=bad)


def test_unset_weave_dumps_exactly_as_before() -> None:
    assert WeaveSpec(exit_ramp="B").model_dump(mode="json") == {
        "exit_ramp": "B",
        "length_m": None,
        "weave_params": {},
    }
    assert WeaveSpec(exit_ramp="B", ramp_to_ramp_share=None).model_dump_json() == (
        '{"exit_ramp":"B","length_m":null,"weave_params":{}}'
    )
    on = WeaveSpec(exit_ramp="B", ramp_to_ramp_share={"u": 1.0})
    assert '"ramp_to_ramp_share":{"u":1.0,"s_max":0.7}' in on.model_dump_json()
    assert WeaveSpec.model_validate(on.model_dump()) == on
