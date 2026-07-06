# Contract: Backtest UI Controls

## Scope

This contract describes the user-facing and request-handling behavior for the 回测诊断 page. It does not define new strategy output fields or new data-provider APIs.

## Render Contract

The backtest page must render:

- A stock pool source selector named `stock_pool_source`.
- Conditional stock pool controls based on the selected source.
- A date range selector for backtest presets.
- A resolved date range summary for non-custom presets.
- Editable start/end inputs only when the date range is custom.
- Existing data source, cash, cache, result, and report sections.

## Submit Contract

When `/thermostat-backtest` is submitted:

- The server resolves the active stock pool source into concrete symbols.
- The server resolves the active date range into `start_date` and `end_date`.
- Inactive form fields are ignored.
- Empty or invalid pools return a user-facing validation result and do not call the backtest engine.
- Valid requests call the existing event-driven backtest function with resolved symbols, resolved dates, and cash.

## Compatibility Contract

The feature must not change:

- Event-driven backtest calculations.
- Existing backtest result table names and structures.
- Watchlist storage format.
- Account data format.
- Market data source behavior.
- Thermostat strategy page behavior.

## Unavailable Source Contract

If a source appears in the strategy page but cannot be resolved consistently for backtest, the 回测诊断 page must clearly mark it unavailable or prevent submission with an explicit message. It must not silently submit a different source.
