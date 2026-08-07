"""Production composition root for the Phase 6 local web application."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Callable, Iterable, Mapping

import pandas as pd

from stock_picker.user import WatchlistStore

from .market_cache import MarketCache
from .phase5_models import AccountProfile, Phase5Error, UniverseSpec
from .phase5_service import PartitionRequest, Phase5Service
from .phase6_controller import Phase6Controller
from .phase6_profile_store import AccountProfileStore
from .phase6_provider_registry import ProviderRegistry
from .run_store import RunStore
from .runtime_data_adapter import RuntimeDataAdapter, normalize_baostock_minute_frame


def create_phase6_application(
    *, project_root: str | Path | None = None,
    service: Phase5Service | None = None,
    profile_store: AccountProfileStore | None = None,
    watchlist_store: WatchlistStore | None = None,
    provider_registry: ProviderRegistry | None = None,
    dependency_factory: Callable[[str], object] | None = None,
    paper_state_loader: Callable[[str], Mapping[str, object]] | None = None,
    trading_calendar: Iterable[object] | None = None,
) -> Phase6Controller:
    """Build the real Phase 5/6 service graph using absolute project paths."""
    root = Path(project_root or Path(__file__).resolve().parents[3]).expanduser().resolve()
    data_root = Path(
        os.getenv("ADAPTIVE_V13_DATA_DIR", root / "data" / "adaptive_trend_v1_3")
    ).expanduser().resolve()
    report_root = Path(
        os.getenv("ADAPTIVE_V13_REPORT_DIR", root / "data" / "reports" / "adaptive_trend_v1_3")
    ).expanduser().resolve()
    user_root = Path(
        os.getenv("ADAPTIVE_V13_USER_DIR", root / "data" / "user" / "default")
    ).expanduser().resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    report_root.mkdir(parents=True, exist_ok=True)
    user_root.mkdir(parents=True, exist_ok=True)

    profiles = profile_store or AccountProfileStore(data_root / "account_profiles.json")
    watches = watchlist_store or WatchlistStore(user_root)
    priorities = tuple(
        item.strip() for item in
        os.getenv("ADAPTIVE_V13_PROVIDERS", "baostock,akshare").split(",")
        if item.strip()
    )
    loaded_profiles = profiles.load()
    if "default" not in loaded_profiles:
        profiles.save(AccountProfile(
            "default", Decimal(os.getenv("ADAPTIVE_V13_BACKTEST_CASH", "100000")),
            Decimal(os.getenv("ADAPTIVE_V13_PAPER_CASH", "100000")),
            os.getenv("ADAPTIVE_V13_FEE_SCHEDULE", "CN_A_DEFAULT"), "CNY",
            UniverseSpec("MANUAL", ("600000.SH",)), priorities,
            str(data_root), str(report_root),
        ))
        loaded_profiles = profiles.load()

    registry = provider_registry or ProviderRegistry.existing()
    if trading_calendar is None:
        calendar, latest_available_date = _production_calendar(registry,priorities,data_root)
    else:
        calendar = tuple(trading_calendar)
        latest_available_date = max(calendar, default=None)
    phase5 = service or Phase5Service(
        cache=MarketCache(data_root / "market_cache.sqlite3"),
        run_store=RunStore(data_root / "runs.sqlite3"),
        account_profiles=loaded_profiles,
        trading_calendar=calendar,
        latest_available_date=latest_available_date,
        watchlist_loader=lambda name: (
            tuple(item.symbols) if (item := watches.get(name)) is not None else None
        ),
        market_scope_loader=lambda scope: _load_market_scope(registry,priorities,scope),
        partition_planner=_partition_planner(registry),
    )
    return Phase6Controller(
        service=phase5,profile_store=profiles,watchlist_store=watches,
        provider_registry=registry,
        dependency_factory=dependency_factory or (
            lambda run_id: RuntimeDataAdapter(
                phase5.cache, phase5.run_store, run_id
            ).dependencies()
        ),
        paper_state_loader=paper_state_loader,
    )


def _production_calendar(
    registry: ProviderRegistry, priorities: tuple[str,...], data_root: Path,
) -> tuple[tuple[date,...], date]:
    """Use a persisted exchange calendar, fetching it read-only only when absent."""
    path = data_root / "trading_calendar.json"
    if path.is_file():
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
            parsed = tuple(sorted({date.fromisoformat(str(item)) for item in values}))
            if parsed:
                latest = _provider_latest_trade_date(registry, priorities)
                cutoff = latest or max((item for item in parsed if item <= date.today()), default=None)
                if cutoff is None:
                    raise Phase5Error("INVALID_CONFIG","trading_calendar_no_completed_date")
                return tuple(item for item in parsed if item <= cutoff), cutoff
        except (OSError,ValueError,TypeError):
            raise Phase5Error("INVALID_CONFIG","trading_calendar_cache_invalid") from None
    end = date.today() + timedelta(days=370)
    for descriptor in registry.partition_providers(
        priorities,dataset_type="daily",frequency="1d",price_basis="RAW",
    ):
        method = getattr(descriptor.provider,"get_trade_dates",None)
        if not callable(method):
            continue
        try:
            values = method("20100101",end.strftime("%Y%m%d"))
            parsed = tuple(sorted({date.fromisoformat(str(item)[:10]) for item in values}))
        except Exception:
            continue
        if parsed:
            path.write_text(
                json.dumps([item.isoformat() for item in parsed],ensure_ascii=False),
                encoding="utf-8",
            )
            if parsed:
                latest = max(parsed)
                return tuple(item for item in parsed if item <= latest), latest
    raise Phase5Error("INVALID_CONFIG","trading_calendar_provider_unavailable")


def _provider_latest_trade_date(
    registry: ProviderRegistry, priorities: tuple[str, ...]
) -> date | None:
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    completed_cutoff = (
        now.date() if now.time() >= time(15, 10) else now.date() - timedelta(days=1)
    )
    calendar_fallback: date | None = None
    for descriptor in registry.partition_providers(
        priorities, dataset_type="daily", frequency="1d", price_basis="RAW",
    ):
        method = getattr(descriptor.provider, "get_trade_dates", None)
        if not callable(method):
            continue
        try:
            values = method("20000101", completed_cutoff.strftime("%Y%m%d"))
            parsed = [date.fromisoformat(str(item)[:10]) for item in values]
        except Exception:
            continue
        available = [item for item in parsed if item <= completed_cutoff]
        if available:
            candidate = max(available)
            calendar_fallback = max(calendar_fallback or candidate,candidate)
            history = getattr(descriptor.provider,"get_history",None)
            if not callable(history):
                continue
            try:
                frame = history(
                    "600000.SH",(candidate - timedelta(days=14)).strftime("%Y%m%d"),
                    candidate.strftime("%Y%m%d"),adjust="",
                )
                column = "date" if "date" in frame.columns else "trade_date"
                actual = pd.to_datetime(frame[column],errors="coerce").dropna()
            except Exception:
                continue
            if not actual.empty:
                return min(actual.max().date(),candidate)
    return calendar_fallback


def _partition_planner(registry: ProviderRegistry):
    def plan(universe, resolved, profile, _mode):
        daily_dates = tuple((*resolved.warmup_dates,*resolved.trading_dates))
        daily_start = daily_dates[0].strftime("%Y%m%d")
        daily_end = daily_dates[-1].strftime("%Y%m%d")
        requests: list[PartitionRequest] = []
        benchmarks = set(universe.benchmark_symbols)
        for symbol in universe.required_symbols:
            dataset = "benchmark_daily_bar" if symbol in benchmarks else "daily_bar"
            providers = []
            for descriptor in registry.partition_providers(
                profile.provider_priority,dataset_type="daily",
                frequency="1d",price_basis="RAW",
            ):
                provider = descriptor.provider
                method = (
                    getattr(provider,"get_index_history",None)
                    if symbol in benchmarks else None
                ) or getattr(provider,"get_history",None)
                if callable(method):
                    is_history = method == getattr(provider,"get_history",None)
                    providers.append((
                        descriptor.provider_id,descriptor.source_version,
                        _daily_fetch(method,symbol,daily_start,daily_end,is_history),
                    ))
            requests.append(PartitionRequest(
                dataset,f"{dataset}:{symbol}",tuple(providers),
                requested_trade_dates=daily_dates,normalized_symbol=symbol,frequency="1d",
            ))
        if resolved.trading_dates:
            minute_start = f"{resolved.trading_dates[0].isoformat()} 09:30:00"
            minute_end = f"{resolved.trading_dates[-1].isoformat()} 15:00:00"
            for symbol in universe.candidate_symbols:
                providers = []
                for descriptor in registry.partition_providers(
                    profile.provider_priority,dataset_type="minute",
                    frequency="5m",price_basis="RAW",
                ):
                    method = getattr(descriptor.provider,"get_minute_history",None)
                    if callable(method):
                        providers.append((
                            descriptor.provider_id,descriptor.source_version,
                            _minute_fetch(method,symbol,minute_start,minute_end),
                        ))
                requests.append(PartitionRequest(
                    "minute_5m_bar",f"minute_5m_bar:{symbol}",tuple(providers),
                    requested_trade_dates=resolved.trading_dates,
                    normalized_symbol=symbol,frequency="5m",
                ))
        return tuple(requests)
    return plan


def _daily_fetch(method, symbol: str, start: str, end: str, adjusted_argument: bool):
    def fetch():
        if adjusted_argument:
            return method(symbol,start,end,adjust="")
        return method(symbol,start,end)
    return fetch


def _minute_fetch(method, symbol: str, start: str, end: str):
    def fetch():
        frame = method(symbol,start,end,period="5",adjust="")
        if isinstance(frame, pd.DataFrame) and "datetime" in frame:
            return normalize_baostock_minute_frame(frame, symbol)
        return frame
    return fetch


def _load_market_scope(
    registry: ProviderRegistry, priorities: tuple[str,...], scope: str,
) -> tuple[str,...]:
    providers = registry.partition_providers(
        priorities,dataset_type="stock",frequency="",price_basis="RAW",
    )
    for descriptor in providers:
        method = getattr(descriptor.provider,"get_stock_symbols",None)
        if not callable(method):
            continue
        stocks = method()
        symbols = tuple(str(getattr(item,"symbol",item)) for item in stocks)
        if scope in {"沪深A股","all_a","ALL_A"}:
            return symbols
        if scope in {"创业板","chinext"}:
            return tuple(item for item in symbols if item.startswith("300"))
        if scope in {"上证A股","shanghai"}:
            return tuple(item for item in symbols if item.endswith(".SH"))
        if scope in {"深证A股","shenzhen"}:
            return tuple(item for item in symbols if item.endswith(".SZ"))
    raise Phase5Error("DATA_NOT_READY", "market_scope_provider_unavailable")
