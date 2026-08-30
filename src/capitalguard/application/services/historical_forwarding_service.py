from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import (
    ChannelCatalog,
    HistoricalShadowChannel,
    HistoricalFinancialCandidate,
    HistoricalForwardReceipt,
    HistoricalImportBatch,
    HistoricalMessageRevision,
    HistoricalRecommendationDraft,
    HistoricalSignalMaterialization,
)

from .historical_evidence_ingestion_service import (
    HistoricalEvidenceIngestionError,
    HistoricalEvidenceIngestionService,
)
from .historical_market_replay_service import HistoricalMarketReplayService
from .historical_message_foundation_service import HistoricalMessageFoundationService
from .historical_outcome_reconciliation_service import TimelineEventInput
from .historical_replay_gate_service import HistoricalReplayGateService
from .historical_signal_materialization_service import (
    HistoricalSignalMaterializationBlocked,
    HistoricalSignalMaterializationService,
)
from .historical_signal_service import HistoricalSignalService, HistoricalSignalValidationError


@dataclass(frozen=True)
class ForwardedMessageInput:
    receiver_chat_id: int
    receiver_message_id: int
    forwarding_user_id: int | None
    source_chat_id: int | None
    source_message_id: int | None
    source_origin_type: str
    source_message_timestamp: datetime | None
    raw_text: str | None
    source_message_revision: int = 0
    source_edit_date: datetime | None = None
    source_reply_to_message_id: int | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ForwardingPreview:
    batch_id: int
    total_records: int
    accepted_records: int
    rejected_records: int
    duplicate_records: int
    hidden_origin_records: int
    manifest: dict[str, Any]


class _StaticCandleProvider:
    """Adapter used after the network fetch has completed outside G6 writes."""

    def __init__(self, candles, endpoint: str | None):
        self.candles = list(candles)
        self.endpoint = endpoint

    def fetch(self, **kwargs):
        return list(self.candles), self.endpoint


class HistoricalForwardingService:
    """Stages user-forwarded Telegram messages for historical review only."""

    SOURCE_KIND = "TELEGRAM_FORWARD"
    AUTO_PROGRESS_POLICY = "HISTORICAL_AUTO_PROGRESS_V1"
    VALID_ORIGIN_TYPES = {"CHANNEL", "MESSAGE_ORIGIN_CHANNEL"}

    def __init__(
        self,
        signal_service: HistoricalSignalService | None = None,
        message_foundation_service: HistoricalMessageFoundationService | None = None,
        evidence_ingestion_service: HistoricalEvidenceIngestionService | None = None,
        materialization_service: HistoricalSignalMaterializationService | None = None,
        replay_service: HistoricalMarketReplayService | None = None,
        replay_gate_service: HistoricalReplayGateService | None = None,
    ):
        self.signal_service = signal_service or HistoricalSignalService()
        self.message_foundation_service = message_foundation_service or HistoricalMessageFoundationService()
        self.evidence_ingestion_service = evidence_ingestion_service or HistoricalEvidenceIngestionService(self.signal_service)
        self.materialization_service = materialization_service or HistoricalSignalMaterializationService()
        self.replay_service = replay_service or HistoricalMarketReplayService(self.signal_service)
        self.replay_gate_service = replay_gate_service or HistoricalReplayGateService()

    @staticmethod
    def _normalize_chat_id(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def canonical_order_key(message: Any) -> tuple[int, datetime, int, int]:
        """Return deterministic source ordering for receipts and manifest records."""
        source_chat_id = HistoricalForwardingService._normalize_chat_id(
            message.get("source_chat_id") if isinstance(message, Mapping) else getattr(message, "source_chat_id", None)
        )
        source_timestamp = (
            message.get("source_message_timestamp")
            if isinstance(message, Mapping)
            else getattr(message, "source_message_timestamp", None)
        )
        source_message_id = HistoricalForwardingService._normalize_chat_id(
            message.get("source_message_id") if isinstance(message, Mapping) else getattr(message, "source_message_id", None)
        )
        revision = (
            message.get("source_message_revision", 0)
            if isinstance(message, Mapping)
            else getattr(message, "source_message_revision", 0)
        )
        if source_timestamp is None and isinstance(message, Mapping):
            source_timestamp = message.get("message_timestamp")
        if source_timestamp is None:
            source_timestamp = datetime.min.replace(tzinfo=timezone.utc)
        elif source_timestamp.tzinfo is None:
            source_timestamp = source_timestamp.replace(tzinfo=timezone.utc)
        else:
            source_timestamp = source_timestamp.astimezone(timezone.utc)
        try:
            revision_value = max(0, int(revision or 0))
        except (TypeError, ValueError):
            revision_value = 0
        return (
            source_chat_id if source_chat_id is not None else -(2**63),
            source_timestamp,
            source_message_id if source_message_id is not None else -1,
            revision_value,
        )

    @classmethod
    def ordered_receipts(cls, receipts: Iterable[HistoricalForwardReceipt]) -> list[HistoricalForwardReceipt]:
        """Order receipts by source facts, never by receiver arrival order."""
        return sorted(list(receipts), key=cls.canonical_order_key)

    @classmethod
    def _timeline_annotations(cls, receipts: Iterable[HistoricalForwardReceipt]) -> dict[int, dict[str, Any]]:
        """Annotate reply relationships without guessing unrelated messages together."""
        ordered = cls.ordered_receipts(receipts)
        source_ids = {
            receipt.source_message_id
            for receipt in ordered
            if receipt.source_message_id is not None
        }
        annotations: dict[int, dict[str, Any]] = {}
        for receipt in ordered:
            parent_id = receipt.source_reply_to_message_id
            if parent_id is not None and parent_id in source_ids:
                annotations[receipt.id] = {
                    "timeline_role": "CHILD_UPDATE",
                    "timeline_parent_message_id": parent_id,
                    "timeline_link_status": "EXPLICIT_REPLY",
                }
            elif parent_id is not None:
                annotations[receipt.id] = {
                    "timeline_role": "UNRESOLVED_CHILD",
                    "timeline_parent_message_id": parent_id,
                    "timeline_link_status": "PENDING_REVIEW",
                }
            else:
                annotations[receipt.id] = {
                    "timeline_role": "ROOT_CANDIDATE",
                    "timeline_parent_message_id": None,
                    "timeline_link_status": "UNLINKED",
                }
        return annotations

    @staticmethod
    def _source_content_hash(raw_text: str | None, metadata: dict[str, Any]) -> str:
        """Hash the raw semantic input, including media identity when present."""
        import hashlib
        import json

        media = metadata.get("media") or {}
        payload = {
            "raw_text": raw_text or "",
            "media": {
                "file_id": media.get("file_id"),
                "media_unique_id": media.get("media_unique_id"),
                "media_type": media.get("media_type"),
            },
        }
        normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_positive(value: int, field: str) -> None:
        if not isinstance(value, int) or value <= 0:
            raise HistoricalSignalValidationError(f"{field} must be a positive integer")

    def start_batch(
        self,
        session: Session,
        *,
        channel_catalog_id: int,
        requested_by_user_id: int,
        expected_source_chat_id: int,
        mode: str = "BATCH",
        max_records: int = 500,
    ) -> HistoricalImportBatch:
        self._validate_positive(channel_catalog_id, "channel_catalog_id")
        self._validate_positive(requested_by_user_id, "requested_by_user_id")
        expected_source_chat_id = self._normalize_chat_id(expected_source_chat_id)
        if expected_source_chat_id in (None, 0):
            raise HistoricalSignalValidationError("expected_source_chat_id must be a non-zero integer")
        if mode not in {"SINGLE", "BATCH"}:
            raise HistoricalSignalValidationError("Unsupported forwarding mode")
        if not 1 <= max_records <= 5000:
            raise HistoricalSignalValidationError("max_records must be between 1 and 5000")
        batch = self.signal_service.create_import_batch(
            session,
            source_kind=self.SOURCE_KIND,
            manifest=[],
            channel_catalog_id=channel_catalog_id,
            requested_by_user_id=requested_by_user_id,
            metadata={
                "mode": mode,
                "expected_source_chat_id": expected_source_chat_id,
                "max_records": max_records,
                "intake_status": "STAGING",
            },
        )
        batch.status = "STAGING"
        session.flush()
        return batch

    def stage_message(
        self,
        session: Session,
        *,
        batch_id: int,
        message: ForwardedMessageInput,
    ) -> HistoricalForwardReceipt:
        batch = session.get(HistoricalImportBatch, batch_id)
        if batch is None:
            raise HistoricalSignalValidationError("Forwarding batch does not exist")
        if batch.status != "STAGING":
            raise HistoricalSignalValidationError("Forwarding batch is not open for staging")
        self._validate_positive(message.receiver_chat_id, "receiver_chat_id")
        self._validate_positive(message.receiver_message_id, "receiver_message_id")
        metadata = dict(message.metadata or {})
        expected_source_chat_id = self._normalize_chat_id(
            (batch.metadata_json or {}).get("expected_source_chat_id")
        )
        source_chat_id = self._normalize_chat_id(message.source_chat_id)
        max_records = int((batch.metadata_json or {}).get("max_records") or 500)

        existing_receiver = session.execute(
            select(HistoricalForwardReceipt).where(
                HistoricalForwardReceipt.receiver_chat_id == message.receiver_chat_id,
                HistoricalForwardReceipt.receiver_message_id == message.receiver_message_id,
            )
        ).scalar_one_or_none()
        if existing_receiver is not None:
            return existing_receiver

        source_timestamp = self._utc(message.source_message_timestamp)
        edit_date = self._utc(message.source_edit_date)
        source_revision = max(0, int(message.source_message_revision or 0))
        content_hash = self._source_content_hash(message.raw_text, metadata)
        rejection_reason = None
        validation_status = "STAGED"
        origin_type = str(message.source_origin_type or "UNKNOWN").upper()
        if len([item for item in session.execute(
            select(HistoricalForwardReceipt).where(HistoricalForwardReceipt.batch_id == batch_id)
        ).scalars().all()]) >= max_records:
            raise HistoricalSignalValidationError("Forwarding batch max_records exceeded")
        if origin_type not in self.VALID_ORIGIN_TYPES or source_chat_id is None or message.source_message_id is None:
            validation_status = "REJECTED_ORIGIN"
            rejection_reason = "Missing or hidden channel origin"
        elif expected_source_chat_id is None or source_chat_id != expected_source_chat_id:
            validation_status = "REJECTED_CHANNEL"
            rejection_reason = "Forwarded source channel is not allow-listed"
        elif source_timestamp is None:
            validation_status = "REJECTED_TIMESTAMP"
            rejection_reason = "Source message timestamp is required"
        elif source_timestamp > datetime.now(timezone.utc):
            validation_status = "REJECTED_TIMESTAMP"
            rejection_reason = "Source message timestamp is in the future"

        existing_source = None
        if source_chat_id is not None and message.source_message_id is not None:
            existing_source = session.execute(
                select(HistoricalForwardReceipt).where(
                    HistoricalForwardReceipt.batch_id == batch_id,
                    HistoricalForwardReceipt.source_chat_id == source_chat_id,
                    HistoricalForwardReceipt.source_message_id == message.source_message_id,
                    HistoricalForwardReceipt.source_message_revision == source_revision,
                )
            ).scalar_one_or_none()
        if existing_source is not None:
            return existing_source

        receipt = HistoricalForwardReceipt(
            batch_id=batch_id,
            forwarding_user_id=message.forwarding_user_id,
            receiver_chat_id=message.receiver_chat_id,
            receiver_message_id=message.receiver_message_id,
            source_chat_id=source_chat_id,
            source_message_id=message.source_message_id,
            source_message_revision=source_revision,
            source_origin_type=origin_type,
            source_message_timestamp=source_timestamp,
            source_edit_date=edit_date,
            source_reply_to_message_id=message.source_reply_to_message_id,
            raw_text=message.raw_text,
            content_hash=content_hash,
            validation_status=validation_status,
            rejection_reason=rejection_reason,
            metadata_json=metadata,
        )
        session.add(receipt)
        session.flush()
        if receipt.validation_status == "STAGED":
            self.message_foundation_service.record_receipt(session, receipt=receipt)
        return receipt

    def preview_batch(self, session: Session, *, batch_id: int) -> ForwardingPreview:
        batch = session.get(HistoricalImportBatch, batch_id)
        if batch is None:
            raise HistoricalSignalValidationError("Forwarding batch does not exist")
        if batch.status not in {"STAGING", "DRY_RUN"}:
            raise HistoricalSignalValidationError("Only a staged batch can be previewed")
        receipts = list(session.execute(
            select(HistoricalForwardReceipt).where(HistoricalForwardReceipt.batch_id == batch_id)
        ).scalars().all())
        accepted = [receipt for receipt in receipts if receipt.validation_status == "STAGED"]
        rejected = [receipt for receipt in receipts if receipt.validation_status.startswith("REJECTED")]
        hidden = [receipt for receipt in receipts if receipt.validation_status == "REJECTED_ORIGIN"]
        duplicates = [receipt for receipt in receipts if receipt.validation_status == "DUPLICATE"]
        ordered_accepted = self.ordered_receipts(accepted)
        timeline_annotations = self._timeline_annotations(ordered_accepted)
        manifest = {
            "source_kind": self.SOURCE_KIND,
            "ordering": "source_chat_id,source_message_timestamp,source_message_id,source_message_revision",
            "records": [
                {
                    "telegram_channel_id": receipt.source_chat_id,
                    "telegram_message_id": receipt.source_message_id,
                    "message_revision": receipt.source_message_revision,
                    "message_timestamp": receipt.source_message_timestamp.isoformat() if receipt.source_message_timestamp else None,
                    "raw_text": receipt.raw_text,
                    "source_uri": f"telegram-forward://{receipt.receiver_chat_id}/{receipt.receiver_message_id}",
                    "metadata": {
                        **(receipt.metadata_json or {}),
                        "receiver_chat_id": receipt.receiver_chat_id,
                        "receiver_message_id": receipt.receiver_message_id,
                        "source_origin_type": receipt.source_origin_type,
                        "source_edit_date": receipt.source_edit_date.isoformat() if receipt.source_edit_date else None,
                        "source_message_timestamp": receipt.source_message_timestamp.isoformat() if receipt.source_message_timestamp else None,
                        "source_reply_to_message_id": receipt.source_reply_to_message_id,
                        "forwarding_receipt_id": receipt.id,
                        **timeline_annotations.get(receipt.id, {}),
                    },
                }
                for receipt in ordered_accepted
            ],
        }
        batch.total_records = len(receipts)
        batch.accepted_records = len(accepted)
        batch.rejected_records = len(rejected)
        batch.status = "DRY_RUN"
        batch.metadata_json = {**(batch.metadata_json or {}), "intake_status": "DRY_RUN"}
        session.flush()
        return ForwardingPreview(
            batch_id=batch_id,
            total_records=len(receipts),
            accepted_records=len(accepted),
            rejected_records=len(rejected),
            duplicate_records=len(duplicates),
            hidden_origin_records=len(hidden),
            manifest=manifest,
        )

    def apply_preview_decision(
        self,
        session: Session,
        *,
        batch_id: int,
        requested_by_user_id: int,
        action: str,
    ) -> HistoricalImportBatch:
        """Apply a human decision to a dry-run historical batch without creating live entities."""
        batch = session.get(HistoricalImportBatch, batch_id)
        if batch is None:
            raise HistoricalSignalValidationError("Forwarding batch does not exist")
        if batch.status != "DRY_RUN":
            raise HistoricalSignalValidationError("Historical preview is no longer awaiting a decision")
        if not requested_by_user_id or batch.requested_by_user_id != requested_by_user_id:
            raise HistoricalSignalValidationError("Only the batch requester can choose a historical preview action")

        normalized = action.strip().upper()
        metadata = dict(batch.metadata_json or {})
        review_modes = ((metadata.get("parser_preview") or {}).get("review_actions_by_mode") or {}).values()
        allowed_sets = [set(str(item).upper() for item in actions) for actions in review_modes]
        allowed_actions = set.intersection(*allowed_sets) if allowed_sets else {"DISMISS"}
        if normalized not in allowed_actions:
            raise HistoricalSignalValidationError("Historical preview action is not allowed for this batch")

        now = datetime.now(timezone.utc).isoformat()
        metadata["preview_decision"] = {
            "action": normalized,
            "requested_by_user_id": requested_by_user_id,
            "decided_at": now,
        }
        if normalized == "IMPORT_HISTORICAL":
            if batch.accepted_records <= 0:
                raise HistoricalSignalValidationError("Cannot request historical review without accepted records")
            batch.status = "REVIEW_REQUIRED"
            metadata["intake_status"] = "REVIEW_REQUIRED"
        elif normalized == "TRACK_ONLY":
            batch.status = "TRACK_ONLY"
            metadata["intake_status"] = "TRACK_ONLY"
        elif normalized == "DISMISS":
            batch.status = "DISMISSED"
            metadata["intake_status"] = "DISMISSED"
        else:
            raise HistoricalSignalValidationError("Unsupported historical preview action")
        batch.metadata_json = metadata
        session.flush()
        return batch

    @staticmethod
    def _interval_seconds(interval: str) -> int:
        units = {"m": 60, "h": 3600, "d": 86400}
        try:
            return int(interval[:-1]) * units[interval[-1]]
        except (KeyError, TypeError, ValueError):
            raise HistoricalSignalValidationError("Unsupported replay interval")

    @staticmethod
    def _auto_replay_interval(start: datetime, end: datetime) -> str:
        age_seconds = max(0, int((end - start).total_seconds()))
        if age_seconds <= 2 * 24 * 60 * 60:
            return "5m"
        if age_seconds <= 10 * 24 * 60 * 60:
            return "15m"
        if age_seconds <= 45 * 24 * 60 * 60:
            return "1h"
        if age_seconds <= 180 * 24 * 60 * 60:
            return "4h"
        return "1d"

    @staticmethod
    def _lifecycle_status(signal, events) -> str:
        event_types = [str(getattr(event, "event_type", "")) for event in events]
        if "AMBIGUOUS" in event_types:
            return "AMBIGUOUS"
        if "SL" in event_types:
            return "CLOSED_SL"
        if "CLOSE" in event_types:
            return "CLOSED_SOURCE"
        target_count = len(signal.targets or [])
        hit_targets = {item for item in event_types if item.startswith("TP") and item[2:].isdigit()}
        if target_count and len(hit_targets) >= target_count:
            return "CLOSED_TARGETS"
        if "ACTIVATED" in event_types:
            return "ACTIVE"
        return "NOT_ACTIVATED"

    def _canonical_auto_batch(self, session: Session, batch: HistoricalImportBatch) -> bool:
        """Allow historical replay from genuine forwards without claiming trust.

        ``canonical`` remains the stronger provenance tier, but it is not a
        prerequisite for an informational historical simulation. Every forward
        still needs immutable Telegram source identity and source timestamp.
        """
        metadata = batch.metadata_json or {}
        if metadata.get("mode") != "AUTO" or batch.source_kind != self.SOURCE_KIND:
            return False
        expected_source = self._normalize_chat_id(metadata.get("source_chat_id"))
        if expected_source is None:
            return False
        has_forward_provenance = session.scalar(
            select(HistoricalForwardReceipt.id).where(
                HistoricalForwardReceipt.batch_id == batch.id,
                HistoricalForwardReceipt.source_chat_id == expected_source,
                HistoricalForwardReceipt.source_message_id.is_not(None),
                HistoricalForwardReceipt.source_message_timestamp.is_not(None),
            ).limit(1)
        ) is not None
        if not has_forward_provenance:
            return False
        catalog = session.get(ChannelCatalog, batch.channel_catalog_id) if batch.channel_catalog_id else None
        if catalog is not None and catalog.telegram_channel_id != expected_source:
            return False
        shadow_id = metadata.get("shadow_channel_id")
        if shadow_id is not None:
            shadow = session.get(HistoricalShadowChannel, int(shadow_id))
            if shadow is None or shadow.telegram_channel_id != expected_source:
                return False
        return True

    def auto_progress_canonical_batch(
        self,
        session: Session,
        *,
        batch_id: int,
        replay_end: datetime | None = None,
        limit: int = 1500,
        provider=None,
    ) -> dict[str, Any]:
        """Advance eligible historical items only; never creates live trading state."""
        batch = session.get(HistoricalImportBatch, batch_id)
        if batch is None:
            raise HistoricalSignalValidationError("Forwarding batch does not exist")
        previous = ((batch.metadata_json or {}).get("auto_progression") or {}).get("items") or []
        previous_by_receipt = {int(item["receipt_id"]): item for item in previous if item.get("receipt_id") is not None}
        if not self._canonical_auto_batch(session, batch):
            return {
                "status": "BLOCKED",
                "reason": "AUTO_PROGRESS_REQUIRES_TELEGRAM_FORWARD_PROVENANCE",
                "progressed": 0,
                "review_required": int(batch.accepted_records or 0),
                "failed": 0,
                "items": list(previous),
            }
        if not 1 <= limit <= 1500:
            raise HistoricalSignalValidationError("Replay limit must be between 1 and 1500")

        end = self._utc(replay_end) or datetime.now(timezone.utc)
        existing_summary = (batch.metadata_json or {}).get("auto_progression")
        staged_exists = session.scalar(
            select(HistoricalForwardReceipt.id).where(
                HistoricalForwardReceipt.batch_id == batch_id,
                HistoricalForwardReceipt.validation_status == "STAGED",
            ).limit(1)
        ) is not None
        if not staged_exists and existing_summary:
            return {
                "status": existing_summary.get("status", "COMPLETED_UNVERIFIABLE"),
                "progressed": existing_summary.get("progressed", 0),
                "review_required": existing_summary.get("review_required", 0),
                "failed": existing_summary.get("failed", 0),
                "items": existing_summary.get("items", []),
            }
        receipts = session.execute(
            select(HistoricalForwardReceipt)
            .where(HistoricalForwardReceipt.batch_id == batch_id)
            .order_by(HistoricalForwardReceipt.id)
        ).scalars().all()
        items: list[dict[str, Any]] = []
        progressed = 0
        review_required = 0
        failed = 0
        replay_statuses: list[str] = []

        for receipt in receipts:
            if receipt.validation_status != "STAGED":
                if receipt.id in previous_by_receipt:
                    items.append(previous_by_receipt[receipt.id])
                continue
            item: dict[str, Any] = {"receipt_id": receipt.id, "status": "REVIEW_REQUIRED"}
            signal = None
            materialization = None
            start = None
            try:
                with session.begin_nested():
                    revision = session.execute(
                        select(HistoricalMessageRevision)
                        .where(HistoricalMessageRevision.receipt_id == receipt.id)
                        .order_by(HistoricalMessageRevision.revision_number.desc())
                    ).scalars().first()
                    if revision is None:
                        # G1 deduplicates canonical revisions by message/content hash.
                        # A repeated genuine forward can therefore reuse an existing
                        # revision whose receipt_id points at an earlier receipt. Recover
                        # that shared revision through the foundation contract instead of
                        # treating the new receipt as an administrative review failure.
                        revision = self.message_foundation_service.record_receipt(
                            session,
                            receipt=receipt,
                        )
                    if revision is None:
                        raise HistoricalSignalMaterializationBlocked("AUTO_PROGRESS_BLOCKED:REVISION_NOT_FOUND")
                    draft = session.execute(
                        select(HistoricalRecommendationDraft).where(
                            HistoricalRecommendationDraft.revision_id == revision.id,
                            HistoricalRecommendationDraft.draft_kind == "NEW_RECOMMENDATION",
                        )
                    ).scalar_one_or_none()
                    if draft is None:
                        raise HistoricalSignalMaterializationBlocked("AUTO_PROGRESS_BLOCKED:DRAFT_NOT_FOUND")
                    projection = (draft.evidence_chain_json or {}).get("semantic_materialization") or {}
                    if projection.get("status") != "SUCCESS":
                        raise HistoricalSignalMaterializationBlocked("AUTO_PROGRESS_BLOCKED:SEMANTIC_REVIEW_REQUIRED")
                    if draft.status not in {"DRAFT", "REVIEW_REQUIRED"}:
                        raise HistoricalSignalMaterializationBlocked("AUTO_PROGRESS_BLOCKED:DRAFT_REVIEW_REQUIRED")
                    # A successful semantic projection is the system-policy acceptance
                    # boundary for genuine Telegram forwards. The adjudicator may still
                    # have marked the draft REVIEW_REQUIRED because extracted candidates
                    # start as PENDING; AUTO progression accepts those candidates below.
                    candidate_ids = self.materialization_service._flatten_candidate_ids(draft.evidence_chain_json)
                    candidates = session.execute(
                        select(HistoricalFinancialCandidate).where(HistoricalFinancialCandidate.id.in_(candidate_ids))
                    ).scalars().all() if candidate_ids else []
                    if not candidate_ids or len(candidates) != len(candidate_ids):
                        raise HistoricalSignalMaterializationBlocked("AUTO_PROGRESS_BLOCKED:CANDIDATE_PROVENANCE_INCOMPLETE")
                    if any(candidate.status != "CANDIDATE" for candidate in candidates):
                        raise HistoricalSignalMaterializationBlocked("AUTO_PROGRESS_BLOCKED:CANDIDATE_CONFLICT")
                    # REVIEW_REQUIRED is an internal adjudication state. A complete
                    # semantic projection is the system-policy acceptance boundary;
                    # genuine field conflicts were already rejected by candidate.status
                    # above, so this state must not block informational historical replay.
                    if any(candidate.review_status not in {"PENDING", "ACCEPTED", "REVIEW_REQUIRED"} for candidate in candidates):
                        raise HistoricalSignalMaterializationBlocked("AUTO_PROGRESS_BLOCKED:CANDIDATE_REVIEW_REQUIRED")
                    for candidate in candidates:
                        candidate.review_status = "ACCEPTED"
                    draft.status = "ACCEPTED"
                    draft.reviewed_at = datetime.now(timezone.utc)
                    draft.review_note = "Accepted by HISTORICAL_AUTO_PROGRESS_V1; this is a system policy action, not owner review."
                    draft.override_json = {
                        "policy": self.AUTO_PROGRESS_POLICY,
                        "actor_type": "SYSTEM_POLICY",
                        "human_reviewer": False,
                        "live_activation": False,
                    }
                    evidence = self.evidence_ingestion_service.ingest_automatic_forward_receipt(
                        session,
                        batch_id=batch.id,
                        receipt=receipt,
                        policy_version=self.AUTO_PROGRESS_POLICY,
                    )
                    if evidence is None:
                        raise HistoricalSignalMaterializationBlocked("AUTO_PROGRESS_BLOCKED:EVIDENCE_NOT_CREATED")
                    revision.evidence_id = evidence.id
                    signal = self.materialization_service.materialize(session, draft_id=draft.id)
                    materialization = session.execute(
                        select(HistoricalSignalMaterialization).where(
                            HistoricalSignalMaterialization.draft_id == draft.id
                        )
                    ).scalar_one()
                    start = self._utc(signal.decision_timestamp)
                    item.update({
                        "status": "MATERIALIZED",
                        "signal_id": signal.id,
                        "public_ref": signal.public_ref,
                        "replay_policy": self.AUTO_PROGRESS_POLICY,
                        "source_timestamp": start.isoformat(),
                    })
                progressed += 1
            except (HistoricalSignalMaterializationBlocked, HistoricalEvidenceIngestionError) as exc:
                item["reason"] = str(exc)
                review_required += 1
                items.append(item)
                continue
            except Exception as exc:
                item["status"] = "PROGRESSION_FAILED"
                item["reason"] = f"AUTO_PROGRESS_FAILED:{type(exc).__name__}"
                failed += 1
                items.append(item)
                continue

            # Network/provider work happens after the G5 savepoint has released.
            interval = self._auto_replay_interval(start, end)
            try:
                with session.begin_nested():
                    replay = self.replay_service.replay_g6(
                        session,
                        signal_id=signal.id,
                        materialization_id=materialization.id,
                        start=start,
                        replay_end=end,
                        interval=interval,
                        limit=limit,
                        provider=provider,
                    )
                    events = replay.get("events") or []
                    result_status = str(replay.get("status") or "FAILED")
                    run = replay.get("run")
                    coverage = replay.get("coverage")
                    coverage_status = getattr(run, "coverage_status", None) or (coverage.status.value if coverage else None)
                    coverage_ratio = getattr(run, "coverage_ratio", None)
                    if coverage_ratio is None and coverage is not None:
                        coverage_ratio = coverage.coverage_ratio
                    actual_start = getattr(run, "actual_start", None) or (coverage.actual_start if coverage else None)
                    actual_end = getattr(run, "actual_end", None) or (coverage.actual_end if coverage else None)
                    item.update({
                        "status": "REPLAYED" if result_status in {"COMPLETED", "COMPLETED_UNVERIFIABLE"} else "REPLAY_PARTIAL" if result_status == "REPLAY_PARTIAL" else "REPLAY_FAILED",
                        "replay_status": result_status,
                        "event_count": len(events),
                        "last_event": getattr(events[-1], "event_type", None) if events else None,
                        "lifecycle_status": self._lifecycle_status(signal, events),
                        "interval": interval,
                        "coverage_status": coverage_status,
                        "coverage_ratio": coverage_ratio,
                        "coverage_start": actual_start.isoformat() if actual_start else None,
                        "coverage_end": actual_end.isoformat() if actual_end else None,
                    })
                    replay_statuses.append(result_status)
                    if result_status not in {"COMPLETED", "COMPLETED_UNVERIFIABLE"}:
                        failed += 1
            except Exception as exc:
                item.update({
                    "status": "REPLAY_FAILED",
                    "replay_status": "FAILED",
                    "reason": "Historical replay failed; prior G5 evidence was preserved.",
                    "error_type": type(exc).__name__,
                    "interval": interval,
                })
                failed += 1

            items.append(item)

        remaining_staged = sum(1 for receipt in receipts if receipt.validation_status == "STAGED")
        if remaining_staged == 0 and progressed:
            batch.status = "EVIDENCE_INGESTED"
        overall_status = "PARTIAL"
        if progressed and not review_required and not failed:
            overall_status = "COMPLETED_UNVERIFIABLE" if "COMPLETED_UNVERIFIABLE" in replay_statuses else "COMPLETED"
        batch.metadata_json = {
            **(batch.metadata_json or {}),
            "auto_progression": {
                "policy": self.AUTO_PROGRESS_POLICY,
                "status": overall_status,
                "progressed": progressed,
                "review_required": review_required,
                "failed": failed,
                "replay_end": end.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "items": items,
            },
        }
        session.flush()
        return {
            "status": overall_status,
            "progressed": progressed,
            "review_required": review_required,
            "failed": failed,
            "items": items,
        }

    def validate_batch(self, session: Session, *, batch_id: int, owner_note: str) -> HistoricalImportBatch:
        preview = self.preview_batch(session, batch_id=batch_id)
        if not owner_note.strip():
            raise HistoricalSignalValidationError("Owner approval note is required")
        batch = self.signal_service.validate_import_batch(
            session,
            batch_id=batch_id,
            accepted_records=preview.accepted_records,
            rejected_records=preview.rejected_records,
        )
        batch.metadata_json = {**(batch.metadata_json or {}), "owner_approval_note": owner_note.strip()}
        session.flush()
        return batch

    def ingest_validated_batch(self, session: Session, *, batch_id: int) -> list[Any]:
        batch = session.get(HistoricalImportBatch, batch_id)
        if batch is None or batch.status != "VALIDATED":
            raise HistoricalSignalValidationError("Only VALIDATED forwarding batches can ingest evidence")
        receipts = list(session.execute(
            select(HistoricalForwardReceipt).where(
                HistoricalForwardReceipt.batch_id == batch_id,
                HistoricalForwardReceipt.validation_status == "STAGED",
            )
        ).scalars().all())
        evidence = []
        for receipt in receipts:
            item = self.signal_service.ingest_evidence(
                session,
                batch_id=batch.id,
                source_kind=self.SOURCE_KIND,
                channel_catalog_id=batch.channel_catalog_id,
                telegram_channel_id=receipt.source_chat_id,
                telegram_message_id=receipt.source_message_id,
                message_revision=receipt.source_message_revision,
                message_timestamp=receipt.source_message_timestamp,
                raw_text=receipt.raw_text,
                source_uri=f"telegram-forward://{receipt.receiver_chat_id}/{receipt.receiver_message_id}",
                metadata=receipt.metadata_json,
            )
            receipt.evidence_id = item.id
            receipt.validation_status = "INGESTED"
            evidence.append(item)
        session.flush()
        return evidence
