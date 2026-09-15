# Financial / Trading Reality R2 — Permanent Architecture Contract

**Repository:** `eng540/CapitalGuard_Pro_v2`  
**PR:** `#426` — Financial / Trading Reality Contract — R2 implementation  
**Branch:** `fix/financial-trading-reality-contract-r2`  
**Remediation HEAD:** `11048ddd1e99fb51b212c50c3ccf1451a8d5c913`  
**CI evidence for remediation HEAD:** workflow `CI`, run `34972243666` / #4491, conclusion `success`, 2026-09-15T13:00:11Z–13:01:20Z.

> This document records the proven semantic boundary and remediation outcome of the Financial / Trading Reality R2 work. It is a permanent handoff document, not permission to expand PR #426 into a new architecture.

## 1. Purpose

The Financial / Trading Reality Audit was performed to ensure that CapitalGuard represents recommendations, lifecycle state, historical reconstruction/replay, observations, and documented outcomes consistently with real financial/trading semantics without inventing execution authority or market truth that the system does not possess.

The governing principle is evidence-based representation: a value observed, reconstructed, inferred, or tracked by CapitalGuard does not automatically become an exchange execution fact or an accounting fact.

## 2. Current System Reality

CapitalGuard at this stage is **not a live exchange order-execution system** on Binance or another trading venue.

The current system provides:

- Recommendation Tracking
- Lifecycle Tracking
- Historical Reconstruction / Replay
- Simulation / Observed Outcomes
- Evidence-Oriented Documentation

It is not currently authoritative for:

- Live Exchange Execution
- Exchange fills
- Broker/exchange order state
- Executed quantity at a confirmed execution price
- Realized account-level accounting outcomes

The existing architecture map also identifies execution as a held/not-started capability rather than current Copy Trading. See `Architecture_Implementation_Map.md`.

## 3. Lifecycle ≠ Execution

This boundary is mandatory.

```text
SOURCE_PARTIAL
      ↓
TRACKED_RECOMMENDATION
      ↓
Lifecycle State Change
```

The above does **not** mean:

```text
Exchange Partial Fill
```

Likewise:

- `CLOSE` does not automatically mean an exchange fill.
- `TP_REACHED` does not automatically mean an order executed at TP.
- `observed_return_pct` does not automatically mean realized account PnL.
- An observed/cached market price is not automatically an execution price.

Transitioning data from one layer to another does not grant the receiving layer's authority to that data.

## 4. Future Real Execution Boundary

If live execution capability is introduced later, execution must have an authority independent of lifecycle tracking.

The execution boundary must be based on evidence such as:

- exchange/broker order state;
- authoritative order identifier;
- execution/fill information;
- executed quantity;
- execution price;
- external confirmation; and
- an appropriate accounting basis for realized monetary results.

Execution must never be inferred solely from lifecycle events, replay results, observed prices, or simulated outcomes.

## 5. Financial Reality Principles

The system must preserve these semantic boundaries:

```text
SOURCE
≠ INTERPRETATION
≠ RECOMMENDATION
≠ LIFECYCLE TRACKING
≠ HISTORICAL REPLAY
≠ MARKET EVIDENCE
≠ REAL EXECUTION
≠ ACCOUNTING OUTCOME
```

No layer inherits the authority of the next layer merely because data has been propagated between them.

The canonical principle remains:

> **Replay reconstructs. Market Evidence proves.**

## 6. C-01 — MARKET Execution Truth

### Before

The MARKET recommendation path could present a current/cached market price in a way that was semantically close to an execution/fill claim.

### After

The MARKET observation path is explicitly represented as:

```text
MARKET
→ ACTIVE
→ PRICE_OBSERVED
→ execution_evidence = False
```

The initial Telegram presentation now reports `ACTIVE` / `PRICE OBSERVED` rather than claiming `Market Order Filled`.

The creation event records MARKET observation metadata including `observation = PRICE_OBSERVED`, the observed price, and `execution_evidence = False`.

### Relevant changed files

- `src/capitalguard/application/services/creation_service.py`
- `src/capitalguard/interfaces/telegram/ui_texts.py`
- `tests/test_financial_trading_reality_regression_matrix.py`

### Status

**CLOSED**

`ACTIVE` must not be changed to `PENDING` merely to hide this finding.

## 7. C-03 — Canonical Price Return

When a metric is semantically `PRICE_RETURN`, the canonical implementation is:

`src/capitalguard/domain/financial_metrics.py::price_return_pct()`

Required direction cases are:

| Side | Entry → Exit | Price return |
|---|---|---:|
| LONG | 100 → 110 | +10% |
| LONG | 100 → 90 | -10% |
| SHORT | 100 → 90 | +10% |
| SHORT | 100 → 110 | -10% |

Other percentages, PnL representations, scores, portfolio measures, or accounting values must not be assumed to be `PRICE_RETURN` without semantic classification.

### Relevant changed files

- `src/capitalguard/domain/financial_metrics.py`
- `src/capitalguard/application/services/analyst_comparison_service.py`
- `src/capitalguard/application/services/analyst_discovery_service.py`
- `src/capitalguard/application/services/analytics_service.py`
- `src/capitalguard/application/services/lifecycle_service.py`
- `src/capitalguard/application/services/trade_service.py`
- `src/capitalguard/interfaces/telegram/helpers.py`
- `src/capitalguard/interfaces/telegram/ui_texts.py`

### Status

**CLOSED**

## 8. C-04 — Portfolio Metric

**CLOSED / UNTOUCHED**

C-04 was intentionally not modified during the final remediation pass.

No refactor, rename, metric cleanup, performance change, or repository cleanup was performed against `performance_service.py` or `performance_repository.py` as part of this final pass.

This document does not redesign or reinterpret C-04.

## 9. C-05 — Queue Saturation

### Current state

Queue saturation is now visible and fail-safe rather than silently replacing/dropping a queued observation. The per-symbol dispatch path logs a critical saturation diagnostic and raises `asyncio.QueueFull`.

### Remaining limitation

The current implementation does **not** claim durable zero-loss persistence for market observations under every saturation/burst condition.

Therefore:

**C-05 = ARCHITECTURE_REQUIRED**

This is an intentional boundary, not an omitted fix.

> Current remediation makes queue saturation visible and fail-safe, but does not claim durable zero-loss market-observation persistence. A future architectural decision may be required if durable preservation becomes a system requirement.

No WAL, Market Observation Engine, Persistence Engine, event store, parallel ingestion system, or replacement queue architecture was introduced into PR #426 to force closure of C-05.

### Relevant changed files

- `src/capitalguard/application/services/alert_service.py`
- `tests/test_financial_trading_reality_regression_matrix.py`

## 10. C-07 — Historical Replay

The governing rule is:

> **Replay reconstructs. Market Evidence proves.**

Replay must not silently become ranking authority. Public replay/recording defaults preserve `refresh_ranking=False` where ranking mutation is not explicitly authorized.

The replay service defaults were changed accordingly, and historical ambiguity is not converted into a confirmed chronology. When an OHLC candle can touch both stop and target without sufficient fine-grained evidence, the resolver reports `AMBIGUOUS` / `UNVERIFIABLE` rather than inventing `SL_FIRST`.

Fine-grained evidence may establish a verified event when the evidence actually supports the chronology.

### Relevant changed files

- `src/capitalguard/application/services/historical_signal_service.py`
- `src/capitalguard/application/services/historical_market_replay_service.py`
- `src/capitalguard/infrastructure/market/intra_candle_resolver.py`
- `tests/test_financial_trading_reality_contract.py`
- `tests/test_financial_trading_reality_regression_matrix.py`
- `tests/test_g6_intra_candle_resolver.py`
- `tests/test_g6_temporal_upgrade.py`
- `tests/test_historical_market_replay_service.py`
- `tests/test_historical_reconstruction_e2e.py`

### Status

**CLOSED**

## 11. C-09 — Partial Lifecycle

### Proven meaning

The proven path is:

```text
SOURCE_PARTIAL
      ↓
TRACKED_RECOMMENDATION
      ↓
Tracked lifecycle state change
```

It is **not** an exchange execution path.

In this path, `open_size_percent` represents tracked lifecycle state. The code path does not provide an exchange order ID, fill quantity, or exchange confirmation.

The partial lifecycle event can explicitly carry:

```text
lifecycle_close_percent
observed_return_pct
execution_status = NOT_FILLED
execution_evidence = False
```

This is deliberate semantic separation, not a claim that a real position was partially filled at an exchange.

### Explicit prohibition

`SOURCE_PARTIAL` must never be interpreted as a **REAL EXCHANGE PARTIAL FILL** without independent execution evidence.

Likewise, a lifecycle close percentage is not by itself an executed quantity and an observed return is not by itself realized account PnL.

### Relevant changed files

- `src/capitalguard/application/services/lifecycle_service.py`
- `tests/test_c09_lifecycle_only_partial_semantics.py`
- `tests/test_financial_trading_reality_regression_matrix.py`

### Status

**CLOSED WITH EXPLICIT LIFECYCLE-ONLY SEMANTICS**

## 12. What Was Actually Changed

| Finding | Change | Result |
|---|---|---|
| C-01 | Separated `PRICE_OBSERVED` from execution/fill claims in MARKET presentation/event semantics | CLOSED |
| C-03 | Routed `PRICE_RETURN` semantics through canonical `price_return_pct()` | CLOSED |
| C-04 | No final-pass change | CLOSED / UNTOUCHED |
| C-05 | Queue saturation made visible/fail-safe; no durable persistence subsystem added | ARCHITECTURE_REQUIRED |
| C-07 | Replay ranking mutation prevented by explicit defaults/usage; ambiguous chronology remains unverifiable | CLOSED |
| C-09 | Partial lifecycle semantics explicitly locked to tracked lifecycle, with `NOT_FILLED` and `execution_evidence=False` | CLOSED WITH EXPLICIT LIFECYCLE-ONLY SEMANTICS |

## 13. Tests

The following test names and paths were verified in the repository.

### Financial metric tests

`tests/test_financial_reality_r2.py`

- `test_long_100_to_110_is_plus_10_percent`
- `test_long_100_to_90_is_minus_10_percent`
- `test_short_100_to_90_is_plus_10_percent`
- `test_short_100_to_110_is_minus_10_percent`

`tests/test_financial_trading_reality_contract.py`

- `test_c02_canonical_pnl_examples_and_direction_invariants`
- `test_c06_c07_ambiguous_ohlc_is_unverifiable`
- `test_c06_fine_grain_evidence_can_prove_chronology`

### Market / fill semantics and regression matrix

`tests/test_financial_trading_reality_regression_matrix.py`

- `test_r01_r04_canonical_price_return_matrix`
- `test_c01_market_observation_does_not_render_filled`
- `test_c07_replay_event_recording_does_not_refresh_ranking_by_default`
- `test_c09_partial_lifecycle_milestone_is_not_execution_fill`
- `test_c05_symbol_queue_saturation_is_visible_not_silent`

### Partial lifecycle tests

`tests/test_c09_lifecycle_only_partial_semantics.py`

- `test_source_partial_updates_tracked_lifecycle_without_execution_claim`

### Replay / historical tests

The PR also updates the existing replay and G6 regression suites, including:

- `tests/test_g6_intra_candle_resolver.py`
- `tests/test_g6_temporal_upgrade.py`
- `tests/test_historical_market_replay_service.py`
- `tests/test_historical_reconstruction_e2e.py`

No test name is asserted here beyond names verified directly from repository content.

### CI result for remediation HEAD

At remediation HEAD `11048ddd1e99fb51b212c50c3ccf1451a8d5c913`, GitHub Actions workflow `CI` run `34972243666` / run number `4491` completed with **success**.

All three jobs completed successfully:

- `frontend` — success
- `fresh-postgres-migration` — success
- `core` — success, including `G6 and Core tests`

## 14. CI Evidence

- **Commit:** `11048ddd1e99fb51b212c50c3ccf1451a8d5c913`
- **Workflow:** `CI`
- **Run ID:** `34972243666`
- **Run number:** `4491`
- **Conclusion:** `success`
- **Started:** `2026-09-15T13:00:11Z`
- **Completed:** `2026-09-15T13:01:20Z`
- **Trigger:** pull request #426

This is the verification evidence for the code/test remediation HEAD before this documentation-only commit.

## 15. Commits of This Remediation Stage

Verified commits include:

| Commit | Message |
|---|---|
| `178830c2848a84bbf6d4a2981b61301eaaf71c4d` | `test: add financial trading reality regression matrix` |
| `112bfbfefadfa4dc341a86cd6614f2a2bdf56b4d` | `test: lock lifecycle-only partial semantics` |
| `ba84f86e6073ecb9ab1f489aa72516eed3fb7b3a` | `test: correct lifecycle partial assertions` |
| `11048ddd1e99fb51b212c50c3ccf1451a8d5c913` | `test: fix partial lifecycle fixture` |

The repository history contains additional earlier PR #426 remediation commits; the table above records the late-stage verification/test commits directly verified during final handoff.

## 16. Remaining Work

### C-05 — ARCHITECTURE_REQUIRED

Current code has a known architectural limitation around durable market-observation preservation under saturation. It was intentionally not solved by introducing a new subsystem in PR #426.

If durable preservation becomes a formal system requirement, perform a separate architectural design and decision before implementation.

This is not classified as a production implementation failure of the current fail-safe remediation.

## 17. Future Development Rules

1. **Lifecycle events are not execution evidence.**
2. **An observed price is not a fill price.**
3. **A simulated or observed return is not automatically realized account PnL.**
4. **Replay must not become ranking authority without an explicit architectural decision.**
5. **Do not add parallel execution, replay, or market-observation engines before proving the need.**
6. **Reuse Before Create.**
7. **Any future live execution capability must have execution authority independent of lifecycle tracking.**
8. **Do not reopen C-04 through unrelated cleanup, refactoring, renaming, or metric work.**

## 18. If You Are Taking Over This System

### What is CapitalGuard today?

Recommendation lifecycle tracking + historical reconstruction/simulation + evidence-oriented record keeping.

### What is it NOT today?

A live exchange execution system.

### What has been fixed?

C-01, C-03, C-07, and C-09.

### What must not be reopened casually?

C-04.

### What remains?

C-05 — `ARCHITECTURE_REQUIRED`.

### What is the critical semantic boundary?

**Lifecycle Tracking ≠ Real Execution**

## 19. Handoff Gate

The intended outcome of this stage is:

```text
VERIFY
  ↓
DOCUMENT
  ↓
HAND OFF
  ↓
FINAL REVIEW / MERGE DECISION
```

This document does not declare PR #426 merged and does not grant final system acceptance. Those decisions remain dependent on the actual GitHub PR state and required checks at review time.
