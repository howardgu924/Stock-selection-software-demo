from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
import math
from types import MappingProxyType

import pandas as pd

from stock_picker.data.models import normalize_symbol


MIN_HISTORY_DAYS = 200
_BENCHMARK_COMPONENTS = (
    ("000300.SH", 0.40, "hs300"),
    ("000852.SH", 0.40, "csi1000"),
    ("399006.SZ", 0.20, "chinext"),
)
BENCHMARK_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {symbol: weight for symbol, weight, _ in _BENCHMARK_COMPONENTS}
)


class OpportunityInputError(ValueError):
    """Stable domain error for invalid Phase-1 call-level inputs."""


class OpportunityStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"


OPPORTUNITY_OUTPUT_COLUMNS = [
    "symbol",
    "as_of",
    "status",
    "invalid_reason",
    "valid_history_days",
    "close",
    "ma20",
    "ma60",
    "ma60_t_minus_10",
    "atr20",
    "stock_return20",
    "stock_return60",
    "hs300_return20",
    "csi1000_return20",
    "chinext_return20",
    "benchmark_return20",
    "hs300_return60",
    "csi1000_return60",
    "chinext_return60",
    "benchmark_return60",
    "rs20",
    "rs60",
    "abs_rs20",
    "abs_rs60",
    "rs20_average_rank",
    "rs60_average_rank",
    "rs20_percentile",
    "rs60_percentile",
    "rs_pool_size",
    "rs_pool_ranking_applied",
    "rs20_score",
    "rs60_score",
    "x1_close_ma20",
    "close_ma20_score",
    "x2_ma20_ma60",
    "ma20_ma60_score",
    "x3_ma60_slope10",
    "ma60_slope_score",
    "signed_er20",
    "signed_er20_t_minus_5",
    "signed_er_change5",
    "signed_er_score",
    "signed_er_change_score",
    "relative_strength_score",
    "trend_structure_score",
    "directional_efficiency_score",
    "opportunity_score",
    "opportunity_rank",
]

_NUMERIC_COLUMNS = [
    column
    for column in OPPORTUNITY_OUTPUT_COLUMNS
    if column
    not in {
        "symbol",
        "as_of",
        "status",
        "invalid_reason",
        "rs_pool_ranking_applied",
    }
]


def clip01(value: float) -> float:
    """Return min(1, max(0, value)) using double-precision arithmetic."""

    return min(1.0, max(0.0, float(value)))


def abs_rs(value: float, lower: float, upper: float) -> float:
    """Map absolute relative strength to [0, 1] per the V1.3.1 contract."""

    value = float(value)
    lower = float(lower)
    upper = float(upper)
    if not lower < 0 < upper:
        raise ValueError("AbsRS requires lower < 0 < upper")
    if value <= lower:
        return 0.0
    if value < 0:
        return 0.30 * (value - lower) / (0.0 - lower)
    if value < upper:
        return 0.30 + 0.70 * value / upper
    return 1.0


def signed_er_series(closes: pd.Series, period: int = 20) -> pd.Series:
    """Calculate SignedER; a zero path-length denominator maps to zero."""

    numeric = pd.to_numeric(closes, errors="coerce").astype("float64")
    numerator = numeric - numeric.shift(period)
    denominator = numeric.diff().abs().rolling(period, min_periods=period).sum()
    result = numerator / denominator
    zero_denominator = denominator.eq(0) & numerator.notna()
    return result.mask(zero_denominator, 0.0)


def score_close_ma20(x1: float) -> float:
    return 10.0 * clip01((float(x1) + 1.0) / 2.0)


def score_ma20_ma60(x2: float) -> float:
    return 15.0 * clip01((float(x2) + 1.0) / 2.5)


def score_ma60_slope(x3: float) -> float:
    return 10.0 * clip01((float(x3) + 0.5) / 1.5)


def score_signed_er(value: float) -> float:
    return 15.0 * clip01((float(value) + 0.20) / 0.80)


def score_signed_er_change(value: float) -> float:
    return 10.0 * clip01((float(value) + 0.15) / 0.30)


def calculate_weighted_benchmark_returns(
    benchmark_histories: Mapping[str, pd.DataFrame],
    *,
    as_of: str | pd.Timestamp | None = None,
) -> dict[str, object]:
    """Calculate 40/40/20 weighted 20- and 60-day benchmark returns.

    All three benchmark histories are mandatory.  Missing or invalid data does
    not cause weight normalization; the returned status is INVALID instead.
    """

    parsed_as_of = _parse_as_of(as_of)
    result = _calculate_weighted_benchmark_returns(
        benchmark_histories,
        as_of=parsed_as_of,
    )
    return {key: value for key, value in result.items() if not key.startswith("_anchor_")}


def _calculate_weighted_benchmark_returns(
    benchmark_histories: Mapping[str, pd.DataFrame],
    *,
    as_of: pd.Timestamp | None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": OpportunityStatus.VALID.value,
        "invalid_reason": "",
    }
    missing = [
        symbol
        for symbol, _, _ in _BENCHMARK_COMPONENTS
        if symbol not in benchmark_histories
    ]
    if missing:
        return {
            **result,
            "status": OpportunityStatus.INVALID.value,
            "invalid_reason": f"missing_benchmark:{','.join(missing)}",
        }

    prepared_indexes: dict[str, pd.DataFrame] = {}
    calendars: list[set[pd.Timestamp]] = []
    for symbol, _, _ in _BENCHMARK_COMPONENTS:
        prepared, reason = _prepare_history(benchmark_histories[symbol], as_of=as_of)
        if reason:
            return {
                **result,
                "status": OpportunityStatus.INVALID.value,
                "invalid_reason": reason,
            }
        prepared_indexes[symbol] = prepared
        calendars.append(set(prepared["date"].tolist()))

    benchmark_calendar = sorted(set.intersection(*calendars))
    if len(benchmark_calendar) < 61:
        return {
            **result,
            "status": OpportunityStatus.INVALID.value,
            "invalid_reason": f"insufficient_common_benchmark_history:{len(benchmark_calendar)}<61",
        }

    anchor_t = benchmark_calendar[-1]
    anchor_t20 = benchmark_calendar[-21]
    anchor_t60 = benchmark_calendar[-61]
    anchors = (("20", anchor_t20), ("60", anchor_t60))
    for symbol, _, prefix in _BENCHMARK_COMPONENTS:
        prepared = prepared_indexes[symbol]
        current, reason = _anchor_close(prepared, anchor_t)
        if reason:
            return _invalid_benchmark_anchor(result, symbol, anchor_t, reason)
        for horizon, anchor_date in anchors:
            previous, reason = _anchor_close(prepared, anchor_date)
            if reason:
                return _invalid_benchmark_anchor(result, symbol, anchor_date, reason)
            result[f"{prefix}_return{horizon}"] = float(current / previous - 1.0)

    result["benchmark_return20"] = sum(
        weight * float(result[f"{prefix}_return20"])
        for _, weight, prefix in _BENCHMARK_COMPONENTS
    )
    result["benchmark_return60"] = sum(
        weight * float(result[f"{prefix}_return60"])
        for _, weight, prefix in _BENCHMARK_COMPONENTS
    )
    result["_anchor_t"] = anchor_t
    result["_anchor_t20"] = anchor_t20
    result["_anchor_t60"] = anchor_t60
    return result


def calculate_opportunity_scores(
    histories: Mapping[str, pd.DataFrame],
    benchmark_histories: Mapping[str, pd.DataFrame],
    *,
    as_of: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Return the fixed 48-column Phase-1 OpportunityScore audit table.

    ``histories`` maps stock symbols to daily frames containing ``date``,
    ``high``, ``low``, and ``close``. ``benchmark_histories`` must contain the
    same fields for 000300.SH, 000852.SH, and 399006.SZ. ``as_of`` is an
    inclusive information cutoff (``date <= as_of``); this pure calculation
    does not guess the trading decision date. A 10:00 backtest scheduler must
    therefore pass the previous trading day as ``as_of``.

    The caller must provide every stock under one consistent, point-in-time
    adjustment convention and all indexes under a mutually comparable index
    price convention. This Phase-1 layer does not transform prices or validate
    ``adjust_type``, ``source``, or ``known_at`` metadata; the later data
    adapter owns those checks and must not mix incompatible raw and adjusted
    series.

    INVALID candidates remain in the result for diagnostics but are excluded
    from both the RS pool and OpportunityScore ranking.
    """

    parsed_as_of = _parse_as_of(as_of)
    normalized_histories: list[tuple[str, pd.DataFrame]] = []
    seen_symbols: set[str] = set()
    for raw_symbol, history in histories.items():
        try:
            symbol = normalize_symbol(str(raw_symbol))
        except (TypeError, ValueError) as exc:
            raise OpportunityInputError(f"invalid_symbol:{raw_symbol}") from exc
        if symbol in seen_symbols:
            raise OpportunityInputError(f"duplicate_normalized_symbol:{symbol}")
        seen_symbols.add(symbol)
        normalized_histories.append((symbol, history))

    benchmark = _calculate_weighted_benchmark_returns(
        benchmark_histories,
        as_of=parsed_as_of,
    )
    rows: list[dict[str, object]] = []
    for symbol, history in normalized_histories:
        rows.append(_candidate_factors(symbol, history, benchmark, as_of=parsed_as_of))

    if not rows:
        return _empty_output()

    frame = pd.DataFrame(rows)
    valid_mask = frame["status"].eq(OpportunityStatus.VALID.value)
    valid_count = int(valid_mask.sum())
    frame["rs_pool_size"] = valid_count
    frame["rs_pool_ranking_applied"] = valid_mask & (valid_count >= 10)

    if valid_count:
        valid_index = frame.index[valid_mask]
        for horizon in (20, 60):
            ranks = frame.loc[valid_index, f"rs{horizon}"].rank(
                method="average", ascending=False
            )
            frame.loc[valid_index, f"rs{horizon}_average_rank"] = ranks
            if valid_count >= 10:
                percentiles = (valid_count - ranks) / (valid_count - 1)
                frame.loc[valid_index, f"rs{horizon}_percentile"] = percentiles
            else:
                frame.loc[valid_index, f"rs{horizon}_percentile"] = pd.NA

        if valid_count >= 10:
            frame.loc[valid_index, "rs20_score"] = 15.0 * (
                0.70 * frame.loc[valid_index, "abs_rs20"]
                + 0.30 * frame.loc[valid_index, "rs20_percentile"]
            )
            frame.loc[valid_index, "rs60_score"] = 25.0 * (
                0.70 * frame.loc[valid_index, "abs_rs60"]
                + 0.30 * frame.loc[valid_index, "rs60_percentile"]
            )
        else:
            frame.loc[valid_index, "rs20_score"] = 15.0 * frame.loc[valid_index, "abs_rs20"]
            frame.loc[valid_index, "rs60_score"] = 25.0 * frame.loc[valid_index, "abs_rs60"]

        frame.loc[valid_index, "relative_strength_score"] = (
            frame.loc[valid_index, "rs20_score"] + frame.loc[valid_index, "rs60_score"]
        )
        frame.loc[valid_index, "trend_structure_score"] = (
            frame.loc[valid_index, "close_ma20_score"]
            + frame.loc[valid_index, "ma20_ma60_score"]
            + frame.loc[valid_index, "ma60_slope_score"]
        )
        frame.loc[valid_index, "directional_efficiency_score"] = (
            frame.loc[valid_index, "signed_er_score"]
            + frame.loc[valid_index, "signed_er_change_score"]
        )
        frame.loc[valid_index, "opportunity_score"] = (
            frame.loc[valid_index, "relative_strength_score"]
            + frame.loc[valid_index, "trend_structure_score"]
            + frame.loc[valid_index, "directional_efficiency_score"]
        ).clip(0.0, 100.0)

        ordered = frame.loc[valid_index].sort_values(
            ["opportunity_score", "rs60", "rs20", "signed_er20", "symbol"],
            ascending=[False, False, False, False, True],
            kind="mergesort",
        )
        frame.loc[ordered.index, "opportunity_rank"] = range(1, valid_count + 1)

    frame = frame.sort_values(
        ["status", "opportunity_rank", "symbol"],
        ascending=[False, True, True],
        na_position="last",
        kind="mergesort",
    ).reset_index(drop=True)
    return _coerce_output(frame)


def _candidate_factors(
    symbol: str,
    history: pd.DataFrame,
    benchmark: dict[str, object],
    *,
    as_of: str | pd.Timestamp | None,
) -> dict[str, object]:
    row = _blank_row(symbol, as_of)
    prepared, reason = _prepare_history(history, as_of=as_of)
    if reason:
        return _invalidate(row, reason)
    if benchmark.get("status") != OpportunityStatus.VALID.value:
        return _invalidate(row, str(benchmark.get("invalid_reason") or "benchmark_invalid"))

    anchor_t = pd.Timestamp(benchmark["_anchor_t"])
    anchor_t20 = pd.Timestamp(benchmark["_anchor_t20"])
    anchor_t60 = pd.Timestamp(benchmark["_anchor_t60"])
    if as_of is None:
        row["as_of"] = anchor_t.strftime("%Y-%m-%d")

    indicator_history = prepared[
        prepared["_valid_row"] & prepared["date"].le(anchor_t)
    ].reset_index(drop=True)
    valid_days = len(indicator_history)
    row["valid_history_days"] = valid_days
    if valid_days < MIN_HISTORY_DAYS:
        return _invalidate(row, "insufficient_valid_history")

    current_rows = prepared[prepared["date"].eq(anchor_t)]
    if not current_rows.empty:
        current_close = current_rows.iloc[0]["close"]
        if _finite(current_close) and float(current_close) <= 0:
            return _invalidate(row, "invalid_price:<=0")

    anchor_closes: dict[str, float] = {}
    for name, anchor_date in (
        ("t", anchor_t),
        ("t20", anchor_t20),
        ("t60", anchor_t60),
    ):
        value, anchor_reason = _anchor_close(prepared, anchor_date)
        if anchor_reason:
            return _invalidate(row, f"{anchor_reason}:{name}:{anchor_date:%Y-%m-%d}")
        anchor_closes[name] = value

    close = indicator_history["close"].astype("float64")
    high = indicator_history["high"].astype("float64")
    low = indicator_history["low"].astype("float64")

    ma20 = close.rolling(20, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=60).mean()
    atr20 = _atr20(high, low, close)
    signed_er = signed_er_series(close, period=20)

    factor_values = {
        "close": close.iloc[-1],
        "ma20": ma20.iloc[-1],
        "ma60": ma60.iloc[-1],
        "ma60_t_minus_10": ma60.iloc[-11],
        "atr20": atr20.iloc[-1],
        "stock_return20": anchor_closes["t"] / anchor_closes["t20"] - 1.0,
        "stock_return60": anchor_closes["t"] / anchor_closes["t60"] - 1.0,
        "signed_er20": signed_er.iloc[-1],
        "signed_er20_t_minus_5": signed_er.iloc[-6],
    }
    invalid_factors = [name for name, value in factor_values.items() if not _finite(value)]
    if invalid_factors:
        return _invalidate(row, f"missing_required_factor:{','.join(invalid_factors)}")
    if float(factor_values["atr20"]) <= 0:
        return _invalidate(row, "invalid_atr20:<=0")
    if any(float(factor_values[name]) <= 0 for name in ("close", "ma20", "ma60")):
        return _invalidate(row, "invalid_price:<=0")

    row.update({name: float(value) for name, value in factor_values.items()})
    for name in (
        "hs300_return20",
        "csi1000_return20",
        "chinext_return20",
        "benchmark_return20",
        "hs300_return60",
        "csi1000_return60",
        "chinext_return60",
        "benchmark_return60",
    ):
        row[name] = float(benchmark[name])

    row["rs20"] = row["stock_return20"] - row["benchmark_return20"]
    row["rs60"] = row["stock_return60"] - row["benchmark_return60"]
    row["abs_rs20"] = abs_rs(row["rs20"], -0.10, 0.10)
    row["abs_rs60"] = abs_rs(row["rs60"], -0.15, 0.20)

    atr = row["atr20"]
    row["x1_close_ma20"] = (row["close"] - row["ma20"]) / atr
    row["close_ma20_score"] = score_close_ma20(row["x1_close_ma20"])
    row["x2_ma20_ma60"] = (row["ma20"] - row["ma60"]) / atr
    row["ma20_ma60_score"] = score_ma20_ma60(row["x2_ma20_ma60"])
    row["x3_ma60_slope10"] = (row["ma60"] - row["ma60_t_minus_10"]) / atr
    row["ma60_slope_score"] = score_ma60_slope(row["x3_ma60_slope10"])

    row["signed_er_change5"] = row["signed_er20"] - row["signed_er20_t_minus_5"]
    row["signed_er_score"] = score_signed_er(row["signed_er20"])
    row["signed_er_change_score"] = score_signed_er_change(row["signed_er_change5"])
    row["status"] = OpportunityStatus.VALID.value
    return row


def _prepare_history(
    history: pd.DataFrame | None,
    *,
    as_of: pd.Timestamp | None,
) -> tuple[pd.DataFrame, str]:
    if history is None or not isinstance(history, pd.DataFrame):
        return pd.DataFrame(), "missing_required_field:date"
    frame = history.copy()
    if "date" not in frame:
        return pd.DataFrame(), "missing_required_field:date"
    missing_columns = [column for column in ("high", "low", "close") if column not in frame]
    if missing_columns:
        return pd.DataFrame(), f"missing_required_field:{','.join(missing_columns)}"

    parsed_dates = pd.to_datetime(frame["date"], errors="coerce")
    if parsed_dates.isna().any():
        return pd.DataFrame(), "invalid_date_value"
    frame["date"] = parsed_dates.dt.normalize()
    if as_of is not None:
        frame = frame[frame["date"] <= as_of]
    for column in ("high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")

    frame = frame.sort_values("date", kind="mergesort")
    duplicate_dates = (
        frame.loc[frame.duplicated("date", keep=False), "date"]
        .drop_duplicates()
        .sort_values()
    )
    for duplicate_date in duplicate_dates:
        group = frame[frame["date"].eq(duplicate_date)]
        if any(group[column].nunique(dropna=False) > 1 for column in ("high", "low", "close")):
            return (
                pd.DataFrame(),
                f"conflicting_duplicate_date:{duplicate_date:%Y-%m-%d}",
            )
    frame = frame.drop_duplicates("date", keep="first").reset_index(drop=True)

    finite = pd.Series(True, index=frame.index)
    for column in ("high", "low", "close"):
        finite &= frame[column].map(_finite)
    frame["_valid_row"] = (
        finite
        & frame["high"].gt(0)
        & frame["low"].gt(0)
        & frame["close"].gt(0)
        & frame["high"].ge(frame["low"])
    )
    return frame, ""


def _atr20(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1, skipna=False)
    return true_range.rolling(20, min_periods=20).mean()


def _anchor_close(prepared: pd.DataFrame, date: pd.Timestamp) -> tuple[float, str]:
    rows = prepared[prepared["date"].eq(date)]
    if rows.empty:
        return math.nan, "missing_return_anchor"
    row = rows.iloc[0]
    if not bool(row["_valid_row"]):
        return math.nan, "invalid_return_anchor"
    return float(row["close"]), ""


def _invalid_benchmark_anchor(
    result: dict[str, object],
    symbol: str,
    date: pd.Timestamp,
    reason: str,
) -> dict[str, object]:
    return {
        **result,
        "status": OpportunityStatus.INVALID.value,
        "invalid_reason": f"{reason}:{symbol}:{date:%Y-%m-%d}",
    }


def _parse_as_of(value: str | pd.Timestamp | None) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        parsed = pd.to_datetime(value, errors="raise")
        if not isinstance(parsed, pd.Timestamp) or pd.isna(parsed):
            raise ValueError("not a scalar timestamp")
        if parsed.tzinfo is not None:
            parsed = parsed.tz_localize(None)
        return parsed.normalize()
    except (TypeError, ValueError, OverflowError) as exc:
        raise OpportunityInputError("invalid_as_of") from exc


def _blank_row(symbol: str, as_of: str | pd.Timestamp | None) -> dict[str, object]:
    row: dict[str, object] = {column: pd.NA for column in OPPORTUNITY_OUTPUT_COLUMNS}
    row.update(
        {
            "symbol": symbol,
            "as_of": "" if as_of is None else pd.to_datetime(as_of).strftime("%Y-%m-%d"),
            "status": OpportunityStatus.INVALID.value,
            "invalid_reason": "",
            "valid_history_days": 0,
            "rs_pool_ranking_applied": False,
        }
    )
    return row


def _invalidate(row: dict[str, object], reason: str) -> dict[str, object]:
    row["status"] = OpportunityStatus.INVALID.value
    row["invalid_reason"] = reason
    return row


def _finite(value: object) -> bool:
    try:
        return value is not None and not pd.isna(value) and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _empty_output() -> pd.DataFrame:
    return _coerce_output(pd.DataFrame(columns=OPPORTUNITY_OUTPUT_COLUMNS))


def _coerce_output(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in OPPORTUNITY_OUTPUT_COLUMNS:
        if column not in result:
            result[column] = pd.NA
    for column in _NUMERIC_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["rs_pool_ranking_applied"] = result["rs_pool_ranking_applied"].fillna(False).astype(bool)
    return result[OPPORTUNITY_OUTPUT_COLUMNS]
