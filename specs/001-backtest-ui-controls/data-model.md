# Data Model: Backtest UI Controls

## Backtest Stock Pool Selection

Represents the active stock universe selection for the 回测诊断 page.

### Fields

- `stock_pool_source`: One of the supported source keys, at minimum `manual` and `watchlist`.
- `symbols`: Manual symbols, active only when `stock_pool_source = manual`.
- `watchlist_name`: Existing watchlist name, active only when `stock_pool_source = watchlist`.
- `market_range`: Selected market range values, active only when `stock_pool_source = market_range`.
- `candidate_source_options`: Optional candidate source settings such as 龙虎榜 range, active only if the source is supported for backtest.
- `resolved_symbols`: Concrete symbols submitted to the existing backtest engine.
- `resolved_count`: Count of valid resolved symbols.
- `source_summary`: User-visible source name/detail for summaries and validation messages.

### Validation Rules

- The active source determines which fields are read.
- Stale fields from inactive sources must not affect submitted symbols.
- Empty or invalid resolved pools must block backtest execution with a clear message.
- Watchlist mode must choose from existing watchlist names and must not require manual watchlist name typing.

## Backtest Date Range Selection

Represents the active time window for the 回测诊断 page.

### Fields

- `backtest_date_range`: Preset key: `1m`, `3m`, `5m`, `half_year`, `1y`, or `custom`.
- `custom_start`: Editable start date, active only in `custom` mode.
- `custom_end`: Editable end date, active only in `custom` mode.
- `resolved_start`: Start date sent to the existing backtest function.
- `resolved_end`: End date sent to the existing backtest function.
- `range_summary`: User-visible description of the active range.

### Validation Rules

- Non-custom presets calculate and display resolved start/end dates.
- Custom mode must show editable start/end fields.
- Stale custom dates must not override preset ranges.
- Invalid ranges must block submit with a user-friendly message.

## Backtest Parameter Summary

Represents the confirmation shown before or after running the backtest.

### Fields

- `stock_pool_source_label`
- `stock_pool_detail`
- `resolved_count`
- `date_range_label`
- `resolved_start`
- `resolved_end`
- `cash`
- `data_source_settings`

### Validation Rules

- Summary must reflect the active submitted values.
- Summary must not show stale manual symbols or stale custom dates as active after the user switches modes.
