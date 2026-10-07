import glob, json, sys
from flowstate_core.config import ScenarioConfig, config_hash, WEAVE_DEFAULTS
import flowstate_core
out = {"_from": flowstate_core.__file__, "_weave_defaults": WEAVE_DEFAULTS}
for f in sorted(glob.glob("scenarios/**/*.yaml", recursive=True)):
    try:
        out[f] = config_hash(ScenarioConfig.from_yaml(f))
    except Exception as e:
        out[f] = "ERR " + type(e).__name__ + ": " + str(e)[:80]
json.dump(out, open(sys.argv[1], "w"), indent=1)
print(len(out) - 2, "scenarios")
