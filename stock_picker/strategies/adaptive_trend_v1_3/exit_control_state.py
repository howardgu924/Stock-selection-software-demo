"""Pure immutable stop and per-position exit-control transitions."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable

import pandas as pd

from stock_picker.strategies.adaptive_trend_v1_3.minute_contract import (
    SHANGHAI_TIMEZONE,
    normalize_security_symbol,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase4b_models import (
    ExitControlState,
    StopUpdateResult,
)


def initialize_exit_control(
    *,
    symbol: str,
    entry_trade_date: date | str,
    entry_price: Decimal | str,
    effective_risk_pct: Decimal | str,
    price_basis_id: str,
) -> StopUpdateResult:
    """Initialize InitialStop and the monotone trailing-stop state."""

    normalized = _symbol(symbol)
    day = _date(entry_trade_date)
    price = _decimal(entry_price)
    risk = _decimal(effective_risk_pct)
    basis = str(price_basis_id).strip()
    if normalized is None or day is None or price is None or price <= 0:
        return StopUpdateResult("INVALID", None, None, "invalid_entry_input")
    if risk is None or risk <= 0 or risk > 1:
        return StopUpdateResult("INVALID", None, None, "invalid_effective_risk")
    if not basis:
        return StopUpdateResult("INVALID", None, None, "invalid_price_basis_id")
    initial = price * (Decimal("1") - risk)
    state = ExitControlState(
        symbol=normalized,
        entry_trade_date=day,
        initial_stop=initial,
        trailing_stop=initial,
        highest_close=price,
        price_basis_id=basis,
    )
    return StopUpdateResult("APPLIED", None, state)


def update_trailing_stop(
    state: ExitControlState,
    *,
    trade_date: date | str,
    daily_close: Decimal | str,
    atr20: Decimal | str,
    price_basis_id: str,
) -> StopUpdateResult:
    """Apply the end-of-day update once; the returned stop is for the next day."""

    day = _date(trade_date)
    if day is None:
        return StopUpdateResult("INVALID", state, state, "invalid_trade_date")
    if str(price_basis_id).strip() != state.price_basis_id:
        return StopUpdateResult("INVALID", state, state, "price_basis_mismatch")
    if state.last_trailing_update_date == day:
        return StopUpdateResult("UNCHANGED", state, state)
    if state.last_trailing_update_date is not None and day < state.last_trailing_update_date:
        return StopUpdateResult("INVALID", state, state, "state_date_regression")
    close = _decimal(daily_close)
    atr = _decimal(atr20)
    if close is None or atr is None or close <= 0 or atr <= 0:
        return StopUpdateResult("UNCHANGED", state, state, "invalid_daily_stop_input")
    highest = max(state.highest_close, close)
    candidate = highest - Decimal("2") * atr
    trailing = max(state.trailing_stop, state.initial_stop, candidate)
    updated = replace(
        state,
        highest_close=highest,
        trailing_stop=trailing,
        last_trailing_update_date=day,
    )
    return StopUpdateResult("APPLIED", state, updated)


def in_soft_exit_protection(
    entry_trade_date: date | str,
    evaluation_date: date | str,
    trading_calendar: Iterable[date | str],
) -> bool:
    """Protect entry day and the first actual trading day after entry."""

    entry = _date(entry_trade_date)
    evaluation = _date(evaluation_date)
    calendar = _calendar(trading_calendar)
    if entry is None or evaluation is None or entry not in calendar:
        return True
    later = [day for day in calendar if day > entry]
    first_after = later[0] if later else entry
    return evaluation <= first_after


def mark_episode_acted(state: ExitControlState, episode_id: str) -> ExitControlState:
    """Record an episode only after its sell FillResult is actually FILLED."""

    value = str(episode_id).strip()
    if not value or value in state.acted_episode_ids:
        return state
    return replace(state, acted_episode_ids=tuple(sorted(state.acted_episode_ids + (value,))))


def record_full_exit(
    state: ExitControlState, reason: str, exit_trade_date: date | str
) -> ExitControlState:
    day = _date(exit_trade_date)
    if day is None:
        return state
    return replace(state, last_full_exit_reason=str(reason), last_full_exit_date=day)


def _calendar(values: Iterable[date | str]) -> list[date]:
    return sorted({day for value in values if (day := _date(value)) is not None})


def _symbol(value: object) -> str | None:
    try:
        return normalize_security_symbol(str(value))
    except ValueError:
        return None


def _date(value: object) -> date | None:
    try:
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.tz_convert(SHANGHAI_TIMEZONE)
        return parsed.date()
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
