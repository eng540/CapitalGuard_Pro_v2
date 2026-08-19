# Telegram Forwarding Intake — Source Findings

## Official Bot API findings

Telegram Bot API is an HTTPS interface for bots. Incoming updates can include ordinary messages, channel posts, edited messages, and edited channel posts. The bot receives JSON-serialized Update objects through either long polling or webhooks. Telegram states that pending updates are retained for no more than 24 hours; this means a forwarding batch must be sent while the bot is available, or the sender must retry from the source.

For the proposed intake, the bot should accept a forwarded message in a private staging chat and inspect the message origin fields. A valid channel-origin message must expose a channel origin that can be mapped to the original chat and message identifier. The receiver-side message identifier is not the same as the original source message identifier and must not replace it.

Forwarded content may be unavailable or privacy-restricted. The system must reject or mark as `UNVERIFIED` any item where the origin is hidden, the original channel identifier is absent, the original timestamp is absent, or the source channel is not allow-listed. A copy-pasted message without origin metadata is not equivalent to a forward and must be stored as manual evidence only.

The forwarding adapter must preserve both identities when available:

```text
receiver_chat_id / receiver_message_id
source_chat_id / source_message_id
source_message_date
reply_to_source_message_id when available
edited/forward metadata when available
```

The Bot API documentation also distinguishes `channel_post` and `edited_channel_post` updates for channel posts known to the bot. The proposed historical intake is intentionally different: it is a user-initiated forwarding workflow that stages the received forward and never treats the receiver message as a live recommendation.

## Engineering implications

1. `ForwardedMessageOrigin` is the evidence boundary. The source channel and source message ID are required for a high-confidence channel attribution.
2. The source timestamp must be used as `decision_timestamp`; the receiver timestamp is only an ingestion timestamp.
3. The receiver message ID is used for deduplication of the intake delivery, while the source channel/message/revision tuple is used for historical evidence deduplication.
4. A forwarded message must enter `DRY_RUN` and `VALIDATED` batch flow, then `HistoricalSignalEvidence`; it must not enter `CreationService`, `Recommendation`, `UserTrade`, Publication Outbox, or PriceStreamer.
5. A batch command should require explicit start/end or a finite number of messages and should show accepted, rejected, hidden-origin, duplicate, and unsupported counts.
6. The system must not infer source ownership from the forwarding user alone. Ownership requires a channel catalog mapping or explicit administrator/analyst review.

## References

[1] Telegram Bot API: https://core.telegram.org/bots/api — official API documentation, including Update, message/channel post updates, getUpdates, webhook behavior, and pending update retention.

[2] python-telegram-bot Message reference: https://docs.python-telegram-bot.org/en/v22.5/telegram.message.html — client representation of Message, forwarded origin, replies, and edit metadata.

[3] python-telegram-bot MessageOrigin reference: https://docs.python-telegram-bot.org/en/v22.5/telegram.messageorigin.html — origin types for forwarded messages, including channel origin.
