from datetime import date

import pytest

from stock_picker.strategies.thermostat_state import (
    GridLayerPosition,
    GridLayerStatus,
    PendingSellLevel,
    PendingSellState,
    ThermostatPositionState,
    TrendBatchRecord,
)


def test_public_state_types_expose_stable_string_values() -> None:
    assert PendingSellLevel.PENDING_REDUCE == "pending_reduce"
    assert PendingSellLevel.PENDING_EXIT == "pending_exit"
    assert PendingSellLevel.PENDING_EMERGENCY_EXIT == "pending_emergency_exit"
    assert GridLayerStatus.WAITING_BUY == "waiting_buy"
    assert GridLayerStatus.BOUGHT_TODAY == "bought_today"
    assert GridLayerStatus.HOLDING_AVAILABLE == "holding_available"


def test_start_trading_day_releases_each_owner_without_losing_ownership() -> None:
    state = ThermostatPositionState(symbol="600001.SH", current_mode="trend")
    state.record_trend_buy(
        batch_index=1,
        shares=100,
        price=10.0,
        trade_date=date(2026, 7, 10),
        target_ratio=0.3,
        trigger_price=9.8,
        planned_shares=100,
    )
    state.record_grid_buy(
        layer_id="g1",
        shares=200,
        price=9.0,
        trade_date=date(2026, 7, 10),
        buy_price=9.0,
        sell_price=9.8,
        target_position_pct=0.2,
        target_shares=200,
    )

    state.start_trading_day(date(2026, 7, 11))

    assert (state.total_shares, state.available_shares, state.today_bought_shares) == (300, 300, 0)
    assert (state.trend_shares, state.trend_available_shares, state.trend_today_bought_shares) == (100, 100, 0)
    assert (state.trend_batches[0].available_shares, state.trend_batches[0].today_bought_shares) == (100, 0)
    layer = state.grid_layers["g1"]
    assert (layer.held_shares, layer.available_shares, layer.today_bought_shares) == (200, 200, 0)
    assert layer.status is GridLayerStatus.HOLDING_AVAILABLE


def test_start_trading_day_does_not_release_same_day_buys() -> None:
    state = ThermostatPositionState(symbol="600001.SH")
    state.record_trend_buy(1, 100, 10.0, date(2026, 7, 10))

    state.start_trading_day(date(2026, 7, 10))

    assert state.available_shares == 0
    assert state.today_bought_shares == 100


def test_buys_update_weighted_costs_but_remain_t1_locked() -> None:
    state = ThermostatPositionState(symbol="600001.SH")
    state.record_trend_buy(1, 100, 10.0, date(2026, 7, 10))
    state.record_trend_buy(1, 100, 12.0, date(2026, 7, 10))
    state.record_grid_buy("g1", 100, 8.0, date(2026, 7, 10))

    assert state.total_shares == 300
    assert state.available_shares == 0
    assert state.today_bought_shares == 300
    assert state.average_cost == pytest.approx(10.0)
    assert state.trend_average_cost == pytest.approx(11.0)
    assert state.grid_layers["g1"].buy_cost == pytest.approx(8.0)


def test_sells_consume_only_matching_available_owner() -> None:
    state = ThermostatPositionState(symbol="600001.SH")
    state.record_trend_buy(1, 100, 10.0, date(2026, 7, 9))
    state.record_grid_buy("g1", 100, 9.0, date(2026, 7, 9))
    state.record_grid_buy("g2", 100, 8.0, date(2026, 7, 10))
    state.start_trading_day(date(2026, 7, 10))

    state.record_trend_sell(40, 12.0, date(2026, 7, 10))
    state.record_grid_sell("g1", 60, 10.0, date(2026, 7, 10))

    assert state.total_shares == 200
    assert state.available_shares == 100
    assert state.today_bought_shares == 100
    assert state.trend_shares == 60
    assert state.grid_layers["g1"].held_shares == 40
    assert state.grid_layers["g2"].held_shares == 100
    with pytest.raises(ValueError, match="available"):
        state.record_grid_sell("g2", 1, 10.0, date(2026, 7, 10))


def test_pending_priority_upgrades_never_downgrades_and_preserves_quantity() -> None:
    state = ThermostatPositionState(symbol="600001.SH")
    state.queue_pending(PendingSellLevel.PENDING_REDUCE, 100, "trend", date(2026, 7, 10))
    state.queue_pending(PendingSellLevel.PENDING_EXIT, 80, "trend", date(2026, 7, 10))
    state.queue_pending(PendingSellLevel.PENDING_REDUCE, 120, "trend", date(2026, 7, 10))

    assert state.pending_sell is not None
    assert state.pending_sell.level is PendingSellLevel.PENDING_EXIT
    assert state.pending_sell.requested_shares == 120
    assert state.pending_sell.remaining_shares == 120
    assert state.pending_sell.origin_family == "trend"


def test_pending_requests_preserve_distinct_owner_buckets_and_global_priority() -> None:
    state = ThermostatPositionState(symbol="600001.SH")
    state.queue_pending(
        PendingSellLevel.PENDING_REDUCE,
        100,
        "trend",
        date(2026, 7, 10),
        batch_index=1,
    )
    state.queue_pending(
        PendingSellLevel.PENDING_EMERGENCY_EXIT,
        60,
        "grid",
        date(2026, 7, 10),
        grid_layer_id="g1",
    )

    assert len(state.pending_sells) == 2
    assert [pending.origin_family for pending in state.pending_sells] == ["grid", "trend"]
    assert state.pending_sells[0].grid_layer_id == "g1"
    assert state.pending_sells[1].batch_index == 1
    assert state.pending_sell is state.pending_sells[0]


def test_requeue_same_pending_owner_does_not_restore_partially_sold_shares() -> None:
    state = ThermostatPositionState(symbol="600001.SH")
    state.queue_pending(PendingSellLevel.PENDING_REDUCE, 100, "trend", date(2026, 7, 10), batch_index=1)
    state.attempt_pending(date(2026, 7, 11), True, sold_shares=40)

    state.queue_pending(PendingSellLevel.PENDING_EXIT, 100, "trend", date(2026, 7, 12), batch_index=1)

    assert state.pending_sell is not None
    assert state.pending_sell.level is PendingSellLevel.PENDING_EXIT
    assert state.pending_sell.requested_shares == 100
    assert state.pending_sell.remaining_shares == 60


def test_larger_requeue_adds_only_the_increment_to_remaining_shares() -> None:
    state = ThermostatPositionState(symbol="600001.SH")
    state.queue_pending(PendingSellLevel.PENDING_REDUCE, 100, "trend", date(2026, 7, 10), batch_index=1)
    state.attempt_pending(date(2026, 7, 11), True, sold_shares=40)

    state.queue_pending(PendingSellLevel.PENDING_EXIT, 130, "trend", date(2026, 7, 12), batch_index=1)

    assert state.pending_sell is not None
    assert state.pending_sell.requested_shares == 130
    assert state.pending_sell.remaining_shares == 90


def test_pending_attempt_is_once_per_date_and_clears_only_when_fully_sold() -> None:
    pending = PendingSellState(
        level=PendingSellLevel.PENDING_EXIT,
        requested_shares=100,
        remaining_shares=100,
        origin_family="trend",
        pending_since=date(2026, 7, 10),
    )

    assert pending.attempt(date(2026, 7, 11), success=True, sold_shares=40) is False
    assert pending.remaining_shares == 60
    assert pending.attempt(date(2026, 7, 11), success=False, failure_reason="limit_down") is False
    assert pending.attempt_count == 1
    assert pending.attempt(date(2026, 7, 12), success=True, sold_shares=60) is True
    assert pending.remaining_shares == 0


def test_state_attempt_pending_retains_failures_and_clears_only_at_zero() -> None:
    state = ThermostatPositionState(symbol="600001.SH")
    state.queue_pending(PendingSellLevel.PENDING_EXIT, 100, "trend", date(2026, 7, 10))

    assert state.attempt_pending(date(2026, 7, 11), False, failure_reason="suspended") is False
    assert state.pending_sell is not None
    assert state.pending_sell.last_failure == "suspended"
    assert state.attempt_pending(date(2026, 7, 12), True, sold_shares=100) is True
    assert state.pending_sell is None


def test_attempt_pending_can_target_one_owner_bucket() -> None:
    state = ThermostatPositionState(symbol="600001.SH")
    state.queue_pending(PendingSellLevel.PENDING_EXIT, 100, "trend", date(2026, 7, 10), batch_index=1)
    state.queue_pending(PendingSellLevel.PENDING_EXIT, 80, "grid", date(2026, 7, 10), grid_layer_id="g1")

    assert state.attempt_pending(
        date(2026, 7, 11),
        True,
        sold_shares=80,
        origin_family="grid",
        grid_layer_id="g1",
    ) is True

    assert len(state.pending_sells) == 1
    assert state.pending_sell is not None
    assert state.pending_sell.origin_family == "trend"


def test_effective_exit_trigger_is_monotonic_and_ignores_invalid_values() -> None:
    state = ThermostatPositionState(symbol="600001.SH")

    assert state.update_effective_exit_trigger(9.0) == 9.0
    assert state.update_effective_exit_trigger(8.5) == 9.0
    assert state.update_effective_exit_trigger(None) == 9.0
    assert state.update_effective_exit_trigger(9.5) == 9.5


def test_boll_mid_crossing_triggers_once_and_rearms_only_above() -> None:
    state = ThermostatPositionState(symbol="600001.SH")

    assert state.observe_boll_mid(11.0, 10.0, date(2026, 7, 9)) is False
    assert state.observe_boll_mid(9.0, 10.0, date(2026, 7, 10)) is True
    assert state.observe_boll_mid(8.0, 10.0, date(2026, 7, 11)) is False
    assert state.observe_boll_mid(11.0, 10.0, date(2026, 7, 12)) is False
    assert state.observe_boll_mid(9.5, 10.0, date(2026, 7, 13)) is True


def test_range_to_trend_disables_waiting_buys_and_skips_duplicate_batch_one() -> None:
    state = ThermostatPositionState(
        symbol="600001.SH",
        current_mode="range",
        grid_layers={
            "waiting": GridLayerPosition("waiting", 9.0, 10.0, 0.1, 100),
            "owned": GridLayerPosition(
                "owned", 8.0, 9.0, 0.2, 200, held_shares=200, available_shares=200,
                buy_cost=8.0, status=GridLayerStatus.HOLDING_AVAILABLE,
            ),
        },
        total_shares=200,
        available_shares=200,
    )

    state.transition_mode("trend", current_position_ratio=0.35)

    assert state.grid_layers["waiting"].status is GridLayerStatus.DISABLED
    assert state.grid_layers["owned"].held_shares == 200
    assert state.trend_batch_index == 2
    assert state.blocked_new_buy is False


def test_trend_to_range_converts_trend_ownership_to_explicit_base_layer() -> None:
    state = ThermostatPositionState(symbol="600001.SH", current_mode="trend")
    state.record_trend_buy(1, 100, 10.0, date(2026, 7, 9))
    state.start_trading_day(date(2026, 7, 10))

    state.transition_mode("range", current_position_ratio=0.65, range_cap_ratio=0.6)

    assert state.trend_shares == 0
    assert state.grid_layers["trend_base"].held_shares == 100
    assert state.grid_layers["trend_base"].available_shares == 100
    assert state.blocked_new_buy is True
    assert state.trend_additions_stopped is True


def test_trend_to_range_preserves_t1_date_for_today_bought_base_shares() -> None:
    state = ThermostatPositionState(symbol="600001.SH", current_mode="trend")
    state.record_trend_buy(1, 100, 10.0, date(2026, 7, 10))

    state.transition_mode("range", current_position_ratio=0.2, range_cap_ratio=0.6)
    state.start_trading_day(date(2026, 7, 11))

    base = state.grid_layers["trend_base"]
    assert base.buy_date == date(2026, 7, 10)
    assert (base.available_shares, base.today_bought_shares) == (100, 0)


@pytest.mark.parametrize(
    ("mode", "risk_exit_required"),
    [("downtrend", True), ("chaotic", False), ("insufficient_data", False)],
)
def test_defensive_modes_block_buys_without_over_liquidating(mode: str, risk_exit_required: bool) -> None:
    state = ThermostatPositionState(symbol="600001.SH", last_effective_exit_trigger=9.0)
    state.queue_pending(PendingSellLevel.PENDING_REDUCE, 100, "trend", date(2026, 7, 10))

    state.transition_mode(mode)

    assert state.blocked_new_buy is True
    assert state.risk_exit_required is risk_exit_required
    assert state.pending_sell is not None
    assert state.last_effective_exit_trigger == 9.0


def test_invariants_reject_aggregate_or_owner_share_drift() -> None:
    state = ThermostatPositionState(symbol="600001.SH")
    state.record_trend_buy(1, 100, 10.0, date(2026, 7, 10))
    state.total_shares += 1

    with pytest.raises(AssertionError, match="aggregate total"):
        state.assert_invariants()


def test_owner_specific_sell_recomputes_cost_from_remaining_owners() -> None:
    state = ThermostatPositionState(symbol="600001.SH")
    state.record_trend_buy(1, 100, 10.0, date(2026, 7, 9))
    state.record_grid_buy("g1", 100, 20.0, date(2026, 7, 9))
    state.start_trading_day(date(2026, 7, 10))

    state.record_grid_sell("g1", 100, 21.0, date(2026, 7, 10))

    assert state.average_cost == pytest.approx(10.0)


def test_fifo_trend_sell_recomputes_trend_and_aggregate_cost_from_remaining_batches() -> None:
    state = ThermostatPositionState(symbol="600001.SH")
    state.record_trend_buy(1, 100, 10.0, date(2026, 7, 8))
    state.record_trend_buy(2, 100, 20.0, date(2026, 7, 8))
    state.record_grid_buy("g1", 100, 30.0, date(2026, 7, 8))
    state.start_trading_day(date(2026, 7, 9))

    state.record_trend_sell(100, 25.0, date(2026, 7, 9))

    assert state.trend_batches[0].actual_shares == 0
    assert state.trend_batches[1].actual_shares == 100
    assert state.trend_average_cost == pytest.approx(20.0)
    assert state.average_cost == pytest.approx(25.0)


def test_fifo_trend_sell_realizes_pnl_from_consumed_batch_cost() -> None:
    state = ThermostatPositionState(symbol="600001.SH")
    state.record_trend_buy(1, 100, 10.0, date(2026, 7, 8))
    state.record_trend_buy(2, 100, 20.0, date(2026, 7, 8))
    state.start_trading_day(date(2026, 7, 9))

    state.record_trend_sell(100, 25.0, date(2026, 7, 9))

    assert state.realized_pnl == pytest.approx(1500.0)


def test_batch_specific_trend_sell_consumes_only_that_batch() -> None:
    state = ThermostatPositionState(symbol="600001.SH")
    state.record_trend_buy(1, 100, 10.0, date(2026, 7, 8))
    state.record_trend_buy(2, 100, 20.0, date(2026, 7, 8))
    state.start_trading_day(date(2026, 7, 9))

    state.record_trend_sell(100, 25.0, date(2026, 7, 9), batch_index=2)

    assert state.trend_batches[0].actual_shares == 100
    assert state.trend_batches[1].actual_shares == 0
    assert state.trend_average_cost == pytest.approx(10.0)
    assert state.realized_pnl == pytest.approx(500.0)


def test_trend_to_range_migrates_pending_owner_to_grid_base() -> None:
    state = ThermostatPositionState(symbol="600001.SH", current_mode="trend")
    state.record_trend_buy(1, 100, 10.0, date(2026, 7, 9))
    state.queue_pending(
        PendingSellLevel.PENDING_EXIT,
        80,
        "trend",
        date(2026, 7, 9),
        batch_index=1,
    )
    state.attempt_pending(
        date(2026, 7, 9),
        False,
        failure_reason="limit_down",
        origin_family="trend",
        batch_index=1,
    )

    state.transition_mode("range", current_position_ratio=0.2, range_cap_ratio=0.6)

    assert len(state.pending_sells) == 1
    pending = state.pending_sell
    assert pending is not None
    assert pending.origin_family == "grid"
    assert pending.grid_layer_id == "trend_base"
    assert pending.batch_index is None
    assert pending.level is PendingSellLevel.PENDING_EXIT
    assert (pending.requested_shares, pending.remaining_shares) == (80, 80)
    assert pending.attempt_count == 1
    assert pending.last_attempt_date == date(2026, 7, 9)
    assert pending.last_failure == "limit_down"
    assert state.attempt_pending(
        date(2026, 7, 10),
        True,
        sold_shares=80,
        origin_family="grid",
        grid_layer_id="trend_base",
    ) is True
    assert state.pending_sell is None


@pytest.mark.parametrize("owner", ["grid", "batch"])
def test_invariants_reject_negative_child_owner_counters(owner: str) -> None:
    state = ThermostatPositionState(symbol="600001.SH")
    if owner == "grid":
        state.record_grid_buy("g1", 100, 10.0, date(2026, 7, 10))
        state.grid_layers["g1"].held_shares = -1
        state.grid_layers["g1"].today_bought_shares = -1
    else:
        state.record_trend_buy(1, 100, 10.0, date(2026, 7, 10))
        state.trend_batches[0].actual_shares = -1
        state.trend_batches[0].today_bought_shares = -1

    with pytest.raises(AssertionError, match="cannot be negative"):
        state.assert_invariants()
