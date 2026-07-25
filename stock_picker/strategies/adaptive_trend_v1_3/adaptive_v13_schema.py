"""Idempotent SQLite schemas owned exclusively by adaptive trend V1.3."""

SCHEMA_VERSION = 3

RUN_SCHEMA = """
CREATE TABLE IF NOT EXISTS adaptive_v13_schema_version (
    component TEXT PRIMARY KEY, version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS adaptive_v13_runs (
    run_id TEXT PRIMARY KEY, run_fingerprint TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL, config_json TEXT NOT NULL, data_snapshot_id TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, failure_reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS adaptive_v13_data_snapshot_partition_links (
    data_snapshot_id TEXT NOT NULL, partition_id TEXT NOT NULL,
    content_hash TEXT NOT NULL, coverage_json TEXT NOT NULL,
    PRIMARY KEY(data_snapshot_id,partition_id)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_run_events (
    event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, trade_date TEXT NOT NULL,
    event_time TEXT NOT NULL, event_type TEXT NOT NULL, sequence_number INTEGER NOT NULL,
    status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(run_id, trade_date, sequence_number),
    FOREIGN KEY(run_id) REFERENCES adaptive_v13_runs(run_id)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_run_checkpoints (
    run_id TEXT NOT NULL, event_id TEXT NOT NULL, sequence_number INTEGER NOT NULL,
    trade_date TEXT NOT NULL DEFAULT '', event_time TEXT NOT NULL DEFAULT '',
    next_event_id TEXT NOT NULL DEFAULT '',
    state_json TEXT NOT NULL, state_hash TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(run_id, event_id), UNIQUE(run_id, sequence_number),
    FOREIGN KEY(run_id) REFERENCES adaptive_v13_runs(run_id)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_universe_snapshots (
    universe_snapshot_id TEXT PRIMARY KEY, content_json TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adaptive_v13_account_snapshots (
    account_snapshot_id TEXT PRIMARY KEY, content_json TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adaptive_v13_data_snapshots (
    data_snapshot_id TEXT PRIMARY KEY, content_json TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE, price_basis_id TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adaptive_v13_decisions (
    decision_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, event_id TEXT NOT NULL,
    symbol TEXT NOT NULL, decision_type TEXT NOT NULL, status TEXT NOT NULL,
    reasons_json TEXT NOT NULL, payload_json TEXT NOT NULL,
    UNIQUE(run_id,event_id,symbol,decision_type)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_exit_intents (
    exit_intent_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, event_id TEXT NOT NULL,
    symbol TEXT NOT NULL, payload_json TEXT NOT NULL,
    UNIQUE(run_id,event_id,symbol)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_fill_requests (
    fill_request_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, event_id TEXT NOT NULL,
    symbol TEXT NOT NULL, execution_type TEXT NOT NULL, payload_json TEXT NOT NULL,
    UNIQUE(run_id,event_id,symbol,execution_type)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_fills (
    fill_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, fill_request_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adaptive_v13_ledger_events (
    ledger_event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, fill_id TEXT NOT NULL UNIQUE,
    cash_delta TEXT NOT NULL, cash_after TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adaptive_v13_position_state_versions (
    position_event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, fill_id TEXT UNIQUE,
    symbol TEXT NOT NULL, version INTEGER NOT NULL, state_json TEXT NOT NULL,
    UNIQUE(run_id,symbol,version)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_exit_control_state_versions (
    state_event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, symbol TEXT NOT NULL,
    version INTEGER NOT NULL, evaluation_date TEXT, episode_id TEXT, state_json TEXT NOT NULL,
    UNIQUE(run_id,symbol,version)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_pending_sell_versions (
    pending_event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, symbol TEXT NOT NULL,
    version INTEGER NOT NULL, attempt_identity TEXT,
    state_json TEXT NOT NULL, UNIQUE(run_id,symbol,version),
    UNIQUE(run_id,symbol,attempt_identity)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_cooldown_records (
    cooldown_event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, symbol TEXT NOT NULL,
    exit_trade_date TEXT NOT NULL, state_json TEXT NOT NULL,
    UNIQUE(run_id,symbol,exit_trade_date)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_daily_account_snapshots (
    daily_snapshot_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, trade_date TEXT NOT NULL,
    cash TEXT NOT NULL, equity TEXT NOT NULL, exposure TEXT NOT NULL,
    stress TEXT NOT NULL, realized_pnl TEXT NOT NULL, unrealized_pnl TEXT NOT NULL,
    payload_json TEXT NOT NULL, UNIQUE(run_id,trade_date)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_audit_events (
    audit_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, event_id TEXT NOT NULL,
    event_at TEXT NOT NULL, event_type TEXT NOT NULL, symbol TEXT NOT NULL DEFAULT '',
    component TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL,
    reason_code TEXT NOT NULL, message TEXT NOT NULL, input_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL, source_ids_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adaptive_v13_report_manifests (
    run_id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL, manifest_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS adaptive_v13_schema_version (
    component TEXT PRIMARY KEY, version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS adaptive_v13_cache_partitions (
    partition_id TEXT PRIMARY KEY, dataset_type TEXT NOT NULL, logical_key TEXT NOT NULL,
    status TEXT NOT NULL, source TEXT NOT NULL, source_version TEXT NOT NULL,
    price_basis_id TEXT NOT NULL, row_count INTEGER NOT NULL, content_sha256 TEXT NOT NULL,
    supersedes TEXT NOT NULL DEFAULT '', reasons_json TEXT NOT NULL, created_at TEXT NOT NULL,
    normalized_symbol TEXT NOT NULL DEFAULT '', frequency TEXT NOT NULL DEFAULT '',
    coverage_start_date TEXT NOT NULL DEFAULT '', coverage_end_date TEXT NOT NULL DEFAULT '',
    covered_trade_dates_json TEXT NOT NULL DEFAULT '[]',
    expected_trade_dates_json TEXT NOT NULL DEFAULT '[]',
    partition_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(logical_key,content_sha256)
);
CREATE INDEX IF NOT EXISTS adaptive_v13_partition_lookup
ON adaptive_v13_cache_partitions(logical_key,created_at);
CREATE TABLE IF NOT EXISTS adaptive_v13_cache_rows (
    partition_id TEXT NOT NULL, row_number INTEGER NOT NULL, row_json TEXT NOT NULL,
    PRIMARY KEY(partition_id,row_number),
    FOREIGN KEY(partition_id) REFERENCES adaptive_v13_cache_partitions(partition_id)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_trading_calendar (
    partition_id TEXT NOT NULL, trade_date TEXT NOT NULL, is_trading_day INTEGER NOT NULL,
    source TEXT NOT NULL, source_version TEXT NOT NULL, known_at TEXT NOT NULL,
    PRIMARY KEY(partition_id,trade_date)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_security_master_snapshot (
    partition_id TEXT NOT NULL, symbol TEXT NOT NULL, effective_date TEXT NOT NULL,
    listing_status TEXT NOT NULL, source TEXT NOT NULL, source_version TEXT NOT NULL,
    known_at TEXT NOT NULL, payload_json TEXT NOT NULL,
    PRIMARY KEY(partition_id,symbol,effective_date)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_industry_classification_snapshot (
    partition_id TEXT NOT NULL, symbol TEXT NOT NULL, effective_date TEXT NOT NULL,
    industry_code TEXT NOT NULL, industry_name TEXT NOT NULL, source TEXT NOT NULL,
    classification_version TEXT NOT NULL, known_at TEXT NOT NULL, payload_json TEXT NOT NULL,
    PRIMARY KEY(partition_id,symbol,effective_date)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_trading_rule_snapshot (
    partition_id TEXT NOT NULL, symbol TEXT NOT NULL, effective_date TEXT NOT NULL,
    known_at TEXT NOT NULL, source TEXT NOT NULL, rule_version TEXT NOT NULL,
    payload_json TEXT NOT NULL, PRIMARY KEY(partition_id,symbol,effective_date)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_fee_rule_snapshot (
    partition_id TEXT NOT NULL, account_profile_id TEXT NOT NULL, effective_date TEXT NOT NULL,
    known_at TEXT NOT NULL, source TEXT NOT NULL, fee_version TEXT NOT NULL,
    payload_json TEXT NOT NULL, PRIMARY KEY(partition_id,account_profile_id,effective_date)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_daily_bar (
    partition_id TEXT NOT NULL, symbol TEXT NOT NULL, trade_date TEXT NOT NULL,
    open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL, close TEXT NOT NULL,
    volume TEXT NOT NULL, amount TEXT NOT NULL, trade_status TEXT NOT NULL,
    limit_status TEXT NOT NULL, price_basis_id TEXT NOT NULL, source TEXT NOT NULL,
    source_version TEXT NOT NULL, known_at TEXT NOT NULL, ingested_at TEXT NOT NULL,
    PRIMARY KEY(partition_id,symbol,trade_date)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_minute_5m_bar (
    partition_id TEXT NOT NULL, symbol TEXT NOT NULL, trade_date TEXT NOT NULL,
    bar_start TEXT NOT NULL, open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL,
    close TEXT NOT NULL, volume TEXT NOT NULL, amount TEXT NOT NULL,
    trade_status TEXT NOT NULL, limit_status TEXT NOT NULL, price_basis_id TEXT NOT NULL,
    source TEXT NOT NULL, source_version TEXT NOT NULL, known_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL, PRIMARY KEY(partition_id,symbol,bar_start)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_benchmark_data (
    partition_id TEXT NOT NULL, symbol TEXT NOT NULL, trade_date TEXT NOT NULL,
    payload_json TEXT NOT NULL, PRIMARY KEY(partition_id,symbol,trade_date)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_immutable_data_snapshots (
    data_snapshot_id TEXT PRIMARY KEY, snapshot_hash TEXT NOT NULL UNIQUE,
    price_basis_id TEXT NOT NULL, created_at TEXT NOT NULL,
    snapshot_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS adaptive_v13_snapshot_partition_links (
    data_snapshot_id TEXT NOT NULL, partition_id TEXT NOT NULL,
    PRIMARY KEY(data_snapshot_id,partition_id),
    FOREIGN KEY(data_snapshot_id) REFERENCES adaptive_v13_immutable_data_snapshots(data_snapshot_id),
    FOREIGN KEY(partition_id) REFERENCES adaptive_v13_cache_partitions(partition_id)
);
CREATE TABLE IF NOT EXISTS adaptive_v13_market_cache_audit (
    audit_id TEXT PRIMARY KEY, preparation_id TEXT NOT NULL,
    data_snapshot_id TEXT NOT NULL DEFAULT '', logical_key TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL DEFAULT '', dataset_type TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '', source_version TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL, status TEXT NOT NULL, reason_code TEXT NOT NULL DEFAULT '',
    covered_dates_hash TEXT NOT NULL, missing_dates_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL, output_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
"""
