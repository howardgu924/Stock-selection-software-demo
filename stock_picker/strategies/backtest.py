from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Iterable

import pandas as pd

from stock_picker.data.models import StockInfo, normalize_symbol, symbol_code
from stock_picker.strategies.engine import (
    HISTORY_STRATEGY_NAMES,
    evaluate_history_strategy,
)


EQUITY_COLUMNS = [
    "date",
    "cash",
    "position_value",
    "total_value",
    "daily_return",
    "drawdown",
]
TRADE_COLUMNS = [
    "date",
    "symbol",
    "action",
    "price",
    "shares",
    "cash_after",
    "total_value",
    "reason",
]
SUMMARY_COLUMNS = [
    "strategy",
    "start_date",
    "end_date",
    "initial_cash",
    "final_value",
    "total_return",
    "annualized_return",
    "annualized_volatility",
    "sharpe_ratio",
    "max_drawdown",
    "benchmark_symbol",
    "benchmark_return",
    "excess_return",
    "trade_count",
]
BACKTEST_STRATEGY_NAMES = (*HISTORY_STRATEGY_NAMES, "bank_rotation")
EXECUTION_TIMINGS = ("next_open", "same_day_pm_open")


@dataclass(frozen=True)
class BacktestRunResult:
    summary: pd.DataFrame
    equity: pd.DataFrame
    trades: pd.DataFrame
    errors: pd.DataFrame


def backtest_strategy(
    service: Any,
    strategy: str,
    symbols: Iterable[str | StockInfo],
    start_date: str,
    end_date: str,
    initial_cash: float = 100_000.0,
    commission_rate: float = 0.0003,
    stamp_tax_rate: float = 0.001,
    slippage_rate: float = 0.0,
    lot_size: int = 100,
    max_positions: int = 5,
    execution_timing: str = "next_open",
    minute_period: str = "5",
    warmup_days: int = 0,
    refresh: bool = False,
    skip_errors: bool = True,
) -> BacktestRunResult:
    normalized = strategy.strip().lower()
    timing = execution_timing.strip().lower()
    if timing not in EXECUTION_TIMINGS:
        raise ValueError(
            "execution_timing must be one of: " + ", ".join(EXECUTION_TIMINGS)
        )
    if normalized == "bank_rotation":
        return _backtest_bank_rotation(
            service=service,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            commission_rate=commission_rate,
            stamp_tax_rate=stamp_tax_rate,
            slippage_rate=slippage_rate,
            lot_size=lot_size,
            refresh=refresh,
            skip_errors=skip_errors,
        )
    if normalized not in HISTORY_STRATEGY_NAMES:
        raise ValueError(
            "backtest currently supports history-price strategies only: "
            f"{', '.join(BACKTEST_STRATEGY_NAMES)}"
        )
    items = [_stock_item(item) for item in symbols]
    if not items:
        raise ValueError("backtest requires at least one symbol")
    if initial_cash <= 0:
        raise ValueError("initial_cash must be greater than 0")
    if max_positions < 1:
        raise ValueError("max_positions must be greater than 0")
    if warmup_days < 0:
        raise ValueError("warmup_days must be greater than or equal to 0")

    if timing == "same_day_pm_open" and normalized != "turtle":
        raise ValueError("same_day_pm_open execution currently supports turtle only")

    fetch_start_date = _warmup_start_date(start_date, warmup_days)
    histories: dict[str, pd.DataFrame] = {}
    errors: list[dict[str, object]] = []
    for item in items:
        try:
            history = service.get_history(
                symbol=item.symbol,
                start_date=fetch_start_date,
                end_date=end_date,
                refresh=refresh,
                indicators=True,
            )
            frame = _prepare_history(history)
            if frame.empty:
                raise ValueError("no historical rows returned")
            histories[item.symbol] = frame
        except Exception as exc:
            if not skip_errors:
                raise
            errors.append({"symbol": item.symbol, "name": item.name, "error": str(exc)})

    if not histories:
        return BacktestRunResult(
            summary=_empty_summary(normalized, start_date, end_date, initial_cash),
            equity=_empty_equity(),
            trades=_empty_trades(),
            errors=pd.DataFrame(errors, columns=["symbol", "name", "error"]),
        )

    if timing == "same_day_pm_open":
        return _backtest_history_same_day_pm_open(
            service=service,
            strategy=normalized,
            items=items,
            histories=histories,
            errors=errors,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            commission_rate=commission_rate,
            stamp_tax_rate=stamp_tax_rate,
            slippage_rate=slippage_rate,
            lot_size=lot_size,
            max_positions=max_positions,
            refresh=refresh,
            skip_errors=skip_errors,
            minute_period=minute_period,
        )

    cash = float(initial_cash)
    positions = {symbol: 0 for symbol in histories}
    start_key = _compact_date(start_date)
    end_key = _compact_date(end_date)
    dates = [
        date
        for date in sorted(set().union(*(set(frame["date"]) for frame in histories.values())))
        if start_key <= date <= end_key
    ]
    equity_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []
    pending_orders: list[dict[str, object]] = []
    peak_value = float(initial_cash)
    previous_value: float | None = None

    for date in dates:
        latest_prices = {
            symbol: _latest_close_on_or_before(frame, date)
            for symbol, frame in histories.items()
        }
        executable_orders: list[dict[str, object]] = []
        remaining_orders: list[dict[str, object]] = []
        for order in pending_orders:
            symbol = str(order["symbol"])
            price = _price_on_date(histories[symbol], date, "open")
            if _is_finite(price) and price > 0:
                executable_orders.append({**order, "price": price})
            else:
                remaining_orders.append(order)
        pending_orders = remaining_orders

        for order in executable_orders:
            if order["action"] != "sell":
                continue
            symbol = str(order["symbol"])
            current_position = positions[symbol]
            if current_position <= 0:
                continue
            price = _execution_price(float(order["price"]), "sell", slippage_rate)
            proceeds = current_position * price
            fee = max(proceeds * commission_rate, 5.0)
            tax = proceeds * stamp_tax_rate
            cash += proceeds - fee - tax
            positions[symbol] = 0
            trade_rows.append(
                _trade_row(
                    date,
                    symbol,
                    "sell",
                    price,
                    current_position,
                    cash,
                    _portfolio_value(cash, positions, latest_prices),
                    str(order["reason"]),
                )
            )

        buy_orders = [order for order in executable_orders if order["action"] == "buy"]
        buy_orders.sort(key=lambda order: float(order.get("score") or 0.0), reverse=True)
        for order in buy_orders:
            symbol = str(order["symbol"])
            held_count = _held_position_count(positions)
            if held_count >= max_positions:
                break
            if positions[symbol] > 0:
                continue
            price = _execution_price(float(order["price"]), "buy", slippage_rate)
            target_cash = cash / max(1, max_positions - held_count)
            shares = _lot_shares(target_cash / price, lot_size)
            if shares <= 0:
                continue
            cost = shares * price
            fee = max(cost * commission_rate, 5.0)
            if cost + fee > cash:
                shares = _lot_shares((cash - 5.0) / price, lot_size)
                cost = shares * price
                fee = max(cost * commission_rate, 5.0) if shares > 0 else 0.0
            if shares <= 0 or cost + fee > cash:
                continue
            cash -= cost + fee
            positions[symbol] += shares
            trade_rows.append(
                _trade_row(
                    date,
                    symbol,
                    "buy",
                    price,
                    shares,
                    cash,
                    _portfolio_value(cash, positions, latest_prices),
                    str(order["reason"]),
                )
            )

        for item in items:
            if item.symbol not in histories:
                continue
            frame = histories[item.symbol]
            history_to_date = frame[frame["date"] <= date].reset_index(drop=True)
            if history_to_date.empty or history_to_date.iloc[-1]["date"] != date:
                continue
            signal = evaluate_history_strategy(normalized, history_to_date, item)
            if signal is None:
                continue
            close = history_to_date.iloc[-1]["close"]
            if not _is_finite(close) or close <= 0:
                continue
            current_position = positions[item.symbol]
            if signal["action"] == "sell" and current_position > 0:
                pending_orders.append(
                    _pending_order(item.symbol, "sell", 0.0, str(signal["reason"]), date)
                )
            elif signal["action"] == "buy" and current_position == 0:
                pending_orders.append(
                    _pending_order(
                        item.symbol,
                        "buy",
                        float(signal.get("score") or 0.0),
                        str(signal["reason"]),
                        date,
                    )
                )

        total_value = _portfolio_value(cash, positions, latest_prices)
        peak_value = max(peak_value, total_value)
        daily_return = (
            0.0
            if previous_value is None or previous_value == 0
            else total_value / previous_value - 1
        )
        previous_value = total_value
        equity_rows.append(
            {
                "date": date,
                "cash": cash,
                "position_value": total_value - cash,
                "total_value": total_value,
                "daily_return": daily_return,
                "drawdown": total_value / peak_value - 1 if peak_value else 0.0,
            }
        )

    if dates:
        cash = _liquidate_positions(
            date=dates[-1],
            cash=cash,
            positions=positions,
            prices={
                symbol: _latest_close_on_or_before(histories[symbol], dates[-1])
                for symbol in histories
            },
            commission_rate=commission_rate,
            stamp_tax_rate=stamp_tax_rate,
            slippage_rate=slippage_rate,
            trade_rows=trade_rows,
            reason="final liquidation",
        )
        _replace_last_equity_row(equity_rows, cash, positions, histories)

    equity = pd.DataFrame(equity_rows, columns=EQUITY_COLUMNS)
    trades = pd.DataFrame(trade_rows, columns=TRADE_COLUMNS)
    benchmark = _benchmark_return(service, start_date, end_date)
    return BacktestRunResult(
        summary=_summary(
            normalized,
            start_date,
            end_date,
            initial_cash,
            equity,
            trades,
            benchmark,
        ),
        equity=equity,
        trades=trades,
        errors=pd.DataFrame(errors, columns=["symbol", "name", "error"]),
    )


def _backtest_history_same_day_pm_open(
    service: Any,
    strategy: str,
    items: list[StockInfo],
    histories: dict[str, pd.DataFrame],
    errors: list[dict[str, object]],
    start_date: str,
    end_date: str,
    initial_cash: float,
    commission_rate: float,
    stamp_tax_rate: float,
    slippage_rate: float,
    lot_size: int,
    max_positions: int,
    refresh: bool,
    skip_errors: bool,
    minute_period: str,
) -> BacktestRunResult:
    minute_histories: dict[str, pd.DataFrame] = {}
    start_key = _compact_date(start_date)
    end_key = _compact_date(end_date)
    use_range_fetch = (pd.to_datetime(end_key) - pd.to_datetime(start_key)).days <= 10
    for item in items:
        if item.symbol not in histories:
            continue
        if use_range_fetch:
            try:
                minute = _get_minute_history_with_retries(
                    service,
                    item.symbol,
                    f"{start_key} 09:30:00",
                    f"{end_key} 15:00:00",
                    period=minute_period,
                    adjust="qfq",
                )
                frame = _prepare_minute_history(minute)
                if frame.empty:
                    raise ValueError("no minute rows returned")
                minute_histories[item.symbol] = frame
                continue
            except Exception as exc:
                if not skip_errors:
                    raise
                errors.append({"symbol": item.symbol, "name": item.name, "error": str(exc)})
                continue
        candidate_dates = [
            date
            for date in _candidate_signal_dates(strategy, histories[item.symbol])
            if start_key <= date <= end_key
        ]
        if not candidate_dates:
            continue
        minute_frames: list[pd.DataFrame] = []
        fetch_errors: list[str] = []
        for candidate_date in candidate_dates:
            start_datetime = f"{candidate_date} 09:30:00"
            end_datetime = f"{candidate_date} 15:00:00"
            try:
                minute = _get_minute_history_with_retries(
                    service,
                    item.symbol,
                    start_datetime,
                    end_datetime,
                    period=minute_period,
                    adjust="qfq",
                )
                frame = _prepare_minute_history(minute)
                if not frame.empty:
                    minute_frames.append(frame)
            except Exception as exc:
                fetch_errors.append(f"{candidate_date}: {exc}")
        if minute_frames:
            minute_histories[item.symbol] = pd.concat(minute_frames, ignore_index=True)
            if fetch_errors:
                errors.append(
                    {
                        "symbol": item.symbol,
                        "name": item.name,
                        "error": "; ".join(fetch_errors[:3]),
                    }
                )
            continue
        try:
            if fetch_errors:
                raise ValueError("; ".join(fetch_errors[:3]))
            raise ValueError("no candidate signal dates")
        except Exception as exc:
            if not skip_errors:
                raise
            errors.append({"symbol": item.symbol, "name": item.name, "error": str(exc)})

    available_symbols = sorted(set(histories) & set(minute_histories))
    if not available_symbols:
        return BacktestRunResult(
            summary=_empty_summary(strategy, start_date, end_date, initial_cash),
            equity=_empty_equity(),
            trades=_empty_trades(),
            errors=pd.DataFrame(errors, columns=["symbol", "name", "error"]),
        )

    available_items = [item for item in items if item.symbol in available_symbols]
    cash = float(initial_cash)
    positions = {symbol: 0 for symbol in available_symbols}
    dates = [
        date
        for date in sorted(set().union(*(set(histories[symbol]["date"]) for symbol in available_symbols)))
        if start_key <= date <= end_key
    ]
    equity_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []
    peak_value = float(initial_cash)
    previous_value: float | None = None

    for date in dates:
        latest_prices = {
            symbol: _latest_close_on_or_before(histories[symbol], date)
            for symbol in available_symbols
        }
        buy_candidates: list[dict[str, object]] = []

        for item in available_items:
            signal_history = _history_through_morning(
                histories[item.symbol],
                minute_histories[item.symbol],
                date,
            )
            if signal_history.empty:
                continue
            signal = evaluate_history_strategy(strategy, signal_history, item)
            if signal is None:
                continue
            execution_price = _afternoon_open_price(minute_histories[item.symbol], date)
            if not _is_finite(execution_price) or execution_price <= 0:
                continue
            current_position = positions[item.symbol]
            reason = f"{signal['reason']}; signal_date={date}; signal_time=midday"
            if signal["action"] == "sell" and current_position > 0:
                price = _execution_price(float(execution_price), "sell", slippage_rate)
                proceeds = current_position * price
                fee = max(proceeds * commission_rate, 5.0)
                tax = proceeds * stamp_tax_rate
                cash += proceeds - fee - tax
                positions[item.symbol] = 0
                trade_rows.append(
                    _trade_row(
                        date,
                        item.symbol,
                        "sell",
                        price,
                        current_position,
                        cash,
                        _portfolio_value(cash, positions, latest_prices),
                        reason,
                    )
                )
            elif signal["action"] == "buy" and current_position == 0:
                buy_candidates.append(
                    {
                        "score": float(signal.get("score") or 0.0),
                        "symbol": item.symbol,
                        "price": float(execution_price),
                        "reason": reason,
                    }
                )

        buy_candidates.sort(key=lambda order: float(order["score"]), reverse=True)
        for order in buy_candidates:
            symbol = str(order["symbol"])
            held_count = _held_position_count(positions)
            if held_count >= max_positions:
                break
            if positions[symbol] > 0:
                continue
            price = _execution_price(float(order["price"]), "buy", slippage_rate)
            target_cash = cash / max(1, max_positions - held_count)
            shares = _lot_shares(target_cash / price, lot_size)
            if shares <= 0:
                continue
            cost = shares * price
            fee = max(cost * commission_rate, 5.0)
            if cost + fee > cash:
                shares = _lot_shares((cash - 5.0) / price, lot_size)
                cost = shares * price
                fee = max(cost * commission_rate, 5.0) if shares > 0 else 0.0
            if shares <= 0 or cost + fee > cash:
                continue
            cash -= cost + fee
            positions[symbol] += shares
            trade_rows.append(
                _trade_row(
                    date,
                    symbol,
                    "buy",
                    price,
                    shares,
                    cash,
                    _portfolio_value(cash, positions, latest_prices),
                    str(order["reason"]),
                )
            )

        total_value = _portfolio_value(cash, positions, latest_prices)
        peak_value = max(peak_value, total_value)
        daily_return = (
            0.0
            if previous_value is None or previous_value == 0
            else total_value / previous_value - 1
        )
        previous_value = total_value
        equity_rows.append(
            {
                "date": date,
                "cash": cash,
                "position_value": total_value - cash,
                "total_value": total_value,
                "daily_return": daily_return,
                "drawdown": total_value / peak_value - 1 if peak_value else 0.0,
            }
        )

    if dates:
        cash = _liquidate_positions(
            date=dates[-1],
            cash=cash,
            positions=positions,
            prices={
                symbol: _latest_close_on_or_before(histories[symbol], dates[-1])
                for symbol in available_symbols
            },
            commission_rate=commission_rate,
            stamp_tax_rate=stamp_tax_rate,
            slippage_rate=slippage_rate,
            trade_rows=trade_rows,
            reason="final liquidation",
        )
        _replace_last_equity_row(equity_rows, cash, positions, histories)

    equity = pd.DataFrame(equity_rows, columns=EQUITY_COLUMNS)
    trades = pd.DataFrame(trade_rows, columns=TRADE_COLUMNS)
    benchmark = _benchmark_return(service, start_date, end_date)
    return BacktestRunResult(
        summary=_summary(
            strategy,
            start_date,
            end_date,
            initial_cash,
            equity,
            trades,
            benchmark,
        ),
        equity=equity,
        trades=trades,
        errors=pd.DataFrame(errors, columns=["symbol", "name", "error"]),
    )


def _backtest_bank_rotation(
    service: Any,
    symbols: Iterable[str | StockInfo],
    start_date: str,
    end_date: str,
    initial_cash: float,
    commission_rate: float,
    stamp_tax_rate: float,
    slippage_rate: float,
    lot_size: int,
    refresh: bool,
    skip_errors: bool,
) -> BacktestRunResult:
    items = [_stock_item(item) for item in symbols]
    if not items:
        raise ValueError("bank_rotation backtest requires at least one symbol")
    if initial_cash <= 0:
        raise ValueError("initial_cash must be greater than 0")

    histories: dict[str, pd.DataFrame] = {}
    valuations: dict[str, pd.DataFrame] = {}
    errors: list[dict[str, object]] = []
    for item in items:
        try:
            history = service.get_history(
                symbol=item.symbol,
                start_date=start_date,
                end_date=end_date,
                refresh=refresh,
                indicators=False,
            )
            frame = _prepare_history(history)
            if frame.empty:
                raise ValueError("no historical rows returned")
            histories[item.symbol] = frame
        except Exception as exc:
            if not skip_errors:
                raise
            errors.append({"symbol": item.symbol, "name": item.name, "error": str(exc)})
            continue
        try:
            valuation = service.get_valuation_history(
                item.symbol,
                indicator="市净率",
                period="近一年",
            )
            valuations[item.symbol] = _prepare_valuation(valuation)
        except Exception as exc:
            if not skip_errors:
                raise
            errors.append({"symbol": item.symbol, "name": item.name, "error": str(exc)})

    available_symbols = sorted(set(histories) & set(valuations))
    if not available_symbols:
        return BacktestRunResult(
            summary=_empty_summary("bank_rotation", start_date, end_date, initial_cash),
            equity=_empty_equity(),
            trades=_empty_trades(),
            errors=pd.DataFrame(errors, columns=["symbol", "name", "error"]),
        )

    cash = float(initial_cash)
    positions = {symbol: 0 for symbol in available_symbols}
    dates = sorted(set().union(*(set(histories[symbol]["date"]) for symbol in available_symbols)))
    equity_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []
    peak_value = float(initial_cash)
    previous_value: float | None = None
    current_holding: str | None = None
    last_rebalance_week: tuple[int, int] | None = None

    for date in dates:
        latest_prices = {
            symbol: _latest_close_on_or_before(histories[symbol], date)
            for symbol in available_symbols
        }
        week_key = _week_key(date)
        if week_key != last_rebalance_week:
            last_rebalance_week = week_key
            target = _lowest_pb_symbol(available_symbols, valuations, date)
            if target is not None and target != current_holding:
                if current_holding is not None and positions.get(current_holding, 0) > 0:
                    shares = positions[current_holding]
                    price = latest_prices[current_holding]
                    if _is_finite(price) and price > 0:
                        proceeds = shares * price
                        fee = max(proceeds * commission_rate, 5.0)
                        tax = proceeds * stamp_tax_rate
                        cash += proceeds - fee - tax
                        positions[current_holding] = 0
                        trade_rows.append(
                            _trade_row(
                                date,
                                current_holding,
                                "sell",
                                price,
                                shares,
                                cash,
                                _portfolio_value(cash, positions, latest_prices),
                                "weekly bank rotation switch",
                            )
                        )
                price = latest_prices[target]
                if _is_finite(price) and price > 0:
                    shares = _lot_shares((cash - 5.0) / price, lot_size)
                    cost = shares * price
                    fee = max(cost * commission_rate, 5.0) if shares > 0 else 0.0
                    if shares > 0 and cost + fee <= cash:
                        cash -= cost + fee
                        positions[target] += shares
                        current_holding = target
                        pb = _latest_pb_on_or_before(valuations[target], date)
                        trade_rows.append(
                            _trade_row(
                                date,
                                target,
                                "buy",
                                price,
                                shares,
                                cash,
                                _portfolio_value(cash, positions, latest_prices),
                                f"weekly lowest PB bank: {pb:.2f}",
                            )
                        )

        total_value = _portfolio_value(cash, positions, latest_prices)
        peak_value = max(peak_value, total_value)
        daily_return = (
            0.0
            if previous_value is None or previous_value == 0
            else total_value / previous_value - 1
        )
        previous_value = total_value
        equity_rows.append(
            {
                "date": date,
                "cash": cash,
                "position_value": total_value - cash,
                "total_value": total_value,
                "daily_return": daily_return,
                "drawdown": total_value / peak_value - 1 if peak_value else 0.0,
            }
        )

    if dates:
        cash = _liquidate_positions(
            date=dates[-1],
            cash=cash,
            positions=positions,
            prices={
                symbol: _latest_close_on_or_before(histories[symbol], dates[-1])
                for symbol in available_symbols
            },
            commission_rate=commission_rate,
            stamp_tax_rate=stamp_tax_rate,
            slippage_rate=slippage_rate,
            trade_rows=trade_rows,
            reason="final liquidation",
        )
        _replace_last_equity_row(equity_rows, cash, positions, histories)

    equity = pd.DataFrame(equity_rows, columns=EQUITY_COLUMNS)
    trades = pd.DataFrame(trade_rows, columns=TRADE_COLUMNS)
    benchmark = _benchmark_return(service, start_date, end_date)
    return BacktestRunResult(
        summary=_summary(
            "bank_rotation",
            start_date,
            end_date,
            initial_cash,
            equity,
            trades,
            benchmark,
        ),
        equity=equity,
        trades=trades,
        errors=pd.DataFrame(errors, columns=["symbol", "name", "error"]),
    )


def _summary(
    strategy: str,
    start_date: str,
    end_date: str,
    initial_cash: float,
    equity: pd.DataFrame,
    trades: pd.DataFrame,
    benchmark: dict[str, object] | None = None,
) -> pd.DataFrame:
    if equity.empty:
        return _empty_summary(strategy, start_date, end_date, initial_cash)
    final_value = float(equity.iloc[-1]["total_value"])
    total_return = final_value / initial_cash - 1
    periods = max(len(equity), 1)
    annualized = (1 + total_return) ** (252 / periods) - 1 if final_value > 0 else -1.0
    daily_returns = pd.to_numeric(equity["daily_return"], errors="coerce").dropna()
    volatility = float(daily_returns.std(ddof=1) * (252 ** 0.5)) if len(daily_returns) > 1 else 0.0
    sharpe = (
        float(daily_returns.mean() / daily_returns.std(ddof=1) * (252 ** 0.5))
        if len(daily_returns) > 1 and daily_returns.std(ddof=1) > 0
        else 0.0
    )
    benchmark = benchmark or {}
    benchmark_return = benchmark.get("benchmark_return")
    if pd.isna(benchmark_return):
        benchmark_return = pd.NA
    excess_return = (
        total_return - float(benchmark_return)
        if pd.notna(benchmark_return)
        else pd.NA
    )
    return pd.DataFrame(
        [
            {
                "strategy": strategy,
                "start_date": start_date,
                "end_date": end_date,
                "initial_cash": initial_cash,
                "final_value": final_value,
                "total_return": total_return,
                "annualized_return": annualized,
                "annualized_volatility": volatility,
                "sharpe_ratio": sharpe,
                "max_drawdown": float(equity["drawdown"].min()),
                "benchmark_symbol": benchmark.get("benchmark_symbol", "000001.SH"),
                "benchmark_return": benchmark_return,
                "excess_return": excess_return,
                "trade_count": len(trades),
            }
        ],
        columns=SUMMARY_COLUMNS,
    )


def _stock_item(item: str | StockInfo) -> StockInfo:
    if isinstance(item, StockInfo):
        return item
    normalized = normalize_symbol(item)
    return StockInfo(symbol=normalized, code=symbol_code(normalized), name="")


def _prepare_history(history: pd.DataFrame) -> pd.DataFrame:
    frame = history.copy()
    if "date" in frame:
        frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
        frame = frame.sort_values("date")
    for column in ["open", "high", "low", "close", "ma5"]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.reset_index(drop=True)


def _prepare_minute_history(history: pd.DataFrame) -> pd.DataFrame:
    frame = history.copy()
    if "datetime" not in frame:
        return pd.DataFrame(
            columns=["datetime", "date", "time", "open", "high", "low", "close", "volume", "amount"]
        )
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
    frame = frame.dropna(subset=["datetime"]).sort_values("datetime")
    frame["date"] = frame["datetime"].dt.strftime("%Y-%m-%d")
    frame["time"] = frame["datetime"].dt.strftime("%H:%M:%S")
    for column in ["open", "high", "low", "close", "volume", "amount"]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        else:
            frame[column] = pd.NA
    return frame.reset_index(drop=True)


def _candidate_signal_dates(strategy: str, daily_history: pd.DataFrame) -> list[str]:
    if strategy != "turtle":
        return daily_history["date"].dropna().astype(str).tolist()
    frame = _prepare_history(daily_history)
    dates: list[str] = []
    for index in range(len(frame)):
        if index < 20:
            continue
        high = frame.iloc[index].get("high")
        low = frame.iloc[index].get("low")
        entry_high = frame["high"].iloc[index - 20 : index].max()
        exit_start = max(0, index - 10)
        exit_low = frame["low"].iloc[exit_start:index].min()
        if (
            _is_finite(high)
            and _is_finite(entry_high)
            and float(high) > float(entry_high)
        ) or (
            _is_finite(low)
            and _is_finite(exit_low)
            and float(low) < float(exit_low)
        ):
            dates.append(str(frame.iloc[index]["date"]))
    return dates


def _get_minute_history_with_retries(
    service: Any,
    symbol: str,
    start_datetime: str,
    end_datetime: str,
    period: str,
    adjust: str,
    attempts: int = 3,
) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return service.get_minute_history(
                symbol,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                period=period,
                adjust=adjust,
            )
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError("minute history request failed")


def _history_through_morning(
    daily_history: pd.DataFrame,
    minute_history: pd.DataFrame,
    date: str,
) -> pd.DataFrame:
    morning = minute_history[
        (minute_history["date"] == date) & (minute_history["time"] <= "11:30:00")
    ].copy()
    morning = morning.dropna(subset=["open", "high", "low", "close"])
    if morning.empty:
        return pd.DataFrame()

    prior = daily_history[daily_history["date"] < date].copy()
    row = {
        "symbol": daily_history.iloc[0].get("symbol") if not daily_history.empty else "",
        "date": date,
        "open": float(morning.iloc[0]["open"]),
        "high": float(morning["high"].max()),
        "low": float(morning["low"].min()),
        "close": float(morning.iloc[-1]["close"]),
        "volume": float(morning["volume"].sum(skipna=True)),
        "amount": float(morning["amount"].sum(skipna=True)),
    }
    frame = pd.concat([prior, pd.DataFrame([row])], ignore_index=True)
    if "ma5" in frame:
        frame["ma5"] = pd.to_numeric(frame["close"], errors="coerce").rolling(5).mean()
    return _prepare_history(frame)


def _afternoon_open_price(minute_history: pd.DataFrame, date: str) -> float:
    afternoon = minute_history[
        (minute_history["date"] == date) & (minute_history["time"] >= "13:00:00")
    ].copy()
    afternoon = afternoon.dropna(subset=["open"]).sort_values(["date", "time"])
    if afternoon.empty:
        return float("nan")
    return float(afternoon.iloc[0]["open"])


def _compact_date(value: str) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def _warmup_start_date(value: str, warmup_days: int) -> str:
    if warmup_days <= 0:
        return value
    return (pd.to_datetime(value) - pd.Timedelta(days=warmup_days)).strftime("%Y%m%d")


def _prepare_valuation(valuation: pd.DataFrame) -> pd.DataFrame:
    frame = valuation.copy()
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    return frame.dropna(subset=["date", "value"]).sort_values("date").reset_index(drop=True)


def _latest_close_on_or_before(frame: pd.DataFrame, date: str) -> float:
    rows = frame[frame["date"] <= date]
    if rows.empty:
        return float("nan")
    return float(rows.iloc[-1]["close"])


def _price_on_date(frame: pd.DataFrame, date: str, column: str) -> float:
    rows = frame[frame["date"] == date]
    if rows.empty:
        return float("nan")
    value = rows.iloc[-1].get(column)
    if not _is_finite(value) and column != "close":
        value = rows.iloc[-1].get("close")
    return float(value) if _is_finite(value) else float("nan")


def _execution_price(price: float, action: str, slippage_rate: float) -> float:
    if slippage_rate <= 0:
        return price
    if action == "buy":
        return price * (1 + slippage_rate)
    return price * (1 - slippage_rate)


def _pending_order(
    symbol: str,
    action: str,
    score: float,
    reason: str,
    signal_date: str,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "action": action,
        "score": score,
        "reason": f"{reason}; signal_date={signal_date}",
    }


def _portfolio_value(
    cash: float,
    positions: dict[str, int],
    prices: dict[str, float],
) -> float:
    value = cash
    for symbol, shares in positions.items():
        price = prices.get(symbol)
        if _is_finite(price):
            value += shares * price
    return float(value)


def _liquidate_positions(
    date: str,
    cash: float,
    positions: dict[str, int],
    prices: dict[str, float],
    commission_rate: float,
    stamp_tax_rate: float,
    slippage_rate: float,
    trade_rows: list[dict[str, object]],
    reason: str,
) -> float:
    for symbol, shares in list(positions.items()):
        if shares <= 0:
            continue
        price = prices.get(symbol)
        if not _is_finite(price) or price <= 0:
            continue
        price = _execution_price(float(price), "sell", slippage_rate)
        proceeds = shares * price
        fee = max(proceeds * commission_rate, 5.0)
        tax = proceeds * stamp_tax_rate
        cash += proceeds - fee - tax
        positions[symbol] = 0
        trade_rows.append(
            _trade_row(
                date,
                symbol,
                "sell",
                price,
                shares,
                cash,
                _portfolio_value(cash, positions, prices),
                reason,
            )
        )
    return cash


def _replace_last_equity_row(
    equity_rows: list[dict[str, object]],
    cash: float,
    positions: dict[str, int],
    histories: dict[str, pd.DataFrame],
) -> None:
    if not equity_rows:
        return
    date = str(equity_rows[-1]["date"])
    prices = {
        symbol: _latest_close_on_or_before(frame, date)
        for symbol, frame in histories.items()
    }
    total_value = _portfolio_value(cash, positions, prices)
    previous_value = (
        float(equity_rows[-2]["total_value"])
        if len(equity_rows) > 1
        else total_value
    )
    peak_value = max(float(row["total_value"]) for row in equity_rows[:-1]) if len(equity_rows) > 1 else total_value
    peak_value = max(peak_value, total_value)
    equity_rows[-1] = {
        "date": date,
        "cash": cash,
        "position_value": total_value - cash,
        "total_value": total_value,
        "daily_return": 0.0 if previous_value == 0 else total_value / previous_value - 1,
        "drawdown": total_value / peak_value - 1 if peak_value else 0.0,
    }


def _open_slot_count(positions: dict[str, int]) -> int:
    return sum(1 for shares in positions.values() if shares == 0)


def _held_position_count(positions: dict[str, int]) -> int:
    return sum(1 for shares in positions.values() if shares > 0)


def _lot_shares(value: float, lot_size: int) -> int:
    if lot_size <= 1:
        return max(int(value), 0)
    return max(int(value // lot_size) * lot_size, 0)


def _trade_row(
    date: str,
    symbol: str,
    action: str,
    price: float,
    shares: int,
    cash_after: float,
    total_value: float,
    reason: str,
) -> dict[str, object]:
    return {
        "date": date,
        "symbol": symbol,
        "action": action,
        "price": price,
        "shares": shares,
        "cash_after": cash_after,
        "total_value": total_value,
        "reason": reason,
    }


def _week_key(date: str) -> tuple[int, int]:
    iso = pd.Timestamp(date).isocalendar()
    return int(iso.year), int(iso.week)


def _lowest_pb_symbol(
    symbols: list[str],
    valuations: dict[str, pd.DataFrame],
    date: str,
) -> str | None:
    candidates = []
    for symbol in symbols:
        pb = _latest_pb_on_or_before(valuations[symbol], date)
        if _is_finite(pb) and pb > 0:
            candidates.append((pb, symbol))
    if not candidates:
        return None
    return sorted(candidates)[0][1]


def _latest_pb_on_or_before(frame: pd.DataFrame, date: str) -> float:
    if frame.empty:
        return float("nan")
    rows = frame[frame["date"] <= date]
    if rows.empty:
        return float("nan")
    return float(rows.iloc[-1]["value"])


def _benchmark_return(
    service: Any,
    start_date: str,
    end_date: str,
    benchmark_symbol: str = "000001.SH",
) -> dict[str, object]:
    try:
        frame = service.get_index_history(
            "000001",
            start_date=start_date,
            end_date=end_date,
        )
    except Exception:
        return {
            "benchmark_symbol": benchmark_symbol,
            "benchmark_return": pd.NA,
        }
    data = _prepare_history(frame.rename(columns={"index_code": "symbol"}))
    data = data.dropna(subset=["close"])
    if len(data) < 2:
        return {
            "benchmark_symbol": benchmark_symbol,
            "benchmark_return": pd.NA,
        }
    start_close = float(data.iloc[0]["close"])
    end_close = float(data.iloc[-1]["close"])
    return {
        "benchmark_symbol": benchmark_symbol,
        "benchmark_return": end_close / start_close - 1 if start_close else pd.NA,
    }


def _empty_summary(
    strategy: str,
    start_date: str,
    end_date: str,
    initial_cash: float,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "strategy": strategy,
                "start_date": start_date,
                "end_date": end_date,
                "initial_cash": initial_cash,
                "final_value": initial_cash,
                "total_return": 0.0,
                "annualized_return": 0.0,
                "annualized_volatility": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "benchmark_symbol": "000001.SH",
                "benchmark_return": pd.NA,
                "excess_return": pd.NA,
                "trade_count": 0,
            }
        ],
        columns=SUMMARY_COLUMNS,
    )


def _empty_equity() -> pd.DataFrame:
    return pd.DataFrame(columns=EQUITY_COLUMNS)


def _empty_trades() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADE_COLUMNS)


def _is_finite(value: object) -> bool:
    return pd.notna(value)
