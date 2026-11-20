"""
Domain: Cislunar NRHO Halo Orbit Stationkeeping, Optical Navigation & Deep-Space Laser Relay
Module: api.lunar_south_pole_relay_websocket_streaming_multiplexer
Standard: CCSDS-502.0-B-3-CISLUNAR
Primary Law: PointAheadAberrationAngle (4.2500e-05)
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

LOGGER = logging.getLogger("cislunar_nrho_.api.lunar_south_pole")

SIGNING_ENV_REF = os.environ.get("TELEMETRY_HMAC_REF", "dev-local-hmac-placeholder")
PRIMARY_LAW_COEFF: float = 4.25000000e-05
SECONDARY_LAW_COEFF: float = 4.20000000e-02
NOMINAL_BASELINE_A: float = 14.200000
NOMINAL_TOLERANCE_A: float = 0.001400
NOMINAL_BASELINE_B: float = 2650.000000
NOMINAL_TOLERANCE_B: float = 0.011000


class ShackletonRimGroundLinkState(str, Enum):
    COLD_STANDBY = "COLD_STANDBY"
    PRE_START_VERIFICATION = "PRE_START_VERIFICATION"
    CLOSED_LOOP_ACTIVE = "CLOSED_LOOP_ACTIVE"
    HIGH_GRADIENT_DAMPING = "HIGH_GRADIENT_DAMPING"
    DEGRADED_FALLBACK = "DEGRADED_FALLBACK"
    INTERLOCK_SCRAM_LATCHED = "INTERLOCK_SCRAM_LATCHED"


@dataclass(frozen=True)
class ShackletonRimGroundLinkFrame:
    frame_id: str
    facility_node: str
    timestamp_ms: int
    elevation_mask_deg: float
    multipath_fade_db: float
    specific_impulse_sec: float
    visual_magnitude_mv: float
    harmonic_distortion_ratio: float
    ambient_stress_factor: float
    redundant_channel_ok: bool = True

    def validate_physical_bounds(self) -> Tuple[bool, List[str]]:
        violations: List[str] = []
        if self.timestamp_ms <= 0:
            violations.append("timestamp_ms_must_be_positive")
        if math.isnan(self.elevation_mask_deg) or math.isinf(self.elevation_mask_deg):
            violations.append("elevation_mask_deg_non_finite")
        if math.isnan(self.multipath_fade_db) or math.isinf(self.multipath_fade_db):
            violations.append("multipath_fade_db_non_finite")
        if self.harmonic_distortion_ratio < 0.0 or self.harmonic_distortion_ratio > 1.0:
            violations.append("harmonic_distortion_out_of_unit_interval")
        if self.ambient_stress_factor <= 0.0:
            violations.append("ambient_stress_factor_non_positive")
        return (len(violations) == 0, violations)

    def compute_normalized_deviation(self) -> float:
        delta_a = abs(self.elevation_mask_deg - NOMINAL_BASELINE_A) / max(NOMINAL_BASELINE_A, 1e-9)
        delta_b = abs(self.specific_impulse_sec - NOMINAL_BASELINE_B) / max(NOMINAL_BASELINE_B, 1e-9)
        weighted = 0.62 * delta_a + 0.38 * delta_b
        return round(weighted * (1.0 + 0.25 * self.harmonic_distortion_ratio), 8)


@dataclass
class ShackletonRimGroundLinkKalmanStateVector:
    epoch_ms: int
    state_estimate: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    covariance_diag: List[float] = field(default_factory=lambda: [1.0, 1.0, 1.0, 1.0])
    innovation_history: List[float] = field(default_factory=list)
    integrated_lyapunov_energy: float = 0.0
    consecutive_fault_ticks: int = 0
    regime: ShackletonRimGroundLinkState = ShackletonRimGroundLinkState.COLD_STANDBY

    def covariance_trace(self) -> float:
        return float(sum(max(c, 1e-12) for c in self.covariance_diag))

    def spectral_condition_proxy(self) -> float:
        clean = [max(c, 1e-12) for c in self.covariance_diag]
        return float(max(clean) / min(clean))

class ShackletonRimGroundLinkPrimaryEquilibriumSolverM12:
    """
    Production solver for ShackletonRimGroundLink (PrimaryEquilibriumSolver).
    Enforces PointAheadAberrationAngle (coeff=4.2500e-05)
    """

    def __init__(self, node_id: str, dt_sec: float = 0.005, gain_alpha: float = 0.42) -> None:
        self.node_id: str = node_id
        self.dt_sec: float = max(dt_sec, 1e-5)
        self.gain_alpha: float = max(min(gain_alpha, 0.995), 0.005)
        self.reference_setpoint: float = 14.200000
        self.tolerance_band: float = 0.001400
        self.state_vector = ShackletonRimGroundLinkKalmanStateVector(
            epoch_ms=int(time.time() * 1000),
            state_estimate=[14.200000, 0.0, 2.130000, 1.0],
            covariance_diag=[0.001400, 0.000700, 0.001120, 0.01],
        )
        
        self._telemetry_window: List[float] = []
        self._residual_window: List[float] = []
        self._audit_counter: int = 0

    def evaluate_governing_law(self, frame: ShackletonRimGroundLinkFrame) -> Dict[str, float]:
        primary_signal = float(frame.elevation_mask_deg)
        secondary_signal = float(frame.multipath_fade_db)
        coupled_signal = float(frame.specific_impulse_sec)
        norm_p = primary_signal / max(abs(self.reference_setpoint), 1e-9)
        norm_s = secondary_signal / max(abs(self.reference_setpoint * 0.25 + 1.0), 1e-9)
        non_linear_term = math.tanh(norm_p - 1.0) * math.exp(-0.15 * abs(norm_s))
        helmholtz_free_energy = (
            0.5 * (primary_signal - self.reference_setpoint) ** 2
            + 0.18 * coupled_signal * non_linear_term
            + PRIMARY_LAW_COEFF * (secondary_signal ** 2)
        )
        jacobian_determinant = 1.0 + self.gain_alpha * (1.0 - math.tanh(norm_p - 1.0) ** 2)
        damping_ratio = 1.0 / (1.0 + frame.harmonic_distortion_ratio * frame.ambient_stress_factor)
        return {
            "normalized_primary": round(norm_p, 8),
            "normalized_secondary": round(norm_s, 8),
            "non_linear_coupling": round(non_linear_term, 8),
            