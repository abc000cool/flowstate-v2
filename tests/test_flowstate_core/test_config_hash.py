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

Version 4 (2026-10-07, Amendment 4; decision A2 of docs/DECISIONS_2026-10-07.md):
weave rules W1b and W2 on at every weaving section —
``WEAVE_AMENDMENT4_DEFAULTS`` (``entrant_giveup_m`` 5, ``entrant_giveup_dwell_s``
60, ``weave_handback``, ``weave_close_leader``, ``weave_resolve_opposing`` 1)
joined ``WEAVE_DEFAULTS``. Every hash moved once (ring ``d5472987265c`` →
``258c09ac0074``); ``config_hash_v3`` reproduces the version-3 hash of every
committed scenario (``tests/golden/scenario_config_hashes.json``) and of every
committed record that quotes one.

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
    config_hash_v3,
    fleet_non_defaults,
    fleet_settings,
)

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "config_defaults.json"
#: Every committed scenario's hash under policies 3 and 4 (2026-10-07).
SCENARIO_HASHES = Path(__file__).resolve().parents[1] / "golden" / "scenario_config_hashes.json"
# scenarios/ring_sugiyama.yaml under policy v4 (2026-10-07)
PINNED_RING_HASH = "258c09ac0074"
# the same file under policy v3 (2026-10-04 to 2026-10-07), as records of then quote it
PINNED_RING_HASH_V3 = "d5472987265c"
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


#: Config hashes of the committed scenarios B1's stage copies (policy v3), as
#: their step-3 battery artifacts record them (artifacts/i24_validation_dc_refit.json,
#: artifacts/i24_validation_flow_speedcal_ref.json, validation_mndot_..._xlsfg_dc.json);
#: reproduced by ``config_hash_v3`` since policy v4.
KNOWN_BOUNDARY_SCENARIO_HASHES = {
    "i24_replica_flow_speedcal_dc_refit.yaml": "ada3f406504b",
    "i24_replica_flow_speedcal.yaml": "ae5861a4d906",
    "mndot_i94_wb_stpaul_weave_dc.yaml": "db9fbab5fc6e",
}


def test_boundary_limit_factor_is_hash_neutral_until_it_is_set():
    """``BoundarySpec.limit_factor`` (2026-10-07, amendment B1, opt-in) is a new
    field: at its default 1.0 every committed boundary scenario hashes and dumps
    (``meta.json["config"]``, YAML) exactly as before it existed; set, the hash
    moves and the value round-trips."""
    import yaml

    root = Path(__file__).resolve().parents[2]
    for name, known_v3 in KNOWN_BOUNDARY_SCENARIO_HASHES.items():
        cfg = ScenarioConfig.from_yaml(root / "scenarios" / name)
        assert config_hash_v3(yaml.safe_load((root / "scenarios" / name).read_text())) == known_v3
        known = config_hash(cfg)
        raw = cfg.model_dump(mode="json")
        assert "limit_factor" not in raw["network"]["boundary"], name
        assert "limit_factor" not in json.dumps(config_hash_payload(cfg)), name
        assert "limit_factor" not in cfg.network.boundary.model_dump_json()  # type: ignore[union-attr]

        explicit = json.loads(json.dumps(raw))
        explicit["network"]["boundary"]["limit_factor"] = 1.0
        explicit_cfg = ScenarioConfig.model_validate(explicit)
        assert config_hash(explicit_cfg) == known, name
        assert explicit_cfg.model_dump(mode="json") == raw, name

        def with_factor(factor: float, doc: dict = raw) -> ScenarioConfig:
            d = json.loads(json.dumps(doc))
            d["network"]["boundary"]["limit_factor"] = factor
            return ScenarioConfig.model_validate(d)

        b1 = with_factor(1.2185)
        assert config_hash(b1) != known, name
        assert config_hash(with_factor(1.3)) != config_hash(b1), name
        dumped = b1.model_dump(mode="json")
        assert dumped["network"]["boundary"]["limit_factor"] == 1.2185
        assert ScenarioConfig.model_validate(dumped) == b1  # YAML/JSON round trip
    # the field's serialization schema is still the model's own (no return annotation)
    from flowstate_core.config import BoundarySpec

    schema = BoundarySpec.model_json_schema(mode="serialization")
    assert "limit_factor" in schema["properties"]
    assert schema == BoundarySpec.model_json_schema(mode="validation")


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
    # record_commands (2026-10-07) the same way: absent at False, kept at True
    assert WeaveSpec(exit_ramp="B", record_commands=False).model_dump(mode="json") == (
        WeaveSpec(exit_ramp="B").model_dump(mode="json")
    )
    assert "record_commands" not in WeaveSpec(exit_ramp="B").model_dump_json()
    on = WeaveSpec(exit_ramp="B", record_commands=True)
    assert on.model_dump()["record_commands"] is True
    assert '"record_commands":true' in on.model_dump_json()
    assert WeaveSpec.model_validate(on.model_dump()) == on
    assert "record_commands" not in raw["weave"]
    schema = WeaveSpec.model_json_schema(mode="serialization")
    assert set(schema["properties"]) == {
        "exit_ramp",
        "length_m",
        "weave_params",
        "ramp_to_ramp_share",
        "record_commands",
    }
    assert schema == WeaveSpec.model_json_schema(mode="validation")


#: Committed scenarios whose weave blocks feed a measured merge zone
#: (``merge: measured``), with their policy-v3 hash (``config_hash_v3``).
KNOWN_MEASURED_WEAVE_SCENARIO_HASHES = {
    "mndot_i94_wb_stpaul_weave_measured.yaml": "d317700d4156",
    "i24_replica_flow_speedcal_measured.yaml": "8103d6067875",
}


def test_record_commands_is_hash_neutral_on_and_off(tmp_path):
    """``WeaveSpec.record_commands`` (2026-10-07, the weave command recorder,
    opt-in) is a pure observer, so it is left out of the config hash at every
    value (review 2026-10-07): a recording re-run of a pinned scenario carries
    the pinned hash (``--expect-hash`` guards, p8c's reproduction check) on a
    weaving section and on a measured zone's weave block alike. At False it is
    absent from every dump (``model_dump``, ``meta.json["config"]``, YAML), as
    before the field existed; true, the dump, the YAML and ``meta.json["config"]``
    still carry it and round-trip."""
    import yaml

    from flowstate_core.config import RampSpec

    root = Path(__file__).resolve().parents[2]
    cases = {
        "mndot_i94_wb_stpaul_weave_dc.yaml": KNOWN_BOUNDARY_SCENARIO_HASHES[
            "mndot_i94_wb_stpaul_weave_dc.yaml"
        ],
        **KNOWN_MEASURED_WEAVE_SCENARIO_HASHES,
    }
    merges_seen = set()
    for name, known_v3 in cases.items():
        cfg = ScenarioConfig.from_yaml(root / "scenarios" / name)
        assert config_hash_v3(yaml.safe_load((root / "scenarios" / name).read_text())) == known_v3
        known = config_hash(cfg)
        raw = cfg.model_dump(mode="json")
        weaves = [r for r in raw["network"]["ramps"] if r.get("weave")]
        assert weaves, f"{name} has weave blocks"
        merges_seen |= {r["merge"] for r in weaves}
        assert all("record_commands" not in r["weave"] for r in weaves), name
        assert "record_commands" not in json.dumps(config_hash_payload(cfg)), name
        cfg.to_yaml(tmp_path / "unset.yaml")
        assert "record_commands" not in (tmp_path / "unset.yaml").read_text(), name

        def with_flag(value: bool, doc: dict = raw) -> ScenarioConfig:
            d = json.loads(json.dumps(doc))
            for r in d["network"]["ramps"]:
                if r.get("weave"):
                    r["weave"]["record_commands"] = value
            return ScenarioConfig.model_validate(d)

        off = with_flag(False)
        assert config_hash(off) == known and off.model_dump(mode="json") == raw, name
        on = with_flag(True)
        assert config_hash(on) == known, name
        assert config_hash_payload(on) == config_hash_payload(cfg), name
        assert "record_commands" not in json.dumps(config_hash_payload(on)), name
        # the v2 reading of a document shares the payload rule
        assert config_hash_v2(on.model_dump(mode="json")) == config_hash_v2(raw), name
        # ... while every other dump still records it (meta.json["config"] is
        # model_dump(mode="json"), the YAML is written from it)
        dumped_on = on.model_dump(mode="json")
        assert all(
            r["weave"]["record_commands"] is True
            for r in dumped_on["network"]["ramps"]
            if r.get("weave")
        ), name
        on.to_yaml(tmp_path / "on.yaml")
        from_yaml = yaml.safe_load((tmp_path / "on.yaml").read_text())
        assert all(
            r["weave"]["record_commands"] is True
            for r in from_yaml["network"]["ramps"]
            if r.get("weave")
        ), name
        assert ScenarioConfig.from_yaml(tmp_path / "on.yaml") == on, name
        # one block on, the others off: the same hash again
        one = json.loads(json.dumps(raw))
        next(r for r in one["network"]["ramps"] if r.get("weave"))["weave"]["record_commands"] = (
            True
        )
        assert config_hash(ScenarioConfig.model_validate(one)) == known, name
    assert merges_seen == {"weave", "measured"}
    # a measured zone's weave block takes the flag (it is not a weave_params key)
    measured = RampSpec.model_validate(
        {
            "kind": "on",
            "edges": ["r"],
            "attach_edge": "e",
            "inflow": [[0.0, 0.1]],
            "merge": "measured",
            "weave": {"exit_ramp": "B", "record_commands": True},
        }
    )
    assert measured.weave is not None and measured.weave.record_commands
    assert measured.model_dump(mode="json")["weave"]["record_commands"] is True


def test_every_committed_scenario_hashes_as_before_with_the_recorder_on_or_off():
    """Every committed scenario hashes as it did before the hash payload's dump
    context existed (the payload a plain ``exclude_defaults`` dump gives: no
    committed scenario sets ``record_commands``), and as it does unchanged with
    ``record_commands`` true on every weave block it has."""
    from flowstate_core.config import CONFIG_HASH_VERSION, _digest

    root = Path(__file__).resolve().parents[2]
    paths = sorted((root / "scenarios").glob("*.yaml"))
    assert len(paths) >= 45
    n_with_weaves = 0
    for path in paths:
        cfg = ScenarioConfig.from_yaml(path)
        before = cfg.model_dump(mode="json", exclude_defaults=True)
        before["network"] = {**before["network"], "kind": cfg.network.kind}
        known = _digest({"hash_version": CONFIG_HASH_VERSION, "config": before})
        assert config_hash(cfg) == known, path.name
        doc = cfg.model_dump(mode="json")
        ramps = doc["network"].get("ramps") or []
        weaves = [r for r in ramps if r.get("weave")]
        if not weaves:
            continue
        n_with_weaves += 1
        for r in weaves:
            r["weave"]["record_commands"] = True
        assert config_hash(ScenarioConfig.model_validate(doc)) == known, path.name
    assert n_with_weaves >= 3


def test_hash_payload_context_reaches_only_the_hash():
    """The hash payload's dump context (``HASH_PAYLOAD_CONTEXT_KEY``) is what
    drops ``record_commands`` true: a plain dump keeps it, a dump with the key
    drops it, and it changes nothing else of a weave block."""
    from flowstate_core.config import HASH_PAYLOAD_CONTEXT_KEY, WeaveSpec

    on = WeaveSpec(exit_ramp="B", ramp_to_ramp_share=0.3, record_commands=True)
    plain = on.model_dump(mode="json")
    assert plain["record_commands"] is True
    hashed = on.model_dump(mode="json", context={HASH_PAYLOAD_CONTEXT_KEY: True})
    assert "record_commands" not in hashed
    assert hashed == {k: v for k, v in plain.items() if k != "record_commands"}
    assert on.model_dump(mode="json", context={HASH_PAYLOAD_CONTEXT_KEY: False}) == plain
    assert on.model_dump(mode="json", context={"other": True}) == plain


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


def test_v3_hash_of_a_document_reproduces_records_before_v4():
    """``config_hash_v3`` reproduces the policy-v3 hash (2026-10-04 to
    2026-10-07) of every committed scenario, as the policy-3 code computed it
    (``tests/golden/scenario_config_hashes.json``, written with the code at
    cd470a9), and ``config_hash`` its policy-v4 hash; the two differ for every
    scenario (the version is in the payload), and only by the version: the
    payload rule and every model default are unchanged (Amendment 4 changed
    defaults in the dict-valued ``WEAVE_DEFAULTS``, hashed as written)."""
    import yaml

    from flowstate_core.config import _digest

    root = Path(__file__).resolve().parents[2]
    table = json.loads(SCENARIO_HASHES.read_text())
    v3, v4 = table["policy_v3"], table["policy_v4"]
    assert set(v3) == set(v4) and len(v3) >= 54
    assert v3["scenarios/ring_sugiyama.yaml"] == PINNED_RING_HASH_V3
    assert v4["scenarios/ring_sugiyama.yaml"] == PINNED_RING_HASH
    for name in sorted(v3):
        doc = yaml.safe_load((root / name).read_text())
        cfg = ScenarioConfig.model_validate(doc)
        assert config_hash_v3(doc) == v3[name], name
        assert config_hash_v3(cfg) == v3[name], name  # a validated config reads the same
        assert config_hash(cfg) == v4[name], name
        assert v3[name] != v4[name], name
        payload = config_hash_payload(cfg)
        assert _digest({"hash_version": 3, "config": payload["config"]}) == v3[name], name
    # the ring reads the same under all three policies' functions
    raw = yaml.safe_load((root / "scenarios" / "ring_sugiyama.yaml").read_text())
    assert config_hash_v2(raw) == PINNED_RING_HASH_V2


#: Committed records whose quoted hash names a scenario file that was rewritten
#: after the record (their hash matched no policy before Amendment 4 either):
#: the I-94 base scenario was regenerated after its first demand record (0ef3b67)
#: and the measured slice after its p2b battery (053a0d0).
_STALE_RECORDS = frozenset(
    {
        "artifacts/demand_mndot_i94_wb_stpaul.json",
        "artifacts/validation_mndot_i94_wb_stpaul_weave_slice_measured_p2b.json",
    }
)


def test_every_committed_record_reproduces_under_its_policy():
    """Every committed artifact that records ``{"scenario", "config_hash"}`` of a
    committed scenario file — battery, gate, demand, lock and ramp-flow records
    — reproduces under the policy of its date: ``config_hash_v3`` for records
    from 2026-10-04 to 2026-10-07, ``config_hash_v2`` for those from 2026-09-06
    to 2026-10-03, ``config_hash`` for those written since the bump. Records of
    scenario copies the stages did not commit (``*_xlsfg.yaml``) are skipped, as
    are the two pre-existing stale ones (``_STALE_RECORDS``)."""
    import yaml

    root = Path(__file__).resolve().parents[2]
    seen = {"v2": 0, "v3": 0}
    for path in sorted((root / "artifacts").glob("*.json")):
        rel = str(path.relative_to(root))
        if rel in _STALE_RECORDS:
            continue
        try:
            record = json.loads(path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        quoted, scenario = record.get("config_hash"), record.get("scenario")
        if not isinstance(quoted, str) or not isinstance(scenario, str):
            continue
        candidates = [root / scenario, root / "scenarios" / f"{scenario}.yaml"]
        file = next((c for c in candidates if c.is_file()), None)
        if file is None:
            continue  # a stage's uncommitted copy
        doc = yaml.safe_load(file.read_text())
        created = str(record.get("created_at") or "")
        if quoted == config_hash(ScenarioConfig.model_validate(doc)):
            assert not created or created >= "2026-10-07", rel  # written since the bump
            continue
        if quoted == config_hash_v3(doc):
            assert not created or created >= "2026-10-04", rel
            seen["v3"] += 1
        else:
            assert quoted == config_hash_v2(doc), rel
            assert not created or created < "2026-10-04", rel
            seen["v2"] += 1
    assert seen["v3"] >= 40 and seen["v2"] >= 10, seen


def test_amendment4_defaults_are_weave_defaults():
    """Amendment 4 (2026-10-07): W1b and W2 have defaults, in
    ``WEAVE_DEFAULTS``' tail in the committed scenarios' order; no weave key is
    left without one; ``WEAVE_AMENDMENT4_OFF`` turns all five off."""
    from flowstate_core.config import (
        WEAVE_AMENDMENT4_DEFAULTS,
        WEAVE_AMENDMENT4_OFF,
        WEAVE_KEYS,
        WEAVE_OPTIONAL_KEYS,
        WEAVE_W1B_KEYS,
        WEAVE_W2_SWITCHES,
    )

    assert dict(WEAVE_AMENDMENT4_DEFAULTS) == {
        "entrant_giveup_m": 5.0,
        "entrant_giveup_dwell_s": 60.0,
        "weave_handback": 1.0,
        "weave_close_leader": 1.0,
        "weave_resolve_opposing": 1.0,
    }
    assert set(WEAVE_AMENDMENT4_DEFAULTS) == WEAVE_W1B_KEYS | WEAVE_W2_SWITCHES
    assert list(WEAVE_DEFAULTS)[-5:] == list(WEAVE_AMENDMENT4_DEFAULTS)
    assert {k: WEAVE_DEFAULTS[k] for k in WEAVE_AMENDMENT4_DEFAULTS} == dict(
        WEAVE_AMENDMENT4_DEFAULTS
    )
    assert WEAVE_OPTIONAL_KEYS == frozenset() and frozenset(WEAVE_DEFAULTS) == WEAVE_KEYS
    assert dict(WEAVE_AMENDMENT4_OFF) == dict.fromkeys(WEAVE_AMENDMENT4_DEFAULTS, 0.0)
    golden = json.loads(GOLDEN.read_text())
    assert golden["hash_version"] == CONFIG_HASH_VERSION == 4
    assert golden["merge_defaults"]["weave"] == dict(WEAVE_DEFAULTS)


def _weave_doc(root: Path) -> dict:
    import yaml

    return yaml.safe_load(
        (root / "scenarios" / "mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2.yaml").read_text()
    )


def test_a_section_setting_all_five_keys_moves_by_the_version_alone():
    """``_dc_cal_w1b_w2`` (p10's arm B) sets the five keys on both weaving
    sections: its payload is the same under policies 3 and 4 and so is its
    physics; its battery's recorded hash (5080d84d4725) is its v3 hash; its v4
    hash differs by the version alone. A scenario that sets none
    (``_dc_cal``) has the same payload too, but runs W1b and W2 under v4."""
    from flowstate_core.config import _digest

    root = Path(__file__).resolve().parents[2]
    doc = _weave_doc(root)
    cfg = ScenarioConfig.model_validate(doc)
    payload = config_hash_payload(cfg)
    assert config_hash_v3(doc) == "5080d84d4725"
    assert config_hash(cfg) == _digest({"hash_version": 4, "config": payload["config"]})
    weaves = [r for r in cfg.network.ramps if r.merge == "weave"]  # type: ignore[union-attr]
    assert len(weaves) == 2
    for r in weaves:
        assert r.weave is not None
        assert {k: r.weave.weave_params[k] for k in r.weave.weave_params} == {
            "exit_prepare": 1.0,
            **{k: WEAVE_DEFAULTS[k] for k in list(WEAVE_DEFAULTS)[-5:]},
        }
    # the stored weave_params stay as written: nothing is filled in by validation
    import yaml

    plain = yaml.safe_load(
        (root / "scenarios" / "mndot_i94_wb_stpaul_weave_dc_cal.yaml").read_text()
    )
    plain_cfg = ScenarioConfig.model_validate(plain)
    for r in plain_cfg.network.ramps:  # type: ignore[union-attr]
        if r.merge == "weave":
            assert r.weave is not None and r.weave.weave_params == {"exit_prepare": 1.0}
    assert config_hash_v3(plain) == "beaaa710e6b3"


def test_w2_without_w1b_is_refused_but_read_by_the_v3_hash():
    """Amendment 4: W2 never without W1b. A weave block with W1b turned off and
    any W2 switch on (explicitly, or by default) is refused with an error naming
    the amendment; W2's switches off as well (the opt-out reproducing a result
    published before the amendment) is allowed; ``config_hash_v3`` still reads
    a document that ran W2 alone under v3 (the G fixtures' arm)."""
    import pytest
    from pydantic import ValidationError

    from flowstate_core.config import WEAVE_AMENDMENT4_OFF, WeaveSpec

    for params in (
        {"entrant_giveup_m": 0.0},
        {"entrant_giveup_dwell_s": 0.0},
        {"entrant_giveup_m": 0.0, "weave_handback": 0.0, "weave_close_leader": 0.0},
        {**WEAVE_AMENDMENT4_OFF, "weave_resolve_opposing": 1.0},
    ):
        with pytest.raises(ValidationError, match=r"Amendment 4 .* never runs W2 without W1b"):
            WeaveSpec(exit_ramp="x", weave_params=params)
    WeaveSpec(exit_ramp="x", weave_params=dict(WEAVE_AMENDMENT4_OFF))
    WeaveSpec(
        exit_ramp="x",
        weave_params={
            "entrant_giveup_m": 0.0,
            **dict.fromkeys(
                ("weave_handback", "weave_close_leader", "weave_resolve_opposing"), 0.0
            ),
        },
    )
    # W1b alone (p9's and p10's arm A): allowed
    WeaveSpec(
        exit_ramp="x",
        weave_params=dict.fromkeys(
            ("weave_handback", "weave_close_leader", "weave_resolve_opposing"), 0.0
        ),
    )
    root = Path(__file__).resolve().parents[2]
    doc = _weave_doc(root)
    w2_alone = json.loads(json.dumps(doc))
    for r in w2_alone["network"]["ramps"]:
        if r.get("merge") == "weave":
            r["weave"]["weave_params"] = {
                "exit_prepare": 1.0,
                "weave_handback": 1.0,
                "weave_close_leader": 1.0,
                "weave_resolve_opposing": 1.0,
                "entrant_giveup_m": 0.0,
            }
    with pytest.raises(ValidationError, match="never runs W2 without W1b"):
        ScenarioConfig.model_validate(w2_alone)
    assert len(config_hash_v3(w2_alone)) == 12
    assert config_hash_v3(w2_alone) != config_hash_v3(doc)


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
