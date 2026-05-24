from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from stock_picker.data.models import normalize_symbol
from stock_picker.data.providers import AkShareProvider
from stock_picker.data.storage import SQLiteMarketDataStore


class MarketDataService:
    def __init__(
        self,
        provider: AkShareProvider | None = None,
        store: SQLiteMarketDataStore | None = None,
    ) -> None:
        self.provider = provider or AkShareProvider()
        self.store = store or SQLiteMarketDataStore()

    def get_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str = "daily",
        adjust: str = "qfq",
        refresh: bool = False,
    ) -> pd.DataFrame:
        normalized = normalize_symbol(symbol)
        if not refresh:
            cached = self.store.load_history(normalized, start_date, end_date)
            if not cached.empty:
                return cached

        frame = self.provider.get_history(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            period=period,
            adjust=adjust,
        )
        self.store.save_history(frame)
        return frame

    def refresh_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str = "daily",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        return self.get_history(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            period=period,
            adjust=adjust,
            refresh=True,
        )

    def get_realtime_quotes(
        self, symbols: Iterable[str] | None = None
    ) -> pd.DataFrame:
        return self.provider.get_realtime_quotes(symbols=symbols)
