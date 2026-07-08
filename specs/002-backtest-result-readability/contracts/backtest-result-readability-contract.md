# Contract: Backtest Result Readability

This contract describes the expected user-facing behavior for the backtest result UI. It is not a raw data schema change.

## Backtest Result Page

After a backtest completes, the page must display:

- A top summary/result area.
- Clearly titled Chinese sections for major result groups.
- Collapsible detail sections for large or lower-frequency result tables.
- A visible report download entry or an explicit unavailable/failed state.

The actual result content may remain near the top of the page.

## Table Label Contract

All visible user-facing result table headers must be Chinese labels.

Default result views must not show raw internal field names such as:

- `signal_time`
- `slippage_cost`
- `shares_after` as a raw label
- snake_case keys without an approved Chinese display label

If a field has no known display label, the renderer must hide it from the default table or provide a controlled Chinese fallback label.

## Transaction Flow Contract

The default transaction-flow table must not show:

- `signal_time`
- order status
- `slippage_cost`

If `shares_after` duplicates operation-after-position information, it must be shown at most once and only with a Chinese label.

## Stock Identity Contract

Stock-level result tables must show:

- Stock code.
- Stock name.

If the name is unavailable, the UI must display a clear fallback such as `未知` while preserving the name column.

## Numeric Display Contract

Money and price-like values in the web interface must display exactly two decimal places.

This includes:

- cash
- principal
- market value
- cost
- fees
- price
- transaction amount
- realized profit/loss
- floating profit/loss
- total portfolio value

The following must not be blindly formatted as money:

- stock codes
- dates
- share counts
- row counts
- identifiers
- percentages
- ratios

Raw values and exported calculation precision must remain unchanged.

## Table Readability Contract

Long and wide result tables must remain readable:

- Column headers stay visible or continuously available while scrolling.
- Horizontal scrolling keeps headers aligned with cells.
- Empty tables show a Chinese empty-state message.

## Progress Contract

Backtest progress must show more than a 0-to-1 transition for non-trivial runs.

Progress should include:

- Chinese stage name.
- Short explanatory message.
- Completed/total counts when known.
- Current item when available, such as current stock, date, table, or report stage.

Expected stage examples:

- Preparing parameters.
- Loading stock data.
- Simulating trades.
- Preparing result tables.
- Preparing report output.
- Completed or failed.

When exact totals are unknown, the stage and message still must update meaningfully.

## Protected Behavior Contract

This feature must not change:

- event-driven backtest calculations
- strategy decisions
- transaction simulation
- account persistence
- watchlist persistence
- stock-pool semantics
- data-source behavior
- exported raw report values
