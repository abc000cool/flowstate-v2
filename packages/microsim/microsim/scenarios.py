"""Scenario loading and one-call convenience runs (CLAUDE.md §3.2).

Named scenarios live as versioned YAML under the repository's ``scenarios/``
directory (``ring_sugiyama``, ``corridor_10km``, …) and validate through
``flowstate_core.config.ScenarioConfig`` (docs/CONTRACTS.md §2).
:func:`run_scenario` resolves a name or path, loads the config, and runs one
micro-tier replicate via :func:`microsim.runner.run_micro`.
"""

from __future__ import annotations

from pathlib import Path

from flowstate_core.config import ScenarioConfig
from microsim.runner import RunPaths, run_micro

#: Repository ``scenarios/`` directory (this file sits at
#: ``packages/microsim/microsim/scenarios.py`` → three parents up is the root).
SCENARIOS_DIR: Path = Path(__file__).resolve().parents[3] / "scenarios"


def resolve_scenario(name_or_path: str | Path) -> Path:
    """Resolve a scenario name or YAML path to a concrete file.

    Args:
        name_or_path: Either an existing YAML file path, or a bare scenario
            name (with or without ``.yaml``) looked up in ``scenarios/``.

    Returns:
        Path of the scenario YAML.

    Raises:
        FileNotFoundError: Nothing matches; the message lists the available
            named scenarios.
    """
    p = Path(name_or_path)
    if p.is_file():
        return p
    stem = p.name.removesuffix(".yaml").removesuffix(".yml")
    for suffix in (".yaml", ".yml"):
        candidate = SCENARIOS_DIR / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    available = sorted(f.stem for f in SCENARIOS_DIR.glob("*.y*ml"))
    raise FileNotFoundError(
        f"scenario {name_or_path!r} not found (looked in {SCENARIOS_DIR}); available: {available}"
    )


def load_scenario(name_or_path: str | Path) -> ScenarioConfig:
    """Load and validate a scenario config by name or path."""
    return ScenarioConfig.from_yaml(resolve_scenario(name_or_path))


def run_scenario(
    name_or_path: str | Path,
    out_dir: str | Path,
    seed: int | None = None,
    *,
    gui: bool = False,
    use_traci: bool = False,
) -> RunPaths:
    """Load a scenario and run one micro-tier replicate.

    Args:
        name_or_path: Scenario name (``scenarios/<name>.yaml``) or YAML path.
        out_dir: Run-tree root (artifacts under ``<hash>/<seed>/``).
        seed: Replicate seed; defaults to the scenario's own ``seed``.
        gui: Launch ``sumo-gui`` (debugging; forces TraCI).
        use_traci: TCP TraCI fallback instead of libsumo.

    Returns:
        :class:`microsim.runner.RunPaths` for the completed replicate.
    """
    cfg = load_scenario(name_or_path)
    return run_micro(
        cfg,
        cfg.seed if seed is None else seed,
        out_dir,
        gui=gui,
        use_traci=use_traci,
    )
