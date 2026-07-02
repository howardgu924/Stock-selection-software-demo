from __future__ import annotations

from stock_picker.data.limits import (
    LimitStatus,
    estimate_limit_prices,
    execution_limit_status,
)


def test_estimates_main_board_limit_prices_from_previous_close() -> None:
    prices = estimate_limit_prices(prev_close=10.0, board="main", is_st=False)

    assert prices.limit_up_price == 11.0
    assert prices.limit_down_price == 9.0
    assert prices.warning == ""


def test_estimates_growth_board_limit_prices_from_previous_close() -> None:
    prices = estimate_limit_prices(prev_close=10.0, board="star", is_st=False)

    assert prices.limit_up_price == 12.0
    assert prices.limit_down_price == 8.0


def test_estimates_st_limit_prices_from_previous_close() -> None:
    prices = estimate_limit_prices(prev_close=10.0, board="main", is_st=True)

    assert prices.limit_up_price == 10.5
    assert prices.limit_down_price == 9.5


def test_missing_previous_close_returns_unknown_status() -> None:
    prices = estimate_limit_prices(prev_close=None, board="main", is_st=False)

    assert prices.limit_up_price is None
    assert prices.limit_down_price is None
    assert "prev_close" in prices.warning


def test_execution_status_is_unknown_when_limit_prices_are_missing() -> None:
    status = execution_limit_status(
        price=10.0,
        limit_up_price=None,
        limit_down_price=9.0,
        is_suspended=False,
    )

    assert status == LimitStatus.UNKNOWN

