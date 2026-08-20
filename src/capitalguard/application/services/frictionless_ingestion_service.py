from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import (
    ChannelCatalog,
    HistoricalImportBatch,
    HistoricalShadowChannel,
)

from .historical_forwarding_service import ForwardedMessageInput, HistoricalForwardingService
from .historical_signal_service import HistoricalSignalService


@dataclass(frozen=True)
class DiscoveredSource:
    telegram_channel_id: int | None
    title: str | None
    username: str | None
    canonical_catalog_id: int | None
    shadow_channel_id: int | None
    claim_status: str


class FrictionlessIngestionService:
    """Direct historical intake facade; never creates live trading entities."""

    SOURCE_KIND = "TELEGRAM_FORWARD"
    AUTO_MODE = "AUTO"

    def __init__(
        self,
        forwarding_service: HistoricalForwardingService | None = None,
        signal_service: HistoricalSignalService | None = None,
    ):
        self.forwarding_service = forwarding_service or HistoricalForwardingService()
        self.signal_service = signal_service or HistoricalSignalService()

    @staticmethod
    def _chat_id(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    def discover_source(
        self,
        session: Session,
        *,
        telegram_channel_id: Any,
        title: str | None,
        username: str | None,
        discovered_by_user_id: int | None,
    ) -> DiscoveredSource:
        channel_id = self._chat_id(telegram_channel_id)
        if channel_id is None:
            return DiscoveredSource(None, title, username, None, None, "UNVERIFIED")

        catalog = session.execute(
            select(ChannelCatalog).where(ChannelCatalog.telegram_channel_id == channel_id)
        ).scalar_one_or_none()
        if catalog is not None:
            if title and not catalog.title:
                catalog.title = title[:255]
            return DiscoveredSource(
                channel_id,
                catalog.title or title,
                username,
                catalog.id,
                None,
                "CANONICAL",
            )

        shadow = session.execute(
            select(HistoricalShadowChannel).where(
                HistoricalShadowChannel.telegram_channel_id == channel_id
            )
        ).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if shadow is None:
            shadow = HistoricalShadowChannel(
                telegram_channel_id=channel_id,
                title=(title or "")[:255] or None,
                username=(username or "")[:255] or None,
                claim_status="UNCLAIMED",
                discovered_by_user_id=discovered_by_user_id,
                first_seen_at=now,
                last_seen_at=now,
                sample_count=0,
                metadata_json={"discovery_source": "DIRECT_FORWARD"},
            )
            session.add(shadow)
            session.flush()
        else:
            if title:
                shadow.title = title[:255]
            if username:
                shadow.username = username[:255]
            shadow.last_seen_at = now
        shadow.sample_count = int(shadow.sample_count or 0) + 1
        session.flush()
        return DiscoveredSource(
            channel_id,
            shadow.title or title,
            shadow.username or username,
            None,
            shadow.id,
            shadow.claim_status or "UNCLAIMED",
        )

    def start_or_reuse_auto_batch(
        self,
        session: Session,
        *,
        source: DiscoveredSource,
        requested_by_user_id: int,
        existing_batch_id: int | None = None,
    ) -> HistoricalImportBatch:
        if existing_batch_id:
            existing = session.get(HistoricalImportBatch, existing_batch_id)
            existing_source = (existing.metadata_json or {}).get("source_chat_id") if existing else None
            if existing is not None and existing.status == "STAGING":
                if self._chat_id(existing_source) == source.telegram_channel_id:
                    return existing

        metadata = {
            "mode": self.AUTO_MODE,
            "intake_status": "STAGING",
            "source_chat_id": source.telegram_channel_id,
            "source_title": source.title,
            "source_username": source.username,
            "claim_status": source.claim_status,
            "shadow_channel_id": source.shadow_channel_id,
            "canonical_channel_catalog_id": source.canonical_catalog_id,
            "debounce_seconds": 3,
            "discovery_source": "DIRECT_FORWARD",
        }
        batch = self.signal_service.create_import_batch(
            session,
            source_kind=self.SOURCE_KIND,
            manifest=[],
            channel_catalog_id=source.canonical_catalog_id,
            requested_by_user_id=requested_by_user_id,
            metadata=metadata,
        )
        batch.status = "STAGING"
        session.flush()
        return batch

    def stage_direct_message(
        self,
        session: Session,
        *,
        batch_id: int,
        message: ForwardedMessageInput,
    ):
        batch = session.get(HistoricalImportBatch, batch_id)
        if batch is None:
            raise ValueError("Automatic historical batch does not exist")
        metadata = dict(batch.metadata_json or {})
        metadata["expected_source_chat_id"] = metadata.get("source_chat_id")
        metadata["max_records"] = metadata.get("max_records", 500)
        batch.metadata_json = metadata
        return self.forwarding_service.stage_message(session, batch_id=batch_id, message=message)

    def preview(self, session: Session, *, batch_id: int):
        return self.forwarding_service.preview_batch(session, batch_id=batch_id)
