"""Immutable data contracts for adaptive-trend Phase 4A."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Iterable, Mapping

import pandas as pd


FrozenReturnSeries = tuple[tuple[object, object], ...]


class Phase4Status(StrEnum):
    VALID = "VALID"
    BLOCK_NEW = "BLOCK_NEW"
    INVALID = "INVALID"


class PositionStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class TransitionStatus(StrEnum):
    APPLIED = "APPLIED"
    INVALID = "INVALID"


@dataclass(frozen=True)
class ClassificationMetadata:
    value: str
    effective_date: date | str
    known_at: date | str
    source: str
    classification_version: str


@dataclass(frozen=True)
class IndustryClassificationSnapshot:
    symbol: str
    industry_code: str
    industry_name: str
    effective_date: date | str
    known_at: object
    source: str
    classification_version: str


@dataclass(frozen=True)
class T1RiskObservation:
    instrument_type: str
    symbol: str
    sample_entry_date: date
    completion_trade_date: date
    completion_bar_start: object
    known_at: object
    entry_price: Decimal
    first_sellable_price: Decimal
    t1_return: Decimal
    t1_loss: Decimal
    censored: bool
    status: str = "VALID"
    failure_reason: str = ""


@dataclass(frozen=True)
class T1RiskResult:
    status: Phase4Status
    source_level: str
    sample_count: int
    t1_loss_q: Decimal
    censored_count: int
    normal_risk_pct: Decimal
    effective_risk_pct: Decimal
    failure_reason: str = ""
    observation_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExistingHolding:
    symbol: str
    actual_weight: Decimal
    industry_snapshot: IndustryClassificationSnapshot | None
    t1_loss_q: Decimal
    daily_returns: FrozenReturnSeries | Mapping[object, object]
    scenario_loss_pct: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        object.__setattr__(self, "daily_returns", freeze_return_series(self.daily_returns))


@dataclass(frozen=True)
class CandidateInput:
    symbol: str
    opportunity_status: str
    opportunity_score: Decimal
    entry_threshold: Decimal
    opportunity_rank: int
    rs60: Decimal
    rs20: Decimal
    signed_er20: Decimal
    market_paused: bool
    emergency_gate: str
    risk_overlay: str
    execution_gate: str
    t1_risk_status: str
    t1_loss_q: Decimal
    entry_atr: Decimal
    entry_price: Decimal
    industry_snapshot: IndustryClassificationSnapshot | None
    cooldown_blocked: bool
    daily_returns: FrozenReturnSeries | Mapping[object, object]
    execution_price: Decimal
    buy_lot_size: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "daily_returns", freeze_return_series(self.daily_returns))


@dataclass(frozen=True)
class SizingResult:
    symbol: str
    eligible: bool
    raw_weight: Decimal
    risk_multiplier: Decimal
    gate_multiplier: Decimal
    correlation_multiplier: Decimal
    adjusted_weight: Decimal
    industry_scaled_weight: Decimal
    exposure_scaled_weight: Decimal
    stress_scaled_weight: Decimal
    final_target_weight: Decimal
    order_qty: int
    actual_order_weight: Decimal
    failure_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class PortfolioAllocationResult:
    selected_symbols: tuple[str, ...]
    existing_exposure: Decimal
    effective_exposure_cap: Decimal
    existing_stress: Decimal
    final_new_stress: Decimal
    final_new_exposure: Decimal
    final_portfolio_stress: Decimal
    stress_status: str
    allocation_status: str
    industry_weights: tuple[tuple[str, Decimal], ...]
    highest_correlation_pair: tuple[str, str, Decimal] | None
    two_limit_down_scenario_loss: Decimal
    status: Phase4Status
    reasons: tuple[str, ...]
    sizing_results: tuple[SizingResult, ...]


@dataclass(frozen=True)
class PositionLot:
    buy_trade_date: date
    qty: int
    remaining_qty: int
    execution_price: Decimal
    allocated_buy_fees: Decimal
    unlock_trade_date: date
    sequence: int
    remaining_cost: Decimal


@dataclass(frozen=True)
class PositionState:
    symbol: str
    total_qty: int
    sellable_qty: int
    today_bought_qty: int
    average_cost: Decimal
    cost_basis: Decimal
    entry_trade_date: date | None
    entry_price: Decimal | None
    entry_atr: Decimal | None
    highest_close: Decimal | None
    realized_pnl: Decimal
    lots: tuple[PositionLot, ...]
    status: PositionStatus
    current_trade_date: date | None


@dataclass(frozen=True)
class PositionTransitionResult:
    status: TransitionStatus
    previous_state: PositionState
    new_state: PositionState
    action: str
    qty_delta: int
    cash_delta: Decimal
    realized_pnl_delta: Decimal
    failure_reason: str = ""


def freeze_return_series(
    values: FrozenReturnSeries | Mapping[object, object] | Iterable[tuple[object, object]],
) -> FrozenReturnSeries:
    """Copy caller-owned return data into a deterministic immutable tuple."""

    if isinstance(values, Mapping):
        items = list(values.items())
    else:
        items = list(values)
    copied: list[tuple[object, object]] = []
    for item in items:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            copied.append(("<invalid_return_item>", repr(item)))
            continue
        key, value = item
        if isinstance(key, pd.Timestamp):
            key = pd.Timestamp(key)
        copied.append((key, value))
    return tuple(
        sorted(
            copied,
            key=lambda item: (
                type(item[0]).__name__,
                repr(item[0]),
                type(item[1]).__name__,
                repr(item[1]),
            ),
        )
    )
