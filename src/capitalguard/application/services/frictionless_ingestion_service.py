from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import (
    ChannelCatalog,
    HistoricalImportBatch,
    HistoricalShadowChannel,
    TemporalForwardDecision,
)

from .historical_forwarding_service import ForwardedMessageInput, HistoricalForwardingService
from .forward_intake_router import ForwardIntakeRouter
from .historical_signal_service import HistoricalSignalService
from .temporal_normalizer import TemporalNormalizer


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
        self.temporal_normalizer = TemporalNormalizer()
        self.forward_router = ForwardIntakeRouter()

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

    @staticmethod
    def _event_relation(raw_text: str | None) -> tuple[str, str]:
        text = (raw_text or "").casefold()
        patterns = (
            (r"trade\s*closed|final\s*close|closed|إغلاق|اغلاق", "CLOSE"),
            (r"tp\s*\d+\s*(hit|✅)|target\s*hit|تحقق\s*الهدف", "TARGET_HIT"),
            (r"partial\s*(exit|close)|خروج\s*جزئي|إغلاق\s*جزئي", "PARTIAL_EXIT"),
            (r"stop\s*(moved|update)|break\s*even|move\s*sl|تحريك\s*الوقف", "STOP_UPDATE"),
            (r"entry\s*(moved|update|changed)|تعديل\s*الدخول", "ENTRY_UPDATE"),
            (r"target\s*(update|changed)|تعديل\s*الهدف", "TARGET_UPDATE"),
            (r"update|amend|تحديث|تعديل", "AMENDMENT"),
        )
        for pattern, relation in patterns:
            if re.search(pattern, text):
                return relation, relation
        return "INITIAL_SIGNAL", "INITIAL_SIGNAL"

    @staticmethod
    def _metadata_time(metadata: dict[str, Any], key: str) -> datetime | None:
        value = metadata.get(key)
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def temporal_metadata_for_message(self, message: ForwardedMessageInput) -> dict[str, Any]:
        relation_name, event_kind = self._event_relation(message.raw_text)
        metadata = dict(message.metadata or {})
        received_time = self._metadata_time(metadata, "receiver_date") or datetime.now(timezone.utc)
        temporal = self.temporal_normalizer.normalize(
            source_time=message.source_message_timestamp,
            received_time=received_time,
            ingested_time=datetime.now(timezone.utc),
            event_time=message.source_message_timestamp if relation_name != "INITIAL_SIGNAL" else None,
            edit_time=message.source_edit_date,
            source_origin_type=message.source_origin_type,
            source_chat_id=message.source_chat_id,
            source_message_id=message.source_message_id,
            source_message_revision=message.source_message_revision,
        )
        relation = relation_name
        plan = self.forward_router.plan(
            temporal=temporal,
            event_kind=event_kind,
            timeline_relation=relation,
            market_data_available=False,
            edited_after_market=bool(
                temporal.edit_time
                and temporal.event_time
                and temporal.edit_time > temporal.event_time
            ),
        )
        decision_payload = plan.decision.as_dict()
        decision_payload["route"] = plan.route.value
        decision_payload["creates_live_entity"] = plan.creates_live_entity
        decision_payload["creates_historical_candidate"] = plan.creates_historical_candidate
        decision_payload["appends_timeline_event"] = plan.appends_timeline_event
        return {
            "event_kind": event_kind,
            "timeline_relation": relation,
            "temporal_decision": decision_payload,
            "market_as_of": temporal.effective_market_as_of.isoformat()
            if temporal.effective_market_as_of
            else None,
        }

    def record_temporal_decision(
        self,
        session: Session,
        *,
        message: ForwardedMessageInput,
        temporal_metadata: dict[str, Any],
    ) -> TemporalForwardDecision:
        existing = session.execute(
            select(TemporalForwardDecision).where(
                TemporalForwardDecision.receiver_chat_id == message.receiver_chat_id,
                TemporalForwardDecision.receiver_message_id == message.receiver_message_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        decision = temporal_metadata.get("temporal_decision") or {}
        received_time = self._metadata_time(dict(message.metadata or {}), "receiver_date")
        temporal = self.temporal_normalizer.normalize(
            source_time=message.source_message_timestamp,
            received_time=received_time,
            ingested_time=datetime.now(timezone.utc),
            event_time=message.source_message_timestamp
            if temporal_metadata.get("event_kind") != "INITIAL_SIGNAL"
            else None,
            edit_time=message.source_edit_date,
            source_origin_type=message.source_origin_type,
            source_chat_id=message.source_chat_id,
            source_message_id=message.source_message_id,
            source_message_revision=message.source_message_revision,
        )
        price_validity = decision.get("price_validity")
        record = TemporalForwardDecision(
            receiver_chat_id=message.receiver_chat_id,
            receiver_message_id=message.receiver_message_id,
            source_chat_id=message.source_chat_id,
            source_message_id=message.source_message_id,
            source_message_revision=message.source_message_revision,
            source_time=temporal.source_time,
            event_time=temporal.event_time,
            received_time=temporal.received_time,
            ingested_time=temporal.ingested_time,
            edit_time=temporal.edit_time,
            mode=decision.get("mode", "UNVERIFIED_TIME"),
            route=decision.get("route", "QUARANTINE"),
            timeline_relation=decision.get("timeline_relation", temporal_metadata.get("timeline_relation", "UNRELATED")),
            confidence=decision.get("confidence", "0"),
            price_validity_score=price_validity,
            age_seconds=decision.get("age_seconds"),
            replay_readiness=decision.get("replay_readiness", "0"),
            reason_codes=decision.get("reason_codes", []),
            metadata_json={
                "event_kind": temporal_metadata.get("event_kind"),
                "market_as_of": temporal_metadata.get("market_as_of"),
                "source_origin_type": message.source_origin_type,
            },
        )
        session.add(record)
        session.flush()
        return record

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
        temporal_metadata = self.temporal_metadata_for_message(message)
        metadata.update(temporal_metadata)
        message = replace(message, metadata={**(message.metadata or {}), **metadata})
        batch.metadata_json = metadata
        self.record_temporal_decision(
            session,
            message=message,
            temporal_metadata=temporal_metadata,
        )
        return self.forwarding_service.stage_message(session, batch_id=batch_id, message=message)

    def preview(self, session: Session, *, batch_id: int):
        return self.forwarding_service.preview_batch(session, batch_id=batch_id)
