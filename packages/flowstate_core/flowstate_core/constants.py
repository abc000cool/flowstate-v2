"""Physical defaults and presets (CLAUDE.md §3.1, §5.1).

Every value here is either a literature default (with its calibration range,
to be replaced by Phase-2 calibration artifacts) or an explicitly labeled
legacy preset. Nothing in this file is a validated claim.
"""

from __future__ import annotations

from typing import Final

from flowstate_core.artifacts import TriangularFD
from flowstate_core.units import kmh_to_ms, veh_km_to_veh_m

# --- IDM literature defaults (Treiber, Hennecke & Helbing 2000), SI units ---
IDM_DEFAULTS: Final[dict[str, float]] = {
    "v0": 33.3,  # desired speed [m/s] (120 km/h)
    "T": 1.4,  # desired time headway [s]
    "a_max": 0.73,  # max acceleration [m/s²]
    "b": 1.67,  # comfortable deceleration [m/s²]
    "s0": 2.0,  # minimum gap [m]
    "delta": 4.0,  # acceleration exponent [-], fixed
}

# Calibration search ranges (CLAUDE.md §3.1 table); delta stays fixed.
IDM_RANGES: Final[dict[str, tuple[float, float]]] = {
    "v0": (25.0, 38.0),
    "T": (0.8, 2.2),
    "a_max": (0.3, 1.5),
    "b": (1.0, 3.0),
    "s0": (1.0, 3.0),
}

# Default per-vehicle heterogeneity: σ as a fraction of the mean (§3.1).
HETEROGENEITY_FRAC_DEFAULT: Final[float] = 0.12

# SUMO ``speedFactor`` of a passenger vehicle when the fleet sets none
# (``FleetSpec.speed_factor``, WP-109): desired speed capped at the posted
# limit, what every run before 2026-10-04 used.
SPEED_FACTOR_DEFAULT: Final[float] = 1.0

# SUMO's default cut-offs for an individual speed factor: the passenger
# default distribution is ``normc(1, 0.1, 0.2, 2)`` (SUMO documentation,
# "Definition of Vehicles, Vehicle Types, and Routes", section "Speed
# Distributions"); a single float ``speedFactor`` with ``speedDev`` keeps them
# (measured on SUMO 1.27.1: mean 1.9, deviation 0.5, largest of 367 draws
# 1.9994).
SPEED_FACTOR_BOUNDS: Final[tuple[float, float]] = (0.2, 2.0)

# Decimals SUMO keeps of a vehicle's speed factor (SUMO 1.27.1, measured:
# ``speedFactor="1.123456" speedDev="0"`` reads back as 1.1235).
SPEED_FACTOR_DECIMALS: Final[int] = 4

# --- v1 legacy fundamental diagram preset (documented default, uncalibrated) ---
# v1 used V_f = 100 km/h, ρ_jam = 160 veh/km, w = −20 km/h.
V1_LEGACY_FD: Final[TriangularFD] = TriangularFD(
    v_f=kmh_to_ms(100.0),
    w=-kmh_to_ms(20.0),
    rho_jam=veh_km_to_veh_m(160.0),
)

# --- Wave detection ---
V_JAM_THRESH: Final[float] = kmh_to_ms(40.0)
"""Speed threshold below which a bin counts as jammed [m/s] (§7.2)."""

WAVE_SPEED_BAND_KMH: Final[tuple[float, float]] = (14.0, 22.0)
"""Empirical backward stop-and-go wave-speed acceptance band [km/h] (§7.1)."""

# --- Sugiyama ring benchmark (§3.2.1) ---
RING_CIRCUMFERENCE_M: Final[float] = 230.0
RING_N_VEHICLES: Final[int] = 22
