# 工作台 UI 重构任务清单

> 基于当前 `spec.md` 和 `plan.md`。本文件只列任务、验证点、涉及文件和执行顺序，不写代码，不开始实现。

## 执行原则

- 每个任务先写测试或明确验证点，再做最小改动。
- 每个任务必须能独立验证，阶段结束后运行对应回归。
- 优先锁定现有行为不变，再调整页面结构。
- 不重写恒温器策略、回测诊断、账户模型、行情 provider、执行辅助、海龟系统、旧 CLI 或旧筛选引擎。
- Web 正常路径只暴露“恒温器策略 / 回测诊断 / 账户”。
- 旧 CLI、旧筛选引擎、旧海龟源码可以保留，但不能在正常 Web 使用路径中重新出现。
- 页面优化只改变展示、分组、字段可见性和交互路径，不改变计算结果。

## 阶段 0：基线和范围锁定

### T001：确认规格、方案和任务范围一致

涉及文件：
- `spec.md`
- `plan.md`
- `tasks.md`

先做验证：
- `spec.md` 的 27 条验收标准都能在本任务清单中找到对应任务。
- `plan.md` 中的模块影响范围都能在本任务清单中找到对应任务。
- 本任务清单不包含策略重写、provider 替换、账户模型重写、自动下单、删除旧源码等不做范围。

实施范围：
- 只调整 `tasks.md` 的任务覆盖和顺序。

验收：
- 每个任务都足够小，且有独立验证方式。
- 没有把多个页面的大改动塞进一个任务。

### T002：记录当前核心回归基线

涉及文件：
- `tests`
- `examples/web_app.py`

先做验证：
- 运行当前 Web、策略、账户、执行、海龟相关测试，记录改动前是否已有失败。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_thermostat_strategy.py tests\test_thermostat_backtest.py tests\test_turtle_system.py tests\test_portfolio_journal.py tests\test_execution.py -q`

实施范围：
- 不修改业务代码。
- 只记录基线结果，作为后续回归对照。

验收：
- 如果基线通过，后续不得引入回归。
- 如果基线失败，记录失败测试名称、失败原因和是否与本次 UI 重构有关。

## 阶段 1：Web 壳层、导航和旧入口可见性

### T003：为顶部导航和旧入口隐藏写 Web 测试

涉及文件：
- `tests/test_web_app.py`
- `examples/web_app.py`

先写测试：
- 顶部导航只显示“恒温器策略 / 回测诊断 / 账户”三个正常入口。
- Web 默认页不显示旧策略列表。
- Web 默认页不显示旧默认技术筛选入口。
- Web 默认页不显示旧海龟系统入口。

实施范围：
- 只调整 Web 正常路径的导航和入口可见性。
- 不删除旧 CLI、旧筛选引擎或旧海龟源码。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 正常 Web 路径只有三个主入口。
- 旧功能保留在源码中，但页面不可见。

### T004：为统一工作台页面壳层写 Web 测试

涉及文件：
- `tests/test_web_app.py`
- `examples/web_app.py`

先写测试：
- 恒温器策略页有页面标题、状态说明和主要内容区。
- 回测诊断页有页面标题、状态说明和主要内容区。
- 账户页有页面标题、状态说明和主要内容区。
- 三个页面都使用统一的页面容器，不出现内容贴边的基础布局。

实施范围：
- 只建立或整理三个主页面的外层结构。
- 不调整具体表单字段。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 三个主入口都有工作台式外层分区。
- 页面外壳调整不影响各页面原有提交入口。

### T005：为回测诊断页轻量分区写回归测试

涉及文件：
- `tests/test_web_app.py`
- 可能涉及：`tests/test_backtest.py`
- `examples/web_app.py`

先写测试：
- 回测诊断页仍可访问。
- 回测输入、运行入口、结果区域和错误提示仍可见。
- 回测诊断页不重新暴露旧策略列表、旧默认技术筛选或旧海龟入口。
- 已有回测计算测试继续通过。

实施范围：
- 只整理回测诊断页外层布局和视觉层级。
- 不改变回测参数语义、计算过程或输出含义。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_backtest.py -q`

验收：
- 回测诊断页被纳入工作台结构。
- 回测结果语义保持不变。

### T006：运行壳层阶段回归

涉及文件：
- `tests/test_web_app.py`
- `tests/test_backtest.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_backtest.py -q`

验收：
- 顶部导航、旧入口隐藏、三页壳层、回测诊断分区全部通过。

## 阶段 2：恒温器策略页股票池来源动态展示

### T007：为股票池来源基础区写 Web 测试

涉及文件：
- `tests/test_web_app.py`
- `examples/web_app.py`

先写测试：
- 恒温器策略页默认显示股票池来源选择。
- 默认显示当前股票池摘要或空状态。
- 不再一次性铺开所有股票池输入字段。

实施范围：
- 只调整股票池来源区域的基础容器和默认摘要。
- 不改股票池解析规则。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 股票池来源区域可见。
- 非当前来源的字段默认不可见。

### T008：为手动输入入口写 Web 测试

涉及文件：
- `tests/test_web_app.py`
- `examples/web_app.py`

先写测试：
- 选择“手动输入”时，主页面显示“编辑手动股票池”或等价入口。
- 主页面不直接显示普通股票代码输入框。
- 主页面不显示独立的“保存手动股票池”区域。

实施范围：
- 只调整恒温器主页面的手动输入入口。
- 不在此任务内实现账户页自选组合管理。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 手动输入从主页面长输入框改为二级编辑入口。
- 保存职责不再重复出现在恒温器策略页。

### T009：为手动股票池编辑区写解析展示测试

涉及文件：
- `tests/test_web_app.py`
- `tests/test_stock_pools.py`
- `examples/web_app.py`
- `stock_picker/pools.py`

先写测试：
- 编辑区支持逗号、空格、换行分隔股票代码。
- 编辑区显示已识别股票数量。
- 编辑区显示重复代码提示。
- 编辑区显示无效代码提示。
- 编辑区支持“仅本次使用”状态。

实施范围：
- 只接入或展示已有股票池解析结果。
- 不改变股票池最终语义。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_stock_pools.py -q`

验收：
- 手动股票池编辑区可以独立验证解析、数量、重复和错误提示。

### T010：为自选股组合来源写 Web 测试

涉及文件：
- `tests/test_web_app.py`
- `tests/test_watchlist_store.py`
- `examples/web_app.py`
- `stock_picker/user/watchlist.py`

先写测试：
- 选择“自选股组合”时，不显示手动填写组合名称的输入框。
- 页面从账户已有自选组合读取列表。
- 每个组合显示名称和股票数量。
- 无自选组合时显示“暂无自选组合，请到账户页创建”或等价空状态。

实施范围：
- 只让恒温器页读取并选择账户已有组合。
- 不在恒温器页实现创建、删除、重命名、添加、删除股票等完整管理功能。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_watchlist_store.py -q`

验收：
- 自选股组合选择和管理职责分离。

### T011：为市场范围多选 UI 写 Web 测试

涉及文件：
- `tests/test_web_app.py`
- `tests/test_pool_market_ranges.py`
- `examples/web_app.py`
- `stock_picker/pools.py`

先写测试：
- 选择“市场范围”时，市场范围控件支持多选。
- 至少显示沪深 A 股、沪市、深市、创业板、科创板、北交所。
- 用户可以同时选择多个市场范围。
- 页面显示所选范围摘要。
- 未选择市场范围时显示明确空状态或错误提示。
- 大范围选择显示耗时提示。

实施范围：
- 只调整市场范围来源的 UI 和摘要展示。
- 不替换现有股票列表获取语义。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_pool_market_ranges.py -q`

验收：
- 市场范围来源从单选或散乱字段收敛为多选入口。

### T012：为龙虎榜来源时间范围 UI 写 Web 测试

涉及文件：
- `tests/test_web_app.py`
- `tests/test_pool_lhb.py`
- `examples/web_app.py`
- `stock_picker/pools.py`

先写测试：
- 选择“龙虎榜”或“同花顺龙虎榜”时，显示时间范围选择。
- 时间范围包含最近 1 周、最近 1 个月、最近 3 个月、最近半年、最近 1 年、自定义。
- 默认不显示开始日期和结束日期。
- 只有选择“自定义”时才显示开始日期和结束日期。
- 摘要显示真实数据来源、时间范围、股票数量、错误或警告。
- 同花顺不可用时显示真实原因和实际使用来源。

实施范围：
- 只调整龙虎榜来源 UI 和来源说明。
- 不新增伪造的数据源，不把其他来源伪装成同花顺。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_pool_lhb.py -q`

验收：
- 龙虎榜时间字段按条件展示。
- 数据来源说明诚实可追溯。

### T013：运行股票池来源阶段回归

涉及文件：
- `tests/test_web_app.py`
- `tests/test_stock_pools.py`
- `tests/test_watchlist_store.py`
- `tests/test_pool_market_ranges.py`
- `tests/test_pool_lhb.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_stock_pools.py tests\test_watchlist_store.py tests\test_pool_market_ranges.py tests\test_pool_lhb.py -q`

验收：
- 四类股票池来源的动态展示、摘要和错误状态全部通过。

## 阶段 3：恒温器策略页日期、现金和高级设置

### T014：为策略日期范围写测试

涉及文件：
- `tests/test_web_app.py`
- 可能涉及：`tests/test_stock_pools.py`
- `examples/web_app.py`

先写测试：
- 恒温器策略页显示“策略日期范围”选择。
- 选项包含最近 1 个月、最近 3 个月、最近半年、最近 1 年、自定义。
- 固定范围时不显示开始日期和结束日期输入框。
- 自定义时才显示开始日期和结束日期输入框。
- 固定范围时页面显示实际使用的日期范围。

实施范围：
- 只调整日期范围 UI 和实际日期展示。
- 不改变策略计算语义。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 用户不需要在固定范围下手动填写日期。

### T015：为可用现金只读展示写测试

涉及文件：
- `tests/test_web_app.py`
- `tests/test_portfolio_journal.py`
- `examples/web_app.py`
- `stock_picker/user/portfolio.py`

先写测试：
- 账户已初始化时，恒温器策略页只读显示当前账户现金。
- 可用现金不作为默认手动输入字段出现。
- 账户未初始化时，显示“账户未初始化，请先到账户页初始化账户”或等价提示。

实施范围：
- 只调整恒温器策略页账户现金展示。
- 不改变账户初始化、现金计算或交易流水逻辑。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_portfolio_journal.py -q`

验收：
- 可用现金从账户状态同步，不再要求用户重复填写。

### T016：为模拟资金临时测算写测试

涉及文件：
- `tests/test_web_app.py`
- `tests/test_portfolio_journal.py`
- `examples/web_app.py`

先写测试：
- 默认不显示模拟资金输入框。
- 启用“使用模拟资金”后才显示模拟资金输入框。
- 页面明确说明模拟资金只用于临时策略测算，不改变账户现金、持仓或交易流水。
- 使用模拟资金后，账户现金、持仓和交易流水保持不变。

实施范围：
- 只调整模拟资金 UI 和提示。
- 不改变账户状态持久化逻辑。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_portfolio_journal.py -q`

验收：
- 模拟资金与真实账户状态边界清晰。

### T017：为低频数据源和执行设置默认收起写测试

涉及文件：
- `tests/test_web_app.py`
- `examples/web_app.py`

先写测试：
- 历史源、股票列表源、实时源、强制刷新默认不全部展开。
- 生成手工执行计划、次日溢价上限、成交量限制默认不全部展开。
- 页面显示“高级设置”“数据与执行设置”或等价入口。
- 展开后这些字段仍可见且可提交。

实施范围：
- 只把低频字段移入折叠区或等价二级入口。
- 不改变默认值和提交含义。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 恒温器策略页默认可见字段明显减少。
- 高级字段仍可通过入口访问。

### T018：为恒温器运行结果不变写回归测试

涉及文件：
- `tests/test_web_app.py`
- `tests/test_thermostat_strategy.py`
- `tests/test_thermostat_backtest.py`
- `examples/web_app.py`

先写测试或确认现有测试：
- UI 重构前后，同一最终股票池和同一策略日期范围下，恒温器策略结果语义不变。
- 持仓建议、新买候选、网格建议和趋势建议来源不变。
- UI 条件展示不改变策略内部判断。

实施范围：
- 只补回归验证或修正 Web 参数传递。
- 不修改恒温器策略内部逻辑。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_thermostat_strategy.py tests\test_thermostat_backtest.py -q`

验收：
- 页面重排不改变恒温器策略计算结果。

### T019：运行恒温器页面阶段回归

涉及文件：
- `tests/test_web_app.py`
- `tests/test_thermostat_strategy.py`
- `tests/test_thermostat_backtest.py`
- `tests/test_portfolio_journal.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_thermostat_strategy.py tests\test_thermostat_backtest.py tests\test_portfolio_journal.py -q`

验收：
- 股票池、日期、现金、模拟资金、高级设置和策略回归全部通过。

## 阶段 4：账户页概览和摘要区

### T020：为账户概览卡片写 Web 测试

涉及文件：
- `tests/test_web_app.py`
- `tests/test_portfolio_journal.py`
- `examples/web_app.py`
- `stock_picker/user/portfolio.py`

先写测试：
- 账户页顶部以卡片形式展示账户概览。
- 概览至少包含本金、现金、持仓市值、总资产、总收益、总收益率、已实现盈亏、浮动盈亏、持仓数量、胜率、盈亏比、最大回撤、佣金率、印花税率。
- 账户未初始化时显示明确空状态和初始化入口。
- 未初始化状态下不显示误导性的零收益卡片。

实施范围：
- 只调整账户概览展示。
- 不改变账户统计计算逻辑。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_portfolio_journal.py -q`

验收：
- 账户概览从分散文字变为整齐卡片。

### T021：为当前持仓摘要写 Web 测试

涉及文件：
- `tests/test_web_app.py`
- `tests/test_portfolio_journal.py`
- `examples/web_app.py`

先写测试：
- 有持仓时，账户页默认显示持仓摘要表或卡片。
- 无持仓时，显示“暂无持仓”或等价空状态。
- 提供“查看全部”入口。

实施范围：
- 只调整当前持仓默认展示。
- 不改变持仓数据来源或估值逻辑。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_portfolio_journal.py -q`

验收：
- 持仓信息默认简洁可扫读。

### T022：为交易流水最近 5 条写 Web 测试

涉及文件：
- `tests/test_web_app.py`
- `tests/test_portfolio_journal.py`
- `examples/web_app.py`

先写测试：
- 账户页默认只展示最近 5 条交易流水。
- 无交易流水时显示“暂无交易流水”或等价空状态。
- 提供“查看全部”入口。
- “查看全部”入口能进入完整表格、弹窗、展开区或二级页面。

实施范围：
- 只调整交易流水默认展示数量和入口。
- 不改变交易流水数据结构或排序语义。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_portfolio_journal.py -q`

验收：
- 账户页不再默认显示完整长交易表。

### T023：运行账户概览阶段回归

涉及文件：
- `tests/test_web_app.py`
- `tests/test_portfolio_journal.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_portfolio_journal.py -q`

验收：
- 账户概览、持仓摘要和交易流水摘要全部通过。

## 阶段 5：账户功能操作区

### T024：为账户功能 tabs 或等价入口写 Web 测试

涉及文件：
- `tests/test_web_app.py`
- `examples/web_app.py`

先写测试：
- 账户页功能操作区使用 tab、accordion、sidebar、弹窗、抽屉或二级页面组织。
- 至少包含自选组合、账户设置、持仓与估值、买入 / 卖出、成本调整、交易记录这些入口或等价分组。
- 不再把所有功能表单纵向展开在同一个长页面。

实施范围：
- 只建立账户功能操作区结构。
- 不修改各功能内部业务逻辑。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 账户页从长表单变成概览加功能入口。

### T025：为自选组合管理统一在账户页写测试

涉及文件：
- `tests/test_web_app.py`
- `tests/test_watchlist_store.py`
- `examples/web_app.py`
- `stock_picker/user/watchlist.py`

先写测试：
- 账户页支持创建组合。
- 账户页支持删除组合。
- 账户页支持重命名组合。
- 账户页支持添加股票。
- 账户页支持删除股票。
- 账户页支持查看组合内股票。
- 添加股票支持逗号、空格、换行分隔。
- 添加后显示解析结果，重复代码和错误代码有提示。
- 删除自选组合或组合内股票不影响账户持仓、现金、交易流水或历史行情缓存。

实施范围：
- 只把自选组合完整管理放到账户页。
- 恒温器策略页只提供跳转或调用统一保存逻辑，不重复完整管理功能。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_watchlist_store.py tests\test_portfolio_journal.py -q`

验收：
- 自选组合管理职责统一到账户页。

### T026：为账户初始化入口写 Web 测试

涉及文件：
- `tests/test_web_app.py`
- `tests/test_portfolio_journal.py`
- `examples/web_app.py`

先写测试：
- 初始化账户位于“账户设置”或等价入口中。
- 初始化字段包含本金、佣金率、最低佣金、印花税率。
- 初始化后账户概览更新。
- 初始化操作有明确确认或结果提示。

实施范围：
- 只调整初始化账户的入口和展示位置。
- 不改变账户初始化计算或存储逻辑。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_portfolio_journal.py -q`

验收：
- 初始化账户不再混在长页面中。

### T027：为刷新行情 / 更新估值入口写 Web 测试

涉及文件：
- `tests/test_web_app.py`
- `tests/test_portfolio_journal.py`
- `examples/web_app.py`

先写测试：
- 刷新行情 / 更新估值位于“持仓与估值”或等价入口中。
- 字段包含账户路径、标记价格、历史源、股票列表源、实时源、是否强制刷新。
- 刷新后账户概览、当前持仓、持仓市值和浮动盈亏同步更新。

实施范围：
- 只调整估值刷新入口和字段分组。
- 不改变估值刷新、行情抓取或缓存逻辑。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_portfolio_journal.py tests\test_data_service.py -q`

验收：
- 估值刷新入口清晰，账户数据同步行为保持不变。

### T028：为手动买入 / 卖出分组和高级信息折叠写测试

涉及文件：
- `tests/test_web_app.py`
- `tests/test_portfolio_journal.py`
- `examples/web_app.py`

先写测试：
- 买入和卖出位于同一分组中，并通过子 tab、左右分栏或折叠面板区分。
- 常用字段默认展示。
- 策略、系统、原因、信号日、执行日、备注默认放入“高级信息”折叠区。
- 买入和卖出提交后账户交易逻辑保持不变。

实施范围：
- 只调整买入 / 卖出表单布局和高级字段默认状态。
- 不改变买入、卖出、成本和交易流水逻辑。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_portfolio_journal.py -q`

验收：
- 常用买卖操作更集中，高级字段不默认铺开。

### T029：为调整成本低频入口写 Web 测试

涉及文件：
- `tests/test_web_app.py`
- `tests/test_portfolio_journal.py`
- `examples/web_app.py`

先写测试：
- 调整成本位于单独入口、折叠面板或低频 tab 中。
- 默认账户页不让调整成本表单占据大面积页面。
- 进入该功能后，页面明确提示这是会修改持仓成本记录的操作。
- 调整成本提交后账户成本逻辑保持不变。

实施范围：
- 只调整调整成本入口和默认展示状态。
- 不改变成本调整业务逻辑。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_portfolio_journal.py -q`

验收：
- 调整成本作为低频操作被收纳。

### T030：运行账户功能区阶段回归

涉及文件：
- `tests/test_web_app.py`
- `tests/test_watchlist_store.py`
- `tests/test_portfolio_journal.py`
- `tests/test_data_service.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_watchlist_store.py tests\test_portfolio_journal.py tests\test_data_service.py -q`

验收：
- 账户功能入口、自选组合、初始化、估值、买卖、成本调整全部通过。

## 阶段 6：视觉层级、响应式和文档

### T031：为视觉层级和表单布局写 Web 结构测试

涉及文件：
- `tests/test_web_app.py`
- `examples/web_app.py`

先写测试或验证点：
- 页面最大宽度合理，内容不贴边。
- 表单使用统一 grid 布局或等价结构。
- 同一组字段宽度一致。
- checkbox 与文字水平对齐。
- 主操作按钮和次要按钮有可识别区别。
- 按钮位于表单底部或右下方，而不是散落在中间。

实施范围：
- 只调整页面 CSS 和结构类名。
- 不改变表单字段含义。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 页面结构支持视觉层级检查。

### T032：为错误和空状态写 Web 测试

涉及文件：
- `tests/test_web_app.py`
- `examples/web_app.py`

先写测试：
- 账户未初始化有明确提示和下一步入口。
- 无自选组合有明确空状态。
- 自选组合为空有明确空状态。
- 手动股票池为空有明确错误。
- 市场范围未选择有明确错误。
- 龙虎榜为空或失败有明确提示。
- 无持仓和无交易流水有明确空状态。

实施范围：
- 只补空状态和错误提示文案。
- 不改变底层错误类型或业务规则。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 错误和空状态告诉用户下一步可以做什么。

### T033：为窄屏可用性做结构验证

涉及文件：
- `tests/test_web_app.py`
- `examples/web_app.py`

先写测试或手动验证点：
- 卡片和表单字段在窄屏下可换行。
- 按钮仍可见。
- 关键文本不与输入框、按钮或下一段内容重叠。
- 长表单通过折叠、tabs、弹窗、抽屉或二级入口收纳。

实施范围：
- 只调整响应式 CSS 和结构。
- 不改变业务交互。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 窄屏下页面可用，没有明显重叠或按钮不可见。

### T034：更新 README 的正常 Web 使用说明

涉及文件：
- `README.md`
- 可能涉及：`tests/test_docs.py`

先写验证：
- README 说明 Web 正常入口仍是恒温器策略、回测诊断、账户。
- README 说明自选组合管理统一在账户页。
- README 说明恒温器策略页的手动股票池保存路径。
- README 说明模拟资金只影响临时策略测算，不改变账户。
- README 说明旧 CLI、旧筛选引擎、旧海龟源码保留但不在正常 Web 主流程展示。

实施范围：
- 只更新文档和文档测试。
- 不改代码。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_docs.py -q`

验收：
- 文档与 `spec.md`、`plan.md` 和实际 Web 行为一致。

### T035：运行视觉和文档阶段回归

涉及文件：
- `tests/test_web_app.py`
- `tests/test_docs.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_docs.py -q`

验收：
- 视觉结构、空状态、窄屏结构和文档全部通过。

## 阶段 7：最终回归和手动验证

### T036：运行核心回归组合

涉及文件：
- `tests/test_web_app.py`
- `tests/test_stock_pools.py`
- `tests/test_pool_market_ranges.py`
- `tests/test_pool_lhb.py`
- `tests/test_watchlist_store.py`
- `tests/test_thermostat_strategy.py`
- `tests/test_thermostat_backtest.py`
- `tests/test_portfolio_journal.py`
- `tests/test_execution.py`
- `tests/test_turtle_system.py`
- `tests/test_data_service.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_stock_pools.py tests\test_pool_market_ranges.py tests\test_pool_lhb.py tests\test_watchlist_store.py tests\test_thermostat_strategy.py tests\test_thermostat_backtest.py tests\test_portfolio_journal.py tests\test_execution.py tests\test_turtle_system.py tests\test_data_service.py -q`

验收：
- Web、股票池、自选组合、恒温器、账户、执行辅助、海龟系统和数据服务全部通过。

### T037：运行完整测试套件

涉及文件：
- `tests`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest -q`

验收：
- 完整测试通过。
- 如存在与本次无关的既有失败，必须记录失败名称、原因和与本次改动无关的证据。

### T038：手动验证本地 Web 正常路径

涉及文件：
- `examples/web_app.py`

验证步骤：
- 启动本地 Web：`.\.venv\Scripts\python.exe examples\web_app.py --host 127.0.0.1 --port 8765`
- 验证 HTTP：`Invoke-WebRequest -Uri http://127.0.0.1:8765 -UseBasicParsing -TimeoutSec 10`
- 打开 `http://127.0.0.1:8765`
- 检查顶部只有“恒温器策略 / 回测诊断 / 账户”。
- 检查恒温器策略页只显示当前股票池来源相关字段。
- 检查手动股票池通过二级入口编辑。
- 检查自选组合来源从账户已有组合读取。
- 检查市场范围可多选。
- 检查龙虎榜日期只在自定义时显示开始/结束日期。
- 检查策略日期只在自定义时显示开始/结束日期。
- 检查账户现金只读展示，模拟资金只在启用后显示。
- 检查高级数据源和执行设置默认收起。
- 检查账户页顶部为概览卡片。
- 检查账户页功能操作区不是长表单堆叠。
- 检查交易流水默认最近 5 条并有查看全部入口。
- 检查手动买入 / 卖出高级字段默认收起。
- 检查调整成本不默认占据大面积页面。
- 检查页面没有旧策略列表、旧默认技术筛选或旧海龟系统入口。

验收：
- HTTP 返回 200。
- 正常路径符合 `spec.md` 的 27 条验收标准。
- 没有发现明显重叠、按钮不可见或长表单无限堆叠。

## 最终完成定义

- `spec.md` 的 27 条验收标准都有对应自动测试或手动验证点。
- 顶部导航只保留三个正常入口。
- 恒温器策略页实现股票池来源、日期、现金、模拟资金和高级设置的条件展示。
- 账户页实现账户概览、摘要区和功能入口分区。
- 自选组合管理统一在账户页完成。
- 回测诊断页纳入工作台壳层且计算语义不变。
- 旧 CLI、旧筛选引擎、旧海龟源码保留但不在 Web 正常路径展示。
- 恒温器策略、回测诊断、账户交易、执行辅助、海龟系统计算结果不变。
- 核心回归、完整测试和本地 Web 手动验证完成。
