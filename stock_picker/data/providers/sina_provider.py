from __future__ import annotations

from collections.abc import Iterable

import pandas as pd
import requests

from stock_picker.data.models import normalize_symbol, sina_symbol


class SinaProvider:
    """Sina quote provider for A-share realtime snapshots."""

    URL = "https://hq.sinajs.cn/list={symbols}"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120 Safari/537.36"
        ),
        "Referer": "https://finance.sina.com.cn/",
    }

    def get_realtime_quotes(self, symbols: Iterable[str] | None = None) -> pd.DataFrame:
        if not symbols:
            raise ValueError("SinaProvider requires explicit symbols.")

        requested = list(symbols)
        query_symbols = ",".join(sina_symbol(symbol) for symbol in requested)
        response = requests.get(
            self.URL.format(symbols=query_symbols),
            headers=self.HEADERS,
            timeout=20,
        )
        response.raise_for_status()
        response.encoding = "gbk"

        rows = []
        for line in response.text.splitlines():
            parsed = self._parse_line(line)
            if parsed:
                rows.append(parsed)

        if not rows:
            return self._empty_realtime_frame()

        return pd.DataFrame(rows, columns=self._columns())

    @classmethod
    def _parse_line(cls, line: str) -> dict[str, object] | None:
        if '="' not in line:
            return None

        raw_symbol = line.split("var hq_str_", 1)[1].split("=", 1)[0]
        payload = line.split('="', 1)[1].rsplit('";', 1)[0]
        if not payload:
            return None

        parts = payload.split(",")
        if len(parts) < 32:
            return None

        open_price = cls._number(parts[1])
        prev_close = cls._number(parts[2])
        price = cls._number(parts[3])
        change = None
        pct_chg = None
        if price is not None and prev_close not in (None, 0):
            change = price - prev_close
            pct_chg = change / prev_close * 100

        return {
            "symbol": cls._normalize_sina_symbol(raw_symbol),
            "name": parts[0],
            "price": price,
            "pct_chg": pct_chg,
            "change": change,
            "volume": cls._number(parts[8]),
            "amount": cls._number(parts[9]),
            "high": cls._number(parts[4]),
            "low": cls._number(parts[5]),
            "open": open_price,
            "prev_close": prev_close,
            "turnover": None,
        }

    @staticmethod
    def _normalize_sina_symbol(value: str) -> str:
        if value.startswith("sh"):
            return normalize_symbol(f"{value[2:]}.SH")
        if value.startswith("sz"):
            return normalize_symbol(f"{value[2:]}.SZ")
        if value.startswith("bj"):
            return normalize_symbol(f"{value[2:]}.BJ")
        return normalize_symbol(value)

    @staticmethod
    def _number(value: str) -> float | None:
        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def _columns() -> list[str]:
        return [
            "symbol",
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
    def _empty_realtime_frame(cls) -> pd.DataFrame:
        return pd.DataFrame(columns=cls._columns())
