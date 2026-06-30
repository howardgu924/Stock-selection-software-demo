from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from stock_picker.data.models import is_supported_stock_symbol, normalize_symbol, split_symbol_tokens


@dataclass(frozen=True)
class Watchlist:
    name: str
    symbols: list[str]
    updated_at: str

    @property
    def count(self) -> int:
        return len(self.symbols)


@dataclass(frozen=True)
class WatchlistOperationResult:
    name: str
    symbols: list[str]
    status: str = "ok"
    message: str = ""
    duplicates: list[str] | None = None
    invalid_symbols: list[str] | None = None


class WatchlistStore:
    def __init__(self, path: str | Path = "data/user/default") -> None:
        self.path = Path(path)
        self.watchlists_path = self.path / "watchlists.json"
        self.preferences_path = self.path / "preferences.json"

    def list(self) -> list[Watchlist]:
        data = self._load_watchlists()
        return [
            Watchlist(
                name=name,
                symbols=list(item.get("symbols", [])),
                updated_at=str(item.get("updated_at", "")),
            )
            for name, item in data.items()
        ]

    def get(self, name: str) -> Watchlist | None:
        data = self._load_watchlists()
        item = data.get(name)
        if item is None:
            return None
        return Watchlist(
            name=name,
            symbols=list(item.get("symbols", [])),
            updated_at=str(item.get("updated_at", "")),
        )

    def create(self, name: str) -> WatchlistOperationResult:
        data = self._load_watchlists()
        if name in data:
            return WatchlistOperationResult(name=name, symbols=list(data[name].get("symbols", [])), status="name_conflict", message="自选股组合名称已存在。")
        data[name] = {"symbols": [], "updated_at": _now()}
        self._save_watchlists(data)
        return WatchlistOperationResult(name=name, symbols=[])

    def add_symbols(self, name: str, symbols: list[str]) -> WatchlistOperationResult:
        data = self._load_watchlists()
        if name not in data:
            return WatchlistOperationResult(name=name, symbols=[], status="not_found", message=f"自选股组合不存在：{name}")
        current = list(data[name].get("symbols", []))
        seen = set(current)
        duplicates: list[str] = []
        invalid: list[str] = []
        added: list[str] = []
        tokens: list[str] = []
        for symbol in symbols:
            tokens.extend(split_symbol_tokens(symbol))
        if not tokens:
            return WatchlistOperationResult(name=name, symbols=current, status="empty_input", message="股票代码为空。")
        for symbol in tokens:
            if not is_supported_stock_symbol(symbol):
                invalid.append(symbol)
                continue
            normalized = normalize_symbol(symbol)
            if normalized in seen:
                duplicates.append(normalized)
                continue
            seen.add(normalized)
            current.append(normalized)
            added.append(normalized)
        if added:
            data[name] = {"symbols": current, "updated_at": _now()}
            self._save_watchlists(data)
        status = "ok"
        if invalid and not added:
            status = "invalid_symbol"
        elif invalid:
            status = "partial_success"
        elif duplicates and not added:
            status = "duplicate"
        message = _operation_message(added_count=len(added), duplicates=duplicates, invalid=invalid)
        return WatchlistOperationResult(
            name=name,
            symbols=current,
            status=status,
            message=message,
            duplicates=duplicates,
            invalid_symbols=invalid,
        )

    def remove_symbol(self, name: str, symbol: str) -> WatchlistOperationResult:
        data = self._load_watchlists()
        if name not in data:
            return WatchlistOperationResult(name=name, symbols=[], status="not_found", message=f"自选股组合不存在：{name}")
        current = list(data[name].get("symbols", []))
        tokens = split_symbol_tokens(symbol)
        if len(tokens) != 1:
            return WatchlistOperationResult(
                name=name,
                symbols=current,
                status="invalid_symbol",
                message="一次只能删除一只股票，请输入单个股票代码。",
                invalid_symbols=tokens,
            )
        if not is_supported_stock_symbol(tokens[0]):
            return WatchlistOperationResult(
                name=name,
                symbols=current,
                status="invalid_symbol",
                message=f"无法识别或暂不支持的股票代码：{tokens[0]}",
                invalid_symbols=tokens,
            )
        normalized = normalize_symbol(tokens[0])
        if normalized not in current:
            return WatchlistOperationResult(name=name, symbols=current, status="missing_symbol", message=f"股票不存在于自选股组合：{normalized}")
        current = [item for item in current if item != normalized]
        data[name] = {"symbols": current, "updated_at": _now()}
        self._save_watchlists(data)
        return WatchlistOperationResult(name=name, symbols=current)

    def rename(self, old_name: str, new_name: str) -> WatchlistOperationResult:
        data = self._load_watchlists()
        if old_name not in data:
            return WatchlistOperationResult(name=old_name, symbols=[], status="not_found", message=f"自选股组合不存在：{old_name}")
        if new_name in data and new_name != old_name:
            return WatchlistOperationResult(name=old_name, symbols=list(data[old_name].get("symbols", [])), status="name_conflict", message="自选股组合名称已存在。")
        item = data.pop(old_name)
        item["updated_at"] = _now()
        data[new_name] = item
        self._save_watchlists(data)
        return WatchlistOperationResult(name=new_name, symbols=list(item.get("symbols", [])))

    def delete(self, name: str) -> WatchlistOperationResult:
        data = self._load_watchlists()
        if name not in data:
            return WatchlistOperationResult(name=name, symbols=[], status="not_found", message=f"自选股组合不存在：{name}")
        item = data.pop(name)
        self._save_watchlists(data)
        return WatchlistOperationResult(name=name, symbols=list(item.get("symbols", [])))

    def save_last_manual_input(self, value: str) -> None:
        data = self._load_preferences()
        data["last_manual_input"] = value
        self._save_preferences(data)

    def load_last_manual_input(self) -> str:
        return str(self._load_preferences().get("last_manual_input", ""))

    def clear_last_manual_input(self) -> None:
        data = self._load_preferences()
        data["last_manual_input"] = ""
        self._save_preferences(data)

    def _load_watchlists(self) -> dict[str, dict[str, object]]:
        if not self.watchlists_path.exists():
            return {}
        return json.loads(self.watchlists_path.read_text(encoding="utf-8"))

    def _save_watchlists(self, data: dict[str, dict[str, object]]) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self.watchlists_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_preferences(self) -> dict[str, object]:
        if not self.preferences_path.exists():
            return {}
        return json.loads(self.preferences_path.read_text(encoding="utf-8"))

    def _save_preferences(self, data: dict[str, object]) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self.preferences_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _operation_message(*, added_count: int, duplicates: list[str], invalid: list[str]) -> str:
    parts: list[str] = []
    if added_count:
        parts.append(f"已添加 {added_count} 只股票")
    if duplicates:
        parts.append(f"重复代码已跳过：{', '.join(duplicates)}")
    if invalid:
        parts.append(f"无法识别或暂不支持：{', '.join(invalid)}")
    return "；".join(parts)
