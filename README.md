# Stock Selection Software Demo

This project is an early MVP for an A-share stock selection tool.

The first version focuses only on market data:

- Fetch A-share historical daily price data
- Fetch A-share realtime quote snapshots
- Fetch all A-share stock codes and names
- Fetch A-share minute price data from Eastmoney through AkShare
- Fetch Eastmoney industry and concept board lists, members, and minute data
- Optionally fetch stock lists, daily prices, and minute prices through
  JoinQuant/JQData
- Select data sources by workflow and configure explicit fallback sources
- Add local MA5, MA10, MA30, and MACD columns to historical data
- Screen A-share stocks with configurable technical rules and sorting
- Normalize output fields
- Cache stock symbols and historical data in local SQLite
- Update only missing historical trading dates when local data is partial
- Continue batch history updates after failed stocks and record errors

## Data Sources

- Historical daily prices: BaoStock
- Realtime quotes: Sina quote API
- A-share stock code/name list: AkShare, with BaoStock fallback if the AkShare
  exchange endpoint is unavailable
- Minute prices and industry/concept boards: Eastmoney endpoints through AkShare
- Optional backup historical/minute source: JoinQuant SDK (`jqdatasdk`)

By default, the examples keep the existing provider workflow. Use `--source` to
select a specific provider for a workflow, and `--fallback` to explicitly allow
another provider if the selected source fails. Fallbacks are not silent: examples
print a warning with the failed source and the provider actually used.

## Setup

Run commands from the repository root:

```powershell
cd C:\Users\23601\Documents\选股软件
```

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

To use JoinQuant/JQData, install dependencies from `requirements.txt` and set
credentials in your shell before running examples:

```powershell
$env:JQDATA_USERNAME="your_joinquant_phone_or_id"
$env:JQDATA_PASSWORD="your_joinquant_password"
```

## Run Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Fetch Historical Data

```powershell
.\.venv\Scripts\python.exe examples\fetch_history.py --symbol 600519 --start 20240101 --end 20240501
```

Use BaoStock as the primary source and JQData only as an explicit backup:

```powershell
.\.venv\Scripts\python.exe examples\fetch_history.py --symbol 600519 --start 20240101 --end 20240501 --source baostock --fallback joinquant
```

Use JQData directly only when your account permission covers the target dates:

```powershell
.\.venv\Scripts\python.exe examples\fetch_history.py --symbol 600519 --start 20251201 --end 20260223 --source joinquant
```

When cached data is partially present, the default workflow checks BaoStock
trading dates and fetches only missing date ranges.

Force a provider fetch and update the local cache:

```powershell
.\.venv\Scripts\python.exe examples\fetch_history.py --symbol 600519 --start 20240101 --end 20240501 --refresh
```

Add local moving averages and MACD to single-stock historical output:

```powershell
.\.venv\Scripts\python.exe examples\fetch_history.py --symbol 600519 --start 20240101 --end 20240501 --indicators
```

Update several stocks and keep going if one fails:

```powershell
.\.venv\Scripts\python.exe examples\fetch_history.py --symbols "600519,000001" --start 20240101 --end 20240501
```

Update all A-share stocks:

```powershell
.\.venv\Scripts\python.exe examples\fetch_history.py --all --start 20240101 --end 20240501 --refresh-symbols
```

Test a small batch before running the full market:

```powershell
.\.venv\Scripts\python.exe examples\fetch_history.py --all --start 20240430 --end 20240430 --limit 5 --progress-every 1
```

Failed stocks are appended to `data/history_errors.csv` by default. Use
`--error-log <path>` to choose another CSV, or `--stop-on-error` to stop at the
first failure.

## Screen Stocks

The default stock screen now matches this workflow:

- all A-share stocks with `--all`
- recent 20-day uptrend
- close above MA30
- expanded volume
- MACD golden cross
- exclude ST stocks
- sort by score, percentage change, amount, or volume

List available rules:

```powershell
.\.venv\Scripts\python.exe examples\screen_stocks.py --list-rules
```

Test a small batch first:

```powershell
.\.venv\Scripts\python.exe examples\screen_stocks.py --all --start 20250527 --end 20260527 --limit 100 --top 20 --sort-by pct_chg
```

Run the full market and export matches:

```powershell
.\.venv\Scripts\python.exe examples\screen_stocks.py --all --start 20250527 --end 20260527 --sort-by pct_chg --top 50 --output data\screen_uptrend_ma30_volume_macd.csv
```

Sort by trading amount instead:

```powershell
.\.venv\Scripts\python.exe examples\screen_stocks.py --all --start 20250527 --end 20260527 --sort-by amount --top 50 --output data\screen_by_amount.csv
```

Find stocks whose close reached a one-year high in the latest three sessions
and whose volume stayed elevated for those three sessions:

```powershell
.\.venv\Scripts\python.exe examples\screen_stocks.py --all --start 20250527 --end 20260527 --rules "close_3d_252d_high,volume_up_3d,exclude_st" --sort-by pct_chg --top 50 --output data\screen_3d_year_high_volume.csv
```

Use the latest completed trading day for `--end` if the current day's daily bar is not available yet. Keep JQData optional because account permissions may not cover the requested date range.

## List A-Share Symbols

```powershell
.\.venv\Scripts\python.exe examples\list_symbols.py --refresh
```

Choose the stock-list source:

```powershell
.\.venv\Scripts\python.exe examples\list_symbols.py --source joinquant --refresh
```

The symbol list is cached in SQLite after the first successful provider fetch.
If AkShare fails with a network or SSL error, the service automatically falls
back to BaoStock's stock list endpoint.

## Check Realtime Quotes

Realtime quotes require explicit stock codes.

```powershell
.\.venv\Scripts\python.exe examples\check_realtime.py --symbols "600519,000001"
```

## Fetch Minute Data

```powershell
.\.venv\Scripts\python.exe examples\fetch_minute.py --symbol 600519 --start "2024-04-30 09:30:00" --end "2024-04-30 15:00:00" --period 5
```

Supported minute periods are `1`, `5`, `15`, `30`, and `60`.

Choose the minute source:

```powershell
.\.venv\Scripts\python.exe examples\fetch_minute.py --symbol 600519 --start "2024-04-30 09:30:00" --end "2024-04-30 15:00:00" --period 5 --source joinquant --fallback akshare
```

## Fetch Industry and Concept Boards

List industry boards:

```powershell
.\.venv\Scripts\python.exe examples\list_boards.py --type industry
```

List concept boards:

```powershell
.\.venv\Scripts\python.exe examples\list_boards.py --type concept
```

Fetch board members by board name or Eastmoney `BK` code:

```powershell
.\.venv\Scripts\python.exe examples\list_boards.py --type industry --members BK1036
```

Fetch board minute data:

```powershell
.\.venv\Scripts\python.exe examples\list_boards.py --type concept --minutes BK0655 --period 5
```

## Error Details

Examples print a short error message by default. Add `--debug` to show the full Python traceback:

```powershell
.\.venv\Scripts\python.exe examples\fetch_history.py --symbol 600519 --start 20240101 --end 20240501 --debug
.\.venv\Scripts\python.exe examples\check_realtime.py --symbols "600519,000001" --debug
```

## Local SQLite Cache

Historical data and stock symbols are cached in:

```text
data/market_data.sqlite3
```

The `historical_prices` table uses `(symbol, date)` as the primary key, so the same stock and date cannot be inserted twice. The `stock_symbols` table stores normalized symbol, six-digit code, name, and update timestamp. The cache is intended for the default historical workflow: daily BaoStock data with the default `qfq` adjustment.

Realtime quote data is not cached.

## Current Workflow

Historical data:

1. Normalize the stock code.
2. If `--refresh` is not used, query the local SQLite cache first.
3. If no cached rows exist, fetch the requested range from BaoStock.
4. If cached rows exist, query BaoStock trading dates and fetch only missing date ranges.
5. Save fetched rows into SQLite and return the full cached range.
6. If `--indicators` is used, calculate MA and MACD locally from returned
   historical rows.

Batch historical updates:

1. Accept explicit stock codes or the cached/refreshed all-A-share symbol list.
2. Update each stock independently.
3. Record failed stocks to the error CSV and continue by default.

Realtime data:

1. Require explicit stock codes through `--symbols`.
2. Fetch quotes from the Sina quote API.
3. Print the normalized result.

Minute and board data:

1. Use BaoStock for historical 5/15/30/60 minute stock bars.
2. Use Eastmoney endpoints through AkShare/custom small-page requests for
   boards and board members.
3. Normalize Chinese source columns to stable English field names.
4. Print the normalized result. These data are not cached yet.

## Historical Data Fields

```text
symbol, date, open, high, low, close, volume, amount, amplitude, pct_chg, change, turnover
```

With `--indicators`, the output also includes:

```text
ma5, ma10, ma30, macd_dif, macd_dea, macd
```

## Minute Data Fields

```text
symbol, datetime, open, high, low, close, volume, amount, average_price, price, amplitude, pct_chg, change, turnover
```

## Board Fields

```text
board_type, rank, name, code, price, change, pct_chg, market_cap, turnover, up_count, down_count, leader, leader_pct_chg
```

## Board Member Fields

```text
board_type, board, symbol, rank, code, name, price, pct_chg, change, volume, amount, amplitude, high, low, open, prev_close, turnover, pe_dynamic, pb
```

## Stock Symbol Fields

```text
symbol, code, name
```

## Realtime Quote Fields

```text
symbol, name, price, pct_chg, change, volume, amount, high, low, open, prev_close, turnover
```
