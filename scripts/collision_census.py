"""Per-cell SUMO collision census of a sweep run tree (WP-95).

Reads only the ``meta.json`` of every run under ``<root>/<cell>/<config
hash>/<seed>/`` (the layout of ``scripts/corridor_sweep.py``,
``scripts/i24_penetration_sweep.py`` and ``scripts/us101_penetration_sweep.py``)
and writes, per cell:

* ``summary``: ``validation.battery.collision_summary`` over the cell's metas,
  labelled by seed (WP-94: the total of ``n_collisions``, the per-run mean with
  its 95 % t-interval, the rate per 1,000 departed vehicles, the logged events
  by lane); ``None`` when no run records the counter — not recorded is not 0.
* ``n_distinct_pairs``: distinct (collider, victim) pairs among the logged
  events. The runner adds every collision SUMO still holds whenever a new one
  is detected in a step (``microsim.runner``, the loop's collision block), so a
  pair still in contact is counted again in that step; the pairs are the
  events net of those repeats (a pair that collides twice apart is counted
  once).
* ``colliders``: the logged events by the rear vehicle's role,
  ``compliant_av`` (in ``complied_ids``), ``noncompliant_av`` (in ``av_ids``
  only) or ``human``; ``victims_of_compliant_av``: those events' front
  vehicle, ``av`` or ``human``.
* ``n_departed`` and ``n_departed_av`` (AV ids among the vehicles with a fuel
  total, which are the departed ones), with the rate per 1,000 departed AVs.
* ``handback``: ``meta.json["av_emergency_handback"]`` (``AVSpec.
  emergency_handback``) summed over the runs that carry it; ``None`` when none
  does.
* ``off_corridor`` and ``close_leader`` (WP-96): ``meta.json["av_off_corridor"]``
  and ``meta.json["av_close_leader"]`` summed the same way, with the number of
  runs that had ``AVSpec.release_off_corridor`` (``n_runs_release``) or
  ``AVSpec.observe_close_leader`` (``n_runs_observed``) on. The runner records
  both in every run with a vehicle controller, on or off; ``None`` when no run
  carries them (no controller, or runs written before WP-96).

Per-vehicle distances are not in ``meta.json``, so no rate per vehicle-km is
given (``validation.battery.COLLISION_DEFINITION`` explains why the WP-94 rate
is per departed vehicle). A run logs its first 50 events only
(``microsim.runner.COLLISION_LOG_MAX``); ``summary.n_logged`` against
``summary.total`` says whether any were cut.

Usage::

    uv run --no-sync python scripts/collision_census.py --root runs/i24_strat_sweep \\
        --out artifacts/collisions_i24_strat_sweep.json
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from validation.battery import collision_summary, json_safe

SCHEMA_VERSION = 1
PER_AVS = 1000


def _roles(meta: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    return set(meta.get("av_ids") or []), set(meta.get("complied_ids") or [])


def cell_census(metas: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The census of one cell from its runs' parsed ``meta.json`` (module docstring).

    Args:
        metas: One parsed ``meta.json`` per run of the cell.

    Returns:
        ``{n_runs, config_hashes, summary, n_distinct_pairs, colliders,
        victims_of_compliant_av, n_departed, n_departed_av,
        per_1000_departed_av, handback, off_corridor, close_leader}``.
    """
    colliders = {"compliant_av": 0, "noncompliant_av": 0, "human": 0}
    victims = {"av": 0, "human": 0}
    n_pairs = 0
    n_departed = n_departed_av = 0
    handback: dict[str, int] | None = None
    off_corridor: dict[str, int] | None = None
    close_leader: dict[str, int] | None = None
    for meta in metas:
        av, complied = _roles(meta)
        pairs: set[tuple[str, str]] = set()
        for event in meta.get("collisions") or []:
            collider, victim = str(event.get("collider")), str(event.get("victim"))
            pairs.add((collider, victim))
            if collider in complied:
                colliders["compliant_av"] += 1
                victims["av" if victim in av else "human"] += 1
            elif collider in av:
                colliders["noncompliant_av"] += 1
            else:
                colliders["human"] += 1
        n_pairs += len(pairs)
        departed = meta.get("fuel_ml_per_vehicle") or {}
        n_departed += int(meta.get("n_vehicles_departed") or 0)
        n_departed_av += len(av.intersection(departed))
        hb = meta.get("av_emergency_handback")
        if isinstance(hb, dict):
            if handback is None:
                handback = {"n_runs": 0, "n_vehicle_steps": 0, "n_withdrawals": 0, "n_vehicles": 0}
            handback["n_runs"] += 1
            for key in ("n_vehicle_steps", "n_withdrawals", "n_vehicles"):
                handback[key] += int(hb.get(key) or 0)
        oc = meta.get("av_off_corridor")
        if isinstance(oc, dict):
            if off_corridor is None:
                off_corridor = dict.fromkeys(
                    ("n_runs", "n_runs_release", "n_vehicles", "n_vehicle_steps", "n_released"), 0
                )
            off_corridor["n_runs"] += 1
            off_corridor["n_runs_release"] += int(bool(oc.get("release")))
            for key in ("n_vehicles", "n_vehicle_steps", "n_released"):
                off_corridor[key] += int(oc.get(key) or 0)
        cl = meta.get("av_close_leader")
        if isinstance(cl, dict):
            if close_leader is None:
                close_leader = dict.fromkeys(
                    ("n_runs", "n_runs_observed", "n_vehicle_steps", "n_vehicles"), 0
                )
            close_leader["n_runs"] += 1
            close_leader["n_runs_observed"] += int(bool(cl.get("observed")))
            for key in ("n_vehicle_steps", "n_vehicles"):
                close_leader[key] += int(cl.get(key) or 0)
    summary = collision_summary(metas)
    total = summary["total"] if summary is not None else None
    return {
        "n_runs": len(metas),
        "config_hashes": sorted({str(m.get("config_hash")) for m in metas}),
        "summary": summary,
        "n_distinct_pairs": n_pairs,
        "colliders": colliders,
        "victims_of_compliant_av": victims,
        "n_departed": n_departed,
        "n_departed_av": n_departed_av,
        "per_1000_departed_av": (
            PER_AVS * total / n_departed_av if total is not None and n_departed_av else None
        ),
        "handback": handback,
        "off_corridor": off_corridor,
        "close_leader": close_leader,
    }


def census(root: Path) -> dict[str, Any]:
    """The census of every cell under ``root`` (cells in name order, runs in path order)."""
    cells: dict[str, Any] = {}
    for cell_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        paths = sorted(cell_dir.glob("*/*/meta.json"))
        if not paths:
            continue
        cells[cell_dir.name] = cell_census([json.loads(p.read_text()) for p in paths])
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "collision_census",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "root": str(root),
        "cells": cells,
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--root", required=True, type=Path, help="sweep run tree <cell>/<hash>/<seed>/")
    ap.add_argument("--out", required=True, type=Path, help="census artifact (JSON)")
    args = ap.parse_args(argv)
    out = census(args.root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(json_safe(out), indent=1, allow_nan=False) + "\n")
    for name, cell in out["cells"].items():
        s = cell["summary"]
        total = "not recorded" if s is None else f"{s['total']} in {s['n_runs_with_collisions']}"
        print(
            f"{name}: {cell['n_runs']} runs, collisions {total}, pairs {cell['n_distinct_pairs']}, "
            f"colliders {cell['colliders']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
