"""Trading-calendar cooldown records for Phase 4B full exits."""

from __future__ import annotations

from datetime import date
from typing import Iterable, Sequence

import pandas as pd

from stock_picker.strategies.adaptive_trend_v1_3.minute_contract import (
    SHANGHAI_TIMEZONE,
    normalize_security_symbol,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase4b_models import CooldownRecord, CooldownStatus


COOLDOWN_DAYS = {
    "EMERGENCY_MARKET": 1,
    "INITIAL_STOP": 5,
    "TRAILING_STOP": 5,
    "STRONG_TOP_DIVERGENCE": 3,
    "MA60_TREND_BREAK": 3,
    "WEAK_SCORE_CONFIRMED": 3,
    "REPLACEMENT_EXIT": 2,
    "PORTFOLIO_EXPOSURE_REDUCTION": 2,
}


def create_cooldown_record(
    *,
    symbol: str,
    exit_reason: str,
    exit_trade_date: date | str,
    trading_calendar: Iterable[date | str],
    full_exit: bool,
) -> CooldownRecord | None:
    """Create cooldown only after a complete exit; partial reductions return None."""

    if not full_exit or exit_reason not in COOLDOWN_DAYS:
        return None
    normalized = _symbol(symbol)
    exit_day = _date(exit_trade_date)
    calendar = _calendar(trading_calendar)
    if normalized is None or exit_day is None or exit_day not in calendar:
        raise ValueError("invalid_cooldown_input")
    later = [day for day in calendar if day > exit_day]
    count = COOLDOWN_DAYS[exit_reason]
    if len(later) <= count:
        raise ValueError("insufficient_trading_calendar")
    blocked = tuple(later[:count])
    return CooldownRecord(
        symbol=normalized,
        exit_reason=exit_reason,
        exit_trade_date=exit_day,
        blocked_trade_dates=blocked,
        reentry_allowed_date=later[count],
        status=CooldownStatus.ACTIVE,
    )


def merge_cooldown_records(records: Sequence[CooldownRecord]) -> CooldownRecord | None:
    """For one normalized symbol, retain the record with the later allowed date."""

    if not records:
        return None
    symbols = {_symbol(record.symbol) for record in records}
    if None in symbols or len(symbols) != 1:
        raise ValueError("cooldown_symbol_conflict")
    return sorted(
        records,
        key=lambda item: (
            item.reentry_allowed_date,
            item.exit_trade_date,
            item.exit_reason,
        ),
    )[-1]


def cooldown_blocked(record: CooldownRecord | None, evaluation_date: date | str) -> bool:
    if record is None:
        return False
    day = _date(evaluation_date)
    return day is None or day < record.reentry_allowed_date


def _calendar(values: Iterable[date | str]) -> list[date]:
    return sorted({day for value in values if (day := _date(value)) is not None})


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


def _symbol(value: object) -> str | None:
    try:
        return normalize_security_symbol(str(value))
    except ValueError:
        return None
