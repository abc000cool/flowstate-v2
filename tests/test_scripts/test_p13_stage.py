"""The ingest of stage ``p13_i24_b2`` of scripts/gcp/pipeline_i24.sh, offline.

``uv``, ``curl``, ``sudo`` and ``nproc`` are stubs on ``PATH``; nothing is simulated
(amendment B2, docs/I24_DISCHARGE_DIAGNOSIS.md §8.4.3). Everything the stage writes is
installed: the rc family's inputs, demand and scenarios (scripts/i24_build_replica.py
--suffix flow_rc), the driver-calibrated and B2 arms, the two batteries and their run
files, the per-battery ramp-flow reductions the score re-reads (corridor_b2.py reduce)
and the score itself; nothing of it is reported as not ingested.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.test_scripts.test_p8c_stage import BASH, GCP, _stubs, _tgz

SEED = "6914975401685141156"  # spawn_seeds(42, 20)[0], the step-3 batteries' first seed
REF_RUN = f"runs/i24_validation/dc_refit_p13ref/ada3f406504b/{SEED}"
ARM_RUN = f"runs/i24_validation/dc_refit_rc/909b89f298c5/{SEED}"
ARTIFACTS = [
    "boundary_b2_corridor.json",
    "i24_b2_ramp_flows_dc_refit_p13ref.json",
    "i24_b2_ramp_flows_dc_refit_rc.json",
    "i24_validation_dc_refit_p13ref.json",
    "i24_validation_dc_refit_rc.json",
    "i24_replica_inputs_flow_rc.json",
    "demand_i24_flow_rc.json",
]
SCENARIOS = {
    # the builder's header names the family, not the file (no "name:" form, so no header check)
    "i24_replica_flow_rc.yaml": "# i24_replica — I-24 westbound\nname: i24_replica_flow_rc\n",
    "i24_replica_flow_rc_corrected.yaml": (
        "# i24_replica_corrected — I-24 westbound\nname: i24_replica_flow_rc_corrected\n"
    ),
    "i24_replica_flow_rc_corrected_dc.yaml": (
        "# i24_replica_flow_rc_corrected_dc: scenarios/i24_replica_flow_rc_corrected.yaml with the "
        "Amendment-1 driver calibration applied\nname: i24_replica_flow_rc_corrected_dc\n"
    ),
    "i24_replica_flow_rc_speedcal_dc_refit.yaml": (
        "# i24_replica_flow_rc_speedcal_dc_refit — amendment B2's corridor arm\n"
        "name: i24_replica_flow_rc_speedcal_dc_refit\n"
    ),
}


def test_the_ingest_installs_every_p13_output_and_the_run_files(tmp_path: Path) -> None:
    _, env = _stubs(tmp_path)
    repo = tmp_path / "ingest_repo"
    (repo / "artifacts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    files = {f"artifacts/{a}": f'{{"name": "{a}"}}' for a in ARTIFACTS}
    files |= {f"scenarios/{s}": text for s, text in SCENARIOS.items()}
    files |= {
        f"{REF_RUN}/meta.json": '{"run": "ref"}',
        f"{REF_RUN}/vehicles.parquet": "ref-vehicles",
        f"{ARM_RUN}/meta.json": '{"run": "rc"}',
        "logs/PIPELINE_EXIT": "rc=0\n",
    }
    tgz = _tgz(tmp_path / "final.tgz", files)
    r = subprocess.run(
        [BASH, str(GCP / "ingest_pipeline_results.sh"), str(tgz)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    for a in ARTIFACTS:
        assert f"artifact {a}\n" in r.stdout, a
    for s in SCENARIOS:
        assert f"scenario {s}\n" in r.stdout, s
    assert "runs: i24_validation/dc_refit_p13ref" in r.stdout
    assert "runs: i24_validation/dc_refit_rc" in r.stdout
    assert "NOT ingested" not in r.stdout, r.stdout
    assert "WARNING" not in r.stdout
    for name in files:
        if not name.startswith("logs/"):
            assert (repo / name).read_text() == files[name], name


def test_the_arm_guard_treats_one_ulp_of_coverage_as_the_same_number() -> None:
    """Stage p13's first launch (2026-10-07) was refused because the coverage factors rebuilt
    on an AMD machine differed from the committed Intel-built ones in the 16th digit; the
    scenario's inflows carry six decimals, so the demand was the same. Ints, shapes and
    strings must still match exactly."""
    import importlib.util

    path = Path("artifacts/i24_discharge_2026-10-07/harness_b2/corridor_b2.py")
    spec = importlib.util.spec_from_file_location("p13_corridor_b2", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    same = mod.same_values
    assert same([0.6052631989032525, 0.49086154730557285], [0.6052631989032526, 0.4908615473055729])
    assert same({"a": [1, 2.0, "x", None]}, {"a": [1, 2.0, "x", None]})
    assert not same([0.6052631989032525], [0.6052631989032525 * (1 + 1e-6)])
    assert not same([1329, 740], [1329, 741])
    assert not same([1.0, 2.0], [1.0])
    assert not same({"a": 1}, {"b": 1})
    assert not same(True, 1.0) or same(True, True)


def test_the_arm_guard_ignores_default_valued_keys_the_committed_file_omits() -> None:
    """Stage p13's second launch (2026-10-07) was refused because the builder on the VM writes
    every field the model knows, defaults included, while the committed `_dc_refit` omits the
    ones added after it was written; the configurations are the same. A changed non-ramp value
    or a different ramp time grid must still refuse."""
    import importlib.util

    import yaml

    path = Path("artifacts/i24_discharge_2026-10-07/harness_b2/corridor_b2.py")
    spec = importlib.util.spec_from_file_location("p13_corridor_b2_cfg", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ref = yaml.safe_load(Path("scenarios/i24_replica_flow_speedcal_dc_refit.yaml").read_text())
    doc = json.loads(json.dumps(ref))
    doc["name"] = "arm"
    doc.setdefault("av", {})["emergency_handback"] = True  # a default the committed file omits
    doc["fleet"]["speed_factor"] = 1.0
    for ramp in doc["network"]["ramps"]:
        key = "inflow" if ramp["kind"] == "on" else "exit_fraction"
        ramp[key] = [[t, v * 0.9] for t, v in ramp[key]]  # the ramp values may differ
    assert mod.same_configuration(doc, ref)
    changed = json.loads(json.dumps(doc))
    changed["fleet"]["speed_factor"] = 1.05
    assert not mod.same_configuration(changed, ref)
    grid = json.loads(json.dumps(doc))
    ramp = grid["network"]["ramps"][0]
    key = "inflow" if ramp["kind"] == "on" else "exit_fraction"
    ramp[key] = ramp[key][:-1]
    assert not mod.same_configuration(grid, ref)
