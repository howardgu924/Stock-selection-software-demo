from datetime import date

import pandas as pd
import pytest

from stock_picker.strategies.adaptive_trend_v1_3 import (
    LookaheadAccessError, MarketView, build_event_clock, deterministic_id,
)


def test_event_clock_frozen_order_and_global_sequence():
    events=build_event_clock("r",(date(2025,1,2),date(2025,1,3)))
    assert events[0].event_type=="SESSION_START"
    assert events[-1].event_type=="SESSION_CLOSE"
    assert [event.sequence_number for event in events]==list(range(len(events)))
    ten=[event.event_type for event in events if event.trade_date==date(2025,1,2) and event.event_time=="10:00"]
    assert ten==["BAR_OPEN","DECISION_1000","BAR_CLOSE"]
    fourteen=[event.event_type for event in events if event.trade_date==date(2025,1,2) and event.event_time=="14:30"]
    assert fourteen==["BAR_OPEN","DECISION_1430","BAR_CLOSE"]


def test_event_ids_are_stable_and_unique():
    first=build_event_clock("r",(date(2025,1,2),))
    second=build_event_clock("r",(date(2025,1,2),))
    assert first==second
    assert len({item.event_id for item in first})==len(first)
    assert deterministic_id("fill","a",1)==deterministic_id("fill","a",1)


@pytest.mark.parametrize(
    "bar_time",
    ["09:30","09:35","09:40","09:45","09:50","09:55","10:00","10:05",
     "10:30","11:25","13:00","13:05","14:25","14:30","14:35","14:55"],
)
def test_each_legal_bar_has_open_before_close(bar_time):
    events=build_event_clock("r",(date(2025,1,2),))
    matching=[event.event_type for event in events if event.event_time==bar_time]
    assert matching[0]=="BAR_OPEN"
    assert matching[-1]=="BAR_CLOSE"


def rows():
    return [
        {"symbol":"600001","bar_start":"2025-01-02 09:55+08:00","open":"10","high":"11","low":"9","close":"10.5","volume":"1","amount":"10"},
        {"symbol":"600001","bar_start":"2025-01-02 10:00+08:00","open":"10.6","high":"12","low":"10","close":"11","volume":"2","amount":"20"},
        {"symbol":"600001","bar_start":"2025-01-02 14:25+08:00","open":"11","high":"12","low":"10","close":"11.5","volume":"3","amount":"30"},
        {"symbol":"600001","bar_start":"2025-01-02 14:30+08:00","open":"11.6","high":"13","low":"11","close":"12","volume":"4","amount":"40"},
    ]


@pytest.mark.parametrize(("phase","count"),[("DECISION_1000",1),("DECISION_1430",3)])
def test_decision_views_exclude_current_bar(phase,count):
    view=MarketView(rows(),as_of="2025-01-02 15:00+08:00",phase=phase,symbol="600001.SH")
    assert len(view.minute_rows())==count


def test_bar_open_exposes_only_open():
    view=MarketView(rows(),as_of="2025-01-02 10:00+08:00",phase="BAR_OPEN",symbol="600001")
    current=view.minute_rows()[-1]
    assert current["open"]=="10.6"
    assert all(name not in current for name in ("high","low","close","volume","amount"))


@pytest.mark.parametrize("requested",["2025-01-02 10:05+08:00","2025-01-03 09:30+08:00"])
def test_future_access_raises(requested):
    view=MarketView(rows(),as_of="2025-01-02 10:00+08:00",phase="BAR_OPEN")
    with pytest.raises(LookaheadAccessError) as caught:
        view.require_not_after(requested)
    assert caught.value.code=="LOOKAHEAD_ACCESS"
