from __future__ import annotations

import importlib.util
from pathlib import Path


LEGACY_MODULES = [
    "stock_picker.strategies.engine",
    "stock_picker.strategies.backtest",
    "stock_picker.strategies.turtle_system",
    "stock_picker.screening.engine",
]

LEGACY_EXAMPLES = [
    "examples/run_strategy.py",
    "examples/backtest_strategy.py",
    "examples/run_turtle_system.py",
    "examples/backtest_turtle_system.py",
    "examples/screen_stocks.py",
]


def test_legacy_strategy_modules_are_removed() -> None:
    for module_name in LEGACY_MODULES:
        assert importlib.util.find_spec(module_name) is None


def test_legacy_strategy_example_scripts_are_removed() -> None:
    root = Path(__file__).resolve().parents[1]

    for relative_path in LEGACY_EXAMPLES:
        assert not (root / relative_path).exists()


def test_thermostat_strategy_surface_remains_available() -> None:
    import stock_picker.strategies as strategies
    from stock_picker.strategies.event_backtest import BacktestSettings, EventBacktestEngine
    from stock_picker.strategies.thermostat import run_thermostat_strategy

    assert strategies.run_thermostat_strategy is run_thermostat_strategy
    assert strategies.BacktestSettings is BacktestSettings
    assert strategies.EventBacktestEngine is EventBacktestEngine
