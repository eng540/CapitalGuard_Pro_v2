# Historical Forwarding Intake Design

## Decision

Add a controlled forwarding intake as a third historical source beside Telegram Export and authorized user-history reading. The forwarding path is an **evidence transport mechanism**, not a live trading path.

```text
Telegram source channel
→ user forwards one or many messages to the bot staging chat
→ Forwarding Intake validates origin and allow-list
→ staging batch with receiver/source identities
→ manifest dry-run
→ owner/reviewer approval
→ immutable HistoricalSignalEvidence
→ historical parser
→ market replay
→ historical wallet/reputation gates
```

No intake record may call `CreationService`, create `Recommendation` or `UserTrade`, send Publication Outbox messages, or start PriceStreamer.

## Modes

| Mode | User action | Behavior |
|---|---|---|
| Single | `/historical_forward_one <channel_code>` then forward one message | Stages one record and returns a validation receipt. The batch remains dry-run until approved. |
| Batch | `/historical_forward_start <channel_code>` → forward messages → `/historical_forward_finish` | Stages a bounded batch, reports accepted/rejected/duplicate/hidden-origin counts, and creates a manifest preview. |
| Admin review | `/historical_forward_review <batch_ref>` | Shows the evidence summary and allows validation or rejection; it does not publish or activate anything. |

The handler must not accept free text as historical evidence in these modes. A pasted/copy-forwarded text without Telegram origin is marked `MANUAL_ATTESTED` or `UNVERIFIED`, not attributed automatically to a channel.

## Evidence identity

Every received forward preserves two identities:

| Identity | Meaning | Dedup role |
|---|---|---|
| Receiver chat/message ID | Message delivered to the bot staging chat | Delivery dedup and audit |
| Source channel/message/revision | Original channel item represented by the forward | Historical evidence dedup |

The source timestamp is the historical decision time. The receiver timestamp is ingestion metadata only. A hidden or missing origin is rejected for verified channel attribution. The source channel must match the requested allow-list code.

## Persistence

Reuse `HistoricalImportBatch` for the batch lifecycle with `source_kind=TELEGRAM_FORWARD` and add an additive `HistoricalForwardReceipt` table containing:

- batch ID and optional evidence ID;
- receiver chat ID and receiver message ID;
- source chat ID and source message ID;
- source origin type, source timestamp, edit metadata, and reply-to-source ID;
- forwarding user ID;
- origin validation status and rejection reason;
- immutable normalized payload hash and timestamps.

The receipt is retained even when the evidence is rejected, so ownership and tamper review remains auditable.

## Safety and abuse controls

The intake is disabled by default for ordinary users. A user must be an analyst/channel owner, an explicitly allow-listed trader, or an administrator. Each batch has a maximum message count, maximum age/window, channel allow-list, and expiration time. The bot must not download arbitrary files or follow links from the forwarded text during staging.

The intake must distinguish a genuine Telegram forward origin from a pasted copy, a message re-sent without origin, a hidden origin, and a forward from an unexpected channel. Only the first case can receive a high-confidence channel attribution.

## Operational workflow

The system responds after each item with a receipt such as `STAGED`, `DUPLICATE`, `REJECTED_ORIGIN`, or `REJECTED_CHANNEL`. On finish it produces a manifest preview and does not auto-validate. The owner reviews the source channel, count, time window, edit count, and parsing summary before approving the batch.

## Scope boundary

This path solves the immediate data-ingestion bottleneck and is suitable for a small historical backfill. It does not recover messages that cannot be forwarded, messages with hidden origin, or market candles that are unavailable from the chosen provider. Those records stay unverified and do not enter public reputation ranking.

## Existing Telegram handler interaction

The live application already has a generic forwarded-message parser registered in group 1. The new historical forwarding conversation is registered in group 0 before the generic parser. Its command starts an explicit staging state, so only forwards received during that state are consumed by the historical intake. This prevents an accidental forward from becoming historical evidence and prevents the generic live parser from taking precedence during an active historical batch.

The handler uses the existing unit-of-work and active-user decorators, resolves a registered `ChannelCatalog`, enforces analyst/administrator access, and stores only normalized receipt metadata. It does not call the live recommendation creation path.
