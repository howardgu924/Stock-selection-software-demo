from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Any

import pandas as pd

from stock_picker.data.models import StockInfo, normalize_symbol, symbol_code


class JoinQuantProvider:
    """JQData provider for optional JoinQuant-backed market data."""

    PRICE_FIELDS = ["open", "high", "low", "close", "volume", "money"]
    MINUTE_PERIODS = {"1", "5", "15", "30", "60"}

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        sdk: Any | None = None,
    ) -> None:
        self.username = username or os.getenv("JQDATA_USERNAME")
        self.password = password or os.getenv("JQDATA_PASSWORD")
        self._sdk = sdk or self._load_sdk()
        self._authenticated = False
        self._symbol_map: dict[str, str] = {}

    def get_query_count(self) -> dict[str, object]:
        self._ensure_auth()
        return dict(self._sdk.get_query_count())

    def get_stock_symbols(self) -> list[StockInfo]:
        self._ensure_auth()
        raw = self._sdk.get_all_securities(types=["stock"], date=None)
        if raw is None or raw.empty:
            return []

        symbols: list[StockInfo] = []
        self._symbol_map.clear()
        for jq_symbol, row in raw.iterrows():
            normalized = self._from_joinquant_symbol(str(jq_symbol))
            self._symbol_map[normalized] = str(jq_symbol)
            name = str(getattr(row, "display_name", "") or getattr(row, "name", ""))
            if normalized and name:
                symbols.append(
                    StockInfo(
                        symbol=normalized,
                        code=symbol_code(normalized),
                        name=name,
                    )
                )
        return symbols

    def get_trade_dates(self, start_date: str, end_date: str) -> list[str]:
        self._ensure_auth()
        dates = self._sdk.get_trade_days(
            start_date=self._format_date(start_date),
            end_date=self._format_date(end_date),
        )
        return [self._format_date_value(item) for item in dates]

    def get_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str = "daily",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        if period != "daily":
            raise ValueError("JoinQuantProvider currently supports only daily history.")

        raw = self._get_price(
            symbol=symbol,
            start_date=self._format_date(start_date),
            end_date=self._format_date(end_date),
            frequency="daily",
            adjust=adjust,
        )
        if raw.empty:
            return self._empty_history_frame()

        frame = self._normalize_price_frame(raw, symbol=normalize_symbol(symbol))
        frame["date"] = pd.to_datetime(frame["datetime"]).dt.strftime("%Y-%m-%d")
        frame = self._add_derived_price_columns(frame)
        return frame[
            [
                "symbol",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "amplitude",
                "pct_chg",
                "change",
                "turnover",
            ]
        ]

    def get_minute_history(
        self,
        symbol: str,
        start_datetime: str,
        end_datetime: str,
        period: str = "5",
        adjust: str = "",
    ) -> pd.DataFrame:
        if period not in self.MINUTE_PERIODS:
            raise ValueError("period must be one of: 1, 5, 15, 30, 60")

        raw = self._get_price(
            symbol=symbol,
            start_date=start_datetime,
            end_date=end_datetime,
            frequency=f"{period}m",
            adjust=adjust,
        )
        if raw.empty:
            return self._empty_minute_frame()

        frame = self._normalize_price_frame(raw, symbol=normalize_symbol(symbol))
        frame = self._add_derived_price_columns(frame)
        frame["average_price"] = pd.NA
        frame["price"] = pd.NA
        return frame[
            [
                "symbol",
                "datetime",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "average_price",
                "price",
                "amplitude",
                "pct_chg",
                "change",
                "turnover",
            ]
        ].reset_index(drop=True)

    def _get_price(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        frequency: str,
        adjust: str,
    ) -> pd.DataFrame:
        self._ensure_auth()
        try:
            raw = self._sdk.get_price(
                security=self._to_joinquant_symbol(symbol),
                start_date=start_date,
                end_date=end_date,
                frequency=frequency,
                fields=self.PRICE_FIELDS,
                skip_paused=False,
                fq=self._adjust_flag(adjust),
                panel=False,
            )
        except Exception as exc:
            raise self._format_jqdata_error(exc, start_date, end_date) from exc
        if raw is None:
            return pd.DataFrame()
        return raw.copy()

    @classmethod
    def _normalize_price_frame(cls, raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
        frame = raw.copy()
        if isinstance(frame.index, pd.MultiIndex):
            frame = frame.reset_index()
        else:
            frame = frame.reset_index().rename(columns={"index": "datetime"})

        if "time" in frame.columns and "datetime" not in frame.columns:
            frame = frame.rename(columns={"time": "datetime"})
        if "date" in frame.columns and "datetime" not in frame.columns:
            frame = frame.rename(columns={"date": "datetime"})
        if "money" in frame.columns:
            frame = frame.rename(columns={"money": "amount"})

        for column in ["open", "high", "low", "close", "volume", "amount"]:
            if column not in frame:
                frame[column] = pd.NA
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

        frame["symbol"] = symbol
        frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce").dt.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        return frame

    @staticmethod
    def _add_derived_price_columns(frame: pd.DataFrame) -> pd.DataFrame:
        result = frame.copy()
        previous_close = result["close"].shift(1)
        result["change"] = result["close"] - previous_close
        result["pct_chg"] = result["change"] / previous_close * 100
        result["amplitude"] = (result["high"] - result["low"]) / previous_close * 100
        result["turnover"] = pd.NA
        return result

    def _ensure_auth(self) -> None:
        if self._authenticated:
            return
        if not self.username or not self.password:
            raise RuntimeError(
                "JoinQuant credentials are required. Set JQDATA_USERNAME and "
                "JQDATA_PASSWORD, or pass username/password to JoinQuantProvider."
            )
        self._sdk.auth(self.username, self.password)
        self._authenticated = True

    @classmethod
    def _format_jqdata_error(
        cls,
        exc: Exception,
        start_date: str,
        end_date: str,
    ) -> Exception:
        message = str(exc)
        match = re.search(
            r"仅能获取(?P<start>\d{4}-\d{2}-\d{2})至(?P<end>\d{4}-\d{2}-\d{2})的数据",
            message,
        )
        if not match:
            return exc

        return RuntimeError(
            "JQData account permission does not cover the requested date range. "
            f"Requested: {cls._format_date(start_date)} to {cls._format_date(end_date)}. "
            f"Allowed: {match.group('start')} to {match.group('end')}. "
            "Use the default data source, configure --fallback, or adjust the dates."
        )
    def _to_joinquant_symbol(self, symbol: str) -> str:
        normalized = normalize_symbol(symbol)
        mapped = self._symbol_map.get(normalized)
        if mapped:
            return mapped

        code = symbol_code(normalized)
        if normalized.endswith(".SH"):
            return f"{code}.XSHG"
        if normalized.endswith(".SZ"):
            return f"{code}.XSHE"
        if normalized.endswith(".BJ"):
            return f"{code}.XBEI"
        return normalized

    @staticmethod
    def _from_joinquant_symbol(symbol: str) -> str:
        if symbol.endswith(".XSHG"):
            return normalize_symbol(f"{symbol_code(symbol)}.SH")
        if symbol.endswith(".XSHE"):
            return normalize_symbol(f"{symbol_code(symbol)}.SZ")
        if symbol.endswith((".XBEI", ".XBSE")):
            return normalize_symbol(f"{symbol_code(symbol)}.BJ")
        return normalize_symbol(symbol_code(symbol))

    @staticmethod
    def _adjust_flag(adjust: str) -> str | None:
        if adjust == "qfq":
            return "pre"
        if adjust == "hfq":
            return "post"
        if adjust in {"", "none", "bfq"}:
            return None
        raise ValueError("adjust must be one of: qfq, hfq, bfq, none")

    @staticmethod
    def _format_date(value: str) -> str:
        if "-" in value:
            return value
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"

    @staticmethod
    def _format_date_value(value: date | datetime | str) -> str:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return JoinQuantProvider._format_date(str(value))

    @staticmethod
    def _load_sdk() -> Any:
        try:
            import jqdatasdk
        except ImportError as exc:
            raise RuntimeError(
                "JQData SDK is not installed. Run: pip install -r requirements.txt"
            ) from exc
        return jqdatasdk

    @staticmethod
    def _empty_history_frame() -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "symbol",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "amplitude",
                "pct_chg",
                "change",
                "turnover",
            ]
        )

    @staticmethod
    def _empty_minute_frame() -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "symbol",
                "datetime",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "average_price",
                "price",
                "amplitude",
                "pct_chg",
                "change",
                "turnover",
            ]
        )
