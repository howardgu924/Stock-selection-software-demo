# Stock Selection Software Demo

This project is an early MVP for an A-share stock selection tool.

The first version focuses only on market data:

- Fetch A-share historical daily price data
- Fetch A-share realtime quote snapshots
- Fetch all A-share stock codes and names
- Normalize output fields
- Cache stock symbols and historical data in local SQLite
- Update only missing historical trading dates when local data is partial
- Continue batch history updates after failed stocks and record errors

## Data Sources

- Historical daily prices: BaoStock
- Realtime quotes: Sina quote API
- A-share stock code/name list: AkShare, with BaoStock fallback if the AkShare
  exchange endpoint is unavailable

## Setup

Run commands from the repository root:

```powershell
cd C:\Users\23601\Desktop\选股软件
```

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Fetch Historical Data

```powershell
.\.venv\Scripts\python.exe examples\fetch_history.py --symbol 600519 --start 20240101 --end 20240501
```

When cached data is partially present, the default workflow checks BaoStock
trading dates and fetches only missing date ranges.

Force a provider fetch and update the local cache:

```powershell
.\.venv\Scripts\python.exe examples\fetch_history.py --symbol 600519 --start 20240101 --end 20240501 --refresh
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

## List A-Share Symbols

```powershell
.\.venv\Scripts\python.exe examples\list_symbols.py --refresh
```

The symbol list is cached in SQLite after the first successful provider fetch.
If AkShare fails with a network or SSL error, the service automatically falls
back to BaoStock's stock list endpoint.

## Check Realtime Quotes

Realtime quotes require explicit stock codes.

```powershell
.\.venv\Scripts\python.exe examples\check_realtime.py --symbols "600519,000001"
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

Batch historical updates:

1. Accept explicit stock codes or the cached/refreshed all-A-share symbol list.
2. Update each stock independently.
3. Record failed stocks to the error CSV and continue by default.

Realtime data:

1. Require explicit stock codes through `--symbols`.
2. Fetch quotes from the Sina quote API.
3. Print the normalized result.

## Historical Data Fields

```text
symbol, date, open, high, low, close, volume, amount, amplitude, pct_chg, change, turnover
```

## Stock Symbol Fields

```text
symbol, code, name
```

## Realtime Quote Fields

```text
symbol, name, price, pct_chg, change, volume, amount, high, low, open, prev_close, turnover
```
