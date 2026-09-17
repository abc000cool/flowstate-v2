"""The merge-experiment variant parser: the arm it starts from, and the ramp level.

``scripts/i24_merge_experiment.py`` builds every probe variant by editing one
arm yaml. Two things changed for the widened Old Hickory grid of
docs/MERGE_ROUND6_PLAN.md §2.3 (addendum 2026-09-17), and both are only safe if
the names already run are untouched:

* ``--base PATH`` chooses the arm, so the grid can run on the "flow" family's
  fitted arm instead of ``scenarios/i24_replica_speedcal.yaml``. A base built on
  the corrected map must not have the ``geometry_corrected*`` levers applied
  twice, and must not be mislabelled ``_entrylanes`` because it already carries
  entry lane shares of its own (they are flow shares).
* ``_oh<mult>`` scales the Old Hickory on-ramp inflow, and nothing else.

Every published variant's config hash is a provenance key in the committed
artifacts, so the first test pins the three rows of
``artifacts/i24_merge_experiment_entryflow.json`` to the parser.

Data-only and fast: no SUMO, no simulation.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from flowstate_core.config import ScenarioConfig, config_hash

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
ENTRYFLOW_ARTIFACT = REPO / "artifacts" / "i24_merge_experiment_entryflow.json"
# the "flow" family's fitted arm; the cloud run that fits it writes
# i24_replica_flow_speedcal.yaml, and its corrected-map parent is committed.
FLOW_YAMLS = (
    REPO / "scenarios" / "i24_replica_flow_speedcal.yaml",
    REPO / "scenarios" / "i24_replica_flow_corrected.yaml",
)


def _load(name: str) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod() -> ModuleType:
    return _load("i24_merge_experiment")


@pytest.fixture(scope="module")
def flow_yaml() -> Path:
    """The committed arm yaml that is already on the corrected map with flow shares."""
    for path in FLOW_YAMLS:
        if path.exists():
            return path
    pytest.skip(f"no flow-family arm yaml on this machine: {[p.name for p in FLOW_YAMLS]}")


def _hash(raw: dict) -> str:
    return config_hash(ScenarioConfig.model_validate(raw))


def _oh(raw: dict, mod: ModuleType) -> dict:
    return next(r for r in raw["network"]["ramps"] if r["name"] == mod.OH)


# --------------------------------------------------------------------------
# The published variants keep their config hashes
# --------------------------------------------------------------------------


def test_published_variant_hashes_reproduce(mod: ModuleType) -> None:
    """Every row of the committed entryflow artifact rebuilds to its recorded hash.

    These are provenance keys in docs/I24_VALIDATION.md §0.9 and in
    docs/MERGE_ROUND6_PLAN.md's candidate-1 addendum; the ``--base`` default and
    the name tagging must leave them byte-identical.
    """
    recorded = json.loads(ENTRYFLOW_ARTIFACT.read_text())
    rows = {r["variant"]: r["config_hash"] for r in recorded["variants"]}
    assert "geometry_corrected_ramplc1_entrylanes" in rows
    assert "geometry_corrected_ramplc1_entryflow" in rows
    for variant, recorded_hash in rows.items():
        assert _hash(mod.variant_config(variant)) == recorded_hash, variant
    # the artifact's arm is this script's default base
    assert recorded["arm"] == str(mod.ARM_YAML.relative_to(REPO))


def test_default_base_is_the_arm_yaml(mod: ModuleType) -> None:
    """The default of ``--base`` is the yaml the parser used before it existed."""
    assert mod._parser().parse_args([]).base == mod.ARM_YAML
    assert mod._parser().parse_args(["--base", "scenarios/x.yaml"]).base == Path("scenarios/x.yaml")
    assert mod.variant_config("as_is") == mod.variant_config("as_is", mod.ARM_YAML)


# --------------------------------------------------------------------------
# _oh<mult>: the Old Hickory on-ramp level
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("variant", "stem", "mult"),
    [
        ("as_is_oh0.55", "as_is", 0.55),
        ("as_is_oh1.25", "as_is", 1.25),
        ("as_is_oh1.6", "as_is", 1.6),
        ("geometry_corrected_ramplc1_entrylanes_oh0.75", None, 0.75),
        ("geometry_corrected_ramplc1_entryflow_oh1.25", None, 1.25),
    ],
)
def test_oh_suffix_round_trip(mod: ModuleType, variant: str, stem: str | None, mult: float) -> None:
    """The suffix parses off the name, tags the scenario, and scales the ramp."""
    base = yaml.safe_load(mod.ARM_YAML.read_text())
    raw = mod.variant_config(variant)
    assert raw["name"] == mod.variant_config(variant.replace(f"_oh{mult:g}", ""))["name"] + (
        f"_oh{mult:g}"
    )
    if stem is not None:
        assert raw["name"] == f"i24_merge_{stem}_oh{mult:g}"
    got = _oh(raw, mod)["inflow"]
    want = _oh(base, mod)["inflow"]
    assert [t for t, _ in got] == [t for t, _ in want]
    assert [q for _, q in got] == pytest.approx([q * mult for _, q in want], rel=1e-12)


def test_oh_suffix_position_and_spelling(mod: ModuleType) -> None:
    """``_oh`` is read anywhere after the base name, and 1 spells 1.0."""
    anywhere = mod.variant_config("geometry_corrected_ramplc1_oh0.55_entryflow")
    ending = mod.variant_config("geometry_corrected_ramplc1_entryflow_oh0.55")
    assert _hash(anywhere) == _hash(ending)
    assert _hash(mod.variant_config("as_is_oh1")) == _hash(mod.variant_config("as_is_oh1.0"))
    # a multiplier of 1 leaves the ramp untouched; only the name records the ask
    base = yaml.safe_load(mod.ARM_YAML.read_text())
    assert _oh(mod.variant_config("as_is_oh1"), mod)["inflow"] == _oh(base, mod)["inflow"]


def test_oh_multiplier_touches_only_the_old_hickory_inflow(mod: ModuleType) -> None:
    """Nothing but that one ramp's inflow column differs from the unscaled variant."""
    plain = mod.variant_config("geometry_corrected_ramplc1_entryflow")
    scaled = mod.variant_config("geometry_corrected_ramplc1_entryflow_oh0.55")
    stripped = copy.deepcopy(scaled)
    stripped["name"] = plain["name"]
    _oh(stripped, mod)["inflow"] = _oh(plain, mod)["inflow"]
    assert stripped == plain
    # the other ramps keep their own inflows and exit fractions
    for ramp in scaled["network"]["ramps"]:
        if ramp["name"] == mod.OH:
            continue
        twin = next(r for r in plain["network"]["ramps"] if r["name"] == ramp["name"])
        assert ramp == twin


def test_oh_suffix_composes_with_the_ramp_level_variants(mod: ModuleType) -> None:
    """``oh_closed`` / ``oh_tracked`` set the level first; the multiplier scales that."""
    closed = _oh(mod.variant_config("oh_closed_oh1.6"), mod)["inflow"]
    assert [q for _, q in closed] == [0.0] * len(closed)
    tracked = _oh(yaml.safe_load(mod.TRACKED_YAML.read_text()), mod)["inflow"]
    got = _oh(mod.variant_config("oh_tracked_oh0.5"), mod)["inflow"]
    assert [q for _, q in got] == pytest.approx([q * 0.5 for _, q in tracked], rel=1e-12)


def test_oh_suffix_composes_with_the_other_suffixes(mod: ModuleType) -> None:
    """``_entryflow`` / ``_entrylanes`` / ``_scripted`` still apply under ``_oh``."""
    flow = mod.variant_config("geometry_corrected_ramplc1_entryflow_oh0.55")
    lanes = mod.variant_config("geometry_corrected_ramplc1_entrylanes_oh0.55")
    assert flow["network"]["entry_lane_shares"] == mod.observed_entry_lane_flow_shares()
    assert lanes["network"]["entry_lane_shares"] == mod.observed_entry_lane_shares()
    assert flow["name"].endswith("_entryflow_oh0.55")
    assert lanes["name"].endswith("_entrylanes_oh0.55")
    scripted = mod.variant_config("geometry_corrected_ramplc1_entrylanes_scripted_court2_oh1.6")
    assert _oh(scripted, mod)["merge"] == "scripted"
    assert _oh(scripted, mod)["merge_params"] == {"courtesy": 2.0}
    assert (
        scripted["name"] == "i24_merge_geometry_corrected_ramplc1_entrylanes_oh1.6_scripted_court2"
    )
    with pytest.raises(ValueError, match="mutually exclusive"):
        mod.variant_config("as_is_entrylanes_entryflow_oh0.55")


# --------------------------------------------------------------------------
# --base: another arm, and no lever applied twice
# --------------------------------------------------------------------------


def test_flow_speedcal_is_the_base_as_is(mod: ModuleType, flow_yaml: Path) -> None:
    """``flow_speedcal`` and ``as_is`` are the same ask: the base yaml unchanged."""
    base = yaml.safe_load(flow_yaml.read_text())
    raw = mod.variant_config("flow_speedcal", flow_yaml)
    assert _hash(raw) == _hash(mod.variant_config("as_is", flow_yaml))
    assert raw["name"] == "i24_merge_as_is"
    assert {k: v for k, v in raw.items() if k != "name"} == {
        k: v for k, v in base.items() if k != "name"
    }
    # and it carries the suffixes, so the grid's reference row and its points agree
    scaled = mod.variant_config("flow_speedcal_oh1.6", flow_yaml)
    assert scaled["name"] == "i24_merge_as_is_oh1.6"
    assert [q for _, q in _oh(scaled, mod)["inflow"]] == pytest.approx(
        [q * 1.6 for _, q in _oh(base, mod)["inflow"]], rel=1e-12
    )
    with pytest.raises(ValueError):
        mod.variant_config("flow_speedcall", flow_yaml)


def test_geometry_corrected_is_a_no_op_on_a_corrected_base(
    mod: ModuleType, flow_yaml: Path
) -> None:
    """The corrected map is already the base's map: setting it changes nothing."""
    base = yaml.safe_load(flow_yaml.read_text())
    assert base["network"]["osm_file"] == mod.CORRECTED_OSM
    for variant in ("geometry_corrected", "geometry_corrected_ramplc1"):
        raw = mod.variant_config(variant, flow_yaml)
        assert raw["network"] == base["network"]
    # ramplc1 asks for the ramp-origin eagerness the flow family already has
    assert mod.variant_config("geometry_corrected_ramplc1", flow_yaml)["fleet"] == base["fleet"]


@pytest.mark.parametrize(
    "variant", ["geometry_corrected_ramplc1", "geometry_corrected_ramplc1_entryflow", "as_is"]
)
def test_base_is_idempotent(mod: ModuleType, flow_yaml: Path, tmp_path: Path, variant: str) -> None:
    """Building a variant from its own output changes nothing but the name prefix.

    Every lever these names carry (the corrected map, the ramp-origin eagerness,
    the entry flow shares) is a value assignment, so a second application must be
    a no-op — which is what makes ``--base`` safe on an arm already built with them.
    """
    once = mod.variant_config(variant, flow_yaml)
    intermediate = tmp_path / "once.yaml"
    intermediate.write_text(yaml.safe_dump(once, sort_keys=False))
    twice = mod.variant_config(variant, intermediate)
    assert {k: v for k, v in twice.items() if k != "name"} == {
        k: v for k, v in once.items() if k != "name"
    }


def test_entry_lane_tag_reflects_the_variant_not_the_base(mod: ModuleType, flow_yaml: Path) -> None:
    """A base that already carries entry lane shares is not tagged ``_entrylanes``.

    The flow family's shares are in flow units, so labelling them with the
    vehicle-time suffix would mis-name the run; the tag records what the variant
    applied. On the default base (no shares of its own) nothing changes, which
    ``test_published_variant_hashes_reproduce`` pins.
    """
    base = yaml.safe_load(flow_yaml.read_text())
    assert base["network"]["entry_lane_shares"] is not None
    assert mod.variant_config("as_is", flow_yaml)["name"] == "i24_merge_as_is"
    lanes = mod.variant_config("as_is_entrylanes", flow_yaml)
    assert lanes["name"] == "i24_merge_as_is_entrylanes"
    assert lanes["network"]["entry_lane_shares"] == mod.observed_entry_lane_shares()


def test_heavylanes_places_the_same_heavy_population_by_lane(mod: ModuleType) -> None:
    """``_heavylanes`` = ``_heavy`` plus ``HeavyVehicleSpec.lane_shares`` from the
    measured per-lane heavy fractions; everything else identical."""
    from microsim.vehicles import heavy_lane_shares_from_artifact

    if not (REPO / "artifacts" / "i24_heavy_by_lane.json").is_file():
        pytest.skip("artifact not present")
    plain = mod.variant_config("as_is_heavy")
    placed = mod.variant_config("as_is_heavylanes")
    shares = placed["fleet"]["heavy"].pop("lane_shares")
    assert len(shares) == 4 and abs(sum(shares) - 1.0) < 1e-3
    expected = heavy_lane_shares_from_artifact(
        REPO / "artifacts" / "i24_heavy_by_lane.json", REPO / "artifacts" / "i24_lane_profile.json"
    )
    assert shares == [round(v, 4) for v in expected]
    assert placed["name"].endswith("_heavylanes") and plain["name"].endswith("_heavy")
    placed["name"] = plain["name"]
    assert placed == plain


@pytest.mark.parametrize("tag", ["merge", "mergecap"])
def test_fleet_suffix_selects_the_population_artifact(mod: ModuleType, tag: str) -> None:
    """``_fleet<tag>`` swaps only ``fleet.idm_calibration`` and tags the name."""
    plain = mod.variant_config("as_is")
    fleet = mod.variant_config(f"as_is_fleet{tag}")
    assert fleet["fleet"]["idm_calibration"] == mod.FLEET_ARTIFACTS[tag]
    assert fleet["name"].endswith(f"_fleet{tag}")
    fleet["fleet"]["idm_calibration"] = plain["fleet"]["idm_calibration"]
    fleet["name"] = plain["name"]
    assert fleet == plain
    # composes with the other suffixes and never mistakes one tag for the other
    both = mod.variant_config(f"as_is_fleet{tag}_oh0.75")
    assert both["fleet"]["idm_calibration"] == mod.FLEET_ARTIFACTS[tag]
    assert both["name"].endswith("_oh0.75") or "_oh0.75" in both["name"]
