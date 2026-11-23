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
