"""Phase 6 controller: the sole boundary used by the adaptive-trend web UI."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from threading import Lock, RLock
import time
from typing import Any, Callable, Iterable, Mapping

from stock_picker.user.watchlist import WatchlistStore

from .market_cache import RAW_PRICE_BASIS
from .phase5_models import (
    AccountProfile, DateRangeSpec, Phase5Error, RunMode, UniverseSpec,
)
from .phase5_service import Phase5Service
from .phase6_models import (
    AccountSummaryVM, CacheProgressVM, DataReadinessVM, DateRangeVM, ErrorVM,
    PreparedInputState, ProviderStatusVM, ReportFileVM, RunDetailVM,
    RunListItemVM, RunSummaryVM, SubmissionResult, UniverseSelectionVM,
)
from .phase6_idempotency import Phase6IdempotencyStore
from .phase6_profile_store import AccountProfileStore, validate_decimal_text
from .phase6_provider_registry import ProviderRegistry
from .run_store import canonical_json, stable_hash


ERRORS: dict[str, tuple[str, str, bool]] = {
    "INVALID_CONFIG": ("配置无效", "请检查账户、目录及运行参数。", False),
    "INVALID_UNIVERSE": ("股票池无效", "请修正无效证券代码或重新选择股票池。", False),
    "INVALID_DATE_RANGE": ("日期范围无效", "请选择有效且顺序正确的交易日期范围。", False),
    "INSUFFICIENT_WARMUP": ("预热数据不足", "请扩大数据范围并重新缓存。", False),
    "DATA_NOT_READY": ("数据尚未就绪", "请先缓存并验证完整数据。", True),
    "PARTIAL_CACHE": ("缓存覆盖不完整", "请补齐缺失交易日后重试。", True),
    "INVALID_PARTITION": ("缓存分区无效", "请重新准备对应数据分区。", True),
    "PROVIDER_FAILED": ("数据源失败", "请检查 Provider 状态或使用已配置回退源。", True),
    "RULE_SNAPSHOT_MISSING": ("交易规则缺失", "请重新缓存交易规则快照。", True),
    "FEE_SNAPSHOT_MISSING": ("费用规则缺失", "请重新缓存费用快照。", True),
    "PRICE_BASIS_MISMATCH": ("价格口径不一致", "请使用原始未复权成交价格快照。", False),
    "LOOKAHEAD_ACCESS": ("检测到前视访问", "运行已阻止，请核对数据时点。", False),
    "DUPLICATE_EVENT": ("事件重复", "请刷新运行状态，不要重复提交。", True),
    "DUPLICATE_FILL": ("成交重复", "请刷新运行状态并检查成交唯一键。", False),
    "LEDGER_CONFLICT": ("账户流水冲突", "请从最近检查点恢复。", True),
    "STATE_VERSION_CONFLICT": ("状态版本冲突", "请刷新后从最近检查点恢复。", True),
    "SCHEMA_VERSION_MISMATCH": ("数据版本不兼容", "请使用匹配版本的数据文件。", False),
    "RUN_FINGERPRINT_MISMATCH": ("运行输入不匹配", "请重新验证缓存和运行输入。", False),
    "REPORT_WRITE_FAILED": ("报告不可用", "请重新生成报告并校验文件。", True),
    "MISSING_MARK_PRICE": ("日终估值价格缺失", "请补齐当日分钟或收盘数据。", True),
    "UNEXPECTED_ENGINE_ERROR": ("运行引擎异常", "请保留运行记录并查看审计原因。", True),
}


class Phase6Controller:
    _submission_lock_guard = Lock()
    _submission_locks: dict[tuple[str, str], RLock] = {}

    def __init__(
        self, *, service: Phase5Service, profile_store: AccountProfileStore,
        watchlist_store: WatchlistStore, provider_registry: ProviderRegistry,
        dependency_factory: Callable[[str], Any] | None = None,
        paper_state_loader: Callable[[str], Mapping[str, Any]] | None = None,
        idempotency_store: Phase6IdempotencyStore | None = None,
    ) -> None:
        self.service = service
        self.profile_store = profile_store
        self.watchlist_store = watchlist_store
        self.provider_registry = provider_registry
        self.dependency_factory = dependency_factory
        self.paper_state_loader = paper_state_loader or (lambda _profile_id: {})
        db_path = getattr(getattr(service, "run_store", None), "db_path", None)
        self.idempotency_store = idempotency_store or (
            Phase6IdempotencyStore(db_path) if db_path is not None else None
        )
        self._prepared: dict[str, PreparedInputState] = {}
        self._active_runs: set[str] = set()
        self._lock = RLock()
        for profile in profile_store.load().values():
            self.service.upsert_account_profile(profile)

    def load_account_summary(
        self, account_profile_id: str, mode: RunMode | str = RunMode.BACKTEST,
    ) -> AccountSummaryVM:
        profile = self._profile(account_profile_id)
        paper = self.paper_state_loader(account_profile_id) or {}
        positions = paper.get("positions", ())
        data_status = "READY" if Path(profile.data_directory).expanduser().exists() else "MISSING"
        latest = next(
            ("READY" for item in reversed(tuple(self._prepared.values()))
             if item.account_profile_id == account_profile_id), "EMPTY"
        )
        return AccountSummaryVM(
            profile.account_profile_id, RunMode(mode).value,
            validate_decimal_text(profile.backtest_initial_cash, "backtest_initial_cash"),
            validate_decimal_text(profile.paper_cash, "paper_cash"),
            len(positions), profile.fee_schedule_id, profile.base_currency,
            tuple(profile.provider_priority), data_status, latest,
        )

    def save_account_settings(self, values: Mapping[str, object]) -> AccountSummaryVM:
        identifier = str(values.get("account_profile_id", "")).strip()
        if not identifier:
            raise Phase5Error("INVALID_CONFIG", "account_profile_id_required")
        providers = _text_tuple(values.get("provider_priority", ()))
        if not providers:
            raise Phase5Error("INVALID_CONFIG", "provider_priority_required")
        universe = values.get("default_universe")
        if not isinstance(universe, UniverseSpec):
            raise Phase5Error("INVALID_CONFIG", "default_universe_required")
        data_directory = str(values.get("data_directory", "")).strip()
        report_directory = str(values.get("report_directory", "")).strip()
        if not data_directory or not report_directory:
            raise Phase5Error("INVALID_CONFIG", "account_required_field_missing")
        profile = AccountProfile(
            identifier,
            validate_decimal_text(values.get("backtest_initial_cash", ""), "backtest_initial_cash"),
            validate_decimal_text(values.get("paper_cash", ""), "paper_cash"),
            str(values.get("fee_schedule_id", "")).strip(),
            str(values.get("base_currency", "CNY")).strip() or "CNY",
            universe, providers,
            str(Path(data_directory).expanduser().resolve()),
            str(Path(report_directory).expanduser().resolve()),
        )
        if not profile.fee_schedule_id or not profile.data_directory or not profile.report_directory:
            raise Phase5Error("INVALID_CONFIG", "account_required_field_missing")
        self.profile_store.save(profile)
        self.service.upsert_account_profile(profile)
        return self.load_account_summary(identifier)

    def list_watchlists(self):
        return tuple(self.watchlist_store.list())

    def save_watchlist(
        self, action: str, *, name: str, symbols: Iterable[str] = (),
        new_name: str = "", source_name: str = "",
    ):
        if action == "create":
            result = self.watchlist_store.create(name)
        elif action == "add":
            result = self.watchlist_store.add_symbols(name,list(symbols))
        elif action == "remove":
            values = tuple(symbols)
            if len(values) != 1:
                raise Phase5Error("INVALID_UNIVERSE","single_remove_symbol_required")
            result = self.watchlist_store.remove_symbol(name,values[0])
        elif action == "rename":
            result = self.watchlist_store.rename(name,new_name)
        elif action == "copy":
            source = self.watchlist_store.get(source_name)
            if source is None:
                raise Phase5Error("INVALID_UNIVERSE","watchlist_not_found")
            result = self.watchlist_store.create(name)
            if result.status == "ok":
                result = self.watchlist_store.add_symbols(name,source.symbols)
        elif action == "set_default":
            selected = self.watchlist_store.get(name)
            if selected is None:
                raise Phase5Error("INVALID_UNIVERSE","watchlist_not_found")
            self.watchlist_store.save_last_manual_input(f"WATCHLIST:{name}")
            result = selected
        else:
            raise Phase5Error("INVALID_CONFIG","unsupported_watchlist_action")
        if getattr(result,"status","ok") not in {"ok","duplicate","partial_success"}:
            raise Phase5Error("INVALID_UNIVERSE",str(getattr(result,"status","watchlist_failed")))
        return result

    def delete_watchlist(self, name: str):
        result = self.watchlist_store.delete(name)
        if result.status != "ok":
            raise Phase5Error("INVALID_UNIVERSE",result.status)
        return result

    def preview_universe(
        self, specification: UniverseSpec, account_profile_id: str,
        date_range_spec: DateRangeSpec, mode: RunMode | str,
        *, current_positions: Iterable[str] = (),
    ) -> UniverseSelectionVM:
        universe,_,_,_ = self.service.resolve_run_inputs(
            specification,date_range_spec,account_profile_id,mode,
            current_positions=current_positions,
        )
        raw = (*specification.manual_symbols,*current_positions)
        return UniverseSelectionVM(
            specification,universe.candidate_symbols,universe.required_symbols,
            universe.benchmark_symbols,tuple(sorted(set(map(str,current_positions)))),
            universe.sources,len(raw),len(set(universe.candidate_symbols)),
            max(0,len(raw)-len(set(raw))),
        )

    def resolve_date_range(
        self, specification: DateRangeSpec, account_profile_id: str,
        universe_spec: UniverseSpec, mode: RunMode | str,
    ) -> DateRangeVM:
        _,resolved,_,_ = self.service.resolve_run_inputs(
            universe_spec,specification,account_profile_id,mode,
        )
        return DateRangeVM(
            specification,
            (resolved.requested_start_date.isoformat(),resolved.requested_end_date.isoformat()),
            (resolved.actual_start_date.isoformat(),resolved.actual_end_date.isoformat()),
            (resolved.warmup_start_date.isoformat(),resolved.warmup_dates[-1].isoformat()),
            len(resolved.trading_dates),len(resolved.warmup_dates),
        )

    def inspect_provider_status(self, account_profile_id: str) -> tuple[ProviderStatusVM, ...]:
        return self.provider_registry.inspect(self._profile(account_profile_id).provider_priority)

    def test_provider_connections(
        self, timeout_seconds: float = 3.0, account_profile_id: str = "default",
    ) -> tuple[ProviderStatusVM, ...]:
        priorities = self._profile(account_profile_id).provider_priority
        return self.provider_registry.test_connections(timeout_seconds, priorities=priorities)

    def prepare_cache(
        self, universe_spec: UniverseSpec, date_range_spec: DateRangeSpec,
        account_profile_id: str, mode: RunMode | str,
        *, current_positions: Iterable[str] = (),
    ) -> tuple[tuple[CacheProgressVM, ...], DataReadinessVM]:
        run_mode = RunMode(mode)
        if run_mode is RunMode.DAILY_PAPER and not tuple(current_positions):
            paper = self.paper_state_loader(account_profile_id) or {}
            current_positions = tuple((paper.get("positions") or {}).keys())
        signature = self._input_hash(
            universe_spec,date_range_spec,account_profile_id,run_mode,current_positions,
        )
        report = self.service.prepare_market_cache(
            universe_spec,date_range_spec,account_profile_id,run_mode,
            current_positions=current_positions,
        )
        universe_snapshot,resolved,_,_ = self.service.create_universe_snapshot(
            universe_spec,account_profile_id,run_mode,date_range_spec=date_range_spec,
            current_positions=current_positions,
        )
        view = DataReadinessVM(
            report.status,signature,report.data_snapshot_id,report.price_basis_id,
            len(report.candidate_symbols),len(report.required_symbols),
            len(resolved.trading_dates),len(resolved.warmup_dates),
            report.complete_partitions,report.partial_partitions,report.invalid_partitions,
            report.failed_partitions,report.sources_used,report.reasons,
            report.status == "READY",
        )
        if report.status == "READY":
            self._prepared[signature] = PreparedInputState(
                signature,account_profile_id,run_mode.value,universe_spec,date_range_spec,
                report.data_snapshot_id,report.price_basis_id,
                universe_snapshot.universe_snapshot_id,
            )
        return (
            CacheProgressVM("RESOLVE_INPUTS","COMPLETED","股票池和日期已解析",1,3),
            CacheProgressVM("PREPARE_PARTITIONS",report.status,"缓存分区已验证",2,3),
            CacheProgressVM("CREATE_SNAPSHOT",report.status,"不可变数据快照已生成",3,3),
        ),view

    def validate_snapshot(
        self, data_snapshot_id: str, universe_spec: UniverseSpec,
        date_range_spec: DateRangeSpec, account_profile_id: str,
        mode: RunMode | str, *, current_positions: Iterable[str] = (),
    ) -> bool:
        signature = self._input_hash(
            universe_spec,date_range_spec,account_profile_id,RunMode(mode),current_positions,
        )
        prepared = self._prepared.get(signature)
        if prepared is None or prepared.data_snapshot_id != data_snapshot_id:
            return False
        try:
            snapshot = self.service.load_data_snapshot(data_snapshot_id)
            cache = getattr(self.service, "cache", None)
            if cache is not None:
                cache.verify_snapshot(snapshot)
        except (AttributeError, Phase5Error, OSError, ValueError):
            return False
        return snapshot.price_basis_id == RAW_PRICE_BASIS

    def create_backtest(
        self, universe_spec: UniverseSpec, date_range_spec: DateRangeSpec,
        account_profile_id: str, data_snapshot_id: str,
        *, current_positions: Iterable[str] = (), run_overrides: Mapping[str, object] | None = None,
    ) -> tuple[str, Any, Mapping[str, Any]]:
        profile_id = self._profile_for_override(account_profile_id,run_overrides)
        signature = self._input_hash(
            universe_spec,date_range_spec,profile_id,RunMode.BACKTEST,current_positions,
        )
        if not self.validate_snapshot(
            data_snapshot_id,universe_spec,date_range_spec,profile_id,RunMode.BACKTEST,
            current_positions=current_positions,
        ):
            raise Phase5Error("RUN_FINGERPRINT_MISMATCH","prepared_input_mismatch")
        universe_snapshot,resolved,_,_ = self.service.create_universe_snapshot(
            universe_spec,profile_id,RunMode.BACKTEST,date_range_spec=date_range_spec,
            current_positions=current_positions,
        )
        data_snapshot = self.service.load_data_snapshot(data_snapshot_id)
        run_id,config,account = self.service.create_run(
            profile_id=profile_id,mode=RunMode.BACKTEST,
            universe_snapshot=universe_snapshot,data_snapshot=data_snapshot,date_range=resolved,
        )
        loader = getattr(self.service, "load_initial_runtime_state", None)
        if callable(loader):
            initial = loader(run_id)
        else:
            initial = {
                "cash":account.cash,"positions":dict(account.positions),
                "pending_sells":{},"exit_controls":{},"cooldowns":{},"fill_requests":(),
            }
        return run_id,config,initial

    def execute_run(self, run_id: str, config: Any, initial_state: Mapping[str, Any]):
        dependencies = self._dependencies(run_id)
        with self._run_lock(run_id):
            return self.service.execute_run(
                run_id,config,dict(initial_state),dependencies=dependencies,
            )

    def create_daily_paper_run(
        self, universe_spec: UniverseSpec, date_range_spec: DateRangeSpec,
        account_profile_id: str, data_snapshot_id: str,
    ):
        paper = self.paper_state_loader(account_profile_id) or {}
        positions = tuple((paper.get("positions") or {}).keys())
        if not self.validate_snapshot(
            data_snapshot_id,universe_spec,date_range_spec,account_profile_id,
            RunMode.DAILY_PAPER,current_positions=positions,
        ):
            raise Phase5Error("RUN_FINGERPRINT_MISMATCH","prepared_input_mismatch")
        universe_snapshot,resolved,_,_ = self.service.create_universe_snapshot(
            universe_spec,account_profile_id,RunMode.DAILY_PAPER,
            date_range_spec=date_range_spec,current_positions=positions,
        )
        data_snapshot = self.service.load_data_snapshot(data_snapshot_id)
        run_id,config,_account = self.service.create_run(
            profile_id=account_profile_id,mode=RunMode.DAILY_PAPER,
            universe_snapshot=universe_snapshot,data_snapshot=data_snapshot,
            date_range=resolved,paper_positions=tuple((paper.get("positions") or {}).items()),
            initial_position_policy="PAPER_ACCOUNT",
            initial_runtime_state=paper,
        )
        return run_id,config,self._load_initial_state(run_id)

    def submit_backtest(
        self, universe_spec: UniverseSpec, date_range_spec: DateRangeSpec,
        account_profile_id: str, data_snapshot_id: str, operation_token: str,
        *, current_positions: Iterable[str] = (),
        run_overrides: Mapping[str, object] | None = None,
    ) -> SubmissionResult:
        profile_id = self._profile_for_override(account_profile_id, run_overrides)
        account = self._preview_account(profile_id, RunMode.BACKTEST)
        fingerprint = self._submission_fingerprint(
            account.account_snapshot_id, universe_spec, date_range_spec,
            data_snapshot_id, RunMode.BACKTEST, run_overrides,
        )
        return self._submit(
            fingerprint, operation_token,
            lambda: self.create_backtest(
                universe_spec, date_range_spec, profile_id, data_snapshot_id,
                current_positions=current_positions,
            ),
        )

    def submit_daily_paper(
        self, universe_spec: UniverseSpec, date_range_spec: DateRangeSpec,
        account_profile_id: str, data_snapshot_id: str, operation_token: str,
    ) -> SubmissionResult:
        paper = self.paper_state_loader(account_profile_id) or {}
        account = self._preview_account(
            account_profile_id, RunMode.DAILY_PAPER,
            paper_positions=tuple((paper.get("positions") or {}).items()),
        )
        fingerprint = self._submission_fingerprint(
            account.account_snapshot_id, universe_spec, date_range_spec,
            data_snapshot_id, RunMode.DAILY_PAPER, None,
        )
        return self._submit(
            fingerprint, operation_token,
            lambda: self.create_daily_paper_run(
                universe_spec, date_range_spec, account_profile_id, data_snapshot_id,
            ),
        )

    def resume_run(self, run_id: str, operation_token: str = ""):
        if not operation_token or self.idempotency_store is None:
            with self._run_lock(run_id):
                return self.service.resume_run(run_id,dependencies=self._dependencies(run_id))
        fingerprint = stable_hash({"action": "resume", "run_id": run_id})
        claim = self.idempotency_store.claim(operation_token, fingerprint)
        if not claim.owned:
            resolved = self._await_submission(claim.operation_token)
            return {
                "run_id": resolved.run_id or run_id,
                "status": self.service.get_run_status(run_id),
            }
        with self._submission_lock(fingerprint):
            self.idempotency_store.bind_run(operation_token, run_id)
            try:
                result = self.service.resume_run(run_id, dependencies=self._dependencies(run_id))
            except Exception:
                self.idempotency_store.finish(operation_token, "RECOVERABLE_FAILED")
                raise
            self.idempotency_store.finish(operation_token, "COMPLETED")
            return result

    def list_runs(
        self, *, mode: str = "", status: str = "", account: str = "",
        strategy_version: str = "", degraded: bool | None = None,
        date_from: str = "", date_to: str = "",
        has_open_positions: bool | None = None,
        page: int = 1, page_size: int = 20,
    ) -> tuple[RunListItemVM, ...]:
        result = []
        for row in self.service.list_runs():
            config = json.loads(row["config_json"])
            item = RunListItemVM(
                row["run_id"],row["run_id"][:12],config.get("run_mode",""),
                row["status"],_nested_date(config,"requested_start_date"),
                _nested_date(config,"requested_end_date"),row["created_at"],row["updated_at"],
                config.get("strategy_version",""),row.get("account_profile_id",""),
                fill_count=int(row.get("fill_count",0)),
                open_position_count=int(row.get("open_position_count",0)),
                degraded=row["status"] == "DEGRADED",
                report_status=str(row.get("report_status","NOT_GENERATED")),
            )
            if mode and item.mode != mode: continue
            if status and item.status != status: continue
            if account and account not in item.account_profile_id: continue
            if strategy_version and item.strategy_version != strategy_version: continue
            if degraded is not None and item.degraded != degraded: continue
            if date_from and item.end_date < date_from: continue
            if date_to and item.start_date > date_to: continue
            if has_open_positions is not None and (item.open_position_count > 0) != has_open_positions:
                continue
            result.append(item)
        size = min(max(int(page_size), 1), 100)
        start = max(int(page)-1,0)*size
        return tuple(result[start:start+size])

    def can_resume_run(self, run_id: str) -> bool:
        return bool(self.service.get_run_recovery_status(run_id)["recoverable"])

    def load_run_detail(self, run_id: str) -> RunDetailVM:
        raw = self.service.load_run_detail(run_id)
        run,config,tables = raw["run"],raw["config"],raw["tables"]
        daily = tables["daily"]
        last = daily[-1] if daily else {}
        summary = RunSummaryVM(
            run_id,run["status"],config.get("run_mode",""),config.get("strategy_version",""),
            config.get("git_commit_sha",""),
            (_nested_date(config,"requested_start_date"),_nested_date(config,"requested_end_date")),
            config.get("account_snapshot_id",""),config.get("data_snapshot_id",""),
            config.get("price_basis_id",""),run["created_at"],run["updated_at"],
            tuple(sorted((str(key),str(value)) for key,value in last.items()
                         if key not in {"run_id","snapshot_id"})),
            len({row.get("symbol","") for row in tables["positions"] if row.get("symbol")}),
            tuple(filter(None, (str(run.get("failure_reason", "")),))),
        )
        series = tuple((str(row.get("trade_date","")),str(row.get("equity",""))) for row in daily)
        return RunDetailVM(
            summary,series,(),(),tuple((str(row.get("trade_date","")),str(row.get("exposure",""))) for row in daily),
            tuple((str(row.get("trade_date","")),str(row.get("position_count",""))) for row in daily),
            _freeze_rows(tables["fills"]),_freeze_rows(tables["fill_requests"]),
            _freeze_rows(tables["positions"]),_freeze_rows(tables["decisions"]),
            _freeze_rows(tables["pending_sells"]),_freeze_rows(tables["cooldowns"]),
            _freeze_rows(raw.get("partition_links",())),_freeze_rows(tables["audits"]),
        )

    def generate_report(self, run_id: str) -> tuple[ReportFileVM, ...]:
        self.service.generate_run_report(run_id)
        return self.list_report_files(run_id)

    def list_report_files(self, run_id: str) -> tuple[ReportFileVM, ...]:
        result = []
        for item in self.service.list_report_files(run_id):
            valid = False
            reason = ""
            try:
                self.service.validate_report_file(run_id,item["name"])
                valid = True
            except Phase5Error as exc:
                reason = exc.code
            result.append(ReportFileVM(
                item["name"],item["path"],item["sha256"],item["size_bytes"],
                _media_type(item["name"]),valid,reason,
            ))
        return tuple(result)

    def validate_report_file(self, run_id: str, name: str) -> Path:
        return self.service.validate_report_file(run_id,name)

    def get_error_view(self, error: BaseException | str) -> ErrorVM:
        code = error.code if isinstance(error,Phase5Error) else str(error)
        if code not in ERRORS:
            code = "UNEXPECTED_ENGINE_ERROR"
        title,action,recoverable = ERRORS[code]
        correlation_id = sha256(
            f"{type(error).__name__}:{code}".encode("utf-8")
        ).hexdigest()[:16]
        return ErrorVM(title,action,code,"",recoverable,correlation_id)

    def _profile(self, identifier: str) -> AccountProfile:
        try:
            return self.service.account_profiles[identifier]
        except KeyError:
            raise Phase5Error("INVALID_CONFIG","account_profile_not_found") from None

    def _profile_for_override(
        self, identifier: str, overrides: Mapping[str, object] | None,
    ) -> str:
        if not overrides:
            return identifier
        base = self._profile(identifier)
        temporary_id = f"{identifier}__run_{stable_hash(overrides)[:12]}"
        profile = AccountProfile(
            temporary_id,
            validate_decimal_text(overrides.get("initial_cash",base.backtest_initial_cash),"initial_cash"),
            base.paper_cash,base.fee_schedule_id,base.base_currency,base.default_universe,
            _text_tuple(overrides.get("provider_priority",base.provider_priority)),
            base.data_directory,str(overrides.get("report_directory",base.report_directory)),
        )
        self.service.upsert_account_profile(profile)
        return temporary_id

    def _input_hash(
        self, universe: UniverseSpec, dates: DateRangeSpec, profile_id: str,
        mode: RunMode, current_positions: Iterable[str],
    ) -> str:
        profile = self._profile(profile_id)
        return stable_hash({
            "universe":asdict(universe),"dates":asdict(dates),"profile":asdict(profile),
            "mode":mode.value,"positions":tuple(sorted(map(str,current_positions))),
            "price_basis":RAW_PRICE_BASIS,
        })

    def _dependencies(self, run_id: str):
        if self.dependency_factory is None:
            raise Phase5Error("DATA_NOT_READY","runtime_dependency_factory_missing")
        return self.dependency_factory(run_id)

    def _load_initial_state(self, run_id: str) -> Mapping[str, Any]:
        loader = getattr(self.service, "load_initial_runtime_state", None)
        if not callable(loader):
            raise Phase5Error("STATE_VERSION_CONFLICT", "initial_state_service_missing")
        return loader(run_id)

    def _preview_account(self, profile_id: str, mode: RunMode, **kwargs):
        preview = getattr(self.service, "preview_account_snapshot", None)
        if not callable(preview):
            raise Phase5Error("INVALID_CONFIG", "account_snapshot_preview_missing")
        return preview(profile_id=profile_id, mode=mode, **kwargs)

    def _submission_fingerprint(
        self, account_snapshot_id: str, universe: UniverseSpec,
        dates: DateRangeSpec, data_snapshot_id: str, mode: RunMode,
        overrides: Mapping[str, object] | None,
    ) -> str:
        return stable_hash({
            "account_snapshot_id": account_snapshot_id,
            "universe_input_hash": stable_hash(asdict(universe)),
            "date_range_input_hash": stable_hash(asdict(dates)),
            "data_snapshot_id": data_snapshot_id,
            "run_mode": mode.value,
            "strategy_version": "V1.3.13",
            "overrides": dict(sorted((str(k), v) for k, v in (overrides or {}).items())),
        })

    def _submit(self, fingerprint: str, operation_token: str, creator) -> SubmissionResult:
        if self.idempotency_store is None:
            raise Phase5Error("INVALID_CONFIG", "idempotency_store_missing")
        claim = self.idempotency_store.claim(operation_token, fingerprint)
        if not claim.owned:
            resolved = self._await_submission(claim.operation_token)
            if not resolved.run_id:
                return self._submit(fingerprint,operation_token,creator)
            return SubmissionResult(
                resolved.run_id,self.service.get_run_status(resolved.run_id),True,fingerprint,
            )
        with self._submission_lock(fingerprint):
            run_id = ""
            try:
                run_id, config, initial = creator()
                self.idempotency_store.bind_run(operation_token, run_id)
                self.execute_run(run_id, config, initial)
                status = self.service.get_run_status(run_id)
                self.idempotency_store.finish(operation_token, "COMPLETED")
                return SubmissionResult(run_id, status, False, fingerprint)
            except Exception as exc:
                if not run_id:
                    self.idempotency_store.finish(operation_token, "RELEASED")
                else:
                    view = self.get_error_view(exc)
                    self.idempotency_store.finish(
                        operation_token,
                        "RECOVERABLE_FAILED" if view.recoverable else "RELEASED",
                    )
                raise

    def _await_submission(self, operation_token: str):
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            current = self.idempotency_store.get(operation_token)
            if current is None:
                break
            if current.run_id or current.state not in {"ACTIVE","ALIAS"}:
                return current
            time.sleep(0.01)
        raise Phase5Error("DUPLICATE_EVENT", "submission_creation_in_progress")

    def _submission_lock(self, fingerprint: str) -> RLock:
        key = (
            str(getattr(self.idempotency_store, "db_path", "")),
            fingerprint,
        )
        with self._submission_lock_guard:
            return self._submission_locks.setdefault(key, RLock())

    class _RunGuard:
        def __init__(self, owner: "Phase6Controller", run_id: str) -> None:
            self.owner,self.run_id = owner,run_id
        def __enter__(self):
            with self.owner._lock:
                if self.run_id in self.owner._active_runs:
                    raise Phase5Error("DUPLICATE_EVENT","run_already_active")
                self.owner._active_runs.add(self.run_id)
        def __exit__(self,*_):
            with self.owner._lock:
                self.owner._active_runs.discard(self.run_id)

    def _run_lock(self, run_id: str) -> "_RunGuard":
        return self._RunGuard(self,run_id)


def _text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value,str):
        return tuple(item.strip() for item in value.replace(";",",").split(",") if item.strip())
    if isinstance(value,Iterable):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _freeze_rows(rows: Iterable[Mapping[str,Any]]) -> tuple[tuple[tuple[str,Any],...],...]:
    return tuple(tuple(sorted((str(key),value) for key,value in row.items())) for row in rows)


def _nested_date(config: Mapping[str,Any], key: str) -> str:
    value = config.get("date_range",{})
    return str(value.get(key,"")) if isinstance(value,Mapping) else ""


def _media_type(name: str) -> str:
    if name.endswith(".xlsx"): return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if name.endswith(".jsonl"): return "application/x-ndjson"
    return "application/json"
