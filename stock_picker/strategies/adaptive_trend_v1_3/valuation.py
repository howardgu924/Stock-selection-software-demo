"""Authoritative Phase 5 session-close mark selection from raw cached inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

import pandas as pd

from .minute_contract import LEGAL_BAR_START_TIMES, SHANGHAI_TIMEZONE, normalize_security_symbol


@dataclass(frozen=True)
class SessionCloseMark:
    status: str
    symbol: str
    trade_date: date
    mark_price: Decimal | None
    mark_bar_start: str
    price_basis_id: str
    source_partition_id: str
    used_previous_mark: bool
    failure_reason: str = ""


def select_session_close_mark(
    *,
    symbol: str,
    trade_date: date,
    bars: pd.DataFrame,
    previous_valid_mark: Mapping[str, Any] | None,
    session_status: str,
    expected_price_basis_id: str,
    allowed_partition_ids: Iterable[str],
) -> SessionCloseMark:
    """Select 14:55, else the last valid completed bar, else suspended-day prior mark."""
    normalized = normalize_security_symbol(symbol)
    allowed = frozenset(str(item) for item in allowed_partition_ids)
    candidates: list[tuple[str, Decimal, str]] = []
    if isinstance(bars, pd.DataFrame):
        for item in bars.copy(deep=True).to_dict("records"):
            selected = _valid_bar(
                item, normalized, trade_date, expected_price_basis_id, allowed
            )
            if selected is not None:
                candidates.append(selected)
    candidates.sort(key=lambda item: item[0])
    if candidates:
        bar_start, close, source = (
            next((item for item in candidates if item[0] == "14:55"), candidates[-1])
        )
        return SessionCloseMark(
            "VALID",normalized,trade_date,close,bar_start,
            expected_price_basis_id,source,False,
        )
    if str(session_status).strip().lower() == "suspended":
        prior = _valid_previous(
            previous_valid_mark, normalized, trade_date, expected_price_basis_id, allowed
        )
        if prior is not None:
            price, source = prior
            return SessionCloseMark(
                "VALID",normalized,trade_date,price,"PREVIOUS_CLOSE",
                expected_price_basis_id,source,True,
            )
    return SessionCloseMark(
        "INVALID",normalized,trade_date,None,"",expected_price_basis_id,"",False,
        "MISSING_MARK_PRICE",
    )


def _valid_bar(item, symbol, trade_date, basis, allowed):
    try:
        if normalize_security_symbol(str(item.get("symbol",""))) != symbol:
            return None
        timestamp = pd.Timestamp(item.get("bar_start"))
        if pd.isna(timestamp):
            return None
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(SHANGHAI_TIMEZONE)
        else:
            timestamp = timestamp.tz_convert(SHANGHAI_TIMEZONE)
        if timestamp.date() != trade_date or timestamp.time().replace(tzinfo=None) not in LEGAL_BAR_START_TIMES:
            return None
        if str(item.get("trade_date",""))[:10] != trade_date.isoformat():
            return None
        if str(item.get("price_basis_id","")) != basis:
            return None
        source = str(item.get("source_partition_id",""))
        if not source or source not in allowed:
            return None
        values = tuple(_decimal(item.get(name)) for name in ("open","high","low","close"))
        if any(value is None or value <= 0 for value in values):
            return None
        open_,high,low,close = values
        if high < max(open_,low,close) or low > min(open_,high,close):
            return None
        if str(item.get("trade_status","normal")).lower() != "normal":
            return None
        return timestamp.strftime("%H:%M"), close, source
    except (TypeError,ValueError,OverflowError):
        return None


def _valid_previous(item, symbol, trade_date, basis, allowed):
    if not isinstance(item, Mapping):
        return None
    try:
        if normalize_security_symbol(str(item.get("symbol",""))) != symbol:
            return None
        prior_date = pd.Timestamp(item.get("trade_date")).date()
        if prior_date >= trade_date or str(item.get("price_basis_id","")) != basis:
            return None
        source = str(item.get("source_partition_id",""))
        price = _decimal(item.get("mark_price",item.get("close")))
        if source not in allowed or price is None or price <= 0:
            return None
        return price,source
    except (TypeError,ValueError,OverflowError):
        return None


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value,bool) or not isinstance(value,(Decimal,str)):
        return None
    try:
        parsed = Decimal(value)
        return parsed if parsed.is_finite() else None
    except (InvalidOperation,ValueError):
        return None
