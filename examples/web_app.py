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


class WebAppHandler(BaseHTTPRequestHandler):
    server_version = "StockPickerWeb/0.1"

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._send_text("ok")
            return
        if path != "/":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        self._send_page(render_page())

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            form = self._read_form()
            if path == "/strategy":
                result = handle_strategy(form)
            elif path == "/turtle":
                result = handle_turtle(form)
            elif path == "/turtle-backtest":
                result = handle_turtle_backtest(form)
            elif path == "/portfolio-init":
                result = handle_portfolio_init(form)
            elif path == "/portfolio-buy":
                result = handle_portfolio_buy(form)
            elif path == "/portfolio-sell":
                result = handle_portfolio_sell(form)
            elif path == "/portfolio-summary":
                result = handle_portfolio_summary(form)
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            self._send_page(render_page(result=result))
        except Exception as exc:
            self._send_page(render_page(error=str(exc)))

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
    return RenderResult(title="Full Turtle System", tables=tables)


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
    )
    return RenderResult("Portfolio Initialized", summaries=[portfolio.summary()])


def handle_portfolio_buy(form: dict[str, str]) -> RenderResult:
    store = ManualPortfolioStore(_value(form, "path", DEFAULT_USER_PATH))
    portfolio = store.buy(
        symbol=_value(form, "symbol"),
        name=_optional(form, "name") or "",
        price=_float(form, "price", 0.0),
        shares=_int(form, "shares", 0),
        fees=_float(form, "fees", 5.0),
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
        fees=_float(form, "fees", 5.0),
        tax_rate=_float(form, "tax_rate", 0.001),
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


def render_page(result: RenderResult | None = None, error: str | None = None) -> str:
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
    <a href="#strategy">策略</a>
    <a href="#turtle">海龟系统</a>
    <a href="#backtest">回测</a>
    <a href="#portfolio">账户</a>
  </nav>
  <main>
    {render_message(result, error)}
    <section id="strategy">
      <h2>策略运行</h2>
      <form method="post" action="/strategy">
        <div class="grid">
          {select("strategy", STRATEGY_NAMES, "turtle", "策略")}
          {input_text("symbols", "股票池", "600519,000001,600036")}
          {input_text("start", "开始日期", "20250527")}
          {input_text("end", "结束日期", "20260527")}
          {input_text("as_of", "快照日期", "20260527")}
          {input_number("top", "Top", "10")}
          {source_fields()}
        </div>
        <button type="submit">运行策略</button>
      </form>
    </section>
    <section id="turtle">
      <h2>完整海龟系统</h2>
      <form method="post" action="/turtle">
        <div class="grid">
          {input_text("symbols", "股票池", "600519,000001,600036")}
          {input_text("start", "开始日期", "")}
          {input_text("end", "结束/As of", "20260528")}
          {input_number("cash", "现金/权益", "5000")}
          {turtle_fields()}
          {execution_fields()}
          {source_fields()}
        </div>
        <label class="check"><input type="checkbox" name="execution_plan" checked> 生成手工执行计划</label>
        <button type="submit">运行海龟系统</button>
      </form>
    </section>
    <section id="backtest">
      <h2>海龟状态机回测</h2>
      <form method="post" action="/turtle-backtest">
        <div class="grid">
          {input_text("symbols", "股票池", "600519,000001,600036")}
          {input_text("start", "开始日期", "20260228")}
          {input_text("end", "结束日期", "20260527")}
          {input_number("cash", "初始资金", "100000")}
          {turtle_fields()}
          {source_fields()}
        </div>
        <button type="submit">运行回测</button>
      </form>
    </section>
    <section id="portfolio">
      <h2>手动账户</h2>
      <div class="columns">
        <form method="post" action="/portfolio-init">
          <h3>初始化</h3>
          {portfolio_path()}
          {input_number("principal", "本金", "5000")}
          {input_number("cash", "现金", "")}
          <button type="submit">初始化账户</button>
        </form>
        <form method="post" action="/portfolio-summary">
          <h3>查看</h3>
          {portfolio_path()}
          {input_text("marks", "标记价格", "600172=15.20")}
          <button type="submit">查看账户</button>
        </form>
      </div>
      <div class="columns">
        <form method="post" action="/portfolio-buy">
          <h3>买入记录</h3>
          {portfolio_path()}
          {trade_fields(side="buy")}
          <button type="submit">记录买入</button>
        </form>
        <form method="post" action="/portfolio-sell">
          <h3>卖出记录</h3>
          {portfolio_path()}
          {trade_fields(side="sell")}
          <button type="submit">记录卖出</button>
        </form>
      </div>
    </section>
  </main>
</body>
</html>"""


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
    return f"<h3>{html.escape(title)} <span>{len(frame)} rows</span></h3>{table}"


def input_text(name: str, label: str, value: str = "") -> str:
    return f'<label>{html.escape(label)}<input name="{name}" value="{html.escape(value)}"></label>'


def input_number(name: str, label: str, value: str = "") -> str:
    return f'<label>{html.escape(label)}<input type="number" step="any" name="{name}" value="{html.escape(value)}"></label>'


def select(name: str, values: tuple[str, ...], default: str, label: str) -> str:
    options = []
    for value in values:
        selected = " selected" if value == default else ""
        options.append(f'<option value="{value}"{selected}>{value}</option>')
    return f'<label>{html.escape(label)}<select name="{name}">{"".join(options)}</select></label>'


def source_fields() -> str:
    return (
        select("source", ("", "baostock", "akshare", "joinquant"), "", "历史源")
        + select("stock_source", ("", "akshare", "baostock", "joinquant"), "", "股票列表源")
        + select("realtime_source", ("sina", "akshare"), "sina", "实时源")
        + '<label class="check"><input type="checkbox" name="refresh"> 强制刷新</label>'
    )


def turtle_fields() -> str:
    return (
        input_number("risk_pct", "单元风险", "0.01")
        + input_number("s1_entry", "S1 入场", "20")
        + input_number("s1_exit", "S1 退出", "10")
        + input_number("s2_entry", "S2 入场", "55")
        + input_number("s2_exit", "S2 退出", "20")
        + input_number("atr_period", "ATR 周期", "20")
        + input_number("max_units", "最多单元", "4")
        + input_number("slippage_rate", "滑点", "0")
    )


def execution_fields() -> str:
    return input_number("next_day_premium", "次日溢价上限", "0.02") + input_number(
        "volume_limit_pct", "成交量限制", "0.10"
    )


def portfolio_path() -> str:
    return input_text("path", "账户路径", DEFAULT_USER_PATH)


def trade_fields(side: str) -> str:
    reason = "entry_reason" if side == "buy" else "exit_reason"
    return (
        input_text("symbol", "股票代码", "600172")
        + input_text("name", "名称", "")
        + input_number("price", "成交价", "")
        + input_number("shares", "股数", "")
        + input_number("fees", "手续费", "5")
        + (input_number("target_sell_price", "目标卖出价", "") if side == "buy" else input_number("tax_rate", "印花税率", "0.001"))
        + input_text("strategy_meta", "策略", "turtle_system")
        + input_text("system", "系统", "S1")
        + input_text(reason, "原因", "")
        + input_text("signal_date", "信号日", "")
        + input_text("execution_date", "执行日", "")
        + input_text("note", "备注", "")
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
main { padding: 18px 28px 40px; }
section, .message {
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
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.data-table th, .data-table td {
  padding: 7px 8px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
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
