# Stock Selection Software Demo

This project is an early MVP for an A-share stock selection tool.

The first version focuses only on market data:

- Fetch A-share historical daily price data
- Fetch A-share realtime quote snapshots
- Normalize output fields
- Cache historical data in local SQLite

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Fetch Historical Data

```powershell
python examples\fetch_history.py --symbol 600519 --start 20240101 --end 20240501
```

## Check Realtime Quotes

```powershell
python examples\check_realtime.py --symbols 600519,000001
```

## Historical Data Fields

```text
symbol, date, open, high, low, close, volume, amount, amplitude, pct_chg, change, turnover
```

## Realtime Quote Fields

```text
symbol, name, price, pct_chg, change, volume, amount, high, low, open, prev_close, turnover
```
