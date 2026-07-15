from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from stock_picker.strategies.adaptive_trend_v1_3.phase3_models import (
    ExecutionType,
    FillResult,
    FillSide,
    FillStatus,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase4_models import (
    PositionStatus,
    TransitionStatus,
)
from stock_picker.strategies.adaptive_trend_v1_3.position_state import (
    apply_buy_fill,
    apply_sell_fill,
    empty_position_state,
    unlock_position_state,
)


def _fill(side: FillSide, day: str, qty: int, price: str, fees: str = "5") -> FillResult:
    gross = Decimal(price) * Decimal(qty)
    total_fees = Decimal(fees)
    buy = side == FillSide.BUY
    return FillResult(
        status=FillStatus.FILLED, side=side,
        execution_type=ExecutionType.ENTRY_BUY if buy else ExecutionType.SOFT_EXIT,
        symbol="600001.SH", requested_qty=qty, filled_qty=qty,
        execution_trade_date=day, execution_bar_start=f"{day}T10:05:00+08:00",
        execution_price=Decimal(price), gross_amount=gross,
        commission=total_fees, stamp_tax=Decimal("0"), transfer_fee=Decimal("0"),
        settlement_fee=Decimal("0"), total_fees=total_fees,
        cash_required=gross + total_fees if buy else Decimal("0"),
        net_proceeds=gross - total_fees if not buy else Decimal("0"),
        failure_reason="", retryable=False,
    )


def _failed_buy() -> FillResult:
    return replace(
        _fill(FillSide.BUY, "2025-01-03", 100, "10"),
        status=FillStatus.FAILED, filled_qty=0, execution_price=None,
        gross_amount=Decimal("0"), total_fees=Decimal("0"),
        cash_required=Decimal("0"), failure_reason="limit_up_buy",
    )


def test_buy_today_not_sellable_and_fees_enter_cost() -> None:
    state = empty_position_state("600001.SH")
    result = apply_buy_fill(
        state, _fill(FillSide.BUY, "2025-01-03", 100, "10"),
        entry_atr="1", trading_calendar=["2025-01-03", "2025-01-06"],
    )
    new = result.new_state
    assert result.status == TransitionStatus.APPLIED
    assert new.total_qty == 100
    assert new.sellable_qty == 0
    assert new.today_bought_qty == 100
    assert new.cost_basis == Decimal("1005")
    assert new.average_cost == Decimal("10.05")
    assert new.lots[0].unlock_trade_date.isoformat() == "2025-01-06"
    assert state == empty_position_state("600001.SH")


def test_friday_unlock_uses_next_actual_calendar_day() -> None:
    bought = apply_buy_fill(
        empty_position_state("600001.SH"),
        _fill(FillSide.BUY, "2025-01-03", 100, "10"), entry_atr="1",
        trading_calendar=["2025-01-03", "2025-01-07"],
    ).new_state
    unlocked = unlock_position_state(
        bought, "2025-01-07", ["2025-01-03", "2025-01-07"]
    )
    assert unlocked.new_state.sellable_qty == 100
    assert unlocked.new_state.today_bought_qty == 0


def test_sell_before_unlock_or_over_sellable_is_invalid_and_immutable() -> None:
    bought = apply_buy_fill(
        empty_position_state("600001.SH"),
        _fill(FillSide.BUY, "2025-01-03", 100, "10"), entry_atr="1",
        trading_calendar=["2025-01-03", "2025-01-06"],
    ).new_state
    result = apply_sell_fill(bought, _fill(FillSide.SELL, "2025-01-03", 100, "11"))
    assert result.status == TransitionStatus.INVALID
    assert result.failure_reason == "insufficient_sellable_qty"
    assert result.new_state is bought


def test_fifo_partial_sell_cost_basis_average_cost_and_pnl() -> None:
    calendar = ["2025-01-02", "2025-01-03", "2025-01-06"]
    first = apply_buy_fill(
        empty_position_state("600001.SH"),
        _fill(FillSide.BUY, "2025-01-02", 100, "10"), entry_atr="1",
        trading_calendar=calendar,
    ).new_state
    day2 = unlock_position_state(first, "2025-01-03", calendar).new_state
    second = apply_buy_fill(
        day2, _fill(FillSide.BUY, "2025-01-03", 100, "20"), entry_atr="2",
        trading_calendar=calendar,
    ).new_state
    day3 = unlock_position_state(second, "2025-01-06", calendar).new_state
    sold = apply_sell_fill(day3, _fill(FillSide.SELL, "2025-01-06", 150, "20"))

    new = sold.new_state
    assert sold.status == TransitionStatus.APPLIED
    assert [lot.remaining_qty for lot in new.lots] == [50]
    assert new.total_qty == new.sellable_qty == 50
    assert new.cost_basis == Decimal("1002.5")
    assert new.average_cost == Decimal("20.05")
    assert sold.realized_pnl_delta == Decimal("987.5")
    assert new.realized_pnl == Decimal("987.5")


def test_full_exit_clears_position_state() -> None:
    calendar = ["2025-01-02", "2025-01-03"]
    bought = apply_buy_fill(
        empty_position_state("600001.SH"),
        _fill(FillSide.BUY, "2025-01-02", 100, "10"), entry_atr="1",
        trading_calendar=calendar,
    ).new_state
    unlocked = unlock_position_state(bought, "2025-01-03", calendar).new_state
    closed = apply_sell_fill(unlocked, _fill(FillSide.SELL, "2025-01-03", 100, "11"))
    state = closed.new_state
    assert state.status == PositionStatus.CLOSED
    assert state.total_qty == state.sellable_qty == state.today_bought_qty == 0
    assert state.cost_basis == state.average_cost == Decimal("0")
    assert state.lots == ()
    assert state.entry_trade_date is state.entry_price is state.entry_atr is None


def test_nonfilled_result_and_inconsistent_lot_summary_do_not_update() -> None:
    empty = empty_position_state("600001.SH")
    nonfilled = apply_buy_fill(
        empty, _failed_buy(), entry_atr="1",
        trading_calendar=["2025-01-03", "2025-01-06"],
    )
    assert nonfilled.status == TransitionStatus.INVALID
    assert nonfilled.new_state is empty

    bought = apply_buy_fill(
        empty, _fill(FillSide.BUY, "2025-01-03", 100, "10"), entry_atr="1",
        trading_calendar=["2025-01-03", "2025-01-06"],
    ).new_state
    broken = replace(bought, total_qty=101)
    result = unlock_position_state(
        broken, "2025-01-06", ["2025-01-03", "2025-01-06"]
    )
    assert result.status == TransitionStatus.INVALID
    assert result.failure_reason == "inconsistent_position_state"
    assert result.new_state is broken

    broken_sellable = replace(bought, sellable_qty=1)
    result = unlock_position_state(
        broken_sellable, "2025-01-06", ["2025-01-03", "2025-01-06"]
    )
    assert result.status == TransitionStatus.INVALID
    assert result.failure_reason == "inconsistent_position_state"


def test_trade_date_regression_is_invalid() -> None:
    state = replace(empty_position_state("600001.SH"), current_trade_date=__import__("datetime").date(2025, 1, 6))
    result = unlock_position_state(state, "2025-01-03", ["2025-01-03", "2025-01-06"])
    assert result.status == TransitionStatus.INVALID
    assert result.failure_reason == "state_date_regression"


@pytest.mark.parametrize(
    "requested,filled",
    [
        (True, True),
        ("100", "100"),
        (Decimal("100"), Decimal("100")),
        (100.0, 100.0),
        (100, 99),
    ],
)
@pytest.mark.parametrize("side", [FillSide.BUY, FillSide.SELL])
def test_fill_quantities_must_be_equal_strict_positive_integers(
    requested, filled, side: FillSide
) -> None:
    fill = replace(
        _fill(side, "2025-01-03", 100, "10"),
        requested_qty=requested,
        filled_qty=filled,
    )
    state = empty_position_state("600001.SH")
    if side == FillSide.BUY:
        result = apply_buy_fill(
            state,
            fill,
            entry_atr="1",
            trading_calendar=["2025-01-03", "2025-01-06"],
        )
    else:
        result = apply_sell_fill(state, fill)
    assert result.status == TransitionStatus.INVALID
    assert result.failure_reason == "invalid_filled_qty"
    assert result.new_state is state


def test_state_transition_contract_explicitly_assigns_replay_to_caller() -> None:
    for function in (apply_buy_fill, apply_sell_fill):
        assert "non-idempotent" in function.__doc__
        assert "deduplicate" in function.__doc__
        assert "apply each fill exactly once" in function.__doc__
        assert "Phase 5" in function.__doc__
        assert "unique key" in function.__doc__

    fill = _fill(FillSide.BUY, "2025-01-03", 100, "10")
    first = apply_buy_fill(
        empty_position_state("600001.SH"), fill, entry_atr="1",
        trading_calendar=["2025-01-03", "2025-01-06"],
    )
    replay = apply_buy_fill(
        first.new_state, fill, entry_atr="1",
        trading_calendar=["2025-01-03", "2025-01-06"],
    )
    assert replay.status == TransitionStatus.APPLIED
    assert replay.new_state.total_qty == 200
