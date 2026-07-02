# 规格：恒温器事件驱动回测系统

## 1. 背景与目标

当前项目已有恒温器策略运行能力，包括 `ThermostatResult`、`ThermostatBacktestResult`、`classify_regime()`、`evaluate_thermostat()`、`run_thermostat_strategy()` 和 `backtest_thermostat_strategy()` 等结构。

当前正式回测结果更接近“简化等权净值回测”：按股票池历史价格生成净值、市场状态表现和诊断指标，但没有真实模拟恒温器信号、现金、持仓、交易失败、交易时间限制、T+1、涨跌停、停牌、费用、滑点和最终清仓。

本次目标是将正式回测升级为“事件驱动回测系统”。正式回测必须按交易日和日内事件顺序模拟策略评估、交易计划、订单执行、持仓变化、现金变化、失败原因和报告输出。

旧的简化回测可以保留，但必须明确标记为 `simplified_backtest`，不得再作为正式回测结果展示。

## 2. 范围

### 2.1 本次要做

- 正式回测入口升级为事件驱动回测。
- 回测前支持缓存目标股票池、市场指数、交易日历和必要价格数据。
- 回测运行时默认基于本地缓存数据，不在每个交易日反复实时请求远端接口。
- 回测继承账户设置中的资金、费用、滑点、交易规则、股票池和策略参数。
- 回测支持本次临时覆盖参数，但临时覆盖不自动写回账户设置。
- 回测按 `morning_open`、`noon`、`afternoon_open`、`close` 四类时间点评估，其中真实成交只允许发生在规定时间点。
- 回测记录每笔成功交易、失败交易、现金变化、持仓变化、手续费、印花税、滑点和失败原因。
- 回测最后一个交易日按收盘价尝试清仓全部持仓。
- Web 端展示简版报告，并提供可下载的详细 Excel 报告。
- 下载报告必须可读、分 sheet、格式化，不得只是原始 DataFrame 导出。

### 2.2 本次不做

- 不重写恒温器正式运行入口的输出表结构。
- 不改变现有账户、股票池、行情源的外部使用方式。
- 不把回测临时覆盖参数自动写回账户设置。
- 不要求第一版必须接入真实分钟级数据源；如果缺少分钟数据，可以用明确标记的弱模拟或缺失状态完成事件回测。
- 不为了增加成交数量而绕过涨跌停、停牌、T+1、现金或交易时间限制。
- 不使用日内最高价或最低价假设成交。
- 不在 `noon` 直接成交。
- 不让旧 `simplified_backtest` 混同为正式回测。
- 不把 Excel 报告做成复杂财务报表系统；本次只要求结构清晰、字段完整、格式可读。

## 3. 核心原则

1. 回测必须保守，不假设不可成交订单能够成交。
2. 信号时间和执行时间必须分离。
3. 现金、持仓、可用股数和总资产必须由交易事件驱动更新。
4. 所有失败交易也必须记录，不能静默忽略。
5. 缺失数据、模拟数据和回退数据必须在报告中明确标记。
6. Web 简版报告优先清晰，Excel 详细报告优先完整可读。
7. 股票池选择、日期范围选择和参数摘要应复用当前恒温器运行系统的用户体验，不重复造一套不一致的输入逻辑。

## 4. 参数来源与优先级

回测参数按以下优先级解析：

1. 本次回测手动覆盖。
2. 账户设置。
3. 策略默认设置。
4. 系统默认设置。

每个关键参数必须记录来源，来源枚举为：

- `user_override`
- `account_setting`
- `strategy_default`
- `system_default`

需要记录来源的参数至少包括：

- 初始资金或账户可用现金。
- 股票池来源和股票池名称。
- 手续费率。
- 最低佣金。
- 印花税率。
- 过户费率，如果存在。
- 滑点模式和滑点值。
- T+1 开关。
- 涨跌停限制开关。
- 停牌限制开关。
- 买入最小单位。
- 是否允许零股卖出。
- 市场基准组合。
- 风险锚指数。
- 恒温器策略参数版本。
- 网格单格仓位和最大层数。
- 最大持仓数量和单股最大仓位。

如果账户缺少某个参数，回测必须使用下一级默认值，并在页面或报告中说明该参数使用了默认值。

## 5. 回测页面行为

回测页面应分为清晰区域：

1. 数据缓存区。
2. 回测参数区。
3. 回测结果区。
4. 报告下载区。

基础设置默认只展示用户必须确认的内容：

- 股票池。
- 回测日期范围。
- 初始资金来源：账户可用现金或模拟资金。
- 缓存历史数据按钮。
- 开始回测按钮。

账户参数摘要应只读展示：

- 账户现金。
- 手续费。
- 印花税。
- 滑点。
- T+1。
- 涨跌停。
- 停牌。
- 买入单位。
- 市场基准。
- 策略参数版本。

高级设置默认折叠，仅用于本次回测覆盖：

- 手续费覆盖。
- 滑点覆盖。
- 市场基准覆盖。
- 策略参数覆盖。
- 是否启用涨跌停。
- 是否启用停牌。
- 是否启用 T+1。
- warm-up 数据长度。
- 是否使用分钟数据。
- 是否允许模拟中午价格。

用户启用本次覆盖后，对应字段才可编辑。关闭覆盖时，应恢复账户或默认参数，不保留误导性编辑状态。

## 6. 股票池与日期范围

回测股票池选择必须复用正式恒温器运行系统的股票池来源逻辑，至少包括：

- 手动输入股票池。
- 自选股组合。
- 当前系统已有的其他股票池来源。

回测缓存和回测运行必须使用同一套股票池解析结果。用户不应在缓存区、回测区和报告区重复填写同一个股票池。

回测日期范围表示正式统计区间，不等于指标计算所需的全部历史区间。系统必须自动向前加载 warm-up 数据，用于计算均线、波动率、分位数和恒温器状态。

默认要求：

- 正式统计只从用户选择的开始日期开始。
- warm-up 期只用于指标计算，不计入收益统计。
- 若需要 252 日指标，系统应尽量向前缓存至少 252 个交易日。
- 如果 warm-up 数据不足，回测仍可运行，但必须记录数据不足 warning。

## 7. 数据缓存

正式事件驱动回测必须先完成缓存校验。用户可以通过“缓存历史数据”按钮主动缓存，也可以在开始正式回测时由系统提示缓存缺失并阻止正式回测继续。

缓存校验通过时，正式回测只能读取本地缓存或本次缓存阶段已经写入的本地数据；不得在每个交易日循环中即时请求远端接口。

缓存必须覆盖：

### 7.1 股票日线数据

至少包含：

- `symbol`
- `date`
- `prev_close`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `amount`
- `turnover`，如果数据源支持。
- 复权或未复权标记。
- `limit_up_price`
- `limit_down_price`
- `is_suspended`
- `board` 或可等价判断板块涨跌幅规则的字段。
- `is_st` 或可等价判断 ST 涨跌幅规则的字段。
- `tradable`

### 7.2 日内时间点数据

至少支持以下逻辑字段：

- `morning_open_price`
- `noon_price`
- `afternoon_open_price`
- `close_price`

如果数据源只有日线 OHLC，则：

- `morning_open_price` 可使用日线 `open`。
- `close_price` 可使用日线 `close`。
- `noon_price` 和 `afternoon_open_price` 不得假装是真实分钟价格。
- 模拟或缺失的日内价格必须标记。
- 如果弱模拟使用日线字段生成 `noon_price` 或 `afternoon_open_price`，报告必须显示其为 `simulated`，且不得在报告中称其为真实中午价或真实下午开盘价。
- 如果系统选择不模拟中午价或下午开盘价，对应字段必须为缺失或不可用状态，并写入 Data Quality。

相关标记包括：

- `simulated_noon_price`
- `simulated_afternoon_open_price`

### 7.3 市场指数数据

默认市场基准：

- 中证1000：50%。
- 创业板指：30%。
- 科创50 或科创100：20%。

沪深300作为系统性风险锚。

参考指数代码默认值：

- 中证1000：`000852.SH`
- 创业板指：`399006.SZ`
- 科创50：`000688.SH`
- 沪深300：`000300.SH`

如果某个指数无法读取：

- 跳过该指数。
- 对剩余指数权重重新归一化。
- 在 Data Quality 或 Parameters 中记录 warning。

如果所有市场指数都无法读取：

- 回退到 `candidate_aggregate`。
- 报告中明确标记 `data_source = candidate_aggregate`。
- 给出 warning。

### 7.4 交易日历与交易状态

缓存应能识别：

- 交易日。
- 非交易日。
- 停牌。
- 是否有可交易价格。
- 涨停。
- 跌停。

涨跌停判断不能只依赖日线 OHLC。缓存数据必须优先包含：

- `prev_close`
- `limit_up_price`
- `limit_down_price`
- `is_suspended`
- `morning_open_limit_status`
- `afternoon_open_limit_status`
- `close_limit_status`

执行时间点的涨跌停状态必须按真实执行时间点分别记录：

- `morning_open_limit_status` 用于开盘卖出判断。
- `afternoon_open_limit_status` 用于下午开盘买入和卖出判断。
- `close_limit_status` 用于收盘卖出和最后清仓判断。

涨跌停状态枚举至少包括：

- `normal`
- `limit_up`
- `limit_down`
- `suspended`
- `limit_status_unknown`

如果数据源不能直接提供涨跌停状态，则可以用 `prev_close` 和板块涨跌幅规则估算 `limit_up_price` 与 `limit_down_price`。估算必须记录数据来源或 warning。

如果缺少 `prev_close`、板块规则、ST 状态或其他必要字段，导致无法判断涨跌停状态，必须标记为 `limit_status_unknown`，不得静默假设可成交。

### 7.5 缓存键

缓存 key 不得只使用 `symbol + date`。缓存必须能区分：

- `symbol`
- `date`
- `time_point` 或 `bar_time`
- `frequency`，例如 `daily`、`1min`、`snapshot`。
- `adjust_type`，例如 `qfq`、`hfq`、`none`。
- `source`
- `updated_at`

## 8. 日内事件顺序

每个交易日按以下顺序运行：

### 8.1 `morning_open`

时间点为 09:30 或第一个可用价格。

行为：

- 更新当日开盘价。
- 对已有持仓运行风控逻辑。
- 允许卖出。
- 不允许新买入。
- 可执行前一交易日收盘后生成的卖出指令。
- 可执行开盘触发的止损、市场下行或个股 `downtrend` 卖出。
- 若卖出时间点跌停，则交易失败。
- 若停牌，则交易失败。

如果使用当天开盘价参与判断，成交必须体现滑点，不得假设同一开盘价无滑点成交。

### 8.2 `noon`

时间点为 11:30 或上午最后一个可用价格。

行为：

- 用截至中午的数据重新运行市场恒温器和个股恒温器。
- 可生成下午买入计划。
- 可生成下午卖出计划。
- 买卖信号不得立即成交，只能排队到 `afternoon_open` 执行，或按规则延迟到 `close`。

必须记录：

- `noon_price`
- `market_regime_noon`
- `stock_regime_noon`
- `action_signal_noon`
- `reason_noon`

### 8.3 `afternoon_open`

时间点为 13:00 或下午第一个可用价格。

行为：

- 执行中午生成的买入计划。
- 执行中午生成的卖出计划。
- 买入价格使用 `afternoon_open_price + slippage`。
- 卖出价格使用 `afternoon_open_price - slippage`。
- 涨停时买入失败，状态为 `failed_limit_up`。
- 跌停时卖出失败，状态为 `failed_limit_down`。
- 停牌时交易失败，状态为 `failed_suspended`。
- 失败交易不得改变现金或持仓。

### 8.4 `close`

时间点为 15:00 或最后一个可用价格。

行为：

- 更新收盘市值。
- 允许卖出。
- 不允许新买入。
- 可执行收盘止损、风控卖出和回测最后一天清仓。
- 跌停或停牌时卖出失败。
- 每日记录净值、现金、持仓市值和总资产。

### 8.5 回测最后一天

最后一个交易日必须按 `close_price` 尝试清仓全部持仓：

- `signal_time = close`
- `execution_time = close`
- `exit_reason = backtest_final_liquidation`
- 成交价为 `close_price - slippage`
- 跌停时状态为 `failed_limit_down`
- 停牌时状态为 `failed_suspended`

如果无法清仓，最终收益按现金加未成功清仓的剩余市值计算，并在报告中单独标记未清仓持仓和原因。

## 9. 成交时间限制

真实成交只允许发生在以下时间点：

- 买入：仅 `afternoon_open`。
- 卖出：`morning_open`、`afternoon_open`、`close`。

`noon` 只能评估和生成计划，不得成交。

除上述时间点外，不得发生任何成交。不得为了成交而在其他时间点补单，也不得用当日最高价或最低价假设成交。

## 10. 信号与执行分离

回测必须区分：

- `signal_time`
- `signal_action`
- `execution_time`
- `execution_status`

示例规则：

- 中午发现买入信号：`signal_time = noon`，`execution_time = afternoon_open`。
- 开盘发现持仓下行：`signal_time = morning_open`，`execution_time = morning_open`。
- 最后一天清仓：`signal_time = close`，`execution_time = close`。

## 11. 交易约束

### 11.1 交易单位

必须根据股票代码和市场规则判断买入最小单位。默认 A 股买入不得少于一手。

买入不足一手时：

- 不成交。
- 记录失败或不可执行原因。
- 不改变现金和持仓。

卖出可按账户规则处理零股卖出。

### 11.2 现金约束

买入必须同时满足：

- 有足够现金覆盖成交金额。
- 有足够现金覆盖手续费、滑点和其他费用。
- 买入股数满足最小单位。

现金不足时：

- 不成交。
- 记录 `insufficient_cash`。

### 11.3 T+1

启用 T+1 时，当日买入股票当日不可卖出。不可卖出的失败或不可执行原因必须记录。

### 11.4 涨跌停与停牌

涨停、跌停、停牌处理必须保守：

- 买入时间点涨停：买入失败，`order_status = failed_limit_up`。
- 卖出时间点跌停：卖出失败，`order_status = failed_limit_down`。
- 执行时间点无可用价格：交易失败，`order_status = failed_suspended`。
- 执行时间点涨跌停状态未知：订单不得静默成交，必须记录 `order_status = limit_status_unknown` 或等价不可执行状态，并写入 failure reason。

失败交易也必须写入 Trades 报告。

## 12. 恒温器策略回测行为

正式事件驱动回测必须调用当前优化后的恒温器逻辑，使用市场状态、股票池强弱、个股状态、趋势建议、网格建议和风控建议生成信号。

市场基准默认使用组合基准，不再只依赖上证指数。沪深300作为风险锚。

仓位建议按当前恒温器规则解释：

- `market_downtrend` 下非持仓股票不得给出可执行买入。
- `market_transition` 下只有强势股可试探仓。
- `market_range` 下震荡股进入网格候选，但不是所有震荡股都启用网格。
- `market_uptrend` 下趋势股优先。

网格参数默认保留：

- `grid_unit_pct = 0.08`
- `grid_max_layers = 4`

网格风控通过限制启用股票数量实现，不通过随意降低单格仓位实现。

## 13. 回测结果数据

正式回测结果至少包含以下数据域：

### 13.1 总体汇总

- 初始资金。
- 结束资金。
- 总收益率。
- 年化收益率。
- 最大回撤。
- 最大回撤发生日期。
- 胜率。
- 盈亏比。
- 交易次数。
- 平均持仓天数。
- 总手续费。
- 总滑点成本。
- 换手率。
- 现金利用率。
- 基准收益。
- 超额收益。

### 13.2 每只股票收益

- 股票代码。
- 名称。
- 买入次数。
- 卖出次数。
- 已实现收益。
- 未实现收益。
- 总收益。
- 最大单笔盈利。
- 最大单笔亏损。
- 平均持仓天数。
- 胜率。
- 是否发生过涨跌停无法成交。
- 是否发生过停牌。

### 13.3 策略分布

- 趋势策略收益贡献。
- 网格策略收益贡献。
- 风控卖出次数。
- `trial_buy` 次数。
- `buy` 次数。
- `sell` 次数。
- `observe` 次数。
- `failed_limit_up` 次数。
- `failed_limit_down` 次数。
- `failed_suspended` 次数。

### 13.4 曲线

- 总资产曲线。
- 现金曲线。
- 持仓市值曲线。
- 回撤曲线。
- 基准对比曲线。

## 14. Excel 详细报告

Web 端必须提供“下载详细报告”入口。报告建议为 Excel 文件。

Excel 至少包含以下 sheet：

1. `Summary`
2. `Daily Portfolio`
3. `Daily Evaluation Detail`
4. `Trades`
5. `Positions`
6. `Symbol Performance`
7. `Data Quality`
8. `Parameters`

### 14.1 `Summary`

记录：

- 回测参数。
- 股票池。
- 时间范围。
- 初始资金。
- 最终资金。
- 总收益。
- 年化收益。
- 最大回撤。
- 交易次数。
- 手续费。
- 滑点。
- 基准收益。
- 策略说明。

### 14.2 `Daily Portfolio`

每个交易日一行，至少包含：

- `date`
- `cash_start`
- `position_value_start`
- `total_value_start`
- `cash_end`
- `position_value_end`
- `total_value_end`
- `daily_return`
- `drawdown`
- `benchmark_return`
- `market_regime_morning`
- `market_regime_noon`
- `market_regime_close`

### 14.3 `Daily Evaluation Detail`

每个交易日、每只股票、每个评估时间点一行，至少包含：

- `date`
- `time_point`
- `symbol`
- `name`
- `open_price`
- `noon_price`
- `afternoon_open_price`
- `close_price`
- `market_regime`
- `stock_regime`
- `strategy_family`
- `signal_action`
- `signal_time`
- `execution_time`
- `suggested_position_pct`
- `suggested_shares`
- `actual_action`
- `actual_shares`
- `signal_reason`
- `risk_note`
- `executable`
- `execution_status`

### 14.4 `Trades`

每笔交易尝试一行，包括失败交易，至少包含：

- `trade_id`
- `date`
- `signal_time`
- `execution_time`
- `symbol`
- `name`
- `side`
- `intended_shares`
- `actual_shares`
- `execution_price`
- `gross_amount`
- `commission`
- `stamp_tax`
- `slippage_cost`
- `net_amount`
- `cash_before`
- `cash_after`
- `position_before`
- `position_after`
- `shares_after`
- `available_shares_after`
- `trade_reason`
- `order_status`
- `failure_reason`

### 14.5 `Positions`

每个交易日每只持仓一行，至少包含：

- `date`
- `symbol`
- `name`
- `total_shares`
- `available_shares`
- `average_cost`
- `last_price`
- `market_value`
- `unrealized_pnl`
- `realized_pnl`
- `holding_days`
- `stop_price`
- `target_price`

### 14.6 `Symbol Performance`

每只股票汇总一行，至少包含：

- `symbol`
- `name`
- `total_buy_amount`
- `total_sell_amount`
- `realized_pnl`
- `total_return`
- `trade_count`
- `win_count`
- `loss_count`
- `win_rate`
- `max_profit_trade`
- `max_loss_trade`
- `average_holding_days`

### 14.7 `Data Quality`

记录数据问题，至少包含：

- `symbol`
- `date`
- `missing_open`
- `missing_noon`
- `missing_afternoon_open`
- `missing_close`
- `suspended`
- `limit_up`
- `limit_down`
- `simulated_noon_price`
- `simulated_afternoon_open_price`
- `data_source`
- `warning`

### 14.8 `Parameters`

记录参数及来源，至少包含：

- `parameter_name`
- `parameter_value`
- `parameter_source`
- `user_overridden`
- `note`

## 14.9 字段可读性

Web 报告和 Excel 报告中的用户可见标题、sheet 名称、表头和主要说明必须可读。内部枚举值可以保留英文，例如 `morning_open`、`failed_limit_up`、`simplified_backtest`，但必须有中文说明或上下文解释。

报告不得出现“未翻译字段”作为最终用户可见标题，也不得出现明显编码错误造成的中文乱码。

## 15. Excel 可读性

下载报告必须满足：

- 每个 sheet 第一行为表头。
- 表头加粗。
- 冻结首行。
- 开启筛选。
- 自动调整列宽。
- 金额保留 2 位小数。
- 收益率显示为百分比。
- 股数显示为整数。
- 日期统一为 `YYYY-MM-DD`。
- 时间点字段统一使用 `morning_open`、`noon`、`afternoon_open`、`close`。
- 重要 sheet 可使用浅色表头，但不得过度装饰。

长文本字段必须可读，适用字段包括：

- `signal_reason`
- `risk_note`
- `trade_reason`
- `warning`
- `evidence`
- `execution_reason`
- `failure_reason`

长文本处理要求：

- 自动换行。
- 垂直居中。
- 合理列宽。
- 必要时拆分 `reason_short` 和 `reason_full`。
- Web 和 Excel 主表优先显示摘要，完整原因保留在详细日志中。

## 16. Web 简版报告

Web 简版报告必须展示：

- 总收益率。
- 年化收益率。
- 最大回撤。
- 胜率。
- 交易次数。
- 最终资金。
- 基准对比。
- 资产曲线。
- 每只股票收益表。
- 数据质量 warning。
- 下载详细报告入口。

Web 页面要求：

- 风格与当前系统一致。
- 缓存区、参数区、结果区、报告下载区分块展示。
- 指标用卡片展示。
- 表格列宽合理。
- 长文本默认截断或折叠，支持查看完整内容。
- 不一次性展示所有底层日志。
- 不要求用户重复填写账户中已有信息。

## 17. 数据质量与错误处理

回测必须记录并展示以下问题：

- 指数数据缺失。
- 股票数据缺失。
- 日线缺失。
- 分时数据缺失。
- `noon_price` 模拟。
- `afternoon_open_price` 模拟。
- 停牌。
- 涨停。
- 跌停。
- 执行时间点涨跌停状态未知。
- 缺少 `prev_close`。
- 缺少 `limit_up_price` 或 `limit_down_price`。
- 缺少板块或 ST 规则，无法估算涨跌停价。
- 无法判断交易单位。
- 现金不足。
- 不足一手。
- T+1 不可卖。
- 回测最后一天无法清仓。

这些问题必须写入：

- Web warning 区域。
- Data Quality sheet。
- 相关交易或评估记录的 reason 字段。

## 18. 兼容性要求

- 现有正式恒温器运行系统保持兼容。
- 现有账户和自选组合功能保持兼容。
- 现有股票池来源逻辑保持兼容。
- 现有行情获取渠道保持兼容。
- 旧简化回测可保留，但必须明确标记为 `simplified_backtest`。
- Web 回测诊断页的默认正式结果必须来自事件驱动回测；如果提供旧简化回测入口，必须作为明确的历史/调试入口展示。
- 正式回测结果不得破坏 Web 端正常渲染。
- 新增详细结果不得要求用户手动读取原始 DataFrame。

## 19. 验收标准

1. 正式回测前必须完成缓存校验；缓存缺失时不得静默继续正式回测。
2. 缓存股票池和时间范围的输入逻辑与恒温器运行系统一致。
3. 正式回测默认使用本地缓存数据，不在每个交易日实时请求远端接口。
4. 缓存 key 能区分 daily、morning_open、noon、afternoon_open、close 等数据。
5. 一天内 `morning_open`、`noon`、`afternoon_open`、`close` 使用不同逻辑价格字段；不得把同一个 `close` 同时冒充四个真实时间点价格。
6. 如果没有真实分钟数据，系统明确标记 `simulated_noon_price` 或 `simulated_afternoon_open_price`，或明确记录对应时间点价格不可用。
7. `noon` 只评估，不成交。
8. 买入只能在 `afternoon_open` 成交。
9. 卖出只能在 `morning_open`、`afternoon_open`、`close` 成交。
10. 除允许时间点外，不发生任何成交。
11. 买入时间点涨停时，交易失败并记录 `failed_limit_up`。
12. 卖出时间点跌停时，交易失败并记录 `failed_limit_down`。
13. 停牌时交易失败并记录 `failed_suspended`。
14. 失败交易也写入 Trades sheet。
15. 最后一个交易日按 `close_price` 尝试清仓全部持仓。
16. 最后一天无法清仓时，报告记录未清仓持仓和原因。
17. 涨停、跌停、停牌、现金不足、不足一手、T+1 不可卖都必须记录为失败或不可执行原因。
18. 缓存数据优先包含 `prev_close`、`limit_up_price`、`limit_down_price`、`is_suspended` 和三个执行时间点的涨跌停状态。
19. 数据源不能直接提供涨跌停状态时，可以用 `prev_close` 和板块涨跌幅规则估算涨跌停价，并在 Data Quality 或 Parameters 中记录。
20. 无法判断执行时间点涨跌停状态时，必须标记 `limit_status_unknown`，不得静默假设可成交。
21. 每笔成功交易必须影响现金、持仓、可用股数和总资产。
22. 回测页面默认从账户设置读取现金、费用、滑点、交易规则、股票池和策略参数。
23. 用户不需要重复填写账户中已有信息。
24. 用户可以选择本次回测覆盖部分参数。
25. 本次覆盖不会自动写回账户设置。
26. 下载报告记录每个关键参数的来源。
27. 页面显示参数摘要，让用户知道本次回测使用了哪些参数。
28. 账户设置缺少参数时，页面明确显示使用了系统默认值。
29. Web 端展示总体回测和各股票收益。
30. 下载报告比 Web 更详细，包含每日开盘评估、中午评估、下午执行、收盘评估。
31. 报告字段可读，不只是原始 DataFrame；用户可见标题不得出现“未翻译字段”或明显中文乱码。
32. Excel 中 reason 类字段有摘要和完整内容的区分，或通过可读格式处理长文本。
33. Excel 报告长文本不横向溢出单元格。
34. Web 端保持整洁统一，不展示过长底层日志。
35. 旧 `simplified_backtest` 不再作为正式回测结果展示，除非明确标记为 simplified。
36. Web 回测诊断页默认展示的是事件驱动正式回测结果，不是旧简化回测结果。
