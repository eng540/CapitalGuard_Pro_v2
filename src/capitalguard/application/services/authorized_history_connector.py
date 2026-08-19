"""Authorized Telegram history connector contracts and safe pagination.

The service is backend-agnostic. A Telethon or TDLib backend can be injected
later; the live bot never receives the user session or calls this service.
"""
from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, Sequence

from .historical_manifest_service import HistoricalManifestService


class ConnectorAccessError(RuntimeError):
    """Raised when an account or channel is outside the approved policy."""


@dataclass(frozen=True)
class ReaderAccountPolicy:
    account_alias: str
    session_secret_ref: str
    allowed_channel_ids: frozenset[int]
    enabled: bool = True
    purpose: str = "HISTORICAL_READ_ONLY"

    def validate_channel(self, channel_id: int) -> None:
        if not self.enabled:
            raise ConnectorAccessError("Reader account is disabled")
        if self.purpose != "HISTORICAL_READ_ONLY":
            raise ConnectorAccessError("Reader account purpose is not read-only")
        if channel_id not in self.allowed_channel_ids:
            raise ConnectorAccessError("Channel is not allow-listed for this reader")

    @property
    def account_fingerprint(self) -> str:
        return hashlib.sha256(self.account_alias.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ConnectorMessage:
    channel_id: int
    message_id: int
    message_timestamp: datetime
    raw_text: str
    edited_timestamp: datetime | None = None
    reply_to_message_id: int | None = None
    metadata: dict = field(default_factory=dict)


class HistoryBackend(Protocol):
    """Read-only backend implemented by MTProto/Telethon or TDLib later."""

    def get_history(
        self,
        *,
        channel_id: int,
        from_message_id: int,
        limit: int,
    ) -> Sequence[ConnectorMessage]: ...


@dataclass(frozen=True)
class HistoryCheckpoint:
    channel_id: int
    last_message_id: int
    pages_fetched: int
    messages_fetched: int
    updated_at: datetime


class AuthorizedHistoryConnector:
    def __init__(
        self,
        *,
        policy: ReaderAccountPolicy,
        backend: HistoryBackend,
        page_size: int = 100,
    ):
        if page_size < 1 or page_size > 100:
            raise ValueError("page_size must be between 1 and 100")
        self.policy = policy
        self.backend = backend
        self.page_size = page_size
        self.manifest_service = HistoricalManifestService()

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ConnectorAccessError("Telegram timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    def fetch_manifest_page(
        self,
        *,
        channel_id: int,
        from_message_id: int = 0,
        max_pages: int = 1,
    ) -> tuple[dict, HistoryCheckpoint]:
        self.policy.validate_channel(channel_id)
        if from_message_id < 0:
            raise ValueError("from_message_id must be non-negative")
        if max_pages < 1 or max_pages > 1000:
            raise ValueError("max_pages must be between 1 and 1000")

        cursor = from_message_id
        records: list[dict] = []
        pages = 0
        while pages < max_pages:
            page_result = self.backend.get_history(channel_id=channel_id, from_message_id=cursor, limit=self.page_size)
            if inspect.isawaitable(page_result):
                raise ConnectorAccessError("Async backend requires fetch_manifest_page_async")
            page = list(page_result)
            if not page:
                break
            page = sorted(page, key=lambda item: item.message_id, reverse=True)
            for message in page:
                if message.channel_id != channel_id:
                    raise ConnectorAccessError("Backend returned a message from an unexpected channel")
                records.append(
                    {
                        "telegram_channel_id": channel_id,
                        "telegram_message_id": message.message_id,
                        "message_revision": 0,
                        "message_timestamp": self._utc(message.message_timestamp).isoformat(),
                        "raw_text": message.raw_text,
                        "source_uri": f"telegram://{channel_id}/{message.message_id}",
                        "metadata": {
                            **message.metadata,
                            "reader_account_fingerprint": self.policy.account_fingerprint,
                            "edited_timestamp": self._utc(message.edited_timestamp).isoformat()
                            if message.edited_timestamp
                            else None,
                            "reply_to_message_id": message.reply_to_message_id,
                        },
                    }
                )
            pages += 1
            next_cursor = min(message.message_id for message in page)
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(page) < self.page_size:
                break

        payload = {"source_kind": "AUTHORIZED_USER_HISTORY", "records": records}
        checkpoint = HistoryCheckpoint(
            channel_id=channel_id,
            last_message_id=cursor,
            pages_fetched=pages,
            messages_fetched=len(records),
            updated_at=datetime.now(timezone.utc),
        )
        return payload, checkpoint

    async def fetch_manifest_page_async(
        self,
        *,
        channel_id: int,
        from_message_id: int = 0,
        max_pages: int = 1,
    ) -> tuple[dict, HistoryCheckpoint]:
        self.policy.validate_channel(channel_id)
        if from_message_id < 0:
            raise ValueError("from_message_id must be non-negative")
        if max_pages < 1 or max_pages > 1000:
            raise ValueError("max_pages must be between 1 and 1000")
        cursor = from_message_id
        records: list[dict] = []
        pages = 0
        while pages < max_pages:
            page_result = self.backend.get_history(
                channel_id=channel_id,
                from_message_id=cursor,
                limit=self.page_size,
            )
            page = list(await page_result) if inspect.isawaitable(page_result) else list(page_result)
            if not page:
                break
            page = sorted(page, key=lambda item: item.message_id, reverse=True)
            for message in page:
                if message.channel_id != channel_id:
                    raise ConnectorAccessError("Backend returned a message from an unexpected channel")
                records.append(
                    {
                        "telegram_channel_id": channel_id,
                        "telegram_message_id": message.message_id,
                        "message_revision": 0,
                        "message_timestamp": self._utc(message.message_timestamp).isoformat(),
                        "raw_text": message.raw_text,
                        "source_uri": f"telegram://{channel_id}/{message.message_id}",
                        "metadata": {
                            **message.metadata,
                            "reader_account_fingerprint": self.policy.account_fingerprint,
                            "edited_timestamp": self._utc(message.edited_timestamp).isoformat()
                            if message.edited_timestamp
                            else None,
                            "reply_to_message_id": message.reply_to_message_id,
                        },
                    }
                )
            pages += 1
            next_cursor = min(message.message_id for message in page)
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(page) < self.page_size:
                break
        payload = {"source_kind": "AUTHORIZED_USER_HISTORY", "records": records}
        checkpoint = HistoryCheckpoint(
            channel_id=channel_id,
            last_message_id=cursor,
            pages_fetched=pages,
            messages_fetched=len(records),
            updated_at=datetime.now(timezone.utc),
        )
        return payload, checkpoint
