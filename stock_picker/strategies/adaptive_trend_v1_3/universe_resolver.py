"""One authoritative universe resolver shared by cache and run services."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from stock_picker.data.models import is_supported_stock_symbol, normalize_symbol

from .phase5_models import Phase5Error, ResolvedUniverse, UniverseKind, UniverseSpec

BENCHMARK_SYMBOLS = ("000300.SH", "000852.SH", "399006.SZ")


def resolve_universe(
    spec: UniverseSpec,
    *,
    watchlist_loader: Callable[[str], Iterable[str] | None] | None = None,
    market_scope_loader: Callable[[str], Iterable[str] | None] | None = None,
    current_positions: Iterable[str] = (),
) -> ResolvedUniverse:
    try:
        kind = UniverseKind(spec.kind)
    except (TypeError, ValueError):
        raise Phase5Error("INVALID_UNIVERSE", "invalid_universe_kind") from None
    candidates: set[str] = set()
    sources: list[str] = []

    def add(values: Iterable[str] | None, source: str) -> None:
        sources.append(source)
        for raw in values or ():
            if not is_supported_stock_symbol(str(raw)):
                raise Phase5Error("INVALID_UNIVERSE", f"invalid_symbol:{raw}")
            candidates.add(normalize_symbol(str(raw)))

    if kind in {UniverseKind.MANUAL, UniverseKind.COMBINED}:
        add(spec.manual_symbols, "MANUAL")
    if kind in {UniverseKind.WATCHLIST, UniverseKind.COMBINED}:
        if watchlist_loader is None:
            raise Phase5Error("INVALID_UNIVERSE", "watchlist_loader_missing")
        for name in sorted(set(spec.watchlist_names)):
            add(watchlist_loader(name), f"WATCHLIST:{name}")
    if kind in {UniverseKind.MARKET_SCOPE, UniverseKind.COMBINED}:
        if market_scope_loader is None:
            raise Phase5Error("INVALID_UNIVERSE", "market_scope_loader_missing")
        for scope in sorted(set(spec.market_scopes)):
            add(market_scope_loader(scope), f"MARKET_SCOPE:{scope}")
    if not candidates:
        raise Phase5Error("INVALID_UNIVERSE", "empty_universe")

    positions = {
        normalize_symbol(str(raw))
        for raw in current_positions
        if is_supported_stock_symbol(str(raw))
    }
    required = tuple(sorted(candidates | positions | set(BENCHMARK_SYMBOLS)))
    return ResolvedUniverse(
        candidate_symbols=tuple(sorted(candidates)),
        required_symbols=required,
        benchmark_symbols=BENCHMARK_SYMBOLS,
        sources=tuple(sorted(set(sources))),
    )
