"""D10's two arms against their base, line by line and value by value (docs/PRE_FRISCO_PROGRAM.md, D10).

usage (repository root):
  scenario_diff.py [--out artifacts/i94_d10_2026-10-07/scenario_diff.json]

Reads only committed files, writes the JSON proof and prints its summary; no simulation, no netconvert.

* **Text.** The base (``scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2.yaml``) and each arm
  (``…_rb.yaml``, ``…_rbc.yaml``) without their header comments have the same number of lines; every line
  that differs is mapped to the YAML value it holds (``yaml.compose`` marks) and must be the ``name`` or a
  value of a series the arm's rule changes (``EXPECTED``).
* **Values.** Every leaf of the two documents is compared; the differing leaves must lie in the same set.
* **Figures.** Per changed series, the base's and the arm's 4-h (05:30-09:30) means and hourly means from
  06:30 / 07:30 / 08:30: an entrance's inflow in veh/h straight from the scenario; an exit's mean fraction
  from the scenario and its volume in the plan's free flow (``scripts/i94_calibration_days.py``'s
  ``plan_volumes`` on the demand records: ``artifacts/demand_mndot_i94_wb_stpaul_cal.json`` is the base's
  series, ``…_cal_rb/_rbc.json`` the arms'). Rule (b)'s evidence recomputed here from the calibration-day
  targets and the data-quality artifact: T.H.61 NB's segment residual (§3 of docs/I94_CALIBRATION_DAYS.md
  quotes -317 against +-261 veh/h) and its sign per calibration day; rule (c)'s, S791 - S792.

Exit status 0 when both arms differ from the base in exactly the expected places, 1 otherwise.
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import yaml

REPO = Path(__file__).resolve().parents[3]
BASE = "scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2.yaml"
BASE_RECORD = "artifacts/demand_mndot_i94_wb_stpaul_cal.json"
OBSERVATIONS = "artifacts/p1_rehearsal_2026-10-04/observations_calibration.json"
DATA_QUALITY = "artifacts/p1_rehearsal_2026-10-04/dq/data_quality.json"
CAL_DATES = ("2026-09-02", "2026-09-03", "2026-09-08", "2026-09-15", "2026-09-16")
OUT = "artifacts/i94_d10_2026-10-07/scenario_diff.json"
#: What each rule may change, and must (D10: (b) T.H.61 NB from the mainline difference; (c) S792 out of
#: the balance, which merges the brackets of the Mounds Blvd and 6th St exits).
TH61 = ("ramp", "on-ramp 53062592", "inflow")
MOUNDS = ("ramp", "off-ramp 18207912", "exit_fraction")
SIXTH = ("ramp", "off-ramp 42165869", "exit_fraction")
EXPECTED: dict[str, dict[str, Any]] = {
    "rb": {
        "scenario": "scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2_rb.yaml",
        "record": "artifacts/demand_mndot_i94_wb_stpaul_cal_rb.json",
        "series": [TH61],
    },
    "rbc": {
        "scenario": "scenarios/mndot_i94_wb_stpaul_weave_dc_cal_w1b_w2_rbc.yaml",
        "record": "artifacts/demand_mndot_i94_wb_stpaul_cal_rbc.json",
        "series": [TH61, MOUNDS, SIXTH],
    },
}
HOURS = ("06:30", "07:30", "08:30", "05:30-09:30")


def _scripts() -> ModuleType:
    """scripts/i94_calibration_days.py (its plan_volumes and hours)."""
    name = "i94_calibration_days"
    if name in sys.modules:
        return sys.modules[name]
    sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def body(rel: str) -> str:
    """A scenario file without its header comment lines."""
    return "".join(
        ln for ln in (REPO / rel).read_text().splitlines(keepends=True) if not ln.startswith("#")
    )


def _ramp_key(node: yaml.Node) -> str | None:
    """The ``name`` of a ramp mapping node."""
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            if k.value == "name" and isinstance(v, yaml.ScalarNode):
                return str(v.value)
    return None


def line_paths(text: str) -> dict[int, tuple[str, ...]]:
    """0-based line -> the path of the scalar written on it (a series row's path stops at the series).

    Paths read ``("name",)``, ``("ramp", <ramp name>, <key>)`` for anything inside a ramp, and the plain
    key chain elsewhere; a series value keeps only the series' path (its row index dropped), so a changed
    value maps to the series it belongs to.
    """
    out: dict[int, tuple[str, ...]] = {}

    def walk(node: yaml.Node, path: tuple[str, ...]) -> None:
        if isinstance(node, yaml.ScalarNode):
            out.setdefault(node.start_mark.line, path)
        elif isinstance(node, yaml.SequenceNode):
            for item in node.value:
                if path == ("network", "ramps"):
                    walk(item, ("ramp", _ramp_key(item) or "?"))
                else:
                    walk(item, path)
        elif isinstance(node, yaml.MappingNode):
            for k, v in node.value:
                walk(v, (*path, str(k.value)))

    root = yaml.compose(text)
    assert root is not None
    walk(root, ())
    return out


def _series_root(path: tuple[str, ...]) -> tuple[str, ...]:
    """A ramp leaf's path cut to (``ramp``, name, key); other paths as they are."""
    return path[:3] if path[:1] == ("ramp",) else path


def leaves(doc: Any, path: tuple[str, ...] = ()) -> dict[tuple[str, ...], Any]:
    """Every scalar of a document by path (ramps keyed by name, list items by index)."""
    out: dict[tuple[str, ...], Any] = {}
    if isinstance(doc, dict):
        for k, v in doc.items():
            out.update(leaves(v, (*path, str(k))))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            if path == ("network", "ramps") and isinstance(v, dict):
                out.update(leaves(v, ("ramp", str(v["name"]))))
            else:
                out.update(leaves(v, (*path, str(i))))
    else:
        out[path] = doc
    return out


def text_diff(base_text: str, arm_text: str) -> dict[str, Any]:
    """The differing body lines of an arm, each mapped to the series (or key) it belongs to.

    The arm must have the base's line count (a value changed in place moves no line); lines are then
    compared one for one. A different count is reported as structural (with difflib's opcodes) and fails.
    """
    a, b = base_text.splitlines(), arm_text.splitlines()
    paths = line_paths(arm_text)
    structural: list[dict[str, Any]] = []
    if len(a) == len(b):
        changed = [i for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]
    else:
        changed = []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
            if tag != "equal":
                structural.append(
                    {"tag": tag, "base_lines": [i1 + 1, i2], "arm_lines": [j1 + 1, j2]}
                )
    where: dict[str, int] = {}
    for ln in changed:
        key = " / ".join(_series_root(paths.get(ln, ("<no scalar>",))))
        where[key] = where.get(key, 0) + 1
    return {
        "n_lines_base": len(a),
        "n_lines_arm": len(b),
        "n_changed_lines": len(changed),
        "structural_changes": structural,
        "changed_lines_by_path": where,
    }


def _hours(values: list[float], digits: int = 1) -> list[float]:
    arr = np.asarray(values, dtype=float)
    return [round(float(np.nanmean(arr[i : i + 12])), digits) for i in (12, 24, 36)] + [
        round(float(np.nanmean(arr)), digits)
    ]


def series_figures(
    base: dict[str, Any], arm: dict[str, Any], key: tuple[str, ...]
) -> dict[str, Any]:
    """Hourly and 4-h means of one series in the two scenarios, as the files hold them."""
    name, field = key[1], key[2]
    rb = next(r for r in base["network"]["ramps"] if r["name"] == name)
    ra = next(r for r in arm["network"]["ramps"] if r["name"] == name)
    scale = 3600.0 if field == "inflow" else 1.0
    unit = "veh/h" if field == "inflow" else "exit fraction"
    before = [v * scale for _, v in rb[field]]
    after = [v * scale for _, v in ra[field]]
    return {
        "unit": unit,
        "hours": list(HOURS),
        "base": _hours(before, 1 if field == "inflow" else 4),
        "arm": _hours(after, 1 if field == "inflow" else 4),
        "n_steps": len(after),
        "n_steps_changed": int(sum(1 for x, y in zip(before, after, strict=True) if x != y)),
    }


def plan_figures(
    record_base: dict[str, Any], record_arm: dict[str, Any], names: list[str]
) -> dict[str, Any]:
    """Each ramp's volume in the plan's free flow [veh/h] (exits: fraction x the flow arriving in the plan)."""
    cd = _scripts()
    obs = cd.Observations.from_json(REPO / OBSERVATIONS)
    station_x = {s.id: float(s.x_m or 0.0) for s in obs.stations if s.kind == "mainline"}
    rb, _ = cd.plan_volumes(record_base, station_x)
    ra, _ = cd.plan_volumes(record_arm, station_x)
    return {
        n: {
            "hours": list(HOURS),
            "base": [round(v, 1) for v in cd.hours(rb[n])],
            "arm": [round(v, 1) for v in cd.hours(ra[n])],
        }
        for n in names
    }


def evidence() -> dict[str, Any]:
    """Rule (b)'s and (c)'s figures, recomputed from the targets and the data-quality artifact."""
    obs = json.loads((REPO / OBSERVATIONS).read_text())
    dq = json.loads((REPO / DATA_QUALITY).read_text())

    def flow(sid: str) -> np.ndarray:
        return np.array(
            [np.nan if v is None else float(v) for v in obs["flows_veh_h"][sid]], dtype=float
        )

    up, down, det = flow("S1069"), flow("S1070"), flow("rnd_88807")
    means = {
        k: float(np.nanmean(v)) for k, v in (("S1069", up), ("S1070", down), ("rnd_88807", det))
    }
    ce = float(dq["parameters"]["count_error"])
    band = ce * math.sqrt(sum(m**2 for m in means.values()))
    days = {
        r["date"]: r["residual_veh_h"]
        for r in dq["mass_balance"]["segment_days"]
        if (r["upstream"], r["downstream"]) == ("S1069", "S1070") and r["date"] in CAL_DATES
    }
    gap = flow("S791") - flow("S792")
    return {
        "th61": {
            "segment": "S1069->S1070",
            "means_4h_veh_h": {k: round(v, 1) for k, v in means.items()},
            "residual_4h_mean_veh_h": round(float(np.nanmean(down - up - det)), 1),
            "residual_of_rounded_means_veh_h": round(
                round(means["S1070"] - means["S1069"]) - round(means["rnd_88807"])
            ),
            "quadrature_band_veh_h": round(band, 1),
            "count_error": ce,
            "quoted_in_section_3": {"residual_veh_h": -317, "band_veh_h": 261},
            "day_residuals_veh_h": [days[d] for d in CAL_DATES],
            "same_sign_every_calibration_day": all(days[d] < 0 for d in CAL_DATES),
        },
        "s792": {"s791_minus_s792_veh_h": dict(zip(HOURS, _hours(list(gap)), strict=True))},
    }


def compare(label: str) -> dict[str, Any]:
    spec = EXPECTED[label]
    base_text, arm_text = body(BASE), body(spec["scenario"])
    base, arm = yaml.safe_load(base_text), yaml.safe_load(arm_text)
    allowed = {("name",), *(tuple(s) for s in spec["series"])}
    td = text_diff(base_text, arm_text)
    la, lb = leaves(base), leaves(arm)
    differing = sorted(
        {
            _series_root(p)
            for p in set(la) | set(lb)
            if la.get(p, "<absent>") != lb.get(p, "<absent>")
        }
    )
    text_paths = {tuple(k.split(" / ")) for k in td["changed_lines_by_path"]}
    ok = (
        not td["structural_changes"]
        and text_paths == allowed
        and set(differing) == allowed
        and arm["name"] == f"{base['name']}_{label}"
    )
    rec_base = json.loads((REPO / BASE_RECORD).read_text())
    rec_arm = json.loads((REPO / spec["record"]).read_text())
    return {
        "scenario": spec["scenario"],
        "base": BASE,
        "name": arm["name"],
        "expected": [" / ".join(s) for s in sorted(allowed)],
        "text": td,
        "values_differing": [" / ".join(p) for p in differing],
        "only_expected_differ": ok,
        "series": {" / ".join(s): series_figures(base, arm, tuple(s)) for s in spec["series"]},
        "plan_free_flow_veh_h": plan_figures(rec_base, rec_arm, [s[1] for s in spec["series"]]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    ap.add_argument("--out", type=Path, default=REPO / OUT)
    args = ap.parse_args(argv)
    doc: dict[str, Any] = {
        "schema": "flowstate.d10_scenario_diff/1",
        "spec": "docs/PRE_FRISCO_PROGRAM.md, D10; docs/I94_CALIBRATION_DAYS.md sections 3 and 4",
        "arms": {label: compare(label) for label in EXPECTED},
        "evidence": evidence(),
    }
    out = args.out if args.out.is_absolute() else REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1) + "\n")
    for label, r in doc["arms"].items():
        print(f"{label}: {r['name']}  only the expected places differ: {r['only_expected_differ']}")
        print(
            f"  body lines {r['text']['n_lines_base']} / {r['text']['n_lines_arm']}, changed {r['text']['n_changed_lines']}:"
        )
        for k, n in r["text"]["changed_lines_by_path"].items():
            print(f"    {n:3d}  {k}")
        for k, f in r["series"].items():
            print(
                f"  {k} ({f['unit']}, {' / '.join(HOURS)}): {f['base']} -> {f['arm']}  ({f['n_steps_changed']} of {f['n_steps']} steps)"
            )
        for k, f in r["plan_free_flow_veh_h"].items():
            print(f"  {k} in the plan's free flow, veh/h: {f['base']} -> {f['arm']}")
    ev = doc["evidence"]["th61"]
    print(
        f"T.H.61 NB: residual {ev['residual_4h_mean_veh_h']:+.1f} veh/h (of the rounded means "
        f"{ev['residual_of_rounded_means_veh_h']:+d}; section 3: -317) against +-{ev['quadrature_band_veh_h']:.1f} "
        f"(section 3: +-261); per day {ev['day_residuals_veh_h']}"
    )
    print(f"S791 - S792: {doc['evidence']['s792']['s791_minus_s792_veh_h']}")
    return 0 if all(r["only_expected_differ"] for r in doc["arms"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
