# Quickstart: Backtest UI Controls Validation

## Prerequisites

- Work from the repository root.
- Use the existing virtual environment.
- Do not change strategy/backtest engine code for this feature.

## Targeted Test Command

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py -q
```

Expected result: web-app tests pass, including new tests for 回测诊断 stock pool source selection and date range presets.

## Full Regression Command

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected result: full suite passes, proving the UI change did not break strategy, account, pool, or event-driven backtest behavior.

## Manual UI Check

1. Start the local web app using the project's normal launch path.
2. Open `http://127.0.0.1:8765/backtest`.
3. Confirm the 回测诊断 page shows a stock pool source selector.
4. Select 自选股组合 and confirm existing watchlists are selectable from a dropdown.
5. Select 最近 5 个月 and confirm the actual resolved date range is visible.
6. Confirm raw start/end fields are not the primary required inputs unless 自定义 is selected.
7. Run a small valid backtest and confirm the result summary shows the selected source and resolved date range.

## Regression Checks

- 恒温器策略 page still renders and runs with its existing source/date controls.
- 账户 page still manages watchlists without format or storage changes.
- Backtest results still include event-driven summary, diagnostics, daily portfolio, trades, positions, symbol performance, data quality, and parameters.
