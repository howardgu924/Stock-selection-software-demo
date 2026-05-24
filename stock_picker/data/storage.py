from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


class SQLiteMarketDataStore:
    def __init__(self, db_path: str | Path = "data/market_data.sqlite3") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def save_history(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        rows = frame.to_dict("records")
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO historical_prices (
                    symbol, date, open, high, low, close, volume, amount,
                    amplitude, pct_chg, change, turnover
                )
                VALUES (
                    :symbol, :date, :open, :high, :low, :close, :volume, :amount,
                    :amplitude, :pct_chg, :change, :turnover
                )
                ON CONFLICT(symbol, date) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    amount = excluded.amount,
                    amplitude = excluded.amplitude,
                    pct_chg = excluded.pct_chg,
                    change = excluded.change,
                    turnover = excluded.turnover
                """,
                rows,
            )

    def load_history(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        start = self._date_for_query(start_date)
        end = self._date_for_query(end_date)
        with self._connect() as conn:
            return pd.read_sql_query(
                """
                SELECT
                    symbol, date, open, high, low, close, volume, amount,
                    amplitude, pct_chg, change, turnover
                FROM historical_prices
                WHERE symbol = ? AND date >= ? AND date <= ?
                ORDER BY date
                """,
                conn,
                params=(symbol, start, end),
            )

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS historical_prices (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    amplitude REAL,
                    pct_chg REAL,
                    change REAL,
                    turnover REAL,
                    PRIMARY KEY (symbol, date)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    @staticmethod
    def _date_for_query(value: str) -> str:
        if "-" in value:
            return value
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
