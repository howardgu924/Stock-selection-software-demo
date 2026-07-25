from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from stock_picker.strategies.adaptive_trend_v1_3 import (
    AccountProfile, DateRangeKind, DateRangeSpec, Phase5Error, RunMode,
    UniverseKind, UniverseSpec, create_account_snapshot, resolve_date_range,
    resolve_universe,
)


def calendar(count=1300):
    return [item.date() for item in pd.bdate_range("2022-01-03", periods=count)]


@pytest.mark.parametrize(
    ("kind","value"),
    [(DateRangeKind.RECENT_MONTHS,1),(DateRangeKind.RECENT_MONTHS,3),
     (DateRangeKind.RECENT_MONTHS,12),(DateRangeKind.RECENT_YEARS,1),
     (DateRangeKind.RECENT_YEARS,2),(DateRangeKind.RECENT_YEARS,3)],
)
def test_recent_ranges_resolve_on_trading_calendar(kind, value):
    result = resolve_date_range(DateRangeSpec(kind,value=value),calendar())
    assert result.actual_start_date in calendar()
    assert result.actual_end_date == calendar()[-1]
    assert len(result.warmup_dates) == 320


def test_custom_range_is_inclusive_and_has_warmup():
    days = calendar()
    result = resolve_date_range(DateRangeSpec("CUSTOM",start_date=days[400],end_date=days[410]),days)
    assert result.trading_dates == tuple(days[400:411])
    assert result.warmup_dates == tuple(days[80:400])


@pytest.mark.parametrize("value",[True,False,0,-1,-99,1.5,"1",None])
def test_invalid_recent_values(value):
    with pytest.raises(Phase5Error) as caught:
        resolve_date_range(DateRangeSpec("RECENT_MONTHS",value=value),calendar())
    assert caught.value.code == "INVALID_DATE_RANGE"


@pytest.mark.parametrize(
    ("start","end"),
    [("bad","2025-01-01"),("2025-01-01","bad"),("2025-02-01","2025-01-01"),(None,None)],
)
def test_invalid_custom_ranges(start,end):
    with pytest.raises(Phase5Error):
        resolve_date_range(DateRangeSpec("CUSTOM",start_date=start,end_date=end),calendar())


def test_insufficient_warmup_is_not_shortened():
    days = calendar(321)
    with pytest.raises(Phase5Error) as caught:
        resolve_date_range(DateRangeSpec("CUSTOM",start_date=days[319],end_date=days[-1]),days)
    assert caught.value.code == "INSUFFICIENT_WARMUP"


@pytest.mark.parametrize("kind",["MANUAL","WATCHLIST","MARKET_SCOPE","COMBINED"])
def test_universe_modes(kind):
    spec = UniverseSpec(
        kind, manual_symbols=("600001","000001.SZ"),
        watchlist_names=("w",),market_scopes=("s",),
    )
    result = resolve_universe(
        spec,watchlist_loader=lambda _:["600002"],market_scope_loader=lambda _:["300001"],
    )
    assert result.candidate_symbols == tuple(sorted(set(result.candidate_symbols)))
    assert set(result.benchmark_symbols).issubset(result.required_symbols)


def test_positions_are_required_not_candidates():
    result = resolve_universe(UniverseSpec("MANUAL",manual_symbols=("600001",)),current_positions=("000001",))
    assert "000001.SZ" in result.required_symbols
    assert "000001.SZ" not in result.candidate_symbols


def test_aliases_deduplicate_stably():
    first = resolve_universe(UniverseSpec("MANUAL",manual_symbols=("600001","600001.SH","000001")))
    second = resolve_universe(UniverseSpec("MANUAL",manual_symbols=("000001","600001.SH","600001")))
    assert first == second
    assert first.candidate_symbols == ("000001.SZ","600001.SH")


@pytest.mark.parametrize("symbols",[(),("bad",),("123",),("600001","bad")])
def test_empty_or_invalid_universe_rejected(symbols):
    with pytest.raises(Phase5Error) as caught:
        resolve_universe(UniverseSpec("MANUAL",manual_symbols=symbols))
    assert caught.value.code == "INVALID_UNIVERSE"


def profile(tmp_path):
    return AccountProfile(
        "p",Decimal("100000"),"200000","fees","CNY",
        UniverseSpec("MANUAL",manual_symbols=("600001",)),("p1",),
        tmp_path/"data",tmp_path/"reports",
    )


@pytest.mark.parametrize("mode",[RunMode.BACKTEST,RunMode.DAILY_PAPER])
def test_account_snapshot_is_deterministic_shape(mode,tmp_path):
    snapshot = create_account_snapshot(profile(tmp_path),mode,paper_positions=(("600001","state"),))
    assert snapshot.cash == (Decimal("100000") if mode == RunMode.BACKTEST else Decimal("200000"))
    assert (not snapshot.positions) if mode == RunMode.BACKTEST else bool(snapshot.positions)


def test_backtest_only_uses_explicit_initial_portfolio(tmp_path):
    snapshot = create_account_snapshot(profile(tmp_path),"BACKTEST",paper_positions=(("600001","paper"),),initial_portfolio=(("000001","historical"),))
    assert snapshot.positions == (("000001.SZ","historical"),)


@pytest.mark.parametrize("cash",[1.0,True,"NaN","Infinity","-1","x"])
def test_account_cash_strict_decimal(cash,tmp_path):
    base = profile(tmp_path)
    invalid = AccountProfile(base.account_profile_id,cash,base.paper_cash,base.fee_schedule_id,base.base_currency,base.default_universe,base.provider_priority,base.data_directory,base.report_directory)
    with pytest.raises(Phase5Error):
        create_account_snapshot(invalid,"BACKTEST")
