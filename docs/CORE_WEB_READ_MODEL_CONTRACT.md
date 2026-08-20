# Core → Web Read Model Contract

## Purpose

`CapitalGuard Core` remains the sole financial source of truth. `CapitalGuard Web` may request a transient, server-to-server **read model**; it must not persist or reconstruct recommendations, trades, PnL, or historical evidence in Web PostgreSQL.

## Endpoint

`GET /api/webapp/read-models/trader/{telegram_id}`

| Requirement | Contract |
|---|---|
| Caller | CapitalGuard Web server only |
| Authentication | `Authorization: Bearer <Core API_KEY>` |
| Browser access | Forbidden; Web never exposes the key with a `VITE_` prefix |
| Identity binding | Web derives `telegram_id` from its signed Telegram-first session; it does not accept a client-selected ID |
| Response | Open positions, live-price snapshot, activated-portfolio performance, lifecycle funnel, response timestamp, and schema version |
| Writes | None |
| Web persistence | None for financial payloads; Web may store only preferences, sessions, and audit metadata |

## Failure semantics

| Status | Meaning | Web behavior |
|---|---|---|
| `401` | Service credential absent | Treat as configuration failure; do not retry from browser. |
| `403` | Service credential rejected | Treat as security event; stop data display and rotate server credential. |
| `404` | Telegram identity has no Core user | Display a deliberate empty/onboarding state. |
| `503` | Core dependency unavailable | Display cached-free degraded state and retry using server query policy. |

## Scope boundary

This contract deliberately excludes Owner Review commands, Evidence ingestion mutations, billing, Copy Trading, and historical record writes. Those require independently gated command APIs with role checks, idempotency keys, and audit events.
