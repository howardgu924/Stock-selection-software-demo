"""Database-authoritative validation and idempotent Phase 5 recovery."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any, Mapping

import pandas as pd

from .market_cache import MarketCache
from .phase3_models import ExecutionType, FillRequest
from .phase4_models import PositionLot, PositionState, PositionStatus
from .phase4b_models import (
    AttemptIdentity, CooldownRecord, CooldownStatus, ExitControlState,
    PendingSellState, PendingSellStatus,
)
from .phase5_models import (
    DataSnapshot, NetworkAccessPolicy, Phase5Error, ResolvedDateRange, RunConfig,
    RunMode,
)
from .run_orchestrator import (
    CoreStrategyDependencies, RuntimeHooks, execute_run, run_fingerprint,
)
from .run_store import RunStore, stable_hash


def resume_run(
    store: RunStore, run_id: str, cache: MarketCache, *,
    dependencies: CoreStrategyDependencies,
    config_assertion: RunConfig | None = None,
    hooks: RuntimeHooks = RuntimeHooks(), trading_calendar=(),
) -> dict[str, Any]:
    """Resume only from persisted snapshots, cache partitions and versioned state."""
    bundle = store.load_snapshot_bundle(run_id)
    run = bundle["run"]
    config = _hydrate_config(bundle["config"])
    account = json.loads(bundle["account"]["content_json"])
    universe = json.loads(bundle["universe"]["content_json"])
    data_raw = json.loads(bundle["data"]["content_json"])
    snapshot = _hydrate_data_snapshot(data_raw)

    if config_assertion is not None and stable_hash(config_assertion) != stable_hash(config):
        raise Phase5Error("RUN_FINGERPRINT_MISMATCH", "config_assertion_changed")
    for key, row, value in (
        ("account", bundle["account"], account),
        ("universe", bundle["universe"], universe),
        ("data", bundle["data"], data_raw),
    ):
        if row["content_hash"] != stable_hash(value):
            raise Phase5Error("RUN_FINGERPRINT_MISMATCH", f"{key}_snapshot_hash_mismatch")
    if {item["partition_id"] for item in bundle["partition_links"]} != set(snapshot.partition_ids):
        raise Phase5Error("DATA_NOT_READY", "snapshot_link_missing")
    cache.verify_snapshot(snapshot)
    database_snapshots = {"account": account, "universe": universe, "data": data_raw}
    if run["run_fingerprint"] != run_fingerprint(config, database_snapshots):
        raise Phase5Error("RUN_FINGERPRINT_MISMATCH")

    checkpoint = store.last_checkpoint(run_id)
    if checkpoint is None:
        raise Phase5Error("STATE_VERSION_CONFLICT", "checkpoint_missing")
    raw_checkpoint = json.loads(checkpoint["state_json"])
    if stable_hash(raw_checkpoint) != checkpoint["state_hash"]:
        raise Phase5Error("RUN_FINGERPRINT_MISMATCH", "checkpoint_corrupt")
    state = _hydrate_state(raw_checkpoint)
    state["cash"] = store.latest_cash(run_id, account.get("cash", "0"))
    position_rows = store.latest_position_rows(run_id)
    if position_rows:
        state["positions"] = {
            row["symbol"]: _hydrate_position(json.loads(row["state_json"]))
            for row in position_rows
        }
    state["pending_sells"] = {
        row["symbol"]: _hydrate_pending(json.loads(row["state_json"]))
        for row in store.latest_state_rows("adaptive_v13_pending_sell_versions", run_id)
    }
    state["exit_controls"] = {
        row["symbol"]: _hydrate_control(json.loads(row["state_json"]))
        for row in store.latest_state_rows("adaptive_v13_exit_control_state_versions", run_id)
    }
    as_of = _recovery_as_of_trade_date(
        checkpoint,config,trading_calendar
    ).isoformat()
    state["cooldowns"] = {
        row["symbol"]: _hydrate_cooldown(json.loads(row["state_json"]))
        for row in store.load_active_cooldowns(run_id, as_of)
    }
    state["fill_requests"] = tuple(
        (row["fill_request_id"], _hydrate_request(json.loads(row["payload_json"])))
        for row in store.unfinished_fill_request_rows(run_id)
    )
    sequence = int(checkpoint["sequence_number"])

    with store.transaction() as connection:
        store.append_audit_event(
            connection, run_id=run_id, event_id="__RECOVERY__",
            event_type="RECOVERY", component="run_recovery", action="resume",
            status="VALIDATED", input_value={"sequence": sequence},
            output_value={
                "pending": len(state["pending_sells"]),
                "controls": len(state["exit_controls"]),
                "cooldowns": len(state["cooldowns"]),
                "requests": len(state["fill_requests"]),
            },
            reason_code="", message="database state and cache snapshot validated",
            source_ids=(snapshot.data_snapshot_id,),
        )
    if run["status"] in {"COMPLETED", "COMPLETED_WITH_OPEN_POSITIONS", "DEGRADED"}:
        return state
    return execute_run(
        store, run_id, config, state, hooks=hooks, trading_calendar=trading_calendar,
        dependencies=dependencies, start_after_sequence=sequence,
    )


def _hydrate_config(raw: Mapping[str, Any]) -> RunConfig:
    range_raw = raw["date_range"]
    resolved = ResolvedDateRange(
        requested_start_date=date.fromisoformat(range_raw["requested_start_date"]),
        requested_end_date=date.fromisoformat(range_raw["requested_end_date"]),
        actual_start_date=date.fromisoformat(range_raw["actual_start_date"]),
        actual_end_date=date.fromisoformat(range_raw["actual_end_date"]),
        warmup_start_date=date.fromisoformat(range_raw["warmup_start_date"]),
        trading_dates=tuple(date.fromisoformat(item) for item in range_raw["trading_dates"]),
        warmup_dates=tuple(date.fromisoformat(item) for item in range_raw["warmup_dates"]),
        warmup_trading_days=int(range_raw.get("warmup_trading_days", raw["warmup_trading_days"])),
    )
    return RunConfig(
        run_mode=RunMode(raw["run_mode"]), strategy_version=raw["strategy_version"],
        account_snapshot_id=raw["account_snapshot_id"],
        universe_snapshot_id=raw["universe_snapshot_id"],
        data_snapshot_id=raw["data_snapshot_id"], date_range=resolved,
        warmup_trading_days=int(raw["warmup_trading_days"]),
        price_basis_id=raw["price_basis_id"],
        network_policy=NetworkAccessPolicy(raw["network_policy"]),
        report_directory=raw["report_directory"],
        initial_position_policy=raw["initial_position_policy"],
        created_at=raw["created_at"], config_hash=raw["config_hash"],
        git_commit_sha=raw.get("git_commit_sha", ""),
        schema_version=int(raw.get("schema_version", 3)),
    )


def _recovery_as_of_trade_date(checkpoint, config, trading_calendar) -> date:
    value=str(checkpoint.get("trade_date",""))
    if value:
        return date.fromisoformat(value)
    first=config.date_range.trading_dates[0]
    calendar=sorted({
        item if isinstance(item,date) else date.fromisoformat(str(item)[:10])
        for item in trading_calendar
    })
    prior=[item for item in calendar if item < first]
    return prior[-1] if prior else first


def _hydrate_data_snapshot(raw: Mapping[str, Any]) -> DataSnapshot:
    return DataSnapshot(
        data_snapshot_id=raw["data_snapshot_id"],
        partition_ids=tuple(raw["partition_ids"]), price_basis_id=raw["price_basis_id"],
        created_at=raw["created_at"], snapshot_hash=raw["snapshot_hash"],
        partition_hashes=tuple(tuple(item) for item in raw.get("partition_hashes", ())),
        required_trade_dates=tuple(raw.get("required_trade_dates", ())),
        rule_snapshot_ids=tuple(raw.get("rule_snapshot_ids", ())),
        fee_snapshot_ids=tuple(raw.get("fee_snapshot_ids", ())),
        readiness_status=raw.get("readiness_status", "READY"),
        partition_metadata=tuple(tuple(item) for item in raw.get("partition_metadata", ())),
        preparation_id=raw.get("preparation_id",""),
    )


def _hydrate_state(raw: Mapping[str, Any]) -> dict[str, Any]:
    state = dict(raw)
    state["positions"] = {
        symbol: item if isinstance(item, PositionState) else _hydrate_position(item)
        for symbol, item in dict(state.get("positions", {})).items()
    }
    state["fill_requests"] = tuple(
        (request_id, item if isinstance(item, FillRequest) else _hydrate_request(item))
        for request_id, item in state.get("fill_requests", ())
    )
    state["cash"] = Decimal(str(state.get("cash", "0")))
    if "entry_atr" in state:
        state["entry_atr"] = {
            key: Decimal(str(value)) for key, value in state["entry_atr"].items()
        }
    return state


def _hydrate_position(item: Mapping[str, Any]) -> PositionState:
    lots = tuple(
        PositionLot(
            buy_trade_date=date.fromisoformat(lot["buy_trade_date"]),
            qty=int(lot["qty"]), remaining_qty=int(lot["remaining_qty"]),
            execution_price=Decimal(lot["execution_price"]),
            allocated_buy_fees=Decimal(lot["allocated_buy_fees"]),
            unlock_trade_date=date.fromisoformat(lot["unlock_trade_date"]),
            sequence=int(lot["sequence"]), remaining_cost=Decimal(lot["remaining_cost"]),
        )
        for lot in item.get("lots", ())
    )
    return PositionState(
        symbol=item["symbol"], total_qty=int(item["total_qty"]),
        sellable_qty=int(item["sellable_qty"]),
        today_bought_qty=int(item["today_bought_qty"]),
        average_cost=Decimal(item["average_cost"]), cost_basis=Decimal(item["cost_basis"]),
        entry_trade_date=_optional_date(item.get("entry_trade_date")),
        entry_price=_optional_decimal(item.get("entry_price")),
        entry_atr=_optional_decimal(item.get("entry_atr")),
        highest_close=_optional_decimal(item.get("highest_close")),
        realized_pnl=Decimal(item["realized_pnl"]), lots=lots,
        status=PositionStatus(item["status"]),
        current_trade_date=_optional_date(item.get("current_trade_date")),
    )


def _hydrate_request(item: Mapping[str, Any]) -> FillRequest:
    return FillRequest(
        execution_type=ExecutionType(item["execution_type"]), symbol=item["symbol"],
        requested_qty=int(item["requested_qty"]), signal_time=item["signal_time"],
        cash_available=Decimal(item["cash_available"]),
        position_qty=int(item["position_qty"]), sellable_qty=int(item["sellable_qty"]),
    )


def _hydrate_attempt(item: Mapping[str, Any] | None) -> AttemptIdentity | None:
    if not item:
        return None
    return AttemptIdentity(
        normalized_symbol=item["normalized_symbol"],
        execution_type=ExecutionType(item["execution_type"]),
        attempt_trade_date=date.fromisoformat(item["attempt_trade_date"]),
        attempt_bar_start=pd.Timestamp(item["attempt_bar_start"]),
    )


def _hydrate_pending(item: Mapping[str, Any]) -> PendingSellState:
    return PendingSellState(
        symbol=item["symbol"], status=PendingSellStatus(item["status"]),
        reason=item["reason"], priority=int(item["priority"]),
        execution_type=ExecutionType(item["execution_type"]),
        target_qty=int(item["target_qty"]), remaining_qty=int(item["remaining_qty"]),
        created_at=pd.Timestamp(item["created_at"]), next_attempt_at=pd.Timestamp(item["next_attempt_at"]),
        sticky=bool(item["sticky"]), requires_revalidation=bool(item["requires_revalidation"]),
        episode_id=item["episode_id"], retry_count=int(item.get("retry_count", 0)),
        last_failure=item.get("last_failure", ""),
        last_attempt_at=_optional_timestamp(item.get("last_attempt_at")),
        completed_at=_optional_timestamp(item.get("completed_at")),
        cancelled_reason=item.get("cancelled_reason", ""),
        last_processed_attempt=_hydrate_attempt(item.get("last_processed_attempt")),
    )


def _hydrate_control(item: Mapping[str, Any]) -> ExitControlState:
    return ExitControlState(
        symbol=item["symbol"], entry_trade_date=date.fromisoformat(item["entry_trade_date"]),
        initial_stop=Decimal(item["initial_stop"]), trailing_stop=Decimal(item["trailing_stop"]),
        highest_close=Decimal(item["highest_close"]), price_basis_id=item["price_basis_id"],
        weak_score_streak=int(item.get("weak_score_streak", 0)),
        ma20_episode_id=item.get("ma20_episode_id", ""),
        ma20_recovery_count=int(item.get("ma20_recovery_count", 0)),
        acted_episode_ids=tuple(item.get("acted_episode_ids", ())),
        active_pending_sell=_hydrate_pending(item["active_pending_sell"]) if item.get("active_pending_sell") else None,
        last_1430_evaluation_date=_optional_date(item.get("last_1430_evaluation_date")),
        last_trailing_update_date=_optional_date(item.get("last_trailing_update_date")),
        last_full_exit_reason=item.get("last_full_exit_reason", ""),
        last_full_exit_date=_optional_date(item.get("last_full_exit_date")),
    )


def _hydrate_cooldown(item: Mapping[str, Any]) -> CooldownRecord:
    return CooldownRecord(
        symbol=item["symbol"], exit_reason=item["exit_reason"],
        exit_trade_date=date.fromisoformat(item["exit_trade_date"]),
        blocked_trade_dates=tuple(date.fromisoformat(value) for value in item["blocked_trade_dates"]),
        reentry_allowed_date=date.fromisoformat(item["reentry_allowed_date"]),
        status=CooldownStatus(item["status"]),
    )


def _optional_date(value: Any) -> date | None:
    return None if value in (None, "") else date.fromisoformat(str(value))


def _optional_decimal(value: Any) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


def _optional_timestamp(value: Any) -> pd.Timestamp | None:
    return None if value in (None, "") else pd.Timestamp(value)
