from __future__ import annotations

import pandas as pd
import pytest

from examples import web_app
from stock_picker.user import ManualPortfolioStore


def _history(symbol: str, closes: list[float], width: float = 1.0) -> pd.DataFrame:
    dates = pd.date_range("2026-04-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "date": date.strftime("%Y-%m-%d"),
                "open": close,
                "high": close + width / 2,
                "low": close - width / 2,
                "close": close,
                "volume": 1000,
            }
            for date, close in zip(dates, closes)
        ]
    )


class FakeWebService:
    def __init__(self, histories: dict[str, pd.DataFrame] | None = None, quotes: pd.DataFrame | None = None) -> None:
        self.histories = histories or {}
        self.quotes = quotes if quotes is not None else pd.DataFrame(columns=["symbol", "name", "price"])

    def get_history(self, symbol: str, **kwargs) -> pd.DataFrame:
        return self.histories[symbol]

    def get_realtime_quotes(self, symbols=None) -> pd.DataFrame:
        if symbols:
            wanted = set(symbols)
            return self.quotes[self.quotes["symbol"].isin(wanted)].reset_index(drop=True)
        return self.quotes

    def get_market_snapshot(self, symbols=None) -> pd.DataFrame:
        return self.get_realtime_quotes(symbols)

    def get_stock_symbols(self, refresh: bool = False):
        return []

    def get_index_history(self, index_code: str, start_date: str, end_date: str, period: str = "daily") -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "index_code": index_code,
                    "date": "2026-04-01",
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1000,
                },
                {
                    "index_code": index_code,
                    "date": "2026-06-04",
                    "open": 105.0,
                    "high": 105.0,
                    "low": 105.0,
                    "close": 105.0,
                    "volume": 1000,
                },
            ]
        )


def test_web_app_parses_symbols_and_marks() -> None:
    form = {"symbols": "600519, 000001\n600036", "marks": "600519=1500.5,000001=12.3"}

    assert web_app._symbols(form) == ["600519", "000001", "600036"]
    assert web_app._marks(form) == {"600519": 1500.5, "000001": 12.3}


def test_web_app_renders_table_and_escapes_html() -> None:
    frame = pd.DataFrame([{"symbol": "600001.SH", "reason": "<breakout>"}])

    html = web_app.render_table("Signals", frame)

    assert "信号" in html
    assert "股票" in html
    assert "原因" in html
    assert 'class="table-wrap"' in html
    assert "&lt;breakout&gt;" in html
    assert "<breakout>" not in html


def test_web_app_page_contains_core_workflows() -> None:
    html = web_app.render_page(page="unknown")

    assert 'action="/strategy"' not in html
    assert 'action="/turtle"' in html
    assert 'href="/strategy"' not in html
    assert 'href="/turtle"' in html
    assert 'value="600519,000001,600036"' not in html
    assert 'value="600172"' not in html


def test_web_app_renders_separate_feature_pages() -> None:
    turtle = web_app.render_page(page="turtle")
    backtest = web_app.render_page(page="backtest")
    portfolio = web_app.render_page(page="portfolio")

    assert 'action="/turtle"' in turtle
    assert 'action="/strategy"' not in turtle
    assert 'action="/turtle-backtest"' in backtest
    assert 'name="pool_mode"' in backtest
    assert 'name="exclude_chinext"' in backtest
    assert 'action="/portfolio-buy"' in portfolio
    assert 'action="/portfolio-sell"' in portfolio
    assert 'action="/portfolio-adjust-cost"' in portfolio
    assert 'name="cash"' not in turtle
    assert "20260604" in turtle


def test_web_app_localizes_result_titles_and_values() -> None:
    result = web_app.RenderResult(
        "Portfolio Summary",
        summaries=[{"symbols": "002579", "position_value": 1200.0, "total_return": 0.2, "refresh": "yes"}],
        tables=[
            web_app.TableBlock(
                "Positions",
                pd.DataFrame([{"symbol": "600001.SH", "rank": 1.0, "action": "hold", "source": "portfolio_holding"}]),
            )
        ],
    )

    html = web_app.render_message(result, None)

    assert "账户概览" in html
    assert "股票池" in html
    assert "持仓市值" in html
    assert "总收益率" in html
    assert "当前持仓" in html
    assert "持有" in html
    assert "账户持仓" in html
    assert "1.000000" not in html


def test_web_app_preserves_submitted_form_values() -> None:
    html = web_app.render_page(form={"symbols": "600172", "start": "20260501", "refresh": "on"})

    assert 'value="600172"' in html
    assert 'value="20260501"' in html
    assert 'name="refresh" checked' in html


def test_web_app_can_render_last_form_on_refresh() -> None:
    web_app.LAST_FORM.clear()
    web_app.LAST_FORM.update({"symbols": "600172"})

    html = web_app.render_page(form=web_app.LAST_FORM)

    assert 'value="600172"' in html


def test_portfolio_trade_form_is_cleared_after_record() -> None:
    cleaned = web_app._clear_trade_form(
        {
            "path": "data/user/custom",
            "symbol": "600487",
            "price": "96.66",
            "shares": "100",
            "strategy_meta": "turtle_system",
            "system": "S1",
            "realtime_source": "sina",
        }
    )

    html = web_app.render_page(page="portfolio", form=cleaned)

    assert cleaned == {"path": "data/user/custom", "realtime_source": "sina"}
    assert 'value="600487"' not in html
    assert 'value="96.66"' not in html
    assert 'value="100"' not in html
    assert 'value="turtle_system"' in html
    assert 'value="S1"' in html


def test_turtle_universe_can_exclude_chinext_symbols() -> None:
    universe = {
        "symbols": ["300001.SZ", "301001.SZ", "600001.SH"],
        "pool": pd.DataFrame(
            [
                {"symbol": "300001.SZ", "code": "300001", "source": "manual"},
                {"symbol": "301001.SZ", "code": "301001", "source": "manual"},
                {"symbol": "600001.SH", "code": "600001", "source": "manual"},
            ]
        ),
        "lhb": pd.DataFrame(
            [
                {"code": "300001", "name": "A", "net_buy": 1.0, "rank": 1},
                {"code": "600001", "name": "B", "net_buy": 2.0, "rank": 2},
            ]
        ),
        "portfolio": None,
        "cash": 5000.0,
    }

    filtered = web_app._exclude_chinext_from_universe(universe)

    assert filtered["symbols"] == ["600001.SH"]
    assert filtered["pool"]["code"].tolist() == ["600001"]
    assert filtered["lhb"]["code"].tolist() == ["600001"]


def test_lhb_pool_forces_portfolio_holdings_into_turtle_universe(tmp_path, monkeypatch) -> None:
    store = ManualPortfolioStore(tmp_path / "account")
    store.initialize(principal=100000)
    store.buy("600002", name="Held", price=10.0, shares=100, fees=0.0, system="S1")
    monkeypatch.setattr(
        web_app,
        "build_lhb_candidates",
        lambda start, end, top: (
            pd.DataFrame([{"code": "600001", "name": "LHB", "net_buy": 100.0, "rank": 1}]),
            pd.DataFrame(),
        ),
    )
    fake = FakeWebService(
        {
            "600001.SH": _history("600001.SH", [10.0] * 30),
            "600002.SH": _history("600002.SH", [10.0] * 30),
        }
    )
    monkeypatch.setattr(web_app, "_service", lambda form: fake)

    result = web_app.handle_turtle(
        {
            "pool_mode": "lhb_top30",
            "lhb_start": "20260501",
            "lhb_end": "20260527",
            "account_path": str(tmp_path / "account"),
            "end": "20260527",
        }
    )

    pool = result.tables[0].frame
    assert pool["symbol"].tolist() == ["600001.SH", "600002.SH"]
    assert pool["source"].tolist() == ["lhb_top30", "portfolio_holding"]


def test_turtle_manual_pool_enriches_missing_stock_name(tmp_path, monkeypatch) -> None:
    store = ManualPortfolioStore(tmp_path / "account")
    store.initialize(principal=100000, cash=8000)
    fake = FakeWebService(
        {
            "002579.SZ": _history("002579.SZ", [10.0] * 30),
        },
        quotes=pd.DataFrame([{"symbol": "002579.SZ", "name": "中京电子", "price": 10.0}]),
    )
    monkeypatch.setattr(web_app, "_service", lambda form: fake)

    result = web_app.handle_turtle(
        {
            "pool_mode": "manual",
            "symbols": "002579",
            "account_path": str(tmp_path / "account"),
            "sync_holdings": "on",
            "end": "20260604",
            "cash": "5000",
        }
    )

    pool = result.tables[0].frame
    assert pool.loc[0, "name"] == "中京电子"
    assert result.summaries[0]["account_cash"] == 8000
    assert "cash" not in result.summaries[0]


def test_turtle_candidate_without_breakout_gets_evaluation_row(tmp_path, monkeypatch) -> None:
    store = ManualPortfolioStore(tmp_path / "account")
    store.initialize(principal=100000, cash=8000)
    fake = FakeWebService(
        {
            "002579.SZ": _history("002579.SZ", [10.0] * 40),
        },
        quotes=pd.DataFrame([{"symbol": "002579.SZ", "name": "中京电子", "price": 10.0}]),
    )
    monkeypatch.setattr(web_app, "_service", lambda form: fake)

    result = web_app.handle_turtle(
        {
            "pool_mode": "manual",
            "symbols": "002579",
            "account_path": str(tmp_path / "account"),
            "sync_holdings": "on",
            "start": "20260518",
            "end": "20260604",
        }
    )

    evaluation = next(table.frame for table in result.tables if table.title == "Candidate Evaluation")
    assert evaluation.loc[0, "symbol"] == "002579.SZ"
    assert evaluation.loc[0, "name"] == "中京电子"
    assert evaluation.loc[0, "evaluation_action"] == "no_signal"
    assert "未触发买入" in evaluation.loc[0, "reason"]
    new_signals = next(table.frame for table in result.tables if table.title == "New Buy Signals")
    assert new_signals.empty


def test_turtle_backtest_adds_benchmark_and_diagnostic_tables(monkeypatch) -> None:
    fake = FakeWebService(
        {
            "600001.SH": _history("600001.SH", [10.0] * 80),
        },
        quotes=pd.DataFrame([{"symbol": "600001.SH", "name": "A", "price": 10.0}]),
    )
    monkeypatch.setattr(web_app, "_service", lambda form: fake)

    result = web_app.handle_turtle_backtest(
        {
            "pool_mode": "manual",
            "symbols": "600001",
            "start": "20260415",
            "end": "20260604",
            "cash": "100000",
        }
    )

    summary = next(table.frame for table in result.tables if table.title == "Summary")
    pool = next(table.frame for table in result.tables if table.title == "Backtest Pool")
    candidate_diff = next(table.frame for table in result.tables if table.title == "Backtest Candidate Difference")
    trade_quality = next(table.frame for table in result.tables if table.title == "Trade Quality")
    monthly = next(table.frame for table in result.tables if table.title == "Monthly Returns")

    assert summary.loc[0, "benchmark_symbol"] == "000001.SH"
    assert summary.loc[0, "benchmark_return"] == pytest.approx(0.05)
    assert "excess_return" in summary.columns
    assert summary.loc[0, "trade_note"] == "未触发交易"
    assert pool["symbol"].tolist() == ["600001.SH"]
    assert candidate_diff.loc[0, "traded"] == "no"
    assert trade_quality.loc[0, "trade_count"] == 0
    assert not monthly.empty


def test_turtle_backtest_lhb_pool_can_exclude_chinext(monkeypatch) -> None:
    monkeypatch.setattr(
        web_app,
        "build_lhb_candidates",
        lambda start, end, top: (
            pd.DataFrame(
                [
                    {"code": "300001", "name": "创业", "net_buy": 100.0, "rank": 1},
                    {"code": "600001", "name": "主板", "net_buy": 90.0, "rank": 2},
                ]
            ),
            pd.DataFrame(),
        ),
    )
    fake = FakeWebService({"600001.SH": _history("600001.SH", [10.0] * 80)})
    monkeypatch.setattr(web_app, "_service", lambda form: fake)

    result = web_app.handle_turtle_backtest(
        {
            "pool_mode": "lhb_top30",
            "lhb_start": "20260601",
            "lhb_end": "20260604",
            "start": "20260415",
            "end": "20260604",
            "exclude_chinext": "on",
        }
    )

    pool = next(table.frame for table in result.tables if table.title == "Backtest Pool")
    assert pool["code"].tolist() == ["600001"]


def test_holding_advice_outputs_sell_hold_and_add(tmp_path) -> None:
    store = ManualPortfolioStore(tmp_path / "account")
    portfolio = store.initialize(principal=100000)
    portfolio = store.buy("600001", price=10.0, shares=100, fees=0.0, system="S1")
    portfolio = store.buy("600002", price=10.0, shares=100, fees=0.0, system="S1")
    portfolio = store.buy("600003", price=10.0, shares=100, fees=0.0, system="S1")
    service = FakeWebService(
        {
            "600001.SH": _history("600001.SH", [10.0] * 20 + [7.0]),
            "600002.SH": _history("600002.SH", [10.0] * 21),
            "600003.SH": _history("600003.SH", [10.0] * 20 + [10.6]),
        }
    )

    advice = web_app._holding_advice(service, portfolio, "20260501", "20260527", web_app.TurtleConfig(), False)

    actions = dict(zip(advice["symbol"], advice["action"]))
    assert actions["600001.SH"] == "sell"
    assert actions["600002.SH"] == "hold"
    assert actions["600003.SH"] == "add"


def test_portfolio_summary_refreshes_valuation_without_adding_trades(tmp_path, monkeypatch) -> None:
    store = ManualPortfolioStore(tmp_path / "account")
    store.initialize(principal=100000)
    store.buy("600001", name="A", price=10.0, shares=100, fees=0.0)
    fake = FakeWebService(
        quotes=pd.DataFrame([{"symbol": "600001.SH", "name": "A", "price": 12.0}])
    )
    monkeypatch.setattr(web_app, "_service", lambda form: fake)

    result = web_app.handle_portfolio_summary({"path": str(tmp_path / "account"), "refresh_valuation": "1"})
    portfolio = store.load()

    assert result.summaries[0]["position_value"] == 1200.0
    assert len(portfolio.trades) == 1
    assert "mark_price" in result.tables[0].frame.columns


def test_web_app_adjusts_portfolio_cost(tmp_path) -> None:
    store = ManualPortfolioStore(tmp_path / "account")
    store.initialize(principal=100000)
    store.buy("002579", name="中京电子", price=16.922, shares=100, fees=5.0)

    result = web_app.handle_portfolio_adjust_cost(
        {"path": str(tmp_path / "account"), "symbol": "002579", "avg_cost": "19.922"}
    )

    positions = result.tables[0].frame
    trades = result.tables[1].frame
    assert positions.loc[0, "avg_cost"] == pytest.approx(19.922)
    assert trades.iloc[-1]["side"] == "adjust_cost"


def test_portfolio_table_html_contains_position_data(tmp_path) -> None:
    store = ManualPortfolioStore(tmp_path / "account")
    store.initialize(principal=100000)
    portfolio = store.buy("600001", name="A", price=10.0, shares=100, fees=0.0)

    html = web_app.render_table("Positions", web_app._positions_view(portfolio.positions))

    assert "600001.SH" in html
    assert "<tbody>" in html
