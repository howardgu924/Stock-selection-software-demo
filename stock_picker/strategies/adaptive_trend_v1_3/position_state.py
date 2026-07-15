"""Immutable T+1 lot accounting transitions for Phase 4A."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable

import pandas as pd

from stock_picker.strategies.adaptive_trend_v1_3.phase3_models import (
    FillResult,
    FillSide,
    FillStatus,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase4_models import (
    PositionLot,
    PositionState,
    PositionStatus,
    PositionTransitionResult,
    TransitionStatus,
)

ZERO = Decimal("0")


def empty_position_state(symbol: str) -> PositionState:
    return PositionState(
        symbol=symbol,
        total_qty=0,
        sellable_qty=0,
        today_bought_qty=0,
        average_cost=ZERO,
        cost_basis=ZERO,
        entry_trade_date=None,
        entry_price=None,
        entry_atr=None,
        highest_close=None,
        realized_pnl=ZERO,
        lots=(),
        status=PositionStatus.CLOSED,
        current_trade_date=None,
    )


def apply_buy_fill(
    state: PositionState,
    fill: FillResult,
    *,
    entry_atr: Decimal | str,
    trading_calendar: Iterable[date | str],
) -> PositionTransitionResult:
    """Apply one unique buy fill.

    This API is intentionally non-idempotent.  The caller must deduplicate
    FILLED results and apply each fill exactly once; Phase 5 owns the durable
    execution-log unique key.
    """

    reason = _common_fill_reason(state, fill, FillSide.BUY)
    atr = _decimal(entry_atr)
    execution_date = _date(fill.execution_trade_date)
    calendar = _calendar(trading_calendar)
    if reason:
        return _invalid(state, "BUY", reason)
    if atr is None or atr <= 0:
        return _invalid(state, "BUY", "invalid_entry_atr")
    if execution_date is None or execution_date not in calendar:
        return _invalid(state, "BUY", "invalid_execution_trade_date")
    if state.current_trade_date not in {None, execution_date}:
        return _invalid(state, "BUY", "state_date_mismatch")
    future = [day for day in calendar if day > execution_date]
    if not future:
        return _invalid(state, "BUY", "missing_unlock_trade_date")
    if not _state_valid(state):
        return _invalid(state, "BUY", "inconsistent_position_state")

    buy_cost = fill.gross_amount + fill.total_fees
    if buy_cost <= 0 or fill.execution_price is None:
        return _invalid(state, "BUY", "invalid_fill_amount")
    sequence = max((lot.sequence for lot in state.lots), default=0) + 1
    lot = PositionLot(
        buy_trade_date=execution_date,
        qty=fill.filled_qty,
        remaining_qty=fill.filled_qty,
        execution_price=fill.execution_price,
        allocated_buy_fees=fill.total_fees,
        unlock_trade_date=future[0],
        sequence=sequence,
        remaining_cost=buy_cost,
    )
    total_qty = state.total_qty + fill.filled_qty
    cost_basis = state.cost_basis + buy_cost
    first = state.total_qty == 0
    new_state = replace(
        state,
        total_qty=total_qty,
        today_bought_qty=state.today_bought_qty + fill.filled_qty,
        average_cost=cost_basis / Decimal(total_qty),
        cost_basis=cost_basis,
        entry_trade_date=execution_date if first else state.entry_trade_date,
        entry_price=fill.execution_price if first else state.entry_price,
        entry_atr=atr if first else state.entry_atr,
        highest_close=fill.execution_price if first else state.highest_close,
        lots=state.lots + (lot,),
        status=PositionStatus.OPEN,
        current_trade_date=execution_date,
    )
    return PositionTransitionResult(
        status=TransitionStatus.APPLIED,
        previous_state=state,
        new_state=new_state,
        action="BUY",
        qty_delta=fill.filled_qty,
        cash_delta=-fill.cash_required,
        realized_pnl_delta=ZERO,
    )


def unlock_position_state(
    state: PositionState,
    current_trade_date: date | str,
    trading_calendar: Iterable[date | str],
) -> PositionTransitionResult:
    day = _date(current_trade_date)
    calendar = _calendar(trading_calendar)
    if day is None or day not in calendar:
        return _invalid(state, "UNLOCK", "invalid_trade_date")
    if state.current_trade_date is not None and day < state.current_trade_date:
        return _invalid(state, "UNLOCK", "state_date_regression")
    if not _state_valid(state):
        return _invalid(state, "UNLOCK", "inconsistent_position_state")
    sellable = sum(
        (
            lot.remaining_qty
            for lot in state.lots
            if lot.unlock_trade_date <= day and lot.remaining_qty > 0
        ),
        0,
    )
    bought_today = sum(
        (
            lot.remaining_qty
            for lot in state.lots
            if lot.buy_trade_date == day and lot.remaining_qty > 0
        ),
        0,
    )
    new_state = replace(
        state,
        sellable_qty=sellable,
        today_bought_qty=bought_today,
        current_trade_date=day,
    )
    return PositionTransitionResult(
        status=TransitionStatus.APPLIED,
        previous_state=state,
        new_state=new_state,
        action="UNLOCK",
        qty_delta=0,
        cash_delta=ZERO,
        realized_pnl_delta=ZERO,
    )


def apply_sell_fill(
    state: PositionState,
    fill: FillResult,
) -> PositionTransitionResult:
    """Apply one unique sell fill.

    This API is intentionally non-idempotent.  The caller must deduplicate
    FILLED results and apply each fill exactly once; replay is a caller error.
    Phase 5 must use the execution-log unique key to prevent duplicate
    application.
    """

    reason = _common_fill_reason(state, fill, FillSide.SELL)
    execution_date = _date(fill.execution_trade_date)
    if reason:
        return _invalid(state, "SELL", reason)
    if not _state_valid(state):
        return _invalid(state, "SELL", "inconsistent_position_state")
    if execution_date is None or state.current_trade_date != execution_date:
        return _invalid(state, "SELL", "state_date_mismatch")
    if fill.filled_qty > state.total_qty:
        return _invalid(state, "SELL", "insufficient_position")
    if fill.filled_qty > state.sellable_qty:
        return _invalid(state, "SELL", "insufficient_sellable_qty")

    remaining_to_sell = fill.filled_qty
    sold_cost = ZERO
    updated: list[PositionLot] = []
    for lot in sorted(state.lots, key=lambda item: (item.buy_trade_date, item.sequence)):
        eligible = lot.unlock_trade_date <= execution_date and lot.remaining_qty > 0
        take = min(remaining_to_sell, lot.remaining_qty) if eligible else 0
        if take:
            if take == lot.remaining_qty:
                cost = lot.remaining_cost
            else:
                cost = lot.remaining_cost * Decimal(take) / Decimal(lot.remaining_qty)
            sold_cost += cost
            lot = replace(
                lot,
                remaining_qty=lot.remaining_qty - take,
                remaining_cost=lot.remaining_cost - cost,
            )
            remaining_to_sell -= take
        updated.append(lot)
    if remaining_to_sell:
        return _invalid(state, "SELL", "inconsistent_sellable_lots")

    total_qty = state.total_qty - fill.filled_qty
    cost_basis = state.cost_basis - sold_cost
    pnl_delta = fill.net_proceeds - sold_cost
    if total_qty == 0:
        new_state = replace(
            state,
            total_qty=0,
            sellable_qty=0,
            today_bought_qty=0,
            average_cost=ZERO,
            cost_basis=ZERO,
            entry_trade_date=None,
            entry_price=None,
            entry_atr=None,
            highest_close=None,
            realized_pnl=state.realized_pnl + pnl_delta,
            lots=(),
            status=PositionStatus.CLOSED,
        )
    else:
        lots = tuple(lot for lot in updated if lot.remaining_qty > 0)
        today_bought = sum(
            lot.remaining_qty
            for lot in lots
            if lot.buy_trade_date == execution_date
        )
        new_state = replace(
            state,
            total_qty=total_qty,
            sellable_qty=state.sellable_qty - fill.filled_qty,
            today_bought_qty=today_bought,
            average_cost=cost_basis / Decimal(total_qty),
            cost_basis=cost_basis,
            realized_pnl=state.realized_pnl + pnl_delta,
            lots=lots,
        )
    return PositionTransitionResult(
        status=TransitionStatus.APPLIED,
        previous_state=state,
        new_state=new_state,
        action="SELL",
        qty_delta=-fill.filled_qty,
        cash_delta=fill.net_proceeds,
        realized_pnl_delta=pnl_delta,
    )


def _common_fill_reason(
    state: PositionState, fill: FillResult, expected_side: FillSide
) -> str:
    if fill.status != FillStatus.FILLED:
        return "fill_not_filled"
    if fill.side != expected_side:
        return "fill_side_mismatch"
    if (
        type(fill.requested_qty) is not int
        or type(fill.filled_qty) is not int
        or fill.requested_qty <= 0
        or fill.filled_qty <= 0
        or fill.filled_qty != fill.requested_qty
    ):
        return "invalid_filled_qty"
    if fill.symbol != state.symbol:
        return "symbol_mismatch"
    if (
        not isinstance(fill.execution_price, Decimal)
        or not fill.execution_price.is_finite()
        or fill.execution_price <= 0
    ):
        return "invalid_fill_amount"
    for value in (
        fill.gross_amount,
        fill.commission,
        fill.stamp_tax,
        fill.transfer_fee,
        fill.settlement_fee,
        fill.total_fees,
        fill.cash_required,
        fill.net_proceeds,
    ):
        if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
            return "invalid_fill_amount"
    if fill.gross_amount != fill.execution_price * Decimal(fill.filled_qty):
        return "invalid_fill_amount"
    if fill.total_fees != (
        fill.commission
        + fill.stamp_tax
        + fill.transfer_fee
        + fill.settlement_fee
    ):
        return "invalid_fill_amount"
    if expected_side == FillSide.BUY and (
        fill.cash_required != fill.gross_amount + fill.total_fees
        or fill.net_proceeds != ZERO
    ):
        return "invalid_fill_amount"
    if expected_side == FillSide.SELL and (
        fill.net_proceeds != fill.gross_amount - fill.total_fees
        or fill.cash_required != ZERO
    ):
        return "invalid_fill_amount"
    return ""


def _state_valid(state: PositionState) -> bool:
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in (state.total_qty, state.sellable_qty, state.today_bought_qty)
    ):
        return False
    if any(
        not isinstance(lot.qty, int)
        or isinstance(lot.qty, bool)
        or lot.qty <= 0
        or not isinstance(lot.remaining_qty, int)
        or isinstance(lot.remaining_qty, bool)
        or lot.remaining_qty < 0
        or lot.remaining_qty > lot.qty
        or not lot.execution_price.is_finite()
        or lot.execution_price <= 0
        or not lot.allocated_buy_fees.is_finite()
        or lot.allocated_buy_fees < 0
        or not lot.remaining_cost.is_finite()
        or lot.remaining_cost < 0
        or lot.unlock_trade_date <= lot.buy_trade_date
        for lot in state.lots
    ):
        return False
    if sum(lot.remaining_qty for lot in state.lots) != state.total_qty:
        return False
    if sum((lot.remaining_cost for lot in state.lots), ZERO) != state.cost_basis:
        return False
    if state.sellable_qty > state.total_qty or state.today_bought_qty > state.total_qty:
        return False
    if state.total_qty == 0:
        return (
            state.cost_basis == ZERO
            and state.average_cost == ZERO
            and not state.lots
            and state.status == PositionStatus.CLOSED
        )
    if state.current_trade_date is None or state.status != PositionStatus.OPEN:
        return False
    expected_sellable = sum(
        lot.remaining_qty
        for lot in state.lots
        if lot.unlock_trade_date <= state.current_trade_date
    )
    expected_today = sum(
        lot.remaining_qty
        for lot in state.lots
        if lot.buy_trade_date == state.current_trade_date
    )
    return (
        state.sellable_qty == expected_sellable
        and state.today_bought_qty == expected_today
        and state.average_cost == state.cost_basis / Decimal(state.total_qty)
    )


def _invalid(state: PositionState, action: str, reason: str) -> PositionTransitionResult:
    return PositionTransitionResult(
        status=TransitionStatus.INVALID,
        previous_state=state,
        new_state=state,
        action=action,
        qty_delta=0,
        cash_delta=ZERO,
        realized_pnl_delta=ZERO,
        failure_reason=reason,
    )


def _calendar(values: Iterable[date | str]) -> list[date]:
    parsed = {_date(value) for value in values}
    return sorted(value for value in parsed if value is not None)


def _date(value: object) -> date | None:
    try:
        parsed = pd.Timestamp(value)
        return None if pd.isna(parsed) else parsed.date()
    except (TypeError, ValueError, OverflowError):
        return None


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (Decimal, str)):
        return None
    try:
        parsed = Decimal(value)
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, ValueError):
        return None
