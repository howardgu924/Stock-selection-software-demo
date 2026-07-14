"""Immutable value objects shared by the V1.3.3 Phase-2 calculators."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Phase2Status(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"


class DivergenceType(StrEnum):
    NONE = "NONE"
    TOP = "TOP"
    STRONG_TOP = "STRONG_TOP"
    BOTTOM = "BOTTOM"


class DivergenceStrength(StrEnum):
    NONE = "NONE"
    NORMAL = "NORMAL"
    STRONG = "STRONG"


class RiskStatus(StrEnum):
    ALLOW = "ALLOW"
    REDUCED = "REDUCED"
    BLOCK_NEW = "BLOCK_NEW"


class HoldingRiskAction(StrEnum):
    NONE = "NONE"
    WATCH = "WATCH"
    REDUCE = "REDUCE"
    EXIT = "EXIT"


class ExecutionGateStatus(StrEnum):
    PASS = "PASS"
    HALF = "HALF"
    REJECT = "REJECT"


class EmergencyStatus(StrEnum):
    NORMAL = "NORMAL"
    LEVEL_1 = "LEVEL_1"
    LEVEL_2 = "LEVEL_2"
    INVALID = "INVALID"


@dataclass(frozen=True)
class DivergenceSignal:
    divergence_type: DivergenceType
    strength: DivergenceStrength
    pivot_1_date: str
    pivot_2_date: str
    confirmed_date: str
    first_usable_date: str
    active_until: str | None
    is_active: bool


@dataclass(frozen=True)
class DivergenceSnapshot:
    status: Phase2Status
    as_of: str
    top_signal: DivergenceSignal | None = None
    bottom_signal: DivergenceSignal | None = None
    invalid_reasons: tuple[str, ...] = ()

    @property
    def has_normal_top(self) -> bool:
        return bool(
            self.top_signal
            and self.top_signal.is_active
            and self.top_signal.divergence_type == DivergenceType.TOP
        )

    @property
    def has_strong_top(self) -> bool:
        return bool(
            self.top_signal
            and self.top_signal.is_active
            and self.top_signal.divergence_type == DivergenceType.STRONG_TOP
        )

    @property
    def has_bottom(self) -> bool:
        return bool(self.bottom_signal and self.bottom_signal.is_active)


@dataclass(frozen=True)
class SecurityStatus:
    is_st: bool = False
    is_star_st: bool = False
    is_delisting: bool = False
    suspended: bool = False
    no_price_limit: bool = False
    trade_status_unknown: bool = False


@dataclass(frozen=True)
class RiskOverlayResult:
    risk_status: RiskStatus
    risk_multiplier: float
    block_new_reason: str
    recovery_watch: bool
    recovery_confirmed: bool
    structure_break: bool
    signed_er_weakening: bool
    holding_risk_action: HoldingRiskAction
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionStatus:
    suspended: bool = False
    limit_status: str = "normal"
    trade_status: str = "normal"


@dataclass(frozen=True)
class ExecutionGateResult:
    execution_gate: ExecutionGateStatus
    gate_multiplier: float
    p10: float | None
    morning_vwap: float | None
    distance_ma20: float | None
    high_to_10_drawdown: float | None
    morning_max_drawdown: float | None
    below_vwap: float | None
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class EmergencyIndexInput:
    previous_close: float
    atr20: float
    p10: float


@dataclass(frozen=True)
class EmergencyMarketResult:
    emergency_status: EmergencyStatus
    shocks: tuple[tuple[str, float | None], ...]
    reject_new_entries: bool
    remove_exposure_drop_limit: bool
    max_reduction_pct: float
    reasons: tuple[str, ...]

    def shock_for(self, symbol: str) -> float | None:
        return dict(self.shocks).get(symbol)


def model_dict(value: Any) -> dict[str, Any]:
    """Return a shallow audit dictionary without mutating an immutable model."""

    return {name: getattr(value, name) for name in value.__dataclass_fields__}
