from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from stock_picker.strategies.event_backtest import BacktestSettings
from stock_picker.user.portfolio import validate_account_risk_settings


@dataclass(frozen=True)
class ResolvedBacktestSettings:
    settings: BacktestSettings
    parameters: pd.DataFrame
    max_total_position_pct: float = 0.95


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
        "slippage_pct": getattr(portfolio, "slippage_pct", None),
    }
    for name, default_value in defaults.__dict__.items():
        account_value = account_values.get(name)
        if name in overrides and overrides[name] is not None:
            values[name] = overrides[name]
            sources[name] = "user_override"
        elif account_value is not None:
            values[name] = account_value
            sources[name] = "account_setting"
        else:
            values[name] = default_value
            sources[name] = "system_default"
    settings = BacktestSettings(**values)
    max_total_position_pct = float(
        getattr(portfolio, "max_total_position_pct", 0.95)
        if portfolio is not None
        else 0.95
    )
    validate_account_risk_settings(
        values["slippage_pct"],
        max_total_position_pct,
    )
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
    parameters = pd.concat(
        [
            parameters,
            pd.DataFrame(
                [
                    {
                        "parameter_name": "max_total_position_pct",
                        "parameter_value": max_total_position_pct,
                        "parameter_source": (
                            "account_setting" if portfolio is not None else "system_default"
                        ),
                        "user_overridden": False,
                        "note": "",
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    return ResolvedBacktestSettings(
        settings=settings,
        parameters=parameters,
        max_total_position_pct=max_total_position_pct,
    )

