# Implementation Plan: Backtest Result Readability

**Branch**: `[002-backtest-result-readability]` | **Date**: 2026-07-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-backtest-result-readability/spec.md`

## Planning Preconditions

- **Spec readiness**: READY FOR PLANNING
- **Unresolved clarification items**: None
- **Target behavior confirmed**: Yes
- **Business rules confirmed**: Yes
- **Protected scope confirmed**: Yes

Planning may proceed because the specification defines the result readability problems, protected calculation scope, and measurable acceptance criteria.

## Summary

Improve the web backtest result experience without changing the event-driven backtest engine, account state, stock-pool selection, strategy decisions, data providers, or exported calculation values. The work is limited to display contracts, table rendering, field localization, money/price formatting, report-download visibility, and progress feedback for the backtest page.

The safest approach is to centralize presentation rules in the existing web layer, reuse existing result payloads, and add focused regression tests that prove calculation payloads are unchanged while the user-facing output becomes readable.

## Technical Context

**Language/Version**: Python 3.x in the existing local project environment.

**Primary Dependencies**: Existing standard-library style web app in `examples/web_app.py`; existing project modules for backtest, thermostat strategy, account, market data, and watchlists.

**Storage**: Existing local project data files and caches only; no storage schema change.

**Testing**: `pytest`, with focused coverage in `tests/test_web_app.py` and any existing backtest/result tests that validate payload compatibility.

**Target Platform**: Local Windows web workflow served from the repository and opened at `127.0.0.1:8765`.

**Project Type**: Python stock-selection application with local web UI, examples, tests, and Spec Kit feature docs.

**Performance Goals**: Rendering large backtest tables must remain usable through collapsible sections and constrained/sticky table layouts; no new full recomputation or extra market-data fetch should be introduced for display-only changes.

**Constraints**: Do not alter event-driven backtest calculations, strategy rules, account persistence, watchlist persistence, data-provider behavior, exported raw values, or report calculation values.

**Scale/Scope**: Backtest result pages with many rows and wide tables; stock-level rows should include names where already available or resolvable through existing metadata.

## Constitution Check

The current constitution file is still a placeholder and does not define enforceable project gates. The plan therefore applies the active project constraints from the feature specification:

- Preserve core calculation and data behavior.
- Keep changes focused on web presentation and tests.
- Add regression coverage before implementation tasks.
- Avoid new dependencies unless existing tooling cannot satisfy the UI requirement.

**Gate status**: PASS. No complexity exception is required.

## Project Structure

### Documentation

```text
specs/002-backtest-result-readability/
|-- spec.md
|-- plan.md
|-- research.md
|-- data-model.md
|-- quickstart.md
|-- contracts/
|   `-- backtest-result-readability-contract.md
`-- checklists/
    `-- requirements.md
```

### Source Code

```text
examples/
`-- web_app.py                  # Existing web UI, form rendering, job/progress rendering, result rendering

tests/
`-- test_web_app.py             # Web rendering, route, formatting, and progress behavior tests

stock_picker/                   # Existing backtest, thermostat, market-data, account, and watchlist modules
`-- ...                         # Protected calculation/data modules; only read or consume existing outputs if needed
```

**Structure Decision**: Keep the feature in the existing single Python web app structure. Do not create a new frontend framework or separate service. Presentation helpers may be extracted only if they reduce duplication in `examples/web_app.py` and stay covered by tests. Money/price formatting must be implemented as a shared display helper used by all web pages that render monetary values, not as one-off formatting only inside the backtest result page.

## Modules To Modify

- `examples/web_app.py`: Backtest result rendering, table section grouping, collapsible sections, sticky/wide table markup, Chinese label mapping, money/price display formatting, stock-name display, report-entry display, and backtest progress presentation.
- `tests/test_web_app.py`: Regression tests for Chinese labels, hidden transaction fields, stock-name display, two-decimal money formatting, report-entry states, table readability markup, and multi-stage progress.
- Optional existing test fixtures/helpers: Add or extend only if current tests need stable sample backtest payloads.

## Behaviors To Keep Unchanged

- Event-driven backtest engine calculations.
- Strategy and thermostat decision logic.
- Trade simulation rules, commissions, taxes, slippage calculation, and cash/position accounting.
- Stock-pool source selection semantics.
- Data-source selection and cache behavior.
- Account and watchlist persistence.
- Exported raw report values and calculation precision.
- Existing route compatibility and form parameter names unless a display-only alias is already required by the UI.

## Phase 0 Research

Research output is captured in [research.md](research.md). Key decisions:

- Treat this feature as a display-layer contract, not a calculation change.
- Centralize field label, visibility, and format decisions so raw internal names do not leak.
- Keep raw data values unchanged while formatting only the web-visible presentation.
- Show report download entry as a stateful result-page component.
- Upgrade backtest progress text and counts using existing job progress information where possible.

## Phase 1 Design

Design output is captured in:

- [data-model.md](data-model.md)
- [contracts/backtest-result-readability-contract.md](contracts/backtest-result-readability-contract.md)
- [quickstart.md](quickstart.md)

The design defines display-only entities for result sections, table views, display fields, progress states, stock identity display, and report download states.

## Testing Strategy

1. Add failing tests first for the user-visible regressions:
   - Backtest result headers and section titles are Chinese.
   - Default transaction table hides `signal_time`, order status, and `slippage_cost`.
   - `shares_after` appears at most once as a Chinese label or is hidden when duplicated.
   - Money/price-like values render with exactly two decimals.
   - Non-money values are not incorrectly formatted as money.
   - Stock-level rows include stock names or the configured fallback.
   - Report download entry is visible after completion or shows an unavailable state.
   - Long/wide table markup supports sticky headers and horizontal readability.
   - Backtest progress includes multiple named stages and counts when known.
   - Account, strategy, backtest form/result, holdings, transaction, and summary displays use the same money/price formatting helper.

2. Add regression checks that protected behavior did not change:
   - Existing backtest result payload values remain the same before display formatting.
   - Existing route/form parameters still work.
   - Existing account/watchlist/strategy tests continue to pass.

3. Run focused tests after each implementation group:
   - `.\.venv\Scripts\python.exe -m pytest tests\test_web_app.py -q`

4. Run final verification:
   - `.\.venv\Scripts\python.exe -m pytest -q`
   - Manual browser check of a representative backtest result page with more than one stock and a custom date range.

## Steadier Implementation Path

1. First add characterization tests around the current rendered HTML for backtest results and progress, using small synthetic result payloads so tests do not depend on network data.
2. Add shared display helpers for labels, hidden fields, duplicate fields, and money/price formatting; verify these helpers independently before changing page layout.
3. Apply the shared helpers to existing tables while keeping the current page structure; this proves localization and formatting before visual restructuring.
4. Add collapsible sections, sticky headers, horizontal table containers, and report-entry placement after the display contract is stable.
5. Improve progress stages last, using existing job state first and adding only lightweight stage updates where current progress lacks countable steps.
6. Finish with a manual browser smoke check against the local `/backtest` page, including a custom date range and a stock pool with multiple symbols.

## Risks

- **Field classification risk**: A value may be incorrectly treated as money or non-money. Mitigation: explicit field-format mapping with tests for dates, share counts, ratios, prices, fees, cash, and identifiers.
- **Label coverage risk**: New or uncommon internal fields may leak raw names. Mitigation: default unknown fields to hidden or a controlled Chinese fallback instead of displaying raw snake_case.
- **Stock-name availability risk**: Existing payloads may not always include names. Mitigation: show a clear fallback and avoid adding new network fetches solely for names.
- **Progress accuracy risk**: Some stages may not know exact totals. Mitigation: show named stages and explanatory text even when counts are unavailable.
- **Layout risk**: Sticky headers can behave differently across browsers. Mitigation: keep CSS simple and verify in the local browser workflow.
- **Scope creep risk**: Result readability could invite backtest logic changes. Mitigation: tasks must explicitly avoid calculation modules except for read-only contract checks.

## Why This Is Not Overly Complex

- It keeps the existing Python web app and route structure.
- It does not introduce a new frontend framework, database schema, job system, or data source.
- It centralizes repeated presentation rules instead of patching every table independently.
- It limits domain changes to display contracts and regression tests.
- It preserves raw result/export values, so correctness risk is contained.

## Complexity Tracking

No constitution violations or complexity exceptions are required.
