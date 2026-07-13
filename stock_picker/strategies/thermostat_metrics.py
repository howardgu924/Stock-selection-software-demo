from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from math import sqrt
from typing import Iterable

import pandas as pd


METRIC_SUMMARY_COLUMNS = [
    "initial_asset", "final_asset", "total_return", "annualized_return",
    "benchmark_return", "excess_return", "max_drawdown", "sharpe_ratio",
    "annual_volatility", "completed_cycle_count", "completed_cycle_win_rate",
    "profit_loss_ratio", "average_win", "average_loss", "average_holding_days",
    "trade_count", "buy_count", "sell_count", "failed_order_count",
    "pending_order_count", "pending_average_duration_days",
    "average_position_utilization", "max_position_utilization",
    "average_cash_ratio", "missing_data_ratio", "ambiguity_count",
    "corporate_action_affected_symbol_count",
    "corporate_action_affected_date_count", "trading_day_count",
]

CLOSED_TRADE_CYCLE_COLUMNS = [
    "cycle_id", "symbol", "family", "owner_id", "buy_order_id",
    "sell_order_id", "buy_date", "sell_date", "shares", "buy_price",
    "sell_price", "buy_fees", "sell_fees", "gross_pnl", "net_pnl",
    "return_pct", "holding_days", "is_win",
]

EQUITY_DRAWDOWN_COLUMNS = [
    "date", "total_asset", "daily_return", "cumulative_return",
    "running_peak", "drawdown", "precision", "precision_disclosure",
    "approximate_intraday_sequence",
]
PERFORMANCE_COLUMNS = [
    "key", "realized_pnl", "unrealized_pnl", "total_pnl", "closed_cost",
    "open_cost", "invested_cost", "return", "completed_cycles", "wins",
    "win_rate",
]
MARKET_PERFORMANCE_COLUMNS = [
    "benchmark_symbol", "start_date", "end_date", "trading_days",
    "benchmark_return", "annualized_benchmark_return",
]


@dataclass(frozen=True)
class T1ThermostatMetricsResult:
    summary: pd.DataFrame
    equity_drawdown: pd.DataFrame
    closed_trade_cycles: pd.DataFrame
    symbol_performance: pd.DataFrame
    trend_performance: pd.DataFrame
    grid_performance: pd.DataFrame
    market_performance: pd.DataFrame


@dataclass
class _OpenLot:
    order_id: str
    trade_date: pd.Timestamp
    symbol: str
    family: str
    owner_id: str
    remaining_shares: int
    price: float
    remaining_fees: float


def cumulative_realized_net_pnl(fills: pd.DataFrame) -> float:
    """Return fee-aware realized P&L from the FIFO cycles used by reports."""
    cycles, _ = _cycles_and_open_lots(fills, None)
    return float(cycles["net_pnl"].sum()) if not cycles.empty else 0.0


def compute_t1_thermostat_metrics(
    *,
    daily_assets: pd.DataFrame,
    fills: pd.DataFrame,
    lifecycle_orders: pd.DataFrame,
    pending_history: pd.DataFrame,
    data_quality: pd.DataFrame,
    corporate_actions: pd.DataFrame,
    benchmark: pd.DataFrame,
    initial_cash: float,
    daily_positions: pd.DataFrame | None = None,
    daily_trigger_plans: pd.DataFrame | None = None,
    benchmark_symbol: str = "",
) -> T1ThermostatMetricsResult:
    equity = _equity_drawdown(daily_assets, initial_cash)
    cycles, open_lots = _cycles_and_open_lots(fills, daily_positions)
    benchmark_stats = _benchmark_performance(
        benchmark, equity, benchmark_symbol,
    )
    summary = _summary(
        equity=equity,
        daily_assets=daily_assets,
        cycles=cycles,
        fills=fills,
        lifecycle_orders=lifecycle_orders,
        pending_history=pending_history,
        data_quality=data_quality,
        corporate_actions=corporate_actions,
        daily_trigger_plans=daily_trigger_plans,
        initial_cash=initial_cash,
        benchmark_return=(
            float(benchmark_stats.iloc[0]["benchmark_return"])
            if not benchmark_stats.empty else 0.0
        ),
    )
    symbol_performance = _performance(cycles, open_lots, "symbol")
    family_performance = _performance(cycles, open_lots, "family")
    trend = family_performance[family_performance["key"] == "trend"].reset_index(drop=True)
    grid = family_performance[family_performance["key"] == "grid"].reset_index(drop=True)
    return T1ThermostatMetricsResult(
        summary=summary,
        equity_drawdown=equity,
        closed_trade_cycles=cycles,
        symbol_performance=symbol_performance,
        trend_performance=trend.reindex(columns=PERFORMANCE_COLUMNS),
        grid_performance=grid.reindex(columns=PERFORMANCE_COLUMNS),
        market_performance=benchmark_stats,
    )


def _equity_drawdown(daily_assets: pd.DataFrame, initial_cash: float) -> pd.DataFrame:
    if daily_assets is None or daily_assets.empty:
        return pd.DataFrame(columns=EQUITY_DRAWDOWN_COLUMNS)
    frame = daily_assets.copy()
    if "date" not in frame or "total_asset" not in frame:
        return pd.DataFrame(columns=EQUITY_DRAWDOWN_COLUMNS)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["total_asset"] = pd.to_numeric(frame["total_asset"], errors="coerce")
    frame = frame.dropna(subset=["date", "total_asset"]).sort_values("date")
    frame = frame.drop_duplicates("date", keep="last")
    if frame.empty:
        return pd.DataFrame(columns=EQUITY_DRAWDOWN_COLUMNS)
    frame["daily_return"] = frame["total_asset"].pct_change().fillna(0.0)
    frame["cumulative_return"] = (
        frame["total_asset"] / initial_cash - 1.0 if initial_cash else 0.0
    )
    frame["running_peak"] = frame["total_asset"].cummax()
    frame["drawdown"] = frame["total_asset"] / frame["running_peak"] - 1.0
    frame["date"] = frame["date"].dt.strftime("%Y-%m-%d")
    return frame.reindex(columns=EQUITY_DRAWDOWN_COLUMNS).reset_index(drop=True)


def _cycles_and_open_lots(
    fills: pd.DataFrame, daily_positions: pd.DataFrame | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if fills is None or fills.empty:
        return (
            pd.DataFrame(columns=CLOSED_TRADE_CYCLE_COLUMNS),
            pd.DataFrame(columns=["symbol", "family", "open_cost", "unrealized_pnl"]),
        )
    frame = fills.copy().reset_index(drop=True)
    required = {"trade_date", "symbol", "family", "side", "actual_shares", "execution_price"}
    if not required.issubset(frame.columns):
        return (
            pd.DataFrame(columns=CLOSED_TRADE_CYCLE_COLUMNS),
            pd.DataFrame(columns=["symbol", "family", "open_cost", "unrealized_pnl"]),
        )
    frame["_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame["_sequence"] = range(len(frame))
    frame = frame.sort_values(["_date", "_sequence"], kind="stable")
    lots: dict[tuple[str, str], deque[_OpenLot]] = defaultdict(deque)
    rows: list[dict[str, object]] = []
    for record in frame.to_dict("records"):
        shares = _integer(record.get("actual_shares"))
        price = _number(record.get("execution_price"))
        trade_date = record.get("_date")
        symbol = str(record.get("symbol") or "")
        family = str(record.get("origin_strategy_family") or record.get("family") or "")
        side = str(record.get("side") or "").lower()
        if shares <= 0 or price is None or pd.isna(trade_date) or not symbol or not family:
            continue
        owner = _owner_id(record)
        fee = (_number(record.get("commission")) or 0.0) + (
            _number(record.get("stamp_tax")) or 0.0
        )
        if side == "buy":
            lots[(symbol, family)].append(_OpenLot(
                order_id=str(record.get("order_id") or ""),
                trade_date=pd.Timestamp(trade_date), symbol=symbol, family=family,
                owner_id=owner, remaining_shares=shares, price=price,
                remaining_fees=fee,
            ))
            continue
        if side != "sell":
            continue
        candidates = lots[(symbol, family)]
        remaining = shares
        sell_fee_remaining = fee
        sell_order_id = str(record.get("order_id") or "")
        sell_owner = owner
        while remaining > 0:
            lot_index = _eligible_lot_index(candidates, sell_owner)
            if lot_index is None:
                break
            lot = candidates[lot_index]
            matched = min(remaining, lot.remaining_shares)
            buy_fee = lot.remaining_fees * matched / lot.remaining_shares
            sell_fee = sell_fee_remaining * matched / remaining
            gross_pnl = (price - lot.price) * matched
            net_pnl = gross_pnl - buy_fee - sell_fee
            cost = lot.price * matched + buy_fee
            rows.append({
                "cycle_id": f"cycle-{len(rows) + 1:08d}",
                "symbol": symbol, "family": family,
                "owner_id": lot.owner_id,
                "buy_order_id": lot.order_id,
                "sell_order_id": sell_order_id,
                "buy_date": lot.trade_date.strftime("%Y-%m-%d"),
                "sell_date": pd.Timestamp(trade_date).strftime("%Y-%m-%d"),
                "shares": matched, "buy_price": lot.price, "sell_price": price,
                "buy_fees": buy_fee, "sell_fees": sell_fee,
                "gross_pnl": gross_pnl, "net_pnl": net_pnl,
                "return_pct": net_pnl / cost if cost else 0.0,
                "holding_days": (pd.Timestamp(trade_date) - lot.trade_date).days,
                "is_win": bool(net_pnl > 0),
            })
            lot.remaining_shares -= matched
            lot.remaining_fees -= buy_fee
            remaining -= matched
            sell_fee_remaining -= sell_fee
            if lot.remaining_shares == 0:
                del candidates[lot_index]
    marks = _latest_marks(daily_positions)
    open_rows = []
    for queue in lots.values():
        for lot in queue:
            mark = marks.get(lot.symbol, lot.price)
            open_cost = lot.price * lot.remaining_shares + lot.remaining_fees
            open_rows.append({
                "symbol": lot.symbol, "family": lot.family,
                "open_cost": open_cost,
                "unrealized_pnl": (
                    (mark - lot.price) * lot.remaining_shares - lot.remaining_fees
                ),
            })
    return (
        pd.DataFrame(rows).reindex(columns=CLOSED_TRADE_CYCLE_COLUMNS),
        pd.DataFrame(open_rows).reindex(
            columns=["symbol", "family", "open_cost", "unrealized_pnl"],
        ),
    )


def _eligible_lot_index(lots: deque[_OpenLot], owner_id: str) -> int | None:
    for index, lot in enumerate(lots):
        if not owner_id or owner_id == lot.owner_id:
            return index
    return None


def _owner_id(record: dict[str, object]) -> str:
    origin_owner = record.get("origin_owner")
    if origin_owner is not None and not pd.isna(origin_owner):
        return str(origin_owner)
    family = str(record.get("family") or "")
    if family == "grid":
        value = record.get("grid_layer")
        return "" if value is None or pd.isna(value) else str(value)
    value = record.get("trend_batch")
    if value is None or pd.isna(value):
        return ""
    return f"batch-{int(value)}"


def _summary(
    *, equity: pd.DataFrame, daily_assets: pd.DataFrame,
    cycles: pd.DataFrame, fills: pd.DataFrame,
    lifecycle_orders: pd.DataFrame, pending_history: pd.DataFrame,
    data_quality: pd.DataFrame, corporate_actions: pd.DataFrame,
    daily_trigger_plans: pd.DataFrame | None, initial_cash: float,
    benchmark_return: float,
) -> pd.DataFrame:
    final_asset = float(equity.iloc[-1]["total_asset"]) if not equity.empty else float(initial_cash)
    total_return = final_asset / initial_cash - 1.0 if initial_cash else 0.0
    periods = max(len(equity) - 1, 0)
    annualized = (
        (1.0 + total_return) ** (252.0 / periods) - 1.0
        if periods and total_return > -1.0 else (0.0 if periods == 0 else -1.0)
    )
    daily_returns = (
        pd.to_numeric(equity["daily_return"], errors="coerce").dropna().iloc[1:]
        if not equity.empty else pd.Series(dtype="float64")
    )
    volatility = float(daily_returns.std(ddof=0) * sqrt(252)) if len(daily_returns) else 0.0
    sharpe = (
        float(daily_returns.mean() / daily_returns.std(ddof=0) * sqrt(252))
        if len(daily_returns) and daily_returns.std(ddof=0) > 0 else 0.0
    )
    wins = cycles[cycles["net_pnl"] > 0] if not cycles.empty else cycles
    losses = cycles[cycles["net_pnl"] < 0] if not cycles.empty else cycles
    gross_profit = float(wins["net_pnl"].sum()) if not wins.empty else 0.0
    gross_loss = abs(float(losses["net_pnl"].sum())) if not losses.empty else 0.0
    fill_sides = fills.get("side", pd.Series(dtype="object")).astype(str).str.lower()
    statuses = lifecycle_orders.get("status", pd.Series(dtype="object")).astype(str)
    pending_rows = pending_history if pending_history is not None else pd.DataFrame()
    pending_duration = pd.to_numeric(
        pending_rows.get("duration_days", pd.Series(dtype="float64")), errors="coerce",
    ).dropna()
    if "episode_id" in pending_rows and not pending_rows.empty:
        pending_duration = (
            pending_rows.assign(
                duration_days=pd.to_numeric(
                    pending_rows["duration_days"], errors="coerce",
                )
            )
            .dropna(subset=["duration_days"])
            .groupby("episode_id", sort=False)["duration_days"]
            .max()
        )
    utilization = _utilization(daily_assets)
    missing_ratio = _missing_ratio(data_quality)
    ambiguity_count = _ambiguity_count(lifecycle_orders, daily_trigger_plans)
    corporate_symbols = _unique_count(corporate_actions, "symbol")
    corporate_dates = _unique_count(corporate_actions, "date")
    row = {
        "initial_asset": float(initial_cash), "final_asset": final_asset,
        "total_return": total_return, "annualized_return": annualized,
        "benchmark_return": benchmark_return,
        "excess_return": total_return - benchmark_return,
        "max_drawdown": float(equity["drawdown"].min()) if not equity.empty else 0.0,
        "sharpe_ratio": sharpe, "annual_volatility": volatility,
        "completed_cycle_count": len(cycles),
        "completed_cycle_win_rate": len(wins) / len(cycles) if len(cycles) else 0.0,
        "profit_loss_ratio": (
            gross_profit / gross_loss if gross_loss
            else float("nan") if gross_profit else 0.0
        ),
        "average_win": float(wins["net_pnl"].mean()) if not wins.empty else 0.0,
        "average_loss": float(losses["net_pnl"].mean()) if not losses.empty else 0.0,
        "average_holding_days": float(cycles["holding_days"].mean()) if not cycles.empty else 0.0,
        "trade_count": len(fills), "buy_count": int((fill_sides == "buy").sum()),
        "sell_count": int((fill_sides == "sell").sum()),
        "failed_order_count": int(statuses.isin(["failed", "cancelled", "expired"]).sum()),
        "pending_order_count": int(statuses.isin(["pending", "pending_retry"]).sum()),
        "pending_average_duration_days": float(pending_duration.mean()) if len(pending_duration) else 0.0,
        "average_position_utilization": utilization[0],
        "max_position_utilization": utilization[1],
        "average_cash_ratio": utilization[2],
        "missing_data_ratio": missing_ratio,
        "ambiguity_count": ambiguity_count,
        "corporate_action_affected_symbol_count": corporate_symbols,
        "corporate_action_affected_date_count": corporate_dates,
        "trading_day_count": len(equity),
    }
    return pd.DataFrame([row]).reindex(columns=METRIC_SUMMARY_COLUMNS)


def _utilization(equity: pd.DataFrame) -> tuple[float, float, float]:
    if equity.empty:
        return 0.0, 0.0, 1.0
    # The metrics equity table intentionally has only total asset. Callers may
    # merge cash/position values in; deterministic defaults keep empty schemas safe.
    total = pd.to_numeric(equity["total_asset"], errors="coerce")
    if "position_value" in equity:
        position = pd.to_numeric(equity["position_value"], errors="coerce").fillna(0.0)
        ratio = (position / total.where(total != 0)).fillna(0.0)
    else:
        ratio = pd.Series(0.0, index=equity.index)
    if "cash" in equity:
        cash = pd.to_numeric(equity["cash"], errors="coerce").fillna(0.0)
        cash_ratio = (cash / total.where(total != 0)).fillna(0.0)
    else:
        cash_ratio = 1.0 - ratio
    return float(ratio.mean()), float(ratio.max()), float(cash_ratio.mean())


def _missing_ratio(data_quality: pd.DataFrame) -> float:
    if data_quality is None or data_quality.empty:
        return 0.0
    if {"observation_expected", "observation_missing"}.issubset(data_quality.columns):
        expected = data_quality[
            data_quality["observation_expected"].fillna(False).astype(bool)
        ]
        if expected.empty:
            return 0.0
        return float(
            expected["observation_missing"].fillna(False).astype(bool).sum()
            / len(expected)
        )
    if "missing" in data_quality:
        return float(data_quality["missing"].fillna(False).astype(bool).mean())
    codes = data_quality.get("code", pd.Series(dtype="object")).astype(str)
    return float(codes.str.contains("missing|insufficient|gap", case=False, regex=True).mean())


def _ambiguity_count(
    lifecycle_orders: pd.DataFrame, daily_trigger_plans: pd.DataFrame | None,
) -> int:
    count = 0
    for frame, use_boolean_disclosure in (
        (lifecycle_orders, False), (daily_trigger_plans, True),
    ):
        if frame is None or frame.empty:
            continue
        ambiguous = pd.Series(False, index=frame.index)
        for column in ("quality_warning", "approximation_warnings"):
            if column in frame:
                ambiguous |= frame[column].astype(str).str.contains(
                    "approximate_intraday_sequence", regex=False,
                )
        if use_boolean_disclosure and "approximate_intraday_sequence" in frame:
            ambiguous |= frame["approximate_intraday_sequence"].fillna(False).astype(bool)
        count += int(ambiguous.sum())
    return count


def _performance(
    cycles: pd.DataFrame, open_lots: pd.DataFrame, key: str,
) -> pd.DataFrame:
    if cycles.empty and open_lots.empty:
        return pd.DataFrame(columns=PERFORMANCE_COLUMNS)
    rows = []
    keys = set(cycles[key].dropna().astype(str)) if not cycles.empty else set()
    keys.update(open_lots[key].dropna().astype(str) if not open_lots.empty else [])
    for value in sorted(keys):
        group = cycles[cycles[key].astype(str) == value] if not cycles.empty else cycles
        open_group = (
            open_lots[open_lots[key].astype(str) == value]
            if not open_lots.empty else open_lots
        )
        realized = float(group["net_pnl"].sum())
        closed_cost = float((group["buy_price"] * group["shares"] + group["buy_fees"]).sum())
        unrealized = float(open_group["unrealized_pnl"].sum()) if not open_group.empty else 0.0
        open_cost = float(open_group["open_cost"].sum()) if not open_group.empty else 0.0
        total_pnl = realized + unrealized
        invested_cost = closed_cost + open_cost
        wins = int((group["net_pnl"] > 0).sum())
        rows.append({
            "key": value, "realized_pnl": realized,
            "unrealized_pnl": unrealized, "total_pnl": total_pnl,
            "closed_cost": closed_cost, "open_cost": open_cost,
            "invested_cost": invested_cost,
            "return": total_pnl / invested_cost if invested_cost else 0.0,
            "completed_cycles": len(group), "wins": wins,
            "win_rate": wins / len(group) if len(group) else 0.0,
        })
    return pd.DataFrame(rows).reindex(columns=PERFORMANCE_COLUMNS)


def _latest_marks(daily_positions: pd.DataFrame | None) -> dict[str, float]:
    if daily_positions is None or daily_positions.empty:
        return {}
    if not {"symbol", "close"}.issubset(daily_positions.columns):
        return {}
    frame = daily_positions.copy()
    if "date" in frame:
        frame["_date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.sort_values("_date", kind="stable")
    frame = frame.drop_duplicates("symbol", keep="last")
    return {
        str(row["symbol"]): float(row["close"])
        for row in frame.to_dict("records")
        if _number(row.get("close")) is not None
    }


def _benchmark_performance(
    benchmark: pd.DataFrame, equity: pd.DataFrame, benchmark_symbol: str,
) -> pd.DataFrame:
    if benchmark is None or benchmark.empty or equity.empty:
        return pd.DataFrame(columns=MARKET_PERFORMANCE_COLUMNS)
    if not {"date", "close"}.issubset(benchmark.columns):
        return pd.DataFrame(columns=MARKET_PERFORMANCE_COLUMNS)
    left = equity[["date"]].copy()
    left["date"] = pd.to_datetime(left["date"], errors="coerce")
    right = benchmark[["date", "close"]].copy()
    right["date"] = pd.to_datetime(right["date"], errors="coerce")
    right["close"] = pd.to_numeric(right["close"], errors="coerce")
    aligned = left.merge(right, on="date", how="inner").dropna().sort_values("date")
    if aligned.empty:
        return pd.DataFrame(columns=MARKET_PERFORMANCE_COLUMNS)
    first = float(aligned.iloc[0]["close"])
    last = float(aligned.iloc[-1]["close"])
    total = last / first - 1.0 if first else 0.0
    periods = max(len(aligned) - 1, 0)
    annualized = (
        (1.0 + total) ** (252.0 / periods) - 1.0
        if periods and total > -1.0 else 0.0
    )
    return pd.DataFrame([{
        "benchmark_symbol": benchmark_symbol,
        "start_date": aligned.iloc[0]["date"].strftime("%Y-%m-%d"),
        "end_date": aligned.iloc[-1]["date"].strftime("%Y-%m-%d"),
        "trading_days": len(aligned), "benchmark_return": total,
        "annualized_benchmark_return": annualized,
    }]).reindex(columns=MARKET_PERFORMANCE_COLUMNS)


def _number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: object) -> int:
    number = _number(value)
    return max(0, int(number)) if number is not None else 0


def _unique_count(frame: pd.DataFrame, column: str) -> int:
    if frame is None or frame.empty or column not in frame:
        return 0
    return int(frame[column].dropna().astype(str).nunique())
