from __future__ import annotations

import pandas as pd
import pytest

from stock_picker.data.models import StockInfo
from stock_picker.strategies import TurtleConfig, backtest_turtle_system, run_turtle_system
from stock_picker.strategies.turtle_system import TurtlePosition, _entry_signal, _unit_shares


class FakeTurtleService:
    def __init__(self, history: pd.DataFrame) -> None:
        self.history = history

    def get_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        refresh: bool = False,
        indicators: bool = False,
    ) -> pd.DataFrame:
        frame = self.history.copy()
        frame["symbol"] = symbol
        return frame


def _history(closes: list[float], width: float = 1.0) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    rows = []
    for date, close in zip(dates, closes):
        rows.append(
            {
                "date": date.strftime("%Y-%m-%d"),
                "open": close,
                "high": close + width / 2,
                "low": close - width / 2,
                "close": close,
                "volume": 1_000_000,
            }
        )
    return pd.DataFrame(rows)


def test_turtle_s1_breakout_opens_and_channel_exits() -> None:
    closes = [10.0] * 20 + [20.0, 20.0, 20.0, 20.0, 4.0, 4.0]
    service = FakeTurtleService(_history(closes, width=11.0))

    result = backtest_turtle_system(
        service,
        [StockInfo("600001.SH", "600001", "A")],
        start_date="2024-01-01",
        end_date="2024-01-26",
        initial_cash=100_000,
        config=TurtleConfig(
            risk_pct=0.1,
            commission_rate=0.0,
            min_commission=0.0,
            stamp_tax_rate=0.0,
        ),
    )

    assert result.trades["action"].tolist()[:2] == ["buy", "sell"]
    assert result.trades.loc[0, "system"] == "S1"
    assert "channel exit" in result.trades.loc[1, "exit_reason"]


def test_turtle_s2_signal_is_valid_when_s1_is_skipped() -> None:
    frame = _history([10.0] * 55 + [12.0])
    state = TurtlePosition("600001.SH", "600001", skip_next_s1=True)

    signal = _entry_signal(frame, state, close=12.0, config=TurtleConfig())

    assert signal is not None
    assert signal["system"] == "S2"


def test_turtle_s1_skip_resets_after_next_s1_signal() -> None:
    frame = _history([10.0] * 20 + [12.0])
    state = TurtlePosition("600001.SH", "600001", skip_next_s1=True)

    signal = _entry_signal(frame, state, close=12.0, config=TurtleConfig())

    assert signal is None
    assert state.skip_next_s1 is False


def test_turtle_adds_half_n_units_up_to_max_units() -> None:
    closes = [10.0] * 20 + [12.0, 12.6, 13.2, 13.8, 14.4, 15.0, 15.6, 16.2]
    service = FakeTurtleService(_history(closes, width=1.0))

    result = backtest_turtle_system(
        service,
        ["600001"],
        start_date="2024-01-01",
        end_date="2024-01-28",
        initial_cash=100_000,
        config=TurtleConfig(commission_rate=0.0, min_commission=0.0, stamp_tax_rate=0.0),
    )

    buys = result.trades[result.trades["action"].isin(["buy", "add"])]
    assert buys["units_after"].max() == 4
    assert (result.trades["action"] == "add").sum() == 3


def test_turtle_2n_stop_exits_before_channel_exit() -> None:
    closes = [10.0] * 20 + [12.0, 12.0, 9.0, 9.0]
    service = FakeTurtleService(_history(closes, width=1.0))

    result = backtest_turtle_system(
        service,
        ["600001"],
        start_date="2024-01-01",
        end_date="2024-01-24",
        initial_cash=100_000,
        config=TurtleConfig(commission_rate=0.0, min_commission=0.0, stamp_tax_rate=0.0),
    )

    sells = result.trades[result.trades["action"] == "sell"]
    assert "2N stop" in sells.iloc[0]["exit_reason"]


def test_turtle_unit_sizing_uses_risk_and_a_share_lots() -> None:
    config = TurtleConfig(risk_pct=0.01, lot_size=100)

    assert _unit_shares(100_000, 2.0, config) == 500
    assert _unit_shares(10_000, 3.0, config) == 0


def test_run_turtle_system_outputs_ranked_risk_fields() -> None:
    service = FakeTurtleService(_history([10.0] * 20 + [12.0]))

    result = run_turtle_system(
        service,
        ["600001"],
        start_date="2024-01-01",
        end_date="2024-01-21",
        cash=100_000,
    )

    assert result.signals.loc[0, "rank"] == 1
    assert result.signals.loc[0, "system"] == "S1"
    assert result.signals.loc[0, "stop_price"] == pytest.approx(9.85)
    assert result.signals.loc[0, "next_add_price"] == pytest.approx(12.5375)
