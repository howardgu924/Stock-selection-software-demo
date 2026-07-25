"""Snapshot-bound data catalog and lookahead-safe market views."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Iterable

import pandas as pd

from .market_cache import MarketCache
from .minute_contract import SHANGHAI_TIMEZONE, normalize_security_symbol
from .phase5_models import Phase5Error


class LookaheadAccessError(Phase5Error):
    def __init__(self, message: str = "future_data_access") -> None:
        super().__init__("LOOKAHEAD_ACCESS", message)


class DataCatalog:
    def __init__(self, cache: MarketCache, data_snapshot_id: str) -> None:
        self.cache = cache
        self.data_snapshot_id = data_snapshot_id
        self.partition_ids = cache.snapshot_partition_ids(data_snapshot_id)
        if not self.partition_ids:
            raise Phase5Error("DATA_NOT_READY", "snapshot_not_found")

    def rows(self) -> tuple[dict[str, object], ...]:
        result: list[dict[str, object]] = []
        for partition_id in self.partition_ids:
            result.extend(self.cache.load_rows(partition_id))
        return tuple(result)

    def market_view(
        self, *, as_of: object, phase: str, symbol: str | None = None
    ) -> "MarketView":
        return MarketView(self.rows(), as_of=as_of, phase=phase, symbol=symbol)


class MarketView:
    """Read-only view enforcing event-time field visibility.

    BAR_OPEN exposes the current Open only. DECISION_1000 is capped at 09:55,
    DECISION_1430 at 14:25, and daily inputs at both decisions are capped at the
    preceding trading date by callers' frozen snapshot rows.
    """

    def __init__(self, rows: Iterable[dict[str, object]], *, as_of: object, phase: str, symbol: str | None = None) -> None:
        timestamp = _timestamp(as_of)
        if timestamp is None:
            raise Phase5Error("INVALID_CONFIG", "invalid_market_view_as_of")
        phase = str(phase).upper()
        caps = {"DECISION_1000": (time(10,0), time(9,55)), "DECISION_1430": (time(14,30), time(14,25))}
        self._max_bar_start: pd.Timestamp | None = None
        if phase in caps:
            decision_time, last_bar = caps[phase]
            timestamp = pd.Timestamp(datetime.combine(timestamp.date(), decision_time)).tz_localize(SHANGHAI_TIMEZONE)
            self._max_bar_start = pd.Timestamp(datetime.combine(timestamp.date(), last_bar)).tz_localize(SHANGHAI_TIMEZONE)
        self._as_of = timestamp
        self._phase = phase
        self._symbol = normalize_security_symbol(symbol) if symbol else None
        self._rows = tuple(dict(row) for row in rows)

    def minute_rows(self) -> tuple[dict[str, object], ...]:
        visible: list[dict[str, object]] = []
        for raw in self._rows:
            if "bar_start" not in raw:
                continue
            if self._symbol and _normalized(raw.get("symbol")) != self._symbol:
                continue
            bar_start = _timestamp(raw.get("bar_start"))
            if bar_start is None:
                continue
            if bar_start > self._as_of:
                continue
            if self._max_bar_start is not None and bar_start > self._max_bar_start:
                continue
            row = dict(raw)
            if self._phase == "BAR_OPEN" and bar_start == self._as_of:
                for field in ("high","low","close","volume","amount"):
                    row.pop(field, None)
            elif bar_start + pd.Timedelta(minutes=5) > self._as_of:
                continue
            visible.append(row)
        return tuple(sorted(visible, key=lambda row: (str(row.get("bar_start")), str(row.get("symbol")))))

    def current_open(self) -> object:
        if self._phase != "BAR_OPEN":
            raise LookaheadAccessError("current_open_only_at_bar_open")
        matches = [row for row in self.minute_rows() if _timestamp(row.get("bar_start")) == self._as_of]
        return None if not matches else matches[0].get("open")

    def require_not_after(self, requested_at: object) -> None:
        value = _timestamp(requested_at)
        if value is None or value > self._as_of:
            raise LookaheadAccessError()


def _normalized(value: object) -> str:
    try:
        return normalize_security_symbol(str(value))
    except ValueError:
        return ""


def _timestamp(value: object) -> pd.Timestamp | None:
    try:
        parsed = pd.Timestamp(value)
        if pd.isna(parsed):
            return None
        return parsed.tz_localize(SHANGHAI_TIMEZONE) if parsed.tzinfo is None else parsed.tz_convert(SHANGHAI_TIMEZONE)
    except (TypeError, ValueError, OverflowError):
        return None
