"""Dataset loaders normalizing public traffic data to FlowState schemas.

Trajectory loaders (→ ``LeaderFollowerEpisode`` lists, CLAUDE.md §6.2):
``ngsim`` (public), ``highd`` (registered access) and ``i24motion`` (registered
access; streaming reader for the multi-GB INCEPTION exports, schema verified
against the 30 Nov 2022 run). Detector loader (→ tidy flow/density/speed table, §6.1): ``pems``.

Detector-observation loaders (→ the tidy station × window frame of the
observations contract): ``detector_csv`` (any CSV, with a column mapping) and
``mndot`` (the MnDOT Regional TMC configuration and 30-second archive API).
"""

from calibration.loaders.detector_csv import (
    DETECTOR_COLUMNS,
    load_detector_csv,
    write_detector_csv,
)
from calibration.loaders.highd import load_highd_episodes
from calibration.loaders.i24motion import (
    convert_i24_to_parquet,
    iter_i24_documents,
    load_i24_episodes,
    load_i24_parquet,
    load_i24_trajectories,
    load_i24_vehicles,
)
from calibration.loaders.ngsim import (
    FEET_TO_M,
    build_ngsim_episodes,
    load_ngsim_episodes,
    load_ngsim_trajectories,
)
from calibration.loaders.pems import load_pems_station_csv

__all__ = [
    "DETECTOR_COLUMNS",
    "FEET_TO_M",
    "build_ngsim_episodes",
    "convert_i24_to_parquet",
    "iter_i24_documents",
    "load_detector_csv",
    "load_highd_episodes",
    "load_i24_episodes",
    "load_i24_parquet",
    "load_i24_trajectories",
    "load_i24_vehicles",
    "load_ngsim_episodes",
    "load_ngsim_trajectories",
    "load_pems_station_csv",
    "write_detector_csv",
]
