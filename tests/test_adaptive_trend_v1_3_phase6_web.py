from __future__ import annotations

import importlib.util
from pathlib import Path
import threading
from types import SimpleNamespace
from urllib.request import Request, urlopen

import pytest

from stock_picker.strategies.adaptive_trend_v1_3.phase6_profile_store import (
    Phase6PreferenceStore,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase6_web import (
    PHASE6_LABELS,
    Phase6WebState,
    handle_phase6_action,
)


SPEC = importlib.util.spec_from_file_location(
    "phase6_web_app", Path(__file__).parents[1] / "examples" / "web_app.py"
)
web_app = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(web_app)


@pytest.mark.parametrize("page", [
    "adaptive-v13-overview","adaptive-v13-cache","adaptive-v13-backtest",
    "adaptive-v13-paper","adaptive-v13-runs","adaptive-v13-account",
])
def test_default_navigation_renders_each_phase6_route(page):
    body = web_app.render_page(page=page,form={})
    assert "<!doctype html>" in body
    assert f'href="/{page}"' in body


@pytest.mark.parametrize("page", ["thermostat","backtest","portfolio"])
def test_legacy_pages_remain_renderable_when_explicitly_enabled(page):
    body = web_app.render_page(
        page=page, form={}, legacy_features_visible=True,
    )
    assert "<!doctype html>" in body
    assert f'href="/{page}"' in body
    assert "旧版/实验功能" in body


def test_default_navigation_contains_exactly_six_adaptive_pages():
    body = web_app.render_page(page="adaptive-v13-overview",form={})
    assert body.count('<div class="nav-group">') == 1
    for page,label in PHASE6_LABELS:
        assert body.count(f'href="/{page}"') == 1
        assert label in body
    for page in ("thermostat","backtest","portfolio"):
        assert f'href="/{page}"' not in body
    assert "旧版/实验功能" not in body


def test_enabled_navigation_keeps_legacy_links_in_independent_group():
    body = web_app.render_page(
        page="adaptive-v13-overview",form={},legacy_features_visible=True,
    )
    assert '<div class="nav-group legacy-nav">' in body
    assert '<span class="nav-title">旧版/实验功能</span>' in body
    for page in ("thermostat","backtest","portfolio"):
        assert body.count(f'href="/{page}"') == 1


def test_unknown_page_falls_back_to_adaptive_overview():
    body = web_app.render_page(page="unknown",form={})
    assert 'data-page="adaptive-v13-overview"' in body
    assert 'action="/thermostat-job"' not in body


def test_preference_defaults_false_for_every_new_account(tmp_path):
    store = Phase6PreferenceStore(tmp_path / "preferences.json")
    assert store.show_legacy_experimental("default") is False
    assert store.show_legacy_experimental("new-account") is False
    assert not store.path.exists()


def test_preference_persists_and_is_account_scoped(tmp_path):
    path = tmp_path / "preferences.json"
    Phase6PreferenceStore(path).set_show_legacy_experimental("default",True)
    recreated = Phase6PreferenceStore(path)
    assert recreated.show_legacy_experimental("default") is True
    assert recreated.show_legacy_experimental("other") is False
    recreated.set_show_legacy_experimental("default",False)
    assert Phase6PreferenceStore(path).show_legacy_experimental("default") is False


def test_account_page_places_persisted_switch_under_maintenance():
    class Controller:
        def load_account_summary(self, *_args, **_kwargs): raise RuntimeError
        def list_watchlists(self): return ()
        def inspect_provider_status(self, *_args): return ()
        def show_legacy_experimental(self, _account): return True

    from stock_picker.strategies.adaptive_trend_v1_3.phase6_web import render_phase6_page
    body = render_phase6_page(
        "adaptive-v13-account",Controller(),Phase6WebState(),
    )
    maintenance = body[body.index("维护与诊断"):]
    assert 'action="/adaptive-v13-legacy-settings"' in maintenance
    assert 'name="show_legacy_experimental"' in maintenance
    assert 'value="true" checked' in maintenance
    assert "旧版功能仅用于兼容和排查，不属于当前自适应趋势 V1.3 工作流。默认隐藏。" in maintenance


@pytest.mark.parametrize(("submitted","expected"), [
    ("true",True),("",False),
])
def test_visibility_action_persists_without_changing_session_state(
    submitted,expected,
):
    class Controller:
        captured = None
        def set_show_legacy_experimental(self, account, enabled):
            self.captured = (account,enabled)

    controller = Controller()
    state = Phase6WebState(
        account_profile_id="paper",data_snapshot_id="snapshot",
        run_id="run",readiness_status="READY",
    )
    page,_ = handle_phase6_action(
        "/adaptive-v13-legacy-settings",
        {"show_legacy_experimental":submitted},
        controller,state,
    )
    assert page == "adaptive-v13-account"
    assert controller.captured == ("paper",expected)
    assert (state.data_snapshot_id,state.run_id,state.readiness_status) == (
        "snapshot","run","READY",
    )


class _VisibilityController:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def show_legacy_experimental(self, _account: str) -> bool:
        return self.enabled

    def list_runs(self, **_kwargs):
        return ()


def _request_web_app(path: str, *, method: str = "GET") -> tuple[int,str]:
    server = web_app.ThreadingHTTPServer(("127.0.0.1",0),web_app.WebAppHandler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_address[1]}{path}",
            data=b"" if method == "POST" else None,
            method=method,
        )
        with urlopen(request,timeout=10) as response:
            return response.status,response.read().decode("utf-8")
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.parametrize("path", [
    "/thermostat","/backtest","/portfolio","/job?id=missing",
    "/thermostat-report?id=missing",
])
def test_hidden_legacy_get_routes_are_not_404_and_do_not_enable_feature(
    monkeypatch,path,
):
    controller = _VisibilityController(False)
    monkeypatch.setattr(web_app,"PHASE6_CONTROLLER",controller)
    status,body = _request_web_app(path)
    assert status == 200
    assert 'data-page="adaptive-v13-overview"' in body
    assert "旧版/实验功能当前已隐藏" in body
    assert controller.enabled is False
    assert 'href="/thermostat"' not in body


@pytest.mark.parametrize("path", [
    "/thermostat","/thermostat-backtest","/portfolio-summary",
    "/watchlist-create",
])
def test_hidden_legacy_post_routes_do_not_execute_and_are_not_404(
    monkeypatch,path,
):
    controller = _VisibilityController(False)
    monkeypatch.setattr(web_app,"PHASE6_CONTROLLER",controller)
    status,body = _request_web_app(path,method="POST")
    assert status == 200
    assert "旧版/实验功能当前已隐藏" in body
    assert controller.enabled is False


def test_root_route_is_adaptive_overview_even_when_legacy_enabled(monkeypatch):
    monkeypatch.setattr(web_app,"PHASE6_CONTROLLER",_VisibilityController(True))
    status,body = _request_web_app("/")
    assert status == 200
    assert 'data-page="adaptive-v13-overview"' in body
    assert "旧版/实验功能" in body


def test_v1318_spec_is_present_in_root():
    path = (
        Path(web_app.__file__).parents[1]
        / "T1软适应中短期趋势系统_V1.3.18_Phase6旧版功能默认隐藏修复与Codex执行指令.txt"
    )
    assert path.is_file()
    assert path.stat().st_size > 4000


@pytest.mark.parametrize("token", [
    "--accent","--ready","status-chip","account-summary","card-grid",
    "selector-grid","table-scroll","button:disabled",
])
def test_phase6_uses_central_design_tokens(token):
    assert token in web_app.CSS


@pytest.mark.parametrize("forbidden", [
    "import sqlite3","sqlite3.connect","SELECT * FROM","INSERT INTO","UPDATE adaptive_v13",
])
def test_web_entrypoint_does_not_directly_access_phase5_sqlite(forbidden):
    source = Path(web_app.__file__).read_text(encoding="utf-8")
    assert forbidden not in source


def test_two_session_state_instances_are_isolated():
    from stock_picker.strategies.adaptive_trend_v1_3.phase6_web import Phase6WebState
    first,second = Phase6WebState(),Phase6WebState()
    first.data_snapshot_id = "data-a"
    first.run_id = "run-a"
    assert second.data_snapshot_id == ""
    assert second.run_id == ""


def test_old_session_keys_are_not_reused_by_phase6():
    assert web_app.PHASE6_STATE is not web_app.LAST_FORM
    assert not isinstance(web_app.PHASE6_STATE,dict)


def test_phase6_spec_is_present_verbatim_in_root():
    path = Path(__file__).parents[1] / "T1软适应中短期趋势系统_V1.3.16_Phase6网页端真实数据接入与运行管理一次性完整规格.txt"
    assert path.is_file()
    assert path.stat().st_size > 10000


def test_phase6_v1317_closure_spec_is_present_in_root():
    matches = tuple(Path(web_app.__file__).parents[1].glob("*V1.3.17*Phase6*.txt"))
    assert len(matches) == 1
    assert matches[0].stat().st_size > 5000


def test_default_startup_formally_configures_phase6_controller(monkeypatch):
    sentinel = object()
    old_controller = web_app.PHASE6_CONTROLLER
    monkeypatch.setattr(web_app,"create_phase6_application",lambda **_kwargs: sentinel)
    try:
        assert web_app.initialize_phase6() is sentinel
        assert web_app.PHASE6_CONTROLLER is sentinel
        assert web_app.PHASE6_STARTUP_ERROR is None
    finally:
        web_app.PHASE6_CONTROLLER = old_controller


def test_startup_failure_is_structured_and_legacy_page_remains_available(monkeypatch):
    old_controller = web_app.PHASE6_CONTROLLER
    monkeypatch.setattr(
        web_app,"create_phase6_application",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("C:\\secret\\db.sqlite SELECT")),
    )
    try:
        web_app.PHASE6_CONTROLLER = None
        assert web_app.initialize_phase6() is None
        body = web_app.render_page(
            page="adaptive-v13-cache",form={},
            phase6_error=web_app.PHASE6_STARTUP_ERROR,
        )
        assert "INVALID_CONFIG" in body
        assert "C:\\secret" not in body and "SELECT" not in body
        assert "<!doctype html>" in web_app.render_page(page="thermostat",form={})
    finally:
        web_app.PHASE6_CONTROLLER = old_controller
        web_app.PHASE6_STARTUP_ERROR = None


def test_run_filters_are_forwarded_and_selected():
    from stock_picker.strategies.adaptive_trend_v1_3.phase6_web import (
        Phase6WebState, render_phase6_page,
    )

    class Controller:
        captured = None
        def load_account_summary(self, *_args, **_kwargs): raise RuntimeError
        def list_runs(self, **kwargs):
            self.captured = kwargs
            return ()

    controller = Controller()
    state = Phase6WebState(run_filters={
        "mode":"BACKTEST","status":"FAILED","date_from":"2025-01-01",
        "date_to":"2025-02-01","account":"paper","strategy_version":"V1.3.13",
        "has_open_positions":"true","degraded":"false","page":"2","page_size":"500",
    })
    body = render_phase6_page("adaptive-v13-runs",controller,state)
    assert controller.captured == {
        "mode":"BACKTEST","status":"FAILED","date_from":"2025-01-01",
        "date_to":"2025-02-01","account":"paper","strategy_version":"V1.3.13",
        "has_open_positions":True,"degraded":False,"page":2,"page_size":100,
    }
    assert 'value="BACKTEST" selected' in body
    assert 'name="page_size" value="100"' in body


def test_recoverable_failed_run_has_real_resume_form():
    from stock_picker.strategies.adaptive_trend_v1_3.phase6_web import (
        Phase6WebState, render_phase6_page,
    )

    class Controller:
        def load_account_summary(self, *_args, **_kwargs): raise RuntimeError
        def list_runs(self, **_kwargs): return ()
        def load_run_detail(self, _run_id):
            return SimpleNamespace(summary=SimpleNamespace(status="FAILED"))
        def can_resume_run(self, _run_id): return True

    state = Phase6WebState(run_id="run-failed")
    body = render_phase6_page("adaptive-v13-runs",Controller(),state)
    assert 'action="/adaptive-v13-resume"' in body
    assert 'name="run_id" value="run-failed"' in body
    assert 'name="operation_token"' in body


def test_completed_run_detail_renders_results_and_report_action():
    from stock_picker.strategies.adaptive_trend_v1_3.phase6_web import (
        Phase6WebState, render_phase6_page,
    )

    class Controller:
        def load_account_summary(self, *_args, **_kwargs): raise RuntimeError
        def list_runs(self, **_kwargs): return ()
        def can_resume_run(self, _run_id): return False
        def list_report_files(self, _run_id): return ()
        def load_run_detail(self, _run_id):
            summary = SimpleNamespace(
                status="COMPLETED",mode="DAILY_PAPER",strategy_version="V1.3.13",
                date_range=("2026-05-06","2026-08-06"),
                price_basis_id="RAW_UNADJUSTED_V1",
                metrics=(("cash","100000"),("equity","100000")),
            )
            return SimpleNamespace(
                summary=summary,decisions=((),()),orders=(),fills=(),positions=(),
            )

    state = Phase6WebState(run_id="run-completed")
    body = render_phase6_page("adaptive-v13-runs",Controller(),state)
    assert "运行结果" in body
    assert "DAILY_PAPER" in body
    assert "模拟现金" in body and "100000" in body
    assert "决策" in body and ">2<" in body
    assert 'action="/adaptive-v13-report"' in body
    assert 'name="run_id" value="run-completed"' in body
    assert "决策结果汇总" in body
    assert "本次没有生成订单" in body
    assert "最近日终权益" in body


def test_phase6_selectors_have_immediate_refresh_hooks():
    from stock_picker.strategies.adaptive_trend_v1_3.phase6_web import (
        Phase6WebState, render_phase6_page,
    )
    body = render_phase6_page("adaptive-v13-cache",None,Phase6WebState())
    assert body.count('onchange="refreshPhase6Selectors(this)"') >= 4
    assert 'window.location.href = "/adaptive-v13-cache"' in body


def test_account_page_has_single_authoritative_cash_fields_and_copy_source():
    from stock_picker.strategies.adaptive_trend_v1_3.phase6_web import (
        Phase6WebState, render_phase6_page,
    )
    body = render_phase6_page("adaptive-v13-account",None,Phase6WebState())
    assert body.count('name="backtest_initial_cash"') == 1
    assert body.count('name="paper_cash"') == 1
    assert 'name="source_name"' in body


def test_long_running_forms_show_immediate_progress_feedback():
    from stock_picker.strategies.adaptive_trend_v1_3.phase6_web import (
        Phase6WebState, render_phase6_page,
    )
    for page in ("adaptive-v13-cache","adaptive-v13-backtest","adaptive-v13-paper"):
        body = render_phase6_page(page,None,Phase6WebState())
        assert 'data-long-running="true"' in body
        assert "正在处理，请勿重复点击" in body


def test_phase6_post_exceptions_never_render_raw_exception_text():
    source = Path(web_app.__file__).read_text(encoding="utf-8")
    start = source.index("if failed_page in PHASE6_PAGES:")
    end = source.index("            else:",start)
    phase6_error_branch = source[start:end]
    assert "phase6_error=view" in phase6_error_branch
    assert "str(exc)" not in phase6_error_branch
