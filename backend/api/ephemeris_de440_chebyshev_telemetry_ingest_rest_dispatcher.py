"""
Domain: Cislunar NRHO Halo Orbit Stationkeeping, Optical Navigation & Deep-Space Laser Relay
Module: api.ephemeris_de440_chebyshev_telemetry_ingest_rest_dispatcher
Standard: CCSDS-502.0-B-3-CISLUNAR
Primary Law: StateTransitionVariational (1.0000e+00)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import os
import statistics
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

LOGGER = logging.getLogger("cislunar_nrho_.api.ephemeris_de440_")

SIGNING_ENV_REF = os.environ.get("TELEMETRY_HMAC_REF", "dev-local-hmac-placeholder")
PRIMARY_LAW_COEFF: float = 1.00000000e+00
SECONDARY_LAW_COEFF: float = 3.05200000e+00
NOMINAL_BASELINE_A: float = 0.012150
NOMINAL_TOLERANCE_A: float = 0.000100
NOMINAL_BASELINE_B: float = 0.140000
NOMINAL_TOLERANCE_B: float = 0.000200


class BarycentricPerturbationState(str, Enum):
    COLD_STANDBY = "COLD_STANDBY"
    PRE_START_VERIFICATION = "PRE_START_VERIFICATION"
    CLOSED_LOOP_ACTIVE = "CLOSED_LOOP_ACTIVE"
    HIGH_GRADIENT_DAMPING = "HIGH_GRADIENT_DAMPING"
    DEGRADED_FALLBACK = "DEGRADED_FALLBACK"
    INTERLOCK_SCRAM_LATCHED = "INTERLOCK_SCRAM_LATCHED"


@dataclass(frozen=True)
class BarycentricPerturbationFrame:
    frame_id: str
    facility_node: str
    timestamp_ms: int
    earth_moon_mu_ratio: float
    solar_tidal_accel_m_s2: float
    lunar_limb_pixel_err: float
    cr_reflectivity_coeff: float
    harmonic_distortion_ratio: float
    ambient_stress_factor: float
    redundant_channel_ok: bool = True

    def validate_physical_bounds(self) -> Tuple[bool, List[str]]:
        violations: List[str] = []
        if self.timestamp_ms <= 0:
            violations.append("timestamp_ms_must_be_positive")
        if math.isnan(self.earth_moon_mu_ratio) or math.isinf(self.earth_moon_mu_ratio):
            violations.append("earth_moon_mu_ratio_non_finite")
        if math.isnan(self.solar_tidal_accel_m_s2) or math.isinf(self.solar_tidal_accel_m_s2):
            violations.append("solar_tidal_accel_m_s2_non_finite")
        if self.harmonic_distortion_ratio < 0.0 or self.harmonic_distortion_ratio > 1.0:
            violations.append("harmonic_distortion_out_of_unit_interval")
        if self.ambient_stress_factor <= 0.0:
            violations.append("ambient_stress_factor_non_positive")
        return (len(violations) == 0, violations)

    def compute_normalized_deviation(self) -> float:
        delta_a = abs(self.earth_moon_mu_ratio - NOMINAL_BASELINE_A) / max(NOMINAL_BASELINE_A, 1e-9)
        delta_b = abs(self.lunar_limb_pixel_err - NOMINAL_BASELINE_B) / max(NOMINAL_BASELINE_B, 1e-9)
        weighted = 0.62 * delta_a + 0.38 * delta_b
        return round(weighted * (1.0 + 0.25 * self.harmonic_distortion_ratio), 8)


@dataclass
class BarycentricPerturbationKalmanStateVector:
    epoch_ms: int
    state_estimate: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    covariance_diag: List[float] = field(default_factory=lambda: [1.0, 1.0, 1.0, 1.0])
    innovation_history: List[float] = field(default_factory=list)
    integrated_lyapunov_energy: float = 0.0
    consecutive_fault_ticks: int = 0
    regime: BarycentricPerturbationState = BarycentricPerturbationState.COLD_STANDBY

    def covariance_trace(self) -> float:
        return float(sum(max(c, 1e-12) for c in self.covariance_diag))

    def spectral_condition_proxy(self) -> float:
        clean = [max(c, 1e-12) for c in self.covariance_diag]
        return float(max(clean) / min(clean))

