"""Optional async Telethon-compatible backend.

Telethon is intentionally not imported here. The application injects an
already-authorized client, keeping login and session handling outside the bot.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .authorized_history_connector import ConnectorMessage


class TelethonHistoryBackend:
    def __init__(self, client: Any):
        self.client = client

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    async def get_history(
        self,
        *,
        channel_id: int,
        from_message_id: int,
        limit: int,
    ) -> list[ConnectorMessage]:
        kwargs = {"entity": channel_id, "limit": limit}
        if from_message_id > 0:
            kwargs["max_id"] = from_message_id
        messages: list[ConnectorMessage] = []
        async for message in self.client.iter_messages(**kwargs):
            raw_text = getattr(message, "message", None) or getattr(message, "text", None) or ""
            message_id = getattr(message, "id", None)
            message_date = getattr(message, "date", None)
            if not isinstance(message_id, int) or not isinstance(message_date, datetime):
                continue
            reply = getattr(message, "reply_to_msg_id", None)
            edit_date = getattr(message, "edit_date", None)
            messages.append(
                ConnectorMessage(
                    channel_id=channel_id,
                    message_id=message_id,
                    message_timestamp=self._utc(message_date),
                    raw_text=str(raw_text),
                    edited_timestamp=self._utc(edit_date) if isinstance(edit_date, datetime) else None,
                    reply_to_message_id=reply if isinstance(reply, int) else None,
                    metadata={"backend": "telethon_mtproto"},
                )
            )
        return messages
