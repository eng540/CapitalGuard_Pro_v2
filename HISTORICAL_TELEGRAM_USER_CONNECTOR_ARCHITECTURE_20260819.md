# Historical Telegram User Connector — Architecture Decision

## Executive decision

The system should support a **read-only authorized Telegram user connector** for historical channel retrieval, but it must run as a separate historical-ingestion worker and must never become part of the live Telegram bot process. The connector should write only to `HistoricalImportBatch` and `HistoricalEvidence`; it must not create live `Recommendation` or `UserTrade` records, publish messages, emit alerts, or trigger execution.

The preferred rollout is two stages. Stage one uses a controlled, one-shot MTProto importer compatible with a user account that has already joined the channel. Stage two, only after the first stage proves stable, may use TDLib as a long-running reader for approved channels and incremental synchronization.

## Official capability boundary

Telegram's official `messages.getHistory` method returns message history and is explicitly restricted to user authorization. It also requires access to the target peer; private channels may return `CHANNEL_PRIVATE` when the account has not joined [1]. TDLib is a full Telegram client library with asynchronous requests, local storage, authorization state handling, and `getChatHistory` pagination [2]. Bot API updates are not a historical archive; incoming updates are retained for a limited period and are intended for bot update delivery [3].

## Option comparison

| Approach | Best use | Advantages | Tradeoffs | Decision |
|---|---|---|---|---|
| Telegram Export | Initial batch and legal review | No live session, easy to archive, reproducible manifest | Manual export, no automatic incremental sync | Keep as safest fallback and first acceptance path |
| MTProto client with user account | Controlled historical import and moderate sync | Direct access to `messages.getHistory`, Python integration, pagination and checkpoints | Requires API ID/hash, phone authorization, 2FA handling, session protection, rate-limit handling | **Recommended for stage one** |
| TDLib sidecar | Long-running reader and incremental history sync | Official client library, managed authorization/update state, local message cache | Native runtime and Docker complexity, larger operational surface, persistent local database | Stage two only after first stage |
| Analyst/trader personal account | Temporary proof-of-access | Already joined to relevant channels | Coupling to a person's account, offboarding risk, privacy and availability risk | Do not use as permanent production identity |
| Dedicated system reader account | Production historical ingestion | Stable ownership, least privilege, auditable lifecycle, independent of staff turnover | Requires one-time owner authorization and channel joins | **Preferred production identity** |

## Account policy

The system must not silently use an analyst or trader's personal account. A user account may be used only after explicit authorization, channel membership verification, and recording the account's purpose. The recommended production identity is a dedicated reader account controlled by the system owner. An administrator account may be used for a controlled backfill, but it should not be the default long-running reader.

The account reference stored in CapitalGuard must be an irreversible account fingerprint or internal alias, not a phone number or session string. The database stores the fact that a reader account was used, while credentials and sessions remain outside ordinary application tables.

## Proposed component boundary

```text
Telegram User Account
        |
        v
Historical Connector Worker
  - MTProto/TDLib client
  - authorization state machine
  - rate limits and backoff
  - channel access check
  - pagination cursor/checkpoint
  - raw-message normalization
        |
        v
HistoricalImportBatch (DRY_RUN -> VALIDATED -> INGESTED/REJECTED)
        |
        v
HistoricalEvidence -> HistoricalSignal -> Timeline / Attribution
        |
        +--> Market Replay (read-only)
        +--> Historical Reputation Gate (confidence separated)
```

The connector worker must not import `CreationService`, `LifecycleService`, `PublicationOutboxService`, `PriceStreamer`, or live Telegram handlers. This dependency rule is a hard safety boundary.

## Authentication and secrets

The first implementation should perform authorization interactively in a controlled owner-operated process. The phone number, login code, and 2FA password must never be entered into source code, GitHub issues, ordinary logs, or the database. The resulting session/database directory must be encrypted at rest and mounted only into the historical worker.

For Railway operation, the minimum secret set is an API ID, API hash, and an encrypted session reference. The raw session should not be exposed to the bot container or application logs. The worker needs a kill switch, a revocation path, a session health metric, and an explicit allow-list of channel IDs.

## Retrieval contract

Each retrieval job must include a channel ID, optional date/message bounds, an owner/authorization reference, a manifest ID, and a checkpoint. The worker reads pages in reverse chronological order, persists the last successful message ID, and resumes from that checkpoint after interruption. Each message is normalized with the Telegram channel ID, message ID, message date, edit date if available, raw text/caption, media reference, reply-to ID, source account fingerprint, fetched-at time, and content hash.

A message is never considered historical evidence merely because it was fetched. It must pass the existing dry-run, parser, ownership, timestamp, and confidence gates. A fetched message with unknown ownership remains a channel historical record and cannot enter public analyst reputation.

## Deduplication and edits

The primary identity is `(telegram_channel_id, telegram_message_id, message_revision)`. If Telegram exposes an edited version, the connector stores a new immutable evidence revision while preserving the original. The latest revision is not allowed to erase the original raw content. Content hashes provide an additional integrity check but do not replace Telegram message identity.

## Operational controls

The worker must enforce bounded date ranges, maximum messages per job, per-channel allow-lists, rate-limit backoff, resumable checkpoints, structured audit logs, and a dry-run-only mode. It must expose metrics for pages fetched, messages accepted/rejected, rate-limit responses, authorization state, checkpoint age, and import batch status. A failed or revoked session must stop ingestion rather than fall back to another account silently.

## Acceptance gates

The connector is not production-ready until the following are demonstrated with a real authorized account and one approved channel:

1. Authorization succeeds without exposing the login code or session in logs.
2. The account can read the approved channel and cannot read a non-allow-listed channel through the worker.
3. Pagination resumes correctly after an intentional interruption.
4. Re-running the same date range produces zero duplicate evidence records.
5. Edited messages preserve both revisions.
6. The dry-run report matches accepted, rejected, and duplicate counts.
7. Historical parser output remains non-operational.
8. Market Replay rejects future observations and only verifies events with a valid market source.
9. No live Outbox, alert, PriceStreamer, Recommendation, or UserTrade activity is generated.
10. Revoking the session stops the worker and leaves an auditable failure state.

## Scope and compliance boundary

This design retrieves only channels the authorized account is allowed to access and only for approved historical analysis. It does not bypass private-channel membership, deleted-message restrictions, protected content, or Telegram access controls. The owner must confirm that the account and intended use are authorized by the channel owner and consistent with applicable terms and privacy obligations.

## References

[1]: https://core.telegram.org/method/messages.getHistory "Telegram API: messages.getHistory"
[2]: https://core.telegram.org/tdlib/getting-started "Telegram TDLib: Getting started"
[3]: https://core.telegram.org/bots/api "Telegram Bot API"

## Implementation baseline

The first code baseline uses an injected async-compatible backend and a Telethon-compatible mapper. The optional worker dependency is pinned separately to `Telethon==1.44.0`, released on June 15, 2026 according to PyPI [4]. It is deliberately excluded from the live bot requirements until the connector gate is approved. Telethon's documented client uses an API ID and API hash from `my.telegram.org`, and its session file contains enough information to authorize without repeating the code; therefore the session path must be treated as a secret and must not be committed or logged [5].

[4]: https://pypi.org/project/Telethon/ "PyPI: Telethon 1.44.0"
[5]: https://docs.telethon.dev/en/stable/modules/client.html "Telethon client reference"
