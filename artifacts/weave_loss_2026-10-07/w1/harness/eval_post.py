"""Per kept run tree of a th52 arm: strand.py's diagnostics (the F2 definition) and
merge_model_selfcheck.run_summary's lock flag. usage: eval_post.py WORK_DIR OUT_JSON"""
import glob, json, sys
from pathlib import Path
REPO = Path.cwd()  # run from the repository root
sys.path.insert(0, str(REPO / "scripts")); sys.path.insert(0, str(REPO / "artifacts/weave_loss_2026-10-07/harness"))
import merge_model_selfcheck as M
import strand
out = {}
for meta in sorted(glob.glob(f"{sys.argv[1]}/**/meta.json", recursive=True)):
    d = Path(meta).parent
    m = json.loads(Path(meta).read_text())
    class P:  # the attributes run_summary reads
        run_dir = d; meta = d / "meta.json"; trajectories = d / "trajectories.parquet"
    rs = M.run_summary(P, 0.0)
    out[m["seed"]] = {"strand": strand.one(str(d)), "lock": rs["lock"], "lowest_zone_minute_ms": rs["lowest_zone_minute_ms"],
                      "config_hash": m["config_hash"], "params": m["weave_sections"][0]["params"]}
    print(m["seed"], out[m["seed"]]["strand"]["strand_s"], rs["lock"], flush=True)
Path(sys.argv[2]).write_text(json.dumps(out, indent=1, default=float))
