from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from stock_picker.strategies.backtest_params import resolve_backtest_settings
from stock_picker.user.portfolio import ManualPortfolio


def _portfolio() -> ManualPortfolio:
    return ManualPortfolio(
        principal=100000.0,
        cash=40000.0,
        commission_rate=0.0002,
        min_commission=3.0,
        stamp_tax_rate=0.001,
        positions=pd.DataFrame(),
        trades=pd.DataFrame(),
        slippage_pct=0.0008,
        max_total_position_pct=0.90,
    )


def test_simulated_cash_override_does_not_replace_account_risk_settings() -> None:
    resolved = resolve_backtest_settings(
        portfolio=_portfolio(),
        overrides={"initial_cash": 50000.0},
    )

    assert resolved.settings.initial_cash == 50000.0
    assert resolved.settings.commission_rate == 0.0002
    assert resolved.settings.min_commission == 3.0
    assert resolved.settings.stamp_tax_rate == 0.001
    assert resolved.settings.slippage_pct == 0.0008
    assert resolved.max_total_position_pct == 0.90
    sources = resolved.parameters.set_index("parameter_name")["parameter_source"].to_dict()
    assert sources["initial_cash"] == "user_override"
    assert sources["commission_rate"] == "account_setting"
    assert sources["stamp_tax_rate"] == "account_setting"


def test_generic_setting_overrides_remain_supported() -> None:
    resolved = resolve_backtest_settings(
        portfolio=_portfolio(),
        overrides={"commission_rate": 0.0005},
    )

    assert resolved.settings.commission_rate == 0.0005
    parameters = resolved.parameters.set_index("parameter_name")
    assert parameters.at["commission_rate", "parameter_source"] == "user_override"


def test_missing_account_uses_system_defaults_with_source() -> None:
    resolved = resolve_backtest_settings(portfolio=None, overrides={})

    sources = resolved.parameters.set_index("parameter_name")["parameter_source"].to_dict()
    assert sources["initial_cash"] == "system_default"
    assert sources["commission_rate"] == "system_default"
    assert resolved.settings.slippage_pct == 0.0
    assert resolved.max_total_position_pct == 0.95


def test_account_slippage_and_total_cap_override_system_defaults() -> None:
    resolved = resolve_backtest_settings(portfolio=_portfolio())

    assert resolved.settings.slippage_pct == 0.0008
    assert resolved.max_total_position_pct == 0.90
    parameters = resolved.parameters.set_index("parameter_name")
    assert parameters.at["slippage_pct", "parameter_source"] == "account_setting"
    assert parameters.at["max_total_position_pct", "parameter_source"] == "account_setting"


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
def test_direct_resolution_rejects_invalid_account_risk_settings(field, value) -> None:
    portfolio = SimpleNamespace(
        cash=40_000.0,
        commission_rate=0.0002,
        min_commission=3.0,
        stamp_tax_rate=0.001,
        slippage_pct=0.0008,
        max_total_position_pct=0.90,
    )
    setattr(portfolio, field, value)

    with pytest.raises(ValueError, match=field):
        resolve_backtest_settings(portfolio=portfolio)

