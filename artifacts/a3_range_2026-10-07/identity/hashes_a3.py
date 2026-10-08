"""Per committed scenario YAML: config hash and sha256 of the full model_dump (what meta.json["config"] records).

usage (from the tree's root, PYTHONPATH on that tree's packages): hashes_a3.py OUT_JSON
"""

import glob
import hashlib
import json
import sys

import flowstate_core
from flowstate_core.config import WEAVE_DEFAULTS, ScenarioConfig, config_hash

out = {"_from": flowstate_core.__file__, "_weave_defaults": WEAVE_DEFAULTS}
for f in sorted(glob.glob("scenarios/**/*.yaml", recursive=True)):
    try:
        cfg = ScenarioConfig.from_yaml(f)
        dump = json.dumps(cfg.model_dump(mode="json"), sort_keys=True)
        out[f] = {
            "hash": config_hash(cfg),
            "dump_sha": hashlib.sha256(dump.encode()).hexdigest()[:16],
        }
    except Exception as e:
        out[f] = "ERR " + type(e).__name__ + ": " + str(e)[:80]
json.dump(out, open(sys.argv[1], "w"), indent=1)
print(len(out) - 2, "scenarios")
