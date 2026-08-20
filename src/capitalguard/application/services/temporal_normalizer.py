from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from capitalguard.domain.temporal import TemporalContext, decimal, utc


class TemporalNormalizer:
    """Normalizes Telegram/source timestamps without collapsing their meanings."""

    CHANNEL_ORIGINS = {"CHANNEL", "MESSAGE_ORIGIN_CHANNEL"}

    @staticmethod
    def _chat_id(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @classmethod
    def normalize_origin_type(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip().upper()
        return "CHANNEL" if normalized in cls.CHANNEL_ORIGINS else normalized

    def normalize(
        self,
        *,
        source_time: datetime | None,
        received_time: datetime | None = None,
        ingested_time: datetime | None = None,
        event_time: datetime | None = None,
        edit_time: datetime | None = None,
        source_origin_type: Any = None,
        source_chat_id: Any = None,
        source_message_id: Any = None,
        source_message_revision: Any = 0,
        source_time_verified: bool | None = None,
    ) -> TemporalContext:
        received = utc(received_time or datetime.now(timezone.utc))
        ingested = utc(ingested_time or datetime.now(timezone.utc))
        revision = int(decimal(source_message_revision) or 0)
        origin_type = self.normalize_origin_type(source_origin_type)
        verified = (
            bool(source_time_verified)
            if source_time_verified is not None
            else bool(source_time and origin_type == "CHANNEL" and self._chat_id(source_chat_id) is not None)
        )
        return TemporalContext(
            source_time=source_time,
            received_time=received,
            ingested_time=ingested,
            event_time=event_time,
            edit_time=edit_time,
            source_origin_type=origin_type,
            source_chat_id=self._chat_id(source_chat_id),
            source_message_id=self._chat_id(source_message_id),
            source_message_revision=revision,
            source_time_verified=verified,
        )
