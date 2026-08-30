"""FlowState v2 pure-function controller library (CLAUDE.md §4).

Every vehicle controller is a pure function ``(obs, params, memory) →
(v_cmd, new_memory)`` and every segment controller ``(obs, params, memory) →
(limits, new_memory)`` (docs/CONTRACTS.md §1): no I/O, no globals, no RNG;
all state threads through the JSON-serializable ``memory`` dict. Shared
verbatim between the microscopic (SUMO) and macroscopic (CTM) tiers.
"""

from controllers.follower_stopper import FOLLOWER_STOPPER_DEFAULTS, follower_stopper
from controllers.gym_env import EnvBackend, FlowStateEnv, SyntheticBackend
from controllers.idm import IDM_PARAM_DEFAULTS, desired_gap, equilibrium_gap, idm_accel
from controllers.jad import JAD_DEFAULTS, JAD_PHASES, jad
from controllers.pi_saturation import PI_SATURATION_DEFAULTS, pi_saturation
from controllers.registry import (
    ALL_SEGMENT_CONTROLLERS,
    ALL_VEHICLE_CONTROLLERS,
    default_params,
    get_segment_controller,
    get_vehicle_controller,
    list_controllers,
)
from controllers.vsl import VSL_THRESHOLD_DEFAULTS, vsl_threshold

__all__ = [
    "ALL_SEGMENT_CONTROLLERS",
    "ALL_VEHICLE_CONTROLLERS",
    "FOLLOWER_STOPPER_DEFAULTS",
    "IDM_PARAM_DEFAULTS",
    "JAD_DEFAULTS",
    "JAD_PHASES",
    "PI_SATURATION_DEFAULTS",
    "VSL_THRESHOLD_DEFAULTS",
    "EnvBackend",
    "FlowStateEnv",
    "SyntheticBackend",
    "default_params",
    "desired_gap",
    "equilibrium_gap",
    "follower_stopper",
    "get_segment_controller",
    "get_vehicle_controller",
    "idm_accel",
    "jad",
    "list_controllers",
    "pi_saturation",
    "vsl_threshold",
]

__version__ = "2.0.0"
