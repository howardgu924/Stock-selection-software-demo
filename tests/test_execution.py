from __future__ import annotations

import pandas as pd

from stock_picker.execution import build_execution_plan, limit_up_price, price_limit_pct


def test_execution_plan_marks_limit_up_and_suggests_alternative() -> None:
    signals = pd.DataFrame(
        [
            {
                "strategy": "turtle",
                "symbol": "600001.SH",
                "name": "A",
                "action": "buy",
                "score": 0.08,
                "rank": 1,
            },
            {
                "strategy": "turtle",
                "symbol": "600002.SH",
                "name": "B",
                "action": "buy",
                "score": 0.05,
                "rank": 2,
            },
        ]
    )
    quotes = pd.DataFrame(
        [
            {
                "symbol": "600001.SH",
                "name": "A",
                "price": 11.0,
                "high": 11.0,
                "prev_close": 10.0,
            },
            {
                "symbol": "600002.SH",
                "name": "B",
                "price": 9.5,
                "high": 9.7,
                "prev_close": 9.0,
            },
        ]
    )

    plan = build_execution_plan(signals, quotes, cash=5000.0)

    limit_row = plan[plan["symbol"] == "600001.SH"].iloc[0]
    buy_row = plan[plan["symbol"] == "600002.SH"].iloc[0]
    assert limit_row["limit_status"] == "limit_up"
    assert limit_row["recommended_action"] == "queue_limit_up"
    assert limit_row["fallback_action"] == "switch_alternative"
    assert limit_row["alternative_symbol"] == "600002.SH"
    assert buy_row["recommended_action"] == "buy_now"
    assert buy_row["shares"] == 500


def test_limit_pct_uses_board_rules() -> None:
    assert price_limit_pct("600001.SH") == 0.10
    assert price_limit_pct("300001.SZ") == 0.20
    assert price_limit_pct("688001.SH") == 0.20
    assert price_limit_pct("600001.SH", "*ST Test") == 0.05
    assert limit_up_price(10.0, 0.10) == 11.0


def test_execution_plan_carries_turtle_risk_prices() -> None:
    signals = pd.DataFrame(
        [
            {
                "strategy": "turtle_system",
                "symbol": "600001.SH",
                "name": "A",
                "action": "buy",
                "score": 0.1,
                "rank": 1,
                "stop_price": 9.0,
                "next_add_price": 12.0,
                "exit_price": 8.5,
            }
        ]
    )
    quotes = pd.DataFrame(
        [{"symbol": "600001.SH", "name": "A", "price": 10.0, "high": 10.1, "prev_close": 9.8}]
    )

    plan = build_execution_plan(signals, quotes, cash=5000.0)

    assert plan.loc[0, "recommended_action"] == "buy_now"
    assert plan.loc[0, "stop_price"] == 9.0
    assert plan.loc[0, "next_add_price"] == 12.0
    assert plan.loc[0, "exit_price"] == 8.5


def test_execution_plan_skips_when_turtle_unit_exceeds_cash() -> None:
    signals = pd.DataFrame(
        [
            {
                "strategy": "turtle_system",
                "symbol": "600001.SH",
                "name": "A",
                "action": "buy",
                "score": 0.1,
                "rank": 1,
                "system": "S1",
                "suggested_shares": 1000,
            }
        ]
    )
    quotes = pd.DataFrame(
        [{"symbol": "600001.SH", "name": "A", "price": 10.0, "high": 11.0, "prev_close": 10.0}]
    )

    plan = build_execution_plan(signals, quotes, cash=5000.0)

    assert plan.loc[0, "recommended_action"] == "skip_insufficient_cash"
    assert plan.loc[0, "system"] == "S1"


def test_execution_plan_accepts_thermostat_buy_and_add_actions() -> None:
    signals = pd.DataFrame(
        [
            {
                "strategy": "thermostat",
                "strategy_family": "trend_following",
                "symbol": "600001.SH",
                "name": "A",
                "action": "add",
                "score": 0.8,
                "rank": 1,
                "suggested_shares": 200,
                "stop_price": 9.2,
                "risk_note": "趋势恶化则退出",
            }
        ]
    )
    quotes = pd.DataFrame(
        [{"symbol": "600001.SH", "name": "A", "price": 10.0, "high": 10.1, "prev_close": 9.8, "volume": 100000}]
    )

    plan = build_execution_plan(signals, quotes, cash=5000.0)

    assert plan.loc[0, "strategy"] == "thermostat"
    assert plan.loc[0, "signal_action"] == "add"
    assert plan.loc[0, "recommended_action"] == "buy_now"
    assert plan.loc[0, "shares"] == 200
    assert plan.loc[0, "stop_price"] == 9.2


def test_execution_plan_preserves_non_buy_thermostat_reasons() -> None:
    signals = pd.DataFrame(
        [
            {
                "strategy": "thermostat",
                "symbol": "600001.SH",
                "name": "A",
                "action": "stop_grid",
                "score": 0.2,
                "rank": 1,
                "reference_price": 9.5,
                "reason": "跌破震荡区间下沿",
                "risk_note": "停止网格扩仓",
            }
        ]
    )
    quotes = pd.DataFrame(
        [{"symbol": "600001.SH", "name": "A", "price": 9.4, "high": 9.6, "prev_close": 9.8, "volume": 100000}]
    )

    plan = build_execution_plan(signals, quotes, cash=5000.0)

    assert plan.loc[0, "signal_action"] == "stop_grid"
    assert plan.loc[0, "recommended_action"] == "manual_stop_grid_review"
    assert plan.loc[0, "executable"] == False  # noqa: E712
    assert "跌破震荡区间下沿" in plan.loc[0, "reason"]
