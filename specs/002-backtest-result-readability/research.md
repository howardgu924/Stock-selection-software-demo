# Research: Backtest Result Readability

## Decision 1: Keep Changes In The Display Layer

**Decision**: Treat this feature as a web presentation change. Backtest engines, strategy decisions, account persistence, stock-pool selection, data-source behavior, and exported raw calculation values remain unchanged.

**Rationale**: The user reported readability, localization, formatting, progress, and result-page usability problems. None require changing calculation behavior.

**Alternatives Considered**:

- Change backtest output schemas: rejected because it risks breaking front-end compatibility and exported reports.
- Recompute or enrich results in backtest modules: rejected unless existing outputs already lack required display fields.

## Decision 2: Centralize Field Labels, Visibility, And Formatting

**Decision**: Use a single presentation mapping for table fields that defines Chinese label, default visibility, duplicate handling, and display format category.

**Rationale**: The same untranslated field problem has appeared repeatedly. A central map reduces inconsistent one-off fixes.

**Alternatives Considered**:

- Rename fields in raw payloads: rejected because it changes internal contracts.
- Translate only the currently visible table: rejected because future tables would leak raw fields again.

## Decision 3: Format Only User-Facing Values

**Decision**: Apply two-decimal formatting to money and price-like web display values while leaving raw values and exported calculation precision unchanged.

**Rationale**: The requirement is readability across pages, not changing numerical data.

**Alternatives Considered**:

- Round values in payloads: rejected because it changes calculation/report data.
- Format every number with two decimals: rejected because dates, shares, counts, IDs, and percentages need different formats.

## Decision 4: Make Backtest Result Sections Collapsible

**Decision**: Keep the actual backtest result content near the top, but group it into titled sections with collapsible details for large tables.

**Rationale**: The user explicitly said top placement is acceptable; the problem is readability.

**Alternatives Considered**:

- Move all results into the lower placeholder area: rejected because placement was not the issue.
- Show all result tables expanded by default: rejected because it does not solve long-page readability.

## Decision 5: Preserve Table Context During Scrolling

**Decision**: Large and wide tables should use constrained table containers, sticky headers or equivalent repeated headers, and horizontal scrolling with aligned headers.

**Rationale**: Users lose column context when scrolling through long backtest results.

**Alternatives Considered**:

- Paginate every table: rejected as heavier and more disruptive.
- Export-only workflow for large tables: rejected because users need readable in-page inspection.

## Decision 6: Use Existing Job Progress Data First

**Decision**: Improve progress display using existing job stages, current messages, and processed/total counts where available. Only add lightweight progress states where the current web job wrapper lacks them.

**Rationale**: The user needs meaningful progress, but the feature should not rewrite the job system.

**Alternatives Considered**:

- Replace the job/progress subsystem: rejected as too broad.
- Keep binary 0-to-1 progress: rejected by the acceptance criteria.

## Decision 7: Report Entry Is Always Visible After A Run

**Decision**: The result page must show a report area after completion, with available, unavailable, or failed state.

**Rationale**: Users currently cannot find the report download entry.

**Alternatives Considered**:

- Hide report UI when unavailable: rejected because it makes failures ambiguous.
- Move report generation into a separate feature: rejected because visibility of the existing report state is part of the readability problem.
