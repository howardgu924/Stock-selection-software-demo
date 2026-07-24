from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from stock_picker.strategies.adaptive_trend_v1_3.exit_control_state import (
    in_soft_exit_protection,
    initialize_exit_control,
    mark_episode_acted,
    update_trailing_stop,
)


def _state():
    return initialize_exit_control(
        symbol="600001", entry_trade_date="2025-01-03", entry_price="10",
        effective_risk_pct="0.08", price_basis_id="RAW_V1",
    ).new_state


def test_initial_stop_formula_and_normalized_symbol() -> None:
    result = initialize_exit_control(
        symbol="600001", entry_trade_date="2025-01-03", entry_price="10",
        effective_risk_pct="0.08", price_basis_id="RAW_V1",
    )
    assert result.status == "APPLIED"
    assert result.new_state.symbol == "600001.SH"
    assert result.new_state.initial_stop == Decimal("9.20")
    assert result.new_state.trailing_stop == Decimal("9.20")
    assert result.new_state.highest_close == Decimal("10")


@pytest.mark.parametrize("price,risk", [(0, "0.1"), ("10", 0.1), ("10", "0"), ("10", "1.1")])
def test_initial_stop_rejects_invalid_decimal_contract(price, risk) -> None:
    result = initialize_exit_control(
        symbol="600001", entry_trade_date="2025-01-03", entry_price=price,
        effective_risk_pct=risk, price_basis_id="RAW_V1",
    )
    assert result.status == "INVALID"


def test_trailing_stop_only_rises_and_same_day_is_idempotent() -> None:
    state = _state()
    first = update_trailing_stop(
        state, trade_date="2025-01-03", daily_close="12", atr20="0.5",
        price_basis_id="RAW_V1",
    )
    assert first.new_state.trailing_stop == Decimal("11.0")
    repeated = update_trailing_stop(
        first.new_state, trade_date="2025-01-03", daily_close="20", atr20="0.1",
        price_basis_id="RAW_V1",
    )
    assert repeated.status == "UNCHANGED"
    assert repeated.new_state is first.new_state
    lower = update_trailing_stop(
        first.new_state, trade_date="2025-01-06", daily_close="10", atr20="2",
        price_basis_id="RAW_V1",
    )
    assert lower.new_state.trailing_stop == Decimal("11.0")


@pytest.mark.parametrize("close,atr", [("NaN", "1"), ("10", "Infinity"), ("10", "0")])
def test_invalid_daily_stop_input_keeps_old_state(close, atr) -> None:
    state = _state()
    result = update_trailing_stop(
        state, trade_date="2025-01-03", daily_close=close, atr20=atr,
        price_basis_id="RAW_V1",
    )
    assert result.status == "UNCHANGED"
    assert result.new_state is state


def test_price_basis_mismatch_is_invalid() -> None:
    state = _state()
    result = update_trailing_stop(
        state, trade_date="2025-01-03", daily_close="12", atr20="1",
        price_basis_id="ADJUSTED",
    )
    assert result.status == "INVALID"
    assert result.failure_reason == "price_basis_mismatch"


def test_protection_uses_actual_trading_calendar() -> None:
    calendar = ["2025-01-03", "2025-01-07", "2025-01-08"]
    assert in_soft_exit_protection("2025-01-03", "2025-01-03", calendar)
    assert in_soft_exit_protection("2025-01-03", "2025-01-07", calendar)
    assert not in_soft_exit_protection("2025-01-03", "2025-01-08", calendar)


def test_episode_set_is_immutable_and_written_once() -> None:
    state = _state()
    updated = mark_episode_acted(state, "DIV:1")
    assert updated.acted_episode_ids == ("DIV:1",)
    assert mark_episode_acted(updated, "DIV:1") is updated
    with pytest.raises(FrozenInstanceError):
        updated.acted_episode_ids = ()


def test_aware_dates_are_interpreted_on_shanghai_trading_date() -> None:
    result = initialize_exit_control(
        symbol="600001", entry_trade_date="2025-01-05T16:30:00Z",
        entry_price="10", effective_risk_pct="0.1", price_basis_id="RAW",
    )
    assert result.new_state.entry_trade_date.isoformat() == "2025-01-06"
