from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pandas as pd

from examples import web_app
from stock_picker.data.backtest_data import BacktestDataBundle
from stock_picker.strategies.thermostat_backtest import (
    RESULT_TABLE_COLUMNS,
    BacktestPrecision,
    T1ThermostatBacktestResult,
)


def _pool(source: str = "manual") -> SimpleNamespace:
    return SimpleNamespace(
        symbols=["600001.SH"],
        errors=[],
        warnings=[],
        should_stop=False,
        summary=SimpleNamespace(
            source=source,
            name=source,
            time_range="",
            source_detail="submitted",
            original_count=1,
            deduped_count=1,
            filtered_count=1,
            removed_count=0,
        ),
    )


def _result() -> T1ThermostatBacktestResult:
    frames = {name: pd.DataFrame(columns=columns) for name, columns in RESULT_TABLE_COLUMNS.items()}
    frames["summary"] = pd.DataFrame(
        [{
            "status": "completed",
            "initial_cash": 100000.0,
            "final_assets": 110000.0,
            "total_return": 0.1,
            "annualized_return": 0.2,
            "max_drawdown": -0.03,
            "sharpe_ratio": 1.25,
        }]
    )
    frames["equity_drawdown"] = pd.DataFrame(
        [
            {"date": "2026-01-01", "equity": 1.0, "drawdown": 0.0},
            {"date": "2026-01-02", "equity": 1.1, "drawdown": -0.02},
        ]
    )
    frames["data_quality"] = pd.DataFrame([{"issue_type": "cache_gap", "symbol": "600001.SH"}])
    frames["trend_batches"] = pd.DataFrame([{"symbol": "600001.SH", "status": "filled"}])
    frames["grid_layers"] = pd.DataFrame([{"symbol": "600001.SH", "status": "pending"}])
    frames["failed_cancelled_orders"] = pd.DataFrame([{"symbol": "600001.SH", "status": "failed"}])
    frames["pending_history"] = pd.DataFrame([{"symbol": "600001.SH", "status": "pending"}])
    return T1ThermostatBacktestResult(**frames)


def _patch_parser_dependencies(monkeypatch, *, source: str = "manual") -> object:
    service = object()
    monkeypatch.setattr(web_app, "_service", lambda form: service)
    monkeypatch.setattr(web_app, "_resolve_thermostat_stock_pool", lambda form, svc: _pool(source))
    monkeypatch.setattr(web_app, "_load_portfolio", lambda path: None)
    return service


def test_shared_parser_builds_daily_request_and_exact_manual_metadata(monkeypatch) -> None:
    service = _patch_parser_dependencies(monkeypatch)
    form = {
        "stock_pool_source": "manual",
        "symbols": "600001",
        "backtest_date_range": "custom",
        "start": "20260101",
        "end": "20260331",
        "cash": "120000",
        "trend_total_max": "0.66",
        "refresh": "1",
        "source": "baostock",
    }

    parsed = web_app.parse_t1_backtest_input(form)

    assert parsed.service is service
    assert parsed.data_request.period == "daily"
    assert parsed.data_request.indicator_adjust == "qfq"
    assert parsed.data_request.execution_adjust == "bfq"
    assert parsed.data_request.refresh is True
    assert parsed.precision is BacktestPrecision.DAILY_APPROXIMATE
    assert parsed.stock_pool_metadata == {
        "pool_type": "manual",
        "membership": "static",
        "generation_method": "as-submitted manual symbols",
        "look_ahead_selection_warning": "",
        "survivor_bias_warning": "",
    }
    assert parsed.runner_request.initial_cash == 120000.0
    assert parsed.runner_request.trend_total_base_max == 0.66


def test_parser_marks_market_and_lhb_static_biases(monkeypatch) -> None:
    _patch_parser_dependencies(monkeypatch, source="market_range")
    market = web_app.parse_t1_backtest_input(
        {"stock_pool_source": "market_range", "start": "20260101", "end": "20260331"}
    )
    assert market.stock_pool_metadata["membership"] == "static current snapshot"
    assert "survivor" in market.stock_pool_metadata["survivor_bias_warning"].lower()

    monkeypatch.setattr(web_app, "_resolve_thermostat_stock_pool", lambda form, svc: _pool("lhb"))
    lhb = web_app.parse_t1_backtest_input(
        {"stock_pool_source": "lhb", "start": "20260101", "end": "20260331"}
    )
    assert lhb.stock_pool_metadata["membership"] == "untrusted/static"
    assert "look-ahead" in lhb.stock_pool_metadata["look_ahead_selection_warning"].lower()


def test_parser_rejects_reversed_dates(monkeypatch) -> None:
    _patch_parser_dependencies(monkeypatch)
    try:
        web_app.parse_t1_backtest_input(
            {"stock_pool_source": "manual", "start": "20260331", "end": "20260101"}
        )
    except ValueError as exc:
        assert "start" in str(exc).lower()
    else:
        raise AssertionError("reversed interval was accepted")


def test_cache_action_counts_loader_warmup_code_separately_from_missing_data(monkeypatch) -> None:
    parsed_service = _patch_parser_dependencies(monkeypatch)
    calls = {"load": 0, "run": 0}

    def fake_load(service, request):
        assert service is parsed_service
        calls["load"] += 1
        return BacktestDataBundle(
            request,
            {},
            (),
            {"cache_hits": 2, "cache_misses": 0, "partial_fetch_ranges": 0, "provider_failures": 0},
            [
                {"code": "cache_gap"},
                {"code": "missing_history"},
                {"code": "insufficient_data"},
            ],
            [],
        )

    monkeypatch.setattr(web_app, "load_t1_backtest_data", fake_load)
    monkeypatch.setattr(web_app, "run_t1_thermostat_backtest", lambda request: calls.__setitem__("run", calls["run"] + 1))

    result = web_app.handle_t1_backtest_cache({"symbols": "600001", "start": "20260101", "end": "20260331"})

    assert calls == {"load": 1, "run": 0}
    assert result.metadata["data_request"].period == "daily"
    assert result.summaries[0]["cache_hits"] == 2
    assert result.summaries[0]["quality_issue_count"] == 3
    assert result.summaries[0]["cache_gap_count"] == 1
    assert result.summaries[0]["insufficient_warmup_count"] == 1


def test_account_initializer_shows_and_persists_backtest_risk_settings(tmp_path) -> None:
    html = web_app.render_account_initializer({"path": str(tmp_path / "account")})
    assert 'name="slippage_pct"' in html
    assert 'name="max_total_position_pct"' in html

    web_app.handle_portfolio_init(
        {
            "path": str(tmp_path / "account"),
            "principal": "100000",
            "slippage_pct": "0.0015",
            "max_total_position_pct": "0.88",
        }
    )
    portfolio = web_app.ManualPortfolioStore(tmp_path / "account").load()
    assert portfolio.slippage_pct == 0.0015
    assert portfolio.max_total_position_pct == 0.88


def test_run_calls_only_t1_runner_and_preserves_raw_result_identity(monkeypatch) -> None:
    _patch_parser_dependencies(monkeypatch)
    raw = _result()
    before = {name: frame.copy(deep=True) for name, frame in raw.tables.items()}
    captured = []
    monkeypatch.setattr(
        web_app,
        "run_t1_thermostat_backtest",
        lambda request, progress_callback=None: captured.append(request) or raw,
    )
    rendered = web_app.handle_thermostat_backtest(
        {"symbols": "600001", "start": "20260101", "end": "20260331"}
    )
    html = web_app.render_message(rendered, None)

    assert len(captured) == 1
    assert rendered.metadata["backtest_result"] is raw
    assert "回测状态" in html
    assert "核心指标" in html
    assert "数据质量" in html
    assert "趋势 / 网格摘要" in html
    assert "失败 / 待处理摘要" in html
    assert "Excel 报告将在下一阶段提供" not in html
    assert 'class="result-section result-section-report"' not in html
    assert "<svg" in html and "<polyline" in html
    assert not rendered.tables
    for name, frame in raw.tables.items():
        pd.testing.assert_frame_equal(frame, before[name])


def test_web_result_never_reads_legacy_advice_tables(monkeypatch) -> None:
    class PoisonedResult(T1ThermostatBacktestResult):
        def __getattribute__(self, name):
            if name in {
                "_deprecated_signal_rows", "holding_advice", "new_candidates",
                "grid_advice", "trend_advice",
            }:
                raise AssertionError(f"legacy advice accessed: {name}")
            return super().__getattribute__(name)

    _patch_parser_dependencies(monkeypatch)
    base = _result()
    raw = PoisonedResult(**{name: frame.copy(deep=True) for name, frame in base.tables.items()})
    monkeypatch.setattr(
        web_app,
        "run_t1_thermostat_backtest",
        lambda request, progress_callback=None: raw,
    )

    rendered = web_app.handle_thermostat_backtest(
        {"symbols": "600001", "start": "20260101", "end": "20260331"}
    )
    html = web_app.render_message(rendered, None)

    assert rendered.metadata["backtest_result"] is raw
    assert "<svg" in html


def test_svg_chart_has_escaped_labels_and_deterministic_empty_state() -> None:
    empty = web_app.render_svg_series([], title="权益 <空>", empty_text="暂无 & 数据")
    chart = web_app.render_svg_series([1.0, float("nan"), 2.0], title="权益 <图>")

    assert empty == '<div class="chart-empty" role="img" aria-label="权益 &lt;空&gt;">暂无 &amp; 数据</div>'
    assert "权益 &lt;图&gt;" in chart
    assert "nan" not in chart.lower()
    assert "<polyline" in chart


def test_backtest_form_has_exact_daily_disclosures_and_no_duplicate_account_risk_fields(tmp_path) -> None:
    account = tmp_path / "account"
    web_app.ManualPortfolioStore(account).initialize(
        principal=100000,
        slippage_pct=0.001,
        max_total_position_pct=0.9,
    )
    html = web_app.render_thermostat_backtest_section({"account_path": str(account)})

    for disclosure in (
        "回测精度：日线近似",
        "分钟线：未使用",
        "盘中触发时间：无法准确识别",
        "同日多触发：使用保守顺序处理",
    ):
        assert disclosure in html
    assert 'name="precision"' in html
    assert "分钟级" not in html
    assert "T+1 日线近似完整回测" in html
    assert "正式事件驱动回测" not in html
    assert 'name="trend_total_max"' in html
    assert 'action="/thermostat-backtest-cache-job"' in html
    for hidden_account_setting in (
        'name="commission_rate"',
        'name="min_commission"',
        'name="stamp_tax_rate"',
        'name="slippage_pct"',
        'name="max_total_position_pct"',
    ):
        assert hidden_account_setting not in html


def test_backtest_progress_has_five_real_nonbinary_stages() -> None:
    values = [
        web_app._progress_percent(stage, 1, 1)
        for stage in ("parse_backtest_request", "load_backtest_data", "simulate_daily", "calculate_metrics", "prepare_report")
    ]
    assert values == sorted(values)
    assert len(set(values)) == 5
    assert values[0] > 0 and values[-1] < 100


def test_cache_first_round_trip_preserves_every_shared_parser_input() -> None:
    form = {
        "stock_pool_source": "lhb",
        "symbols": "600001",
        "watchlist_name": "观察",
        "market_range": "all_a,star",
        "lhb_range": "custom",
        "lhb_confirmed_top": "50",
        "lhb_start": "20260101",
        "lhb_end": "20260131",
        "backtest_date_range": "custom",
        "start": "20260201",
        "end": "20260331",
        "account_path": "data/user/simulated",
        "cash": "120000",
        "use_simulated_cash": "on",
        "trend_total_max": "0.67",
        "source": "baostock",
        "stock_source": "akshare",
        "realtime_source": "sina",
        "refresh": "on",
        "indicator_adjust": "qfq",
        "execution_adjust": "bfq",
        "precision": "日线近似",
        "benchmark_symbol": "000300.SH",
    }

    displayed = web_app._display_form_after_success("/thermostat-backtest-cache-job", form)

    assert displayed == form


def test_cache_job_reports_only_actual_parse_load_and_result_preparation(monkeypatch) -> None:
    events: list[str] = []

    class FakeJob:
        form = {"symbols": "600001"}

        def update(self, event):
            events.append(str(event["stage"]))

        def complete(self, result):
            events.append("done")

        def fail(self, exc):
            raise exc

    def fake_cache(form, progress_callback=None):
        progress_callback({"stage": "parse_backtest_request", "completed": 1, "total": 1})
        progress_callback({"stage": "load_backtest_data", "completed": 1, "total": 1})
        progress_callback({"stage": "prepare_report", "completed": 1, "total": 1})
        return web_app.RenderResult("cache")

    monkeypatch.setitem(web_app.JOBS, "cache-progress", FakeJob())
    monkeypatch.setattr(web_app, "handle_t1_backtest_cache", fake_cache)

    web_app._run_t1_backtest_cache_job("cache-progress")

    assert events == ["parse_backtest_request", "load_backtest_data", "prepare_report", "done"]


def test_web_run_progress_reports_parse_before_parser_then_forwards_runner_events(monkeypatch) -> None:
    _patch_parser_dependencies(monkeypatch)
    trace: list[str] = []
    raw = _result()
    real_parse = web_app.parse_t1_backtest_input

    def traced_parse(form):
        trace.append("parser")
        return real_parse(form)

    def fake_runner(request, progress_callback=None):
        trace.append("runner")
        for stage in ("load_backtest_data", "simulate_daily", "calculate_metrics"):
            progress_callback({"stage": stage, "completed": 1, "total": 1})
        return raw

    monkeypatch.setattr(web_app, "parse_t1_backtest_input", traced_parse)
    monkeypatch.setattr(web_app, "run_t1_thermostat_backtest", fake_runner)

    web_app.handle_thermostat_backtest(
        {"symbols": "600001", "start": "20260101", "end": "20260331"},
        progress_callback=lambda event: trace.append(str(event["stage"])),
    )

    assert trace == [
        "parse_backtest_request",
        "parser",
        "parse_backtest_request",
        "runner",
        "load_backtest_data",
        "simulate_daily",
        "calculate_metrics",
    ]


def test_cache_handler_progress_wraps_actual_parser_loader_and_result(monkeypatch) -> None:
    parsed_service = _patch_parser_dependencies(monkeypatch)
    trace: list[str] = []
    real_parse = web_app.parse_t1_backtest_input

    def traced_parse(form):
        trace.append("parser")
        return real_parse(form)

    def traced_load(service, request):
        assert service is parsed_service
        trace.append("loader")
        return BacktestDataBundle(
            request, {}, (),
            {"cache_hits": 0, "cache_misses": 0, "partial_fetch_ranges": 0, "provider_failures": 0},
            [], [],
        )

    monkeypatch.setattr(web_app, "parse_t1_backtest_input", traced_parse)
    monkeypatch.setattr(web_app, "load_t1_backtest_data", traced_load)

    web_app.handle_t1_backtest_cache(
        {"symbols": "600001", "start": "20260101", "end": "20260331"},
        progress_callback=lambda event: trace.append(
            f'{event["stage"]}:{event["completed"]}'
        ),
    )

    assert trace == [
        "parse_backtest_request:0",
        "parser",
        "parse_backtest_request:1",
        "load_backtest_data:0",
        "loader",
        "load_backtest_data:1",
        "prepare_report:0",
        "prepare_report:1",
    ]


def test_run_job_prepare_report_progress_brackets_actual_html_preparation(monkeypatch) -> None:
    trace: list[str] = []
    job = web_app.ThermostatJob("render-progress", {})
    result = web_app.RenderResult("T1 result")

    def fake_render(render_result, error):
        assert render_result is result
        trace.append("render_message")
        return "<p>result</p>"

    def fake_report_entry(render_job):
        assert render_job is job
        trace.append("report_entry")
        return "<p>report</p>"

    monkeypatch.setattr(web_app, "render_message", fake_render)
    monkeypatch.setattr(web_app, "_thermostat_report_entry", fake_report_entry)

    job.complete(
        result,
        progress_callback=lambda event: trace.append(
            f'{event["stage"]}:{event["completed"]}/{event["total"]}'
        ),
    )

    assert trace == [
        "prepare_report:0/1",
        "render_message",
        "report_entry",
        "prepare_report:1/1",
    ]
    assert job.status == "done"
