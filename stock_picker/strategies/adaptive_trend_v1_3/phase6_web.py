"""HTML rendering helpers for the existing stdlib web application."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import html
import secrets
from typing import Mapping

from .phase5_models import DateRangeKind, DateRangeSpec, RunMode, UniverseKind, UniverseSpec
from .phase6_controller import Phase6Controller
from .phase6_models import ErrorVM


PHASE6_PAGES = {
    "adaptive-v13-overview",
    "adaptive-v13-cache",
    "adaptive-v13-backtest",
    "adaptive-v13-paper",
    "adaptive-v13-runs",
    "adaptive-v13-account",
}

PHASE6_LABELS = (
    ("adaptive-v13-overview","总览"),
    ("adaptive-v13-cache","数据缓存"),
    ("adaptive-v13-backtest","回测"),
    ("adaptive-v13-paper","每日模拟"),
    ("adaptive-v13-runs","运行记录"),
    ("adaptive-v13-account","账户与设置"),
)


@dataclass
class Phase6WebState:
    account_profile_id: str = "default"
    universe_spec: UniverseSpec = field(default_factory=lambda: UniverseSpec(
        UniverseKind.MANUAL,manual_symbols=("600000.SH",),
    ))
    date_range_spec: DateRangeSpec = field(default_factory=lambda: DateRangeSpec(
        DateRangeKind.RECENT_MONTHS,value=3,
    ))
    data_snapshot_id: str = ""
    run_id: str = ""
    readiness_status: str = "EMPTY"
    readiness_message: str = ""
    backtest_operation_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    paper_operation_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    resume_operation_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    run_filters: dict[str, str] = field(default_factory=dict)


def render_phase6_page(
    page: str, controller: Phase6Controller | None,
    state: Phase6WebState, *, message: str = "", error: ErrorVM | None = None,
) -> str:
    if error is not None:
        error = f"{error.title} [{error.code}] {error.action} ({error.correlation_id})"
    title = dict(PHASE6_LABELS).get(page,"自适应趋势 V1.3")
    account = _account_bar(controller,state)
    content = {
        "adaptive-v13-overview": _overview,
        "adaptive-v13-cache": _cache,
        "adaptive-v13-backtest": _backtest,
        "adaptive-v13-paper": _paper,
        "adaptive-v13-runs": _runs,
        "adaptive-v13-account": _account,
    }[page](controller,state)
    notice = (
        f'<div class="message error"><strong>操作失败</strong><span>{html.escape(error)}</span></div>'
        if error else
        (f'<div class="message success">{html.escape(message)}</div>' if message else "")
    )
    return f"""
    <section class="adaptive-v13" data-page="{page}">
      <div class="strategy-heading"><div><span class="eyebrow">自适应趋势 V1.3</span>
      <h2>{title}</h2></div><span class="status-chip status-{state.readiness_status.lower()}">
      {html.escape(state.readiness_status)}</span></div>
      {account}{notice}{content}
    </section>"""


def phase6_nav(current: str) -> str:
    links = "".join(
        f'<a href="/{target}"{" class=active" if target == current else ""}>{label}</a>'
        for target,label in PHASE6_LABELS
    )
    return f'<div class="nav-group"><span class="nav-title">自适应趋势 V1.3</span>{links}</div>'


def update_selection(state: Phase6WebState, form: Mapping[str,str]) -> None:
    state.account_profile_id = form.get("account_profile_id",state.account_profile_id).strip() or "default"
    kind = form.get("universe_kind",str(state.universe_spec.kind))
    manual = _tokens(form.get("manual_symbols",",".join(state.universe_spec.manual_symbols)))
    watchlists = _tokens(form.get("watchlist_names",",".join(state.universe_spec.watchlist_names)))
    scopes = _tokens(form.get("market_scopes",",".join(state.universe_spec.market_scopes)))
    state.universe_spec = UniverseSpec(kind,manual,watchlists,scopes)
    date_kind = form.get("date_kind",str(state.date_range_spec.kind))
    if date_kind == DateRangeKind.CUSTOM.value:
        state.date_range_spec = DateRangeSpec(
            date_kind,start_date=form.get("start_date",""),end_date=form.get("end_date",""),
        )
    else:
        state.date_range_spec = DateRangeSpec(
            date_kind,value=int(form.get("date_value",str(state.date_range_spec.value or 3)))
        )


def refresh_snapshot_state(
    controller: Phase6Controller, state: Phase6WebState, mode: RunMode | str,
) -> bool:
    """Revalidate session snapshot and current input hashes on every relevant GET."""
    if not state.data_snapshot_id:
        state.readiness_status = "EMPTY"
        return False
    current_positions: tuple[str, ...] = ()
    if RunMode(mode) is RunMode.DAILY_PAPER:
        paper = controller.paper_state_loader(state.account_profile_id) or {}
        current_positions = tuple((paper.get("positions") or {}).keys())
    try:
        valid = controller.validate_snapshot(
            state.data_snapshot_id,state.universe_spec,state.date_range_spec,
            state.account_profile_id,mode,current_positions=current_positions,
        )
    except Exception:
        valid = False
    if valid:
        state.readiness_status = "READY"
        state.readiness_message = ""
        return True
    state.readiness_status = "STALE"
    state.readiness_message = "输入已变化，需要重新缓存"
    return False


def handle_phase6_action(
    path: str, form: Mapping[str,str], controller: Phase6Controller,
    state: Phase6WebState,
) -> tuple[str,str]:
    if path in {
        "/adaptive-v13-preview","/adaptive-v13-cache-prepare",
        "/adaptive-v13-backtest-run","/adaptive-v13-paper-run",
    }:
        update_selection(state,form)
    if path == "/adaptive-v13-preview":
        controller.preview_universe(
            state.universe_spec,state.account_profile_id,state.date_range_spec,RunMode.BACKTEST,
        )
        controller.resolve_date_range(
            state.date_range_spec,state.account_profile_id,state.universe_spec,RunMode.BACKTEST,
        )
        return "adaptive-v13-cache","数据需求已预览"
    if path == "/adaptive-v13-cache-prepare":
        _,ready = controller.prepare_cache(
            state.universe_spec,state.date_range_spec,state.account_profile_id,
            form.get("run_mode",RunMode.BACKTEST.value),
        )
        state.data_snapshot_id = ready.data_snapshot_id
        state.readiness_status = ready.status
        return "adaptive-v13-cache",f"数据状态：{ready.status}"
    if path == "/adaptive-v13-backtest-run":
        result = controller.submit_backtest(
            state.universe_spec,state.date_range_spec,state.account_profile_id,
            state.data_snapshot_id,form.get("operation_token",""),
        )
        state.run_id = result.run_id
        run_id = result.run_id
        state.backtest_operation_token = secrets.token_urlsafe(24)
        return "adaptive-v13-runs",f"回测完成：{run_id}"
    if path == "/adaptive-v13-paper-run":
        result = controller.submit_daily_paper(
            state.universe_spec,state.date_range_spec,state.account_profile_id,
            state.data_snapshot_id,form.get("operation_token",""),
        )
        state.run_id = result.run_id
        run_id = result.run_id
        state.paper_operation_token = secrets.token_urlsafe(24)
        return "adaptive-v13-paper",f"每日模拟完成：{run_id}"
    if path == "/adaptive-v13-resume":
        run_id = form.get("run_id",state.run_id)
        controller.resume_run(run_id,form.get("operation_token",""))
        state.run_id = run_id
        state.resume_operation_token = secrets.token_urlsafe(24)
        return "adaptive-v13-runs","运行已从最近检查点恢复"
    if path == "/adaptive-v13-report":
        controller.generate_report(form.get("run_id",state.run_id))
        return "adaptive-v13-runs","报告已生成并校验"
    if path == "/adaptive-v13-provider-test":
        controller.test_provider_connections(
            float(form.get("timeout_seconds","3")),state.account_profile_id,
        )
        return "adaptive-v13-account","Provider 检测完成（不会写入缓存）"
    if path == "/adaptive-v13-account-save":
        controller.save_account_settings({
            "account_profile_id":form.get("account_profile_id","default"),
            "backtest_initial_cash":form.get("backtest_initial_cash",""),
            "paper_cash":form.get("paper_cash",""),
            "fee_schedule_id":form.get("fee_schedule_id",""),
            "base_currency":form.get("base_currency","CNY"),
            "provider_priority":form.get("provider_priority",""),
            "data_directory":form.get("data_directory",""),
            "report_directory":form.get("report_directory",""),
            "default_universe":state.universe_spec,
        })
        return "adaptive-v13-account","账户默认设置已保存"
    if path == "/adaptive-v13-watchlist":
        action = form.get("watchlist_action","")
        if action == "delete":
            controller.delete_watchlist(form.get("watchlist_name",""))
        else:
            controller.save_watchlist(
                action,name=form.get("watchlist_name",""),
                new_name=form.get("new_name",""),
                source_name=form.get("source_name",""),
                symbols=_tokens(form.get("symbols","")),
            )
        return "adaptive-v13-account","自选股组合已更新"
    raise ValueError("unsupported_phase6_action")


def _account_bar(controller: Phase6Controller | None, state: Phase6WebState) -> str:
    if controller is None:
        values = (("账户",state.account_profile_id),("模式","PAPER"),("数据","服务未配置"))
    else:
        try:
            vm = controller.load_account_summary(state.account_profile_id)
            values = (
                ("账户",vm.account_profile_id),("模式",vm.mode),
                ("回测资金",str(vm.backtest_initial_cash)),("模拟现金",str(vm.paper_cash)),
                ("模拟持仓",str(vm.paper_position_count)),("费用",vm.fee_schedule_id),
                ("Provider"," → ".join(vm.provider_priority)),
                ("数据目录",vm.data_directory_status),("最近准备",vm.latest_readiness_status),
            )
        except Exception:
            values = (("账户",state.account_profile_id),("状态","账户配置待创建"))
    return '<div class="account-summary">' + "".join(
        f'<div><span>{html.escape(k)}</span><strong>{html.escape(v)}</strong></div>' for k,v in values
    ) + "</div>"


def _overview(controller: Phase6Controller | None, state: Phase6WebState) -> str:
    runs = controller.list_runs(page_size=5) if controller else ()
    rows = "".join(
        f"<tr><td>{html.escape(item.short_run_id)}</td><td>{item.mode}</td>"
        f"<td>{item.status}</td><td>{html.escape(item.created_at)}</td></tr>" for item in runs
    ) or '<tr><td colspan="4" class="empty-state">暂无运行记录</td></tr>'
    return f"""<div class="card-grid">
      {_card("研究流程","选择账户和股票池 → 缓存验证 → 回测或每日模拟。")}
      {_card("数据门禁",f"当前状态：{state.readiness_status}。未 READY 时不会临时联网运行。")}
      {_card("执行后端","PAPER 可用；FUTURE_QMT 预留且不可选择。")}
    </div><section class="panel"><h3>最近运行</h3><div class="table-scroll">
    <table><thead><tr><th>Run</th><th>模式</th><th>状态</th><th>创建时间</th></tr></thead>
    <tbody>{rows}</tbody></table></div></section>"""


def _cache(controller: Phase6Controller | None, state: Phase6WebState) -> str:
    return f"""<form method="post" action="/adaptive-v13-cache-prepare" class="panel">
      <h3>一键缓存并验证</h3>{_selectors(state)}
      <div class="data-summary"><span>状态 <strong>{state.readiness_status}</strong></span>
      <span>price_basis <strong>RAW_UNADJUSTED_V1</strong></span>
      <span class="advanced">snapshot <code>{html.escape(state.data_snapshot_id or "尚未生成")}</code></span></div>
      <div class="actions"><button formaction="/adaptive-v13-preview">预览数据需求</button>
      <button class="primary" type="submit">缓存并验证数据</button></div>
      <p class="hint">缓存和回测复用同一 UniverseSpec、DateRangeSpec 与 data_snapshot。</p>
    </form>{_provider_table(controller,state)}"""


def _backtest(controller: Phase6Controller | None, state: Phase6WebState) -> str:
    ready = state.readiness_status == "READY" and bool(state.data_snapshot_id)
    disabled = "" if ready else " disabled"
    return f"""<form method="post" action="/adaptive-v13-backtest-run" class="panel">
      <input type="hidden" name="operation_token" value="{html.escape(state.backtest_operation_token)}">
      <h3>创建并同步执行回测</h3>{_selectors(state)}
      <fieldset><legend>本次运行设置</legend>
      <label><input type="checkbox" name="override_enabled"> 启用本次运行覆盖（默认关闭）</label>
      <p class="hint">账户资金、费用、Provider 和报告目录默认来自账户设置；执行期间网络策略为 FORBID。</p>
      </fieldset><button class="primary" type="submit"{disabled}>运行回测</button>
      {'' if ready else '<p class="validation">请先缓存并验证数据，当前运行按钮已禁用。</p>'}
    </form>"""


def _paper(controller: Phase6Controller | None, state: Phase6WebState) -> str:
    ready = state.readiness_status == "READY" and bool(state.data_snapshot_id)
    return f"""<section class="panel warning"><strong>模拟运行，不会向券商发送订单。</strong>
      <p>仅支持手动 DAILY_PAPER；不包含自动调度、QMT 或实盘下单。</p></section>
      <form method="post" action="/adaptive-v13-paper-run" class="panel">{_selectors(state)}
      <input type="hidden" name="operation_token" value="{html.escape(state.paper_operation_token)}">
      <input type="hidden" name="run_mode" value="DAILY_PAPER">
      <div class="actions"><button formaction="/adaptive-v13-cache-prepare">准备当日数据</button>
      <button class="primary" {'disabled' if not ready else ''}>运行当日模拟</button></div>
      <div class="empty-state">持仓、SellableQty、TodayBoughtQty、PendingSell 与 Cooldown 从服务层加载。</div>
      </form>"""


def _runs_v1316(controller: Phase6Controller | None, state: Phase6WebState) -> str:
    detail_html = ""
    if controller and state.run_id:
        try:
            detail = controller.load_run_detail(state.run_id)
            files = controller.list_report_files(state.run_id)
            file_links = "".join(
                f'<li><a href="/adaptive-v13-report-file?run_id={html.escape(state.run_id)}'
                f'&name={html.escape(item.name)}">{html.escape(item.name)}</a> '
                f'<span>{html.escape(item.sha256[:12])}…</span></li>'
                for item in files if item.valid
            ) or "<li>尚未生成报告</li>"
            metrics = "".join(
                f"<dt>{html.escape(key)}</dt><dd>{html.escape(value)}</dd>"
                for key,value in detail.summary.metrics
            )
            detail_html = f"""<section class="panel"><h3>Run 详情：{html.escape(state.run_id)}</h3>
            <div class="data-summary"><span>状态 <strong>{detail.summary.status}</strong></span>
            <span>策略 <strong>{detail.summary.strategy_version}</strong></span>
            <span>price_basis <strong>{detail.summary.price_basis_id}</strong></span></div>
            <dl class="summary">{metrics}</dl>
            <details><summary>成交与订单</summary><p>成交 {len(detail.fills)}；订单 {len(detail.orders)}</p></details>
            <details><summary>持仓、Pending 与冷却</summary><p>持仓版本 {len(detail.positions)}；
            Pending {len(detail.pending_sells)}；Cooldown {len(detail.cooldowns)}</p></details>
            <details><summary>数据覆盖与审计</summary><p>覆盖 {len(detail.coverage)}；审计 {len(detail.audits)}</p></details>
            <h4>报告下载（manifest + SHA 校验）</h4><ul>{file_links}</ul>
            <form method="post" action="/adaptive-v13-report"><input type="hidden" name="run_id"
            value="{html.escape(state.run_id)}"><button>生成/刷新报告</button></form></section>"""
        except Exception as exc:
            view = controller.get_error_view(exc)
            detail_html = f'<section class="panel warning">{html.escape(view.title)} [{view.code}]</section>'
    runs = controller.list_runs(page_size=20) if controller else ()
    rows = "".join(
        f"<tr><td><a href='/adaptive-v13-runs?run_id={html.escape(item.run_id)}'>{item.short_run_id}</a></td>"
        f"<td>{item.mode}</td><td>{item.status}</td><td>{item.start_date}—{item.end_date}</td>"
        f"<td>{item.created_at}</td><td>{item.open_position_count}</td></tr>" for item in runs
    ) or '<tr><td colspan="6" class="empty-state">暂无运行记录</td></tr>'
    return f"""{detail_html}<section class="panel"><h3>运行记录</h3>
      <form method="get" class="filter-row"><select name="mode"><option value="">全部模式</option>
      <option>BACKTEST</option><option>DAILY_PAPER</option></select>
      <select name="status"><option value="">全部状态</option><option>FAILED</option>
      <option>COMPLETED</option><option>DEGRADED</option></select><button>筛选</button></form>
      <div class="table-scroll"><table><thead><tr><th>Run</th><th>模式</th><th>状态</th>
      <th>日期</th><th>创建</th><th>开放持仓</th></tr></thead><tbody>{rows}</tbody></table></div>
      <p class="hint">列表由 Phase 5 list_runs 分页读取，详情和指标不会在网页重新计算。</p>
    </section>"""


def _account(controller: Phase6Controller | None, state: Phase6WebState) -> str:
    watches = controller.list_watchlists() if controller else ()
    watch_rows = "".join(
        f"<tr><td>{html.escape(item.name)}</td><td>{item.count}</td><td>{html.escape(item.updated_at)}</td></tr>"
        for item in watches
    ) or '<tr><td colspan="3" class="empty-state">暂无自选股组合</td></tr>'
    return f"""<div class="settings-tabs">
      <form method="post" action="/adaptive-v13-account-save" class="panel">
      <h3>账户默认设置</h3><input type="hidden" name="account_profile_id"
      value="{html.escape(state.account_profile_id)}"><div class="form-grid">
      <label>回测初始资金 *<input name="backtest_initial_cash" value="100000" inputmode="decimal"></label>
      <label>模拟现金 *<input name="paper_cash" value="100000" inputmode="decimal"></label>
      <label>费用方案 *<input name="fee_schedule_id" value="CN_A_DEFAULT"></label>
      <label>基础货币 *<input name="base_currency" value="CNY"></label>
      <label>Provider 优先级 *<input name="provider_priority" value="baostock,akshare"></label>
      <label>数据目录 *<input name="data_directory" value="data/adaptive_trend_v1_3"></label>
      <label>报告目录 *<input name="report_directory" value="data/reports/adaptive_trend_v1_3"></label>
      </div><button class="primary">保存账户默认设置</button></form>
      <section class="panel"><h3>概览</h3><p>账户默认值是资金、费用、Provider、目录及默认 Universe 的唯一来源。</p></section>
      <section class="panel"><h3>资金与费用</h3><div class="form-grid">
      <label>回测初始资金 *<input name="backtest_initial_cash" inputmode="decimal"></label>
      <label>模拟现金 *<input name="paper_cash" inputmode="decimal"></label></div></section>
      <section class="panel"><h3>数据源与目录</h3>{_provider_table(controller,state)}
      <form method="post" action="/adaptive-v13-provider-test"><label>超时秒数
      <input name="timeout_seconds" value="3"></label><button>检测数据源</button>
      <p class="hint">检测有超时且不会写入缓存，也不会记录密钥、Cookie 或凭证。</p></form></section>
      <section class="panel"><h3>自选股组合</h3>
      <form method="post" action="/adaptive-v13-watchlist" class="form-grid">
      <label>操作<select name="watchlist_action"><option value="create">新建</option>
      <option value="add">批量添加</option><option value="remove">删除代码</option>
      <option value="rename">重命名</option><option value="copy">复制</option>
      <option value="set_default">设为默认</option><option value="delete">删除组合</option></select></label>
      <label>组合名称 *<input name="watchlist_name"></label><label>新名称<input name="new_name"></label>
      <label class="span-2">证券代码<textarea name="symbols"></textarea></label>
      <button>保存</button></form><div class="table-scroll"><table><thead><tr>
      <th>名称</th><th>证券数</th><th>更新时间</th></tr></thead><tbody>{watch_rows}</tbody></table></div></section>
      <section class="panel"><h3>维护与诊断</h3><p>报告 SHA、缓存覆盖及运行审计均从 Phase 5 权威数据读取。</p></section>
    </div>"""


def _selectors_v1316(state: Phase6WebState) -> str:
    return f"""<input type="hidden" name="account_profile_id" value="{html.escape(state.account_profile_id)}">
    <div class="selector-grid"><fieldset><legend>股票池 *</legend>
    <label>来源<select name="universe_kind"><option value="MANUAL">手动输入</option>
    <option value="WATCHLIST">自选股组合</option><option value="MARKET_SCOPE">市场范围</option>
    <option value="COMBINED">组合来源</option></select></label>
    <label data-dynamic="manual">手动代码<textarea name="manual_symbols"
    placeholder="600000, 000001">{html.escape(','.join(state.universe_spec.manual_symbols))}</textarea></label>
    <label>自选股名称<input name="watchlist_names" value="{html.escape(','.join(state.universe_spec.watchlist_names))}"></label>
    <label>市场范围（可多选）<select name="market_scopes" multiple>
    <option>沪深A股</option><option>上证A股</option><option>深证A股</option><option>创业板</option>
    </select></label></fieldset><fieldset><legend>日期范围 *</legend>
    <label>预设<select name="date_kind"><option value="RECENT_MONTHS">最近月份</option>
    <option value="RECENT_YEARS">最近年份</option><option value="CUSTOM">自定义</option></select></label>
    <label>数值<select name="date_value">{''.join(f'<option>{v}</option>' for v in (1,2,3,5,6))}</select></label>
    <div data-dynamic="custom" class="form-grid"><label>开始日期<input type="date" name="start_date"></label>
    <label>结束日期<input type="date" name="end_date"></label></div>
    <p class="hint">实际交易日及 320 交易日预热由 Phase 5 统一解析。</p></fieldset></div>"""


def _provider_table(controller: Phase6Controller | None, state: Phase6WebState) -> str:
    providers = controller.inspect_provider_status(state.account_profile_id) if controller else ()
    rows = "".join(
        f"<tr><td>{html.escape(item.display_name)}</td><td>{item.availability}</td>"
        f"<td>{html.escape(', '.join(item.dataset_types))}</td><td>{html.escape(', '.join(item.frequencies))}</td>"
        f"<td>{html.escape(item.price_basis)}</td><td>{html.escape(item.error_code)}</td></tr>"
        for item in providers
    ) or '<tr><td colspan="6" class="empty-state">Provider 服务尚未配置</td></tr>'
    return f"""<div class="table-scroll"><table><thead><tr><th>Provider</th><th>状态</th>
    <th>数据类型</th><th>频率</th><th>价格口径</th><th>最近错误</th></tr></thead>
    <tbody>{rows}</tbody></table></div>"""


def _card(title: str, text: str) -> str:
    return f'<article class="metric-card"><h3>{html.escape(title)}</h3><p>{html.escape(text)}</p></article>'


def _tokens(value: str) -> tuple[str,...]:
    normalized = value.replace("\n",",").replace(";",",").replace(" ",",")
    return tuple(item.strip() for item in normalized.split(",") if item.strip())


# These final renderers intentionally override the first-pass V1.3.16 versions.
# Keeping the surrounding page code unchanged preserves the legacy web stack.
def _selectors(state: Phase6WebState) -> str:
    universe_kind = str(state.universe_spec.kind)
    date_kind = str(state.date_range_spec.kind)
    universe_options = "".join(
        f'<option value="{value}"{_selected(universe_kind,value)}>{label}</option>'
        for value,label in (
            ("MANUAL","手动输入"),("WATCHLIST","自选股组合"),
            ("MARKET_SCOPE","市场范围"),("COMBINED","组合来源"),
        )
    )
    date_options = "".join(
        f'<option value="{value}"{_selected(date_kind,value)}>{label}</option>'
        for value,label in (
            ("RECENT_MONTHS","最近月份"),("RECENT_YEARS","最近年份"),
            ("CUSTOM","自定义"),
        )
    )
    values = (1,3,6) if date_kind == "RECENT_MONTHS" else (1,2,3,5)
    date_values = "".join(
        f'<option value="{value}"{_selected(str(state.date_range_spec.value),str(value))}>'
        f'{value}</option>' for value in values
    )
    manual = ""
    if universe_kind in {"MANUAL","COMBINED"}:
        manual = (
            '<label data-dynamic="manual">手动代码'
            f'<textarea name="manual_symbols">{html.escape(",".join(state.universe_spec.manual_symbols))}'
            '</textarea></label>'
        )
    watchlist = ""
    if universe_kind in {"WATCHLIST","COMBINED"}:
        watchlist = (
            '<label>自选股名称<input name="watchlist_names" '
            f'value="{html.escape(",".join(state.universe_spec.watchlist_names))}"></label>'
        )
    scopes = ""
    if universe_kind in {"MARKET_SCOPE","COMBINED"}:
        selected_scopes = set(state.universe_spec.market_scopes)
        scope_options = "".join(
            f'<option value="{html.escape(value)}"'
            f'{" selected" if value in selected_scopes else ""}>{html.escape(value)}</option>'
            for value in ("沪深A股","上证A股","深证A股","创业板")
        )
        scopes = (
            '<label>市场范围（可多选）<select name="market_scopes" multiple>'
            f'{scope_options}</select></label>'
        )
    custom = ""
    if date_kind == "CUSTOM":
        custom = (
            '<div data-dynamic="custom" class="form-grid">'
            f'<label>开始日期<input type="date" name="start_date" value="{html.escape(str(state.date_range_spec.start_date or ""))}"></label>'
            f'<label>结束日期<input type="date" name="end_date" value="{html.escape(str(state.date_range_spec.end_date or ""))}"></label>'
            '</div>'
        )
    preset = "" if date_kind == "CUSTOM" else (
        f'<label>数值<select name="date_value">{date_values}</select></label>'
    )
    return (
        f'<input type="hidden" name="account_profile_id" value="{html.escape(state.account_profile_id)}">'
        '<div class="selector-grid"><fieldset><legend>股票池 *</legend>'
        f'<label>来源<select name="universe_kind">{universe_options}</select></label>'
        f'{manual}{watchlist}{scopes}</fieldset><fieldset><legend>日期范围 *</legend>'
        f'<label>预设<select name="date_kind">{date_options}</select></label>{preset}{custom}'
        '<p class="hint">实际交易日及320交易日预热由Phase 5统一解析。</p>'
        '</fieldset></div>'
    )


def _runs(controller: Phase6Controller | None, state: Phase6WebState) -> str:
    filters = dict(state.run_filters)
    kwargs = {
        "mode": filters.get("mode",""),
        "status": filters.get("status",""),
        "date_from": filters.get("date_from",""),
        "date_to": filters.get("date_to",""),
        "account": filters.get("account",""),
        "strategy_version": filters.get("strategy_version",""),
        "has_open_positions": _optional_bool(filters.get("has_open_positions","")),
        "degraded": _optional_bool(filters.get("degraded","")),
        "page": _bounded_int(filters.get("page","1"),1,1_000_000),
        "page_size": _bounded_int(filters.get("page_size","20"),20,100),
    }
    runs = controller.list_runs(**kwargs) if controller else ()
    detail_html = ""
    if controller and state.run_id:
        try:
            detail = controller.load_run_detail(state.run_id)
            can_resume = controller.can_resume_run(state.run_id)
            resume = ""
            if can_resume:
                resume = (
                    '<form method="post" action="/adaptive-v13-resume">'
                    f'<input type="hidden" name="run_id" value="{html.escape(state.run_id)}">'
                    f'<input type="hidden" name="operation_token" value="{html.escape(state.resume_operation_token)}">'
                    '<button class="primary">从检查点恢复</button></form>'
                )
            detail_html = (
                '<section class="panel"><h3>Run详情</h3>'
                f'<p><code>{html.escape(state.run_id)}</code> '
                f'<strong>{html.escape(detail.summary.status)}</strong></p>{resume}</section>'
            )
        except Exception as exc:
            view = controller.get_error_view(exc)
            detail_html = _error_notice(view)
    rows = "".join(
        '<tr>'
        f'<td><a href="/adaptive-v13-runs?run_id={html.escape(item.run_id)}">{html.escape(item.short_run_id)}</a></td>'
        f'<td>{html.escape(item.mode)}</td><td>{html.escape(item.status)}</td>'
        f'<td>{html.escape(item.start_date)}—{html.escape(item.end_date)}</td>'
        f'<td>{item.open_position_count}</td></tr>'
        for item in runs
    ) or '<tr><td colspan="5" class="empty-state">暂无运行记录</td></tr>'
    return (
        f'{detail_html}<section class="panel"><h3>运行记录</h3>'
        '<form method="get" action="/adaptive-v13-runs" class="filter-row">'
        f'{_filter_select("mode",filters.get("mode",""),("","BACKTEST","DAILY_PAPER"))}'
        f'{_filter_select("status",filters.get("status",""),("","CREATED","RUNNING","FAILED","COMPLETED","DEGRADED"))}'
        f'<input type="date" name="date_from" value="{html.escape(filters.get("date_from",""))}">'
        f'<input type="date" name="date_to" value="{html.escape(filters.get("date_to",""))}">'
        f'<input name="account" placeholder="账户" value="{html.escape(filters.get("account",""))}">'
        f'<input name="strategy_version" placeholder="策略版本" value="{html.escape(filters.get("strategy_version",""))}">'
        f'{_filter_select("has_open_positions",filters.get("has_open_positions",""),("","true","false"))}'
        f'{_filter_select("degraded",filters.get("degraded",""),("","true","false"))}'
        f'<input type="number" min="1" name="page" value="{kwargs["page"]}">'
        f'<input type="number" min="1" max="100" name="page_size" value="{kwargs["page_size"]}">'
        '<button>筛选</button></form><div class="table-scroll"><table><thead><tr>'
        '<th>Run</th><th>模式</th><th>状态</th><th>日期</th><th>开放持仓</th>'
        f'</tr></thead><tbody>{rows}</tbody></table></div>'
        '<p class="hint">列表由 Phase 5 list_runs 权威分页读取。</p></section>'
    )


def _selected(current: str, expected: str) -> str:
    return " selected" if current == expected else ""


def _optional_bool(value: str) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def _bounded_int(value: str, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed,1),maximum)


def _filter_select(name: str, current: str, values: tuple[str,...]) -> str:
    options = "".join(
        f'<option value="{html.escape(value)}"{_selected(current,value)}>'
        f'{html.escape(value or "全部")}</option>' for value in values
    )
    return f'<select name="{name}">{options}</select>'


def _error_notice(error: ErrorVM) -> str:
    detail = (
        f'<details><summary>技术详情</summary>{html.escape(error.detail)}</details>'
        if error.detail else ""
    )
    return (
        '<div class="message error"><strong>'
        f'{html.escape(error.title)} [{html.escape(error.code)}]</strong>'
        f'<span>{html.escape(error.action)}</span>{detail}'
        f'<small>correlation_id={html.escape(error.correlation_id)}</small></div>'
    )
