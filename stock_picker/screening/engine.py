from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import pandas as pd

from stock_picker.data.models import StockInfo, normalize_symbol, symbol_code


DEFAULT_SCREENING_RULES = (
    "uptrend_20d",
    "close_above_ma30",
    "volume_up",
    "macd_golden_cross",
    "exclude_st",
)


@dataclass(frozen=True)
class ScreeningRunResult:
    results: pd.DataFrame
    errors: pd.DataFrame


RuleFunc = Callable[[pd.DataFrame, StockInfo], bool]


def screen_stocks(
    service: Any,
    symbols: Iterable[str | StockInfo],
    start_date: str,
    end_date: str,
    rules: Iterable[str] | None = None,
    refresh: bool = False,
    skip_errors: bool = True,
    progress_callback: Callable[[int, int, str, str, str], None] | None = None,
    sort_by: str = "score",
) -> ScreeningRunResult:
    selected_rules = _normalize_rules(rules)
    sort_column = _normalize_sort_by(sort_by)
    items = [_stock_item(item) for item in symbols]
    rows: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    total = len(items)

    for index, item in enumerate(items, start=1):
        try:
            history = service.get_history(
                symbol=item.symbol,
                start_date=start_date,
                end_date=end_date,
                refresh=refresh,
                indicators=True,
            )
            record = _evaluate_stock(item, history, selected_rules)
        except Exception as exc:
            if not skip_errors:
                raise
            error = str(exc)
            errors.append(
                {
                    "symbol": item.symbol,
                    "name": item.name,
                    "error": error,
                }
            )
            if progress_callback:
                progress_callback(index, total, item.symbol, "failed", error)
            continue

        if record is not None:
            rows.append(record)
            status = "matched"
        else:
            status = "skipped"
        if progress_callback:
            progress_callback(index, total, item.symbol, status, "")

    results = pd.DataFrame(rows, columns=_result_columns())
    if not results.empty:
        sort_columns = _sort_columns(sort_column)
        results = results.sort_values(
            by=sort_columns,
            ascending=[False] * len(sort_columns),
        ).reset_index(drop=True)
    return ScreeningRunResult(
        results=results,
        errors=pd.DataFrame(errors, columns=["symbol", "name", "error"]),
    )


def available_rules() -> list[str]:
    return sorted(SCREENING_RULES)


def _evaluate_stock(
    item: StockInfo,
    history: pd.DataFrame,
    selected_rules: tuple[str, ...],
) -> dict[str, object] | None:
    frame = _prepare_history(history)
    if frame.empty:
        return None

    rule_passes = {
        rule_name: SCREENING_RULES[rule_name](frame, item)
        for rule_name in selected_rules
    }
    if not all(rule_passes.values()):
        return None

    latest = frame.iloc[-1]
    return {
        "symbol": item.symbol,
        "code": item.code,
        "name": item.name,
        "date": latest.get("date"),
        "close": latest.get("close"),
        "pct_chg": latest.get("pct_chg"),
        "volume": latest.get("volume"),
        "amount": latest.get("amount"),
        "ma5": latest.get("ma5"),
        "ma10": latest.get("ma10"),
        "ma30": latest.get("ma30"),
        "macd_dif": latest.get("macd_dif"),
        "macd_dea": latest.get("macd_dea"),
        "macd": latest.get("macd"),
        "score": sum(1 for passed in rule_passes.values() if passed),
        "matched_rules": ",".join(selected_rules),
    }


def _prepare_history(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return history
    frame = history.copy()
    if "date" in frame:
        frame = frame.sort_values("date")
    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "pct_chg",
        "ma5",
        "ma10",
        "ma30",
        "macd_dif",
        "macd_dea",
        "macd",
    ]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.reset_index(drop=True)


def _normalize_sort_by(sort_by: str) -> str:
    normalized = sort_by.strip().lower()
    allowed = {"score", "pct_chg", "amount", "volume"}
    if normalized not in allowed:
        raise ValueError(
            f"sort_by must be one of: {', '.join(sorted(allowed))}"
        )
    return normalized


def _sort_columns(sort_by: str) -> list[str]:
    columns = [sort_by]
    for fallback in ["score", "pct_chg", "amount", "volume"]:
        if fallback not in columns:
            columns.append(fallback)
    return columns


def _normalize_rules(rules: Iterable[str] | None) -> tuple[str, ...]:
    selected = tuple(rules or DEFAULT_SCREENING_RULES)
    unknown = sorted(set(selected) - set(SCREENING_RULES))
    if unknown:
        raise ValueError(
            "Unknown screening rules: "
            f"{', '.join(unknown)}. Available rules: {', '.join(available_rules())}"
        )
    return selected


def _stock_item(item: str | StockInfo) -> StockInfo:
    if isinstance(item, StockInfo):
        return item
    normalized = normalize_symbol(item)
    return StockInfo(symbol=normalized, code=symbol_code(normalized), name="")


def _latest(frame: pd.DataFrame) -> pd.Series:
    return frame.iloc[-1]


def _previous(frame: pd.DataFrame) -> pd.Series | None:
    if len(frame) < 2:
        return None
    return frame.iloc[-2]


def _is_finite(value: object) -> bool:
    return pd.notna(value)


def _exclude_st(frame: pd.DataFrame, item: StockInfo) -> bool:
    name = item.name.strip().upper()
    return "ST" not in name


def _close_above_ma30(frame: pd.DataFrame, item: StockInfo | None = None) -> bool:
    latest = _latest(frame)
    return _is_finite(latest.get("ma30")) and latest["close"] > latest["ma30"]


def _ma5_above_ma10(frame: pd.DataFrame, item: StockInfo | None = None) -> bool:
    latest = _latest(frame)
    return _is_finite(latest.get("ma10")) and latest["ma5"] > latest["ma10"]


def _ma10_above_ma30(frame: pd.DataFrame, item: StockInfo | None = None) -> bool:
    latest = _latest(frame)
    return _is_finite(latest.get("ma30")) and latest["ma10"] > latest["ma30"]


def _macd_golden_cross(frame: pd.DataFrame, item: StockInfo | None = None) -> bool:
    previous = _previous(frame)
    if previous is None:
        return False
    latest = _latest(frame)
    required = [
        previous.get("macd_dif"),
        previous.get("macd_dea"),
        latest.get("macd_dif"),
        latest.get("macd_dea"),
    ]
    if not all(_is_finite(value) for value in required):
        return False
    return (
        previous["macd_dif"] <= previous["macd_dea"]
        and latest["macd_dif"] > latest["macd_dea"]
    )


def _macd_above_zero(frame: pd.DataFrame, item: StockInfo | None = None) -> bool:
    latest = _latest(frame)
    return (
        _is_finite(latest.get("macd_dif"))
        and _is_finite(latest.get("macd_dea"))
        and latest["macd_dif"] > 0
        and latest["macd_dea"] > 0
    )


def _volume_up(frame: pd.DataFrame, item: StockInfo | None = None) -> bool:
    if len(frame) < 6:
        return False
    latest = _latest(frame)
    baseline = frame["volume"].iloc[-6:-1].mean()
    return (
        _is_finite(baseline)
        and baseline > 0
        and latest["volume"] > baseline * 1.5
    )


def _uptrend_20d(frame: pd.DataFrame, item: StockInfo | None = None) -> bool:
    if len(frame) < 21:
        return False
    window = frame["close"].tail(20)
    base_close = frame["close"].iloc[-21]
    latest_close = frame["close"].iloc[-1]
    if not _is_finite(base_close) or base_close <= 0:
        return False
    higher_highs = window.tail(5).max() >= window.head(5).max()
    above_ma10 = (
        "ma10" in frame
        and _is_finite(frame["ma10"].iloc[-1])
        and latest_close > frame["ma10"].iloc[-1]
    )
    positive_return = latest_close > base_close
    return positive_return and higher_highs and above_ma10


def _return_20d_gt_10(frame: pd.DataFrame, item: StockInfo | None = None) -> bool:
    if len(frame) < 21:
        return False
    latest_close = frame["close"].iloc[-1]
    base_close = frame["close"].iloc[-21]
    return (
        _is_finite(base_close)
        and base_close > 0
        and latest_close / base_close - 1 > 0.10
    )


def _close_near_20d_high(frame: pd.DataFrame, item: StockInfo | None = None) -> bool:
    if len(frame) < 20:
        return False
    latest_close = frame["close"].iloc[-1]
    high_20d = frame["high"].iloc[-20:].max()
    return _is_finite(high_20d) and high_20d > 0 and latest_close >= high_20d * 0.98



def _close_3d_252d_high(frame: pd.DataFrame, item: StockInfo | None = None) -> bool:
    if len(frame) < 252:
        return False
    close = frame["close"]
    recent_close = close.tail(3)
    rolling_high = close.rolling(252, min_periods=252).max().tail(3)
    if recent_close.isna().any() or rolling_high.isna().all():
        return False
    return bool((recent_close >= rolling_high).any())


def _volume_up_3d(frame: pd.DataFrame, item: StockInfo | None = None) -> bool:
    if len(frame) < 8:
        return False
    prior_avg = frame["volume"].iloc[-8:-3].mean()
    recent = frame["volume"].tail(3)
    if not _is_finite(prior_avg) or prior_avg <= 0 or recent.isna().any():
        return False
    return bool((recent > prior_avg * 1.3).all())


SCREENING_RULES: dict[str, RuleFunc] = {
    "close_3d_252d_high": _close_3d_252d_high,
    "exclude_st": _exclude_st,
    "uptrend_20d": _uptrend_20d,
    "close_above_ma30": _close_above_ma30,
    "ma5_above_ma10": _ma5_above_ma10,
    "ma10_above_ma30": _ma10_above_ma30,
    "macd_golden_cross": _macd_golden_cross,
    "macd_above_zero": _macd_above_zero,
    "volume_up": _volume_up,
    "volume_up_3d": _volume_up_3d,
    "return_20d_gt_10": _return_20d_gt_10,
    "close_near_20d_high": _close_near_20d_high,
}


def _result_columns() -> list[str]:
    return [
        "symbol",
        "code",
        "name",
        "date",
        "close",
        "pct_chg",
        "volume",
        "amount",
        "ma5",
        "ma10",
        "ma30",
        "macd_dif",
        "macd_dea",
        "macd",
        "score",
        "matched_rules",
    ]
