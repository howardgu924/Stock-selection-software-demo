# 自选组合输入规范化与校验任务清单

> 基于当前 `spec.md` 和 `plan.md`。本文件只列任务、验证点、涉及文件和执行顺序，不写代码，不开始实现。

## 执行原则

- 每个任务先写测试或明确验证点，再做最小改动。
- 每个任务必须能独立验证。
- 优先复用现有股票池解析语义，不在 Web 表单里单独写一套校验规则。
- 不改行情渠道、恒温器策略计算、账户资产逻辑、持仓逻辑、交易流水逻辑和旧 CLI。
- 不新增 ETF、基金、可转债等证券类型支持。
- 不静默批量修复历史 `watchlists.json`；第一阶段只识别、提示和阻断异常条目污染策略。
- 不做全局机械替换。

## 阶段 0：范围和基线

### T001：核对规格、方案和任务范围

涉及文件：
- `spec.md`
- `plan.md`
- `tasks.md`

先做验证：
- `spec.md` 的验收标准在本任务清单中都有对应任务。
- `plan.md` 的测试策略在本任务清单中都有对应任务。
- 本任务清单不包含数据源替换、策略重写、账户模型重写、证券类型扩展或历史数据批量静默修复。

实施范围：
- 只调整任务覆盖和顺序。

验收：
- 每个任务足够小。
- 每个任务有独立验证方式。
- 任务顺序先测试、再底层解析、再存储、再 Web、再策略读取、最后回归。

### T002：记录当前测试基线

涉及文件：
- `tests`

先做验证：
- 运行当前相关测试，确认进入实现前的基线状态。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_stock_pools.py -q`
- `.\.venv\Scripts\python.exe -m pytest tests\test_watchlist_store.py -q`
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

实施范围：
- 不修改代码。
- 只记录当前通过或失败情况。

验收：
- 如果基线通过，后续任务不得引入回归。
- 如果基线失败，记录失败测试名、失败原因和是否与本次自选组合输入问题有关。

## 阶段 1：先写失败测试

### T003：为股票池批量分隔符写解析测试

涉及文件：
- `tests/test_stock_pools.py`

先写测试：
- 输入 `600519，000001;300750 603309\n688001` 能按多个 token 处理。
- 结果中不得出现包含逗号、中文逗号、分号、空格或换行的原始整体字符串。
- 合法且当前支持的代码应规范化为统一格式。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_stock_pools.py -q`

预期：
- 如果当前解析只支持部分分隔符，测试应先失败。

验收：
- 测试覆盖英文逗号、中文逗号、空格、换行、分号。

### T004：为无效代码和当前不支持代码写解析测试

涉及文件：
- `tests/test_stock_pools.py`

先写测试：
- 输入 `abc` 时记录为无效，不进入有效股票列表。
- 输入 `516650,515880,515070,159801` 时不得把整段文本作为一个股票。
- 如果拆分后的 5xxxxx 代码当前不支持，应逐项记录为不支持或无法识别，不得原样保存为有效股票。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_stock_pools.py -q`

预期：
- 如果当前只检查 6 位数字或无法区分不支持代码，测试应先失败。

验收：
- 无效输入和当前不支持代码都有可验证的结果。
- 不新增 ETF、基金、可转债支持。

### T005：为手动股票池保存入口一致性写测试

涉及文件：
- `tests/test_web_app.py`

先写测试：
- “从手动股票池保存为自选组合”使用批量输入时，保存结果与股票池解析结果一致。
- 重复代码只保存一次。
- 无效或当前不支持代码不会写入自选组合。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

预期：
- 如果手动保存入口已经正确，测试可以通过；该测试作为后续回归保护。

验收：
- 手动保存入口成为账户页添加入口的对照基线。

### T006：为 WatchlistStore 拒绝原始组合字符串写测试

涉及文件：
- `tests/test_watchlist_store.py`

先写测试：
- 向同一组合添加 `["600519", "000001"]` 时保存为规范化列表。
- 向同一组合添加 `["600519,000001"]` 时不得保存原始整体字符串。
- 重复股票只保存一次，并能反馈重复项。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_watchlist_store.py -q`

预期：
- 如果当前存储层会把原始组合字符串保存进去，测试应先失败。

验收：
- 存储层不再成为无效字符串入口。

### T007：为 WatchlistStore 删除规范化代码写测试

涉及文件：
- `tests/test_watchlist_store.py`

先写测试：
- 已保存 `600519.SH` 时，用户输入 `600519` 可以删除。
- 用户输入不存在或无效代码时，不改变组合内容，并返回可见状态。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_watchlist_store.py -q`

预期：
- 如果删除只支持完全一致字符串或无效输入反馈不足，测试应先失败。

验收：
- 删除入口与保存入口的规范化规则一致。

### T008：为账户页添加股票批量输入写 Web 测试

涉及文件：
- `tests/test_web_app.py`

先写测试：
- 账户页“添加股票”输入 `600519, 000001 300750` 后，保存为多个规范化股票。
- 自选组合表格不显示 `600519, 000001 300750` 这种原始整体字符串。
- 页面结果显示新增数量和最终组合数量。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

预期：
- 当前账户页如果只把输入框内容当作单个 symbol，测试应先失败。

验收：
- 用户截图中的核心入口被测试覆盖。

### T009：为账户页无效和不支持代码反馈写 Web 测试

涉及文件：
- `tests/test_web_app.py`

先写测试：
- 输入 `abc` 时不会写入自选组合，页面显示错误或警告。
- 输入 `516650,515880,515070,159801` 时不得保存整段原始字符串。
- 如果拆分后的代码当前不支持，页面逐项显示“不支持”或“无法识别”的提示。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

预期：
- 如果当前页面静默保存或反馈不足，测试应先失败。

验收：
- 用户在保存前能看到问题，而不是运行策略后才看到 BaoStock 错误。

### T010：为自选组合表格历史异常数据写 Web 测试

涉及文件：
- `tests/test_web_app.py`

先写测试：
- 构造已有 `watchlists.json` 中包含 `516650,515880` 这类历史异常字符串。
- 账户页仍可打开自选组合。
- 自选组合表格显示该组合存在异常代码或需要清理的提示。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

预期：
- 如果当前表格把历史异常字符串当作普通股票展示，测试应先失败。

验收：
- 历史异常数据可见、可定位，不会让用户误以为组合完全可用。

### T011：为恒温器读取异常自选组合写测试

涉及文件：
- `tests/test_web_app.py`
- `tests/test_stock_pools.py`

先写测试：
- 构造自选组合中包含 `600519.SH` 和 `516650,515880`。
- 解析自选组合时只允许有效股票进入结果。
- 恒温器运行或股票池生成时不得把 `516650,515880` 传给行情查询。
- 页面显示异常条目相关警告或错误。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_stock_pools.py tests\test_web_app.py -q`

预期：
- 如果历史异常条目仍进入行情查询，测试应先失败。

验收：
- 策略读取边界受到保护。

### T012：为自选组合操作不影响账户资产写回归测试

涉及文件：
- `tests/test_watchlist_store.py`
- `tests/test_web_app.py`

先写或确认测试：
- 创建组合、添加股票、删除股票、删除组合前后，账户现金不变。
- 持仓不变。
- 交易流水不变。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_watchlist_store.py tests\test_web_app.py -q`

预期：
- 现有测试可能已覆盖部分场景；本任务补齐 Web 入口相关回归。

验收：
- 自选组合仍然不是持仓。

### T013：运行失败测试阶段验证

涉及文件：
- `tests/test_stock_pools.py`
- `tests/test_watchlist_store.py`
- `tests/test_web_app.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_stock_pools.py tests\test_watchlist_store.py tests\test_web_app.py -q`

验收：
- 新增测试覆盖批量输入、分隔符、无效输入、不支持代码、历史异常数据、策略读取边界和账户资产不变。
- 失败点能对应到具体待实现行为。

## 阶段 2：底层解析规则

### T014：统一股票池输入分隔规则

涉及文件：
- `stock_picker/pools.py`
- `tests/test_stock_pools.py`

先做验证：
- 运行 T003 对应测试，确认当前分隔符缺口。

实施范围：
- 只调整股票池输入 token 拆分规则。
- 支持英文逗号、中文逗号、空格、换行、分号。
- 不改变市场范围和龙虎榜股票池逻辑。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_stock_pools.py -q`

验收：
- T003 通过。
- 现有股票池测试继续通过。

### T015：收紧当前支持股票代码判定

涉及文件：
- `stock_picker/pools.py`
- `tests/test_stock_pools.py`

先做验证：
- 运行 T004 对应测试，确认 unsupported code 缺口。

实施范围：
- 只调整股票池有效代码判定。
- 只有能规范化到当前支持市场后缀的代码才能作为有效股票。
- 当前不支持的 5xxxxx 等代码不得伪装成有效股票。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_stock_pools.py -q`

验收：
- T004 通过。
- 不新增 ETF、基金、可转债支持。

### T016：让自选组合解析复用股票池校验结果

涉及文件：
- `stock_picker/pools.py`
- `tests/test_stock_pools.py`

先做验证：
- 运行 T011 中 `tests/test_stock_pools.py` 覆盖的解析测试。

实施范围：
- 只确保 `resolve_watchlist_pool` 对历史异常条目执行同等校验。
- 无效或不支持条目不得进入有效股票列表。
- 保留 warning 或 error 供 Web 展示。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_stock_pools.py -q`

验收：
- 历史异常条目不会进入股票池有效结果。
- 正常自选组合解析仍可用。

### T017：运行股票池解析阶段回归

涉及文件：
- `tests/test_stock_pools.py`
- `tests/test_pool_market_ranges.py`
- `tests/test_pool_lhb.py`
- `tests/test_lhb_candidates.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_stock_pools.py tests\test_pool_market_ranges.py tests\test_pool_lhb.py tests\test_lhb_candidates.py -q`

验收：
- 股票池、市场范围、龙虎榜候选相关测试通过。
- 市场范围和龙虎榜行为没有被本次输入解析修复误伤。

## 阶段 3：自选组合存储边界

### T018：扩展自选组合操作结果信息

涉及文件：
- `stock_picker/user/watchlist.py`
- `tests/test_watchlist_store.py`

先做验证：
- 运行 T006、T007 对应测试，确认当前结果信息缺口。

实施范围：
- 只扩展自选组合操作结果能表达的信息。
- 覆盖新增项、重复项、无效项、当前不支持项和最终组合数量。
- 不改变 `watchlists.json` 的基本结构。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_watchlist_store.py -q`

验收：
- 操作结果足够 Web 层展示。
- 旧组合仍可读取。

### T019：在 WatchlistStore 添加入口拒绝原始组合字符串

涉及文件：
- `stock_picker/user/watchlist.py`
- `tests/test_watchlist_store.py`

先做验证：
- 运行 T006 对应测试，确认原始组合字符串保存问题。

实施范围：
- 只调整添加股票入口。
- 保存前对输入做统一解析、校验、规范化和去重。
- 无效或当前不支持代码不得写入 `watchlists.json`。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_watchlist_store.py -q`

验收：
- T006 通过。
- `watchlists.json` 不再新增原始组合字符串。

### T020：在 WatchlistStore 删除入口统一规范化

涉及文件：
- `stock_picker/user/watchlist.py`
- `tests/test_watchlist_store.py`

先做验证：
- 运行 T007 对应测试，确认删除规范化缺口。

实施范围：
- 只调整删除股票入口。
- 用户输入原始代码或规范化代码都按同一规则匹配。
- 无效输入不改变组合内容。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_watchlist_store.py -q`

验收：
- T007 通过。
- 删除行为不影响其他组合操作。

### T021：运行自选组合存储阶段回归

涉及文件：
- `tests/test_watchlist_store.py`
- `tests/test_portfolio_journal.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_watchlist_store.py tests\test_portfolio_journal.py -q`

验收：
- 自选组合测试通过。
- 账户现金、持仓和交易流水相关回归通过。

## 阶段 4：Web 账户页反馈

### T022：账户页添加股票使用统一解析结果

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`

先做验证：
- 运行 T008、T009 对应测试，确认账户页添加入口缺口。

实施范围：
- 只调整 `/watchlist-add-symbol` 表单处理。
- 添加入口使用与手动股票池保存一致的拆分、校验、规范化和去重语义。
- 不改创建、重命名、删除组合的业务语义。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- T008、T009 通过。
- 账户页不会保存原始多代码字符串。

### T023：账户页添加股票显示操作反馈

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`

先做验证：
- 运行 T008、T009 对应测试，确认页面反馈缺口。

实施范围：
- 只调整自选组合操作结果展示。
- 显示新增数量、重复数量、无效或不支持代码、最终组合数量。
- 全部无效时显示明确错误或警告。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 用户保存前后能知道哪些代码被接受、跳过或拒绝。

### T024：自选组合表格展示历史异常提示

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`

先做验证：
- 运行 T010 对应测试，确认历史异常展示缺口。

实施范围：
- 只调整自选组合表格展示或表格旁提示。
- 历史异常条目可见且提示清理。
- 不静默修改历史 `watchlists.json`。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- T010 通过。
- 正常组合展示不受影响。

### T025：账户页表单文案提示支持批量输入

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`

先做验证：
- 检查账户页“添加股票”输入项是否告诉用户支持逗号、空格、换行等分隔。

实施范围：
- 只调整页面说明文字或输入提示。
- 不改变表单布局结构。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 用户能从页面知道可批量输入。
- 页面说明不声称支持当前未支持的证券类型。

### T026：运行 Web 账户页阶段回归

涉及文件：
- `tests/test_web_app.py`
- `tests/test_watchlist_store.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_watchlist_store.py -q`

验收：
- 账户页自选组合相关测试通过。
- 存储层测试继续通过。

## 阶段 5：恒温器读取防护

### T027：阻断异常自选组合条目进入恒温器行情查询

涉及文件：
- `examples/web_app.py`
- `stock_picker/pools.py`
- `tests/test_web_app.py`
- `tests/test_stock_pools.py`

先做验证：
- 运行 T011 对应测试，确认异常条目是否仍进入行情查询。

实施范围：
- 只调整自选组合来源解析和恒温器读取边界。
- 有效股票继续运行。
- 无效或不支持条目只进入警告或错误展示，不进入行情查询。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_stock_pools.py tests\test_web_app.py -q`

验收：
- T011 通过。
- 不再出现由原始组合字符串触发的 BaoStock 下游格式错误。

### T028：处理异常组合最终无有效股票的页面反馈

涉及文件：
- `examples/web_app.py`
- `tests/test_web_app.py`

先写或确认测试：
- 如果自选组合所有条目都无效或当前不支持，恒温器页面显示“自选组合无有效股票”或等价中文错误。
- 不调用行情查询。

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

验收：
- 用户能理解为什么没有运行结果。
- 策略层不收到空的异常输入。

### T029：运行恒温器读取阶段回归

涉及文件：
- `tests/test_web_app.py`
- `tests/test_stock_pools.py`
- `tests/test_thermostat_strategy.py`
- `tests/test_execution.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py tests\test_stock_pools.py tests\test_thermostat_strategy.py tests\test_execution.py -q`

验收：
- 自选组合读取防护通过。
- 恒温器策略和执行辅助回归通过。

## 阶段 6：最终回归和手动检查

### T030：运行核心回归组合

涉及文件：
- `tests/test_stock_pools.py`
- `tests/test_pool_market_ranges.py`
- `tests/test_pool_lhb.py`
- `tests/test_lhb_candidates.py`
- `tests/test_watchlist_store.py`
- `tests/test_web_app.py`
- `tests/test_thermostat_strategy.py`
- `tests/test_execution.py`
- `tests/test_portfolio_journal.py`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest tests\test_stock_pools.py tests\test_pool_market_ranges.py tests\test_pool_lhb.py tests\test_lhb_candidates.py tests\test_watchlist_store.py tests\test_web_app.py tests\test_thermostat_strategy.py tests\test_execution.py tests\test_portfolio_journal.py -q`

验收：
- 股票池、市场范围、龙虎榜、自选组合、Web、恒温器、执行辅助和账户回归通过。

### T031：运行完整测试套件

涉及文件：
- `tests`

验证命令：
- `.\.venv\Scripts\python.exe -m pytest -q`

验收：
- 完整测试通过。
- 如存在与本次无关的既有失败，必须记录失败名称、失败原因和与本次修复无关的证据。

### T032：手动验证账户页自选组合

涉及文件：
- `examples/web_app.py`

验证步骤：
- 启动本地 Web：`.\.venv\Scripts\python.exe examples\web_app.py --host 127.0.0.1 --port 8765`
- 验证 HTTP：`Invoke-WebRequest -Uri http://127.0.0.1:8765 -UseBasicParsing -TimeoutSec 10`
- 打开 `http://127.0.0.1:8765`
- 进入账户页。
- 创建或选择一个测试自选组合。
- 添加 `603309`，确认保存为统一格式。
- 添加 `600519, 000001 300750`，确认拆成多个代码。
- 添加 `516650,515880,515070,159801`，确认不会保存整段原始文本，且页面显示不支持或无法识别提示。
- 确认账户现金、持仓和交易流水未变化。

验收：
- HTTP 返回 200。
- 自选组合表格不再出现多代码原始字符串作为单个股票。
- 页面反馈能解释新增、重复、无效或不支持代码。

### T033：手动验证恒温器读取自选组合

涉及文件：
- `examples/web_app.py`

验证步骤：
- 使用正常自选组合运行恒温器，确认可以运行。
- 使用包含历史异常字符串的自选组合运行恒温器。
- 确认异常条目不会进入行情查询。
- 确认页面显示中文警告或错误。
- 确认不再出现由原始组合字符串触发的 BaoStock “股票代码应为9位”下游错误。

验收：
- 正常组合仍可运行。
- 异常组合被阻断或过滤并提示。
- 恒温器策略计算逻辑未被改动。

## 最终完成定义

- `spec.md` 的 13 条验收标准都有对应自动测试或手动验证点。
- 自选组合所有股票输入入口遵循一致的拆分、校验、规范化、去重和反馈规则。
- 无效或当前不支持代码不会静默写入自选组合。
- 历史异常自选组合不会把异常条目传给行情查询。
- 自选组合操作不改变账户现金、持仓或交易流水。
- 行情渠道、恒温器策略计算、账户计算和旧 CLI 保持不变。
- 核心回归、完整测试和本地 Web 手动验证完成。

## Task Review 审查结论

- 是否小步可执行：是。任务按测试、解析、存储、Web、恒温器读取、回归拆分，每个任务只聚焦一个边界。
- 是否每步都有验证方式：是。每个任务都有定向测试命令或手动验收步骤。
- 是否会一次改太多文件：已控制。大多数任务只涉及一个实现文件和对应测试；跨文件任务仅用于恒温器读取边界和阶段回归。
- 是否遗漏测试、回归验证或手动检查：未遗漏。覆盖自动测试、核心回归、完整测试、账户页手动检查和恒温器手动检查。
- 是否需要调整顺序降低风险：已按低风险顺序排列，先锁定底层解析，再处理存储，再处理 Web，最后处理策略读取历史异常数据。
