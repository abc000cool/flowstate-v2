"""FlowState v2 calibration: FD fitting, IDM population calibration, demand
fitting and public-dataset loaders (CLAUDE.md §6, docs/CONTRACTS.md §5)."""

from calibration.demand import fit_inflow, geh
from calibration.episodes import (
    LeaderFollowerEpisode,
    episodes_from_pairs,
    extract_episodes,
    is_valid_episode,
    validate_episode,
)
from calibration.fd_fit import fit_triangular_fd
from calibration.idm_fit import (
    EpisodeFit,
    equilibrium_gap,
    fit_episode,
    fit_population,
    gap_rmse,
    idm_accel,
    simulate_follower,
)

__all__ = [
    "EpisodeFit",
    "LeaderFollowerEpisode",
    "episodes_from_pairs",
    "equilibrium_gap",
    "extract_episodes",
    "fit_episode",
    "fit_inflow",
    "fit_population",
    "fit_triangular_fd",
    "gap_rmse",
    "geh",
    "idm_accel",
    "is_valid_episode",
    "simulate_follower",
    "validate_episode",
]

__version__ = "2.0.0"
