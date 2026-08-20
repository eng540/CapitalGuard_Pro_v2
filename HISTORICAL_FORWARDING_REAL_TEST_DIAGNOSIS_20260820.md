# Historical Forwarding — Real Test Diagnosis (2026-08-20)

## Verified visual evidence

The screenshots show the following sequence in the private Telegram chat:

1. `/historical_forward_start CH-000001` succeeds and opens a batch for channel `CH-000001`.
2. The bot displays several forwarded messages with `source_chat=-1002433437205` and source message IDs `4323`, `4328`, `4340`, `4349`, `4357`, `4371`, `4430`, `4437`, `4446`, and `4448`.
3. Every visible receipt is `REJECTED_CHANNEL`; the final dry-run reports `total=11`, `accepted=0`, `rejected=11`, `hidden_origin=0`.
4. The bot intermittently emits `Operation cancelled.` and later enters the live forward-analysis path, displaying `Analyzing forwarded message...`, followed by `Analysis service is unreachable` and the live manual-entry card.
5. The selected historical batch still finishes with 11 receipts, which proves the historical handler processed the forwarded messages at least for part of the interaction.

## Initial diagnosis

`REJECTED_CHANNEL` is a deterministic allow-list mismatch: the actual Telegram source ID is `-1002433437205`, while the batch compares it with `ChannelCatalog.telegram_channel_id` for `CH-000001`. The screenshots do not show the registered expected ID, so the database configuration must be inspected with the new `/historical_channels` and `/historical_forward_status` commands before changing the allow-list. The ID must not be auto-overridden from a forwarded message.

The live analysis fallback indicates that, for at least some forwarded messages, the historical ConversationHandler was no longer the handler that consumed the update. The likely causes are loss of the staging key or conversation termination, followed by Group 1's broad `filters.FORWARDED & filters.TEXT` entry point. The existing live parser also has a broad fallback `MessageHandler(filters.ALL & filters.ChatType.PRIVATE, ...)`, which can produce `Operation cancelled.` when its conversation state receives an update it cannot interpret.

## Acceptance implications

The batch was not accepted and no historical evidence was ingested. No conclusion should be drawn about Parser or Market Replay until the source ID is correctly registered and the live parser is proven unable to capture forwarded messages during an active historical session.
