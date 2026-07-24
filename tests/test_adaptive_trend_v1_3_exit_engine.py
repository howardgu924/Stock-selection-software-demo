from dataclasses import replace
from decimal import Decimal

import pandas as pd
import pytest

from stock_picker.strategies.adaptive_trend_v1_3.exit_control_state import (
    initialize_exit_control,
    mark_episode_acted,
)
from stock_picker.strategies.adaptive_trend_v1_3.exit_engine import (
    build_exit_fill_request,
    evaluate_1430_exit,
    evaluate_hard_exit,
    reduction_quantity,
    select_highest_intent,
)
from stock_picker.strategies.adaptive_trend_v1_3.fill_engine import execute_fill
from stock_picker.strategies.adaptive_trend_v1_3.phase3_models import (
    ExecutionType,
    FeeRuleSnapshot,
    FillStatus,
    TradingRuleSnapshot,
)
from stock_picker.strategies.adaptive_trend_v1_3.position_state import empty_position_state


def _control():
    return initialize_exit_control(
        symbol="600001", entry_trade_date="2025-01-02", entry_price="10",
        effective_risk_pct="0.08", price_basis_id="RAW",
    ).new_state


def _position(total=1000, sellable=1000):
    return replace(
        empty_position_state("600001.SH"), total_qty=total, sellable_qty=sellable,
        today_bought_qty=total - sellable,
    )


def _soft(position=None, control=None, **overrides):
    values = dict(
        position=position or _position(), control=control or _control(),
        decision_trade_date="2025-01-08", p1430="10", previous_ma20="9.5",
        previous_ma60="9", ma20_slope5="0.1", opportunity_status="VALID",
        opportunity_score="80", entry_threshold="60", strong_top_divergence=False,
        normal_top_divergence=False, divergence_episode_id="", partial_sell_lot_size=100,
        protected=False, market_data_valid=True,
    )
    values.update(overrides)
    return evaluate_1430_exit(**values)


def test_emergency_has_priority_over_both_stops() -> None:
    control = replace(_control(), trailing_stop=Decimal("9.5"))
    result = evaluate_hard_exit(
        _position(), control, trigger_bar_start="2025-01-08 10:00",
        completed_bar_low="9", emergency_status="LEVEL_2", price_basis_id="RAW",
    )
    assert result.selected_intent.reason == "EMERGENCY_MARKET"
    assert result.selected_intent.sticky is True
    assert result.all_triggered_reasons == (
        "EMERGENCY_MARKET", "INITIAL_STOP", "TRAILING_STOP"
    )


def test_initial_and_trailing_stop_selection() -> None:
    initial = evaluate_hard_exit(
        _position(), _control(), trigger_bar_start="2025-01-08 10:00",
        completed_bar_low="9.1", emergency_status="NORMAL", price_basis_id="RAW",
    )
    trailing_control = replace(_control(), trailing_stop=Decimal("9.6"))
    trailing = evaluate_hard_exit(
        _position(), trailing_control, trigger_bar_start="2025-01-08 10:00",
        completed_bar_low="9.1", emergency_status="NORMAL", price_basis_id="RAW",
    )
    assert initial.selected_intent.reason == "INITIAL_STOP"
    assert trailing.selected_intent.reason == "TRAILING_STOP"
    assert trailing.active_stop == Decimal("9.6")


def test_hard_exit_t1_split_never_requests_unsellable_quantity() -> None:
    result = evaluate_hard_exit(
        _position(1000, 0), _control(), trigger_bar_start="2025-01-02 10:00",
        completed_bar_low="9", emergency_status="NORMAL", price_basis_id="RAW",
    )
    assert result.executable_qty == 0
    assert result.pending_remaining_qty == 1000
    assert result.unsellable_qty == 1000


def test_hard_exit_uses_next_bar_open_for_jump_and_lunch_crossing() -> None:
    decision = evaluate_hard_exit(
        _position(100, 100), _control(), trigger_bar_start="2025-01-08 11:25",
        completed_bar_low="9", emergency_status="NORMAL", price_basis_id="RAW",
    )
    request = build_exit_fill_request(
        decision.selected_intent, executable_qty=100, position_qty=100, sellable_qty=100
    )
    bars = pd.DataFrame([{
        "symbol": "600001.SH", "trade_date": "2025-01-08",
        "bar_start": "2025-01-08 13:00", "open": "8", "high": "8.2",
        "low": "7.8", "close": "8", "volume": "1", "trade_status": "normal",
        "limit_status": "normal",
    }])
    rule = TradingRuleSnapshot("SSE", "MAIN", "STOCK", "2025-01-08", 100, 100, True, Decimal("0.01"))
    fees = FeeRuleSnapshot("2025-01-08", *(Decimal("0") for _ in range(7)))
    fill = execute_fill(request, bars, rule, fees, trading_calendar=["2025-01-08"])
    assert fill.status == FillStatus.FILLED
    assert fill.execution_bar_start.endswith("13:00:00+08:00")
    assert fill.execution_price == Decimal("8")
    assert fill.execution_price != decision.active_stop


def test_hard_exit_1455_crosses_to_next_actual_trade_day() -> None:
    result = evaluate_hard_exit(
        _position(), _control(), trigger_bar_start="2025-01-10 14:55",
        completed_bar_low="9", emergency_status="NORMAL", price_basis_id="RAW",
    )
    request = build_exit_fill_request(
        result.selected_intent, executable_qty=1000, position_qty=1000, sellable_qty=1000
    )
    from stock_picker.strategies.adaptive_trend_v1_3.minute_contract import resolve_next_execution_bar
    target = resolve_next_execution_bar(request.signal_time, ["2025-01-10", "2025-01-14"])
    assert target.execution_bar_start.isoformat().endswith("2025-01-14T09:30:00+08:00")


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"strong_top_divergence": True}, "STRONG_TOP_DIVERGENCE"),
        ({"p1430": "8", "previous_ma20": "9", "previous_ma60": "9.5"}, "MA60_TREND_BREAK"),
    ],
)
def test_soft_full_exit_reasons(overrides, reason) -> None:
    result = _soft(**overrides)
    assert result.selected_intent.reason == reason
    assert result.selected_intent.execution_type.value == "SOFT_EXIT"
    assert result.selected_intent.trigger_bar_start.hour == 14
    assert result.selected_intent.trigger_bar_start.minute == 25


def test_weak_score_two_days_invalid_freeze_and_same_day_idempotency() -> None:
    first = _soft(decision_trade_date="2025-01-08", opportunity_score="47")
    assert first.status.value == "NO_ACTION"
    assert first.new_control_state.weak_score_streak == 1
    repeated = _soft(
        control=first.new_control_state, decision_trade_date="2025-01-08",
        opportunity_score="47",
    )
    assert repeated.new_control_state.weak_score_streak == 1
    invalid = _soft(
        control=first.new_control_state, decision_trade_date="2025-01-09",
        opportunity_status="INVALID", opportunity_score="0",
    )
    assert invalid.new_control_state.weak_score_streak == 1
    second = _soft(
        control=invalid.new_control_state, decision_trade_date="2025-01-10",
        opportunity_score="47",
    )
    assert second.selected_intent.reason == "WEAK_SCORE_CONFIRMED"


def test_protection_blocks_soft_exit_but_not_hard_exit() -> None:
    soft = _soft(strong_top_divergence=True, protected=True)
    assert soft.selected_intent is None
    hard = evaluate_hard_exit(
        _position(1000, 0), _control(), trigger_bar_start="2025-01-02 10:00",
        completed_bar_low="9", emergency_status="NORMAL", price_basis_id="RAW",
    )
    assert hard.selected_intent.reason == "INITIAL_STOP"


def test_normal_divergence_episode_once_only_after_acted() -> None:
    first = _soft(normal_top_divergence=True, divergence_episode_id="DIV:P1")
    assert first.selected_intent.requested_target_qty == 500
    assert first.selected_intent.episode_id == "DIV:P1"
    not_filled = _soft(
        control=first.new_control_state, decision_trade_date="2025-01-09",
        normal_top_divergence=True, divergence_episode_id="DIV:P1",
    )
    assert not_filled.selected_intent is not None
    acted = mark_episode_acted(first.new_control_state, "DIV:P1")
    repeated = _soft(
        control=acted, decision_trade_date="2025-01-09",
        normal_top_divergence=True, divergence_episode_id="DIV:P1",
    )
    assert repeated.selected_intent is None
    new_episode = _soft(
        control=acted, decision_trade_date="2025-01-10",
        normal_top_divergence=True, divergence_episode_id="DIV:P2",
    )
    assert new_episode.selected_intent.episode_id == "DIV:P2"


def test_ma20_episode_two_valid_recovery_days_and_invalid_freeze() -> None:
    triggered = _soft(p1430="9", previous_ma20="10", ma20_slope5="0")
    episode = triggered.new_control_state.ma20_episode_id
    assert episode == "MA20_BREAK:2025-01-08"
    acted = mark_episode_acted(triggered.new_control_state, episode)
    invalid = _soft(
        control=acted, decision_trade_date="2025-01-09", market_data_valid=False,
        p1430="NaN",
    )
    assert invalid.new_control_state.ma20_recovery_count == 0
    day1 = _soft(control=acted, decision_trade_date="2025-01-10", p1430="10")
    assert day1.new_control_state.ma20_recovery_count == 1
    day2 = _soft(control=day1.new_control_state, decision_trade_date="2025-01-13", p1430="10")
    assert day2.new_control_state.ma20_episode_id == ""


@pytest.mark.parametrize("total,lot,expected", [(1000, 100, 500), (199, 100, 0), (201, 100, 100)])
def test_reduction_quantity_never_exceeds_half(total, lot, expected) -> None:
    assert reduction_quantity(total, lot) == expected


def test_same_priority_selection_is_input_order_independent() -> None:
    strong = _soft(strong_top_divergence=True).selected_intent
    other = replace(strong, reason="OTHER", priority=strong.priority)
    assert select_highest_intent([other, strong]) == strong
    assert select_highest_intent([strong, other]) == strong


def test_invalid_opportunity_score_freezes_streak_without_blocking_other_exit() -> None:
    result = _soft(
        opportunity_status="INVALID", opportunity_score="NaN",
        strong_top_divergence=True,
    )
    assert result.selected_intent.reason == "STRONG_TOP_DIVERGENCE"
    assert result.new_control_state.weak_score_streak == 0


def test_emergency_result_object_is_accepted_at_phase2_boundary() -> None:
    class EmergencyResult:
        emergency_status = "LEVEL_1"

    result = evaluate_hard_exit(
        _position(), _control(), trigger_bar_start="2025-01-08 10:00",
        completed_bar_low="10", emergency_status=EmergencyResult(), price_basis_id="RAW",
    )
    assert result.selected_intent.reason == "EMERGENCY_MARKET"


def test_fill_request_cannot_exceed_intent_target() -> None:
    intent = _soft(
        normal_top_divergence=True, divergence_episode_id="DIV:LIMIT"
    ).selected_intent
    assert intent.requested_target_qty == 500
    with pytest.raises(ValueError, match="invalid_exit_fill_quantity"):
        build_exit_fill_request(
            intent,
            executable_qty=1000,
            position_qty=1000,
            sellable_qty=1000,
        )


def test_partial_full_exit_request_is_allowed() -> None:
    intent = _soft(strong_top_divergence=True).selected_intent
    request = build_exit_fill_request(
        intent,
        executable_qty=500,
        position_qty=1000,
        sellable_qty=500,
    )
    assert request.requested_qty == 500


@pytest.mark.parametrize("value", [True, 1000.0, "1000"])
def test_fill_request_rejects_non_integer_quantity(value) -> None:
    intent = _soft(strong_top_divergence=True).selected_intent
    with pytest.raises(ValueError, match="invalid_exit_fill_quantity"):
        build_exit_fill_request(
            intent,
            executable_qty=value,
            position_qty=1000,
            sellable_qty=1000,
        )


def test_fill_request_rejects_buy_execution_type() -> None:
    intent = replace(
        _soft(strong_top_divergence=True).selected_intent,
        execution_type=ExecutionType.ENTRY_BUY,
    )
    with pytest.raises(ValueError, match="invalid_exit_execution_type"):
        build_exit_fill_request(
            intent,
            executable_qty=100,
            position_qty=1000,
            sellable_qty=1000,
        )


def test_forged_priority_cannot_change_selection() -> None:
    strong = _soft(strong_top_divergence=True).selected_intent
    forged = replace(strong, reason="REPLACEMENT_EXIT", priority=999)
    assert select_highest_intent([forged, strong]) == strong
    assert select_highest_intent([forged]) is None


def test_invalid_market_data_does_not_trigger_ma_exits() -> None:
    ma60 = _soft(
        market_data_valid=False,
        p1430="8",
        previous_ma20="9",
        previous_ma60="9.5",
    )
    ma20 = _soft(
        market_data_valid=False,
        p1430="8",
        previous_ma20="9",
        previous_ma60="7",
        ma20_slope5="-0.1",
    )
    assert ma60.selected_intent is None
    assert ma20.selected_intent is None
    assert ma20.new_control_state.ma20_episode_id == ""


def test_invalid_market_data_still_allows_independent_signals() -> None:
    first = _soft(
        decision_trade_date="2025-01-08",
        market_data_valid=False,
        p1430="NaN",
        previous_ma20="NaN",
        previous_ma60="NaN",
        ma20_slope5="NaN",
        opportunity_score="47",
    )
    second = _soft(
        control=first.new_control_state,
        decision_trade_date="2025-01-09",
        market_data_valid=False,
        p1430="NaN",
        previous_ma20="NaN",
        previous_ma60="NaN",
        ma20_slope5="NaN",
        opportunity_score="47",
    )
    divergence = _soft(
        market_data_valid=False,
        p1430="NaN",
        previous_ma20="NaN",
        previous_ma60="NaN",
        ma20_slope5="NaN",
        strong_top_divergence=True,
    )
    assert second.selected_intent.reason == "WEAK_SCORE_CONFIRMED"
    assert divergence.selected_intent.reason == "STRONG_TOP_DIVERGENCE"
