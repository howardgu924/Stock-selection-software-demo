"""Pure V1.3.3 market-regime calculations for the three fixed indexes."""

from __future__ import annotations

from collections.abc import Mapping
import math
from types import MappingProxyType

import pandas as pd

from stock_picker.strategies.adaptive_trend_v1_3.opportunity_score import clip01
from stock_picker.strategies.adaptive_trend_v1_3.phase2_models import Phase2Status


_INDEX_COMPONENTS = (
    ("000300.SH", 0.40, "hs300"),
    ("000852.SH", 0.40, "csi1000"),
    ("399006.SZ", 0.20, "chinext"),
)
MARKET_INDEX_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {symbol: weight for symbol, weight, _ in _INDEX_COMPONENTS}
)

MARKET_OVERLAY_COLUMNS = [
    "date",
    "status",
    "invalid_reasons",
    "hs300_score",
    "csi1000_score",
    "chinext_score",
    "raw_market_score",
    "smoothed_market_score",
    "entry_threshold",
    "raw_exposure_cap",
    "effective_exposure_cap",
    "pause_new_entries",
]


def wilder_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 20,
) -> pd.Series:
    """Return Wilder ATR with an arithmetic first seed and recursive updates."""

    if period <= 0:
        raise ValueError("period_must_be_positive")
    high_values = pd.to_numeric(high, errors="coerce").astype("float64")
    low_values = pd.to_numeric(low, errors="coerce").astype("float64")
    close_values = pd.to_numeric(close, errors="coerce").astype("float64")
    previous_close = close_values.shift(1)
    true_range = pd.concat(
        [
            high_values - low_values,
            (high_values - previous_close).abs(),
            (low_values - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1, skipna=True)
    result = pd.Series(math.nan, index=close_values.index, dtype="float64")
    if len(true_range) < period:
        return result
    seed = true_range.iloc[:period]
    if not seed.map(_finite).all():
        return result
    result.iloc[period - 1] = float(seed.mean())
    for position in range(period, len(true_range)):
        current_tr = true_range.iloc[position]
        previous_atr = result.iloc[position - 1]
        if not _finite(current_tr) or not _finite(previous_atr):
            continue
        result.iloc[position] = (
            float(previous_atr) * (period - 1) + float(current_tr)
        ) / period
    return result


def score_index_factors(x1: float, x2: float, x3: float) -> dict[str, float]:
    """Score one index using the frozen 30/40/30 factor mapping."""

    price = 100.0 * clip01((float(x1) + 1.0) / 2.0)
    structure = 100.0 * clip01((float(x2) + 1.0) / 2.5)
    slope = 100.0 * clip01((float(x3) + 0.5) / 1.5)
    return {
        "index_price_score": price,
        "index_structure_score": structure,
        "index_slope_score": slope,
        "single_index_score": 0.30 * price + 0.40 * structure + 0.30 * slope,
    }


def effective_exposure_cap(
    raw_exposure_cap: float,
    previous_effective_exposure_cap: float | None,
    *,
    remove_drop_limit: bool = False,
) -> float:
    """Apply the normal ten-percentage-point daily exposure-cap decline limit."""

    raw = float(raw_exposure_cap)
    if previous_effective_exposure_cap is None or remove_drop_limit:
        return raw
    return max(raw, float(previous_effective_exposure_cap) - 0.10)


def calculate_market_overlay(
    index_histories: Mapping[str, pd.DataFrame],
    *,
    as_of: str | pd.Timestamp | None = None,
    previous_smoothed_market_score: float | None = None,
    previous_effective_exposure_cap: float | None = None,
) -> pd.DataFrame:
    """Return the chronological MarketOverlay audit series through ``as_of``.

    ``as_of`` is inclusive. Inputs are never fetched, filled, or reweighted.
    INVALID dates remain in the output and do not update either EMA state or
    the previous valid EffectiveExposureCap state.
    """

    parsed_as_of, as_of_reason = _parse_as_of(as_of)
    prepared: dict[str, pd.DataFrame] = {}
    source_reasons: dict[str, tuple[str, ...]] = {}
    calendar: set[pd.Timestamp] = set()
    for symbol, _, _ in _INDEX_COMPONENTS:
        if symbol not in index_histories:
            prepared[symbol] = pd.DataFrame()
            source_reasons[symbol] = (f"missing_index:{symbol}",)
            continue
        frame, reasons = _prepare_index(index_histories[symbol], parsed_as_of)
        prepared[symbol] = frame
        source_reasons[symbol] = reasons
        if not frame.empty:
            calendar.update(frame["date"].tolist())

    state_reasons: list[str] = []
    if previous_smoothed_market_score is not None and not _finite(
        previous_smoothed_market_score
    ):
        state_reasons.append("invalid_previous_smoothed_market_score")
    if previous_effective_exposure_cap is not None and not _finite(
        previous_effective_exposure_cap
    ):
        state_reasons.append("invalid_previous_effective_exposure_cap")

    dates = sorted(calendar)
    if not dates:
        dates = [parsed_as_of] if parsed_as_of is not None else [pd.NaT]

    ema_state = (
        float(previous_smoothed_market_score)
        if previous_smoothed_market_score is not None
        and _finite(previous_smoothed_market_score)
        else None
    )
    exposure_state = (
        float(previous_effective_exposure_cap)
        if previous_effective_exposure_cap is not None
        and _finite(previous_effective_exposure_cap)
        else None
    )
    rows: list[dict[str, object]] = []
    for date in dates:
        reasons: list[str] = []
        if as_of_reason:
            reasons.append(as_of_reason)
        reasons.extend(state_reasons)
        index_scores: dict[str, float] = {}
        for symbol, _, prefix in _INDEX_COMPONENTS:
            reasons.extend(source_reasons[symbol])
            frame = prepared[symbol]
            match = frame[frame["date"].eq(date)] if not frame.empty else frame
            if match.empty:
                reasons.append(f"missing_index_date:{symbol}:{_date_text(date)}")
                continue
            record = match.iloc[0]
            if record["status"] != Phase2Status.VALID.value:
                reasons.extend(record["invalid_reasons"])
                continue
            index_scores[prefix] = float(record["single_index_score"])

        row: dict[str, object] = {
            column: None for column in MARKET_OVERLAY_COLUMNS
        }
        row.update(
            {
                "date": _date_text(date),
                "status": Phase2Status.INVALID.value,
                "invalid_reasons": tuple(_unique(reasons)),
                "pause_new_entries": True,
            }
        )
        for prefix in ("hs300", "csi1000", "chinext"):
            row[f"{prefix}_score"] = index_scores.get(prefix)

        if not reasons and len(index_scores) == 3:
            raw = sum(
                weight * index_scores[prefix]
                for _, weight, prefix in _INDEX_COMPONENTS
            )
            smoothed = raw if ema_state is None else raw / 3.0 + ema_state * 2.0 / 3.0
            entry_threshold = 70.0 + 8.0 * (1.0 - smoothed / 100.0)
            raw_cap = 0.90 * smoothed / 100.0
            effective_cap = effective_exposure_cap(raw_cap, exposure_state)
            row.update(
                {
                    "status": Phase2Status.VALID.value,
                    "invalid_reasons": (),
                    "raw_market_score": raw,
                    "smoothed_market_score": smoothed,
                    "entry_threshold": entry_threshold,
                    "raw_exposure_cap": raw_cap,
                    "effective_exposure_cap": effective_cap,
                    "pause_new_entries": smoothed < 20.0,
                }
            )
            ema_state = smoothed
            exposure_state = effective_cap
        rows.append(row)
    return pd.DataFrame(rows, columns=MARKET_OVERLAY_COLUMNS)


def _prepare_index(
    history: pd.DataFrame,
    as_of: pd.Timestamp | None,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    if not isinstance(history, pd.DataFrame):
        return pd.DataFrame(), ("invalid_history_type",)
    missing = [name for name in ("date", "high", "low", "close") if name not in history]
    if missing:
        return pd.DataFrame(), tuple(f"missing_required_field:{name}" for name in missing)
    frame = history[["date", "high", "low", "close"]].copy()
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().any():
        return pd.DataFrame(), ("invalid_date_value",)
    frame["date"] = dates.dt.normalize()
    if as_of is not None:
        frame = frame[frame["date"].le(as_of)]
    for column in ("high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")
    frame = frame.sort_values("date", kind="mergesort")
    for duplicate_date in (
        frame.loc[frame.duplicated("date", keep=False), "date"]
        .drop_duplicates()
        .sort_values()
    ):
        group = frame[frame["date"].eq(duplicate_date)]
        if any(group[name].nunique(dropna=False) > 1 for name in ("high", "low", "close")):
            return pd.DataFrame(), (
                f"conflicting_duplicate_date:{duplicate_date:%Y-%m-%d}",
            )
    frame = frame.drop_duplicates("date", keep="first").reset_index(drop=True)
    valid_mask = pd.Series(True, index=frame.index)
    for column in ("high", "low", "close"):
        valid_mask &= frame[column].map(_finite) & frame[column].gt(0)
    valid_mask &= frame["high"].ge(frame["low"])
    valid = frame.loc[valid_mask].copy().reset_index(drop=True)
    if not valid.empty:
        valid["ma20"] = valid["close"].rolling(20, min_periods=20).mean()
        valid["ma60"] = valid["close"].rolling(60, min_periods=60).mean()
        valid["ma60_t_minus_10"] = valid["ma60"].shift(10)
        valid["atr20"] = wilder_atr(valid["high"], valid["low"], valid["close"], 20)
        valid["x1"] = (valid["close"] - valid["ma20"]) / valid["atr20"]
        valid["x2"] = (valid["ma20"] - valid["ma60"]) / valid["atr20"]
        valid["x3"] = (valid["ma60"] - valid["ma60_t_minus_10"]) / valid["atr20"]
        scored = valid.apply(_score_index_row, axis=1, result_type="expand")
        valid = pd.concat([valid, scored], axis=1)
    output = frame[["date"]].copy()
    output = output.merge(
        (
            valid[["date", "single_index_score", "factor_invalid_reason"]]
            if not valid.empty
            else valid
        ),
        on="date",
        how="left",
    )
    statuses: list[str] = []
    invalid_reasons: list[tuple[str, ...]] = []
    for _, row in output.iterrows():
        source = frame[frame["date"].eq(row["date"])].iloc[0]
        reasons: list[str] = []
        if not all(_finite(source[name]) and float(source[name]) > 0 for name in ("high", "low", "close")):
            reasons.append(f"invalid_price:{_date_text(row['date'])}")
        elif float(source["high"]) < float(source["low"]):
            reasons.append(f"high_below_low:{_date_text(row['date'])}")
        elif not _finite(row.get("single_index_score")):
            factor_reason = row.get("factor_invalid_reason")
            reasons.append(
                str(factor_reason)
                if isinstance(factor_reason, str) and factor_reason
                else f"insufficient_index_factors:{_date_text(row['date'])}"
            )
        statuses.append(Phase2Status.INVALID.value if reasons else Phase2Status.VALID.value)
        invalid_reasons.append(tuple(reasons))
    output["status"] = statuses
    output["invalid_reasons"] = invalid_reasons
    return output, ()


def _score_index_row(row: pd.Series) -> pd.Series:
    values = (row.get("x1"), row.get("x2"), row.get("x3"), row.get("atr20"))
    if _finite(row.get("atr20")) and float(row["atr20"]) <= 0:
        return pd.Series(
            {
                "single_index_score": math.nan,
                "factor_invalid_reason": f"invalid_atr20:{_date_text(row['date'])}",
            }
        )
    if not all(_finite(value) for value in values):
        return pd.Series(
            {"single_index_score": math.nan, "factor_invalid_reason": ""}
        )
    result = score_index_factors(row["x1"], row["x2"], row["x3"])
    result["factor_invalid_reason"] = ""
    return pd.Series(result)


def _parse_as_of(value: object) -> tuple[pd.Timestamp | None, str]:
    if value is None:
        return None, ""
    try:
        parsed = pd.to_datetime(value, errors="raise")
        if not isinstance(parsed, pd.Timestamp) or pd.isna(parsed):
            raise ValueError
        if parsed.tzinfo is not None:
            parsed = parsed.tz_localize(None)
        return parsed.normalize(), ""
    except (TypeError, ValueError, OverflowError):
        return None, "invalid_as_of"


def _finite(value: object) -> bool:
    try:
        return value is not None and not pd.isna(value) and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _date_text(value: object) -> str:
    return "" if pd.isna(value) else pd.Timestamp(value).strftime("%Y-%m-%d")


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
