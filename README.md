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

## Run Strategy Lists

Strategies under `strategy/` are implemented as selection or rotation lists,
not as automatic orders or full portfolio backtests. Output columns are:

```text
strategy, symbol, code, name, date, action, score, rank, weight, reason
```

Run the turtle breakout strategy for one stock:

```powershell
.\.venv\Scripts\python.exe examples\run_strategy.py --strategy turtle --symbol 600519 --start 20250527 --end 20260527
```

Run the small-cap strategy from historical valuation data for a test universe:

```powershell
.\.venv\Scripts\python.exe examples\run_strategy.py --strategy small_cap --symbols "600519,000001" --as-of 20260527 --top 3 --output data\strategy_small_cap.csv
```

Available strategies are `ma_cross`, `turtle`, `small_cap`, `undervalued`, and
`bank_rotation`. The `Dual_Thrust` futures strategy is intentionally not wired
into this first stock-selection workflow.

`small_cap` and `undervalued` need an explicit stock universe through
`--symbol`, `--symbols`, or `--all`; they use historical valuation/financial
data rather than realtime quotes. Start with a small `--symbols` list or
`--all --limit` before scanning the full market.

Run bank rotation against an explicit bank universe:

```powershell
.\.venv\Scripts\python.exe examples\run_strategy.py --strategy bank_rotation --symbols "600036,601288,601166,601398,601939,600000,601988,000001,601328,601229,002966" --as-of 20260527 --output data\strategy_bank_rotation.csv
```

## Backtest Strategies

Backtesting currently supports history-price strategies: `ma_cross` and
`turtle`, plus `bank_rotation` for an explicit bank universe. It uses daily
historical bars and historical valuation data, not realtime quotes.

```powershell
.\.venv\Scripts\python.exe examples\backtest_strategy.py --strategy turtle --symbol 600519 --start 20250527 --end 20260527 --cash 100000 --output data\backtest_turtle_summary.csv --equity-output data\backtest_turtle_equity.csv --trades-output data\backtest_turtle_trades.csv
```

If cached historical rows exist locally and the online trade calendar is
temporarily unavailable, the backtest uses the cached rows instead of failing.

Run bank rotation backtests:

```powershell
.\.venv\Scripts\python.exe examples\backtest_strategy.py --strategy bank_rotation --symbols "600036,601288,601166,601398,601939,600000,601988,000001,601328,601229,002966" --start 20260215 --end 20260527 --cash 100000 --output data\bank_rotation_20260215_20260527_summary.csv --equity-output data\bank_rotation_20260215_20260527_equity.csv --trades-output data\bank_rotation_20260215_20260527_trades.csv

.\.venv\Scripts\python.exe examples\backtest_strategy.py --strategy bank_rotation --symbols "600036,601288,601166,601398,601939,600000,601988,000001,601328,601229,002966" --start 20250613 --end 20250926 --cash 100000 --output data\bank_rotation_20250613_20250926_summary.csv --equity-output data\bank_rotation_20250613_20250926_equity.csv --trades-output data\bank_rotation_20250613_20250926_trades.csv
```

Run turtle as a portfolio strategy over a stock universe. The backtest checks
each stock daily, sells positions that break the 10-day exit low, then buys the
strongest 20-day breakouts up to `--max-positions`.

```powershell
.\.venv\Scripts\python.exe examples\backtest_strategy.py --strategy turtle --symbols "600519,000001,600036" --start 20250613 --end 20250926 --cash 100000 --max-positions 3 --output data\turtle_portfolio_summary.csv --equity-output data\turtle_portfolio_equity.csv --trades-output data\turtle_portfolio_trades.csv
```

The default execution timing is conservative for daily-bar strategies:

```text
--execution-timing next_open
```

This means a signal is generated after the current daily close and executed at
the next trading day's open. For intraday turtle experiments, use midday
signals and afternoon open execution:

```powershell
.\.venv\Scripts\python.exe examples\backtest_strategy.py --strategy turtle --symbols "600519,000001,600036" --start 20260520 --end 20260527 --cash 100000 --max-positions 3 --execution-timing same_day_pm_open --minute-source akshare --warmup-days 60 --output data\turtle_midday_summary.csv --equity-output data\turtle_midday_equity.csv --trades-output data\turtle_midday_trades.csv
```

`same_day_pm_open` uses morning minute bars through 11:30 to synthesize the
signal bar, then executes at the first available afternoon minute-bar open.
`--warmup-days` reads earlier daily bars only for channel/indicator state; the
equity curve and performance summary still start at `--start`.

## Full Turtle System

`examples/run_strategy.py --strategy turtle` remains a lightweight 20-day
breakout signal list. For the complete Turtle Trading state machine, use
`examples/run_turtle_system.py` and `examples/backtest_turtle_system.py`.

The full turtle system supports S1 `20/10` and S2 `55/20`, ATR/N based unit
sizing, A-share 100-share lots, `0.5N` pyramiding up to four units, `2N` hard
stops, channel exits, and the S1 profitable-exit skip rule. It is long-only
and designed for manual A-share execution assistance.

Run current/as-of signals and an execution plan:

```powershell
.\.venv\Scripts\python.exe examples\run_turtle_system.py --symbols "600519,000001,600036" --cash 5000 --as-of 20260528 --signals-output data\turtle_system_signals.csv --plan-output data\turtle_system_plan.csv
```

Run a state-machine backtest:

```powershell
.\.venv\Scripts\python.exe examples\backtest_turtle_system.py --symbols "600519,000001,600036" --start 20260228 --end 20260527 --cash 100000 --output data\turtle_system_summary.csv --equity-output data\turtle_system_equity.csv --trades-output data\turtle_system_trades.csv --positions-output data\turtle_system_positions.csv --drawdowns-output data\turtle_system_drawdowns.csv --symbol-pnl-output data\turtle_system_symbol_pnl.csv
```

Run a simple robustness sweep:

```powershell
.\.venv\Scripts\python.exe examples\backtest_turtle_system.py --symbols "600519,000001,600036" --start 20260228 --end 20260527 --cash 100000 --sweep-risk-pct "0.005,0.01,0.02" --sweep-slippage-rate "0,0.002" --sweep-s1-entry "20,25" --sweep-output data\turtle_system_sweep.csv
```

## Plan Manual Execution

Strategy signals are not always executable. For example, a stock can break out
and close at its limit-up price, but a manual trader may not be able to buy it.
Use the execution planner to combine strategy output with realtime quotes:

```powershell
.\.venv\Scripts\python.exe examples\plan_execution.py --signals data\strategy_turtle.csv --cash 5000 --next-day-premium 0.02 --output data\execution_plan.csv
```

The planner identifies limit-up buy signals and returns multiple options:

- `buy_now`: executable under the current quote, cash, volume limit, and lot
  rules.
- `queue_limit_up`: buy signal exists but the stock is at limit-up; queue only
  if you accept uncertain fill.
- `buy_next_day_below_limit`: fallback plan for a limit-up signal; buy next day
  only below the generated `next_day_max_price`.
- `switch_alternative`: fallback to the best executable alternative signal.
- `skip_insufficient_cash`: signal exists but cash is not enough for one lot.
- `skip_volume_limit`: suggested shares exceed the configured quote-volume
  participation cap.

Limit-up rules are approximate and board-aware: main board `10%`, STAR/ChiNext
`20%`, Beijing-style codes `30%`, and ST names `5%`.

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
.\.venv\Scripts\python.exe examples\portfolio_journal.py buy --symbol 600172 --name "Huanghe Xuanfeng" --price 14.60 --shares 300 --target-sell-price 16.00 --strategy turtle_system --system S1 --entry-reason "20-day breakout" --signal-date 2026-05-28 --execution-date 2026-05-28
```

Record a buy from an execution plan while still confirming actual price and
shares manually:

```powershell
.\.venv\Scripts\python.exe examples\portfolio_journal.py buy --from-plan data\turtle_system_plan.csv --symbol 600172 --price 14.60 --shares 300
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
