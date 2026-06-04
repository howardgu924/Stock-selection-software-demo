from __future__ import annotations

import pytest

from stock_picker.user import ManualPortfolioStore


def test_manual_portfolio_records_buy_sell_and_win_rate(tmp_path) -> None:
    store = ManualPortfolioStore(tmp_path / "account")
    store.initialize(5000.0)

    portfolio = store.buy(
        "600172",
        name="A",
        price=10.0,
        shares=300,
        fees=5.0,
        target_sell_price=12.0,
        timestamp="2026-05-28T13:00:00",
        strategy="turtle_system",
        system="S1",
        entry_reason="S1 breakout",
        signal_date="2026-05-28",
        execution_date="2026-05-28",
    )
    assert portfolio.cash == pytest.approx(1995.0)
    assert portfolio.positions.loc[0, "avg_cost"] == pytest.approx(10.0166666667)

    portfolio = store.sell(
        "600172",
        price=11.0,
        shares=100,
        fees=5.0,
        tax_rate=0.001,
        timestamp="2026-05-29T10:00:00",
        exit_reason="manual partial take profit",
        execution_date="2026-05-29",
    )
    summary = portfolio.summary({"600172": 11.0})

    assert int(portfolio.positions.loc[0, "shares"]) == 200
    assert summary["sell_count"] == 1
    assert summary["win_count"] == 1
    assert summary["win_rate"] == pytest.approx(1.0)
    assert summary["average_holding_days"] == pytest.approx(1.0)
    assert summary["realized_pnl"] > 0
    assert portfolio.trades.loc[0, "strategy"] == "turtle_system"
    assert portfolio.trades.loc[1, "system"] == "S1"
    assert portfolio.trades.loc[1, "exit_reason"] == "manual partial take profit"


def test_manual_portfolio_rejects_oversell(tmp_path) -> None:
    store = ManualPortfolioStore(tmp_path / "account")
    store.initialize(5000.0)
    store.buy("600172", price=10.0, shares=100, fees=5.0)

    with pytest.raises(ValueError, match="only 100 shares held"):
        store.sell("600172", price=11.0, shares=200)


def test_manual_portfolio_uses_account_fee_settings(tmp_path) -> None:
    store = ManualPortfolioStore(tmp_path / "account")
    store.initialize(
        10_000.0,
        commission_rate=0.001,
        min_commission=1.0,
        stamp_tax_rate=0.002,
    )

    portfolio = store.buy("600172", price=10.0, shares=100)
    assert portfolio.trades.loc[0, "fees"] == pytest.approx(1.0)
    assert portfolio.cash == pytest.approx(8999.0)

    portfolio = store.sell("600172", price=11.0, shares=100)
    sell = portfolio.trades.iloc[-1]
    assert sell["fees"] == pytest.approx(1.1)
    assert sell["tax"] == pytest.approx(2.2)


def test_manual_portfolio_adjusts_average_cost_without_cash_change(tmp_path) -> None:
    store = ManualPortfolioStore(tmp_path / "account")
    store.initialize(10_000.0)
    portfolio = store.buy("002579", name="中京电子", price=16.922, shares=100, fees=5.0)
    cash_before = portfolio.cash

    portfolio = store.adjust_cost("002579", avg_cost=19.922, timestamp="2026-06-04T10:30:00")

    assert portfolio.cash == pytest.approx(cash_before)
    assert portfolio.positions.loc[0, "avg_cost"] == pytest.approx(19.922)
    adjustment = portfolio.trades.iloc[-1]
    assert adjustment["side"] == "adjust_cost"
    assert adjustment["price"] == pytest.approx(19.922)
    assert adjustment["shares"] == 100
    assert "adjust avg_cost" in adjustment["note"]
