from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import (
    HistoricalFinancialCandidate,
    HistoricalMessageRevision,
    HistoricalRecommendationDraft,
    HistoricalSignal,
    HistoricalSignalMaterialization,
)


class HistoricalSignalMaterializationBlocked(ValueError):
    """Raised when a G4 draft cannot be safely materialized into history."""


class HistoricalSignalMaterializationService:
    """G5-only writer: materializes accepted drafts, never market outcomes or live entities."""

    REQUIRED_NEW = {"ASSET", "DIRECTION", "ENTRY"}

    @staticmethod
    def _utc(value: datetime | None) -> datetime:
        if value is None:
            raise HistoricalSignalMaterializationBlocked("MATERIALIZATION_BLOCKED:SOURCE_TIMESTAMP_MISSING")
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    @staticmethod
    def _flatten_candidate_ids(chain: dict[str, Any] | None) -> list[int]:
        values: list[Any] = []
        for item in (chain or {}).values():
            values.extend(item if isinstance(item, list) else [item])
        return sorted({int(item) for item in values if isinstance(item, int) or (isinstance(item, str) and item.isdigit())})

    @staticmethod
    def _candidate_value(candidate: HistoricalFinancialCandidate):
        value = candidate.value_json
        if isinstance(value, dict) and "value" in value:
            return value["value"]
        return value if value is not None else candidate.normalized_value

    def materialize(self, session: Session, *, draft_id: int) -> HistoricalSignal:
        existing = session.execute(
            select(HistoricalSignalMaterialization).where(HistoricalSignalMaterialization.draft_id == draft_id)
        ).scalar_one_or_none()
        if existing is not None:
            return existing.signal

        draft = session.get(HistoricalRecommendationDraft, draft_id)
        if draft is None:
            raise HistoricalSignalMaterializationBlocked("MATERIALIZATION_BLOCKED:DRAFT_NOT_FOUND")
        if draft.status != "ACCEPTED":
            raise HistoricalSignalMaterializationBlocked("MATERIALIZATION_BLOCKED:DRAFT_NOT_ACCEPTED")
        parent_materialization = None
        if draft.related_draft_id is not None:
            parent_materialization = session.execute(
                select(HistoricalSignalMaterialization).where(
                    HistoricalSignalMaterialization.draft_id == draft.related_draft_id
                )
            ).scalar_one_or_none()
            if parent_materialization is None:
                raise HistoricalSignalMaterializationBlocked("MATERIALIZATION_BLOCKED:LIFECYCLE_PARENT_MATERIALIZATION_REQUIRED")
        revision = session.get(HistoricalMessageRevision, draft.revision_id)
        if revision is None or revision.message_id is None or revision.evidence_id is None:
            raise HistoricalSignalMaterializationBlocked("MATERIALIZATION_BLOCKED:PROVENANCE_INCOMPLETE")
        source_timestamp = self._utc(revision.source_timestamp)

        candidate_ids = self._flatten_candidate_ids(draft.evidence_chain_json)
        candidates = session.execute(
            select(HistoricalFinancialCandidate).where(HistoricalFinancialCandidate.id.in_(candidate_ids))
        ).scalars().all() if candidate_ids else []
        if candidate_ids and len(candidates) != len(candidate_ids):
            raise HistoricalSignalMaterializationBlocked("MATERIALIZATION_BLOCKED:CANDIDATE_PROVENANCE_INCOMPLETE")
        if any(item.status != "CANDIDATE" or item.review_status != "ACCEPTED" for item in candidates):
            raise HistoricalSignalMaterializationBlocked("MATERIALIZATION_BLOCKED:CANDIDATE_NOT_ACCEPTED")

        by_field: dict[str, list[HistoricalFinancialCandidate]] = {}
        for item in candidates:
            by_field.setdefault(item.field_type, []).append(item)
        if any(field != "TARGET" and len(items) > 1 for field, items in by_field.items()):
            raise HistoricalSignalMaterializationBlocked("MATERIALIZATION_BLOCKED:CANDIDATE_CONFLICT")
        if draft.draft_kind == "NEW_RECOMMENDATION" and not self.REQUIRED_NEW.issubset(by_field):
            raise HistoricalSignalMaterializationBlocked("MATERIALIZATION_BLOCKED:REQUIRED_CANDIDATE_MISSING")

        values = {field: self._candidate_value(items[0]) for field, items in by_field.items()}
        target_values = []
        for index, candidate in enumerate(by_field.get("TARGET", []), start=1):
            value = self._candidate_value(candidate)
            if isinstance(value, dict):
                target_values.append({
                    "price": value.get("price", value.get("value", value.get("target"))),
                    "close_percent": value.get("close_percent", value.get("percentage", 0)),
                    "index": value.get("index", index),
                })
            else:
                target_values.append({"price": value, "close_percent": 0, "index": index})
        signal = parent_materialization.signal if parent_materialization else HistoricalSignal(
            public_ref=f"HIST-G5-{draft.id:012d}",
            evidence_id=revision.evidence_id,
            asset=str(values["ASSET"]) if "ASSET" in values else None,
            side=str(values["DIRECTION"]) if "DIRECTION" in values else None,
            entry=Decimal(str(values["ENTRY"])) if "ENTRY" in values else None,
            stop_loss=Decimal(str(values["STOP_LOSS"])) if "STOP_LOSS" in values else None,
            targets=target_values,
            market=str(values["MARKET"]) if "MARKET" in values else None,
            decision_timestamp=source_timestamp,
            status="MATERIALIZED",
            trust_tier="UNVERIFIED",
            confidence_score=Decimal(str(draft.confidence_score or 0)),
            eligible_for_ranking=False,
        )
        try:
            if parent_materialization is None:
                session.add(signal)
                session.flush()
            materialization = HistoricalSignalMaterialization(
                draft_id=draft.id,
                signal_id=signal.id,
                related_materialization_id=parent_materialization.id if parent_materialization else None,
                revision_id=revision.id,
                evidence_id=revision.evidence_id,
                materialization_kind=draft.draft_kind,
                source_timestamp=source_timestamp,
                provenance_json={
                    "draft_id": draft.id,
                    "candidate_ids": candidate_ids,
                    "revision_id": revision.id,
                    "canonical_message_id": revision.message_id,
                    "evidence_id": revision.evidence_id,
                    "source_timestamp": source_timestamp.isoformat(),
                },
            )
            session.add(materialization)
            session.flush()
        except IntegrityError:
            session.rollback()
            materialized = session.execute(
                select(HistoricalSignalMaterialization).where(HistoricalSignalMaterialization.draft_id == draft_id)
            ).scalar_one_or_none()
            if materialized is None:
                raise
            return materialized.signal
        return signal
