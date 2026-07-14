from __future__ import annotations

from datetime import date, time

import numpy as np
import pandas as pd
import pytest

from stock_picker.strategies.adaptive_trend_v1_3.minute_contract import (
    SHANGHAI_TIMEZONE,
    legal_bar_start_times,
    resolve_next_execution_bar,
    validate_minute_bars,
)


def _bar(
    bar_start: object = "2025-07-14 10:05:00",
    **overrides,
) -> pd.DataFrame:
    values = {
        "symbol": "600001.SH",
        "trade_date": "2025-07-14",
        "bar_start": bar_start,
        "open": "10.00",
        "high": "10.20",
        "low": "9.90",
        "close": "10.10",
        "volume": "1000",
        "trade_status": "normal",
        "limit_status": "normal",
    }
    values.update(overrides)
    return pd.DataFrame([values])


def test_exactly_48_legal_bar_times_and_lunch_break_is_illegal() -> None:
    times = legal_bar_start_times()

    assert len(times) == 48
    assert times[0] == time(9, 30)
    assert times[23] == time(11, 25)
    assert times[24] == time(13, 0)
    assert times[-1] == time(14, 55)
    assert time(11, 30) not in times
    invalid = validate_minute_bars(_bar("2025-07-14 11:30:00"))
    assert invalid.status == "INVALID"
    assert "invalid_bar_start" in invalid.invalid_reasons


def test_naive_time_is_localized_and_homogeneous_utc_is_converted() -> None:
    naive = validate_minute_bars(_bar())
    utc = validate_minute_bars(_bar(pd.Timestamp("2025-07-14 02:05:00", tz="UTC")))

    assert naive.status == "VALID"
    assert str(naive.bars.loc[0, "bar_start"].tzinfo) == SHANGHAI_TIMEZONE
    assert utc.status == "VALID"
    assert utc.bars.loc[0, "bar_start"].strftime("%H:%M") == "10:05"


def test_mixed_timezone_representations_are_invalid() -> None:
    bars = pd.concat(
        [
            _bar("2025-07-14 10:05:00"),
            _bar(
                pd.Timestamp("2025-07-14 02:10:00", tz="UTC"),
            ).assign(symbol="600002.SH"),
        ],
        ignore_index=True,
    )

    result = validate_minute_bars(bars)

    assert result.status == "INVALID"
    assert "invalid_timezone" in result.invalid_reasons


@pytest.mark.parametrize(
    ("overrides", "reason_prefix"),
    [
        ({"high": "9.99"}, "invalid_ohlc"),
        ({"low": "10.11"}, "invalid_ohlc"),
        ({"open": "0"}, "invalid_ohlc"),
        ({"close": np.nan}, "invalid_ohlc"),
        ({"high": np.inf}, "invalid_ohlc"),
        ({"volume": "-1"}, "invalid_volume"),
        ({"trade_status": "halted"}, "invalid_trade_status"),
        ({"limit_status": "other"}, "invalid_limit_status"),
        ({"trade_date": "2025-07-15"}, "trade_date_bar_start_mismatch"),
    ],
)
def test_ohlcv_and_enum_contract_validation(overrides, reason_prefix) -> None:
    result = validate_minute_bars(_bar(**overrides))

    assert result.status == "INVALID"
    assert reason_prefix in result.invalid_reasons


def test_identical_duplicate_is_deduplicated_deterministically() -> None:
    original = _bar()
    duplicate = pd.concat([original, original], ignore_index=True)

    result = validate_minute_bars(duplicate)

    assert result.status == "VALID"
    assert len(result.bars) == 1


def test_conflicting_duplicate_bar_is_invalid_in_any_input_order() -> None:
    first = _bar()
    second = _bar(open="10.01")
    orders = (
        pd.concat([first, second], ignore_index=True),
        pd.concat([second, first], ignore_index=True),
    )

    results = [validate_minute_bars(frame) for frame in orders]

    assert [result.invalid_reasons for result in results] == [
        ("conflicting_duplicate_bar:10:05",),
        ("conflicting_duplicate_bar:10:05",),
    ]


def test_input_order_does_not_change_canonical_output() -> None:
    bars = pd.concat(
        [
            _bar("2025-07-14 10:10:00"),
            _bar("2025-07-14 10:05:00"),
        ],
        ignore_index=True,
    )

    forward = validate_minute_bars(bars)
    reverse = validate_minute_bars(bars.iloc[::-1].reset_index(drop=True))

    pd.testing.assert_frame_equal(forward.bars, reverse.bars)


def test_invalid_input_order_does_not_change_reason_order() -> None:
    bars = pd.concat(
        [
            _bar("2025-07-14 10:10:00", volume="-1"),
            _bar("2025-07-14 10:05:00", open="0"),
        ],
        ignore_index=True,
    )

    forward = validate_minute_bars(bars)
    reverse = validate_minute_bars(bars.iloc[::-1].reset_index(drop=True))

    assert forward.invalid_reasons == reverse.invalid_reasons


def test_resolve_normal_next_bar_and_lunch_boundary() -> None:
    normal = resolve_next_execution_bar("2025-07-14 10:05", ["2025-07-14"])
    lunch = resolve_next_execution_bar("2025-07-14 11:25", ["2025-07-14"])

    assert normal.execution_bar_start.strftime("%H:%M") == "10:10"
    assert lunch.execution_bar_start.strftime("%H:%M") == "13:00"


def test_resolve_1455_uses_next_calendar_day_and_friday_to_monday() -> None:
    next_day = resolve_next_execution_bar(
        "2025-07-14 14:55", ["2025-07-14", "2025-07-15"]
    )
    weekend = resolve_next_execution_bar(
        "2025-07-18 14:55", [date(2025, 7, 18), date(2025, 7, 21)]
    )

    assert next_day.execution_bar_start.isoformat() == "2025-07-15T09:30:00+08:00"
    assert weekend.execution_bar_start.isoformat() == "2025-07-21T09:30:00+08:00"


def test_resolve_1455_without_next_calendar_day_fails_stably() -> None:
    result = resolve_next_execution_bar("2025-07-14 14:55", ["2025-07-14"])

    assert result.status == "FAILED"
    assert result.execution_bar_start is None
    assert result.failure_reason == "missing_execution_bar"


def test_utc_conversion_uses_shanghai_local_date_across_midnight() -> None:
    result = validate_minute_bars(
        _bar(
            pd.Timestamp("2025-07-14 16:00:00", tz="UTC"),
            trade_date="2025-07-14",
        )
    )

    assert result.status == "INVALID"
    assert result.bars.loc[0, "bar_start"].isoformat().startswith(
        "2025-07-15T00:00:00+08:00"
    )
    assert "trade_date_bar_start_mismatch" in result.invalid_reasons


def test_unparseable_row_reasons_never_depend_on_index_or_input_order() -> None:
    first = _bar("not-a-time", symbol="bad-symbol", trade_date="not-a-date")
    second = _bar("also-bad", symbol="also-bad", trade_date="still-bad")
    bars = pd.concat([first, second], ignore_index=True)

    forward = validate_minute_bars(bars)
    reverse = validate_minute_bars(bars.iloc[::-1].reset_index(drop=True))

    assert forward.invalid_reasons == reverse.invalid_reasons
    assert not any("row=" in reason for reason in forward.invalid_reasons)
    assert "invalid_symbol" in forward.invalid_reasons
    assert "invalid_bar_start" in forward.invalid_reasons
    assert "invalid_trade_date" in forward.invalid_reasons


def test_duplicate_key_excludes_trade_date_and_conflict_is_order_independent() -> None:
    first = _bar(symbol="600001", trade_date="2025-07-14")
    second = _bar(symbol="600001.SH", trade_date="2025-07-15")
    orders = (
        pd.concat([first, second], ignore_index=True),
        pd.concat([second, first], ignore_index=True),
    )

    results = [validate_minute_bars(frame) for frame in orders]

    assert [result.status for result in results] == ["INVALID", "INVALID"]
    assert [result.invalid_reasons for result in results] == [
        (
            "conflicting_duplicate_bar:10:05",
            "trade_date_bar_start_mismatch",
        ),
        (
            "conflicting_duplicate_bar:10:05",
            "trade_date_bar_start_mismatch",
        ),
    ]
