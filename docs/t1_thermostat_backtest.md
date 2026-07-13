# T+1 恒温器完整回测

## 公共契约与边界

公共完整回测入口为：

```python
from stock_picker.strategies import (
    T1ThermostatBacktestRequest,
    backtest_thermostat_strategy,
)

result = backtest_thermostat_strategy(
    T1ThermostatBacktestRequest(
        service=service,
        symbols=("600519.SH",),
        start="2025-01-01",
        end="2026-06-30",
    )
)
```

入口返回 `T1ThermostatBacktestResult`，并委托唯一完整实现
`run_t1_thermostat_backtest`。新路径只消费每日 `trigger_plan`，不得把
`_deprecated_signal_rows`、`holding_advice`、`new_candidates`、
`grid_advice` 或 `trend_advice` 当作信号。旧事件驱动温控实现仅以
`legacy_backtest_thermostat_strategy` 显式保留；`event_backtest.py` 是通用兼容
边界，其他策略继续使用原执行及期末平仓语义。

## 精度声明

本实现是日线近似，不能解释为分钟级回测、精确盘中重放或真实可成交性证明。
Web 和 Excel 报告逐字展示：

- 回测精度：日线近似
- 分钟线：未使用
- 盘中触发时间：无法准确识别
- 同日多触发：使用保守顺序处理

`BacktestPrecision.MINUTE_5M` / `minute_5m` 仅为未来扩展保留，v1 传入后会被
拒绝；当前项目虽然有独立分钟行情抓取能力，但完整恒温器回测不会读取分钟数据。

## 数据时序、预热与公司行为

- 每个模拟交易日的计划只使用严格早于当日的前复权（qfq）日线，至少需要 252
  个前序交易日；不足时记录 `insufficient_data`，禁止凭不完整数据买入。
- 当日触达、保守成交和收盘估值只使用不复权（bfq）OHLCV。当前日 qfq 的开、高、
  低、收、量不能进入当日指标或计划。
- qfq 用于连续指标、bfq 用于现金成交，两流在公司行为附近可能不能可靠对应。
  检出的持续比例跳变记为 `unsupported_corporate_action`；结果保留质量和影响记录，
  但 v1 不进行逐笔复权映射、配股、分红或拆并股的精确持仓重述。因此受影响区间
  不应被当成精确收益证据。

## 日线保守执行规则

同日事件固定排序为：pending 卖出、风险控制卖出、趋势退出、趋势减仓、网格
卖出、趋势买入、网格买入。同一优先级再按风险等级、计划优先级、标准化股票代码
和持仓所有者排序；每次成交后立即重算现金与仓位约束。

成交基准价和公式：

- pending 卖出基准价 = 下一交易日开盘价，仅在开盘尝试一次；停牌、缺少开盘价
  或开盘等于跌停价时保留 pending，当日不在午后重试。
- 趋势买入/加仓基准价 = `max(触发价, 当日收盘价)`。
- 趋势减仓/退出基准价 = `min(触发价, 当日收盘价)`。
- 网格买卖基准价 = 对应网格层价格。
- 风控/下跌趋势卖出基准价 = 当日收盘价。
- 买入成交价 = `min(基准价 × (1 + slippage_pct), 涨停价)`。
- 卖出成交价 = `max(基准价 × (1 - slippage_pct), 跌停价)`。
- 买入费用 = `max(成交金额 × commission_rate, minimum_commission)`。
- 卖出费用 = `max(成交金额 × commission_rate, minimum_commission) + 成交金额 × stamp_tax_rate`。

一字涨停买入记失败，不虚构成交；跌停或停牌卖不出时保留失败/pending 生命周期。
当日买入股数不可当日卖出，触发卖出时转为对应所有者的 pending。默认
`force_final_liquidation=False`，最后一日保留未平仓、当日买入和 pending 并按收盘
估值；`force_final_liquidation=True` 在 v1 明确报错，不会绕过 T+1、停牌或涨跌停。

## 股票池偏差

手工股票池和自选股是整个历史区间不变的静态成员。市场范围池使用当前成分股快照
回放历史，存在幸存者偏差（survivor bias）；聚合龙虎榜范围不是逐日历史成员，
同时存在前视选择偏差（look-ahead selection bias）。这些元数据会写入结果和报告。
回测只描述“以该静态池回放”的结果，不代表当时可获得的无偏股票池表现。

## 缓存、运行与账户复用

Web 的“仅缓存并校验回测数据”只执行规范化、qfq/bfq 缓存补齐和质量检查，不调用
交易 runner；“开始回测”才使用同一请求契约执行每日模拟。`refresh` 控制是否强制
更新；未强制刷新时优先复用本地缓存并仅补缺口。缓存成功不等于已运行回测，运行
结果也不会写回账户持仓或交易流水。

账户路径沿用现有手工账户。佣金率、最低佣金、印花税率、滑点率和账户总仓位上限
从保存的账户设置解析；Web 不重复提供这些字段。只有勾选模拟资金时，本次
`initial_cash` 才覆盖账户现金，费用和仓位参数仍来自账户，且任何覆盖都不写回。

## 输出与报告

Web 运行结果与 Excel 导出使用同一个原始 `T1ThermostatBacktestResult`。报告目录为
`data/reports/`，默认文件名为
`t1_thermostat_backtest_YYYYMMDD_HHMMSS.xlsx`。固定 21 个工作表依次为：

`回测摘要`、`回测说明`、`参数与账户设置`、`数据来源与股票池`、`每日资产`、
`权益与回撤`、`每日持仓`、`每日触发计划`、`订单明细`、`成交明细`、`失败订单`、
`取消订单`、`pending明细`、`趋势批次`、`网格层级`、`个股表现`、`趋势策略表现`、
`网格策略表现`、`市场状态表现`、`数据质量`、`公司行为影响`。

失败、过期、取消、成交和 pending 记录均保留订单、计划、候选或 pending episode
可用的追踪 ID；未创建执行候选的过期计划允许 `candidate_trace_id` 留空，不伪造 ID。

## 验证命令

从仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_thermostat_strategy.py tests\test_thermostat_state.py tests\test_thermostat_execution.py tests\test_thermostat_trend_executor.py tests\test_thermostat_grid_executor.py tests\test_backtest_data.py tests\test_t1_thermostat_backtest.py tests\test_thermostat_metrics.py tests\test_t1_thermostat_web.py tests\test_t1_thermostat_backtest_report.py tests\test_web_app.py tests\test_thermostat_backtest.py tests\test_thermostat_event_backtest.py tests\test_event_backtest_engine.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m py_compile stock_picker\strategies\thermostat.py stock_picker\strategies\__init__.py stock_picker\strategies\thermostat_backtest.py stock_picker\strategies\thermostat_execution.py stock_picker\strategies\thermostat_state.py stock_picker\strategies\thermostat_trend_executor.py stock_picker\strategies\thermostat_grid_executor.py stock_picker\strategies\thermostat_metrics.py stock_picker\strategies\backtest_params.py stock_picker\data\backtest_data.py stock_picker\data\storage.py stock_picker\user\portfolio.py stock_picker\reporting\t1_thermostat_backtest_report.py examples\web_app.py
D:\Tools\PortableGit\cmd\git.exe diff --check
```
