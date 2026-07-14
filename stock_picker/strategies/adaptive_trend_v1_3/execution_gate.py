"""Pure 10:00 ExecutionGate and EmergencyMarketGate calculations."""

from __future__ import annotations

from collections.abc import Mapping
import math

import pandas as pd

from stock_picker.strategies.adaptive_trend_v1_3.phase2_models import (
    EmergencyIndexInput,
    EmergencyMarketResult,
    EmergencyStatus,
    ExecutionGateResult,
    ExecutionGateStatus,
    ExecutionStatus,
)


REQUIRED_BAR_STARTS = ("09:30", "09:35", "09:40", "09:45", "09:50", "09:55")
_EMERGENCY_INDEXES = ("000300.SH", "000852.SH", "399006.SZ")


def calculate_execution_gate(
    bars: pd.DataFrame,
    *,
    ma20: float,
    atr20: float,
    execution_status: ExecutionStatus = ExecutionStatus(),
    emergency_status: EmergencyStatus = EmergencyStatus.NORMAL,
) -> ExecutionGateResult:
    """Evaluate only the six completed 09:30--09:55 bars available at 10:00."""

    reasons: list[str] = []
    prepared = _prepare_bars(bars, reasons)
    if not _finite(ma20) or float(ma20) <= 0:
        reasons.append("invalid_ma20")
    if not _finite(atr20) or float(atr20) <= 0:
        reasons.append("invalid_atr20")
    if execution_status.suspended:
        reasons.append("security_suspended")
    if execution_status.limit_status == "limit_up":
        reasons.append("limit_up")
    elif execution_status.limit_status == "unknown":
        reasons.append("limit_status_unknown")
    if execution_status.trade_status == "unknown":
        reasons.append("trade_status_unknown")
    if emergency_status in {EmergencyStatus.LEVEL_1, EmergencyStatus.LEVEL_2}:
        reasons.append(f"emergency_market:{emergency_status.value}")
    elif emergency_status == EmergencyStatus.INVALID:
        reasons.append("emergency_market:INVALID")

    p10 = morning_vwap = distance = high_to_10 = max_drawdown = below_vwap = None
    data_invalid = any(_is_data_reject(reason) for reason in reasons)
    if not data_invalid and prepared is not None:
        p10 = float(prepared.iloc[-1]["close"])
        total_volume = float(prepared["volume"].sum())
        if total_volume <= 0:
            reasons.append("nonpositive_total_volume")
        else:
            typical = (prepared["high"] + prepared["low"] + prepared["close"]) / 3.0
            morning_vwap = float((typical * prepared["volume"]).sum() / total_volume)
            distance = (p10 - float(ma20)) / float(atr20)
            high_to_10 = (float(prepared["high"].max()) - p10) / float(atr20)
            running_high = prepared["high"].cummax()
            max_drawdown = float(((running_high - prepared["low"]) / float(atr20)).max())
            below_vwap = max(0.0, morning_vwap - p10) / float(atr20)
            _append_threshold_reasons(
                reasons,
                distance=distance,
                high_to_10=high_to_10,
                max_drawdown=max_drawdown,
                below_vwap=below_vwap,
            )

    reasons = _unique(reasons)
    if any(_is_reject_reason(reason) for reason in reasons):
        gate = ExecutionGateStatus.REJECT
        multiplier = 0.0
    elif any(reason.endswith(":HALF") for reason in reasons):
        gate = ExecutionGateStatus.HALF
        multiplier = 0.5
    else:
        gate = ExecutionGateStatus.PASS
        multiplier = 1.0
    return ExecutionGateResult(
        execution_gate=gate,
        gate_multiplier=multiplier,
        p10=p10,
        morning_vwap=morning_vwap,
        distance_ma20=distance,
        high_to_10_drawdown=high_to_10,
        morning_max_drawdown=max_drawdown,
        below_vwap=below_vwap,
        reasons=tuple(reasons),
    )


def calculate_emergency_market_gate(
    index_inputs: Mapping[str, EmergencyIndexInput | Mapping[str, object]],
) -> EmergencyMarketResult:
    """Calculate three index shocks and the frozen NORMAL/LEVEL_1/LEVEL_2 state."""

    reasons: list[str] = []
    shocks: list[tuple[str, float | None]] = []
    for symbol in _EMERGENCY_INDEXES:
        value = index_inputs.get(symbol)
        if value is None:
            reasons.append(f"missing_index:{symbol}")
            shocks.append((symbol, None))
            continue
        if isinstance(value, EmergencyIndexInput):
            previous_close, atr20, p10 = value.previous_close, value.atr20, value.p10
        elif isinstance(value, Mapping):
            previous_close = value.get("previous_close")
            atr20 = value.get("atr20")
            p10 = value.get("p10")
        else:
            reasons.append(f"invalid_index_input:{symbol}")
            shocks.append((symbol, None))
            continue
        fields = {
            "previous_close": previous_close,
            "atr20": atr20,
            "p10": p10,
        }
        invalid = [
            name
            for name, field in fields.items()
            if not _finite(field) or float(field) <= 0
        ]
        if invalid:
            reasons.extend(f"invalid_{name}:{symbol}" for name in invalid)
            shocks.append((symbol, None))
            continue
        shocks.append((symbol, (float(p10) - float(previous_close)) / float(atr20)))

    reasons = _unique(reasons)
    if reasons:
        return EmergencyMarketResult(
            emergency_status=EmergencyStatus.INVALID,
            shocks=tuple(shocks),
            reject_new_entries=True,
            remove_exposure_drop_limit=False,
            max_reduction_pct=0.0,
            reasons=tuple(reasons),
        )
    level_2_symbols = [symbol for symbol, shock in shocks if float(shock) <= -1.20]
    level_1_symbols = [symbol for symbol, shock in shocks if float(shock) <= -0.80]
    if len(level_2_symbols) >= 2:
        status = EmergencyStatus.LEVEL_2
        reasons = [*(f"shock_level_2:{symbol}" for symbol in level_2_symbols), "emergency_level_2"]
        return EmergencyMarketResult(
            emergency_status=status,
            shocks=tuple(shocks),
            reject_new_entries=True,
            remove_exposure_drop_limit=True,
            max_reduction_pct=0.30,
            reasons=tuple(reasons),
        )
    if len(level_1_symbols) >= 2:
        status = EmergencyStatus.LEVEL_1
        reasons = [*(f"shock_level_1:{symbol}" for symbol in level_1_symbols), "emergency_level_1"]
        return EmergencyMarketResult(
            emergency_status=status,
            shocks=tuple(shocks),
            reject_new_entries=True,
            remove_exposure_drop_limit=False,
            max_reduction_pct=0.0,
            reasons=tuple(reasons),
        )
    return EmergencyMarketResult(
        emergency_status=EmergencyStatus.NORMAL,
        shocks=tuple(shocks),
        reject_new_entries=False,
        remove_exposure_drop_limit=False,
        max_reduction_pct=0.0,
        reasons=(),
    )


def _prepare_bars(bars: object, reasons: list[str]) -> pd.DataFrame | None:
    if not isinstance(bars, pd.DataFrame):
        reasons.append("invalid_bars_type")
        return None
    missing = [
        name for name in ("bar_start", "high", "low", "close", "volume") if name not in bars
    ]
    if missing:
        reasons.extend(f"missing_required_field:{name}" for name in missing)
        return None
    frame = bars[["bar_start", "high", "low", "close", "volume"]].copy()
    times = frame["bar_start"].map(_bar_time)
    if times.isna().any():
        reasons.append("invalid_bar_start")
        return None
    frame["bar_time"] = times
    duplicate_times = sorted(frame.loc[frame["bar_time"].duplicated(False), "bar_time"].unique())
    reasons.extend(f"duplicate_bar_start:{value}" for value in duplicate_times)
    present = set(frame["bar_time"])
    reasons.extend(
        f"missing_bar_start:{value}" for value in REQUIRED_BAR_STARTS if value not in present
    )
    reasons.extend(
        f"unexpected_bar_start:{value}" for value in sorted(present - set(REQUIRED_BAR_STARTS))
    )
    for name in ("high", "low", "close", "volume"):
        frame[name] = pd.to_numeric(frame[name], errors="coerce").astype("float64")
    for _, row in frame.iterrows():
        time = row["bar_time"]
        for name in ("high", "low", "close"):
            if not _finite(row[name]) or float(row[name]) <= 0:
                reasons.append(f"invalid_{name}:{time}")
        if _finite(row["high"]) and _finite(row["low"]) and float(row["high"]) < float(row["low"]):
            reasons.append(f"high_below_low:{time}")
        if not _finite(row["volume"]) or float(row["volume"]) < 0:
            reasons.append(f"invalid_volume:{time}")
    if reasons:
        return None
    return frame.sort_values("bar_time", kind="mergesort").reset_index(drop=True)


def _append_threshold_reasons(
    reasons: list[str],
    *,
    distance: float,
    high_to_10: float,
    max_drawdown: float,
    below_vwap: float,
) -> None:
    if distance > 2.0:
        reasons.append("distance_ma20_above:REJECT")
    elif distance < -1.0:
        reasons.append("distance_ma20_below:REJECT")
    elif 1.5 <= distance <= 2.0:
        reasons.append("distance_ma20:HALF")
    if high_to_10 > 0.75:
        reasons.append("high_to_10_drawdown:REJECT")
    elif 0.50 <= high_to_10 <= 0.75:
        reasons.append("high_to_10_drawdown:HALF")
    if max_drawdown > 0.75:
        reasons.append("morning_max_drawdown:REJECT")
    elif 0.50 <= max_drawdown <= 0.75:
        reasons.append("morning_max_drawdown:HALF")
    if below_vwap > 0.50:
        reasons.append("below_vwap:REJECT")
    elif 0.25 <= below_vwap <= 0.50:
        reasons.append("below_vwap:HALF")


def _bar_time(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        if len(text) == 5 and text[:2].isdigit() and text[3:].isdigit():
            return text
    try:
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            return None
        return parsed.strftime("%H:%M")
    except (TypeError, ValueError):
        return None


def _is_data_reject(reason: str) -> bool:
    return not reason.endswith(":HALF") and (
        reason.startswith(("invalid_", "missing_", "duplicate_", "unexpected_", "high_below"))
    )


def _is_reject_reason(reason: str) -> bool:
    return not reason.endswith(":HALF")


def _finite(value: object) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
