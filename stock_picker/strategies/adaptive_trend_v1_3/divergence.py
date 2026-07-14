"""MACD and causal 3/3 pivot-divergence calculations for V1.3.3."""

from __future__ import annotations

import math

import pandas as pd

from stock_picker.strategies.adaptive_trend_v1_3.market_overlay import wilder_atr
from stock_picker.strategies.adaptive_trend_v1_3.phase2_models import (
    DivergenceSignal,
    DivergenceSnapshot,
    DivergenceStrength,
    DivergenceType,
    Phase2Status,
)


def calculate_macd(close: pd.Series) -> pd.DataFrame:
    """Calculate the fixed EMA12/EMA26/DEA9 and doubled A-share histogram."""

    values = pd.to_numeric(close, errors="coerce").astype("float64")
    ema12 = values.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = values.ewm(span=26, adjust=False, min_periods=26).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False, min_periods=9).mean()
    histogram = 2.0 * (dif - dea)
    return pd.DataFrame(
        {"ema12": ema12, "ema26": ema26, "dif": dif, "dea": dea, "macd_hist": histogram}
    )


def find_swing_points(history: pd.DataFrame) -> pd.DataFrame:
    """Return strict 3-left/3-right pivots with causal confirmation positions."""

    columns = [
        "kind",
        "pivot_position",
        "pivot_date",
        "confirmed_position",
        "confirmed_date",
        "first_usable_position",
        "first_usable_date",
    ]
    if len(history) < 8:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    for position in range(3, len(history) - 3):
        high = float(history.iloc[position]["high"])
        surrounding_highs = pd.concat(
            [
                history.iloc[position - 3 : position]["high"],
                history.iloc[position + 1 : position + 4]["high"],
            ]
        )
        low = float(history.iloc[position]["low"])
        surrounding_lows = pd.concat(
            [
                history.iloc[position - 3 : position]["low"],
                history.iloc[position + 1 : position + 4]["low"],
            ]
        )
        confirmed_position = position + 3
        first_usable_position = position + 4
        kinds: list[str] = []
        if bool((high > surrounding_highs).all()):
            kinds.append("HIGH")
        if bool((low < surrounding_lows).all()):
            kinds.append("LOW")
        for kind in kinds:
            rows.append(
                {
                    "kind": kind,
                    "pivot_position": position,
                    "pivot_date": history.iloc[position]["date"],
                    "confirmed_position": confirmed_position,
                    "confirmed_date": history.iloc[confirmed_position]["date"],
                    "first_usable_position": first_usable_position,
                    "first_usable_date": (
                        history.iloc[first_usable_position]["date"]
                        if first_usable_position < len(history)
                        else pd.NaT
                    ),
                }
            )
    return pd.DataFrame(rows, columns=columns)


def calculate_divergence(
    history: pd.DataFrame,
    *,
    as_of: str | pd.Timestamp | None = None,
) -> DivergenceSnapshot:
    """Evaluate the latest causal top and bottom divergence states at ``as_of``."""

    frame, reasons, parsed_as_of = _prepare_history(history, as_of)
    as_of_text = _date_text(parsed_as_of)
    if reasons:
        return DivergenceSnapshot(
            status=Phase2Status.INVALID,
            as_of=as_of_text,
            invalid_reasons=reasons,
        )
    if frame.empty:
        return DivergenceSnapshot(
            status=Phase2Status.INVALID,
            as_of=as_of_text,
            invalid_reasons=("empty_history",),
        )
    as_of_text = _date_text(frame.iloc[-1]["date"])
    macd = calculate_macd(frame["close"])
    frame = pd.concat([frame.reset_index(drop=True), macd], axis=1)
    frame["atr20"] = wilder_atr(frame["high"], frame["low"], frame["close"], 20)
    current_atr = frame.iloc[-1]["atr20"]
    if _finite(current_atr) and float(current_atr) <= 0:
        return DivergenceSnapshot(
            status=Phase2Status.INVALID,
            as_of=as_of_text,
            invalid_reasons=("invalid_atr20:<=0",),
        )
    pivots = find_swing_points(frame)
    signals = _build_signals(frame, pivots)
    top = _latest_signal(signals, {DivergenceType.TOP, DivergenceType.STRONG_TOP})
    bottom = _latest_signal(signals, {DivergenceType.BOTTOM})
    return DivergenceSnapshot(
        status=Phase2Status.VALID,
        as_of=as_of_text,
        top_signal=top,
        bottom_signal=bottom,
    )


def _build_signals(
    history: pd.DataFrame,
    pivots: pd.DataFrame,
) -> list[DivergenceSignal]:
    signals: list[DivergenceSignal] = []
    for kind in ("HIGH", "LOW"):
        points = pivots[pivots["kind"].eq(kind)].sort_values("pivot_position")
        point_rows = list(points.to_dict("records"))
        for second_number in range(1, len(point_rows)):
            second = point_rows[second_number]
            if pd.isna(second["first_usable_date"]):
                continue
            candidates = [
                first
                for first in point_rows[:second_number]
                if 5
                <= int(second["pivot_position"]) - int(first["pivot_position"])
                <= 40
                and _pivot_factors_available(history, first, kind)
            ]
            if not candidates or not _pivot_factors_available(history, second, kind):
                continue
            first = max(candidates, key=lambda item: int(item["pivot_position"]))
            first_row = history.iloc[int(first["pivot_position"])]
            second_row = history.iloc[int(second["pivot_position"])]
            divergence_type = _classify_pair(kind, first_row, second_row)
            if divergence_type == DivergenceType.NONE:
                continue
            usable_position = int(second["first_usable_position"])
            active_until_position = usable_position + 19
            active_until = (
                _date_text(history.iloc[active_until_position]["date"])
                if active_until_position < len(history)
                else None
            )
            is_active = len(history) - 1 - usable_position <= 19
            strength = (
                DivergenceStrength.STRONG
                if divergence_type == DivergenceType.STRONG_TOP
                else DivergenceStrength.NORMAL
            )
            signals.append(
                DivergenceSignal(
                    divergence_type=divergence_type,
                    strength=strength,
                    pivot_1_date=_date_text(first["pivot_date"]),
                    pivot_2_date=_date_text(second["pivot_date"]),
                    confirmed_date=_date_text(second["confirmed_date"]),
                    first_usable_date=_date_text(second["first_usable_date"]),
                    active_until=active_until,
                    is_active=is_active,
                )
            )
    return signals


def _classify_pair(
    kind: str,
    first: pd.Series,
    second: pd.Series,
) -> DivergenceType:
    if kind == "HIGH":
        normal = float(second["high"]) > float(first["high"]) and float(
            second["dif"]
        ) < float(first["dif"])
        if not normal:
            return DivergenceType.NONE
        strong = (
            float(second["high"]) - float(first["high"])
            >= 0.5 * float(second["atr20"])
            and float(second["macd_hist"]) < float(first["macd_hist"])
        )
        return DivergenceType.STRONG_TOP if strong else DivergenceType.TOP
    bottom = float(second["low"]) < float(first["low"]) and float(
        second["dif"]
    ) > float(first["dif"])
    return DivergenceType.BOTTOM if bottom else DivergenceType.NONE


def _pivot_factors_available(
    history: pd.DataFrame,
    pivot: dict[str, object],
    kind: str,
) -> bool:
    row = history.iloc[int(pivot["pivot_position"])]
    required = [row["dif"], row["dea"], row["macd_hist"]]
    if kind == "HIGH":
        required.append(row["atr20"])
    return all(_finite(value) for value in required) and (
        kind != "HIGH" or float(row["atr20"]) > 0
    )


def _latest_signal(
    signals: list[DivergenceSignal],
    accepted: set[DivergenceType],
) -> DivergenceSignal | None:
    matches = [signal for signal in signals if signal.divergence_type in accepted]
    return max(matches, key=lambda signal: signal.first_usable_date) if matches else None


def _prepare_history(
    history: pd.DataFrame,
    as_of: object,
) -> tuple[pd.DataFrame, tuple[str, ...], pd.Timestamp | None]:
    parsed_as_of, as_of_reason = _parse_as_of(as_of)
    if as_of_reason:
        return pd.DataFrame(), (as_of_reason,), None
    if not isinstance(history, pd.DataFrame):
        return pd.DataFrame(), ("invalid_history_type",), parsed_as_of
    missing = [name for name in ("date", "high", "low", "close") if name not in history]
    if missing:
        return (
            pd.DataFrame(),
            tuple(f"missing_required_field:{name}" for name in missing),
            parsed_as_of,
        )
    frame = history[["date", "high", "low", "close"]].copy()
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().any():
        return pd.DataFrame(), ("invalid_date_value",), parsed_as_of
    frame["date"] = dates.dt.normalize()
    if parsed_as_of is not None:
        frame = frame[frame["date"].le(parsed_as_of)]
    for name in ("high", "low", "close"):
        frame[name] = pd.to_numeric(frame[name], errors="coerce").astype("float64")
    frame = frame.sort_values("date", kind="mergesort")
    for date in (
        frame.loc[frame.duplicated("date", keep=False), "date"]
        .drop_duplicates()
        .sort_values()
    ):
        group = frame[frame["date"].eq(date)]
        if any(group[name].nunique(dropna=False) > 1 for name in ("high", "low", "close")):
            return (
                pd.DataFrame(),
                (f"conflicting_duplicate_date:{date:%Y-%m-%d}",),
                parsed_as_of,
            )
    frame = frame.drop_duplicates("date", keep="first").reset_index(drop=True)
    for position, row in frame.iterrows():
        if not all(_finite(row[name]) and float(row[name]) > 0 for name in ("high", "low", "close")):
            return (
                pd.DataFrame(),
                (f"invalid_price:{_date_text(row['date'])}",),
                parsed_as_of,
            )
        if float(row["high"]) < float(row["low"]):
            return (
                pd.DataFrame(),
                (f"high_below_low:{_date_text(row['date'])}",),
                parsed_as_of,
            )
    return frame, (), parsed_as_of


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
    return "" if value is None or pd.isna(value) else pd.Timestamp(value).strftime("%Y-%m-%d")
