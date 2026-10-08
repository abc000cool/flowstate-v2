"""scripts/apply_driver_calibration.py: the Amendment-1 choice written into the calibrated scenarios.

Text only, no simulation (the laptop rule):

* the line edits on tiny synthetic scenarios — only the top-level name and the
  fleet's own ``idm_calibration`` / ``lc_keep_right`` lines change (nested
  keys of the same name do not), plus, for I-94, the ``xlsfg`` lines; a source
  that does not run the current setting, or whose layout the recipe does not
  fit, is refused;
* on the committed I-94 weave scenario the ``xlsfg`` edit at the current
  setting is stage ``p1_mndot_ref``'s sed/awk recipe byte for byte and hashes
  to the phase-1 battery's recorded config hash (b550b46fe751);
* both corridors' calibrated files, built from explicit values, are the grid's
  data recipe (``calibrate_driver_grid``) with the new name, preceded by a
  provenance header that states their config hash;
* from a synthetic grid artifact (its selection made by
  ``validation.driver_calibration.select_pair``, its population records by
  ``calibrate_driver_grid.check_populations``): written, checked, and refused
  for another corridor, an incomplete grid, a changed population, an
  off-grid value, an existing file.
"""

from __future__ import annotations

import copy
import difflib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from flowstate_core.config import CONFIG_HASH_VERSION, ScenarioConfig, config_hash, config_hash_v3
from validation.driver_calibration import K_GRID, GridScore, grid_pairs, select_pair

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
WEAVE = REPO_ROOT / "scenarios" / "mndot_i94_wb_stpaul_weave.yaml"
I24_REF = REPO_ROOT / "scenarios" / "i24_replica_flow_speedcal.yaml"
BASE_POP = "artifacts/idm_i24_capacity.json"


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ap = _load("apply_driver_calibration")
g = _load("calibrate_driver_grid")

TINY = """\
# tiny: a synthetic scenario for the apply script's tests
name: tiny
network:
  kind: straight
  ramps:
  - name: ramp a
    lc_keep_right: 0.0
fleet:
  model: IDM
  idm_calibration: artifacts/idm_i24_capacity.json
  lc_strategic: 5.0
  lc_keep_right: 0.0
  heavy:
    idm_calibration: artifacts/heavy.json
    lc_keep_right: 0.0
av:
  penetration: 0.0
seed: 42
"""

TINY_OSM = """\
name: tinyosm
network:
  kind: osm
  ramps:
  - kind: on
    merge: scripted
    merge_params: {}
  - kind: on
    merge: lane_change
    merge_params: {}
  - kind: weave
    weave:
      weave_params: {}
    merge_params: {}
  - kind: on
    merge: scripted
    merge_params: {}
fleet:
  idm_calibration: artifacts/idm_i24_capacity.json
  lc_keep_right: 0.0
seed: 42
"""


def _changed_lines(old: str, new: str) -> list[tuple[str, str]]:
    """``(old line, new line)`` of every replaced line (inserted lines as ``("", new)``)."""
    out: list[tuple[str, str]] = []
    a, b = old.splitlines(), new.splitlines()
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "replace":
            out += list(zip(a[i1:i2], b[j1:j2], strict=True))
        elif op == "insert":
            out += [("", line) for line in b[j1:j2]]
        elif op == "delete":
            out += [(line, "") for line in a[i1:i2]]
    return out


# --- the line edits ---------------------------------------------------------------------------


def test_only_the_name_and_the_fleets_own_two_lines_change() -> None:
    new, counts = ap.edit_text(
        TINY,
        name="tiny_dc",
        population="artifacts/idm_i24_capacity_amax_k0.5.json",
        keep_right=0.25,
        xlsfg=False,
        expect_population=BASE_POP,
    )
    assert counts == {}
    assert _changed_lines(TINY, new) == [
        ("name: tiny", "name: tiny_dc"),
        (
            "  idm_calibration: artifacts/idm_i24_capacity.json",
            "  idm_calibration: artifacts/idm_i24_capacity_amax_k0.5.json",
        ),
        ("  lc_keep_right: 0.0", "  lc_keep_right: 0.25"),
    ]
    doc = yaml.safe_load(new)
    assert doc["fleet"]["heavy"] == {
        "idm_calibration": "artifacts/heavy.json",
        "lc_keep_right": 0.0,
    }
    assert doc["network"]["ramps"][0] == {"name": "ramp a", "lc_keep_right": 0.0}
    # CRLF line endings survive
    crlf, _ = ap.edit_text(
        TINY.replace("\n", "\r\n"),
        name="tiny_dc",
        population="p.json",
        keep_right=1.0,
        xlsfg=False,
        expect_population=BASE_POP,
    )
    assert crlf.count("\r\n") == TINY.count("\n") and "  lc_keep_right: 1.0\r\n" in crlf


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("  lc_keep_right: 0.0\n  heavy", "  lc_keep_right: 1.0\n  heavy", "current setting"),
        ("  idm_calibration: artifacts/idm_i24_capacity.json", "  idm_calibration: x.json", "current setting"),
        ("seed: 42", "seed: 42\nname: again", "one top-level 'name:'"),
        ("fleet:\n", "fleetx:\n", "one top-level 'fleet:'"),
        ("  lc_strategic: 5.0\n", "  lc_strategic: 5.0\n  lc_keep_right: 0.0\n", "one 'fleet.lc_keep_right'"),
    ],
)  # fmt: skip
def test_a_source_the_recipe_does_not_fit_is_refused(old: str, new: str, match: str) -> None:
    text = TINY.replace(old, new, 1)
    assert text != TINY
    with pytest.raises(ValueError, match=match):
        ap.edit_text(
            text,
            name="t",
            population="p.json",
            keep_right=0.5,
            xlsfg=False,
            expect_population=BASE_POP,
        )


def test_the_xlsfg_lines_on_a_tiny_osm_scenario() -> None:
    new, counts = ap.edit_text(
        TINY_OSM,
        name="tinyosm_xlsfg_dc",
        population=BASE_POP,
        keep_right=0.0,
        xlsfg=True,
        expect_population=BASE_POP,
    )
    assert counts == {"weave_sections": 1, "scripted_merges": 2, "lane_end_giveup_m": 1}
    assert _changed_lines(TINY_OSM, new) == [
        ("name: tinyosm", "name: tinyosm_xlsfg_dc"),
        ("", "  lane_end_giveup_m: 7.5"),
        ("    merge_params: {}", "    merge_params: {force_guard: 1.0}"),
        ("      weave_params: {}", "      weave_params: {exit_prepare: 1.0}"),
        ("    merge_params: {}", "    merge_params: {force_guard: 1.0}"),
    ]
    doc = yaml.safe_load(new)
    assert [r["merge_params"] for r in doc["network"]["ramps"]] == [
        {"force_guard": 1.0},
        {},
        {},
        {"force_guard": 1.0},
    ]
    # the edit is the grid's data recipe
    raw = yaml.safe_load(TINY_OSM)
    want = g.pair_config(g.xlsfg_variant(raw), BASE_POP, 0.0)
    want["name"] = "tinyosm_xlsfg_dc"
    assert doc == want


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("  kind: osm", "  kind: straight", "not an OSM corridor"),
        ("    merge: scripted\n    merge_params: {}\nfleet", "    merge: scripted\n    merge_params: {accept_gap_s: 0.6}\nfleet", "expected 2 scripted merges"),
        ("  kind: osm\n", "  kind: osm\n  lane_end_giveup_m: 5.0\n", "lane_end_giveup_m already"),
    ],
)  # fmt: skip
def test_xlsfg_refuses_what_the_stage_would_not_do(old: str, new: str, match: str) -> None:
    text = TINY_OSM.replace(old, new, 1)
    assert text != TINY_OSM
    with pytest.raises(ValueError, match=match):
        ap.edit_text(
            text,
            name="t",
            population=BASE_POP,
            keep_right=0.0,
            xlsfg=True,
            expect_population=BASE_POP,
        )


def test_the_i94_edit_is_stage_p1_mndot_ref_byte_for_byte(tmp_path: Path) -> None:
    out = tmp_path / "weave_xlsfg.yaml"
    m = "mndot_i94_wb_stpaul"
    recipe = (  # scripts/gcp/pipeline_i24.sh stage p1_mndot_ref, verbatim but for the paths
        "sed -e 's#weave_params: {}#weave_params: {exit_prepare: 1.0}#' " + str(WEAVE) + " "
        "| awk '{print} /^  kind: osm$/ && !d {print \"  lane_end_giveup_m: 7.5\"; d=1}' "
        "| awk '/^    merge: scripted$/ {s=1; print; next} s && /^    merge_params: \\{\\}$/ "
        '{print "    merge_params: {force_guard: 1.0}"; s=0; next} {s=0; print}\' '
        f"| sed -e 's#^name: {m}_weave$#name: {m}_weave_xlsfg#' > " + str(out)
    )
    subprocess.run(["bash", "-c", recipe], check=True)
    ours, counts = ap.edit_text(
        WEAVE.read_text(),
        name=f"{m}_weave_xlsfg",
        population=BASE_POP,
        keep_right=0.0,
        xlsfg=True,
        expect_population=BASE_POP,
    )
    assert ours == out.read_text()
    assert counts == {"weave_sections": 2, "scripted_merges": 2, "lane_end_giveup_m": 1}
    p1 = json.loads(
        (REPO_ROOT / "artifacts" / "validation_mndot_i94_wb_stpaul_weave_xlsfg_p1.json").read_text()
    )
    # the p1 battery (2026-10-05) recorded its hash under config-hash policy 3
    h = config_hash_v3(ScenarioConfig.model_validate(yaml.safe_load(ours)))
    assert h == p1["config_hash"] == "b550b46fe751"


# --- the calibrated files ---------------------------------------------------------------------


def _body(text: str) -> str:
    """The file without the header this script prepends (its lines up to the source's)."""
    lines = text.splitlines(keepends=True)
    i = next(
        i
        for i, ln in enumerate(lines)
        if re.fullmatch(r"# config hash [0-9a-f]{12} \(policy v\d+\)\.\n", ln)
    )
    rest = lines[i + 1 :]
    if rest and rest[0].startswith("# The source's own header follows"):
        rest = rest[1:]
    return "".join(rest)


@pytest.mark.parametrize(
    ("corridor", "k", "kr"), [("i24", 0.5, 0.25), ("i94", 1.0, 1.0), ("i24", 0.0, 0.1)]
)
def test_explicit_values_give_the_grids_recipe_under_the_new_name(
    corridor: str, k: float, kr: float
) -> None:
    target = ap.TARGETS[corridor]
    res = ap.build(
        target, ap.explicit_choice(k, kr), source=target.source, name=None, date="2026-10-06"
    )
    src = (REPO_ROOT / target.source).read_text()
    raw = yaml.safe_load(src)
    ref = g.xlsfg_variant(raw) if target.xlsfg else raw
    pop = g.population_for(g.SPECS[corridor], k)
    want = g.pair_config(ref, pop, kr)
    want["name"] = f"{ref['name']}_dc"
    assert res.document == want == yaml.safe_load(res.text)
    assert res.population == pop
    if k == 0.0:
        assert pop == BASE_POP
    assert res.config_hash == config_hash(ScenarioConfig.model_validate(want))
    # labelled with today's policy (docs/CONTRACTS.md section 2), never a fixed one
    assert f"# config hash {res.config_hash} (policy v{CONFIG_HASH_VERSION})." in res.text
    assert ap.stated_hash_problem(res.text, res.document) is None
    assert "NOT the grid's choice" in res.text
    assert f"Source: {target.source} (sha256 {ap.sha256_of(target.source)})" in res.text
    body = _body(res.text)
    changed = _changed_lines(src, body)
    n_xlsfg = 5 if target.xlsfg else 0  # 2 weave, 2 merge, 1 inserted line
    # the name, each fleet line whose value moves, and the xlsfg lines; nothing else
    assert len(changed) == 1 + (k != 0.0) + (kr != 0.0) + n_xlsfg
    if corridor == "i24":
        assert body.startswith(src[: src.index("name: ")])  # the source's header kept


def test_the_names_and_files_are_the_studys() -> None:
    i24, i94 = ap.TARGETS["i24"], ap.TARGETS["i94"]
    assert i24.out == "scenarios/i24_replica_flow_speedcal_dc.yaml" and not i24.xlsfg
    assert i94.out == "scenarios/mndot_i94_wb_stpaul_weave_dc.yaml" and i94.xlsfg
    assert ap.default_name(I24_REF.read_text(), False) == "i24_replica_flow_speedcal_dc"
    assert ap.default_name(WEAVE.read_text(), True) == "mndot_i94_wb_stpaul_weave_xlsfg_dc"
    assert i24.source == g.SPECS["i24"].base_scenario


# --- from the grid's artifact -----------------------------------------------------------------


def _artifact(path: Path, corridor: str, chosen: tuple[float, float]) -> dict[str, Any]:
    """A driver-calibration artifact whose rule picks ``chosen`` (scores designed for it)."""
    scores = [
        GridScore(k, kr, 1.0 if (k, kr) == chosen else 3.0, 0.01 if (k, kr) == chosen else 0.1)
        for k, kr in grid_pairs()
    ]
    sel = select_pair(scores).to_dict()
    assert (sel["chosen"]["k"], sel["chosen"]["lc_keep_right"]) == chosen
    spec = g.SPECS[corridor]
    pops, measured = g.check_populations(spec, list(K_GRID))
    art = {
        "schema": g.SCHEMA,
        "corridor": corridor,
        "reference": {"scenario": spec.base_scenario, "transform": spec.transform},
        "grid": {"is_amendment_grid": True},
        "populations": pops,
        "measured_population": measured,
        "complete": True,
        "selection": sel,
    }
    path.write_text(json.dumps(art))
    return art


def test_from_the_artifact_written_then_checked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    art = tmp_path / "driver_calibration_i24.json"
    _artifact(art, "i24", (0.5, 0.25))
    out = tmp_path / "i24_dc.yaml"
    base = ["--corridor", "i24", "--artifact", str(art), "--out", str(out)]
    assert ap.main([*base, "--date", "2026-10-06"]) == 0
    text = out.read_text()
    doc = yaml.safe_load(text)
    assert doc["name"] == "i24_replica_flow_speedcal_dc"
    assert doc["fleet"]["idm_calibration"] == "artifacts/idm_i24_capacity_amax_k0.5.json"
    assert doc["fleet"]["lc_keep_right"] == 0.25
    assert f"(sha256 {ap.sha256_of(art)})" in text
    assert "selection.chosen k = 0.5, lc_keep_right = 0.25 (outcome rule_chose_other)" in text
    assert "Written 2026-10-06" in text
    capsys.readouterr()
    assert ap.main([*base, "--check"]) == 0
    assert "is the scenario for k = 0.5" in capsys.readouterr().out
    assert ap.main(base) == 2  # exists: --force overwrites
    assert ap.main([*base, "--force", "--date", "2026-10-07"]) == 0
    assert ap.main([*base, "--check"]) == 0  # the date is not part of the check
    _artifact(art, "i24", (1.0, 0.0))  # the grid chose otherwise: the file is stale
    assert ap.main([*base, "--check"]) == 1
    assert "not the scenario for k = 1" in capsys.readouterr().err
    assert ap.main(["--corridor", "i24", "--artifact", str(art), "--out", str(tmp_path / "no.yaml"), "--check"]) == 1  # fmt: skip


def test_the_current_setting_is_written_with_a_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    art = tmp_path / "a.json"
    _artifact(art, "i94", (0.0, 0.0))
    out = tmp_path / "i94_dc.yaml"
    assert ap.main(["--corridor", "i94", "--artifact", str(art), "--out", str(out)]) == 0
    assert "the chosen pair is the current setting" in capsys.readouterr().out
    doc = yaml.safe_load(out.read_text())
    assert doc["fleet"]["idm_calibration"] == BASE_POP and doc["fleet"]["lc_keep_right"] == 0.0
    ref = g.xlsfg_variant(yaml.safe_load(WEAVE.read_text()))
    assert {**ref, "name": doc["name"]} == doc


def _mutate(art: Path, fn: Any) -> None:
    d = json.loads(art.read_text())
    fn(d)
    art.write_text(json.dumps(d))


@pytest.mark.parametrize(
    ("mutation", "corridor", "match"),
    [
        (None, "i94", "is the 'i24' grid"),
        (lambda d: d.update(complete=False, selection=None), "i24", "incomplete"),
        (lambda d: d.update(schema="other/1"), "i24", "schema"),
        (lambda d: d["grid"].update(is_amendment_grid=False), "i24", "not the Amendment-1 grid"),
        (lambda d: d["reference"].update(scenario="scenarios/other.yaml"), "i24", "reference"),
        (lambda d: d["populations"]["0.5"].update(sha256="0" * 64), "i24", "changed since the grid ran"),
        (lambda d: d["selection"]["chosen"].update(k=0.3), "i24", "not on the Amendment-1 grid"),
    ],
)  # fmt: skip
def test_an_unusable_artifact_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mutation: Any, corridor: str, match: str
) -> None:
    art = tmp_path / "a.json"
    _artifact(art, "i24", (0.5, 0.25))
    if mutation is not None:
        _mutate(art, mutation)
    out = tmp_path / "x.yaml"
    assert ap.main(["--corridor", corridor, "--artifact", str(art), "--out", str(out)]) == 2
    assert match in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize(
    ("argv", "match"),
    [
        (["--k", "0.3", "--keep-right", "0.0"], "not on the Amendment-1 grid"),
        (["--k", "0.5", "--keep-right", "0.3"], "not on the Amendment-1 grid"),
        (["--k", "0.5"], "both --k and --keep-right"),
        (["--k", "0.5", "--keep-right", "0.0", "--artifact", "a.json"], "not both"),
        (
            ["--k", "0.5", "--keep-right", "0.0", "--source", "scenarios/x.yaml"],
            "--source needs --out",
        ),
    ],
)
def test_bad_explicit_arguments_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: list[str], match: str
) -> None:
    extra = [] if "--source" in argv else ["--out", str(tmp_path / "x.yaml")]
    assert ap.main(["--corridor", "i24", *argv, *extra]) == 2
    assert match in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_another_source_for_the_demand_refit(tmp_path: Path) -> None:
    """The opt-in refit stage's base: the corrected profile with the calibrated drivers."""
    src = "scenarios/i24_replica_flow_corrected.yaml"
    out = tmp_path / "corrected_dc.yaml"
    argv = ["--corridor", "i24", "--k", "0.75", "--keep-right", "0.5", "--source", src, "--out", str(out)]  # fmt: skip
    assert ap.main(argv) == 0
    doc = yaml.safe_load(out.read_text())
    raw = copy.deepcopy(yaml.safe_load((REPO_ROOT / src).read_text()))
    want = g.pair_config(raw, "artifacts/idm_i24_capacity_amax_k0.75.json", 0.5)
    want["name"] = "i24_replica_flow_corrected_dc"
    assert doc == want


# --- config-hash policy 4 (docs/CONTRACTS.md section 2) ---------------------------------------


@pytest.mark.parametrize(
    "rel",
    ["scenarios/mndot_i94_wb_stpaul_weave_dc.yaml", "scenarios/i24_replica_flow_speedcal_dc.yaml"],
)
def test_a_committed_header_is_read_under_the_policy_it_names(rel: str) -> None:
    """The committed _dc files were written under policy 3 and say so; they are not rewritten."""
    text = (REPO_ROOT / rel).read_text()
    doc = yaml.safe_load(text)
    assert f"# config hash {config_hash_v3(doc)} (policy v3)." in text
    assert ap.stated_hash_problem(text, doc) is None


def test_a_mislabelled_or_foreign_header_hash_is_named() -> None:
    text = (REPO_ROOT / "scenarios" / "mndot_i94_wb_stpaul_weave_dc.yaml").read_text()
    doc = yaml.safe_load(text)
    v3 = config_hash_v3(doc)
    relabelled = text.replace(f"{v3} (policy v3).", f"{v3} (policy v{CONFIG_HASH_VERSION}).")
    problem = ap.stated_hash_problem(relabelled, doc)
    assert problem is not None and f"policy v{CONFIG_HASH_VERSION}" in problem
    assert "does not reproduce" in str(
        ap.stated_hash_problem(text.replace("(policy v3).", "(policy v1)."), doc)
    )
    assert "no config hash" in str(ap.stated_hash_problem("name: x\n", doc))
