"""Single-candidate risk sizing and correlation multipliers for Phase 4A."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from typing import Iterable, Mapping, Sequence

import pandas as pd

from stock_picker.strategies.adaptive_trend_v1_3.phase4_models import (
    CandidateInput,
    ExistingHolding,
    FrozenReturnSeries,
    SizingResult,
)
from stock_picker.strategies.adaptive_trend_v1_3.minute_contract import (
    SHANGHAI_TIMEZONE,
    normalize_security_symbol,
)

ZERO = Decimal("0")
ONE = Decimal("1")
MAX_WEIGHT = Decimal("0.15")
RISK_BUDGET = Decimal("0.005")


def normal_and_effective_risk(
    entry_atr: Decimal | str,
    entry_price: Decimal | str,
    t1_loss_q: Decimal | str,
) -> tuple[Decimal, Decimal]:
    atr = _required_decimal(entry_atr)
    price = _required_decimal(entry_price)
    tail = _required_decimal(t1_loss_q)
    if atr <= 0 or price <= 0 or tail < 0 or tail > 1:
        raise ValueError("invalid_effective_risk")
    normal = Decimal("2") * atr / price
    effective = max(normal, tail)
    if not effective.is_finite() or effective <= 0:
        raise ValueError("invalid_effective_risk")
    return normal, effective


def raw_weight_from_risk(effective_risk_pct: Decimal | str) -> Decimal:
    risk = _required_decimal(effective_risk_pct)
    if risk <= 0:
        raise ValueError("invalid_effective_risk")
    return min(MAX_WEIGHT, RISK_BUDGET / risk)


def calculate_candidate_sizing(
    candidate: CandidateInput,
    existing_holdings: Sequence[ExistingHolding],
) -> SizingResult:
    """Apply qualification, risk, gate, and correlation exactly once."""

    candidate_symbol = _normalized_symbol(candidate.symbol)
    reasons = _qualification_reasons(candidate, existing_holdings)
    try:
        _, effective = normal_and_effective_risk(
            candidate.entry_atr, candidate.entry_price, candidate.t1_loss_q
        )
        raw = raw_weight_from_risk(effective)
    except (ValueError, InvalidOperation):
        reasons.append("invalid_effective_risk")
        raw = ZERO

    risk_multiplier = {
        "ALLOW": ONE,
        "REDUCED": Decimal("0.75"),
        "BLOCK_NEW": ZERO,
    }.get(str(candidate.risk_overlay), ZERO)
    gate_multiplier = {
        "PASS": ONE,
        "HALF": Decimal("0.50"),
        "REJECT": ZERO,
    }.get(str(candidate.execution_gate), ZERO)
    if risk_multiplier == ZERO:
        reasons.append("risk_overlay_blocked")
    if gate_multiplier == ZERO:
        reasons.append("execution_gate_rejected")

    correlation_multiplier, correlation_reason = correlation_multiplier_for_candidate(
        candidate.daily_returns,
        existing_holdings,
        candidate_symbol=candidate_symbol or str(candidate.symbol),
    )
    if correlation_reason:
        reasons.append(correlation_reason)
    adjusted = min(
        MAX_WEIGHT,
        raw * risk_multiplier * gate_multiplier * correlation_multiplier,
    )
    if reasons:
        adjusted = ZERO
    unique_reasons = tuple(dict.fromkeys(reasons))
    return SizingResult(
        symbol=candidate_symbol or str(candidate.symbol),
        eligible=not unique_reasons and adjusted > 0,
        raw_weight=raw,
        risk_multiplier=risk_multiplier,
        gate_multiplier=gate_multiplier,
        correlation_multiplier=correlation_multiplier,
        adjusted_weight=adjusted,
        industry_scaled_weight=adjusted,
        exposure_scaled_weight=adjusted,
        stress_scaled_weight=adjusted,
        final_target_weight=adjusted,
        order_qty=0,
        actual_order_weight=ZERO,
        failure_reasons=unique_reasons,
    )


def correlation_multiplier_for_candidate(
    candidate_returns: FrozenReturnSeries | Mapping[object, object],
    holdings: Sequence[ExistingHolding],
    *,
    candidate_symbol: str = "CANDIDATE",
) -> tuple[Decimal, str]:
    if not holdings:
        return ONE, ""
    normalized_holdings: dict[str, ExistingHolding] = {}
    for holding in holdings:
        symbol = _normalized_symbol(holding.symbol)
        if symbol is None:
            return ZERO, "invalid_existing_symbol"
        if symbol in normalized_holdings:
            return ZERO, f"duplicate_normalized_existing_symbol:{symbol}"
        normalized_holdings[symbol] = holding
    high_count = 0
    for symbol, holding in sorted(normalized_holdings.items()):
        correlation, reason = _pearson_with_reason(
            candidate_returns,
            holding.daily_returns,
            candidate_symbol,
            symbol,
        )
        if reason:
            return ZERO, reason
        if correlation is None:
            return ZERO, "insufficient_correlation_history"
        if correlation >= Decimal("0.80"):
            high_count += 1
    if high_count >= 2:
        return ZERO, "two_high_correlations"
    if high_count == 1:
        return Decimal("0.50"), ""
    return ONE, ""


def pearson_correlation(
    left: FrozenReturnSeries | Mapping[object, object],
    right: FrozenReturnSeries | Mapping[object, object],
) -> Decimal | None:
    correlation, _ = _pearson_with_reason(left, right, "LEFT", "RIGHT")
    return correlation


def _pearson_with_reason(
    left: FrozenReturnSeries | Mapping[object, object],
    right: FrozenReturnSeries | Mapping[object, object],
    left_symbol: str,
    right_symbol: str,
) -> tuple[Decimal | None, str]:
    left_values, left_reason = _returns(left, left_symbol)
    if left_reason:
        return None, left_reason
    right_values, right_reason = _returns(right, right_symbol)
    if right_reason:
        return None, right_reason
    common = sorted(set(left_values) & set(right_values))[-60:]
    if len(common) < 40:
        return None, "insufficient_correlation_history"
    xs = [left_values[key] for key in common]
    ys = [right_values[key] for key in common]
    mean_x = sum(xs, ZERO) / Decimal(len(xs))
    mean_y = sum(ys, ZERO) / Decimal(len(ys))
    covariance = sum(
        ((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)), ZERO
    )
    variance_x = sum(((x - mean_x) ** 2 for x in xs), ZERO)
    variance_y = sum(((y - mean_y) ** 2 for y in ys), ZERO)
    if variance_x <= 0 or variance_y <= 0:
        return None, "insufficient_correlation_history"
    return covariance / (variance_x * variance_y).sqrt(), ""


def order_quantity(
    portfolio_equity: Decimal | str,
    execution_price: Decimal | str,
    buy_lot_size: int,
    final_target_weight: Decimal | str,
) -> tuple[int, Decimal, str]:
    equity = _required_decimal(portfolio_equity)
    price = _required_decimal(execution_price)
    weight = _required_decimal(final_target_weight)
    if equity <= 0 or price <= 0 or weight < 0:
        raise ValueError("invalid_order_input")
    if not isinstance(buy_lot_size, int) or isinstance(buy_lot_size, bool) or buy_lot_size <= 0:
        raise ValueError("invalid_buy_lot_size")
    raw_qty = equity * weight / price
    lots = (raw_qty / Decimal(buy_lot_size)).to_integral_value(rounding=ROUND_FLOOR)
    quantity = int(lots) * buy_lot_size
    if quantity <= 0:
        return 0, ZERO, "below_min_trade"
    actual_weight = Decimal(quantity) * price / equity
    return quantity, actual_weight, ""


def _qualification_reasons(
    candidate: CandidateInput, holdings: Sequence[ExistingHolding]
) -> list[str]:
    reasons: list[str] = []
    if candidate.opportunity_status != "VALID":
        reasons.append("invalid_opportunity")
    if candidate.opportunity_score < candidate.entry_threshold:
        reasons.append("below_entry_threshold")
    if candidate.opportunity_rank <= 0 or candidate.opportunity_rank > 6:
        reasons.append("rank_not_eligible")
    if candidate.market_paused:
        reasons.append("market_paused")
    if candidate.emergency_gate != "NORMAL":
        reasons.append("emergency_market_gate")
    if candidate.risk_overlay == "BLOCK_NEW":
        reasons.append("risk_overlay_blocked")
    if candidate.execution_gate == "REJECT":
        reasons.append("execution_gate_rejected")
    if candidate.t1_risk_status != "VALID":
        reasons.append("t1_risk_blocked")
    candidate_symbol = _normalized_symbol(candidate.symbol)
    if candidate_symbol is None:
        reasons.append("invalid_candidate_symbol")
    holding_symbols = {
        symbol
        for symbol in (_normalized_symbol(holding.symbol) for holding in holdings)
        if symbol is not None
    }
    if candidate_symbol is not None and candidate_symbol in holding_symbols:
        reasons.append("already_held")
    if candidate.cooldown_blocked:
        reasons.append("cooldown_blocked")
    return reasons


def _returns(
    values: FrozenReturnSeries | Mapping[object, object], symbol: str
) -> tuple[dict[date, Decimal], str]:
    items: Iterable[tuple[object, object]] = (
        values.items() if isinstance(values, Mapping) else values
    )
    grouped: dict[date, set[Decimal]] = {}
    for key, value in items:
        try:
            parsed_day = pd.Timestamp(key)
            if pd.isna(parsed_day):
                return {}, f"invalid_return_date:{symbol}"
            if parsed_day.tzinfo is not None:
                parsed_day = parsed_day.tz_convert(SHANGHAI_TIMEZONE)
            day = parsed_day.date()
            parsed = _required_decimal(value)
        except (TypeError, ValueError, InvalidOperation):
            return {}, "invalid_return_value"
        grouped.setdefault(day, set()).add(parsed)
    result: dict[date, Decimal] = {}
    for day in sorted(grouped):
        distinct = grouped[day]
        if len(distinct) != 1:
            return {}, f"conflicting_return_date:{symbol}:{day.isoformat()}"
        result[day] = next(iter(distinct))
    return result, ""


def _normalized_symbol(value: object) -> str | None:
    try:
        return normalize_security_symbol(str(value))
    except ValueError:
        return None


def _required_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Decimal, str)):
        raise ValueError("invalid_decimal")
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise ValueError("invalid_decimal")
    return parsed
