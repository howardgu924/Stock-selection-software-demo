# Research: Backtest UI Controls

## Decision: Reuse existing thermostat stock-pool source concepts

**Rationale**: The strategy page already exposes user-facing concepts for 手动输入, 自选股组合, 市场范围, and supported candidate sources. Reusing those concepts keeps the workbench coherent and reduces the chance that backtest and live strategy runs interpret the same source differently.

**Alternatives considered**:

- Keep raw `symbols` input and add helper text only. Rejected because it does not satisfy the requirement for selectable stock pools.
- Build a separate backtest-only source model. Rejected because it would duplicate behavior and increase drift from the thermostat page.

## Decision: Resolve stock pool before calling the existing backtest function

**Rationale**: The event-driven backtest should continue receiving a concrete symbol list. Resolving the selected UI source at the web handler boundary keeps the backtest engine unchanged and preserves output compatibility.

**Alternatives considered**:

- Teach the backtest engine about watchlists and market ranges. Rejected because that changes protected internal behavior.
- Resolve stock pools in the browser only. Rejected because the local server must validate the submitted source and prevent stale hidden values.

## Decision: Add backtest-specific date range resolution

**Rationale**: Backtest needs a preset list that includes 最近 5 个月. The strategy page helper currently serves the strategy workflow and does not need to change. A small backtest-specific resolver avoids accidental strategy-page behavior changes.

**Alternatives considered**:

- Reuse the strategy date helper directly. Rejected because it does not currently include 最近 5 个月 and changing it may alter the strategy page.
- Keep raw start/end inputs. Rejected because the user explicitly reported this as the problem.

## Decision: Treat unsupported candidate sources as unavailable

**Rationale**: The spec requires unsupported sources to be marked unavailable rather than silently falling back. This avoids misleading backtest results.

**Alternatives considered**:

- Display all source options and let failures happen on submit. Rejected because it creates avoidable confusion.
- Silently convert unsupported sources to manual input. Rejected because it violates active-source correctness.

## Decision: Verify with focused web tests before full regression

**Rationale**: The feature is primarily a web input workflow. Focused tests can prove rendering, handler routing, validation, and stale-state behavior quickly, while full pytest protects broader regressions before completion.

**Alternatives considered**:

- Manual browser verification only. Rejected because stale form state and handler routing are easy to regress.
- Only full pytest. Rejected because it would not necessarily assert the new UI requirements.
