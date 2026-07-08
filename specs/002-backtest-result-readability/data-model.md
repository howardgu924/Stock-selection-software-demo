# Data Model: Backtest Result Readability

This feature defines display-layer models only. It does not change persisted data, raw backtest payloads, or exported report values.

## ResultSection

Represents one visible section on the completed backtest result page.

- `id`: stable section identifier for rendering and tests.
- `title`: Chinese user-facing section title.
- `priority`: ordering hint; summary appears before detail sections.
- `collapsible`: whether users may collapse the section.
- `default_expanded`: whether the section opens by default.
- `empty_state`: Chinese text shown when the section has no rows or content.
- `content_type`: summary, table, report, diagnostics, or progress/result status.

Validation rules:

- Every section title must be Chinese user-facing text.
- Large detail sections should be collapsible.
- Empty sections must show clear empty-state text.

## ResultTableView

Represents a user-facing table derived from existing backtest result rows.

- `id`: stable table identifier.
- `section_id`: owning result section.
- `columns`: ordered `ResultDisplayField` list.
- `rows`: raw row values after display-only filtering.
- `sticky_header`: whether the table keeps headers visible.
- `horizontal_scroll`: whether wide-table scrolling is enabled.

Validation rules:

- Default visible columns must not include hidden internal fields.
- Headers must remain aligned with table cells when horizontally scrolled.
- Tables with many rows must preserve column context through sticky headers or equivalent behavior.

## ResultDisplayField

Defines how a raw field is shown in the UI.

- `source_key`: raw payload key.
- `label`: Chinese user-facing label.
- `visible_by_default`: whether the field appears in default views.
- `format_type`: money, price, quantity, date, percent, ratio, text, code, or status.
- `duplicate_of`: optional semantic field this duplicates.
- `fallback_text`: optional fallback for missing values.

Validation rules:

- Raw internal names must not be used as final labels.
- `signal_time`, order status, and `slippage_cost` are hidden in the default transaction-flow view.
- `shares_after` appears at most once and only with a Chinese label if shown.

## MonetaryDisplayValue

Represents a user-facing money or price-like value.

- `raw_value`: original numeric value.
- `display_value`: formatted string.
- `format_type`: money or price.

Validation rules:

- User-facing money and price-like display strings must use exactly two decimals.
- Formatting must not mutate `raw_value`.
- Dates, stock codes, IDs, share counts, row counts, percentages, and ratios must not be formatted as money.

## StockIdentityDisplay

Represents stock identity in stock-level result tables.

- `code`: stock code.
- `name`: stock name if available.
- `display_name`: name or configured fallback.

Validation rules:

- Stock-level tables must include a stock-name column.
- Missing names show a clear fallback such as `未知`.
- Missing names must not break row layout.

## BacktestProgressView

Represents progress shown while a backtest runs.

- `stage`: Chinese stage name.
- `message`: explanatory current action.
- `completed`: optional completed count.
- `total`: optional total count.
- `percent`: optional percent derived from known work.
- `current_item`: optional current stock, date, or table name.

Validation rules:

- Runs with more than one stock or more than one trading day must not show only a binary 0/1 state.
- At least three meaningful stages should be visible during non-trivial runs.
- Counts are shown when known; unknown totals still show stage and message.

## ReportDownloadEntry

Represents report availability after a backtest run.

- `state`: available, unavailable, or failed.
- `label`: Chinese label.
- `href`: optional download URL when available.
- `message`: explanatory text when unavailable or failed.

Validation rules:

- A report entry is visible after completed runs.
- Unavailable or failed report generation is shown explicitly.
- The entry does not imply a report exists when generation failed.
