from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class BacktestReport:
    tables: dict[str, pd.DataFrame]


def build_backtest_report(result) -> BacktestReport:
    tables = {
        "Summary": _copy_frame(result.summary),
        "Daily Portfolio": _copy_frame(result.daily_portfolio),
        "Daily Evaluation Detail": _copy_frame(result.evaluation_detail),
        "Trades": _copy_frame(result.trades),
        "Positions": _copy_frame(result.positions),
        "Symbol Performance": _copy_frame(result.symbol_performance),
        "Data Quality": _copy_frame(result.data_quality),
        "Parameters": _copy_frame(result.parameters),
    }
    return BacktestReport(tables=tables)


def export_backtest_excel(report: BacktestReport, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, frame in report.tables.items():
            safe = sheet_name[:31]
            frame.to_excel(writer, sheet_name=safe, index=False)
            worksheet = writer.sheets[safe]
            worksheet.freeze_panes = "A2"
            if frame.shape[1] > 0:
                worksheet.auto_filter.ref = worksheet.dimensions
            for cell in worksheet[1]:
                font = copy(cell.font)
                font.bold = True
                cell.font = font
            for column_cells in worksheet.columns:
                values = [str(cell.value) if cell.value is not None else "" for cell in column_cells]
                width = min(max(max((len(value) for value in values), default=8) + 2, 10), 48)
                worksheet.column_dimensions[column_cells[0].column_letter].width = width
    return path


def _copy_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None:
        return pd.DataFrame()
    data = frame.copy()
    return data
