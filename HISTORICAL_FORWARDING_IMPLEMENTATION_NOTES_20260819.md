# Historical Forwarding Intake — Implementation Notes

## Scope

This slice adds an explicit Telegram forwarding intake for historical channel reconstruction. It supports one-message and bounded batch modes. A user starts a batch, forwards source messages to the private staging chat, and finishes the batch to produce a dry-run preview.

## Safety boundary

The intake is not a live trading parser. It never calls `CreationService`, never creates `Recommendation` or `UserTrade`, never queues Publication Outbox messages, and never starts PriceStreamer. The only durable output before owner approval is a staging receipt and import batch metadata.

## Source validation

The service preserves receiver chat/message identity and source channel/message identity. It accepts only a channel-origin forward from the expected allow-listed channel and rejects hidden origins, unexpected channels, missing source timestamps, and future timestamps. It deduplicates both receiver delivery and source message/revision.

## Workflow

```text
/historical_forward_start CH-CODE
→ forward messages
→ /historical_forward_finish
→ manifest preview
→ owner review
→ batch VALIDATED
→ evidence ingestion
→ parser/replay/reputation gates
```

`/historical_forward_one CH-CODE` is the one-message variant. The command directory and admin panel expose the workflow. The generic live forwarded-message parser is registered after the historical conversation so an active historical batch is not interpreted as a live trade.

## Quality evidence

- Full suite: 143 passed, 1 skipped.
- Targeted modified-file critical Flake8: passed.
- Compileall: passed.
- Bandit on the new service and handler: passed.
- New service test: passed, including allow-list rejection, hidden-origin rejection, receiver/source deduplication, dry-run preview, owner validation, evidence ingestion, and live-entity isolation.

## Limits

Telegram forwards are evidence transport, not proof of full channel history. Hidden-origin forwards, copied text, deleted content, missing source timestamps, and unavailable market candles remain unverified. The batch must be owner-reviewed before validation and never enters public reputation until market replay and ownership gates pass.
