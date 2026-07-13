from __future__ import annotations

from dataclasses import asdict
from datetime import date

import pandas as pd
import pytest

from stock_picker.strategies.thermostat_execution import (
    DailyBar,
    PortfolioLedger,
    T1ExecutionSettings,
    execute_buy,
    execute_sell,
)
from stock_picker.strategies.thermostat_metrics import (
    CLOSED_TRADE_CYCLE_COLUMNS,
    METRIC_SUMMARY_COLUMNS,
    compute_t1_thermostat_metrics,
)


def test_trend_lot_closed_after_trend_to_range_owner_migration() -> None:
    settings = T1ExecutionSettings(slippage_pct=0.0)
    ledger = PortfolioLedger(cash=100_000.0, initial_capital=100_000.0)
    buy_date = date(2026, 7, 1)
    sell_date = date(2026, 7, 2)
    buy = execute_buy(
        ledger,
        settings,
        DailyBar(buy_date, 10.0, 10.5, 9.5, 10.0, 1_000.0, 9.8),
        symbol="600001.SH",
        mode="trend",
        family="trend",
        trigger_type="trend_buy",
        trigger_price=10.0,
        intended_shares=100,
        trade_date=buy_date,
        trend_batch=1,
    )
    state = ledger.positions["600001.SH"]
    state.start_trading_day(sell_date)
    state.transition_mode("range", current_position_ratio=0.01, range_cap_ratio=0.6)
    sell = execute_sell(
        ledger,
        settings,
        DailyBar(sell_date, 12.0, 12.5, 11.5, 12.0, 1_000.0, 10.0),
        symbol="600001.SH",
        mode="range",
        family="grid",
        trigger_type="grid_sell",
        trigger_price=12.0,
        intended_shares=100,
        trade_date=sell_date,
        grid_layer="trend_base",
    )

    result = compute_t1_thermostat_metrics(
        daily_assets=pd.DataFrame([{"date": "2026-07-02", "total_asset": ledger.cash}]),
        daily_positions=pd.DataFrame([
            {"date": "2026-07-02", "symbol": "600001.SH", "close": 12.0},
        ]),
        fills=pd.DataFrame([asdict(buy), asdict(sell)]),
        lifecycle_orders=pd.DataFrame(),
        pending_history=pd.DataFrame(),
        data_quality=pd.DataFrame(),
        corporate_actions=pd.DataFrame(),
        benchmark=pd.DataFrame(),
        initial_cash=100_000.0,
    )

    assert state.total_shares == 0
    assert len(result.closed_trade_cycles) == 1
    cycle = result.closed_trade_cycles.iloc[0]
    assert cycle["buy_order_id"] == buy.order_id
    assert cycle["sell_order_id"] == sell.order_id
    assert cycle["family"] == "trend"
    assert result.summary.iloc[0]["completed_cycle_count"] == 1
    assert result.summary.iloc[0]["completed_cycle_win_rate"] == pytest.approx(1.0)
    assert result.symbol_performance.iloc[0]["realized_pnl"] == pytest.approx(cycle["net_pnl"])
    assert result.trend_performance.iloc[0]["realized_pnl"] == pytest.approx(cycle["net_pnl"])
    assert result.grid_performance.empty


def _fills() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "order_id": "buy-closed", "trade_date": "2026-07-01",
                "symbol": "600001.SH", "family": "trend", "side": "buy",
                "actual_shares": 100, "execution_price": 10.0,
                "commission": 5.0, "stamp_tax": 0.0, "trend_batch": 1,
                "grid_layer": None,
            },
            {
                "order_id": "sell-closed", "trade_date": "2026-07-03",
                "symbol": "600001.SH", "family": "trend", "side": "sell",
                "actual_shares": 100, "execution_price": 12.0,
                "commission": 5.0, "stamp_tax": 1.2, "trend_batch": None,
                "grid_layer": None,
            },
            {
                "order_id": "buy-open", "trade_date": "2026-07-03",
                "symbol": "600002.SH", "family": "grid", "side": "buy",
                "actual_shares": 100, "execution_price": 8.0,
                "commission": 5.0, "stamp_tax": 0.0, "trend_batch": None,
                "grid_layer": "grid-1",
            },
        ]
    )


def test_fifo_closed_cycles_allocate_costs_and_exclude_unsold_lots() -> None:
    daily_assets = pd.DataFrame(
        [
            {"date": "2026-07-01", "total_asset": 100_000.0, "cash": 98_995.0, "position_value": 1_000.0},
            {"date": "2026-07-02", "total_asset": 100_100.0, "cash": 98_995.0, "position_value": 1_100.0},
            {"date": "2026-07-03", "total_asset": 100_983.8, "cash": 99_183.8, "position_value": 1_800.0},
        ]
    )

    result = compute_t1_thermostat_metrics(
        daily_assets=daily_assets,
        daily_positions=pd.DataFrame([
            {"date": "2026-07-03", "symbol": "600001.SH", "close": 12.0},
            {"date": "2026-07-03", "symbol": "600002.SH", "close": 9.0},
        ]),
        fills=_fills(),
        lifecycle_orders=pd.DataFrame(),
        pending_history=pd.DataFrame(),
        data_quality=pd.DataFrame(),
        corporate_actions=pd.DataFrame(),
        benchmark=pd.DataFrame(),
        initial_cash=100_000.0,
    )

    assert len(result.closed_trade_cycles) == 1
    cycle = result.closed_trade_cycles.iloc[0]
    assert cycle["buy_order_id"] == "buy-closed"
    assert cycle["sell_order_id"] == "sell-closed"
    assert cycle["shares"] == 100
    assert cycle["net_pnl"] == pytest.approx(188.8)
    assert cycle["holding_days"] == 2
    assert "buy-open" not in set(result.closed_trade_cycles["buy_order_id"])
    summary = result.summary.iloc[0]
    assert summary["completed_cycle_count"] == 1
    assert summary["completed_cycle_win_rate"] == pytest.approx(1.0)
    assert summary["buy_count"] == 2
    assert summary["sell_count"] == 1
    assert pd.isna(summary["profit_loss_ratio"])
    expected_utilization = pd.Series([1000 / 100000, 1100 / 100100, 1800 / 100983.8]).mean()
    assert summary["average_position_utilization"] == pytest.approx(expected_utilization)
    assert summary["max_position_utilization"] == pytest.approx(1800 / 100983.8)
    open_grid = result.grid_performance.iloc[0]
    assert open_grid["completed_cycles"] == 0
    assert open_grid["unrealized_pnl"] == pytest.approx(95.0)
    assert open_grid["total_pnl"] == pytest.approx(95.0)
    assert "600002.SH" in set(result.symbol_performance["key"])


def test_metrics_empty_inputs_and_zero_variance_have_stable_deterministic_outputs() -> None:
    result = compute_t1_thermostat_metrics(
        daily_assets=pd.DataFrame(),
        daily_positions=pd.DataFrame(),
        fills=pd.DataFrame(),
        lifecycle_orders=pd.DataFrame(),
        pending_history=pd.DataFrame(),
        data_quality=pd.DataFrame(),
        corporate_actions=pd.DataFrame(),
        benchmark=pd.DataFrame(),
        initial_cash=50_000.0,
    )

    assert list(result.summary.columns) == METRIC_SUMMARY_COLUMNS
    assert list(result.closed_trade_cycles.columns) == CLOSED_TRADE_CYCLE_COLUMNS
    summary = result.summary.iloc[0]
    assert summary["initial_asset"] == pytest.approx(50_000.0)
    assert summary["final_asset"] == pytest.approx(50_000.0)
    assert summary["total_return"] == 0.0
    assert summary["annualized_return"] == 0.0
    assert summary["sharpe_ratio"] == 0.0
    assert summary["annual_volatility"] == 0.0
    assert summary["completed_cycle_count"] == 0
    assert result.symbol_performance.empty
    assert result.trend_performance.empty
    assert result.grid_performance.empty


def test_ambiguity_count_counts_ambiguous_rows_not_daily_precision_defaults() -> None:
    result = compute_t1_thermostat_metrics(
        daily_assets=pd.DataFrame(), daily_positions=pd.DataFrame(),
        fills=pd.DataFrame(),
        lifecycle_orders=pd.DataFrame([
            {
                "approximate_intraday_sequence": True,
                "quality_warning": "daily_plan_expires_after_close",
            },
            {
                "approximate_intraday_sequence": True,
                "quality_warning": "approximate_intraday_sequence",
            },
        ]),
        pending_history=pd.DataFrame(), data_quality=pd.DataFrame(),
        corporate_actions=pd.DataFrame(), benchmark=pd.DataFrame(),
        initial_cash=50_000.0,
        daily_trigger_plans=pd.DataFrame([
            {
                "approximate_intraday_sequence": True,
                "approximation_warnings": "approximate_intraday_sequence",
            },
        ]),
    )

    assert result.summary.iloc[0]["ambiguity_count"] == 2
