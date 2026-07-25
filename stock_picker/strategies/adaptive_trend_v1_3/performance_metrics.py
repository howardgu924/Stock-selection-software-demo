"""Frozen V1.3.13 daily performance and benchmark formulas."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from math import sqrt
from statistics import mean, stdev
from typing import Iterable, Mapping, Sequence

from .phase5_models import PerformanceMetrics, Phase5Error

BENCHMARK_WEIGHTS = {
    "000300.SH": Decimal("0.40"),
    "000852.SH": Decimal("0.40"),
    "399006.SZ": Decimal("0.20"),
}


def calculate_performance_metrics(
    daily_rows: Sequence[Mapping[str, object]],
    fills: Sequence[Mapping[str, object]] = (),
) -> PerformanceMetrics:
    equities = [_decimal(row.get("equity")) for row in daily_rows]
    if any(value is None or value <= 0 for value in equities):
        raise Phase5Error("INVALID_CONFIG", "invalid_equity_curve")
    eq = [value for value in equities if value is not None]
    returns = [eq[index] / eq[index - 1] - 1 for index in range(1, len(eq))]
    total = None if not eq else eq[-1] / eq[0] - 1
    annualized = None if len(eq) < 2 else (eq[-1] / eq[0]) ** (Decimal(252) / Decimal(len(eq) - 1)) - 1
    vol = None
    sharpe = None
    if len(returns) >= 2:
        std = Decimal(str(stdev(float(item) for item in returns)))
        vol = std * Decimal(str(sqrt(252)))
        if std != 0:
            sharpe = Decimal(str(mean(float(item) for item in returns) / float(std) * sqrt(252)))
    peak = eq[0] if eq else Decimal("0")
    drawdowns: list[Decimal] = []
    for value in eq:
        peak = max(peak, value)
        drawdowns.append(value / peak - 1)
    exposures = [_decimal(row.get("exposure")) for row in daily_rows]
    exposure_values = [value for value in exposures if value is not None]
    gross = sum((_decimal(row.get("gross_amount")) or Decimal("0") for row in fills), Decimal("0"))
    average_equity = sum(eq, Decimal("0")) / Decimal(len(eq)) if eq else Decimal("0")
    realized = [_decimal(row.get("realized_pnl_delta")) for row in fills if str(row.get("side","")).upper() == "SELL"]
    nonzero_realized = [item for item in realized if item is not None and item != 0]
    positives = [item for item in nonzero_realized if item > 0]
    negatives = [item for item in nonzero_realized if item < 0]
    fees = sum((_decimal(row.get("total_fees")) or Decimal("0") for row in fills), Decimal("0"))
    return PerformanceMetrics(
        total_return=total, annualized_return=annualized, annualized_volatility=vol,
        sharpe=sharpe, max_drawdown=min(drawdowns) if drawdowns else None,
        average_exposure=(sum(exposure_values,Decimal("0")) / Decimal(len(exposure_values)) if exposure_values else None),
        max_exposure=max(exposure_values) if exposure_values else None,
        turnover=(gross / average_equity if average_equity > 0 else None),
        realized_win_rate=(Decimal(len(positives)) / Decimal(len(nonzero_realized)) if nonzero_realized else None),
        profit_factor=(sum(positives,Decimal("0")) / abs(sum(negatives,Decimal("0"))) if negatives else None),
        buy_count=sum(1 for row in fills if str(row.get("side","")).upper() == "BUY"),
        sell_count=sum(1 for row in fills if str(row.get("side","")).upper() == "SELL"),
        total_fees=fees,
    )


def build_benchmark_curve(
    dates: Sequence[str], index_closes: Mapping[str, Mapping[str, object]]
) -> tuple[tuple[dict[str, object], ...], tuple[str, ...]]:
    normalized: dict[str, dict[str, Decimal]] = {}
    stale: list[str] = []
    for symbol, weight in BENCHMARK_WEIGHTS.items():
        source = index_closes.get(symbol, {})
        last: Decimal | None = None
        series: dict[str, Decimal] = {}
        for day in dates:
            current = _decimal(source.get(day))
            if current is None:
                if last is None:
                    raise Phase5Error("DATA_NOT_READY", f"benchmark_start_missing:{symbol}")
                current = last
                stale.append(f"{symbol}:{day}")
            series[day] = current
            last = current
        normalized[symbol] = series
    starts = {symbol: values[dates[0]] for symbol, values in normalized.items()}
    rows = []
    for day in dates:
        value = sum((BENCHMARK_WEIGHTS[symbol] * normalized[symbol][day] / starts[symbol] for symbol in BENCHMARK_WEIGHTS), Decimal("0"))
        rows.append({"date": day, "benchmark_value": value, "benchmark_return": value - 1})
    return tuple(rows), tuple(stale)


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None
