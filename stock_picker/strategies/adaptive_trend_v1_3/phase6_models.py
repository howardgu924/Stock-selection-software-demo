"""Immutable Phase 6 web-controller contracts.

These models deliberately contain no database handles, mutable DataFrames or
provider credentials.  They are safe to retain in the local web session.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .phase5_models import DateRangeSpec, UniverseSpec


class ExecutionBackend(StrEnum):
    PAPER = "PAPER"
    FUTURE_QMT = "FUTURE_QMT"


@dataclass(frozen=True)
class AccountSummaryVM:
    account_profile_id: str
    mode: str
    backtest_initial_cash: Decimal
    paper_cash: Decimal
    paper_position_count: int
    fee_schedule_id: str
    base_currency: str
    provider_priority: tuple[str, ...]
    data_directory_status: str
    latest_readiness_status: str = "EMPTY"


@dataclass(frozen=True)
class AccountSettingsVM:
    account_profile_id: str
    backtest_initial_cash: str
    paper_cash: str
    fee_schedule_id: str
    base_currency: str
    provider_priority: str
    data_directory: str
    report_directory: str


@dataclass(frozen=True)
class UniverseSelectionVM:
    specification: UniverseSpec
    candidate_symbols: tuple[str, ...]
    required_symbols: tuple[str, ...]
    benchmark_symbols: tuple[str, ...]
    current_holding_symbols: tuple[str, ...]
    sources: tuple[str, ...]
    raw_count: int = 0
    normalized_count: int = 0
    duplicate_count: int = 0
    invalid_symbols: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class DateRangeVM:
    specification: DateRangeSpec
    requested_range: tuple[str, str]
    actual_range: tuple[str, str]
    warmup_range: tuple[str, str]
    trading_day_count: int
    warmup_trading_day_count: int


@dataclass(frozen=True)
class ProviderStatusVM:
    provider_id: str
    display_name: str
    source_version: str
    availability: str
    dataset_types: tuple[str, ...]
    frequencies: tuple[str, ...]
    history_range: str
    timezone: str
    price_basis: str
    capabilities: tuple[str, ...]
    last_checked_at: str = ""
    error_code: str = ""
    fallback_available: bool = False
    adjustment_modes: tuple[str, ...] = ()
    supports_rules: bool = False
    supports_suspension: bool = False
    supports_limit_prices: bool = False
    supports_industry: bool = False
    configured: bool = False
    enabled: bool = False
    priority: int = 0


@dataclass(frozen=True)
class CacheProgressVM:
    stage: str
    status: str
    message: str
    completed: int = 0
    total: int = 0


@dataclass(frozen=True)
class DataReadinessVM:
    status: str
    input_hash: str
    data_snapshot_id: str
    price_basis_id: str
    candidate_count: int
    required_count: int
    trading_day_count: int
    warmup_day_count: int
    complete_partitions: tuple[str, ...]
    partial_partitions: tuple[str, ...]
    invalid_partitions: tuple[str, ...]
    failed_partitions: tuple[str, ...]
    providers: tuple[str, ...]
    reasons: tuple[str, ...] = ()
    matches_current_input: bool = False


@dataclass(frozen=True)
class RunListItemVM:
    run_id: str
    short_run_id: str
    mode: str
    status: str
    start_date: str
    end_date: str
    created_at: str
    completed_at: str
    strategy_version: str
    account_profile_id: str
    total_return: str = ""
    max_drawdown: str = ""
    fill_count: int = 0
    open_position_count: int = 0
    degraded: bool = False
    report_status: str = "NOT_GENERATED"


@dataclass(frozen=True)
class RunSummaryVM:
    run_id: str
    status: str
    mode: str
    strategy_version: str
    git_commit_sha: str
    date_range: tuple[str, str]
    account_snapshot_id: str
    data_snapshot_id: str
    price_basis_id: str
    created_at: str
    completed_at: str
    metrics: tuple[tuple[str, str], ...]
    open_position_count: int
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunDetailVM:
    summary: RunSummaryVM
    equity: tuple[tuple[str, str], ...]
    benchmark: tuple[tuple[str, str], ...]
    drawdown: tuple[tuple[str, str], ...]
    exposure: tuple[tuple[str, str], ...]
    position_count: tuple[tuple[str, str], ...]
    fills: tuple[tuple[tuple[str, Any], ...], ...]
    orders: tuple[tuple[tuple[str, Any], ...], ...]
    positions: tuple[tuple[tuple[str, Any], ...], ...]
    decisions: tuple[tuple[tuple[str, Any], ...], ...]
    pending_sells: tuple[tuple[tuple[str, Any], ...], ...]
    cooldowns: tuple[tuple[tuple[str, Any], ...], ...]
    coverage: tuple[tuple[tuple[str, Any], ...], ...]
    audits: tuple[tuple[tuple[str, Any], ...], ...]


@dataclass(frozen=True)
class ReportFileVM:
    name: str
    path: str
    sha256: str
    size_bytes: int
    media_type: str
    valid: bool
    reason: str = ""


@dataclass(frozen=True)
class ErrorVM:
    title: str
    action: str
    code: str
    detail: str = ""
    recoverable: bool = False
    correlation_id: str = ""


@dataclass(frozen=True)
class PreparedInputState:
    input_hash: str
    account_profile_id: str
    mode: str
    universe_spec: UniverseSpec
    date_range_spec: DateRangeSpec
    data_snapshot_id: str
    price_basis_id: str
    universe_snapshot_id: str


@dataclass(frozen=True)
class SubmissionResult:
    run_id: str
    status: str
    reused: bool
    submission_fingerprint: str
