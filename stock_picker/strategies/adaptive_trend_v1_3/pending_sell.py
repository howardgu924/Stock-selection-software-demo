"""Deterministic immutable PendingSell lifecycle for Phase 4B."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time
from typing import Iterable

import pandas as pd

from stock_picker.strategies.adaptive_trend_v1_3.exit_engine import (
    EXIT_PRIORITY,
    canonical_exit_priority,
    valid_exit_intent,
)
from stock_picker.strategies.adaptive_trend_v1_3.minute_contract import (
    SHANGHAI_TIMEZONE,
    normalize_security_symbol,
    resolve_next_execution_bar,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase3_models import (
    ExecutionType,
    FillSide,
    FillResult,
    FillStatus,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase4b_models import (
    AttemptIdentity,
    ExitIntent,
    PendingSellState,
    PendingSellStatus,
    PendingUpdateResult,
)


def create_or_merge_pending(
    existing: PendingSellState | None,
    intent: ExitIntent,
    *,
    total_qty: int,
    remaining_qty: int,
    next_attempt_at: object,
) -> PendingUpdateResult:
    """Keep at most one ACTIVE record per normalized symbol without accumulation."""

    symbol = _symbol(intent.symbol)
    attempt = _timestamp(next_attempt_at)
    if (
        symbol is None
        or not valid_exit_intent(intent)
        or type(total_qty) is not int
        or type(remaining_qty) is not int
        or total_qty < 0
        or remaining_qty < 0
        or remaining_qty > total_qty
        or attempt is None
    ):
        reason = "invalid_exit_priority" if not _valid_intent_priority(intent) else "invalid_pending_input"
        return PendingUpdateResult("INVALID", existing, existing, reason)
    if total_qty == 0 or remaining_qty == 0:
        if existing is None:
            return PendingUpdateResult("NO_ACTION", None, None)
        completed = replace(
            existing,
            status=PendingSellStatus.COMPLETED,
            remaining_qty=0,
            completed_at=intent.created_at,
        )
        return PendingUpdateResult("APPLIED", existing, completed)
    if (
        existing is not None
        and existing.status == PendingSellStatus.ACTIVE
        and not _valid_pending_priority(existing)
    ):
        return PendingUpdateResult(
            "INVALID",
            existing,
            existing,
            "invalid_exit_priority",
        )
    target = total_qty if intent.full_exit else min(intent.requested_target_qty, total_qty)
    target = max(target, remaining_qty)
    if existing is None or existing.status != PendingSellStatus.ACTIVE:
        pending = PendingSellState(
            symbol=symbol,
            status=PendingSellStatus.ACTIVE,
            reason=intent.reason,
            priority=canonical_exit_priority(intent.reason),
            execution_type=intent.execution_type,
            target_qty=target,
            remaining_qty=remaining_qty,
            created_at=intent.created_at,
            next_attempt_at=attempt,
            sticky=intent.sticky,
            requires_revalidation=intent.requires_revalidation,
            episode_id=intent.episode_id,
        )
        return PendingUpdateResult("APPLIED", existing, pending)
    if _symbol(existing.symbol) != symbol:
        return PendingUpdateResult("INVALID", existing, existing, "pending_symbol_mismatch")
    if existing.reason == intent.reason and existing.episode_id == intent.episode_id:
        return PendingUpdateResult("UNCHANGED", existing, existing)
    if not _intent_wins(intent, existing):
        return PendingUpdateResult("UNCHANGED", existing, existing)
    pending = replace(
        existing,
        reason=intent.reason,
        priority=canonical_exit_priority(intent.reason),
        execution_type=intent.execution_type,
        target_qty=max(existing.remaining_qty, target),
        remaining_qty=max(existing.remaining_qty, remaining_qty),
        next_attempt_at=attempt,
        sticky=intent.sticky,
        requires_revalidation=intent.requires_revalidation,
        episode_id=intent.episode_id,
    )
    return PendingUpdateResult("APPLIED", existing, pending)


def apply_pending_fill_result(
    pending: PendingSellState,
    fill: FillResult,
    *,
    position_qty_after_fill: int,
    attempt_at: object,
    trading_calendar: Iterable[date | str],
) -> PendingUpdateResult:
    """Advance pending only after Phase 3 FillEngine returns a result."""

    attempt = _timestamp(attempt_at)
    identity = _attempt_identity(pending, attempt)
    if attempt is None or identity is None:
        return PendingUpdateResult("INVALID", pending, pending, "pending_not_active")
    if pending.last_processed_attempt == identity:
        return PendingUpdateResult("UNCHANGED", pending, pending)
    if (
        pending.last_processed_attempt is not None
        and identity.attempt_bar_start < pending.last_processed_attempt.attempt_bar_start
    ):
        return PendingUpdateResult("UNCHANGED", pending, pending, "stale_attempt")
    if pending.status != PendingSellStatus.ACTIVE:
        return PendingUpdateResult("INVALID", pending, pending, "pending_not_active")
    if type(position_qty_after_fill) is not int or position_qty_after_fill < 0:
        return PendingUpdateResult("INVALID", pending, pending, "invalid_position_qty")
    if not _fill_matches_attempt(pending, fill, identity):
        return _error(pending, attempt, "fill_contract_mismatch", identity)
    if position_qty_after_fill == 0:
        completed = replace(
            pending,
            status=PendingSellStatus.COMPLETED,
            remaining_qty=0,
            last_attempt_at=attempt,
            completed_at=attempt,
            last_processed_attempt=identity,
        )
        return PendingUpdateResult("APPLIED", pending, completed)
    if fill.status == FillStatus.FILLED:
        if type(fill.filled_qty) is not int or fill.filled_qty <= 0 or fill.filled_qty > pending.remaining_qty:
            return _error(pending, attempt, "fill_contract_mismatch", identity)
        remaining = pending.remaining_qty - fill.filled_qty
        updated = replace(
            pending,
            remaining_qty=remaining,
            last_attempt_at=attempt,
            status=(PendingSellStatus.COMPLETED if remaining == 0 else PendingSellStatus.ACTIVE),
            completed_at=(attempt if remaining == 0 else None),
            last_processed_attempt=identity,
        )
        return PendingUpdateResult("APPLIED", pending, updated)
    if fill.status == FillStatus.FAILED and fill.retryable:
        next_attempt = next_pending_attempt(pending, attempt, trading_calendar)
        if next_attempt is None:
            return _error(pending, attempt, "missing_next_attempt")
        updated = replace(
            pending,
            retry_count=pending.retry_count + 1,
            last_failure=fill.failure_reason,
            last_attempt_at=attempt,
            next_attempt_at=next_attempt,
            last_processed_attempt=identity,
        )
        return PendingUpdateResult("APPLIED", pending, updated)
    return _error(
        pending,
        attempt,
        fill.failure_reason or "non_retryable_fill_failure",
        identity,
    )


def revalidate_pending(
    pending: PendingSellState,
    *,
    signal_valid: bool,
    position_qty: int,
    evaluated_at: object,
) -> PendingUpdateResult:
    """Cancel non-sticky signals that no longer hold; sticky signals remain active."""

    evaluated = _timestamp(evaluated_at)
    if pending.status != PendingSellStatus.ACTIVE or evaluated is None:
        return PendingUpdateResult("INVALID", pending, pending, "pending_not_active")
    if type(position_qty) is not int or position_qty < 0:
        return PendingUpdateResult("INVALID", pending, pending, "invalid_position_qty")
    if position_qty == 0:
        completed = replace(
            pending,
            status=PendingSellStatus.COMPLETED,
            remaining_qty=0,
            completed_at=evaluated,
        )
        return PendingUpdateResult("APPLIED", pending, completed)
    if pending.sticky or not pending.requires_revalidation or signal_valid:
        return PendingUpdateResult("UNCHANGED", pending, pending)
    cancelled = replace(
        pending,
        status=PendingSellStatus.CANCELLED,
        cancelled_reason="signal_no_longer_valid",
        last_attempt_at=evaluated,
    )
    return PendingUpdateResult("APPLIED", pending, cancelled)


def next_pending_attempt(
    pending: PendingSellState,
    last_attempt_at: object,
    trading_calendar: Iterable[date | str],
) -> pd.Timestamp | None:
    last = _timestamp(last_attempt_at)
    if last is None:
        return None
    if pending.execution_type == ExecutionType.HARD_EXIT:
        resolution = resolve_next_execution_bar(last, trading_calendar)
        return resolution.execution_bar_start if resolution.status == "VALID" else None
    calendar = sorted({_date(value) for value in trading_calendar} - {None})
    later = [day for day in calendar if day > last.date()]
    return _at(later[0], time(14, 30)) if later else None


def initial_pending_attempt(
    intent: ExitIntent,
    trading_calendar: Iterable[date | str],
) -> pd.Timestamp | None:
    """Resolve the first attempt for the T+1-unsellable part of an intent.

    A hard intent's unsellable quantity cannot use another bar on its buy day;
    it first becomes eligible at 09:30 on the next actual trading day.  A
    non-hard intent is first attempted at the frozen same-day 14:35 bar.
    """

    calendar = sorted({_date(value) for value in trading_calendar} - {None})
    if intent.execution_type == ExecutionType.HARD_EXIT:
        later = [day for day in calendar if day > intent.decision_trade_date]
        return _at(later[0], time(9, 30)) if later else None
    if intent.decision_trade_date not in calendar:
        return None
    return _at(intent.decision_trade_date, time(14, 35))


def _intent_wins(intent: ExitIntent, pending: PendingSellState) -> bool:
    new_priority = canonical_exit_priority(intent.reason)
    try:
        old_priority = canonical_exit_priority(pending.reason)
    except ValueError:
        return True
    if new_priority != old_priority:
        return new_priority > old_priority
    order = tuple(EXIT_PRIORITY)
    new_index = order.index(intent.reason) if intent.reason in order else len(order)
    old_index = order.index(pending.reason) if pending.reason in order else len(order)
    return new_index < old_index


def _error(
    pending: PendingSellState,
    attempt: pd.Timestamp,
    reason: str,
    identity: AttemptIdentity | None = None,
) -> PendingUpdateResult:
    updated = replace(
        pending,
        status=PendingSellStatus.ERROR,
        last_failure=reason,
        last_attempt_at=attempt,
        last_processed_attempt=identity,
    )
    return PendingUpdateResult("APPLIED", pending, updated)


def _valid_intent_priority(intent: object) -> bool:
    try:
        return (
            isinstance(intent, ExitIntent)
            and intent.priority == canonical_exit_priority(intent.reason)
        )
    except ValueError:
        return False


def _valid_pending_priority(pending: PendingSellState) -> bool:
    try:
        return pending.priority == canonical_exit_priority(pending.reason)
    except ValueError:
        return False


def _attempt_identity(
    pending: PendingSellState,
    attempt: pd.Timestamp | None,
) -> AttemptIdentity | None:
    symbol = _symbol(pending.symbol)
    if symbol is None or attempt is None:
        return None
    return AttemptIdentity(
        normalized_symbol=symbol,
        execution_type=pending.execution_type,
        attempt_trade_date=attempt.date(),
        attempt_bar_start=attempt,
    )


def _fill_matches_attempt(
    pending: PendingSellState,
    fill: FillResult,
    identity: AttemptIdentity,
) -> bool:
    fill_bar = _timestamp(fill.execution_bar_start)
    fill_day = _date(fill.execution_trade_date)
    return (
        fill.side == FillSide.SELL
        and fill.execution_type == pending.execution_type
        and _symbol(fill.symbol) == identity.normalized_symbol
        and type(fill.requested_qty) is int
        and fill.requested_qty > 0
        and fill.requested_qty <= pending.remaining_qty
        and type(fill.filled_qty) is int
        and fill.filled_qty >= 0
        and fill.filled_qty <= fill.requested_qty
        and fill.filled_qty <= pending.remaining_qty
        and fill_day == identity.attempt_trade_date
        and fill_bar == identity.attempt_bar_start
        and (fill.status != FillStatus.FILLED or fill.filled_qty > 0)
        and (fill.status == FillStatus.FILLED or fill.filled_qty == 0)
    )


def _at(day: date, value: time) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, value)).tz_localize(SHANGHAI_TIMEZONE)


def _timestamp(value: object) -> pd.Timestamp | None:
    try:
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            return None
        return parsed.tz_localize(SHANGHAI_TIMEZONE) if parsed.tzinfo is None else parsed.tz_convert(SHANGHAI_TIMEZONE)
    except (TypeError, ValueError, OverflowError):
        return None


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
