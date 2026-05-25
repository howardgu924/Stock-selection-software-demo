from __future__ import annotations

from collections.abc import Iterable
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from stock_picker.data.models import StockInfo, normalize_symbol
from stock_picker.data.providers import AkShareProvider, BaoStockProvider, SinaProvider
from stock_picker.data.storage import SQLiteMarketDataStore


class MarketDataService:
    def __init__(
        self,
        history_provider: BaoStockProvider | None = None,
        realtime_provider: SinaProvider | None = None,
        stock_provider: AkShareProvider | None = None,
        store: SQLiteMarketDataStore | None = None,
    ) -> None:
        self.history_provider = history_provider or BaoStockProvider()
        self.realtime_provider = realtime_provider or SinaProvider()
        self.stock_provider = stock_provider or AkShareProvider()
        self.store = store or SQLiteMarketDataStore()

    def get_stock_symbols(self, refresh: bool = False) -> list[StockInfo]:
        if not refresh:
            cached = self.store.load_stock_symbols()
            if cached:
                return cached

        try:
            symbols = self.stock_provider.get_stock_symbols()
        except Exception as primary_exc:
            try:
                symbols = self.history_provider.get_stock_symbols()
            except Exception as fallback_exc:
                raise RuntimeError(
                    "Stock symbol fetch failed. "
                    f"AkShare: {primary_exc}; BaoStock fallback: {fallback_exc}"
                ) from fallback_exc
        self.store.save_stock_symbols(symbols)
        return symbols

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
        if refresh:
            frame = self.history_provider.get_history(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                period=period,
                adjust=adjust,
            )
            self.store.save_history(frame)
            return frame

        cached = self.store.load_history(normalized, start_date, end_date)
        if cached.empty:
            frame = self.history_provider.get_history(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                period=period,
                adjust=adjust,
            )
            self.store.save_history(frame)
            return frame

        trade_dates = self.history_provider.get_trade_dates(start_date, end_date)
        cached_dates = set(cached["date"].tolist())
        missing_ranges = self._missing_date_ranges(trade_dates, cached_dates)

        for range_start, range_end in missing_ranges:
            frame = self.history_provider.get_history(
                symbol=symbol,
                start_date=range_start,
                end_date=range_end,
                period=period,
                adjust=adjust,
            )
            self.store.save_history(frame)

        return self.store.load_history(normalized, start_date, end_date)

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
        return self.realtime_provider.get_realtime_quotes(symbols=symbols)

    def update_history(
        self,
        symbols: Iterable[str],
        start_date: str,
        end_date: str,
        period: str = "daily",
        adjust: str = "qfq",
        refresh: bool = False,
        skip_errors: bool = True,
        error_log_path: str | Path = "data/history_errors.csv",
        progress_callback: Callable[[int, int, str, str, int, str], None] | None = None,
    ) -> pd.DataFrame:
        symbols = list(symbols)
        total = len(symbols)
        results = []
        failures = []

        batch_session = getattr(self.history_provider, "batch_session", None)
        session = batch_session() if batch_session else nullcontext()

        with session:
            for index, symbol in enumerate(symbols, start=1):
                normalized = normalize_symbol(symbol)
                try:
                    frame = self.get_history(
                        symbol=symbol,
                        start_date=start_date,
                        end_date=end_date,
                        period=period,
                        adjust=adjust,
                        refresh=refresh,
                    )
                except Exception as exc:
                    if not skip_errors:
                        raise
                    error = str(exc)
                    results.append(
                        {
                            "symbol": normalized,
                            "status": "failed",
                            "rows": 0,
                            "error": error,
                        }
                    )
                    failures.append(
                        {
                            "timestamp": datetime.now().isoformat(timespec="seconds"),
                            "symbol": normalized,
                            "start_date": start_date,
                            "end_date": end_date,
                            "error": error,
                        }
                    )
                    if progress_callback:
                        progress_callback(index, total, normalized, "failed", 0, error)
                    continue

                results.append(
                    {
                        "symbol": normalized,
                        "status": "ok",
                        "rows": len(frame),
                        "error": "",
                    }
                )
                if progress_callback:
                    progress_callback(index, total, normalized, "ok", len(frame), "")

        self._append_error_log(failures, error_log_path)
        return pd.DataFrame(results, columns=["symbol", "status", "rows", "error"])

    def update_all_history(
        self,
        start_date: str,
        end_date: str,
        period: str = "daily",
        adjust: str = "qfq",
        refresh: bool = False,
        refresh_symbols: bool = False,
        skip_errors: bool = True,
        error_log_path: str | Path = "data/history_errors.csv",
        limit: int | None = None,
        progress_callback: Callable[[int, int, str, str, int, str], None] | None = None,
    ) -> pd.DataFrame:
        symbols = self.get_stock_symbols(refresh=refresh_symbols)
        if limit is not None:
            symbols = symbols[:limit]
        return self.update_history(
            symbols=[item.symbol for item in symbols],
            start_date=start_date,
            end_date=end_date,
            period=period,
            adjust=adjust,
            refresh=refresh,
            skip_errors=skip_errors,
            error_log_path=error_log_path,
            progress_callback=progress_callback,
        )

    @staticmethod
    def _missing_date_ranges(
        trade_dates: list[str], cached_dates: set[str]
    ) -> list[tuple[str, str]]:
        ranges = []
        range_start = None
        previous = None

        for trade_date in trade_dates:
            if trade_date in cached_dates:
                if range_start is not None and previous is not None:
                    ranges.append((range_start, previous))
                    range_start = None
                previous = None
                continue

            if range_start is None:
                range_start = trade_date
            previous = trade_date

        if range_start is not None and previous is not None:
            ranges.append((range_start, previous))

        return ranges

    @staticmethod
    def _append_error_log(
        failures: list[dict[str, object]], error_log_path: str | Path
    ) -> None:
        if not failures:
            return

        path = Path(error_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame(failures)
        frame.to_csv(path, mode="a", header=not path.exists(), index=False)
