from __future__ import annotations

import pandas as pd

from stock_picker.strategies.event_backtest import (
    BacktestSettings,
    EventBacktestEngine,
    Signal,
)


def _prices(
    *,
    morning_status: str = "normal",
    afternoon_status: str = "normal",
    close_status: str = "normal",
) -> pd.DataFrame:
    rows = []
    for time_point, price, status in [
        ("morning_open", 10.0, morning_status),
        ("noon", 10.2, "normal"),
        ("afternoon_open", 10.4, afternoon_status),
        ("close", 10.6, close_status),
    ]:
        rows.append(
            {
                "symbol": "600001.SH",
                "date": "2026-01-02",
                "time_point": time_point,
                "price": price,
                "limit_status": status,
                "is_suspended": status == "suspended",
            }
        )
    return pd.DataFrame(rows)


def test_noon_signal_does_not_trade_until_afternoon_open() -> None:
    engine = EventBacktestEngine(
        BacktestSettings(initial_cash=20_000.0, force_final_liquidation=False)
    )

    def signal_provider(context):
        if context.time_point == "noon":
            return [Signal(symbol="600001.SH", side="buy", shares=100, reason="中午信号")]
        return []

    result = engine.run(_prices(), signal_provider=signal_provider)

    assert result.trades["signal_time"].tolist() == ["noon"]
    assert result.trades["execution_time"].tolist() == ["afternoon_open"]
    assert result.trades["order_status"].tolist() == ["filled"]
    assert result.daily_portfolio.loc[0, "cash_end"] < 20_000.0


def test_buy_fails_at_afternoon_limit_up_and_is_recorded() -> None:
    engine = EventBacktestEngine(
        BacktestSettings(initial_cash=20_000.0, force_final_liquidation=False)
    )

    def signal_provider(context):
        if context.time_point == "noon":
            return [Signal(symbol="600001.SH", side="buy", shares=100, reason="中午信号")]
        return []

    result = engine.run(
        _prices(afternoon_status="limit_up"),
        signal_provider=signal_provider,
    )

    assert result.trades["order_status"].tolist() == ["failed_limit_up"]
    assert result.trades["actual_shares"].tolist() == [0]
    assert result.daily_portfolio.loc[0, "cash_end"] == 20_000.0


def test_unknown_limit_status_blocks_trade() -> None:
    engine = EventBacktestEngine(
        BacktestSettings(initial_cash=20_000.0, force_final_liquidation=False)
    )

    def signal_provider(context):
        if context.time_point == "noon":
            return [Signal(symbol="600001.SH", side="buy", shares=100, reason="中午信号")]
        return []

    result = engine.run(
        _prices(afternoon_status="limit_status_unknown"),
        signal_provider=signal_provider,
    )

    assert result.trades["order_status"].tolist() == ["limit_status_unknown"]
    assert "未知" in result.trades.loc[0, "failure_reason"]


def test_t1_prevents_same_day_sell_after_buy() -> None:
    engine = EventBacktestEngine(
        BacktestSettings(
            initial_cash=20_000.0,
            t_plus_one=True,
            force_final_liquidation=False,
        )
    )

    def signal_provider(context):
        if context.time_point == "noon":
            return [
                Signal(symbol="600001.SH", side="buy", shares=100, reason="买入"),
                Signal(symbol="600001.SH", side="sell", shares=100, reason="同日卖出"),
            ]
        return []

    result = engine.run(_prices(), signal_provider=signal_provider)

    assert result.trades["order_status"].tolist() == ["filled", "failed_t_plus_one"]


def test_final_close_liquidation_records_failed_limit_down() -> None:
    engine = EventBacktestEngine(
        BacktestSettings(initial_cash=20_000.0, force_final_liquidation=True, t_plus_one=False)
    )

    def signal_provider(context):
        if context.time_point == "noon":
            return [Signal(symbol="600001.SH", side="buy", shares=100, reason="买入")]
        return []

    result = engine.run(
        _prices(close_status="limit_down"),
        signal_provider=signal_provider,
    )

    assert result.trades["order_status"].tolist() == ["filled", "failed_limit_down"]
    assert result.positions.loc[0, "total_shares"] == 100
