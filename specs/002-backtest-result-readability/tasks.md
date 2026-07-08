# Tasks: Backtest Result Readability

**Input**: Design documents from `specs/002-backtest-result-readability/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/backtest-result-readability-contract.md](contracts/backtest-result-readability-contract.md), [quickstart.md](quickstart.md)

**Readiness Gate**: PASS. `spec.md` is READY FOR PLANNING, and `plan.md` defines display-layer-only changes with protected backtest/account/data behavior unchanged.

**Tests**: Required. The spec and plan both require regression tests before implementation. Each story starts with a failing test or explicit verification point.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel after its phase prerequisites are met.
- **[Story]**: User story label from `spec.md`.
- Every task names the exact file path it touches or validates.

## Phase 1: Setup And Characterization

**Purpose**: Capture current rendering behavior and create stable test inputs without changing implementation behavior.

- [x] T001 Review current backtest result rendering entry points in `examples/web_app.py` and note functions to cover in `specs/002-backtest-result-readability/quickstart.md`
- [x] T002 [P] Add or extend a synthetic backtest result fixture for web rendering tests in `tests/test_web_app.py`
- [x] T003 [P] Add a focused assertion helper for checking absence of raw snake_case labels in `tests/test_web_app.py`
- [x] T004 Run `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q` and record the baseline result in `specs/002-backtest-result-readability/quickstart.md`

---

## Phase 2: Foundational Display Contract

**Purpose**: Add shared display helpers before changing individual result sections. This phase blocks all user-story implementation.

- [x] T005 Write failing tests for Chinese field labels, hidden fields, duplicate `shares_after`, and unknown-field handling in `tests/test_web_app.py`
- [x] T006 Write failing tests for money/price two-decimal formatting and non-money formatting preservation in `tests/test_web_app.py`
- [x] T007 Implement shared display field rules for labels, visibility, duplicate handling, and format type in `examples/web_app.py`
- [x] T008 Implement shared value formatting helpers for money, price, quantity, date, percent, ratio, text, and code fields in `examples/web_app.py`
- [x] T009 Apply shared display helpers to the existing generic table rendering path in `examples/web_app.py`
- [x] T010 Run `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q` and verify the foundational display tests pass

**Checkpoint**: Shared labels and formatting are available for all stories without changing backtest calculations.

---

## Phase 3: User Story 1 - Read Backtest Results Clearly (Priority: P1) MVP

**Goal**: Completed backtest results are grouped into readable Chinese sections, large detail areas are collapsible, and report access is visible.

**Independent Test**: Render a completed synthetic backtest result and verify the top summary, titled sections, collapsible detail sections, empty states, and report entry are present.

### Tests for User Story 1

- [x] T011 [P] [US1] Write failing tests for Chinese result section titles and top summary placement in `tests/test_web_app.py`
- [x] T012 [P] [US1] Write failing tests for collapsible large detail sections and empty-state text in `tests/test_web_app.py`
- [x] T013 [P] [US1] Write failing tests for visible report download entry and unavailable/failed report state in `tests/test_web_app.py`

### Implementation for User Story 1

- [x] T014 [US1] Add result-section rendering structure for summary, trades, holdings, daily assets, diagnostics, data quality, and report sections in `examples/web_app.py`
- [x] T015 [US1] Update backtest result rendering to use result sections while preserving existing `RenderResult` payload values in `examples/web_app.py`
- [x] T016 [US1] Add collapsible markup for large or low-frequency detail sections in `examples/web_app.py`
- [x] T017 [US1] Add report download entry rendering for available, unavailable, and failed states in `examples/web_app.py`
- [x] T018 [US1] Run `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q` and verify US1 result-readability tests pass

**Checkpoint**: User Story 1 is independently testable as the MVP.

---

## Phase 4: User Story 2 - See Localized And Relevant Fields (Priority: P1)

**Goal**: Backtest result tables use Chinese labels, show stock names where available, and hide irrelevant technical fields in default transaction-flow views.

**Independent Test**: Render stock-level and transaction-flow result tables and confirm Chinese labels, name column/fallback, hidden technical fields, and no raw duplicate `shares_after`.

### Tests for User Story 2

- [x] T019 [P] [US2] Write failing tests that default transaction-flow output hides `signal_time`, order status, and `slippage_cost` in `tests/test_web_app.py`
- [x] T020 [P] [US2] Write failing tests that `shares_after` is hidden or shown once with a Chinese label in `tests/test_web_app.py`
- [x] T021 [P] [US2] Write failing tests that stock-level tables include stock code and stock name with `鏈煡` fallback in `tests/test_web_app.py`
- [x] T022 [P] [US2] Write failing tests that visible result table headers contain no raw snake_case labels in `tests/test_web_app.py`

### Implementation for User Story 2

- [x] T023 [US2] Apply table-specific hidden-field rules for transaction-flow and duplicated fields in `examples/web_app.py`
- [x] T024 [US2] Add stock-name display handling for stock-level result rows using existing payload fields or existing metadata only in `examples/web_app.py`
- [x] T025 [US2] Ensure unknown raw fields are hidden or mapped to controlled Chinese fallback labels in `examples/web_app.py`
- [x] T026 [US2] Run `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q` and verify US2 localization tests pass

**Checkpoint**: User Story 2 works independently without changing raw payload schemas.

---

## Phase 5: User Story 3 - Compare Numbers Reliably (Priority: P1)

**Goal**: Money and price-like values across the web interface display exactly two decimals while non-money values keep appropriate formatting.

**Independent Test**: Render account, strategy, backtest result, holdings, transaction, and summary views and confirm monetary values use two decimals without corrupting dates, stock codes, counts, shares, or percentages.

### Tests for User Story 3

- [x] T027 [P] [US3] Write failing tests for two-decimal money and price rendering in backtest summary/result tables in `tests/test_web_app.py`
- [x] T028 [P] [US3] Write failing tests for two-decimal money rendering in account overview, holdings, trades, and strategy displays in `tests/test_web_app.py`
- [x] T029 [P] [US3] Write failing tests that dates, stock codes, share counts, row counts, percentages, and ratios are not formatted as money in `tests/test_web_app.py`

### Implementation for User Story 3

- [x] T030 [US3] Apply the shared money/price formatting helper to backtest summaries and result tables in `examples/web_app.py`
- [x] T031 [US3] Apply the shared money/price formatting helper to account overview, holdings, trades, and strategy result displays in `examples/web_app.py`
- [x] T032 [US3] Add explicit non-money format rules for dates, stock codes, identifiers, shares, counts, percentages, and ratios in `examples/web_app.py`
- [x] T033 [US3] Run `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q` and verify US3 numeric-format tests pass

**Checkpoint**: User Story 3 works independently and preserves raw calculation values.

---

## Phase 6: User Story 4 - Navigate Large Tables Without Losing Context (Priority: P2)

**Goal**: Long and wide backtest tables remain readable while scrolling.

**Independent Test**: Render a table with at least 50 rows and a wide set of columns, then verify sticky/continuous header markup and horizontal scroll container behavior.

### Tests for User Story 4

- [x] T034 [P] [US4] Write failing tests for sticky or continuously available table-header markup on long result tables in `tests/test_web_app.py`
- [x] T035 [P] [US4] Write failing tests for horizontal table container markup and aligned header/data classes on wide tables in `tests/test_web_app.py`

### Implementation for User Story 4

- [x] T036 [US4] Add constrained table container markup for long and wide tables in `examples/web_app.py`
- [x] T037 [US4] Add or adjust CSS for sticky headers and horizontal scrolling in `examples/web_app.py`
- [x] T038 [US4] Ensure empty tables keep section titles and Chinese empty-state messages in `examples/web_app.py`
- [x] T039 [US4] Run `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q` and verify US4 table-readability tests pass

**Checkpoint**: User Story 4 is independently verifiable through rendered HTML and manual browser inspection.

---

## Phase 7: User Story 5 - Understand Backtest Progress (Priority: P2)

**Goal**: Backtest progress shows multiple meaningful stages and counts instead of a binary 0-to-1 jump.

**Independent Test**: Start or simulate a backtest job with more than one stock or more than one trading day and verify at least three Chinese progress stages plus processed/total counts where available.

### Tests for User Story 5

- [x] T040 [P] [US5] Write failing tests for non-binary backtest progress stages in `tests/test_web_app.py`
- [x] T041 [P] [US5] Write failing tests for processed/total count display and current item text when counts are known in `tests/test_web_app.py`
- [x] T042 [P] [US5] Write failing tests that unknown totals still show meaningful Chinese stage and message text in `tests/test_web_app.py`

### Implementation for User Story 5

- [x] T043 [US5] Add lightweight backtest job stage updates for parameter preparation, data loading, trade simulation, result preparation, and report preparation in `examples/web_app.py`
- [x] T044 [US5] Update progress rendering to show stage, explanatory message, completed/total counts, percent, and current item when available in `examples/web_app.py`
- [x] T045 [US5] Ensure failure and completion progress summaries remain Chinese and readable in `examples/web_app.py`
- [x] T046 [US5] Run `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q` and verify US5 progress tests pass

**Checkpoint**: User Story 5 works independently and does not require rewriting the job system.

---

## Phase 8: Polish And Cross-Cutting Verification

**Purpose**: Confirm protected behavior and complete end-to-end validation.

- [x] T047 Add regression tests that rendered display formatting does not mutate raw `RenderResult` values in `tests/test_web_app.py`
- [x] T048 Run `.\.venv\Scripts\python.exe -m pytest tests\test_backtest.py tests\test_event_backtest_engine.py tests\test_thermostat_backtest.py tests\test_web_app.py -q`
- [x] T049 Run `.\.venv\Scripts\python.exe -m pytest -q`
- [x] T050 Manually verify `/backtest` in the local browser using a stock pool with multiple symbols and a custom date range, following `specs/002-backtest-result-readability/quickstart.md`
- [x] T051 Update `specs/002-backtest-result-readability/quickstart.md` with final verification evidence and any remaining manual-check limitations

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup And Characterization**: No dependencies.
- **Phase 2 Foundational Display Contract**: Depends on Phase 1; blocks all user stories.
- **US1, US2, US3**: Depend on Phase 2 and should be completed before P2 stories because they address P1 readability, localization, and numeric consistency.
- **US4**: Depends on Phase 2 and benefits from US1 table-section work.
- **US5**: Depends on Phase 2 and can proceed after display helpers exist; it does not depend on US4.
- **Phase 8 Polish**: Depends on all selected user stories.

### User Story Dependencies

- **US1 (P1)**: Can start after Phase 2; MVP scope.
- **US2 (P1)**: Can start after Phase 2; shares display helper from Phase 2.
- **US3 (P1)**: Can start after Phase 2; shares display helper from Phase 2.
- **US4 (P2)**: Should start after US1 to avoid reworking table-section markup.
- **US5 (P2)**: Can start after Phase 2; safest after US1 so completed results and progress remain visually consistent.

### Within Each User Story

- Write tests before implementation.
- Run focused `tests/test_web_app.py` after each story.
- Do not edit backtest calculation modules unless a task explicitly calls for a read-only verification.
- Stop at each checkpoint if tests fail and debug before continuing.

## Parallel Opportunities

- T002 and T003 can run in parallel after T001.
- T011, T012, and T013 can run in parallel after Phase 2.
- T019, T020, T021, and T022 can run in parallel after Phase 2.
- T027, T028, and T029 can run in parallel after Phase 2.
- T034 and T035 can run in parallel after US1 table structure is available.
- T040, T041, and T042 can run in parallel after Phase 2.

## Parallel Example: User Story 2

```text
Task: "T019 Write failing tests that default transaction-flow output hides technical fields in tests/test_web_app.py"
Task: "T020 Write failing tests that shares_after is hidden or shown once with a Chinese label in tests/test_web_app.py"
Task: "T021 Write failing tests that stock-level tables include stock code and stock name with fallback in tests/test_web_app.py"
Task: "T022 Write failing tests that visible result table headers contain no raw snake_case labels in tests/test_web_app.py"
```

## Implementation Strategy

### MVP First

1. Complete Phase 1 and Phase 2.
2. Complete US1 so the result page becomes readable and report access is visible.
3. Stop and validate US1 independently with `tests/test_web_app.py`.

### Incremental Delivery

1. Add shared display contract and tests.
2. Deliver US1 result grouping and report visibility.
3. Deliver US2 localization and stock-name display.
4. Deliver US3 numeric consistency across web pages.
5. Deliver US4 large-table readability.
6. Deliver US5 meaningful progress.
7. Run focused, full, and manual verification.

### Risk-Control Rules

- Keep all changes display-layer-only unless a failing test proves existing display data is unavailable.
- Prefer synthetic web-rendering tests over live data tests.
- Do not change exported raw report values.
- Do not introduce new frontend frameworks or job-system rewrites.
