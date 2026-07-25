"""Immutable Phase 5 orchestration, snapshot, cache and reporting contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping


class Phase5Error(RuntimeError):
    """Stable domain error used at Phase 5 service boundaries."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


class DateRangeKind(StrEnum):
    RECENT_MONTHS = "RECENT_MONTHS"
    RECENT_YEARS = "RECENT_YEARS"
    CUSTOM = "CUSTOM"


class UniverseKind(StrEnum):
    MANUAL = "MANUAL"
    WATCHLIST = "WATCHLIST"
    MARKET_SCOPE = "MARKET_SCOPE"
    COMBINED = "COMBINED"


class RunMode(StrEnum):
    BACKTEST = "BACKTEST"
    DAILY_PAPER = "DAILY_PAPER"


class NetworkAccessPolicy(StrEnum):
    FORBID = "FORBID"
    ALLOW_CACHE_PREPARATION = "ALLOW_CACHE_PREPARATION"


class PartitionStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INVALID = "INVALID"
    FAILED = "FAILED"


class RunStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_OPEN_POSITIONS = "COMPLETED_WITH_OPEN_POSITIONS"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class DateRangeSpec:
    kind: DateRangeKind | str
    value: int | None = None
    start_date: date | str | None = None
    end_date: date | str | None = None


@dataclass(frozen=True)
class ResolvedDateRange:
    requested_start_date: date
    requested_end_date: date
    actual_start_date: date
    actual_end_date: date
    warmup_start_date: date
    trading_dates: tuple[date, ...]
    warmup_dates: tuple[date, ...]
    warmup_trading_days: int = 320


@dataclass(frozen=True)
class UniverseSpec:
    kind: UniverseKind | str
    manual_symbols: tuple[str, ...] = ()
    watchlist_names: tuple[str, ...] = ()
    market_scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedUniverse:
    candidate_symbols: tuple[str, ...]
    required_symbols: tuple[str, ...]
    benchmark_symbols: tuple[str, ...]
    sources: tuple[str, ...]


@dataclass(frozen=True)
class CachePartition:
    partition_id: str
    dataset_type: str
    logical_key: str
    status: PartitionStatus
    source: str
    source_version: str
    price_basis_id: str
    row_count: int
    content_sha256: str
    supersedes: str = ""
    reasons: tuple[str, ...] = ()
    normalized_symbol: str = ""
    frequency: str = ""
    coverage_start_date: str = ""
    coverage_end_date: str = ""
    covered_trade_dates: tuple[str, ...] = ()
    expected_trade_dates: tuple[str, ...] = ()
    partition_version: int = 1


@dataclass(frozen=True)
class DataSnapshot:
    data_snapshot_id: str
    partition_ids: tuple[str, ...]
    price_basis_id: str
    created_at: str
    snapshot_hash: str
    partition_hashes: tuple[tuple[str, str], ...] = ()
    required_trade_dates: tuple[str, ...] = ()
    rule_snapshot_ids: tuple[str, ...] = ()
    fee_snapshot_ids: tuple[str, ...] = ()
    readiness_status: str = "READY"
    partition_metadata: tuple[tuple[str, ...], ...] = ()
    preparation_id: str = ""


@dataclass(frozen=True)
class DataReadinessReport:
    status: str
    requested_range: tuple[str, str]
    actual_range: tuple[str, str]
    warmup_range: tuple[str, str]
    candidate_symbols: tuple[str, ...]
    required_symbols: tuple[str, ...]
    benchmark_symbols: tuple[str, ...]
    complete_partitions: tuple[str, ...]
    partial_partitions: tuple[str, ...]
    invalid_partitions: tuple[str, ...]
    failed_partitions: tuple[str, ...]
    sources_used: tuple[str, ...]
    price_basis_id: str
    data_snapshot_id: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountProfile:
    account_profile_id: str
    backtest_initial_cash: Decimal | str
    paper_cash: Decimal | str
    fee_schedule_id: str
    base_currency: str
    default_universe: UniverseSpec
    provider_priority: tuple[str, ...]
    data_directory: Path | str
    report_directory: Path | str


@dataclass(frozen=True)
class AccountSnapshot:
    account_snapshot_id: str
    account_profile_id: str
    run_mode: RunMode
    cash: Decimal
    positions: tuple[tuple[str, Any], ...]
    fee_schedule_id: str
    base_currency: str
    created_at: str
    snapshot_hash: str
    provider_priority: tuple[str, ...] = ()
    data_directory: str = ""
    report_directory: str = ""
    initial_position_policy: str = "EMPTY"
    exit_controls: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class UniverseSnapshot:
    universe_snapshot_id: str
    candidate_symbols: tuple[str, ...]
    required_symbols: tuple[str, ...]
    current_holding_symbols: tuple[str, ...]
    benchmark_symbols: tuple[str, ...]
    source_specification: tuple[str, ...]
    snapshot_hash: str
    created_at: str


@dataclass(frozen=True)
class RunConfig:
    run_mode: RunMode
    strategy_version: str
    account_snapshot_id: str
    universe_snapshot_id: str
    data_snapshot_id: str
    date_range: ResolvedDateRange
    warmup_trading_days: int
    price_basis_id: str
    network_policy: NetworkAccessPolicy
    report_directory: str
    initial_position_policy: str
    created_at: str
    config_hash: str
    git_commit_sha: str = ""
    schema_version: int = 3


@dataclass(frozen=True)
class ClockEvent:
    event_id: str
    trade_date: date
    event_time: str
    event_type: str
    sequence_number: int
    bar_start: str = ""


@dataclass(frozen=True)
class LedgerEvent:
    ledger_event_id: str
    run_id: str
    fill_id: str
    cash_delta: Decimal
    cash_after: Decimal
    created_at: str


@dataclass(frozen=True)
class PerformanceMetrics:
    total_return: Decimal | None
    annualized_return: Decimal | None
    annualized_volatility: Decimal | None
    sharpe: Decimal | None
    max_drawdown: Decimal | None
    average_exposure: Decimal | None
    max_exposure: Decimal | None
    turnover: Decimal | None
    realized_win_rate: Decimal | None
    profit_factor: Decimal | None
    buy_count: int
    sell_count: int
    total_fees: Decimal


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    status: str
    fingerprint: str
    start_date: str
    end_date: str
    final_cash: Decimal
    final_equity: Decimal
    open_position_count: int
    metrics: PerformanceMetrics | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ServiceResult:
    status: str
    value: Any = None
    error_code: str = ""
    reasons: tuple[str, ...] = ()


def freeze_mapping(value: Mapping[str, Any] | None) -> tuple[tuple[str, Any], ...]:
    return tuple(sorted((str(key), item) for key, item in (value or {}).items()))
