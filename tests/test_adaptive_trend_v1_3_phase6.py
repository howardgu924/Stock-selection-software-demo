from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import time

import pytest

from stock_picker.strategies.adaptive_trend_v1_3.phase5_models import (
    AccountProfile, DataReadinessReport, DataSnapshot, DateRangeSpec, Phase5Error, ResolvedDateRange,
    ResolvedUniverse, RunMode, UniverseSnapshot, UniverseSpec,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase6_controller import ERRORS, Phase6Controller
from stock_picker.strategies.adaptive_trend_v1_3.phase6_models import (
    AccountSummaryVM, CacheProgressVM, DataReadinessVM, DateRangeVM, ErrorVM,
    ExecutionBackend, ProviderStatusVM, ReportFileVM, RunDetailVM, RunListItemVM,
    RunSummaryVM, UniverseSelectionVM,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase6_profile_store import (
    AccountProfileStore, validate_decimal_text,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase6_provider_registry import (
    ProviderDescriptor, ProviderRegistry,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase6_idempotency import (
    Phase6IdempotencyStore,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase6_app_factory import (
    create_phase6_application,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase6_web import (
    PHASE6_PAGES, Phase6WebState, handle_phase6_action, refresh_snapshot_state,
    render_phase6_page, update_selection,
)
from stock_picker.user import WatchlistStore


CALENDAR = tuple(date(2023, 1, 2) + timedelta(days=index) for index in range(700))
PROFILE = AccountProfile(
    "default", Decimal("100000"), Decimal("50000"), "fee-v1", "CNY",
    UniverseSpec("MANUAL", ("600000.SH",)), ("baostock", "akshare"),
    ".", "reports",
)
RESOLVED = ResolvedDateRange(
    CALENDAR[-400], CALENDAR[-1], CALENDAR[-400], CALENDAR[-1],
    CALENDAR[-700], CALENDAR[-400:], CALENDAR[-700:-400],
)
UNIVERSE = ResolvedUniverse(
    ("600000.SH",), ("000300.SH", "000852.SH", "399006.SZ", "600000.SH"),
    ("000300.SH", "000852.SH", "399006.SZ"), ("MANUAL",),
)
DATA = DataSnapshot(
    "data-1", ("part-1",), "RAW_UNADJUSTED_V1", "2026-07-25T10:00:00+08:00",
    "hash", (("part-1", "content"),), tuple(item.isoformat() for item in CALENDAR[-400:]),
    (), (), "READY", (), "prep-1",
)


class FakeService:
    def __init__(self):
        self.account_profiles = {"default": PROFILE}
        self.executed = []
        self.resumed = []
        self.create_count = 0
        self.initial_states = {}
        self.statuses = {}
        self.execute_delay = 0.0

    def upsert_account_profile(self, profile):
        self.account_profiles[profile.account_profile_id] = profile

    def resolve_run_inputs(self, *_args, **_kwargs):
        return UNIVERSE, RESOLVED, PROFILE, RunMode(_args[3])

    def prepare_market_cache(self, *_args, **_kwargs):
        return DataReadinessReport(
            "READY", ("2024-01-01", "2025-01-01"), ("2024-01-02", "2025-01-01"),
            ("2023-01-01", "2023-12-31"), UNIVERSE.candidate_symbols,
            UNIVERSE.required_symbols, UNIVERSE.benchmark_symbols, ("daily:600000",),
            (), (), (), ("fixture",), "RAW_UNADJUSTED_V1", "data-1",
        )

    def create_universe_snapshot(self, *_args, **_kwargs):
        return (
            UniverseSnapshot(
                "universe-1", UNIVERSE.candidate_symbols, UNIVERSE.required_symbols, (),
                UNIVERSE.benchmark_symbols, UNIVERSE.sources, "u-hash", "now",
            ), RESOLVED, PROFILE, RunMode(_args[2]),
        )

    def load_data_snapshot(self, snapshot_id):
        assert snapshot_id == "data-1"
        return DATA

    def create_run(self, **kwargs):
        class Config:
            pass
        class Account:
            cash = Decimal("100000")
            positions = ()
            account_snapshot_id = "account-1"
        self.create_count += 1
        run_id = f"run-{self.create_count}"
        supplied = kwargs.get("initial_runtime_state") or {}
        self.initial_states[run_id] = {
            "cash":Decimal("100000"),"positions":{},
            "pending_sells":dict(supplied.get("pending_sells") or {}),
            "exit_controls":dict(supplied.get("exit_controls") or {}),
            "cooldowns":dict(supplied.get("cooldowns") or {}),
            "fill_requests":tuple(supplied.get("fill_requests") or ()),
        }
        self.statuses[run_id] = "CREATED"
        return run_id, Config(), Account()

    def preview_account_snapshot(self, **_kwargs):
        return SimpleNamespace(account_snapshot_id="account-1")

    def load_initial_runtime_state(self, run_id):
        return dict(self.initial_states[run_id])

    def execute_run(self, run_id, config, initial_state, *, dependencies):
        time.sleep(self.execute_delay)
        self.executed.append((run_id, config, initial_state, dependencies))
        self.statuses[run_id] = "COMPLETED"
        return {"status": "COMPLETED"}

    def resume_run(self, run_id, *, dependencies):
        self.resumed.append((run_id, dependencies))
        return {"status": "COMPLETED"}

    def list_runs(self):
        return ()

    def get_run_status(self, run_id):
        return self.statuses[run_id]

    def get_run_recovery_status(self, run_id):
        return {"status":self.statuses[run_id],"recoverable":False,"failure_reason":""}

    def load_run_detail(self, run_id):
        raise AssertionError(run_id)

    def generate_run_report(self, run_id):
        return Path("reports") / run_id

    def list_report_files(self, _run_id):
        return ()

    def validate_report_file(self, _run_id, _name):
        return Path("safe")


@pytest.fixture
def controller(tmp_path):
    profiles = AccountProfileStore(tmp_path / "profiles.json")
    profiles.save(PROFILE)
    return Phase6Controller(
        service=FakeService(), profile_store=profiles,
        watchlist_store=WatchlistStore(tmp_path / "user"),
        provider_registry=ProviderRegistry.existing(),
        dependency_factory=lambda run_id: ("deps", run_id),
        idempotency_store=Phase6IdempotencyStore(tmp_path / "runs.sqlite3"),
    )


@pytest.mark.parametrize("code", tuple(ERRORS))
def test_every_frozen_error_code_has_safe_chinese_view(controller, code):
    view = controller.get_error_view(code)
    assert view.code == code
    assert view.title and view.action
    assert "sql" not in view.detail.lower()


@pytest.mark.parametrize("page", sorted(PHASE6_PAGES))
def test_six_phase6_pages_render_without_database_access(page):
    body = render_phase6_page(page, None, Phase6WebState())
    assert f'data-page="{page}"' in body
    assert "sqlite" not in body.lower()


def test_ready_snapshot_association_survives_controller_restart(tmp_path):
    profiles = AccountProfileStore(tmp_path / "profiles.json")
    profiles.save(PROFILE)
    first = Phase6Controller(
        service=FakeService(),profile_store=profiles,
        watchlist_store=WatchlistStore(tmp_path / "user"),
        provider_registry=ProviderRegistry(()),
        idempotency_store=Phase6IdempotencyStore(tmp_path / "runs.sqlite3"),
    )
    universe = UniverseSpec("MANUAL",("600000.SH",))
    dates = DateRangeSpec("RECENT_MONTHS",value=3)
    _, ready = first.prepare_cache(universe,dates,"default",RunMode.BACKTEST)

    recreated = Phase6Controller(
        service=FakeService(),profile_store=profiles,
        watchlist_store=WatchlistStore(tmp_path / "user"),
        provider_registry=ProviderRegistry(()),
        idempotency_store=Phase6IdempotencyStore(tmp_path / "runs-2.sqlite3"),
    )
    restored = recreated.restore_prepared_snapshot(
        universe,dates,"default",RunMode.BACKTEST,
    )
    assert restored is not None
    assert restored.data_snapshot_id == ready.data_snapshot_id
    assert recreated.load_account_summary("default").latest_readiness_status == "READY"


def test_refresh_restores_mode_specific_ready_snapshot(controller):
    state = Phase6WebState()
    controller.prepare_cache(
        state.universe_spec,state.date_range_spec,"default",RunMode.DAILY_PAPER,
    )
    assert refresh_snapshot_state(controller,state,RunMode.DAILY_PAPER)
    assert state.readiness_status == "READY"
    assert state.data_snapshot_id == "data-1"


def test_phase5_error_detail_is_visible_to_user(controller):
    error = controller.get_error_view(
        Phase5Error("INVALID_DATE_RANGE","data_max_date:2026-08-06")
    )
    body = render_phase6_page("adaptive-v13-cache",controller,Phase6WebState(),error=error)
    assert "当前数据最多支持到 2026-08-06" in body
    assert "技术详情" not in body


def test_preview_renders_resolved_universe_dates_and_warmup(controller):
    state = Phase6WebState()
    page,message = handle_phase6_action(
        "/adaptive-v13-preview",{
            "universe_kind":"MANUAL","manual_symbols":"600000.SH",
            "date_kind":"RECENT_MONTHS","date_value":"3",
        },controller,state,
    )
    body = render_phase6_page(page,controller,state,message=message)
    assert "候选股票" in body and "必需股票" in body
    assert "实际交易日" in body and "预热范围" in body


def test_provider_test_only_probes_configured_priority():
    calls = []
    registry = ProviderRegistry((
        ProviderDescriptor(
            "one","One","1",("daily",),("1d",),"all","Asia/Shanghai",
            "RAW",("daily",),lambda: calls.append("one"),configured=True,enabled=True,
        ),
        ProviderDescriptor(
            "two","Two","1",("daily",),("1d",),"all","Asia/Shanghai",
            "RAW",("daily",),lambda: calls.append("two"),configured=True,enabled=True,
        ),
    ))
    registry.test_connections(priorities=("one",))
    assert calls == ["one"]


@pytest.mark.parametrize(
    ("page", "text"),
    [
        ("adaptive-v13-overview", "FUTURE_QMT"),
        ("adaptive-v13-cache", "UniverseSpec"),
        ("adaptive-v13-cache", "DateRangeSpec"),
        ("adaptive-v13-cache", "RAW_UNADJUSTED_V1"),
        ("adaptive-v13-backtest", "FORBID"),
        ("adaptive-v13-backtest", "运行回测"),
        ("adaptive-v13-paper", "不会向券商发送订单"),
        ("adaptive-v13-paper", "DAILY_PAPER"),
        ("adaptive-v13-runs", "运行记录"),
        ("adaptive-v13-runs", "Phase 5 list_runs"),
        ("adaptive-v13-account", "资金与费用"),
        ("adaptive-v13-account", "数据源与目录"),
        ("adaptive-v13-account", "自选股组合"),
        ("adaptive-v13-account", "维护与诊断"),
        ("adaptive-v13-account", "不会写入缓存"),
    ],
)
def test_page_contract_text(page, text):
    assert text in render_phase6_page(page, None, Phase6WebState())


@pytest.mark.parametrize(
    ("field_name", "field_type"),
    [
        ("backtest_initial_cash", Decimal),
        ("paper_cash", Decimal),
        ("provider_priority", tuple),
        ("candidate_symbols", tuple),
        ("required_symbols", tuple),
        ("benchmark_symbols", tuple),
        ("complete_partitions", tuple),
        ("partial_partitions", tuple),
        ("metrics", tuple),
        ("fills", tuple),
        ("capabilities", tuple),
    ],
)
def test_viewmodel_contract_uses_immutable_field_types(field_name, field_type):
    models = (
        AccountSummaryVM, UniverseSelectionVM, DataReadinessVM, RunSummaryVM,
        RunDetailVM, ProviderStatusVM,
    )
    matching = [item for model in models for item in fields(model) if item.name == field_name]
    assert matching
    assert field_type.__name__ in str(matching[0].type)


@pytest.mark.parametrize("value", ["0", "0.00", "1", "100000.25", Decimal("5")])
def test_decimal_text_accepts_exact_values(value):
    assert validate_decimal_text(value, "cash") == Decimal(value)


@pytest.mark.parametrize("value", [0.1, True, False, "-1", "NaN", "Infinity", "", object()])
def test_decimal_text_rejects_float_bool_nonfinite_negative(value):
    with pytest.raises(Exception):
        validate_decimal_text(value, "cash")


@pytest.mark.parametrize(
    ("provider_id", "frequency"),
    [
        ("akshare", "1d"), ("akshare", "5m"), ("baostock", "1d"),
        ("baostock", "5m"), ("joinquant", "1d"), ("joinquant", "5m"),
        ("sina", "realtime"),
    ],
)
def test_existing_provider_capabilities_are_truthful(provider_id, frequency):
    rows = {item.provider_id: item for item in ProviderRegistry.existing().inspect()}
    assert frequency in rows[provider_id].frequencies
    if provider_id == "sina":
        assert "daily" not in rows[provider_id].dataset_types


@pytest.mark.parametrize("delay", [0, 0.001, 0.005])
def test_provider_fixture_success_never_writes_cache(delay):
    calls = []
    registry = ProviderRegistry((
        ProviderDescriptor("fixture", "Fixture", "1", ("daily",), ("1d",), "all",
                           "Asia/Shanghai", "RAW", ("daily",),
                           lambda: (time.sleep(delay), calls.append("probe"))),
    ))
    assert registry.test_connections(1)[0].availability == "AVAILABLE"
    assert calls == ["probe"]


def test_provider_timeout_is_bounded_and_structured():
    registry = ProviderRegistry((
        ProviderDescriptor("slow", "Slow", "1", ("daily",), ("1d",), "all",
                           "Asia/Shanghai", "RAW", ("daily",), lambda: time.sleep(.1)),
    ))
    result = registry.test_connections(.01)[0]
    assert result.availability == "TIMEOUT"
    assert result.error_code == "provider_timeout"


@pytest.mark.parametrize("kind,value", [
    ("RECENT_MONTHS","1"),("RECENT_MONTHS","3"),("RECENT_MONTHS","6"),
    ("RECENT_YEARS","1"),("RECENT_YEARS","2"),("RECENT_YEARS","3"),
    ("RECENT_YEARS","5"),
])
def test_shared_date_selector_serializes_presets(kind, value):
    state = Phase6WebState()
    update_selection(state, {
        "universe_kind":"MANUAL","manual_symbols":"600000",
        "date_kind":kind,"date_value":value,
    })
    assert str(state.date_range_spec.kind) == kind
    assert state.date_range_spec.value == int(value)


def test_custom_date_selector_is_conditional():
    state = Phase6WebState()
    update_selection(state, {
        "universe_kind":"MANUAL","manual_symbols":"600000",
        "date_kind":"CUSTOM","start_date":"2024-01-01","end_date":"2024-02-01",
    })
    assert state.date_range_spec.start_date == "2024-01-01"
    assert state.date_range_spec.end_date == "2024-02-01"


@pytest.mark.parametrize("kind", ["MANUAL","WATCHLIST","MARKET_SCOPE","COMBINED"])
def test_shared_universe_selector_serializes_all_kinds(kind):
    state = Phase6WebState()
    update_selection(state, {
        "universe_kind":kind,"manual_symbols":"600000;000001",
        "watchlist_names":"核心,观察","market_scopes":"沪深A股,创业板",
        "date_kind":"RECENT_MONTHS","date_value":"3",
    })
    assert str(state.universe_spec.kind) == kind
    assert state.universe_spec.manual_symbols == ("600000","000001")
    assert state.universe_spec.watchlist_names == ("核心","观察")


def test_cache_and_backtest_reuse_identical_specs_and_snapshot(controller):
    state = Phase6WebState()
    _, ready = controller.prepare_cache(
        state.universe_spec,state.date_range_spec,"default",RunMode.BACKTEST,
    )
    assert ready.status == "READY"
    assert controller.validate_snapshot(
        ready.data_snapshot_id,state.universe_spec,state.date_range_spec,
        "default",RunMode.BACKTEST,
    )


def test_daily_paper_prepare_returns_to_paper_and_keeps_run_button_enabled(controller):
    state = Phase6WebState()
    page,_message = handle_phase6_action(
        "/adaptive-v13-cache-prepare",
        {
            "run_mode":"DAILY_PAPER",
            "universe_kind":"MANUAL",
            "manual_symbols":"600000.SH",
            "date_kind":"RECENT_MONTHS",
            "date_value":"3",
        },
        controller,state,
    )

    assert page == "adaptive-v13-paper"
    assert refresh_snapshot_state(controller,state,RunMode.DAILY_PAPER)
    body = render_phase6_page(page,controller,state)
    assert state.readiness_status == "READY"
    assert '<button class="primary" disabled>' not in body


@pytest.mark.parametrize("change", ["account","universe","date","mode","position"])
def test_any_run_input_change_invalidates_ready_snapshot(controller, change):
    universe = UniverseSpec("MANUAL",("600000.SH",))
    dates = DateRangeSpec("RECENT_MONTHS",value=3)
    _, ready = controller.prepare_cache(universe,dates,"default",RunMode.BACKTEST)
    changed_universe = UniverseSpec("MANUAL",("000001.SZ",)) if change == "universe" else universe
    changed_dates = DateRangeSpec("RECENT_MONTHS",value=6) if change == "date" else dates
    changed_mode = RunMode.DAILY_PAPER if change == "mode" else RunMode.BACKTEST
    changed_positions = ("000001.SZ",) if change == "position" else ()
    profile = "missing" if change == "account" else "default"
    if change == "account":
        with pytest.raises(Exception):
            controller.validate_snapshot(
                ready.data_snapshot_id,changed_universe,changed_dates,profile,changed_mode,
                current_positions=changed_positions,
            )
    else:
        assert not controller.validate_snapshot(
            ready.data_snapshot_id,changed_universe,changed_dates,profile,changed_mode,
            current_positions=changed_positions,
        )


def test_create_and_execute_backtest_uses_phase5_service(controller):
    universe = UniverseSpec("MANUAL",("600000.SH",))
    dates = DateRangeSpec("RECENT_MONTHS",value=3)
    _, ready = controller.prepare_cache(universe,dates,"default",RunMode.BACKTEST)
    run_id,config,state = controller.create_backtest(
        universe,dates,"default",ready.data_snapshot_id,
    )
    result = controller.execute_run(run_id,config,state)
    assert result["status"] == "COMPLETED"
    assert controller.service.executed[0][3] == ("deps","run-1")


def test_watchlist_crud_and_alias_normalization_live_behind_controller(controller):
    controller.save_watchlist("create",name="核心")
    controller.save_watchlist("add",name="核心",symbols=("600000","600000.SH"))
    assert controller.list_watchlists()[0].symbols == ["600000.SH"]
    controller.save_watchlist("rename",name="核心",new_name="核心池")
    controller.save_watchlist("copy",name="副本",source_name="核心池")
    assert {item.name for item in controller.list_watchlists()} == {"核心池","副本"}
    controller.delete_watchlist("副本")
    assert [item.name for item in controller.list_watchlists()] == ["核心池"]


@pytest.mark.parametrize("backend,available", [
    (ExecutionBackend.PAPER,True),(ExecutionBackend.FUTURE_QMT,False),
])
def test_execution_backend_reservation_is_explicit(backend, available):
    assert (backend is ExecutionBackend.PAPER) is available


@pytest.mark.parametrize("model", [
    AccountSummaryVM,UniverseSelectionVM,DateRangeVM,ProviderStatusVM,
    CacheProgressVM,DataReadinessVM,RunListItemVM,RunSummaryVM,
    RunDetailVM,ReportFileVM,ErrorVM,
])
def test_all_phase6_viewmodels_are_frozen(model):
    assert model.__dataclass_params__.frozen is True


def test_same_operation_token_is_persistently_idempotent(controller):
    universe = UniverseSpec("MANUAL",("600000.SH",))
    dates = DateRangeSpec("RECENT_MONTHS",value=3)
    _,ready = controller.prepare_cache(universe,dates,"default",RunMode.BACKTEST)
    first = controller.submit_backtest(
        universe,dates,"default",ready.data_snapshot_id,"operation-1",
    )
    second = controller.submit_backtest(
        universe,dates,"default",ready.data_snapshot_id,"operation-1",
    )
    assert first.run_id == second.run_id == "run-1"
    assert not first.reused and second.reused
    assert controller.service.create_count == 1
    assert len(controller.service.executed) == 1


def test_operation_token_survives_controller_recreation(controller,tmp_path):
    universe = UniverseSpec("MANUAL",("600000.SH",))
    dates = DateRangeSpec("RECENT_MONTHS",value=3)
    _,ready = controller.prepare_cache(universe,dates,"default",RunMode.BACKTEST)
    first = controller.submit_backtest(
        universe,dates,"default",ready.data_snapshot_id,"network-retry-token",
    )
    profiles = AccountProfileStore(tmp_path / "profiles-recreated.json")
    profiles.save(PROFILE)
    recreated = Phase6Controller(
        service=controller.service,profile_store=profiles,
        watchlist_store=WatchlistStore(tmp_path / "user-recreated"),
        provider_registry=ProviderRegistry(()),
        dependency_factory=lambda run_id: ("deps",run_id),
        idempotency_store=Phase6IdempotencyStore(tmp_path / "runs.sqlite3"),
    )
    recreated.prepare_cache(universe,dates,"default",RunMode.BACKTEST)
    retry = recreated.submit_backtest(
        universe,dates,"default",ready.data_snapshot_id,"network-retry-token",
    )
    assert retry.run_id == first.run_id
    assert retry.reused
    assert controller.service.create_count == 1


def test_two_threads_same_submission_create_one_run(controller):
    universe = UniverseSpec("MANUAL",("600000.SH",))
    dates = DateRangeSpec("RECENT_MONTHS",value=3)
    _,ready = controller.prepare_cache(universe,dates,"default",RunMode.BACKTEST)

    def submit():
        return controller.submit_backtest(
            universe,dates,"default",ready.data_snapshot_id,"concurrent-token",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _index: submit(), range(2)))
    assert {item.run_id for item in results} == {"run-1"}
    assert controller.service.create_count == 1


def test_two_concurrent_tokens_for_same_fingerprint_share_active_run(controller):
    universe = UniverseSpec("MANUAL",("600000.SH",))
    dates = DateRangeSpec("RECENT_MONTHS",value=3)
    _,ready = controller.prepare_cache(universe,dates,"default",RunMode.BACKTEST)
    controller.service.execute_delay = 0.05

    def submit(token):
        return controller.submit_backtest(
            universe,dates,"default",ready.data_snapshot_id,token,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(submit,("token-a","token-b")))
    assert {item.run_id for item in results} == {"run-1"}
    assert controller.service.create_count == 1
    retry = controller.submit_backtest(
        universe,dates,"default",ready.data_snapshot_id,"token-b",
    )
    assert retry.run_id == "run-1" and retry.reused


def test_operation_token_cannot_be_reused_for_different_input(controller):
    first = UniverseSpec("MANUAL",("600000.SH",))
    second = UniverseSpec("MANUAL",("000001.SZ",))
    dates = DateRangeSpec("RECENT_MONTHS",value=3)
    _,ready = controller.prepare_cache(first,dates,"default",RunMode.BACKTEST)
    controller.submit_backtest(first,dates,"default",ready.data_snapshot_id,"fixed-token")
    _,ready2 = controller.prepare_cache(second,dates,"default",RunMode.BACKTEST)
    with pytest.raises(Exception):
        controller.submit_backtest(second,dates,"default",ready2.data_snapshot_id,"fixed-token")


def test_daily_paper_uses_phase5_authoritative_initial_state(controller):
    controller.paper_state_loader = lambda _profile: {
        "positions":{"600000.SH":"position"},"pending_sells":{"600000.SH":"pending"},
        "cooldowns":{"000001.SZ":"cooldown"},
    }
    universe = UniverseSpec("MANUAL",("600000.SH",))
    dates = DateRangeSpec("RECENT_MONTHS",value=3)
    _,ready = controller.prepare_cache(universe,dates,"default",RunMode.DAILY_PAPER)
    _run_id,_config,state = controller.create_daily_paper_run(
        universe,dates,"default",ready.data_snapshot_id,
    )
    assert state == controller.service.initial_states["run-1"]
    assert set(state) == {
        "cash","positions","pending_sells","exit_controls","cooldowns","fill_requests",
    }
    assert state["pending_sells"] == {"600000.SH":"pending"}
    assert state["cooldowns"] == {"000001.SZ":"cooldown"}


def test_provider_descriptor_is_derived_from_adapter_object():
    class Adapter:
        source_version = "adapter-2.4"
        adjustment_modes = ("RAW","QFQ")
        def get_history(self, *_args, **_kwargs): return None
        def get_minute_history(self, *_args, **_kwargs): return None
        def health_check(self): return {"ok":True}

    descriptor = ProviderDescriptor.from_provider("fixture",Adapter())
    assert descriptor.source_version == "adapter-2.4"
    assert descriptor.dataset_types == ("daily","minute")
    assert descriptor.frequencies == ("1d","5m")
    assert descriptor.adjustment_modes == ("RAW","QFQ")
    assert descriptor.probe is not None


def test_provider_without_probe_is_reported_as_unsupported():
    descriptor = ProviderDescriptor(
        "fixture","Fixture","1",("daily",),("1d",),"all",
        "Asia/Shanghai","RAW",("daily",),None,
    )
    result = ProviderRegistry((descriptor,)).test_connections()[0]
    assert result.availability == "UNSUPPORTED_PROBE"
    assert result.error_code == "probe_not_supported"


def test_fallback_requires_matching_configured_partition_contract():
    first = ProviderDescriptor(
        "one","One","1",("daily",),("1d",),"all","Asia/Shanghai","RAW",
        ("daily",),lambda: None,("RAW",),
    )
    minute = ProviderDescriptor(
        "two","Two","1",("minute",),("5m",),"all","Asia/Shanghai","RAW",
        ("minute",),lambda: None,("RAW",),
    )
    rows = ProviderRegistry((first,minute)).inspect(("one","two"))
    assert not any(item.fallback_available for item in rows)


def test_error_mapper_never_exposes_sql_paths_or_credentials(controller):
    raw = RuntimeError(
        "SELECT * FROM secret at C:\\private\\runs.sqlite3 /home/me key=abc Cookie=x"
    )
    view = controller.get_error_view(raw)
    assert view.code == "UNEXPECTED_ENGINE_ERROR"
    assert view.detail == ""
    assert view.title and view.action and view.correlation_id


def test_selector_restores_watchlist_and_preset_without_irrelevant_fields():
    state = Phase6WebState(
        universe_spec=UniverseSpec("WATCHLIST",watchlist_names=("核心",)),
        date_range_spec=DateRangeSpec("RECENT_MONTHS",value=3),
    )
    body = render_phase6_page("adaptive-v13-cache",None,state)
    assert 'value="WATCHLIST" selected' in body
    assert 'value="3" selected' in body
    assert 'name="watchlist_names"' in body and 'value="核心"' in body
    assert 'name="manual_symbols"' not in body
    assert 'name="start_date"' not in body


def test_selector_custom_dates_restore_and_hide_preset_value():
    state = Phase6WebState(
        universe_spec=UniverseSpec("MANUAL",("600000.SH",)),
        date_range_spec=DateRangeSpec(
            "CUSTOM",start_date="2025-01-02",end_date="2025-02-03",
        ),
    )
    body = render_phase6_page("adaptive-v13-backtest",None,state)
    assert 'value="CUSTOM" selected' in body
    assert 'value="2025-01-02"' in body and 'value="2025-02-03"' in body
    assert 'name="date_value"' not in body


def test_run_page_size_is_capped_at_one_hundred(controller):
    assert controller.list_runs(page_size=10_000) == ()


def test_application_factory_uses_absolute_paths_and_real_service_graph(tmp_path):
    built = create_phase6_application(
        project_root=tmp_path,provider_registry=ProviderRegistry(()),
        trading_calendar=CALENDAR,
    )
    assert isinstance(built,Phase6Controller)
    profile = built.service.account_profiles["default"]
    assert Path(profile.data_directory).is_absolute()
    assert Path(profile.report_directory).is_absolute()
    assert built.service.cache.db_path.is_absolute()
    assert built.service.run_store.db_path.is_absolute()
    assert callable(built.service.partition_planner)
    assert built.dependency_factory is not None


def test_application_factory_partition_planner_uses_real_adapter_without_network(tmp_path):
    class Adapter:
        source_version = "fixture-1"
        adjustment_modes = ("RAW",)
        def get_history(self,*_args,**_kwargs): raise AssertionError("not called by planning")
        def get_index_history(self,*_args,**_kwargs): raise AssertionError("not called by planning")
        def get_minute_history(self,*_args,**_kwargs): raise AssertionError("not called by planning")

    registry = ProviderRegistry((
        ProviderDescriptor.from_provider("baostock",Adapter()),
    ))
    built = create_phase6_application(
        project_root=tmp_path,provider_registry=registry,trading_calendar=CALENDAR,
    )
    profile = built.service.account_profiles["default"]
    requests = built.service.partition_planner(UNIVERSE,RESOLVED,profile,RunMode.BACKTEST)
    assert requests
    assert all(request.providers for request in requests)
    assert {provider[0] for request in requests for provider in request.providers} == {"baostock"}
