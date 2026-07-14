"""Pure A-share five-minute bar validation and execution-time resolution."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable

import pandas as pd

from stock_picker.data.models import is_supported_stock_symbol, normalize_symbol
from stock_picker.strategies.adaptive_trend_v1_3.phase3_models import (
    ExecutionBarResolution,
    MinuteContractResult,
)


SHANGHAI_TIMEZONE = "Asia/Shanghai"
REQUIRED_MINUTE_FIELDS = (
    "symbol",
    "trade_date",
    "bar_start",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_status",
    "limit_status",
)
TRADE_STATUSES = frozenset({"normal", "suspended", "unknown"})
LIMIT_STATUSES = frozenset({"normal", "limit_up", "limit_down", "unknown"})


def normalize_security_symbol(value: object) -> str:
    """Return one canonical A-share code or raise ``ValueError`` stably."""

    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("invalid_symbol")
    stripped = value.strip()
    if not is_supported_stock_symbol(stripped):
        raise ValueError("invalid_symbol")
    return normalize_symbol(stripped)


def _time_range(first: time, last: time) -> list[time]:
    current = datetime.combine(date(2000, 1, 1), first)
    finish = datetime.combine(date(2000, 1, 1), last)
    result: list[time] = []
    while current <= finish:
        result.append(current.time())
        current += timedelta(minutes=5)
    return result


def legal_bar_start_times() -> tuple[time, ...]:
    """Return the 48 legal A-share five-minute ``bar_start`` times."""

    morning = _time_range(time(9, 30), time(11, 25))
    afternoon = _time_range(time(13, 0), time(14, 55))
    return tuple(morning + afternoon)


LEGAL_BAR_START_TIMES = legal_bar_start_times()
_LEGAL_TIME_SET = frozenset(LEGAL_BAR_START_TIMES)


def validate_minute_bars(bars: pd.DataFrame) -> MinuteContractResult:
    """Validate and deterministically canonicalize supplied five-minute bars.

    Naive timestamps are interpreted as Asia/Shanghai. A homogeneous aware
    timezone is converted to Asia/Shanghai; mixed timezone representations are
    INVALID. No missing bar is filled and no neighbouring bar is substituted.
    """

    if not isinstance(bars, pd.DataFrame):
        return _invalid_result(("invalid_bars_type",))
    missing = [field for field in REQUIRED_MINUTE_FIELDS if field not in bars]
    if missing:
        return _invalid_result(
            tuple(f"missing_required_field:{field}" for field in missing)
        )

    rows: list[dict[str, object]] = []
    reasons: list[str] = []
    timezone_kinds: set[str] = set()
    for _, source in bars.loc[:, REQUIRED_MINUTE_FIELDS].iterrows():
        row: dict[str, object] = {}
        try:
            row["symbol"] = normalize_security_symbol(source["symbol"])
        except (TypeError, ValueError):
            reasons.append("invalid_symbol")
            row["symbol"] = str(source["symbol"])

        parsed_bar, timezone_kind = _parse_bar_start(source["bar_start"])
        if parsed_bar is None:
            reasons.append("invalid_bar_start")
        else:
            timezone_kinds.add(timezone_kind)
        row["bar_start"] = parsed_bar

        parsed_trade_date = _parse_trade_date(source["trade_date"])
        if parsed_trade_date is None:
            reasons.append("invalid_trade_date")
        row["trade_date"] = parsed_trade_date

        for field in ("open", "high", "low", "close", "volume"):
            value = _to_decimal(source[field])
            if value is None:
                reasons.append(
                    "invalid_volume" if field == "volume" else "invalid_ohlc"
                )
            row[field] = value

        row["trade_status"] = str(source["trade_status"])
        row["limit_status"] = str(source["limit_status"])
        if row["trade_status"] not in TRADE_STATUSES:
            reasons.append("invalid_trade_status")
        if row["limit_status"] not in LIMIT_STATUSES:
            reasons.append("invalid_limit_status")

        if parsed_bar is not None and parsed_trade_date is not None:
            if parsed_bar.date() != parsed_trade_date:
                reasons.append("trade_date_bar_start_mismatch")
            if parsed_bar.time().replace(tzinfo=None) not in _LEGAL_TIME_SET:
                reasons.append("invalid_bar_start")

        prices = [row[field] for field in ("open", "high", "low", "close")]
        if all(value is not None for value in prices):
            if any(value <= 0 for value in prices):
                reasons.append("invalid_ohlc")
            else:
                open_price, high, low, close = prices
                if high < max(open_price, close, low):
                    reasons.append("invalid_ohlc")
                if low > min(open_price, close, high):
                    reasons.append("invalid_ohlc")
        if row["volume"] is not None and row["volume"] < 0:
            reasons.append("invalid_volume")
        rows.append(row)

    if len(timezone_kinds) > 1:
        reasons.append("invalid_timezone")

    canonical = pd.DataFrame(rows, columns=REQUIRED_MINUTE_FIELDS)
    if not canonical.empty:
        valid_keys = canonical[canonical["bar_start"].notna()]
        duplicate_keys = valid_keys.loc[
            valid_keys.duplicated(["symbol", "bar_start"], keep=False),
            ["symbol", "bar_start"],
        ].drop_duplicates()
        for _, key in duplicate_keys.sort_values(
            ["symbol", "bar_start"], kind="mergesort"
        ).iterrows():
            group = canonical[
                canonical["symbol"].eq(key["symbol"])
                & canonical["bar_start"].eq(key["bar_start"])
            ]
            if len(group.drop_duplicates(list(REQUIRED_MINUTE_FIELDS))) > 1:
                reasons.append(
                    f"conflicting_duplicate_bar:{pd.Timestamp(key['bar_start']):%H:%M}"
                )
        canonical = canonical.drop_duplicates(
            list(REQUIRED_MINUTE_FIELDS), keep="first"
        )
        canonical = canonical.sort_values(
            ["symbol", "trade_date", "bar_start"], kind="mergesort"
        ).reset_index(drop=True)

    reasons = _unique(reasons)
    return MinuteContractResult(
        status="INVALID" if reasons else "VALID",
        bars=canonical,
        invalid_reasons=tuple(reasons),
    )


def validate_target_minute_bars(
    bars: pd.DataFrame,
    *,
    symbol: str,
    bar_start: pd.Timestamp,
) -> MinuteContractResult:
    """Select, then validate only one security's exact execution-bar group.

    Selection uses normalized security codes and Shanghai-local timestamps.
    Rows for other securities or other timestamps are deliberately ignored,
    including malformed unrelated rows. The input frame is never modified.
    """

    if not isinstance(bars, pd.DataFrame):
        return _invalid_result(("invalid_bars_type",))
    if "symbol" not in bars or "bar_start" not in bars:
        missing = tuple(
            f"missing_required_field:{field}"
            for field in ("symbol", "bar_start")
            if field not in bars
        )
        return _invalid_result(missing)

    selected_positions: list[int] = []
    for position, (_, source) in enumerate(
        bars.loc[:, ["symbol", "bar_start"]].iterrows()
    ):
        try:
            row_symbol = normalize_security_symbol(source["symbol"])
        except (TypeError, ValueError):
            continue
        parsed_start, _ = _parse_bar_start(source["bar_start"])
        if row_symbol == symbol and parsed_start == bar_start:
            selected_positions.append(position)

    if not selected_positions:
        return MinuteContractResult(
            status="VALID",
            bars=pd.DataFrame(columns=REQUIRED_MINUTE_FIELDS),
        )
    selected = bars.iloc[selected_positions].copy(deep=True)
    return validate_minute_bars(selected)


def resolve_next_execution_bar(
    signal_bar_start: str | pd.Timestamp,
    trading_calendar: Iterable[date | str | pd.Timestamp],
) -> ExecutionBarResolution:
    """Resolve exactly the next legal trading bar without searching past it."""

    signal, _ = _parse_bar_start(signal_bar_start)
    if signal is None or signal.time().replace(tzinfo=None) not in _LEGAL_TIME_SET:
        return ExecutionBarResolution("INVALID", None, "invalid_signal_bar_start")
    position = LEGAL_BAR_START_TIMES.index(signal.time().replace(tzinfo=None))
    if position < len(LEGAL_BAR_START_TIMES) - 1:
        next_time = LEGAL_BAR_START_TIMES[position + 1]
        target = pd.Timestamp(datetime.combine(signal.date(), next_time)).tz_localize(
            SHANGHAI_TIMEZONE
        )
        return ExecutionBarResolution("VALID", target)

    calendar_dates = sorted(
        {
            parsed
            for value in trading_calendar
            if (parsed := _parse_trade_date(value)) is not None
            and parsed > signal.date()
        }
    )
    if not calendar_dates:
        return ExecutionBarResolution("FAILED", None, "missing_execution_bar")
    target = pd.Timestamp(
        datetime.combine(calendar_dates[0], LEGAL_BAR_START_TIMES[0])
    ).tz_localize(SHANGHAI_TIMEZONE)
    return ExecutionBarResolution("VALID", target)


def _parse_bar_start(value: object) -> tuple[pd.Timestamp | None, str]:
    try:
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            return None, ""
        if parsed.tzinfo is None:
            return parsed.tz_localize(SHANGHAI_TIMEZONE), "naive"
        timezone_kind = str(parsed.tzinfo)
        return parsed.tz_convert(SHANGHAI_TIMEZONE), timezone_kind
    except (TypeError, ValueError, OverflowError):
        return None, ""


def _parse_trade_date(value: object) -> date | None:
    try:
        parsed = pd.Timestamp(value)
        return None if pd.isna(parsed) else parsed.date()
    except (TypeError, ValueError, OverflowError):
        return None


def _to_decimal(value: object) -> Decimal | None:
    try:
        if isinstance(value, bool):
            return None
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _invalid_result(reasons: tuple[str, ...]) -> MinuteContractResult:
    return MinuteContractResult(
        status="INVALID",
        bars=pd.DataFrame(columns=REQUIRED_MINUTE_FIELDS),
        invalid_reasons=reasons,
    )


def _unique(values: list[str]) -> list[str]:
    return sorted(set(values))
