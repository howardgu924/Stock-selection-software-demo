"""Historical T+1 tail-risk observations and deterministic fallback selection."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping, Sequence

import pandas as pd

from stock_picker.strategies.adaptive_trend_v1_3.minute_contract import (
    LEGAL_BAR_START_TIMES,
    SHANGHAI_TIMEZONE,
    normalize_security_symbol,
    validate_target_minute_bars,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase4_models import (
    ClassificationMetadata,
    Phase4Status,
    T1RiskObservation,
    T1RiskResult,
)

ZERO = Decimal("0")
Q95 = Decimal("0.95")
THREE_INDEX_KEYS = ("CSI300", "CSI1000", "CHINEXT")


def build_t1_risk_observation(
    symbol: str,
    entry_date: date | str,
    minute_bars: pd.DataFrame,
    trading_calendar: Iterable[date | str],
    *,
    instrument_type: str = "SECURITY",
) -> T1RiskObservation:
    """Build one observation from caller-supplied raw, unadjusted minute bars."""

    try:
        normalized = normalize_security_symbol(symbol)
    except ValueError:
        return _invalid_observation(instrument_type, str(symbol), _date(entry_date), "invalid_symbol")
    day = _date(entry_date)
    calendar = _calendar(trading_calendar)
    if day is None or day not in calendar:
        return _invalid_observation(instrument_type, normalized, day, "invalid_entry_date")

    entry_target = _timestamp(day, time(10, 5))
    entry_contract = validate_target_minute_bars(
        minute_bars, symbol=normalized, bar_start=entry_target
    )
    if entry_contract.status != "VALID" or entry_contract.bars.empty:
        return _invalid_observation(instrument_type, normalized, day, "invalid_entry_bar")
    entry_bar = entry_contract.bars.iloc[0]
    if (
        entry_bar["trade_status"] != "normal"
        or entry_bar["limit_status"] in {"limit_up", "unknown"}
    ):
        return _invalid_observation(instrument_type, normalized, day, "entry_not_fillable")
    entry_price = entry_bar["open"]

    future_days = [value for value in calendar if value > day][:5]
    for sell_day in future_days:
        for bar_time in LEGAL_BAR_START_TIMES:
            target = _timestamp(sell_day, bar_time)
            contract = validate_target_minute_bars(
                minute_bars, symbol=normalized, bar_start=target
            )
            if contract.status != "VALID" or contract.bars.empty:
                continue
            bar = contract.bars.iloc[0]
            if (
                bar["trade_status"] == "normal"
                and bar["limit_status"] not in {"limit_down", "unknown"}
            ):
                return _observation(
                    instrument_type,
                    normalized,
                    day,
                    entry_price,
                    sell_day,
                    target,
                    target + pd.Timedelta(minutes=5),
                    bar["open"],
                    False,
                )

    if len(future_days) < 5:
        return _invalid_observation(
            instrument_type, normalized, day, "insufficient_trading_calendar"
        )
    fifth_day = future_days[4]
    close_price: Decimal | None = None
    completion_bar: pd.Timestamp | None = None
    for bar_time in reversed(LEGAL_BAR_START_TIMES):
        target = _timestamp(fifth_day, bar_time)
        contract = validate_target_minute_bars(
            minute_bars, symbol=normalized, bar_start=target
        )
        if contract.status == "VALID" and not contract.bars.empty:
            close_price = contract.bars.iloc[0]["close"]
            completion_bar = target
            break
    if close_price is None or completion_bar is None:
        return _invalid_observation(
            instrument_type, normalized, day, "missing_fifth_day_valid_close"
        )
    return _observation(
        instrument_type,
        normalized,
        day,
        entry_price,
        fifth_day,
        completion_bar,
        _timestamp(fifth_day, time(15, 0)),
        close_price,
        True,
    )


def linear_quantile_95(
    observations: Sequence[T1RiskObservation],
    evaluation_as_of: object,
) -> tuple[Decimal | None, int, int]:
    """Return the recent-252 linear 95th percentile, count, and censored count."""

    quantile, count, censored, _ = _quantile_details(observations, evaluation_as_of)
    return quantile, count, censored


def calculate_t1_risk(
    *,
    evaluation_as_of: object,
    entry_atr: Decimal | str,
    entry_price: Decimal | str,
    security_observations: Sequence[T1RiskObservation],
    industry_observations: Sequence[T1RiskObservation] = (),
    board_observations: Sequence[T1RiskObservation] = (),
    index_observations: Mapping[str, Sequence[T1RiskObservation]] | None = None,
    industry_metadata: ClassificationMetadata | None = None,
    board_metadata: ClassificationMetadata | None = None,
) -> T1RiskResult:
    """Select security/industry/board/index risk using information known as-of."""

    evaluation = _evaluation_as_of(evaluation_as_of)
    atr = _decimal(entry_atr)
    price = _decimal(entry_price)
    if evaluation is None or atr is None or price is None or atr <= 0 or price <= 0:
        return _blocked("invalid_normal_risk")

    combined_index = (
        tuple(
            observation
            for key in THREE_INDEX_KEYS
            for observation in index_observations[key]
        )
        if index_observations is not None
        and all(key in index_observations for key in THREE_INDEX_KEYS)
        else ()
    )
    levels: list[tuple[str, Sequence[T1RiskObservation], bool]] = [
        ("SECURITY", security_observations, True),
        (
            "INDUSTRY",
            industry_observations,
            _metadata_valid(industry_metadata, evaluation),
        ),
        ("BOARD", board_observations, _metadata_valid(board_metadata, evaluation)),
        ("THREE_INDEX", combined_index, True),
    ]
    accumulated_reasons: list[str] = []
    for level, observations, available in levels:
        if not available:
            continue
        quantile, count, censored, reasons = _quantile_details(observations, evaluation)
        accumulated_reasons.extend(reasons)
        if quantile is None:
            continue
        normal = Decimal("2") * atr / price
        effective = max(normal, quantile)
        if not normal.is_finite() or not effective.is_finite() or effective <= 0:
            return _blocked("invalid_effective_risk", accumulated_reasons)
        return T1RiskResult(
            status=Phase4Status.VALID,
            source_level=level,
            sample_count=count,
            t1_loss_q=quantile,
            censored_count=censored,
            normal_risk_pct=normal,
            effective_risk_pct=effective,
            observation_reasons=tuple(dict.fromkeys(accumulated_reasons)),
        )
    return _blocked("insufficient_t1_risk_samples", accumulated_reasons)


def _quantile_details(
    observations: Sequence[T1RiskObservation], evaluation_as_of: object
) -> tuple[Decimal | None, int, int, tuple[str, ...]]:
    evaluation = _evaluation_as_of(evaluation_as_of)
    if evaluation is None:
        return None, 0, 0, ("invalid_evaluation_as_of",)

    grouped: dict[tuple[str, str, date], list[tuple[T1RiskObservation, tuple[object, ...]]]] = defaultdict(list)
    reasons: list[str] = []
    for item in observations:
        canonical, reason = _canonical_observation(item, evaluation)
        if canonical is None:
            if reason:
                reasons.append(reason)
            continue
        key = canonical[0], canonical[1], canonical[2]
        grouped[key].append((item, canonical))

    unique: list[tuple[T1RiskObservation, tuple[object, ...]]] = []
    for key in sorted(grouped):
        records = grouped[key]
        signatures = {record[1] for record in records}
        if len(signatures) != 1:
            reasons.append(
                f"conflicting_t1_observation:{key[0]}:{key[1]}:{key[2].isoformat()}"
            )
            continue
        unique.append(records[0])

    unique.sort(
        key=lambda record: (
            record[1][2],
            record[1][5],
            record[1][0],
            record[1][1],
        )
    )
    selected = unique[-252:]
    if len(selected) < 120:
        return (
            None,
            len(selected),
            sum(bool(record[1][10]) for record in selected),
            tuple(sorted(set(reasons))),
        )
    losses = sorted(record[1][9] for record in selected)
    position = Decimal(len(losses) - 1) * Q95
    lower = int(position)
    fraction = position - Decimal(lower)
    upper = min(lower + 1, len(losses) - 1)
    quantile = losses[lower] + fraction * (losses[upper] - losses[lower])
    return (
        quantile,
        len(selected),
        sum(bool(record[1][10]) for record in selected),
        tuple(sorted(set(reasons))),
    )


def _canonical_observation(
    item: T1RiskObservation, evaluation: pd.Timestamp
) -> tuple[tuple[object, ...] | None, str]:
    if item.status != "VALID":
        return None, "invalid_t1_observation_status"
    instrument_type = str(item.instrument_type).strip().upper()
    if not instrument_type:
        return None, "invalid_t1_observation_instrument_type"
    try:
        symbol = normalize_security_symbol(item.symbol)
    except ValueError:
        return None, "invalid_t1_observation_symbol"
    sample_date = _date(item.sample_entry_date)
    completion_date = _date(item.completion_trade_date)
    completion_bar = _as_shanghai_timestamp(item.completion_bar_start)
    known_at = _as_shanghai_timestamp(item.known_at)
    entry_price = _decimal(item.entry_price)
    sell_price = _decimal(item.first_sellable_price)
    t1_return = _decimal(item.t1_return)
    t1_loss = _decimal(item.t1_loss)
    if sample_date is None or completion_date is None or completion_bar is None or known_at is None:
        return None, "invalid_t1_observation_time"
    if sample_date >= evaluation.date() or known_at >= evaluation:
        return None, "t1_observation_not_known_as_of"
    if completion_date != completion_bar.date() or completion_date != known_at.date():
        return None, "inconsistent_t1_observation_completion"
    expected_known = (
        _timestamp(completion_date, time(15, 0))
        if type(item.censored) is bool and item.censored
        else completion_bar + pd.Timedelta(minutes=5)
    )
    if known_at != expected_known:
        return None, "inconsistent_t1_observation_known_at"
    if entry_price is None or sell_price is None or entry_price <= 0 or sell_price <= 0:
        return None, "invalid_t1_observation_price"
    if t1_return is None:
        return None, "invalid_t1_return"
    if t1_loss is None or t1_loss < 0 or t1_loss > 1:
        return None, "invalid_t1_loss"
    if type(item.censored) is not bool:
        return None, "invalid_t1_observation_censored"
    return (
        instrument_type,
        symbol,
        sample_date,
        completion_date,
        completion_bar,
        known_at,
        entry_price,
        sell_price,
        t1_return,
        t1_loss,
        item.censored,
    ), ""


def _observation(
    instrument_type: str,
    symbol: str,
    entry_date: date,
    entry_price: Decimal,
    completion_date: date,
    completion_bar_start: pd.Timestamp,
    known_at: pd.Timestamp,
    sell_price: Decimal,
    censored: bool,
) -> T1RiskObservation:
    result = sell_price / entry_price - Decimal("1")
    return T1RiskObservation(
        instrument_type=instrument_type.strip().upper(),
        symbol=symbol,
        sample_entry_date=entry_date,
        completion_trade_date=completion_date,
        completion_bar_start=completion_bar_start,
        known_at=known_at,
        entry_price=entry_price,
        first_sellable_price=sell_price,
        t1_return=result,
        t1_loss=max(ZERO, -result),
        censored=censored,
    )


def _invalid_observation(
    instrument_type: str, symbol: str, entry_date: date | None, reason: str
) -> T1RiskObservation:
    safe_date = entry_date or date.min
    safe_timestamp = _timestamp(safe_date, time(0, 0))
    return T1RiskObservation(
        instrument_type=str(instrument_type).strip().upper(),
        symbol=symbol,
        sample_entry_date=safe_date,
        completion_trade_date=safe_date,
        completion_bar_start=safe_timestamp,
        known_at=safe_timestamp,
        entry_price=ZERO,
        first_sellable_price=ZERO,
        t1_return=ZERO,
        t1_loss=ZERO,
        censored=False,
        status="INVALID_OBSERVATION",
        failure_reason=reason,
    )


def _blocked(reason: str, observation_reasons: Sequence[str] = ()) -> T1RiskResult:
    return T1RiskResult(
        status=Phase4Status.BLOCK_NEW,
        source_level="",
        sample_count=0,
        t1_loss_q=ZERO,
        censored_count=0,
        normal_risk_pct=ZERO,
        effective_risk_pct=ZERO,
        failure_reason=reason,
        observation_reasons=tuple(dict.fromkeys(observation_reasons)),
    )


def _metadata_valid(
    metadata: ClassificationMetadata | None, evaluation_as_of: pd.Timestamp
) -> bool:
    if metadata is None:
        return False
    effective = _date(metadata.effective_date)
    known = _evaluation_as_of(metadata.known_at, date_only_at_ten=False)
    return bool(
        effective is not None
        and known is not None
        and effective <= evaluation_as_of.date()
        and known < evaluation_as_of
        and metadata.value.strip()
        and metadata.source.strip()
        and metadata.classification_version.strip()
    )


def _calendar(values: Iterable[date | str]) -> list[date]:
    parsed = {_date(value) for value in values}
    return sorted(value for value in parsed if value is not None)


def _timestamp(day: date, bar_time: time) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, bar_time)).tz_localize(SHANGHAI_TIMEZONE)


def _date(value: object) -> date | None:
    try:
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.tz_convert(SHANGHAI_TIMEZONE)
        return parsed.date()
    except (TypeError, ValueError, OverflowError):
        return None


def _evaluation_as_of(
    value: object, *, date_only_at_ten: bool = True
) -> pd.Timestamp | None:
    try:
        date_only = isinstance(value, date) and not isinstance(value, datetime)
        if isinstance(value, str):
            stripped = value.strip()
            date_only = len(stripped) == 10 and stripped[4:5] == "-" and stripped[7:8] == "-"
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            return None
        if date_only and date_only_at_ten:
            parsed = pd.Timestamp(datetime.combine(parsed.date(), time(10, 0)))
        if parsed.tzinfo is None:
            parsed = parsed.tz_localize(SHANGHAI_TIMEZONE)
        else:
            parsed = parsed.tz_convert(SHANGHAI_TIMEZONE)
        return parsed
    except (TypeError, ValueError, OverflowError):
        return None


def _as_shanghai_timestamp(value: object) -> pd.Timestamp | None:
    return _evaluation_as_of(value, date_only_at_ten=False)


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (Decimal, str)):
        return None
    try:
        parsed = Decimal(value)
        return parsed if parsed.is_finite() else None
    except (InvalidOperation, ValueError):
        return None
