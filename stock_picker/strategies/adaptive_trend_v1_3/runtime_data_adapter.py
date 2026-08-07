"""Small cache-to-runtime bridge used by the Phase 6 composition root.

This module deliberately owns data shape conversion only. Strategy functions and
execution rules remain in their existing Phase 1--4B modules.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

import pandas as pd

from .market_cache import MarketCache
from .phase3_models import FeeRuleSnapshot, TradingRuleSnapshot
from .run_orchestrator import RuntimeDataDependencies


def normalize_baostock_minute_frame(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Convert BaoStock's completed-bar timestamp to the V1.3 minute contract.

    BaoStock labels a 09:35 bar by its end time; V1.3 labels it by its start
    time. No bars are filled or invented.
    """
    columns = [
        "symbol", "trade_date", "bar_start", "open", "high", "low", "close",
        "volume", "amount", "trade_status", "limit_status",
    ]
    if raw is None or raw.empty:
        return pd.DataFrame(columns=columns)
    frame = raw.copy(deep=True)
    if "datetime" not in frame:
        raise ValueError("baostock_minute_datetime_missing")
    end_time = pd.to_datetime(frame["datetime"], errors="coerce")
    start_time = end_time - pd.Timedelta(minutes=5)
    frame["symbol"] = symbol
    frame["trade_date"] = start_time.dt.strftime("%Y-%m-%d")
    frame["bar_start"] = start_time.dt.strftime("%Y-%m-%d %H:%M:%S")
    frame["trade_status"] = "normal"
    frame["limit_status"] = "normal"
    return frame[columns].reset_index(drop=True)


class RuntimeDataAdapter:
    """Read the immutable Phase 6 snapshot without reaching a provider."""

    def __init__(self, cache: MarketCache, run_store: Any, run_id: str) -> None:
        self.cache = cache
        self.run_store = run_store
        self.run_id = run_id
        bundle = run_store.load_snapshot_bundle(run_id)
        self._rows = self._load_rows(bundle["config"]["data_snapshot_id"])

    def _load_rows(self, snapshot_id: str) -> tuple[dict[str, Any], ...]:
        snapshot = self.cache.load_snapshot(snapshot_id)
        metadata = {
            item[0]: {
                "dataset_type": item[1],
                "logical_key": item[2],
                "normalized_symbol": item[6],
            }
            for item in snapshot.partition_metadata
        }
        rows: list[dict[str, Any]] = []
        for partition_id in snapshot.partition_ids:
            partition = metadata.get(partition_id, {})
            logical_key = str(partition.get("logical_key", ""))
            canonical_symbol = str(partition.get("normalized_symbol", ""))
            if not canonical_symbol and ":" in logical_key:
                canonical_symbol = logical_key.rsplit(":", 1)[1]
            for source in self.cache.load_rows(partition_id):
                row = dict(source)
                if canonical_symbol:
                    row["symbol"] = canonical_symbol
                row["_dataset_type"] = str(partition.get("dataset_type", ""))
                row["_logical_key"] = logical_key
                rows.append(row)
        return tuple(rows)

    def daily_history(self, symbol: str) -> pd.DataFrame:
        rows = [
            row for row in self._rows
            if str(row.get("symbol", "")) == symbol
            and row.get("_dataset_type") in {"daily_bar", "benchmark_daily_bar"}
            and "date" in row
        ]
        frame = pd.DataFrame(rows)
        frame = frame.drop(columns=["_dataset_type", "_logical_key"], errors="ignore")
        return frame.sort_values("date").reset_index(drop=True) if not frame.empty else frame

    def minute_bars(self, symbol: str) -> pd.DataFrame:
        rows = [
            row for row in self._rows
            if str(row.get("symbol", "")) == symbol
            and row.get("_dataset_type") == "minute_5m_bar"
            and "bar_start" in row
        ]
        frame = pd.DataFrame(rows)
        frame = frame.drop(columns=["_dataset_type", "_logical_key"], errors="ignore")
        return frame.sort_values("bar_start").reset_index(drop=True) if not frame.empty else frame

    def stock_info(self, symbol: str) -> Mapping[str, str]:
        """Return the minimal identity/status record required by runtime code."""
        status = self.trade_status(symbol)
        return {"symbol": symbol, "name": symbol, **status}

    def current_price(self, symbol: str, as_of: object | None = None) -> Any:
        frame = self.minute_bars(symbol)
        if frame.empty:
            frame = self.daily_history(symbol)
            return None if frame.empty else frame.iloc[-1].get("close")
        if as_of is not None:
            frame = frame[pd.to_datetime(frame["bar_start"]) <= pd.Timestamp(as_of)]
        return None if frame.empty else frame.iloc[-1].get("close")

    def trade_status(self, symbol: str, as_of: object | None = None) -> Mapping[str, str]:
        frame = self.minute_bars(symbol)
        if frame.empty:
            frame = self.daily_history(symbol)
        if as_of is not None and not frame.empty:
            field = "bar_start" if "bar_start" in frame else "date"
            frame = frame[pd.to_datetime(frame[field]) <= pd.Timestamp(as_of)]
        if frame.empty:
            return {"trade_status": "unknown", "limit_status": "unknown"}
        row = frame.iloc[-1]
        return {"trade_status": str(row.get("trade_status", "unknown")),
                "limit_status": str(row.get("limit_status", "unknown"))}

    def dependencies(self) -> RuntimeDataDependencies:
        """Expose the existing runtime dependency contract for the run engine.

        The Phase 6 data bridge is intentionally narrow; an application may
        still inject the richer decision builder through ``dependency_factory``.
        """
        def decision_1000(state, event):
            index_symbols = ("000300.SH", "000852.SH", "399006.SZ")
            histories = {
                symbol: self.daily_history(symbol)
                for symbol in {row.get("symbol") for row in self._rows}
                if symbol
            }
            benchmark_histories = {
                symbol: histories.get(symbol, pd.DataFrame()) for symbol in index_symbols
            }
            candidates = {
                symbol: frame for symbol, frame in histories.items()
                if symbol not in index_symbols
            }
            known_dates = sorted({
                pd.Timestamp(value).date()
                for frame in histories.values() if not frame.empty
                for value in frame.get("date", ())
                if pd.Timestamp(value).date() < event.trade_date
            })
            as_of = known_dates[-1] if known_dates else event.trade_date
            return {
                "market_overlay": {"index_histories": benchmark_histories, "as_of": as_of},
                "opportunity_score": {
                    "histories": candidates,
                    "benchmark_histories": benchmark_histories,
                    "as_of": as_of,
                },
                "divergence": (), "risk_overlay": (), "execution_gate": (),
                "t1_risk": (), "position_sizing": (),
                "portfolio_allocator": {
                    "existing_holdings": (),
                    "portfolio_equity": Decimal(str(state.get("cash", "0"))),
                    "evaluation_as_of": event.trade_date,
                },
            }
        def decision_1430(state, event):
            return {
                "holdings": (), "replacement_candidates": (),
                "portfolio_equity": Decimal(str(state.get("cash", "0"))),
                "existing_exposure": Decimal("0"),
                "effective_exposure_cap": Decimal("0"),
                "market_allows_new": False, "emergency_normal": True,
                "no_new_slots": False,
            }
        return RuntimeDataDependencies(
            decision_1000_data=decision_1000, bar_close_data=lambda _state, _event: {},
            decision_1430_data=decision_1430,
            session_close_data=lambda _state, _event: {},
            minute_bars=lambda request, _event: self.minute_bars(request.symbol),
            trading_rule=lambda _symbol, event: TradingRuleSnapshot(
                "SSE", "MAIN", "STOCK", event.trade_date, 100, 100, True, 0.01,
            ),
            fee_rule=lambda event: FeeRuleSnapshot(
                event.trade_date, 0.0003, 5, 0, 0.00002, 0, 0.00002, 0.001,
            ),
        )
