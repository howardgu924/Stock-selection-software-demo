# Web 汉化泄漏清单

本清单只覆盖正常 Web 使用路径中的用户可见内容。内部字段名、DataFrame
列名、CLI 参数、配置键和持久化结构不在本次汉化范围内。

## 必须汉化

| 来源页面 | 当前英文 | 期望中文 | 对应任务 |
| --- | --- | --- | --- |
| 恒温器结果区 | Stock Pool Summary | 股票池摘要 | T004/T012 |
| 恒温器结果区 | Market Overview | 市场概览 | T004/T012 |
| 恒温器结果区 | Holding Advice | 持仓建议 | T004/T012 |
| 恒温器结果区 | New Buy Candidates | 新买候选 | T004/T012 |
| 恒温器结果区 | Grid Advice | 网格建议 | T004/T012 |
| 恒温器结果区 | Trend Advice | 趋势建议 | T004/T012 |
| 恒温器结果区 | Execution Plan | 手工执行计划 | T004/T012 |
| 恒温器结果区 | Errors | 错误 | T004/T012 |
| 龙虎榜结果区 | LHB Top 20 | 龙虎榜前 20 名 | T005/T013 |
| 龙虎榜结果区 | LHB Top 30 | 龙虎榜前 30 名 | T005/T013 |
| 龙虎榜结果区 | LHB Top 50 | 龙虎榜前 50 名 | T005/T013 |
| 回测结果区 | Summary | 摘要 | T005/T013 |
| 回测结果区 | Diagnostics | 诊断明细 | T005/T013 |
| 回测结果区 | Regime Performance | 市场状态表现 | T005/T013 |
| 账户结果区 | Trades | 交易流水 | T005/T013 |
| 账户结果区 | Positions | 当前持仓 | T005/T013 |
| 表格列名 | watchlist_name | 自选组合名称 | T006/T015 |
| 表格列名 | time_range | 时间范围 | T006/T015 |
| 表格列名 | source_detail | 来源说明 | T006/T015 |
| 表格列名 | market_regime | 市场状态 | T006/T015 |
| 表格列名 | confidence | 置信度 | T006/T015 |
| 表格列名 | data_source | 数据来源 | T006/T015 |
| 表格列名 | data_sufficient | 数据是否充足 | T006/T015 |
| 执行计划列名 | recommended_action | 推荐操作 | T006/T015 |
| 执行计划列名 | fallback_action | 备选操作 | T006/T015 |
| 执行计划列名 | limit_status | 涨跌停状态 | T006/T015 |
| 执行计划列名 | volume_limit_pct | 成交量限制比例 | T006/T015 |
| 执行计划列名 | skip_insufficient_cash | 资金不足跳过 | T006/T015 |
| 执行计划列名 | skip_volume_limit | 成交量限制跳过 | T006/T015 |
| 状态值 | range | 震荡区间 | T007/T019 |
| 状态值 | uptrend | 上升趋势 | T007/T019 |
| 状态值 | downtrend | 下降趋势 | T007/T019 |
| 状态值 | trend_following | 趋势跟随 | T007/T019 |
| 状态值 | grid | 网格策略 | T007/T019 |
| 状态值 | observe | 观察 | T007/T019 |
| 状态值 | buy | 买入 | T007/T019 |
| 状态值 | wait_confirm | 等待确认 | T007/T019 |
| 进度 | load_candidate_history | 正在加载候选股历史 | T009/T023 |
| 进度 | evaluate_candidates | 正在评估候选股 | T009/T023 |
| 进度 | evaluate_holdings | 正在评估持仓 | T009/T023 |
| 进度 | build_execution_plan | 正在生成手工执行计划 | T009/T023 |

## 允许保留英文

- 股票代码，例如 `600519.SH`。
- 数据源、库、协议或品牌名，例如 `akshare`、`BaoStock`、`Sina`、`JoinQuant`。
- 文件路径、账户路径、URL、命令行参数和配置键，例如 `data/user/default`。
- 外部服务原始异常文本，但必须放在中文摘要之后。
- 开发者日志和内部测试断言中的字段键名。
