# 恒温器策略逻辑优化 tasks

## 执行规则

- 本文件只定义任务，不包含实现代码。
- 实现阶段必须优先写测试或验证点，再改实现。
- 每个任务完成后运行该任务列出的验证命令。
- 不允许在一个任务里同时大改策略分类、仓位、网格和前端展示。
- 不修改账户、自选组合、龙虎榜、数据源抓取接口和前端整体布局。

## 任务列表

### T01. 锁定当前输出列兼容性

**目标**：保证后续策略改动不会删除或重命名 `REQUIRED_ADVICE_COLUMNS`。

**涉及文件**：
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 增加或强化测试：恒温器建议输出必须包含 `REQUIRED_ADVICE_COLUMNS` 的所有字段。
- [ ] 增加或强化测试：现有前端依赖的结果分区仍可读取，包括市场概览、持仓建议、新买候选、网格建议、趋势建议和错误。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：新增兼容性测试在当前实现下通过或仅暴露后续任务要修复的新规则差异。

### T02. 为指标计算补测试

**目标**：先用测试定义 ret20、ret60、ma20、ma60、range20、range60、vol20、均线斜率、均线距离、历史分位数和 trend_strength 的行为。

**涉及文件**：
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 增加测试：60 日以下数据标记为数据不足。
- [ ] 增加测试：60 到 120 日数据可计算基础状态但应标记为 reduced。
- [ ] 增加测试：120 到 252 日数据不使用 252 日分位数。
- [ ] 增加测试：252 日以上数据可计算 vol20 和 range20 历史分位数。
- [ ] 增加测试：vol20 为 0 或缺失时，trend_strength 为 0。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：这些测试先失败或标出缺失能力，供后续实现任务修复。

### T03A. 实现基础指标计算

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_strategy.py`

**目标**：先实现不依赖 252 日历史分位数的基础指标，降低一次性改动风险。

**任务**：
- [ ] 增加内部基础指标计算逻辑：close、ret20、ret60、ma20、ma60、range20、range60、vol20、ma20_slope、ma60_slope、close_ma20_distance、close_ma60_distance。
- [ ] 确保短数据和缺失值安全降级，不抛出索引错误。
- [ ] 保持现有公开入口兼容，不要求前端改调用方式。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：T02 中基础指标相关测试通过；252 日分位数和 trend_strength 相关测试可继续失败，交给 T03B。

### T03B. 实现历史分位数和趋势强度

**目标**：在基础指标稳定后，再补充 252 日历史分位数和 trend_strength。

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 252 日以上数据计算 vol20_percentile_252 和 range20_percentile_252。
- [ ] 120 到 252 日数据不使用历史分位数。
- [ ] 计算 trend_strength。
- [ ] vol20 为 0 或缺失时，trend_strength 返回 0，不抛除零错误。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：T02 指标计算相关测试全部通过。

### T04. 为市场状态分类补测试

**目标**：用测试锁定市场分类阈值和优先级，确保市场和个股不再共用一套阈值。

**涉及文件**：
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 增加测试：market_uptrend 满足 ret60、close > ma60、ma20_slope > 0。
- [ ] 增加测试：market_range 满足 abs(ret60)、range60、abs(ma60_slope)。
- [ ] 增加测试：market_downtrend 满足 ret60、close < ma60、ma20_slope < 0。
- [ ] 增加测试：vol20 > 3.5%、range20 > 12% 或趋势冲突时进入 market_transition。
- [ ] 增加测试：市场数据不足时进入 insufficient_data。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：市场分类测试先失败或暴露当前共用阈值问题。

### T05. 实现市场状态分类

**目标**：实现独立的市场分类逻辑，不再让市场指数沿用个股阈值。

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 增加市场分类入口或在现有分类入口中支持 market 模式。
- [ ] 按 spec 中的市场阈值和优先级返回 market_uptrend、market_range、market_downtrend、market_transition、insufficient_data。
- [ ] 保留 `classify_regime` 的兼容入口，避免旧导入直接失效。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：T04 市场分类测试通过，旧兼容测试不破。

### T06. 为个股状态分类补测试

**目标**：用测试锁定 strong_uptrend、uptrend、range、transition、downtrend、insufficient_data。

**涉及文件**：
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 增加测试：strong_uptrend 满足 ret60、close > ma20、ma20 > ma60、trend_strength。
- [ ] 增加测试：uptrend 满足 ret60、close > ma60、ma20 > ma60、ma20_slope。
- [ ] 增加测试：range 满足 ret20、range20、ma20_slope、close_ma20_distance。
- [ ] 增加测试：downtrend 满足 ret60、close < ma60、ma20_slope < 0。
- [ ] 增加测试：range20 > 30%、高历史分位数或趋势冲突优先 transition。
- [ ] 增加测试：少于 60 日为 insufficient_data。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：个股分类测试先失败或暴露当前共用阈值问题。

### T07. 实现个股状态分类

**目标**：实现独立个股分类逻辑，并与市场分类共用指标、不共用阈值。

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 增加个股分类入口或在现有分类入口中支持 stock 模式。
- [ ] 按 spec 中的个股阈值和优先级返回 strong_uptrend、uptrend、range、transition、downtrend、insufficient_data。
- [ ] 确保 252 日分位数只在数据足够时参与 transition 判断。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：T06 个股分类测试通过，T04 市场分类测试仍通过。

### T08. 为组合市场基准补测试

**目标**：先定义默认组合基准、指数缺失降级和系统性风险锚的行为。

**涉及文件**：
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 增加测试：默认组合基准使用中证1000、创业板指、科创50。
- [ ] 增加测试：单个指数不可用时跳过并重新归一化权重。
- [ ] 增加测试：全部默认指数不可用时回退候选池聚合逻辑。
- [ ] 增加测试：沪深300、中证1000、创业板指同时 downtrend 时触发防守状态。
- [ ] 增加测试：指数读取失败不导致策略整体崩溃。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：组合基准测试先失败或标出缺失能力。

### T09A. 实现组合市场基准配置和加权

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_strategy.py`

**目标**：先把默认市场判断从单一 `000001.SH` 改为可配置组合基准。

**任务**：
- [ ] 增加集中默认指数配置：中证1000、创业板指、科创50、沪深300风险锚。
- [ ] 实现默认组合指数正常可用时的加权基准计算。
- [ ] 确保现有 `run_thermostat_strategy` 调用方式保持兼容。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：T08 中默认组合基准正常加权相关测试通过；指数缺失和风险锚相关测试可继续失败，交给 T09B。

### T09B. 实现指数缺失降级和风险锚

**目标**：在组合基准可用后，补齐指数失败降级、候选池回退和系统性风险防守。

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 实现指数缺失跳过和权重重归一化。
- [ ] 实现全部组合指数不可用时回退候选池聚合。
- [ ] 实现沪深300、中证1000、创业板指同时 downtrend 时触发防守状态。
- [ ] 确保指数读取失败不导致策略整体崩溃。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：T08 组合基准测试全部通过。

### T10. 为数据长度和短日期范围补测试

**目标**：确保用户选择较短日期范围时系统可以运行，但不会误导为完整买入或网格建议。

**涉及文件**：
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 增加策略测试：少于 60 日不允许 buy、add 或可执行 grid。
- [ ] 增加策略测试：少于 60 日 suggested_position_pct 和 suggested_shares 都为 0。
- [ ] 增加策略测试：reason 或 risk_note 明确说明数据不足。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：短数据行为测试先失败或暴露当前误导点。

### T11. 实现数据长度规则

**目标**：落实少于 60 日、60 到 120 日、120 到 252 日、252 日以上的分层规则。

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 少于 60 日时，强制 observe 或 wait_confirm，不允许 buy、add、可执行 grid。
- [ ] 60 到 120 日时，只允许 reduced 或试探仓，不给满仓位建议。
- [ ] 120 到 252 日时，允许正常趋势和网格建议，但不使用历史分位数。
- [ ] 252 日以上时，启用历史分位数判断。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：T10 测试通过。

### T12. 为股票池强弱补测试

**目标**：定义 pool_strong、pool_neutral、pool_weak、pool_chaotic，并证明它们不直接决定买卖。

**涉及文件**：
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 增加测试：pool_above_ma20_ratio >= 60% 为 pool_strong。
- [ ] 增加测试：40% 到 60% 为 pool_neutral。
- [ ] 增加测试：低于 40% 为 pool_weak。
- [ ] 增加测试：ret20 分化大且 pool_avg_vol20 >= 4% 为 pool_chaotic。
- [ ] 增加测试：pool_weak 或 pool_chaotic 不覆盖 market_downtrend 禁买规则。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：股票池强弱测试先失败或标出缺失能力。

### T13. 实现股票池强弱判断

**目标**：增加股票池层判断，仅影响排序、优先级和仓位微调。

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 计算 pool_above_ma20_ratio、pool_uptrend_count、pool_downtrend_count、pool_ret20、pool_avg_vol20。
- [ ] 返回 pool_strong、pool_neutral、pool_weak、pool_chaotic。
- [ ] 将股票池强弱接入候选排序或仓位微调。
- [ ] 确保股票池强弱不能覆盖市场防守、数据不足、现金不足和 transition 观察规则。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：T12 测试通过。

### T14. 为仓位和动作路由补测试

**目标**：先锁定 market_downtrend、market_transition、market_range、market_uptrend 下的 action、strength、executable、suggested_position_pct 和 suggested_shares。

**涉及文件**：
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 增加测试：market_downtrend 下非持仓股票不新买，仓位和股数为 0。
- [ ] 增加测试：market_downtrend 下持仓 downtrend 给 sell。
- [ ] 增加测试：market_transition 下非持仓 strong_uptrend 只允许试探仓或兼容 buy + reduced。
- [ ] 增加测试：market_transition 下普通 uptrend、range、transition 为 observe。
- [ ] 增加测试：market_range 下 uptrend 或 strong_uptrend 只给试探仓或 observe。
- [ ] 增加测试：market_uptrend 下 strong_uptrend 给 10% 到 12%，uptrend 给 8% 到 10%。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：仓位和动作路由测试先失败或标出当前逻辑差异。

### T15A. 实现 market_downtrend 动作路由

**目标**：先实现下跌市场的禁买和持仓风控，优先降低错误买入风险。

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 实现 market_downtrend 下的非持仓禁买和持仓风控。
- [ ] 非持仓股票 action 为 blocked 或 observe，仓位和股数为 0，executable 为 False。
- [ ] 持仓 downtrend 给 sell。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：T14 中 market_downtrend 相关测试通过，其余动作路由测试可继续失败。

### T15B. 实现 market_transition 动作路由

**目标**：实现过渡市场下的试探仓和观察规则。

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 非持仓 strong_uptrend 只允许试探仓或兼容 buy + reduced。
- [ ] 普通 uptrend、range、transition 为 observe。
- [ ] 不新开网格。
- [ ] 如果不扩展前端 action 枚举，使用 buy + reduced + reason 包含“试探仓”的兼容表达。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：T14 中 market_transition 相关测试通过。

### T15C. 实现 market_range 动作路由

**目标**：实现震荡市场下的试探仓和网格候选分流。

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] strong_uptrend 或 uptrend 给试探仓或 observe。
- [ ] range 股票进入 grid_candidate，不直接全部可执行。
- [ ] transition 和 downtrend 默认 observe 或风控。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：T14 中 market_range 相关测试通过。

### T15D. 实现 market_uptrend 动作路由

**目标**：实现上升市场下的趋势买入或加仓规则。

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] strong_uptrend 给 10% 到 12% 的正常趋势仓位。
- [ ] uptrend 给 8% 到 10% 的正常或略低趋势仓位。
- [ ] range 可进入网格候选，但趋势策略优先。
- [ ] 确保 T15A 到 T15C 已通过的路由规则不回退。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：T14 动作路由测试全部通过。

### T16. 为现金不足一手补测试

**目标**：避免 `suggested_position_pct > 0` 但 `suggested_shares = 0` 且无说明的矛盾。

**涉及文件**：
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 增加测试：现金不足买入一手时，suggested_position_pct 为 0。
- [ ] 增加测试：现金不足买入一手时，suggested_shares 为 0。
- [ ] 增加测试：reason 或 risk_note 包含“现金不足以买入一手”。
- [ ] 增加测试：现金充足时，suggested_position_pct 和 suggested_shares 同步产生。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：现金一致性测试先失败或标出当前矛盾。

### T17. 实现现金和建议股数一致性

**目标**：统一 suggested_position_pct 和 suggested_shares 的计算与降级。

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 调整建议股数计算，使现金不足一手时统一返回 0 仓位和 0 股数。
- [ ] 在 reason 或 risk_note 中加入现金不足说明。
- [ ] 确保允许买入时股数按现金、价格和一手规则计算。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：T16 测试通过。

### T18. 为网格评分补测试

**目标**：先定义 grid_candidate、评分维度、价格位置判断和可选字段归一化。

**涉及文件**：
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 增加测试：range 股票先进入 grid_candidate，不直接全部启用。
- [ ] 增加测试：接近 grid_mid 的股票评分高于接近 grid_upper 的股票。
- [ ] 增加测试：range20 < 8%、range20 > 18%、vol20 > 5% 会降低或阻止网格启用。
- [ ] 增加测试：range20 > 30% 优先 transition，不作为可执行网格。
- [ ] 增加测试：无换手率或行业字段时跳过该评分项并归一化，不报错。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：网格评分测试先失败或标出当前直接网格问题。

### T19A. 实现网格核心评分

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_strategy.py`

**目标**：先实现不依赖可选字段的网格评分和排序。

**任务**：
- [ ] 实现震荡稳定性、区间宽度、波动适中、当前位置评分。
- [ ] 接近 grid_mid 的股票评分高于接近 grid_upper 的股票。
- [ ] range20 太窄、太宽、vol20 太高时降低评分或阻止启用。
- [ ] range20 > 30% 优先 transition，不作为可执行网格。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：T18 中核心网格评分相关测试通过；可选字段和未启用解释相关测试可继续失败，交给 T19B。

### T19B. 实现网格可选字段和未启用解释

**目标**：补齐换手率、行业分散等可选评分，并解释未启用的 range 股票。

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 如果有换手率字段，纳入流动性评分；没有则跳过并归一化。
- [ ] 如果有行业字段，纳入行业分散评分；没有则跳过并归一化。
- [ ] 未入选网格的 range 股票设置为 observe 或兼容 grid_candidate，executable 为 False。
- [ ] reason 说明网格优先级不足。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：T18 测试通过。

### T20. 为网格启用数量限制补测试

**目标**：锁定 market_range、market_uptrend、market_transition、market_downtrend 下的网格启用数量。

**涉及文件**：
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 增加测试：market_range 默认最多启用 2 只网格股票。
- [ ] 增加测试：market_range 且非常稳定时最多启用 3 只网格股票。
- [ ] 增加测试：market_uptrend 默认最多启用 1 只网格股票。
- [ ] 增加测试：market_uptrend 且没有可执行趋势候选时最多启用 2 只。
- [ ] 增加测试：market_transition 和 market_downtrend 不新开网格。
- [ ] 增加测试：grid_unit_pct 仍为 0.08，grid_max_layers 仍为 4。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：网格数量限制测试先失败或标出当前逻辑差异。

### T21. 实现网格启用数量限制

**目标**：按市场状态限制可执行网格数量，不通过降低 grid_unit_pct 控制风险。

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] market_range 默认只启用评分前 2 只。
- [ ] market_range 且非常稳定时最多启用评分前 3 只。
- [ ] market_uptrend 默认最多启用 1 只；无趋势可执行候选时最多 2 只。
- [ ] market_transition 和 market_downtrend 不新开网格。
- [ ] 保持 grid_unit_pct 和 grid_max_layers 不变。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：T20 测试通过。

### T22. 为 ATR20 止损目标价补测试

**目标**：先定义 ATR20 可用时的自适应止损目标价，以及不可用时的 fallback。

**涉及文件**：
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 增加测试：可计算 ATR20 时，stop_pct 限制在 6% 到 12%。
- [ ] 增加测试：target_pct 为 stop_pct 的 2 倍。
- [ ] 增加测试：无法计算 ATR20 时使用 close * 0.92 和 close * 1.18。
- [ ] 增加测试：止损和目标价仍写入现有输出字段，不新增前端必需字段。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：ATR20 测试先失败或标出当前固定比例逻辑。

### T23. 实现 ATR20 止损目标价

**目标**：趋势建议优先使用波动率自适应止损目标价，失败时保留固定比例 fallback。

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `tests/test_thermostat_strategy.py`

**任务**：
- [ ] 计算 ATR20 可用性。
- [ ] 可用时按 2 * ATR20 / close 计算 stop_pct 并限制在 6% 到 12%。
- [ ] target_pct 使用 stop_pct 的 2 倍。
- [ ] 不可用时保留当前固定比例 fallback。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py -q`
- [ ] 预期：T22 测试通过。

### T24. 为前端中文兼容补测试

**目标**：确保新增状态、动作、强度和策略族不会显示“未翻译字段”。

**涉及文件**：
- `tests/test_web_app.py`

**任务**：
- [ ] 增加测试：市场状态新增值能显示中文。
- [ ] 增加测试：个股状态新增值能显示中文或兼容中文说明。
- [ ] 增加测试：grid_candidate、blocked、wait_confirm、reduced、trial_buy 或兼容试探仓表达不显示“未翻译字段”。
- [ ] 增加测试：结果页仍能显示原有六类结果区域。
- [ ] 增加测试：短日期范围结果页能提示数据不足，不显示成完整买入或可执行网格建议。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py -q`
- [ ] 预期：前端中文兼容测试先失败或标出缺失映射。

### T25. 实现前端中文兼容

**目标**：只补中文映射和兼容展示，不改前端整体结构。

**涉及文件**：
- `examples/web_app.py`
- `tests/test_web_app.py`

**任务**：
- [ ] 补充市场状态中文映射。
- [ ] 补充个股状态中文映射。
- [ ] 补充动作、强度、策略族中文映射。
- [ ] 确保未扩展前端 action 枚举时，试探仓以 buy + reduced + reason 兼容展示。
- [ ] 不改股票池入口、账户入口、页面布局。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py -q`
- [ ] 预期：T24 测试通过。

### T26. 回测诊断兼容检查

**目标**：确认恒温器策略输出语义变化不会破坏回测诊断。

**涉及文件**：
- `tests/test_thermostat_backtest.py`
- 必要时 `stock_picker/strategies/thermostat.py`

**任务**：
- [ ] 运行现有回测诊断测试，确认是否受策略输出字段变化影响。
- [ ] 如果失败，只补兼容测试或兼容映射，不重写回测主流程。
- [ ] 确认 `REQUIRED_ADVICE_COLUMNS` 仍被回测兼容读取。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_backtest.py -q`
- [ ] 预期：回测诊断测试通过；如失败，失败原因必须明确归因到输出兼容而不是回测重构。

### T27. 策略集成回归

**目标**：把策略层、前端兼容和回测兼容放在一起做最小集成验证。

**涉及文件**：
- `stock_picker/strategies/thermostat.py`
- `examples/web_app.py`
- `tests/test_thermostat_strategy.py`
- `tests/test_web_app.py`
- `tests/test_thermostat_backtest.py`

**任务**：
- [ ] 运行恒温器策略单测。
- [ ] 运行前端兼容测试。
- [ ] 运行回测兼容测试。
- [ ] 检查失败是否来自本次新规则；如果失败，先定位根因再修复。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest tests/test_thermostat_strategy.py tests/test_web_app.py tests/test_thermostat_backtest.py -q`
- [ ] 预期：三个测试文件全部通过。

### T28. 完整验证

**目标**：完成前进行全量回归，确认没有破坏项目其他功能。

**涉及文件**：
- 全项目

**任务**：
- [ ] 运行完整测试。
- [ ] 检查 `git status --short --branch`，确认只包含本次相关文件变更。
- [ ] 人工检查结果页中文输出，不应出现“未翻译字段”。
- [ ] 人工检查一个短日期范围场景，结果必须明确说明数据不足，不给完整买入或网格建议。
- [ ] 人工检查一个市场下跌场景，非持仓股票不应给新买仓位。

**验证**：
- [ ] 运行 `.\.venv\Scripts\python.exe -m pytest -q`
- [ ] 运行 `git status --short --branch`
- [ ] 预期：测试通过，变更范围符合 `spec.md` 和 `plan.md`。

## 任务顺序说明

1. T01 到 T03B 先锁输出兼容和指标层，避免后面分类和建议逻辑无基础。
2. T04 到 T07 拆市场和个股分类，先解决核心阈值问题。
3. T08 到 T11 接入组合市场基准和数据长度规则，降低错误买入风险。
4. T12 到 T17 处理股票池强弱、动作路由和仓位一致性，其中 T15A 到 T15D 按市场状态拆分执行。
5. T18 到 T21 单独处理网格评分和启用限制，其中 T19A 到 T19B 先核心评分、后可选字段和解释。
6. T22 到 T25 处理止损目标价和前端中文兼容。
7. T26 到 T28 做回测、集成和完整验证。

## Task Review 审查结论

1. 小步可执行：已将原本偏大的指标实现、组合市场基准、动作路由、网格评分拆成 A/B 或按市场状态拆分的小任务。
2. 验证方式：每个任务都有独立验证命令；实现任务完成后运行对应测试，最后再运行集成和完整测试。
3. 改动范围：策略核心任务优先限制在 `stock_picker/strategies/thermostat.py` 和 `tests/test_thermostat_strategy.py`；前端只在 T24 到 T25 处理，回测只在 T26 单独检查。
4. 测试覆盖：覆盖了指标、市场分类、个股分类、组合基准、数据长度、股票池强弱、动作路由、现金一致性、网格评分、网格数量、ATR、前端中文、回测和全量回归。
5. 顺序风险：先测试和兼容基线，再做分类和市场基准，之后才进入仓位、网格和前端，避免先大改主流程。

## 不做范围

- 不重写账户、持仓、交易流水、自选组合。
- 不修改龙虎榜候选池流程。
- 不更换行情数据源。
- 不新增真实交易执行。
- 不重构前端页面布局。
- 不删除或重命名 `REQUIRED_ADVICE_COLUMNS`。
