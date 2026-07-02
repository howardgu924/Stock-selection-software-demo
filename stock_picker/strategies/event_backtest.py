from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pandas as pd


EVENT_ORDER = ["morning_open", "noon", "afternoon_open", "close"]
SELL_TIMES = {"morning_open", "afternoon_open", "close"}
BUY_TIMES = {"afternoon_open"}


@dataclass(frozen=True)
class BacktestSettings:
    initial_cash: float = 100000.0
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.001
    slippage_pct: float = 0.0
    buy_lot_size: int = 100
    t_plus_one: bool = True
    force_final_liquidation: bool = True


@dataclass(frozen=True)
class Signal:
    symbol: str
    side: str
    shares: int
    reason: str = ""
    strategy_family: str = "thermostat"


@dataclass(frozen=True)
class EventContext:
    date: str
    time_point: str
    prices: pd.DataFrame
    positions: dict[str, dict[str, Any]]
    cash: float


@dataclass
class EventBacktestResult:
    summary: pd.DataFrame
    daily_portfolio: pd.DataFrame
    evaluation_detail: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame
    symbol_performance: pd.DataFrame
    data_quality: pd.DataFrame
    parameters: pd.DataFrame


class EventBacktestEngine:
    def __init__(self, settings: BacktestSettings | None = None) -> None:
        self.settings = settings or BacktestSettings()

    def run(
        self,
        event_prices: pd.DataFrame,
        signal_provider: Callable[[EventContext], list[Signal]],
    ) -> EventBacktestResult:
        prices = _prepare_event_prices(event_prices)
        dates = sorted(prices["date"].unique().tolist())
        cash = float(self.settings.initial_cash)
        positions: dict[str, dict[str, Any]] = {}
        trades: list[dict[str, Any]] = []
        evaluations: list[dict[str, Any]] = []
        daily_rows: list[dict[str, Any]] = []
        data_quality: list[dict[str, Any]] = []
        pending_for_afternoon: list[tuple[str, Signal]] = []

        for date_index, date in enumerate(dates):
            for position in positions.values():
                position["available_shares"] = position["total_shares"]
            cash_start = cash
            position_value_start = _position_value(positions, prices, date, "morning_open")

            for time_point in EVENT_ORDER:
                current_prices = prices[
                    (prices["date"] == date) & (prices["time_point"] == time_point)
                ]
                for warning in _price_warnings(current_prices, date, time_point):
                    data_quality.append(warning)
                context = EventContext(
                    date=date,
                    time_point=time_point,
                    prices=current_prices,
                    positions=positions,
                    cash=cash,
                )
                signals = signal_provider(context)
                evaluations.extend(
                    {
                        "date": date,
                        "time_point": time_point,
                        "symbol": signal.symbol,
                        "signal_action": signal.side,
                        "signal_reason": signal.reason,
                        "execution_time": "afternoon_open" if time_point == "noon" else time_point,
                    }
                    for signal in signals
                )
                if time_point == "noon":
                    pending_for_afternoon.extend((date, signal) for signal in signals)
                    continue
                if time_point == "afternoon_open":
                    todays_pending = [
                        signal for signal_date, signal in pending_for_afternoon if signal_date == date
                    ]
                    pending_for_afternoon = [
                        item for item in pending_for_afternoon if item[0] != date
                    ]
                    for signal in todays_pending:
                        cash = self._execute_signal(
                            signal=signal,
                            date=date,
                            signal_time="noon",
                            execution_time=time_point,
                            prices=current_prices,
                            cash=cash,
                            positions=positions,
                            trades=trades,
                        )
                for signal in signals:
                    if time_point == "afternoon_open" and signal in todays_pending:
                        continue
                    if signal.side == "buy" and time_point not in BUY_TIMES:
                        continue
                    if signal.side == "sell" and time_point not in SELL_TIMES:
                        continue
                    cash = self._execute_signal(
                        signal=signal,
                        date=date,
                        signal_time=time_point,
                        execution_time=time_point,
                        prices=current_prices,
                        cash=cash,
                        positions=positions,
                        trades=trades,
                    )
                if (
                    time_point == "close"
                    and self.settings.force_final_liquidation
                    and date_index == len(dates) - 1
                ):
                    for symbol, position in list(positions.items()):
                        if position["total_shares"] <= 0:
                            continue
                        cash = self._execute_signal(
                            signal=Signal(
                                symbol=symbol,
                                side="sell",
                                shares=int(position["total_shares"]),
                                reason="backtest_final_liquidation",
                                strategy_family="final_liquidation",
                            ),
                            date=date,
                            signal_time="close",
                            execution_time="close",
                            prices=current_prices,
                            cash=cash,
                            positions=positions,
                            trades=trades,
                        )

            position_value_end = _position_value(positions, prices, date, "close")
            total_value_end = cash + position_value_end
            daily_rows.append(
                {
                    "date": date,
                    "cash_start": cash_start,
                    "position_value_start": position_value_start,
                    "total_value_start": cash_start + position_value_start,
                    "cash_end": cash,
                    "position_value_end": position_value_end,
                    "total_value_end": total_value_end,
                    "daily_return": 0.0,
                    "drawdown": 0.0,
                }
            )

        daily = pd.DataFrame(daily_rows)
        if not daily.empty:
            daily["daily_return"] = daily["total_value_end"].pct_change().fillna(0.0)
            peak = daily["total_value_end"].cummax()
            daily["drawdown"] = daily["total_value_end"] / peak - 1
        trades_frame = pd.DataFrame(trades, columns=_trade_columns())
        positions_frame = pd.DataFrame(_position_rows(positions, prices), columns=_position_columns())
        summary = _summary(daily, trades_frame, self.settings.initial_cash)
        return EventBacktestResult(
            summary=summary,
            daily_portfolio=daily,
            evaluation_detail=pd.DataFrame(evaluations),
            trades=trades_frame,
            positions=positions_frame,
            symbol_performance=_symbol_performance(trades_frame),
            data_quality=pd.DataFrame(data_quality),
            parameters=_parameters(self.settings),
        )

    def _execute_signal(
        self,
        *,
        signal: Signal,
        date: str,
        signal_time: str,
        execution_time: str,
        prices: pd.DataFrame,
        cash: float,
        positions: dict[str, dict[str, Any]],
        trades: list[dict[str, Any]],
    ) -> float:
        price_row = _price_row(prices, signal.symbol)
        price = _as_float(price_row.get("price"))
        status = str(price_row.get("limit_status") or "limit_status_unknown")
        position = positions.setdefault(
            signal.symbol,
            {
                "symbol": signal.symbol,
                "total_shares": 0,
                "available_shares": 0,
                "average_cost": 0.0,
                "realized_pnl": 0.0,
            },
        )
        order_status = "filled"
        failure_reason = ""
        actual_shares = int(signal.shares)
        execution_price = price

        if price is None or status == "suspended":
            order_status = "failed_suspended"
            failure_reason = "停牌或执行价格缺失"
        elif status == "limit_status_unknown":
            order_status = "limit_status_unknown"
            failure_reason = "执行时间点涨跌停状态未知"
        elif signal.side == "buy" and status == "limit_up":
            order_status = "failed_limit_up"
            failure_reason = "买入时间点涨停"
        elif signal.side == "sell" and status == "limit_down":
            order_status = "failed_limit_down"
            failure_reason = "卖出时间点跌停"
        elif signal.side == "buy" and actual_shares < self.settings.buy_lot_size:
            order_status = "failed_lot_size"
            failure_reason = "买入数量不足一手"
        elif signal.side == "sell" and self.settings.t_plus_one and actual_shares > int(position["available_shares"]):
            order_status = "failed_t_plus_one"
            failure_reason = "T+1 限制，当日买入不可卖出"
        elif signal.side == "sell" and actual_shares > int(position["total_shares"]):
            order_status = "failed_position"
            failure_reason = "持仓不足"

        gross_amount = 0.0
        commission = 0.0
        stamp_tax = 0.0
        slippage_cost = 0.0
        cash_before = cash
        position_before = int(position["total_shares"])
        if order_status == "filled" and execution_price is not None:
            side_mult = 1 if signal.side == "buy" else -1
            adjusted_price = execution_price * (1 + self.settings.slippage_pct * side_mult)
            slippage_cost = abs(adjusted_price - execution_price) * actual_shares
            execution_price = round(adjusted_price, 4)
            gross_amount = execution_price * actual_shares
            commission = max(gross_amount * self.settings.commission_rate, self.settings.min_commission)
            if signal.side == "sell":
                stamp_tax = gross_amount * self.settings.stamp_tax_rate
            if signal.side == "buy":
                total_cost = gross_amount + commission
                if total_cost > cash:
                    order_status = "insufficient_cash"
                    failure_reason = "现金不足"
            if order_status == "filled" and signal.side == "buy":
                cash -= gross_amount + commission
                previous_cost = float(position["average_cost"]) * int(position["total_shares"])
                position["total_shares"] = int(position["total_shares"]) + actual_shares
                position["average_cost"] = (previous_cost + gross_amount) / int(position["total_shares"])
            elif order_status == "filled" and signal.side == "sell":
                cash += gross_amount - commission - stamp_tax
                position["total_shares"] = int(position["total_shares"]) - actual_shares
                position["available_shares"] = max(0, int(position["available_shares"]) - actual_shares)
                position["realized_pnl"] = float(position["realized_pnl"]) + (
                    gross_amount - float(position["average_cost"]) * actual_shares - commission - stamp_tax
                )

        if order_status != "filled":
            actual_shares = 0
            gross_amount = 0.0
            commission = 0.0
            stamp_tax = 0.0
            slippage_cost = 0.0

        trades.append(
            {
                "trade_id": uuid4().hex,
                "date": date,
                "signal_time": signal_time,
                "execution_time": execution_time,
                "symbol": signal.symbol,
                "name": "",
                "side": signal.side,
                "intended_shares": signal.shares,
                "actual_shares": actual_shares,
                "execution_price": execution_price,
                "gross_amount": gross_amount,
                "commission": commission,
                "stamp_tax": stamp_tax,
                "slippage_cost": slippage_cost,
                "net_amount": gross_amount - commission - stamp_tax if signal.side == "sell" else gross_amount + commission,
                "cash_before": cash_before,
                "cash_after": cash,
                "position_before": position_before,
                "position_after": int(position["total_shares"]),
                "shares_after": int(position["total_shares"]),
                "available_shares_after": int(position["available_shares"]),
                "trade_reason": signal.reason,
                "order_status": order_status,
                "failure_reason": failure_reason,
                "strategy_family": signal.strategy_family,
            }
        )
        return cash


def _prepare_event_prices(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data["date"] = data["date"].astype(str)
    data["time_point"] = data["time_point"].astype(str)
    data["event_order"] = data["time_point"].map({name: idx for idx, name in enumerate(EVENT_ORDER)})
    return data.sort_values(["date", "event_order", "symbol"]).reset_index(drop=True)


def _price_row(prices: pd.DataFrame, symbol: str) -> dict[str, Any]:
    rows = prices[prices["symbol"] == symbol]
    if rows.empty:
        return {"symbol": symbol, "price": None, "limit_status": "suspended"}
    return rows.iloc[0].to_dict()


def _position_value(positions: dict[str, dict[str, Any]], prices: pd.DataFrame, date: str, time_point: str) -> float:
    total = 0.0
    day_prices = prices[(prices["date"] == date) & (prices["time_point"] == time_point)]
    for symbol, position in positions.items():
        if int(position["total_shares"]) <= 0:
            continue
        row = _price_row(day_prices, symbol)
        price = _as_float(row.get("price")) or float(position["average_cost"])
        total += int(position["total_shares"]) * price
    return total


def _price_warnings(prices: pd.DataFrame, date: str, time_point: str) -> list[dict[str, Any]]:
    warnings = []
    for row in prices.to_dict("records"):
        status = str(row.get("limit_status") or "")
        warning = str(row.get("warning") or "")
        if status == "limit_status_unknown" or warning:
            warnings.append(
                {
                    "symbol": row.get("symbol", ""),
                    "date": date,
                    "time_point": time_point,
                    "warning": warning or "limit_status_unknown",
                }
            )
    return warnings


def _position_rows(positions: dict[str, dict[str, Any]], prices: pd.DataFrame) -> list[dict[str, Any]]:
    if prices.empty:
        return []
    last_date = sorted(prices["date"].unique().tolist())[-1]
    close_prices = prices[(prices["date"] == last_date) & (prices["time_point"] == "close")]
    rows = []
    for symbol, position in positions.items():
        if int(position["total_shares"]) <= 0:
            continue
        row = _price_row(close_prices, symbol)
        last_price = _as_float(row.get("price")) or float(position["average_cost"])
        rows.append(
            {
                "date": last_date,
                "symbol": symbol,
                "name": "",
                "total_shares": int(position["total_shares"]),
                "available_shares": int(position["available_shares"]),
                "average_cost": float(position["average_cost"]),
                "last_price": last_price,
                "market_value": last_price * int(position["total_shares"]),
                "unrealized_pnl": (last_price - float(position["average_cost"])) * int(position["total_shares"]),
                "realized_pnl": float(position["realized_pnl"]),
                "holding_days": 0,
                "stop_price": None,
                "target_price": None,
            }
        )
    return rows


def _summary(daily: pd.DataFrame, trades: pd.DataFrame, initial_cash: float) -> pd.DataFrame:
    final_value = float(daily.iloc[-1]["total_value_end"]) if not daily.empty else initial_cash
    total_return = final_value / initial_cash - 1 if initial_cash else 0.0
    return pd.DataFrame(
        [
            {
                "strategy": "event_driven_thermostat",
                "backtest_type": "event_driven",
                "initial_cash": initial_cash,
                "final_value": final_value,
                "total_return": total_return,
                "annualized_return": total_return,
                "max_drawdown": float(daily["drawdown"].min()) if not daily.empty else 0.0,
                "win_rate": 0.0,
                "profit_loss_ratio": 0.0,
                "average_holding_days": 0.0,
                "trade_count": len(trades),
                "position_utilization": float((daily["position_value_end"] > 0).mean()) if not daily.empty else 0.0,
                "cash_ratio": float(daily.iloc[-1]["cash_end"] / final_value) if final_value and not daily.empty else 1.0,
                "benchmark_return": 0.0,
                "total_commission": float(pd.to_numeric(trades.get("commission"), errors="coerce").fillna(0).sum()) if not trades.empty else 0.0,
                "total_slippage_cost": float(pd.to_numeric(trades.get("slippage_cost"), errors="coerce").fillna(0).sum()) if not trades.empty else 0.0,
            }
        ]
    )


def _symbol_performance(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["symbol", "trade_count"])
    return trades.groupby("symbol", as_index=False).agg(trade_count=("trade_id", "count"))


def _parameters(settings: BacktestSettings) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "parameter_name": name,
                "parameter_value": value,
                "parameter_source": "system_default",
                "user_overridden": False,
                "note": "",
            }
            for name, value in settings.__dict__.items()
        ]
    )


def _as_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _trade_columns() -> list[str]:
    return [
        "trade_id",
        "date",
        "signal_time",
        "execution_time",
        "symbol",
        "name",
        "side",
        "intended_shares",
        "actual_shares",
        "execution_price",
        "gross_amount",
        "commission",
        "stamp_tax",
        "slippage_cost",
        "net_amount",
        "cash_before",
        "cash_after",
        "position_before",
        "position_after",
        "shares_after",
        "available_shares_after",
        "trade_reason",
        "order_status",
        "failure_reason",
        "strategy_family",
    ]


def _position_columns() -> list[str]:
    return [
        "date",
        "symbol",
        "name",
        "total_shares",
        "available_shares",
        "average_cost",
        "last_price",
        "market_value",
        "unrealized_pnl",
        "realized_pnl",
        "holding_days",
        "stop_price",
        "target_price",
    ]

