from __future__ import annotations

from collections.abc import Iterable
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Sequence

import pandas as pd

from stock_picker.data.indicators import add_technical_indicators
from stock_picker.data.models import StockInfo, normalize_symbol
from stock_picker.data.providers import (
    AkShareProvider,
    BaoStockProvider,
    JoinQuantProvider,
    SinaProvider,
)
from stock_picker.data.storage import SQLiteMarketDataStore


@dataclass(frozen=True)
class DataSourceCallResult:
    feature: str
    source: str
    fallback_from: str | None = None
    fallback_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class DataSourceConfig:
    history_source: str | None = None
    realtime_source: str | None = None
    minute_source: str | None = None
    stock_source: str | None = None
    market_source: str | None = None
    fallback_sources: Mapping[str, Sequence[str]] | Sequence[str] | None = None

    def source_for(self, feature: str) -> str | None:
        return getattr(self, f"{feature}_source")

    def fallbacks_for(self, feature: str) -> tuple[str, ...]:
        fallbacks = self.fallback_sources
        if fallbacks is None:
            return ()
        if isinstance(fallbacks, Mapping):
            values = fallbacks.get(feature, ())
        else:
            values = fallbacks
        if isinstance(values, str):
            return (values,)
        return tuple(values)

    def has_routing(self, feature: str) -> bool:
        return self.source_for(feature) is not None or bool(self.fallbacks_for(feature))


class DataSourceError(RuntimeError):
    pass


class MarketDataService:
    DEFAULT_SOURCES = {
        "history": "baostock",
        "realtime": "sina",
        "minute": "baostock",
        "stock": "akshare",
        "market": "akshare",
    }
    SUPPORTED_SOURCES = {
        "history": {"baostock", "akshare", "joinquant"},
        "realtime": {"sina", "akshare"},
        "minute": {"baostock", "akshare", "joinquant"},
        "stock": {"akshare", "baostock", "joinquant"},
        "market": {"akshare"},
    }
    PROVIDER_FACTORIES = {
        "akshare": AkShareProvider,
        "baostock": BaoStockProvider,
        "joinquant": JoinQuantProvider,
        "sina": SinaProvider,
    }

    def __init__(
        self,
        history_provider: BaoStockProvider | None = None,
        realtime_provider: SinaProvider | None = None,
        stock_provider: AkShareProvider | None = None,
        market_provider: AkShareProvider | None = None,
        store: SQLiteMarketDataStore | None = None,
        data_source_config: DataSourceConfig | None = None,
    ) -> None:
        self.data_source_config = data_source_config or DataSourceConfig()
        self._provider_cache: dict[str, object] = {}
        self.last_source_results: dict[str, DataSourceCallResult] = {}
        self.history_provider = history_provider or self._provider_for_feature("history")
        self.realtime_provider = realtime_provider or self._provider_for_feature(
            "realtime"
        )
        self.stock_provider = stock_provider or self._provider_for_feature("stock")
        if market_provider is not None:
            self.market_provider = market_provider
        elif stock_provider is not None and not self.data_source_config.has_routing(
            "market"
        ):
            self.market_provider = self.stock_provider
        else:
            self.market_provider = self._provider_for_feature("market")
        self.store = store or SQLiteMarketDataStore()

    def get_stock_symbols(self, refresh: bool = False) -> list[StockInfo]:
        routed = self.data_source_config.has_routing("stock")
        if not refresh and not routed:
            cached = self.store.load_stock_symbols()
            if cached:
                return cached

        if routed:
            symbols = self._call_provider("stock", "get_stock_symbols")
        else:
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
        indicators: bool = False,
    ) -> pd.DataFrame:
        normalized = normalize_symbol(symbol)
        routed = self.data_source_config.has_routing("history")
        if routed:
            frame = self._call_provider(
                "history",
                "get_history",
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                period=period,
                adjust=adjust,
            )
            self.store.save_history(frame)
            return add_technical_indicators(frame) if indicators else frame

        if refresh:
            frame = self.history_provider.get_history(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                period=period,
                adjust=adjust,
            )
            self.store.save_history(frame)
            return add_technical_indicators(frame) if indicators else frame

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
            return add_technical_indicators(frame) if indicators else frame

        try:
            trade_dates = self.history_provider.get_trade_dates(start_date, end_date)
        except Exception:
            return add_technical_indicators(cached) if indicators else cached
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

        frame = self.store.load_history(normalized, start_date, end_date)
        return add_technical_indicators(frame) if indicators else frame

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
        if self.data_source_config.has_routing("realtime"):
            return self._call_provider(
                "realtime",
                "get_realtime_quotes",
                symbols=symbols,
            )
        return self.realtime_provider.get_realtime_quotes(symbols=symbols)

    def get_minute_history(
        self,
        symbol: str,
        start_datetime: str,
        end_datetime: str,
        period: str = "5",
        adjust: str = "",
    ) -> pd.DataFrame:
        if self.data_source_config.has_routing("minute"):
            return self._call_provider(
                "minute",
                "get_minute_history",
                symbol=symbol,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                period=period,
                adjust=adjust,
            )

        if period in {"5", "15", "30", "60"} and hasattr(
            self.history_provider, "get_minute_history"
        ):
            return self.history_provider.get_minute_history(
                symbol=symbol,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                period=period,
                adjust=adjust,
            )

        return self.market_provider.get_minute_history(
            symbol=symbol,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            period=period,
            adjust=adjust,
        )

    def get_boards(self, board_type: str) -> pd.DataFrame:
        if self.data_source_config.has_routing("market"):
            return self._call_provider("market", "get_boards", board_type=board_type)
        return self.market_provider.get_boards(board_type=board_type)

    def get_board_members(self, board_type: str, board: str) -> pd.DataFrame:
        if self.data_source_config.has_routing("market"):
            return self._call_provider(
                "market",
                "get_board_members",
                board_type=board_type,
                board=board,
            )
        return self.market_provider.get_board_members(
            board_type=board_type,
            board=board,
        )

    def get_board_minute_history(
        self,
        board_type: str,
        board: str,
        period: str = "5",
    ) -> pd.DataFrame:
        if self.data_source_config.has_routing("market"):
            return self._call_provider(
                "market",
                "get_board_minute_history",
                board_type=board_type,
                board=board,
                period=period,
            )
        return self.market_provider.get_board_minute_history(
            board_type=board_type,
            board=board,
            period=period,
        )

    def get_market_snapshot(
        self,
        symbols: Iterable[str] | None = None,
    ) -> pd.DataFrame:
        if self.data_source_config.has_routing("market"):
            return self._call_provider(
                "market",
                "get_market_snapshot",
                symbols=symbols,
            )
        return self.market_provider.get_market_snapshot(symbols=symbols)

    def get_financial_indicators(
        self,
        symbol: str,
        start_year: str = "1900",
    ) -> pd.DataFrame:
        if self.data_source_config.has_routing("market"):
            return self._call_provider(
                "market",
                "get_financial_indicators",
                symbol=symbol,
                start_year=start_year,
            )
        return self.market_provider.get_financial_indicators(
            symbol=symbol,
            start_year=start_year,
        )

    def get_index_members(self, index_code: str) -> pd.DataFrame:
        if self.data_source_config.has_routing("market"):
            return self._call_provider(
                "market",
                "get_index_members",
                index_code=index_code,
            )
        return self.market_provider.get_index_members(index_code=index_code)

    def get_valuation_history(
        self,
        symbol: str,
        indicator: str = "总市值",
        period: str = "近一年",
    ) -> pd.DataFrame:
        if self.data_source_config.has_routing("market"):
            return self._call_provider(
                "market",
                "get_valuation_history",
                symbol=symbol,
                indicator=indicator,
                period=period,
            )
        return self.market_provider.get_valuation_history(
            symbol=symbol,
            indicator=indicator,
            period=period,
        )

    def get_index_history(
        self,
        index_code: str,
        start_date: str,
        end_date: str,
        period: str = "daily",
    ) -> pd.DataFrame:
        if self.data_source_config.has_routing("market"):
            return self._call_provider(
                "market",
                "get_index_history",
                index_code=index_code,
                start_date=start_date,
                end_date=end_date,
                period=period,
            )
        return self.market_provider.get_index_history(
            index_code=index_code,
            start_date=start_date,
            end_date=end_date,
            period=period,
        )

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

    def _call_provider(self, feature: str, method_name: str, **kwargs):
        source = self.data_source_config.source_for(feature) or self.DEFAULT_SOURCES[
            feature
        ]
        candidates = (
            self._normalize_source(source),
            *(
                self._normalize_source(item)
                for item in self.data_source_config.fallbacks_for(feature)
            ),
        )
        errors: list[str] = []

        for index, candidate in enumerate(candidates):
            try:
                provider = self._provider_for_source(feature, candidate)
                method = getattr(provider, method_name, None)
                if method is None:
                    raise DataSourceError(
                        f"{candidate} does not support {feature} data."
                    )
                result = method(**kwargs)
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
                continue

            self.last_source_results[feature] = DataSourceCallResult(
                feature=feature,
                source=candidate,
                fallback_from=candidates[0] if index > 0 else None,
                fallback_errors=tuple(errors),
            )
            return result

        raise DataSourceError(
            self._format_source_error(
                feature=feature,
                primary=candidates[0],
                fallbacks=candidates[1:],
                errors=errors,
            )
        )

    def _provider_for_feature(self, feature: str):
        source = self.data_source_config.source_for(feature) or self.DEFAULT_SOURCES[
            feature
        ]
        return self._provider_for_source(feature, source)

    def _provider_for_source(self, feature: str, source: str):
        normalized = self._normalize_source(source)
        supported = self.SUPPORTED_SOURCES[feature]
        if normalized not in supported:
            raise DataSourceError(
                f"{normalized} does not support {feature} data. "
                f"Supported sources: {', '.join(sorted(supported))}"
            )

        provider = self._provider_cache.get(normalized)
        if provider is None:
            provider = self.PROVIDER_FACTORIES[normalized]()
            self._provider_cache[normalized] = provider
        return provider

    @staticmethod
    def _normalize_source(source: str) -> str:
        return source.strip().lower()

    @staticmethod
    def _format_source_error(
        feature: str,
        primary: str,
        fallbacks: Sequence[str],
        errors: Sequence[str],
    ) -> str:
        fallback_text = ", ".join(fallbacks) if fallbacks else "none configured"
        return (
            f"{feature} data fetch failed. "
            f"Selected source: {primary}. "
            f"Fallback sources: {fallback_text}. "
            f"Errors: {'; '.join(errors)}"
        )
