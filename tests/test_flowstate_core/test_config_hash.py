"""Config-hash policy (docs/CONTRACTS.md §2): defaults excluded, versioned.

* A new optional field leaves the hash of every scenario that does not use
  it unchanged (explicit defaults hash like omitted ones).
* ``tests/golden/config_defaults.json`` pins the full default dump of a
  canonical config, and the module-level defaults of the two dict-valued
  merge parameter blocks (``SCRIPTED_MERGE_DEFAULTS``, ``WEAVE_DEFAULTS``),
  which a model dump does not show (``merge_params`` defaults to ``{}``): a
  default that drifts changes the physics of every scenario relying on it,
  so the snapshot must be regenerated together with a bump of
  ``CONFIG_HASH_VERSION`` and a CHANGELOG note.
* One hash is pinned outright so a policy change is visible.

Version 3 (2026-10-04, WP-98): ``AVSpec.emergency_handback``,
``release_off_corridor``, ``observe_close_leader`` and
``SCRIPTED_MERGE_DEFAULTS["force_guard"]`` turned on.

2026-10-06, regenerated without a bump: the dead merge switches were removed
(docs/MERGE_MODEL.md, amendment A4) — keys left the two merge tables, no
remaining default changed value, and a config that sets a removed key is
refused (``flowstate_core.config.REMOVED_WEAVE_KEYS``), so no hash can name
two physics. The regeneration also pinned ``fleet.speed_factor`` 1.0 and
``fleet.speed_dev`` 0.0 (WP-109), which the previous snapshot predated.

Regenerate the snapshot (after bumping the version) with::

    uv run --no-sync python tests/test_flowstate_core/test_config_hash.py --regenerate
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from flowstate_core.config import (
    CONFIG_HASH_VERSION,
    FLEET_SETTINGS_FIELDS,
    SCRIPTED_MERGE_DEFAULTS,
    WEAVE_DEFAULTS,
    FleetSpec,
    HeavyVehicleSpec,
    MacroOptions,
    ScenarioConfig,
    config_hash,
    config_hash_payload,
    config_hash_v2,
    fleet_non_defaults,
    fleet_settings,
)

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "config_defaults.json"
# scenarios/ring_sugiyama.yaml under policy v3 (2026-10-04)
PINNED_RING_HASH = "d5472987265c"
# the same file under policy v2 (2026-09-06 to 2026-10-03), as records of then quote it
PINNED_RING_HASH_V2 = "a226444c0145"


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
        # dict-valued parameter blocks whose defaults live in module constants
        # (since policy v3): a model dump shows ``merge_params: {}`` whatever
        # SCRIPTED_MERGE_DEFAULTS holds
        "merge_defaults": {
            "scripted": dict(SCRIPTED_MERGE_DEFAULTS),
            "weave": dict(WEAVE_DEFAULTS),
        },
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


HEAVY = {
    "fraction": 0.2,
    "length_m": 20.5,
    "emission_class": "HBEFA4/TT_AT_gt34-40t_Euro-VI_A-C",
    "v0": 28.0,
    "T": 1.8,
    "a_max": 0.5,
    "b": 1.5,
    "s0": 3.0,
}


def _with_heavy(**over: object) -> ScenarioConfig:
    """The canonical 2-lane corridor with a heavy population."""
    base = _canonical()
    heavy = HeavyVehicleSpec(**HEAVY, **over)  # type: ignore[arg-type]
    return base.model_copy(update={"fleet": base.fleet.model_copy(update={"heavy": heavy})})


def test_heavy_lane_shares_is_hash_neutral_until_it_is_set():
    """A lane-placed heavy population is a new optional field (policy v2)."""
    plain = _with_heavy()
    assert config_hash(_with_heavy(lane_shares=None)) == config_hash(plain)
    assert "lane_shares" not in config_hash_payload(plain)["config"]["fleet"]["heavy"]
    # ... and a scenario with no heavy block at all is untouched by the field
    assert "heavy" not in config_hash_payload(_canonical())["config"].get("fleet", {})

    placed = _with_heavy(lane_shares=[0.3, 0.7])
    assert config_hash(placed) != config_hash(plain)
    assert config_hash_payload(placed)["config"]["fleet"]["heavy"]["lane_shares"] == [0.3, 0.7]
    # ... and a different placement is a different run
    assert config_hash(_with_heavy(lane_shares=[0.7, 0.3])) != config_hash(placed)


def test_macro_tier_fields_are_hash_neutral_until_they_are_set():
    """``fd_calibration`` and ``macro`` are new optional fields (policy v2).

    A calibrated fundamental diagram and the CTM solver options change what a
    screening run computes, so setting either must move the hash — but no
    scenario that leaves them alone may be re-hashed by their existence.
    """
    base = _canonical()
    payload = config_hash_payload(base)["config"]
    assert "fd_calibration" not in payload
    assert "macro" not in payload
    assert config_hash(base.model_copy(update={"fd_calibration": None, "macro": None})) == (
        config_hash(base)
    )

    calibrated = base.model_copy(update={"fd_calibration": "artifacts/fd_i24.json"})
    assert config_hash(calibrated) != config_hash(base)
    assert config_hash_payload(calibrated)["config"]["fd_calibration"] == "artifacts/fd_i24.json"

    # Explicit defaults inside the macro block hash like an absent block's
    # values, but the block's presence is itself a choice and is recorded.
    default_opts = base.model_copy(update={"macro": MacroOptions()})
    assert config_hash_payload(default_opts)["config"]["macro"] == {}
    variant = base.model_copy(update={"macro": MacroOptions(bottleneck_variant="capacity")})
    assert config_hash(variant) != config_hash(base)
    assert config_hash(base.model_copy(update={"macro": MacroOptions(dx_m=50.0)})) != config_hash(
        variant
    )


def test_ramp_to_ramp_share_is_hash_neutral_until_it_is_set():
    """``WeaveSpec.ramp_to_ramp_share`` (2026-10-07) is a new optional field:
    unset, a weave scenario hashes and dumps (``meta.json["config"]``, YAML)
    exactly as before the field existed; set, the hash moves; out of [0, 1]
    is refused."""
    import pytest
    from pydantic import ValidationError

    root = Path(__file__).resolve().parents[2]
    cfg = ScenarioConfig.from_yaml(root / "scenarios" / "mndot_i94_wb_stpaul_weave_dc.yaml")
    raw = cfg.model_dump(mode="json")
    weaves = [r for r in raw["network"]["ramps"] if r.get("weave")]
    assert weaves, "the scenario has weave entrances"
    assert all("ramp_to_ramp_share" not in r["weave"] for r in weaves)
    assert "ramp_to_ramp_share" not in json.dumps(config_hash_payload(cfg))

    explicit = json.loads(json.dumps(raw))
    for r in explicit["network"]["ramps"]:
        if r.get("weave"):
            r["weave"]["ramp_to_ramp_share"] = None
    explicit_cfg = ScenarioConfig.model_validate(explicit)
    assert config_hash(explicit_cfg) == config_hash(cfg)
    assert explicit_cfg.model_dump(mode="json") == raw

    def with_share(share: float) -> ScenarioConfig:
        doc = json.loads(json.dumps(raw))
        next(r for r in doc["network"]["ramps"] if r.get("weave"))["weave"][
            "ramp_to_ramp_share"
        ] = share
        return ScenarioConfig.model_validate(doc)

    set_cfg = with_share(0.5)
    assert config_hash(set_cfg) != config_hash(cfg)
    assert config_hash(with_share(0.6)) != config_hash(set_cfg)
    dumped = set_cfg.model_dump(mode="json")
    assert ScenarioConfig.model_validate(dumped) == set_cfg  # YAML/JSON round trip
    for bad in (-0.1, 1.2):
        with pytest.raises(ValidationError, match="ramp_to_ramp_share"):
            with_share(bad)


def test_unset_fields_are_dropped_without_pydantic_2_12_features():
    """The packages declare ``pydantic>=2.10``; ``Field(exclude_if=...)`` exists
    from 2.12 only (on 2.10/2.11 it is a deprecated extra: nothing is excluded
    and the JSON schema cannot be generated). No field of the schema may rely
    on it (review 2026-10-07, finding 2): ``WeaveSpec`` drops an unset
    ``ramp_to_ramp_share`` with a wrap serializer instead, in every dump form,
    and its serialization schema is still the model's own."""
    import pydantic

    import flowstate_core.config as config_module
    from flowstate_core.config import WeaveSpec

    for name in dir(config_module):
        model = getattr(config_module, name)
        if isinstance(model, type) and issubclass(model, pydantic.BaseModel):
            for field_name, info in model.model_fields.items():
                assert getattr(info, "exclude_if", None) is None, (name, field_name)
    unset = WeaveSpec(exit_ramp="B", weave_params={"entrant_giveup_m": 5.0})
    assert unset.model_dump() == {
        "exit_ramp": "B",
        "length_m": None,
        "weave_params": {"entrant_giveup_m": 5.0},
    }
    assert unset.model_dump_json() == (
        '{"exit_ramp":"B","length_m":null,"weave_params":{"entrant_giveup_m":5.0}}'
    )
    assert WeaveSpec(exit_ramp="B", ramp_to_ramp_share=None).model_dump(mode="json") == (
        WeaveSpec(exit_ramp="B").model_dump(mode="json")
    )
    set_ = WeaveSpec(exit_ramp="B", ramp_to_ramp_share=0.3)
    assert set_.model_dump(mode="json")["ramp_to_ramp_share"] == 0.3
    assert '"ramp_to_ramp_share":0.3' in set_.model_dump_json()
    assert WeaveSpec.model_validate(set_.model_dump()) == set_
    # nested: a ramp's weave block dumps the same way
    ramp = {"kind": "on", "edges": ["r"], "attach_edge": "e", "inflow": [[0.0, 0.1]]}
    ramp["merge"] = "weave"
    ramp["weave"] = {"exit_ramp": "B"}
    raw = config_module.RampSpec.model_validate(ramp).model_dump(mode="json")
    assert "ramp_to_ramp_share" not in raw["weave"]
    schema = WeaveSpec.model_json_schema(mode="serialization")
    assert set(schema["properties"]) == {
        "exit_ramp",
        "length_m",
        "weave_params",
        "ramp_to_ramp_share",
    }
    assert schema == WeaveSpec.model_json_schema(mode="validation")


def test_pinned_ring_hash():
    root = Path(__file__).resolve().parents[2]
    cfg = ScenarioConfig.from_yaml(root / "scenarios" / "ring_sugiyama.yaml")
    assert config_hash(cfg) == PINNED_RING_HASH, (
        "the hash policy or a ring default moved; bump CONFIG_HASH_VERSION, regenerate the "
        "goldens and this pin with a CHANGELOG note"
    )


def test_v2_hash_of_a_document_reproduces_records_before_v3():
    """``config_hash_v2`` reads a document as policy v2 did (the AV
    command-path keys it does not set are false) for provenance checks
    against records dated before 2026-10-04."""
    import yaml

    root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((root / "scenarios" / "ring_sugiyama.yaml").read_text())
    assert config_hash_v2(raw) == PINNED_RING_HASH_V2
    assert config_hash(ScenarioConfig.model_validate(raw)) == PINNED_RING_HASH
    # under v2 an explicit false was the default and an explicit true moved the hash
    for key in ("emergency_handback", "release_off_corridor", "observe_close_leader"):
        off = json.loads(json.dumps(raw))
        off["av"][key] = False
        assert config_hash_v2(off) == PINNED_RING_HASH_V2, key
        on = json.loads(json.dumps(raw))
        on["av"][key] = True
        assert config_hash_v2(on) != PINNED_RING_HASH_V2, key
        # ... and under v3 the other way round
        assert config_hash(ScenarioConfig.model_validate(on)) == PINNED_RING_HASH, key
        assert config_hash(ScenarioConfig.model_validate(off)) != PINNED_RING_HASH, key
    # a document without an av block at all
    no_av = {k: v for k, v in raw.items() if k != "av"}
    assert config_hash_v2(no_av) == PINNED_RING_HASH_V2


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
    diffs += _existing_defaults_unchanged(
        golden["merge_defaults"], current["merge_defaults"], "/merge_defaults"
    )
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


def test_fleet_non_defaults_follows_the_hash_omission_rule():
    """``fleet_non_defaults`` (the re-onboarding report line) omits exactly
    what the config hash omits: a field at its default, an int ``1`` for a
    float default ``1.0`` included, ``idm_calibration: None`` included; a
    path and a changed float are listed in field order."""
    assert fleet_non_defaults(FleetSpec()) == {}
    assert (
        fleet_non_defaults(FleetSpec.model_validate({"lc_strategic": 1, "lc_keep_right": 1})) == {}
    )
    assert fleet_non_defaults(FleetSpec(idm_calibration=None)) == {}
    listed = fleet_non_defaults(
        FleetSpec.model_validate(
            {
                "lc_keep_right": 0,
                "idm_calibration": "artifacts/idm_unit.json",
                "model": "EIDM",
                "lc_strategic": 5,
            }
        )
    )
    assert list(listed) == ["model", "idm_calibration", "lc_strategic", "lc_keep_right"]
    assert listed == {
        "model": "EIDM",
        "idm_calibration": "artifacts/idm_unit.json",
        "lc_strategic": 5.0,
        "lc_keep_right": 0.0,
    }
    cfg = _canonical().model_copy(update={"fleet": FleetSpec.model_validate(dict(listed))})
    assert config_hash_payload(cfg)["config"]["fleet"] == listed
    assert "fleet" not in config_hash_payload(_canonical())["config"]


def test_fleet_settings_states_every_field_in_order():
    """The demand record's ``fleet_settings`` always carries the six fields,
    defaults and ``None`` included, in ``FLEET_SETTINGS_FIELDS`` order; a
    mapping as the scenario file carries it validates like the spec."""
    settings = fleet_settings({"lc_keep_right": 0})
    assert tuple(settings) == FLEET_SETTINGS_FIELDS
    assert settings == fleet_settings(FleetSpec(lc_keep_right=0.0))
    assert settings["lc_keep_right"] == 0.0 and settings["idm_calibration"] is None
    assert settings["lc_strategic_ramp"] is None and settings["model"] == "IDM"
