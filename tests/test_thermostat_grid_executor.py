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
from stock_picker.strategies.thermostat_grid_executor import (
    execute_grid_candidate,
    execute_grid_day,
    finalize_grid_day,
    prepare_grid_day,
    preview_grid_phase,
)
from stock_picker.strategies.thermostat_state import (
    GridLayerStatus,
    PendingSellLevel,
    ThermostatPositionState,
)
from stock_picker.strategies.thermostat_trend_executor import (
    execute_trend_candidate,
    prepare_trend_day,
    preview_trend_phase,
)


DAY = date(2026, 7, 10)
PREVIOUS_DAY = date(2026, 7, 9)
SYMBOL = "600001.SH"


def plan(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "symbol": SYMBOL,
        "date": DAY,
        "data_cutoff_date": PREVIOUS_DAY,
        "stock_mode": "range",
        "grid_lower": 8.0,
        "grid_mid": 10.0,
        "grid_upper": 12.0,
        "grid_buy_levels": "9.5|9.0|8.5",
        "grid_sell_levels": "10.5|11.0|11.5",
        "configured_grid_layers": 3,
        "effective_grid_layers": 3,
        "target_position_pct": 0.15,
        "max_position_pct": 0.15,
        "grid_total_max_position_pct": 0.40,
        "market_position_discount": 1.0,
    }
    values.update(changes)
    return values


def bar(**changes: object) -> DailyBar:
    values = dict(
        date=DAY, open=10.0, high=10.2, low=9.8, close=10.0,
        volume=100_000.0, previous_close=10.0,
        limit_up_price=11.0, limit_down_price=9.0,
    )
    values.update(changes)
    return DailyBar(**values)


def settings(**changes: object) -> T1ExecutionSettings:
    values = dict(
        commission_rate=0.0, minimum_commission=0.0, stamp_tax_rate=0.0,
        slippage_pct=0.0, grid_symbol_base_max=1.0,
        grid_total_hard_max=0.40, account_total_max=1.0,
    )
    values.update(changes)
    return T1ExecutionSettings(**values)


def ledger(cash: float = 100_000.0) -> PortfolioLedger:
    return PortfolioLedger(cash=cash, initial_capital=100_000.0)


def held_state(*, available_layers: tuple[str, ...] = (), locked_layers: tuple[str, ...] = ()) -> ThermostatPositionState:
    state = ThermostatPositionState(
        symbol=SYMBOL, current_mode="range", blocked_new_buy=False,
    )
    prices = {
        "grid-1": (9.5, 10.5), "grid-2": (9.0, 11.0), "grid-3": (8.5, 11.5),
    }
    for layer_id in available_layers:
        buy_price, sell_price = prices[layer_id]
        state.record_grid_buy(
            layer_id, 100, buy_price, PREVIOUS_DAY,
            buy_price=buy_price, sell_price=sell_price,
            target_position_pct=0.05, target_shares=500,
        )
    for layer_id in locked_layers:
        buy_price, sell_price = prices[layer_id]
        state.record_grid_buy(
            layer_id, 100, buy_price, DAY,
            buy_price=buy_price, sell_price=sell_price,
            target_position_pct=0.05, target_shares=500,
        )
    if available_layers:
        state.start_trading_day(DAY)
    return state


def other_grid_position(position_pct: float) -> ThermostatPositionState:
    state = ThermostatPositionState(
        symbol="600002.SH", current_mode="range", blocked_new_buy=False,
    )
    shares = int(position_pct * 100_000 / 10.0)
    state.record_grid_buy(
        "grid-1", shares, 10.0, PREVIOUS_DAY,
        buy_price=10.0, sell_price=11.0,
        target_position_pct=position_pct, target_shares=shares,
    )
    state.start_trading_day(DAY)
    return state


def other_trend_position(position_pct: float) -> ThermostatPositionState:
    state = ThermostatPositionState(
        symbol="600002.SH", current_mode="trend", blocked_new_buy=False,
    )
    shares = int(position_pct * 100_000 / 10.0)
    state.record_trend_buy(1, shares, 10.0, PREVIOUS_DAY)
    state.start_trading_day(DAY)
    return state


def test_invalid_temporal_plan_is_audited_without_mutation() -> None:
    account = ledger()
    result = execute_grid_day(
        plan(date=PREVIOUS_DAY, data_cutoff_date=DAY), bar(), account, settings(), DAY,
    )
    assert result.orders[-1].failure_reason == "invalid_plan"
    assert {"stale_plan_date", "data_cutoff_not_before_trade_date"}.issubset(
        result.data_quality_warnings
    )
    assert account.positions == {}


def test_levels_are_deduplicated_paired_nearest_mid_and_sized_by_effective_layers() -> None:
    account = ledger()
    result = execute_grid_day(
        plan(
            grid_buy_levels=[9.5, 9.5, 9.0],
            grid_sell_levels="10.5|10.5|11.0",
            effective_grid_layers=2,
            max_position_pct=0.12,
        ),
        bar(), account, settings(), DAY,
    )
    state = account.positions[SYMBOL]
    assert result.effective_layer_count == 2
    assert list(state.grid_layers) == ["grid-1", "grid-2"]
    assert (state.grid_layers["grid-1"].buy_price, state.grid_layers["grid-1"].sell_price) == (9.5, 10.5)
    assert state.grid_layers["grid-1"].target_position_pct == pytest.approx(0.06)
    assert state.grid_layers["grid-1"].target_shares == 600


@pytest.mark.parametrize(
    ("changes", "warning"),
    [
        ({"grid_buy_levels": "9.0|9.5|8.5"}, "invalid_grid_buy_levels"),
        ({"grid_sell_levels": "11.0|10.5|11.5"}, "invalid_grid_sell_levels"),
        ({"grid_buy_levels": "9.5|9.0", "grid_sell_levels": "10.5"}, "mismatched_grid_layer_count"),
    ],
)
def test_invalid_level_shape_is_audited(changes: dict[str, object], warning: str) -> None:
    account = ledger()
    result = execute_grid_day(plan(**changes), bar(), account, settings(), DAY)
    assert result.orders[-1].failure_reason == "invalid_plan"
    assert warning in result.data_quality_warnings


def test_crossing_multiple_buys_selects_highest_waiting_layer_only() -> None:
    account = ledger()
    result = execute_grid_day(plan(), bar(low=8.4), account, settings(), DAY)
    assert result.selected_buy_layer == "grid-1"
    assert result.selected_sell_layer is None
    fills = [order for order in result.orders if order.status is OrderStatus.FILLED]
    assert [(order.side, order.grid_layer) for order in fills] == [("buy", "grid-1")]
    assert account.positions[SYMBOL].grid_layers["grid-2"].status is GridLayerStatus.WAITING_BUY


def test_repeated_same_day_call_cannot_buy_a_second_layer() -> None:
    account = ledger()
    first = execute_grid_day(plan(), bar(low=8.4), account, settings(), DAY)
    second = execute_grid_day(plan(), bar(low=8.4), account, settings(), DAY)
    assert first.selected_buy_layer == "grid-1"
    assert second.selected_buy_layer is None
    fills = [
        order for order in account.fills
        if order.trade_date == DAY and order.family == "grid" and order.side == "buy"
    ]
    assert [(order.side, order.grid_layer) for order in fills] == [("buy", "grid-1")]
    assert sum(order.trigger_type == "grid_plan" for order in account.orders) == 1


def test_held_or_incomplete_layer_cannot_buy_again() -> None:
    state = held_state(available_layers=("grid-1",))
    account = ledger()
    account.positions[SYMBOL] = state
    result = execute_grid_day(plan(), bar(low=8.4), account, settings(), DAY)
    assert result.selected_buy_layer == "grid-2"
    assert state.grid_layers["grid-1"].held_shares == 100


def test_crossing_multiple_sells_uses_lowest_sell_price_and_matching_owner() -> None:
    state = held_state(available_layers=("grid-1", "grid-2"))
    account = ledger()
    account.positions[SYMBOL] = state
    result = execute_grid_day(plan(), bar(high=12.0), account, settings(), DAY)
    assert result.selected_sell_layer == "grid-1"
    assert result.selected_buy_layer is None
    assert state.grid_layers["grid-1"].held_shares == 0
    assert state.grid_layers["grid-2"].held_shares == 100


def test_repeated_same_day_call_cannot_sell_a_second_layer() -> None:
    state = held_state(available_layers=("grid-1", "grid-2"))
    account = ledger()
    account.positions[SYMBOL] = state
    first = execute_grid_day(plan(), bar(high=12.0), account, settings(), DAY)
    second = execute_grid_day(plan(), bar(high=12.0), account, settings(), DAY)
    assert first.selected_sell_layer == "grid-1"
    assert second.selected_sell_layer is None
    assert state.grid_layers["grid-2"].held_shares == 100


def test_sell_first_ends_grid_actions_when_buy_and_sell_coexist() -> None:
    state = held_state(available_layers=("grid-1",))
    account = ledger()
    account.positions[SYMBOL] = state
    result = execute_grid_day(plan(), bar(high=11.0, low=8.4), account, settings(), DAY)
    assert result.selected_sell_layer == "grid-1"
    assert result.selected_buy_layer is None
    assert state.grid_layers["grid-2"].held_shares == 0


def test_locked_layer_cannot_sell_and_different_waiting_layer_may_buy() -> None:
    state = held_state(locked_layers=("grid-1",))
    account = ledger()
    account.positions[SYMBOL] = state
    result = execute_grid_day(plan(), bar(high=11.0, low=8.4), account, settings(), DAY)
    assert result.selected_sell_layer is None
    assert result.selected_buy_layer == "grid-2"
    assert state.grid_layers["grid-1"].today_bought_shares == 100


def test_lower_break_disables_buys_sells_available_and_queues_locked_per_layer() -> None:
    state = held_state(available_layers=("grid-1",), locked_layers=("grid-2",))
    account = ledger()
    account.positions[SYMBOL] = state
    result = execute_grid_day(plan(), bar(low=7.9), account, settings(), DAY)
    assert result.buys_blocked_for_day is True
    assert state.grid_layers["grid-1"].held_shares == 0
    assert state.grid_layers["grid-2"].held_shares == 100
    assert state.grid_layers["grid-3"].status is GridLayerStatus.DISABLED
    pending = next(item for item in state.pending_sells if item.grid_layer_id == "grid-2")
    assert pending.level is PendingSellLevel.PENDING_EXIT
    assert any(order.status is OrderStatus.CANCELLED for order in result.orders)
    assert "grid_lower_break_approximation" in result.data_quality_warnings


def test_lower_break_sell_failure_keeps_available_and_locked_shares_pending() -> None:
    state = held_state(available_layers=("grid-1",))
    state.record_grid_buy(
        "grid-1", 100, 9.5, DAY, buy_price=9.5, sell_price=10.5,
        target_position_pct=0.05, target_shares=500,
    )
    account = ledger()
    account.positions[SYMBOL] = state
    result = execute_grid_day(plan(), bar(low=7.9, suspended=True), account, settings(), DAY)
    pending = next(item for item in state.pending_sells if item.grid_layer_id == "grid-1")
    assert pending.requested_shares == 200
    assert pending.remaining_shares == 200
    assert any(
        order.status is OrderStatus.FAILED and order.failure_reason == "suspended"
        for order in result.orders
    )
    assert any(
        order.status is OrderStatus.PENDING and order.intended_shares == 100
        for order in result.orders
    )


def test_lower_break_failed_available_sell_keeps_emergency_priority_in_locked_audit() -> None:
    state = held_state(available_layers=("grid-1",))
    state.record_grid_buy(
        "grid-1", 100, 9.5, DAY, buy_price=9.5, sell_price=10.5,
        target_position_pct=0.05, target_shares=500,
    )
    account = ledger()
    account.positions[SYMBOL] = state

    result = execute_grid_day(
        plan(), bar(low=7.9, suspended=True), account, settings(), DAY,
    )

    owner_pending = next(
        item for item in state.pending_sells if item.grid_layer_id == "grid-1"
    )
    locked_audit = next(
        order for order in result.orders
        if order.status is OrderStatus.PENDING and order.intended_shares == 100
    )
    assert owner_pending.level is PendingSellLevel.PENDING_EMERGENCY_EXIT
    assert locked_audit.pending_level is PendingSellLevel.PENDING_EMERGENCY_EXIT
    assert "pending_priority_upgraded_to_emergency_exit" in locked_audit.quality_warning


@pytest.mark.parametrize("mode", ["chaotic", "insufficient_data"])
def test_non_range_quality_modes_block_buys_without_liquidating(mode: str) -> None:
    state = held_state(available_layers=("grid-1",))
    account = ledger()
    account.positions[SYMBOL] = state
    result = execute_grid_day(plan(stock_mode=mode), bar(low=8.4), account, settings(), DAY)
    assert result.buys_blocked_for_day is True
    assert state.total_shares == 100
    if mode == "insufficient_data":
        assert "insufficient_data" in result.data_quality_warnings


def test_downtrend_risk_exits_grid_owners() -> None:
    state = held_state(available_layers=("grid-1",), locked_layers=("grid-2",))
    account = ledger()
    account.positions[SYMBOL] = state
    result = execute_grid_day(plan(stock_mode="downtrend"), bar(), account, settings(), DAY)
    assert result.buys_blocked_for_day is True
    assert state.grid_layers["grid-1"].held_shares == 0
    assert state.grid_layers["grid-2"].held_shares == 100
    assert any(item.grid_layer_id == "grid-2" for item in state.pending_sells)


def test_plan_cap_is_used_per_call_without_mutating_settings() -> None:
    account = ledger()
    configured = settings(grid_symbol_base_max=0.30)
    execute_grid_day(plan(max_position_pct=0.12), bar(low=9.4), account, configured, DAY)
    assert configured.grid_symbol_base_max == pytest.approx(0.30)
    state = account.positions[SYMBOL]
    assert state.grid_layers["grid-1"].target_position_pct == pytest.approx(0.04)
    assert state.grid_layers["grid-1"].target_shares == 400


def test_plan_symbol_cap_only_tightens_caller_setting() -> None:
    account = ledger()
    configured = settings(grid_symbol_base_max=0.06)
    execute_grid_day(plan(max_position_pct=0.12), bar(low=9.4), account, configured, DAY)
    state = account.positions[SYMBOL]
    assert state.grid_layers["grid-1"].target_position_pct == pytest.approx(0.02)


@pytest.mark.parametrize("invalid_cap", [None, 0.0, -0.1, 0.41, float("inf"), float("nan")])
def test_plan_grid_total_cap_must_be_finite_positive_and_at_most_40_percent(
    invalid_cap: object,
) -> None:
    account = ledger()
    result = execute_grid_day(
        plan(grid_total_max_position_pct=invalid_cap), bar(low=9.4),
        account, settings(), DAY,
    )
    assert result.orders[-1].failure_reason == "invalid_plan"
    assert "invalid_grid_total_max_position_pct" in result.data_quality_warnings


def test_grid_total_cap_never_exceeds_40_percent_when_caller_setting_is_higher() -> None:
    account = ledger()
    account.positions["600002.SH"] = other_grid_position(0.39)

    result = execute_grid_day(
        plan(max_position_pct=0.15, grid_total_max_position_pct=0.40),
        bar(low=9.4), account,
        settings(grid_total_hard_max=0.80, account_total_max=1.0), DAY,
    )

    buy = next(order for order in result.orders if order.trigger_type == "grid_buy")
    assert buy.status is OrderStatus.FAILED
    assert buy.failure_reason == "grid_total_cap_exceeded"


def test_grid_buy_respects_account_total_cap() -> None:
    account = ledger()
    account.positions["600002.SH"] = other_trend_position(0.39)

    result = execute_grid_day(
        plan(max_position_pct=0.15), bar(low=9.4), account,
        settings(account_total_max=0.42), DAY,
    )

    buy = next(order for order in result.orders if order.trigger_type == "grid_buy")
    assert buy.status is OrderStatus.FAILED
    assert buy.failure_reason == "account_total_cap_exceeded"


def test_one_word_limit_up_grid_buy_failure_is_audited() -> None:
    account = ledger()
    result = execute_grid_day(
        plan(
            grid_lower=10.0, grid_mid=12.0, grid_upper=14.0,
            grid_buy_levels="11.5|11.2|11.0",
            grid_sell_levels="12.5|13.0|13.5",
        ),
        bar(open=11.0, high=11.0, low=11.0, close=11.0, limit_up_price=11.0),
        account, settings(), DAY,
    )
    buy = next(order for order in result.orders if order.trigger_type == "grid_buy")
    assert buy.status is OrderStatus.FAILED
    assert buy.failure_reason == "one_word_limit_up"


@pytest.mark.parametrize(
    ("bar_changes", "failure_reason"),
    [
        ({"open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0}, "one_word_limit_down"),
        ({"low": 7.9, "suspended": True}, "suspended"),
    ],
)
def test_lower_break_limit_or_suspension_risk_sell_retains_emergency_pending(
    bar_changes: dict[str, object], failure_reason: str,
) -> None:
    state = held_state(available_layers=("grid-1",))
    account = ledger()
    account.positions[SYMBOL] = state

    result = execute_grid_day(
        plan(grid_lower=9.5), bar(**bar_changes), account, settings(), DAY,
    )

    failed = next(order for order in result.orders if order.failure_reason == failure_reason)
    pending = next(item for item in state.pending_sells if item.grid_layer_id == "grid-1")
    assert failed.pending_level is PendingSellLevel.PENDING_EMERGENCY_EXIT
    assert pending.level is PendingSellLevel.PENDING_EMERGENCY_EXIT
    assert pending.remaining_shares == 100


def test_missing_daily_prices_are_quality_warnings_and_no_trade() -> None:
    account = ledger()
    result = execute_grid_day(
        plan(), bar(open=None, high=None, low=None, close=None), account, settings(), DAY,
    )
    assert {"missing_daily_high", "missing_daily_low", "missing_daily_close"}.issubset(
        result.data_quality_warnings
    )
    assert not any(order.status is OrderStatus.FILLED for order in result.orders)


def test_grid_preview_is_side_effect_free_and_prepare_finalize_are_idempotent() -> None:
    account = ledger()
    prepare_grid_day(plan(), bar(low=9.4), account, settings(), DAY)
    prepared = deepcopy(account)

    first = preview_grid_phase(
        plan(), bar(low=9.4), account, settings(), DAY, ExecutionPhase.GRID_BUY,
    )
    second = preview_grid_phase(
        plan(), bar(low=9.4), account, settings(), DAY, ExecutionPhase.GRID_BUY,
    )

    assert account == prepared
    assert first == second
    assert first[0].grid_layer == "grid-1"
    prepare_grid_day(plan(), bar(low=9.4), account, settings(), DAY)
    finalize_grid_day(plan(), bar(low=9.4), account, settings(), DAY)
    finalize_grid_day(plan(), bar(low=9.4), account, settings(), DAY)
    assert sum(order.trigger_type == "grid_plan" for order in account.orders) == 1


def test_finalized_grid_context_cannot_preview_or_execute_candidates() -> None:
    account = ledger()
    current_bar = bar(low=9.4)
    prepare_grid_day(plan(), current_bar, account, settings(), DAY)
    candidate = preview_grid_phase(
        plan(), current_bar, account, settings(), DAY, ExecutionPhase.GRID_BUY,
    )[0]
    finalize_grid_day(plan(), current_bar, account, settings(), DAY)

    assert preview_grid_phase(
        plan(), current_bar, account, settings(), DAY, ExecutionPhase.GRID_BUY,
    ) == []
    orders = execute_grid_candidate(
        candidate, plan(), current_bar, account, settings(), DAY,
    )
    assert orders[-1].status is OrderStatus.CANCELLED
    assert orders[-1].failure_reason == "stale_candidate"
    assert account.positions[SYMBOL].total_shares == 0


def test_grid_candidate_revalidates_stale_layer_before_mutation() -> None:
    account = ledger()
    current_bar = bar(low=9.4)
    prepare_grid_day(plan(), current_bar, account, settings(), DAY)
    candidate = preview_grid_phase(
        plan(), current_bar, account, settings(), DAY, ExecutionPhase.GRID_BUY,
    )[0]
    account.positions[SYMBOL].grid_layers["grid-1"].status = GridLayerStatus.COMPLETED
    before_cash = account.cash

    orders = execute_grid_candidate(
        candidate, plan(), current_bar, account, settings(), DAY,
    )

    assert orders[-1].status is OrderStatus.CANCELLED
    assert orders[-1].failure_reason == "stale_candidate"
    assert account.cash == before_cash


def test_grid_plan_candidate_and_order_share_actual_trace_ids() -> None:
    account = ledger()
    current_bar = bar(low=9.4)
    prepared = prepare_grid_day(plan(), current_bar, account, settings(), DAY)
    candidate = preview_grid_phase(
        plan(), current_bar, account, settings(), DAY, ExecutionPhase.GRID_BUY,
    )[0]

    orders = execute_grid_candidate(
        candidate, plan(), current_bar, account, settings(), DAY,
    )

    assert candidate.plan_trace_id == prepared.plan_order_id
    assert candidate.order_trace_id
    assert orders[-1].order_id == candidate.order_trace_id


def test_sell_candidate_cash_is_visible_to_following_trend_buy_candidate() -> None:
    state = held_state(available_layers=("grid-1",))
    account = ledger(cash=0.0)
    account.positions[SYMBOL] = state
    current_bar = bar(high=10.6, low=9.8, close=10.2)
    configured = settings()
    trend_plan = {
        "symbol": "600002.SH", "date": DAY, "data_cutoff_date": PREVIOUS_DAY,
        "stock_mode": "trend", "trend_buy_trigger": 10.0,
        "trend_reduce_trigger": 9.5, "trend_exit_trigger": 8.5,
        "effective_trend_exit_trigger": 8.8, "atr20": 1.0,
        "boll_upper": 10.5, "volume_ma20": 100_000.0,
        "target_position_pct": 0.05, "max_position_pct": 0.05,
        "market_position_discount": 1.0,
    }
    prepare_grid_day(plan(), current_bar, account, configured, DAY)
    prepare_trend_day(trend_plan, current_bar, account, configured, DAY)
    sell = preview_grid_phase(
        plan(), current_bar, account, configured, DAY, ExecutionPhase.GRID_SELL,
    )[0]
    buy = preview_trend_phase(
        trend_plan, current_bar, account, configured, DAY, ExecutionPhase.TREND_BUY,
    )[0]

    execute_grid_candidate(sell, plan(), current_bar, account, configured, DAY)
    buy_orders = execute_trend_candidate(
        buy, trend_plan, current_bar, account, configured, DAY,
    )

    assert account.cash > 0
    assert buy_orders[-1].status is OrderStatus.FILLED
    assert buy_orders[-1].actual_shares == 100


def test_grid_sell_changes_account_cap_used_by_following_trend_buy() -> None:
    state = held_state(available_layers=("grid-1",))
    account = ledger(cash=100_000.0)
    account.positions[SYMBOL] = state
    current_bar = bar(high=10.6, low=9.8, close=10.2)
    configured = settings(account_total_max=0.025)
    trend_plan = {
        "symbol": "600002.SH", "date": DAY, "data_cutoff_date": PREVIOUS_DAY,
        "stock_mode": "trend", "trend_buy_trigger": 10.0,
        "trend_reduce_trigger": 9.5, "trend_exit_trigger": 8.5,
        "effective_trend_exit_trigger": 8.8, "atr20": 1.0,
        "boll_upper": 10.5, "volume_ma20": 100_000.0,
        "target_position_pct": 0.05, "max_position_pct": 0.05,
        "market_position_discount": 1.0,
    }
    prepare_grid_day(plan(), current_bar, account, configured, DAY)
    prepare_trend_day(trend_plan, current_bar, account, configured, DAY)
    sell = preview_grid_phase(
        plan(), current_bar, account, configured, DAY, ExecutionPhase.GRID_SELL,
    )[0]
    buy = preview_trend_phase(
        trend_plan, current_bar, account, configured, DAY, ExecutionPhase.TREND_BUY,
    )[0]
    blocked_account = deepcopy(account)
    blocked = execute_trend_candidate(
        buy, trend_plan, current_bar, blocked_account, configured, DAY,
    )[-1]
    assert blocked.failure_reason == "account_total_cap_exceeded"

    execute_grid_candidate(sell, plan(), current_bar, account, configured, DAY)
    allowed = execute_trend_candidate(
        buy, trend_plan, current_bar, account, configured, DAY,
    )[-1]

    assert allowed.status is OrderStatus.FILLED
