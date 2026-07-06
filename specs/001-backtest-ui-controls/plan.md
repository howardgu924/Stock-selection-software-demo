# Implementation Plan: Backtest UI Controls

**Branch**: `[001-backtest-ui-controls]` | **Date**: 2026-07-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-backtest-ui-controls/spec.md`

## Planning Preconditions *(mandatory)*

- **Spec readiness**: READY FOR PLANNING
- **Unresolved clarification items**: None
- **Target behavior confirmed**: Yes
- **Business rules confirmed**: Yes
- **Protected scope confirmed**: Yes

Planning may proceed. The plan does not introduce new strategy formulas, stock selection rules, backtest accounting rules, data-provider behavior, or watchlist storage behavior.

## Summary

The 回测诊断 page still exposes the older raw `股票池 / 开始日期 / 结束日期` inputs. This feature will align that page with the existing workbench interaction model by adding a stock pool source selector, watchlist dropdown flow, supported market-range flow, and preset date range selector. Submitted backtests will use the resolved source and resolved date range, while the existing event-driven backtest engine and result tables remain unchanged.

## Technical Context

**Language/Version**: Python 3.x in the existing local project runtime.

**Primary Dependencies**: Existing standard-library HTML rendering in `examples/web_app.py`, pandas-backed strategy/backtest objects, existing stock pool helpers, watchlist store, and pytest.

**Storage**: Existing file-backed user/account/watchlist data under the current user path; no new persistence.

**Testing**: pytest, primarily `tests/test_web_app.py`, with existing regression coverage for thermostat pages and event-driven backtest output.

**Target Platform**: Local Windows/Python web app served from `examples/web_app.py`.

**Project Type**: Single-repo local web app plus Python strategy library.

**Performance Goals**: No additional backtest work before submit beyond lightweight form rendering and existing pool resolution behavior. The feature must not make the backtest engine slower.

**Constraints**:

- Keep event-driven backtest calculation, account behavior, data sources, stock normalization, and watchlist storage unchanged.
- Reuse existing source concepts where possible to avoid divergent UI rules.
- Do not silently submit stale manual symbols or stale custom dates after users switch modes.
- Keep the backtest result output compatible with existing front-end result rendering.

**Scale/Scope**: One web page input workflow, one backtest submit handler, focused tests, and Spec Kit documentation artifacts.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The project constitution file currently contains placeholder principles and no enforceable project-specific gates. This plan applies the working project constraints from the spec instead:

- Preserve protected business logic and data behavior.
- Keep the change scoped to the backtest UI/request path.
- Add tests before implementation tasks.
- Avoid introducing new storage or calculation layers.

**Gate result**: PASS. No constitution violations or complexity exceptions.

## Project Structure

### Documentation (this feature)

```text
specs/001-backtest-ui-controls/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── backtest-ui-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md              # Created later by /speckit-tasks
```

### Source Code (repository root)

```text
examples/
└── web_app.py            # Backtest page rendering and submit handling

tests/
└── test_web_app.py       # UI rendering, handler routing, and regression tests

stock_picker/
├── pools/                # Existing pool resolution behavior, read/reuse only unless tests expose a boundary bug
└── strategies/           # Existing event-driven backtest behavior, unchanged
```

**Structure Decision**: Keep this as a focused web-app workflow change. The only planned implementation module is `examples/web_app.py`, with tests in `tests/test_web_app.py`. Strategy modules and data providers are protected and should not be modified for this feature.

## 修改模块

- **`examples/web_app.py`**
  - Update the 回测诊断 form to use a stock pool source selector instead of showing raw stock-code input as the only primary workflow.
  - Reuse or mirror the existing thermostat stock-pool source controls for 手动输入, 自选股组合, 市场范围, and the single existing 龙虎榜 entry when it can be resolved by the current server handler.
  - Do not introduce a second duplicate candidate-source entry such as separate “同花顺龙虎榜” unless the existing strategy page and resolver already expose it as a stable, distinct supported source.
  - Add a backtest date range selector with presets: 最近 1 个月, 最近 3 个月, 最近 5 个月, 最近半年, 最近 1 年, 自定义.
  - Show resolved date range summary for preset modes and show editable start/end only in custom mode.
  - Route submit handling through resolved stock pool and resolved date range values before calling the existing backtest function.
  - Include stock pool source, selected detail, stock count when available, and resolved date range in request/result summaries.

- **`tests/test_web_app.py`**
  - Add focused tests for the new backtest form controls and submit behavior.
  - Update existing backtest-page expectations so they assert the new guided workflow instead of the old raw-only form.
  - Keep existing event-driven backtest output tests as regression coverage.

- **Spec artifacts**
  - Maintain `specs/001-backtest-ui-controls/plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/backtest-ui-contract.md`.

## 保持不变的行为

- 恒温器策略页的现有股票池来源、策略日期、账户资金和运行行为保持不变。
- 账户页、自选组合保存结构、股票代码解析规则、市场数据源选择和缓存规则保持不变。
- `backtest_thermostat_strategy` 的事件驱动回测计算、交易撮合、数据质量诊断、输出表结构保持不变。
- 回测结果区仍展示摘要、每日资产、交易明细、持仓、数据质量和报告下载提示。
- 已有测试覆盖的旧海龟系统退出默认流程、汉化字段和账户输入清空行为不应被本次修改影响。

## Phase 0: Research

Completed in [research.md](research.md). Main decision: reuse the thermostat page's existing source concepts and pool resolver where possible, and add a backtest-specific date range resolver rather than changing the strategy-page date helper.

## Phase 1: Design & Contracts

Completed artifacts:

- [data-model.md](data-model.md)
- [contracts/backtest-ui-contract.md](contracts/backtest-ui-contract.md)
- [quickstart.md](quickstart.md)

The design treats the feature as a UI/request contract change, not a strategy or persistence change.

## 测试策略

Implementation should follow test-first order:

1. Add rendering tests proving 回测诊断 displays `stock_pool_source`, watchlist selection, market range when supported, and no raw start date as the required primary input for preset ranges.
2. Add date preset tests proving 最近 5 个月 resolves to visible start/end summary and custom mode shows editable start/end fields.
3. Add submit-handler tests proving 自选股组合 submits resolved watchlist symbols to the existing backtest function and empty watchlists fail with a clear message.
4. Add stale-state tests proving manual symbols are ignored after switching to watchlist/market range, and custom dates are ignored after switching to a preset.
5. Add source-availability tests proving unsupported candidate sources are disabled or return an explicit unavailable message instead of silently falling back.
6. Add regression assertions that 恒温器策略 and 账户 pages still render their existing stock-pool/watchlist workflows.
7. Run targeted regression: `.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py -q`.
8. Run full regression before completion: `.\.venv\Scripts\python.exe -m pytest -q`.

## 风险点

- **表单状态残留**: HTML forms may still carry hidden or previously typed values. Handler tests must assert active mode wins over stale values.
- **日期计算边界**: Month-based presets can differ from fixed-day approximations. The plan should use one consistent rule and show the resolved dates clearly.
- **自选组合为空**: Empty or missing watchlists must stop before backtest execution.
- **来源能力不一致**: 龙虎榜 or other candidate sources should only appear if the backtest handler can resolve them consistently; otherwise show unavailable rather than falling back silently.
- **测试过度绑定 HTML**: Tests should assert meaningful controls and labels, not fragile full markup.

## 更稳的实现路径

1. First add failing tests for the 回测诊断 render contract: source selector, preset date selector, 最近 5 个月, custom date visibility, and watchlist dropdown.
2. Add a backtest-specific date range resolver and make only those tests pass.
3. Wire the backtest form to existing stock-pool controls and resolver behavior, keeping the existing event-driven backtest call unchanged.
4. Add submit-handler tests for watchlist resolution, empty watchlist validation, inactive stale fields, and unsupported source handling.
5. Run web regression before touching any broader tests; if failures appear outside 回测诊断, stop and inspect whether the change leaked into protected pages.
6. Only after targeted tests pass, run full pytest for compatibility.

## 为什么这个方案不过度复杂

- It reuses existing source concepts and pool-resolution behavior instead of inventing a second backtest-only stock-pool model.
- It keeps all calculation and data modules unchanged.
- It adds one small date-range resolver for the backtest page rather than changing strategy-page behavior.
- It limits implementation to one web module and one test module.
- It keeps the feature verifiable through UI/handler tests without needing slow end-to-end market-data runs.

## Complexity Tracking

No constitution or scope violations require complexity justification.
