from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd

from stock_picker.data.models import StockInfo, normalize_symbol, symbol_code


TURTLE_SUMMARY_COLUMNS = [
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
    "max_drawdown_days",
    "win_rate",
    "profit_loss_ratio",
    "average_holding_days",
    "trade_count",
    "position_utilization",
]
TURTLE_EQUITY_COLUMNS = [
    "date",
    "cash",
    "position_value",
    "total_value",
    "daily_return",
    "drawdown",
    "held_symbols",
    "held_units",
]
TURTLE_TRADE_COLUMNS = [
    "date",
    "symbol",
    "code",
    "name",
    "action",
    "system",
    "price",
    "shares",
    "unit_shares",
    "units_after",
    "cash_after",
    "total_value",
    "n",
    "stop_price",
    "next_add_price",
    "entry_reason",
    "exit_reason",
    "realized_pnl",
    "realized_pnl_pct",
]
TURTLE_POSITION_COLUMNS = [
    "date",
    "symbol",
    "code",
    "name",
    "system",
    "shares",
    "units",
    "avg_cost",
    "last_entry_price",
    "n",
    "stop_price",
    "next_add_price",
    "market_value",
    "unrealized_pnl",
]
TURTLE_SIGNAL_COLUMNS = [
    "strategy",
    "symbol",
    "code",
    "name",
    "date",
    "action",
    "system",
    "score",
    "rank",
    "unit_shares",
    "suggested_shares",
    "price",
    "n",
    "stop_price",
    "next_add_price",
    "exit_price",
    "reason",
]


@dataclass(frozen=True)
class TurtleConfig:
    s1_entry: int = 20
    s1_exit: int = 10
    s2_entry: int = 55
    s2_exit: int = 20
    atr_period: int = 20
    risk_pct: float = 0.01
    add_unit_atr: float = 0.5
    stop_atr: float = 2.0
    max_units: int = 4
    lot_size: int = 100
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.001
    slippage_rate: float = 0.0


@dataclass
class TurtlePosition:
    symbol: str
    code: str
    name: str = ""
    system: str | None = None
    shares: int = 0
    units: int = 0
    unit_shares: int = 0
    avg_cost: float = 0.0
    first_entry_price: float | None = None
    last_entry_price: float | None = None
    entry_date: str | None = None
    last_entry_date: str | None = None
    n: float | None = None
    stop_price: float | None = None
    next_add_price: float | None = None
    skip_next_s1: bool = False


@dataclass(frozen=True)
class TurtleSystemResult:
    summary: pd.DataFrame
    equity: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame
    drawdowns: pd.DataFrame
    symbol_pnl: pd.DataFrame
    signals: pd.DataFrame
    errors: pd.DataFrame


def run_turtle_system(
    service: Any,
    symbols: Iterable[str | StockInfo],
    start_date: str,
    end_date: str,
    cash: float,
    config: TurtleConfig | None = None,
    refresh: bool = False,
    skip_errors: bool = True,
) -> TurtleSystemResult:
    config = config or TurtleConfig()
    items = [_stock_item(item) for item in symbols]
    if not items:
        raise ValueError("turtle system requires at least one symbol")
    if cash <= 0:
        raise ValueError("cash must be greater than 0")

    histories, errors = _load_histories(
        service, items, start_date, end_date, refresh=refresh, skip_errors=skip_errors
    )
    signals = _current_signals(histories, items, cash, config)
    return TurtleSystemResult(
        summary=_empty_summary(start_date, end_date, cash),
        equity=_empty_equity(),
        trades=_empty_trades(),
        positions=_empty_positions(),
        drawdowns=_empty_drawdowns(),
        symbol_pnl=_empty_symbol_pnl(),
        signals=signals,
        errors=pd.DataFrame(errors, columns=["symbol", "name", "error"]),
    )


def backtest_turtle_system(
    service: Any,
    symbols: Iterable[str | StockInfo],
    start_date: str,
    end_date: str,
    initial_cash: float = 100_000.0,
    config: TurtleConfig | None = None,
    refresh: bool = False,
    skip_errors: bool = True,
) -> TurtleSystemResult:
    config = config or TurtleConfig()
    items = [_stock_item(item) for item in symbols]
    if not items:
        raise ValueError("turtle system backtest requires at least one symbol")
    if initial_cash <= 0:
        raise ValueError("initial_cash must be greater than 0")

    histories, errors = _load_histories(
        service, items, start_date, end_date, refresh=refresh, skip_errors=skip_errors
    )
    if not histories:
        return TurtleSystemResult(
            summary=_empty_summary(start_date, end_date, initial_cash),
            equity=_empty_equity(),
            trades=_empty_trades(),
            positions=_empty_positions(),
            drawdowns=_empty_drawdowns(),
            symbol_pnl=_empty_symbol_pnl(),
            signals=_empty_signals(),
            errors=pd.DataFrame(errors, columns=["symbol", "name", "error"]),
        )

    cash = float(initial_cash)
    states = {symbol: TurtlePosition(symbol=symbol, code=symbol_code(symbol)) for symbol in histories}
    item_map = {item.symbol: item for item in items}
    dates = sorted(set().union(*(set(frame["date"]) for frame in histories.values())))
    dates = [date for date in dates if _date_key(start_date) <= date <= _date_key(end_date)]
    pending_orders: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []
    equity_rows: list[dict[str, object]] = []
    position_rows: list[dict[str, object]] = []
    peak_value = float(initial_cash)
    previous_value: float | None = None

    for date in dates:
        prices = {symbol: _price_on_date(frame, date, "close") for symbol, frame in histories.items()}
        open_prices = {symbol: _price_on_date(frame, date, "open") for symbol, frame in histories.items()}
        executable = [order for order in pending_orders if _is_finite(open_prices.get(str(order["symbol"])))]
        pending_orders = [order for order in pending_orders if order not in executable]

        for order in executable:
            symbol = str(order["symbol"])
            price = _apply_slippage(float(open_prices[symbol]), str(order["action"]), config.slippage_rate)
            if price <= 0:
                continue
            state = states[symbol]
            item = item_map.get(symbol, StockInfo(symbol=symbol, code=symbol_code(symbol), name=""))
            if order["action"] in {"enter", "add"}:
                shares = int(order["shares"])
                cost = shares * price
                fee = max(cost * config.commission_rate, config.min_commission)
                if shares <= 0 or cost + fee > cash:
                    continue
                cash -= cost + fee
                _apply_entry(state, item, price, shares, str(order["system"]), float(order["n"]), config, date, cost + fee)
                trade_rows.append(
                    _trade_row(
                        date,
                        state,
                        "buy" if order["action"] == "enter" else "add",
                        price,
                        shares,
                        cash,
                        _portfolio_value(cash, states, prices),
                        str(order.get("entry_reason") or order.get("reason") or ""),
                        "",
                    )
                )
            elif order["action"] == "exit" and state.shares > 0:
                cash = _exit_position(
                    date,
                    state,
                    price,
                    cash,
                    config,
                    prices,
                    trade_rows,
                    states=states,
                    exit_reason=str(order.get("exit_reason") or order.get("reason") or ""),
                )

        for item in items:
            if item.symbol not in histories:
                continue
            frame = histories[item.symbol]
            history_to_date = frame[frame["date"] <= date].reset_index(drop=True)
            if history_to_date.empty or history_to_date.iloc[-1]["date"] != date:
                continue
            state = states[item.symbol]
            close = float(history_to_date.iloc[-1]["close"])
            n_value = _atr(history_to_date, config.atr_period)
            if not _is_finite(close) or not _is_finite(n_value) or n_value <= 0:
                continue

            if state.shares > 0:
                exit_reason = _exit_reason(history_to_date, state, close, config)
                if exit_reason:
                    pending_orders.append({"symbol": item.symbol, "action": "exit", "exit_reason": exit_reason})
                    continue
                if state.units < config.max_units and state.next_add_price is not None and close >= state.next_add_price:
                    shares = _unit_shares(_portfolio_value(cash, states, prices), n_value, config)
                    if shares > 0:
                        pending_orders.append(
                            {
                                "symbol": item.symbol,
                                "action": "add",
                                "system": state.system,
                                "shares": shares,
                                "n": n_value,
                                "entry_reason": f"add unit at 0.5N: close {close:.2f} >= {state.next_add_price:.2f}",
                            }
                        )
            else:
                signal = _entry_signal(history_to_date, state, close, config)
                if signal:
                    shares = _unit_shares(_portfolio_value(cash, states, prices), n_value, config)
                    if shares > 0:
                        pending_orders.append(
                            {
                                "symbol": item.symbol,
                                "action": "enter",
                                "system": signal["system"],
                                "shares": shares,
                                "n": n_value,
                                "entry_reason": signal["reason"],
                            }
                        )

        total_value = _portfolio_value(cash, states, prices)
        peak_value = max(peak_value, total_value)
        daily_return = 0.0 if previous_value in (None, 0) else total_value / previous_value - 1
        previous_value = total_value
        equity_rows.append(
            {
                "date": date,
                "cash": cash,
                "position_value": total_value - cash,
                "total_value": total_value,
                "daily_return": daily_return,
                "drawdown": total_value / peak_value - 1 if peak_value else 0.0,
                "held_symbols": sum(1 for state in states.values() if state.shares > 0),
                "held_units": sum(state.units for state in states.values()),
            }
        )
        position_rows.extend(_position_rows(date, states, prices))

    if dates:
        final_prices = {symbol: _price_on_date(frame, dates[-1], "close") for symbol, frame in histories.items()}
        for state in states.values():
            if state.shares > 0 and _is_finite(final_prices.get(state.symbol)):
                cash = _exit_position(
                    dates[-1],
                    state,
                    float(final_prices[state.symbol]),
                    cash,
                    config,
                    final_prices,
                    trade_rows,
                    states=states,
                    exit_reason="final liquidation",
                )
        if equity_rows:
            total_value = _portfolio_value(cash, states, final_prices)
            previous = float(equity_rows[-2]["total_value"]) if len(equity_rows) > 1 else total_value
            peak = max([float(row["total_value"]) for row in equity_rows[:-1]] + [total_value])
            equity_rows[-1].update(
                {
                    "cash": cash,
                    "position_value": total_value - cash,
                    "total_value": total_value,
                    "daily_return": 0.0 if previous == 0 else total_value / previous - 1,
                    "drawdown": total_value / peak - 1 if peak else 0.0,
                    "held_symbols": 0,
                    "held_units": 0,
                }
            )

    equity = pd.DataFrame(equity_rows, columns=TURTLE_EQUITY_COLUMNS)
    trades = pd.DataFrame(trade_rows, columns=TURTLE_TRADE_COLUMNS)
    positions = pd.DataFrame(position_rows, columns=TURTLE_POSITION_COLUMNS)
    summary = _summary(start_date, end_date, initial_cash, equity, trades)
    return TurtleSystemResult(
        summary=summary,
        equity=equity,
        trades=trades,
        positions=positions,
        drawdowns=_drawdowns(equity),
        symbol_pnl=_symbol_pnl(trades),
        signals=_empty_signals(),
        errors=pd.DataFrame(errors, columns=["symbol", "name", "error"]),
    )


def _load_histories(
    service: Any,
    items: list[StockInfo],
    start_date: str,
    end_date: str,
    refresh: bool,
    skip_errors: bool,
) -> tuple[dict[str, pd.DataFrame], list[dict[str, object]]]:
    histories: dict[str, pd.DataFrame] = {}
    errors: list[dict[str, object]] = []
    warmup_start = (pd.to_datetime(start_date) - pd.Timedelta(days=120)).strftime("%Y%m%d")
    for item in items:
        try:
            frame = _prepare_history(
                service.get_history(
                    item.symbol,
                    start_date=warmup_start,
                    end_date=end_date,
                    refresh=refresh,
                    indicators=True,
                )
            )
            if frame.empty:
                raise ValueError("no historical rows returned")
            histories[item.symbol] = frame
        except Exception as exc:
            if not skip_errors:
                raise
            errors.append({"symbol": item.symbol, "name": item.name, "error": str(exc)})
    return histories, errors


def _current_signals(
    histories: dict[str, pd.DataFrame],
    items: list[StockInfo],
    cash: float,
    config: TurtleConfig,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for item in items:
        frame = histories.get(item.symbol)
        if frame is None or frame.empty:
            continue
        close = float(frame.iloc[-1]["close"])
        n_value = _atr(frame, config.atr_period)
        state = TurtlePosition(item.symbol, item.code, item.name)
        signal = _entry_signal(frame, state, close, config)
        if not signal or not _is_finite(n_value) or n_value <= 0:
            continue
        unit = _unit_shares(cash, n_value, config)
        rows.append(
            {
                "strategy": "turtle_system",
                "symbol": item.symbol,
                "code": item.code,
                "name": item.name,
                "date": frame.iloc[-1]["date"],
                "action": "buy",
                "system": signal["system"],
                "score": signal["score"],
                "rank": None,
                "unit_shares": unit,
                "suggested_shares": unit,
                "price": close,
                "n": n_value,
                "stop_price": close - config.stop_atr * n_value,
                "next_add_price": close + config.add_unit_atr * n_value,
                "exit_price": _exit_channel(frame, signal["system"], config),
                "reason": signal["reason"],
            }
        )
    output = pd.DataFrame(rows, columns=TURTLE_SIGNAL_COLUMNS)
    if output.empty:
        return output
    output = output.sort_values("score", ascending=False).reset_index(drop=True)
    output["rank"] = range(1, len(output) + 1)
    return output


def _prepare_history(history: pd.DataFrame) -> pd.DataFrame:
    frame = history.copy()
    if "date" in frame:
        frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    for column in ["open", "high", "low", "close", "volume"]:
        if column not in frame:
            frame[column] = pd.NA
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date").reset_index(drop=True)


def _entry_signal(
    frame: pd.DataFrame,
    state: TurtlePosition,
    close: float,
    config: TurtleConfig,
) -> dict[str, object] | None:
    if len(frame) < config.s1_entry + 1:
        return None
    high20 = frame["high"].iloc[-config.s1_entry - 1 : -1].max()
    high55 = frame["high"].iloc[-config.s2_entry - 1 : -1].max() if len(frame) >= config.s2_entry + 1 else float("nan")
    s1 = _is_finite(high20) and close > high20
    s2 = _is_finite(high55) and close > high55
    if s1 and not state.skip_next_s1:
        return {
            "system": "S1",
            "score": close / high20 - 1 if high20 else 0.0,
            "reason": f"S1 close {close:.2f} broke 20-day high {high20:.2f}",
        }
    if s2:
        return {
            "system": "S2",
            "score": close / high55 - 1 if high55 else 0.0,
            "reason": f"S2 close {close:.2f} broke 55-day high {high55:.2f}",
        }
    if s1 and state.skip_next_s1:
        state.skip_next_s1 = False
    return None


def _exit_reason(
    frame: pd.DataFrame,
    state: TurtlePosition,
    close: float,
    config: TurtleConfig,
) -> str | None:
    if state.stop_price is not None and close <= state.stop_price:
        return f"2N stop: close {close:.2f} <= stop {state.stop_price:.2f}"
    window = config.s1_exit if state.system == "S1" else config.s2_exit
    if len(frame) >= window + 1:
        low = frame["low"].iloc[-window - 1 : -1].min()
        if _is_finite(low) and close < low:
            return f"{state.system} channel exit: close {close:.2f} < {window}-day low {low:.2f}"
    return None


def _exit_channel(frame: pd.DataFrame, system: str, config: TurtleConfig) -> float:
    window = config.s1_exit if system == "S1" else config.s2_exit
    if len(frame) < window + 1:
        return float("nan")
    return float(frame["low"].iloc[-window - 1 : -1].min())


def _apply_entry(
    state: TurtlePosition,
    item: StockInfo,
    price: float,
    shares: int,
    system: str,
    n_value: float,
    config: TurtleConfig,
    date: str,
    cost: float,
) -> None:
    old_value = state.avg_cost * state.shares
    state.name = item.name or state.name
    state.system = system
    state.shares += shares
    state.units += 1
    state.unit_shares = shares if state.unit_shares == 0 else state.unit_shares
    state.avg_cost = (old_value + cost) / state.shares
    state.first_entry_price = state.first_entry_price or price
    state.last_entry_price = price
    state.entry_date = state.entry_date or date
    state.last_entry_date = date
    state.n = n_value
    state.stop_price = price - config.stop_atr * n_value
    state.next_add_price = price + config.add_unit_atr * n_value if state.units < config.max_units else None


def _exit_position(
    date: str,
    state: TurtlePosition,
    price: float,
    cash: float,
    config: TurtleConfig,
    prices: dict[str, float],
    trade_rows: list[dict[str, object]],
    exit_reason: str,
    states: dict[str, TurtlePosition] | None = None,
) -> float:
    proceeds = state.shares * price
    fee = max(proceeds * config.commission_rate, config.min_commission)
    tax = proceeds * config.stamp_tax_rate
    realized = proceeds - fee - tax - state.avg_cost * state.shares
    realized_pct = realized / (state.avg_cost * state.shares) if state.avg_cost else 0.0
    cash += proceeds - fee - tax
    old_system = state.system or ""
    old_shares = state.shares
    old_total_value = cash
    if states is not None:
        old_total_value = cash
        for symbol, other in states.items():
            if symbol == state.symbol or other.shares <= 0:
                continue
            mark = prices.get(symbol)
            if _is_finite(mark):
                old_total_value += other.shares * float(mark)
    trade_rows.append(
        {
            "date": date,
            "symbol": state.symbol,
            "code": state.code,
            "name": state.name,
            "action": "sell",
            "system": old_system,
            "price": price,
            "shares": old_shares,
            "unit_shares": state.unit_shares,
            "units_after": 0,
            "cash_after": cash,
            "total_value": old_total_value,
            "n": state.n,
            "stop_price": state.stop_price,
            "next_add_price": state.next_add_price,
            "entry_reason": "",
            "exit_reason": exit_reason,
            "realized_pnl": realized,
            "realized_pnl_pct": realized_pct,
        }
    )
    if old_system == "S1" and realized > 0:
        state.skip_next_s1 = True
    skip = state.skip_next_s1
    state.shares = 0
    state.units = 0
    state.unit_shares = 0
    state.avg_cost = 0.0
    state.system = None
    state.first_entry_price = None
    state.last_entry_price = None
    state.entry_date = None
    state.last_entry_date = None
    state.n = None
    state.stop_price = None
    state.next_add_price = None
    state.skip_next_s1 = skip
    return cash


def _trade_row(
    date: str,
    state: TurtlePosition,
    action: str,
    price: float,
    shares: int,
    cash: float,
    total_value: float,
    entry_reason: str,
    exit_reason: str,
) -> dict[str, object]:
    return {
        "date": date,
        "symbol": state.symbol,
        "code": state.code,
        "name": state.name,
        "action": action,
        "system": state.system,
        "price": price,
        "shares": shares,
        "unit_shares": state.unit_shares,
        "units_after": state.units,
        "cash_after": cash,
        "total_value": total_value,
        "n": state.n,
        "stop_price": state.stop_price,
        "next_add_price": state.next_add_price,
        "entry_reason": entry_reason,
        "exit_reason": exit_reason,
        "realized_pnl": None,
        "realized_pnl_pct": None,
    }


def _position_rows(date: str, states: dict[str, TurtlePosition], prices: dict[str, float]) -> list[dict[str, object]]:
    rows = []
    for state in states.values():
        if state.shares <= 0:
            continue
        price = prices.get(state.symbol, state.avg_cost)
        value = state.shares * price if _is_finite(price) else state.shares * state.avg_cost
        rows.append(
            {
                "date": date,
                "symbol": state.symbol,
                "code": state.code,
                "name": state.name,
                "system": state.system,
                "shares": state.shares,
                "units": state.units,
                "avg_cost": state.avg_cost,
                "last_entry_price": state.last_entry_price,
                "n": state.n,
                "stop_price": state.stop_price,
                "next_add_price": state.next_add_price,
                "market_value": value,
                "unrealized_pnl": value - state.avg_cost * state.shares,
            }
        )
    return rows


def _summary(start_date: str, end_date: str, initial_cash: float, equity: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    if equity.empty:
        return _empty_summary(start_date, end_date, initial_cash)
    final_value = float(equity.iloc[-1]["total_value"])
    total_return = final_value / initial_cash - 1
    periods = max(len(equity), 1)
    daily_returns = pd.to_numeric(equity["daily_return"], errors="coerce").dropna()
    std = daily_returns.std(ddof=1) if len(daily_returns) > 1 else 0.0
    wins, losses = _closed_trade_pnls(trades)
    holding_days = _holding_days(trades)
    return pd.DataFrame(
        [
            {
                "strategy": "turtle_system",
                "start_date": start_date,
                "end_date": end_date,
                "initial_cash": initial_cash,
                "final_value": final_value,
                "total_return": total_return,
                "annualized_return": (1 + total_return) ** (252 / periods) - 1 if final_value > 0 else -1.0,
                "annualized_volatility": float(std * (252 ** 0.5)) if std else 0.0,
                "sharpe_ratio": float(daily_returns.mean() / std * (252 ** 0.5)) if std else 0.0,
                "max_drawdown": float(equity["drawdown"].min()),
                "max_drawdown_days": _max_drawdown_days(equity),
                "win_rate": len(wins) / (len(wins) + len(losses)) if len(wins) + len(losses) else 0.0,
                "profit_loss_ratio": abs(wins.mean() / losses.mean()) if len(wins) and len(losses) and losses.mean() else 0.0,
                "average_holding_days": sum(holding_days) / len(holding_days) if holding_days else 0.0,
                "trade_count": len(trades),
                "position_utilization": float((pd.to_numeric(equity["position_value"], errors="coerce") > 0).mean()),
            }
        ],
        columns=TURTLE_SUMMARY_COLUMNS,
    )


def _drawdowns(equity: pd.DataFrame) -> pd.DataFrame:
    if equity.empty:
        return _empty_drawdowns()
    rows = []
    in_dd = False
    start = None
    min_date = None
    min_dd = 0.0
    for row in equity.itertuples(index=False):
        dd = float(row.drawdown)
        if dd < 0 and not in_dd:
            in_dd = True
            start = row.date
            min_date = row.date
            min_dd = dd
        elif dd < 0 and in_dd and dd < min_dd:
            min_dd = dd
            min_date = row.date
        elif dd == 0 and in_dd:
            rows.append({"start_date": start, "trough_date": min_date, "end_date": row.date, "max_drawdown": min_dd})
            in_dd = False
    if in_dd:
        rows.append({"start_date": start, "trough_date": min_date, "end_date": None, "max_drawdown": min_dd})
    return pd.DataFrame(rows, columns=["start_date", "trough_date", "end_date", "max_drawdown"])


def _symbol_pnl(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "realized_pnl" not in trades:
        return _empty_symbol_pnl()
    sells = trades[trades["action"] == "sell"].copy()
    if sells.empty:
        return _empty_symbol_pnl()
    sells["realized_pnl"] = pd.to_numeric(sells["realized_pnl"], errors="coerce").fillna(0.0)
    return (
        sells.groupby(["symbol", "code", "name"], as_index=False)
        .agg(realized_pnl=("realized_pnl", "sum"), trades=("realized_pnl", "count"))
        .sort_values("realized_pnl", ascending=False)
        .reset_index(drop=True)
    )


def _closed_trade_pnls(trades: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    if trades.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    pnl = pd.to_numeric(trades.loc[trades["action"] == "sell", "realized_pnl"], errors="coerce").dropna()
    return pnl[pnl > 0], pnl[pnl < 0]


def _holding_days(trades: pd.DataFrame) -> list[int]:
    days: list[int] = []
    entries: dict[str, pd.Timestamp] = {}
    for row in trades.itertuples(index=False):
        if row.action in {"buy", "add"} and row.symbol not in entries:
            entries[row.symbol] = pd.to_datetime(row.date)
        elif row.action == "sell" and row.symbol in entries:
            days.append(max((pd.to_datetime(row.date) - entries.pop(row.symbol)).days, 0))
    return days


def _max_drawdown_days(equity: pd.DataFrame) -> int:
    max_days = 0
    current = 0
    for dd in pd.to_numeric(equity["drawdown"], errors="coerce").fillna(0):
        if dd < 0:
            current += 1
            max_days = max(max_days, current)
        else:
            current = 0
    return max_days


def _portfolio_value(cash: float, states: dict[str, TurtlePosition], prices: dict[str, float]) -> float:
    value = cash
    for symbol, state in states.items():
        price = prices.get(symbol)
        if state.shares > 0 and _is_finite(price):
            value += state.shares * float(price)
    return float(value)


def _unit_shares(equity: float, n_value: float, config: TurtleConfig) -> int:
    if not _is_finite(equity) or not _is_finite(n_value) or n_value <= 0:
        return 0
    raw = equity * config.risk_pct / n_value
    if config.lot_size <= 1:
        return max(int(raw), 0)
    return max(int(raw // config.lot_size) * config.lot_size, 0)


def _atr(frame: pd.DataFrame, period: int) -> float:
    if len(frame) < period + 1:
        return float("nan")
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return float(tr.tail(period).mean())


def _price_on_date(frame: pd.DataFrame, date: str, column: str) -> float:
    rows = frame[frame["date"] == date]
    if rows.empty:
        return float("nan")
    value = rows.iloc[-1].get(column)
    return float(value) if _is_finite(value) else float("nan")


def _apply_slippage(price: float, action: str, slippage_rate: float) -> float:
    if slippage_rate <= 0:
        return price
    if action in {"enter", "add", "buy"}:
        return price * (1 + slippage_rate)
    return price * (1 - slippage_rate)


def _stock_item(item: str | StockInfo) -> StockInfo:
    if isinstance(item, StockInfo):
        return item
    symbol = normalize_symbol(item)
    return StockInfo(symbol=symbol, code=symbol_code(symbol), name="")


def _date_key(value: str) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def _empty_summary(start_date: str, end_date: str, initial_cash: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "strategy": "turtle_system",
                "start_date": start_date,
                "end_date": end_date,
                "initial_cash": initial_cash,
                "final_value": initial_cash,
                "total_return": 0.0,
                "annualized_return": 0.0,
                "annualized_volatility": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "max_drawdown_days": 0,
                "win_rate": 0.0,
                "profit_loss_ratio": 0.0,
                "average_holding_days": 0.0,
                "trade_count": 0,
                "position_utilization": 0.0,
            }
        ],
        columns=TURTLE_SUMMARY_COLUMNS,
    )


def _empty_equity() -> pd.DataFrame:
    return pd.DataFrame(columns=TURTLE_EQUITY_COLUMNS)


def _empty_trades() -> pd.DataFrame:
    return pd.DataFrame(columns=TURTLE_TRADE_COLUMNS)


def _empty_positions() -> pd.DataFrame:
    return pd.DataFrame(columns=TURTLE_POSITION_COLUMNS)


def _empty_signals() -> pd.DataFrame:
    return pd.DataFrame(columns=TURTLE_SIGNAL_COLUMNS)


def _empty_drawdowns() -> pd.DataFrame:
    return pd.DataFrame(columns=["start_date", "trough_date", "end_date", "max_drawdown"])


def _empty_symbol_pnl() -> pd.DataFrame:
    return pd.DataFrame(columns=["symbol", "code", "name", "realized_pnl", "trades"])


def _is_finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
