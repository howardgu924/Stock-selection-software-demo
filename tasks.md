# 优化汉化任务清单

> 基于当前 `spec.md` 和 `plan.md`。本文件只列任务、验证点、涉及文件和执行顺序，不写代码，不开始实现。

## 执行原则

- 每个任务先写测试或明确验证点，再做最小改动。
- 每个任务必须能独立验证。
- 汉化只发生在 Web 用户可见展示层。
- 不重命名内部字段名，不改 DataFrame 原始列名，不改持久化数据结构。
- 不改信息渠道、策略逻辑、账户逻辑、回测逻辑、股票池逻辑、执行辅助逻辑和 CLI 兼容性。
- 不做全局机械替换。
- 测试不得写成“页面不能包含任何英文字符”，因为股票代码、数据源品牌名、路径、URL、命令行参数和外部异常原文允许保留英文。

## 阶段 0：范围和基线

### T001：核对规格、方案和任务范围

涉及文件：
- `spec.md`
- `plan.md`
- `tasks.md`

先做验证：
- `spec.md` 的验收标准在本任务清单中都有对应任务。
- `plan.md` 的推荐实施顺序在本任务清单中都有对应阶段。
- 本任务清单不包含策略重写、字段重命名、provider 替换、账户模型重写、CLI 重写或全局机械替换。

实施范围：
- 只调整任务覆盖和顺序。

验收：
- 每个任务足够小。
- 每个任务有独立验证方式。
- 任务顺序先验证、再改展示、最后回归。

### T002：记录当前测试基线

涉及文件：
- `tests`
- `examples/web_app.py`

先做验证：
- 运行当前测试，确认进入实现前的基线状态。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`
- `.\.venv\Scripts\python.exe -m pytest tests\test_thermostat_strategy.py tests\test_thermostat_backtest.py tests\test_execution.py -q`
- `.\.venv\Scripts\python.exe -m pytest tests\test_stock_pools.py tests\test_pool_market_ranges.py tests\test_pool_lhb.py tests\test_lhb_candidates.py -q`
- `.\.venv\Scripts\python.exe -m pytest tests\test_portfolio_journal.py tests\test_watchlist_store.py -q`

实施范围：
- 不修改代码。
- 只记录当前通过或失败情况。

验收：
- 如果基线通过，后续任务不得引入回归。
- 如果基线失败，记录失败测试名、失败原因和是否与本次汉化有关。

### T003：盘点正常 Web 路径英文泄漏清单

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`
- 新建或更新：`docs/localization-leak-checklist.md`

先做验证：
- 从现有渲染路径盘点用户可见英文标题、列名、枚举值、进度阶段和错误文案。
- 区分必须汉化和允许保留英文。

检查范围：
- 恒温器策略结果。
- 龙虎榜候选和 Top N 结果。
- 回测诊断结果。
- 账户概览、持仓、交易流水、自选组合。
- 进度条和失败提示。

实施范围：
- 只记录清单，不改页面代码。
- 清单按“必须汉化”和“允许保留英文”分组。
- 每个必须汉化项记录来源页面、当前英文、期望中文、对应后续任务编号。

验收：
- 明确列出必须覆盖的英文标题和字段来源。
- 明确列出允许保留英文的内容，例如股票代码、数据源品牌名、路径、URL、外部异常原文。
- `docs/localization-leak-checklist.md` 存在，且可直接用于后续测试和映射补齐。

## 阶段 1：先写失败测试锁定英文泄漏

### T004：为恒温器结果区标题汉化写 Web 测试

涉及文件：
- `tests/test_web_app.py`

先写测试：
- 恒温器结果页面不显示 `Stock Pool Summary`、`Market Overview`、`Holding Advice`、`New Buy Candidates`、`Grid Advice`、`Trend Advice`、`Execution Plan`、`Errors`。
- 页面显示“股票池摘要”“市场概览”“持仓建议”“新买候选”“网格建议”“趋势建议”“手工执行计划”“错误”。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

预期：
- 如果当前恒温器结果区仍有英文标题泄漏，测试应先失败。

验收：
- 测试能准确抓到恒温器结果区标题未汉化问题。
- 测试不禁止股票代码、路径或数据源品牌名。

### T005：为龙虎榜、回测和账户结果区标题汉化写 Web 测试

涉及文件：
- `tests/test_web_app.py`

先写测试：
- 龙虎榜结果标题显示“龙虎榜前 20 名”“龙虎榜前 30 名”“龙虎榜前 50 名”。
- 回测诊断和账户结果不显示 `Summary`、`Diagnostics`、`Trades`、`Positions` 等规格列出的英文区块标题。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

预期：
- 如果当前龙虎榜、回测或账户结果区仍有英文标题泄漏，测试应先失败。

验收：
- 测试能准确抓到龙虎榜、回测和账户结果区标题未汉化问题。
- 测试不禁止股票代码、路径或数据源品牌名。

### T006：为表格列名汉化写 Web 测试

涉及文件：
- `tests/test_web_app.py`

先写测试：
- 页面不直接显示 `watchlist_name`、`time_range`、`source_detail`、`market_regime`、`confidence`、`data_source`、`data_sufficient`。
- 页面显示“自选组合名称”“时间范围”“来源说明”“市场状态”“置信度”“数据来源”“数据是否充足”。
- 执行计划表格不直接显示 `recommended_action`、`fallback_action`、`limit_status`、`volume_limit_pct`、`skip_insufficient_cash`、`skip_volume_limit`。
- 执行计划表格显示“推荐操作”“备选操作”“涨跌停状态”“成交量限制比例”“资金不足跳过”“成交量限制跳过”。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

预期：
- 如果当前列名仍直接使用内部字段名，测试应先失败。

验收：
- 测试覆盖规格第 7 节的关键字段。
- 测试只检查用户页面，不要求内部 DataFrame 改中文列名。

### T007：为状态值和枚举值汉化写 Web 测试

涉及文件：
- `tests/test_web_app.py`

先写测试：
- 页面中的 `range` 显示为“震荡区间”。
- 页面中的 `uptrend` 显示为“上升趋势”。
- 页面中的 `downtrend` 显示为“下降趋势”。
- 页面中的 `trend_following` 显示为“趋势跟随”。
- 页面中的 `grid` 显示为“网格策略”。
- 页面中的 `observe` 显示为“观察”。
- 页面中的 `buy` 显示为“买入”。
- 页面中的 `wait_confirm` 显示为“等待确认”。
- 布尔值在用户页面显示为“是”或“否”。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

预期：
- 如果用户页面仍直接显示内部枚举值，测试应先失败。

验收：
- 测试覆盖市场状态、策略类型、建议动作和布尔值。
- 测试不要求内部策略结果枚举值改成中文。

### T008：为未知字段中文兜底写渲染测试

涉及文件：
- `tests/test_web_app.py`

先写测试：
- 当用户可见表格出现未登记列名时，页面显示“未翻译字段：原字段名”或等价中文提示。
- 未知字段不应静默以纯英文列名展示。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

预期：
- 当前如果未知字段直接回落为英文列名，测试应先失败。

验收：
- 漏翻字段可见、可定位、可继续补映射。

### T009：为进度提示汉化写 Web 测试

涉及文件：
- `tests/test_web_app.py`

先写测试：
- 进度展示不直接显示内部 `stage`。
- 进度标题显示中文阶段名，例如“正在获取龙虎榜候选”“正在加载候选股历史”“正在评估恒温器”“正在生成手工执行计划”。
- 有总数时显示“已完成 x / y”。
- 有当前股票时显示当前处理股票。
- 失败时显示中文失败摘要。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

预期：
- 如果进度仍直接暴露内部 stage，测试应先失败。

验收：
- 进度条和小字都有中文用户可读信息。

### T010：为错误、警告和空状态中文摘要写 Web 测试

涉及文件：
- `tests/test_web_app.py`

先写测试：
- 股票池为空显示中文摘要。
- 自选组合不存在或为空显示中文摘要。
- 龙虎榜候选为空或抓取失败显示中文摘要。
- 账户未初始化显示中文摘要。
- 当前无持仓显示“暂无持仓”。
- 当前无交易流水显示“暂无交易流水”。
- 外部异常原文如果显示，必须位于中文摘要之后。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

预期：
- 如果页面只显示英文异常或空状态不清楚，测试应先失败。

验收：
- 错误、警告和空状态能告诉用户下一步可以做什么。

### T011：运行汉化测试锁定阶段验证

涉及文件：
- `tests/test_web_app.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 新增测试能覆盖标题、列名、枚举值、未知字段、进度、错误和空状态。
- 当前失败点能对应到具体汉化缺口。

## 阶段 2：标题和列名展示汉化

### T012：补齐恒温器结果区标题映射

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`

先做验证：
- 运行 T004 对应测试，确认恒温器标题汉化缺口。

实施范围：
- 只补齐恒温器结果区标题映射。
- 覆盖股票池摘要、市场概览、持仓建议、新买候选、网格建议、趋势建议、手工执行计划和错误区。
- 不改 `TableBlock` 的业务含义。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- T004 中列出的恒温器英文标题不再直接显示。
- 对应中文标题全部显示。

### T013：补齐龙虎榜、回测和账户结果区标题映射

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`

先做验证：
- 运行 T005 对应测试，确认龙虎榜、回测和账户标题汉化缺口。

实施范围：
- 只补齐龙虎榜、回测诊断、账户、持仓、交易流水、自选组合等结果区标题映射。
- 不改 `TableBlock` 的业务含义。
- 不改回测、账户或龙虎榜计算逻辑。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- T005 中列出的英文标题不再直接显示。
- 对应中文标题全部显示。

### T014：统一结果区标题渲染出口

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`

先做验证：
- 用测试确认新增或已有 `TableBlock` 标题都经过中文展示。

实施范围：
- 只调整 Web 渲染出口。
- 不改路由、业务处理或数据结构。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 所有用户可见结果区标题都走统一汉化出口。
- 未登记标题不静默裸露英文。

### T015：补齐关键表格列名映射

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`

先做验证：
- 运行 T006 对应测试，确认列名汉化缺口。

实施范围：
- 只补齐 `spec.md` 第 7 节列出的关键列名映射。
- 优先覆盖恒温器、股票池摘要、市场概览、执行计划、回测、账户表格。
- 不改内部 DataFrame 原始列名。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 用户页面不再直接显示规格列出的英文字段名。
- 内部测试仍可使用英文键名。

### T016：统一表格列名渲染出口

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`

先做验证：
- 用渲染测试确认所有表格列名都经过中文展示。

实施范围：
- 只调整表格渲染出口。
- 不改表格数据来源。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 表格列名统一映射。
- 未知字段使用中文兜底提示。

### T017：统一摘要字段名渲染出口

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`

先做验证：
- 用摘要渲染测试确认摘要字段名能转为中文。

实施范围：
- 只调整摘要展示。
- 不改摘要数据结构。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 结果摘要不直接显示内部字段名。
- 摘要字段和值的展示与表格展示规则一致。

### T018：运行标题和列名阶段回归

涉及文件：
- `tests/test_web_app.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 标题、表格列名、摘要字段名相关测试通过。
- 未触及策略、账户和回测计算逻辑。

## 阶段 3：状态值、动作值和执行计划值汉化

### T019：补齐状态值和枚举值映射

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`

先做验证：
- 运行 T007 对应测试，确认枚举值汉化缺口。

实施范围：
- 只补齐用户可见枚举值映射。
- 覆盖市场状态、个股状态、策略类型、建议动作、置信度、布尔值、股票池来源、数据来源。
- 不改内部枚举值。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 页面显示“震荡区间”“趋势跟随”“观察”“买入”“等待确认”等中文值。
- 策略层返回的原始枚举值保持不变。

### T020：统一表格单元格值渲染出口

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`

先做验证：
- 用 Web 测试确认同一个枚举值在不同表格中显示一致。

实施范围：
- 只调整用户页面单元格显示。
- 不改 DataFrame 内容。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 枚举值展示统一。
- 股票代码、路径、URL、数据源品牌名保留原样。

### T021：补齐执行计划动作值汉化

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`
- `tests/test_execution.py`

先写或确认测试：
- `recommended_action` 显示为中文推荐操作。
- `fallback_action` 显示为中文备选操作。
- `limit_status` 显示为中文涨跌停状态。
- 资金不足、涨停、成交量限制、报价缺失等情况有中文摘要或中文动作说明。
- 执行计划计算结果行数和原始动作值不变。

实施范围：
- 只调整执行计划展示。
- 不改 `build_execution_plan` 的计算逻辑和输出结构。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_execution.py -q`

验收：
- 执行计划用户页面可读。
- 执行辅助计算结果不变。

### T022：运行状态值阶段回归

涉及文件：
- `tests/test_web_app.py`
- `tests/test_execution.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_execution.py -q`

验收：
- 枚举值、动作值和执行计划值汉化测试通过。
- 执行辅助回归通过。

## 阶段 4：进度、错误、警告和空状态

### T023：汉化进度阶段标题

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`

先做验证：
- 运行 T009 对应测试，确认内部 stage 泄漏点。

实施范围：
- 只调整进度展示映射。
- 不改变后台任务状态结构和执行顺序。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 进度标题显示中文阶段名。
- 内部 stage 不直接作为用户可见文本。

### T024：汉化进度小字和处理数量

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`

先写或确认测试：
- 有 `completed` 和 `total` 时显示“已完成 x / y”。
- 有 `current_symbol` 时显示当前处理股票。
- 有中文 `node` 时优先显示节点说明。
- 无中文 `node` 时使用中文阶段名兜底。

实施范围：
- 只调整进度小字渲染。
- 不改变任务进度 payload 产生逻辑。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 用户能看到当前阶段、当前股票和处理数量。

### T025：汉化错误和警告摘要

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`

先做验证：
- 运行 T010 对应测试，确认错误和警告英文泄漏点。

实施范围：
- 只调整用户可见错误摘要和警告摘要。
- 原始异常详情保留在中文摘要之后。
- 不改底层异常类型或数据源错误处理。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 用户看到中文摘要。
- 排查仍可看到原始异常详情。

### T026：汉化空状态和下一步提示

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`

先写或确认测试：
- 股票池为空、自选组合为空、龙虎榜为空、账户未初始化、无持仓、无交易流水、无可执行买入候选都有中文空状态。
- 空状态提供下一步建议。

实施范围：
- 只调整页面文案。
- 不改空状态判断逻辑。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 空状态中文、明确、可操作。

### T027：运行进度和错误阶段回归

涉及文件：
- `tests/test_web_app.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 进度、错误、警告、空状态相关测试通过。

## 阶段 5：页面覆盖和计算结果不变验证

### T028：验证恒温器页面汉化覆盖

涉及文件：
- `tests/test_web_app.py`
- `tests/test_thermostat_strategy.py`
- `tests/test_thermostat_backtest.py`

先做验证：
- 恒温器策略结果页标题、列名、状态值和动作值显示中文。
- 股票代码、数据源品牌名和路径允许保留英文。
- 同一输入下，恒温器候选数量、原始建议动作和计算结果不变。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_thermostat_strategy.py tests\test_thermostat_backtest.py -q`

验收：
- 恒温器页面汉化覆盖通过。
- 恒温器策略计算回归通过。

### T029：验证龙虎榜页面汉化覆盖

涉及文件：
- `tests/test_web_app.py`
- `tests/test_pool_lhb.py`
- `tests/test_lhb_candidates.py`

先做验证：
- 龙虎榜 Top N 标题中文。
- 龙虎榜来源说明、时间范围、候选数量、错误或警告中文。
- 龙虎榜候选池生成、Top N、去重、过滤和排序规则不变。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_pool_lhb.py tests\test_lhb_candidates.py -q`

验收：
- 龙虎榜用户页面中文可读。
- 龙虎榜候选逻辑不变。

### T030：验证回测诊断页面汉化覆盖

涉及文件：
- `tests/test_web_app.py`
- `tests/test_backtest.py`
- `tests/test_thermostat_backtest.py`

先做验证：
- 回测诊断结果区标题中文。
- 回测表格列名中文。
- 回测状态值中文。
- 回测计算逻辑、统计口径和输出数据不变。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_backtest.py tests\test_thermostat_backtest.py -q`

验收：
- 回测诊断页面汉化覆盖通过。
- 回测回归通过。

### T031：验证账户页面汉化覆盖

涉及文件：
- `tests/test_web_app.py`
- `tests/test_portfolio_journal.py`
- `tests/test_watchlist_store.py`

先做验证：
- 账户概览、当前持仓、交易记录、自选组合标题中文。
- 账户表格列名中文。
- 空状态中文。
- 账户初始化、买入、卖出、成本调整、估值刷新、持仓和交易流水逻辑不变。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_portfolio_journal.py tests\test_watchlist_store.py -q`

验收：
- 账户页面汉化覆盖通过。
- 账户和自选组合回归通过。

### T032：验证允许保留英文不会被误伤

涉及文件：
- `tests/test_web_app.py`

先写或确认测试：
- 页面可以显示 `600519.SH` 等股票代码。
- 页面可以显示 `akshare`、`BaoStock`、`Sina`、`JoinQuant` 等数据源品牌名。
- 页面可以显示 `data/user/default` 等路径。
- 外部异常原文可以显示，但必须有中文摘要。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 测试没有过宽禁止英文。
- 合法英文保留，业务标签中文。

### T033：运行页面覆盖阶段回归

涉及文件：
- `tests/test_web_app.py`
- `tests/test_thermostat_strategy.py`
- `tests/test_thermostat_backtest.py`
- `tests/test_pool_lhb.py`
- `tests/test_lhb_candidates.py`
- `tests/test_backtest.py`
- `tests/test_portfolio_journal.py`
- `tests/test_watchlist_store.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_thermostat_strategy.py tests\test_thermostat_backtest.py tests\test_pool_lhb.py tests\test_lhb_candidates.py tests\test_backtest.py tests\test_portfolio_journal.py tests\test_watchlist_store.py -q`

验收：
- 四类页面汉化覆盖通过。
- 计算结果相关回归通过。

## 阶段 6：最终回归和手动检查

### T034：运行核心回归组合

涉及文件：
- `tests/test_web_app.py`
- `tests/test_stock_pools.py`
- `tests/test_pool_market_ranges.py`
- `tests/test_pool_lhb.py`
- `tests/test_lhb_candidates.py`
- `tests/test_watchlist_store.py`
- `tests/test_thermostat_strategy.py`
- `tests/test_thermostat_backtest.py`
- `tests/test_portfolio_journal.py`
- `tests/test_execution.py`
- `tests/test_backtest.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_stock_pools.py tests\test_pool_market_ranges.py tests\test_pool_lhb.py tests\test_lhb_candidates.py tests\test_watchlist_store.py tests\test_thermostat_strategy.py tests\test_thermostat_backtest.py tests\test_portfolio_journal.py tests\test_execution.py tests\test_backtest.py -q`

验收：
- Web、股票池、龙虎榜、自选组合、恒温器、账户、执行辅助和回测全部通过。

### T035：运行完整测试套件

涉及文件：
- `tests`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest -q`

验收：
- 完整测试通过。
- 如存在与本次无关的既有失败，必须记录失败名称、原因和与本次汉化无关的证据。

### T036：手动验证本地 Web 汉化效果

涉及文件：
- `examples/web_app.py`

验证步骤：
- 启动本地 Web：`.\.venv\Scripts\python.exe examples\web_app.py --host 127.0.0.1 --port 8765`
- 验证 HTTP：`Invoke-WebRequest -Uri http://127.0.0.1:8765 -UseBasicParsing -TimeoutSec 10`
- 打开 `http://127.0.0.1:8765`
- 运行恒温器策略，检查结果区标题、表格列名、状态值、动作值是否中文。
- 切换龙虎榜来源，检查 Top N 标题、候选数量、时间范围、进度和错误提示是否中文。
- 打开回测诊断页，检查结果区标题和表格列名是否中文。
- 打开账户页，检查账户概览、当前持仓、交易流水、自选组合是否中文。
- 检查股票代码、数据源品牌名、路径、URL 等允许英文内容仍可正常显示。
- 检查外部异常如果出现，是否有中文摘要。

验收：
- HTTP 返回 200。
- 正常 Web 路径没有规格禁止的英文标题、英文字段名和内部枚举值。
- 允许保留英文的内容没有被误翻译。

## 最终完成定义

- `spec.md` 的验收标准都有对应自动测试或手动验证点。
- 正常 Web 使用路径的标题、列名、状态值、动作值、进度提示、错误提示和空状态完成中文展示。
- 股票代码、数据源品牌名、路径、URL、外部异常原文等允许英文内容保留合理。
- 内部字段名、数据结构、接口语义、持久化结构和 CLI 兼容性保持不变。
- 恒温器策略、龙虎榜候选、手工执行计划、回测诊断和账户计算结果保持不变。
- 核心回归、完整测试和本地 Web 手动验证完成。
