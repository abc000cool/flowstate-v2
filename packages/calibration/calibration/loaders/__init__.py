"""Dataset loaders normalizing public traffic data to FlowState schemas.

Trajectory loaders (→ ``LeaderFollowerEpisode`` lists, CLAUDE.md §6.2):
``ngsim`` (public), ``highd`` and ``i24motion`` (registered access; parsed
formats follow the published documentation and are unit-tested on synthetic
fixtures). Detector loader (→ tidy flow/density/speed table, §6.1): ``pems``.
"""

from calibration.loaders.highd import load_highd_episodes
from calibration.loaders.i24motion import load_i24_episodes, load_i24_trajectories
from calibration.loaders.ngsim import (
    FEET_TO_M,
    build_ngsim_episodes,
    load_ngsim_episodes,
    load_ngsim_trajectories,
)
from calibration.loaders.pems import load_pems_station_csv

__all__ = [
    "FEET_TO_M",
    "build_ngsim_episodes",
    "load_highd_episodes",
    "load_i24_episodes",
    "load_i24_trajectories",
    "load_ngsim_episodes",
    "load_ngsim_trajectories",
    "load_pems_station_csv",
]
