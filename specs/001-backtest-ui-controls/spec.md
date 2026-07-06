# Feature Specification: Backtest UI Controls

**Feature Branch**: `[001-backtest-ui-controls]`

**Created**: 2026-07-02

**Status**: Draft

**Input**: User description: "股票池不是下拉可选的，开始日期、结束日期也没有可选的时间段，还是原来的样子"

## Specification Readiness Gate *(mandatory)*

**Readiness Status**: READY FOR PLANNING

**Gate Decision**: The request defines a clear UI behavior gap on the backtest page: stock pool selection remains a free text field instead of a selectable source workflow, and date inputs remain raw start/end fields instead of offering preset ranges. The target behavior can be specified from the existing thermostat workbench patterns and the current screenshot without needing additional clarification.

### Required Product Decisions

- **Target behavior**: The backtest page must provide selectable stock pool sources and preset date ranges instead of requiring users to type a raw stock pool and raw start date by default.
- **Business rules**: Backtest stock pool selection must reuse the same user-facing source concepts as the thermostat strategy page where applicable: manual input, watchlist, market range, and existing candidate sources. Date range selection must support common presets and only show custom date fields when users choose custom.
- **Affected workflows/modules**: Backtest diagnosis page input workflow, backtest request summary, backtest validation messages, and displayed backtest parameter summary.
- **Protected workflows/modules**: Existing thermostat strategy page, account page, account data, watchlist data, stock pool parsing rules, backtest calculation rules, event-driven backtest behavior, and data sources must not change as part of this UI-only feature.
- **Acceptance criteria**: Users can choose a backtest stock pool from controls instead of typing the pool manually by default; users can choose a backtest date range preset; custom start/end date fields appear only when custom range is selected; submitted backtest uses the selected source and resolved date range.
- **Out of scope**: No change to strategy formulas, backtest engine execution logic, account accounting, data-provider routing, stock code normalization rules, or watchlist storage behavior.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Choose Backtest Stock Pool Source (Priority: P1)

A user opening the 回测诊断 page wants to choose the stock pool in the same guided way as the 恒温器策略 page, instead of manually typing a list of stock codes into a blank field.

**Why this priority**: The current page makes the primary input ambiguous and inconsistent with the workbench model. Backtesting cannot be confidently started if users cannot tell whether they are testing a manual pool, watchlist, market range, or another source.

**Independent Test**: Can be tested by opening the 回测诊断 page and verifying that stock pool selection is presented as a source selector with conditional controls rather than only a raw text field.

**Acceptance Scenarios**:

1. **Given** the user opens 回测诊断, **When** the page loads, **Then** the stock pool area displays a selectable stock pool source control.
2. **Given** the user selects 自选股组合, **When** watchlists exist, **Then** the page shows a selectable list of existing watchlists instead of asking the user to type stock codes.
3. **Given** the user selects 自选股组合, **When** no watchlists exist, **Then** the page shows a clear empty state telling the user to create a watchlist in the account page.
4. **Given** the user selects 手动输入, **When** they need to provide codes, **Then** the page provides a manual stock pool input path that is clearly labeled as manual input.

---

### User Story 2 - Choose Backtest Date Range Preset (Priority: P1)

A user wants to run a backtest for a common period such as recent 1 month, 3 months, 5 months, half year, or 1 year without manually calculating and entering start and end dates.

**Why this priority**: The screenshot shows start and end date fields still exposed as raw inputs. This is error-prone and inconsistent with the strategy page date-range behavior.

**Independent Test**: Can be tested by opening the 回测诊断 page, selecting a preset range, and verifying that the displayed resolved date range updates without requiring manual start date entry.

**Acceptance Scenarios**:

1. **Given** the user opens 回测诊断, **When** the page loads, **Then** the date area displays a backtest date range selector.
2. **Given** the user selects 最近 5 个月, **When** the page updates, **Then** the resolved start and end dates are shown as read-only or summary information.
3. **Given** the user selects 自定义, **When** the custom range mode is active, **Then** start date and end date fields are shown and editable.
4. **Given** the user selects any non-custom preset, **When** the preset is active, **Then** raw start date and end date fields are not shown as the primary required inputs.

---

### User Story 3 - Confirm Backtest Inputs Before Running (Priority: P2)

A user wants to see exactly which stock pool and date range will be used before starting the backtest.

**Why this priority**: Backtests can be slow and results are only useful if the selected stock pool and date range are clear.

**Independent Test**: Can be tested by selecting a stock pool source and date preset and verifying the page summary shows the resolved source, stock count if available, and resolved date range before or after running.

**Acceptance Scenarios**:

1. **Given** the user selects a stock pool source, **When** the selection is complete, **Then** the page displays the selected source name.
2. **Given** the selected stock pool can be resolved before running, **When** the page displays the parameter summary, **Then** it shows the candidate count or a clear pending/unknown state.
3. **Given** the user selects a date preset, **When** the backtest form is ready, **Then** the page displays the actual date range that will be submitted.

### Edge Cases

- If the user chooses 自选股组合 and the selected watchlist has no valid stocks, the page must block the backtest and show a clear message.
- If a preset date range resolves to dates later than available local data, the page must show the resolved requested range and the actual data range used or a clear data gap message.
- If the user switches from 自定义 to a preset range, stale custom start/end values must not remain visually presented as the active range.
- If the user switches from manual input to another stock pool source, stale manual symbols must not be visually presented as the active pool.
- If the selected source is not currently supported by backtest, the page must show that source as unavailable rather than silently falling back to manual input.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The 回测诊断 page MUST display a stock pool source selector instead of relying only on a raw stock-code text field.
- **FR-002**: The stock pool source selector MUST include at least 手动输入 and 自选股组合.
- **FR-003**: If the normal thermostat strategy page supports 市场范围 for stock pool selection, the 回测诊断 page MUST expose 市场范围 for backtesting with equivalent user-facing meaning.
- **FR-004**: If the normal thermostat strategy page supports 龙虎榜 or equivalent candidate sources for stock pool selection, the 回测诊断 page SHOULD expose those sources only if they can be resolved consistently for backtesting; otherwise the page MUST clearly mark them unavailable.
- **FR-005**: When 自选股组合 is selected, users MUST choose from existing watchlist names; they MUST NOT type the watchlist name manually as the primary workflow.
- **FR-006**: When 手动输入 is selected, the page MUST clearly label the manual input area and explain that entered codes are used only for the selected backtest unless saved elsewhere by an existing workflow.
- **FR-007**: The 回测诊断 page MUST display a backtest date range selector with preset options.
- **FR-008**: Preset date range options MUST include 最近 1 个月, 最近 3 个月, 最近 5 个月, 最近半年, 最近 1 年, and 自定义.
- **FR-009**: For non-custom date presets, the page MUST display the resolved actual start and end dates as summary information.
- **FR-010**: Start date and end date input fields MUST be visible and editable only when 自定义 is selected.
- **FR-011**: The default backtest date range MUST be a preset rather than a blank start date.
- **FR-012**: The page MUST show the selected stock pool source, selected stock pool name when applicable, and resolved date range in the backtest parameter summary.
- **FR-013**: Running a backtest MUST use the selected stock pool source and selected date range, not stale hidden values from a previous source or range mode.
- **FR-014**: The page MUST show user-friendly validation messages when the selected stock pool cannot be resolved, contains no valid stocks, or the selected date range is invalid.
- **FR-015**: This feature MUST NOT change the event-driven backtest calculation rules, strategy recommendations, account accounting, existing watchlist data, or market data source behavior.

### Key Entities *(include if feature involves data)*

- **Backtest Stock Pool Selection**: The user's selected source for the backtest stock universe, including source type, optional watchlist name, optional manual symbols, optional market range, and resolved candidate count.
- **Backtest Date Range Selection**: The user's selected time window for the backtest, including preset key, resolved start date, resolved end date, and custom dates when custom mode is selected.
- **Backtest Parameter Summary**: The user-visible confirmation of the active stock pool and active date range before or after the backtest runs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can select 自选股组合 and choose an existing watchlist for backtesting without typing any stock codes.
- **SC-002**: A user can select 最近 5 个月 and see a resolved start/end date range before running the backtest.
- **SC-003**: For non-custom ranges, no raw start date field is presented as a required primary input.
- **SC-004**: In a UI check of the 回测诊断 page, the stock pool source, selected pool detail, date preset, and resolved date range are all visible within the backtest input area.
- **SC-005**: Existing tests or manual checks confirm that 恒温器策略 and 账户 pages keep their existing stock pool and account behavior unchanged.
- **SC-006**: Attempting to run a backtest with an empty selected watchlist produces a clear validation message instead of starting a backtest with an empty or stale manual pool.

## Assumptions

- The backtest page should follow the same workbench interaction model already used by the thermostat strategy page where the concepts overlap.
- Existing watchlist data remains the source of truth for 自选股组合.
- The current local date is used as the default end date for preset range calculation unless the user explicitly selects a custom end date.
- This specification concerns user-facing behavior only; implementation details are deferred to planning.
