from dataclasses import replace
from datetime import date
from decimal import Decimal
import sqlite3

import pandas as pd
import pytest

import stock_picker.strategies.adaptive_trend_v1_3.strategy_runtime as strategy_runtime
from stock_picker.strategies.adaptive_trend_v1_3 import (
    AccountSnapshot, CoreStrategyDependencies, DataSnapshot, MarketCache,
    NetworkAccessPolicy, Phase5Error, PositionLot, PositionState, PositionStatus,
    ResolvedDateRange, RunConfig, RunMode, RunStore, UniverseSnapshot,
    ClockEvent, ExecutionType, ExitControlState, FeeRuleSnapshot, FillRequest, PendingSellState,
    CooldownRecord, CooldownStatus, PendingSellStatus, RuntimeHooks,
    TradingRuleSnapshot, create_run, execute_run, resume_run,
)
from stock_picker.strategies.adaptive_trend_v1_3.run_orchestrator import _apply_due_fills
from stock_picker.strategies.adaptive_trend_v1_3.run_store import canonical_json, stable_hash


DAY = date(2025, 1, 2)


def resolved():
    return ResolvedDateRange(DAY, DAY, DAY, DAY, DAY, (DAY,), (DAY,), 1)


def config(tmp_path, data_id="data"):
    return RunConfig(
        RunMode.BACKTEST, "V1.3.14", "account", "universe", data_id,
        resolved(), 1, "RAW_UNADJUSTED_V1", NetworkAccessPolicy.FORBID,
        str(tmp_path), "EMPTY", "2025-01-01T00:00:00+08:00", "config",
        git_commit_sha="b" * 40, schema_version=3,
    )


def position():
    lot = PositionLot(
        date(2025, 1, 1), 100, 100, Decimal("10"), Decimal("0"),
        DAY, 1, Decimal("1000"),
    )
    return PositionState(
        "600001.SH", 100, 0, 100, Decimal("10"), Decimal("1000"),
        date(2025, 1, 1), Decimal("10"), Decimal("1"), Decimal("10"),
        Decimal("0"), (lot,), PositionStatus.OPEN, date(2025, 1, 1),
    )


def bundle(*, with_position=False, data=None):
    positions=(("600001.SH", position()),) if with_position else ()
    controls=(("600001.SH",control_state()),) if with_position else ()
    return {
        "account": AccountSnapshot(
            "account", "profile", RunMode.BACKTEST, Decimal("1000"), positions,
            "fee", "CNY", "2025-01-01T00:00:00+08:00", "account-hash",
            exit_controls=controls,
        ),
        "universe": UniverseSnapshot(
            "universe", ("600001.SH",), ("600001.SH",), (),
            ("000300.SH", "000905.SH", "000852.SH"), ("manual",),
            "universe-hash", "2025-01-01T00:00:00+08:00",
        ),
        "data": data or DataSnapshot(
            "data", ("p",), "RAW_UNADJUSTED_V1", "2025-01-01T00:00:00+08:00",
            "data-hash", (), (DAY.isoformat(),), ("rule",), ("fee",), "READY",
            (("p", "daily_bar", "k", "provider", "1", "1d"),),
        ),
    }


def state(*, with_position=False):
    positions={"600001.SH": position()} if with_position else {}
    controls={"600001.SH":control_state()} if with_position else {}
    return {
        "cash": "1000", "positions": positions, "pending_sells": {},
        "exit_controls": controls, "cooldowns": {}, "fill_requests": (),
    }


def dependencies(calls=None, marks=None):
    dates=pd.bdate_range(end="2025-01-01",periods=220)
    history=pd.DataFrame({
        "date":dates,"open":"10","high":"11","low":"9",
        "close":[str(10+i/100) for i in range(220)],
    })
    indexes={symbol:history for symbol in ("000300.SH","000852.SH","399006.SZ")}
    def data_1000(context,event):
        return {
            "market_overlay":{"index_histories":indexes,"as_of":"2025-01-01"},
            "opportunity_score":{"histories":{},"benchmark_histories":indexes,"as_of":"2025-01-01"},
            "divergence":(),"risk_overlay":(),"execution_gate":(),"t1_risk":(),
            "position_sizing":(),
            "portfolio_allocator":{
                "candidates":(),"existing_holdings":(),"portfolio_equity":Decimal("1000"),
                "effective_exposure_cap":Decimal("1"),"evaluation_as_of":"2025-01-02 10:00+08:00",
            },
        }
    def hard_data(context,event):
        return {
            symbol:{
                "trigger_bar_start":pd.Timestamp(
                    f"{event.trade_date} {event.event_time}",tz="Asia/Shanghai"
                ),
                "completed_bar_low":"12","emergency_status":"NORMAL",
                "price_basis_id":"RAW_UNADJUSTED_V1",
            }
            for symbol in context.get("positions",{})
        }
    def close_data(context,event):
        holdings={}
        for symbol in context.get("positions",{}):
            mark=(marks or {}).get(symbol)
            if mark:
                price=mark.get("close",mark.get("mark_price"))
                bars=pd.DataFrame([{
                    "symbol":symbol,"trade_date":event.trade_date,
                    "bar_start":pd.Timestamp(f"{event.trade_date} 14:55",tz="Asia/Shanghai"),
                    "open":price,"high":price,"low":price,"close":price,"volume":"1",
                    "trade_status":"normal","price_basis_id":"RAW_UNADJUSTED_V1",
                    "source_partition_id":"p",
                }])
                holdings[symbol]={"bars":bars,"session_status":"normal","atr20":mark.get("atr20","1")}
            else:
                holdings[symbol]={"bars":pd.DataFrame(),"session_status":"normal","atr20":"1"}
        return {"holdings":holdings}
    return CoreStrategyDependencies(
        decision_1000_data=data_1000,bar_close_data=hard_data,
        decision_1430_data=lambda context,event:{
            "holdings":(),"replacement_candidates":(),"portfolio_equity":Decimal("1000"),
            "existing_exposure":Decimal("0"),"effective_exposure_cap":Decimal("1"),
            "market_allows_new":True,"emergency_normal":True,"no_new_slots":False,
        },
        session_close_data=close_data,
        minute_bars=lambda request,event:pd.DataFrame(),
        trading_rule=lambda symbol,event:None, fee_rule=lambda event:None,
    )


def control_state():
    return ExitControlState(
        "600001.SH",date(2025,1,1),Decimal("8"),Decimal("8"),
        Decimal("10"),"RAW_UNADJUSTED_V1",
    )


@pytest.mark.parametrize("missing", (
    "decision_1000_data","bar_close_data","decision_1430_data",
    "session_close_data","minute_bars","trading_rule","fee_rule",
))
def test_each_mandatory_dependency_is_rejected(tmp_path, missing):
    store=RunStore(tmp_path/f"{missing}.sqlite3")
    run=create_run(store,config(tmp_path),bundle(),run_id=missing)
    deps=dependencies()
    object.__setattr__(deps,missing,None)
    with pytest.raises(Phase5Error) as caught:
        execute_run(store,run,config(tmp_path),state(),dependencies=deps)
    assert caught.value.code=="INVALID_CONFIG"


def test_mandatory_event_chain_and_1430_run_without_hooks(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3")
    run=create_run(store,config(tmp_path),bundle(),run_id="run")
    execute_run(store,run,config(tmp_path),state(),dependencies=dependencies())
    assert len(store.completed_event_ids(run)) == 100
    assert {
        row["decision_type"] for row in store.rows("adaptive_v13_decisions",run)
    } == {"ENTRY", "EXIT_1430"}


@pytest.mark.parametrize("obsolete", (
    "market_overlay","opportunity_score","divergence","risk_overlay",
    "execution_gate","t1_risk","position_sizing","portfolio_allocator",
    "hard_exit","coordinate_1430","valuation",
))
def test_obsolete_core_callable_dependency_cannot_be_constructed(obsolete):
    values = {
        name: getattr(dependencies(), name)
        for name in CoreStrategyDependencies.__dataclass_fields__
    }
    values[obsolete] = lambda *_: {}
    with pytest.raises(TypeError):
        CoreStrategyDependencies(**values)


@pytest.mark.parametrize("hook_result", (None, False, {}))
def test_hook_return_value_cannot_skip_fixed_core_chain(tmp_path, hook_result):
    calls=[]
    store=RunStore(tmp_path/f"hook-{hook_result is False}.sqlite3")
    run=create_run(store,config(tmp_path),bundle(),run_id="run")
    execute_run(
        store,run,config(tmp_path),state(),dependencies=dependencies(),
        hooks=RuntimeHooks(before_component=lambda name,value:(calls.append(name),hook_result)[1]),
    )
    assert calls[:8] == list(strategy_runtime.ORDER_1000)
    assert calls.count("coordinate_1430") == 1


def test_internal_real_1430_coordinator_called_exactly_once(tmp_path, monkeypatch):
    calls=[]
    original=strategy_runtime.coordinate_1430_exit_cycle
    def spy(**kwargs):
        calls.append(kwargs["decision_trade_date"])
        return original(**kwargs)
    monkeypatch.setattr(strategy_runtime,"coordinate_1430_exit_cycle",spy)
    deps=dependencies()
    object.__setattr__(deps,"coordinate_1430",lambda *_: (_ for _ in ()).throw(
        AssertionError("obsolete injected coordinator was called")
    ))
    store=RunStore(tmp_path/"coordinator.sqlite3")
    run=create_run(store,config(tmp_path),bundle(),run_id="run")
    execute_run(store,run,config(tmp_path),state(),dependencies=deps)
    assert calls == [DAY]


def test_core_engine_exception_fails_event_and_run(tmp_path, monkeypatch):
    def fail(**_):
        raise RuntimeError("injected_core_failure")
    monkeypatch.setattr(strategy_runtime,"calculate_market_overlay",fail)
    store=RunStore(tmp_path/"failure.sqlite3")
    run=create_run(store,config(tmp_path),bundle(),run_id="run")
    with pytest.raises(RuntimeError,match="injected_core_failure"):
        execute_run(store,run,config(tmp_path),state(),dependencies=dependencies())
    assert store.get_run(run)["status"] == "FAILED"
    assert all(
        row["event_type"] != "DECISION_1000"
        for row in store.rows("adaptive_v13_run_events",run)
    )


def test_every_bar_close_evaluates_each_holding(tmp_path):
    calls=[]
    store=RunStore(tmp_path/"runs.sqlite3")
    run=create_run(store,config(tmp_path),bundle(with_position=True),run_id="run")
    execute_run(
        store,run,config(tmp_path),state(with_position=True),
        dependencies=dependencies(marks={"600001.SH":{"close":"12","atr20":"1","price_basis_id":"RAW_UNADJUSTED_V1"}}),
        hooks=RuntimeHooks(before_component=lambda name,value:calls.append((name,"",""))),
        trading_calendar=(date(2025,1,1),DAY),
    )
    assert sum(item[0]=="hard_exit" for item in calls)==48


def test_session_close_values_open_position_not_cash_only(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3")
    run=create_run(store,config(tmp_path),bundle(with_position=True),run_id="run")
    result=execute_run(
        store,run,config(tmp_path),state(with_position=True),
        dependencies=dependencies(marks={"600001.SH":{"close":"12","atr20":"1","price_basis_id":"RAW_UNADJUSTED_V1"}}),
        trading_calendar=(date(2025,1,1),DAY),
    )
    daily=store.rows("adaptive_v13_daily_account_snapshots",run)[0]
    assert Decimal(daily["equity"])==Decimal("2200")
    assert result["last_equity"]==Decimal("2200")
    assert store.get_run(run)["status"]=="COMPLETED_WITH_OPEN_POSITIONS"


def test_external_final_mark_fields_cannot_override_raw_bar_selection(tmp_path):
    deps=dependencies()
    original=deps.session_close_data
    def raw_only(context,event):
        supplied=dict(original(context,event))
        supplied["position_marks"]={
            "600001.SH":{"close":"999","mark_price":"999"}
        }
        supplied["final_equity"]="999999"
        return supplied
    deps=replace(deps,session_close_data=raw_only)
    store=RunStore(tmp_path/"external-mark.sqlite3")
    run=create_run(store,config(tmp_path),bundle(with_position=True),run_id="run")
    result=execute_run(
        store,run,config(tmp_path),state(with_position=True),dependencies=replace(
            deps,session_close_data=lambda context,event:{
                **raw_only(context,event),
                "holdings":{
                    "600001.SH":{
                        "bars":pd.DataFrame([{
                            "symbol":"600001.SH","trade_date":DAY,
                            "bar_start":pd.Timestamp(f"{DAY} 14:55",tz="Asia/Shanghai"),
                            "open":"12","high":"12","low":"12","close":"12","volume":"1",
                            "trade_status":"normal","price_basis_id":"RAW_UNADJUSTED_V1",
                            "source_partition_id":"p",
                        }]),
                        "session_status":"normal","atr20":"1",
                    }
                },
            },
        ),trading_calendar=(date(2025,1,1),DAY),
    )
    assert result["last_equity"]==Decimal("2200")


def test_missing_mark_degrades_but_does_not_force_liquidation(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3")
    run=create_run(store,config(tmp_path),bundle(with_position=True),run_id="run")
    result=execute_run(
        store,run,config(tmp_path),state(with_position=True),
        dependencies=dependencies(),trading_calendar=(date(2025,1,1),DAY),
    )
    assert "MISSING_MARK_PRICE:600001.SH" in result["degraded_reasons"]
    assert result["positions"]["600001.SH"].total_qty==100
    assert store.get_run(run)["status"]=="DEGRADED"


@pytest.mark.parametrize(
    ("requested","missing"),
    [
        ((DAY,), ()),
        ((DAY,date(2025,1,3)), ("2025-01-03",)),
        ((date(2025,1,1),DAY), ("2025-01-01",)),
    ],
)
def test_cache_coverage_is_exact_by_trade_date(tmp_path, requested, missing):
    cache=MarketCache(tmp_path/"cache.sqlite3")
    cache.store_partition(
        "daily_bar","600001",[
            {"date":DAY,"open":"10","high":"11","low":"9","close":"10"},
        ],source="p",source_version="1",expected_trade_dates=(DAY,),
    )
    _,actual=cache.coverage("600001",requested)
    assert actual==missing


def real_snapshot(cache):
    part=cache.store_partition(
        "daily_bar","600001",[
            {"date":DAY,"open":"10","high":"11","low":"9","close":"10"},
        ],source="provider",source_version="1",expected_trade_dates=(DAY,),
    )
    rule=cache.store_partition(
        "trading_rule_snapshot","rule",[
            {"date":DAY,"rule_version":"rule-v1"},
        ],source="rules",source_version="1",expected_trade_dates=(DAY,),
    )
    fee=cache.store_partition(
        "fee_rule_snapshot","fee",[
            {"date":DAY,"fee_version":"fee-v1"},
        ],source="fees",source_version="1",expected_trade_dates=(DAY,),
    )
    return cache.create_snapshot(
        (part.partition_id,rule.partition_id,fee.partition_id),
        required_trade_dates=(DAY,),
    )


def test_resume_reads_database_snapshot_without_caller_mapping(tmp_path):
    cache=MarketCache(tmp_path/"cache.sqlite3")
    data=real_snapshot(cache)
    cfg=config(tmp_path,data.data_snapshot_id)
    store=RunStore(tmp_path/"runs.sqlite3")
    run=create_run(store,cfg,bundle(data=data),run_id="run")
    store.update_run_status(run,"COMPLETED")
    recovered=resume_run(
        store,run,cache,dependencies=dependencies(),
        trading_calendar=(DAY,),
    )
    assert recovered["cash"]==Decimal("1000")
    assert store.rows("adaptive_v13_audit_events",run)[-1]["component"]=="run_recovery"


def test_resume_rejects_deleted_cache_partition(tmp_path):
    cache=MarketCache(tmp_path/"cache.sqlite3")
    data=real_snapshot(cache)
    cfg=config(tmp_path,data.data_snapshot_id)
    store=RunStore(tmp_path/"runs.sqlite3")
    run=create_run(store,cfg,bundle(data=data),run_id="run")
    with sqlite3.connect(cache.db_path) as connection:
        connection.execute("DELETE FROM adaptive_v13_cache_rows")
        connection.execute("DELETE FROM adaptive_v13_snapshot_partition_links")
        connection.execute("DELETE FROM adaptive_v13_cache_partitions")
    with pytest.raises(Phase5Error) as caught:
        resume_run(store,run,cache,dependencies=dependencies())
    assert caught.value.code=="DATA_NOT_READY"


def test_resume_rejects_changed_partition_hash(tmp_path):
    cache=MarketCache(tmp_path/"cache.sqlite3")
    data=real_snapshot(cache)
    cfg=config(tmp_path,data.data_snapshot_id)
    store=RunStore(tmp_path/"runs.sqlite3")
    run=create_run(store,cfg,bundle(data=data),run_id="run")
    with sqlite3.connect(cache.db_path) as connection:
        connection.execute(
            "UPDATE adaptive_v13_cache_partitions SET content_sha256='changed'"
        )
    with pytest.raises(Phase5Error) as caught:
        resume_run(store,run,cache,dependencies=dependencies())
    assert caught.value.code=="RUN_FINGERPRINT_MISMATCH"


@pytest.mark.parametrize(
    ("checkpoint_day","retained"),
    ((date(2025,1,3),True),(date(2025,1,6),False)),
)
def test_resume_cooldown_uses_checkpoint_boundary_not_run_end(
    tmp_path,checkpoint_day,retained
):
    cache=MarketCache(tmp_path/"cache.sqlite3")
    data=real_snapshot(cache)
    end=date(2025,1,10)
    dates=(DAY,date(2025,1,3),date(2025,1,6),date(2025,1,7),end)
    date_range=ResolvedDateRange(DAY,end,DAY,end,DAY,dates,dates,1)
    cfg=replace(config(tmp_path,data.data_snapshot_id),date_range=date_range)
    store=RunStore(tmp_path/"runs.sqlite3")
    run=create_run(store,cfg,bundle(data=data),run_id="run")
    cooldown=CooldownRecord(
        "600001.SH","INITIAL_STOP",DAY,
        (date(2025,1,3),),date(2025,1,6),CooldownStatus.ACTIVE,
    )
    checkpoint_state=state()
    with store.transaction() as connection:
        store.append_cooldown_record(connection,run,cooldown)
        connection.execute(
            """INSERT INTO adaptive_v13_run_checkpoints
            (run_id,event_id,sequence_number,trade_date,event_time,next_event_id,
             state_json,state_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                run,"checkpoint",10,checkpoint_day.isoformat(),"10:00","next",
                canonical_json(checkpoint_state),stable_hash(checkpoint_state),
                "2025-01-03T10:00:00+08:00",
            ),
        )
    store.update_run_status(run,"COMPLETED")
    recovered=resume_run(
        store,run,cache,dependencies=dependencies(),trading_calendar=dates,
    )
    assert ("600001.SH" in recovered["cooldowns"]) is retained


def test_cache_snapshot_round_trip_preserves_authoritative_metadata(tmp_path):
    cache=MarketCache(tmp_path/"cache.sqlite3")
    snapshot=real_snapshot(cache)
    loaded=cache.load_snapshot(snapshot.data_snapshot_id)
    assert loaded==snapshot
    cache.verify_snapshot(loaded)


def test_initial_state_cannot_bypass_atomic_account_snapshot(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3")
    run=create_run(store,config(tmp_path),bundle(),run_id="run")
    forged={**state(),"cash":"999999"}
    with pytest.raises(Phase5Error) as caught:
        execute_run(store,run,config(tmp_path),forged,dependencies=dependencies())
    assert caught.value.code=="STATE_VERSION_CONFLICT"


def test_retryable_sell_persists_pending_and_next_request_in_same_transaction(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3")
    run=create_run(store,config(tmp_path),bundle(with_position=True),run_id="run")
    attempt=pd.Timestamp("2025-01-02 10:00",tz="Asia/Shanghai")
    pending=PendingSellState(
        "600001.SH",PendingSellStatus.ACTIVE,"INITIAL_STOP",80,
        ExecutionType.HARD_EXIT,100,100,attempt,attempt,True,False,"",
    )
    request=FillRequest(
        ExecutionType.HARD_EXIT,"600001.SH",100,
        pd.Timestamp("2025-01-02 09:55",tz="Asia/Shanghai"),
        Decimal("0"),100,100,
    )
    frame=pd.DataFrame([{
        "symbol":"600001.SH","trade_date":DAY,"bar_start":attempt,
        "open":"10","high":"10","low":"10","close":"10","volume":"100",
        "trade_status":"normal","limit_status":"limit_down",
    }])
    rule=TradingRuleSnapshot(
        "SSE","main","stock",DAY,100,100,True,Decimal("0.01"),
    )
    fees=FeeRuleSnapshot(
        DAY,Decimal("0.0003"),Decimal("5"),Decimal("0.00001"),
        Decimal("0.00001"),Decimal("0.00002"),Decimal("0.00002"),
        Decimal("0.0005"),
    )
    deps=replace(
        dependencies(),minute_bars=lambda request,event:frame,
        trading_rule=lambda symbol,event:rule,fee_rule=lambda event:fees,
    )
    current=position()
    current=replace(current,sellable_qty=100,today_bought_qty=0,current_trade_date=DAY)
    runtime_state={
        "cash":Decimal("1000"),"positions":{"600001.SH":current},
        "pending_sells":{"600001.SH":pending},"fill_requests":(("request-1",request),),
    }
    event=ClockEvent("event",DAY,"10:00","BAR_OPEN",1,"10:00")
    with store.transaction() as connection:
        _apply_due_fills(connection,run,event,runtime_state,deps,(DAY,date(2025,1,3)),store)
    updated=runtime_state["pending_sells"]["600001.SH"]
    assert updated.status==PendingSellStatus.ACTIVE
    assert updated.retry_count==1
    assert len(runtime_state["fill_requests"])==1
    assert runtime_state["fill_requests"][0][0]!="request-1"
    assert len(store.latest_state_rows("adaptive_v13_pending_sell_versions",run))==1
    assert len(store.unfinished_fill_request_rows(run))==1


def test_session_close_persists_next_day_trailing_stop_state(tmp_path):
    store=RunStore(tmp_path/"runs.sqlite3")
    run=create_run(store,config(tmp_path),bundle(with_position=True),run_id="run")
    control=control_state()
    deps=dependencies(
        marks={"600001.SH":{"close":"12","atr20":"1","price_basis_id":"RAW_UNADJUSTED_V1"}}
    )
    result=execute_run(
        store,run,config(tmp_path),state(with_position=True),dependencies=deps,
        trading_calendar=(date(2025,1,1),DAY,date(2025,1,3)),
    )
    updated=result["exit_controls"]["600001.SH"]
    assert updated.trailing_stop==Decimal("10")
    assert updated.last_trailing_update_date==DAY
    rows=store.latest_state_rows("adaptive_v13_exit_control_state_versions",run)
    assert len(rows)==1
    assert DAY.isoformat() in rows[0]["state_json"]
