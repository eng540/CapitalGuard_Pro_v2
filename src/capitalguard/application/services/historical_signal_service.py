"""Safe historical signal ingestion and temporal replay primitives."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import (
    HistoricalSignal,
    HistoricalSignalAttribution,
    HistoricalSignalEvidence,
    HistoricalSignalEvent,
)


class HistoricalSignalValidationError(ValueError):
    """Raised when historical evidence would be ambiguous or temporally invalid."""


class HistoricalSignalService:
    SOURCE_CONFIDENCE = {
        "LIVE_BOT_UPDATE": Decimal("1.0000"),
        "AUTHORIZED_USER_HISTORY": Decimal("0.9500"),
        "TELEGRAM_EXPORT": Decimal("0.9000"),
        "MANUAL_ADMIN_IMPORT": Decimal("0.4000"),
    }
    TRUST_TIER = {
        "LIVE_BOT_UPDATE": "VERIFIED_LIVE",
        "AUTHORIZED_USER_HISTORY": "VERIFIED_HISTORY",
        "TELEGRAM_EXPORT": "VERIFIED_HISTORY",
        "MANUAL_ADMIN_IMPORT": "MANUAL_ATTESTED",
    }
    VERIFIED_REPLAY_STATUSES = {"VERIFIED"}
    RANKABLE_TRUST_TIERS = {"VERIFIED_LIVE", "VERIFIED_HISTORY", "RECONSTRUCTED"}

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _normalize_text(raw_text: str | None) -> str:
        return re.sub(r"\s+", " ", (raw_text or "").strip()).casefold()

    @classmethod
    def _content_hash(cls, raw_text: str | None) -> str:
        return hashlib.sha256(cls._normalize_text(raw_text).encode("utf-8")).hexdigest()

    @classmethod
    def _dedup_key(
        cls,
        *,
        channel_identity: str | None,
        telegram_message_id: int | None,
        message_revision: int,
        message_timestamp: datetime,
        content_hash: str,
    ) -> str:
        if channel_identity and telegram_message_id is not None:
            return f"telegram:{channel_identity}:{telegram_message_id}:r{message_revision}"
        return (
            f"content:{channel_identity or 'unknown'}:{cls._utc(message_timestamp).isoformat()}"
            f":{content_hash}"
        )

    def create_import_batch(
        self,
        session: Session,
        *,
        source_kind: str,
        manifest: list[dict[str, Any]],
        channel_catalog_id: int | None = None,
        requested_by_user_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        from capitalguard.infrastructure.db.models import HistoricalImportBatch

        source = source_kind.strip().upper()
        if source not in self.SOURCE_CONFIDENCE:
            raise HistoricalSignalValidationError(f"Unsupported historical source kind: {source_kind}")
        canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str)
        batch = HistoricalImportBatch(
            batch_ref=f"HIMP-{uuid4().hex[:24].upper()}",
            channel_catalog_id=channel_catalog_id,
            source_kind=source,
            requested_by_user_id=requested_by_user_id,
            status="DRY_RUN",
            manifest_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            total_records=len(manifest),
            metadata_json=metadata or {},
        )
        session.add(batch)
        session.flush()
        return batch

    def validate_import_batch(
        self,
        session: Session,
        *,
        batch_id: int,
        accepted_records: int,
        rejected_records: int,
    ):
        from capitalguard.infrastructure.db.models import HistoricalImportBatch

        batch = session.get(HistoricalImportBatch, batch_id)
        if batch is None:
            raise HistoricalSignalValidationError("Import batch does not exist")
        if accepted_records < 0 or rejected_records < 0:
            raise HistoricalSignalValidationError("Batch counts cannot be negative")
        if accepted_records + rejected_records > batch.total_records:
            raise HistoricalSignalValidationError("Batch counts exceed manifest size")
        batch.accepted_records = accepted_records
        batch.rejected_records = rejected_records
        batch.status = "VALIDATED"
        session.flush()
        return batch

    def ingest_evidence(
        self,
        session: Session,
        *,
        source_kind: str,
        message_timestamp: datetime,
        raw_text: str | None,
        batch_id: int | None = None,
        channel_catalog_id: int | None = None,
        telegram_channel_id: int | None = None,
        telegram_message_id: int | None = None,
        message_revision: int = 0,
        source_uri: str | None = None,
        ownership_proof_type: str | None = None,
        ownership_proof_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> HistoricalSignalEvidence:
        source = source_kind.strip().upper()
        if source not in self.SOURCE_CONFIDENCE:
            raise HistoricalSignalValidationError(f"Unsupported historical source kind: {source_kind}")
        if message_timestamp is None:
            raise HistoricalSignalValidationError("message_timestamp is required")
        if message_revision < 0:
            raise HistoricalSignalValidationError("message_revision cannot be negative")
        timestamp = self._utc(message_timestamp)
        if batch_id is not None:
            from capitalguard.infrastructure.db.models import HistoricalImportBatch

            batch = session.get(HistoricalImportBatch, batch_id)
            if batch is None or batch.status != "VALIDATED":
                raise HistoricalSignalValidationError("Evidence import requires a VALIDATED batch")
            if batch.source_kind != source:
                raise HistoricalSignalValidationError("Evidence source does not match import batch source")
        content_hash = self._content_hash(raw_text)
        channel_identity = str(telegram_channel_id) if telegram_channel_id is not None else None
        key = self._dedup_key(
            channel_identity=channel_identity,
            telegram_message_id=telegram_message_id,
            message_revision=message_revision,
            message_timestamp=timestamp,
            content_hash=content_hash,
        )
        existing = session.execute(
            select(HistoricalSignalEvidence).where(HistoricalSignalEvidence.dedup_key == key)
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        evidence = HistoricalSignalEvidence(
            batch_id=batch_id,
            channel_catalog_id=channel_catalog_id,
            telegram_channel_id=telegram_channel_id,
            telegram_message_id=telegram_message_id,
            message_revision=message_revision,
            source_kind=source,
            source_uri=source_uri,
            message_timestamp=timestamp,
            raw_text=raw_text,
            content_hash=content_hash,
            dedup_key=key,
            ownership_proof_type=ownership_proof_type,
            ownership_proof_ref=ownership_proof_ref,
            evidence_confidence=self.SOURCE_CONFIDENCE[source],
            metadata_json=metadata or {},
        )
        session.add(evidence)
        session.flush()
        return evidence

    def create_signal(
        self,
        session: Session,
        *,
        evidence_id: int,
        decision_timestamp: datetime,
        channel_catalog_id: int | None = None,
        channel_id: int | None = None,
        analyst_id: int | None = None,
        asset: str | None = None,
        side: str | None = None,
        entry: Any = None,
        stop_loss: Any = None,
        targets: Any = None,
        market: str | None = None,
        public_ref: str | None = None,
    ) -> HistoricalSignal:
        evidence = session.get(HistoricalSignalEvidence, evidence_id)
        if evidence is None:
            raise HistoricalSignalValidationError("Evidence record does not exist")
        decision_time = self._utc(decision_timestamp)
        if decision_time < self._utc(evidence.message_timestamp):
            raise HistoricalSignalValidationError("decision_timestamp cannot precede source message time")
        trust_tier = self.TRUST_TIER[evidence.source_kind]
        confidence = Decimal(str(evidence.evidence_confidence or 0))
        signal = HistoricalSignal(
            public_ref=public_ref or f"HIST-{uuid4().hex[:24].upper()}",
            evidence_id=evidence.id,
            channel_catalog_id=channel_catalog_id or evidence.channel_catalog_id,
            channel_id=channel_id,
            analyst_id=analyst_id,
            asset=asset,
            side=side,
            entry=entry,
            stop_loss=stop_loss,
            targets=targets,
            market=market,
            decision_timestamp=decision_time,
            status="PARSED",
            trust_tier=trust_tier,
            confidence_score=confidence,
            eligible_for_ranking=False,
        )
        session.add(signal)
        session.flush()
        return signal

    def record_event(
        self,
        session: Session,
        *,
        signal_id: int,
        event_type: str,
        event_timestamp: datetime,
        dedup_key: str,
        market_as_of: datetime | None = None,
        data_source: str | None = None,
        price: Any = None,
        replay_status: str = "UNVERIFIED",
        event_confidence: Any = Decimal("0"),
        event_data: dict[str, Any] | None = None,
        source_evidence_id: int | None = None,
    ) -> HistoricalSignalEvent:
        signal = session.get(HistoricalSignal, signal_id)
        if signal is None:
            raise HistoricalSignalValidationError("Historical signal does not exist")
        event_time = self._utc(event_timestamp)
        decision_time = self._utc(signal.decision_timestamp)
        if event_time < decision_time:
            raise HistoricalSignalValidationError("event_timestamp cannot precede decision_timestamp")
        market_time = self._utc(market_as_of) if market_as_of is not None else None
        if market_time is not None and market_time > event_time:
            raise HistoricalSignalValidationError("market_as_of cannot be later than event_timestamp")
        status = replay_status.strip().upper()
        if status == "VERIFIED" and (market_time is None or not data_source or price is None):
            raise HistoricalSignalValidationError(
                "VERIFIED replay events require market_as_of, data_source, and price"
            )
        existing = session.execute(
            select(HistoricalSignalEvent).where(HistoricalSignalEvent.dedup_key == dedup_key)
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        event = HistoricalSignalEvent(
            signal_id=signal_id,
            source_evidence_id=source_evidence_id,
            event_type=event_type.strip().upper(),
            event_timestamp=event_time,
            market_as_of=market_time,
            data_source=data_source,
            price=price,
            replay_status=status,
            event_confidence=event_confidence,
            event_data=event_data or {},
            dedup_key=dedup_key,
        )
        session.add(event)
        session.flush()
        self.refresh_ranking_eligibility(session, signal_id)
        return event

    def add_attribution(
        self,
        session: Session,
        *,
        signal_id: int,
        attribution_kind: str,
        dedup_key: str,
        analyst_id: int | None = None,
        channel_id: int | None = None,
        trader_user_id: int | None = None,
        proof_type: str | None = None,
        proof_ref: str | None = None,
        confidence_score: Any = Decimal("0"),
        status: str = "PROPOSED",
    ) -> HistoricalSignalAttribution:
        if session.get(HistoricalSignal, signal_id) is None:
            raise HistoricalSignalValidationError("Historical signal does not exist")
        existing = session.execute(
            select(HistoricalSignalAttribution).where(HistoricalSignalAttribution.dedup_key == dedup_key)
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        attribution = HistoricalSignalAttribution(
            signal_id=signal_id,
            attribution_kind=attribution_kind.strip().upper(),
            analyst_id=analyst_id,
            channel_id=channel_id,
            trader_user_id=trader_user_id,
            proof_type=proof_type,
            proof_ref=proof_ref,
            confidence_score=confidence_score,
            status=status.strip().upper(),
            dedup_key=dedup_key,
        )
        session.add(attribution)
        session.flush()
        return attribution

    def record_trader_follow(
        self,
        session: Session,
        *,
        signal_id: int,
        trader_user_id: int,
        dedup_key: str,
        proof_ref: str | None = None,
    ) -> HistoricalSignalAttribution:
        """Record a historical follow in the trader/channel history without live activation."""
        return self.add_attribution(
            session,
            signal_id=signal_id,
            attribution_kind="TRADER_FOLLOW",
            trader_user_id=trader_user_id,
            proof_type="USER_ACTION",
            proof_ref=proof_ref,
            confidence_score=Decimal("1.0000"),
            status="RECORDED",
            dedup_key=dedup_key,
        )

    def refresh_ranking_eligibility(self, session: Session, signal_id: int) -> bool:
        signal = session.get(HistoricalSignal, signal_id)
        if signal is None:
            raise HistoricalSignalValidationError("Historical signal does not exist")
        events = session.execute(
            select(HistoricalSignalEvent).where(HistoricalSignalEvent.signal_id == signal_id)
        ).scalars().all()
        verified_events = bool(events) and all(
            event.replay_status in self.VERIFIED_REPLAY_STATUSES for event in events
        )
        eligible = (
            signal.analyst_id is not None
            and signal.trust_tier in self.RANKABLE_TRUST_TIERS
            and Decimal(str(signal.confidence_score or 0)) >= Decimal("0.8000")
            and verified_events
        )
        signal.eligible_for_ranking = eligible
        signal.status = "REPLAYED" if verified_events else signal.status
        session.flush()
        return eligible
