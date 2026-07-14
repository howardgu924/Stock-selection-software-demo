from __future__ import annotations

import inspect

import pytest

from stock_picker.strategies.adaptive_trend_v1_3.execution_gate import calculate_execution_gate
from stock_picker.strategies.adaptive_trend_v1_3.phase2_models import (
    DivergenceSignal,
    DivergenceSnapshot,
    DivergenceStrength,
    DivergenceType,
    Phase2Status,
    SecurityStatus,
)
from stock_picker.strategies.adaptive_trend_v1_3.risk_overlay import calculate_risk_overlay


def _signal(kind: DivergenceType) -> DivergenceSignal:
    return DivergenceSignal(
        divergence_type=kind,
        strength=(
            DivergenceStrength.STRONG
            if kind == DivergenceType.STRONG_TOP
            else DivergenceStrength.NORMAL
        ),
        pivot_1_date="2025-01-02",
        pivot_2_date="2025-01-20",
        confirmed_date="2025-01-23",
        first_usable_date="2025-01-24",
        active_until="2025-02-20",
        is_active=True,
    )


def _divergence(kind: DivergenceType = DivergenceType.NONE) -> DivergenceSnapshot:
    return DivergenceSnapshot(
        status=Phase2Status.VALID,
        as_of="2025-02-01",
        top_signal=_signal(kind) if kind in {DivergenceType.TOP, DivergenceType.STRONG_TOP} else None,
        bottom_signal=_signal(kind) if kind == DivergenceType.BOTTOM else None,
    )


def _calculate(
    *,
    divergence: DivergenceSnapshot | None = None,
    security_status: SecurityStatus = SecurityStatus(),
    close: float = 105.0,
    ma20: float = 100.0,
    ma60: float = 95.0,
    atr20: float = 4.0,
    rs20: float = 0.05,
    rs20_t_minus_5: float | None = 0.02,
    signed_er20: float = 0.20,
    signed_er20_t_minus_5: float | None = 0.10,
):
    return calculate_risk_overlay(
        {"status": "VALID", "opportunity_score": 80.0},
        divergence or _divergence(),
        security_status,
        close=close,
        ma20=ma20,
        ma60=ma60,
        atr20=atr20,
        rs20=rs20,
        rs20_t_minus_5=rs20_t_minus_5,
        signed_er20=signed_er20,
        signed_er20_t_minus_5=signed_er20_t_minus_5,
    )


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (SecurityStatus(is_st=True), "security_st"),
        (SecurityStatus(is_star_st=True), "security_star_st"),
        (SecurityStatus(is_delisting=True), "security_delisting"),
        (SecurityStatus(suspended=True), "security_suspended"),
        (SecurityStatus(no_price_limit=True), "security_no_price_limit"),
        (SecurityStatus(trade_status_unknown=True), "security_trade_status_unknown"),
    ],
)
def test_each_security_hard_block_status(status, reason) -> None:
    result = _calculate(security_status=status)

    assert result.risk_status.value == "BLOCK_NEW"
    assert result.risk_multiplier == 0.0
    assert reason in result.reasons


def test_invalid_opportunity_and_divergence_are_hard_blocks_with_stable_reasons() -> None:
    divergence = DivergenceSnapshot(
        status=Phase2Status.INVALID,
        as_of="2025-02-01",
        invalid_reasons=("invalid_date_value",),
    )
    result = calculate_risk_overlay(
        {"status": "INVALID", "invalid_reason": "missing_required_factor", "opportunity_score": None},
        divergence,
        SecurityStatus(),
        close=105.0,
        ma20=100.0,
        ma60=95.0,
        atr20=4.0,
        rs20=0.05,
        rs20_t_minus_5=0.02,
        signed_er20=0.2,
        signed_er20_t_minus_5=0.1,
    )

    assert result.risk_status.value == "BLOCK_NEW"
    assert "input_invalid:opportunity:missing_required_factor" in result.reasons
    assert "input_invalid:divergence:invalid_date_value" in result.reasons


def test_normal_top_divergence_applies_point_seven_five_exactly_once() -> None:
    result = _calculate(divergence=_divergence(DivergenceType.TOP))

    assert result.risk_status.value == "REDUCED"
    assert result.risk_multiplier == pytest.approx(0.75)
    assert result.reasons.count("normal_top_divergence") == 1


def test_strong_top_is_blocked_and_execution_gate_has_no_divergence_input() -> None:
    result = _calculate(divergence=_divergence(DivergenceType.STRONG_TOP))

    assert result.risk_status.value == "BLOCK_NEW"
    assert result.risk_multiplier == 0.0
    assert "divergence" not in inspect.signature(calculate_execution_gate).parameters


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, True),
        ({"close": 99.0}, False),
        ({"rs20": 0.0}, False),
        ({"rs20": 0.03, "rs20_t_minus_5": 0.02}, False),
        ({"signed_er20": 0.0}, False),
    ],
)
def test_bottom_divergence_recovery_requires_all_three_conditions(overrides, expected) -> None:
    result = _calculate(divergence=_divergence(DivergenceType.BOTTOM), **overrides)

    assert result.recovery_watch is True
    assert result.recovery_confirmed is expected


def test_structure_break_uses_both_frozen_conditions() -> None:
    price_break = _calculate(close=97.9, ma20=100.0, ma60=95.0, atr20=4.0)
    average_break = _calculate(close=105.0, ma20=94.0, ma60=95.0, atr20=4.0)
    boundary = _calculate(close=98.0, ma20=100.0, ma60=95.0, atr20=4.0)

    assert price_break.structure_break is True
    assert average_break.structure_break is True
    assert boundary.structure_break is False


def test_signed_er_weakening_uses_negative_or_exact_minus_point_two_change() -> None:
    negative = _calculate(signed_er20=-0.01, signed_er20_t_minus_5=-0.50)
    change = _calculate(signed_er20=0.10, signed_er20_t_minus_5=0.30)
    not_weak = _calculate(signed_er20=0.11, signed_er20_t_minus_5=0.30)

    assert negative.signed_er_weakening is True
    assert change.signed_er_weakening is True
    assert not_weak.signed_er_weakening is False


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "WATCH"),
        ({"signed_er20": -0.01}, "REDUCE"),
        ({"close": 97.0}, "EXIT"),
        ({"close": 97.0, "signed_er20": -0.01}, "EXIT"),
    ],
)
def test_strong_top_holding_actions_watch_reduce_exit(overrides, expected) -> None:
    result = _calculate(divergence=_divergence(DivergenceType.STRONG_TOP), **overrides)

    assert result.holding_risk_action.value == expected


def test_all_simultaneous_hard_block_reasons_are_retained() -> None:
    status = SecurityStatus(is_st=True, suspended=True, trade_status_unknown=True)
    result = _calculate(
        divergence=_divergence(DivergenceType.STRONG_TOP),
        security_status=status,
    )

    assert {
        "security_st",
        "security_suspended",
        "security_trade_status_unknown",
        "strong_top_divergence",
    }.issubset(result.reasons)


def test_risk_overlay_does_not_mutate_opportunity_output() -> None:
    opportunity = {
        "status": "VALID",
        "opportunity_score": 80.0,
        "rs20": 0.05,
        "invalid_reason": "",
    }
    before = opportunity.copy()

    calculate_risk_overlay(
        opportunity,
        _divergence(DivergenceType.TOP),
        SecurityStatus(),
        close=105.0,
        ma20=100.0,
        ma60=95.0,
        atr20=4.0,
        rs20=0.05,
        rs20_t_minus_5=0.02,
        signed_er20=0.20,
        signed_er20_t_minus_5=0.10,
    )

    assert opportunity == before
