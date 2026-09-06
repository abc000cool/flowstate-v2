"""Write a scenario variant that carries the recording's heavy-vehicle share.

Copies ``--scenario`` to ``--out`` with ``FleetSpec.heavy`` set from
``artifacts/i24_heavy_observed.json`` (share of fragments, median length)
and ``artifacts/idm_i24_heavy.json`` (the fitted heavy population), the
HBEFA4 tractor-trailer emission class, and ``vClass`` truck — the same
block ``scripts/i24_build_replica.py --heavy`` writes. Everything else is
unchanged; the name gets a ``_heavy`` suffix. Run from the repo root::

    uv run --no-sync python scripts/i24_add_heavy_arm.py --scenario scenarios/i24_replica_speedcal.yaml \\
        --out scenarios/i24_replica_speedcal_heavy.yaml
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from flowstate_core.config import ScenarioConfig, config_hash

REPO = Path(__file__).resolve().parents[1]
HEAVY_FLEET_ARTIFACT = "artifacts/idm_i24_heavy.json"
HEAVY_EMISSION_CLASS = "HBEFA4/TT_AT_gt34-40t_Euro-VI_A-C"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--scenario", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    obs = json.loads((REPO / "artifacts" / "i24_heavy_observed.json").read_text())
    raw = yaml.safe_load(a.scenario.read_text())
    raw["fleet"]["heavy"] = {
        "fraction": float(obs["fraction_fragments"]),
        "length_m": float(obs["length_median_m"]),
        "emission_class": HEAVY_EMISSION_CLASS,
        "vclass": "truck",
        "idm_calibration": HEAVY_FLEET_ARTIFACT,
    }
    raw["name"] = f"{raw['name']}_heavy"
    cfg = ScenarioConfig.model_validate(raw)
    header = (
        f"# {raw['name']} — {a.scenario.name} plus the recording's heavy vehicles: "
        f"{100 * obs['fraction_fragments']:.1f}% of fragments, median length {obs['length_median_m']} m,\n"
        f"# population {HEAVY_FLEET_ARTIFACT} (docs/I24_DATA.md, heavy-vehicle section); no other change,\n"
        "# so the passenger capacity calibration is the one done without trucks (docs/I24_CAPACITY.md).\n"
        f"# config hash {config_hash(cfg)}; seeded=False.\n"
    )
    a.out.write_text(header + yaml.safe_dump(raw, sort_keys=False))
    print(f"-> {a.out} ({config_hash(cfg)})")


if __name__ == "__main__":
    main()
