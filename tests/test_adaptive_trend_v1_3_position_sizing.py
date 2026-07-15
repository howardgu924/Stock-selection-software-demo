from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from stock_picker.strategies.adaptive_trend_v1_3.phase4_models import (
    CandidateInput,
    ExistingHolding,
    IndustryClassificationSnapshot,
)
from stock_picker.strategies.adaptive_trend_v1_3.position_sizing import (
    calculate_candidate_sizing,
    normal_and_effective_risk,
    order_quantity,
    raw_weight_from_risk,
)


def _returns(multiplier: Decimal = Decimal("1"), count: int = 60):
    start = date(2025, 1, 1)
    return {
        start + timedelta(days=index): Decimal((index % 9) - 4) / Decimal("100") * multiplier
        for index in range(count)
    }


def _industry(symbol: str) -> IndustryClassificationSnapshot:
    return IndustryClassificationSnapshot(
        symbol=symbol,
        industry_code="801780",
        industry_name="Bank",
        effective_date="2024-01-01",
        known_at="2024-01-02 09:00+08:00",
        source="sw",
        classification_version="v1",
    )


def _candidate(**overrides) -> CandidateInput:
    values = dict(
        symbol="600001.SH", opportunity_status="VALID",
        opportunity_score=Decimal("80"), entry_threshold=Decimal("60"),
        opportunity_rank=1, rs60=Decimal("0.1"), rs20=Decimal("0.1"),
        signed_er20=Decimal("0.3"), market_paused=False,
        emergency_gate="NORMAL", risk_overlay="ALLOW", execution_gate="PASS",
        t1_risk_status="VALID", t1_loss_q=Decimal("0.05"),
        entry_atr=Decimal("1"), entry_price=Decimal("100"),
        industry_snapshot=_industry("600001.SH"),
        cooldown_blocked=False, daily_returns=_returns(),
        execution_price=Decimal("10"), buy_lot_size=100,
    )
    values.update(overrides)
    return CandidateInput(**values)


def _holding(symbol: str, returns=None) -> ExistingHolding:
    return ExistingHolding(
        symbol=symbol, actual_weight=Decimal("0.1"),
        industry_snapshot=_industry(symbol),
        t1_loss_q=Decimal("0.05"), daily_returns=returns or _returns(),
    )


def test_normal_effective_risk_and_raw_weight_limits() -> None:
    normal, effective = normal_and_effective_risk("1", "100", "0.05")
    assert normal == Decimal("0.02")
    assert effective == Decimal("0.05")
    assert raw_weight_from_risk(effective) == Decimal("0.1")
    assert raw_weight_from_risk("0.01") == Decimal("0.15")


def test_risk_and_gate_multipliers_apply_once() -> None:
    result = calculate_candidate_sizing(
        _candidate(risk_overlay="REDUCED", execution_gate="HALF"), []
    )
    assert result.raw_weight == Decimal("0.1")
    assert result.risk_multiplier == Decimal("0.75")
    assert result.gate_multiplier == Decimal("0.50")
    assert result.adjusted_weight == Decimal("0.037500")


def test_one_high_correlation_halves_and_two_reject() -> None:
    one = calculate_candidate_sizing(_candidate(), [_holding("600002.SH")])
    two = calculate_candidate_sizing(
        _candidate(), [_holding("600002.SH"), _holding("600003.SH")]
    )
    assert one.eligible is True
    assert one.correlation_multiplier == Decimal("0.50")
    assert one.adjusted_weight == Decimal("0.05")
    assert two.eligible is False
    assert two.correlation_multiplier == Decimal("0")
    assert "two_high_correlations" in two.failure_reasons


def test_low_negative_correlation_not_penalized() -> None:
    result = calculate_candidate_sizing(
        _candidate(), [_holding("600002.SH", _returns(Decimal("-1")))]
    )
    assert result.correlation_multiplier == Decimal("1")


def test_insufficient_common_history_blocks() -> None:
    result = calculate_candidate_sizing(
        _candidate(), [_holding("600002.SH", _returns(count=39))]
    )
    assert result.eligible is False
    assert result.correlation_multiplier == Decimal("0")
    assert "insufficient_correlation_history" in result.failure_reasons


def test_constant_returns_are_unknown_not_zero_correlation() -> None:
    constant = {day: Decimal("0.01") for day in _returns()}
    result = calculate_candidate_sizing(_candidate(), [_holding("600002.SH", constant)])
    assert result.eligible is False
    assert "insufficient_correlation_history" in result.failure_reasons


@pytest.mark.parametrize(
    "overrides",
    [
        {"opportunity_status": "INVALID"}, {"opportunity_score": Decimal("59")},
        {"opportunity_rank": 7}, {"market_paused": True},
        {"emergency_gate": "EMERGENCY"}, {"risk_overlay": "BLOCK_NEW"},
        {"execution_gate": "REJECT"}, {"t1_risk_status": "BLOCK_NEW"},
        {"cooldown_blocked": True},
    ],
)
def test_candidate_qualification_blocks_each_frozen_gate(overrides) -> None:
    assert calculate_candidate_sizing(_candidate(**overrides), []).eligible is False


def test_order_quantity_floors_lot_and_below_min_trade() -> None:
    qty, actual, reason = order_quantity("100000", "10.01", 100, "0.1234")
    assert qty == 1200
    assert actual == Decimal("1200") * Decimal("10.01") / Decimal("100000")
    assert reason == ""
    below = order_quantity("1000", "10.01", 100, "0.01")
    assert below == (0, Decimal("0"), "below_min_trade")


@pytest.mark.parametrize("lot", [0, True, 1.5])
def test_buy_lot_size_must_be_positive_integer(lot) -> None:
    with pytest.raises(ValueError):
        order_quantity("100000", "10", lot, "0.1")


def test_candidate_symbol_alias_is_recognized_as_already_held() -> None:
    result = calculate_candidate_sizing(_candidate(), [_holding("600001")])
    assert result.eligible is False
    assert result.final_target_weight == 0
    assert "already_held" in result.failure_reasons
    assert result.symbol == "600001.SH"


def test_duplicate_return_date_same_value_deduplicates() -> None:
    values = list(_returns().items())
    values.append(("2025-01-01", _returns()[date(2025, 1, 1)]))
    result = calculate_candidate_sizing(
        _candidate(daily_returns=values), [_holding("600002.SH")]
    )
    assert result.correlation_multiplier == Decimal("0.50")
    assert result.eligible is True


def test_conflicting_return_date_is_unknown_and_order_independent() -> None:
    values = list(_returns().items()) + [("2025-01-01", Decimal("0.99"))]
    expected = "conflicting_return_date:600001.SH:2025-01-01"
    for records in (values, list(reversed(values))):
        result = calculate_candidate_sizing(
            _candidate(daily_returns=records), [_holding("600002.SH")]
        )
        assert result.eligible is False
        assert result.correlation_multiplier == 0
        assert expected in result.failure_reasons


@pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity")])
def test_nonfinite_return_is_unknown(bad: Decimal) -> None:
    values = list(_returns().items())
    values[0] = (values[0][0], bad)
    result = calculate_candidate_sizing(
        _candidate(daily_returns=values), [_holding("600002.SH")]
    )
    assert result.eligible is False
    assert "invalid_return_value" in result.failure_reasons


def test_daily_returns_are_copied_into_immutable_tuple() -> None:
    original = _returns()
    candidate = _candidate(daily_returns=original)
    frozen = candidate.daily_returns
    original[date(2025, 1, 1)] = Decimal("0.99")
    assert candidate.daily_returns == frozen
    assert isinstance(candidate.daily_returns, tuple)
