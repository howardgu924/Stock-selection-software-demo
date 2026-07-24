from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from stock_picker.strategies.adaptive_trend_v1_3 import (
    ExecutionType,
    ExitIntent,
    FillResult,
    FillSide,
    FillStatus,
    PendingSellStatus,
    apply_pending_fill_result,
    create_or_merge_pending,
    initial_pending_attempt,
    next_pending_attempt,
    revalidate_pending,
)


DAY = date(2025, 1, 6)
CALENDAR = ["2025-01-06", "2025-01-07", "2025-01-09"]


def _ts(value: str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="Asia/Shanghai")


def _intent(
    reason="MA20_BREAK_REDUCTION",
    priority=45,
    execution_type=ExecutionType.ORDINARY_REDUCTION,
    *,
    qty=300,
    full=False,
    sticky=False,
    revalidate=True,
    episode="episode-1",
):
    return ExitIntent(
        symbol="600001.SH",
        decision_trade_date=DAY,
        decision_time="14:30",
        execution_type=execution_type,
        reason=reason,
        priority=priority,
        requested_target_qty=qty,
        full_exit=full,
        sticky=sticky,
        requires_revalidation=revalidate,
        episode_id=episode,
        trigger_bar_start=_ts("2025-01-06 14:25"),
        trigger_price=Decimal("10"),
        active_stop=None,
        created_at=_ts("2025-01-06 14:30"),
        reasons=(reason,),
    )


def _pending(intent=None, remaining=300):
    intent = intent or _intent()
    return create_or_merge_pending(
        None,
        intent,
        total_qty=500,
        remaining_qty=remaining,
        next_attempt_at="2025-01-06 14:35",
    ).new_state


def _fill(
    status,
    *,
    retryable=False,
    filled=0,
    reason="",
    side=FillSide.SELL,
    execution_type=ExecutionType.ORDINARY_REDUCTION,
    symbol="600001",
    requested=300,
    trade_date="2025-01-06",
    bar_start="2025-01-06 14:35:00+08:00",
):
    zero = Decimal("0.00")
    return FillResult(
        status=status,
        side=side,
        execution_type=execution_type,
        symbol=symbol,
        requested_qty=requested,
        filled_qty=filled,
        execution_trade_date=trade_date,
        execution_bar_start=bar_start,
        execution_price=Decimal("10") if status == FillStatus.FILLED else None,
        gross_amount=Decimal("3000") if status == FillStatus.FILLED else zero,
        commission=zero,
        stamp_tax=zero,
        transfer_fee=zero,
        settlement_fee=zero,
        total_fees=zero,
        cash_required=zero,
        net_proceeds=Decimal("3000") if status == FillStatus.FILLED else zero,
        failure_reason=reason,
        retryable=retryable,
    )


def test_create_normalizes_symbol_and_is_immutable():
    pending = _pending()
    assert pending.symbol == "600001.SH"
    assert pending.status == PendingSellStatus.ACTIVE
    with pytest.raises(FrozenInstanceError):
        pending.remaining_qty = 1


def test_duplicate_episode_does_not_accumulate_quantity():
    pending = _pending()
    result = create_or_merge_pending(
        pending, _intent(qty=500), total_qty=500, remaining_qty=500,
        next_attempt_at="2025-01-06 14:35",
    )
    assert result.status == "UNCHANGED"
    assert result.new_state is pending
    assert result.new_state.remaining_qty == 300


def test_higher_priority_upgrades_single_pending():
    pending = _pending()
    hard = _intent("INITIAL_STOP", 90, ExecutionType.HARD_EXIT, qty=500,
                   full=True, sticky=True, revalidate=False, episode="stop")
    result = create_or_merge_pending(
        pending, hard, total_qty=500, remaining_qty=500,
        next_attempt_at="2025-01-06 14:40",
    )
    assert result.new_state.reason == "INITIAL_STOP"
    assert result.new_state.remaining_qty == 500
    assert result.new_state.sticky is True


def test_lower_priority_cannot_replace_pending():
    high = _pending(_intent("STRONG_TOP_DIVERGENCE", 70, full=True))
    result = create_or_merge_pending(
        high, _intent("REPLACEMENT_EXIT", 30, full=True), total_qty=500,
        remaining_qty=500, next_attempt_at="2025-01-06 14:35",
    )
    assert result.status == "UNCHANGED"
    assert result.new_state.reason == "STRONG_TOP_DIVERGENCE"


@pytest.mark.parametrize("total,remaining", [(-1, 0), (1, -1), (1, 2), (True, 1)])
def test_invalid_pending_quantities_are_stable(total, remaining):
    result = create_or_merge_pending(
        None, _intent(), total_qty=total, remaining_qty=remaining,
        next_attempt_at="2025-01-06 14:35",
    )
    assert (result.status, result.failure_reason) == ("INVALID", "invalid_pending_input")


def test_filled_result_completes_pending():
    pending = _pending()
    result = apply_pending_fill_result(
        pending, _fill(FillStatus.FILLED, filled=300), position_qty_after_fill=200,
        attempt_at="2025-01-06 14:35", trading_calendar=CALENDAR,
    )
    assert result.new_state.status == PendingSellStatus.COMPLETED
    assert result.new_state.remaining_qty == 0


def test_retryable_hard_failure_uses_next_legal_bar():
    pending = _pending(_intent("TRAILING_STOP", 85, ExecutionType.HARD_EXIT,
                               sticky=True, revalidate=False))
    result = apply_pending_fill_result(
        pending, _fill(
            FillStatus.FAILED,
            retryable=True,
            reason="limit_down",
            execution_type=ExecutionType.HARD_EXIT,
            bar_start="2025-01-06 11:25:00+08:00",
        ),
        position_qty_after_fill=500, attempt_at="2025-01-06 11:25",
        trading_calendar=CALENDAR,
    )
    assert result.new_state.status == PendingSellStatus.ACTIVE
    assert result.new_state.next_attempt_at == _ts("2025-01-06 13:00")
    assert result.new_state.retry_count == 1


def test_retryable_soft_failure_moves_to_next_actual_day_revalidation():
    pending = _pending()
    result = apply_pending_fill_result(
        pending, _fill(FillStatus.FAILED, retryable=True, reason="limit_down"),
        position_qty_after_fill=500, attempt_at="2025-01-06 14:35",
        trading_calendar=CALENDAR,
    )
    assert result.new_state.next_attempt_at == _ts("2025-01-07 14:30")


def test_nonretryable_failure_enters_error():
    pending = _pending()
    result = apply_pending_fill_result(
        pending, _fill(FillStatus.FAILED, reason="invalid_rule"),
        position_qty_after_fill=500, attempt_at="2025-01-06 14:35",
        trading_calendar=CALENDAR,
    )
    assert result.new_state.status == PendingSellStatus.ERROR
    assert result.new_state.last_failure == "invalid_rule"


def test_nonsticky_invalidated_signal_is_cancelled():
    pending = _pending()
    result = revalidate_pending(
        pending, signal_valid=False, position_qty=500,
        evaluated_at="2025-01-07 14:30",
    )
    assert result.new_state.status == PendingSellStatus.CANCELLED
    assert result.new_state.cancelled_reason == "signal_no_longer_valid"


def test_sticky_pending_survives_signal_revalidation():
    pending = _pending(_intent("INITIAL_STOP", 90, ExecutionType.HARD_EXIT,
                               sticky=True, revalidate=False))
    result = revalidate_pending(
        pending, signal_valid=False, position_qty=500,
        evaluated_at="2025-01-07 14:30",
    )
    assert result.status == "UNCHANGED"
    assert result.new_state is pending


def test_closed_position_completes_pending_without_fill():
    pending = _pending()
    result = revalidate_pending(
        pending, signal_valid=True, position_qty=0,
        evaluated_at="2025-01-07 14:30",
    )
    assert result.new_state.status == PendingSellStatus.COMPLETED
    assert result.new_state.remaining_qty == 0


def test_hard_next_attempt_1455_uses_actual_calendar():
    pending = _pending(_intent("INITIAL_STOP", 90, ExecutionType.HARD_EXIT))
    assert next_pending_attempt(pending, "2025-01-07 14:55", CALENDAR) == _ts("2025-01-09 09:30")


def test_initial_t1_pending_attempt_never_uses_buy_day_for_hard_exit():
    hard = _intent("INITIAL_STOP", 90, ExecutionType.HARD_EXIT,
                   full=True, sticky=True, revalidate=False)
    assert initial_pending_attempt(hard, CALENDAR) == _ts("2025-01-07 09:30")


def test_initial_soft_pending_attempt_is_same_day_1435():
    assert initial_pending_attempt(_intent(), CALENDAR) == _ts("2025-01-06 14:35")


@pytest.mark.parametrize(
    "changes",
    [
        {"side": FillSide.BUY, "execution_type": ExecutionType.ENTRY_BUY},
        {"execution_type": ExecutionType.SOFT_EXIT},
        {"symbol": "600002"},
        {"filled": 301, "requested": 301},
        {"filled": 300, "requested": 299},
    ],
)
def test_fill_contract_mismatch_moves_pending_to_error(changes):
    pending = _pending()
    fill = _fill(FillStatus.FILLED, **changes)
    result = apply_pending_fill_result(
        pending,
        fill,
        position_qty_after_fill=200,
        attempt_at="2025-01-06 14:35",
        trading_calendar=CALENDAR,
    )
    assert result.new_state.status == PendingSellStatus.ERROR
    assert result.new_state.remaining_qty == 300
    assert result.new_state.retry_count == 0
    assert result.new_state.last_failure == "fill_contract_mismatch"
    assert pending.status == PendingSellStatus.ACTIVE
    assert pending.remaining_qty == 300


def test_forged_priority_is_rejected_before_pending_merge():
    forged = _intent("REPLACEMENT_EXIT", 999, full=True)
    result = create_or_merge_pending(
        None,
        forged,
        total_qty=500,
        remaining_qty=500,
        next_attempt_at="2025-01-06 14:35",
    )
    assert (result.status, result.failure_reason) == (
        "INVALID",
        "invalid_exit_priority",
    )


def test_same_retryable_attempt_is_applied_once():
    pending = _pending()
    fill = _fill(FillStatus.FAILED, retryable=True, reason="limit_down")
    first = apply_pending_fill_result(
        pending,
        fill,
        position_qty_after_fill=500,
        attempt_at="2025-01-06 14:35",
        trading_calendar=CALENDAR,
    )
    second = apply_pending_fill_result(
        first.new_state,
        fill,
        position_qty_after_fill=500,
        attempt_at="2025-01-06 14:35",
        trading_calendar=CALENDAR,
    )
    assert first.new_state.retry_count == 1
    assert second.status == "UNCHANGED"
    assert second.new_state.retry_count == 1
    assert second.new_state.next_attempt_at == first.new_state.next_attempt_at


@pytest.mark.parametrize("status", [FillStatus.FILLED, FillStatus.INVALID])
def test_same_filled_or_invalid_attempt_is_idempotent(status):
    pending = _pending()
    fill = _fill(
        status,
        filled=300 if status == FillStatus.FILLED else 0,
        reason="invalid_rule" if status == FillStatus.INVALID else "",
    )
    first = apply_pending_fill_result(
        pending,
        fill,
        position_qty_after_fill=200,
        attempt_at="2025-01-06 14:35",
        trading_calendar=CALENDAR,
    )
    second = apply_pending_fill_result(
        first.new_state,
        fill,
        position_qty_after_fill=200,
        attempt_at="2025-01-06 14:35",
        trading_calendar=CALENDAR,
    )
    assert second.status == "UNCHANGED"
    assert second.new_state == first.new_state


def test_stale_attempt_does_not_change_pending():
    pending = _pending()
    later = _fill(
        FillStatus.FAILED,
        retryable=True,
        reason="limit_down",
        trade_date="2025-01-07",
        bar_start="2025-01-07 14:35:00+08:00",
    )
    updated = apply_pending_fill_result(
        pending,
        later,
        position_qty_after_fill=500,
        attempt_at="2025-01-07 14:35",
        trading_calendar=CALENDAR,
    ).new_state
    stale = apply_pending_fill_result(
        updated,
        _fill(FillStatus.FAILED, retryable=True, reason="limit_down"),
        position_qty_after_fill=500,
        attempt_at="2025-01-06 14:35",
        trading_calendar=CALENDAR,
    )
    assert stale.status == "UNCHANGED"
    assert stale.new_state == updated
