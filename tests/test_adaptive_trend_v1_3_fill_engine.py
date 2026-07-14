from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from stock_picker.strategies.adaptive_trend_v1_3.fill_engine import (
    _RETRYABLE_REASONS,
    calculate_fill_fees,
    execute_fill,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase3_models import (
    ExecutionType,
    FeeRuleSnapshot,
    FillRequest,
    FillSide,
    TradingRuleSnapshot,
)


def _rule(**overrides) -> TradingRuleSnapshot:
    values = {
        "exchange": "SSE",
        "board": "main",
        "security_type": "stock",
        "effective_date": "2025-07-14",
        "buy_lot_size": 100,
        "partial_sell_lot_size": 100,
        "full_exit_odd_lot_allowed": True,
        "price_tick": Decimal("0.01"),
    }
    values.update(overrides)
    return TradingRuleSnapshot(**values)


def _fees(**overrides) -> FeeRuleSnapshot:
    values = {
        "effective_date": "2025-07-14",
        "commission_rate": Decimal("0.0003"),
        "minimum_commission": Decimal("5.00"),
        "buy_transfer_fee_rate": Decimal("0.00001"),
        "sell_transfer_fee_rate": Decimal("0.00001"),
        "buy_settlement_fee_rate": Decimal("0.00002"),
        "sell_settlement_fee_rate": Decimal("0.00002"),
        "stamp_tax_rate": Decimal("0.0005"),
    }
    values.update(overrides)
    return FeeRuleSnapshot(**values)


def _bar(
    bar_start: str = "2025-07-14 10:05:00",
    *,
    limit_status: str = "normal",
    trade_status: str = "normal",
    open_price: str = "10.00",
    high: str = "10.20",
    low: str = "9.90",
    close: str = "10.10",
    volume: str = "1000",
) -> pd.DataFrame:
    timestamp = pd.Timestamp(bar_start)
    return pd.DataFrame(
        [
            {
                "symbol": "600001.SH",
                "trade_date": timestamp.strftime("%Y-%m-%d"),
                "bar_start": timestamp,
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "trade_status": trade_status,
                "limit_status": limit_status,
            }
        ]
    )


def _buy_request(**overrides) -> FillRequest:
    values = {
        "execution_type": ExecutionType.ENTRY_BUY,
        "symbol": "600001.SH",
        "requested_qty": 100,
        "signal_time": "2025-07-14 10:00:00",
        "cash_available": Decimal("100000.00"),
    }
    values.update(overrides)
    return FillRequest(**values)


def _sell_request(**overrides) -> FillRequest:
    values = {
        "execution_type": ExecutionType.SOFT_EXIT,
        "symbol": "600001.SH",
        "requested_qty": 100,
        "signal_time": "2025-07-14 14:30:00",
        "position_qty": 500,
        "sellable_qty": 500,
    }
    values.update(overrides)
    return FillRequest(**values)


def test_entry_buy_uses_exact_1005_open() -> None:
    result = execute_fill(_buy_request(), _bar(), _rule(), _fees())

    assert result.status.value == "FILLED"
    assert result.execution_bar_start.endswith("T10:05:00+08:00")
    assert result.execution_price == Decimal("10.00")
    assert result.filled_qty == 100


def test_changing_non_open_ohlcv_does_not_change_fill_price_or_eligibility() -> None:
    baseline = execute_fill(_buy_request(), _bar(), _rule(), _fees())
    changed_bar = _bar(high="20.00", low="5.00", close="15.00", volume="0")
    changed = execute_fill(_buy_request(), changed_bar, _rule(), _fees())

    assert baseline.status == changed.status
    assert baseline.execution_price == changed.execution_price == Decimal("10.00")
    assert baseline.gross_amount == changed.gross_amount


def test_no_slippage_capacity_or_partial_fill_fields_and_all_or_nothing_quantity() -> None:
    result = execute_fill(_buy_request(requested_qty=200), _bar(), _rule(), _fees())

    assert result.filled_qty == result.requested_qty == 200
    assert not hasattr(result, "slippage")
    assert not hasattr(result, "capacity")
    assert not hasattr(result, "partial_fill")


@pytest.mark.parametrize("limit_status", ["normal", "limit_down"])
def test_buy_normal_and_limit_down_succeed(limit_status) -> None:
    result = execute_fill(
        _buy_request(), _bar(limit_status=limit_status), _rule(), _fees()
    )

    assert result.status.value == "FILLED"


def test_limit_up_buy_fails_nonretryably() -> None:
    result = execute_fill(
        _buy_request(), _bar(limit_status="limit_up"), _rule(), _fees()
    )

    assert result.status.value == "FAILED"
    assert result.failure_reason == "limit_up_buy"
    assert result.retryable is False


@pytest.mark.parametrize(
    ("bar", "reason"),
    [
        (_bar(trade_status="suspended"), "suspended"),
        (_bar(trade_status="unknown"), "unknown_trade_status"),
        (_bar(limit_status="unknown"), "unknown_limit_status"),
    ],
)
def test_suspended_and_unknown_buy_fail_retryably(bar, reason) -> None:
    result = execute_fill(_buy_request(), bar, _rule(), _fees())

    assert result.status.value == "FAILED"
    assert result.failure_reason == reason
    assert result.retryable is True


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [(0, "invalid_quantity"), (-100, "invalid_quantity"), (99, "invalid_lot_size"), (100, "")],
)
def test_buy_quantity_and_lot_boundaries(quantity, expected) -> None:
    result = execute_fill(
        _buy_request(requested_qty=quantity), _bar(), _rule(), _fees()
    )

    assert result.failure_reason == expected
    assert result.status.value == ("FILLED" if not expected else "INVALID")


def test_cash_exactly_equal_to_required_succeeds_and_one_cent_less_fails() -> None:
    probe = execute_fill(_buy_request(), _bar(), _rule(), _fees())
    exact = execute_fill(
        _buy_request(cash_available=probe.cash_required), _bar(), _rule(), _fees()
    )
    short = execute_fill(
        _buy_request(cash_available=probe.cash_required - Decimal("0.01")),
        _bar(),
        _rule(),
        _fees(),
    )

    assert exact.status.value == "FILLED"
    assert short.failure_reason == "insufficient_cash"
    assert short.filled_qty == 0


def test_insufficient_cash_never_resizes_requested_quantity() -> None:
    result = execute_fill(
        _buy_request(requested_qty=200, cash_available=Decimal("1005.03")),
        _bar(),
        _rule(),
        _fees(),
    )

    assert result.status.value == "FAILED"
    assert result.failure_reason == "insufficient_cash"
    assert result.requested_qty == 200
    assert result.filled_qty == 0


def test_missing_1005_does_not_fall_forward_to_1010() -> None:
    result = execute_fill(
        _buy_request(), _bar("2025-07-14 10:10:00"), _rule(), _fees()
    )

    assert result.failure_reason == "missing_execution_bar"
    assert result.execution_price is None
    assert result.retryable is True


@pytest.mark.parametrize("limit_status", ["normal", "limit_up"])
def test_sell_normal_and_limit_up_succeed(limit_status) -> None:
    result = execute_fill(
        _sell_request(),
        _bar("2025-07-14 14:35:00", limit_status=limit_status),
        _rule(),
        _fees(),
    )

    assert result.status.value == "FILLED"
    assert result.side.value == "SELL"


@pytest.mark.parametrize(
    "execution_type",
    [
        ExecutionType.SOFT_EXIT,
        ExecutionType.REPLACEMENT_EXIT,
        ExecutionType.ORDINARY_REDUCTION,
    ],
)
def test_all_soft_execution_types_use_exact_1435_bar(execution_type) -> None:
    request = _sell_request(execution_type=execution_type)

    result = execute_fill(
        request, _bar("2025-07-14 14:35:00"), _rule(), _fees()
    )

    assert result.status.value == "FILLED"
    assert result.execution_bar_start.endswith("T14:35:00+08:00")


def test_limit_down_sell_fails_and_is_retryable() -> None:
    result = execute_fill(
        _sell_request(),
        _bar("2025-07-14 14:35:00", limit_status="limit_down"),
        _rule(),
        _fees(),
    )

    assert result.failure_reason == "limit_down_sell"
    assert result.retryable is True


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"requested_qty": 600, "position_qty": 500, "sellable_qty": 500}, "insufficient_position"),
        ({"requested_qty": 400, "position_qty": 500, "sellable_qty": 300}, "insufficient_sellable_qty"),
    ],
)
def test_position_and_sellable_quantity_checks(overrides, reason) -> None:
    result = execute_fill(
        _sell_request(**overrides), _bar("2025-07-14 14:35:00"), _rule(), _fees()
    )

    assert result.failure_reason == reason
    assert result.filled_qty == 0


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"position_qty": "500"}, "invalid_quantity"),
        ({"sellable_qty": "500"}, "invalid_quantity"),
        ({"position_qty": -1}, "invalid_quantity"),
        ({"sellable_qty": -1}, "invalid_quantity"),
    ],
)
def test_malformed_account_quantities_fail_stably(overrides, reason) -> None:
    result = execute_fill(
        _sell_request(**overrides), _bar("2025-07-14 14:35:00"), _rule(), _fees()
    )

    assert result.failure_reason == reason
    assert result.filled_qty == 0


def test_partial_sell_must_follow_partial_lot_size() -> None:
    invalid = execute_fill(
        _sell_request(requested_qty=150),
        _bar("2025-07-14 14:35:00"),
        _rule(),
        _fees(),
    )
    valid = execute_fill(
        _sell_request(requested_qty=200),
        _bar("2025-07-14 14:35:00"),
        _rule(),
        _fees(),
    )

    assert invalid.failure_reason == "invalid_lot_size"
    assert valid.status.value == "FILLED"


def test_full_exit_odd_lot_rule() -> None:
    request = _sell_request(requested_qty=150, position_qty=150, sellable_qty=150)
    allowed = execute_fill(
        request, _bar("2025-07-14 14:35:00"), _rule(full_exit_odd_lot_allowed=True), _fees()
    )
    denied = execute_fill(
        request, _bar("2025-07-14 14:35:00"), _rule(full_exit_odd_lot_allowed=False), _fees()
    )

    assert allowed.status.value == "FILLED"
    assert denied.failure_reason == "invalid_lot_size"


def test_hard_exit_uses_exact_next_bar_and_does_not_search_later() -> None:
    request = _sell_request(
        execution_type=ExecutionType.HARD_EXIT,
        signal_time="2025-07-14 10:05:00",
    )
    exact = execute_fill(request, _bar("2025-07-14 10:10:00"), _rule(), _fees())
    later_only = execute_fill(request, _bar("2025-07-14 10:15:00"), _rule(), _fees())

    assert exact.status.value == "FILLED"
    assert exact.execution_bar_start.endswith("T10:10:00+08:00")
    assert later_only.failure_reason == "missing_execution_bar"


def test_hard_exit_crosses_lunch_and_trading_day() -> None:
    lunch_request = _sell_request(
        execution_type=ExecutionType.HARD_EXIT,
        signal_time="2025-07-14 11:25:00",
    )
    close_request = _sell_request(
        execution_type=ExecutionType.HARD_EXIT,
        signal_time="2025-07-14 14:55:00",
    )
    lunch = execute_fill(
        lunch_request, _bar("2025-07-14 13:00:00"), _rule(), _fees()
    )
    next_day = execute_fill(
        close_request,
        _bar("2025-07-15 09:30:00"),
        _rule(effective_date="2025-07-15"),
        _fees(effective_date="2025-07-15"),
        trading_calendar=["2025-07-14", "2025-07-15"],
    )

    assert lunch.execution_bar_start.endswith("T13:00:00+08:00")
    assert next_day.execution_bar_start.startswith("2025-07-15T09:30")


def test_hard_exit_friday_uses_monday_calendar_date() -> None:
    request = _sell_request(
        execution_type=ExecutionType.HARD_EXIT,
        signal_time="2025-07-18 14:55:00",
    )
    result = execute_fill(
        request,
        _bar("2025-07-21 09:30:00"),
        _rule(effective_date="2025-07-21"),
        _fees(effective_date="2025-07-21"),
        trading_calendar=["2025-07-18", "2025-07-21"],
    )

    assert result.status.value == "FILLED"
    assert result.execution_trade_date == "2025-07-21"


def test_commission_minimum_percentage_and_decimal_half_up() -> None:
    minimum = calculate_fill_fees(FillSide.BUY, Decimal("1000.00"), _fees())
    percentage = calculate_fill_fees(FillSide.BUY, Decimal("100000.00"), _fees())
    half_up = calculate_fill_fees(
        FillSide.BUY,
        Decimal("100.00"),
        _fees(
            commission_rate=Decimal("0.00005"),
            minimum_commission=Decimal("0"),
            buy_transfer_fee_rate=Decimal("0"),
            buy_settlement_fee_rate=Decimal("0"),
        ),
    )

    assert minimum["commission"] == Decimal("5.00")
    assert percentage["commission"] == Decimal("30.00")
    assert half_up["commission"] == Decimal("0.01")


def test_buy_and_sell_fee_components_cash_and_net_proceeds() -> None:
    buy = execute_fill(_buy_request(), _bar(), _rule(), _fees())
    sell = execute_fill(
        _sell_request(), _bar("2025-07-14 14:35:00"), _rule(), _fees()
    )

    assert buy.gross_amount == Decimal("1000.00")
    assert buy.commission == Decimal("5.00")
    assert buy.transfer_fee == Decimal("0.01")
    assert buy.settlement_fee == Decimal("0.02")
    assert buy.stamp_tax == Decimal("0.00")
    assert buy.cash_required == Decimal("1005.03")
    assert sell.stamp_tax == Decimal("0.50")
    assert sell.total_fees == Decimal("5.53")
    assert sell.net_proceeds == Decimal("994.47")


def test_invalid_rule_snapshot_and_conflicting_bar_are_invalid_results() -> None:
    invalid_rule = execute_fill(
        _buy_request(), _bar(), _rule(buy_lot_size=0), _fees()
    )
    conflict = pd.concat([_bar(), _bar(open_price="10.01")], ignore_index=True)
    invalid_bar = execute_fill(_buy_request(), conflict, _rule(), _fees())

    assert invalid_rule.status.value == "INVALID"
    assert invalid_rule.failure_reason == "invalid_rule_snapshot"
    assert invalid_bar.status.value == "INVALID"
    assert invalid_bar.failure_reason == "conflicting_duplicate_bar:10:05"


def test_fill_result_fields_retryability_and_simplified_flag_are_complete() -> None:
    filled = execute_fill(_buy_request(), _bar(), _rule(), _fees())
    failed = execute_fill(
        _buy_request(), _bar(limit_status="limit_up"), _rule(), _fees()
    )
    invalid = execute_fill(
        _buy_request(requested_qty=0), _bar(), _rule(), _fees()
    )

    expected_fields = {
        "status", "side", "execution_type", "symbol", "requested_qty", "filled_qty",
        "execution_trade_date", "execution_bar_start", "execution_price", "gross_amount",
        "commission", "stamp_tax", "transfer_fee", "settlement_fee", "total_fees",
        "cash_required", "net_proceeds", "failure_reason", "retryable",
        "simplified_direct_fill",
    }
    assert set(filled.__dataclass_fields__) == expected_fields
    assert filled.simplified_direct_fill is True
    assert failed.simplified_direct_fill is True
    assert invalid.simplified_direct_fill is True
    assert failed.filled_qty == invalid.filled_qty == 0


def test_retryable_reason_mapping_is_exactly_the_frozen_contract() -> None:
    assert _RETRYABLE_REASONS == {
        "suspended",
        "limit_down_sell",
        "unknown_trade_status",
        "unknown_limit_status",
        "missing_execution_bar",
    }


def test_invalid_price_and_invalid_bar_contract_are_nonretryable() -> None:
    invalid_price = execute_fill(
        _buy_request(), _bar(open_price="NaN"), _rule(), _fees()
    )
    invalid_contract = execute_fill(
        _buy_request(), _bar(high="9.00"), _rule(), _fees()
    )

    assert invalid_price.failure_reason == "invalid_price"
    assert invalid_price.retryable is False
    assert invalid_contract.failure_reason == "invalid_bar_contract"
    assert invalid_contract.retryable is False


def test_invalid_future_bar_does_not_affect_current_1005_fill() -> None:
    future = _bar("2025-07-14 10:10:00", open_price="NaN")
    bars = pd.concat([future, _bar()], ignore_index=True)

    result = execute_fill(_buy_request(), bars, _rule(), _fees())

    assert result.status.value == "FILLED"
    assert result.execution_price == Decimal("10.00")


def test_invalid_other_security_does_not_affect_target_fill() -> None:
    unrelated = _bar(open_price="NaN").assign(symbol="not-a-security")
    bars = pd.concat([unrelated, _bar()], ignore_index=True)

    result = execute_fill(_buy_request(), bars, _rule(), _fees())

    assert result.status.value == "FILLED"


def test_target_symbol_alias_duplicates_deduplicate_and_conflict_stably() -> None:
    alias = _bar().assign(symbol="600001")
    identical = pd.concat([_bar(), alias], ignore_index=True)
    conflicting = pd.concat(
        [_bar(), alias.assign(open="10.01")], ignore_index=True
    )

    filled = execute_fill(_buy_request(), identical, _rule(), _fees())
    invalid = execute_fill(_buy_request(), conflicting, _rule(), _fees())
    reversed_invalid = execute_fill(
        _buy_request(), conflicting.iloc[::-1].reset_index(drop=True), _rule(), _fees()
    )

    assert filled.status.value == "FILLED"
    assert invalid.status.value == "INVALID"
    assert invalid.failure_reason == "conflicting_duplicate_bar:10:05"
    assert reversed_invalid.failure_reason == invalid.failure_reason


def test_raw_gross_amount_is_fee_base_before_display_rounding() -> None:
    fees = calculate_fill_fees(
        FillSide.BUY,
        Decimal("1.005"),
        _fees(
            commission_rate=Decimal("0.5"),
            minimum_commission=Decimal("0"),
            buy_transfer_fee_rate=Decimal("0"),
            buy_settlement_fee_rate=Decimal("0"),
        ),
    )
    result = execute_fill(
        _buy_request(requested_qty=1),
        _bar(open_price="1.005", high="1.005", low="1.005", close="1.005"),
        _rule(buy_lot_size=1, price_tick=Decimal("0.001")),
        _fees(
            commission_rate=Decimal("0.5"),
            minimum_commission=Decimal("0"),
            buy_transfer_fee_rate=Decimal("0"),
            buy_settlement_fee_rate=Decimal("0"),
        ),
    )

    assert fees["commission"] == Decimal("0.50")
    assert result.gross_amount == Decimal("1.01")
    assert result.commission == Decimal("0.50")
    assert result.cash_required == Decimal("1.51")


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"commission_rate": 0.0003}, "invalid_fee_snapshot"),
        ({"commission_rate": Decimal("NaN")}, "invalid_fee_snapshot"),
        ({"commission_rate": Decimal("Infinity")}, "invalid_fee_snapshot"),
        ({"commission_rate": Decimal("-0.1")}, "invalid_fee_snapshot"),
        ({"commission_rate": Decimal("1.01")}, "invalid_fee_snapshot"),
        ({"minimum_commission": Decimal("-0.01")}, "invalid_fee_snapshot"),
    ],
)
def test_invalid_fee_decimal_domain_is_rejected(overrides, reason) -> None:
    result = execute_fill(_buy_request(), _bar(), _rule(), _fees(**overrides))

    assert result.status.value == "INVALID"
    assert result.failure_reason == reason
    assert result.retryable is False


def test_decimal_string_fee_and_cash_inputs_match_decimal_inputs() -> None:
    decimal_result = execute_fill(_buy_request(), _bar(), _rule(), _fees())
    string_result = execute_fill(
        _buy_request(cash_available="100000.00"),
        _bar(),
        _rule(price_tick="0.01"),
        _fees(
            commission_rate="0.0003",
            minimum_commission="5.00",
            buy_transfer_fee_rate="0.00001",
            sell_transfer_fee_rate="0.00001",
            buy_settlement_fee_rate="0.00002",
            sell_settlement_fee_rate="0.00002",
            stamp_tax_rate="0.0005",
        ),
    )

    assert string_result.status.value == "FILLED"
    assert string_result.gross_amount == decimal_result.gross_amount
    assert string_result.total_fees == decimal_result.total_fees
    assert string_result.cash_required == decimal_result.cash_required


def test_float_cash_available_is_invalid_not_insufficient() -> None:
    result = execute_fill(
        _buy_request(cash_available=100000.0), _bar(), _rule(), _fees()
    )

    assert result.status.value == "INVALID"
    assert result.failure_reason == "invalid_cash_available"


def test_zero_gross_never_charges_minimum_commission_and_negative_is_rejected() -> None:
    zero = calculate_fill_fees(FillSide.BUY, Decimal("0"), _fees())

    assert set(zero.values()) == {Decimal("0.00")}
    with pytest.raises(ValueError, match="negative_gross_amount"):
        calculate_fill_fees(FillSide.BUY, Decimal("-0.01"), _fees())


@pytest.mark.parametrize(
    ("rule_date", "fee_date", "reason"),
    [
        ("2025-07-13", "2025-07-14", "invalid_rule_snapshot"),
        ("2025-07-15", "2025-07-14", "invalid_rule_snapshot"),
        ("2025-07-14", "2025-07-13", "invalid_fee_snapshot"),
        ("2025-07-14", "2025-07-15", "invalid_fee_snapshot"),
    ],
)
def test_both_snapshot_effective_dates_must_equal_execution_date(
    rule_date, fee_date, reason
) -> None:
    result = execute_fill(
        _buy_request(),
        _bar(),
        _rule(effective_date=rule_date),
        _fees(effective_date=fee_date),
    )

    assert result.status.value == "INVALID"
    assert result.failure_reason == reason


@pytest.mark.parametrize(
    "overrides",
    [
        {"buy_lot_size": 0},
        {"buy_lot_size": True},
        {"partial_sell_lot_size": 0},
        {"partial_sell_lot_size": False},
    ],
)
def test_lot_sizes_must_be_positive_non_boolean_integers(overrides) -> None:
    result = execute_fill(_buy_request(), _bar(), _rule(**overrides), _fees())

    assert result.status.value == "INVALID"
    assert result.failure_reason == "invalid_rule_snapshot"


@pytest.mark.parametrize(
    "overrides",
    [
        {"requested_qty": 100.0},
        {"requested_qty": "100"},
        {"requested_qty": True},
        {"position_qty": -1},
        {"position_qty": 1.0},
        {"sellable_qty": -1},
        {"sellable_qty": False},
    ],
)
def test_all_quantity_fields_use_strict_integer_contract(overrides) -> None:
    request = _sell_request(**overrides)
    result = execute_fill(
        request, _bar("2025-07-14 14:35:00"), _rule(), _fees()
    )

    assert result.status.value == "INVALID"
    assert result.failure_reason == "invalid_quantity"


@pytest.mark.parametrize(
    "result",
    [
        execute_fill(
            _buy_request(), _bar(limit_status="limit_up"), _rule(), _fees()
        ),
        execute_fill(_buy_request(requested_qty=0), _bar(), _rule(), _fees()),
    ],
)
def test_failed_and_invalid_results_have_no_price_and_all_zero_amounts(result) -> None:
    assert result.execution_price is None
    assert result.filled_qty == 0
    for field in (
        "gross_amount",
        "commission",
        "stamp_tax",
        "transfer_fee",
        "settlement_fee",
        "total_fees",
        "cash_required",
        "net_proceeds",
    ):
        assert getattr(result, field) == Decimal("0.00")


def test_execute_fill_does_not_mutate_inputs() -> None:
    request = _buy_request()
    rule = _rule()
    fees = _fees()
    bars = pd.concat([_bar(), _bar("2025-07-14 10:10:00")], ignore_index=True)
    original_bars = bars.copy(deep=True)
    calendar = ["2025-07-14", "2025-07-15"]
    original_calendar = list(calendar)

    execute_fill(request, bars, rule, fees, trading_calendar=calendar)

    pd.testing.assert_frame_equal(bars, original_bars)
    assert request == _buy_request()
    assert rule == _rule()
    assert fees == _fees()
    assert calendar == original_calendar


def test_friday_hard_exit_uses_tuesday_when_monday_is_not_in_calendar() -> None:
    request = _sell_request(
        execution_type=ExecutionType.HARD_EXIT,
        signal_time="2025-07-18 14:55:00",
    )
    result = execute_fill(
        request,
        _bar("2025-07-22 09:30:00"),
        _rule(effective_date="2025-07-22"),
        _fees(effective_date="2025-07-22"),
        trading_calendar=["2025-07-18", "2025-07-22"],
    )

    assert result.status.value == "FILLED"
    assert result.execution_trade_date == "2025-07-22"


@pytest.mark.parametrize(
    ("fill_request", "rule", "fees"),
    [
        (_buy_request(), _rule(), _fees(commission_rate=0.1)),
        (_buy_request(), _rule(buy_lot_size=0), _fees()),
        (_buy_request(requested_qty=0), _rule(), _fees()),
        (_buy_request(cash_available=0.1), _rule(), _fees()),
    ],
    ids=("invalid-fee", "invalid-rule", "invalid-quantity", "invalid-cash"),
)
def test_missing_target_bar_has_priority_over_later_input_validation(
    fill_request, rule, fees
) -> None:
    result = execute_fill(
        fill_request, _bar("2025-07-14 10:10:00"), rule, fees
    )

    assert result.status.value == "FAILED"
    assert result.failure_reason == "missing_execution_bar"
    assert result.retryable is True


@pytest.mark.parametrize(
    "cash_available",
    [0.1, True, Decimal("NaN"), Decimal("Infinity"), Decimal("-0.01"), "bad"],
)
def test_sell_rejects_invalid_cash_available(cash_available) -> None:
    result = execute_fill(
        _sell_request(cash_available=cash_available),
        _bar("2025-07-14 14:35:00"),
        _rule(),
        _fees(),
    )

    assert result.status.value == "INVALID"
    assert result.failure_reason == "invalid_cash_available"
    assert result.retryable is False


@pytest.mark.parametrize("cash_available", [Decimal("0"), "0.00"])
def test_valid_decimal_cash_does_not_affect_sell(cash_available) -> None:
    result = execute_fill(
        _sell_request(cash_available=cash_available),
        _bar("2025-07-14 14:35:00"),
        _rule(),
        _fees(),
    )

    assert result.status.value == "FILLED"


def test_alias_trade_date_conflict_is_stable_in_both_input_orders() -> None:
    valid_date = _bar().assign(symbol="600001")
    conflicting_date = _bar().assign(
        symbol="600001.SH", trade_date="2025-07-15"
    )
    forward = pd.concat([valid_date, conflicting_date], ignore_index=True)
    reverse = forward.iloc[::-1].reset_index(drop=True)

    results = [
        execute_fill(_buy_request(), bars, _rule(), _fees())
        for bars in (forward, reverse)
    ]

    assert [result.status.value for result in results] == ["INVALID", "INVALID"]
    assert [result.failure_reason for result in results] == [
        "conflicting_duplicate_bar:10:05",
        "conflicting_duplicate_bar:10:05",
    ]
    assert [result.retryable for result in results] == [False, False]


@pytest.mark.parametrize(
    "unrelated",
    [
        _bar("2025-07-14 09:55:00", open_price="NaN"),
        _bar("2025-07-15 10:05:00", open_price="NaN"),
    ],
    ids=("earlier-same-symbol", "next-trading-day"),
)
def test_invalid_same_symbol_non_target_bar_does_not_affect_fill(unrelated) -> None:
    bars = pd.concat([unrelated, _bar()], ignore_index=True)

    result = execute_fill(_buy_request(), bars, _rule(), _fees())

    assert result.status.value == "FILLED"
    assert result.execution_price == Decimal("10.00")


@pytest.mark.parametrize(
    "field",
    [
        "commission_rate",
        "minimum_commission",
        "buy_transfer_fee_rate",
        "sell_transfer_fee_rate",
        "buy_settlement_fee_rate",
        "sell_settlement_fee_rate",
        "stamp_tax_rate",
    ],
)
@pytest.mark.parametrize(
    "invalid_value",
    [
        pytest.param(0.1, id="float"),
        pytest.param(True, id="bool"),
        pytest.param(0.3 - 0.2, id="float-expression"),
    ],
)
def test_every_fee_decimal_field_rejects_float_and_bool(
    field, invalid_value
) -> None:
    result = execute_fill(
        _buy_request(), _bar(), _rule(), _fees(**{field: invalid_value})
    )

    assert result.status.value == "INVALID"
    assert result.failure_reason == "invalid_fee_snapshot"
    assert result.retryable is False


def test_new_missing_and_invalid_cash_failures_keep_zero_amount_contract() -> None:
    results = [
        execute_fill(
            _buy_request(cash_available=0.1),
            _bar("2025-07-14 10:10:00"),
            _rule(),
            _fees(),
        ),
        execute_fill(
            _sell_request(cash_available=True),
            _bar("2025-07-14 14:35:00"),
            _rule(),
            _fees(),
        ),
    ]

    for result in results:
        assert result.filled_qty == 0
        assert result.execution_price is None
        assert result.failure_reason
        assert result.simplified_direct_fill is True
        for field in (
            "gross_amount",
            "commission",
            "stamp_tax",
            "transfer_fee",
            "settlement_fee",
            "total_fees",
            "cash_required",
            "net_proceeds",
        ):
            assert getattr(result, field) == Decimal("0.00")
