from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LimitStatus(StrEnum):
    NORMAL = "normal"
    LIMIT_UP = "limit_up"
    LIMIT_DOWN = "limit_down"
    SUSPENDED = "suspended"
    UNKNOWN = "limit_status_unknown"


@dataclass(frozen=True)
class LimitPrices:
    limit_up_price: float | None
    limit_down_price: float | None
    warning: str = ""


def estimate_limit_prices(
    prev_close: float | None,
    board: str | None = None,
    is_st: bool | None = False,
) -> LimitPrices:
    if prev_close is None or prev_close <= 0:
        return LimitPrices(None, None, "missing prev_close; cannot estimate limit prices")
    if board is None:
        return LimitPrices(None, None, "missing board; cannot estimate limit prices")
    pct = _limit_pct(board, bool(is_st))
    return LimitPrices(
        limit_up_price=round(prev_close * (1 + pct), 2),
        limit_down_price=round(prev_close * (1 - pct), 2),
    )


def execution_limit_status(
    price: float | None,
    limit_up_price: float | None,
    limit_down_price: float | None,
    is_suspended: bool | None = False,
) -> LimitStatus:
    if is_suspended:
        return LimitStatus.SUSPENDED
    if price is None or limit_up_price is None or limit_down_price is None:
        return LimitStatus.UNKNOWN
    tolerance = 0.0001
    if price >= limit_up_price - tolerance:
        return LimitStatus.LIMIT_UP
    if price <= limit_down_price + tolerance:
        return LimitStatus.LIMIT_DOWN
    return LimitStatus.NORMAL


def _limit_pct(board: str, is_st: bool) -> float:
    if is_st:
        return 0.05
    normalized = board.strip().lower()
    if normalized in {"star", "科创板", "科创", "chinext", "创业板", "growth"}:
        return 0.20
    return 0.10

