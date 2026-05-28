from __future__ import annotations

import math
import pandas as pd

from stock_picker.data.models import normalize_symbol, symbol_code


PLAN_COLUMNS = [
    "strategy",
    "symbol",
    "name",
    "signal_action",
    "signal_date",
    "system",
    "score",
    "rank",
    "price",
    "prev_close",
    "limit_pct",
    "limit_up_price",
    "limit_status",
    "executable",
    "shares",
    "suggested_price",
    "suggested_shares",
    "estimated_cost",
    "recommended_action",
    "fallback_action",
    "next_day_max_price",
    "alternative_symbol",
    "stop_price",
    "next_add_price",
    "exit_price",
    "reason",
]


def build_execution_plan(
    strategy_results: pd.DataFrame,
    quotes: pd.DataFrame,
    cash: float,
    lot_size: int = 100,
    max_positions: int = 1,
    commission_rate: float = 0.0003,
    min_commission: float = 5.0,
    next_day_premium: float = 0.02,
    volume_limit_pct: float = 0.10,
) -> pd.DataFrame:
    if cash <= 0:
        raise ValueError("cash must be greater than 0")
    if lot_size < 1:
        raise ValueError("lot_size must be greater than 0")
    if max_positions < 1:
        raise ValueError("max_positions must be greater than 0")
    if next_day_premium < 0:
        raise ValueError("next_day_premium must be greater than or equal to 0")
    if volume_limit_pct <= 0:
        raise ValueError("volume_limit_pct must be greater than 0")

    signals = _prepare_signals(strategy_results)
    quote_map = _quote_map(quotes)
    rows: list[dict[str, object]] = []
    buy_rows = signals[signals["action"] == "buy"].copy()
    if buy_rows.empty:
        return pd.DataFrame(columns=PLAN_COLUMNS)

    allocation = cash / max_positions
    for signal in buy_rows.itertuples(index=False):
        quote = quote_map.get(signal.symbol)
        rows.append(
            _plan_row(
                signal=signal,
                quote=quote,
                cash=allocation,
                lot_size=lot_size,
                commission_rate=commission_rate,
                min_commission=min_commission,
                next_day_premium=next_day_premium,
                volume_limit_pct=volume_limit_pct,
            )
        )

    plan = pd.DataFrame(rows, columns=PLAN_COLUMNS)
    alternative = _best_executable_symbol(plan)
    for index, row in plan.iterrows():
        if row["recommended_action"] == "queue_limit_up" and alternative:
            if alternative != row["symbol"]:
                plan.at[index, "alternative_symbol"] = alternative
                plan.at[index, "fallback_action"] = "switch_alternative"
                plan.at[index, "reason"] = (
                    f"{row['reason']}; alternative={alternative}"
                )
    return plan


def price_limit_pct(symbol: str, name: str | None = None) -> float:
    code = symbol_code(symbol)
    display_name = name or ""
    if "ST" in display_name.upper() or "*ST" in display_name.upper():
        return 0.05
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("4", "8", "9")):
        return 0.30
    return 0.10


def limit_up_price(prev_close: float, limit_pct: float) -> float:
    return round(float(prev_close) * (1 + limit_pct), 2)


def is_limit_up(
    price: float,
    high: float | None,
    prev_close: float,
    limit_pct: float,
    tolerance: float = 0.001,
) -> bool:
    target = limit_up_price(prev_close, limit_pct)
    values = [price]
    if high is not None and _is_finite(high):
        values.append(float(high))
    return any(_is_finite(value) and float(value) >= target - tolerance for value in values)


def _plan_row(
    signal,
    quote: dict[str, object] | None,
    cash: float,
    lot_size: int,
    commission_rate: float,
    min_commission: float,
    next_day_premium: float,
    volume_limit_pct: float,
) -> dict[str, object]:
    if quote is None:
        return _empty_plan_row(signal, "missing quote")

    price = _number(quote.get("price"))
    prev_close = _number(quote.get("prev_close"))
    high = _number(quote.get("high"))
    volume = _number(quote.get("volume"))
    name = str(quote.get("name") or signal.name or "")
    if not _is_finite(price) or price <= 0:
        return _empty_plan_row(signal, "missing price", name=name)
    if not _is_finite(prev_close) or prev_close <= 0:
        return _empty_plan_row(signal, "missing previous close", name=name, price=price)

    pct = price_limit_pct(signal.symbol, name)
    up_price = limit_up_price(prev_close, pct)
    limit = is_limit_up(price, high, prev_close, pct)
    shares, fixed_requested_size = _requested_shares(signal, cash, price, lot_size)
    if not fixed_requested_size:
        shares = _affordable_lot_shares(cash, price, lot_size, commission_rate, min_commission)
    fee = max(shares * price * commission_rate, min_commission) if shares > 0 else 0.0
    estimated_cost = shares * price + fee
    affordable = shares > 0 and estimated_cost <= cash
    liquid = not _is_finite(volume) or volume <= 0 or shares <= volume * volume_limit_pct

    if not affordable:
        recommended = "skip_insufficient_cash"
        fallback = None
        executable = False
        status = "limit_up" if limit else "normal"
        reason = "buy signal but cash is insufficient for one lot or requested turtle unit"
    elif limit:
        recommended = "queue_limit_up"
        fallback = "buy_next_day_below_limit"
        executable = False
        status = "limit_up"
        reason = (
            f"buy signal but price is at limit-up {up_price:.2f}; "
            f"wait for order book fill, otherwise buy next day only below "
            f"{up_price * (1 + next_day_premium):.2f} or switch to alternative"
        )
    elif not liquid:
        recommended = "skip_volume_limit"
        fallback = None
        executable = False
        status = "normal"
        reason = f"buy signal but suggested shares exceed {volume_limit_pct:.0%} of quoted volume"
    else:
        recommended = "buy_now"
        fallback = None
        executable = True
        status = "normal"
        reason = "buy signal is executable under current quote"

    return {
        "strategy": signal.strategy,
        "symbol": signal.symbol,
        "name": name,
        "signal_action": signal.action,
        "signal_date": getattr(signal, "date", None),
        "system": getattr(signal, "system", None),
        "score": signal.score,
        "rank": signal.rank,
        "price": price,
        "prev_close": prev_close,
        "limit_pct": pct,
        "limit_up_price": up_price,
        "limit_status": status,
        "executable": executable,
        "shares": shares if executable else 0,
        "suggested_price": price if executable else (up_price if limit else None),
        "suggested_shares": shares if affordable else 0,
        "estimated_cost": estimated_cost if executable else 0.0,
        "recommended_action": recommended,
        "fallback_action": fallback,
        "next_day_max_price": up_price * (1 + next_day_premium),
        "alternative_symbol": None,
        "stop_price": getattr(signal, "stop_price", None),
        "next_add_price": getattr(signal, "next_add_price", None),
        "exit_price": getattr(signal, "exit_price", None),
        "reason": reason,
    }


def _empty_plan_row(
    signal,
    reason: str,
    name: str | None = None,
    price: float | None = None,
) -> dict[str, object]:
    return {
        "strategy": signal.strategy,
        "symbol": signal.symbol,
        "name": name or signal.name,
        "signal_action": signal.action,
        "signal_date": getattr(signal, "date", None),
        "system": getattr(signal, "system", None),
        "score": signal.score,
        "rank": signal.rank,
        "price": price,
        "prev_close": None,
        "limit_pct": None,
        "limit_up_price": None,
        "limit_status": "unknown",
        "executable": False,
        "shares": 0,
        "suggested_price": None,
        "suggested_shares": 0,
        "estimated_cost": 0.0,
        "recommended_action": "skip",
        "fallback_action": None,
        "next_day_max_price": None,
        "alternative_symbol": None,
        "stop_price": getattr(signal, "stop_price", None),
        "next_add_price": getattr(signal, "next_add_price", None),
        "exit_price": getattr(signal, "exit_price", None),
        "reason": reason,
    }


def _prepare_signals(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    for column in [
        "strategy",
        "symbol",
        "name",
        "action",
        "date",
        "score",
        "rank",
        "system",
        "unit_shares",
        "suggested_shares",
        "stop_price",
        "next_add_price",
        "exit_price",
    ]:
        if column not in data:
            data[column] = None
    data["symbol"] = data["symbol"].map(normalize_symbol)
    data["action"] = data["action"].astype(str).str.lower()
    data["score"] = pd.to_numeric(data["score"], errors="coerce").fillna(0.0)
    data["rank"] = pd.to_numeric(data["rank"], errors="coerce")
    return data.sort_values(["score", "rank"], ascending=[False, True])


def _quote_map(quotes: pd.DataFrame) -> dict[str, dict[str, object]]:
    if quotes.empty:
        return {}
    frame = quotes.copy()
    frame["symbol"] = frame["symbol"].map(normalize_symbol)
    return {str(row["symbol"]): row.to_dict() for _, row in frame.iterrows()}


def _best_executable_symbol(plan: pd.DataFrame) -> str | None:
    candidates = plan[plan["executable"] == True].copy()  # noqa: E712
    if candidates.empty:
        return None
    candidates["score"] = pd.to_numeric(candidates["score"], errors="coerce").fillna(0.0)
    return str(candidates.sort_values("score", ascending=False).iloc[0]["symbol"])


def _lot_shares(value: float, lot_size: int) -> int:
    if not _is_finite(value):
        return 0
    if lot_size <= 1:
        return max(int(value), 0)
    return max(int(value // lot_size) * lot_size, 0)


def _requested_shares(signal, cash: float, price: float, lot_size: int) -> tuple[int, bool]:
    for column in ("suggested_shares", "unit_shares"):
        value = getattr(signal, column, None)
        if _is_finite(value) and float(value) > 0:
            return _lot_shares(float(value), lot_size), True
    return _lot_shares(cash / price, lot_size), False


def _affordable_lot_shares(
    cash: float,
    price: float,
    lot_size: int,
    commission_rate: float,
    min_commission: float,
) -> int:
    shares = _lot_shares(cash / price, lot_size)
    while shares > 0:
        fee = max(shares * price * commission_rate, min_commission)
        if shares * price + fee <= cash:
            return shares
        shares -= lot_size
    return 0


def _number(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return result


def _is_finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
