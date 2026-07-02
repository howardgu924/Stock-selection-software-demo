# 任务：恒温器事件驱动回测系统

> 实施时必须逐项勾选。每个任务都应先补测试或验证点，再做最小实现，再运行指定测试。不要在一个任务里同时修改多个无关模块。

## 任务分组

- A. 基线与回归保护
- B. 数据缓存和涨跌停状态
- C. 事件驱动回测核心
- D. 恒温器信号接入
- E. 参数来源和账户兼容
- F. 报告输出
- G. Web 回测入口
- H. 端到端验证

---

## A. 基线与回归保护

### A1. 记录当前回测相关测试基线

影响文件：

- 不改业务代码。
- 可按需要补充 `tests/test_thermostat_backtest.py` 中的旧行为保护测试。

步骤：

- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_backtest.py -q`，记录当前失败或通过情况。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py -q`，记录当前失败或通过情况。
- [ ] 若当前测试已有失败，先记录失败测试名和错误摘要，不在本任务修复。

验证：

- 能明确知道本次改动前回测和 Web 测试的基线状态。

### A2. 锁定旧简化回测的 legacy 标识测试

影响文件：

- `tests/test_thermostat_backtest.py`
- 后续可能涉及 `stock_picker/strategies/thermostat.py`

步骤：

- [ ] 新增测试：旧简化回测如果仍可调用，结果必须包含或暴露 `simplified_backtest` 标识。
- [ ] 新增测试：正式回测默认路径不能把旧简化结果当作正式事件驱动结果。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_backtest.py -q`，确认新测试先失败或暴露当前缺口。

验证：

- 测试能区分“正式事件驱动回测”和“旧简化回测”。

---

## B. 数据缓存和涨跌停状态

### B1. 增加事件价格缓存 schema 测试

影响文件：

- `tests/test_data_service.py` 或新增 `tests/test_event_price_cache.py`
- 后续涉及 `stock_picker/data/storage.py`

步骤：

- [ ] 新增测试：事件价格缓存 key 必须区分 `symbol`、`date`、`time_point`、`frequency`、`adjust_type`、`source`。
- [ ] 新增测试：同一股票同一天的 `daily`、`morning_open`、`noon`、`afternoon_open`、`close` 记录可以并存。
- [ ] 新增测试：旧日线缓存读写仍保持可用。
- [ ] 运行对应测试，确认新增测试失败且旧日线缓存测试不受影响。

验证：

- 事件缓存需求被测试覆盖。
- 旧缓存兼容性被测试保护。

### B2. 实现事件价格缓存读写

影响文件：

- `stock_picker/data/storage.py`
- `tests/test_event_price_cache.py` 或 `tests/test_data_service.py`

步骤：

- [ ] 在缓存层新增事件价格记录的读写能力。
- [ ] 保持现有 `historical_prices` 表和读取行为不破坏。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_event_price_cache.py tests/test_data_service.py -q`。

验证：

- 事件缓存读写测试通过。
- 旧日线缓存测试通过。

### B3. 增加涨跌停状态枚举和估算测试

影响文件：

- 新增 `tests/test_limit_status.py`
- 后续新增或修改 `stock_picker/data/limits.py`

步骤：

- [ ] 新增测试：普通 A 股按 10% 规则估算涨停价和跌停价。
- [ ] 新增测试：创业板/科创板按 20% 规则估算。
- [ ] 新增测试：ST 股票按 5% 规则估算。
- [ ] 新增测试：缺少 `prev_close` 或板块/ST 判断信息时返回 `limit_status_unknown`，不能返回可成交。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_limit_status.py -q`，确认测试先失败。

验证：

- 涨跌停估算和未知状态的保守规则被测试锁定。

### B4. 实现涨跌停状态判断和数据质量说明

影响文件：

- `stock_picker/data/limits.py`
- `tests/test_limit_status.py`

步骤：

- [ ] 实现 `normal`、`limit_up`、`limit_down`、`suspended`、`limit_status_unknown` 的统一判断。
- [ ] 无法判断时返回可报告的数据质量说明。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_limit_status.py -q`。

验证：

- 所有涨跌停和未知状态测试通过。

### B5. 增加缓存完整性校验测试

影响文件：

- `tests/test_event_price_cache.py`
- 后续涉及 `stock_picker/data/service.py`

步骤：

- [ ] 新增测试：正式回测所需字段缺失时，缓存校验返回缺口列表。
- [ ] 新增测试：缺少 `prev_close`、`limit_up_price`、`limit_down_price`、执行时间点状态时，返回 Data Quality warning。
- [ ] 新增测试：缓存不完整时正式回测不得进入逐日循环。
- [ ] 运行对应测试，确认新增测试先失败。

验证：

- 回测前缓存校验的阻断规则可测试。

### B6. 实现正式回测缓存校验

影响文件：

- `stock_picker/data/service.py`
- `stock_picker/data/storage.py`
- `tests/test_event_price_cache.py`

步骤：

- [ ] 增加正式回测缓存校验接口。
- [ ] 缓存缺失时返回缺失股票、日期、时间点、字段和 warning。
- [ ] 缓存完整时返回可用于回测的本地数据摘要。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_event_price_cache.py tests/test_data_service.py -q`。

验证：

- 缓存完整性校验通过测试。
- 不破坏现有数据服务测试。

---

## C. 事件驱动回测核心

### C1. 增加事件顺序测试

影响文件：

- 新增 `tests/test_event_backtest_engine.py`
- 后续新增 `stock_picker/strategies/event_backtest.py`

步骤：

- [ ] 新增测试：单个交易日事件顺序固定为 `morning_open`、`noon`、`afternoon_open`、`close`。
- [ ] 新增测试：`noon` 只生成信号，不改变现金和持仓。
- [ ] 新增测试：买入只能在 `afternoon_open` 成交。
- [ ] 新增测试：卖出只能在 `morning_open`、`afternoon_open`、`close` 成交。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_event_backtest_engine.py -q`，确认测试先失败。

验证：

- 回测时间顺序和成交时间限制被测试锁定。

### C2. 实现最小事件循环

影响文件：

- `stock_picker/strategies/event_backtest.py`
- `tests/test_event_backtest_engine.py`

步骤：

- [ ] 新增最小事件循环结构。
- [ ] 支持交易日列表和固定事件点。
- [ ] 支持信号计划和执行状态分离。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_event_backtest_engine.py -q`。

验证：

- 事件顺序测试通过。

### C3. 增加订单失败测试

影响文件：

- `tests/test_event_backtest_engine.py`
- 后续涉及 `stock_picker/strategies/event_backtest.py`

步骤：

- [ ] 新增测试：涨停时买入失败，状态为 `failed_limit_up`。
- [ ] 新增测试：跌停时卖出失败，状态为 `failed_limit_down`。
- [ ] 新增测试：停牌时买卖失败，状态为 `failed_suspended`。
- [ ] 新增测试：涨跌停状态未知时订单不可成交，状态或原因包含 `limit_status_unknown`。
- [ ] 新增测试：失败订单必须进入交易记录。
- [ ] 运行对应测试，确认新增测试先失败。

验证：

- 不可成交场景不会被静默忽略。

### C4. 实现订单执行约束

影响文件：

- `stock_picker/strategies/event_backtest.py`
- `tests/test_event_backtest_engine.py`

步骤：

- [ ] 实现涨停、跌停、停牌、未知状态的订单阻断。
- [ ] 实现失败订单记录，不改变现金和持仓。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_event_backtest_engine.py -q`。

验证：

- 所有订单失败测试通过。

### C5. 增加现金、费用和交易单位测试

影响文件：

- `tests/test_event_backtest_engine.py`

步骤：

- [ ] 新增测试：现金不足时买入失败并记录 `insufficient_cash`。
- [ ] 新增测试：买入不足一手时失败并记录原因。
- [ ] 新增测试：成功买入扣除成交金额、佣金、滑点。
- [ ] 新增测试：成功卖出扣除佣金、印花税、滑点并更新现金。
- [ ] 运行对应测试，确认新增测试先失败。

验证：

- 现金、费用、交易单位规则被测试覆盖。

### C6. 实现现金、费用和交易单位处理

影响文件：

- `stock_picker/strategies/event_backtest.py`
- `tests/test_event_backtest_engine.py`

步骤：

- [ ] 实现买入最小单位检查。
- [ ] 实现现金充足性检查。
- [ ] 实现佣金、最低佣金、印花税、滑点扣减。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_event_backtest_engine.py -q`。

验证：

- 现金、费用、交易单位测试通过。

### C7. 增加 T+1 和最终清仓测试

影响文件：

- `tests/test_event_backtest_engine.py`

步骤：

- [ ] 新增测试：启用 T+1 时，当日买入股票当日不可卖出。
- [ ] 新增测试：最后一个交易日按 `close` 尝试清仓。
- [ ] 新增测试：最后一天跌停或停牌导致无法清仓时，最终报告保留未清仓持仓和原因。
- [ ] 运行对应测试，确认新增测试先失败。

验证：

- T+1 和最终清仓规则被测试覆盖。

### C8. 实现 T+1、可用股数和最终清仓

影响文件：

- `stock_picker/strategies/event_backtest.py`
- `tests/test_event_backtest_engine.py`

步骤：

- [ ] 实现持仓总股数和可用股数区分。
- [ ] 实现 T+1 可卖约束。
- [ ] 实现最后交易日收盘清仓尝试。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_event_backtest_engine.py -q`。

验证：

- T+1、可用股数和最终清仓测试通过。

---

## D. 恒温器信号接入

### D1. 增加恒温器信号适配测试

影响文件：

- `tests/test_thermostat_event_backtest.py`
- 后续涉及 `stock_picker/strategies/thermostat_backtest.py`

步骤：

- [ ] 新增测试：正式回测调用现有恒温器信号生成能力，而不是旧等权净值逻辑。
- [ ] 新增测试：`market_downtrend` 下非持仓股票不会生成可执行买入。
- [ ] 新增测试：`noon` 生成的买入计划只能在 `afternoon_open` 执行。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_event_backtest.py -q`，确认测试先失败。

验证：

- 恒温器信号和事件执行层的边界被测试覆盖。

### D2. 新增正式恒温器事件回测入口

影响文件：

- `stock_picker/strategies/thermostat_backtest.py`
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_event_backtest.py`
- `tests/test_thermostat_backtest.py`

步骤：

- [ ] 增加正式事件驱动回测入口。
- [ ] 保留旧简化回测路径，并明确标记为 `simplified_backtest`。
- [ ] 让现有公开入口在正式回测场景走事件驱动路径。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_event_backtest.py tests/test_thermostat_backtest.py -q`。

验证：

- 正式回测和旧简化回测可区分。
- 旧测试不被无关破坏。

### D3. 增加网格和趋势策略分布测试

影响文件：

- `tests/test_thermostat_event_backtest.py`

步骤：

- [ ] 新增测试：趋势信号能进入交易计划和交易记录。
- [ ] 新增测试：网格候选不会绕过事件成交限制。
- [ ] 新增测试：失败网格或趋势订单仍进入 Trades。
- [ ] 运行对应测试，确认新增测试先失败。

验证：

- 恒温器策略族信息能进入正式回测结果。

### D4. 实现策略族结果写入

影响文件：

- `stock_picker/strategies/thermostat_backtest.py`
- `stock_picker/strategies/event_backtest.py`
- `tests/test_thermostat_event_backtest.py`

步骤：

- [ ] 将趋势、网格、风控、观察等策略族写入评估明细。
- [ ] 将实际成交和失败原因写入交易明细。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_event_backtest.py -q`。

验证：

- 策略族相关测试通过。

---

## E. 参数来源和账户兼容

### E1. 增加参数优先级测试

影响文件：

- 新增 `tests/test_backtest_params.py`
- 后续新增或修改 `stock_picker/strategies/backtest_params.py`

步骤：

- [ ] 新增测试：用户本次覆盖优先于账户设置。
- [ ] 新增测试：账户设置优先于策略默认。
- [ ] 新增测试：缺少账户字段时使用系统默认并记录来源。
- [ ] 新增测试：本次覆盖不会写回账户文件。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_backtest_params.py -q`，确认测试先失败。

验证：

- 参数来源优先级和不写回账户规则被测试覆盖。

### E2. 实现回测参数解析

影响文件：

- `stock_picker/strategies/backtest_params.py`
- `stock_picker/user/portfolio.py`，仅在确需读取兼容字段时修改
- `tests/test_backtest_params.py`

步骤：

- [ ] 实现回测参数解析和来源记录。
- [ ] 保持账户文件格式兼容。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_backtest_params.py tests/test_portfolio_journal.py -q`。

验证：

- 参数来源测试通过。
- 账户相关旧测试通过。

### E3. 将参数来源接入正式回测

影响文件：

- `stock_picker/strategies/thermostat_backtest.py`
- `stock_picker/strategies/event_backtest.py`
- `tests/test_thermostat_event_backtest.py`
- `tests/test_backtest_params.py`

步骤：

- [ ] 正式回测结果包含参数值和来源。
- [ ] 账户缺省值、系统默认值和用户覆盖值能进入结果。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_backtest_params.py tests/test_thermostat_event_backtest.py -q`。

验证：

- 回测结果可解释本次使用了哪些参数以及来源。

---

## F. 报告输出

### F1. 增加报告结构测试

影响文件：

- 新增 `tests/test_backtest_report.py`
- 后续新增 `stock_picker/reporting/backtest_report.py`

步骤：

- [ ] 新增测试：报告对象能生成 Summary、Daily Portfolio、Daily Evaluation Detail、Trades、Positions、Symbol Performance、Data Quality、Parameters。
- [ ] 新增测试：Trades 包含失败交易。
- [ ] 新增测试：Data Quality 包含涨跌停未知、模拟价格、缺字段 warning。
- [ ] 新增测试：用户可见标题不出现“未翻译字段”和明显乱码。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_backtest_report.py -q`，确认测试先失败。

验证：

- 报告结构和字段可读性被测试覆盖。

### F2. 实现结构化报告生成

影响文件：

- `stock_picker/reporting/backtest_report.py`
- `tests/test_backtest_report.py`

步骤：

- [ ] 实现从事件回测结果生成各报告表。
- [ ] 使用中文表头或已有统一翻译。
- [ ] 长文本字段保留摘要或完整内容。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_backtest_report.py -q`。

验证：

- 报告结构测试通过。

### F3. 增加 Excel 导出测试

影响文件：

- `tests/test_backtest_report.py`

步骤：

- [ ] 新增测试：Excel 文件包含所有指定 sheet。
- [ ] 新增测试：每个 sheet 第一行为表头。
- [ ] 新增测试：冻结首行、开启筛选、关键金额和百分比字段格式可读。
- [ ] 新增测试：长文本字段不会只输出不可读的原始对象。
- [ ] 运行对应测试，确认新增测试先失败。

验证：

- Excel 可读性要求被测试覆盖。

### F4. 实现 Excel 详细报告导出

影响文件：

- `stock_picker/reporting/backtest_report.py`
- `tests/test_backtest_report.py`

步骤：

- [ ] 实现 Excel 导出。
- [ ] 设置表头、筛选、冻结首行、列宽、数字格式和长文本换行。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_backtest_report.py -q`。

验证：

- Excel 报告测试通过。

---

## G. Web 回测入口

### G1. 增加 Web 缓存区和正式回测默认路径测试

影响文件：

- `tests/test_web_app.py`
- 后续涉及 `examples/web_app.py`

步骤：

- [ ] 新增测试：回测诊断页显示缓存区、参数区、结果区、报告下载区。
- [ ] 新增测试：开始正式回测前，如果缓存缺失，页面提示缺失而不是静默继续。
- [ ] 新增测试：Web 默认结果来自事件驱动正式回测，不是旧简化回测。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py -q`，确认新增测试先失败或暴露缺口。

验证：

- Web 默认正式回测路径被测试覆盖。

### G2. 接入 Web 正式回测流程

影响文件：

- `examples/web_app.py`
- `tests/test_web_app.py`

步骤：

- [ ] 回测诊断页增加缓存状态展示。
- [ ] 正式回测入口调用事件驱动回测。
- [ ] 缓存缺失时展示缺口和 warning。
- [ ] 旧简化回测如果保留，必须明确标注。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py -q`。

验证：

- Web 正式回测流程测试通过。

### G3. 增加 Web 报告展示测试

影响文件：

- `tests/test_web_app.py`

步骤：

- [ ] 新增测试：Web 结果展示总收益、年化收益、最大回撤、胜率、交易次数、最终资金。
- [ ] 新增测试：Web 结果展示每只股票收益表和数据质量 warning。
- [ ] 新增测试：Web 结果提供详细报告下载入口。
- [ ] 新增测试：页面不出现“未翻译字段”或乱码。
- [ ] 运行对应测试，确认新增测试先失败。

验证：

- Web 简版报告展示要求被测试覆盖。

### G4. 实现 Web 简版报告和下载入口

影响文件：

- `examples/web_app.py`
- `stock_picker/reporting/backtest_report.py`
- `tests/test_web_app.py`

步骤：

- [ ] Web 展示摘要卡片、收益表、交易摘要和数据质量 warning。
- [ ] 接入 Excel 下载入口。
- [ ] 长文本默认摘要展示，不一次性铺开底层日志。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py tests/test_backtest_report.py -q`。

验证：

- Web 简版报告和下载入口测试通过。

---

## H. 端到端验证

### H1. 增加小股票池端到端回测测试

影响文件：

- 新增 `tests/test_event_backtest_e2e.py`
- 可复用 `stock_picker/strategies/thermostat_backtest.py`

步骤：

- [ ] 使用 2 到 3 只股票的确定性假缓存数据构造端到端回测。
- [ ] 覆盖至少一次成功买入、一次成功卖出、一次失败交易、一次最终清仓。
- [ ] 验证 Summary、Daily Portfolio、Trades、Positions、Data Quality 都有一致数据。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_event_backtest_e2e.py -q`，确认测试先失败。

验证：

- 正式回测主链路有端到端保护。

### H2. 打通端到端正式回测

影响文件：

- `stock_picker/strategies/event_backtest.py`
- `stock_picker/strategies/thermostat_backtest.py`
- `stock_picker/reporting/backtest_report.py`
- `tests/test_event_backtest_e2e.py`

步骤：

- [ ] 打通缓存数据、参数解析、事件引擎、恒温器信号和报告输出。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_event_backtest_e2e.py -q`。

验证：

- 小股票池端到端测试通过。

### H3. 运行分组回归测试

影响文件：

- 不新增业务改动。

步骤：

- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_limit_status.py tests/test_event_price_cache.py tests/test_event_backtest_engine.py -q`。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_event_backtest.py tests/test_thermostat_backtest.py -q`。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_backtest_params.py tests/test_backtest_report.py tests/test_event_backtest_e2e.py -q`。
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py -q`。

验证：

- 每个分组测试都通过。
- 若失败，先定位属于缓存、引擎、报告还是 Web，不做猜测式修复。

### H4. 运行完整测试

影响文件：

- 不新增业务改动。

步骤：

- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest -q`。
- [ ] 检查输出中是否还有失败、warning 或乱码相关失败。
- [ ] 如果完整测试失败，回到对应最小失败测试定位。

验证：

- 完整测试通过，或明确记录剩余未验证项和失败根因。

### H5. 手动检查 Web 回测页面

影响文件：

- 不新增业务改动。

步骤：

- [ ] 启动本地 Web 应用。
- [ ] 打开回测诊断页。
- [ ] 检查页面分区：缓存区、参数区、结果区、报告下载区。
- [ ] 用小股票池运行一次正式事件驱动回测。
- [ ] 检查缓存缺失提示、数据质量 warning、摘要卡片、收益表、交易摘要和下载入口。
- [ ] 下载 Excel 报告并确认 sheet 和表头可读。

验证：

- 页面行为与规格一致。
- 用户可见内容没有“未翻译字段”或明显乱码。

---

## 完成定义

本任务集完成时必须满足：

- [ ] 正式回测默认走事件驱动路径。
- [ ] 旧简化回测被明确标记为 `simplified_backtest`，不再冒充正式回测。
- [ ] 缓存缺失或涨跌停状态未知时不会静默成交。
- [ ] `noon` 只评估不成交。
- [ ] 买入只在 `afternoon_open` 成交。
- [ ] 卖出只在 `morning_open`、`afternoon_open`、`close` 成交。
- [ ] 失败交易进入 Trades。
- [ ] 最后一天尝试收盘清仓，失败原因可见。
- [ ] Web 和 Excel 报告字段可读，无“未翻译字段”和乱码。
- [ ] 分组测试和完整测试已运行，并记录结果。
