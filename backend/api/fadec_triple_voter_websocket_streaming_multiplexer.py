"""
Domain: Hypersonic Dual-Mode Scramjet FADEC, Shock-Train Isolator & Endothermic Cooling Control
Module: api.fadec_triple_voter_websocket_streaming_multiplexer
Standard: DO-178C-DAL-A-HYPERSONIC
Primary Law: ObliqueShockRankineHugoniot (6.8000e+00)
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

LOGGER = logging.getLogger("hypersonic_scr.api.fadec_triple_vot")

SIGNING_ENV_REF = os.environ.get("TELEMETRY_HMAC_REF", "dev-local-hmac-placeholder")
PRIMARY_LAW_COEFF: float = 6.80000000e+00
SECONDARY_LAW_COEFF: float = 3.85000000e-01
NOMINAL_BASELINE_A: float = 125.000000
NOMINAL_TOLERANCE_A: float = 0.004400
NOMINAL_BASELINE_B: float = 0.740000
NOMINAL_TOLERANCE_B: float = 0.002200


class ByzantineTriplexBusState(str, Enum):
    COLD_STANDBY = "COLD_STANDBY"
    PRE_START_VERIFICATION = "PRE_START_VERIFICATION"
    CLOSED_LOOP_ACTIVE = "CLOSED_LOOP_ACTIVE"
    HIGH_GRADIENT_DAMPING = "HIGH_GRADIENT_DAMPING"
    DEGRADED_FALLBACK = "DEGRADED_FALLBACK"
    INTERLOCK_SCRAM_LATCHED = "INTERLOCK_SCRAM_LATCHED"


@dataclass(frozen=True)
class ByzantineTriplexBusFrame:
    frame_id: str
    facility_node: str
    timestamp_ms: int
    channel_skew_usec: float
    servo_current_ma: float
    jp7_conversion_frac: float
    stagnation_temp_kelvin: float
    harmonic_distortion_ratio: float
    ambient_stress_factor: float
    redundant_channel_ok: bool = True

    def validate_physical_bounds(self) -> Tuple[bool, List[str]]:
        violations: List[str] = []
        if self.timestamp_ms <= 0:
            violations.append("timestamp_ms_must_be_positive")
        if math.isnan(self.channel_skew_usec) or math.isinf(self.channel_skew_usec):
            violations.append("channel_skew_usec_non_finite")
        if math.isnan(self.servo_current_ma) or math.isinf(self.servo_current_ma):
            violations.append("servo_current_ma_non_finite")
        if self.harmonic_distortion_ratio < 0.0 or self.harmonic_distortion_ratio > 1.0:
            violations.append("harmonic_distortion_out_of_unit_interval")
        if self.ambient_stress_factor <= 0.0:
            violations.append("ambient_stress_factor_non_positive")
        return (len(violations) == 0, violations)

    def compute_normalized_deviation(self) -> float:
        delta_a = abs(self.channel_skew_usec - NOMINAL_BASELINE_A) / max(NOMINAL_BASELINE_A, 1e-9)
        delta_b = abs(self.jp7_conversion_frac - NOMINAL_BASELINE_B) / max(NOMINAL_BASELINE_B, 1e-9)
        weighted = 0.62 * delta_a + 0.38 * delta_b
        return round(weighted * (1.0 + 0.25 * self.harmonic_distortion_ratio), 8)


@dataclass
class ByzantineTriplexBusKalmanStateVector:
    epoch_ms: int
    state_estimate: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    covariance_diag: List[float] = field(default_factory=lambda: [1.0, 1.0, 1.0, 1.0])
    innovation_history: List[float] = field(default_factory=list)
    integrated_lyapunov_energy: float = 0.0
    consecutive_fault_ticks: int = 0
    regime: ByzantineTriplexBusState = ByzantineTriplexBusState.COLD_STANDBY

    def covariance_trace(self) -> float:
        return float(sum(max(c, 1e-12) for c in self.covariance_diag))

    def spectral_condition_proxy(self) -> float:
        clean = [max(c, 1e-12) for c in self.covariance_diag]
        return float(max(clean) / min(clean))

class ByzantineTriplexBusPrimaryEquilibriumSolverM12:
    """
    Production solver for ByzantineTriplexBus (PrimaryEquilibriumSolver).
    Enforces ObliqueShockRankineHugoniot (coeff=6.8000e+00)
    """

    def __init__(self, node_id: str, dt_sec: float = 0.005, gain_alpha: float = 0.42) -> None:
        self.node_id: str = node_id
        self.dt_sec: float = max(dt_sec, 1e-5)
        self.gain_alpha: float = max(min(gain_alpha, 0.995), 0.005)
        self.reference_setpoint: float = 125.000000
        self.tolerance_band: float = 0.004400
        self.state_vector = ByzantineTriplexBusKalmanStateVector(
            epoch_ms=int(time.time() * 1000),
            state_estimate=[125.000000, 0.0, 18.750000, 1.0],
            covariance_diag=[0.004400, 0.002200, 0.003520, 0.01],
        )
        self._telemetry_window: List[float] = []
        self._residual_window: List[float] = []
        self._audit_counter: int = 0

    def evaluate_governing_law(self, frame: ByzantineTriplexBusFrame) -> Dict[str, float]:
        primary_signal = float(frame.channel_skew_usec)
        secondary_signal = float(frame.servo_current_ma)
        coupled_signal = float(frame.jp7_conversion_frac)
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
            "helmholtz_energy": round(helmholtz_free_energy, 8),
            "jacobian_determinant": round(jacobian_determinant, 8),
            "effective_damping_ratio": round(damping_ratio, 8),
        }

    def step_extended_kalman_filter(self, frame: ByzantineTriplexBusFrame) -> Dict[str, Any]:
        is_valid, errors = frame.validate_physical_bounds()
        if not is_valid:
            self.state_vector.consecutive_fault_ticks += 1
            self.state_vector.regime = ByzantineTriplexBusState.DEGRADED_FALLBACK
            return {
                "status": "REJECTED_FRAME",
                "errors": errors,
                "regime": self.state_vector.regime.value,
            }
        x0, x1, x2, x3 = self.state_vector.state_estimate
        p0, p1, p2, p3 = self.state_vector.covariance_diag
        pred_x0 = x0 + self.dt_sec * x1
        pred_x1 = 0.96 * x1 - 0.12 * (x0 - self.reference_setpoint)
        pred_x2 = 0.88 * x2 + 0.12 * frame.jp7_conversion_frac
        pred_x3 = max(0.05, min(2.5, x3 * (1.0 - 0.01 * frame.harmonic_distortion_ratio)))
        q_proc = max(self.tolerance_band * 0.05, 1e-7)
        pred_p0 = p0 + self.dt_sec * p1 + q_proc
        pred_p1 = p1 * 0.94 + q_proc
        pred_p2 = p2 * 0.95 + q_proc
        pred_p3 = p3 * 0.98 + q_proc * 0.1
        measurement_z = float(frame.channel_skew_usec)
        r_noise = max(self.tolerance_band * (1.0 + frame.harmonic_distortion_ratio), 1e-6)
