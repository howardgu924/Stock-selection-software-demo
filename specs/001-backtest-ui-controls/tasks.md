# Tasks: Backtest UI Controls

**Input**: Design documents from `specs/001-backtest-ui-controls/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/backtest-ui-contract.md`, `quickstart.md`

**Readiness Gate**: PASS. `spec.md` is READY FOR PLANNING, and `plan.md` has no unresolved clarification markers.

**Tests**: Required by the user request and plan. Each user story starts with tests or validation points before implementation.

**Scope Guard**: Do not modify `stock_picker/strategies/`, account/watchlist storage formats, market data sources, or event-driven backtest calculation rules unless a test exposes a direct UI-boundary bug that cannot be fixed in `examples/web_app.py`.

**Task Review Result**: Reviewed with Superpowers task-review criteria on 2026-07-03. Tasks remain implementation-ready after tightening source-control granularity and adding explicit verification expectations.

**Per-Task Verification Rule**: Every test-writing task must name the behavior it proves and be followed by a targeted failing-test run in the same phase. Every implementation task must be verified by the next targeted test command in that phase before moving to the next story.

## Format: `[ID] [P?] [Story] Description`

- **[P]** means the task can be done in parallel because it touches different files or is a read-only/manual validation task.
- **[Story]** maps to the user stories in `spec.md`.
- Every task includes exact file paths.

---

## Phase 1: Setup and Baseline

**Purpose**: Confirm current boundaries and capture the existing backtest UI failure before implementation.

- [X] T001 Review the current 回测诊断 render and submit boundaries in `examples/web_app.py` around `render_thermostat_backtest_section`, `handle_thermostat_backtest`, `stock_pool_fields`, and `_resolve_thermostat_stock_pool`; verification: note which existing helpers can be reused before editing code
- [X] T002 Review existing web regression coverage in `tests/test_web_app.py` around `test_web_thermostat_backtest_outputs_diagnostics` and `test_backtest_page_shows_cache_parameters_results_and_download_sections`; verification: identify the tests to extend rather than duplicate
- [X] T003 Run baseline web tests with `.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py -q` and record the current result before adding failing tests; verification: do not continue if unrelated web tests already fail

**Checkpoint**: Current behavior and test baseline are understood. No production code has been changed.

---

## Phase 2: User Story 1 - Choose Backtest Stock Pool Source (Priority: P1) 🎯 MVP

**Goal**: 回测诊断 page exposes stock pool source controls, including manual input and watchlist dropdown, instead of relying only on a raw stock-code input.

**Independent Test**: Render `render_thermostat_backtest_section()` with manual, watchlist, empty-watchlist, and market-range forms and verify the active source controls are visible while stale/inactive source fields are not presented as active.

### Tests for User Story 1

> Write these tests first and verify they fail before implementation.

- [X] T004 [US1] Add a failing render test in `tests/test_web_app.py` proving `render_thermostat_backtest_section({})` includes `name="stock_pool_source"` and a clearly labeled manual stock pool input path
- [X] T005 [US1] Add a failing render test in `tests/test_web_app.py` proving `stock_pool_source=watchlist` shows existing watchlists as selectable options and does not require typing stock codes
- [X] T006 [US1] Add a failing render test in `tests/test_web_app.py` proving `stock_pool_source=watchlist` with no watchlists shows `暂无自选组合，请到账户页创建`
- [X] T007 [US1] Add a failing render test in `tests/test_web_app.py` proving `stock_pool_source=market_range` exposes market range controls with the same user-facing meaning as the thermostat strategy page
- [X] T008 [US1] Add a failing render test in `tests/test_web_app.py` proving 回测诊断 does not show duplicate candidate-source entries such as separate `龙虎榜` and `同花顺龙虎榜`
- [X] T009 [US1] Run the new US1 tests with `.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py -q` and confirm the new assertions fail for the current raw-only backtest page

### Implementation for User Story 1

- [X] T010 [US1] Replace the raw-only stock pool area in `examples/web_app.py` `render_thermostat_backtest_section` with the existing stock source selector shell; verification: T004 begins passing while non-manual tests may still fail
- [X] T011 [US1] Render manual and watchlist controls conditionally for the backtest page in `examples/web_app.py`; verification: T004, T005, and T006 pass
- [X] T012 [US1] Render market-range controls conditionally for the backtest page in `examples/web_app.py`; verification: T007 passes without changing thermostat strategy page rendering
- [X] T013 [US1] Ensure unsupported or duplicate candidate sources in `examples/web_app.py` are hidden, disabled, or explicitly marked unavailable rather than silently falling back to manual input; verification: T008 passes and only one user-facing 龙虎榜-style entry is visible when supported
- [X] T014 [US1] Run `.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py -q` and confirm all US1 tests pass without breaking existing web tests

**Checkpoint**: User Story 1 is independently functional: users can choose a backtest stock pool source without relying only on raw stock input.

---

## Phase 3: User Story 2 - Choose Backtest Date Range Preset (Priority: P1)

**Goal**: 回测诊断 page provides date range presets, including 最近 5 个月, and only shows editable start/end fields for 自定义.

**Independent Test**: Render the backtest page with preset and custom date forms and verify presets, resolved date summaries, and custom field visibility.

### Tests for User Story 2

> Write these tests first and verify they fail before implementation.

- [X] T015 [US2] Add a failing render test in `tests/test_web_app.py` proving 回测诊断 includes a backtest date range selector with 最近 1 个月, 最近 3 个月, 最近 5 个月, 最近半年, 最近 1 年, and 自定义
- [X] T016 [US2] Add a failing render test in `tests/test_web_app.py` proving non-custom date presets show a resolved actual date range summary and do not show raw start/end fields as the primary required inputs
- [X] T017 [US2] Add a failing render test in `tests/test_web_app.py` proving 自定义 mode shows editable start date and end date fields
- [X] T018 [US2] Add a failing resolver or handler test in `tests/test_web_app.py` proving 最近 5 个月 resolves from the selected end date into the submitted start/end range
- [X] T019 [US2] Add a failing stale-state test in `tests/test_web_app.py` proving custom start/end values do not override the active preset after switching away from 自定义
- [X] T020 [US2] Run the new US2 tests with `.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py -q` and confirm the new assertions fail before implementation

### Implementation for User Story 2

- [X] T021 [US2] Add a backtest-specific date range resolver in `examples/web_app.py` without changing `_strategy_range_dates`; verification: T018 can pass independently
- [X] T022 [US2] Update `examples/web_app.py` `render_thermostat_backtest_section` to render the backtest date range selector and resolved date range summary; verification: T015 and T016 pass
- [X] T023 [US2] Update `examples/web_app.py` so raw start/end inputs are rendered only when the backtest date range is `custom`; verification: T017 passes
- [X] T024 [US2] Ensure `examples/web_app.py` submit handling uses the resolved preset dates instead of stale custom fields; verification: T019 passes
- [X] T025 [US2] Run `.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py -q` and confirm all US2 tests pass without breaking US1

**Checkpoint**: User Story 2 is independently functional: users can select common backtest periods without manually calculating dates.

---

## Phase 4: User Story 3 - Confirm Backtest Inputs Before Running (Priority: P2)

**Goal**: The page and result/request summary clearly show the active stock pool source, selected detail, resolved count when available, and resolved date range.

**Independent Test**: Submit valid and invalid forms to `handle_thermostat_backtest()` and verify active values are used, invalid pools are blocked, and summaries describe the submitted source/date range.

### Tests for User Story 3

> Write these tests first and verify they fail before implementation.

- [X] T026 [US3] Add a failing handler test in `tests/test_web_app.py` proving `stock_pool_source=watchlist` resolves the selected watchlist symbols before calling the existing event-driven backtest function
- [X] T027 [US3] Add a failing handler test in `tests/test_web_app.py` proving an empty selected watchlist returns a clear validation result and does not call the backtest engine
- [X] T028 [US3] Add a failing handler test in `tests/test_web_app.py` proving inactive manual `symbols` are ignored when `stock_pool_source=watchlist` or `stock_pool_source=market_range`
- [X] T029 [US3] Add a failing result-summary test in `tests/test_web_app.py` proving the backtest request summary shows active stock pool source, selected pool detail, resolved count when available, and resolved date range
- [X] T030 [US3] Add a failing unavailable-source test in `tests/test_web_app.py` proving unsupported sources return an explicit unavailable message instead of silently falling back to manual symbols
- [X] T031 [US3] Run the new US3 tests with `.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py -q` and confirm the new assertions fail before implementation

### Implementation for User Story 3

- [X] T032 [US3] Update `examples/web_app.py` `handle_thermostat_backtest` to resolve the active stock pool through the existing pool-resolution boundary before calling the backtest function; verification: T026 and T028 pass
- [X] T033 [US3] Update `examples/web_app.py` `handle_thermostat_backtest` to block empty or invalid resolved pools with the existing user-facing stock-pool error result pattern; verification: T027 passes
- [X] T034 [US3] Update `examples/web_app.py` request summaries to include active stock pool source, selected detail, resolved count when available, date range preset, resolved start, and resolved end; verification: T029 passes
- [X] T035 [US3] Ensure `examples/web_app.py` keeps the existing event-driven backtest call signature and result tables unchanged after source/date resolution; verification: existing `test_web_thermostat_backtest_outputs_diagnostics` still passes
- [X] T036 [US3] Run `.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py -q` and confirm all US3 tests pass without breaking US1 or US2

**Checkpoint**: User Story 3 is independently functional: submitted backtests use and display the active selected source and resolved date range.

---

## Phase 5: Polish and Cross-Cutting Regression

**Purpose**: Confirm protected workflows remain unchanged and run final verification.

- [X] T037 Add or update regression assertions in `tests/test_web_app.py` proving the 恒温器策略 page still renders its existing stock-pool workflow; verification: targeted web tests pass
- [X] T038 Add or update regression assertions in `tests/test_web_app.py` proving the 账户 page still renders self-select/watchlist management without storage-format changes; verification: targeted web tests pass
- [X] T039 Run targeted web regression with `.\.venv\Scripts\python.exe -m pytest tests/test_web_app.py -q`
- [X] T040 Run full regression for repository tests under `tests/` with `.\.venv\Scripts\python.exe -m pytest -q`
- [X] T041 [P] Perform the manual quickstart validation from `specs/001-backtest-ui-controls/quickstart.md` against `http://127.0.0.1:8765/backtest`

**Checkpoint**: All targeted and full regression checks pass, and manual UI validation confirms the screenshot issue is resolved.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup and Baseline**: No dependency.
- **Phase 2 User Story 1**: Depends on Phase 1.
- **Phase 3 User Story 2**: Depends on Phase 1; can be developed after US1 tests are written, but should merge after shared backtest form changes are stable.
- **Phase 4 User Story 3**: Depends on US1 and US2 because submit handling needs active source and active date range controls.
- **Phase 5 Polish**: Depends on selected user stories being complete.

### User Story Dependencies

- **US1 (P1)**: MVP. Provides selectable stock pool source controls.
- **US2 (P1)**: Can be validated independently from US1 at render/helper level, but final form integration shares `render_thermostat_backtest_section`.
- **US3 (P2)**: Depends on US1 and US2 to submit the resolved stock pool and resolved date range.

### Within Each User Story

- Write failing tests first.
- Run targeted tests and confirm failure.
- Implement the minimum web-app change.
- Run targeted tests and confirm pass.
- Do not proceed if failures appear in protected pages or strategy/backtest modules.

## Parallel Opportunities

This feature intentionally has limited parallelism because most changes are in `examples/web_app.py` and `tests/test_web_app.py`.

- T041 manual quickstart validation can run after T039 and in parallel with non-code documentation review.
- Read-only review tasks T001 and T002 can be done in parallel.
- If multiple people work on this, split by story only after agreeing on exact helper names to avoid conflicts in `examples/web_app.py`.

## Parallel Example

```text
Task A: T001 Review examples/web_app.py backtest boundaries
Task B: T002 Review tests/test_web_app.py web regression coverage
```

After implementation:

```text
Task A: T039 Run targeted pytest regression
Task B: T041 Perform manual quickstart validation after the web server is running
```

## Implementation Strategy

### MVP First

1. Complete Phase 1.
2. Complete Phase 2 / US1.
3. Stop and validate that 回测诊断 has stock pool source selection and watchlist dropdown behavior.

### Incremental Delivery

1. Add US1 stock pool source controls.
2. Add US2 date presets and resolved date summary.
3. Add US3 submit-handler resolution and request summary.
4. Add protected-page regression checks and run full verification.

### Risk-Reducing Rule

Keep changes at the web boundary. If a task appears to require editing `stock_picker/strategies/` or data-source modules, stop and re-check whether the implementation is leaking outside the spec.
