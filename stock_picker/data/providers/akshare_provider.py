from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterable

import pandas as pd

from stock_picker.data.models import normalize_symbol, symbol_code


class AkShareProvider:
    """AkShare based A-share market data provider."""

    EASTMONEY_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120 Safari/537.36"
        ),
        "Referer": "https://quote.eastmoney.com/",
    }

    HISTORY_COLUMNS = {
        "\u65e5\u671f": "date",
        "\u5f00\u76d8": "open",
        "\u6536\u76d8": "close",
        "\u6700\u9ad8": "high",
        "\u6700\u4f4e": "low",
        "\u6210\u4ea4\u91cf": "volume",
        "\u6210\u4ea4\u989d": "amount",
        "\u632f\u5e45": "amplitude",
        "\u6da8\u8dcc\u5e45": "pct_chg",
        "\u6da8\u8dcc\u989d": "change",
        "\u6362\u624b\u7387": "turnover",
    }

    REALTIME_COLUMNS = {
        "\u4ee3\u7801": "code",
        "\u540d\u79f0": "name",
        "\u6700\u65b0\u4ef7": "price",
        "\u6da8\u8dcc\u5e45": "pct_chg",
        "\u6da8\u8dcc\u989d": "change",
        "\u6210\u4ea4\u91cf": "volume",
        "\u6210\u4ea4\u989d": "amount",
        "\u6700\u9ad8": "high",
        "\u6700\u4f4e": "low",
        "\u4eca\u5f00": "open",
        "\u6628\u6536": "prev_close",
        "\u6362\u624b\u7387": "turnover",
    }

    def __init__(self) -> None:
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError(
                "AkShare is not installed. Run: pip install -r requirements.txt"
            ) from exc
        self._ak = ak

    def get_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str = "daily",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        with self._eastmoney_request_context():
            raw = self._ak.stock_zh_a_hist(
                symbol=symbol_code(symbol),
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
        if raw.empty:
            return self._empty_history_frame()

        df = raw.rename(columns=self.HISTORY_COLUMNS)
        missing = [column for column in self._history_columns() if column not in df]
        if missing:
            raise ValueError(f"AkShare history response missing columns: {missing}")

        df.insert(0, "symbol", normalize_symbol(symbol))
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return df[["symbol", *self._history_columns()]]

    def get_realtime_quotes(self, symbols: Iterable[str] | None = None) -> pd.DataFrame:
        with self._eastmoney_request_context():
            raw = self._ak.stock_zh_a_spot_em()
        if raw.empty:
            return self._empty_realtime_frame()

        df = raw.rename(columns=self.REALTIME_COLUMNS)
        missing = [column for column in ["code", *self._realtime_columns()] if column not in df]
        if missing:
            raise ValueError(f"AkShare realtime response missing columns: {missing}")

        df["symbol"] = df["code"].map(normalize_symbol)

        if symbols:
            wanted = {normalize_symbol(item) for item in symbols}
            df = df[df["symbol"].isin(wanted)]

        return df[["symbol", *self._realtime_columns()]].reset_index(drop=True)

    @staticmethod
    def _history_columns() -> list[str]:
        return [
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

    @staticmethod
    def _realtime_columns() -> list[str]:
        return [
            "name",
            "price",
            "pct_chg",
            "change",
            "volume",
            "amount",
            "high",
            "low",
            "open",
            "prev_close",
            "turnover",
        ]

    @classmethod
    def _empty_history_frame(cls) -> pd.DataFrame:
        return pd.DataFrame(columns=["symbol", *cls._history_columns()])

    @classmethod
    def _empty_realtime_frame(cls) -> pd.DataFrame:
        return pd.DataFrame(columns=["symbol", *cls._realtime_columns()])

    @classmethod
    @contextmanager
    def _eastmoney_request_context(cls):
        import requests

        original_no_proxy = os.environ.get("NO_PROXY")
        original_no_proxy_lower = os.environ.get("no_proxy")
        original_request = requests.sessions.Session.request

        no_proxy = "push2his.eastmoney.com,82.push2.eastmoney.com,.eastmoney.com"
        os.environ["NO_PROXY"] = no_proxy
        os.environ["no_proxy"] = no_proxy

        def patched_request(session, method, url, **kwargs):
            if "eastmoney.com" in str(url):
                session.trust_env = False
                headers = dict(kwargs.get("headers") or {})
                for key, value in cls.EASTMONEY_HEADERS.items():
                    headers.setdefault(key, value)
                kwargs["headers"] = headers
            return original_request(session, method, url, **kwargs)

        requests.sessions.Session.request = patched_request
        try:
            yield
        finally:
            requests.sessions.Session.request = original_request
            if original_no_proxy is None:
                os.environ.pop("NO_PROXY", None)
            else:
                os.environ["NO_PROXY"] = original_no_proxy

            if original_no_proxy_lower is None:
                os.environ.pop("no_proxy", None)
            else:
                os.environ["no_proxy"] = original_no_proxy_lower
