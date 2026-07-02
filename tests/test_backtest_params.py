from __future__ import annotations

import pandas as pd

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
    )


def test_user_overrides_take_priority_over_account_settings() -> None:
    resolved = resolve_backtest_settings(
        portfolio=_portfolio(),
        overrides={"initial_cash": 50000.0, "commission_rate": 0.0005},
    )

    assert resolved.settings.initial_cash == 50000.0
    assert resolved.settings.commission_rate == 0.0005
    sources = resolved.parameters.set_index("parameter_name")["parameter_source"].to_dict()
    assert sources["initial_cash"] == "user_override"
    assert sources["commission_rate"] == "user_override"
    assert sources["stamp_tax_rate"] == "account_setting"


def test_missing_account_uses_system_defaults_with_source() -> None:
    resolved = resolve_backtest_settings(portfolio=None, overrides={})

    sources = resolved.parameters.set_index("parameter_name")["parameter_source"].to_dict()
    assert sources["initial_cash"] == "system_default"
    assert sources["commission_rate"] == "system_default"

