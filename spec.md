# 恒温器策略逻辑优化规格

## 1. 背景与问题

当前恒温器策略已经具备“市场状态 + 个股状态”的基本结构，但存在以下问题：

- 市场状态和个股状态共用同一套 `classify_regime()` 阈值，未区分指数和个股的波动特征。
- 市场判断默认依赖单一 `000001.SH`，不适合偏中小盘、成长、科技制造、通信设备、半导体、IT 服务、自动化设备的自选股池。
- 股票池整体强弱没有单独判断，候选排序和优先级缺少组合层面的参考。
- 个股进入网格策略过于直接，所有 `range` 股票都有可能进入网格建议，缺少筛选和数量限制。
- 仓位建议存在潜在不一致：可能出现 `suggested_position_pct > 0` 但 `suggested_shares = 0` 时原因不明确。
- 当前固定止损和目标价可以保留，但需要优先使用波动率自适应方式。

本规格目标是在保留现有数据结构、输出表结构和前端兼容性的基础上，优化 `thermostat.py` 中恒温器策略逻辑，不重写整个项目。

## 2. 总体目标

恒温器策略应拆分为三层判断：

1. 市场恒温器
   - 判断整体市场环境。
   - 决定是否允许新买、是否允许试探仓、是否允许网格。
   - 不再只依赖单一 `000001.SH`。

2. 股票池强弱
   - 根据当前股票池判断整体强弱。
   - 只用于排序、优先级和仓位微调。
   - 不直接决定买卖。

3. 个股恒温器
   - 判断每只股票属于 `strong_uptrend`、`uptrend`、`range`、`transition`、`downtrend` 或 `insufficient_data`。
   - 个股状态决定策略类型：趋势、网格、观察或风控。

## 3. 范围

本次规格覆盖：

- 市场状态判断规则。
- 个股状态判断规则。
- 组合市场基准和系统性风险锚。
- 股票池强弱判断。
- 趋势、网格、观察、风控之间的策略路由。
- 仓位比例、建议股数、可执行性和说明字段。
- 网格候选筛选、评分、排序和启用数量限制。
- 止损价和目标价的自适应计算。
- 保持现有前端和输出表结构兼容。

本次规格不覆盖：

- 重写整个选股系统。
- 更换行情数据源。
- 改写账户、持仓、交易流水、手动买卖、成本调整逻辑。
- 自动交易或真实下单。
- 新增前端复杂参数面板。
- 改变现有 `REQUIRED_ADVICE_COLUMNS` 字段集合。
- 将策略参数做成完整参数优化系统。

## 4. 市场基准要求

### 4.1 组合市场基准

默认市场判断不应只依赖 `000001.SH`。系统应支持组合市场基准，默认权重如下：

- 中证1000：50%，默认代码 `000852.SH`
- 创业板指：30%，默认代码 `399006.SZ`
- 科创50 或科创100：20%，默认代码优先 `000688.SH`

组合市场基准用于判断 `market_regime`。

### 4.2 系统性风险锚

系统应同时使用沪深300作为风险锚：

- 沪深300默认代码：`000300.SH`
- 沪深300不参与组合市场基准权重计算。
- 沪深300只用于判断系统性风险。

如果沪深300、中证1000、创业板指同时为 `downtrend`，市场应进入防守状态，不允许非持仓股票新买。

### 4.3 指数缺失处理

如果某个指数无法读取或数据不足：

- 程序不得崩溃。
- 跳过该指数。
- 对剩余可用指数重新归一化权重。
- 如果所有市场指数都无法读取，再回退到当前候选股票池聚合逻辑。
- 结果说明中应体现实际使用的数据来源或降级原因。

### 4.4 可配置性

默认指数代码和权重应可配置。规格不要求前端立即暴露配置入口，但核心逻辑不得把所有指数和权重不可替换地散落在业务判断中。

## 5. 市场和个股阈值拆分

市场状态和个股状态不得继续共用同一套阈值。

允许以下两种接口形态之一：

- 独立市场判断：`classify_market_regime()`
- 独立个股判断：`classify_stock_regime()`

或保留统一入口，但必须能通过 `mode="market"` 和 `mode="stock"` 使用两套不同阈值。

市场指数波动较小，阈值应更窄。个股波动较大，阈值应更宽。

## 6. 状态判断指标

市场和个股状态判断应尽量计算以下指标。若数据不足以计算某个指标，应安全降级，不得除零或崩溃。

基础指标：

- `close`
- `ret20`：最近20个交易日首尾收益
- `ret60`：最近60个交易日首尾收益
- `ma20`：20日均线
- `ma60`：60日均线
- `range20`：最近20日最高收盘价与最低收盘价的区间宽度 / 20日均价
- `range60`：最近60日最高收盘价与最低收盘价的区间宽度 / 60日均价
- `vol20`：最近20日日收益率标准差
- `ma20_slope`：ma20 相比 5 个交易日前 ma20 的变化率
- `ma60_slope`：ma60 相比 10 个交易日前 ma60 的变化率
- `close_ma20_distance`：`close / ma20 - 1`
- `close_ma60_distance`：`close / ma60 - 1`

当至少有 252 个交易日数据时，还应计算：

- `vol20_percentile_252`：当前 vol20 在过去252日滚动 vol20 中的分位数
- `range20_percentile_252`：当前 range20 在过去252日滚动 range20 中的分位数

趋势强度：

- `trend_strength = ret60 / (vol20 * sqrt(60))`
- 如果 `vol20` 为 0、缺失或不可用，`trend_strength` 必须设为 0。

## 7. 数据长度规则

策略定位为短期或中期由股票状态决定，不由用户手动固定。

数据长度规则如下：

1. 少于60个交易日
   - `data_sufficient = False`
   - 不允许 `buy`、`add`、`grid`
   - `action = wait_confirm` 或 `observe`
   - `suggested_position_pct = 0`
   - `suggested_shares = 0`
   - 说明中应提示数据不足，不能生成完整买入、加仓或网格建议。

2. 60至120个交易日
   - 可以判断状态。
   - 允许试探仓。
   - 不给满仓位建议。
   - `strength = reduced`。

3. 120至252个交易日
   - 可以正常输出趋势和网格建议。
   - 不使用历史分位数。

4. 252个交易日以上
   - 正常输出。
   - 使用 `vol20_percentile_252` 和 `range20_percentile_252` 优化 `transition` 判断。

如果用户选择最近1个月等短日期范围，系统可以继续运行，但不得让用户误以为该结果能产生完整买入、加仓或网格建议。

## 8. 市场状态规则

市场状态输出应至少包括：

- `market_uptrend`
- `market_range`
- `market_downtrend`
- `market_transition`
- `insufficient_data`

### 8.1 market_uptrend

满足：

- `ret60 >= 5%`
- `close > ma60`
- `ma20_slope > 0`

### 8.2 market_range

满足：

- `abs(ret60) <= 5%`
- `range60 <= 15%`
- `abs(ma60_slope) <= 2%`

### 8.3 market_downtrend

满足：

- `ret60 <= -6%`
- `close < ma60`
- `ma20_slope < 0`

### 8.4 market_transition

满足以下任一情况即可：

- `vol20 > 3.5%`
- `range20 > 12%`
- 趋势指标冲突，例如 `ret60 > 0` 但 `close < ma60`
- 趋势指标冲突，例如 `ret60 < 0` 但 `close > ma60`
- 不满足 `uptrend`、`range`、`downtrend` 的其他情况

### 8.5 市场状态优先级

市场状态判断优先级：

1. `insufficient_data`
2. `market_downtrend`
3. 极端波动或明显冲突导致的 `market_transition`
4. `market_uptrend`
5. `market_range`
6. 其他 `market_transition`

## 9. 个股状态规则

个股状态输出应至少包括：

- `strong_uptrend`
- `uptrend`
- `range`
- `transition`
- `downtrend`
- `insufficient_data`

### 9.1 stock_strong_uptrend

满足：

- `ret60 >= 12%`
- `close > ma20`
- `ma20 > ma60`
- `trend_strength >= 1.2`

### 9.2 stock_uptrend

满足：

- `ret60 >= 8%`
- `close > ma60`
- `ma20 > ma60`
- `ma20_slope > 0`

### 9.3 stock_range

满足：

- `abs(ret20) <= 5%`
- `range20 >= 6%`
- `range20 <= 20%`
- `abs(ma20_slope) <= 2%`
- `abs(close_ma20_distance) <= 3%`

### 9.4 stock_downtrend

满足：

- `ret60 <= -8%`
- `close < ma60`
- `ma20_slope < 0`

### 9.5 stock_transition

满足以下任一情况即可：

- 有252日数据且 `vol20_percentile_252 >= 80%`
- 有252日数据且 `range20_percentile_252 >= 80%`
- `range20 > 30%`
- 趋势指标冲突
- 不满足 `uptrend`、`range`、`downtrend` 的其他情况

### 9.6 个股状态优先级

个股状态判断优先级：

1. `insufficient_data`
2. `downtrend`
3. 极端波动、`range20 > 30%` 或分位数过高导致的 `transition`
4. `strong_uptrend`
5. `uptrend`
6. `range`
7. 其他 `transition`

## 10. 短期和中期策略选择

策略周期不由用户手动固定，而由个股状态决定：

- `range`：短期网格策略候选
- `transition`：观察，不交易
- `uptrend`：中期趋势跟随
- `strong_uptrend`：中期趋势跟随
- `downtrend`：风控，卖出或禁买
- `insufficient_data`：等待确认或观察

## 11. 仓位和动作规则

系统必须统一 `suggested_position_pct` 和 `suggested_shares`：

- 不允许买：`suggested_position_pct = 0` 且 `suggested_shares = 0`
- 允许试探仓：`suggested_position_pct` 在 3% 至 5% 之间，`suggested_shares` 按现金和价格计算
- 正常买入：`suggested_position_pct` 在 8% 至 12% 之间，`suggested_shares` 按现金和价格计算

不得出现 `suggested_position_pct > 0` 但 `suggested_shares = 0` 且没有解释的情况。如果现金不足以买入一手，`reason` 或 `risk_note` 必须说明“现金不足以买入一手”。

### 11.1 market_downtrend

非持仓股票：

- `action = blocked` 或 `observe`
- `suggested_position_pct = 0`
- `suggested_shares = 0`
- `executable = False`

持仓 `strong_uptrend`：

- `action = hold`
- 不加仓
- `suggested_position_pct = 0`
- `suggested_shares = 0`

持仓 `uptrend`：

- `action = hold` 或 `reduce`
- 不加仓
- `suggested_position_pct = 0`
- `suggested_shares = 0`

持仓 `downtrend`：

- `action = sell`
- `suggested_position_pct = 0`
- `suggested_shares = 0`

### 11.2 market_transition

非持仓 `strong_uptrend`：

- `action = trial_buy`
- 如前端暂不支持 `trial_buy`，可保留 `action = buy`
- `strength = reduced`
- `suggested_position_pct = 0.03` 至 `0.05`
- `suggested_shares` 按现金和价格计算
- `reason` 必须注明“试探仓”

非持仓普通 `uptrend`、`range`、`transition`：

- `action = observe`
- `suggested_position_pct = 0`
- `suggested_shares = 0`

### 11.3 market_range

`strong_uptrend` 或 `uptrend`：

- `action = trial_buy` 或 `observe`
- 如前端暂不支持 `trial_buy`，可保留 `action = buy`
- `strength = reduced`
- `suggested_position_pct = 0.03` 至 `0.05`
- `suggested_shares` 按现金和价格计算

`range`：

- 进入 `grid_candidate` 筛选。
- 不是所有 `range` 股票都直接开网格。

### 11.4 market_uptrend

`strong_uptrend`：

- `action = buy` 或 `add`
- `suggested_position_pct = 0.10` 至 `0.12`
- `suggested_shares` 按现金和价格计算

`uptrend`：

- `action = buy` 或 `add`
- `suggested_position_pct = 0.08` 至 `0.10`
- `suggested_shares` 按现金和价格计算

`range`：

- 可作为 `grid_candidate`
- 趋势策略优先

## 12. 网格策略规则

### 12.1 保留参数

必须保留：

- `grid_unit_pct = 0.08`
- `grid_max_layers = 4`

不得因为多个股票触发网格就直接降低 `grid_unit_pct`。网格风险控制通过“只选择部分股票开网格”实现。

### 12.2 网格启用数量限制

- `market_range`：最多同时启用2只网格股票；如果市场非常稳定，最多3只。
- `market_uptrend`：最多启用1至2只，趋势策略优先。
- `market_transition`：不新开网格。
- `market_downtrend`：不新开网格。

所有 `stock_range` 股票应先进入 `grid_candidate`，经过评分排序后，只有排名靠前的股票才能给出真正可执行的网格建议。

### 12.3 网格候选评分

网格候选评分应体现以下维度：

1. 震荡稳定性，30%
   - `abs(ret20)` 越接近 0 越好。
   - `abs(ma20_slope)` 越接近 0 越好。

2. 区间宽度，20%
   - `range20` 在 8% 至 18% 最优。
   - 太窄利润空间不足。
   - 太宽可能不是震荡而是混乱。

3. 波动适中，20%
   - `vol20` 在 1.5% 至 5% 最优。

4. 当前位置，15%
   - `close` 靠近 `grid_mid` 或低于 `grid_mid` 更优。
   - `close` 接近 `grid_upper` 不适合新开。

5. 流动性，10%
   - 如果有换手率字段，换手率 1% 至 8% 更优。
   - 如果没有换手率字段，则跳过该项并归一化权重。

6. 行业分散，5%
   - 如果有行业字段，避免启用的网格股票过度集中在同一行业。
   - 如果没有行业字段，则跳过该项并归一化权重。

### 12.4 网格优先条件

网格优先选择条件：

- `abs(ret20) <= 5%`
- `range20` 在 8% 至 18%
- `vol20` 在 1.5% 至 5%
- `abs(ma20_slope) <= 2%`
- `close <= grid_mid` 或接近 `grid_mid`
- 市场不是 `transition` 或 `downtrend`

### 12.5 不适合开网格

以下情况不适合新开网格：

- `range20` 太窄
- `range20` 太宽
- `vol20` 太高
- `ma20_slope` 明显向下
- `close` 接近区间上沿
- `market_downtrend`
- `market_transition`

### 12.6 未入选网格的 range 股票

未入选网格启用名单的 `range` 股票：

- `action = observe`
- `strategy_family = grid_candidate` 或保持为兼容前端的 `grid`
- `executable = False`
- `reason` 中说明：“符合震荡条件，但网格优先级不足，未进入本轮启用名单。”

## 13. 股票池强弱

系统应增加股票池强弱判断，但该判断不得直接决定买卖，只能用于排序、优先级和仓位微调。

### 13.1 指标

股票池强弱指标包括：

- `pool_above_ma20_ratio`：股票池中 `close > ma20` 的比例
- `pool_uptrend_count`
- `pool_downtrend_count`
- `pool_ret20`：股票池等权20日收益
- `pool_avg_vol20`

### 13.2 状态

- `pool_strong`：至少60%的股票 `close > ma20`
- `pool_neutral`：40%至60%的股票 `close > ma20`
- `pool_weak`：低于40%的股票 `close > ma20`
- `pool_chaotic`：涨跌分化大，且 `pool_avg_vol20` 较高

### 13.3 用途

- `pool_strong`：趋势候选优先级上调。
- `pool_neutral`：不调整。
- `pool_weak`：趋势候选优先级下调，试探仓优先于正常仓。
- `pool_chaotic`：减少新买，更多 `observe`。

股票池强弱不得覆盖市场防守规则，不得让 `market_downtrend` 下的非持仓股票获得新买仓位。

## 14. 止损和目标价

当前固定止损和目标价可保留为 fallback：

- `stop_price = close * 0.92`
- `target_price = close * 1.18`

但趋势策略应优先使用波动率自适应方式：

- 如果能计算 `ATR20`：
  - `stop_pct = clamp(2 * ATR20 / close, 6%, 12%)`
  - `target_pct = 2 * stop_pct`
  - `stop_price = close * (1 - stop_pct)`
  - `target_price = close * (1 + target_pct)`

- 如果不能计算 `ATR20`：
  - 使用固定 fallback。

止损和目标价必须保留在现有输出字段中，不新增前端必需字段。

## 15. 输出兼容性

必须保持 `REQUIRED_ADVICE_COLUMNS` 中所有字段兼容，不得删除或改名。

可以在 `reason` 和 `risk_note` 中加入更多解释，包括：

- 市场状态证据
- 个股状态证据
- 股票池强弱证据
- `ret20`、`ret60`、`ma20`、`ma60`、`range20`、`vol20`、`trend_strength`
- 为什么是试探仓
- 为什么不允许买
- 为什么是 `grid_candidate` 但没有启用
- 现金不足以买入一手时的说明

`executable` 字段规则：

- `buy`、`add`、`trial_buy`、`sell`：如果确实允许执行，则为 `True`
- `observe`、`blocked`、`wait_confirm`：必须为 `False`
- 未启用的 `grid_candidate`：必须为 `False`
- 已启用的网格建议：可以为 `True`，或按现有系统习惯设置，但必须在说明中清楚表达

## 16. 保持不变

本次优化必须保持：

- 现有前端可继续读取恒温器输出表。
- `REQUIRED_ADVICE_COLUMNS` 不被破坏。
- 账户现金、持仓、交易流水、买入、卖出、成本调整逻辑不变。
- 股票池来源、龙虎榜、自选组合、市场范围等输入入口不被重写。
- 现有数据源调用方式不被替换。
- 现有回测诊断页面和账户页面不因本次规格被整体重构。
- 策略仍只生成建议，不自动下单。

## 17. 验收标准

满足以下条件才视为规格完成：

1. 市场状态和个股状态不再共用同一套阈值。
2. 默认市场基准不再只依赖 `000001.SH`。
3. 当某个默认指数不可用时，系统跳过该指数并重新归一化可用指数权重。
4. 当所有默认指数不可用时，系统回退到候选池聚合逻辑，不崩溃。
5. 沪深300、中证1000、创业板指同时 `downtrend` 时，市场进入防守状态，非持仓股票不允许新买。
6. 少于60个交易日数据不会给出 `buy`、`add` 或可执行网格建议。
7. 60至120个交易日数据最多只能给出试探仓或降级建议。
8. `market_downtrend` 下，非持仓股票不会出现 `suggested_position_pct > 0`。
9. 不再出现 `suggested_position_pct > 0` 但 `suggested_shares = 0` 且无原因说明的结果。
10. 现金不足以买入一手时，`reason` 或 `risk_note` 明确说明“现金不足以买入一手”。
11. `range` 股票不会全部自动进入网格，而是先进入 `grid_candidate` 并经过评分排序。
12. `market_range` 下默认最多启用2只网格股票；市场非常稳定时最多3只。
13. `market_transition` 和 `market_downtrend` 下不新开网格。
14. `grid_unit_pct` 仍为 8%。
15. `grid_max_layers` 仍为 4。
16. `uptrend` 和 `strong_uptrend` 使用中期趋势逻辑。
17. `range` 使用短期网格逻辑。
18. `transition` 默认观察，不交易。
19. `downtrend` 对持仓进入风控，对非持仓禁买或观察。
20. 股票池强弱只影响排序、优先级和仓位微调，不直接决定买卖。
21. `pool_weak` 或 `pool_chaotic` 不得覆盖市场防守规则。
22. 趋势策略优先使用 ATR20 自适应止损和目标价；无法计算时使用固定 fallback。
23. `REQUIRED_ADVICE_COLUMNS` 保持兼容。
24. 前端结果页仍能显示市场概览、持仓建议、新买候选、网格建议、趋势建议和错误表。
25. `reason` 和 `risk_note` 能解释每个建议的核心原因。
26. 用户选择较短日期范围时，系统不得误导用户认为能生成完整买入、加仓或网格建议。

## 18. Review/Brainstorming 自审结论

- 用户问题已准确表达：核心不是重写项目，而是在保持兼容的前提下优化恒温器策略判断、路由、仓位和网格筛选。
- 验收标准可测试：每条标准均可通过单元测试、策略输出表检查或页面结果检查验证。
- 未混入过早实现细节：规格允许独立函数或 mode 参数两种形态，不限定具体内部拆分方式。
- 不做范围明确：不改账户、不改数据源、不重写前端、不自动交易、不破坏输出列。
- 已明确关键歧义：市场基准使用组合指数，沪深300仅作为风险锚；股票池强弱只用于排序和微调，不直接决定买卖。

## 19. Review/Brainstorming 审查修订

本节用于消除规格中可能影响后续计划和验收的含糊点，不新增实现方案。

### 19.1 用户问题表达审查

用户问题已准确表达为“优化恒温器策略逻辑”，不是“新增一个新策略”或“重写整个系统”。规格必须继续围绕以下目标展开：

- 保留现有数据结构、输出表结构和前端兼容性。
- 优化市场判断、个股判断、股票池强弱、仓位建议和网格筛选。
- 策略仍只输出建议，不自动交易。

### 19.2 状态值和前端兼容性澄清

规格中出现的 `market_uptrend`、`market_range`、`market_downtrend`、`market_transition` 是市场判断语义。最终输出必须满足以下要求之一：

- 前端能够正常显示这些新状态值，并且不出现“未翻译字段”或裸英文状态值；或
- 在输出给前端前映射为当前兼容的状态值，同时在 `reason` 或 `risk_note` 中保留市场判断证据。

个股新增的 `strong_uptrend`、`grid_candidate`、`trial_buy` 等状态或动作也必须满足同样要求：可以作为策略语义存在，但不得破坏现有页面展示、执行计划生成或测试中依赖的输出表结构。

如果第一阶段不扩展前端动作枚举，`trial_buy` 应以兼容方式表达为：

- `action = buy`
- `strength = reduced`
- `reason` 明确包含“试探仓”
- 仓位比例和建议股数按试探仓规则计算

### 19.3 验收标准可测试性修订

以下描述必须按可测试规则理解：

- “市场非常稳定”定义为：市场状态为 `market_range`，且 `range60 <= 10%`、`vol20 <= 1.5%`、`abs(ma60_slope) <= 1%`。
- `market_range` 下默认最多启用 2 只网格股票；只有满足“市场非常稳定”时最多启用 3 只。
- `market_uptrend` 下默认最多启用 1 只网格股票；如果没有任何可执行趋势买入或加仓候选，最多可启用 2 只网格股票。
- “close 接近 grid_mid”定义为：`abs(close / grid_mid - 1) <= 3%`。
- “close 接近 grid_upper”定义为：`close` 位于 `grid_mid` 至 `grid_upper` 区间的上方 25% 区域内。
- “range20 太窄”定义为：`range20 < 8%`。
- “range20 太宽”定义为：`range20 > 18%`；如果 `range20 > 30%`，优先视为 `transition`，不得作为可执行网格。
- “vol20 太高”定义为：`vol20 > 5%`；如果有 252 日数据且 `vol20_percentile_252 >= 80%`，也视为波动过高。

### 19.4 股票池强弱可测试性修订

股票池强弱状态按以下优先级和阈值解释：

1. `pool_chaotic` 优先级最高：股票池内个股 `ret20` 标准差较高，且 `pool_avg_vol20 >= 4%`。若无法计算 `ret20` 标准差，则不得仅凭缺失数据判定为 chaotic。
2. `pool_strong`：`pool_above_ma20_ratio >= 60%`。
3. `pool_neutral`：`40% <= pool_above_ma20_ratio < 60%`。
4. `pool_weak`：`pool_above_ma20_ratio < 40%`。

股票池强弱只能调整排序、优先级和仓位微调，不得覆盖以下硬规则：

- `market_downtrend` 下非持仓禁买。
- 少于60个交易日数据不允许买入、加仓或开网格。
- `transition` 默认观察。
- 现金不足以买入一手时不得给出无解释的正仓位建议。

### 19.5 仓位和建议股数一致性修订

如果按仓位比例计算出的建议股数不足 100 股：

- `suggested_shares = 0`
- `suggested_position_pct` 可以保留原始意图比例，但必须在 `reason` 或 `risk_note` 中明确说明“现金不足以买入一手”；或
- 将 `suggested_position_pct` 同步降为 0，并在说明中提示现金不足。

两种方式均可接受，但同一版本中必须保持一致，不得在不同分支里混用。

### 19.6 不做范围复核

本规格不要求：

- 新增页面参数配置面板。
- 修改账户资金、持仓、交易流水含义。
- 新增或替换行情数据源。
- 批量重构前端页面。
- 新增真实交易执行能力。
- 建立参数优化或机器学习调参系统。

### 19.7 审查结论

- 用户问题表达准确。
- 验收标准已补充为更具体、可测试的阈值。
- 规格中保留的函数名和字段名只用于约束行为和兼容性，不要求固定实现路径。
- 不做范围明确。
- 已修正“非常稳定”“接近”“太宽/太窄”“1-2只”“pool_chaotic”等含糊描述。
