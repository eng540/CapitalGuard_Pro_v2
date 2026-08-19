# Historical Signal Reconstruction — Implementation Notes

## Implemented in this slice

The branch adds four historical layers plus an import-batch control: `historical_import_batches`, `historical_signal_evidence`, `historical_signals`, `historical_signal_events`, and `historical_signal_attributions`.

Evidence is immutable in meaning and carries source kind, channel/message identifiers, source timestamp, raw text hash, ownership proof fields, and confidence. A batch starts as `DRY_RUN`; evidence ingestion through a batch is rejected until the batch is explicitly marked `VALIDATED`.

The service deduplicates live Telegram message IDs and falls back to normalized content/time hashes when no message ID exists. It rejects decision/event timestamps that travel backward, rejects market observations later than the event being proven, and requires market timestamp, data source, and price for a `VERIFIED` replay event.

Historical trader follows are stored as `TRADER_FOLLOW` attributions and queried through a read-only historical query service. They do not create a live `UserTrade`, do not enter the publication Outbox, and cannot activate price streaming.

## Trust and ranking boundary

`MANUAL_ATTESTED` historical signals remain outside public ranking by default. Ranking eligibility requires a known analyst, a verified trust tier, a confidence score of at least 0.8000, and at least one fully verified market-replay event. This is a conservative initial gate and will be revised only through a documented R2/R3 decision.

## Not implemented yet

This slice does not connect a Telegram user-account/MTProto importer, does not fetch market candles, does not parse arbitrary exports automatically, and does not expose a production history-import command. Those integrations require explicit credentials/authorization, source-specific rate limits, and a controlled manifest review. The next slice should add an admin dry-run manifest workflow and the market-replay adapter behind a feature flag.
