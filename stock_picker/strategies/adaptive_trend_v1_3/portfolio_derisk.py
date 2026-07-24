"""Portfolio exposure reductions and one-per-day replacement exits."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from typing import Sequence

import pandas as pd

from stock_picker.strategies.adaptive_trend_v1_3.exit_engine import EXIT_PRIORITY
from stock_picker.strategies.adaptive_trend_v1_3.minute_contract import (
    SHANGHAI_TIMEZONE,
    normalize_security_symbol,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase3_models import ExecutionType
from stock_picker.strategies.adaptive_trend_v1_3.phase4b_models import (
    DeriskHoldingInput,
    ExitIntent,
    PortfolioDeriskResult,
    ReplacementCandidate,
    ReplacementIncumbent,
    ReplacementResult,
)


def plan_portfolio_derisk(
    holdings: Sequence[DeriskHoldingInput],
    *,
    decision_trade_date: date | str,
    portfolio_equity: Decimal | str,
    existing_exposure: Decimal | str,
    effective_exposure_cap: Decimal | str,
    higher_priority_planned_sell_weight: Decimal | str,
) -> PortfolioDeriskResult:
    """Reduce only new planned quantities, after higher-priority sells."""

    day = _date(decision_trade_date)
    equity = _positive_decimal(portfolio_equity)
    exposure = _unit_decimal(existing_exposure)
    cap = _unit_decimal(effective_exposure_cap)
    planned = _unit_decimal(higher_priority_planned_sell_weight)
    if day is None or any(value is None for value in (equity, exposure, cap, planned)):
        return PortfolioDeriskResult("INVALID", (), Decimal("0"), Decimal("0"), Decimal("0"), ("invalid_derisk_input",))
    projected = max(Decimal("0"), exposure - planned)
    excess = max(Decimal("0"), projected - cap)
    if excess == 0:
        return PortfolioDeriskResult("NO_ACTION", (), projected, excess, Decimal("0"))

    validated: list[DeriskHoldingInput] = []
    symbols: set[str] = set()
    for item in holdings:
        symbol = _symbol(item.symbol)
        market_value = _positive_decimal(item.market_value)
        price = _positive_decimal(item.p1430)
        score = _decimal(item.opportunity_score)
        rs60 = _decimal(item.rs60)
        rs20 = _decimal(item.rs20)
        if (
            symbol is None
            or symbol in symbols
            or any(value is None for value in (market_value, price, score, rs60, rs20))
            or type(item.total_qty) is not int
            or type(item.sellable_qty) is not int
            or type(item.partial_sell_lot_size) is not int
            or item.total_qty < 0
            or item.sellable_qty < 0
            or item.sellable_qty > item.total_qty
            or item.partial_sell_lot_size <= 0
        ):
            return PortfolioDeriskResult("INVALID", (), projected, excess, excess, ("invalid_derisk_holding",))
        symbols.add(symbol)
        validated.append(
            DeriskHoldingInput(
                symbol=symbol,
                total_qty=item.total_qty,
                sellable_qty=item.sellable_qty,
                market_value=market_value,
                p1430=price,
                opportunity_score=score,
                rs60=rs60,
                rs20=rs20,
                partial_sell_lot_size=item.partial_sell_lot_size,
                protected=item.protected,
                higher_priority_full_exit=item.higher_priority_full_exit,
            )
        )
    remaining_notional = excess * equity
    intents: list[ExitIntent] = []
    created = _at(day, time(14, 30))
    for item in sorted(
        validated,
        key=lambda value: (
            value.opportunity_score,
            value.rs60,
            value.rs20,
            value.symbol,
        ),
    ):
        if item.higher_priority_full_exit or remaining_notional <= 0:
            continue
        target_notional = min(item.market_value, remaining_notional)
        raw_qty = (target_notional / item.p1430).to_integral_value(rounding=ROUND_FLOOR)
        qty = (int(raw_qty) // item.partial_sell_lot_size) * item.partial_sell_lot_size
        qty = min(qty, item.total_qty)
        if qty <= 0:
            continue
        intent = ExitIntent(
            symbol=item.symbol,
            decision_trade_date=day,
            decision_time="14:30",
            execution_type=ExecutionType.ORDINARY_REDUCTION,
            reason="PORTFOLIO_EXPOSURE_REDUCTION",
            priority=EXIT_PRIORITY["PORTFOLIO_EXPOSURE_REDUCTION"],
            requested_target_qty=qty,
            full_exit=qty == item.total_qty,
            sticky=False,
            requires_revalidation=True,
            episode_id=f"PORTFOLIO_DERISK:{day.isoformat()}",
            trigger_bar_start=_at(day, time(14, 25)),
            trigger_price=item.p1430,
            active_stop=None,
            created_at=created,
            reasons=("PORTFOLIO_EXPOSURE_REDUCTION",),
        )
        intents.append(intent)
        remaining_notional = max(Decimal("0"), remaining_notional - Decimal(qty) * item.p1430)
    residual = remaining_notional / equity
    return PortfolioDeriskResult(
        "PLANNED" if intents else "NO_ACTION",
        tuple(intents),
        projected,
        excess,
        residual,
    )


def select_replacement_exit(
    incumbents: Sequence[ReplacementIncumbent],
    candidates: Sequence[ReplacementCandidate],
    *,
    decision_trade_date: date | str,
    current_holding_symbols: Sequence[str],
    market_allows_new: bool,
    emergency_normal: bool,
    no_new_slots: bool,
) -> ReplacementResult:
    """Choose at most one deterministic incumbent; never create a buy order."""

    day = _date(decision_trade_date)
    if day is None:
        return ReplacementResult("INVALID", None, "", "", "invalid_replacement_date")
    if not market_allows_new or not emergency_normal or not no_new_slots:
        return ReplacementResult("NO_ACTION", None, "", "", "replacement_precondition_failed")
    normalized_held = [_symbol(value) for value in current_holding_symbols]
    held = set(normalized_held)
    if None in held or len(held) != len(normalized_held):
        return ReplacementResult("INVALID", None, "", "", "invalid_holding_symbol")

    valid_incumbents: list[ReplacementIncumbent] = []
    incumbent_symbols: set[str] = set()
    for item in incumbents:
        symbol = _symbol(item.symbol)
        score = _decimal(item.opportunity_score)
        threshold = _decimal(item.entry_threshold)
        rs60 = _decimal(item.rs60)
        rs20 = _decimal(item.rs20)
        if (
            symbol is None
            or symbol in incumbent_symbols
            or symbol not in held
            or type(item.total_qty) is not int
            or item.total_qty <= 0
            or any(value is None for value in (score, threshold, rs60, rs20))
            or type(item.protected) is not bool
            or type(item.has_active_pending) is not bool
            or type(item.has_higher_exit) is not bool
        ):
            return ReplacementResult("INVALID", None, "", "", "invalid_replacement_incumbent")
        incumbent_symbols.add(symbol)
        if item.protected or item.has_active_pending or item.has_higher_exit:
            continue
        valid_incumbents.append(
            ReplacementIncumbent(
                symbol,
                item.total_qty,
                score,
                threshold,
                rs60,
                rs20,
                item.protected,
                item.has_active_pending,
                item.has_higher_exit,
            )
        )
    valid_candidates: list[ReplacementCandidate] = []
    candidate_symbols: set[str] = set()
    for item in candidates:
        symbol = _symbol(item.symbol)
        score = _decimal(item.opportunity_score)
        threshold = _decimal(item.entry_threshold)
        rs60 = _decimal(item.rs60)
        rs20 = _decimal(item.rs20)
        signed_er20 = _decimal(item.signed_er20)
        if (
            symbol is None
            or symbol in candidate_symbols
            or any(value is None for value in (score, threshold, rs60, rs20, signed_er20))
            or type(item.final_order_qty) is not int
            or type(item.cooldown_blocked) is not bool
        ):
            return ReplacementResult("INVALID", None, "", "", "invalid_replacement_candidate")
        candidate_symbols.add(symbol)
        if (
            symbol in held
            or item.cooldown_blocked
            or item.final_order_qty <= 0
            or score < threshold
        ):
            continue
        valid_candidates.append(
            ReplacementCandidate(
                symbol,
                score,
                threshold,
                rs60,
                rs20,
                signed_er20,
                item.final_order_qty,
                item.cooldown_blocked,
            )
        )
    if not valid_incumbents or not valid_candidates:
        return ReplacementResult("NO_ACTION", None, "", "", "no_replacement_pair")
    incumbent = sorted(
        valid_incumbents,
        key=lambda item: (item.opportunity_score, item.rs60, item.rs20, item.symbol),
    )[0]
    candidate = sorted(
        valid_candidates,
        key=lambda item: (
            -item.opportunity_score,
            -item.rs60,
            -item.rs20,
            -item.signed_er20,
            item.symbol,
        ),
    )[0]
    if (
        incumbent.opportunity_score >= incumbent.entry_threshold
        or candidate.opportunity_score - incumbent.opportunity_score < Decimal("12")
    ):
        return ReplacementResult("NO_ACTION", None, incumbent.symbol, candidate.symbol, "replacement_score_gap")
    created = _at(day, time(14, 30))
    intent = ExitIntent(
        symbol=incumbent.symbol,
        decision_trade_date=day,
        decision_time="14:30",
        execution_type=ExecutionType.REPLACEMENT_EXIT,
        reason="REPLACEMENT_EXIT",
        priority=EXIT_PRIORITY["REPLACEMENT_EXIT"],
        requested_target_qty=incumbent.total_qty,
        full_exit=True,
        sticky=False,
        requires_revalidation=True,
        episode_id=f"REPLACEMENT:{day.isoformat()}:{candidate.symbol}",
        trigger_bar_start=_at(day, time(14, 25)),
        trigger_price=None,
        active_stop=None,
        created_at=created,
        reasons=("REPLACEMENT_EXIT",),
    )
    return ReplacementResult("TRIGGERED", intent, incumbent.symbol, candidate.symbol)


def _at(day: date, value: time) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, value)).tz_localize(SHANGHAI_TIMEZONE)


def _date(value: object) -> date | None:
    try:
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.tz_convert(SHANGHAI_TIMEZONE)
        return parsed.date()
    except (TypeError, ValueError, OverflowError):
        return None


def _symbol(value: object) -> str | None:
    try:
        return normalize_security_symbol(str(value))
    except ValueError:
        return None


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (Decimal, str)):
        return None
    try:
        parsed = Decimal(value)
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _positive_decimal(value: object) -> Decimal | None:
    parsed = _decimal(value)
    return parsed if parsed is not None and parsed > 0 else None


def _unit_decimal(value: object) -> Decimal | None:
    parsed = _decimal(value)
    return parsed if parsed is not None and Decimal("0") <= parsed <= Decimal("1") else None
