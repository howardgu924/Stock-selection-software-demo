# 恒温器策略逻辑优化 plan

## 目标和边界

本次改动目标是优化 `thermostat.py` 的恒温器策略判断逻辑，在保留现有数据结构、输出表结构和前端读取方式的前提下，提高市场判断、个股判断、仓位建议和网格筛选的合理性。

本次不是重写项目，不调整账户体系、数据源体系、股票池管理、龙虎榜流程、回测页面结构或前端整体布局。所有新增判断应服务于当前恒温器策略主流程，并保持现有调用方可继续运行。

## 修改模块

### 主要修改

1. `stock_picker/strategies/thermostat.py`
   - 拆分市场状态判断和个股状态判断，避免共用同一套阈值。
   - 增加统一指标计算层，覆盖 ret20、ret60、ma20、ma60、range20、range60、vol20、均线斜率、均线距离、历史分位数和趋势强度。
   - 增加组合市场基准逻辑，默认使用中证1000、创业板指、科创50，并以沪深300作为系统性风险锚。
   - 增加股票池强弱判断，只用于排序、优先级和仓位微调，不直接决定买卖。
   - 调整建议生成逻辑，使市场状态、个股状态、是否持仓、数据长度和现金约束共同决定 action、strategy_family、strength、executable、suggested_position_pct 和 suggested_shares。
   - 增加网格候选评分和启用数量限制，保留 `grid_unit_pct = 0.08` 和 `grid_max_layers = 4`。
   - 增加 ATR20 优先的止损和目标价计算，无法计算时继续使用当前固定比例 fallback。
   - 保留 `REQUIRED_ADVICE_COLUMNS` 的全部字段，避免破坏前端表格读取。

2. `examples/web_app.py`
   - 只在必要时补充新状态、新动作、新强度值的中文展示兼容。
   - 不改页面结构、不改股票池入口、不改账户功能。
   - 确保前端不再因新增语义值显示“未翻译字段”。

3. 测试文件
   - `tests/test_thermostat_strategy.py`：作为核心策略单元测试的主要承载文件。
   - `tests/test_web_app.py`：覆盖前端兼容展示、中文字段和现有页面读取。
   - `tests/test_thermostat_backtest.py`：只在回测依赖恒温器输出语义时补充回归测试，不做无关扩展。

### 尽量不修改

1. 不修改账户持仓、交易流水、自选组合的数据结构。
2. 不修改信息抓取渠道和缓存规则。
3. 不修改龙虎榜候选池流程。
4. 不修改前端整体 UI 结构。
5. 不修改回测诊断的主流程。
6. 不删除或重命名现有输出列。

## 行为保持不变

1. 现有恒温器策略仍由当前入口触发，前端仍通过同一结果结构展示。
2. 股票池来源、账户路径、日期范围、强制刷新等输入方式保持兼容。
3. 账户现金、持仓、交易记录和自选组合功能不受本次策略逻辑调整影响。
4. 系统仍只生成建议，不自动执行真实交易。
5. `grid_unit_pct` 继续为 8%，`grid_max_layers` 继续为 4。
6. 所有 `REQUIRED_ADVICE_COLUMNS` 字段继续存在。
7. 数据源读取失败时应降级或记录错误，不让整个策略崩溃。

## 方案步骤

### 1. 建立测试基线

先补充策略核心测试，锁定以下行为：

1. 市场状态和个股状态使用不同阈值。
2. 少于 60 个交易日不会输出 buy / add / grid。
3. market_downtrend 下非持仓股票不允许新买。
4. `suggested_position_pct > 0` 时，`suggested_shares` 必须同步可执行；现金不足一手时必须在原因中说明。
5. range 股票必须先成为网格候选，再经过评分和数量限制。
6. `REQUIRED_ADVICE_COLUMNS` 不变。

这一步先让测试表达新规则，再调整实现。

### 2. 增加统一指标计算

在 `thermostat.py` 内部增加一层指标计算逻辑，为市场和个股提供同一套基础指标，但不共用分类阈值。

指标包括：

1. 收益：ret20、ret60。
2. 均线：ma20、ma60。
3. 区间：range20、range60。
4. 波动：vol20。
5. 斜率：ma20_slope、ma60_slope。
6. 距离：close_ma20_distance、close_ma60_distance。
7. 分位数：252 日以上才计算 vol20_percentile_252、range20_percentile_252。
8. 趋势强度：trend_strength，波动率缺失或为 0 时返回 0。

数据长度规则在指标层或分类层统一处理，保证短数据不会继续生成完整买入或网格建议。

### 3. 拆分市场和个股分类

将当前共用分类逻辑拆成市场分类和个股分类。

市场分类输出：

1. market_uptrend。
2. market_range。
3. market_downtrend。
4. market_transition。
5. insufficient_data。

个股分类输出：

1. strong_uptrend。
2. uptrend。
3. range。
4. transition。
5. downtrend。
6. insufficient_data。

保留旧 `classify_regime` 的兼容入口或测试适配路径，避免外部导入突然失效。

### 4. 增加组合市场基准

默认市场基准改为组合指数：

1. 中证1000：50%。
2. 创业板指：30%。
3. 科创50：20%。

沪深300作为系统性风险锚，不参与常规综合权重，但参与防守状态判断。

指数读取规则：

1. 某个指数不可用时跳过。
2. 剩余指数权重重新归一化。
3. 全部组合指数不可用时，回退到候选池聚合逻辑。
4. 如果沪深300、中证1000、创业板指同时 downtrend，则市场进入防守状态，不允许非持仓股票新买。

默认指数配置集中放在策略模块配置处，避免散落在业务判断里。

### 5. 增加股票池强弱判断

基于当前候选池计算：

1. pool_above_ma20_ratio。
2. pool_uptrend_count。
3. pool_downtrend_count。
4. pool_ret20。
5. pool_avg_vol20。

股票池状态只影响排序、优先级和仓位微调：

1. pool_strong：趋势候选优先级上调。
2. pool_neutral：不调整。
3. pool_weak：趋势候选优先级下调，正常仓位降为试探仓倾向。
4. pool_chaotic：减少新买，更多 observe。

股票池强弱不得覆盖市场下跌、数据不足、现金不足、个股 transition 等硬约束。

### 6. 调整建议路由和仓位一致性

按市场状态、个股状态、是否持仓和数据长度生成建议。

核心规则：

1. market_downtrend：
   - 非持仓不新买。
   - 持仓 strong_uptrend / uptrend 以 hold 或 reduce 为主。
   - 持仓 downtrend 给 sell。

2. market_transition：
   - 非持仓 strong_uptrend 只允许试探仓。
   - 其他非持仓以 observe 为主。
   - 不新开网格。

3. market_range：
   - strong_uptrend / uptrend 可试探仓或观察。
   - range 进入网格候选筛选。

4. market_uptrend：
   - strong_uptrend 给正常趋势仓位。
   - uptrend 给正常或略低趋势仓位。
   - range 可进入网格候选，但趋势策略优先。

仓位规则：

1. 不允许买入时，`suggested_position_pct = 0` 且 `suggested_shares = 0`。
2. 试探仓为 3% 到 5%。
3. 正常趋势仓为 8% 到 12%。
4. 如果现金不足买入一手，本计划统一采用保守口径：`suggested_position_pct = 0` 且 `suggested_shares = 0`，并在 reason 或 risk_note 中说明“现金不足以买入一手”。
5. 如果前端暂时不支持 `trial_buy`，可用 `action = buy`、`strength = reduced` 并在 reason 中明确“试探仓”。

### 7. 优化网格筛选

所有 range 股票先成为 grid_candidate，再按评分排序。

评分维度：

1. 震荡稳定性。
2. 区间宽度。
3. 波动适中。
4. 当前价格位置。
5. 流动性，如果有字段。
6. 行业分散，如果有字段。

可选字段不存在时跳过该维度并归一化剩余权重。

启用数量限制：

1. market_range 默认最多 2 只。
2. market_range 且市场非常稳定时最多 3 只；“市场非常稳定”按 `range60 <= 10%`、`vol20 <= 1.5%`、`abs(ma60_slope) <= 1%` 理解。
3. market_uptrend 默认最多 1 只；如果没有可执行趋势候选，可最多 2 只。
4. market_transition 和 market_downtrend 不新开网格。

未入选的 range 股票保留为观察或候选，`executable = False`，reason 说明“符合震荡条件，但网格优先级不足，未进入本轮启用名单”。

网格位置和阈值按可测试口径执行：

1. 接近 `grid_mid`：`abs(close / grid_mid - 1) <= 3%`。
2. 接近 `grid_upper`：价格位于 `grid_mid` 到 `grid_upper` 区间的上方 25%。
3. range20 太窄：`range20 < 8%`。
4. range20 太宽：`range20 > 18%`；如果 `range20 > 30%`，优先视为 transition，不作为可执行网格。
5. vol20 太高：`vol20 > 5%`；如果有 252 日数据且 `vol20_percentile_252 >= 80%`，也视为波动过高。

### 8. 优化止损和目标价

优先使用 ATR20：

1. stop_pct 限制在 6% 到 12%。
2. target_pct 为 stop_pct 的 2 倍。
3. 使用 close 计算 stop_price 和 target_price。

如果 ATR20 无法计算，继续使用当前固定比例：

1. stop_price = close * 0.92。
2. target_price = close * 1.18。

### 9. 前端兼容和中文展示

如果新增状态或动作进入前端展示，需要同步中文映射：

1. market_uptrend / market_range / market_downtrend / market_transition。
2. strong_uptrend / uptrend / range / transition / downtrend / insufficient_data。
3. grid_candidate。
4. trial_buy 或试探仓兼容表达。
5. blocked / wait_confirm / reduced 等字段。

目标是不出现“未翻译字段”，同时不改变页面主结构。

## 测试策略

### 单元测试

1. 指标计算：
   - 60 日以下、60 到 120 日、120 到 252 日、252 日以上分别覆盖。
   - vol20 为 0 或缺失时，trend_strength 为 0。

2. 市场分类：
   - market_uptrend、market_range、market_downtrend、market_transition。
   - 极端波动优先进入 transition。
   - 数据不足进入 insufficient_data。

3. 个股分类：
   - strong_uptrend、uptrend、range、downtrend、transition。
   - 252 日分位数触发 transition。
   - range20 > 30% 优先 transition。

4. 组合市场基准：
   - 正常按权重合成。
   - 单个指数缺失时跳过并重新归一化。
   - 全部指数缺失时回退候选池聚合。
   - 沪深300、中证1000、创业板指同时 downtrend 时触发防守。

5. 股票池强弱：
   - strong、neutral、weak、chaotic。
   - 只影响排序或微调，不覆盖硬性禁买条件。

6. 建议生成：
   - market_downtrend 下非持仓不新买。
   - market_transition 下只允许 strong_uptrend 试探仓。
   - market_uptrend 下 strong_uptrend 和 uptrend 给趋势仓位。
   - 少于 60 日不允许 buy / add / grid。
   - 现金不足一手时 reason 有明确说明。

7. 网格筛选：
   - range 股票不会全部启用网格。
   - market_range 默认最多 2 只，稳定市场最多 3 只。
   - market_transition / market_downtrend 不新开网格。
   - `grid_unit_pct` 和 `grid_max_layers` 不变。

8. 输出兼容：
   - `REQUIRED_ADVICE_COLUMNS` 完整保留。
   - 新增语义值不会导致前端未翻译字段。

### 回归测试

每完成一组相关修改后运行对应测试：

1. `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
2. `.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py -q`
3. 如涉及回测输出，再运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_backtest.py -q`

最终运行完整测试：

`.\.venv\Scripts\python.exe -m pytest -q`

## 风险点

1. 指数数据源可能不支持部分指数代码，需要跳过、重归一化和回退逻辑覆盖充分。
2. 新增状态值可能影响前端中文展示，需要同步映射，避免“未翻译字段”。
3. 60 日以下禁买规则会让用户选择最近 1 个月时更常看到 observe，需要 reason 明确说明数据不足。
4. 网格评分如果过严，可能导致候选池为空，需要区分“无候选”和“有候选但未启用”。
5. 股票池强弱如果权重过大，可能误导买卖决策，因此只能做排序和仓位微调。
6. 现金不足一手时，需要避免仓位比例和建议股数矛盾。
7. 旧测试或外部代码可能直接导入 `classify_regime`，需要保留兼容入口或明确迁移测试。

## 为什么不过度复杂

1. 主改动集中在 `thermostat.py`，不拆散到多套新框架。
2. 不引入新的数据源、数据库、任务队列或前端配置系统。
3. 组合市场基准只是在现有历史数据读取能力上做加权和降级，不改变数据抓取架构。
4. 网格优化采用评分和数量限制，不引入参数优化器或回测寻优。
5. 股票池强弱只作为辅助排序，不新增独立交易系统。
6. 前端只做兼容展示，不重构页面。
7. 输出列保持不变，降低对现有页面、测试和脚本的影响。

## 实施顺序

1. 补测试，锁定新规则和兼容边界。
2. 增加指标计算和数据长度规则。
3. 拆分市场和个股分类。
4. 接入组合市场基准和风险锚。
5. 增加股票池强弱判断。
6. 调整建议路由和仓位一致性。
7. 增加网格评分和启用限制。
8. 增加 ATR20 止损目标价 fallback。
9. 补前端中文映射和兼容测试。
10. 运行目标测试和完整测试。

## Planning/Review 审查结论

1. 是否解决核心问题：计划覆盖了 `spec.md` 的核心矛盾，即市场判断和个股判断共用阈值、默认市场基准过窄、股票池强弱缺失、range 股票直接进入网格、仓位比例和建议股数不一致等问题。
2. 测试方案是否清晰：计划已按指标计算、市场分类、个股分类、组合市场基准、股票池强弱、建议生成、网格筛选、输出兼容拆分测试点，并明确了目标测试和完整测试命令。
3. 是否过重复杂：计划把主要实现限制在 `stock_picker/strategies/thermostat.py`，前端只做中文映射和兼容展示，不引入新数据源、数据库、任务队列、参数优化器或前端配置系统。
4. 更稳的实现路径：执行时应先用测试锁定现有输出列和兼容入口，再逐步增加指标层、分类层、市场基准、建议路由和网格评分；不要一次性重写 `run_thermostat_strategy`。
5. 已修订的含糊点：计划已补充“市场非常稳定”、`grid_mid` / `grid_upper` 接近判断、range20 太窄 / 太宽、vol20 太高、现金不足一手的统一处理口径。

后续生成 `tasks.md` 时，应按上述顺序拆成小任务，并优先为每个规则写可独立运行的测试或验证点。
