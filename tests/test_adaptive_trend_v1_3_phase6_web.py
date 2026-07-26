from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location(
    "phase6_web_app", Path(__file__).parents[1] / "examples" / "web_app.py"
)
web_app = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(web_app)


@pytest.mark.parametrize("page", [
    "thermostat","backtest","portfolio",
    "adaptive-v13-overview","adaptive-v13-cache","adaptive-v13-backtest",
    "adaptive-v13-paper","adaptive-v13-runs","adaptive-v13-account",
])
def test_legacy_and_phase6_routes_render(page):
    body = web_app.render_page(page=page,form={})
    assert "<!doctype html>" in body
    assert f'href="/{page}"' in body


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


def test_phase6_post_exceptions_never_render_raw_exception_text():
    source = Path(web_app.__file__).read_text(encoding="utf-8")
    start = source.index("if failed_page in PHASE6_PAGES:")
    end = source.index("            else:",start)
    phase6_error_branch = source[start:end]
    assert "phase6_error=view" in phase6_error_branch
    assert "str(exc)" not in phase6_error_branch
