"""
Domain: Hypersonic Dual-Mode Scramjet FADEC, Shock-Train Isolator & Endothermic Cooling Control
Module: api.BoundaryLayerBleedSlot_telemetry_ingest_rest_dispatcher
Standard: DO-178C-DAL-A-HYPERSONIC
Primary Law: EndothermicHeatSinkCapacity (4.6500e+06)
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

LOGGER = logging.getLogger("hypersonic_scr.api.BoundaryLayerBle")

SIGNING_ENV_REF = os.environ.get("TELEMETRY_HMAC_REF", "dev-local-hmac-placeholder")
PRIMARY_LAW_COEFF: float = 4.65000000e+06
SECONDARY_LAW_COEFF: float = 1.40000000e+00
NOMINAL_BASELINE_A: float = 0.045000
NOMINAL_TOLERANCE_A: float = 0.000300
NOMINAL_BASELINE_B: float = 6.850000
NOMINAL_TOLERANCE_B: float = 0.001700


class PorousThroatBleedManifoldState(str, Enum):
    COLD_STANDBY = "COLD_STANDBY"
    PRE_START_VERIFICATION = "PRE_START_VERIFICATION"
    CLOSED_LOOP_ACTIVE = "CLOSED_LOOP_ACTIVE"
    HIGH_GRADIENT_DAMPING = "HIGH_GRADIENT_DAMPING"
    DEGRADED_FALLBACK = "DEGRADED_FALLBACK"
    INTERLOCK_SCRAM_LATCHED = "INTERLOCK_SCRAM_LATCHED"


@dataclass(frozen=True)
class PorousThroatBleedManifoldFrame:
    frame_id: str
    facility_node: str
    timestamp_ms: int
    displacement_thick_mm: float
    mass_bleed_fraction: float
    fuel_supercritical_mpa: float
    radical_oh_ppm: float
    harmonic_distortion_ratio: float
    ambient_stress_factor: float
    redundant_channel_ok: bool = True

    def validate_physical_bounds(self) -> Tuple[bool, List[str]]:
        violations: List[str] = []
        if self.timestamp_ms <= 0:
            violations.append("timestamp_ms_must_be_positive")
        if math.isnan(self.displacement_thick_mm) or math.isinf(self.displacement_thick_mm):
            violations.append("displacement_thick_mm_non_finite")
        if math.isnan(self.mass_bleed_fraction) or math.isinf(self.mass_bleed_fraction):
            violations.append("mass_bleed_fraction_non_finite")
        if self.harmonic_distortion_ratio < 0.0 or self.harmonic_distortion_ratio > 1.0:
            violations.append("harmonic_distortion_out_of_unit_interval")
        if self.ambient_stress_factor <= 0.0:
            violations.append("ambient_stress_factor_non_positive")
        return (len(violations) == 0, violations)

    def compute_normalized_deviation(self) -> float:
        delta_a = abs(self.displacement_thick_mm - NOMINAL_BASELINE_A) / max(NOMINAL_BASELINE_A, 1e-9)
        delta_b = abs(self.fuel_supercritical_mpa - NOMINAL_BASELINE_B) / max(NOMINAL_BASELINE_B, 1e-9)
        weighted = 0.62 * delta_a + 0.38 * delta_b
        return round(weighted * (1.0 + 0.25 * self.harmonic_distortion_ratio), 8)


@dataclass
class PorousThroatBleedManifoldKalmanStateVector:
    epoch_ms: int
    state_estimate: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    covariance_diag: List[float] = field(default_factory=lambda: [1.0, 1.0, 1.0, 1.0])
    innovation_history: List[float] = field(default_factory=list)
    integrated_lyapunov_energy: float = 0.0
    consecutive_fault_ticks: int = 0
    regime: PorousThroatBleedManifoldState = PorousThroatBleedManifoldState.COLD_STANDBY

    def covariance_trace(self) -> float:
        return float(sum(max(c, 1e-12) for c in self.covariance_diag))

    def spectral_condition_proxy(self) -> float:
        clean = [max(c, 1e-12) for c in self.covariance_diag]
        return float(max(clean) / min(clean))

class PorousThroatBleedManifoldPrimaryEquilibriumSolverM11:
    """
    Production solver for PorousThroatBleedManifold (PrimaryEquilibriumSolver).
    Enforces EndothermicHeatSinkCapacity (coeff=4.6500e+06)
    """

    def __init__(self, node_id: str, dt_sec: float = 0.005, gain_alpha: float = 0.42) -> None:
        self.node_id: str = node_id
        self.dt_sec: float = max(dt_sec, 1e-5)
        self.gain_alpha: float = max(min(gain_alpha, 0.995), 0.005)
        self.reference_setpoint: float = 0.045000
        self.tolerance_band: float = 0.000300
        self.state_vector = PorousThroatBleedManifoldKalmanStateVector(
            epoch_ms=int(time.time() * 1000),
            state_estimate=[0.045000, 0.0, 0.006750, 1.0],
            covariance_diag=[0.000300, 0.000150, 0.000240, 0.01],
        )
        self._telemetry_window: List[float] = []
        self._residual_window: List[float] = []
        self._audit_counter: int = 0

    def evaluate_governing_law(self, frame: PorousThroatBleedManifoldFrame) -> Dict[str, float]:
        primary_signal = float(frame.displacement_thick_mm)
        secondary_signal = float(frame.mass_bleed_fraction)
        coupled_signal = float(frame.fuel_supercritical_mpa)
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

    def step_extended_kalman_filter(self, frame: PorousThroatBleedManifoldFrame) -> Dict[str, Any]:
        is_valid, errors = frame.validate_physical_bounds()
        if not is_valid:
            self.state_vector.consecutive_fault_ticks += 1
            self.state_vector.regime = PorousThroatBleedManifoldState.DEGRADED_FALLBACK
            return {
                "status": "REJECTED_FRAME",
                "errors": errors,
                "regime": self.state_vector.regime.value,
            }
        x0, x1, x2, x3 = self.state_vector.state_estimate
        p0, p1, p2, p3 = self.state_vector.covariance_diag
        pred_x0 = x0 + self.dt_sec * x1
        pred_x1 = 0.96 * x1 - 0.12 * (x0 - self.reference_setpoint)
        pred_x2 = 0.88 * x2 + 0.12 * frame.fuel_supercritical_mpa
        pred_x3 = max(0.05, min(2.5, x3 * (1.0 - 0.01 * frame.harmonic_distortion_ratio)))
        q_proc = max(self.tolerance_band * 0.05, 1e-7)
        pred_p0 = p0 + self.dt_sec * p1 + q_proc
        pred_p1 = p1 * 0.94 + q_proc
