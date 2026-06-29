from __future__ import annotations

import pandas as pd

from examples import web_app
from stock_picker.user import ManualPortfolioStore, WatchlistStore


def _history(symbol: str, closes: list[float]) -> pd.DataFrame:
    dates = pd.date_range("2026-04-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "symbol": symbol,
            "date": dates.strftime("%Y-%m-%d"),
            "open": closes,
            "high": [value * 1.01 for value in closes],
            "low": [value * 0.99 for value in closes],
            "close": closes,
            "volume": [100000] * len(closes),
        }
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
        return _history("000001.SH", [3000 + i * 10 for i in range(40)])


def test_web_app_parses_symbols_and_marks() -> None:
    form = {"symbols": "600519, 000001\n600036", "marks": "600519=1500.5,000001=12.3"}

    assert web_app._symbols(form) == ["600519", "000001", "600036"]
    assert web_app._marks(form) == {"600519": 1500.5, "000001": 12.3}


def test_web_default_path_is_thermostat_and_hides_old_entries() -> None:
    html = web_app.render_page(page="unknown")

    assert "恒温器策略" in html
    assert 'action="/thermostat"' in html
    assert 'href="/thermostat"' in html
    assert 'href="/portfolio"' in html
    assert 'action="/turtle"' not in html
    assert 'action="/turtle-backtest"' not in html
    assert 'href="/turtle"' not in html
    assert "海龟系统" not in html
    assert "默认技术筛选" not in html
    assert 'href="/strategy"' not in html


def test_web_pages_use_workbench_shell() -> None:
    for page, title in [
        ("thermostat", "恒温器策略"),
        ("backtest", "恒温器回测诊断"),
        ("portfolio", "账户"),
    ]:
        html = web_app.render_page(page=page)

        assert 'class="workbench-page"' in html
        assert title in html
        assert "页面状态" in html
        assert "工作区" in html
        assert "海龟系统" not in html


def test_web_thermostat_page_shows_stock_pool_controls(tmp_path) -> None:
    store = WatchlistStore(tmp_path / "account")
    store.create("高关注")
    store.add_symbols("高关注", ["600519"])
    store.save_last_manual_input("600519,000001")

    html = web_app.render_page(
        page="thermostat",
        form={"account_path": str(tmp_path / "account"), "stock_pool_source": "watchlist"},
    )

    assert "股票池来源" in html
    assert 'name="stock_pool_source"' in html
    assert "手动输入" in html
    assert "自选股组合" in html
    assert "市场范围" in html
    assert "龙虎榜" in html
    assert "同花顺龙虎榜" in html
    assert "高关注" in html
    assert "600519,000001" not in html
    assert "剔除科创板" in html
    assert "将在后续阶段接入" not in html
    assert "海龟系统" not in html


def test_web_thermostat_uses_conditional_stock_pool_controls(tmp_path) -> None:
    store = WatchlistStore(tmp_path / "account")
    store.create("高关注")
    store.add_symbols("高关注", ["600519"])

    manual_html = web_app.render_page(
        page="thermostat",
        form={"account_path": str(tmp_path / "account"), "stock_pool_source": "manual", "symbols": "600519 000001"},
    )
    watchlist_html = web_app.render_page(
        page="thermostat",
        form={"account_path": str(tmp_path / "account"), "stock_pool_source": "watchlist"},
    )
    empty_watchlist_html = web_app.render_page(
        page="thermostat",
        form={"account_path": str(tmp_path / "empty"), "stock_pool_source": "watchlist"},
    )
    market_html = web_app.render_page(page="thermostat", form={"stock_pool_source": "market_range"})
    lhb_html = web_app.render_page(page="thermostat", form={"stock_pool_source": "lhb", "lhb_range": "1w"})
    custom_lhb_html = web_app.render_page(page="thermostat", form={"stock_pool_source": "lhb", "lhb_range": "custom"})

    assert "编辑手动股票池" in manual_html
    assert "仅本次使用" in manual_html
    assert "已识别股票数量" in manual_html
    assert "保存手动股票池" not in manual_html
    assert "自选股组合名称" not in manual_html
    assert "高关注" in watchlist_html
    assert "暂无自选组合，请到账户页创建" in empty_watchlist_html
    assert "沪深 A 股" in market_html
    assert "创业板" in market_html
    assert "北交所" in market_html
    assert 'type="checkbox" name="market_range"' in market_html
    assert "龙虎榜开始日期" not in lhb_html
    assert "龙虎榜结束日期" not in lhb_html
    assert "龙虎榜开始日期" in custom_lhb_html
    assert "龙虎榜结束日期" in custom_lhb_html


def test_web_thermostat_uses_date_range_account_cash_and_advanced_settings(tmp_path) -> None:
    account_path = tmp_path / "account"
    ManualPortfolioStore(account_path).initialize(principal=100000, cash=87654)

    initialized_html = web_app.render_page(page="thermostat", form={"account_path": str(account_path)})
    missing_html = web_app.render_page(page="thermostat", form={"account_path": str(tmp_path / "missing")})
    simulated_html = web_app.render_page(page="thermostat", form={"use_simulated_cash": "on"})
    custom_range_html = web_app.render_page(page="thermostat", form={"strategy_date_range": "custom"})

    assert "策略日期范围" in initialized_html
    assert "最近 1 个月" in initialized_html
    assert "最近 3 个月" in initialized_html
    assert "最近半年" in initialized_html
    assert "最近 1 年" in initialized_html
    assert "策略开始日期" not in initialized_html
    assert "策略结束日期" not in initialized_html
    assert "策略开始日期" in custom_range_html
    assert "策略结束日期" in custom_range_html
    assert "账户现金" in initialized_html
    assert "87654" in initialized_html
    assert 'name="cash"' not in initialized_html
    assert "账户未初始化，请先到账户页初始化账户" in missing_html
    assert "使用模拟资金" in initialized_html
    assert 'name="cash"' not in initialized_html
    assert "临时策略测算" in simulated_html
    assert 'name="cash"' in simulated_html
    assert "高级设置" in initialized_html
    assert "数据与执行设置" in initialized_html


def test_web_can_save_manual_input_as_watchlist(tmp_path) -> None:
    result = web_app.handle_watchlist_save_manual(
        {
            "path": str(tmp_path / "account"),
            "symbols": "600519,000001",
            "watchlist_name": "高关注",
        }
    )

    saved = WatchlistStore(tmp_path / "account").get("高关注")

    assert saved is not None
    assert saved.symbols == ["600519.SH", "000001.SZ"]
    assert result.title == "自选股已保存"


def test_web_portfolio_page_uses_overview_and_function_tabs(tmp_path) -> None:
    account_path = tmp_path / "account"
    store = ManualPortfolioStore(account_path)
    store.initialize(principal=100000, cash=90000)
    store.buy("600001", name="A", price=10.0, shares=100, fees=0.0)
    for idx in range(6):
        store.adjust_cost("600001", avg_cost=10.0 + idx * 0.1, note=f"note-{idx}")

    html = web_app.render_page(page="portfolio", form={"path": str(account_path)})

    assert "账户概览" in html
    assert 'class="overview-card"' in html
    for label in ["本金", "现金", "持仓市值", "总资产", "总收益", "总收益率", "已实现盈亏", "浮动盈亏", "持仓数量", "胜率", "盈亏比", "最大回撤", "佣金率", "印花税率"]:
        assert label in html
    assert "当前持仓" in html
    assert "查看全部持仓" in html
    assert "交易流水" in html
    assert "查看全部交易流水" in html
    assert html.count("adjust_cost") <= 5
    assert "功能操作区" in html
    for tab in ["自选组合", "账户设置", "持仓与估值", "买入 / 卖出", "成本调整", "交易记录"]:
        assert tab in html
    assert "高级信息" in html
    assert "会修改持仓成本记录" in html


def test_web_portfolio_empty_states(tmp_path) -> None:
    uninitialized = web_app.render_page(page="portfolio", form={"path": str(tmp_path / "missing")})
    initialized_path = tmp_path / "account"
    ManualPortfolioStore(initialized_path).initialize(principal=100000)
    initialized = web_app.render_page(page="portfolio", form={"path": str(initialized_path)})

    assert "账户未初始化" in uninitialized
    assert "初始化账户" in uninitialized
    assert "暂无持仓" in initialized
    assert "暂无交易流水" in initialized
    assert "暂无自选组合" in initialized


def test_web_portfolio_page_exposes_watchlist_management_without_hiding_account_forms(tmp_path) -> None:
    WatchlistStore(tmp_path / "account").create("高关注")

    html = web_app.render_page(page="portfolio", form={"path": str(tmp_path / "account")})

    assert "自选股组合" in html
    assert "自选股不是持仓" in html
    assert 'action="/watchlist-create"' in html
    assert 'action="/watchlist-add-symbol"' in html
    assert 'action="/watchlist-remove-symbol"' in html
    assert 'action="/watchlist-rename"' in html
    assert 'action="/watchlist-delete"' in html
    assert "高关注" in html
    assert 'action="/portfolio-buy"' in html
    assert 'action="/portfolio-sell"' in html


def test_web_normal_pages_do_not_show_old_strategy_entries() -> None:
    for page in ["thermostat", "backtest", "portfolio"]:
        html = web_app.render_page(page=page)
        assert 'action="/turtle"' not in html
        assert 'action="/turtle-backtest"' not in html
        assert 'href="/turtle"' not in html
        assert "海龟系统" not in html
        assert "旧策略列表" not in html


def test_web_thermostat_result_contains_market_holdings_and_candidates(tmp_path, monkeypatch) -> None:
    store = ManualPortfolioStore(tmp_path / "account")
    store.initialize(principal=100000, cash=80000)
    store.buy("600001", name="Held", price=18.0, shares=100, fees=0.0, strategy="thermostat", system="risk_control")
    fake = FakeWebService(
        {
            "600001.SH": _history("600001.SH", [20 - i * 0.25 for i in range(40)]),
            "600002.SH": _history("600002.SH", [10 + i * 0.25 for i in range(40)]),
        },
        quotes=pd.DataFrame(
            [
                {"symbol": "600002.SH", "name": "Candidate", "price": 19.8, "high": 20.0, "prev_close": 19.0, "volume": 100000}
            ]
        ),
    )
    monkeypatch.setattr(web_app, "_service", lambda form: fake)

    result = web_app.handle_thermostat(
        {
            "symbols": "600002",
            "account_path": str(tmp_path / "account"),
            "start": "20260401",
            "end": "20260510",
            "execution_plan": "on",
        }
    )

    titles = [table.title for table in result.tables]
    assert titles[:4] == ["Stock Pool Summary", "Market Overview", "Holding Advice", "New Buy Candidates"]
    pool_summary = next(table.frame for table in result.tables if table.title == "Stock Pool Summary")
    assert int(pool_summary.loc[0, "filtered_count"]) == 1
    holding = next(table.frame for table in result.tables if table.title == "Holding Advice")
    candidates = next(table.frame for table in result.tables if table.title == "New Buy Candidates")
    assert holding.loc[0, "symbol"] == "600001.SH"
    assert candidates.loc[0, "symbol"] == "600002.SH"


def test_web_thermostat_rejects_invalid_and_empty_stock_pool(tmp_path) -> None:
    account_path = str(tmp_path / "missing-account")
    empty = web_app.handle_thermostat({"symbols": " ", "account_path": account_path})
    invalid = web_app.handle_thermostat({"symbols": "abc", "account_path": account_path})

    assert empty.title == "股票池错误"
    assert "手动输入为空" in empty.summaries[0]["errors"]
    assert invalid.title == "股票池错误"
    assert "abc" in invalid.summaries[0]["warnings"]


def test_web_thermostat_backtest_outputs_diagnostics(monkeypatch) -> None:
    fake = FakeWebService({"600001.SH": _history("600001.SH", [10 + i * 0.1 for i in range(80)])})
    monkeypatch.setattr(web_app, "_service", lambda form: fake)

    result = web_app.handle_thermostat_backtest(
        {"symbols": "600001", "start": "20260101", "end": "20260320", "cash": "100000"}
    )

    titles = [table.title for table in result.tables]
    assert titles == ["Summary", "Regime Performance", "Diagnostics"]


def test_portfolio_trade_form_is_cleared_after_record() -> None:
    cleaned = web_app._clear_trade_form(
        {
            "path": "data/user/custom",
            "symbol": "600487",
            "price": "96.66",
            "shares": "100",
            "strategy_meta": "thermostat",
            "system": "trend_following",
            "realtime_source": "sina",
        }
    )

    html = web_app.render_page(page="portfolio", form=cleaned)

    assert cleaned == {"path": "data/user/custom", "realtime_source": "sina"}
    assert 'value="600487"' not in html
    assert 'value="96.66"' not in html
    assert 'value="100"' not in html
    assert 'value="thermostat"' in html
    assert 'value="trend_following"' in html


def test_portfolio_summary_refreshes_valuation_without_adding_trades(tmp_path, monkeypatch) -> None:
    store = ManualPortfolioStore(tmp_path / "account")
    store.initialize(principal=100000)
    store.buy("600001", name="A", price=10.0, shares=100, fees=0.0)
    fake = FakeWebService(quotes=pd.DataFrame([{"symbol": "600001.SH", "name": "A", "price": 12.0}]))
    monkeypatch.setattr(web_app, "_service", lambda form: fake)

    result = web_app.handle_portfolio_summary({"path": str(tmp_path / "account"), "refresh_valuation": "1"})
    portfolio = store.load()

    assert result.summaries[0]["position_value"] == 1200.0
    assert len(portfolio.trades) == 1
    assert "mark_price" in result.tables[0].frame.columns


def test_web_app_adjusts_portfolio_cost(tmp_path) -> None:
    store = ManualPortfolioStore(tmp_path / "account")
    store.initialize(principal=100000)
    store.buy("002579", name="A", price=16.922, shares=100, fees=5.0)

    result = web_app.handle_portfolio_adjust_cost(
        {"path": str(tmp_path / "account"), "symbol": "002579", "avg_cost": "19.922"}
    )

    positions = result.tables[0].frame
    trades = result.tables[1].frame
    assert positions.loc[0, "avg_cost"] == 19.922
    assert trades.iloc[-1]["side"] == "adjust_cost"


def test_portfolio_table_html_contains_position_data(tmp_path) -> None:
    store = ManualPortfolioStore(tmp_path / "account")
    store.initialize(principal=100000)
    portfolio = store.buy("600001", name="A", price=10.0, shares=100, fees=0.0)

    html = web_app.render_table("Positions", web_app._positions_view(portfolio.positions))

    assert "600001.SH" in html
    assert "<tbody>" in html
