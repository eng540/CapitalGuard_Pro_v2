# CapitalGuard Pro Frontend Redesign Specification

## Decision

The frontend is reorganized around three user-facing hubs rather than a flat list of operational pages:

1. **Radar** — capture, inspect, and understand one message, multiple messages, or a historical import.
2. **Portfolio** — follow active and pending records, read timelines, and perform explicit Core-governed actions.
3. **Studio & Operations** — create/publish analyst recommendations and handle owner/admin review and system operations.

The three hubs are an information-architecture decision, not a replacement for Core domains. Existing routes remain available for backward compatibility while the primary shell exposes only these hubs.

## UX contract

Every important result follows this order:

> **What was received → What was extracted → What was verified → What needs attention → What can be done next.**

No page may calculate PnL, decide historical/live routing, infer replay readiness, or create a business state. Those responsibilities remain in Core. The web server adapts Core contracts, and presentation view models translate typed Core states to human-readable copy.

## Hub boundaries

| Hub | Owns | Does not own |
|---|---|---|
| Radar | capture entry, extraction result, source/timestamp/provenance, batch summary, historical inspection | trade activation, replay decisions, financial calculations |
| Portfolio | Core-backed tracked records, lifecycle timeline, explicit user commands | raw ingestion, owner review policy, local PnL or lifecycle decisions |
| Studio & Operations | analyst composition/publication, owner review, evidence/replay operations, system health | consumer capture flow and local business state |

## State presentation

Core states are mapped through typed presentation descriptors containing `label`, `description`, `severity`, `nextAction`, and `allowedActions`. Components must not infer semantics from arbitrary status strings or substring matching.

## Performance contract

The shell must remain responsive on mobile. Hub pages use one primary read model where available, lazy-load detail queries, pause polling when hidden, and retain important errors in the page instead of relying only on transient toasts. No duplicate business read model or frontend database is allowed.

## Rollout

- Stage A: shell, three hub navigation, default Radar entry, and role-aware secondary links.
- Stage B: Radar capture and Inspector in one result-first flow.
- Stage C: Portfolio and lifecycle details with contextual actions.
- Stage D: Studio and Operations split by role and task.
- Stage E: Telegram result card and public proof share, only when Core returns verified data.
- Stage F: mobile/accessibility/performance acceptance.

Each stage is independently tested and merged. A stage must not be considered complete merely because a route renders; the expected user result and failure state must be observable.
