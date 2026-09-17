"""ROADMAP §1.2 task 2 — IDM population calibration from I-24 MOTION episodes.

Runs the calibration package's population fit (seeded differential evolution
per episode, gap-RMSE objective, 70/30 holdout, q = 0.9 RMSE trim) over the
episodes extracted by ``scripts/i24_extract_episodes.py``. When the episode
count exceeds ``--max-episodes`` a **seeded random subsample** is fitted and
the artifact notes say so; the default cap keeps the run inside a few hours
on 8 processes at 5 Hz (each episode costs ~3 s to fit).

Input: ``data/i24motion/processed/i24_wb_episodes.pkl``.
Output: ``artifacts/idm_i24.json`` (IDMCalibration artifact).

**Sub-corridor fits.** ``--x-range LO HI`` (with ``--t-range`` and ``--tag``)
fits the same protocol on the episodes whose follower stayed inside a stretch
of the corridor, read from the position sidecar
``scripts/i24_extract_episodes.py --positions`` writes; the artifact then
carries the selection in its ``source`` and its notes. Without those flags the
selection step is skipped entirely and the default run is byte-identical to the
one that produced ``artifacts/idm_i24.json``.

Run: ``uv run --no-sync python scripts/fit_idm_i24.py [--max-episodes N] [--procs P]``
     ``uv run --no-sync python scripts/fit_idm_i24.py --x-range 750 2500 \\
         --t-range 1800 9000 --tag merge``
"""

from __future__ import annotations

import argparse
import json
import pickle
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from calibration.idm_fit import PARAM_ORDER, fit_population
from calibration.loaders.i24motion import I24_CITATION
from flowstate_core.rng import make_rng

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i24_data import PROCESSED_DIR, REPO_ROOT, data_hash

SEED = 42

NOTES = (
    "I-24 MOTION INCEPTION v1.x westbound, 30 Nov 2022 06:00-10:00 CST (Gloudemans et al. "
    "2023). Positions are the pipeline's smoothed back-center coordinates at 25 Hz; speed "
    "is their gradient (no NGSIM-style differentiation noise), decimated to 5 Hz. Episodes "
    "are cut on unstitched trajectory FRAGMENTS (median fragment ~6 s / ~120 m), so only "
    "the ~11% of fragments lasting >= 30 s can host an episode and episodes are short "
    "(rarely > 60 s); long-horizon behaviour (v0) is therefore still weakly excited. "
    "Leaders are position-ordered within the lane (the schema publishes none): a gap "
    "outside [0.5, 100] m is masked as 'untracked true leader' / 'duplicate fragment' and "
    "cuts the episode, but an untracked leader closer than 100 m cannot be detected, so a "
    "minority of episodes pair a follower with the wrong vehicle; the q=0.9 RMSE trim and "
    "the holdout number carry that cost honestly. Followers: passenger classes 0-3, "
    "mainline lanes 1-4."
)


def select_by_position(
    episodes: list,
    index_path: Path,
    x_range: tuple[float, float] | None,
    t_range: tuple[float, float] | None,
) -> tuple[list, dict]:
    """Keep the episodes whose follower stayed inside a position / time window.

    The window is applied to the whole episode (``x_start >= lo`` and
    ``x_end <= hi``; likewise in time), not to an overlap: a fit on a stretch of
    road should only see car-following that happened there. ``x`` is the data
    frame's travel-oriented position (0 = MM 62.7) recorded by
    ``scripts/i24_extract_episodes.py --positions``, keyed by each episode's
    ordinal in the pickled list.

    Args:
        episodes: The pickled episode list, in file order.
        index_path: The position sidecar JSON.
        x_range: Inclusive ``[lo, hi]`` position window [m], or None.
        t_range: Half-open ``[lo, hi)`` time window [s since 06:00 CST], or None.

    Returns:
        ``(selected_episodes, provenance)``; the provenance dict records the
        window, the counts and the sidecar it came from.

    Raises:
        ValueError: If the sidecar does not describe this episode list.
    """
    idx = json.loads(index_path.read_text())
    if int(idx["n_episodes"]) != len(episodes):
        raise ValueError(
            f"{index_path.name} indexes {idx['n_episodes']} episodes, the pickle holds "
            f"{len(episodes)}: rebuild it with scripts/i24_extract_episodes.py --positions"
        )
    cols = {name: i for i, name in enumerate(idx["columns"])}
    keep: list[int] = []
    for row in idx["rows"]:
        i = int(row[cols["index"]])
        if episodes[i].veh_id != row[cols["veh_id"]]:
            raise ValueError(f"{index_path.name} row {i} names another follower")
        x0, x1 = row[cols["x_start_m"]], row[cols["x_end_m"]]
        if x_range is not None and (x0 is None or x1 is None):
            continue
        if x_range is not None and not (x_range[0] <= x0 and x1 <= x_range[1]):
            continue
        if t_range is not None and not (
            t_range[0] <= row[cols["t_start_s"]] and row[cols["t_end_s"]] < t_range[1]
        ):
            continue
        keep.append(i)
    selected = [episodes[i] for i in keep]
    lanes = sorted({int(ep.metadata["lane"]) for ep in selected})
    return selected, {
        "position_index": index_path.name,
        "x_range_m": list(x_range) if x_range else None,
        "t_range_s": list(t_range) if t_range else None,
        "rule": "the whole episode inside the window (x_start >= lo, x_end <= hi; "
        "t_start >= lo, t_end < hi)",
        "n_episodes_available": len(episodes),
        "n_episodes_selected": len(selected),
        "n_distinct_followers": len({ep.veh_id for ep in selected}),
        "lanes": lanes,
        "episodes_per_lane": {
            str(lane): sum(1 for ep in selected if int(ep.metadata["lane"]) == lane)
            for lane in lanes
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--max-episodes", type=int, default=12000)
    ap.add_argument("--procs", type=int, default=8)
    ap.add_argument("--de-maxiter", type=int, default=60)
    ap.add_argument(
        "--classes",
        choices=("passenger", "heavy"),
        default="passenger",
        help="which extracted episode set to fit: passenger followers (default, "
        "artifacts/idm_i24.json) or heavy followers (semis and trucks, "
        "scripts/i24_extract_episodes.py --classes heavy -> artifacts/idm_i24_heavy.json)",
    )
    ap.add_argument(
        "--x-range",
        type=float,
        nargs=2,
        metavar=("LO", "HI"),
        default=None,
        help="fit only episodes whose follower stayed within this data-x window [m] "
        "(0 = MM 62.7), read from the position sidecar; needs --tag",
    )
    ap.add_argument(
        "--t-range",
        type=float,
        nargs=2,
        metavar=("LO", "HI"),
        default=None,
        help="fit only episodes entirely inside this time window [s since 06:00 CST], "
        "e.g. 1800 9000 for the 06:30-08:30 study period; needs --tag",
    )
    ap.add_argument(
        "--episodes-index",
        type=Path,
        default=None,
        help="position sidecar for --x-range/--t-range (default: the "
        "i24_wb_episode_positions[suffix].json beside the episode pickle)",
    )
    ap.add_argument(
        "--tag",
        default=None,
        help="name the sub-corridor fit: writes artifacts/idm_i24[_classes]_<tag>.json "
        "instead of the corridor-wide artifact (required with --x-range/--t-range)",
    )
    args = ap.parse_args()
    suffix = "" if args.classes == "passenger" else "_heavy"
    follower_label = (
        "passenger followers" if args.classes == "passenger" else "heavy followers (semi/truck)"
    )
    if (args.x_range or args.t_range) and not args.tag:
        ap.error("--x-range/--t-range need --tag so the corridor-wide artifact is not overwritten")

    with open(PROCESSED_DIR / f"i24_wb_episodes{suffix}.pkl", "rb") as f:
        episodes = pickle.load(f)
    select_note = ""
    selection: dict | None = None
    if args.x_range or args.t_range:
        index_path = args.episodes_index or PROCESSED_DIR / f"i24_wb_episode_positions{suffix}.json"
        episodes, selection = select_by_position(
            episodes,
            Path(index_path),
            tuple(args.x_range) if args.x_range else None,
            tuple(args.t_range) if args.t_range else None,
        )
        where = []
        if args.x_range:
            where.append(f"data x in [{args.x_range[0]:g}, {args.x_range[1]:g}] m")
        if args.t_range:
            where.append(f"t in [{args.t_range[0]:g}, {args.t_range[1]:g}) s after 06:00 CST")
        select_note = (
            f"SUB-CORRIDOR FIT '{args.tag}': only the "
            f"{selection['n_episodes_selected']} of {selection['n_episodes_available']} "
            f"episodes ({selection['n_distinct_followers']} distinct followers) that lie "
            f"entirely within {' and '.join(where)} were fitted (episodes per lane "
            f"{selection['episodes_per_lane']}); selection by the position sidecar "
            f"{selection['position_index']}, rule: {selection['rule']}."
        )
        print(select_note, flush=True)
    n_all = len(episodes)
    subsample_note = ""
    if args.max_episodes > 0 and n_all > args.max_episodes:
        rng = make_rng(SEED)
        idx = np.sort(rng.choice(n_all, size=args.max_episodes, replace=False))
        episodes = [episodes[i] for i in idx]
        subsample_note = (
            f"Seeded (seed {SEED}) random subsample of {len(episodes)} of {n_all} extracted "
            "episodes fitted for wall-clock budget."
        )
    created_at = subprocess.run(
        ["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"], capture_output=True, text=True, check=True
    ).stdout.strip()
    print(
        f"fitting {len(episodes)} of {n_all} episodes with {args.procs} processes (seed {SEED}) ...",
        flush=True,
    )
    t0 = time.perf_counter()
    cal = fit_population(
        episodes,
        seed=SEED,
        created_at=created_at,
        source=(
            "I-24 MOTION INCEPTION v1.x, 30 Nov 2022 westbound (6386d89efb3ff533c12df167__post10), "
            f"{len(episodes)} of {n_all} leader-follower episodes (>= 30 s, 5 Hz, "
            f"{follower_label}, mainline lanes 1-4"
            + (
                f"; sub-corridor selection '{args.tag}': {selection['n_episodes_selected']} of "
                f"{selection['n_episodes_available']} corridor-wide episodes, "
                f"x_range_m {selection['x_range_m']}, t_range_s {selection['t_range_s']}"
                if selection
                else ""
            )
            + "); cite "
            + I24_CITATION
        ),
        data_hash=data_hash(),
        holdout_frac=0.3,
        trim_quantile=0.9,
        de_maxiter=args.de_maxiter,
        n_procs=args.procs,
        notes=(select_note + " " if select_note else "")
        + (subsample_note + " " if subsample_note else "")
        + NOTES.replace("Followers: passenger classes 0-3", f"Followers: {follower_label}"),
    )
    wall = time.perf_counter() - t0
    tag_suffix = f"_{args.tag}" if args.tag else ""
    out = REPO_ROOT / "artifacts" / f"idm_i24{suffix}{tag_suffix}.json"
    cal.save(out)
    sd = np.sqrt(np.diag(np.array(cal.cov)))
    print(f"done in {wall:.0f} s -> {out}")
    print(f"train/holdout: {cal.n_episodes_fit}/{cal.n_episodes_holdout}")
    print(f"holdout gap RMSE (population-mean params): {cal.holdout_gap_rmse_m:.2f} m")
    for i, name in enumerate(PARAM_ORDER):
        print(f"  {name:6s} mean={cal.mean[name]:7.3f}  sd={sd[i]:6.3f}")
    rmses = np.array(cal.per_episode_rmse_m)
    print(
        f"training per-episode gap RMSE: median={np.median(rmses):.2f} m, "
        f"mean={rmses.mean():.2f} m, q90={np.quantile(rmses, 0.9):.2f} m"
    )
    (PROCESSED_DIR / f"i24_idm_fit_run{suffix}{tag_suffix}.json").write_text(
        json.dumps(
            {
                "n_episodes_available": n_all,
                "n_episodes_fitted": len(episodes),
                "procs": args.procs,
                "de_maxiter": args.de_maxiter,
                "seed": SEED,
                "selection": selection,
                "wall_s": round(wall, 1),
                "artifact": str(out.relative_to(REPO_ROOT)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
