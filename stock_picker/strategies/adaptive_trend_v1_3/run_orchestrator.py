"""Transactional Phase 5 event orchestration over frozen Phase 1-4B engines."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
import json
import sqlite3
from typing import Any, Callable, Mapping
from uuid import uuid4

from .account_ledger import apply_fill_to_ledger
from .cooldown import create_cooldown_record
from .exit_control_state import update_trailing_stop
from .event_clock import build_event_clock, deterministic_id
from .phase3_models import FeeRuleSnapshot, FillRequest, FillResult, FillStatus, TradingRuleSnapshot
from .phase4_models import PositionState, TransitionStatus
from .position_state import apply_buy_fill, apply_sell_fill, unlock_position_state
from .fill_engine import execute_fill
from .pending_sell import apply_pending_fill_result
from .phase4b_models import PendingSellStatus
from .minute_contract import resolve_next_execution_bar
from .phase5_models import Phase5Error, RunConfig, RunStatus
from .run_store import RunStore, canonical_json, stable_hash
from .strategy_runtime import (
    run_1000_strategy, run_1430_strategy, run_hard_exit_strategy,
)
from .valuation import select_session_close_mark


@dataclass(frozen=True)
class RuntimeHooks:
    """Optional observability/fault-injection hooks; never replace core logic."""

    on_event: Callable[[str, Mapping[str, Any]], None] | None = None
    before_component: Callable[[str, Mapping[str, Any]], None] | None = None


@dataclass(frozen=True)
class RuntimeDataDependencies:
    """Inject raw data readers only; frozen strategy functions are not fields."""

    decision_1000_data: Callable[[Mapping[str,Any],object],Mapping[str,Any]]
    bar_close_data: Callable[[Mapping[str,Any],object],Mapping[str,Any]]
    decision_1430_data: Callable[[Mapping[str,Any],object],Mapping[str,Any]]
    session_close_data: Callable[[Mapping[str,Any],object],Mapping[str,Any]]
    minute_bars: Callable[[FillRequest,object],Any]
    trading_rule: Callable[[str,object],TradingRuleSnapshot]
    fee_rule: Callable[[object],FeeRuleSnapshot]


CoreStrategyDependencies = RuntimeDataDependencies


def run_fingerprint(config: RunConfig, snapshots: Mapping[str, Any]) -> str:
    return stable_hash({"strategy_version": config.strategy_version, "config": config, "snapshots": snapshots})


def create_run(
    store: RunStore, config: RunConfig, snapshots: Mapping[str, Any], *, run_id: str | None = None
) -> str:
    if not all(key in snapshots for key in ("account","universe","data")):
        raise Phase5Error("INVALID_CONFIG","snapshot_bundle_required")
    expected_ids = (
        (config.account_snapshot_id, getattr(snapshots["account"], "account_snapshot_id", None)),
        (config.universe_snapshot_id, getattr(snapshots["universe"], "universe_snapshot_id", None)),
        (config.data_snapshot_id, getattr(snapshots["data"], "data_snapshot_id", None)),
    )
    if any(expected != actual for expected, actual in expected_ids):
        raise Phase5Error("INVALID_CONFIG", "snapshot_id_mismatch")
    identifier = run_id or str(uuid4())
    fingerprint = run_fingerprint(config, snapshots)
    store.create_run_bundle(
        identifier,fingerprint,config,account_snapshot=snapshots["account"],
        universe_snapshot=snapshots["universe"],data_snapshot=snapshots["data"],
        created_at=config.created_at,
    )
    return identifier


def execute_run(
    store: RunStore, run_id: str, config: RunConfig, initial_state: Mapping[str, Any],
    *, hooks: RuntimeHooks = RuntimeHooks(), trading_calendar=(),
    dependencies: RuntimeDataDependencies | None = None, start_after_sequence: int = -1,
) -> dict[str, Any]:
    if config.network_policy.value != "FORBID":
        raise Phase5Error("INVALID_CONFIG", "run_network_policy_must_forbid")
    if dependencies is None or any(
        not callable(getattr(dependencies,name,None)) for name in RuntimeDataDependencies.__dataclass_fields__
    ):
        raise Phase5Error("INVALID_CONFIG","core_strategy_dependencies_missing")
    if start_after_sequence < 0:
        checkpoint = store.last_checkpoint(run_id)
        if checkpoint is None or int(checkpoint["sequence_number"]) != -1:
            raise Phase5Error("STATE_VERSION_CONFLICT", "initial_checkpoint_missing")
        if stable_hash(initial_state) != checkpoint["state_hash"]:
            raise Phase5Error("STATE_VERSION_CONFLICT", "initial_state_differs_from_snapshot")
    state = _copy_state(initial_state)
    snapshot_bundle = store.load_snapshot_bundle(run_id)
    snapshot_content = json.loads(snapshot_bundle["data"]["content_json"])
    allowed_partition_ids = tuple(snapshot_content.get("partition_ids", ()))
    store.update_run_status(run_id, RunStatus.RUNNING.value)
    events = build_event_clock(run_id, config.date_range.trading_dates)
    try:
        for event in events:
            if event.sequence_number <= start_after_sequence:
                continue
            result = store.process_event(
                run_id, event,
                lambda connection, current: _handle_event(
                    connection, run_id, current, state, hooks, tuple(trading_calendar or config.date_range.trading_dates)
                    ,dependencies,store,allowed_partition_ids,config.price_basis_id
                ),
            )
            if result is not None:
                state = _copy_state(result.get("state", state))
        positions = state.get("positions", {})
        open_positions = any(getattr(value, "total_qty", 0) > 0 for value in positions.values())
        degraded = bool(state.get("degraded_reasons"))
        final = RunStatus.DEGRADED if degraded else (
            RunStatus.COMPLETED_WITH_OPEN_POSITIONS if open_positions else RunStatus.COMPLETED
        )
        store.update_run_status(run_id, final.value)
        return state
    except Exception as exc:
        reason = _error_code(exc)
        store.update_run_status(run_id, RunStatus.FAILED.value, reason=reason)
        store.record_run_failure(run_id, reason, str(exc))
        raise


def _handle_event(
    connection: sqlite3.Connection, run_id: str, event, state: dict[str, Any],
    hooks: RuntimeHooks, trading_calendar: tuple[date, ...],
    dependencies: RuntimeDataDependencies, store: RunStore,
    allowed_partition_ids: tuple[str,...], expected_price_basis_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    updated = _copy_state(state)
    if event.event_type == "SESSION_START":
        positions = dict(updated.get("positions", {}))
        for symbol in sorted(positions):
            transition = unlock_position_state(positions[symbol], event.trade_date, trading_calendar)
            if transition.status != TransitionStatus.APPLIED:
                raise Phase5Error("STATE_VERSION_CONFLICT", transition.failure_reason)
            positions[symbol] = transition.new_state
            _persist_position_version(
                connection,run_id,symbol,transition.new_state,
                position_event_id=deterministic_id("position",run_id,event.event_id,symbol),
                fill_id=None,
            )
        updated["positions"] = positions
        for pending in updated.get("pending_sells", {}).values():
            store.append_pending_sell_version(connection, run_id, pending)
        for control in updated.get("exit_controls", {}).values():
            store.append_exit_control_state_version(connection, run_id, control)
        for cooldown in updated.get("cooldowns", {}).values():
            store.append_cooldown_record(connection, run_id, cooldown)
    elif event.event_type == "BAR_OPEN":
        _apply_due_fills(connection,run_id,event,updated,dependencies,trading_calendar,store)
    elif event.event_type == "DECISION_1000":
        payload = _run_1000_chain(updated,event,dependencies,hooks)
        _merge_decision(connection,run_id,updated,payload,event,"ENTRY",store)
    elif event.event_type == "BAR_CLOSE":
        payload = _run_hard_exit(updated,event,dependencies,hooks,trading_calendar)
        _merge_decision(connection,run_id,updated,payload,event,"HARD_EXIT",store)
    elif event.event_type == "DECISION_1430":
        payload = run_1430_strategy(
            dependencies.decision_1430_data(updated,event),updated,event,
            trading_calendar,lambda name,value:_notify(hooks,name,value),
        )
        _merge_decision(connection,run_id,updated,payload,event,"EXIT_1430",store)
    elif event.event_type == "SESSION_CLOSE":
        _notify(hooks,"valuation",updated)
        supplied = dict(dependencies.session_close_data(updated,event))
        payload = _mandatory_valuation(
            updated,supplied,event,allowed_partition_ids,expected_price_basis_id,
        )
        updated.update(payload.get("state_updates", {}))
        controls = dict(updated.get("exit_controls",{}))
        for symbol,control in sorted(controls.items()):
            mark = payload["position_marks"].get(symbol, {})
            transition = update_trailing_stop(
                control, trade_date=event.trade_date,
                daily_close=mark.get("close", mark.get("mark_price", "")),
                atr20=mark.get("atr20", ""),
                price_basis_id=mark.get("price_basis_id", control.price_basis_id),
            )
            new_control = transition.new_state
            controls[symbol] = new_control
            store.append_exit_control_state_version(connection,run_id,new_control)
        updated["exit_controls"] = controls
        if payload["degraded_reasons"]:
            updated["degraded_reasons"] = tuple(sorted(set(
                (*updated.get("degraded_reasons", ()), *payload["degraded_reasons"])
            )))
        _persist_daily_snapshot(connection,run_id,event,payload)
    store.append_audit_event(
        connection,run_id=run_id,event_id=event.event_id,event_type=event.event_type,
        component="run_orchestrator",action="core_event",status="COMPLETED",
        input_value={"sequence":event.sequence_number},output_value=updated,
        reason_code="",message="core event completed",
    )
    if hooks.on_event:
        hooks.on_event(event.event_type,updated)
    result = {"event_id": event.event_id, "event_type": event.event_type, "state": updated}
    return result, updated


def _apply_due_fills(connection, run_id, event, state, dependencies, trading_calendar, store) -> None:
    requests = list(state.get("fill_requests", ()))
    remaining = []
    positions = dict(state.get("positions", {}))
    cash = Decimal(str(state.get("cash", "0")))
    for request_id, request in requests:
        due = _request_due(request, event, trading_calendar)
        if not due:
            remaining.append((request_id, request)); continue
        result = execute_fill(
            request,dependencies.minute_bars(request,event),dependencies.trading_rule(request.symbol,event),
            dependencies.fee_rule(event),trading_calendar=trading_calendar,
        )
        fill_id = deterministic_id("fill", run_id, request_id)
        try:
            connection.execute(
                "INSERT INTO adaptive_v13_fills(fill_id,run_id,fill_request_id,status,payload_json) VALUES(?,?,?,?,?)",
                (fill_id,run_id,request_id,result.status.value,canonical_json(result)),
            )
        except sqlite3.IntegrityError as exc:
            raise Phase5Error("DUPLICATE_FILL") from exc
        pending_by_symbol = dict(state.get("pending_sells",{}))
        pending = pending_by_symbol.get(result.symbol)
        if result.status != FillStatus.FILLED:
            if result.side.value == "SELL":
                if pending is None:
                    raise Phase5Error("STATE_VERSION_CONFLICT","retryable_sell_without_pending")
                update = apply_pending_fill_result(
                    pending,result,position_qty_after_fill=positions[result.symbol].total_qty,
                    attempt_at=result.execution_bar_start,trading_calendar=trading_calendar,
                )
                if update.new_state is None:
                    raise Phase5Error("STATE_VERSION_CONFLICT","pending_transition_missing")
                pending_by_symbol[result.symbol] = update.new_state
                store.append_pending_sell_version(connection,run_id,update.new_state)
                if update.new_state.status == PendingSellStatus.ACTIVE and result.retryable:
                    if request.execution_type.value == "HARD_EXIT":
                        retry_request = replace(
                            request,signal_time=result.execution_bar_start,
                            position_qty=positions[result.symbol].total_qty,
                            sellable_qty=positions[result.symbol].sellable_qty,
                        )
                        retry_id = deterministic_id(
                            "fill_request",run_id,result.symbol,update.new_state.retry_count,
                            update.new_state.next_attempt_at,
                        )
                        connection.execute(
                            """INSERT INTO adaptive_v13_fill_requests
                            (fill_request_id,run_id,event_id,symbol,execution_type,payload_json)
                            VALUES(?,?,?,?,?,?)""",
                            (retry_id,run_id,event.event_id,result.symbol,retry_request.execution_type.value,canonical_json(retry_request)),
                        )
                        remaining.append((retry_id,retry_request))
                state["pending_sells"] = pending_by_symbol
                store.append_audit_event(
                    connection,run_id=run_id,event_id=event.event_id,event_type="BAR_OPEN",
                    component="pending_sell",action="fill_failure",status=update.new_state.status.value,
                    input_value=result,output_value=update.new_state,reason_code=result.failure_reason,
                    message="sell failure applied to pending",symbol=result.symbol,
                )
            continue
        current = positions.get(result.symbol)
        if current is None:
            raise Phase5Error("STATE_VERSION_CONFLICT", "position_state_missing")
        if result.side.value == "BUY":
            transition = apply_buy_fill(current,result,entry_atr=state.get("entry_atr",{}).get(result.symbol),trading_calendar=trading_calendar)
        else:
            transition = apply_sell_fill(current,result)
        if transition.status != TransitionStatus.APPLIED:
            raise Phase5Error("STATE_VERSION_CONFLICT", transition.failure_reason)
        ledger = apply_fill_to_ledger(connection,run_id=run_id,fill_id=fill_id,fill=result,current_cash=cash)
        cash = ledger.cash_after
        positions[result.symbol] = transition.new_state
        _persist_position_version(
            connection,run_id,result.symbol,transition.new_state,
            position_event_id=deterministic_id("position",run_id,fill_id),fill_id=fill_id,
        )
        if result.side.value == "SELL" and pending is not None:
            update = apply_pending_fill_result(
                pending,result,position_qty_after_fill=transition.new_state.total_qty,
                attempt_at=result.execution_bar_start,trading_calendar=trading_calendar,
            )
            if update.new_state is not None:
                pending_by_symbol[result.symbol] = update.new_state
                state["pending_sells"] = pending_by_symbol
                store.append_pending_sell_version(connection,run_id,update.new_state)
            if transition.new_state.total_qty == 0:
                cooldown = create_cooldown_record(
                    symbol=result.symbol,exit_reason=pending.reason,
                    exit_trade_date=result.execution_trade_date,trading_calendar=trading_calendar,
                    full_exit=True,
                )
                if cooldown is not None:
                    cooldowns = dict(state.get("cooldowns",{})); cooldowns[result.symbol] = cooldown
                    state["cooldowns"] = cooldowns
                    store.append_cooldown_record(connection,run_id,cooldown)
        store.append_audit_event(
            connection,run_id=run_id,event_id=event.event_id,event_type="BAR_OPEN",
            component="fill_engine",action="execute_fill",status=result.status.value,
            input_value=request,output_value=result,reason_code=result.failure_reason,
            message="fill processed",symbol=result.symbol,
        )
    state["fill_requests"] = tuple(remaining)
    state["positions"] = positions
    state["cash"] = cash


def _request_due(request: FillRequest, event, trading_calendar=()) -> bool:
    target = request.signal_time
    if request.execution_type.value == "ENTRY_BUY":
        return event.event_type == "BAR_OPEN" and event.event_time == "10:05" and str(target)[:10] == event.trade_date.isoformat()
    if request.execution_type.value in {"SOFT_EXIT","REPLACEMENT_EXIT","ORDINARY_REDUCTION"}:
        return event.event_type == "BAR_OPEN" and event.event_time == "14:35" and str(target)[:10] == event.trade_date.isoformat()
    resolution = resolve_next_execution_bar(request.signal_time, trading_calendar)
    target_bar = resolution.execution_bar_start
    return bool(
        target_bar is not None
        and event.event_type == "BAR_OPEN"
        and target_bar.date() == event.trade_date
        and target_bar.strftime("%H:%M") == event.event_time
    )


def _merge_decision(
    connection, run_id: str, state: dict[str, Any], payload: Mapping[str, Any],
    event, decision_type: str, store: RunStore,
) -> None:
    requests = list(state.get("fill_requests", ()))
    for request in payload.get("fill_requests", ()):
        request_id = deterministic_id("fill_request", event.event_id, request.symbol, request.execution_type.value)
        if all(existing_id != request_id for existing_id, _ in requests):
            requests.append((request_id,request))
        connection.execute(
            """INSERT OR IGNORE INTO adaptive_v13_fill_requests
            (fill_request_id,run_id,event_id,symbol,execution_type,payload_json) VALUES(?,?,?,?,?,?)""",
            (request_id,run_id,event.event_id,request.symbol,request.execution_type.value,canonical_json(request)),
        )
        store.append_audit_event(
            connection,run_id=run_id,event_id=event.event_id,event_type=event.event_type,
            component="fill_request",action="create",status="PENDING",
            input_value={},output_value=request,reason_code="",message="fill request persisted",
            symbol=request.symbol,
        )
    for index, decision in enumerate(payload.get("decisions", ())):
        item = _as_mapping(decision)
        symbol = str(item.get("symbol",""))
        decision_id = deterministic_id("decision",event.event_id,decision_type,symbol,index)
        connection.execute(
            """INSERT OR IGNORE INTO adaptive_v13_decisions
            (decision_id,run_id,event_id,symbol,decision_type,status,reasons_json,payload_json)
            VALUES(?,?,?,?,?,?,?,?)""",
            (decision_id,run_id,event.event_id,symbol,decision_type,str(item.get("status","")),
             canonical_json(item.get("reasons",())),canonical_json(decision)),
        )
        store.append_audit_event(
            connection,run_id=run_id,event_id=event.event_id,event_type=event.event_type,
            component="decision",action=decision_type,status=str(item.get("status","")),
            input_value={},output_value=decision,
            reason_code="|".join(map(str,item.get("reasons",()))),
            message="strategy decision persisted",symbol=symbol,
        )
    for intent in payload.get("exit_intents", ()):
        item = _as_mapping(intent); symbol = str(item.get("symbol",""))
        intent_id = deterministic_id("exit_intent",event.event_id,symbol)
        connection.execute(
            """INSERT OR IGNORE INTO adaptive_v13_exit_intents
            (exit_intent_id,run_id,event_id,symbol,payload_json) VALUES(?,?,?,?,?)""",
            (intent_id,run_id,event.event_id,symbol,canonical_json(intent)),
        )
    state["fill_requests"] = tuple(sorted(requests,key=lambda item:item[0]))
    pending_sells = dict(state.get("pending_sells",{}))
    for pending in payload.get("pending_states",()):
        pending_sells[pending.symbol] = pending
        store.append_pending_sell_version(connection,run_id,pending)
        store.append_audit_event(
            connection,run_id=run_id,event_id=event.event_id,event_type=event.event_type,
            component="pending_sell",action="append_version",status=pending.status.value,
            input_value={},output_value=pending,reason_code="",
            message="pending sell version persisted",symbol=pending.symbol,
        )
    state["pending_sells"] = pending_sells
    controls = dict(state.get("exit_controls",{}))
    for control in payload.get("exit_controls",()):
        controls[control.symbol] = control
        store.append_exit_control_state_version(connection,run_id,control)
        store.append_audit_event(
            connection,run_id=run_id,event_id=event.event_id,event_type=event.event_type,
            component="exit_control",action="append_version",status="ACTIVE",
            input_value={},output_value=control,reason_code="",
            message="exit control version persisted",symbol=control.symbol,
        )
    state["exit_controls"] = controls
    cooldowns = dict(state.get("cooldowns",{}))
    for cooldown in payload.get("cooldowns",()):
        cooldowns[cooldown.symbol] = cooldown
        store.append_cooldown_record(connection,run_id,cooldown)
        store.append_audit_event(
            connection,run_id=run_id,event_id=event.event_id,event_type=event.event_type,
            component="cooldown",action="append",status=cooldown.status.value,
            input_value={},output_value=cooldown,reason_code="",
            message="cooldown persisted",symbol=cooldown.symbol,
        )
    state["cooldowns"] = cooldowns
    state.update(dict(payload.get("state_updates", {})))


def _run_1000_chain(state,event,dependencies,hooks) -> Mapping[str,Any]:
    return run_1000_strategy(
        dependencies.decision_1000_data(state,event),state,event,
        lambda name,value:_notify(hooks,name,value),
    )


def _run_hard_exit(state,event,dependencies,hooks,trading_calendar) -> Mapping[str,Any]:
    raw=dict(dependencies.bar_close_data(state,event))
    raw["_trading_calendar"]=trading_calendar
    return run_hard_exit_strategy(
        raw,state,event,lambda name,value:_notify(hooks,name,value)
    )


def _mandatory_valuation(
    state: Mapping[str, Any], supplied: Mapping[str, Any], event,
    allowed_partition_ids: tuple[str,...], expected_price_basis_id: str,
) -> dict[str, Any]:
    """Select marks from raw bars internally, then compute immutable account truth."""
    cash = Decimal(str(state.get("cash", "0")))
    positions = dict(state.get("positions", {}))
    previous = dict(state.get("last_position_marks", {}))
    market_value = Decimal("0")
    unrealized = Decimal("0")
    realized = Decimal("0")
    degraded: list[str] = []
    normalized_marks: dict[str, Mapping[str, Any]] = {}
    for symbol, position in sorted(positions.items()):
        if position.total_qty <= 0:
            continue
        raw = dict(supplied.get("holdings",{}).get(symbol,{}))
        selection = select_session_close_mark(
            symbol=symbol,trade_date=event.trade_date,
            bars=raw.get("bars"),previous_valid_mark=previous.get(symbol),
            session_status=raw.get("session_status",""),
            expected_price_basis_id=expected_price_basis_id,
            allowed_partition_ids=allowed_partition_ids,
        )
        if selection.status != "VALID":
            degraded.append(f"MISSING_MARK_PRICE:{symbol}")
            price=Decimal("0")
        else:
            price=selection.mark_price
        mark={
            "symbol":symbol,"mark_price":price,"close":price,"atr20":raw.get("atr20",""),
            "price_basis_id":expected_price_basis_id,
            "trade_date":event.trade_date.isoformat(),
            "source_partition_id":selection.source_partition_id,
            "mark_bar_start":selection.mark_bar_start,
        }
        normalized_marks[symbol] = mark
        value = price * position.total_qty
        market_value += value
        unrealized += value - position.cost_basis
        realized += position.realized_pnl
    equity = cash + market_value
    exposure = Decimal("0") if equity <= 0 else market_value / equity
    return {
        "cash": cash, "equity": equity, "exposure": exposure,
        "stress": Decimal(str(state.get("portfolio_stress", "0"))),
        "realized_pnl": realized, "unrealized_pnl": unrealized,
        "position_market_value": market_value,
        "holding_count": sum(1 for item in positions.values() if item.total_qty > 0),
        "position_marks": normalized_marks,
        "degraded_reasons": tuple(degraded),
        "state_updates": {
            "last_position_marks": normalized_marks,
            "last_equity": equity,
        },
    }


def _notify(hooks: RuntimeHooks, component: str, state: Mapping[str,Any]) -> None:
    if hooks.before_component:
        hooks.before_component(component,state)


def _persist_position_version(
    connection, run_id: str, symbol: str, position: PositionState,
    *, position_event_id: str, fill_id: str | None,
) -> None:
    version = connection.execute(
        "SELECT COUNT(*) FROM adaptive_v13_position_state_versions WHERE run_id=? AND symbol=?",
        (run_id,symbol),
    ).fetchone()[0] + 1
    connection.execute(
        """INSERT INTO adaptive_v13_position_state_versions
        (position_event_id,run_id,fill_id,symbol,version,state_json) VALUES(?,?,?,?,?,?)""",
        (position_event_id,run_id,fill_id,symbol,version,canonical_json(position)),
    )


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value,Mapping):
        return value
    from dataclasses import asdict, is_dataclass
    return asdict(value) if is_dataclass(value) else {"value":repr(value)}


def _persist_daily_snapshot(connection, run_id, event, payload) -> None:
    fields = {name: str(payload.get(name,"0")) for name in ("cash","equity","exposure","stress","realized_pnl","unrealized_pnl")}
    connection.execute(
        """INSERT INTO adaptive_v13_daily_account_snapshots
        (daily_snapshot_id,run_id,trade_date,cash,equity,exposure,stress,realized_pnl,unrealized_pnl,payload_json)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (deterministic_id("daily",run_id,event.trade_date),run_id,event.trade_date.isoformat(),
         fields["cash"],fields["equity"],fields["exposure"],fields["stress"],
         fields["realized_pnl"],fields["unrealized_pnl"],canonical_json(payload)),
    )


def _copy_state(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item.copy() if isinstance(item, dict) else tuple(item) if isinstance(item,list) else item for key,item in value.items()}


def _error_code(exc: Exception) -> str:
    return exc.code if isinstance(exc,Phase5Error) else "UNEXPECTED_ENGINE_ERROR"
