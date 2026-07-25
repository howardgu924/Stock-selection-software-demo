"""Single service facade for Phase 5 and future Phase 6 UI integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import subprocess
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd

from .account_snapshot_service import create_account_snapshot, validate_profile_paths
from .date_range import resolve_date_range
from .market_cache import (
    MarketCache, RAW_PRICE_BASIS, fetch_complete_partition, validate_coverage,
)
from .phase5_models import (
    AccountProfile, DataReadinessReport, DateRangeSpec, NetworkAccessPolicy, DataSnapshot,
    PartitionStatus, Phase5Error, RunConfig, RunMode, ServiceResult, UniverseSpec,
    UniverseSnapshot,
)
from .run_orchestrator import (
    CoreStrategyDependencies, RuntimeHooks, create_run as create_runtime_run,
    execute_run as execute_runtime_run,
)
from .run_recovery import resume_run as resume_runtime_run
from .run_reporting import generate_run_report as build_report
from .run_store import RunStore, stable_hash
from .universe_resolver import resolve_universe


@dataclass(frozen=True)
class PartitionRequest:
    dataset_type: str
    logical_key: str
    providers: tuple[tuple[str, str, Callable[[], pd.DataFrame]], ...]
    suspended: bool = False
    requested_trade_dates: tuple[object, ...] = ()
    normalized_symbol: str = ""
    frequency: str = ""


class Phase5Service:
    def __init__(
        self, *, cache: MarketCache, run_store: RunStore,
        account_profiles: Mapping[str, AccountProfile],
        trading_calendar: Iterable[object],
        watchlist_loader: Callable[[str], Iterable[str] | None] | None = None,
        market_scope_loader: Callable[[str], Iterable[str] | None] | None = None,
        partition_planner: Callable[[Any, Any, AccountProfile, RunMode], Sequence[PartitionRequest]] | None = None,
    ) -> None:
        self.cache = cache
        self.run_store = run_store
        self.account_profiles = dict(account_profiles)
        self.trading_calendar = tuple(trading_calendar)
        self.watchlist_loader = watchlist_loader
        self.market_scope_loader = market_scope_loader
        self.partition_planner = partition_planner

    def resolve_run_inputs(
        self, universe_spec: UniverseSpec, date_range_spec: DateRangeSpec,
        account_profile_id: str, mode: RunMode | str,
        *, current_positions: Iterable[str] = (),
    ) -> tuple[Any, Any, AccountProfile, RunMode]:
        profile = self._profile(account_profile_id)
        run_mode = RunMode(mode)
        date_range = resolve_date_range(date_range_spec,self.trading_calendar)
        universe = resolve_universe(
            universe_spec,watchlist_loader=self.watchlist_loader,
            market_scope_loader=self.market_scope_loader,current_positions=current_positions,
        )
        return universe,date_range,profile,run_mode

    def prepare_market_cache(
        self, universe_spec: UniverseSpec, date_range_spec: DateRangeSpec,
        account_profile_id: str, mode: RunMode | str,
        *, current_positions: Iterable[str] = (),
    ) -> DataReadinessReport:
        universe,date_range,profile,run_mode = self.resolve_run_inputs(
            universe_spec,date_range_spec,account_profile_id,mode,current_positions=current_positions
        )
        if self.partition_planner is None:
            raise Phase5Error("DATA_NOT_READY","partition_planner_missing")
        preparation_id="prep_"+stable_hash((
            universe,date_range,profile.account_profile_id,run_mode.value,
            datetime.now().astimezone().isoformat(),
        ))
        partition_ids: list[str] = []
        complete: list[str] = []; partial: list[str] = []; invalid: list[str] = []; failed: list[str] = []
        sources: list[str] = []; reasons: list[str] = []
        for request in self.partition_planner(universe,date_range,profile,run_mode):
            requested_dates = request.requested_trade_dates or tuple((*date_range.warmup_dates,*date_range.trading_dates))
            self.cache.append_audit(
                preparation_id=preparation_id,action="CACHE_LOOKUP",status="STARTED",
                logical_key=request.logical_key,symbol=request.normalized_symbol,
                dataset_type=request.dataset_type,input_value=requested_dates,
            )
            cached_partitions,missing_dates = self.cache.coverage(request.logical_key,requested_dates)
            self.cache.append_audit(
                preparation_id=preparation_id,
                action=("CACHE_HIT" if not missing_dates else "CACHE_MISS"),
                status=("COMPLETE" if not missing_dates else "MISSING"),
                logical_key=request.logical_key,symbol=request.normalized_symbol,
                dataset_type=request.dataset_type,
                covered_dates=tuple(day for item in cached_partitions for day in item.covered_trade_dates),
                missing_dates=missing_dates,output_value=tuple(item.partition_id for item in cached_partitions),
            )
            if cached_partitions and missing_dates:
                self.cache.append_audit(
                    preparation_id=preparation_id,action="CACHE_PARTIAL",status="PARTIAL",
                    logical_key=request.logical_key,symbol=request.normalized_symbol,
                    dataset_type=request.dataset_type,
                    covered_dates=tuple(
                        day for item in cached_partitions for day in item.covered_trade_dates
                    ),
                    missing_dates=missing_dates,
                    output_value=tuple(item.partition_id for item in cached_partitions),
                )
            if missing_dates:
                self.cache.append_audit(
                    preparation_id=preparation_id,action="MISSING_TRADING_DATES",status="MISSING",
                    logical_key=request.logical_key,symbol=request.normalized_symbol,
                    dataset_type=request.dataset_type,missing_dates=missing_dates,
                    output_value=missing_dates,
                )
            partition_ids.extend(item.partition_id for item in cached_partitions)
            sources.extend(item.source for item in cached_partitions)
            if not missing_dates:
                complete.append(request.logical_key)
                continue
            try:
                providers = tuple(
                    (
                        source,
                        version,
                        self._audited_provider(
                            preparation_id,request,source,version,fetch,missing_dates
                        ),
                    )
                    for source,version,fetch in request.providers
                )
                frame,source,version,fallbacks = fetch_complete_partition(
                    providers,request.dataset_type,suspended=request.suspended,
                    expected_trade_dates=missing_dates,
                )
                for fallback in fallbacks:
                    rejected_source=fallback.split(":",1)[0]
                    self.cache.append_audit(
                        preparation_id=preparation_id,action="PROVIDER_REJECTED",status="REJECTED",
                        logical_key=request.logical_key,symbol=request.normalized_symbol,
                        dataset_type=request.dataset_type,source=rejected_source,
                        reason_code=fallback,missing_dates=missing_dates,output_value=fallback,
                    )
                self.cache.append_audit(
                    preparation_id=preparation_id,
                    action=("PROVIDER_FALLBACK" if fallbacks else "PROVIDER_ATTEMPT"),
                    status="ACCEPTED",logical_key=request.logical_key,
                    symbol=request.normalized_symbol,dataset_type=request.dataset_type,
                    source=source,source_version=version,missing_dates=missing_dates,
                    output_value={"rows":len(frame)},
                )
                status,status_reasons = validate_coverage(
                    request.dataset_type,frame,missing_dates
                )
                partition = self.cache.store_partition(
                    request.dataset_type,request.logical_key,frame.to_dict("records"),
                    source=source,source_version=version,status=status,reasons=(*fallbacks,*status_reasons),
                    normalized_symbol=request.normalized_symbol,frequency=request.frequency,
                    expected_trade_dates=missing_dates,
                )
                sources.append(source); reasons.extend(fallbacks)
                refreshed,remaining_missing = self.cache.coverage(request.logical_key,requested_dates)
                self.cache.append_audit(
                    preparation_id=preparation_id,action="PARTITION_VALIDATED",
                    status=status.value,logical_key=request.logical_key,
                    symbol=request.normalized_symbol,dataset_type=request.dataset_type,
                    source=source,source_version=version,covered_dates=partition.covered_trade_dates,
                    missing_dates=remaining_missing,output_value=partition.partition_id,
                )
                partition_ids.extend(item.partition_id for item in refreshed)
                if status == PartitionStatus.COMPLETE and not remaining_missing:
                    complete.append(request.logical_key); partition_ids.append(partition.partition_id)
                elif status == PartitionStatus.PARTIAL: partial.append(request.logical_key)
                else: invalid.append(request.logical_key)
            except Phase5Error as exc:
                failed.append(request.logical_key); reasons.append(f"{request.logical_key}:{exc.code}")
                self.cache.append_audit(
                    preparation_id=preparation_id,action="PARTITION_INVALID",status="FAILED",
                    logical_key=request.logical_key,symbol=request.normalized_symbol,
                    dataset_type=request.dataset_type,reason_code=exc.code,
                    missing_dates=missing_dates,output_value=str(exc),
                )
        snapshot_id = ""
        status = "READY"
        if partial or invalid or failed:
            status = "NOT_READY"
        else:
            required_dates = tuple((*date_range.warmup_dates,*date_range.trading_dates))
            try:
                snapshot_id = self.cache.create_snapshot(
                    partition_ids,price_basis_id=RAW_PRICE_BASIS,
                    required_trade_dates=required_dates,preparation_id=preparation_id,
                ).data_snapshot_id
                self.cache.append_audit(
                    preparation_id=preparation_id,action="DATA_SNAPSHOT_CREATED",
                    status="COMPLETE",data_snapshot_id=snapshot_id,
                    covered_dates=required_dates,output_value=snapshot_id,
                )
            except Phase5Error as exc:
                status="NOT_READY"
                failed.append("data_snapshot")
                reasons.append(f"data_snapshot:{exc.code}")
                self.cache.append_audit(
                    preparation_id=preparation_id,action="PARTITION_INVALID",
                    status="FAILED",reason_code=exc.code,
                    missing_dates=required_dates,output_value=str(exc),
                )
        self.cache.append_audit(
            preparation_id=preparation_id,
            action=("DATA_READINESS_READY" if status=="READY" else "DATA_READINESS_FAILED"),
            status=status,data_snapshot_id=snapshot_id,reason_code="|".join(sorted(set(reasons))),
            output_value={"partial":partial,"invalid":invalid,"failed":failed},
        )
        return DataReadinessReport(
            status=status,
            requested_range=(date_range.requested_start_date.isoformat(),date_range.requested_end_date.isoformat()),
            actual_range=(date_range.actual_start_date.isoformat(),date_range.actual_end_date.isoformat()),
            warmup_range=(date_range.warmup_start_date.isoformat(),date_range.warmup_dates[-1].isoformat()),
            candidate_symbols=universe.candidate_symbols,required_symbols=universe.required_symbols,
            benchmark_symbols=universe.benchmark_symbols,complete_partitions=tuple(sorted(complete)),
            partial_partitions=tuple(sorted(partial)),invalid_partitions=tuple(sorted(invalid)),
            failed_partitions=tuple(sorted(failed)),sources_used=tuple(sorted(set(sources))),
            price_basis_id=RAW_PRICE_BASIS,data_snapshot_id=snapshot_id,reasons=tuple(sorted(set(reasons))),
        )

    def validate_data_readiness(self, report: DataReadinessReport) -> bool:
        return report.status == "READY" and bool(report.data_snapshot_id) and not (
            report.partial_partitions or report.invalid_partitions or report.failed_partitions
        )

    def create_run(
        self, *, profile_id: str, mode: RunMode | str,
        universe_snapshot: UniverseSnapshot, data_snapshot: DataSnapshot,
        date_range, initial_position_policy: str = "EMPTY", paper_positions=(),
        initial_portfolio=None,
    ) -> tuple[str, RunConfig, Any]:
        profile = self._profile(profile_id)
        data_directory,report_directory = validate_profile_paths(profile)
        account = create_account_snapshot(
            profile,mode,paper_positions=paper_positions,initial_portfolio=initial_portfolio
        )
        self.cache.verify_snapshot(data_snapshot)
        created_at = datetime.now().astimezone().isoformat()
        raw = {
            "mode":RunMode(mode).value,"account":account.account_snapshot_id,
            "universe":universe_snapshot.universe_snapshot_id,
            "data":data_snapshot.data_snapshot_id,"date_range":date_range,
            "price_basis":RAW_PRICE_BASIS,"initial_position_policy":initial_position_policy,
        }
        config = RunConfig(
            run_mode=RunMode(mode),strategy_version="V1.3.13",
            account_snapshot_id=account.account_snapshot_id,
            universe_snapshot_id=universe_snapshot.universe_snapshot_id,
            data_snapshot_id=data_snapshot.data_snapshot_id,date_range=date_range,warmup_trading_days=320,
            price_basis_id=RAW_PRICE_BASIS,network_policy=NetworkAccessPolicy.FORBID,
            report_directory=str(report_directory),initial_position_policy=initial_position_policy,
            created_at=created_at,config_hash=stable_hash(raw),git_commit_sha=_git_commit_sha(),
        )
        run_id = create_runtime_run(
            self.run_store,config,
            {"account":account,"universe":universe_snapshot,"data":data_snapshot},
        )
        self.run_store.import_cache_audits(
            run_id,self.cache.audit_rows(data_snapshot.preparation_id)
        )
        return run_id,config,account

    def execute_run(
        self, run_id: str, config: RunConfig, initial_state: Mapping[str, Any], *,
        dependencies: CoreStrategyDependencies, hooks: RuntimeHooks = RuntimeHooks(),
    ) -> dict[str, Any]:
        return execute_runtime_run(
            self.run_store,run_id,config,initial_state,hooks=hooks,
            trading_calendar=self.trading_calendar,dependencies=dependencies,
        )

    def resume_run(
        self, run_id: str, *, dependencies: CoreStrategyDependencies,
        config_assertion: RunConfig | None = None, hooks: RuntimeHooks = RuntimeHooks(),
    ) -> dict[str, Any]:
        return resume_runtime_run(
            self.run_store,run_id,self.cache,dependencies=dependencies,
            config_assertion=config_assertion,hooks=hooks,
            trading_calendar=self.trading_calendar,
        )

    def get_run_status(self, run_id: str) -> str:
        row = self.run_store.get_run(run_id)
        if row is None: raise Phase5Error("INVALID_CONFIG","run_not_found")
        return str(row["status"])

    def generate_run_report(self, run_id: str, report_directory: str | Path | None = None) -> Path:
        row = self.run_store.get_run(run_id)
        if row is None: raise Phase5Error("REPORT_WRITE_FAILED","run_not_found")
        config = __import__("json").loads(row["config_json"])
        return build_report(self.run_store,run_id,report_directory or config["report_directory"])

    def list_runs(self):
        return self.run_store.list_runs()

    def load_run_summary(self, run_id: str) -> dict[str, Any]:
        row = self.run_store.get_run(run_id)
        if row is None: raise Phase5Error("INVALID_CONFIG","run_not_found")
        daily = self.run_store.rows("adaptive_v13_daily_account_snapshots",run_id)
        return {**row,"daily_count":len(daily),"last_daily":daily[-1] if daily else None}

    def _profile(self, identifier: str) -> AccountProfile:
        try:
            return self.account_profiles[identifier]
        except KeyError:
            raise Phase5Error("INVALID_CONFIG","account_profile_not_found") from None

    def _audited_provider(
        self, preparation_id: str, request: PartitionRequest, source: str,
        version: str, fetch: Callable[[], pd.DataFrame], missing_dates,
    ) -> Callable[[], pd.DataFrame]:
        def audited() -> pd.DataFrame:
            self.cache.append_audit(
                preparation_id=preparation_id,action="PROVIDER_ATTEMPT",status="STARTED",
                logical_key=request.logical_key,symbol=request.normalized_symbol,
                dataset_type=request.dataset_type,source=source,source_version=version,
                missing_dates=missing_dates,input_value={"source":source,"version":version},
            )
            return fetch()
        return audited


def resolve_run_inputs(service: Phase5Service, *args, **kwargs):
    return service.resolve_run_inputs(*args,**kwargs)


def prepare_market_cache(service: Phase5Service, *args, **kwargs):
    return service.prepare_market_cache(*args,**kwargs)


def validate_data_readiness(report: DataReadinessReport) -> bool:
    return report.status == "READY" and bool(report.data_snapshot_id)


def _git_commit_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True,
            text=True, timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        try:
            return subprocess.run(
                [r"D:\Tools\PortableGit\cmd\git.exe", "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise Phase5Error("INVALID_CONFIG", "git_commit_sha_unavailable") from exc
