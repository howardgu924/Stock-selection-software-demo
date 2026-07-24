"""Pure deterministic 14:30 portfolio exit-cycle coordinator."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Iterable, Sequence

import pandas as pd

from stock_picker.strategies.adaptive_trend_v1_3.exit_engine import (
    build_exit_fill_request,
    canonical_exit_priority,
    evaluate_1430_exit,
    select_highest_intent,
)
from stock_picker.strategies.adaptive_trend_v1_3.minute_contract import (
    SHANGHAI_TIMEZONE,
    normalize_security_symbol,
)
from stock_picker.strategies.adaptive_trend_v1_3.pending_sell import (
    create_or_merge_pending,
    revalidate_pending,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase3_models import ExecutionType
from stock_picker.strategies.adaptive_trend_v1_3.phase4b_models import (
    DeriskHoldingInput,
    ExitCycleHoldingInput,
    ExitCycleResult,
    ExitIntent,
    PendingSellState,
    PendingSellStatus,
    PendingUpdateResult,
    ReplacementCandidate,
    ReplacementIncumbent,
)
from stock_picker.strategies.adaptive_trend_v1_3.portfolio_derisk import (
    plan_portfolio_derisk,
    select_replacement_exit,
)


def coordinate_1430_exit_cycle(
    holdings: Sequence[ExitCycleHoldingInput],
    pending_sells: Sequence[PendingSellState],
    replacement_candidates: Sequence[ReplacementCandidate],
    *,
    decision_trade_date: date | str,
    portfolio_equity: Decimal | str,
    existing_exposure: Decimal | str,
    effective_exposure_cap: Decimal | str,
    market_allows_new: bool,
    emergency_normal: bool,
    no_new_slots: bool,
    trading_calendar: Iterable[date | str],
) -> ExitCycleResult:
    """Execute the frozen V1.3.11 nine-step 14:30 order without mutations."""

    day = _date(decision_trade_date)
    calendar = sorted(
        {
            parsed
            for value in trading_calendar
            if (parsed := _date(value)) is not None
        }
    )
    equity = _positive_decimal(portfolio_equity)
    exposure = _unit_decimal(existing_exposure)
    cap = _unit_decimal(effective_exposure_cap)
    if (
        day is None
        or equity is None
        or exposure is None
        or cap is None
        or type(market_allows_new) is not bool
        or type(emergency_normal) is not bool
        or type(no_new_slots) is not bool
    ):
        return _invalid("invalid_exit_cycle_input")

    normalized: dict[str, ExitCycleHoldingInput] = {}
    for item in holdings:
        symbol = _symbol(item.position.symbol)
        if (
            symbol is None
            or symbol in normalized
            or _symbol(item.control.symbol) != symbol
            or type(item.pending_signal_valid) is not bool
        ):
            return _invalid("duplicate_or_invalid_holding_symbol")
        normalized[symbol] = item

    candidate_symbols: set[str] = set()
    for candidate in replacement_candidates:
        symbol = _symbol(candidate.symbol)
        if symbol is None or symbol in candidate_symbols:
            return _invalid("duplicate_or_invalid_candidate_symbol")
        candidate_symbols.add(symbol)

    active_pending: dict[str, PendingSellState] = {}
    for pending in pending_sells:
        symbol = _symbol(pending.symbol)
        if symbol is None:
            return _invalid("invalid_pending_symbol")
        if pending.status == PendingSellStatus.ACTIVE:
            if symbol in active_pending:
                return _invalid("duplicate_active_pending")
            try:
                expected_priority = canonical_exit_priority(pending.reason)
            except ValueError:
                return _invalid("invalid_exit_priority")
            if pending.priority != expected_priority:
                return _invalid("invalid_exit_priority")
            active_pending[symbol] = replace(
                pending,
                symbol=symbol,
                priority=expected_priority,
            )

    pending_updates: list[PendingUpdateResult] = []
    cancelled: list[str] = []
    evaluated_at = _at(day, time(14, 30))
    for symbol, pending in tuple(sorted(active_pending.items())):
        item = normalized.get(symbol)
        position_qty = item.position.total_qty if item is not None else 0
        signal_valid = item.pending_signal_valid if item is not None else False
        update = revalidate_pending(
            pending,
            signal_valid=signal_valid,
            position_qty=position_qty,
            evaluated_at=evaluated_at,
        )
        pending_updates.append(update)
        new_state = update.new_state
        if new_state is not None and new_state.status == PendingSellStatus.ACTIVE:
            if (
                new_state.execution_type != ExecutionType.HARD_EXIT
                and new_state.requires_revalidation
            ):
                new_state = replace(
                    new_state,
                    next_attempt_at=_at(day, time(14, 35)),
                )
                pending_updates[-1] = PendingUpdateResult(
                    "APPLIED",
                    update.previous_state,
                    new_state,
                    update.failure_reason,
                )
            active_pending[symbol] = new_state
        else:
            active_pending.pop(symbol, None)
            if new_state is not None and new_state.status == PendingSellStatus.CANCELLED:
                cancelled.append(symbol)

    soft_intents: list[ExitIntent] = []
    controls: dict[str, object] = {}
    for symbol, item in sorted(normalized.items()):
        result = evaluate_1430_exit(
            item.position,
            item.control,
            decision_trade_date=day,
            p1430=item.p1430,
            previous_ma20=item.previous_ma20,
            previous_ma60=item.previous_ma60,
            ma20_slope5=item.ma20_slope5,
            opportunity_status=item.opportunity_status,
            opportunity_score=item.opportunity_score,
            entry_threshold=item.entry_threshold,
            strong_top_divergence=item.strong_top_divergence,
            normal_top_divergence=item.normal_top_divergence,
            divergence_episode_id=item.divergence_episode_id,
            partial_sell_lot_size=item.partial_sell_lot_size,
            protected=item.protected,
            market_data_valid=item.market_data_valid,
        )
        if result.status.value == "INVALID":
            return _invalid(result.failure_reason or "invalid_soft_exit_input")
        controls[symbol] = result.new_control_state
        if result.selected_intent is not None:
            soft_intents.append(result.selected_intent)

    pending_intents = [
        _pending_as_intent(pending, normalized[symbol], day)
        for symbol, pending in sorted(active_pending.items())
        if symbol in normalized
    ]
    higher_by_symbol = _select_by_symbol((*pending_intents, *soft_intents))
    planned_notional = sum(
        (
            Decimal(min(intent.requested_target_qty, normalized[symbol].position.total_qty))
            * (_positive_decimal(normalized[symbol].p1430) or Decimal("0"))
        )
        for symbol, intent in higher_by_symbol.items()
    )
    higher_weight = min(exposure, planned_notional / equity)

    derisk_inputs: list[DeriskHoldingInput] = []
    for symbol, item in sorted(normalized.items()):
        market_value = _positive_decimal(item.market_value)
        price = _positive_decimal(item.p1430)
        score = _decimal(item.opportunity_score)
        rs60 = _decimal(item.rs60)
        rs20 = _decimal(item.rs20)
        if any(value is None for value in (market_value, price, score, rs60, rs20)):
            return _invalid("invalid_derisk_holding")
        selected = higher_by_symbol.get(symbol)
        derisk_inputs.append(
            DeriskHoldingInput(
                symbol=symbol,
                total_qty=item.position.total_qty,
                sellable_qty=item.position.sellable_qty,
                market_value=market_value,
                p1430=price,
                opportunity_score=score,
                rs60=rs60,
                rs20=rs20,
                partial_sell_lot_size=item.partial_sell_lot_size,
                protected=item.protected,
                higher_priority_full_exit=bool(selected and selected.full_exit),
            )
        )
    derisk = plan_portfolio_derisk(
        derisk_inputs,
        decision_trade_date=day,
        portfolio_equity=equity,
        existing_exposure=exposure,
        effective_exposure_cap=cap,
        higher_priority_planned_sell_weight=higher_weight,
    )
    if derisk.status == "INVALID":
        return _invalid("invalid_derisk_input")

    before_replacement = _select_by_symbol(
        (*pending_intents, *soft_intents, *derisk.intents)
    )
    incumbents: list[ReplacementIncumbent] = []
    for symbol, item in sorted(normalized.items()):
        score = _decimal(item.opportunity_score)
        threshold = _decimal(item.entry_threshold)
        rs60 = _decimal(item.rs60)
        rs20 = _decimal(item.rs20)
        if any(value is None for value in (score, threshold, rs60, rs20)):
            return _invalid("invalid_replacement_incumbent")
        incumbents.append(
            ReplacementIncumbent(
                symbol=symbol,
                total_qty=item.position.total_qty,
                opportunity_score=score,
                entry_threshold=threshold,
                rs60=rs60,
                rs20=rs20,
                protected=item.protected,
                has_active_pending=symbol in active_pending,
                has_higher_exit=symbol in before_replacement,
            )
        )
    replacement = select_replacement_exit(
        incumbents,
        replacement_candidates,
        decision_trade_date=day,
        current_holding_symbols=tuple(sorted(normalized)),
        market_allows_new=market_allows_new,
        emergency_normal=emergency_normal,
        no_new_slots=no_new_slots,
    )
    if replacement.status == "INVALID":
        return _invalid(replacement.failure_reason or "invalid_replacement_input")

    all_intents: list[ExitIntent] = [
        *pending_intents,
        *soft_intents,
        *derisk.intents,
    ]
    if replacement.intent is not None:
        all_intents.append(replacement.intent)
    final_by_symbol = _select_by_symbol(all_intents)

    fill_requests = []
    for symbol, intent in sorted(final_by_symbol.items()):
        item = normalized[symbol]
        position = item.position
        existing = active_pending.get(symbol)
        from_existing = existing is not None and intent.episode_id == existing.episode_id
        desired = (
            min(existing.remaining_qty, position.total_qty)
            if from_existing
            else min(intent.requested_target_qty, position.total_qty)
        )
        executable = min(desired, position.sellable_qty)
        if from_existing:
            if existing.next_attempt_at != _at(day, time(14, 35)):
                executable = 0
        pending_remaining = desired - executable

        if executable > 0:
            request_intent = replace(intent, requested_target_qty=desired)
            fill_requests.append(
                build_exit_fill_request(
                    request_intent,
                    executable_qty=executable,
                    position_qty=position.total_qty,
                    sellable_qty=position.sellable_qty,
                )
            )
        if not from_existing and pending_remaining > 0:
            later_days = [trade_day for trade_day in calendar if trade_day > day]
            if not later_days:
                return _invalid("missing_next_pending_revalidation")
            next_attempt = _at(later_days[0], time(14, 30))
            update = create_or_merge_pending(
                existing,
                intent,
                total_qty=position.total_qty,
                remaining_qty=pending_remaining,
                next_attempt_at=next_attempt,
            )
            pending_updates.append(update)
            if (
                update.new_state is not None
                and update.new_state.status == PendingSellStatus.ACTIVE
            ):
                active_pending[symbol] = update.new_state

    return ExitCycleResult(
        status="VALID",
        intents_by_symbol=tuple(sorted(final_by_symbol.items())),
        fill_requests=tuple(sorted(fill_requests, key=lambda value: value.symbol)),
        pending_updates=tuple(pending_updates),
        cancelled_pending=tuple(sorted(set(cancelled))),
        projected_exposure=derisk.projected_exposure,
        residual_excess=derisk.residual_excess,
        replacement_symbol=(
            replacement.intent.symbol if replacement.intent is not None else ""
        ),
        control_states=tuple(sorted(controls.items())),
        reasons=(),
    )


def _pending_as_intent(
    pending: PendingSellState,
    item: ExitCycleHoldingInput,
    day: date,
) -> ExitIntent:
    price = _positive_decimal(item.p1430)
    return ExitIntent(
        symbol=_symbol(pending.symbol) or pending.symbol,
        decision_trade_date=day,
        decision_time="14:30",
        execution_type=pending.execution_type,
        reason=pending.reason,
        priority=canonical_exit_priority(pending.reason),
        requested_target_qty=pending.remaining_qty,
        full_exit=pending.target_qty >= item.position.total_qty,
        sticky=pending.sticky,
        requires_revalidation=pending.requires_revalidation,
        episode_id=pending.episode_id,
        trigger_bar_start=_at(
            day,
            time(14, 30)
            if pending.execution_type == ExecutionType.HARD_EXIT
            else time(14, 25),
        ),
        trigger_price=price,
        active_stop=None,
        created_at=_at(day, time(14, 30)),
        reasons=(pending.reason,),
    )


def _select_by_symbol(intents: Sequence[ExitIntent]) -> dict[str, ExitIntent]:
    grouped: dict[str, list[ExitIntent]] = {}
    for intent in intents:
        symbol = _symbol(intent.symbol)
        if symbol is None:
            continue
        grouped.setdefault(symbol, []).append(replace(intent, symbol=symbol))
    return {
        symbol: selected
        for symbol, values in sorted(grouped.items())
        if (selected := select_highest_intent(values)) is not None
    }


def _invalid(reason: str) -> ExitCycleResult:
    return ExitCycleResult(
        status="INVALID",
        intents_by_symbol=(),
        fill_requests=(),
        pending_updates=(),
        cancelled_pending=(),
        projected_exposure=Decimal("0"),
        residual_excess=Decimal("0"),
        replacement_symbol="",
        control_states=(),
        reasons=(reason,),
    )


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
