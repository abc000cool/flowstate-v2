"""Weave rules W1b and W2 in every battery and report (Amendment 4, 2026-10-07).

docs/FRISCO_PROTOCOL.md Amendment 4 (decision A2 of docs/DECISIONS_2026-10-07.md)
puts weave rules W1b and W2 into the model at every weaving section and asks
every battery and report to state each weave's W1b releases as a share of its
entrance's departures, pooled over the seeds, beside ``no_locks``, with W2's
counters — a share above 1 % flagged in the limitations, not a gate failure.

No SUMO here: hand-written ``meta.json`` blocks shaped as the runner writes
them (``weave_sections[i]`` with ``params``, ``n_entrant_took_exit`` and W2's
counters; ``ramps[k].n_departed``), fed to
:func:`validation.battery.weave_release_summary`,
:func:`validation.criteria.evaluate`, the corridor battery's
``build_artifact`` and :func:`validation.report.generate_report`.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from flowstate_core.config import WEAVE_AMENDMENT4_OFF, WEAVE_DEFAULTS, WEAVE_W2_SWITCHES
from tests.test_validation import test_validation_corridor_battery_collisions as col
from tests.test_validation.test_validation_report import _limitations, _section, _write_run
from validation.battery import (
    WEAVE_RELEASE_DEFINITION,
    json_safe,
    weave_release_rows,
    weave_release_summary,
)
from validation.criteria import (
    NO_LOCKS,
    REPORTED,
    W1B_RELEASE_SHARE,
    W1B_RELEASE_SHARE_FLAG,
    CriteriaResult,
    evaluate,
)
from validation.report import generate_report

#: Everything off but W1b (p9's and p10's arm A).
W1B_ONLY = {**WEAVE_DEFAULTS, **dict.fromkeys(WEAVE_W2_SWITCHES, 0.0)}
#: The pre-amendment weave (every result published before 2026-10-07).
ALL_OFF = {**WEAVE_DEFAULTS, **WEAVE_AMENDMENT4_OFF}


def _w2(handback: int, close: int, deferred: int, vetoed: int) -> dict[str, int]:
    return {
        "n_handback_skips": handback,
        "n_close_leader_withheld": close,
        "n_opposing_deferred": deferred,
        "n_opposing_vetoed": vetoed,
    }


def _section_entry(
    ramp: str,
    exit_name: str,
    *,
    releases: int | None,
    params: dict[str, float] | None,
    w2: dict[str, int] | None = None,
) -> dict[str, Any]:
    """A ``meta.json["weave_sections"][i]`` entry as the runner writes it: the
    W1 counter only while W1 is on, each W2 counter only while its switch is."""
    entry: dict[str, Any] = {
        "ramp": ramp,
        "exit": exit_name,
        "n_missed": (releases or 0) + 1,
        "n_missed_exit": 1,
        "n_reached_section_exiting": 100,
    }
    if params is not None:
        entry["params"] = dict(params)
    if releases is not None:
        entry["n_entrant_took_exit"] = releases
    entry.update(w2 or {})
    return entry


def _meta(seed: int, sections: list[dict[str, Any]], departed: dict[str, int]) -> dict[str, Any]:
    """One run: its weave sections and its ramps (on-ramps named by ``departed``;
    a key ``"@e7"`` is an unnamed on-ramp attached to edge ``e7``)."""
    ramps: list[dict[str, Any]] = []
    for index, (name, n) in enumerate(departed.items()):
        unnamed = name.startswith("@")
        ramps.append(
            {
                "index": index,
                "name": "" if unnamed else name,
                "kind": "on",
                "attach_edge": name[1:] if unnamed else f"edge{index}",
                "n_planned": n + 5,
                "n_departed": n,
            }
        )
    ramps.append({"index": len(ramps), "name": "A-OFF", "kind": "off", "attach_edge": "x"})
    return {
        "seed": seed,
        "n_vehicles_planned": 1000,
        "n_vehicles_departed": 1000,
        "n_collisions": 0,
        "collisions": [],
        "weave_sections": sections,
        "ramps": ramps,
    }


def _two_runs() -> list[dict[str, Any]]:
    """A: 3 + 1 releases of 200 + 200 departures (1.0 %, at the bound: not flagged);
    B: 6 + 4 of 300 + 200 (2.0 %, flagged); both with every rule on."""
    return [
        _meta(
            101,
            [
                _section_entry(
                    "A-ON", "A-OFF", releases=3, params=WEAVE_DEFAULTS, w2=_w2(10, 2, 30, 20)
                ),
                _section_entry(
                    "B-ON", "B-OFF", releases=6, params=WEAVE_DEFAULTS, w2=_w2(5, 0, 7, 1)
                ),
            ],
            {"A-ON": 200, "B-ON": 300},
        ),
        _meta(
            102,
            [
                _section_entry(
                    "A-ON", "A-OFF", releases=1, params=WEAVE_DEFAULTS, w2=_w2(1, 1, 3, 2)
                ),
                _section_entry(
                    "B-ON", "B-OFF", releases=4, params=WEAVE_DEFAULTS, w2=_w2(0, 0, 0, 0)
                ),
            ],
            {"A-ON": 200, "B-ON": 200},
        ),
    ]


class TestRows:
    def test_one_run_per_section(self) -> None:
        (a, b) = weave_release_rows(_two_runs()[0])  # type: ignore[misc]
        assert a == {
            "ramp": "A-ON",
            "exit": "A-OFF",
            "w1b_on": True,
            "w1b_releases": 3,
            "entrance_departed": 200,
            "w2_on": {
                "weave_handback": True,
                "weave_close_leader": True,
                "weave_resolve_opposing": True,
            },
            **_w2(10, 2, 30, 20),
        }
        assert b["w1b_releases"] == 6 and b["entrance_departed"] == 300

    def test_a_run_without_sections_says_nothing(self) -> None:
        assert weave_release_rows({"seed": 1}) is None
        assert weave_release_rows({"weave_sections": []}) is None

    def test_rules_off_are_read_from_the_params(self) -> None:
        meta = _meta(
            1,
            [
                _section_entry("A-ON", "A-OFF", releases=None, params=ALL_OFF),
                _section_entry("B-ON", "B-OFF", releases=2, params=W1B_ONLY),
            ],
            {"A-ON": 50, "B-ON": 60},
        )
        a, b = weave_release_rows(meta)  # type: ignore[misc]
        assert a["w1b_on"] is False and a["w1b_releases"] is None
        assert not any(a["w2_on"].values())
        assert all(a[k] is None for k in _w2(0, 0, 0, 0))
        assert b["w1b_on"] is True and b["w1b_releases"] == 2
        assert not any(b["w2_on"].values())
        # W1 at once (dwell 0) is not W1b
        w1 = {**ALL_OFF, "entrant_giveup_m": 5.0}
        (c,) = weave_release_rows(
            _meta(1, [_section_entry("A-ON", "A-OFF", releases=4, params=w1)], {"A-ON": 9})
        )  # type: ignore[misc]
        assert c["w1b_on"] is False and c["w1b_releases"] == 4

    def test_a_meta_without_params_is_read_from_its_counters(self) -> None:
        on = _section_entry("A-ON", "A-OFF", releases=0, params=None, w2=_w2(1, 2, 3, 4))
        off = _section_entry("B-ON", "B-OFF", releases=None, params=None)
        rows = weave_release_rows(_meta(1, [on, off], {"A-ON": 10, "B-ON": 10}))
        assert rows is not None
        assert rows[0]["w1b_on"] is True and all(rows[0]["w2_on"].values())
        assert rows[1]["w1b_on"] is False and not any(rows[1]["w2_on"].values())

    def test_an_unnamed_entrance_is_matched_by_its_attach_edge(self) -> None:
        meta = _meta(
            1,
            [_section_entry("e7", "x", releases=1, params=WEAVE_DEFAULTS)],
            {"@e7": 80, "OTHER": 5},
        )
        (row,) = weave_release_rows(meta)  # type: ignore[misc]
        assert row["entrance_departed"] == 80
        # an entrance the meta does not list: departures not recorded
        meta["ramps"] = []
        (row,) = weave_release_rows(meta)  # type: ignore[misc]
        assert row["entrance_departed"] is None


class TestSummary:
    def test_two_sections_over_two_runs(self) -> None:
        summary = weave_release_summary(_two_runs())
        assert summary["flag_share"] == W1B_RELEASE_SHARE_FLAG == 0.01
        assert summary["n_runs"] == 2
        assert summary["definition"] == WEAVE_RELEASE_DEFINITION
        a, b = summary["sections"]
        assert a == {
            "ramp": "A-ON",
            "exit": "A-OFF",
            "n_runs": 2,
            "n_runs_w1b": 2,
            "n_runs_w1b_off": 0,
            "w1b_releases": 4,
            "entrance_departed": 400,
            "share": pytest.approx(0.01),
            "flagged": False,  # strictly above the bound only
            "n_runs_w2": 2,
            "n_runs_w2_off": 0,
            "w2": _w2(11, 3, 33, 22),
        }
        assert b["w1b_releases"] == 10 and b["entrance_departed"] == 500
        assert b["share"] == pytest.approx(0.02) and b["flagged"] is True
        assert b["w2"] == _w2(5, 0, 7, 1)
        assert summary["flagged"] == ["B-ON"]

    def test_runs_with_a_rule_off_are_counted_apart(self) -> None:
        metas = _two_runs()
        metas[1]["weave_sections"][0] = _section_entry(
            "A-ON", "A-OFF", releases=None, params=ALL_OFF
        )
        a = weave_release_summary(metas)["sections"][0]
        assert a["n_runs"] == 2 and a["n_runs_w1b"] == 1 and a["n_runs_w1b_off"] == 1
        assert a["w1b_releases"] == 3 and a["entrance_departed"] == 200
        assert a["share"] == pytest.approx(0.015) and a["flagged"] is True
        assert a["n_runs_w2"] == 1 and a["n_runs_w2_off"] == 1
        assert a["w2"] == _w2(10, 2, 30, 20)  # the run with W2 off adds nothing

    def test_nothing_on_is_an_undefined_share_not_a_zero(self) -> None:
        metas = [
            _meta(1, [_section_entry("A-ON", "A-OFF", releases=None, params=ALL_OFF)], {"A-ON": 9})
        ]
        (a,) = weave_release_summary(metas)["sections"]
        assert a["n_runs_w1b"] == 0 and a["w1b_releases"] == 0
        assert math.isnan(a["share"]) and a["flagged"] is False
        assert a["w2"] == dict.fromkeys(_w2(0, 0, 0, 0))
        # W1b on but nothing departed from the entrance
        metas = [
            _meta(
                1, [_section_entry("A-ON", "A-OFF", releases=0, params=WEAVE_DEFAULTS)], {"A-ON": 0}
            )
        ]
        (a,) = weave_release_summary(metas)["sections"]
        assert a["n_runs_w1b"] == 1 and math.isnan(a["share"])

    def test_runs_without_sections_say_nothing(self) -> None:
        summary = weave_release_summary([{"seed": 1}, {"weave_sections": []}])
        assert summary["sections"] == [] and summary["n_runs"] == 0
        assert summary["flagged"] == []
        # measured merge zones do not run W1b and are not read
        measured = {"measured_merges": [{"kind": "weave", "ramp": "M", "n_missed": 3}]}
        assert weave_release_summary([measured])["sections"] == []

    def test_a_custom_bound_is_honoured_and_recorded(self) -> None:
        summary = weave_release_summary(_two_runs(), flag_share=0.025)
        assert summary["flag_share"] == 0.025 and summary["flagged"] == []

    def test_the_summary_is_json_serialisable(self) -> None:
        text = json.dumps(json_safe(weave_release_summary(_two_runs())), allow_nan=False)
        assert json.loads(text)["sections"][1]["flagged"] is True


class TestCriteria:
    def test_reported_rows_follow_no_locks_one_per_section(self) -> None:
        rows = evaluate(weave_releases=weave_release_summary(_two_runs()))
        names = [r.name for r in rows]
        assert names[-3:] == [
            NO_LOCKS,
            f"{W1B_RELEASE_SHARE} (A-ON)",
            f"{W1B_RELEASE_SHARE} (B-ON)",
        ]
        a, b = rows[-2:]
        for row in (a, b):
            assert row.status == "REPORTED" and row.detail.startswith(REPORTED)
            assert row.evaluated is True and row.passed is True
            assert "not gating" in row.threshold and "above 1% flagged" in row.threshold
            assert "not an FHWA or DOT criterion" in row.detail
        assert a.value == pytest.approx(0.01)
        assert "4 of 400 entrance departures released into the paired exit by W1b over 2 " in (
            a.detail
        )
        assert "(1.00 %), within the 1% design bound" in a.detail
        assert (
            "W2 over the runs it was on in: handback skips 11, close-leader withholds 3, "
            "opposing deferrals 33, of them vetoes 22" in a.detail
        )
        assert b.value == pytest.approx(0.02)
        assert "ABOVE the 1% design bound (flagged in Limitations)" in b.detail

    def test_a_section_with_w1b_off_is_reported_not_counted(self) -> None:
        metas = [
            _meta(1, [_section_entry("A-ON", "A-OFF", releases=None, params=ALL_OFF)], {"A-ON": 9})
        ]
        (row,) = [
            r
            for r in evaluate(weave_releases=weave_release_summary(metas))
            if r.name.startswith(W1B_RELEASE_SHARE)
        ]
        assert row.status == "REPORTED" and row.value is None
        assert row.evaluated is False and row.passed is False
        assert "no run with W1b on records its releases" in row.detail
        assert "W1b off in 1 of 1 run(s) (an opt-out" in row.detail
        assert "W2 off (any switch) in 1 of 1 run(s)" in row.detail
        assert "handback skips not recorded" in row.detail

    def test_without_the_summary_or_sections_no_row_is_written(self) -> None:
        plain = evaluate(collision_counts=[0])
        assert plain[-1].name == NO_LOCKS
        empty = evaluate(collision_counts=[0], weave_releases=weave_release_summary([{}]))
        assert [r.name for r in empty] == [r.name for r in plain]

    def test_the_reported_status_is_neither_a_pass_nor_a_fail(self) -> None:
        row = CriteriaResult("x", 0.5, "t", passed=True, evaluated=True, detail=f"{REPORTED}: x")
        assert row.status == "REPORTED"
        assert CriteriaResult("x", 0.5, "t", passed=True, evaluated=True).status == "PASS"


class TestBattery:
    @staticmethod
    def _metas(with_weaves: bool) -> list[dict[str, Any]]:
        metas = col._metas()
        runs = _two_runs()
        for i, meta in enumerate(metas):
            if with_weaves:
                src = runs[min(i, 1)]
                meta["weave_sections"] = src["weave_sections"]
                meta["ramps"] = src["ramps"]
        return metas

    def test_per_seed_rows_and_the_pooled_block(self, tmp_path: Path) -> None:
        battery = col._load_script()
        artifact = col._strict(col._build(tmp_path, self._metas(True)))
        keys = list(artifact)
        assert keys.index("weave_releases") == keys.index("zero_locks") + 1
        block = artifact["weave_releases"]
        assert block["n_runs"] == 3
        a, b = block["sections"]
        # seeds 101, 102, 103 carry runs 1, 2, 2 of _two_runs()
        assert a["w1b_releases"] == 3 + 1 + 1 and a["entrance_departed"] == 600
        assert b["w1b_releases"] == 6 + 4 + 4 and b["entrance_departed"] == 700
        assert b["flagged"] is True and block["flagged"] == ["B-ON"]
        rows = [row["weave_releases"] for row in artifact["per_seed"]]
        assert [r[0]["w1b_releases"] for r in rows] == [3, 1, 1]
        assert rows[0][0]["entrance_departed"] == 200
        lines = [battery.weave_release_line(s, block["flag_share"]) for s in block["sections"]]
        assert lines[0].strip().startswith("weave releases")
        assert (
            "A-ON: W1b 5 of 600 entrance departures (0.83 %, within the 1 % bound, 3 run(s))"
            in (lines[0])
        )
        assert "W2 handback 12, close-leader 4, opposing 36, vetoed 24" in lines[0]
        assert "B-ON: W1b 14 of 700 entrance departures (2.00 %, ABOVE the 1 % bound" in lines[1]

    def test_the_keys_are_additive_and_every_existing_value_is_unchanged(
        self, tmp_path: Path
    ) -> None:
        """A battery whose seeds list weaving sections differs from the same
        battery without them only by the new keys: top-level ``weave_releases``
        and each seed's ``weave_releases`` (null without sections or metas);
        ``notes`` included, every other key is byte-identical."""
        without = col._strict(col._build(tmp_path / "plain", self._metas(False)))
        with_weaves = col._strict(col._build(tmp_path / "weave", self._metas(True)))
        no_metas = col._strict(col._build(tmp_path / "none", None))
        assert without["weave_releases"] is None and no_metas["weave_releases"] is None
        assert [r["weave_releases"] for r in without["per_seed"]] == [None, None, None]
        assert [r["weave_releases"] for r in no_metas["per_seed"]] == [None, None, None]
        assert set(with_weaves) == set(without) == set(no_metas)
        for key in set(without) - {"weave_releases", "per_seed"}:
            assert json.dumps(with_weaves[key]) == json.dumps(without[key]), key
        for a, b in zip(without["per_seed"], with_weaves["per_seed"], strict=True):
            assert set(a) == set(b)
            for key in set(a) - {"weave_releases"}:
                assert json.dumps(a[key]) == json.dumps(b[key]), key
        assert with_weaves["schema"] == "flowstate.corridor_validation/1"


class TestReport:
    @staticmethod
    def _run_set(root: Path, metas: list[dict[str, Any]]) -> Path:
        for seed, extra in enumerate(metas, start=1):
            run_dir = _write_run(root / "cafe01234567" / str(seed), seed=seed)
            meta = json.loads((run_dir / "meta.json").read_text())
            meta.update({k: v for k, v in extra.items() if k != "seed"})
            (run_dir / "meta.json").write_text(json.dumps(meta))
        return root

    @staticmethod
    def _report(root: Path, tmp_path: Path) -> str:
        out = tmp_path / "report" / "report.md"
        generate_report(root, out)
        return out.read_text()

    def test_lines_rows_and_the_flagged_limitation(self, tmp_path: Path) -> None:
        text = self._report(self._run_set(tmp_path / "runs", _two_runs()), tmp_path)
        section = _section(text, "## Model integrity")
        assert (
            "- Weave rules at A-ON (exit A-OFF): W1b released 4 of 400 entrance departures "
            "(1 %) over 2 run(s), within the 1 % design bound; W2 (2 of 2 run(s) with all three "
            "guards on): handback skips 11, close-leader withholds 3, opposing deferrals 33, "
            "of them vetoes 22." in section
        )
        assert "- Weave rules at B-ON (exit B-OFF): W1b released 10 of 500 entrance " in section
        assert "above the 1 % design bound" in section
        criteria = _section(text, "## Acceptance criteria")
        row_a = next(
            ln for ln in criteria.splitlines() if ln.startswith("| w1b_release_share (A-ON) |")
        )
        assert "| REPORTED — reported, not gating: 4 of 400 entrance departures" in row_a
        assert "A row marked REPORTED is a disclosure, never a pass or a fail" in criteria
        assert criteria.index(f"| {NO_LOCKS} |") < criteria.index("| w1b_release_share (A-ON) |")
        flagged = [i for i in _limitations(text) if i.startswith("At the B-ON weave, W1b sent")]
        assert len(flagged) == 1
        assert "10 of 500 entering vehicles (2 %, over 2 run(s))" in flagged[0]
        assert "above the 1 % design bound of Amendment 4" in flagged[0]
        assert not [i for i in _limitations(text) if i.startswith("At the A-ON weave")]

    def test_an_opt_out_is_listed_in_the_limitations(self, tmp_path: Path) -> None:
        metas = _two_runs()
        metas[1]["weave_sections"][0] = _section_entry(
            "A-ON", "A-OFF", releases=None, params=ALL_OFF
        )
        text = self._report(self._run_set(tmp_path / "runs", metas), tmp_path)
        items = [i for i in _limitations(text) if i.startswith("At the A-ON weave, W1b off in 1")]
        assert len(items) == 1
        assert "W2 (any guard) off in 1 of 2 run(s)" in items[0]
        assert "allowed only to reproduce a result published before it" in items[0]

    def test_a_run_set_without_weaves_says_nothing(self, tmp_path: Path) -> None:
        root = tmp_path / "runs"
        _write_run(root / "cafe01234567" / "1", seed=1)
        text = self._report(root, tmp_path)
        assert "Weave rules at" not in text and "w1b_release_share" not in text
        assert "REPORTED" not in text
