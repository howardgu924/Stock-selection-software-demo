from copy import deepcopy
from datetime import date

import pytest

from stock_picker.strategies.thermostat_execution import (
    DailyBar,
    ExecutionPhase,
    OrderStatus,
    PortfolioLedger,
    T1ExecutionSettings,
)
from stock_picker.strategies.thermostat_state import (
    PendingSellLevel,
    ThermostatPositionState,
    TrendBatchRecord,
)
from stock_picker.strategies.thermostat_trend_executor import (
    execute_trend_candidate,
    execute_trend_day,
    finalize_trend_day,
    prepare_trend_day,
    preview_trend_phase,
)


DAY = date(2026, 7, 10)
PREVIOUS_DAY = date(2026, 7, 9)
SYMBOL = "600001.SH"


def bar(**changes: object) -> DailyBar:
    values = dict(
        date=DAY, open=10.0, high=10.4, low=9.8, close=10.2, volume=100_000.0,
        previous_close=10.0, limit_up_price=11.0, limit_down_price=9.0,
    )
    values.update(changes)
    return DailyBar(**values)


def plan(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "symbol": SYMBOL,
        "date": DAY,
        "data_cutoff_date": PREVIOUS_DAY,
        "stock_mode": "trend",
        "trend_buy_trigger": 10.0,
        "trend_reduce_trigger": 9.5,
        "trend_exit_trigger": 8.5,
        "effective_trend_exit_trigger": 8.8,
        "atr20": 1.0,
        "boll_upper": 10.5,
        "volume_ma20": 100_000.0,
        "target_position_pct": 0.50,
        "max_position_pct": 0.50,
        "market_position_discount": 1.0,
    }
    values.update(changes)
    return values


def settings(**changes: object) -> T1ExecutionSettings:
    values = dict(
        commission_rate=0.0, minimum_commission=0.0, stamp_tax_rate=0.0,
        slippage_pct=0.0, trend_symbol_base_max=1.0, trend_total_base_max=1.0,
        account_total_max=1.0,
    )
    values.update(changes)
    return T1ExecutionSettings(**values)


def ledger(cash: float = 100_000.0) -> PortfolioLedger:
    return PortfolioLedger(cash=cash, initial_capital=100_000.0)


def held_state(*, available: int, locked: int = 0, mode: str = "trend") -> ThermostatPositionState:
    state = ThermostatPositionState(symbol=SYMBOL, current_mode=mode, blocked_new_buy=False)
    if available:
        state.record_trend_buy(1, available, 10.0, date(2026, 7, 8))
    if locked:
        state.record_trend_buy(2 if available else 1, locked, 10.0, DAY)
    if available:
        state.start_trading_day(DAY)
    return state


def test_invalid_plan_is_audited_and_never_trades() -> None:
    account = ledger()

    result = execute_trend_day(
        plan(symbol="", trend_buy_trigger=float("nan")), bar(), account, settings(), DAY,
    )

    assert result.orders[-1].status in {OrderStatus.FAILED, OrderStatus.CANCELLED}
    assert result.orders[-1].failure_reason == "invalid_plan"
    assert "invalid_plan_symbol" in result.data_quality_warnings
    assert account.positions == {} and account.cash == pytest.approx(100_000.0)


def test_trade_and_bar_date_mismatch_is_audited() -> None:
    account = ledger()
    result = execute_trend_day(plan(), bar(), account, settings(), date(2026, 7, 11))

    assert result.orders[-1].failure_reason == "trade_date_bar_date_mismatch"
    assert account.positions == {}


@pytest.mark.parametrize(
    ("changes", "warning"),
    [
        ({"date": PREVIOUS_DAY}, "stale_plan_date"),
        ({"data_cutoff_date": None}, "invalid_data_cutoff_date"),
        ({"data_cutoff_date": DAY}, "data_cutoff_not_before_trade_date"),
    ],
)
def test_plan_requires_current_trade_date_and_prior_data_cutoff(
    changes: dict[str, object], warning: str,
) -> None:
    account = ledger()

    result = execute_trend_day(plan(**changes), bar(), account, settings(), DAY)

    assert result.orders[-1].failure_reason == "invalid_plan"
    assert warning in result.data_quality_warnings
    assert account.positions == {}


@pytest.mark.parametrize("mode", ["downtrend", "chaotic", "insufficient_data"])
def test_non_buy_modes_block_buys(mode: str) -> None:
    state = held_state(available=100, locked=100)
    account = ledger(0.0)
    account.positions[SYMBOL] = state

    result = execute_trend_day(plan(stock_mode=mode), bar(close=9.0), account, settings(), DAY)

    assert result.buys_blocked_for_day is True
    assert state.blocked_new_buy is True
    if mode == "downtrend":
        assert account.orders[-1].trigger_type == "downtrend_risk_sell"
        assert account.orders[-1].actual_shares == 100
        assert state.pending_sell is not None
        assert state.pending_sell.level is PendingSellLevel.PENDING_EMERGENCY_EXIT
    elif mode == "chaotic":
        assert state.total_shares == 200
    else:
        assert "insufficient_data" in result.data_quality_warnings
        assert state.total_shares == 200


def test_chaotic_keeps_previous_hard_stop_active() -> None:
    state = held_state(available=100)
    state.last_effective_exit_trigger = 9.4
    account = ledger(0.0)
    account.positions[SYMBOL] = state

    result = execute_trend_day(
        plan(stock_mode="chaotic", effective_trend_exit_trigger=""),
        bar(low=9.2, close=9.3), account, settings(), DAY,
    )

    assert result.orders[-1].trigger_type == "trend_exit"
    assert state.total_shares == 0


def test_insufficient_data_keeps_previous_hard_stop_and_queues_locked_shares() -> None:
    state = held_state(available=100, locked=100)
    state.last_effective_exit_trigger = 9.4
    account = ledger(0.0)
    account.positions[SYMBOL] = state

    result = execute_trend_day(
        plan(
            stock_mode="insufficient_data",
            effective_trend_exit_trigger="",
            trend_exit_trigger="",
        ),
        bar(high=20.0, low=9.2, close=9.3),
        account,
        settings(),
        DAY,
    )

    exit_orders = [order for order in result.orders if order.trigger_type == "trend_exit"]
    assert len(exit_orders) == 1
    assert exit_orders[0].status is OrderStatus.PENDING
    assert exit_orders[0].intended_shares == 200
    assert exit_orders[0].actual_shares == 100
    assert all(order.side != "buy" for order in result.orders)
    assert state.last_effective_exit_trigger == pytest.approx(9.4)
    assert state.total_shares == 100
    assert state.pending_sell is not None
    assert state.pending_sell.level is PendingSellLevel.PENDING_EXIT
    assert state.pending_sell.remaining_shares == 100
    assert result.buys_blocked_for_day is True


def test_exit_ratchets_and_sells_available_while_locking_remainder() -> None:
    state = held_state(available=200, locked=100)
    state.last_effective_exit_trigger = 9.2
    account = ledger(0.0)
    account.positions[SYMBOL] = state

    result = execute_trend_day(
        plan(effective_trend_exit_trigger=8.8), bar(low=9.1, close=9.15),
        account, settings(), DAY,
    )

    assert state.last_effective_exit_trigger == pytest.approx(9.2)
    assert result.orders[-1].actual_shares == 200
    assert state.pending_sell is not None
    assert state.pending_sell.level is PendingSellLevel.PENDING_EXIT
    assert state.pending_sell.remaining_shares == 100
    assert result.buys_blocked_for_day is True


def test_mid_reduce_fires_once_then_rearms_after_a_close_above() -> None:
    state = held_state(available=500)
    state.mid_band_state = "above"
    account = ledger(0.0)
    account.positions[SYMBOL] = state

    first = execute_trend_day(plan(), bar(low=9.4, close=9.4), account, settings(), DAY)
    assert first.orders[-1].trigger_type == "trend_reduce"
    assert first.orders[-1].actual_shares == 200

    second_day = date(2026, 7, 11)
    continuous = execute_trend_day(
        plan(date=second_day, data_cutoff_date=DAY), bar(date=second_day, low=9.3, close=9.3),
        account, settings(), second_day,
    )
    assert [order.status for order in continuous.orders] == [OrderStatus.PLAN_CREATED]

    third_day = date(2026, 7, 12)
    execute_trend_day(
        plan(date=third_day, data_cutoff_date=second_day), bar(date=third_day, low=9.7, close=9.7),
        account, settings(), third_day,
    )
    fourth_day = date(2026, 7, 13)
    rearmed = execute_trend_day(
        plan(date=fourth_day, data_cutoff_date=third_day), bar(date=fourth_day, low=9.4, close=9.4),
        account, settings(), fourth_day,
    )
    assert rearmed.orders[-1].trigger_type == "trend_reduce"


def test_mid_reduce_sells_all_when_available_is_below_two_lots() -> None:
    state = held_state(available=100)
    state.mid_band_state = "above"
    account = ledger(0.0)
    account.positions[SYMBOL] = state

    result = execute_trend_day(plan(), bar(low=9.4, close=9.4), account, settings(), DAY)

    assert result.orders[-1].actual_shares == 100
    assert state.total_shares == 0


def test_armed_mid_reduce_uses_daily_low_then_close_rearms_for_future_day() -> None:
    state = held_state(available=300)
    state.mid_band_state = "above"
    account = ledger(0.0)
    account.positions[SYMBOL] = state

    result = execute_trend_day(
        plan(), bar(low=9.4, close=9.8), account, settings(), DAY,
    )

    assert result.orders[-1].trigger_type == "trend_reduce"
    assert result.orders[-1].actual_shares == 100
    assert state.mid_band_state == "above"


def test_repeated_same_day_wrapper_call_after_finalize_executes_no_candidates() -> None:
    state = held_state(available=500)
    state.mid_band_state = "above"
    account = ledger(0.0)
    account.positions[SYMBOL] = state
    current_bar = bar(low=9.4, close=9.8)

    first = execute_trend_day(plan(), current_bar, account, settings(), DAY)
    second = execute_trend_day(plan(), current_bar, account, settings(), DAY)

    assert [order.trigger_type for order in first.orders] == ["trend_plan", "trend_reduce"]
    assert second.orders == []
    assert state.total_shares == 300


def test_reduce_sizes_available_and_locked_owners_separately() -> None:
    state = held_state(available=300, locked=300)
    state.mid_band_state = "above"
    account = ledger(0.0)
    account.positions[SYMBOL] = state

    result = execute_trend_day(
        plan(), bar(low=9.4, close=9.8), account, settings(), DAY,
    )

    filled = next(order for order in result.orders if order.status is OrderStatus.FILLED)
    pending = [order for order in result.orders if order.status is OrderStatus.PENDING]
    assert filled.actual_shares == 100
    assert len(pending) == 1 and pending[0].intended_shares == 100
    assert pending[0].trend_batch == 2
    assert state.pending_sell is not None
    assert state.pending_sell.remaining_shares == 100
    assert state.pending_sell.batch_index == 2


def test_three_batches_use_actual_fill_and_wait_until_next_day() -> None:
    account = ledger()
    first = execute_trend_day(plan(), bar(high=10.2), account, settings(), DAY)
    state = account.positions[SYMBOL]

    assert first.orders[-1].trend_batch == 1
    assert first.orders[-1].actual_shares == 2000
    assert first.next_batch_index == 2

    same_day = execute_trend_day(plan(), bar(high=20.0), account, settings(), DAY)
    assert same_day.orders == []

    next_day = date(2026, 7, 11)
    second = execute_trend_day(
        plan(date=next_day, data_cutoff_date=DAY), bar(date=next_day, high=10.8, close=10.7),
        account, settings(), next_day,
    )
    assert second.orders[-1].trend_batch == 2
    assert second.orders[-1].trigger_price == pytest.approx(10.7)
    assert state.trend_batch_index == 2


def test_partial_cash_fill_tops_up_same_batch_on_next_day() -> None:
    account = ledger(9_180.0)

    first = execute_trend_day(plan(), bar(high=10.2), account, settings(), DAY)
    state = account.positions[SYMBOL]
    first_batch = state.trend_batches[0]
    assert first.orders[-1].actual_shares == 900
    assert first_batch.planned_shares == 2_000
    assert first.next_batch_index == 1

    account.cash += 20_000.0
    next_day = date(2026, 7, 11)
    second = execute_trend_day(
        plan(date=next_day, data_cutoff_date=DAY),
        bar(date=next_day, high=10.3, close=10.3), account, settings(), next_day,
    )

    top_up = next(order for order in second.orders if order.side == "buy")
    assert top_up.trend_batch == 1
    assert top_up.intended_shares == 1_100
    assert first_batch.actual_shares == 2_000
    assert first_batch.fill_price == pytest.approx((900 * 10.2 + 1_100 * 10.3) / 2_000)
    assert first_batch.first_fill_date == DAY
    assert first_batch.last_fill_date == next_day
    assert second.next_batch_index == 2


def test_partial_batch_is_not_marked_covered_by_a_higher_later_trigger() -> None:
    account = ledger(9_180.0)
    execute_trend_day(plan(), bar(high=10.2), account, settings(), DAY)
    account.cash += 40_000.0
    next_day = date(2026, 7, 11)

    result = execute_trend_day(
        plan(date=next_day, data_cutoff_date=DAY, trend_buy_trigger=30.0),
        bar(date=next_day, high=31.0, close=30.0), account, settings(), next_day,
    )

    buy = next(order for order in result.orders if order.side == "buy")
    assert buy.trend_batch == 1
    assert buy.intended_shares == 1_100


def test_existing_holding_covers_first_batch_without_duplicate_buy() -> None:
    state = held_state(available=2_000)
    account = ledger(80_000.0)
    account.positions[SYMBOL] = state

    result = execute_trend_day(plan(), bar(high=10.2), account, settings(), DAY)

    assert all(order.status is not OrderStatus.FILLED for order in result.orders)
    assert result.next_batch_index == 2
    assert state.total_shares == 2_000


def test_existing_base_coverage_uses_actual_account_capital_and_buys_only_gap() -> None:
    state = ThermostatPositionState(symbol=SYMBOL, current_mode="trend", blocked_new_buy=False)
    state.record_grid_buy("trend_base", 2_000, 10.0, date(2026, 7, 8))
    state.start_trading_day(DAY)
    account = PortfolioLedger(cash=180_000.0, initial_capital=200_000.0, positions={SYMBOL: state})

    result = execute_trend_day(plan(), bar(high=10.2), account, settings(), DAY)

    assert result.orders[-1].status is OrderStatus.FILLED
    assert result.orders[-1].actual_shares == 2_000
    assert state.total_shares == 4_000


def test_covered_base_record_uses_cost_and_cutoff_for_next_batch_trigger() -> None:
    state = ThermostatPositionState(symbol=SYMBOL, current_mode="trend", blocked_new_buy=False)
    state.record_grid_buy("trend_base", 2_500, 8.0, PREVIOUS_DAY)
    state.start_trading_day(DAY)
    account = ledger(80_000.0)
    account.positions[SYMBOL] = state

    result = execute_trend_day(
        plan(effective_trend_exit_trigger=7.0, trend_exit_trigger=7.0),
        bar(open=8.4, high=8.6, low=8.2, close=8.5), account, settings(), DAY,
    )

    covered = state.trend_batches[0]
    assert covered.batch_index == 1 and covered.status == "covered"
    assert covered.fill_price == pytest.approx(8.0)
    assert covered.fill_date == PREVIOUS_DAY
    buy = next(order for order in result.orders if order.side == "buy")
    assert buy.trend_batch == 2
    assert buy.trigger_price == pytest.approx(8.5)


def test_covered_base_acquired_today_cannot_unlock_second_batch_today() -> None:
    state = ThermostatPositionState(symbol=SYMBOL, current_mode="trend", blocked_new_buy=False)
    state.record_grid_buy("trend_base", 2_500, 8.0, DAY)
    account = ledger(80_000.0)
    account.positions[SYMBOL] = state

    result = execute_trend_day(
        plan(effective_trend_exit_trigger=7.0, trend_exit_trigger=7.0),
        bar(open=8.4, high=20.0, low=8.2, close=8.5), account, settings(), DAY,
    )

    assert state.trend_batches[0].fill_date == DAY
    assert all(order.side != "buy" for order in result.orders)
    assert result.next_batch_index == 2


def test_plan_caps_are_applied_per_call_without_mutating_settings() -> None:
    base_settings = settings(trend_total_base_max=0.40)
    account = ledger()

    result = execute_trend_day(
        plan(target_position_pct=0.20, max_position_pct=0.05, market_position_discount=0.50),
        bar(high=10.1, close=10.0), account, base_settings, DAY,
    )

    assert result.orders[-1].failure_reason == "symbol_cap_exceeded"
    assert base_settings.trend_symbol_base_max == pytest.approx(1.0)
    assert base_settings.trend_total_base_max == pytest.approx(0.40)


def test_plan_cap_cannot_loosen_global_symbol_cap() -> None:
    base_settings = settings(trend_symbol_base_max=0.20)

    result = execute_trend_day(
        plan(target_position_pct=0.80, max_position_pct=0.80),
        bar(high=10.1, close=10.0), ledger(), base_settings, DAY,
    )

    assert result.orders[-1].failure_reason == "symbol_cap_exceeded"
    assert base_settings.trend_symbol_base_max == pytest.approx(0.20)


@pytest.mark.parametrize("field", ["target_position_pct", "max_position_pct"])
def test_plan_position_caps_above_one_are_rejected(field: str) -> None:
    result = execute_trend_day(
        plan(**{field: 1.01}), bar(), ledger(), settings(), DAY,
    )

    assert result.orders[-1].failure_reason == "invalid_plan"
    assert f"invalid_{field}" in result.data_quality_warnings


def test_market_discount_scales_trend_total_cap() -> None:
    other = ThermostatPositionState(symbol="600002.SH", current_mode="trend", blocked_new_buy=False)
    other.record_trend_buy(1, 1_500, 10.0, PREVIOUS_DAY)
    other.start_trading_day(DAY)
    account = PortfolioLedger(
        cash=85_000.0, initial_capital=100_000.0, positions={"600002.SH": other},
    )

    result = execute_trend_day(
        plan(target_position_pct=0.20, max_position_pct=0.20, market_position_discount=0.50),
        bar(high=10.1, close=10.0), account, settings(trend_total_base_max=0.40), DAY,
    )

    assert result.orders[-1].failure_reason == "trend_total_cap_exceeded"


@pytest.mark.parametrize("discount", [float("nan"), -0.1, 1.1])
def test_invalid_market_discount_is_rejected(discount: float) -> None:
    result = execute_trend_day(
        plan(market_position_discount=discount), bar(), ledger(), settings(), DAY,
    )

    assert result.orders[-1].failure_reason == "invalid_plan"
    assert "invalid_market_position_discount" in result.data_quality_warnings


def test_valid_plan_without_trigger_still_has_plan_created_audit() -> None:
    account = ledger()

    result = execute_trend_day(plan(), bar(high=9.9, low=9.6), account, settings(), DAY)

    assert len(result.orders) == 1
    assert result.orders[0].status is OrderStatus.PLAN_CREATED
    assert result.orders[0].trigger_type == "trend_plan"


def test_missing_daily_prices_are_explicit_quality_warnings() -> None:
    account = ledger()

    result = execute_trend_day(
        plan(), bar(high=None, low=None, close=None), account, settings(), DAY,
    )

    assert {"missing_daily_high", "missing_daily_low", "missing_daily_close"}.issubset(
        result.data_quality_warnings
    )
    assert all(order.status is not OrderStatus.FILLED for order in result.orders)


def test_no_holding_buy_exit_ambiguity_buys_first_then_queues_exit() -> None:
    account = ledger()

    result = execute_trend_day(plan(), bar(high=10.2, low=8.7), account, settings(), DAY)

    executions = [order for order in result.orders if order.side]
    assert [order.side for order in executions] == ["buy", "sell"]
    assert executions[0].status is OrderStatus.FILLED
    assert executions[1].status is OrderStatus.PENDING
    assert "approximate_intraday_sequence" in result.data_quality_warnings
    assert account.positions[SYMBOL].pending_sell is not None


def test_no_holding_buy_reduce_ambiguity_buys_first_then_queues_reduce() -> None:
    account = ledger()

    result = execute_trend_day(plan(), bar(high=10.2, low=9.4), account, settings(), DAY)

    executions = [order for order in result.orders if order.side]
    assert [order.side for order in executions] == ["buy", "sell"]
    assert executions[1].pending_level is PendingSellLevel.PENDING_REDUCE
    assert "approximate_intraday_sequence" in result.data_quality_warnings


def test_strict_fake_breakout_never_cancels_buy_and_upgrades_to_exit() -> None:
    account = ledger()
    strict = bar(open=10.2, high=12.0, low=10.0, close=10.8, volume=300_000.0)

    result = execute_trend_day(plan(boll_upper=11.0), strict, account, settings(), DAY)

    assert result.strict_fake_breakout is True
    assert next(order for order in result.orders if order.side == "buy").status is OrderStatus.FILLED
    assert account.positions[SYMBOL].pending_sell is not None
    assert account.positions[SYMBOL].pending_sell.level is PendingSellLevel.PENDING_EXIT


def test_trend_preview_is_side_effect_free_and_prepare_finalize_are_idempotent() -> None:
    account = ledger()
    current_bar = bar(high=10.2)
    prepare_trend_day(plan(), current_bar, account, settings(), DAY)
    prepared = deepcopy(account)

    first = preview_trend_phase(
        plan(), current_bar, account, settings(), DAY, ExecutionPhase.TREND_BUY,
    )
    second = preview_trend_phase(
        plan(), current_bar, account, settings(), DAY, ExecutionPhase.TREND_BUY,
    )

    assert account == prepared
    assert first == second
    assert first[0].trend_batch == 1
    prepare_trend_day(plan(), current_bar, account, settings(), DAY)
    finalize_trend_day(plan(), current_bar, account, settings(), DAY)
    finalize_trend_day(plan(), current_bar, account, settings(), DAY)
    assert sum(order.trigger_type == "trend_plan" for order in account.orders) == 1


def test_trend_candidate_revalidates_filled_batch_before_mutation() -> None:
    account = ledger()
    current_bar = bar(high=10.2)
    prepare_trend_day(plan(), current_bar, account, settings(), DAY)
    candidate = preview_trend_phase(
        plan(), current_bar, account, settings(), DAY, ExecutionPhase.TREND_BUY,
    )[0]
    state = account.positions.setdefault(
        SYMBOL, ThermostatPositionState(symbol=SYMBOL, current_mode="trend", blocked_new_buy=False),
    )
    state.record_trend_buy(1, 100, 10.0, DAY, planned_shares=100)
    before_cash = account.cash

    orders = execute_trend_candidate(
        candidate, plan(), current_bar, account, settings(), DAY,
    )

    assert orders[-1].status is OrderStatus.CANCELLED
    assert orders[-1].failure_reason == "stale_candidate"
    assert account.cash == before_cash


def test_finalized_trend_context_rejects_previously_previewed_candidate() -> None:
    account = ledger()
    current_bar = bar(high=10.2)
    prepare_trend_day(plan(), current_bar, account, settings(), DAY)
    candidate = preview_trend_phase(
        plan(), current_bar, account, settings(), DAY, ExecutionPhase.TREND_BUY,
    )[0]
    finalize_trend_day(plan(), current_bar, account, settings(), DAY)

    orders = execute_trend_candidate(
        candidate, plan(), current_bar, account, settings(), DAY,
    )

    assert orders[-1].status is OrderStatus.CANCELLED
    assert orders[-1].failure_reason == "stale_candidate"
    assert account.positions == {}


def test_trend_plan_candidate_and_order_share_actual_trace_ids() -> None:
    account = ledger()
    current_bar = bar(high=10.2)
    prepared = prepare_trend_day(plan(), current_bar, account, settings(), DAY)
    candidate = preview_trend_phase(
        plan(), current_bar, account, settings(), DAY, ExecutionPhase.TREND_BUY,
    )[0]

    orders = execute_trend_candidate(
        candidate, plan(), current_bar, account, settings(), DAY,
    )

    assert candidate.plan_trace_id == prepared.plan_order_id
    assert candidate.order_trace_id
    assert orders[-1].order_id == candidate.order_trace_id
