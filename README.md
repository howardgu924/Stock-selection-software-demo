# Stock Selection Software Demo

This project is an early MVP for an A-share stock selection tool.

The normal workflow now uses the 恒温器策略: evaluate the current market
regime first, then route each stock to trend following, grid/range trading,
risk control, or observation. Account management, market-data fetching,
fallback sources, local cache, realtime quotes, execution assistance, and the
local web app remain part of the supported workflow.

## 正常使用路径

Start the local web app:

```powershell
.\.venv\Scripts\python.exe examples\web_app.py --host 127.0.0.1 --port 8765
```

Open:

```text
http://127.0.0.1:8765
```

Use the 恒温器策略 page to choose a 股票池 and strategy date range. 可用现金从账户读取
and is displayed as read-only on the strategy page. If the account is not
initialized, go to the 账户 page first. 模拟资金只影响临时策略测算 and never changes
account cash, positions, or trades. The page shows market regime overview,
holding advice, new candidates, grid advice, trend advice, execution checks,
and the account page. The program does not place orders automatically; manual
portfolio records are still confirmed by the user.

股票池 can come from manual input, a saved 自选股组合, a market range, or a
Longhu Bang source. Manual input can be saved as a named 自选股组合 from the web
page and reused later. 账户页统一管理自选组合. 自选股组合不是持仓: adding, deleting, renaming, or removing
symbols from a watchlist does not change account cash, positions, trades, or
P&L. Holding advice still comes from the account positions; watchlists only
control the new-candidate input range.

The run form keeps the 剔除科创板 option. 剔除科创板只影响本次运行 and never deletes
saved watchlist members. Results show the stock-pool source, original count,
deduped count, filtered count, removed count, warnings, and errors before the
恒温器策略 consumes the final symbol list. If 同花顺龙虎榜不可用, the result explains
the unavailable reason and shows the real data source actually used instead of
pretending another source is 同花顺.

The maintained base capabilities are:

- Fetch A-share historical daily price data
- Fetch A-share realtime quote snapshots
- Fetch all A-share stock codes and names
- Fetch A-share minute price data from Eastmoney through AkShare
- Fetch Eastmoney industry and concept board lists, members, and minute data
- Optionally fetch stock lists, daily prices, and minute prices through
  JoinQuant/JQData
- Select data sources by workflow and configure explicit fallback sources
- Add local MA5, MA10, MA30, and MACD columns to historical data
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

## 历史兼容

The old CLI strategy commands, old screening engine, and Turtle strategy modules
have been removed. The legacy text files that previously lived under
`strategy/` were already removed from the normal project surface.

## Removed Legacy Strategies

The old stock-picking paths have been removed from the supported codebase:
ordinary technical screening, simple moving-average crossover, lightweight
Turtle breakout, small-cap selection, undervalued value selection, bank
rotation, and the complete Turtle state machine.

Use the local web app's thermostat strategy and thermostat backtest workflow for
normal stock-pool evaluation. Market data fetching, realtime quotes, account
management, watchlists, Longhu Bang pools, and report downloads remain
supported.

## Manual Portfolio Journal

Use the journal when trades are executed manually rather than by the program.
It records principal, cash, positions, average cost, target sell price,
strategy/system metadata, signal/execution dates, realized P&L, win rate,
profit/loss ratio, and average holding days.

Initialize an account:

```powershell
.\.venv\Scripts\python.exe examples\portfolio_journal.py init --principal 5000
```

Record a buy:

```powershell
.\.venv\Scripts\python.exe examples\portfolio_journal.py buy --symbol 600172 --name "Huanghe Xuanfeng" --price 14.60 --shares 300 --target-sell-price 16.00 --strategy thermostat --system trend_following --entry-reason "thermostat signal" --signal-date 2026-05-28 --execution-date 2026-05-28
```

Record a buy from an execution plan while still confirming actual price and
shares manually:

```powershell
.\.venv\Scripts\python.exe examples\portfolio_journal.py buy --symbol 600172 --price 14.60 --shares 300 --strategy thermostat
```

Record a sell:

```powershell
.\.venv\Scripts\python.exe examples\portfolio_journal.py sell --symbol 600172 --price 15.20 --shares 300 --exit-reason "channel exit" --execution-date 2026-06-10
```

Print account status, optionally marking open positions to market:

```powershell
.\.venv\Scripts\python.exe examples\portfolio_journal.py summary --mark 600172=15.20
.\.venv\Scripts\python.exe examples\portfolio_journal.py positions
.\.venv\Scripts\python.exe examples\portfolio_journal.py trades
```

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

## Local Web App

Run the local browser interface without installing any extra web framework:

```powershell
.\.venv\Scripts\python.exe examples\web_app.py --host 127.0.0.1 --port 8765
```

Then open:

```text
http://127.0.0.1:8765
```

The web app wraps the existing Python modules. It can run the thermostat
strategy, run thermostat backtests, manage watchlists, and record manual
portfolio buys/sells. It is local-only; account files still live under
`data/user/default` unless you choose another path in the page.

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
