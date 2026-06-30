from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class StockSymbol:
    raw: str

    @property
    def code(self) -> str:
        value = self.raw.strip().upper()
        if "." in value:
            return value.split(".", 1)[0]
        return value

    @property
    def normalized(self) -> str:
        code = self.code
        if code.startswith(("6", "9")):
            return f"{code}.SH"
        if code.startswith(("0", "3", "2")):
            return f"{code}.SZ"
        if code.startswith(("4", "8")):
            return f"{code}.BJ"
        return code


def normalize_symbol(symbol: str) -> str:
    return StockSymbol(symbol).normalized


def symbol_code(symbol: str) -> str:
    return StockSymbol(symbol).code


def split_symbol_tokens(raw: str) -> list[str]:
    return [item.strip() for item in re.split(r"[\s,，;；、]+", str(raw).strip()) if item.strip()]


def is_supported_stock_symbol(symbol: str) -> bool:
    code = symbol_code(str(symbol).strip())
    normalized = normalize_symbol(str(symbol).strip())
    return len(code) == 6 and code.isdigit() and normalized.endswith((".SH", ".SZ", ".BJ"))


def baostock_symbol(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    if normalized.endswith(".SH"):
        return f"sh.{symbol_code(normalized)}"
    if normalized.endswith(".SZ"):
        return f"sz.{symbol_code(normalized)}"
    if normalized.endswith(".BJ"):
        return f"bj.{symbol_code(normalized)}"
    return normalized.lower()


def sina_symbol(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    if normalized.endswith(".SH"):
        return f"sh{symbol_code(normalized)}"
    if normalized.endswith(".SZ"):
        return f"sz{symbol_code(normalized)}"
    if normalized.endswith(".BJ"):
        return f"bj{symbol_code(normalized)}"
    return symbol_code(normalized).lower()


@dataclass(frozen=True)
class StockInfo:
    symbol: str
    code: str
    name: str

    @classmethod
    def from_code_name(cls, code: str, name: str) -> "StockInfo":
        normalized = normalize_symbol(code)
        return cls(symbol=normalized, code=symbol_code(normalized), name=name)
