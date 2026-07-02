from __future__ import annotations

from pathlib import Path

import pandas as pd

from stock_picker.reporting.backtest_report import build_backtest_report, export_backtest_excel
from stock_picker.strategies.event_backtest import BacktestSettings, EventBacktestEngine, Signal


def _event_result():
    prices = pd.DataFrame(
        [
            {"symbol": "600001.SH", "date": "2026-01-02", "time_point": "morning_open", "price": 10.0, "limit_status": "normal"},
            {"symbol": "600001.SH", "date": "2026-01-02", "time_point": "noon", "price": 10.2, "limit_status": "normal"},
            {"symbol": "600001.SH", "date": "2026-01-02", "time_point": "afternoon_open", "price": 10.4, "limit_status": "limit_up"},
            {"symbol": "600001.SH", "date": "2026-01-02", "time_point": "close", "price": 10.6, "limit_status": "normal"},
        ]
    )
    engine = EventBacktestEngine(BacktestSettings(initial_cash=20_000.0, force_final_liquidation=False))

    def signal_provider(context):
        if context.time_point == "noon":
            return [Signal(symbol="600001.SH", side="buy", shares=100, reason="中午信号")]
        return []

    return engine.run(prices, signal_provider=signal_provider)


def test_backtest_report_contains_required_sheets_and_failed_trades() -> None:
    report = build_backtest_report(_event_result())

    assert set(report.tables) >= {
        "Summary",
        "Daily Portfolio",
        "Daily Evaluation Detail",
        "Trades",
        "Positions",
        "Symbol Performance",
        "Data Quality",
        "Parameters",
    }
    assert report.tables["Trades"]["order_status"].tolist() == ["failed_limit_up"]
    rendered_titles = " ".join(report.tables)
    assert "未翻译字段" not in rendered_titles


def test_export_backtest_excel_has_all_sheets(tmp_path: Path) -> None:
    report = build_backtest_report(_event_result())
    output = tmp_path / "report.xlsx"

    export_backtest_excel(report, output)

    book = pd.ExcelFile(output)
    assert set(book.sheet_names) >= set(report.tables)

