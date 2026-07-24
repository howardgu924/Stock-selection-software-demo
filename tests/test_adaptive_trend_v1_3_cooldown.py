from datetime import date

import pytest

from stock_picker.strategies.adaptive_trend_v1_3 import (
    COOLDOWN_DAYS,
    cooldown_blocked,
    create_cooldown_record,
    merge_cooldown_records,
)


CAL = ["2025-01-03", "2025-01-06", "2025-01-07", "2025-01-09",
       "2025-01-10", "2025-01-13", "2025-01-14", "2025-01-15"]


@pytest.mark.parametrize(
    "reason,days",
    [("EMERGENCY_MARKET", 1), ("INITIAL_STOP", 5), ("TRAILING_STOP", 5),
     ("STRONG_TOP_DIVERGENCE", 3), ("MA60_TREND_BREAK", 3),
     ("WEAK_SCORE_CONFIRMED", 3), ("REPLACEMENT_EXIT", 2),
     ("PORTFOLIO_EXPOSURE_REDUCTION", 2)],
)
def test_frozen_cooldown_durations_use_actual_trading_days(reason, days):
    record = create_cooldown_record(symbol="600001", exit_reason=reason,
                                    exit_trade_date="2025-01-03",
                                    trading_calendar=CAL, full_exit=True)
    assert COOLDOWN_DAYS[reason] == days
    assert len(record.blocked_trade_dates) == days
    assert date(2025, 1, 3) not in record.blocked_trade_dates
    assert record.reentry_allowed_date == pd_date(CAL[days + 1])


def pd_date(value):
    return date.fromisoformat(value)


def test_partial_exit_and_unlisted_reason_create_no_cooldown():
    assert create_cooldown_record(symbol="600001", exit_reason="INITIAL_STOP",
                                  exit_trade_date="2025-01-03", trading_calendar=CAL,
                                  full_exit=False) is None
    assert create_cooldown_record(symbol="600001", exit_reason="MA20_REDUCTION",
                                  exit_trade_date="2025-01-03", trading_calendar=CAL,
                                  full_exit=True) is None


def test_blocked_until_allowed_date():
    record = create_cooldown_record(symbol="600001", exit_reason="REPLACEMENT_EXIT",
                                    exit_trade_date="2025-01-03", trading_calendar=CAL,
                                    full_exit=True)
    assert cooldown_blocked(record, "2025-01-07") is True
    assert cooldown_blocked(record, "2025-01-09") is False


def test_merge_keeps_later_allowed_date_and_normalizes_alias():
    short = create_cooldown_record(symbol="600001", exit_reason="EMERGENCY_MARKET",
                                   exit_trade_date="2025-01-03", trading_calendar=CAL,
                                   full_exit=True)
    long = create_cooldown_record(symbol="600001.SH", exit_reason="INITIAL_STOP",
                                  exit_trade_date="2025-01-03", trading_calendar=CAL,
                                  full_exit=True)
    assert merge_cooldown_records([short, long]) is long


def test_invalid_symbol_or_insufficient_calendar_is_stable():
    with pytest.raises(ValueError, match="invalid_cooldown_input"):
        create_cooldown_record(symbol="bad", exit_reason="INITIAL_STOP",
                               exit_trade_date="2025-01-03", trading_calendar=CAL,
                               full_exit=True)
    with pytest.raises(ValueError, match="insufficient_trading_calendar"):
        create_cooldown_record(symbol="600001", exit_reason="INITIAL_STOP",
                               exit_trade_date="2025-01-03", trading_calendar=CAL[:3],
                               full_exit=True)
