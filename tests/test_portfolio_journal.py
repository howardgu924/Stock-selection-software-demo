from __future__ import annotations

import json

import pytest

from stock_picker.user import ManualPortfolioStore


def test_old_account_json_loads_new_risk_defaults(tmp_path) -> None:
    account_dir = tmp_path / "account"
    account_dir.mkdir()
    (account_dir / "account.json").write_text(
        json.dumps(
            {
                "principal": 100_000.0,
                "cash": 80_000.0,
                "commission_rate": 0.0003,
                "min_commission": 5.0,
                "stamp_tax_rate": 0.001,
            }
        ),
        encoding="utf-8",
    )

    portfolio = ManualPortfolioStore(account_dir).load()

    assert portfolio.slippage_pct == 0.0
    assert portfolio.max_total_position_pct == 0.95


def test_new_account_risk_settings_round_trip(tmp_path) -> None:
    store = ManualPortfolioStore(tmp_path / "account")

    initialized = store.initialize(
        100_000.0,
        slippage_pct=0.0008,
        max_total_position_pct=0.90,
    )
    loaded = store.load()

    assert initialized.slippage_pct == pytest.approx(0.0008)
    assert initialized.max_total_position_pct == pytest.approx(0.90)
    assert loaded.slippage_pct == pytest.approx(0.0008)
    assert loaded.max_total_position_pct == pytest.approx(0.90)
    account = json.loads(store.account_path.read_text(encoding="utf-8"))
    assert account["slippage_pct"] == pytest.approx(0.0008)
    assert account["max_total_position_pct"] == pytest.approx(0.90)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("slippage_pct", -0.0001),
        ("slippage_pct", float("nan")),
        ("slippage_pct", float("inf")),
        ("max_total_position_pct", 0.0),
        ("max_total_position_pct", 1.01),
        ("max_total_position_pct", float("nan")),
        ("max_total_position_pct", float("inf")),
    ],
)
def test_account_rejects_invalid_risk_settings(tmp_path, field, value) -> None:
    kwargs = {field: value}
    with pytest.raises(ValueError, match=field):
        ManualPortfolioStore(tmp_path / field).initialize(100_000.0, **kwargs)


def test_account_load_validates_persisted_risk_settings(tmp_path) -> None:
    account_dir = tmp_path / "account"
    account_dir.mkdir()
    (account_dir / "account.json").write_text(
        json.dumps(
            {
                "principal": 100_000.0,
                "cash": 100_000.0,
                "slippage_pct": 0.0,
                "max_total_position_pct": 2.0,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="max_total_position_pct"):
        ManualPortfolioStore(account_dir).load()


def test_account_save_revalidates_mutated_risk_settings(tmp_path) -> None:
    store = ManualPortfolioStore(tmp_path / "account")
    portfolio = store.initialize(100_000.0)
    portfolio.slippage_pct = float("nan")

    with pytest.raises(ValueError, match="slippage_pct"):
        store.save(portfolio)


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
        strategy="thermostat",
        system="trend_following",
        entry_reason="thermostat signal",
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
    assert portfolio.trades.loc[0, "strategy"] == "thermostat"
    assert portfolio.trades.loc[1, "system"] == "trend_following"
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
