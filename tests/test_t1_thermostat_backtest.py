from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_picker.data.backtest_data import (
    BacktestDataBundle,
    BacktestDataRequest,
    SymbolBacktestData,
)
from stock_picker.strategies.backtest_params import resolve_backtest_settings
from stock_picker.strategies.thermostat_backtest import (
    RESULT_TABLE_COLUMNS,
    BacktestPrecision,
    T1ThermostatBacktestRequest,
    _resolve_settings,
    run_t1_thermostat_backtest,
)
from stock_picker.strategies.thermostat_execution import T1ExecutionSettings
from stock_picker.strategies import backtest_thermostat_strategy


DAY = "2026-01-02"
SYMBOL = "600001.SH"
BENCHMARK = "000300.SH"
SECOND_SYMBOL = "600002.SH"


def test_resolved_account_cap_reaches_t1_settings_with_cash_only_override() -> None:
    account = SimpleNamespace(
        cash=40_000.0,
        commission_rate=0.0002,
        min_commission=3.0,
        stamp_tax_rate=0.001,
        slippage_pct=0.0008,
        max_total_position_pct=0.90,
    )
    resolved = resolve_backtest_settings(portfolio=account)
    request = T1ThermostatBacktestRequest(
        service=object(),
        symbols=(SYMBOL,),
        start=DAY,
        end=DAY,
        initial_cash=50_000.0,
        resolved_account_settings=resolved,
    )

    initial_cash, settings, parameter_rows = _resolve_settings(request)

    assert initial_cash == 50_000.0
    assert settings.commission_rate == 0.0002
    assert settings.slippage_pct == 0.0008
    assert settings.account_total_max == 0.90
    parameters = {row["parameter_name"]: row for row in parameter_rows}
    assert parameters["initial_cash"]["parameter_source"] == "user_override"
    for name in (
        "commission_rate",
        "minimum_commission",
        "stamp_tax_rate",
        "slippage_pct",
        "account_total_max",
    ):
        assert parameters[name]["parameter_source"] == "account_setting"


def test_resolved_default_cap_keeps_system_default_source() -> None:
    resolved = resolve_backtest_settings(portfolio=None)
    request = T1ThermostatBacktestRequest(
        service=object(),
        symbols=(SYMBOL,),
        start=DAY,
        end=DAY,
        resolved_account_settings=resolved,
    )

    _, settings, parameter_rows = _resolve_settings(request)

    assert settings.account_total_max == 0.95
    parameters = {row["parameter_name"]: row for row in parameter_rows}
    for name in (
        "commission_rate",
        "minimum_commission",
        "stamp_tax_rate",
        "slippage_pct",
        "buy_lot_size",
        "account_total_max",
    ):
        assert parameters[name]["parameter_source"] == "system_default"


def test_explicit_execution_settings_are_reported_as_user_overrides() -> None:
    request = T1ThermostatBacktestRequest(
        service=object(),
        symbols=(SYMBOL,),
        start=DAY,
        end=DAY,
        execution_settings=T1ExecutionSettings(
            commission_rate=0.0006,
            minimum_commission=7.0,
            stamp_tax_rate=0.0004,
            slippage_pct=0.002,
            buy_lot_size=200,
            account_total_max=0.88,
        ),
    )

    _, _, parameter_rows = _resolve_settings(request)

    parameters = {row["parameter_name"]: row for row in parameter_rows}
    for name in (
        "commission_rate",
        "minimum_commission",
        "stamp_tax_rate",
        "slippage_pct",
        "buy_lot_size",
        "account_total_max",
    ):
        assert parameters[name]["parameter_source"] == "user_override"
        assert parameters[name]["user_overridden"] is True


def _history(*, current_qfq_close: float = 35.2) -> tuple[pd.DataFrame, pd.DataFrame]:
    prior = pd.bdate_range(end="2025-12-31", periods=252)
    closes = pd.Series([10.0 + index * 0.1 for index in range(252)])
    qfq = pd.DataFrame(
        {
            "date": prior.strftime("%Y-%m-%d"),
            "open": closes - 0.05,
            "high": closes + 0.1,
            "low": closes - 0.1,
            "close": closes,
            "volume": 1000.0,
            "adjust_type": "qfq",
        }
    )
    current_qfq = pd.DataFrame(
        [{
            "date": DAY, "open": current_qfq_close - 1.0,
            "high": current_qfq_close + 50.0,
            "low": max(0.5, current_qfq_close - 2.0), "close": current_qfq_close,
            "volume": current_qfq_close * 1234.0, "adjust_type": "qfq",
        }]
    )
    qfq = pd.concat([qfq, current_qfq], ignore_index=True)
    bfq = pd.DataFrame(
        [{
            "date": DAY, "open": 35.5, "high": 36.0, "low": 35.4,
            "close": 35.8, "volume": 2000.0, "prev_close": 35.1,
            "limit_up_price": 38.61, "limit_down_price": 31.59,
            "is_suspended": False, "adjust_type": "bfq", "warning": "",
        }]
    )
    return qfq, bfq


def _bundle(*, current_qfq_close: float = 35.2) -> BacktestDataBundle:
    qfq, bfq = _history(current_qfq_close=current_qfq_close)
    benchmark = qfq.copy()
    request = BacktestDataRequest(
        symbols=(SYMBOL, BENCHMARK), start=DAY, end=DAY,
    )
    symbols = {
        SYMBOL: SymbolBacktestData(SYMBOL, qfq, bfq, 252, buy_eligible=True),
        BENCHMARK: SymbolBacktestData(BENCHMARK, benchmark, bfq, 252, buy_eligible=True),
    }
    return BacktestDataBundle(
        request=request, symbols=symbols,
        trading_calendar=tuple(list(qfq["date"].astype(str))),
        load_summary={"cache_hits": 0, "cache_misses": 4, "partial_fetch_ranges": 4, "provider_failures": 0},
        quality_issues=[], corporate_action_impacts=[],
    )


def test_runner_progress_callback_tracks_actual_load_simulation_and_metrics(monkeypatch) -> None:
    trace: list[str] = []
    bundle = _bundle()

    def fake_load(service, request):
        trace.append("loader")
        return bundle

    from stock_picker.strategies import thermostat_backtest as module

    real_metrics = module.compute_t1_thermostat_metrics

    def traced_metrics(**kwargs):
        trace.append("metrics")
        return real_metrics(**kwargs)

    monkeypatch.setattr(module, "load_t1_backtest_data", fake_load)
    monkeypatch.setattr(module, "compute_t1_thermostat_metrics", traced_metrics)

    def progress(event):
        trace.append(f'{event["stage"]}:{event["completed"]}/{event["total"]}')

    run_t1_thermostat_backtest(
        T1ThermostatBacktestRequest(
            service=object(), symbols=(SYMBOL,), start=DAY, end=DAY,
        ),
        progress_callback=progress,
    )

    assert trace == [
        "load_backtest_data:0/1",
        "loader",
        "load_backtest_data:1/1",
        "simulate_daily:0/1",
        "simulate_daily:1/1",
        "calculate_metrics:0/1",
        "metrics",
        "calculate_metrics:1/1",
    ]


def test_public_full_backtest_entry_delegates_to_t1_request_result_contract(monkeypatch) -> None:
    from stock_picker.strategies import thermostat_backtest as module

    request = T1ThermostatBacktestRequest(
        service=object(), symbols=(SYMBOL,), start=DAY, end=DAY,
    )
    expected = object()
    calls = []
    monkeypatch.setattr(
        module,
        "run_t1_thermostat_backtest",
        lambda actual, progress_callback=None: calls.append((actual, progress_callback)) or expected,
    )
    progress = lambda event: None

    actual = backtest_thermostat_strategy(request, progress_callback=progress)

    assert actual is expected
    assert calls == [(request, progress)]


def _three_day_bundle() -> BacktestDataBundle:
    prior_qfq, _ = _history()
    prior_qfq = prior_qfq[prior_qfq["date"] < DAY].copy()
    days = ["2026-01-02", "2026-01-05", "2026-01-06"]
    qfq_tail = pd.DataFrame([
        {"date": days[0], "open": 35.15, "high": 35.3, "low": 35.1, "close": 35.2, "volume": 1000.0, "adjust_type": "qfq"},
        {"date": days[1], "open": 35.25, "high": 35.4, "low": 35.2, "close": 35.3, "volume": 1000.0, "adjust_type": "qfq"},
        {"date": days[2], "open": 35.35, "high": 35.5, "low": 35.3, "close": 35.4, "volume": 1000.0, "adjust_type": "qfq"},
    ])
    qfq = pd.concat([prior_qfq, qfq_tail], ignore_index=True)
    bfq = pd.DataFrame([
        {"date": days[0], "open": 35.5, "high": 36.0, "low": 35.4, "close": 35.8, "volume": 1000.0},
        {"date": days[1], "open": 36.2, "high": 37.0, "low": 36.1, "close": 36.8, "volume": 1000.0},
        {"date": days[2], "open": 37.2, "high": 38.0, "low": 37.1, "close": 37.8, "volume": 1000.0},
    ])
    bfq["adjust_type"] = "bfq"
    bfq["prev_close"] = [35.1, 35.8, 36.8]
    bfq["limit_up_price"] = [38.61, 39.38, 40.48]
    bfq["limit_down_price"] = [31.59, 32.22, 33.12]
    bfq["is_suspended"] = False
    bfq["warning"] = ""
    request = BacktestDataRequest(symbols=(SYMBOL, BENCHMARK), start=days[0], end=days[-1])
    symbol_data = SymbolBacktestData(SYMBOL, qfq, bfq, 252, buy_eligible=True)
    benchmark_data = SymbolBacktestData(BENCHMARK, qfq.copy(), bfq.copy(), 252, buy_eligible=True)
    return BacktestDataBundle(
        request=request, symbols={SYMBOL: symbol_data, BENCHMARK: benchmark_data},
        trading_calendar=tuple(qfq["date"].astype(str)),
        load_summary={"cache_hits": 0, "cache_misses": 4, "partial_fetch_ranges": 4, "provider_failures": 0},
        quality_issues=[], corporate_action_impacts=[],
    )


def _pending_retry_bundle() -> BacktestDataBundle:
    bundle = _three_day_bundle()
    bfq = bundle.symbols[SYMBOL].execution_frame.copy()
    bfq.loc[0, ["open", "high", "low", "close", "volume"]] = [34.8, 36.0, 34.0, 34.5, 3000.0]
    bfq.loc[1, ["open", "high", "low", "close", "volume"]] = [32.22, 32.22, 32.22, 32.22, 1000.0]
    bfq.loc[2, ["open", "high", "low", "close", "volume"]] = [34.0, 34.2, 33.5, 33.8, 1000.0]
    bundle.symbols[SYMBOL] = replace(bundle.symbols[SYMBOL], execution_frame=bfq)
    return bundle


def _pending_missing_bar_bundle() -> BacktestDataBundle:
    bundle = _pending_retry_bundle()
    first = bundle.symbols[SYMBOL]
    missing_middle = first.execution_frame[
        first.execution_frame["date"] != "2026-01-05"
    ].reset_index(drop=True)
    bundle.symbols[SYMBOL] = replace(first, execution_frame=missing_middle)
    second_qfq = first.indicator_frame.copy()
    second_bfq = pd.DataFrame([{
        "date": "2026-01-05", "open": 10.0, "high": 10.1, "low": 9.9,
        "close": 10.0, "volume": 1000.0, "prev_close": 10.0,
        "limit_up_price": 11.0, "limit_down_price": 9.0,
        "is_suspended": False, "adjust_type": "bfq", "warning": "",
    }])
    bundle.symbols[SECOND_SYMBOL] = SymbolBacktestData(
        SECOND_SYMBOL, second_qfq, second_bfq, 252, buy_eligible=True,
    )
    return bundle


def _grid_bundle() -> BacktestDataBundle:
    bundle = _three_day_bundle()
    bfq = bundle.symbols[SYMBOL].execution_frame.copy()
    bfq.loc[0, ["open", "high", "low", "close"]] = [10.0, 10.2, 9.4, 10.0]
    bfq.loc[1, ["open", "high", "low", "close"]] = [9.6, 10.3, 8.9, 9.5]
    bfq.loc[2, ["open", "high", "low", "close"]] = [9.8, 10.6, 8.4, 10.0]
    bfq["prev_close"] = [10.0, 10.0, 9.5]
    bfq["limit_up_price"] = [11.0, 11.0, 10.45]
    bfq["limit_down_price"] = [9.0, 9.0, 8.55]
    bundle.symbols[SYMBOL] = replace(bundle.symbols[SYMBOL], execution_frame=bfq)
    return bundle


def _fixed_grid_evaluation(*args, **kwargs):
    del args, kwargs
    return SimpleNamespace(trigger_plan=pd.DataFrame([{
        "symbol": SYMBOL, "date": "", "stock_mode": "range",
        "market_regime": "market_range", "market_regime_normalized": "normal",
        "market_position_discount": 1.0, "target_position_pct": 0.15,
        "max_position_pct": 0.15, "grid_lower": 8.0, "grid_mid": 10.0,
        "grid_upper": 12.0, "grid_buy_levels": "9.5|9.0|8.5",
        "grid_sell_levels": "10.5|11.0|11.5", "configured_grid_layers": 3,
        "effective_grid_layers": 3, "grid_total_max_position_pct": 0.40,
        "trigger_status": "planned", "filled_status": "not_checked",
        "failed_reason": "", "data_sufficient": True,
    }]))


def _cross_symbol_bundle() -> BacktestDataBundle:
    base = _three_day_bundle()
    days = ["2026-01-02", "2026-01-05"]
    first_qfq = base.symbols[SYMBOL].indicator_frame
    first_bfq = pd.DataFrame([
        {"date": days[0], "open": 10.0, "high": 10.2, "low": 9.4, "close": 10.0, "volume": 1000.0},
        {"date": days[1], "open": 10.3, "high": 10.6, "low": 9.8, "close": 10.4, "volume": 1000.0},
    ])
    second_bfq = pd.DataFrame([
        {"date": days[0], "open": 9.5, "high": 9.8, "low": 9.2, "close": 9.5, "volume": 1000.0},
        {"date": days[1], "open": 9.8, "high": 10.2, "low": 9.5, "close": 10.0, "volume": 1000.0},
    ])
    for frame in (first_bfq, second_bfq):
        frame["adjust_type"] = "bfq"
        frame["prev_close"] = 10.0
        frame["limit_up_price"] = 11.0
        frame["limit_down_price"] = 9.0
        frame["is_suspended"] = False
        frame["warning"] = ""
    symbols = {
        SYMBOL: SymbolBacktestData(SYMBOL, first_qfq, first_bfq, 252, buy_eligible=True),
        SECOND_SYMBOL: SymbolBacktestData(SECOND_SYMBOL, first_qfq.copy(), second_bfq, 252, buy_eligible=True),
        BENCHMARK: base.symbols[BENCHMARK],
    }
    return BacktestDataBundle(
        request=BacktestDataRequest(symbols=tuple(symbols), start=days[0], end=days[1]),
        symbols=symbols, trading_calendar=tuple(first_qfq["date"].astype(str)),
        load_summary=base.load_summary, quality_issues=[], corporate_action_impacts=[],
    )


def _grid_plan(symbol: str) -> dict[str, object]:
    return {
        "symbol": symbol, "stock_mode": "range", "market_position_discount": 1.0,
        "target_position_pct": 0.15, "max_position_pct": 0.15,
        "grid_lower": 8.0, "grid_mid": 10.0, "grid_upper": 12.0,
        "grid_buy_levels": "9.5|9.0|8.5", "grid_sell_levels": "10.5|11.0|11.5",
        "configured_grid_layers": 3, "effective_grid_layers": 3,
        "grid_total_max_position_pct": 0.40, "data_sufficient": True,
    }


def _cross_symbol_evaluation(*args, **kwargs):
    del args
    day_two = kwargs.get("as_of") == "2026-01-02"
    second = {
        "symbol": SECOND_SYMBOL,
        "stock_mode": "trend" if day_two else "chaotic",
        "market_position_discount": 1.0,
        "target_position_pct": 0.20 if day_two else 0.0,
        "max_position_pct": 0.20 if day_two else 0.0,
        "trend_buy_trigger": 10.0 if day_two else "",
        "trend_reduce_trigger": 9.0 if day_two else "",
        "trend_exit_trigger": 8.0 if day_two else "",
        "effective_trend_exit_trigger": 8.0 if day_two else "",
        "atr20": 1.0 if day_two else "",
        "boll_upper": 9.8 if day_two else "",
        "volume_ma20": 1000.0 if day_two else "",
        "data_sufficient": True,
    }
    return SimpleNamespace(trigger_plan=pd.DataFrame([_grid_plan(SYMBOL), second]))


def _mark_revaluation_evaluation(*args, **kwargs):
    result = _cross_symbol_evaluation(*args, **kwargs)
    if kwargs.get("as_of") == "2026-01-02":
        result.trigger_plan.loc[
            result.trigger_plan["symbol"] == SYMBOL, "stock_mode"
        ] = "chaotic"
    return result


def test_request_rejects_unsupported_precision_liquidation_and_invalid_trend_cap() -> None:
    assert T1ThermostatBacktestRequest(
        service=object(), symbols=(SYMBOL,), start=DAY, end=DAY,
    ).force_final_liquidation is False
    with pytest.raises(ValueError, match="MINUTE_5M"):
        T1ThermostatBacktestRequest(
            service=object(), symbols=(SYMBOL,), start=DAY, end=DAY,
            precision=BacktestPrecision.MINUTE_5M,
        )
    with pytest.raises(ValueError, match="force_final_liquidation"):
        T1ThermostatBacktestRequest(
            service=object(), symbols=(SYMBOL,), start=DAY, end=DAY,
            force_final_liquidation=True,
        )
    with pytest.raises(ValueError, match="trend_total_base_max"):
        T1ThermostatBacktestRequest(
            service=object(), symbols=(SYMBOL,), start=DAY, end=DAY,
            trend_total_base_max=0.59,
        )


def test_runner_never_reads_legacy_advice_tables(monkeypatch) -> None:
    class PoisonedEvaluation:
        trigger_plan = pd.DataFrame()

        def __getattribute__(self, name):
            if name in {
                "_deprecated_signal_rows",
                "holding_advice",
                "new_candidates",
                "grid_advice",
                "trend_advice",
            }:
                raise AssertionError(f"legacy advice accessed: {name}")
            return object.__getattribute__(self, name)

    monkeypatch.setattr(
        "stock_picker.strategies.thermostat_backtest.load_t1_backtest_data",
        lambda service, request: _bundle(),
    )
    monkeypatch.setattr(
        "stock_picker.strategies.thermostat_backtest.evaluate_thermostat",
        lambda **kwargs: PoisonedEvaluation(),
    )

    result = run_t1_thermostat_backtest(
        T1ThermostatBacktestRequest(
            service=object(), symbols=(SYMBOL,), start=DAY, end=DAY,
        )
    )

    assert not result.summary.empty
    assert result.summary.loc[0, "final_asset"] == 100_000.0


def test_runner_uses_only_prior_qfq_for_plan_current_bfq_for_fill_and_keeps_final_position(monkeypatch) -> None:
    bundles = iter([_bundle(current_qfq_close=1.0), _bundle(current_qfq_close=999.0)])
    load_calls: list[object] = []

    def fake_load(service, request):
        del service
        load_calls.append(request)
        return next(bundles)

    monkeypatch.setattr(
        "stock_picker.strategies.thermostat_backtest.load_t1_backtest_data",
        fake_load,
    )
    request = T1ThermostatBacktestRequest(
        service=object(), symbols=(SYMBOL,), start=DAY, end=DAY,
        benchmark_symbol=BENCHMARK, initial_cash=100_000.0,
    )

    first = run_t1_thermostat_backtest(request)
    second = run_t1_thermostat_backtest(request)

    assert len(load_calls) == 2
    plan_columns = [
        "symbol", "date", "data_cutoff_date", "stock_mode",
        "trend_buy_trigger", "trend_reduce_trigger", "trend_exit_trigger",
        "boll_upper", "atr20", "volume_ma20",
    ]
    pd.testing.assert_frame_equal(
        first.daily_trigger_plans[plan_columns].reset_index(drop=True),
        second.daily_trigger_plans[plan_columns].reset_index(drop=True),
        check_dtype=False,
    )
    plan = first.daily_trigger_plans.iloc[0]
    assert plan["date"] == DAY
    assert plan["data_cutoff_date"] == "2025-12-31"
    assert plan["precision"] == BacktestPrecision.DAILY_APPROXIMATE.value
    assert plan["approximate_intraday_sequence"] in (True, False)
    assert "daily" in plan["precision_disclosure"]
    assert not first.fills.empty
    assert first.fills.iloc[0]["execution_price"] >= 35.5
    final_position = first.daily_positions.iloc[-1]
    assert final_position["total_shares"] > 0
    assert final_position["today_bought_shares"] > 0
    assert first.summary.iloc[0]["final_asset"] > 0
    for daily_table in (
        first.daily_assets, first.equity_drawdown,
        first.daily_positions, first.daily_trigger_plans,
    ):
        assert daily_table["precision_disclosure"].astype(str).str.len().gt(0).all()
        assert daily_table["approximate_intraday_sequence"].notna().all()
    for name, columns in RESULT_TABLE_COLUMNS.items():
        assert list(getattr(first, name).columns) == columns


def test_252_bar_warmup_blocks_buys_but_emits_plan_and_quality_record(monkeypatch) -> None:
    bundle = _bundle()
    short_qfq = bundle.symbols[SYMBOL].indicator_frame.tail(100).reset_index(drop=True)
    bundle.symbols[SYMBOL] = replace(
        bundle.symbols[SYMBOL], indicator_frame=short_qfq,
        available_warmup_count=99, buy_eligible=False,
    )
    monkeypatch.setattr(
        "stock_picker.strategies.thermostat_backtest.load_t1_backtest_data",
        lambda service, request: bundle,
    )

    result = run_t1_thermostat_backtest(T1ThermostatBacktestRequest(
        service=object(), symbols=(SYMBOL,), start=DAY, end=DAY,
        benchmark_symbol=BENCHMARK,
    ))

    assert result.daily_trigger_plans.iloc[0]["stock_mode"] == "insufficient_data"
    assert result.fills.empty
    assert (
        (result.data_quality["symbol"] == SYMBOL)
        & (result.data_quality["code"] == "insufficient_data")
    ).any()


def test_data_cutoff_is_latest_actual_prior_qfq_date_per_symbol(monkeypatch) -> None:
    bundle = _bundle()
    first = bundle.symbols[SYMBOL]
    second_qfq = first.indicator_frame[
        first.indicator_frame["date"] != "2025-12-31"
    ].reset_index(drop=True)
    bundle.symbols[SECOND_SYMBOL] = SymbolBacktestData(
        SECOND_SYMBOL, second_qfq, first.execution_frame.copy(),
        251, buy_eligible=False,
    )
    monkeypatch.setattr(
        "stock_picker.strategies.thermostat_backtest.load_t1_backtest_data",
        lambda service, request: bundle,
    )

    result = run_t1_thermostat_backtest(T1ThermostatBacktestRequest(
        service=object(), symbols=(SYMBOL, SECOND_SYMBOL), start=DAY, end=DAY,
        benchmark_symbol=BENCHMARK,
    ))

    cutoffs = result.daily_trigger_plans.set_index("symbol")["data_cutoff_date"]
    assert cutoffs[SYMBOL] == "2025-12-31"
    assert cutoffs[SECOND_SYMBOL] == "2025-12-30"


def test_three_trading_days_fill_three_t1_trend_batches_without_final_liquidation(monkeypatch) -> None:
    bundle = _three_day_bundle()
    monkeypatch.setattr(
        "stock_picker.strategies.thermostat_backtest.load_t1_backtest_data",
        lambda service, request: bundle,
    )

    result = run_t1_thermostat_backtest(T1ThermostatBacktestRequest(
        service=object(), symbols=(SYMBOL,), start="2026-01-02", end="2026-01-06",
        benchmark_symbol=BENCHMARK, initial_cash=100_000.0,
    ))

    trend_buys = result.fills[
        (result.fills["family"] == "trend") & (result.fills["side"] == "buy")
    ]
    assert trend_buys["trend_batch"].tolist() == [1, 2, 3]
    assert trend_buys["trade_date"].tolist() == ["2026-01-02", "2026-01-05", "2026-01-06"]
    final = result.daily_positions.iloc[-1]
    assert final["total_shares"] == trend_buys["actual_shares"].sum()
    assert final["available_shares"] == trend_buys.iloc[:2]["actual_shares"].sum()
    assert final["today_bought_shares"] == trend_buys.iloc[2]["actual_shares"]
    assert result.closed_trade_cycles.empty


def test_multi_day_pending_retries_at_open_and_remains_traceable_until_fifo_close(monkeypatch) -> None:
    bundle = _pending_retry_bundle()
    monkeypatch.setattr(
        "stock_picker.strategies.thermostat_backtest.load_t1_backtest_data",
        lambda service, request: bundle,
    )

    result = run_t1_thermostat_backtest(T1ThermostatBacktestRequest(
        service=object(), symbols=(SYMBOL,), start="2026-01-02", end="2026-01-06",
        benchmark_symbol=BENCHMARK, initial_cash=100_000.0,
    ))

    retry = result.lifecycle_orders[
        (result.lifecycle_orders["trade_date"] == "2026-01-05")
        & (result.lifecycle_orders["trigger_type"] == "pending_sell")
    ].iloc[0]
    assert retry["status"] == "pending_retry"
    assert retry["failure_reason"] == "open_at_limit_down"
    assert retry["plan_trace_id"]
    final_pending_fill = result.fills[
        (result.fills["trade_date"] == "2026-01-06")
        & (result.fills["trigger_type"] == "pending_sell")
    ]
    assert len(final_pending_fill) == 1
    day_two_pending = result.pending_history[
        result.pending_history["date"] == "2026-01-05"
    ].iloc[0]
    assert day_two_pending["attempt_count"] == 1
    assert day_two_pending["last_failure"] == "open_at_limit_down"
    assert day_two_pending["source_order_id"]
    assert day_two_pending["plan_trace_id"]
    terminal = result.pending_history[
        (result.pending_history["date"] == "2026-01-06")
        & result.pending_history["is_terminal"]
    ].iloc[0]
    assert terminal["event_type"] == "filled"
    assert terminal["remaining_shares"] == 0
    assert terminal["duration_days"] == 2
    assert result.pending_history["episode_id"].nunique() == 1
    assert result.summary.iloc[0]["pending_average_duration_days"] == pytest.approx(2.0)
    assert len(result.closed_trade_cycles) == 1
    final_position = result.daily_positions.iloc[-1]
    final_assets = result.daily_assets.iloc[-1]
    cycle_net_pnl = result.closed_trade_cycles["net_pnl"].sum()
    assert final_position["total_shares"] == 0
    assert final_assets["realized_pnl"] == pytest.approx(cycle_net_pnl)
    assert final_assets["realized_pnl"] == pytest.approx(final_assets["cash"] - 100_000.0)
    first_plan = result.daily_trigger_plans.iloc[0]
    assert bool(first_plan["approximate_intraday_sequence"]) is True
    assert "approximate_intraday_sequence" in first_plan["approximation_warnings"]


def test_pending_is_audited_on_union_date_when_symbol_has_no_bfq_bar(monkeypatch) -> None:
    bundle = _pending_missing_bar_bundle()
    from stock_picker.strategies import thermostat_backtest as backtest_module

    prepared_marks: list[tuple[str, str, dict[str, float]]] = []
    original_prepare_trend_day = backtest_module.prepare_trend_day

    def capture_prepare_marks(plan, bar, ledger, settings, trade_date, portfolio_marks):
        prepared_marks.append((trade_date.isoformat(), plan["symbol"], dict(portfolio_marks)))
        return original_prepare_trend_day(
            plan, bar, ledger, settings, trade_date, portfolio_marks,
        )

    monkeypatch.setattr(backtest_module, "prepare_trend_day", capture_prepare_marks)
    monkeypatch.setattr(
        "stock_picker.strategies.thermostat_backtest.load_t1_backtest_data",
        lambda service, request: bundle,
    )

    result = run_t1_thermostat_backtest(T1ThermostatBacktestRequest(
        service=object(), symbols=(SYMBOL, SECOND_SYMBOL),
        start="2026-01-02", end="2026-01-06", benchmark_symbol=BENCHMARK,
        initial_cash=100_000.0,
    ))

    missing_retry = result.lifecycle_orders[
        (result.lifecycle_orders["trade_date"] == "2026-01-05")
        & (result.lifecycle_orders["symbol"] == SYMBOL)
        & (result.lifecycle_orders["trigger_type"] == "pending_sell")
    ].iloc[0]
    assert missing_retry["status"] == "pending_retry"
    assert missing_retry["failure_reason"] == "no_valid_price"
    assert missing_retry["plan_trace_id"]
    pending = result.pending_history[
        (result.pending_history["date"] == "2026-01-05")
        & (result.pending_history["symbol"] == SYMBOL)
    ].iloc[0]
    assert pending["attempt_count"] == 1
    assert pending["last_failure"] == "no_valid_price"
    held = result.daily_positions[result.daily_positions["symbol"] == SYMBOL].set_index("date")
    assets = result.daily_assets.set_index("date")
    assert held.loc["2026-01-05", "close"] == pytest.approx(
        held.loc["2026-01-02", "close"]
    )
    assert assets.loc["2026-01-05", "total_asset"] == pytest.approx(
        assets.loc["2026-01-02", "total_asset"]
    )
    stale = result.data_quality[
        (result.data_quality["date"] == "2026-01-05")
        & (result.data_quality["symbol"] == SYMBOL)
        & (result.data_quality["code"] == "stale_valuation_mark")
    ]
    assert len(stale) == 1
    day_two_marks = next(
        marks for value, symbol, marks in prepared_marks
        if value == "2026-01-05" and symbol == SECOND_SYMBOL
    )
    assert day_two_marks[SYMBOL] == pytest.approx(34.5)


def test_grid_layers_are_independent_and_same_day_sell_wins_over_reachable_buy(monkeypatch) -> None:
    bundle = _grid_bundle()
    monkeypatch.setattr(
        "stock_picker.strategies.thermostat_backtest.load_t1_backtest_data",
        lambda service, request: bundle,
    )
    monkeypatch.setattr(
        "stock_picker.strategies.thermostat_backtest.evaluate_thermostat",
        _fixed_grid_evaluation,
    )

    result = run_t1_thermostat_backtest(T1ThermostatBacktestRequest(
        service=object(), symbols=(SYMBOL,), start="2026-01-02", end="2026-01-06",
        benchmark_symbol=BENCHMARK, initial_cash=100_000.0,
        execution_settings=None,
    ))

    grid_fills = result.fills[result.fills["family"] == "grid"]
    assert grid_fills[["trade_date", "side", "grid_layer"]].values.tolist() == [
        ["2026-01-02", "buy", "grid-1"],
        ["2026-01-05", "buy", "grid-2"],
        ["2026-01-06", "sell", "grid-1"],
    ]
    assert not (
        (grid_fills["trade_date"] == "2026-01-06")
        & (grid_fills["side"] == "buy")
    ).any()
    final_layers = result.grid_layers[result.grid_layers["date"] == "2026-01-06"]
    assert final_layers.set_index("layer_id").loc["grid-2", "held_shares"] > 0
    assert final_layers.set_index("layer_id").loc["grid-3", "held_shares"] == 0
    assert len(result.closed_trade_cycles) == 1
    assert result.closed_trade_cycles.iloc[0]["owner_id"] == "grid-1"


def test_multi_symbol_global_sell_precedes_buy_and_buy_revalidates_post_sell_cap(monkeypatch) -> None:
    bundle = _cross_symbol_bundle()
    monkeypatch.setattr(
        "stock_picker.strategies.thermostat_backtest.load_t1_backtest_data",
        lambda service, request: bundle,
    )
    monkeypatch.setattr(
        "stock_picker.strategies.thermostat_backtest.evaluate_thermostat",
        _cross_symbol_evaluation,
    )
    settings = __import__(
        "stock_picker.strategies.thermostat_execution", fromlist=["T1ExecutionSettings"]
    ).T1ExecutionSettings(account_total_max=0.081)

    result = run_t1_thermostat_backtest(T1ThermostatBacktestRequest(
        service=object(), symbols=(SECOND_SYMBOL, SYMBOL),
        start="2026-01-02", end="2026-01-05", benchmark_symbol=BENCHMARK,
        initial_cash=100_000.0, execution_settings=settings,
    ))

    day_two = result.fills[result.fills["trade_date"] == "2026-01-05"]
    assert day_two[["symbol", "side", "trigger_type"]].values.tolist() == [
        [SYMBOL, "sell", "grid_sell"],
        [SECOND_SYMBOL, "buy", "trend_buy"],
    ]
    sell, buy = day_two.iloc[0], day_two.iloc[1]
    assert buy["cash_before"] == pytest.approx(sell["cash_after"])
    assert buy["status"] == "filled"
    assert not (
        (result.lifecycle_orders["trade_date"] == "2026-01-05")
        & (result.lifecycle_orders["symbol"] == SECOND_SYMBOL)
        & (result.lifecycle_orders["failure_reason"] == "account_total_cap_exceeded")
    ).any()


def test_buy_cap_revalidation_uses_current_bfq_marks_for_every_held_symbol(monkeypatch) -> None:
    bundle = _cross_symbol_bundle()
    first = bundle.symbols[SYMBOL]
    bfq = first.execution_frame.copy()
    bfq.loc[1, ["open", "high", "low", "close"]] = [19.5, 20.5, 19.0, 20.0]
    bundle.symbols[SYMBOL] = replace(first, execution_frame=bfq)
    monkeypatch.setattr(
        "stock_picker.strategies.thermostat_backtest.load_t1_backtest_data",
        lambda service, request: bundle,
    )
    monkeypatch.setattr(
        "stock_picker.strategies.thermostat_backtest.evaluate_thermostat",
        _mark_revaluation_evaluation,
    )
    settings = __import__(
        "stock_picker.strategies.thermostat_execution", fromlist=["T1ExecutionSettings"]
    ).T1ExecutionSettings(account_total_max=0.15)

    result = run_t1_thermostat_backtest(T1ThermostatBacktestRequest(
        service=object(), symbols=(SYMBOL, SECOND_SYMBOL),
        start="2026-01-02", end="2026-01-05", benchmark_symbol=BENCHMARK,
        initial_cash=100_000.0, execution_settings=settings,
    ))

    rejected = result.lifecycle_orders[
        (result.lifecycle_orders["trade_date"] == "2026-01-05")
        & (result.lifecycle_orders["symbol"] == SECOND_SYMBOL)
        & (result.lifecycle_orders["trigger_type"] == "trend_buy")
    ].iloc[0]
    assert rejected["status"] == "failed"
    assert rejected["failure_reason"] == "account_total_cap_exceeded"
    assert not (
        (result.fills["trade_date"] == "2026-01-05")
        & (result.fills["symbol"] == SECOND_SYMBOL)
    ).any()


def test_no_execution_dates_returns_every_result_table_with_stable_columns(monkeypatch) -> None:
    empty = pd.DataFrame()
    bundle = BacktestDataBundle(
        request=BacktestDataRequest(symbols=(SYMBOL, BENCHMARK), start=DAY, end=DAY),
        symbols={
            SYMBOL: SymbolBacktestData(SYMBOL, empty, empty, 0, buy_eligible=False),
            BENCHMARK: SymbolBacktestData(BENCHMARK, empty, empty, 0, buy_eligible=False),
        },
        trading_calendar=(), load_summary={},
        quality_issues=[{"code": "missing_trade_calendar"}],
        corporate_action_impacts=[],
    )
    monkeypatch.setattr(
        "stock_picker.strategies.thermostat_backtest.load_t1_backtest_data",
        lambda service, request: bundle,
    )

    result = run_t1_thermostat_backtest(T1ThermostatBacktestRequest(
        service=object(), symbols=(SYMBOL,), start=DAY, end=DAY,
        benchmark_symbol=BENCHMARK,
    ))

    for name, columns in RESULT_TABLE_COLUMNS.items():
        assert list(getattr(result, name).columns) == columns
    assert result.summary.iloc[0]["initial_asset"] == pytest.approx(100_000.0)
    assert result.summary.iloc[0]["final_asset"] == pytest.approx(100_000.0)
    assert result.daily_assets.empty
    assert result.daily_trigger_plans.empty


def test_missing_data_ratio_uses_symbol_date_observations_not_warning_rows(monkeypatch) -> None:
    bundle = _bundle()
    first = bundle.symbols[SYMBOL]
    bundle.symbols[SECOND_SYMBOL] = SymbolBacktestData(
        SECOND_SYMBOL, first.indicator_frame.copy(), pd.DataFrame(),
        252, buy_eligible=False,
    )
    bundle.quality_issues.extend(
        {"code": f"unrelated_warning_{index}", "symbol": SYMBOL}
        for index in range(7)
    )
    monkeypatch.setattr(
        "stock_picker.strategies.thermostat_backtest.load_t1_backtest_data",
        lambda service, request: bundle,
    )

    result = run_t1_thermostat_backtest(T1ThermostatBacktestRequest(
        service=object(), symbols=(SYMBOL, SECOND_SYMBOL), start=DAY, end=DAY,
        benchmark_symbol=BENCHMARK,
    ))

    assert result.summary.iloc[0]["missing_data_ratio"] == pytest.approx(0.5)
    observations = result.data_quality[result.data_quality["observation_expected"]]
    assert len(observations) == 2
    assert observations["observation_missing"].sum() == 1
