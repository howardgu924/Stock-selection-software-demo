from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from stock_picker.strategies.event_backtest import BacktestSettings


@dataclass(frozen=True)
class ResolvedBacktestSettings:
    settings: BacktestSettings
    parameters: pd.DataFrame


def resolve_backtest_settings(
    portfolio=None,
    overrides: dict[str, Any] | None = None,
) -> ResolvedBacktestSettings:
    overrides = overrides or {}
    defaults = BacktestSettings()
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    account_values = {
        "initial_cash": getattr(portfolio, "cash", None),
        "commission_rate": getattr(portfolio, "commission_rate", None),
        "min_commission": getattr(portfolio, "min_commission", None),
        "stamp_tax_rate": getattr(portfolio, "stamp_tax_rate", None),
    }
    for name, default_value in defaults.__dict__.items():
        if name in overrides and overrides[name] is not None:
            values[name] = overrides[name]
            sources[name] = "user_override"
        elif account_values.get(name) is not None:
            values[name] = account_values[name]
            sources[name] = "account_setting"
        else:
            values[name] = default_value
            sources[name] = "system_default"
    settings = BacktestSettings(**values)
    parameters = pd.DataFrame(
        [
            {
                "parameter_name": name,
                "parameter_value": value,
                "parameter_source": sources[name],
                "user_overridden": sources[name] == "user_override",
                "note": "",
            }
            for name, value in values.items()
        ]
    )
    return ResolvedBacktestSettings(settings=settings, parameters=parameters)

