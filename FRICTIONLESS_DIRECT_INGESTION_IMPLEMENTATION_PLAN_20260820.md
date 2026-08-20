# Frictionless Direct Historical Ingestion — Implementation Plan

## Approved product behavior

A genuine Telegram channel forward received in a private chat is routed to the historical pipeline automatically. The user does not need a channel code or start/finish commands. The live parser must not consume the same update.

## Safety boundary

The direct path creates only historical staging records. It must not create `Recommendation`, `UserTrade`, publication outbox operations, or live analyst statistics. A newly discovered channel is a shadow/unclaimed source and cannot enter public reputation ranking.

## MVP delivered in this implementation

1. Direct private-forward router.
2. Find-or-create canonical or unclaimed shadow channel discovery.
3. Hidden auto batch with a short debounce window.
4. Idempotent historical receipts and private dry-run summary.
5. Discoverable status output including canonical and shadow sources.
6. Regression tests for new channels, repeated forwards, and live-entity isolation.

The existing explicit historical commands remain available for controlled owner/admin workflows. They are not required in the ordinary user path.

## Deferred after this gate

Historical parser and market replay are attached to the auto-batch completion worker as a separate gate. A result is never fabricated when OHLCV coverage is missing. Public reputation remains blocked until claim and verification.
