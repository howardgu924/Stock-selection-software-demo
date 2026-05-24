from __future__ import annotations

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
