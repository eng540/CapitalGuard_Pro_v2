# Historical Intake Usage

## Purpose

The existing Historical workspace accepts one message, multiple messages, or a batch through the current Web interface. The intake is historical and non-commercial: it does not create a live recommendation, `UserTrade`, order, publication, or copy-trading action.

## Supported input modes

| Mode | Input | Source confidence | Notes |
|---|---|---|---|
| `PASTE` | One or more text messages | `UNVERIFIED` | Separate multiple messages with a line containing `---`. |
| `UPLOAD` | `.txt`, `.json`, or `.csv` content | `UNVERIFIED` unless source IDs are present | JSON arrays are treated as multiple items. |
| `TELEGRAM_EXPORT` | Telegram export JSON with `messages` or `records` | `VERIFIED_PROVENANCE` when channel/message IDs are present | Existing Telegram source identity is preserved. |
| Telegram Forward | Existing Telegram bot flow | Existing Telegram provenance | Uses the existing frictionless ingestion and auto-batch services. |

## Processing lifecycle

```text
Input 1..N
→ HistoricalImportBatch
→ HistoricalForwardReceipt per item
→ Canonical message/revision memory
→ Parser and semantic materialization
→ Preview with per-item status
→ Owner Review
→ Evidence Ingestion
→ G5 HistoricalSignal materialization
→ G6 Market Replay
→ Outcome Reconciliation
→ Item Report + Batch Report
```

The Web facade reuses `HistoricalImportBatch`, `HistoricalForwardReceipt`, `HistoricalMessageFoundationService`, `HistoricalSemanticMaterializationService`, and the existing evidence, replay, reconciliation, and read-model services. It does not create a parallel storage model or a second replay engine.

## Web API surface

All endpoints require the existing Core service key and a registered `actor_telegram_id`:

```text
POST /api/webapp/historical/intake
GET  /api/webapp/historical/intake?actor_telegram_id=<id>
GET  /api/webapp/historical/intake/<batch_id>?actor_telegram_id=<id>
GET  /api/webapp/historical/intake/<batch_id>/report?actor_telegram_id=<id>
```

The Web tRPC layer exposes the same operations to the current authenticated Historical page. The page displays item identity, order, source verification, missing fields, conflicts, batch counts, and the next action from Core.

## Batch semantics

A batch supports `1..N` items up to the current safety limit of 5,000 items. Each item retains an `item_key`, order, content hash, raw text, source metadata, and processing status. Duplicate content within a batch is rejected without creating another receipt. A partial batch is marked in metadata and remains resumable through the existing batch identity.

Unverified Web Paste/Upload content is stored with an explicit `WEB_UNVERIFIED` canonical source identity. It can be reviewed and evidence-ingested, but its lack of external source proof remains visible and is not silently converted into Telegram provenance.

## Safety boundaries

Historical processing never routes into the live recommendation or trading lifecycle. G5/G6 approval, market evidence, and owner review remain explicit. Ranking, Trust release, Trading, Copy Trading, and G8 are outside this intake surface.
