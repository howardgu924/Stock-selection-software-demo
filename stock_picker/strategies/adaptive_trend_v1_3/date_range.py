"""Trading-calendar based Phase 5 date range resolution."""

from __future__ import annotations

from datetime import date
from typing import Iterable

import pandas as pd

from .phase5_models import DateRangeKind, DateRangeSpec, Phase5Error, ResolvedDateRange

WARMUP_TRADING_DAYS = 320


def resolve_date_range(
    spec: DateRangeSpec,
    trading_calendar: Iterable[date | str | pd.Timestamp],
    *,
    as_of: date | str | pd.Timestamp | None = None,
) -> ResolvedDateRange:
    calendar = _calendar(trading_calendar)
    if not calendar:
        raise Phase5Error("INVALID_DATE_RANGE", "empty_trading_calendar")
    end_hint = _date(as_of) if as_of is not None else calendar[-1]
    if end_hint is None:
        raise Phase5Error("INVALID_DATE_RANGE", "invalid_as_of")
    try:
        kind = DateRangeKind(spec.kind)
    except (ValueError, TypeError):
        raise Phase5Error("INVALID_DATE_RANGE", "invalid_range_kind") from None

    if kind == DateRangeKind.CUSTOM:
        requested_start, requested_end = _date(spec.start_date), _date(spec.end_date)
        if requested_start is None or requested_end is None or requested_start > requested_end:
            raise Phase5Error("INVALID_DATE_RANGE", "invalid_custom_range")
    else:
        if type(spec.value) is not int or spec.value <= 0:
            raise Phase5Error("INVALID_DATE_RANGE", "invalid_recent_value")
        requested_end = end_hint
        offset = pd.DateOffset(months=spec.value) if kind == DateRangeKind.RECENT_MONTHS else pd.DateOffset(years=spec.value)
        requested_start = (pd.Timestamp(requested_end) - offset).date()

    actual = tuple(day for day in calendar if requested_start <= day <= requested_end)
    if not actual:
        raise Phase5Error("INVALID_DATE_RANGE", "no_trading_dates")
    first_index = calendar.index(actual[0])
    if first_index < WARMUP_TRADING_DAYS:
        raise Phase5Error("INSUFFICIENT_WARMUP")
    warmup = tuple(calendar[first_index - WARMUP_TRADING_DAYS:first_index])
    return ResolvedDateRange(
        requested_start_date=requested_start,
        requested_end_date=requested_end,
        actual_start_date=actual[0],
        actual_end_date=actual[-1],
        warmup_start_date=warmup[0],
        trading_dates=actual,
        warmup_dates=warmup,
    )


def _calendar(values: Iterable[object]) -> list[date]:
    parsed = {_date(value) for value in values}
    return sorted(value for value in parsed if value is not None)


def _date(value: object) -> date | None:
    try:
        parsed = pd.Timestamp(value)
        return None if pd.isna(parsed) else parsed.date()
    except (TypeError, ValueError, OverflowError):
        return None
