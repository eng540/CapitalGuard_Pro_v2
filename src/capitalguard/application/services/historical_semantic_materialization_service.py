from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.application.services.historical_content_understanding_service import (
    HistoricalContentUnderstandingService,
)
from capitalguard.application.services.historical_financial_candidate_service import (
    HistoricalFinancialCandidateService,
)
from capitalguard.application.services.historical_adjudication_service import HistoricalAdjudicationService
from capitalguard.application.services.parsing_service import ParsingService
from capitalguard.infrastructure.db.models import (
    HistoricalContentInterpretation,
    HistoricalFinancialCandidate,
    HistoricalMessageRevision,
)
from capitalguard.infrastructure.db.repository import ParsingRepository


class HistoricalSemanticMaterializationError(ValueError):
    """Raised when source semantics cannot be materialized safely."""


class HistoricalSemanticMaterializationService:
    """Materialize source meaning through existing G1-G3 contracts.

    This service deliberately does not create HistoricalSignal or invoke G5.
    It persists the semantic projection inside the existing G2 interpretation
    JSON and reuses HistoricalFinancialCandidate for field-level evidence.
    """

    MATERIALIZATION_VERSION = "g6-semantic-v1"
    IMAGE_EXTRACTOR_VERSION = "g6-vision-candidate-v1"
    _FIELD_MAP = {
        "ASSET": "asset",
        "DIRECTION": "direction",
        "ENTRY": "entry",
        "STOP_LOSS": "stop_loss",
        "LEVERAGE": "leverage",
    }

    def __init__(self):
        self.interpreter = HistoricalContentUnderstandingService()
        self.candidate_service = HistoricalFinancialCandidateService()
        self.adjudicator = HistoricalAdjudicationService()
        self.parser = ParsingService(ParsingRepository)

    @staticmethod
    def _as_decimal(value: Any) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value if value.is_finite() and value > 0 else None
        try:
            return ParsingService(ParsingRepository)._parse_one_number(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

    @staticmethod
    def _confidence(payload: dict[str, Any], field: str) -> Decimal:
        raw = payload.get(f"{field.lower()}_confidence", payload.get("confidence", 0.5))
        try:
            value = Decimal(str(raw))
        except Exception:
            value = Decimal("0.5000")
        return max(Decimal("0"), min(Decimal("1"), value)).quantize(Decimal("0.0001"))

    @staticmethod
    def _image_payload(image_result: dict[str, Any] | None) -> dict[str, Any] | None:
        if not image_result:
            return None
        if image_result.get("status") == "error":
            return None
        payload = image_result.get("data", image_result)
        return payload if isinstance(payload, dict) else None

    def _candidate_value(self, field: str, value: Any) -> tuple[Any, str] | None:
        if field in {"ENTRY", "STOP_LOSS", "LEVERAGE"}:
            numeric = self._as_decimal(value)
            if numeric is None:
                return None
            return {"value": str(numeric)}, str(numeric)
        if field == "TARGET":
            numeric = self._as_decimal(value)
            if numeric is None:
                return None
            return {"value": str(numeric)}, str(numeric)
        if value is None or not str(value).strip():
            return None
        normalized = str(value).strip().upper()
        return {"value": normalized}, normalized

    def _persist_image_candidates(
        self,
        session: Session,
        *,
        interpretation: HistoricalContentInterpretation,
        image_result: dict[str, Any],
        image_provenance: dict[str, Any] | None,
    ) -> list[HistoricalFinancialCandidate]:
        payload = self._image_payload(image_result)
        if payload is None:
            return []
        provenance_base = {
            "revision_id": interpretation.revision_id,
            "interpretation_id": interpretation.id,
            "modality": "IMAGE",
            "extraction_method": "VISION",
            "extractor_version": self.IMAGE_EXTRACTOR_VERSION,
            **(image_provenance or {}),
        }
        candidates: list[HistoricalFinancialCandidate] = []
        scalar_fields = {
            "ASSET": payload.get("asset"),
            "DIRECTION": payload.get("side", payload.get("direction")),
            "ENTRY": payload.get("entry"),
            "STOP_LOSS": payload.get("stop_loss"),
            "LEVERAGE": payload.get("leverage"),
        }
        for field, raw_value in scalar_fields.items():
            normalized = self._candidate_value(field, raw_value)
            if normalized is None:
                continue
            value_json, normalized_value = normalized
            span = f"image:{field.lower()}"
            provenance = {**provenance_base, "field": field, "location": payload.get(f"{field.lower()}_location")}
            existing = session.execute(
                select(HistoricalFinancialCandidate).where(
                    HistoricalFinancialCandidate.interpretation_id == interpretation.id,
                    HistoricalFinancialCandidate.field_type == field,
                    HistoricalFinancialCandidate.normalized_value == normalized_value,
                    HistoricalFinancialCandidate.span_text == span,
                    HistoricalFinancialCandidate.extractor_version == self.IMAGE_EXTRACTOR_VERSION,
                )
            ).scalar_one_or_none()
            if existing is not None:
                candidates.append(existing)
                continue
            item = HistoricalFinancialCandidate(
                interpretation_id=interpretation.id,
                field_type=field,
                value_json=value_json,
                normalized_value=normalized_value,
                span_text=span,
                confidence_score=self._confidence(payload, field),
                status="CANDIDATE",
                extractor_version=self.IMAGE_EXTRACTOR_VERSION,
                provenance_json=provenance,
                review_status="PENDING",
            )
            session.add(item)
            candidates.append(item)

        targets = payload.get("targets") or []
        for index, target in enumerate(targets, start=1):
            raw_value = target.get("price") if isinstance(target, dict) else target
            normalized = self._candidate_value("TARGET", raw_value)
            if normalized is None:
                continue
            value_json, normalized_value = normalized
            value_json["index"] = index
            span = f"image:target:{index}"
            provenance = {**provenance_base, "field": "TARGET", "index": index, "location": (target.get("location") if isinstance(target, dict) else None)}
            existing = session.execute(
                select(HistoricalFinancialCandidate).where(
                    HistoricalFinancialCandidate.interpretation_id == interpretation.id,
                    HistoricalFinancialCandidate.field_type == "TARGET",
                    HistoricalFinancialCandidate.normalized_value == normalized_value,
                    HistoricalFinancialCandidate.span_text == span,
                    HistoricalFinancialCandidate.extractor_version == self.IMAGE_EXTRACTOR_VERSION,
                )
            ).scalar_one_or_none()
            if existing is not None:
                candidates.append(existing)
                continue
            item = HistoricalFinancialCandidate(
                interpretation_id=interpretation.id,
                field_type="TARGET",
                value_json=value_json,
                normalized_value=normalized_value,
                span_text=span,
                confidence_score=Decimal("0.5000"),
                status="CANDIDATE",
                extractor_version=self.IMAGE_EXTRACTOR_VERSION,
                provenance_json=provenance,
                review_status="PENDING",
            )
            session.add(item)
            candidates.append(item)
        session.flush()
        return candidates

    @staticmethod
    def _distinct_values(items: list[HistoricalFinancialCandidate]) -> list[str]:
        return sorted({str(item.normalized_value) for item in items})

    @staticmethod
    def _market_value(raw_text: str | None, image_payload: dict[str, Any] | None) -> str | None:
        image_market = (image_payload or {}).get("market")
        if image_market:
            return str(image_market).strip().upper()
        text = (raw_text or "").upper()
        if "FUTURES" in text or "PERP" in text:
            return "FUTURES"
        if "SPOT" in text:
            return "SPOT"
        return None

    def _build_projection(
        self,
        candidates: list[HistoricalFinancialCandidate],
        *,
        raw_text: str | None = None,
        image_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        grouped: dict[str, list[HistoricalFinancialCandidate]] = {}
        for item in candidates:
            grouped.setdefault(item.field_type, []).append(item)
        canonical: dict[str, Any] = {}
        evidence: dict[str, list[dict[str, Any]]] = {}
        conflicts: list[str] = []
        missing: list[str] = []
        required = ("ASSET", "DIRECTION", "ENTRY", "STOP_LOSS", "TARGET")
        for field, output in self._FIELD_MAP.items():
            rows = grouped.get(field, [])
            values = self._distinct_values(rows)
            if len(values) > 1:
                conflicts.append(output)
                canonical[output] = None
            elif values:
                canonical[output] = values[0] if field not in {"ENTRY", "STOP_LOSS", "LEVERAGE"} else values[0]
            else:
                canonical[output] = None
            evidence[output] = [
                {
                    "candidate_id": row.id,
                    "modality": (row.provenance_json or {}).get("modality", "TEXT"),
                    "span": row.span_text,
                    "raw_value": row.value_json,
                    "normalized_value": row.normalized_value,
                    "normalization": {
                        "source_span": row.span_text,
                        "normalized_value": row.normalized_value,
                    },
                    "extractor_version": row.extractor_version,
                    "provenance": row.provenance_json,
                    "status": row.status,
                    "validation_status": row.status,
                    "review_status": row.review_status,
                    "final_semantic_status": None,
                }
                for row in rows
            ]
        target_rows = grouped.get("TARGET", [])
        if target_rows:
            target_values = self._distinct_values(target_rows)
            canonical["targets"] = target_values
        else:
            canonical["targets"] = []
        for field in required:
            if not grouped.get(field):
                missing.append(self._FIELD_MAP.get(field, field.lower()))
        if conflicts:
            status = "CONFLICT"
        elif missing:
            status = "INCOMPLETE"
        elif any(row.status != "CANDIDATE" for row in candidates):
            status = "AMBIGUOUS"
        else:
            status = "SUCCESS"
        for rows in evidence.values():
            for item in rows:
                item["final_semantic_status"] = status
        canonical["market"] = self._market_value(raw_text, image_payload)
        if canonical["market"] is None:
            missing.append("market")
            if status == "SUCCESS":
                status = "INCOMPLETE"
                for rows in evidence.values():
                    for item in rows:
                        item["final_semantic_status"] = status
        return {
            "materialization_version": self.MATERIALIZATION_VERSION,
            "status": status,
            "canonical": canonical,
            "field_evidence": evidence,
            "missing_fields": missing,
            "conflicting_fields": conflicts,
        }

    def materialize_revision(
        self,
        session: Session,
        *,
        revision_id: int,
        image_result: dict[str, Any] | None = None,
        image_provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        revision = session.get(HistoricalMessageRevision, revision_id)
        if revision is None:
            raise HistoricalSemanticMaterializationError("Historical message revision does not exist")
        interpretation = self.interpreter.interpret_revision(session, revision_id=revision_id)
        candidates = []
        if revision.raw_text and revision.raw_text.strip():
            candidates.extend(self.candidate_service.extract(session, interpretation_id=interpretation.id))
        if image_result:
            candidates.extend(
                self._persist_image_candidates(
                    session,
                    interpretation=interpretation,
                    image_result=image_result,
                    image_provenance=image_provenance,
                )
            )
        projection = self._build_projection(
            candidates,
            raw_text=revision.raw_text,
            image_payload=self._image_payload(image_result),
        )
        draft = self.adjudicator.adjudicate(session, revision_id=revision_id)
        draft.evidence_chain_json = {
            **(draft.evidence_chain_json or {}),
            "semantic_materialization": projection,
            "materialization_version": self.MATERIALIZATION_VERSION,
        }
        interpretation.provenance_json = {
            **(interpretation.provenance_json or {}),
            "semantic_materialization": {
                "version": self.MATERIALIZATION_VERSION,
                "revision_id": revision_id,
                "draft_id": draft.id,
                "image_provenance": image_provenance,
            },
        }
        session.flush()
        return projection

    def materialize_related_revisions(
        self,
        session: Session,
        *,
        anchor_revision_id: int,
        related_revision_ids: list[int],
    ) -> dict[str, Any]:
        """Materialize approved related revisions without flattening their evidence."""
        revisions = [session.get(HistoricalMessageRevision, anchor_revision_id)]
        revisions.extend(session.get(HistoricalMessageRevision, item) for item in related_revision_ids)
        if any(item is None for item in revisions):
            raise HistoricalSemanticMaterializationError("Related historical revision does not exist")
        message_ids = [item.message_id for item in revisions]
        if len(set(message_ids)) != len(message_ids):
            raise HistoricalSemanticMaterializationError("Related revisions must belong to distinct messages")
        if related_revision_ids:
            from capitalguard.infrastructure.db.models import HistoricalMessageRelationship

            relationships = session.execute(
                select(HistoricalMessageRelationship).where(
                    HistoricalMessageRelationship.source_message_id.in_(message_ids[1:]),
                    HistoricalMessageRelationship.target_message_id == message_ids[0],
                    HistoricalMessageRelationship.review_status == "ACCEPTED",
                )
            ).scalars().all()
            if len(relationships) != len(related_revision_ids):
                raise HistoricalSemanticMaterializationError("Related-message context is not approved")
        projections = [
            self.materialize_revision(session, revision_id=item.id)
            for item in revisions
        ]
        interpretation_ids = [
            row.id
            for row in session.execute(
                select(HistoricalContentInterpretation).where(
                    HistoricalContentInterpretation.revision_id.in_(item.id for item in revisions)
                )
            ).scalars().all()
        ]
        candidates = session.execute(
            select(HistoricalFinancialCandidate).where(
                HistoricalFinancialCandidate.interpretation_id.in_(interpretation_ids)
            )
        ).scalars().all()
        combined = self._build_projection(
            list(candidates),
            raw_text="\n".join(item.raw_text or "" for item in revisions),
        )
        combined["related_context"] = {
            "anchor_revision_id": anchor_revision_id,
            "related_revision_ids": related_revision_ids,
            "source_revision_ids": [item.id for item in revisions],
            "individual_statuses": [item["status"] for item in projections],
        }
        anchor_interpretation = session.execute(
            select(HistoricalContentInterpretation).where(
                HistoricalContentInterpretation.revision_id == anchor_revision_id
            )
        ).scalar_one()
        anchor_draft = self.adjudicator.adjudicate(session, revision_id=anchor_revision_id)
        anchor_draft.evidence_chain_json = {
            **(anchor_draft.evidence_chain_json or {}),
            "semantic_materialization": combined,
            "materialization_version": self.MATERIALIZATION_VERSION,
        }
        session.flush()
        return combined
