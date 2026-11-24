"""
Domain: Hypersonic Dual-Mode Scramjet FADEC, Shock-Train Isolator & Endothermic Cooling Control
Module: api.oblique_shock_isolator_interlock_override_command_router
Standard: DO-178C-DAL-A-HYPERSONIC
Primary Law: BilligShockTrainCorrelation (1.4000e+00)
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

LOGGER = logging.getLogger("hypersonic_scr.api.oblique_shock_is")

SIGNING_ENV_REF = os.environ.get("TELEMETRY_HMAC_REF", "dev-local-hmac-placeholder")
PRIMARY_LAW_COEFF: float = 1.40000000e+00
SECONDARY_LAW_COEFF: float = 1.12000000e+06
NOMINAL_BASELINE_A: float = 28.400000
NOMINAL_TOLERANCE_A: float = 0.004100
NOMINAL_BASELINE_B: float = 0.920000
NOMINAL_TOLERANCE_B: float = 0.001500


class BilligShockTrainDuctState(str, Enum):
    COLD_STANDBY = "COLD_STANDBY"
    PRE_START_VERIFICATION = "PRE_START_VERIFICATION"
    CLOSED_LOOP_ACTIVE = "CLOSED_LOOP_ACTIVE"
    HIGH_GRADIENT_DAMPING = "HIGH_GRADIENT_DAMPING"
    DEGRADED_FALLBACK = "DEGRADED_FALLBACK"
    INTERLOCK_SCRAM_LATCHED = "INTERLOCK_SCRAM_LATCHED"


@dataclass(frozen=True)
class BilligShockTrainDuctFrame:
    frame_id: str
    facility_node: str
    timestamp_ms: int
    pre_combustion_shock_x: float
    unstart_margin_pct: float
    damkohler_residence_no: float
    freestream_mach_est: float
    harmonic_distortion_ratio: float
    ambient_stress_factor: float
    redundant_channel_ok: bool = True

    def validate_physical_bounds(self) -> Tuple[bool, List[str]]:
        violations: List[str] = []
        if self.timestamp_ms <= 0:
            violations.append("timestamp_ms_must_be_positive")
        if math.isnan(self.pre_combustion_shock_x) or math.isinf(self.pre_combustion_shock_x):
            violations.append("pre_combustion_shock_x_non_finite")
        if math.isnan(self.unstart_margin_pct) or math.isinf(self.unstart_margin_pct):
            violations.append("unstart_margin_pct_non_finite")
        if self.harmonic_distortion_ratio < 0.0 or self.harmonic_distortion_ratio > 1.0:
            violations.append("harmonic_distortion_out_of_unit_interval")
        if self.ambient_stress_factor <= 0.0:
            violations.append("ambient_stress_factor_non_positive")
        return (len(violations) == 0, violations)

    def compute_normalized_deviation(self) -> float:
        delta_a = abs(self.pre_combustion_shock_x - NOMINAL_BASELINE_A) / max(NOMINAL_BASELINE_A, 1e-9)
        delta_b = abs(self.damkohler_residence_no - NOMINAL_BASELINE_B) / max(NOMINAL_BASELINE_B, 1e-9)
        weighted = 0.62 * delta_a + 0.38 * delta_b
        return round(weighted * (1.0 + 0.25 * self.harmonic_distortion_ratio), 8)


@dataclass
class BilligShockTrainDuctKalmanStateVector:
    epoch_ms: int
    state_estimate: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    covariance_diag: List[float] = field(default_factory=lambda: [1.0, 1.0, 1.0, 1.0])
    innovation_history: List[float] = field(default_factory=list)
    integrated_lyapunov_energy: float = 0.0
    consecutive_fault_ticks: int = 0
    regime: BilligShockTrainDuctState = BilligShockTrainDuctState.COLD_STANDBY

    def covariance_trace(self) -> float:
        return float(sum(max(c, 1e-12) for c in self.covariance_diag))

    def spectral_condition_proxy(self) -> float:
        clean = [max(c, 1e-12) for c in self.covariance_diag]
        return float(max(clean) / min(clean))

class BilligShockTrainDuctPrimaryEquilibriumSolverM13:
    """
    Production solver for BilligShockTrainDuct (PrimaryEquilibriumSolver).
    Enforces BilligShockTrainCorrelation (coeff=1.4000e+00)
    """

    def __init__(self, node_id: str, dt_sec: float = 0.005, gain_alpha: float = 0.42) -> None:
        self.node_id: str = node_id
        self.dt_sec: float = max(dt_sec, 1e-5)
        self.gain_alpha: float = max(min(gain_alpha, 0.995), 0.005)
        self.reference_setpoint: float = 28.400000
        self.tolerance_band: float = 0.004100
        self.state_vector = BilligShockTrainDuctKalmanStateVector(
            epoch_ms=int(time.time() * 1000),
            state_estimate=[28.400000, 0.0, 4.260000, 1.0],
            covariance_diag=[0.004100, 0.002050, 0.003280, 0.01],
        )
        self._telemetry_window: List[float] = []
