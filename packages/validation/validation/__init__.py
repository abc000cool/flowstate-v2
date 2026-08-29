"""FlowState v2 validation: metrics, wave detection, string stability,
FHWA-style acceptance criteria, and the auto-generated validation report
(CLAUDE.md §7; docs/CONTRACTS.md §§3, 4, 7)."""

from validation.criteria import CriteriaProfile, CriteriaResult, evaluate
from validation.fields import (
    DensityField,
    FlowField,
    SpeedField,
    density_field,
    flow_field,
    speed_field,
)
from validation.metrics import (
    CI,
    Metrics,
    aggregate,
    compute_metrics,
    geh,
    rmspe,
    travel_times,
)
from validation.report import ReportRefusedError, generate_report
from validation.string_stability import (
    IDMPartials,
    equilibrium_gap,
    equilibrium_speed,
    idm_partials,
    is_string_stable,
    stability_criterion,
    unstable_band,
)
from validation.waves import Wave, WaveSet, detect_waves

__all__ = [
    "CI",
    "CriteriaProfile",
    "CriteriaResult",
    "DensityField",
    "FlowField",
    "IDMPartials",
    "Metrics",
    "ReportRefusedError",
    "SpeedField",
    "Wave",
    "WaveSet",
    "aggregate",
    "compute_metrics",
    "density_field",
    "detect_waves",
    "equilibrium_gap",
    "equilibrium_speed",
    "evaluate",
    "flow_field",
    "geh",
    "generate_report",
    "idm_partials",
    "is_string_stable",
    "rmspe",
    "speed_field",
    "stability_criterion",
    "travel_times",
    "unstable_band",
]
