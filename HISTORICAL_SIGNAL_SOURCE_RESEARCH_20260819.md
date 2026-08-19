# Historical Signal Source Research

## Telegram capabilities

Telegram's official `messages.getHistory` method returns message history ordered by date and supports pagination through offsets, message IDs, dates, and limits. The official method documentation explicitly states that only users can use this method; it is not a Bot API method. [1]

The official Bot API receives incoming updates through `getUpdates` or webhooks, and the update queue is retained for no more than 24 hours. Bot API updates include new channel posts and edited channel posts, but the documented interface does not provide a general historical-channel enumeration method. [2]

Telegram distinguishes the Bot API from the Telegram API/TDLib. Historical reconstruction therefore needs an authorized user/TDLib/MTProto ingestion path, a user-provided Telegram export, or an explicit admin-uploaded archive. A bot token alone is not a sufficient basis for arbitrary old-channel history. [3]

## Product implications

1. Historical imports must record the acquisition method: `LIVE_BOT_UPDATE`, `AUTHORIZED_USER_HISTORY`, `TELEGRAM_EXPORT`, or `MANUAL_ADMIN_IMPORT`.
2. The system must never imply that a historical message was observed live by the bot when it was imported later.
3. Channel ownership and analyst attribution require evidence. Channel admin status, a verified channel link, signed/admin confirmation, or a trusted import manifest should be recorded separately from message parsing.
4. Protected content, private channels, deleted messages, missing media, and edited posts must be represented as unknown or unavailable; they must not be reconstructed from assumptions.
5. Market replay must use point-in-time data available at or before the message timestamp. If a price or candle cannot be verified, the event remains `UNVERIFIED` and must not feed a high-confidence performance metric.

## References

[1]: https://core.telegram.org/method/messages.getHistory "Telegram API: messages.getHistory"
[2]: https://core.telegram.org/bots/api "Telegram Bot API: updates, webhooks, and channel posts"
[3]: https://core.telegram.org/api "Telegram APIs: Bot API, Telegram API, and TDLib"
