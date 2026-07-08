# Feature Specification: Backtest Result Readability

**Feature Branch**: `[002-backtest-result-readability]`

**Created**: 2026-07-08

**Status**: Draft

**Input**: User description: "回测之后的数据页面依然不具备可读性；所有页面金额数字改成2位小数；回测结果页面依旧有未翻译字段；股票名称未显示；交易流水不用显示 signal_time、订单状态、slippage_cost；操作后持仓和未翻译字段 shares_after 可能重复；实际回测结果放在页面顶端可以；没看到报告下载入口；数据过多时表格顶栏滚动后不可见；回测进度条不对，直接就是0到1。"

## Specification Readiness Gate *(mandatory)*

**Readiness Status**: READY FOR PLANNING

**Gate Decision**: The request identifies concrete target behavior for the backtest result experience, numeric formatting, localization, table readability, report access, and backtest progress feedback. No blocking clarification is required before planning.

### Required Product Decisions

- **Target behavior**: Backtest results must be easier to read through clearer grouping, collapsible result sections, sticky table headers, visible report download entry, fully Chinese user-facing labels, visible stock names, and meaningful progress feedback.
- **Business rules**: Display and formatting changes must not alter backtest calculations, trading simulation, stock pool selection, data fetching, account state, or exported data values. Monetary display values in the web interface must use 2 decimal places across all user-facing pages.
- **Affected workflows/modules**: Backtest result page, shared result-table rendering, shared user-facing label display, monetary/price display formatting, report download presentation, and backtest progress display.
- **Protected workflows/modules**: Strategy logic, event-driven backtest accounting, trading rules, data source behavior, stock selection logic, account persistence, watchlist persistence, and exported raw report data must remain unchanged.
- **Acceptance criteria**: Users can run a backtest and read the top result area, table sections, transaction flow, holdings, and report download entry without raw untranslated field names, hidden table headers, or unexplained 0-to-1 progress jumps.
- **Out of scope**: Changing backtest formulas, changing recommendation rules, changing account balances, changing data-provider selection, changing exported calculation values, adding new strategy metrics, or redesigning non-result workflows beyond shared display formatting.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Read Backtest Results Clearly (Priority: P1)

As a user reviewing a completed backtest, I want the result page to be organized into clear sections so I can quickly understand summary, portfolio results, trades, holdings, diagnostics, and report access without scanning a long unreadable wall of tables.

**Why this priority**: The backtest output is the main value of the workflow. If the result page is not readable, users cannot evaluate the strategy.

**Independent Test**: Run any backtest that produces multiple result tables and verify that result sections are clearly grouped, large sections can be collapsed or expanded, and the primary result summary remains easy to locate at the top.

**Acceptance Scenarios**:

1. **Given** a completed backtest with several result tables, **When** the result page is shown, **Then** the main result summary appears at the top and each major result table is placed in a clearly titled section.
2. **Given** a result page with many tables, **When** the user wants to focus on one area, **Then** less important or large detail sections can be collapsed without losing the top summary.
3. **Given** a result page after a successful run, **When** the user looks for report output, **Then** a visible report download entry is available on the result page.

---

### User Story 2 - See Localized and Relevant Fields (Priority: P1)

As a user reading backtest output, I want all visible field names to be understandable Chinese labels, with stock names shown where available, and irrelevant technical fields removed from default tables.

**Why this priority**: Raw field names and missing stock names make the results difficult to trust and interpret.

**Independent Test**: Inspect the backtest result page and confirm that user-facing labels are Chinese, stock name columns are present where stock-level rows are shown, and the transaction flow omits fields that the user does not need.

**Acceptance Scenarios**:

1. **Given** a backtest result with stock-level rows, **When** tables are displayed, **Then** stock names appear alongside stock codes when the name is available; if the name is unavailable, the table shows a clear placeholder instead of omitting the name column.
2. **Given** a backtest result table, **When** column headers are displayed, **Then** no raw untranslated field names such as `shares_after` appear to users.
3. **Given** the transaction flow table, **When** it is displayed, **Then** `signal_time`, order status, and `slippage_cost` are not shown in the default transaction-flow view.
4. **Given** a field such as `shares_after` duplicates an already displayed "操作后持仓" meaning, **When** the table is displayed, **Then** the duplicate raw field is removed or represented once with a clear Chinese label.

---

### User Story 3 - Compare Numbers Reliably (Priority: P1)

As a user reading results across pages, I want money and price-like numbers to use a consistent 2-decimal display so amounts are easier to compare.

**Why this priority**: Inconsistent precision makes result pages noisy and harder to read.

**Independent Test**: Inspect account, strategy, backtest, result, holdings, transaction, and report summary pages and confirm that user-facing monetary display values use 2 decimal places.

**Acceptance Scenarios**:

1. **Given** any page that shows money, price, fees, market value, cash, cost, profit/loss, or transaction amount, **When** the value is displayed, **Then** it uses exactly 2 decimal places.
2. **Given** non-monetary quantities such as share counts, row counts, dates, percentages, or identifiers, **When** displayed, **Then** they keep an appropriate non-money format and are not incorrectly forced into money formatting.

---

### User Story 4 - Navigate Large Tables Without Losing Context (Priority: P2)

As a user reviewing long tables, I want table headers to remain visible while scrolling so I can understand which column I am reading.

**Why this priority**: Long backtest tables become hard to inspect when headers disappear during scrolling.

**Independent Test**: Open a backtest result table with more rows than fit on screen and scroll through it; verify that column headers remain visible or are otherwise repeated/preserved while reading.

**Acceptance Scenarios**:

1. **Given** a long table such as trades, daily portfolio, positions, or data quality, **When** the user scrolls within the table or down the page, **Then** the table header remains visible enough to identify columns such as stock code, price, amount, and date.
2. **Given** a wide table, **When** the user scrolls horizontally, **Then** header alignment remains clear and usable.

---

### User Story 5 - Understand Backtest Progress (Priority: P2)

As a user running a backtest, I want the progress area to show meaningful stages and counts rather than jumping from 0 to 1, so I know whether the system is still working and what it is doing.

**Why this priority**: Backtests can take time. A 0-to-1 progress indicator looks broken and gives no confidence during long runs.

**Independent Test**: Start a backtest with more than one stock or more than one trading day and observe the progress area; verify that it shows multiple named stages and processed counts when counts are known.

**Acceptance Scenarios**:

1. **Given** a backtest has started, **When** progress is displayed, **Then** it shows more than a binary 0/1 state.
2. **Given** a backtest is loading or evaluating multiple stocks, **When** progress updates, **Then** it shows the current stage and processed count such as completed items out of total items when that information is available.
3. **Given** a backtest is preparing results after computation, **When** progress updates, **Then** the progress text explains that results, tables, or report outputs are being prepared.

### Edge Cases

- Backtest result contains an empty table: the page shows a clear empty-state message and still keeps surrounding sections readable.
- A stock name is unavailable: the page shows the stock code and an explicit blank or fallback name without breaking table layout.
- A table contains a field without a known Chinese label: the page must not show raw field text as the final user-facing label; it must either map to a Chinese label or be hidden if not relevant.
- Very large result tables: the page remains navigable through collapsible sections, constrained table areas, sticky headers, or equivalent readable behavior.
- Mixed numeric types: monetary values use 2 decimals, while dates, shares, counts, ratios, and identifiers keep their correct formats.
- Report generation is unavailable or failed: the result page still shows the report area with a clear unavailable or failed state rather than hiding the entry.
- Backtest progress cannot know exact totals: the progress area still shows meaningful named stages and explanatory text rather than a silent or binary 0/1 indicator.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The backtest result page MUST present completed results in clearly separated sections with user-readable Chinese titles.
- **FR-002**: The backtest result page MUST allow large or low-frequency detail sections to be collapsed or expanded, while keeping the main result summary easy to find at the top.
- **FR-003**: The backtest result page MUST show a visible report download entry after a backtest run, including a clear state when the report is available, unavailable, or failed.
- **FR-004**: All user-facing table column names, summary labels, status labels, and section titles in backtest results MUST be Chinese user-facing labels; raw untranslated internal field names MUST NOT be shown as final labels.
- **FR-005**: Stock-level result tables MUST include a stock-name column alongside stock codes; rows with unavailable names MUST show a clear placeholder such as "未知" rather than removing the name column.
- **FR-006**: The default transaction-flow table MUST NOT show `signal_time`, order status, or `slippage_cost`.
- **FR-007**: If `shares_after` duplicates "操作后持仓" or equivalent information, the result page MUST show that information at most once with a clear Chinese label, not as a raw duplicate field.
- **FR-008**: Monetary and price-like values in the web interface MUST display with exactly 2 decimal places on every page where they appear.
- **FR-009**: Non-monetary values such as shares, dates, row counts, percentages, stock codes, and identifiers MUST keep appropriate readable formatting and MUST NOT be blindly formatted as money.
- **FR-010**: Long result tables MUST keep column headers visible or otherwise continuously available while users scroll through table rows.
- **FR-011**: Wide result tables MUST remain readable when horizontally scrolled, with headers staying aligned to data columns.
- **FR-012**: Backtest progress MUST display multiple meaningful stages rather than only a 0-to-1 transition.
- **FR-013**: Backtest progress MUST show processed count and total count when the work involves known stock counts, table counts, date counts, or other countable units.
- **FR-014**: Backtest progress MUST include short explanatory text describing the current stage, such as loading data, evaluating stocks, simulating trades, preparing tables, or preparing report output.
- **FR-015**: Result readability changes MUST NOT alter backtest calculation results, strategy decisions, transaction simulation, account persistence, watchlist persistence, or data source behavior.

### Key Entities

- **Backtest Result Page**: The user-facing page shown after a backtest run, containing summary, detailed tables, progress output, and report access.
- **Result Section**: A logical grouping of result content, such as summary, daily portfolio, trades, holdings, diagnostics, data quality, or report download.
- **Result Table**: A tabular display of backtest output rows with localized headers, formatted values, and readable scrolling behavior.
- **Transaction Flow View**: The default user-facing transaction table within backtest results, excluding fields the user marked as unnecessary.
- **Progress State**: The user-facing status shown while a backtest runs, including stage name, explanatory text, and optional completed/total counts.
- **Report Download Entry**: The visible result-page area or action that lets users access the generated detailed report or understand why it is unavailable.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a completed backtest result page, 100% of visible result section titles and table headers are readable Chinese labels, with no raw snake_case internal field names visible in default tables.
- **SC-002**: 100% of displayed monetary and price-like values across the web pages show exactly 2 decimal places.
- **SC-003**: In stock-level result tables where stock names are available, at least 95% of stock rows show a name next to the stock code; unavailable names are represented with a clear fallback.
- **SC-004**: The default transaction-flow view contains 0 occurrences of `signal_time`, order status, and `slippage_cost`.
- **SC-005**: A backtest result containing at least 50 table rows remains readable while scrolling because column headers remain visible or continuously available.
- **SC-006**: A backtest run with more than one stock or more than one trading day shows at least 3 meaningful progress stages before completion, and never presents progress solely as a 0-to-1 jump.
- **SC-007**: A completed backtest result page shows a report download entry in 100% of successful runs, or a clear unavailable state when report output is not available.

## Assumptions

- Users primarily read the web interface in Chinese.
- "金额数字" includes cash, principal, market value, cost, fees, price, transaction amount, realized/unrealized profit/loss, and portfolio value.
- Percentages and ratios are not considered monetary values and may keep their existing percentage/ratio formatting unless they are currently unreadable.
- The actual result content can remain near the top of the page; the requirement is to make that placement readable and structured, not to force it back into the lower placeholder "回测结果区".
- The report download entry can be a visible download action, link, button, or explicit report state as long as users can find it after a backtest run.
- This feature is a display/readability improvement and must not change the underlying event-driven backtest model or generated calculation values.
