# Quickstart: Validate Backtest Result Readability

## Automated Checks

Run focused web tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q
```

Run the full regression suite before completion:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Manual Web Check

Start the local web app:

```powershell
.\.venv\Scripts\python.exe examples\web_app.py
```

Open:

```text
http://127.0.0.1:8765/backtest
```

Run a representative backtest with:

- A stock pool containing more than one stock.
- A date range with more than one trading day.
- A successful result that produces trades, holdings, daily assets, or diagnostics.

## Expected Result Page

Verify:

- Main backtest result summary is visible near the top.
- Major result groups have Chinese section titles.
- Large detail sections can be collapsed or expanded.
- Table headers are Chinese and do not show raw snake_case fields.
- Stock-level rows show stock code and stock name, or `未知` when unavailable.
- Transaction flow does not show `signal_time`, order status, or `slippage_cost`.
- `shares_after` does not appear as a raw duplicate field.
- Money and price-like values display exactly two decimals.
- Dates, stock codes, share counts, row counts, and percentages keep non-money formatting.
- Long tables keep headers visible or continuously available while scrolling.
- Wide tables remain readable with aligned headers during horizontal scrolling.
- Report download entry is visible, or an unavailable/failed report state is shown.
- Progress shows multiple Chinese stages and count information when available, not only 0 to 1.

## Regression Guard

Confirm that result readability changes do not alter:

- Backtest calculations.
- Strategy actions.
- Transaction simulation values.
- Account or watchlist persistence.
- Data provider selection.
- Exported raw report values.

## Verification Evidence

Recorded on 2026-07-08:

- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q` -> 65 passed.
- `.\.venv\Scripts\python.exe -m pytest tests\test_backtest.py tests\test_event_backtest_engine.py tests\test_thermostat_backtest.py tests\test_web_app.py -q` -> 81 passed.
- `.\.venv\Scripts\python.exe -m pytest -q` -> 196 passed.
- Local `/backtest` HTTP smoke check -> 200.
- Local `/backtest` with `stock_pool_source=watchlist`, `backtest_date_range=custom`, `start=20240101`, and `end=20260706` -> 200 and rendered watchlist/date controls.

Manual limitation: browser visual inspection was represented by local HTTP smoke checks in this run because the stable verification target was server-rendered HTML.
