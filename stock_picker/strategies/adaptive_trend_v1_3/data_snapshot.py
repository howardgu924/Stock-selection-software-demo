"""Immutable data snapshot construction and verification."""

from __future__ import annotations

from .market_cache import MarketCache, RAW_PRICE_BASIS
from .phase5_models import DataSnapshot, Phase5Error


def create_data_snapshot(
    cache: MarketCache, partition_ids, *, price_basis_id: str = RAW_PRICE_BASIS
) -> DataSnapshot:
    if price_basis_id in {"qfq", "hfq", "QFQ", "HFQ"}:
        raise Phase5Error("PRICE_BASIS_MISMATCH", "non_pit_adjusted_forbidden")
    return cache.create_snapshot(partition_ids, price_basis_id=price_basis_id)


def verify_data_snapshot(cache: MarketCache, snapshot: DataSnapshot) -> None:
    cache.verify_snapshot(snapshot)
