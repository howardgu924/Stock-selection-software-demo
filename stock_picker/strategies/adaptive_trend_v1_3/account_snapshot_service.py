"""Account profile adapters and immutable run-start snapshots."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from stock_picker.data.models import normalize_symbol

from .phase5_models import AccountProfile, AccountSnapshot, Phase5Error, RunMode
from .run_store import stable_hash


def create_account_snapshot(
    profile: AccountProfile,
    run_mode: RunMode | str,
    *,
    paper_positions: Iterable[tuple[str, Any]] = (),
    initial_portfolio: Iterable[tuple[str, Any]] | None = None,
) -> AccountSnapshot:
    try:
        mode = RunMode(run_mode)
    except (ValueError, TypeError):
        raise Phase5Error("INVALID_CONFIG", "invalid_run_mode") from None
    cash = _decimal(profile.backtest_initial_cash if mode == RunMode.BACKTEST else profile.paper_cash)
    if cash is None or cash < 0:
        raise Phase5Error("INVALID_CONFIG", "invalid_account_cash")
    if mode == RunMode.BACKTEST:
        positions_source = () if initial_portfolio is None else initial_portfolio
    else:
        positions_source = paper_positions
    positions = _positions(positions_source)
    now = datetime.now().astimezone().isoformat()
    payload = {
        "profile": profile.account_profile_id, "mode": mode.value, "cash": cash,
        "positions": positions, "fee_schedule": profile.fee_schedule_id,
        "currency": profile.base_currency, "provider_priority":profile.provider_priority,
        "data_directory":str(profile.data_directory),"report_directory":str(profile.report_directory),
        "initial_position_policy":"EXPLICIT" if initial_portfolio is not None else "EMPTY",
    }
    digest = stable_hash(payload)
    return AccountSnapshot(
        account_snapshot_id=f"account_{digest}", account_profile_id=profile.account_profile_id,
        run_mode=mode, cash=cash, positions=positions, fee_schedule_id=profile.fee_schedule_id,
        base_currency=profile.base_currency, created_at=now, snapshot_hash=digest,
        provider_priority=tuple(profile.provider_priority),data_directory=str(profile.data_directory),
        report_directory=str(profile.report_directory),
        initial_position_policy="EXPLICIT" if initial_portfolio is not None else "EMPTY",
    )


def validate_profile_paths(profile: AccountProfile) -> tuple[Path, Path]:
    data = Path(profile.data_directory).expanduser()
    report = Path(profile.report_directory).expanduser()
    if not data.is_absolute() or not report.is_absolute():
        raise Phase5Error("INVALID_CONFIG", "paths_must_be_absolute")
    return data.resolve(), report.resolve()


def _positions(values: Iterable[tuple[str, Any]]) -> tuple[tuple[str, Any], ...]:
    result: dict[str, Any] = {}
    for symbol, state in values:
        normalized = normalize_symbol(symbol)
        if normalized in result:
            raise Phase5Error("INVALID_CONFIG", f"duplicate_position:{normalized}")
        result[normalized] = state
    return tuple(sorted(result.items()))


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (Decimal, str)):
        return None
    try:
        parsed = Decimal(value)
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, ValueError):
        return None
