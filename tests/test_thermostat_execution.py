from datetime import date

import pytest

from stock_picker.strategies.thermostat_execution import (
    BacktestOrder,
    DailyBar,
    ExecutionCandidate,
    ExecutionPhase,
    OrderStatus,
    PortfolioLedger,
    T1ExecutionSettings,
    buy_fees,
    conservative_base_price,
    conservative_execution_price,
    execute_buy,
    execute_sell,
    is_one_word_limit,
    is_pending_open_limit_down,
    process_pending_sells,
    round_buy_shares,
    sell_fees,
    stable_sort_candidates,
    sort_execution_events,
)
from stock_picker.strategies.thermostat_state import (
    PendingSellLevel,
    ThermostatPositionState,
)


DAY = date(2026, 7, 10)


def bar(**overrides: object) -> DailyBar:
    values = {
        "date": DAY,
        "open": 10.0,
        "high": 10.5,
        "low": 9.5,
        "close": 10.2,
        "volume": 1000.0,
        "previous_close": 9.8,
        "limit_up_price": 10.78,
        "limit_down_price": 8.82,
        "suspended": False,
    }
    values.update(overrides)
    return DailyBar(**values)


def test_settings_and_order_status_expose_stable_execution_contract() -> None:
    settings = T1ExecutionSettings()

    assert settings.buy_lot_size == 100
    assert settings.trend_symbol_base_max == pytest.approx(0.20)
    assert settings.trend_total_base_max == pytest.approx(0.65)
    assert settings.grid_symbol_base_max == pytest.approx(0.15)
    assert settings.grid_total_hard_max == pytest.approx(0.40)
    assert settings.account_total_max == pytest.approx(0.95)
    assert settings.force_final_liquidation is False
    assert {status.value for status in OrderStatus} == {
        "plan_created", "order_created", "triggered", "filled", "failed",
        "cancelled", "pending", "pending_retry", "expired",
    }


def test_execution_settings_rejects_forced_final_liquidation() -> None:
    with pytest.raises(ValueError, match="force_final_liquidation"):
        T1ExecutionSettings(force_final_liquidation=True)


def test_daily_bar_rejects_invalid_prices_and_volume() -> None:
    with pytest.raises(ValueError, match="high"):
        bar(high=float("nan"))
    with pytest.raises(ValueError, match="open"):
        bar(open=0.0)
    with pytest.raises(ValueError, match="volume"):
        bar(volume=-1.0)


def test_lot_rounding_and_fees_include_minimum_commission_and_sell_tax() -> None:
    settings = T1ExecutionSettings(
        commission_rate=0.0003,
        minimum_commission=5.0,
        stamp_tax_rate=0.001,
    )

    assert round_buy_shares(199, settings.buy_lot_size) == 100
    assert round_buy_shares(99, settings.buy_lot_size) == 0
    assert buy_fees(1000.0, settings) == pytest.approx(5.0)
    assert sell_fees(1000.0, settings) == pytest.approx(6.0)


def test_conservative_prices_cover_each_execution_family_and_slippage_direction() -> None:
    current = bar(close=10.2)

    assert conservative_base_price("pending_sell", 9.0, current) == pytest.approx(10.0)
    assert conservative_base_price("trend_buy", 10.0, current) == pytest.approx(10.2)
    assert conservative_base_price("trend_add", 10.5, current) == pytest.approx(10.5)
    assert conservative_base_price("trend_reduce", 10.0, current) == pytest.approx(10.0)
    assert conservative_base_price("trend_exit", 10.5, current) == pytest.approx(10.2)
    assert conservative_base_price("grid_buy", 9.4, current) == pytest.approx(9.4)
    assert conservative_base_price("grid_sell", 10.6, current) == pytest.approx(10.6)
    assert conservative_base_price("risk_control_sell", 9.0, current) == pytest.approx(10.2)
    assert conservative_base_price("trend_buy", 10.0, bar(close=None)) == pytest.approx(10.0)
    assert conservative_execution_price("buy", 10.0, 0.001) == pytest.approx(10.01)
    assert conservative_execution_price("sell", 10.0, 0.001) == pytest.approx(9.99)


def test_event_sort_is_stable_and_uses_priority_risk_plan_symbol_then_owner_id() -> None:
    events = [
        BacktestOrder("7", DAY, "600003.SH", "range", "grid", "grid_buy", side="buy"),
        BacktestOrder("6", DAY, "600002.SH", "trend", "trend", "trend_buy", side="buy", trend_batch=2),
        BacktestOrder("5", DAY, "600001.SH", "range", "grid", "grid_sell", side="sell", grid_layer="g2"),
        BacktestOrder("4", DAY, "600001.SH", "trend", "trend", "trend_reduce", side="sell"),
        BacktestOrder("3", DAY, "600001.SH", "trend", "trend", "trend_exit", side="sell"),
        BacktestOrder("2b", DAY, "600002.SH", "downtrend", "trend", "risk_control_sell", side="sell", risk_rank=2, plan_priority=1),
        BacktestOrder("2a", DAY, "600001.SH", "downtrend", "trend", "risk_control_sell", side="sell", risk_rank=3, plan_priority=9),
        BacktestOrder("1", DAY, "600001.SH", "trend", "trend", "pending_sell", side="sell"),
    ]

    assert [event.order_id for event in sort_execution_events(events)] == [
        "1", "2a", "2b", "3", "4", "5", "6", "7",
    ]


def test_execution_candidates_are_immutable_and_sort_by_global_phase_contract() -> None:
    candidates = [
        ExecutionCandidate(
            candidate_id="buy-b", trade_date=DAY, symbol="600002", mode="trend",
            family="trend", phase=ExecutionPhase.TREND_BUY,
            trigger_type="trend_buy", trigger_price=10.0, side="buy",
            owner_id="2", trend_batch=2, risk_rank=0, plan_priority=1,
        ),
        ExecutionCandidate(
            candidate_id="sell-b", trade_date=DAY, symbol="600002", mode="range",
            family="grid", phase=ExecutionPhase.GRID_SELL,
            trigger_type="grid_sell", trigger_price=11.0, side="sell",
            owner_id="grid-2", grid_layer="grid-2", risk_rank=1, plan_priority=2,
        ),
        ExecutionCandidate(
            candidate_id="sell-a", trade_date=DAY, symbol="600001", mode="range",
            family="grid", phase=ExecutionPhase.GRID_SELL,
            trigger_type="grid_sell", trigger_price=10.5, side="sell",
            owner_id="grid-1", grid_layer="grid-1", risk_rank=2, plan_priority=3,
        ),
    ]

    assert [item.candidate_id for item in sorted(candidates)] == [
        "sell-a", "sell-b", "buy-b",
    ]
    with pytest.raises((AttributeError, TypeError)):
        candidates[0].symbol = "600003.SH"  # type: ignore[misc]


def test_equal_key_candidate_sort_preserves_input_order() -> None:
    candidates = [
        ExecutionCandidate(
            candidate_id=candidate_id, trade_date=DAY, symbol="600001.SH",
            mode="trend", family="trend", phase=ExecutionPhase.TREND_BUY,
            trigger_type="trend_buy", trigger_price=10.0, side="buy",
            owner_id="0000000001", trend_batch=1,
        )
        for candidate_id in ("z-last-lexically", "a-first-lexically")
    ]

    assert [item.candidate_id for item in sorted(candidates)] == [
        "z-last-lexically", "a-first-lexically",
    ]


def test_stable_sort_candidates_uses_reviewed_key_and_preserves_equal_input_order() -> None:
    equal_first = ExecutionCandidate(
        candidate_id="first", trade_date=DAY, symbol="600001", mode="range",
        family="grid", phase=ExecutionPhase.GRID_SELL,
        trigger_type="grid_sell", trigger_price=11.0, side="sell",
        owner_id="g1", risk_rank=5, plan_priority=2,
    )
    earlier_phase = ExecutionCandidate(
        candidate_id="earlier", trade_date=DAY, symbol="600002", mode="trend",
        family="trend", phase=ExecutionPhase.TREND_EXIT,
        trigger_type="trend_exit", trigger_price=9.0, side="sell",
    )
    equal_second = ExecutionCandidate(
        candidate_id="second", trade_date=DAY, symbol="600001", mode="range",
        family="grid", phase=ExecutionPhase.GRID_SELL,
        trigger_type="grid_sell", trigger_price=12.0, side="sell",
        owner_id="g1", risk_rank=5, plan_priority=2,
    )

    ordered = stable_sort_candidates([equal_first, earlier_phase, equal_second])

    assert [item.candidate_id for item in ordered] == ["earlier", "first", "second"]


def test_execution_candidate_recursively_freezes_nested_metadata() -> None:
    source = {
        "nested": {"levels": [1, {"owners": {"b", "a"}}]},
    }
    candidate = ExecutionCandidate(
        candidate_id="frozen", trade_date=DAY, symbol="600001.SH",
        mode="trend", family="trend", phase=ExecutionPhase.TREND_BUY,
        trigger_type="trend_buy", trigger_price=10.0, side="buy",
        metadata=source,  # type: ignore[arg-type]
    )
    frozen = candidate.metadata

    source["nested"]["levels"].append(2)  # type: ignore[index,union-attr]

    assert candidate.metadata == frozen
    assert isinstance(candidate.metadata, tuple)
    assert isinstance(candidate.metadata[0][1], tuple)


def test_one_word_limit_and_pending_open_limit_down_are_exact() -> None:
    assert is_one_word_limit(bar(open=10.78, high=10.78, low=10.78, close=10.78), "up")
    assert not is_one_word_limit(bar(open=10.78, high=10.78, low=10.0, close=10.5), "up")
    assert is_pending_open_limit_down(bar(open=8.82))
    assert not is_pending_open_limit_down(bar(open=8.83))


def test_buy_rounds_to_lot_includes_fees_and_records_auditable_failure_below_one_lot() -> None:
    settings = T1ExecutionSettings(commission_rate=0.0003, minimum_commission=5.0)
    ledger = PortfolioLedger(cash=1000.0, initial_capital=1000.0)

    order = execute_buy(
        ledger, settings, bar(open=10.0, high=10.0, low=9.5, close=10.0),
        symbol="600001.SH", mode="trend", family="trend", trigger_type="trend_buy",
        trigger_price=10.0, intended_shares=100, trade_date=DAY, trend_batch=1,
    )

    assert order.status is OrderStatus.FAILED
    assert order.failure_reason == "below_one_lot"
    assert order.intended_shares == 100 and order.actual_shares == 0
    assert ledger.cash == pytest.approx(1000.0)
    assert ledger.orders == [order]


def test_sequential_buys_recheck_symbol_and_total_caps_after_each_fill() -> None:
    settings = T1ExecutionSettings(
        commission_rate=0.0,
        minimum_commission=0.0,
        slippage_pct=0.0,
        trend_symbol_base_max=0.20,
        trend_total_base_max=0.30,
        account_total_max=0.95,
    )
    ledger = PortfolioLedger(cash=10000.0, initial_capital=10000.0)
    current = bar(open=10.0, close=10.0)

    first = execute_buy(
        ledger, settings, current, symbol="600001.SH", mode="trend", family="trend",
        trigger_type="trend_buy", trigger_price=10.0, intended_shares=200,
        trade_date=DAY, trend_batch=1,
    )
    second = execute_buy(
        ledger, settings, current, symbol="600001.SH", mode="trend", family="trend",
        trigger_type="trend_add", trigger_price=10.0, intended_shares=100,
        trade_date=DAY, trend_batch=2,
    )
    third = execute_buy(
        ledger, settings, current, symbol="600002.SH", mode="trend", family="trend",
        trigger_type="trend_buy", trigger_price=10.0, intended_shares=200,
        trade_date=DAY, trend_batch=1,
    )

    assert first.status is OrderStatus.FILLED
    assert second.status is OrderStatus.FAILED and second.failure_reason == "symbol_cap_exceeded"
    assert third.status is OrderStatus.FAILED and third.failure_reason == "trend_total_cap_exceeded"
    assert len(ledger.orders) == 3 and ledger.fills == [first]


def test_buy_rejects_suspension_and_one_word_limit_up_but_warns_on_ambiguous_limit() -> None:
    settings = T1ExecutionSettings(commission_rate=0.0, minimum_commission=0.0)
    ledger = PortfolioLedger(cash=10000.0, initial_capital=10000.0)
    common = dict(
        ledger=ledger, settings=settings, symbol="600001.SH", mode="trend",
        family="trend", trigger_type="trend_buy", trigger_price=10.0,
        intended_shares=100, trade_date=DAY, trend_batch=1,
    )

    suspended = execute_buy(bar=bar(suspended=True), **common)
    one_word = execute_buy(
        bar=bar(open=10.78, high=10.78, low=10.78, close=10.78), **common,
    )
    ambiguous = execute_buy(
        bar=bar(open=10.2, high=10.78, low=10.0, close=10.2), **common,
    )

    assert (suspended.status, suspended.failure_reason) == (OrderStatus.FAILED, "suspended")
    assert (one_word.status, one_word.failure_reason) == (OrderStatus.FAILED, "one_word_limit_up")
    assert ambiguous.status is OrderStatus.FILLED
    assert "limit_up_intraday_sequence_ambiguous" in ambiguous.quality_warning


def test_invalid_buy_owner_fails_without_mutating_cash_or_position() -> None:
    settings = T1ExecutionSettings(
        commission_rate=0.0, minimum_commission=0.0, slippage_pct=0.0,
    )
    ledger = PortfolioLedger(cash=10000.0, initial_capital=10000.0)

    order = execute_buy(
        ledger, settings, bar(close=10.0), symbol="600001.SH", mode="range",
        family="grid", trigger_type="grid_buy", trigger_price=10.0,
        intended_shares=100, trade_date=DAY,
    )

    assert order.status is OrderStatus.FAILED
    assert order.failure_reason == "missing_grid_layer"
    assert ledger.cash == pytest.approx(10000.0)
    assert ledger.positions == {}


def test_buy_state_rejection_is_atomic_and_audited() -> None:
    settings = T1ExecutionSettings(
        commission_rate=0.0, minimum_commission=0.0, slippage_pct=0.0,
    )
    state = ThermostatPositionState(symbol="600001.SH", current_mode="trend", blocked_new_buy=False)
    state.total_shares = 1
    ledger = PortfolioLedger(cash=10000.0, initial_capital=10000.0, positions={state.symbol: state})

    order = execute_buy(
        ledger, settings, bar(close=10.0), symbol=state.symbol, mode="trend",
        family="trend", trigger_type="trend_buy", trigger_price=10.0,
        intended_shares=100, trade_date=DAY, trend_batch=1,
    )

    assert order.status is OrderStatus.FAILED
    assert order.failure_reason == "invalid_position_state"
    assert ledger.cash == pytest.approx(10000.0)
    assert state.total_shares == 1 and state.trend_shares == 0
    assert ledger.orders[-1] is order


def test_ordinary_trend_sell_fills_available_and_queues_t1_remainder() -> None:
    settings = T1ExecutionSettings(
        commission_rate=0.0, minimum_commission=0.0, stamp_tax_rate=0.0,
        slippage_pct=0.0,
    )
    state = ThermostatPositionState(symbol="600001.SH", current_mode="trend")
    state.record_trend_buy(1, 100, 10.0, date(2026, 7, 8))
    state.start_trading_day(DAY)
    state.record_trend_buy(2, 100, 10.0, DAY)
    ledger = PortfolioLedger(cash=0.0, initial_capital=2000.0, positions={state.symbol: state})

    order = execute_sell(
        ledger, settings, bar(close=9.8), symbol=state.symbol, mode="trend",
        family="trend", trigger_type="trend_reduce", trigger_price=10.0,
        intended_shares=200, trade_date=DAY,
    )

    assert order.status is OrderStatus.PENDING
    assert order.actual_shares == 100
    assert order.pending_level is PendingSellLevel.PENDING_REDUCE
    assert state.total_shares == 100 and state.today_bought_shares == 100
    assert state.pending_sell is not None
    assert state.pending_sell.remaining_shares == 100
    assert ledger.orders == [order] and ledger.fills == [order]


def test_ordinary_grid_sell_uses_only_matching_layer_and_queues_its_locked_shares() -> None:
    settings = T1ExecutionSettings(
        commission_rate=0.0, minimum_commission=0.0, stamp_tax_rate=0.0,
        slippage_pct=0.0,
    )
    state = ThermostatPositionState(symbol="600001.SH", current_mode="range")
    state.record_grid_buy("g1", 100, 9.0, date(2026, 7, 8))
    state.record_grid_buy("g2", 100, 8.0, date(2026, 7, 8))
    state.start_trading_day(DAY)
    state.record_grid_buy("g1", 100, 9.0, DAY)
    ledger = PortfolioLedger(cash=0.0, initial_capital=3000.0, positions={state.symbol: state})

    order = execute_sell(
        ledger, settings, bar(), symbol=state.symbol, mode="range", family="grid",
        trigger_type="grid_sell", trigger_price=10.0, intended_shares=200,
        trade_date=DAY, grid_layer="g1",
    )

    assert order.actual_shares == 100 and order.status is OrderStatus.PENDING
    assert state.grid_layers["g1"].held_shares == 100
    assert state.grid_layers["g2"].held_shares == 100
    assert state.pending_sell is not None and state.pending_sell.grid_layer_id == "g1"


def test_risk_sell_with_only_locked_shares_is_pending_emergency_exit() -> None:
    settings = T1ExecutionSettings()
    state = ThermostatPositionState(symbol="600001.SH", current_mode="downtrend")
    state.record_trend_buy(1, 100, 10.0, DAY)
    ledger = PortfolioLedger(cash=1000.0, initial_capital=2000.0, positions={state.symbol: state})

    order = execute_sell(
        ledger, settings, bar(), symbol=state.symbol, mode="downtrend", family="trend",
        trigger_type="risk_control_sell", trigger_price=9.0, intended_shares=100,
        trade_date=DAY,
    )

    assert order.status is OrderStatus.PENDING and order.actual_shares == 0
    assert order.pending_level is PendingSellLevel.PENDING_EMERGENCY_EXIT
    assert state.pending_sell is not None


def test_execution_prices_are_clamped_to_daily_limits() -> None:
    buy_settings = T1ExecutionSettings(
        commission_rate=0.0, minimum_commission=0.0, slippage_pct=0.05,
        trend_symbol_base_max=1.0, trend_total_base_max=1.0, account_total_max=1.0,
    )
    buy_ledger = PortfolioLedger(cash=10000.0, initial_capital=10000.0)
    buy = execute_buy(
        buy_ledger, buy_settings, bar(close=10.5), symbol="600001.SH", mode="trend",
        family="trend", trigger_type="trend_buy", trigger_price=10.5,
        intended_shares=100, trade_date=DAY, trend_batch=1,
    )
    assert buy.execution_price == pytest.approx(10.78)

    sell_settings = T1ExecutionSettings(
        commission_rate=0.0, minimum_commission=0.0, stamp_tax_rate=0.0,
        slippage_pct=0.20,
    )
    state = ThermostatPositionState(symbol="600002.SH", current_mode="trend")
    state.record_trend_buy(1, 100, 10.0, date(2026, 7, 8))
    state.start_trading_day(DAY)
    sell_ledger = PortfolioLedger(cash=0.0, initial_capital=1000.0, positions={state.symbol: state})
    sell = execute_sell(
        sell_ledger, sell_settings, bar(close=10.0), symbol=state.symbol, mode="trend",
        family="trend", trigger_type="trend_exit", trigger_price=10.0,
        intended_shares=100, trade_date=DAY,
    )
    assert sell.execution_price == pytest.approx(8.82)


def test_buy_and_sell_date_mismatch_are_audited_without_mutation() -> None:
    settings = T1ExecutionSettings(commission_rate=0.0, minimum_commission=0.0)
    state = ThermostatPositionState(symbol="600001.SH", current_mode="trend")
    state.record_trend_buy(1, 100, 10.0, date(2026, 7, 8))
    state.start_trading_day(DAY)
    ledger = PortfolioLedger(cash=10000.0, initial_capital=11000.0, positions={state.symbol: state})
    wrong_day = date(2026, 7, 11)

    buy = execute_buy(
        ledger, settings, bar(), symbol=state.symbol, mode="trend", family="trend",
        trigger_type="trend_buy", trigger_price=10.0, intended_shares=100,
        trade_date=wrong_day, trend_batch=2,
    )
    sell = execute_sell(
        ledger, settings, bar(), symbol=state.symbol, mode="trend", family="trend",
        trigger_type="trend_exit", trigger_price=10.0, intended_shares=100,
        trade_date=wrong_day,
    )

    assert buy.failure_reason == sell.failure_reason == "trade_date_bar_date_mismatch"
    assert state.total_shares == 100 and ledger.cash == pytest.approx(10000.0)


def test_date_mismatched_buy_does_not_create_an_empty_position() -> None:
    ledger = PortfolioLedger(cash=10000.0, initial_capital=10000.0)

    order = execute_buy(
        ledger, T1ExecutionSettings(), bar(), symbol="600003.SH", mode="trend",
        family="trend", trigger_type="trend_buy", trigger_price=10.0,
        intended_shares=100, trade_date=date(2026, 7, 11), trend_batch=1,
    )

    assert order.status is OrderStatus.FAILED
    assert ledger.positions == {}


def test_pending_attempts_once_per_date_fail_at_limit_down_then_partially_sell_available() -> None:
    settings = T1ExecutionSettings(
        commission_rate=0.0, minimum_commission=0.0, stamp_tax_rate=0.0,
        slippage_pct=0.0,
    )
    state = ThermostatPositionState(symbol="600001.SH", current_mode="trend")
    state.record_trend_buy(1, 100, 10.0, date(2026, 7, 8))
    state.record_trend_buy(2, 100, 10.0, DAY)
    state.start_trading_day(DAY)
    state.queue_pending(PendingSellLevel.PENDING_EXIT, 200, "trend", DAY)
    ledger = PortfolioLedger(cash=0.0, initial_capital=2000.0, positions={state.symbol: state})

    failed = process_pending_sells(ledger, settings, bar(open=8.82), state, DAY)
    duplicate = process_pending_sells(ledger, settings, bar(open=9.0), state, DAY)

    assert len(failed) == 1
    assert failed[0].status is OrderStatus.PENDING_RETRY
    assert failed[0].failure_reason == "open_at_limit_down"
    assert duplicate == []
    assert state.pending_sell is not None and state.pending_sell.remaining_shares == 200

    next_day = date(2026, 7, 11)
    partial = process_pending_sells(
        ledger, settings, bar(date=next_day, open=9.0), state, next_day,
    )

    assert len(partial) == 1
    assert partial[0].status is OrderStatus.PENDING_RETRY
    assert partial[0].actual_shares == 100
    assert state.total_shares == 100
    assert state.pending_sell is not None and state.pending_sell.remaining_shares == 100
    assert ledger.cash == pytest.approx(900.0)


def test_batch_owned_pending_sell_consumes_only_its_exact_trend_batch() -> None:
    settings = T1ExecutionSettings(
        commission_rate=0.0, minimum_commission=0.0, stamp_tax_rate=0.0,
        slippage_pct=0.0,
    )
    state = ThermostatPositionState(symbol="600001.SH", current_mode="trend")
    state.record_trend_buy(1, 100, 10.0, date(2026, 7, 8))
    state.record_trend_buy(2, 100, 20.0, date(2026, 7, 8))
    state.start_trading_day(DAY)
    state.queue_pending(
        PendingSellLevel.PENDING_EXIT, 100, "trend", DAY, batch_index=2,
    )
    ledger = PortfolioLedger(cash=0.0, initial_capital=3000.0, positions={state.symbol: state})

    orders = process_pending_sells(ledger, settings, bar(open=25.0), state, DAY)

    assert orders[0].status is OrderStatus.FILLED
    assert state.trend_batches[0].actual_shares == 100
    assert state.trend_batches[1].actual_shares == 0
    assert state.trend_average_cost == pytest.approx(10.0)
    assert state.realized_pnl == pytest.approx(500.0)


@pytest.mark.parametrize(
    ("bar_kwargs", "reason"),
    [({"suspended": True}, "suspended"), ({"open": None}, "missing_open")],
)
def test_pending_failure_is_auditable_and_retains_state(bar_kwargs: dict[str, object], reason: str) -> None:
    settings = T1ExecutionSettings()
    state = ThermostatPositionState(symbol="600001.SH", current_mode="trend")
    state.queue_pending(PendingSellLevel.PENDING_REDUCE, 100, "trend", DAY)
    ledger = PortfolioLedger(cash=1000.0, initial_capital=1000.0, positions={state.symbol: state})

    orders = process_pending_sells(ledger, settings, bar(**bar_kwargs), state, DAY)

    assert len(orders) == 1
    assert orders[0].status is OrderStatus.PENDING_RETRY
    assert orders[0].failure_reason == reason
    assert ledger.orders[-1] is orders[0]
    assert state.pending_sell is not None and state.pending_sell.last_failure == reason


def test_invalid_pending_origin_is_audited_without_consuming_shares() -> None:
    settings = T1ExecutionSettings()
    state = ThermostatPositionState(symbol="600001.SH", current_mode="trend")
    state.record_trend_buy(1, 100, 10.0, date(2026, 7, 8))
    state.start_trading_day(DAY)
    state.queue_pending(PendingSellLevel.PENDING_EXIT, 100, "invalid", DAY)
    ledger = PortfolioLedger(cash=0.0, initial_capital=1000.0, positions={state.symbol: state})

    orders = process_pending_sells(ledger, settings, bar(), state, DAY)

    assert len(orders) == 1
    assert orders[0].status is OrderStatus.PENDING_RETRY
    assert orders[0].failure_reason == "invalid_origin_family"
    assert state.total_shares == 100 and state.available_shares == 100
    assert state.pending_sell is not None
    assert state.pending_sell.last_attempt_date == DAY


def test_pending_trade_date_must_match_bar_date_before_attempt() -> None:
    settings = T1ExecutionSettings()
    state = ThermostatPositionState(symbol="600001.SH", current_mode="trend")
    state.queue_pending(PendingSellLevel.PENDING_EXIT, 100, "trend", DAY)
    ledger = PortfolioLedger(cash=1000.0, initial_capital=1000.0, positions={state.symbol: state})

    with pytest.raises(ValueError, match="trade_date must equal bar.date"):
        process_pending_sells(ledger, settings, bar(), state, date(2026, 7, 11))

    assert state.pending_sell is not None
    assert state.pending_sell.attempt_count == 0
    assert ledger.orders == []
