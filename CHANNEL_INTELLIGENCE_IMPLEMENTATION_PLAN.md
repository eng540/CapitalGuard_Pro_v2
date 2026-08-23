# Channel Intelligence & Evidence Reconstruction — G0 Implementation Plan

**Scope.** This is a code-grounded G0 audit only. It adds no table, migration, domain object, parser rewrite, AI pipeline, trust computation, live-trading behavior, or replay change. Its purpose is to decide the smallest safe Gate 1.

## 1. Verified current trace: forwarded Telegram message to historical outcome

| Step | Repository code | Input → output | Persisted state | Classification |
|---|---|---|---|---|
| Telegram intake | `src/capitalguard/interfaces/telegram/historical_forwarding_handler.py` → `direct_historical_forward_handler()` | Telegram message → `ForwardedMessageInput` | none directly | REUSE |
| Source discovery and temporal routing | `application/services/frictionless_ingestion_service.py` → `discover_source()`, `start_or_reuse_auto_batch()`, `temporal_metadata_for_message()`, `stage_direct_message()` | forwarded input + parsed preview → source/batch/temporal metadata | shadow/canonical source, temporal decision, batch | REUSE |
| Staging | `application/services/historical_forwarding_service.py` → `stage_message()` | input → `HistoricalForwardReceipt` | `historical_forward_receipts` | REUSE |
| Dry-run / explicit decision | same service → `preview_batch()`, `apply_preview_decision()`; Telegram `_finalize_auto_batch_job()` and `historical_preview_decision_callback()` | staged receipt → manifest / owner-review request | `historical_import_batches` state | REUSE |
| Owner review | `application/services/historical_owner_review_service.py` | review command → validated batch | batch review metadata/audit | REUSE |
| Evidence and parsed signal | `application/services/historical_evidence_ingestion_service.py` → `ingest_reviewed_batch()` / `_ensure_replayable_signal()` | validated receipt → immutable evidence, then fully parsed historical signal | `historical_signal_evidence`, `historical_signals` | REUSE / recently corrected |
| Deterministic parsing | `application/services/historical_parser_service.py` → `parse()` over `ParsingService` | raw text → `HistoricalParseResult` | parse status/errors currently copied to receipt metadata; primary financial fields copied to signal | EXTEND |
| Replay eligibility and command | `application/services/web_command_service.py` → `replay_reviewed_batch_from_binance()` | EVIDENCE_INGESTED batch → bounded signal replay calls | command audit / historical results | REUSE |
| Market replay | `application/services/historical_market_replay_service.py` → `replay_from_binance()` / `replay_candles()` | signal + UTC range → market evidence + verified activation/SL/TP events | `historical_market_evidence`, `historical_signal_events` | DO NOT TOUCH |
| Owner display | `frontend/client/src/pages/Admin.tsx` through server-only Adapter/tRPC | batch result → owner status/toast | no financial Web DB copy | EXTEND presentation only |

> The verified historical path is **historical-only**. `HistoricalSignal` records must not enter recommendation publication, UserTrade, outbox, broker, or live execution flows.

## 2. Current component reuse map

| Capability | Existing component | Actual responsibility | G0 decision |
|---|---|---|---|
| Channel identity | `ChannelCatalog`, `HistoricalShadowChannel`, `HistoricalChannelClaimService` | distinguishes discovered/claimable source from reviewed canonical mapping | REUSE |
| Raw forwarding receipt | `HistoricalForwardReceipt` | intake receipt, transport/source identity, raw text, revision number, provenance, dedup constraints, staging status | EXTEND, not replace |
| Immutable evidence | `HistoricalSignalEvidence` | evidence for one imported source message, hash, source URI, ownership proof, batch linkage | REUSE |
| Parsed signal | `HistoricalSignal` | one fully parsed historical trade claim with entry/SL/targets/time and evidence link | REUSE |
| Replay outcomes | `HistoricalSignalEvent` | market-verified activation/SL/TP events, not source-message update events | EXTEND boundary documented |
| Market proof | `HistoricalMarketEvidence` | bounded OHLCV artifact, provider, range, hash and replay run reference | REUSE |
| Source claims and review | `HistoricalSignalAttribution` | analyst/channel/trader attribution plus proof and review | REUSE |
| Parser | `ParsingService` + `HistoricalParserService` | normalization, Arabic digits/suffixes, asset/side/entry/SL/TP extraction, financial consistency | REUSE / EXTEND |
| AI/OCR | `image_parsing_service.py` and parsing infrastructure | external adapter only; not part of historical evidence-to-replay path | DO NOT TOUCH in G0 |

## 3. Canonical-message decision

`HistoricalForwardReceipt` is **an ingestion receipt**, not yet a complete canonical message aggregate. It carries raw text, source and receiver identities, source revision, source/reply timestamps, provenance and deduplication. It does **not** currently provide an immutable message-level lifecycle shared across revisions, durable interpretation versions, or relationships across messages.

`HistoricalSignalEvidence` is immutable source-message evidence. It does not represent market evidence, a parse/AI interpretation version, or a relationship graph. `HistoricalMarketEvidence` is the distinct market-proof record.

**G1 decision to validate:** extend the existing receipt/evidence identity path first. A new canonical message/revision table is justified only if G1 proves that one receipt cannot model a source message with multiple retained revisions and relationship edges without breaking source provenance. G0 authorizes no new table.

## 4. Current parser and confidence boundary

`HistoricalParserService.parse()` returns `PARSED`, `PARTIAL`, or `UNPARSED`, a deterministic confidence score, extracted asset/side/entry/stop/targets, financial-consistency findings, and claimed outcome reconciliation. It is deterministic-first; no LLM result is market truth. Current gaps are source spans, parser/ruleset version persistence, explicit taxonomy beyond parse status, entry zones/conditions/leverage, and conflict fusion.

Future AI/OCR may only be a versioned adapter behind validation. It may contribute an interpretation; disagreement with deterministic extraction must become `CONFLICT`/review, never an arbitrary signal.

## 5. Replay contract and non-negotiable boundaries

`HistoricalMarketReplayService` requires a signal with UTC `decision_timestamp`, asset, side, entry, stop and targets. `replay_from_binance()` fetches a bounded 1m OHLCV window and rejects no-candle responses. `replay_candles()` validates UTC/positive values, stores a content-hashed `HistoricalMarketEvidence`, emits idempotent verified events, and applies `PESSIMISTIC_SL_FIRST` when a candle crosses stop and target.

The current batch command derives the range from Core; the browser never supplies signal ID or UTC range. Provider failure is fail-closed and transaction rollback is covered. G1–G4 must consume this contract, not rewrite it.

## 6. G1 implementation specification — not implemented by this document

G1 shall be limited to canonical historical message/revision/provenance and a relationship foundation. It must preserve raw source claims, retain revisions, distinguish source assertions from market facts, and create no live recommendation or trade. It must not introduce AI, trust, metrics redesign or replay modification.

Required G1 acceptance trace: a known source message and an update message can be stored with immutable provenance, evaluated as `NEW_RECOMMENDATION` / `SL_UPDATE` / `TP_UPDATE` / `CLOSURE` / `COMMENTARY` / `UNKNOWN`, and linked only with explicit confidence/review semantics. It does not yet assert market outcome.

## 7. Evidence required before a system-level readiness claim

The following seven confidence layers require separately traceable proof: source provenance, extraction, interpretation, message relationship, replay/data, outcome verification, and statistical confidence. A complete claim requires an E2E dataset covering original messages, revisions, updates, cancellations, results without originals, duplicates, provider errors, and non-recommendation content; each outcome must link back to immutable source and market proof.

