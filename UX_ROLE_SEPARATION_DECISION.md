# UX Role Separation Decision

**Date:** 2026-08-19  
**Branch:** `feature/ux-role-separation-20260819`  
**Scope:** Telegram portfolio navigation, record identity, role-based command discovery, and administrator command discoverability.

## Decision

Accept the UX role-separation package for CI review. Analyst recommendations, trader direct logs, and tracked signals are now represented with explicit source metadata and stable record identifiers in the presentation layer. The portfolio hub exposes separate direct/tracked counts and navigation entries. The package also adds a role-aware `/commands` directory and an admin-only `/admin` panel.

## Identity contract

| Source | Badge | Identifier shown |
|---|---|---|
| `ANALYST_RECOMMENDATION` | Analyst Recommendation | `Recommendation #<id>` |
| `DIRECT_INPUT` | Trader Log | `UserTrade #<id>` |
| `TRACKED_RECOMMENDATION` | Tracked Signal | `UserTrade #<id>` |

The identity fields are presentation metadata. They do not alter lifecycle transitions, PnL calculation, publication outbox delivery, entitlement state, or execution behavior.

## Role contract

Traders receive `/log`, portfolio, My Logs, and Tracked Signals discovery. Analysts additionally receive `/newrec`, `/channels`, and `/events`. Administrators receive `/admin` and the existing access, role, backup, and restore operations. The admin panel is read-only discovery; existing destructive operations retain their current authorization and restore confirmation controls.

## Quality gates

The package is accepted for PR review only when the full test suite, compileall, critical Flake8 selection (`F821,F401,E999`), and Bandit high-severity scan pass. The current local evidence is **88 passed, 1 skipped**, compileall clean, critical Flake8 clean, and Bandit clean with only informational comment-name warnings.

## Non-goals and release guardrails

This package does not introduce payments, subscriptions, copy trading, automatic execution, or changes to the publication lifecycle. Those capabilities remain blocked until Alpha stability and retention evidence is reviewed according to the existing roadmap.
