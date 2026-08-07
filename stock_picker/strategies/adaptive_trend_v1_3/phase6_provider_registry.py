"""Truthful provider registry and bounded read-only connection probes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import metadata
import inspect
from typing import Callable, Iterable, Mapping

from stock_picker.data import MarketDataService

from .phase6_models import ProviderStatusVM


@dataclass(frozen=True)
class ProviderDescriptor:
    provider_id: str
    display_name: str
    source_version: str
    dataset_types: tuple[str, ...]
    frequencies: tuple[str, ...]
    history_range: str
    timezone: str
    price_basis: str
    capabilities: tuple[str, ...]
    probe: Callable[[], object] | None = None
    adjustment_modes: tuple[str, ...] = ("RAW",)
    supports_rules: bool = False
    supports_suspension: bool = False
    supports_limit_prices: bool = False
    supports_industry: bool = False
    configured: bool = True
    enabled: bool = True
    provider: object | None = None

    @classmethod
    def from_provider(
        cls, provider_id: str, provider: object, *, configured: bool = True,
        enabled: bool = True,
    ) -> "ProviderDescriptor":
        methods = {
            name for name in dir(provider)
            if callable(getattr(provider, name, None))
        }
        datasets: list[str] = []
        frequencies: list[str] = []
        capabilities: list[str] = []
        if {"get_history", "get_index_history"} & methods:
            datasets.append("daily")
            frequencies.append("1d")
            capabilities.append("daily")
        if {"get_minute_history", "get_board_minute_history"} & methods:
            datasets.append("minute")
            frequencies.append("5m")
            capabilities.append("minute_5m")
        if "get_realtime_quotes" in methods:
            datasets.append("realtime")
            frequencies.append("realtime")
            capabilities.append("realtime")
        if {"get_industry_boards", "get_board_symbols"} & methods:
            datasets.append("industry")
            capabilities.append("industry")
        if "get_stock_symbols" in methods:
            datasets.append("stock")
            capabilities.append("stock")
        source_version = _source_version(provider)
        adjustments = _adjustment_modes(provider)
        adapter_configured = configured and not (
            hasattr(provider,"username")
            and (not getattr(provider,"username",None) or not getattr(provider,"password",None))
        )
        return cls(
            provider_id=provider_id,
            display_name=type(provider).__name__.removesuffix("Provider"),
            source_version=source_version,
            dataset_types=tuple(sorted(set(datasets))),
            frequencies=tuple(sorted(set(frequencies))),
            history_range=str(getattr(provider, "historical_range", "provider-defined")),
            timezone=str(getattr(provider, "timezone", "Asia/Shanghai")),
            price_basis="/".join(adjustments),
            capabilities=tuple(sorted(set(capabilities))),
            probe=_read_only_probe(provider),
            adjustment_modes=adjustments,
            supports_rules=bool(getattr(provider, "supports_rules", False)),
            supports_suspension=bool(
                getattr(provider, "supports_suspension", "get_history" in methods)
            ),
            supports_limit_prices=bool(getattr(provider, "supports_limit_prices", False)),
            supports_industry="industry" in datasets,
            configured=adapter_configured,
            enabled=enabled and adapter_configured,
            provider=provider,
        )


class ProviderRegistry:
    def __init__(self, providers: Iterable[ProviderDescriptor] = ()) -> None:
        items = tuple(providers)
        ids = tuple(item.provider_id for item in items)
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_provider_id")
        self._providers = tuple(sorted(items, key=lambda item: item.provider_id))
        self._last_results: dict[str, tuple[str, str, str]] = {}

    @classmethod
    def existing(
        cls, adapters: Mapping[str, object] | None = None,
        *, configured: Iterable[str] | None = None,
    ) -> "ProviderRegistry":
        configured_ids = set(configured or MarketDataService.PROVIDER_FACTORIES)
        actual: dict[str, object] = dict(adapters or {})
        descriptors: list[ProviderDescriptor] = []
        for provider_id, factory in MarketDataService.PROVIDER_FACTORIES.items():
            provider = actual.get(provider_id)
            if provider is None:
                try:
                    provider = factory()
                except Exception:
                    provider = None
            if provider is None:
                descriptors.append(_unavailable_descriptor(
                    provider_id, factory, provider_id in configured_ids,
                ))
            else:
                descriptors.append(ProviderDescriptor.from_provider(
                    provider_id, provider,
                    configured=provider_id in configured_ids,
                    enabled=provider_id in configured_ids,
                ))
        return cls(descriptors)

    def inspect(
        self, priorities: Iterable[str] = (), *, dataset_type: str = "daily",
        frequency: str = "1d", price_basis: str = "RAW",
        requested_range: tuple[str, str] | None = None,
    ) -> tuple[ProviderStatusVM, ...]:
        priority_ids = tuple(dict.fromkeys(str(item) for item in priorities))
        configured = set(priority_ids)
        result = []
        for item in self._providers:
            is_configured = item.configured and (
                not priority_ids or item.provider_id in configured
            )
            is_enabled = item.enabled and is_configured
            state, checked, error = self._last_results.get(
                item.provider_id, ("NOT_TESTED", "", "")
            )
            fallback = any(
                other.provider_id != item.provider_id
                and _fallback_pair(
                    item,other,configured,dataset_type,frequency,price_basis,requested_range
                )
                for other in self._providers
            )
            result.append(ProviderStatusVM(
                item.provider_id, item.display_name, item.source_version,
                ("CONFIGURED" if is_configured and state == "NOT_TESTED" else state),
                item.dataset_types, item.frequencies, item.history_range,
                item.timezone, item.price_basis, item.capabilities,
                checked, error, fallback, item.adjustment_modes,
                item.supports_rules, item.supports_suspension,
                item.supports_limit_prices, item.supports_industry,
                is_configured, is_enabled,
                priority_ids.index(item.provider_id) + 1
                if item.provider_id in priority_ids else 0,
            ))
        return tuple(result)

    def partition_providers(
        self, priorities: Iterable[str], *, dataset_type: str,
        frequency: str, price_basis: str = "RAW",
    ) -> tuple[ProviderDescriptor, ...]:
        """Return enabled real adapters in the persisted account priority order."""
        order = tuple(dict.fromkeys(str(item) for item in priorities))
        ranked = sorted(
            self._providers,
            key=lambda item: (
                order.index(item.provider_id) if item.provider_id in order else len(order),
                item.provider_id,
            ),
        )
        return tuple(
            item for item in ranked
            if item.provider is not None
            and _covers(item,set(order),dataset_type,frequency,price_basis,None)
        )

    def test_connections(
        self, timeout_seconds: float = 10.0, *,
        priorities: Iterable[str] = (),
    ) -> tuple[ProviderStatusVM, ...]:
        selected = {str(item) for item in priorities if str(item)}
        for item in self._providers:
            if selected and item.provider_id not in selected:
                continue
            checked = datetime.now().astimezone().isoformat()
            if item.probe is None:
                self._last_results[item.provider_id] = (
                    "UNSUPPORTED_PROBE", checked, "probe_not_supported",
                )
                continue
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(item.probe)
            try:
                future.result(timeout=max(float(timeout_seconds), 0.01))
                self._last_results[item.provider_id] = ("AVAILABLE", checked, "")
            except TimeoutError:
                future.cancel()
                self._last_results[item.provider_id] = (
                    "TIMEOUT", checked, "provider_timeout",
                )
            except Exception as exc:
                self._last_results[item.provider_id] = (
                    "UNAVAILABLE", checked, f"provider_failed:{type(exc).__name__}",
                )
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
        return self.inspect(priorities)


def _covers(
    descriptor: ProviderDescriptor, configured: set[str], dataset_type: str,
    frequency: str, price_basis: str, requested_range: tuple[str, str] | None,
) -> bool:
    if not descriptor.configured or not descriptor.enabled:
        return False
    if configured and descriptor.provider_id not in configured:
        return False
    if dataset_type not in descriptor.dataset_types:
        return False
    if frequency and frequency not in descriptor.frequencies:
        return False
    if price_basis.upper() not in descriptor.adjustment_modes:
        return False
    if requested_range and descriptor.history_range == "current snapshot only":
        return False
    return True


def _fallback_pair(
    primary: ProviderDescriptor, other: ProviderDescriptor, configured: set[str],
    dataset_type: str, frequency: str, price_basis: str,
    requested_range: tuple[str,str] | None,
) -> bool:
    if not primary.configured or not primary.enabled:
        return False
    if configured and primary.provider_id not in configured:
        return False
    if not _covers(other,configured,dataset_type,frequency,price_basis,requested_range):
        return False
    return (
        dataset_type in primary.dataset_types
        and frequency in primary.frequencies
        and price_basis.upper() in primary.adjustment_modes
    )


def _source_version(provider: object) -> str:
    for value in (
        getattr(provider, "source_version", None),
        getattr(provider, "__version__", None),
    ):
        if value:
            return str(value)
    distributions = {
        "AkShareProvider":("akshare",),
        "BaoStockProvider":("baostock",),
        "JoinQuantProvider":("jqdatasdk",),
        "SinaProvider":("requests",),
    }.get(type(provider).__name__,(type(provider).__module__.split(".",1)[0],))
    for distribution in distributions:
        try:
            return metadata.version(distribution)
        except metadata.PackageNotFoundError:
            continue
    return "unknown"


def _adjustment_modes(provider: object) -> tuple[str, ...]:
    explicit = getattr(provider, "adjustment_modes", None)
    if explicit:
        return tuple(str(item).upper() for item in explicit)
    history = getattr(provider, "get_history", None)
    if callable(history):
        parameters = inspect.signature(history).parameters
        if {"adjust", "adjustment"} & set(parameters):
            return ("RAW", "QFQ", "HFQ")
    return ("RAW",)


def _read_only_probe(provider: object) -> Callable[[], object] | None:
    for name in ("ping", "health_check", "get_query_count"):
        method = getattr(provider, name, None)
        if callable(method):
            return method
    realtime = getattr(provider, "get_realtime_quotes", None)
    if callable(realtime):
        return lambda: realtime(("000001.SZ",))
    trade_dates = getattr(provider, "get_trade_dates", None)
    if callable(trade_dates):
        return lambda: trade_dates(
            (datetime.now().date() - timedelta(days=10)).strftime("%Y%m%d"),
            datetime.now().date().strftime("%Y%m%d"),
        )
    stock = getattr(provider, "get_stock_symbols", None)
    if callable(stock):
        return stock
    return None


def _unavailable_descriptor(provider_id: str, factory: object, configured: bool) -> ProviderDescriptor:
    name = getattr(factory, "__name__", provider_id).removesuffix("Provider")
    features = {
        feature for feature, providers in MarketDataService.SUPPORTED_SOURCES.items()
        if provider_id in providers
    }
    datasets = tuple(sorted({
        {"history": "daily", "minute": "minute", "realtime": "realtime",
         "stock": "stock", "market": "industry"}[feature]
        for feature in features
    }))
    frequencies = tuple(sorted({
        {"history": "1d", "minute": "5m", "realtime": "realtime"}.get(feature, "")
        for feature in features
    } - {""}))
    return ProviderDescriptor(
        provider_id, name, "unknown", datasets, frequencies,
        "provider-defined", "Asia/Shanghai", "RAW", tuple(sorted(features)),
        None, ("RAW",), configured=configured, enabled=False,
    )
