# Alpha Gate Decision — Layered Identity Release

**Date:** 2026-08-19  
**Baseline:** `main` at `1301c1ff`  
**Production:** Railway Southeast Asia service

## Decision

The layered identity release is technically ready for a controlled Alpha operator gate. It is not an authorization to open unrestricted registration or to activate payments, subscriptions, Copy Trading, or automatic execution.

## Evidence

The release passed CI with 95 local tests passing and one pre-existing skipped test. Railway returned HTTP 200 from `/health`; Publication Outbox metrics showed REPLY, UPDATE, and CLOSE deliveries in SENT state and queue size zero. PR #192 was merged into `main`.

## Alpha entry conditions

The operator must first complete the manual checklist with one active trader, one active analyst, and the administrator account. The check must confirm `/commands`, `/admin`, My Logs, Tracked Signals, recommendation IDs, trader IDs, channel codes, two-channel publication references, and legacy callback compatibility.

After the manual check, Alpha may be limited to 20–50 invited users with no real-fund execution promise. Daily observation should record health, migration status, Telegram error rate, Outbox delivery success, duplicate suppression, active users, day-1/day-7 return, and support incidents.

## Exit or pause conditions

Pause onboarding if health fails, migrations fail, Outbox queue grows persistently, cross-role records appear, scoped identifiers collide, or a user cannot retrieve a record by its displayed identity. Payment and Copy Trading remain blocked until stability and retention are demonstrated.

## Next R2 scope

After the Alpha observation window, continue with analyst profiles, discoverable analyst search, sample-size-aware performance, drawdown, exposure, and comparison views. Do not rank analysts on win rate alone and do not expose performance with an insufficient sample size.
