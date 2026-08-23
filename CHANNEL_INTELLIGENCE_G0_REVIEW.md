# Channel Intelligence & Evidence Reconstruction — G0 Review

## Gate decision

**G0 status: documentation audit in progress; no functional implementation is approved by this document.** The following conclusions are based on current Core code paths and tests, not naming alone.

## HistoricalForwardReceipt assessment

| Question | Code-backed answer | Decision |
|---|---|---|
| What is it? | A forwarded-message intake receipt (`historical_forwarding.py`) created by `HistoricalForwardingService.stage_message()`. | REUSE |
| Identity | receiver chat/message uniqueness plus batch/source chat/source message/source revision uniqueness. | REUSE |
| Provenance | source origin type, source/reply/edit timestamps, raw text, content hash and metadata. | REUSE / EXTEND |
| Lifecycle | STAGED, rejected variants, then INGESTED through reviewed evidence ingestion. | REUSE |
| Immutable raw revision aggregate? | It records one received revision but does not yet provide a source-message aggregate with retained revision lineage. | GAP: G1 candidate |
| Interpretation or relationship graph? | No durable classification, interpretation version or inter-message relationship entity exists. | GAP: G1+ |

## Claim / interpretation / market-fact separation

| Layer | Current location | Status |
|---|---|---|
| Source claim | `HistoricalForwardReceipt.raw_text` and `HistoricalSignalEvidence.raw_text` | present |
| System interpretation | `HistoricalParseResult` and copied signal fields | partial; provenance/version/spans are incomplete |
| Market fact | `HistoricalMarketEvidence` and replay-created `HistoricalSignalEvent` | present for successful bounded replay |

## Assurance Matrix — current evidence, not a readiness claim

| Stage | Status | Code evidence | What is still required before assurance |
|---|---|---|---|
| Message intake | **Proven** for authorized historical forwards | `direct_historical_forward_handler()` → `FrictionlessIngestionService.stage_direct_message()` | live/external import parity inventory |
| Immutable source evidence | **Proven** for reviewed forwards | `HistoricalForwardReceipt` → `HistoricalSignalEvidence` through `ingest_reviewed_batch()` | revision lineage aggregate |
| Parsing / classification | **Partial** | `HistoricalParserService.parse()` produces PARSED/PARTIAL/UNPARSED and financial fields | content taxonomy, source spans, parser/ruleset versions, non-recommendation classification |
| Relationship | **Not implemented** | source reply ID is stored only | explicit relationship graph, confidence and review rules |
| Historical signal / events | **Partial** | fully parsed evidence creates `HistoricalSignal`; replay writes verified events | source-authored update lifecycle and message evidence links |
| Replay | **Proven** for bounded complete signals | `HistoricalMarketReplayService.replay_from_binance()` / `replay_candles()` | real-provider UAT that produces a documented outcome, not merely command success |
| Market evidence | **Proven** when candles exist | `HistoricalMarketEvidence` artifact hash/range/provider | provider availability and retained artifact verification in UAT |
| Outcome / metrics | **Partial** | activation/SL/TP replay events and historical quality/reputation services | MFE, MAE, R, duration and a verified aggregate metric contract |
| Admin / end-user traceability | **Partial** | owner batch/replay surface exists | drill-down source → interpretation → relationship → signal → market evidence → outcome |

> **Release rule:** CapitalGuard must not claim that the complete Channel Intelligence & Evidence Reconstruction Pipeline is guaranteed, complete, or production-ready until a documented E2E test proves the entire row sequence above from a real or approved historical message set to an auditable rendered outcome. `UNKNOWN`, `PARTIAL`, `CONFLICT`, provider failure, and review-required are correct fail-closed results—not evidence of readiness.

## Gap register

| Capability | Existing component | Gap | G0 action |
|---|---|---|---|
| Raw message | HistoricalForwardReceipt | no canonical multi-revision lineage | EXTEND decision in G1 |
| Revision | receipt revision number | no retained canonical message/revision relationship | IMPLEMENT only if extension proof fails |
| Classification | HistoricalParserService parse status | no rich content taxonomy | EXTEND |
| Extraction | ParsingService/HistoricalParserService | missing source spans/versioned interpretations/conflict representation | EXTEND |
| Relationships | source reply ID only | no relationship engine or confidence graph | IMPLEMENT in G1/G3 sequence |
| Lifecycle | HistoricalSignal + market events | source-authored updates are not lifecycle events yet | EXTEND |
| Replay | HistoricalMarketReplayService | works only from complete signal; no redesign permitted | DO NOT TOUCH |
| AI/OCR | external image adapter | not part of audited historical pipeline | DO NOT TOUCH until later contract gate |
| Trust | reputation/release services | historical quality gates exist, but seven-layer confidence is not complete | DO NOT TOUCH in G0 |

## Risk register

| Risk | Control |
|---|---|
| Treating commentary or a result claim as a trade signal | taxonomy with `UNKNOWN` / `COMMENTARY` / `RESULT_CLAIM` and review before signal creation |
| Losing original source meaning during update processing | immutable raw content and revision lineage before relationship automation |
| Look-ahead bias | retain `decision_timestamp`; existing replay ignores earlier/future-invalid candles and uses bounded UTC evidence |
| AI hallucination | deterministic-first validation; AI as interpretation only; conflicts require review |
| Historical data affecting live finance | retain current isolation; no Recommendation/UserTrade/Outbox side effect |
| Schema drift | fresh and existing schema migration checks; treat UAT schema errors as release blockers |

## Do-not-touch list

`HistoricalMarketReplayService`, Binance provider contract, live recommendation lifecycle, UserTrade, broker/execution paths, commercial flags, parser behavior, existing historical schema, and AI/OCR integration are not changed in G0.

## G0 exit criteria

1. Both documents are reviewed against named code paths.
2. The current UAT result is recorded as partial: replay command reached Core for one batch; `0` events is not a verified market outcome.
3. A future Gate 1 PR specification exists with no implementation mixed in.
4. No existing live or historical contract is changed.
