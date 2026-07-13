from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Mapping, Sequence

import pandas as pd

from stock_picker.data.limits import estimate_limit_prices, execution_limit_status
from stock_picker.data.models import normalize_symbol, symbol_code
from stock_picker.data.service import MarketDataService


# Ratios are rounded to six decimals. A candidate step must exceed both this floor
# and three times the combined uncertainty from two prices rounded to a CNY 0.01
# tick, then persist through the next aligned trading day.
CORPORATE_ACTION_RATIO_TOLERANCE = 0.0001


@dataclass(frozen=True)
class BacktestDataRequest:
    symbols: Sequence[str]
    start: str
    end: str
    period: str = "daily"
    indicator_adjust: str = "qfq"
    execution_adjust: str = "bfq"
    source: str = "baostock"
    refresh: bool = False
    warmup_trading_days: int = 252


@dataclass
class SymbolBacktestData:
    symbol: str
    indicator_frame: pd.DataFrame
    execution_frame: pd.DataFrame
    available_warmup_count: int
    issues: list[dict[str, object]] = field(default_factory=list)
    buy_eligible: bool = True

    @property
    def qfq_indicator_frame(self) -> pd.DataFrame:
        return self.indicator_frame

    @property
    def bfq_execution_frame(self) -> pd.DataFrame:
        return self.execution_frame


@dataclass
class BacktestDataBundle:
    request: BacktestDataRequest
    symbols: Mapping[str, SymbolBacktestData]
    trading_calendar: tuple[str, ...]
    load_summary: dict[str, int]
    quality_issues: list[dict[str, object]]
    corporate_action_impacts: list[dict[str, object]]

    @property
    def per_symbol(self) -> Mapping[str, SymbolBacktestData]:
        return self.symbols


def load_t1_backtest_data(
    service: MarketDataService,
    request: BacktestDataRequest,
) -> BacktestDataBundle:
    if request.period != "daily":
        raise ValueError("T+1 backtest data supports daily period only")
    start = _date(request.start)
    end = _date(request.end)
    if start > end:
        raise ValueError("backtest start must not be after end")
    if request.warmup_trading_days < 0:
        raise ValueError("warmup_trading_days must be non-negative")

    warmup_start = (pd.Timestamp(start) - timedelta(days=400)).strftime("%Y-%m-%d")
    provider = _history_provider(service, request.source)
    summary = {
        "cache_hits": 0,
        "cache_misses": 0,
        "partial_fetch_ranges": 0,
        "provider_failures": 0,
    }
    bundle_issues: list[dict[str, object]] = []
    impacts: list[dict[str, object]] = []
    normalized_symbols = tuple(dict.fromkeys(normalize_symbol(item) for item in request.symbols))

    try:
        calendar = tuple(
            sorted({_date(item) for item in provider.get_trade_dates(warmup_start, end)})
        )
    except Exception as exc:
        calendar = ()
        bundle_issues.append(
            _issue("missing_trade_calendar", error=str(exc), source=request.source)
        )

    if not calendar:
        if not bundle_issues:
            bundle_issues.append(
                _issue("missing_trade_calendar", source=request.source)
            )
        symbol_data = {
            symbol: SymbolBacktestData(
                symbol=symbol,
                indicator_frame=_empty_frame(),
                execution_frame=_empty_frame(),
                available_warmup_count=0,
                issues=[_issue("missing_history", symbol=symbol)],
                buy_eligible=False,
            )
            for symbol in normalized_symbols
        }
        return BacktestDataBundle(
            request=request,
            symbols=symbol_data,
            trading_calendar=(),
            load_summary=summary,
            quality_issues=bundle_issues,
            corporate_action_impacts=[],
        )

    symbol_data: dict[str, SymbolBacktestData] = {}
    for symbol in normalized_symbols:
        issues: list[dict[str, object]] = []
        streams: dict[str, pd.DataFrame] = {}
        for role, adjust_type in (
            ("indicator", request.indicator_adjust),
            ("execution", request.execution_adjust),
        ):
            validation = service.store.validate_backtest_daily_prices(
                symbol=symbol,
                start_date=warmup_start,
                end_date=end,
                period=request.period,
                adjust_type=adjust_type,
                source=request.source,
                expected_dates=calendar,
                warmup_before=start,
                required_warmup_count=request.warmup_trading_days,
            )
            if request.refresh:
                missing_ranges = [(warmup_start, end)]
            else:
                missing_ranges = _contiguous_ranges(calendar, validation.missing_dates)

            if not missing_ranges and validation.ok:
                summary["cache_hits"] += 1
            elif not missing_ranges:
                summary["cache_misses"] += 1
            else:
                summary["cache_misses"] += 1
                summary["partial_fetch_ranges"] += len(missing_ranges)
                for range_start, range_end in missing_ranges:
                    try:
                        fetched = provider.get_history(
                            symbol=symbol_code(symbol),
                            start_date=range_start,
                            end_date=range_end,
                            period=request.period,
                            adjust=adjust_type,
                        )
                    except Exception as exc:
                        summary["provider_failures"] += 1
                        issues.append(
                            _issue(
                                "missing_history",
                                symbol=symbol,
                                stream=role,
                                start=range_start,
                                end=range_end,
                                error=str(exc),
                            )
                        )
                        continue
                    normalized, normalization_issues = _normalize_provider_frame(
                        fetched,
                        symbol=symbol,
                        period=request.period,
                        adjust_type=adjust_type,
                        source=request.source,
                    )
                    issues.extend(normalization_issues)
                    service.store.save_backtest_daily_prices(normalized)

            frame = service.store.load_backtest_daily_prices(
                symbol,
                warmup_start,
                end,
                request.period,
                adjust_type,
                request.source,
            )
            frame = _enrich_daily_metadata(frame, symbol, calendar)
            service.store.save_backtest_daily_prices(frame)
            streams[role] = frame

            cached_dates = set(frame["date"].astype(str)) if not frame.empty else set()
            remaining = [item for item in calendar if item not in cached_dates]
            if remaining:
                issues.append(
                    _issue(
                        "cache_gap",
                        symbol=symbol,
                        stream=role,
                        dates=remaining,
                    )
                )
                if role == "indicator":
                    issues.append(_issue("missing_history", symbol=symbol, dates=remaining))

        indicator = streams["indicator"]
        execution = streams["execution"]
        indicator_dates = set(indicator["date"].astype(str)) if not indicator.empty else set()
        execution_dates = set(execution["date"].astype(str)) if not execution.empty else set()
        requested_dates = {item for item in calendar if start <= item <= end}
        missing_execution_dates = requested_dates - execution_dates
        if not execution.empty:
            null_execution = execution[
                execution["date"].isin(requested_dates)
                & pd.to_numeric(execution["close"], errors="coerce").isna()
            ]
            missing_execution_dates.update(null_execution["date"].astype(str))
        missing_execution = sorted(missing_execution_dates)
        if missing_execution:
            issues.append(
                _issue("missing_execution_price", symbol=symbol, dates=missing_execution)
            )
        if indicator_dates != execution_dates:
            issues.append(
                _issue(
                    "adjustment_mismatch",
                    symbol=symbol,
                    indicator_only=sorted(indicator_dates - execution_dates),
                    execution_only=sorted(execution_dates - indicator_dates),
                )
            )

        warmup_count = sum(item < start for item in indicator_dates)
        if warmup_count < request.warmup_trading_days:
            issues.append(
                _issue(
                    "insufficient_data",
                    symbol=symbol,
                    available=warmup_count,
                    required=request.warmup_trading_days,
                )
            )
        for row in execution.to_dict("records"):
            warning = str(row.get("warning") or "")
            if warning:
                issues.append(
                    _issue(
                        "limit_metadata_warning",
                        symbol=symbol,
                        date=row["date"],
                        warning=warning,
                    )
                )

        symbol_impacts = _corporate_action_impacts(symbol, indicator, execution)
        impacts.extend(symbol_impacts)
        issues.extend(symbol_impacts)
        symbol_data[symbol] = SymbolBacktestData(
            symbol=symbol,
            indicator_frame=indicator.reset_index(drop=True),
            execution_frame=execution.reset_index(drop=True),
            available_warmup_count=warmup_count,
            issues=issues,
            buy_eligible=(
                warmup_count >= request.warmup_trading_days
                and not missing_execution
                and requested_dates.issubset(indicator_dates)
            ),
        )
        bundle_issues.extend(issues)

    return BacktestDataBundle(
        request=request,
        symbols=symbol_data,
        trading_calendar=calendar,
        load_summary=summary,
        quality_issues=bundle_issues,
        corporate_action_impacts=impacts,
    )


def _history_provider(service: MarketDataService, source: str):
    normalized = service._normalize_source(source)
    configured = service.data_source_config.source_for("history")
    selected = service._normalize_source(configured or service.DEFAULT_SOURCES["history"])
    if normalized == selected:
        return service.history_provider
    return service._provider_for_source("history", normalized)


def _normalize_provider_frame(
    frame: pd.DataFrame,
    *,
    symbol: str,
    period: str,
    adjust_type: str,
    source: str,
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    issues: list[dict[str, object]] = []
    if frame is None or frame.empty:
        return _empty_frame(), issues
    result = frame.copy()
    if "adjust_type" in result:
        actual_adjustments = sorted(
            set(result["adjust_type"].dropna().astype(str).tolist())
        )
        if any(value != adjust_type for value in actual_adjustments):
            issues.append(
                _issue(
                    "adjustment_mismatch",
                    symbol=symbol,
                    requested=adjust_type,
                    actual=actual_adjustments,
                )
            )
            return _empty_frame(), issues
    if "source" in result:
        actual_sources = sorted(set(result["source"].dropna().astype(str).tolist()))
        if any(value != source for value in actual_sources):
            issues.append(
                _issue(
                    "source_mismatch",
                    symbol=symbol,
                    requested=source,
                    actual=actual_sources,
                )
            )
            return _empty_frame(), issues
    result["symbol"] = symbol
    result["date"] = result["date"].map(_date)
    result["period"] = period
    result["adjust_type"] = adjust_type
    result["source"] = source
    for column in ("open", "high", "low", "close", "volume", "amount"):
        if column not in result:
            result[column] = None
    return result, issues


def _enrich_daily_metadata(
    frame: pd.DataFrame,
    symbol: str,
    expected_calendar: Sequence[str],
) -> pd.DataFrame:
    if frame.empty:
        return frame
    result = frame.sort_values("date").drop_duplicates("date", keep="last").copy()
    board = _infer_board(symbol)
    expected_dates = list(expected_calendar)
    expected_index = {date: index for index, date in enumerate(expected_dates)}
    close_by_date = {
        str(row["date"]): _number(row.get("close"))
        for row in result.to_dict("records")
    }
    previous_closes: list[float | None] = []
    warnings: list[str] = []
    up_prices: list[float | None] = []
    down_prices: list[float | None] = []
    suspended: list[bool] = []
    statuses: list[str] = []
    for row in result.to_dict("records"):
        date = str(row["date"])
        calendar_position = expected_index.get(date)
        previous_date = (
            expected_dates[calendar_position - 1]
            if calendar_position is not None and calendar_position > 0
            else None
        )
        previous_close = close_by_date.get(previous_date) if previous_date else None
        previous_closes.append(previous_close)
        close = _number(row.get("close"))
        explicit_suspension = _optional_bool(row.get("is_suspended"))
        limits = estimate_limit_prices(previous_close, board=board)
        status = execution_limit_status(
            close,
            limits.limit_up_price,
            limits.limit_down_price,
            explicit_suspension is True,
        )
        warning_parts = []
        if previous_date is not None and previous_date not in close_by_date:
            warning_parts.append("previous expected trading date is missing")
        elif previous_date is not None and previous_close is None:
            warning_parts.append("previous expected close is unavailable")
        if limits.warning:
            warning_parts.append(limits.warning)
        if close is None:
            warning_parts.append("missing execution price")
        warnings.append("; ".join(warning_parts))
        up_prices.append(limits.limit_up_price)
        down_prices.append(limits.limit_down_price)
        suspended.append(explicit_suspension)
        statuses.append(str(status))
    result["prev_close"] = previous_closes
    result["limit_up_price"] = up_prices
    result["limit_down_price"] = down_prices
    result["is_suspended"] = suspended
    result["limit_status"] = statuses
    result["warning"] = warnings
    return result


def _corporate_action_impacts(
    symbol: str,
    indicator: pd.DataFrame,
    execution: pd.DataFrame,
) -> list[dict[str, object]]:
    if indicator.empty or execution.empty:
        return []
    merged = indicator[["date", "close"]].merge(
        execution[["date", "close"]], on="date", suffixes=("_qfq", "_bfq")
    )
    qfq = pd.to_numeric(merged["close_qfq"], errors="coerce")
    bfq = pd.to_numeric(merged["close_bfq"], errors="coerce")
    merged["ratio"] = (qfq / bfq.where(bfq != 0)).round(6)
    merged["tick_error"] = [
        _ratio_tick_error(qfq_price, bfq_price)
        for qfq_price, bfq_price in zip(qfq, bfq)
    ]
    impacts: list[dict[str, object]] = []
    rows = merged.to_dict("records")
    for index in range(1, len(rows) - 1):
        previous = _number(rows[index - 1].get("ratio"))
        ratio = _number(rows[index].get("ratio"))
        following = _number(rows[index + 1].get("ratio"))
        previous_error = _number(rows[index - 1].get("tick_error"))
        error = _number(rows[index].get("tick_error"))
        following_error = _number(rows[index + 1].get("tick_error"))
        if None in {
            previous,
            ratio,
            following,
            previous_error,
            error,
            following_error,
        }:
            continue
        step_threshold = max(
            CORPORATE_ACTION_RATIO_TOLERANCE,
            3.0 * (previous_error + error),
        )
        persistence_tolerance = max(
            CORPORATE_ACTION_RATIO_TOLERANCE,
            3.0 * (error + following_error),
        )
        following_step_threshold = max(
            CORPORATE_ACTION_RATIO_TOLERANCE,
            3.0 * (previous_error + following_error),
        )
        significant_step = abs(ratio - previous) > step_threshold
        persists = (
            abs(following - ratio) <= persistence_tolerance
            and abs(following - previous) > following_step_threshold
        )
        if significant_step and persists:
            impacts.append(
                _issue(
                    "unsupported_corporate_action",
                    symbol=symbol,
                    date=rows[index]["date"],
                    evidence={
                        "previous_ratio": previous,
                        "current_ratio": ratio,
                        "following_ratio": following,
                        "threshold": CORPORATE_ACTION_RATIO_TOLERANCE,
                        "combined_tick_error": previous_error + error,
                        "persistent_through": rows[index + 1]["date"],
                    },
                )
            )
    return impacts


def _ratio_tick_error(qfq_price: object, bfq_price: object) -> float | None:
    qfq = _number(qfq_price)
    bfq = _number(bfq_price)
    half_tick = 0.005
    if qfq is None or bfq is None or bfq <= half_tick:
        return None
    ratio = qfq / bfq
    lower = max(qfq - half_tick, 0.0) / (bfq + half_tick)
    upper = (qfq + half_tick) / (bfq - half_tick)
    return max(abs(ratio - lower), abs(upper - ratio))


def _contiguous_ranges(
    calendar: Sequence[str], missing_dates: Sequence[str]
) -> list[tuple[str, str]]:
    missing = set(missing_dates)
    ranges: list[tuple[str, str]] = []
    range_start: str | None = None
    previous: str | None = None
    for date in calendar:
        if date in missing:
            if range_start is None:
                range_start = date
            previous = date
        elif range_start is not None:
            ranges.append((range_start, previous or range_start))
            range_start = None
            previous = None
    if range_start is not None:
        ranges.append((range_start, previous or range_start))
    return ranges


def _infer_board(symbol: str) -> str | None:
    code = symbol_code(symbol)
    if code.startswith("688"):
        return "star"
    if code.startswith(("300", "301")):
        return "chinext"
    if symbol.endswith(".SH") and code.startswith(("600", "601", "603", "605")):
        return "main"
    if symbol.endswith(".SZ") and code.startswith(("000", "001", "002", "003")):
        return "main"
    return None


def _number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _optional_bool(value: object) -> bool | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
        return None
    return bool(value)


def _date(value: object) -> str:
    return pd.Timestamp(str(value)).strftime("%Y-%m-%d")


def _issue(code: str, **details: object) -> dict[str, object]:
    return {"code": code, **details}


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "symbol",
            "date",
            "period",
            "adjust_type",
            "source",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "prev_close",
            "limit_up_price",
            "limit_down_price",
            "is_suspended",
            "limit_status",
            "warning",
            "updated_at",
        ]
    )
