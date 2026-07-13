from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from stock_picker.data.backtest_data import BacktestDataRequest, load_t1_backtest_data
from stock_picker.data.service import MarketDataService
from stock_picker.data.storage import SQLiteMarketDataStore


class DailyProvider:
    def __init__(
        self,
        dates: pd.DatetimeIndex,
        *,
        missing: dict[str, set[str]] | None = None,
        null_close: dict[str, set[str]] | None = None,
        ratio_change_date: str | None = None,
        fail: bool = False,
        payload_metadata: dict[str, str] | None = None,
        suspended: dict[str, set[str]] | None = None,
    ) -> None:
        self.dates = dates
        self.missing = missing or {}
        self.null_close = null_close or {}
        self.ratio_change_date = ratio_change_date
        self.fail = fail
        self.payload_metadata = payload_metadata or {}
        self.suspended = suspended or {}
        self.calls: list[tuple[str, str, str, str]] = []

    def get_trade_dates(self, start_date: str, end_date: str) -> list[str]:
        if self.fail:
            raise RuntimeError("calendar unavailable")
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        return [item.strftime("%Y-%m-%d") for item in self.dates if start <= item <= end]

    def get_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str = "daily",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        if self.fail:
            raise RuntimeError("history unavailable")
        self.calls.append((adjust, symbol, _iso(start_date), _iso(end_date)))
        dates = self.get_trade_dates(start_date, end_date)
        dates = [item for item in dates if item not in self.missing.get(adjust, set())]
        rows = []
        for date in dates:
            sequence = int((pd.Timestamp(date) - self.dates[0]).days)
            bfq_close = 10.0 + sequence / 100.0
            ratio = 0.5 if adjust == "qfq" else 1.0
            if adjust == "qfq" and self.ratio_change_date and date >= self.ratio_change_date:
                ratio = 0.4
            close = bfq_close * ratio
            if date in self.null_close.get(adjust, set()):
                close = None
            row = {
                    "symbol": "600519.SH",
                    "date": date,
                    "open": None if close is None else close - 0.1,
                    "high": None if close is None else close + 0.2,
                    "low": None if close is None else close - 0.2,
                    "close": close,
                    "volume": 1000.0,
                    "amount": None if close is None else close * 1000.0,
                }
            row.update(self.payload_metadata)
            if date in self.suspended.get(adjust, set()):
                row["is_suspended"] = True
            rows.append(row)
        return pd.DataFrame(rows)


def _iso(value: str) -> str:
    text = str(value)
    return text if "-" in text else f"{text[:4]}-{text[4:6]}-{text[6:]}"


def _request(**changes: object) -> BacktestDataRequest:
    base = BacktestDataRequest(
        symbols=("600519",),
        start="2024-03-01",
        end="2024-03-15",
        source="baostock",
    )
    return replace(base, **changes)


def _service(tmp_path: Path, provider: DailyProvider) -> MarketDataService:
    return MarketDataService(
        history_provider=provider,
        stock_provider=object(),
        market_provider=object(),
        store=SQLiteMarketDataStore(tmp_path / "market.sqlite3"),
    )


def _dates() -> pd.DatetimeIndex:
    return pd.bdate_range("2022-12-01", "2024-03-15")


def test_full_cache_hit_uses_exact_daily_streams_without_provider_fetch(tmp_path: Path) -> None:
    provider = DailyProvider(_dates())
    service = _service(tmp_path, provider)
    first = load_t1_backtest_data(service, _request())
    assert first.symbols["600519.SH"].available_warmup_count >= 252
    assert {call[0] for call in provider.calls} == {"qfq", "bfq"}

    provider.calls.clear()
    second = load_t1_backtest_data(service, _request())

    assert provider.calls == []
    assert second.load_summary["cache_hits"] == 2
    assert second.symbols["600519.SH"].indicator_frame["adjust_type"].eq("qfq").all()
    assert second.symbols["600519.SH"].execution_frame["adjust_type"].eq("bfq").all()


def test_partial_cache_hit_fetches_only_contiguous_missing_range(tmp_path: Path) -> None:
    provider = DailyProvider(_dates())
    service = _service(tmp_path, provider)
    load_t1_backtest_data(service, _request())
    with service.store._connect() as conn:
        conn.execute(
            "DELETE FROM backtest_daily_prices WHERE date = ? AND adjust_type = ?",
            ("2024-03-06", "qfq"),
        )
    provider.calls.clear()

    bundle = load_t1_backtest_data(service, _request())

    assert provider.calls == [("qfq", "600519", "2024-03-06", "2024-03-06")]
    assert bundle.load_summary["partial_fetch_ranges"] == 1
    assert not any(issue["code"] == "cache_gap" for issue in bundle.quality_issues)


def test_wrong_adjustment_and_source_rows_are_isolated(tmp_path: Path) -> None:
    provider = DailyProvider(_dates())
    service = _service(tmp_path, provider)
    wrong = provider.get_history("600519", "2023-02-01", "2024-03-15", adjust="qfq")
    wrong["period"] = "daily"
    wrong["adjust_type"] = "hfq"
    wrong["source"] = "other"
    service.store.save_backtest_daily_prices(wrong)
    provider.calls.clear()

    bundle = load_t1_backtest_data(service, _request())

    assert {call[0] for call in provider.calls} == {"qfq", "bfq"}
    exact = service.store.load_backtest_daily_prices(
        "600519.SH", "2023-02-01", "2024-03-15", "daily", "qfq", "baostock"
    )
    assert not exact.empty
    assert exact["source"].eq("baostock").all()
    assert bundle.symbols["600519.SH"].indicator_frame["adjust_type"].eq("qfq").all()


@pytest.mark.parametrize(
    ("payload_metadata", "issue_code"),
    [
        ({"adjust_type": "hfq"}, "adjustment_mismatch"),
        ({"source": "other"}, "source_mismatch"),
    ],
)
def test_provider_metadata_mismatch_is_rejected_and_never_cached(
    tmp_path: Path,
    payload_metadata: dict[str, str],
    issue_code: str,
) -> None:
    provider = DailyProvider(_dates(), payload_metadata=payload_metadata)
    service = _service(tmp_path, provider)

    first = load_t1_backtest_data(service, _request())
    first_call_count = len(provider.calls)
    second = load_t1_backtest_data(service, _request())

    assert any(issue["code"] == issue_code for issue in first.quality_issues)
    assert second.load_summary["cache_hits"] == 0
    assert len(provider.calls) == first_call_count * 2
    cached = service.store.load_backtest_daily_prices(
        "600519.SH", "2023-01-26", "2024-03-15", "daily", "qfq", "baostock"
    )
    assert cached.empty


def test_warmup_shortage_never_uses_post_start_rows(tmp_path: Path) -> None:
    short_dates = pd.bdate_range("2023-12-01", "2024-03-15")
    service = _service(tmp_path, DailyProvider(short_dates))
    bundle = load_t1_backtest_data(service, _request())
    data = bundle.symbols["600519.SH"]

    expected = sum(item < pd.Timestamp("2024-03-01") for item in short_dates)
    assert data.available_warmup_count == expected
    assert any(issue["code"] == "insufficient_data" for issue in data.issues)
    assert data.buy_eligible is False
    cached_bundle = load_t1_backtest_data(service, _request())
    assert cached_bundle.load_summary["cache_hits"] == 0


def test_dual_stream_alignment_keeps_missing_execution_price_explicit(tmp_path: Path) -> None:
    provider = DailyProvider(_dates(), missing={"bfq": {"2024-03-07"}})
    bundle = load_t1_backtest_data(_service(tmp_path, provider), _request())
    data = bundle.symbols["600519.SH"]

    assert "2024-03-07" in data.indicator_frame["date"].tolist()
    assert "2024-03-07" not in data.execution_frame["date"].tolist()
    assert any(issue["code"] == "missing_execution_price" for issue in data.issues)
    assert any(issue["code"] == "adjustment_mismatch" for issue in data.issues)


def test_null_execution_close_is_missing_not_an_invented_status(tmp_path: Path) -> None:
    provider = DailyProvider(_dates(), null_close={"bfq": {"2024-03-07"}})
    bundle = load_t1_backtest_data(_service(tmp_path, provider), _request())
    data = bundle.symbols["600519.SH"]

    assert any(issue["code"] == "missing_execution_price" for issue in data.issues)
    row = data.execution_frame[data.execution_frame["date"] == "2024-03-07"].iloc[0]
    assert row["is_suspended"] in (None, False) or pd.isna(row["is_suspended"])
    assert row["limit_status"] == "limit_status_unknown"
    assert "missing execution price" in row["warning"]


def test_only_explicit_provider_suspension_produces_suspended_status(tmp_path: Path) -> None:
    provider = DailyProvider(
        _dates(),
        null_close={"bfq": {"2024-03-07"}},
        suspended={"bfq": {"2024-03-07"}},
    )
    data = load_t1_backtest_data(_service(tmp_path, provider), _request()).symbols[
        "600519.SH"
    ]

    row = data.execution_frame[data.execution_frame["date"] == "2024-03-07"].iloc[0]
    assert bool(row["is_suspended"]) is True
    assert row["limit_status"] == "suspended"


def test_calendar_gap_invalidates_next_rows_previous_close_and_limits(tmp_path: Path) -> None:
    provider = DailyProvider(_dates(), missing={"bfq": {"2024-03-07"}})
    data = load_t1_backtest_data(_service(tmp_path, provider), _request()).symbols[
        "600519.SH"
    ]

    row = data.execution_frame[data.execution_frame["date"] == "2024-03-08"].iloc[0]
    assert pd.isna(row["prev_close"])
    assert pd.isna(row["limit_up_price"])
    assert pd.isna(row["limit_down_price"])
    assert "previous expected trading date is missing" in row["warning"]


def test_ratio_change_emits_unsupported_corporate_action_evidence(tmp_path: Path) -> None:
    provider = DailyProvider(_dates(), ratio_change_date="2024-03-08")
    bundle = load_t1_backtest_data(_service(tmp_path, provider), _request())

    impacts = bundle.corporate_action_impacts
    assert impacts
    assert impacts[0]["code"] == "unsupported_corporate_action"
    assert impacts[0]["symbol"] == "600519.SH"
    assert impacts[0]["date"] == "2024-03-08"
    assert "previous_ratio" in impacts[0]["evidence"]


def test_low_price_tick_rounding_does_not_emit_corporate_action(tmp_path: Path) -> None:
    class RoundedLowPriceProvider(DailyProvider):
        def get_history(self, *args, **kwargs) -> pd.DataFrame:
            adjust = kwargs.get("adjust", "qfq")
            frame = super().get_history(*args, **kwargs)
            if frame.empty:
                return frame
            sequence = pd.Series(range(len(frame)), index=frame.index)
            bfq_close = (0.11 + (sequence % 7) * 0.01).round(2)
            close = (bfq_close * 0.5).round(2) if adjust == "qfq" else bfq_close
            frame["close"] = close
            frame["open"] = close
            frame["high"] = close
            frame["low"] = close
            frame["amount"] = close * frame["volume"]
            return frame

    bundle = load_t1_backtest_data(
        _service(tmp_path, RoundedLowPriceProvider(_dates())), _request()
    )

    assert bundle.corporate_action_impacts == []


def test_default_store_path_is_stable_when_cwd_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    store = SQLiteMarketDataStore()

    repository_root = Path(__file__).resolve().parents[1]
    assert store.db_path.is_absolute()
    assert store.db_path == repository_root / "data" / "market_data.sqlite3"


def test_provider_failure_returns_bundle_with_explicit_issues(tmp_path: Path) -> None:
    bundle = load_t1_backtest_data(
        _service(tmp_path, DailyProvider(_dates(), fail=True)),
        _request(),
    )

    assert bundle.trading_calendar == ()
    assert any(issue["code"] == "missing_trade_calendar" for issue in bundle.quality_issues)
    assert any(issue["code"] == "missing_history" for issue in bundle.symbols["600519.SH"].issues)
    assert bundle.symbols["600519.SH"].buy_eligible is False


def test_refresh_refetches_full_warmup_and_requested_range_for_both_streams(tmp_path: Path) -> None:
    provider = DailyProvider(_dates())
    service = _service(tmp_path, provider)
    load_t1_backtest_data(service, _request())
    provider.calls.clear()

    load_t1_backtest_data(service, _request(refresh=True))

    assert len(provider.calls) == 2
    assert {call[0] for call in provider.calls} == {"qfq", "bfq"}
    assert {call[2] for call in provider.calls} == {"2023-01-26"}
    assert {call[3] for call in provider.calls} == {"2024-03-15"}
