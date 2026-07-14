"""Immutable contracts for V1.3.4/V1.3.5 minute data and direct fills."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

import pandas as pd


class FillSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class ExecutionType(StrEnum):
    ENTRY_BUY = "ENTRY_BUY"
    HARD_EXIT = "HARD_EXIT"
    SOFT_EXIT = "SOFT_EXIT"
    REPLACEMENT_EXIT = "REPLACEMENT_EXIT"
    ORDINARY_REDUCTION = "ORDINARY_REDUCTION"


class FillStatus(StrEnum):
    FILLED = "FILLED"
    FAILED = "FAILED"
    INVALID = "INVALID"


@dataclass(frozen=True)
class TradingRuleSnapshot:
    exchange: str
    board: str
    security_type: str
    effective_date: date | str | pd.Timestamp
    buy_lot_size: int
    partial_sell_lot_size: int
    full_exit_odd_lot_allowed: bool
    price_tick: Decimal


@dataclass(frozen=True)
class FeeRuleSnapshot:
    effective_date: date | str | pd.Timestamp
    commission_rate: Decimal
    minimum_commission: Decimal
    buy_transfer_fee_rate: Decimal
    sell_transfer_fee_rate: Decimal
    buy_settlement_fee_rate: Decimal
    sell_settlement_fee_rate: Decimal
    stamp_tax_rate: Decimal


@dataclass(frozen=True)
class FillRequest:
    execution_type: ExecutionType
    symbol: str
    requested_qty: int
    signal_time: str | pd.Timestamp
    cash_available: Decimal = Decimal("0")
    position_qty: int = 0
    sellable_qty: int = 0


@dataclass(frozen=True)
class FillResult:
    status: FillStatus
    side: FillSide
    execution_type: ExecutionType
    symbol: str
    requested_qty: int
    filled_qty: int
    execution_trade_date: str
    execution_bar_start: str
    execution_price: Decimal | None
    gross_amount: Decimal
    commission: Decimal
    stamp_tax: Decimal
    transfer_fee: Decimal
    settlement_fee: Decimal
    total_fees: Decimal
    cash_required: Decimal
    net_proceeds: Decimal
    failure_reason: str
    retryable: bool
    simplified_direct_fill: bool = True


@dataclass(frozen=True)
class MinuteContractResult:
    status: str
    bars: pd.DataFrame
    invalid_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionBarResolution:
    status: str
    execution_bar_start: pd.Timestamp | None
    failure_reason: str = ""
