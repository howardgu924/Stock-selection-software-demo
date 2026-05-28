from __future__ import annotations

import argparse
import html
import sys
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stock_picker.data import DataSourceConfig, MarketDataService
from stock_picker.data.models import normalize_symbol
from stock_picker.execution import build_execution_plan
from stock_picker.strategies import (
    STRATEGY_NAMES,
    TurtleConfig,
    backtest_turtle_system,
    run_strategy,
    run_turtle_system,
)
from stock_picker.user import ManualPortfolioStore


DEFAULT_PORT = 8765
DEFAULT_USER_PATH = "data/user/default"
LAST_FORM: dict[str, str] = {}
PAGES = {"strategy", "turtle", "backtest", "portfolio"}


class WebAppHandler(BaseHTTPRequestHandler):
    server_version = "StockPickerWeb/0.1"

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._send_text("ok")
            return
        if path == "/":
            self._send_page(render_page(page="strategy", form=LAST_FORM))
            return
        page = path.strip("/")
        if page not in PAGES:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        result = None
        if page == "portfolio":
            try:
                result = handle_portfolio_summary({"path": LAST_FORM.get("path", DEFAULT_USER_PATH)})
            except Exception:
                result = None
        self._send_page(render_page(page=page, result=result, form=LAST_FORM))

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        form: dict[str, str] = {}
        try:
            form = self._read_form()
            LAST_FORM.clear()
            LAST_FORM.update(form)
            if path == "/strategy":
                page = "strategy"
                result = handle_strategy(form)
            elif path == "/turtle":
                page = "turtle"
                result = handle_turtle(form)
            elif path == "/turtle-backtest":
                page = "backtest"
                result = handle_turtle_backtest(form)
            elif path == "/portfolio-init":
                page = "portfolio"
                result = handle_portfolio_init(form)
            elif path == "/portfolio-buy":
                page = "portfolio"
                result = handle_portfolio_buy(form)
            elif path == "/portfolio-sell":
                page = "portfolio"
                result = handle_portfolio_sell(form)
            elif path == "/portfolio-summary":
                page = "portfolio"
                result = handle_portfolio_summary(form)
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            self._send_page(render_page(page=page, result=result, form=form))
        except Exception as exc:
            self._send_page(render_page(page=_page_for_path(path), error=str(exc), form=form))

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[web] {self.address_string()} - {fmt % args}", file=sys.stderr)

    def _read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        values = parse_qs(raw, keep_blank_values=True)
        return {key: value[-1] for key, value in values.items()}

    def _send_page(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_text(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def handle_strategy(form: dict[str, str]) -> RenderResult:
    service = _service(form)
    strategy = _value(form, "strategy", "turtle")
    symbols = _symbols(form)
    if strategy in {"ma_cross", "turtle", "small_cap", "undervalued"} and not symbols:
        raise ValueError("This strategy needs symbols. Enter comma-separated stock codes.")
    result = run_strategy(
        service=service,
        strategy=strategy,
        symbols=symbols or None,
        start_date=_optional(form, "start"),
        end_date=_optional(form, "end"),
        as_of=_optional(form, "as_of"),
        top=_int(form, "top", 10),
        refresh=_checked(form, "refresh"),
        skip_errors=True,
    )
    return RenderResult(
        title=f"Strategy: {strategy}",
        summaries=[_request_summary(form, ["strategy", "symbols", "start", "end", "as_of", "top", "source", "refresh"])],
        tables=[
            TableBlock("Results", result.results),
            TableBlock("Errors", result.errors),
        ],
    )


def handle_turtle(form: dict[str, str]) -> RenderResult:
    service = _service(form)
    end = _value(form, "end", _value(form, "as_of", ""))
    if not end:
        raise ValueError("Turtle system needs End or As of date.")
    start = _optional(form, "start") or (
        pd.to_datetime(end) - pd.Timedelta(days=180)
    ).strftime("%Y%m%d")
    symbols = _require_symbols(form)
    cash = _float(form, "cash", 5000.0)
    config = _turtle_config(form)
    result = run_turtle_system(
        service=service,
        symbols=symbols,
        start_date=start,
        end_date=end,
        cash=cash,
        config=config,
        refresh=_checked(form, "refresh"),
        skip_errors=True,
    )
    tables = [
        TableBlock("Signals", result.signals),
        TableBlock("Errors", result.errors),
    ]
    if _checked(form, "execution_plan") and not result.signals.empty:
        quotes = service.get_realtime_quotes(result.signals["symbol"].dropna().astype(str).tolist())
        plan = build_execution_plan(
            result.signals,
            quotes,
            cash=cash,
            lot_size=config.lot_size,
            commission_rate=config.commission_rate,
            min_commission=config.min_commission,
            next_day_premium=_float(form, "next_day_premium", 0.02),
            volume_limit_pct=_float(form, "volume_limit_pct", 0.10),
        )
        tables.insert(1, TableBlock("Execution Plan", plan))
    return RenderResult(
        title="Full Turtle System",
        summaries=[
            _request_summary(
                form,
                ["symbols", "start", "end", "cash", "risk_pct", "s1_entry", "s1_exit", "s2_entry", "s2_exit", "source", "refresh"],
            )
        ],
        tables=tables,
    )


def handle_turtle_backtest(form: dict[str, str]) -> RenderResult:
    service = _service(form)
    symbols = _require_symbols(form)
    start = _value(form, "start")
    end = _value(form, "end")
    if not start or not end:
        raise ValueError("Backtest needs Start and End dates.")
    result = backtest_turtle_system(
        service=service,
        symbols=symbols,
        start_date=start,
        end_date=end,
        initial_cash=_float(form, "cash", 100000.0),
        config=_turtle_config(form),
        refresh=_checked(form, "refresh"),
        skip_errors=True,
    )
    return RenderResult(
        title="Turtle Backtest",
        summaries=[
            _request_summary(
                form,
                ["symbols", "start", "end", "cash", "risk_pct", "s1_entry", "s1_exit", "s2_entry", "s2_exit", "source", "refresh"],
            )
        ],
        tables=[
            TableBlock("Summary", result.summary),
            TableBlock("Trades", result.trades.tail(100)),
            TableBlock("Equity", result.equity.tail(120)),
            TableBlock("Drawdowns", result.drawdowns),
            TableBlock("Symbol PnL", result.symbol_pnl),
            TableBlock("Errors", result.errors),
        ],
    )


def handle_portfolio_init(form: dict[str, str]) -> RenderResult:
    store = ManualPortfolioStore(_value(form, "path", DEFAULT_USER_PATH))
    portfolio = store.initialize(
        principal=_float(form, "principal", 5000.0),
        cash=_optional_float(form, "cash"),
        commission_rate=_float(form, "commission_rate", 0.0003),
        min_commission=_float(form, "min_commission", 5.0),
        stamp_tax_rate=_float(form, "stamp_tax_rate", 0.001),
    )
    return RenderResult("Portfolio Initialized", summaries=[portfolio.summary()])


def handle_portfolio_buy(form: dict[str, str]) -> RenderResult:
    store = ManualPortfolioStore(_value(form, "path", DEFAULT_USER_PATH))
    symbol = _value(form, "symbol")
    portfolio = store.buy(
        symbol=symbol,
        name=_lookup_stock_name(symbol, form),
        price=_float(form, "price", 0.0),
        shares=_int(form, "shares", 0),
        target_sell_price=_optional_float(form, "target_sell_price"),
        strategy=_optional(form, "strategy_meta") or "",
        system=_optional(form, "system") or "",
        entry_reason=_optional(form, "entry_reason") or "",
        signal_date=_optional(form, "signal_date"),
        execution_date=_optional(form, "execution_date"),
        note=_optional(form, "note") or "",
    )
    return RenderResult(
        "Buy Recorded",
        summaries=[portfolio.summary()],
        tables=[TableBlock("Positions", portfolio.positions), TableBlock("Trades", portfolio.trades.tail(20))],
    )


def handle_portfolio_sell(form: dict[str, str]) -> RenderResult:
    store = ManualPortfolioStore(_value(form, "path", DEFAULT_USER_PATH))
    portfolio = store.sell(
        symbol=_value(form, "symbol"),
        price=_float(form, "price", 0.0),
        shares=_int(form, "shares", 0),
        strategy=_optional(form, "strategy_meta") or "",
        system=_optional(form, "system") or "",
        exit_reason=_optional(form, "exit_reason") or "",
        signal_date=_optional(form, "signal_date"),
        execution_date=_optional(form, "execution_date"),
        note=_optional(form, "note") or "",
    )
    return RenderResult(
        "Sell Recorded",
        summaries=[portfolio.summary(_marks(form))],
        tables=[TableBlock("Positions", portfolio.positions), TableBlock("Trades", portfolio.trades.tail(20))],
    )


def handle_portfolio_summary(form: dict[str, str]) -> RenderResult:
    store = ManualPortfolioStore(_value(form, "path", DEFAULT_USER_PATH))
    portfolio = store.load()
    return RenderResult(
        "Portfolio Summary",
        summaries=[portfolio.summary(_marks(form))],
        tables=[TableBlock("Positions", portfolio.positions), TableBlock("Trades", portfolio.trades.tail(50))],
    )


def render_page(
    page: str = "strategy",
    result: RenderResult | None = None,
    error: str | None = None,
    form: dict[str, str] | None = None,
) -> str:
    form = form or {}
    page = page if page in PAGES else "strategy"
    page_body = {
        "strategy": render_strategy_section(form),
        "turtle": render_turtle_section(form),
        "backtest": render_backtest_section(form),
        "portfolio": render_portfolio_section(form),
    }[page]
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Stock Picker Local Web</title>
  <style>{CSS}</style>
</head>
<body>
  <header>
    <div>
      <h1>Stock Picker</h1>
      <p>本地策略、海龟系统、回测和手动账户工作台</p>
    </div>
    <div class="status">Local only · {html.escape(datetime.now().strftime("%Y-%m-%d %H:%M"))}</div>
  </header>
  <nav>
    {nav_link("strategy", "策略", page)}
    {nav_link("turtle", "海龟系统", page)}
    {nav_link("backtest", "回测", page)}
    {nav_link("portfolio", "账户", page)}
  </nav>
  <main>
    {render_message(result, error)}
    {page_body}
  </main>
</body>
</html>"""


def nav_link(target: str, label: str, current: str) -> str:
    active = ' class="active"' if target == current else ""
    return f'<a href="/{target}"{active}>{html.escape(label)}</a>'


def render_strategy_section(form: dict[str, str]) -> str:
    return f"""
    <section id="strategy">
      <h2>策略运行</h2>
      <form method="post" action="/strategy">
        <div class="grid">
          {select("strategy", STRATEGY_NAMES, "turtle", "策略", form)}
          {input_text("symbols", "股票池", "", form)}
          {input_text("start", "开始日期", "20250527", form)}
          {input_text("end", "结束日期", "20260527", form)}
          {input_text("as_of", "快照日期", "20260527", form)}
          {input_number("top", "Top", "10", form)}
          {source_fields(form)}
        </div>
        <button type="submit">运行策略</button>
      </form>
    </section>"""


def render_turtle_section(form: dict[str, str]) -> str:
    return f"""
    <section id="turtle">
      <h2>完整海龟系统</h2>
      <form method="post" action="/turtle">
        <div class="grid">
          {input_text("symbols", "股票池", "", form)}
          {input_text("start", "开始日期", "", form)}
          {input_text("end", "结束/As of", "20260528", form)}
          {input_number("cash", "现金/权益", "5000", form)}
          {turtle_fields(form)}
          {execution_fields(form)}
          {source_fields(form)}
        </div>
        {checkbox("execution_plan", "生成手工执行计划", form, checked=True)}
        <button type="submit">运行海龟系统</button>
      </form>
    </section>"""


def render_backtest_section(form: dict[str, str]) -> str:
    return f"""
    <section id="backtest">
      <h2>海龟状态机回测</h2>
      <form method="post" action="/turtle-backtest">
        <div class="grid">
          {input_text("symbols", "股票池", "", form)}
          {input_text("start", "开始日期", "20260228", form)}
          {input_text("end", "结束日期", "20260527", form)}
          {input_number("cash", "初始资金", "100000", form)}
          {turtle_fields(form)}
          {source_fields(form)}
        </div>
        <button type="submit">运行回测</button>
      </form>
    </section>"""


def render_portfolio_section(form: dict[str, str]) -> str:
    return f"""
    <section id="portfolio">
      <h2>手动账户</h2>
      <div class="columns">
        <form method="post" action="/portfolio-init">
          <h3>初始化</h3>
          {portfolio_path(form)}
          {input_number("principal", "本金", "5000", form)}
          {input_number("cash", "现金", "", form)}
          {input_number("commission_rate", "佣金率", "0.0003", form)}
          {input_number("min_commission", "最低佣金", "5", form)}
          {input_number("stamp_tax_rate", "印花税率", "0.001", form)}
          <button type="submit">初始化账户</button>
        </form>
        <form method="post" action="/portfolio-summary">
          <h3>查看</h3>
          {portfolio_path(form)}
          {input_text("marks", "标记价格", "", form)}
          <button type="submit">查看账户</button>
        </form>
      </div>
      <div class="columns">
        <form method="post" action="/portfolio-buy">
          <h3>买入记录</h3>
          {portfolio_path(form)}
          {trade_fields(side="buy", form=form)}
          <button type="submit">记录买入</button>
        </form>
        <form method="post" action="/portfolio-sell">
          <h3>卖出记录</h3>
          {portfolio_path(form)}
          {trade_fields(side="sell", form=form)}
          <button type="submit">记录卖出</button>
        </form>
      </div>
    </section>
    """


def render_message(result: RenderResult | None, error: str | None) -> str:
    if error:
        return f'<section class="message error"><strong>Error</strong><p>{html.escape(error)}</p></section>'
    if result is None:
        return ""
    parts = [f'<section class="message"><h2>{html.escape(result.title)}</h2>']
    for summary in result.summaries:
        parts.append(render_summary(summary))
    for table in result.tables:
        parts.append(render_table(table.title, table.frame))
    parts.append("</section>")
    return "\n".join(parts)


def render_summary(values: dict[str, object]) -> str:
    rows = []
    for key, value in values.items():
        display = f"{value:.6f}" if isinstance(value, float) else str(value)
        rows.append(f"<dt>{html.escape(str(key))}</dt><dd>{html.escape(display)}</dd>")
    return f'<dl class="summary">{"".join(rows)}</dl>'


def render_table(title: str, frame: pd.DataFrame) -> str:
    if frame is None or frame.empty:
        return f"<h3>{html.escape(title)}</h3><p class=\"muted\">No rows.</p>"
    data = frame.copy()
    if len(data) > 200:
        data = data.tail(200)
    table = data.to_html(index=False, escape=True, classes="data-table", border=0)
    return (
        f"<h3>{html.escape(title)} <span>{len(frame)} rows</span></h3>"
        f'<div class="table-wrap">{table}</div>'
    )


def input_text(
    name: str,
    label: str,
    value: str = "",
    form: dict[str, str] | None = None,
) -> str:
    value = _field_value(form, name, value)
    return f'<label>{html.escape(label)}<input name="{name}" value="{html.escape(value)}"></label>'


def input_number(
    name: str,
    label: str,
    value: str = "",
    form: dict[str, str] | None = None,
) -> str:
    value = _field_value(form, name, value)
    return f'<label>{html.escape(label)}<input type="number" step="any" name="{name}" value="{html.escape(value)}"></label>'


def select(
    name: str,
    values: tuple[str, ...],
    default: str,
    label: str,
    form: dict[str, str] | None = None,
) -> str:
    current = _field_value(form, name, default)
    options = []
    for value in values:
        selected = " selected" if value == current else ""
        options.append(f'<option value="{value}"{selected}>{value}</option>')
    return f'<label>{html.escape(label)}<select name="{name}">{"".join(options)}</select></label>'


def checkbox(
    name: str,
    label: str,
    form: dict[str, str] | None = None,
    checked: bool = False,
) -> str:
    current = checked if form is None or not form else name in form
    marker = " checked" if current else ""
    return f'<label class="check"><input type="checkbox" name="{name}"{marker}> {html.escape(label)}</label>'


def source_fields(form: dict[str, str] | None = None) -> str:
    return (
        select("source", ("", "baostock", "akshare", "joinquant"), "", "历史源", form)
        + select("stock_source", ("", "akshare", "baostock", "joinquant"), "", "股票列表源", form)
        + select("realtime_source", ("sina", "akshare"), "sina", "实时源", form)
        + checkbox("refresh", "强制刷新", form)
    )


def turtle_fields(form: dict[str, str] | None = None) -> str:
    return (
        input_number("risk_pct", "单元风险", "0.01", form)
        + input_number("s1_entry", "S1 入场", "20", form)
        + input_number("s1_exit", "S1 退出", "10", form)
        + input_number("s2_entry", "S2 入场", "55", form)
        + input_number("s2_exit", "S2 退出", "20", form)
        + input_number("atr_period", "ATR 周期", "20", form)
        + input_number("max_units", "最多单元", "4", form)
        + input_number("slippage_rate", "滑点", "0", form)
    )


def execution_fields(form: dict[str, str] | None = None) -> str:
    return input_number("next_day_premium", "次日溢价上限", "0.02", form) + input_number(
        "volume_limit_pct", "成交量限制", "0.10", form
    )


def portfolio_path(form: dict[str, str] | None = None) -> str:
    return input_text("path", "账户路径", DEFAULT_USER_PATH, form)


def trade_fields(side: str, form: dict[str, str] | None = None) -> str:
    reason = "entry_reason" if side == "buy" else "exit_reason"
    return (
        input_text("symbol", "股票代码", "", form)
        + input_number("price", "成交价", "", form)
        + input_number("shares", "股数", "", form)
        + (
            input_number("target_sell_price", "目标卖出价", "", form)
            if side == "buy"
            else ""
        )
        + input_text("strategy_meta", "策略", "turtle_system", form)
        + input_text("system", "系统", "S1", form)
        + input_text(reason, "原因", "", form)
        + input_text("signal_date", "信号日", "", form)
        + input_text("execution_date", "执行日", "", form)
        + input_text("note", "备注", "", form)
    )


def _service(form: dict[str, str]) -> MarketDataService:
    config = DataSourceConfig(
        history_source=_optional(form, "source"),
        stock_source=_optional(form, "stock_source"),
        realtime_source=_optional(form, "realtime_source"),
    )
    if not any([config.history_source, config.stock_source, config.realtime_source]):
        return MarketDataService()
    return MarketDataService(data_source_config=config)


def _lookup_stock_name(symbol: str, form: dict[str, str]) -> str:
    normalized = normalize_symbol(symbol)
    service = _service(form)
    try:
        quotes = service.get_realtime_quotes([normalized])
        name = _first_name(quotes)
        if name:
            return name
    except Exception:
        pass
    try:
        snapshot = service.get_market_snapshot([normalized])
        name = _first_name(snapshot)
        if name:
            return name
    except Exception:
        pass
    try:
        for item in service.get_stock_symbols(refresh=False):
            if item.symbol == normalized and item.name:
                return item.name
    except Exception:
        pass
    return ""


def _first_name(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty or "name" not in frame:
        return ""
    for value in frame["name"].dropna().astype(str):
        if value.strip():
            return value.strip()
    return ""


def _turtle_config(form: dict[str, str]) -> TurtleConfig:
    return TurtleConfig(
        s1_entry=_int(form, "s1_entry", 20),
        s1_exit=_int(form, "s1_exit", 10),
        s2_entry=_int(form, "s2_entry", 55),
        s2_exit=_int(form, "s2_exit", 20),
        atr_period=_int(form, "atr_period", 20),
        risk_pct=_float(form, "risk_pct", 0.01),
        max_units=_int(form, "max_units", 4),
        slippage_rate=_float(form, "slippage_rate", 0.0),
    )


def _symbols(form: dict[str, str]) -> list[str]:
    raw = _optional(form, "symbols") or ""
    return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]


def _require_symbols(form: dict[str, str]) -> list[str]:
    symbols = _symbols(form)
    if not symbols:
        raise ValueError("Enter at least one symbol.")
    return symbols


def _marks(form: dict[str, str]) -> dict[str, float]:
    raw = _optional(form, "marks") or ""
    result: dict[str, float] = {}
    for item in raw.replace("\n", ",").split(","):
        if not item.strip():
            continue
        if "=" not in item:
            raise ValueError(f"Invalid mark: {item}")
        symbol, price = item.split("=", 1)
        result[symbol.strip()] = float(price.strip())
    return result


def _checked(form: dict[str, str], key: str) -> bool:
    return key in form


def _page_for_path(path: str) -> str:
    if path in {"/turtle", "/portfolio-buy", "/portfolio-sell", "/portfolio-init", "/portfolio-summary"}:
        return "portfolio" if path.startswith("/portfolio") else "turtle"
    if path == "/turtle-backtest":
        return "backtest"
    if path == "/strategy":
        return "strategy"
    return "strategy"


def _field_value(form: dict[str, str] | None, key: str, default: str) -> str:
    if form is None:
        return default
    return form.get(key, default)


def _request_summary(form: dict[str, str], keys: list[str]) -> dict[str, object]:
    values: dict[str, object] = {}
    for key in keys:
        if key == "refresh":
            values[key] = "yes" if key in form else "no"
            continue
        value = _optional(form, key)
        if value is not None:
            values[key] = value
    return values


def _optional(form: dict[str, str], key: str) -> str | None:
    value = form.get(key, "").strip()
    return value or None


def _value(form: dict[str, str], key: str, default: str = "") -> str:
    value = form.get(key, "").strip()
    return value or default


def _int(form: dict[str, str], key: str, default: int) -> int:
    value = _optional(form, key)
    return int(value) if value is not None else default


def _float(form: dict[str, str], key: str, default: float) -> float:
    value = _optional(form, key)
    return float(value) if value is not None else default


def _optional_float(form: dict[str, str], key: str) -> float | None:
    value = _optional(form, key)
    return float(value) if value is not None else None


class TableBlock:
    def __init__(self, title: str, frame: pd.DataFrame) -> None:
        self.title = title
        self.frame = frame


class RenderResult:
    def __init__(
        self,
        title: str,
        tables: list[TableBlock] | None = None,
        summaries: list[dict[str, object]] | None = None,
    ) -> None:
        self.title = title
        self.tables = tables or []
        self.summaries = summaries or []


CSS = """
:root {
  color-scheme: light;
  --line: #d8dee7;
  --text: #1f2937;
  --muted: #607086;
  --bg: #f6f8fb;
  --panel: #ffffff;
  --accent: #0f766e;
  --danger: #b42318;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  overflow-x: hidden;
  background: var(--bg);
  color: var(--text);
  font-family: "Segoe UI", Arial, sans-serif;
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 28px;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}
h1, h2, h3, p { margin-top: 0; }
h1 { font-size: 24px; margin-bottom: 4px; }
h2 { font-size: 18px; }
h3 { font-size: 15px; margin: 16px 0 8px; }
header p, .muted, .status { color: var(--muted); }
nav {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  gap: 18px;
  padding: 10px 28px;
  border-bottom: 1px solid var(--line);
  background: #eef3f7;
}
nav a { color: var(--text); text-decoration: none; font-weight: 600; }
nav a.active {
  color: var(--accent);
  text-decoration: underline;
  text-underline-offset: 6px;
}
main { padding: 18px 28px 40px; }
section, .message {
  max-width: 100%;
  margin-bottom: 18px;
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
}
.message { border-left: 4px solid var(--accent); }
.message.error { border-left-color: var(--danger); }
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 12px;
}
.columns {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 16px;
  margin-top: 12px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 5px;
  color: var(--muted);
  font-size: 13px;
}
label.check {
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  margin: 12px 16px 0 0;
}
input, select {
  width: 100%;
  min-height: 36px;
  padding: 7px 9px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
  color: var(--text);
  font: inherit;
}
button {
  margin-top: 14px;
  min-height: 38px;
  padding: 0 14px;
  border: 0;
  border-radius: 6px;
  background: var(--accent);
  color: #fff;
  font-weight: 700;
  cursor: pointer;
}
.summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 8px 16px;
  margin: 8px 0 16px;
}
.summary dt { color: var(--muted); font-size: 12px; }
.summary dd { margin: 0; font-weight: 700; }
.table-wrap {
  max-width: 100%;
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 6px;
}
.data-table {
  width: 100%;
  min-width: max-content;
  border-collapse: collapse;
  font-size: 13px;
}
.data-table th, .data-table td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
  white-space: nowrap;
}
.data-table th {
  position: sticky;
  top: 41px;
  background: #f2f5f8;
  z-index: 1;
}
h3 span { color: var(--muted); font-weight: 400; }
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Stock Picker web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), WebAppHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"Stock Picker local web app running at {url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
