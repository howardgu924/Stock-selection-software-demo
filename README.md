# A 股行情数据 MVP

第一版只覆盖数据能力：

- 获取 A 股历史日线数据
- 获取 A 股实时行情快照
- 统一字段格式
- 将历史数据缓存到本地 SQLite

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 验证历史数据

```powershell
python examples\fetch_history.py --symbol 600519 --start 20240101 --end 20240501
```

## 验证实时行情

```powershell
python examples\check_realtime.py --symbols 600519,000001
```

## 数据字段

历史数据统一字段：

```text
symbol, date, open, high, low, close, volume, amount, amplitude, pct_chg, change, turnover
```

实时行情统一字段：

```text
symbol, name, price, pct_chg, change, volume, amount, high, low, open, prev_close, turnover
```
