# R3 Non-Commercial Entitlement and Ledger Design

## Architectural decision

The first R3 slice will build auditable entitlement and ledger primitives without connecting a payment provider and without collecting money. The system must remain operationally useful for Alpha through explicit zero-cost grants.

## Components

| Component | Purpose | Commercial state |
|---|---|---|
| `entitlement_grants` | Records which user can access which feature, with source and validity window | Active for Alpha grants only |
| `subscription_ledger_entries` | Append-only audit trail for grant/revoke/expire actions and future provider events | Zero amount only; no provider callbacks |
| Billing gate | Central guard that rejects provider/charge operations while `BILLING_ENABLED=false` | Disabled by default |

## Ledger rules

Every grant or revoke has an idempotency key, actor, source, timestamp, and metadata. Alpha grants use `source=ALPHA_GRANT`, `amount_minor=0`, and `currency=USD` only as a bookkeeping placeholder. Any non-zero amount or provider event must be rejected while billing is disabled. No row is deleted to reverse an entitlement; a revoke or expiry event is appended.

## Entitlement rules

The effective entitlement is the latest non-expired grant/revoke decision for a `(user_id, feature_code)` pair. Validity is evaluated in UTC. The service exposes `grant_alpha`, `revoke`, `has_feature`, and `list_active` operations. It does not expose a charge or checkout operation in this slice.

## Future activation gate

Payment providers, signed webhooks, reconciliation, refunds, tax/currency handling, and subscription upgrades remain outside this implementation. They require a separate commercial decision after Alpha retention and reliability evidence.
