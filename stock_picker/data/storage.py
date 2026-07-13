from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from pathlib import Path

import pandas as pd

from stock_picker.data.models import StockInfo


@dataclass(frozen=True)
class EventCacheValidationResult:
    ok: bool
    missing: list[dict[str, object]]
    warnings: list[dict[str, object]]


@dataclass(frozen=True)
class BacktestCacheValidationResult:
    ok: bool
    missing_dates: tuple[str, ...]
    available_warmup_count: int


class SQLiteMarketDataStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = Path(__file__).resolve().parents[2] / "data" / "market_data.sqlite3"
        self.db_path = Path(db_path).resolve()
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

    def save_event_prices(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        normalized = frame.copy()
        for column in _EVENT_PRICE_COLUMNS:
            if column not in normalized:
                normalized[column] = None
        rows = normalized[_EVENT_PRICE_COLUMNS].to_dict("records")
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO event_prices (
                    symbol, date, time_point, frequency, adjust_type, source,
                    price, open, high, low, close, prev_close, limit_up_price,
                    limit_down_price, is_suspended, limit_status, simulated,
                    warning, updated_at
                )
                VALUES (
                    :symbol, :date, :time_point, :frequency, :adjust_type, :source,
                    :price, :open, :high, :low, :close, :prev_close, :limit_up_price,
                    :limit_down_price, :is_suspended, :limit_status, :simulated,
                    :warning, CURRENT_TIMESTAMP
                )
                ON CONFLICT(symbol, date, time_point, frequency, adjust_type, source)
                DO UPDATE SET
                    price = excluded.price,
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    prev_close = excluded.prev_close,
                    limit_up_price = excluded.limit_up_price,
                    limit_down_price = excluded.limit_down_price,
                    is_suspended = excluded.is_suspended,
                    limit_status = excluded.limit_status,
                    simulated = excluded.simulated,
                    warning = excluded.warning,
                    updated_at = excluded.updated_at
                """,
                rows,
            )

    def save_backtest_daily_prices(self, frame: pd.DataFrame) -> None:
        if frame.empty:
            return
        normalized = frame.copy()
        for column in _BACKTEST_DAILY_PRICE_COLUMNS:
            if column not in normalized:
                normalized[column] = None
        rows = normalized[_BACKTEST_DAILY_PRICE_COLUMNS].to_dict("records")
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO backtest_daily_prices (
                    symbol, date, period, adjust_type, source, open, high, low,
                    close, volume, amount, prev_close, limit_up_price,
                    limit_down_price, is_suspended, limit_status, warning,
                    updated_at
                ) VALUES (
                    :symbol, :date, :period, :adjust_type, :source, :open, :high,
                    :low, :close, :volume, :amount, :prev_close, :limit_up_price,
                    :limit_down_price, :is_suspended, :limit_status, :warning,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT(symbol, date, period, adjust_type, source) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    amount = excluded.amount,
                    prev_close = excluded.prev_close,
                    limit_up_price = excluded.limit_up_price,
                    limit_down_price = excluded.limit_down_price,
                    is_suspended = excluded.is_suspended,
                    limit_status = excluded.limit_status,
                    warning = excluded.warning,
                    updated_at = excluded.updated_at
                """,
                rows,
            )

    def load_backtest_daily_prices(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str,
        adjust_type: str,
        source: str,
    ) -> pd.DataFrame:
        with self._connect() as conn:
            return pd.read_sql_query(
                f"""
                SELECT {", ".join(_BACKTEST_DAILY_PRICE_SELECT_COLUMNS)}
                FROM backtest_daily_prices
                WHERE symbol = ? AND date >= ? AND date <= ?
                  AND period = ? AND adjust_type = ? AND source = ?
                ORDER BY date
                """,
                conn,
                params=(
                    symbol,
                    self._date_for_query(start_date),
                    self._date_for_query(end_date),
                    period,
                    adjust_type,
                    source,
                ),
            )

    def validate_backtest_daily_prices(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str,
        adjust_type: str,
        source: str,
        expected_dates: list[str] | tuple[str, ...],
        warmup_before: str,
        required_warmup_count: int,
    ) -> BacktestCacheValidationResult:
        rows = self.load_backtest_daily_prices(
            symbol, start_date, end_date, period, adjust_type, source
        )
        cached_dates = set(rows["date"].astype(str)) if not rows.empty else set()
        normalized_expected = tuple(self._date_for_query(item) for item in expected_dates)
        missing = tuple(item for item in normalized_expected if item not in cached_dates)
        cutoff = self._date_for_query(warmup_before)
        warmup_count = sum(item < cutoff for item in cached_dates)
        return BacktestCacheValidationResult(
            ok=not missing and warmup_count >= required_warmup_count,
            missing_dates=missing,
            available_warmup_count=warmup_count,
        )

    def save_stock_symbols(self, symbols: list[StockInfo]) -> None:
        if not symbols:
            return

        rows = [
            {"symbol": item.symbol, "code": item.code, "name": item.name}
            for item in symbols
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO stock_symbols (symbol, code, name, updated_at)
                VALUES (:symbol, :code, :name, CURRENT_TIMESTAMP)
                ON CONFLICT(symbol) DO UPDATE SET
                    code = excluded.code,
                    name = excluded.name,
                    updated_at = excluded.updated_at
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

    def load_event_prices(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        time_points: list[str] | None = None,
    ) -> pd.DataFrame:
        if not symbols:
            return pd.DataFrame(columns=_EVENT_PRICE_SELECT_COLUMNS)
        start = self._date_for_query(start_date)
        end = self._date_for_query(end_date)
        symbol_marks = ",".join("?" for _ in symbols)
        params: list[object] = [*symbols, start, end]
        time_clause = ""
        if time_points:
            time_marks = ",".join("?" for _ in time_points)
            time_clause = f" AND time_point IN ({time_marks})"
            params.extend(time_points)
        with self._connect() as conn:
            return pd.read_sql_query(
                f"""
                SELECT {", ".join(_EVENT_PRICE_SELECT_COLUMNS)}
                FROM event_prices
                WHERE symbol IN ({symbol_marks})
                  AND date >= ?
                  AND date <= ?
                  {time_clause}
                ORDER BY date, time_point, symbol
                """,
                conn,
                params=params,
            )

    def validate_event_cache(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        required_time_points: list[str],
    ) -> EventCacheValidationResult:
        rows = self.load_event_prices(symbols, start_date, end_date)
        missing: list[dict[str, object]] = []
        warnings: list[dict[str, object]] = []
        if rows.empty:
            for symbol in symbols:
                for time_point in required_time_points:
                    missing.append(
                        {
                            "symbol": symbol,
                            "date": self._date_for_query(start_date),
                            "time_point": time_point,
                            "field": "event_price",
                        }
                    )
            return EventCacheValidationResult(False, missing, warnings)

        dates = sorted(rows["date"].unique().tolist())
        for symbol in symbols:
            symbol_rows = rows[rows["symbol"] == symbol]
            for date in dates:
                date_rows = symbol_rows[symbol_rows["date"] == date]
                for time_point in required_time_points:
                    point_rows = date_rows[date_rows["time_point"] == time_point]
                    if point_rows.empty:
                        missing.append(
                            {
                                "symbol": symbol,
                                "date": date,
                                "time_point": time_point,
                                "field": "event_price",
                            }
                        )
                        warnings.append(
                            {
                                "symbol": symbol,
                                "date": date,
                                "time_point": time_point,
                                "field": "limit_status",
                                "warning": f"missing limit status for {time_point}",
                            }
                        )
                        continue
                    row = point_rows.iloc[0]
                    for field in ("prev_close", "limit_up_price", "limit_down_price", "limit_status"):
                        if pd.isna(row.get(field)) or row.get(field) in {"", None}:
                            warnings.append(
                                {
                                    "symbol": symbol,
                                    "date": date,
                                    "time_point": time_point,
                                    "field": field,
                                    "warning": f"missing limit field: {field}",
                                }
                            )
        if not warnings:
            for row in rows.to_dict("records"):
                warning = str(row.get("warning") or "")
                status = str(row.get("limit_status") or "")
                if warning or status == "limit_status_unknown":
                    warnings.append(
                        {
                            "symbol": row["symbol"],
                            "date": row["date"],
                            "time_point": row["time_point"],
                            "field": "limit_status",
                            "warning": warning or "limit_status_unknown",
                        }
                    )
        return EventCacheValidationResult(not missing, missing, warnings)

    def load_stock_symbols(self) -> list[StockInfo]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT symbol, code, name
                FROM stock_symbols
                ORDER BY symbol
                """
            ).fetchall()

        return [
            StockInfo(symbol=row[0], code=row[1], name=row[2])
            for row in rows
        ]

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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_symbols (
                    symbol TEXT NOT NULL PRIMARY KEY,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS event_prices (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    time_point TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    adjust_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    price REAL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    prev_close REAL,
                    limit_up_price REAL,
                    limit_down_price REAL,
                    is_suspended INTEGER,
                    limit_status TEXT,
                    simulated INTEGER,
                    warning TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (
                        symbol, date, time_point, frequency, adjust_type, source
                    )
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_daily_prices (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    period TEXT NOT NULL,
                    adjust_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    prev_close REAL,
                    limit_up_price REAL,
                    limit_down_price REAL,
                    is_suspended INTEGER,
                    limit_status TEXT,
                    warning TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, date, period, adjust_type, source)
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


_EVENT_PRICE_COLUMNS = [
    "symbol",
    "date",
    "time_point",
    "frequency",
    "adjust_type",
    "source",
    "price",
    "open",
    "high",
    "low",
    "close",
    "prev_close",
    "limit_up_price",
    "limit_down_price",
    "is_suspended",
    "limit_status",
    "simulated",
    "warning",
]

_EVENT_PRICE_SELECT_COLUMNS = [
    *_EVENT_PRICE_COLUMNS,
    "updated_at",
]

_BACKTEST_DAILY_PRICE_COLUMNS = [
    "symbol",
    "date",
    "period",
    "adjust_type",
    "source",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "prev_close",
    "limit_up_price",
    "limit_down_price",
    "is_suspended",
    "limit_status",
    "warning",
]

_BACKTEST_DAILY_PRICE_SELECT_COLUMNS = [
    *_BACKTEST_DAILY_PRICE_COLUMNS,
    "updated_at",
]
