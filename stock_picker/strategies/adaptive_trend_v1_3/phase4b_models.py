"""Immutable contracts for adaptive-trend Phase 4B exit management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

import pandas as pd

from stock_picker.strategies.adaptive_trend_v1_3.phase3_models import (
    ExecutionType,
    FillRequest,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase4_models import PositionState


class ExitDecisionStatus(StrEnum):
    TRIGGERED = "TRIGGERED"
    NO_ACTION = "NO_ACTION"
    INVALID = "INVALID"


class PendingSellStatus(StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class CooldownStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class AttemptIdentity:
    normalized_symbol: str
    execution_type: ExecutionType
    attempt_trade_date: date
    attempt_bar_start: pd.Timestamp


@dataclass(frozen=True)
class ExitIntent:
    symbol: str
    decision_trade_date: date
    decision_time: str
    execution_type: ExecutionType
    reason: str
    priority: int
    requested_target_qty: int
    full_exit: bool
    sticky: bool
    requires_revalidation: bool
    episode_id: str
    trigger_bar_start: pd.Timestamp | None
    trigger_price: Decimal | None
    active_stop: Decimal | None
    created_at: pd.Timestamp
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class PendingSellState:
    symbol: str
    status: PendingSellStatus
    reason: str
    priority: int
    execution_type: ExecutionType
    target_qty: int
    remaining_qty: int
    created_at: pd.Timestamp
    next_attempt_at: pd.Timestamp
    sticky: bool
    requires_revalidation: bool
    episode_id: str
    retry_count: int = 0
    last_failure: str = ""
    last_attempt_at: pd.Timestamp | None = None
    completed_at: pd.Timestamp | None = None
    cancelled_reason: str = ""
    last_processed_attempt: AttemptIdentity | None = None


@dataclass(frozen=True)
class ExitControlState:
    symbol: str
    entry_trade_date: date
    initial_stop: Decimal
    trailing_stop: Decimal
    highest_close: Decimal
    price_basis_id: str
    weak_score_streak: int = 0
    ma20_episode_id: str = ""
    ma20_recovery_count: int = 0
    acted_episode_ids: tuple[str, ...] = ()
    active_pending_sell: PendingSellState | None = None
    last_1430_evaluation_date: date | None = None
    last_trailing_update_date: date | None = None
    last_full_exit_reason: str = ""
    last_full_exit_date: date | None = None


@dataclass(frozen=True)
class CooldownRecord:
    symbol: str
    exit_reason: str
    exit_trade_date: date
    blocked_trade_dates: tuple[date, ...]
    reentry_allowed_date: date
    status: CooldownStatus


@dataclass(frozen=True)
class ExitDecisionResult:
    symbol: str
    status: ExitDecisionStatus
    selected_intent: ExitIntent | None
    all_triggered_reasons: tuple[str, ...]
    active_stop: Decimal | None
    sellable_qty: int
    unsellable_qty: int
    executable_qty: int
    pending_remaining_qty: int
    previous_control_state: ExitControlState
    new_control_state: ExitControlState
    failure_reason: str = ""


@dataclass(frozen=True)
class StopUpdateResult:
    status: str
    previous_state: ExitControlState | None
    new_state: ExitControlState | None
    failure_reason: str = ""


@dataclass(frozen=True)
class PendingUpdateResult:
    status: str
    previous_state: PendingSellState | None
    new_state: PendingSellState | None
    failure_reason: str = ""


@dataclass(frozen=True)
class DeriskHoldingInput:
    symbol: str
    total_qty: int
    sellable_qty: int
    market_value: Decimal
    p1430: Decimal
    opportunity_score: Decimal
    rs60: Decimal
    rs20: Decimal
    partial_sell_lot_size: int
    protected: bool = False
    higher_priority_full_exit: bool = False


@dataclass(frozen=True)
class PortfolioDeriskResult:
    status: str
    intents: tuple[ExitIntent, ...]
    projected_exposure: Decimal
    excess_exposure: Decimal
    residual_excess: Decimal
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplacementIncumbent:
    symbol: str
    total_qty: int
    opportunity_score: Decimal
    entry_threshold: Decimal
    rs60: Decimal
    rs20: Decimal
    protected: bool
    has_active_pending: bool
    has_higher_exit: bool


@dataclass(frozen=True)
class ReplacementCandidate:
    symbol: str
    opportunity_score: Decimal
    entry_threshold: Decimal
    rs60: Decimal
    rs20: Decimal
    signed_er20: Decimal
    final_order_qty: int
    cooldown_blocked: bool


@dataclass(frozen=True)
class ReplacementResult:
    status: str
    intent: ExitIntent | None
    incumbent_symbol: str
    candidate_symbol: str
    failure_reason: str = ""


@dataclass(frozen=True)
class ExitCycleHoldingInput:
    position: PositionState
    control: ExitControlState
    p1430: Decimal | str
    previous_ma20: Decimal | str
    previous_ma60: Decimal | str
    ma20_slope5: Decimal | str
    opportunity_status: str
    opportunity_score: Decimal | str
    entry_threshold: Decimal | str
    strong_top_divergence: bool
    normal_top_divergence: bool
    divergence_episode_id: str
    partial_sell_lot_size: int
    protected: bool
    market_data_valid: bool
    market_value: Decimal | str
    rs60: Decimal | str
    rs20: Decimal | str
    pending_signal_valid: bool = True


@dataclass(frozen=True)
class ExitCycleResult:
    status: str
    intents_by_symbol: tuple[tuple[str, ExitIntent], ...]
    fill_requests: tuple[FillRequest, ...]
    pending_updates: tuple[PendingUpdateResult, ...]
    cancelled_pending: tuple[str, ...]
    projected_exposure: Decimal
    residual_excess: Decimal
    replacement_symbol: str
    control_states: tuple[tuple[str, ExitControlState], ...]
    reasons: tuple[str, ...] = ()
