from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd

from stock_picker.data.models import StockInfo, normalize_symbol, symbol_code


STRATEGY_NAMES = (
    "ma_cross",
    "turtle",
    "small_cap",
    "undervalued",
    "bank_rotation",
)
HISTORY_STRATEGY_NAMES = ("ma_cross", "turtle")

RESULT_COLUMNS = [
    "strategy",
    "symbol",
    "code",
    "name",
    "date",
    "action",
    "score",
    "rank",
    "weight",
    "reason",
]

SMALL_CAP_MIN = 20 * 100_000_000
SMALL_CAP_MAX = 30 * 100_000_000


@dataclass(frozen=True)
class StrategyRunResult:
    results: pd.DataFrame
    errors: pd.DataFrame


def run_strategy(
    service: Any,
    strategy: str,
    start_date: str | None = None,
    end_date: str | None = None,
    as_of: str | None = None,
    symbols: Iterable[str | StockInfo] | None = None,
    top: int | None = None,
    refresh: bool = False,
    skip_errors: bool = True,
) -> StrategyRunResult:
    normalized = strategy.strip().lower()
    if normalized not in STRATEGY_NAMES:
        raise ValueError(f"strategy must be one of: {', '.join(STRATEGY_NAMES)}")

    if normalized in HISTORY_STRATEGY_NAMES:
        return _run_history_strategy(
            service,
            strategy=normalized,
            evaluator=_history_evaluator(normalized),
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            refresh=refresh,
            skip_errors=skip_errors,
            top=top,
        )
    if normalized == "small_cap":
        return _run_small_cap(
            service,
            symbols=symbols,
            as_of=as_of or end_date,
            top=top or 3,
            skip_errors=skip_errors,
        )
    if normalized == "undervalued":
        return _run_undervalued(
            service,
            symbols=symbols,
            as_of=as_of or end_date,
            top=top or 10,
            skip_errors=skip_errors,
        )
    return _run_bank_rotation(service, symbols=symbols, as_of=as_of or end_date)


def evaluate_history_strategy(
    strategy: str,
    history: pd.DataFrame,
    item: StockInfo,
) -> dict[str, object] | None:
    normalized = strategy.strip().lower()
    if normalized not in HISTORY_STRATEGY_NAMES:
        raise ValueError(
            f"history strategy must be one of: {', '.join(HISTORY_STRATEGY_NAMES)}"
        )
    return _history_evaluator(normalized)(history, item)


def _history_evaluator(strategy: str):
    if strategy == "ma_cross":
        return _evaluate_ma_cross
    if strategy == "turtle":
        return _evaluate_turtle
    raise ValueError(
        f"history strategy must be one of: {', '.join(HISTORY_STRATEGY_NAMES)}"
    )


def _run_history_strategy(
    service: Any,
    strategy: str,
    evaluator,
    symbols: Iterable[str | StockInfo] | None,
    start_date: str | None,
    end_date: str | None,
    refresh: bool,
    skip_errors: bool,
    top: int | None,
) -> StrategyRunResult:
    if not start_date or not end_date:
        raise ValueError(f"{strategy} requires start_date and end_date")
    items = [_stock_item(item) for item in _require_symbols(symbols, strategy)]
    rows: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []

    for item in items:
        try:
            history = service.get_history(
                symbol=item.symbol,
                start_date=start_date,
                end_date=end_date,
                refresh=refresh,
                indicators=True,
            )
            record = evaluator(history, item)
        except Exception as exc:
            if not skip_errors:
                raise
            errors.append({"symbol": item.symbol, "name": item.name, "error": str(exc)})
            continue
        if record is not None:
            rows.append(record)

    results = _finalize_results(pd.DataFrame(rows, columns=RESULT_COLUMNS), top=top)
    return StrategyRunResult(
        results=results,
        errors=pd.DataFrame(errors, columns=["symbol", "name", "error"]),
    )


def _evaluate_ma_cross(history: pd.DataFrame, item: StockInfo) -> dict[str, object] | None:
    frame = _prepare_history(history)
    if frame.empty:
        return None
    latest = frame.iloc[-1]
    close = latest.get("close")
    ma5 = latest.get("ma5")
    if not _is_finite(close) or not _is_finite(ma5):
        action = "hold"
        score = 0.0
        reason = "insufficient MA5 data"
    elif close > ma5 * 1.01:
        action = "buy"
        score = 1.0
        reason = f"close {close:.2f} is above MA5 {ma5:.2f} by more than 1%"
    elif close < ma5:
        action = "sell"
        score = -1.0
        reason = f"close {close:.2f} is below MA5 {ma5:.2f}"
    else:
        action = "hold"
        score = 0.0
        reason = f"close {close:.2f} is near MA5 {ma5:.2f}"
    return _result_row("ma_cross", item, latest.get("date"), action, score, reason)


def _evaluate_turtle(history: pd.DataFrame, item: StockInfo) -> dict[str, object] | None:
    frame = _prepare_history(history)
    if frame.empty:
        return None
    latest = frame.iloc[-1]
    close = latest.get("close")
    if len(frame) < 21 or not _is_finite(close):
        return _result_row(
            "turtle",
            item,
            latest.get("date"),
            "hold",
            0.0,
            "insufficient 20-day channel data",
        )

    entry_high = frame["high"].iloc[-21:-1].max()
    exit_low = frame["low"].iloc[-11:-1].min()
    atr = _average_true_range(frame.tail(21))
    if _is_finite(entry_high) and close > entry_high:
        action = "buy"
        score = close / entry_high - 1 if entry_high > 0 else 1.0
        reason = f"close {close:.2f} broke 20-day high {entry_high:.2f}; N={atr:.3f}"
    elif _is_finite(exit_low) and close < exit_low:
        action = "sell"
        score = -1.0
        reason = f"close {close:.2f} fell below 10-day low {exit_low:.2f}; N={atr:.3f}"
    else:
        action = "hold"
        score = 0.0
        reason = f"inside channel high {entry_high:.2f} low {exit_low:.2f}; N={atr:.3f}"
    return _result_row("turtle", item, latest.get("date"), action, score, reason)


def _run_small_cap(
    service: Any,
    symbols: Iterable[str | StockInfo] | None,
    as_of: str | None,
    top: int,
    skip_errors: bool,
) -> StrategyRunResult:
    if symbols:
        return _run_small_cap_from_valuation_history(
            service,
            symbols=symbols,
            as_of=as_of,
            top=top,
            skip_errors=skip_errors,
        )
    frame = _prepare_snapshot(service.get_market_snapshot())
    if frame.empty:
        return StrategyRunResult(_empty_results(), _empty_errors())

    candidates = frame[
        frame["market_cap"].between(SMALL_CAP_MIN, SMALL_CAP_MAX, inclusive="both")
    ].sort_values(["market_cap", "pb"], na_position="last")
    rows = []
    for rank, row in enumerate(candidates.head(top).itertuples(index=False), start=1):
        market_cap_yi = row.market_cap / 100_000_000
        rows.append(
            _result_row(
                "small_cap",
                _stock_from_row(row),
                as_of,
                "buy",
                float(SMALL_CAP_MAX - row.market_cap),
                f"market cap {market_cap_yi:.2f} yi within 20-30 yi range",
                rank=rank,
                weight=1 / top if top else None,
            )
        )
    return StrategyRunResult(_finalize_results(pd.DataFrame(rows)), _empty_errors())


def _run_small_cap_from_valuation_history(
    service: Any,
    symbols: Iterable[str | StockInfo],
    as_of: str | None,
    top: int,
    skip_errors: bool,
) -> StrategyRunResult:
    rows: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    for item in [_stock_item(item) for item in symbols]:
        try:
            valuation = service.get_valuation_history(
                item.symbol,
                indicator="总市值",
                period="近一年",
            )
            latest = _valuation_as_of(valuation, as_of)
            if latest is None:
                continue
        except Exception as exc:
            if not skip_errors:
                raise
            errors.append({"symbol": item.symbol, "name": item.name, "error": str(exc)})
            continue
        market_cap = float(latest["value"]) * 100_000_000
        if SMALL_CAP_MIN <= market_cap <= SMALL_CAP_MAX:
            rows.append(
                {
                    "symbol": item.symbol,
                    "code": item.code,
                    "name": item.name,
                    "date": latest["date"],
                    "market_cap": market_cap,
                    "pb": pd.NA,
                }
            )

    candidates = pd.DataFrame(rows)
    if candidates.empty:
        return StrategyRunResult(
            _empty_results(),
            pd.DataFrame(errors, columns=["symbol", "name", "error"]),
        )
    candidates = candidates.sort_values("market_cap", na_position="last").head(top)
    result_rows = []
    for rank, row in enumerate(candidates.itertuples(index=False), start=1):
        market_cap_yi = row.market_cap / 100_000_000
        result_rows.append(
            _result_row(
                "small_cap",
                StockInfo(symbol=row.symbol, code=row.code, name=row.name),
                row.date,
                "buy",
                float(SMALL_CAP_MAX - row.market_cap),
                f"historical market cap {market_cap_yi:.2f} yi within 20-30 yi range",
                rank=rank,
                weight=1 / top if top else None,
            )
        )
    return StrategyRunResult(
        _finalize_results(pd.DataFrame(result_rows)),
        pd.DataFrame(errors, columns=["symbol", "name", "error"]),
    )


def _run_undervalued(
    service: Any,
    symbols: Iterable[str | StockInfo] | None,
    as_of: str | None,
    top: int,
    skip_errors: bool,
) -> StrategyRunResult:
    if symbols:
        return _run_undervalued_from_history(
            service,
            symbols=symbols,
            as_of=as_of,
            top=top,
            skip_errors=skip_errors,
        )
    snapshot = _prepare_snapshot(service.get_market_snapshot())
    if snapshot.empty:
        return StrategyRunResult(_empty_results(), _empty_errors())

    candidates = snapshot[(snapshot["pb"] > 0) & (snapshot["pb"] < 2)].copy()
    rows: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    for row in candidates.itertuples(index=False):
        item = _stock_from_row(row)
        try:
            financial = _latest_financial_row(
                service.get_financial_indicators(item.symbol, start_year="1900")
            )
        except Exception as exc:
            errors.append({"symbol": item.symbol, "name": item.name, "error": str(exc)})
            continue
        if financial is None:
            continue
        rows.append(
            {
                "symbol": item.symbol,
                "code": item.code,
                "name": item.name,
                "date": financial.get("date") or as_of,
                "pb": row.pb,
                "current_ratio": financial.get("current_ratio"),
                "debt_asset_ratio": financial.get("debt_asset_ratio"),
            }
        )

    base = pd.DataFrame(rows)
    if base.empty:
        return StrategyRunResult(_empty_results(), pd.DataFrame(errors))

    for column in ["pb", "current_ratio", "debt_asset_ratio"]:
        base[column] = pd.to_numeric(base[column], errors="coerce")
    liquid = base[base["current_ratio"] > 1.2].copy()
    debt_median = liquid["debt_asset_ratio"].median()
    selected = liquid[liquid["debt_asset_ratio"] > debt_median].copy()
    if selected.empty:
        return StrategyRunResult(_empty_results(), pd.DataFrame(errors))

    selected["score_value"] = (
        (2 - selected["pb"])
        + (selected["current_ratio"] - 1.2)
        + selected["debt_asset_ratio"] / 100
    )
    selected = selected.sort_values(
        ["score_value", "pb"],
        ascending=[False, True],
        na_position="last",
    ).head(top)

    result_rows = []
    for rank, row in enumerate(selected.itertuples(index=False), start=1):
        item = StockInfo(symbol=row.symbol, code=row.code, name=row.name)
        result_rows.append(
            _result_row(
                "undervalued",
                item,
                row.date or as_of,
                "buy",
                float(row.score_value),
                (
                    f"PB {row.pb:.2f}, current ratio {row.current_ratio:.2f}, "
                    f"debt/assets {row.debt_asset_ratio:.2f}% above median {debt_median:.2f}%"
                ),
                rank=rank,
                weight=1 / top if top else None,
            )
        )
    return StrategyRunResult(
        _finalize_results(pd.DataFrame(result_rows)),
        pd.DataFrame(errors, columns=["symbol", "name", "error"]),
    )


def _run_undervalued_from_history(
    service: Any,
    symbols: Iterable[str | StockInfo],
    as_of: str | None,
    top: int,
    skip_errors: bool,
) -> StrategyRunResult:
    rows: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    for item in [_stock_item(item) for item in symbols]:
        try:
            pb_row = _valuation_as_of(
                service.get_valuation_history(item.symbol, indicator="市净率", period="近一年"),
                as_of,
            )
            financial = _latest_financial_row(
                service.get_financial_indicators(item.symbol, start_year="1900")
            )
            if pb_row is None or financial is None:
                continue
            rows.append(
                {
                    "symbol": item.symbol,
                    "code": item.code,
                    "name": item.name,
                    "date": pb_row["date"],
                    "pb": pb_row["value"],
                    "current_ratio": financial.get("current_ratio"),
                    "debt_asset_ratio": financial.get("debt_asset_ratio"),
                }
            )
        except Exception as exc:
            if not skip_errors:
                raise
            errors.append({"symbol": item.symbol, "name": item.name, "error": str(exc)})
    return _undervalued_result_from_rows(rows, errors, as_of, top)


def _undervalued_result_from_rows(
    rows: list[dict[str, object]],
    errors: list[dict[str, object]],
    as_of: str | None,
    top: int,
) -> StrategyRunResult:
    base = pd.DataFrame(rows)
    if base.empty:
        return StrategyRunResult(_empty_results(), pd.DataFrame(errors))
    for column in ["pb", "current_ratio", "debt_asset_ratio"]:
        base[column] = pd.to_numeric(base[column], errors="coerce")
    liquid = base[(base["pb"] > 0) & (base["pb"] < 2) & (base["current_ratio"] > 1.2)].copy()
    debt_median = liquid["debt_asset_ratio"].median()
    selected = liquid[liquid["debt_asset_ratio"] > debt_median].copy()
    if selected.empty:
        return StrategyRunResult(_empty_results(), pd.DataFrame(errors))
    selected["score_value"] = (
        (2 - selected["pb"])
        + (selected["current_ratio"] - 1.2)
        + selected["debt_asset_ratio"] / 100
    )
    selected = selected.sort_values(
        ["score_value", "pb"],
        ascending=[False, True],
        na_position="last",
    ).head(top)

    result_rows = []
    for rank, row in enumerate(selected.itertuples(index=False), start=1):
        result_rows.append(
            _result_row(
                "undervalued",
                StockInfo(symbol=row.symbol, code=row.code, name=row.name),
                row.date or as_of,
                "buy",
                float(row.score_value),
                (
                    f"PB {row.pb:.2f}, current ratio {row.current_ratio:.2f}, "
                    f"debt/assets {row.debt_asset_ratio:.2f}% above median {debt_median:.2f}%"
                ),
                rank=rank,
                weight=1 / top if top else None,
            )
        )
    return StrategyRunResult(
        _finalize_results(pd.DataFrame(result_rows)),
        pd.DataFrame(errors, columns=["symbol", "name", "error"]),
    )


def _run_bank_rotation(
    service: Any,
    symbols: Iterable[str | StockInfo] | None,
    as_of: str | None,
) -> StrategyRunResult:
    members = _bank_members_from_symbols(symbols) if symbols else _bank_members(service)
    if members.empty:
        return StrategyRunResult(_empty_results(), _empty_errors())

    rows = []
    for row in members.itertuples(index=False):
        item = _stock_from_row(row)
        pb = getattr(row, "pb", pd.NA)
        date = as_of
        if not _is_finite(pb):
            try:
                pb_row = _valuation_as_of(
                    service.get_valuation_history(item.symbol, indicator="市净率", period="近一年"),
                    as_of,
                )
            except Exception:
                pb_row = None
            if pb_row is None:
                continue
            pb = pb_row["value"]
            date = pb_row["date"]
        rows.append(
            {
                "symbol": item.symbol,
                "code": item.code,
                "name": item.name,
                "date": date,
                "pb": pb,
            }
        )
    snapshot = _prepare_snapshot(pd.DataFrame(rows))
    if snapshot.empty:
        return StrategyRunResult(_empty_results(), _empty_errors())

    candidates = snapshot[(snapshot["pb"] > 0)].sort_values("pb", na_position="last")
    if candidates.empty:
        return StrategyRunResult(_empty_results(), _empty_errors())

    row = candidates.iloc[0]
    item = StockInfo(
        symbol=str(row["symbol"]),
        code=str(row.get("code") or symbol_code(str(row["symbol"]))),
        name=str(row.get("name") or ""),
    )
    result = _result_row(
        "bank_rotation",
        item,
        row.get("date") or as_of,
        "buy",
        float(2 - row["pb"]),
        f"lowest PB in bank universe: {row['pb']:.2f}",
        rank=1,
        weight=1.0,
    )
    return StrategyRunResult(_finalize_results(pd.DataFrame([result])), _empty_errors())


def _bank_members_from_symbols(symbols: Iterable[str | StockInfo] | None) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "symbol": item.symbol,
                "code": item.code,
                "name": item.name,
                "pb": pd.NA,
            }
            for item in (_stock_item(value) for value in symbols)
        ]
    )


def _bank_members(service: Any) -> pd.DataFrame:
    try:
        members = service.get_index_members("399951")
        if not members.empty:
            return members
    except Exception:
        pass
    try:
        return service.get_board_members("industry", "\u94f6\u884c")
    except Exception:
        return pd.DataFrame()


def _require_symbols(
    symbols: Iterable[str | StockInfo] | None,
    strategy: str,
) -> list[str | StockInfo]:
    items = list(symbols or [])
    if not items:
        raise ValueError(f"{strategy} requires --symbol, --symbols, or --all")
    return items


def _stock_item(item: str | StockInfo) -> StockInfo:
    if isinstance(item, StockInfo):
        return item
    normalized = normalize_symbol(item)
    return StockInfo(symbol=normalized, code=symbol_code(normalized), name="")


def _stock_from_row(row: Any) -> StockInfo:
    symbol = str(getattr(row, "symbol"))
    code = str(getattr(row, "code", symbol_code(symbol)) or symbol_code(symbol))
    name = str(getattr(row, "name", "") or "")
    return StockInfo(symbol=symbol, code=code, name=name)


def _prepare_history(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return history
    frame = history.copy()
    if "date" in frame:
        frame = frame.sort_values("date")
    for column in ["open", "high", "low", "close", "ma5"]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.reset_index(drop=True)


def _prepare_snapshot(snapshot: pd.DataFrame) -> pd.DataFrame:
    if snapshot.empty:
        return snapshot
    frame = snapshot.copy()
    if "symbol" not in frame and "code" in frame:
        frame["symbol"] = frame["code"].map(normalize_symbol)
    for column in ["market_cap", "pb", "current_ratio", "debt_asset_ratio"]:
        if column not in frame:
            frame[column] = pd.NA
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.reset_index(drop=True)


def _latest_financial_row(frame: pd.DataFrame) -> pd.Series | None:
    if frame.empty:
        return None
    data = frame.copy()
    if "date" in data:
        data = data.sort_values("date")
    return data.iloc[-1]


def _valuation_as_of(frame: pd.DataFrame, as_of: str | None) -> pd.Series | None:
    if frame.empty:
        return None
    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data = data.dropna(subset=["date"]).sort_values("date")
    if as_of:
        cutoff = pd.to_datetime(as_of, errors="coerce")
        if pd.notna(cutoff):
            data = data[data["date"] <= cutoff]
    data = data.dropna(subset=["value"])
    if data.empty:
        return None
    latest = data.iloc[-1].copy()
    latest["date"] = latest["date"].strftime("%Y-%m-%d")
    return latest


def _average_true_range(frame: pd.DataFrame) -> float:
    highs = pd.to_numeric(frame["high"], errors="coerce")
    lows = pd.to_numeric(frame["low"], errors="coerce")
    closes = pd.to_numeric(frame["close"], errors="coerce")
    previous_close = closes.shift(1)
    ranges = pd.concat(
        [
            highs - lows,
            (highs - previous_close).abs(),
            (lows - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    value = ranges.tail(20).mean()
    return float(value) if _is_finite(value) else float("nan")


def _result_row(
    strategy: str,
    item: StockInfo,
    date: object,
    action: str,
    score: float,
    reason: str,
    rank: int | None = None,
    weight: float | None = None,
) -> dict[str, object]:
    return {
        "strategy": strategy,
        "symbol": item.symbol,
        "code": item.code,
        "name": item.name,
        "date": date,
        "action": action,
        "score": score,
        "rank": rank,
        "weight": weight,
        "reason": reason,
    }


def _finalize_results(frame: pd.DataFrame, top: int | None = None) -> pd.DataFrame:
    if frame.empty:
        return _empty_results()
    result = frame.copy()
    for column in RESULT_COLUMNS:
        if column not in result:
            result[column] = pd.NA
    if "rank" not in frame or result["rank"].isna().all():
        result = result.sort_values("score", ascending=False).reset_index(drop=True)
        result["rank"] = range(1, len(result) + 1)
    if top is not None:
        result = result.head(top)
    return result[RESULT_COLUMNS].reset_index(drop=True)


def _empty_results() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def _empty_errors() -> pd.DataFrame:
    return pd.DataFrame(columns=["symbol", "name", "error"])


def _is_finite(value: object) -> bool:
    return pd.notna(value)
