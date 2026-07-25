"""Historical point-in-time rule and fee snapshot validation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable

import pandas as pd

from .minute_contract import SHANGHAI_TIMEZONE, normalize_security_symbol
from .phase3_models import FeeRuleSnapshot, TradingRuleSnapshot
from .phase5_models import Phase5Error


def select_trading_rule_snapshot(
    records: Iterable[object], symbol: str, execution_at: object
) -> TradingRuleSnapshot:
    target = normalize_security_symbol(symbol)
    timestamp = _timestamp(execution_at)
    if timestamp is None:
        raise Phase5Error("RULE_SNAPSHOT_MISSING")
    valid = [
        item for item in records
        if _symbol(item) == target
        and _date(getattr(item, "effective_date", None)) == timestamp.date()
        and (_timestamp(getattr(item, "known_at", None)) or _future()) <= timestamp
    ]
    if not valid:
        raise Phase5Error("RULE_SNAPSHOT_MISSING")
    valid.sort(key=lambda item: (_timestamp(getattr(item, "known_at", None)), str(getattr(item, "rule_version", ""))))
    return valid[-1].rule if hasattr(valid[-1], "rule") else valid[-1]


def select_fee_rule_snapshot(
    records: Iterable[object], account_profile_id: str, execution_at: object
) -> FeeRuleSnapshot:
    timestamp = _timestamp(execution_at)
    if timestamp is None:
        raise Phase5Error("FEE_SNAPSHOT_MISSING")
    valid = [
        item for item in records
        if str(getattr(item, "account_profile_id", account_profile_id)) == account_profile_id
        and _date(getattr(item, "effective_date", None)) == timestamp.date()
        and (_timestamp(getattr(item, "known_at", None)) or _future()) <= timestamp
    ]
    if not valid:
        raise Phase5Error("FEE_SNAPSHOT_MISSING")
    valid.sort(key=lambda item: (_timestamp(getattr(item, "known_at", None)), str(getattr(item, "fee_version", ""))))
    return valid[-1].rule if hasattr(valid[-1], "rule") else valid[-1]


def _symbol(item: object) -> str:
    try:
        return normalize_security_symbol(str(getattr(item, "symbol")))
    except (AttributeError, ValueError):
        return ""


def _timestamp(value: object) -> pd.Timestamp | None:
    try:
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            return None
        return parsed.tz_localize(SHANGHAI_TIMEZONE) if parsed.tzinfo is None else parsed.tz_convert(SHANGHAI_TIMEZONE)
    except (TypeError, ValueError, OverflowError):
        return None


def _date(value: object) -> date | None:
    parsed = _timestamp(value)
    return None if parsed is None else parsed.date()


def _future() -> pd.Timestamp:
    return pd.Timestamp.max.tz_localize("UTC").tz_convert(SHANGHAI_TIMEZONE)
