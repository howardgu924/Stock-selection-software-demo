"""Atomic, reproducible Phase 5 JSON/JSONL/Excel report generation."""

from __future__ import annotations

from copy import copy
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import os
from typing import Any, Mapping

import pandas as pd
from openpyxl.styles import Alignment

from .phase5_models import Phase5Error
from .adaptive_v13_schema import SCHEMA_VERSION
from .run_store import RunStore, canonical_json

SHEETS = (
    "运行摘要","每日权益","基准对比","成交明细","订单与失败","每日持仓",
    "候选与评分","退出与Pending","冷却期","数据覆盖","异常与警告",
)
TABLES = {
    "每日权益": "adaptive_v13_daily_account_snapshots",
    "成交明细": "adaptive_v13_fills",
    "订单与失败": "adaptive_v13_fill_requests",
    "候选与评分": "adaptive_v13_decisions",
    "退出与Pending": "adaptive_v13_pending_sell_versions",
    "冷却期": "adaptive_v13_cooldown_records",
    "异常与警告": "adaptive_v13_audit_events",
}


def generate_run_report(
    store: RunStore, run_id: str, report_directory: str | Path,
    *, manifest_context: Mapping[str, Any] | None = None,
    data_readiness: Mapping[str, Any] | None = None,
) -> Path:
    run = store.get_run(run_id)
    if run is None:
        raise Phase5Error("REPORT_WRITE_FAILED", "run_not_found")
    root = Path(report_directory).expanduser().resolve() / run_id
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "run_manifest.json"
    manifest_path.unlink(missing_ok=True)
    try:
        bundle = store.load_snapshot_bundle(run_id)
        config = json.loads(run["config_json"])
        account = json.loads(bundle["account"]["content_json"])
        universe = json.loads(bundle["universe"]["content_json"])
        data = json.loads(bundle["data"]["content_json"])
        _validate_manifest_sources(config, account, universe, data)
        with store.transaction() as connection:
            store.append_audit_event(
                connection,run_id=run_id,event_id="__REPORT__",event_type="REPORT",
                component="run_reporting",action="generate",status="STARTED",
                input_value={"run_id":run_id},output_value={"directory":str(root)},
                reason_code="",message="authoritative report generation started",
                source_ids=(
                    config["account_snapshot_id"], config["universe_snapshot_id"],
                    config["data_snapshot_id"],
                ),
            )
        config_path = _atomic_text(root / "run_config.json", _pretty_json(json.loads(run["config_json"])))
        readiness = {
            "status": data["readiness_status"],
            "required_trade_dates": data["required_trade_dates"],
            "partition_ids": data["partition_ids"],
            "partition_metadata": data["partition_metadata"],
        }
        readiness_path = _atomic_text(root / "data_readiness.json", _pretty_json(readiness))
        audit_rows = store.rows("adaptive_v13_audit_events", run_id)
        audit_path = _atomic_text(root / "audit_log.jsonl", "\n".join(canonical_json(row) for row in audit_rows) + ("\n" if audit_rows else ""))
        excel_path = _excel(store, run_id, root / "backtest_report.xlsx", run)
        files = (excel_path, config_path, audit_path, readiness_path)
        manifest = {
            "run_id": run_id, "run_fingerprint": run["run_fingerprint"],
            "git_commit_sha": config["git_commit_sha"],
            "strategy_version": config["strategy_version"],
            "schema_version": config["schema_version"],
            "account_snapshot_id": config["account_snapshot_id"],
            "account_snapshot_hash": bundle["account"]["content_hash"],
            "universe_snapshot_id": config["universe_snapshot_id"],
            "universe_snapshot_hash": bundle["universe"]["content_hash"],
            "data_snapshot_id": run["data_snapshot_id"],
            "data_snapshot_hash": bundle["data"]["content_hash"],
            "cache_preparation_id": data.get("preparation_id",""),
            "partition_metadata": data["partition_metadata"],
            "trading_rule_versions": data["rule_snapshot_ids"],
            "fee_versions": data["fee_snapshot_ids"],
            "price_basis_id": data["price_basis_id"],
            "date_range": config["date_range"],
            "status": run["status"], "generated_at": datetime.now().astimezone().isoformat(),
            "started_at": run["created_at"], "completed_at": run["updated_at"],
            "warnings": tuple(
                row["reason_code"] for row in audit_rows
                if row["status"] not in {"COMPLETED","VALIDATED","STARTED"} and row["reason_code"]
            ),
            "files": tuple({"name": path.name, "sha256": _file_hash(path)} for path in files),
        }
        _scrub_secrets(manifest)
        manifest_path = _atomic_text(manifest_path, _pretty_json(manifest))
        with store.transaction() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO adaptive_v13_report_manifests
                (run_id,manifest_json,manifest_hash,created_at) VALUES(?,?,?,?)""",
                (run_id,canonical_json(manifest),_file_hash(manifest_path),datetime.now().astimezone().isoformat()),
            )
            store.append_audit_event(
                connection,run_id=run_id,event_id="__REPORT__",event_type="REPORT",
                component="run_reporting",action="generate",status="COMPLETED",
                input_value={"files":tuple(path.name for path in files)},
                output_value={"manifest_hash":_file_hash(manifest_path)},
                reason_code="",message="report and manifest completed",
                source_ids=(config["data_snapshot_id"],),
            )
        return root
    except Exception as exc:
        manifest_path.unlink(missing_ok=True)
        if isinstance(exc, Phase5Error):
            raise
        raise Phase5Error("REPORT_WRITE_FAILED", type(exc).__name__) from exc


def _validate_manifest_sources(config, account, universe, data) -> None:
    required_config = (
        "git_commit_sha","schema_version","strategy_version","account_snapshot_id",
        "universe_snapshot_id","data_snapshot_id","date_range",
    )
    if any(not config.get(name) for name in required_config):
        raise Phase5Error("REPORT_WRITE_FAILED", "manifest_config_metadata_missing")
    if int(config["schema_version"]) != SCHEMA_VERSION:
        raise Phase5Error("REPORT_WRITE_FAILED", "manifest_schema_version_mismatch")
    for label, value in (("account", account), ("universe", universe), ("data", data)):
        if not value.get("snapshot_hash"):
            raise Phase5Error("REPORT_WRITE_FAILED", f"{label}_snapshot_hash_missing")
    required_data = (
        "partition_metadata","rule_snapshot_ids","fee_snapshot_ids",
        "price_basis_id","required_trade_dates","readiness_status",
    )
    if any(not data.get(name) for name in required_data):
        raise Phase5Error("REPORT_WRITE_FAILED", "manifest_data_metadata_missing")


def _excel(store: RunStore, run_id: str, path: Path, run: Mapping[str, Any]) -> Path:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with pd.ExcelWriter(temporary, engine="openpyxl") as writer:
            for name in SHEETS:
                if name == "运行摘要":
                    frame = pd.DataFrame([dict(run)])
                elif name in TABLES:
                    frame = pd.DataFrame(store.rows(TABLES[name], run_id))
                else:
                    frame = pd.DataFrame()
                frame.to_excel(writer, sheet_name=name, index=False)
                sheet = writer.sheets[name]
                sheet.freeze_panes = "A2"
                if frame.shape[1]:
                    sheet.auto_filter.ref = sheet.dimensions
                for cell in sheet[1]:
                    font = copy(cell.font); font.bold = True; cell.font = font
                for row in sheet.iter_rows():
                    for cell in row:
                        cell.alignment = Alignment(wrap_text=True, vertical="top")
                        if str(sheet.cell(1,cell.column).value).lower() == "symbol":
                            cell.number_format = "@"
                for column in sheet.columns:
                    values = [str(cell.value or "") for cell in column]
                    sheet.column_dimensions[column[0].column_letter].width = min(max(max(map(len,values),default=8)+2,10),50)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return path


def _atomic_text(path: Path, content: str) -> Path:
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return path


def _pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _scrub_secrets(value: Any) -> None:
    forbidden = ("password","secret","token","api_key","apikey")
    if isinstance(value, dict):
        for key in list(value):
            if any(item in str(key).lower() for item in forbidden):
                value.pop(key)
            else:
                _scrub_secrets(value[key])
    elif isinstance(value, list):
        for item in value:
            _scrub_secrets(item)
