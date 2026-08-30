from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from capitalguard.application.services.continuum_handoff_gate import ContinuumHandoffFacts, ContinuumHandoffGate
from capitalguard.application.services.historical_evidence_ingestion_service import HistoricalEvidenceIngestionService
from capitalguard.application.services.historical_owner_review_service import HistoricalOwnerReviewService
from capitalguard.application.services.operational_admission_service import OperationalAdmissionService
from capitalguard.application.services.operational_decision_service import OperationalDecisionService
from capitalguard.config import settings
from capitalguard.infrastructure.db.models import HistoricalForwardReceipt, HistoricalImportBatch, HistoricalSignal, HistoricalSignalEvidence, HistoricalSignalMaterialization, UserTrade, UserTradeStatusEnum, WebCommandAudit
from capitalguard.infrastructure.db.repository import UserRepository


class WebCommandError(ValueError):
    """Raised when a privileged Web command violates the Core command boundary."""


class WebCommandService:
    _IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$")

    REVIEW = "HISTORICAL_OWNER_REVIEW"
    INGEST = "HISTORICAL_EVIDENCE_INGEST"
    MATERIALIZE = "HISTORICAL_SIGNAL_MATERIALIZE"
    REPLAY_BINANCE = "HISTORICAL_BINANCE_REPLAY"
    G6_REPLAY = "G6_HISTORICAL_REPLAY"
    CONTINUUM_HANDOFF_DECISION = "CONTINUUM_HANDOFF_DECISION"
    CONTINUUM_HANDOFF_EXECUTE = "CONTINUUM_HANDOFF_EXECUTE"
    CONTINUUM_ACTIVATE_USER_TRADE = "CONTINUUM_ACTIVATE_USER_TRADE"
    CLOSE_USER_TRADE = "USER_TRADE_MANUAL_CLOSE"
    PARTIAL_CLOSE_USER_TRADE = "USER_TRADE_MANUAL_PARTIAL_CLOSE"
    MOVE_USER_TRADE_STOP_TO_BREAKEVEN = "USER_TRADE_MOVE_STOP_TO_BREAKEVEN"
    UPDATE_PENDING_USER_TRADE_ENTRY = "USER_TRADE_PENDING_ENTRY_UPDATE"
    CANCEL_USER_TRADE = "USER_TRADE_PENDING_CANCEL"
    CREATE_ANALYST_RECOMMENDATION = "ANALYST_RECOMMENDATION_CONFIRM"

    @staticmethod
    def _require_owner(session: Session, actor_telegram_id: int):
        configured_owner = str(settings.TELEGRAM_ADMIN_CHAT_ID or "")
        if not configured_owner or configured_owner != str(actor_telegram_id):
            raise WebCommandError("Owner authorization required")
        user = UserRepository(session).find_by_telegram_id(actor_telegram_id)
        if user is None:
            raise WebCommandError("Owner identity does not exist in Core")
        return user

    def require_owner(self, session: Session, actor_telegram_id: int):
        """Public owner assertion for read-only operations telemetry endpoints."""
        return self._require_owner(session, actor_telegram_id)

    @staticmethod
    def _fingerprint(
        command_type: str,
        actor_telegram_id: int,
        target_type: str,
        target_id: int,
        payload: dict,
    ) -> str:
        raw = json.dumps(
            {
                "command_type": command_type,
                "actor": actor_telegram_id,
                "target_type": target_type,
                "target_id": target_id,
                "payload": payload,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def derive_compatibility_idempotency_key(
        actor_telegram_id: int,
        recommendation: dict,
    ) -> str:
        """Derive a stable key for legacy payloads that have no command key."""
        payload = dict(recommendation)
        payload["target_channel_ids"] = sorted(
            int(channel_id)
            for channel_id in payload.get("target_channel_ids", set())
        )
        raw = json.dumps(
            {"actor": actor_telegram_id, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return "legacy-create-" + hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def _validate_idempotency_key(cls, idempotency_key: str) -> str:
        if not isinstance(idempotency_key, str) or not cls._IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
            raise WebCommandError("Idempotency key must be 8-160 safe characters")
        return idempotency_key

    @classmethod
    def _replay_or_reject(cls, session: Session, *, idempotency_key: str, request_hash: str) -> dict | None:
        idempotency_key = cls._validate_idempotency_key(idempotency_key)
        existing = session.execute(select(WebCommandAudit).where(WebCommandAudit.idempotency_key == idempotency_key)).scalar_one_or_none()
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise WebCommandError("Idempotency key cannot be reused with a different command")
        replayed = dict(existing.response_json or {})
        replayed.pop("_meta", None)
        return replayed

    @classmethod
    def _record(cls, session: Session, *, idempotency_key: str, command_type: str, actor_user_id: int, target_type: str, target_id: int, request_hash: str, response: dict, status: str = "COMPLETED") -> None:
        idempotency_key = cls._validate_idempotency_key(idempotency_key)
        audit_response = dict(response)
        audit_response["_meta"] = {
            "correlation_id": idempotency_key,
            "request_hash": request_hash,
        }
        session.add(WebCommandAudit(
            idempotency_key=idempotency_key,
            command_type=command_type,
            actor_user_id=actor_user_id,
            target_type=target_type,
            target_id=target_id,
            request_hash=request_hash,
            status=status,
            response_json=audit_response,
        ))
        session.flush()

    def list_reviewable_batches(self, session: Session, *, actor_telegram_id: int) -> list[dict]:
        self._require_owner(session, actor_telegram_id)
        batches = session.execute(
            select(HistoricalImportBatch)
            .where(HistoricalImportBatch.status.in_(("DRY_RUN", "REVIEW_REQUIRED", "VALIDATED", "EVIDENCE_INGESTED")))
            .order_by(HistoricalImportBatch.created_at.desc())
            .limit(100)
        ).scalars().all()
        batch_ids = [batch.id for batch in batches]
        reviewed_signal_counts = {}
        if batch_ids:
            reviewed_signal_counts = dict(
                session.execute(
                    select(HistoricalSignalEvidence.batch_id, func.count(HistoricalSignal.id))
                    .join(HistoricalSignal, HistoricalSignal.evidence_id == HistoricalSignalEvidence.id)
                    .where(HistoricalSignalEvidence.batch_id.in_(batch_ids))
                    .group_by(HistoricalSignalEvidence.batch_id)
                ).all()
            )
        result = []
        for batch in batches:
            replay_signal_count = int(reviewed_signal_counts.get(batch.id, 0))
            replay_ready = batch.status == "EVIDENCE_INGESTED" and replay_signal_count > 0
            replay_block_reason = None
            if batch.status == "EVIDENCE_INGESTED" and not replay_ready:
                replay_block_reason = "HISTORICAL_REPLAY_NOT_READY"
            result.append({
                "id": batch.id,
                "ref": batch.batch_ref,
                "status": batch.status,
                "source_kind": batch.source_kind,
                "total_records": batch.total_records,
                "accepted_records": batch.accepted_records,
                "rejected_records": batch.rejected_records,
                "created_at": batch.created_at.isoformat() if batch.created_at else None,
                "owner_review": (batch.metadata_json or {}).get("owner_review"),
                "replay_ready": replay_ready,
                "replay_signal_count": replay_signal_count,
                "replay_block_reason": replay_block_reason,
            })
        return result

    def review_batch(self, session: Session, *, actor_telegram_id: int, batch_id: int, approved: bool, note: str | None, idempotency_key: str) -> dict:
        owner = self._require_owner(session, actor_telegram_id)
        request_hash = self._fingerprint(self.REVIEW, actor_telegram_id, "HISTORICAL_IMPORT_BATCH", batch_id, {"approved": approved, "note": note})
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing
        batch = HistoricalOwnerReviewService().review_batch(
            session,
            batch_id=batch_id,
            reviewer_user_id=owner.id,
            approved=approved,
            note=note,
        )
        response = {"ok": True, "batch_id": batch.id, "status": batch.status, "replayed": False}
        self._record(session, idempotency_key=idempotency_key, command_type=self.REVIEW, actor_user_id=owner.id, target_type="HISTORICAL_IMPORT_BATCH", target_id=batch_id, request_hash=request_hash, response=response)
        return response

    def ingest_evidence(self, session: Session, *, actor_telegram_id: int, batch_id: int, idempotency_key: str) -> dict:
        owner = self._require_owner(session, actor_telegram_id)
        request_hash = self._fingerprint(self.INGEST, actor_telegram_id, "HISTORICAL_IMPORT_BATCH", batch_id, {})
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing
        ingested, skipped = HistoricalEvidenceIngestionService().ingest_reviewed_batch(
            session,
            batch_id=batch_id,
            reviewer_user_id=owner.id,
        )
        response = {"ok": True, "batch_id": batch_id, "ingested": ingested, "skipped": skipped, "replayed": False}
        self._record(session, idempotency_key=idempotency_key, command_type=self.INGEST, actor_user_id=owner.id, target_type="HISTORICAL_IMPORT_BATCH", target_id=batch_id, request_hash=request_hash, response=response)
        return response

    def materialize_accepted_historical_draft(self, session: Session, *, actor_telegram_id: int, draft_id: int, idempotency_key: str) -> dict:
        """Owner-only G5 boundary; browser never supplies financial fields or replay inputs."""
        owner = self._require_owner(session, actor_telegram_id)
        request_hash = self._fingerprint(self.MATERIALIZE, actor_telegram_id, "HISTORICAL_RECOMMENDATION_DRAFT", draft_id, {})
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing
        from capitalguard.application.services.historical_signal_materialization_service import HistoricalSignalMaterializationService
        try:
            signal = HistoricalSignalMaterializationService().materialize(session, draft_id=draft_id)
        except ValueError as exc:
            raise WebCommandError(str(exc)) from exc
        response = {
            "ok": True,
            "draft_id": draft_id,
            "signal_id": signal.id,
            "public_ref": signal.public_ref,
            "status": signal.status,
            "replayed": False,
            "commercial_enabled": False,
        }
        self._record(session, idempotency_key=idempotency_key, command_type=self.MATERIALIZE, actor_user_id=owner.id, target_type="HISTORICAL_RECOMMENDATION_DRAFT", target_id=draft_id, request_hash=request_hash, response=response)
        return response

    def replay_historical_signal_from_binance(self, session: Session, *, actor_telegram_id: int, signal_id: int, start: datetime, end: datetime, interval: str, limit: int, idempotency_key: str) -> dict:
        owner = self._require_owner(session, actor_telegram_id)
        signal = session.get(HistoricalSignal, signal_id)
        if signal is None or signal.evidence is None or signal.evidence.batch is None or signal.evidence.batch.status != "EVIDENCE_INGESTED" or not signal.evidence.ownership_proof_ref:
            raise WebCommandError("Historical replay requires reviewed evidence")
        request_hash = self._fingerprint(self.REPLAY_BINANCE, actor_telegram_id, "HISTORICAL_SIGNAL", signal_id, {"start": start.isoformat(), "end": end.isoformat(), "interval": interval, "limit": limit})
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing
        from capitalguard.application.services.historical_market_replay_service import HistoricalMarketReplayService
        try:
            events = HistoricalMarketReplayService().replay_from_binance(session, signal_id=signal_id, start=start, replay_end=end, interval=interval, limit=limit)
        except Exception as exc:
            raise WebCommandError("Historical Binance replay failed without changes") from exc
        response = {"ok": True, "signal_id": signal_id, "event_count": len(events), "replayed": False, "commercial_enabled": False}
        self._record(session, idempotency_key=idempotency_key, command_type=self.REPLAY_BINANCE, actor_user_id=owner.id, target_type="HISTORICAL_SIGNAL", target_id=signal_id, request_hash=request_hash, response=response)
        return response

    def replay_reviewed_batch_from_binance(self, session: Session, *, actor_telegram_id: int, batch_id: int, idempotency_key: str) -> dict:
        """Replay all reviewed signals in one ingested batch using Core-derived windows.

        The Web client never supplies a signal identifier or timestamps.  A one-day
        1m window ending at the source decision timestamp is deliberately bounded
        by the provider's 1500-candle limit and is recorded in command audit.
        """
        owner = self._require_owner(session, actor_telegram_id)
        batch = session.get(HistoricalImportBatch, batch_id)
        if batch is None or batch.status != "EVIDENCE_INGESTED":
            raise WebCommandError("Historical replay requires evidence ingested from this batch")
        HistoricalEvidenceIngestionService().ensure_replayable_signals(session, batch_id=batch_id)
        signals = session.execute(
            select(HistoricalSignal)
            .join(HistoricalSignalEvidence, HistoricalSignal.evidence_id == HistoricalSignalEvidence.id)
            .where(HistoricalSignalEvidence.batch_id == batch_id)
            .order_by(HistoricalSignal.id)
        ).scalars().all()
        if not signals:
            raise WebCommandError("Historical batch has no reviewed signals ready for replay")
        request_hash = self._fingerprint(self.REPLAY_BINANCE, actor_telegram_id, "HISTORICAL_IMPORT_BATCH", batch_id, {"window": "SOURCE_TIMESTAMP_MINUS_24H", "interval": "1m", "limit": 1500})
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing
        from capitalguard.application.services.historical_market_replay_service import HistoricalMarketReplayService
        from capitalguard.infrastructure.market.binance_client import HistoricalMarketProviderError
        total_events = 0
        for signal in signals:
            end = signal.decision_timestamp
            start = end - timedelta(hours=24)
            try:
                total_events += len(HistoricalMarketReplayService().replay_from_binance(session, signal_id=signal.id, start=start, replay_end=end, interval="1m", limit=1500))
            except HistoricalMarketProviderError as exc:
                raise WebCommandError("Historical Binance source unavailable; replay made no changes") from exc
        response = {"ok": True, "batch_id": batch_id, "signal_ids": [signal.id for signal in signals], "event_count": total_events, "window": "SOURCE_TIMESTAMP_MINUS_24H", "commercial_enabled": False, "replayed": True}
        self._record(session, idempotency_key=idempotency_key, command_type=self.REPLAY_BINANCE, actor_user_id=owner.id, target_type="HISTORICAL_IMPORT_BATCH", target_id=batch_id, request_hash=request_hash, response=response)
        return response

    def retry_g6_historical_receipt(self, session: Session, *, actor_telegram_id: int, receipt_id: int, idempotency_key: str) -> dict:
        owner = self._require_owner(session, actor_telegram_id)
        receipt = session.get(HistoricalForwardReceipt, receipt_id)
        if receipt is None:
            raise WebCommandError("Historical receipt does not exist")
        request_hash = self._fingerprint("G6_HISTORICAL_REPLAY_RETRY", actor_telegram_id, "HISTORICAL_FORWARD_RECEIPT", receipt_id, {})
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing
        from capitalguard.application.services.historical_market_replay_service import HistoricalMarketReplayService
        try:
            result = HistoricalMarketReplayService().retry_g6(session, receipt_id=receipt_id)
        except ValueError as exc:
            raise WebCommandError(str(exc)) from exc
        run = result["run"]
        response = {"ok": result["status"] != "FAILED", "receipt_id": receipt_id, "signal_id": run.signal_id, "materialization_id": run.materialization_id, "replay_run_id": run.id, "replay_run_ref": run.run_ref, "status": result["status"], "event_count": len(result.get("events") or []), "coverage_status": run.coverage_status, "coverage_ratio": run.coverage_ratio, "actual_start": run.actual_start.isoformat() if run.actual_start else None, "actual_end": run.actual_end.isoformat() if run.actual_end else None, "quality_status": run.quality_status, "ambiguity_status": run.ambiguity_status, "commercial_enabled": False}
        self._record(session, idempotency_key=idempotency_key, command_type="G6_HISTORICAL_REPLAY_RETRY", actor_user_id=owner.id, target_type="HISTORICAL_FORWARD_RECEIPT", target_id=receipt_id, request_hash=request_hash, response=response, status="FAILED" if result["status"] == "FAILED" else "COMPLETED")
        return response

    def replay_g6_historical_signal(self, session: Session, *, actor_telegram_id: int, signal_id: int, idempotency_key: str) -> dict:
        """Execute the G6 contract from a G5 materialized signal only.

        The Web caller supplies only identity and idempotency. The Core derives the
        historical window and all market parameters from the source signal.
        """
        owner = self._require_owner(session, actor_telegram_id)
        signal = session.get(HistoricalSignal, signal_id)
        materialization = session.execute(
            select(HistoricalSignalMaterialization)
            .where(HistoricalSignalMaterialization.signal_id == signal_id)
            .order_by(HistoricalSignalMaterialization.id)
        ).scalars().first()
        if signal is None or materialization is None:
            raise WebCommandError("G6 replay requires a G5 materialized HistoricalSignal")
        if signal.decision_timestamp is None or signal.decision_timestamp.tzinfo is None:
            raise WebCommandError("G6 replay requires a timezone-aware source decision timestamp")
        from datetime import timedelta
        start = signal.decision_timestamp
        end = signal.decision_timestamp + timedelta(hours=24)
        interval = "1m"
        limit = 1500
        request_hash = self._fingerprint(
            self.G6_REPLAY,
            actor_telegram_id,
            "HISTORICAL_SIGNAL",
            signal_id,
            {
                "materialization_id": materialization.id,
                "window": "SOURCE_TIMESTAMP_PLUS_24H",
                "interval": interval,
                "limit": limit,
            },
        )
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing
        from capitalguard.application.services.historical_market_replay_service import HistoricalMarketReplayService
        try:
            result = HistoricalMarketReplayService().replay_g6(
                session,
                signal_id=signal_id,
                materialization_id=materialization.id,
                start=start,
                replay_end=end,
                interval=interval,
                limit=limit,
            )
        except ValueError as exc:
            raise WebCommandError(str(exc)) from exc
        run = result["run"]
        response = {
            "ok": result["status"] != "FAILED",
            "signal_id": signal_id,
            "materialization_id": materialization.id,
            "replay_run_id": run.id,
            "replay_run_ref": run.run_ref,
            "status": result["status"],
            "event_count": len(result["events"]),
            "window": "SOURCE_TIMESTAMP_PLUS_24H",
            "replay_version": run.replay_version,
            "ambiguity_status": run.ambiguity_status,
            "quality_status": run.quality_status,
            "failure_reason": result.get("failure_reason"),
            "commercial_enabled": False,
        }
        self._record(
            session,
            idempotency_key=idempotency_key,
            command_type=self.G6_REPLAY,
            actor_user_id=owner.id,
            target_type="HISTORICAL_SIGNAL",
            target_id=signal_id,
            request_hash=request_hash,
            response=response,
            status="FAILED" if result["status"] == "FAILED" else "COMPLETED",
        )
        return response

    def _continuum_handoff_context(
        self,
        session: Session,
        *,
        actor_telegram_id: int,
        signal_id: int,
        consent_given: bool,
        idempotency_key: str,
        command_type: str,
    ):
        owner = self._require_owner(session, actor_telegram_id)
        signal = session.get(HistoricalSignal, signal_id)
        if signal is None or signal.evidence is None or signal.evidence.batch is None:
            raise WebCommandError("Continuum handoff requires a materialized historical signal")
        evidence = signal.evidence
        batch = evidence.batch
        materialization = session.execute(
            select(HistoricalSignalMaterialization)
            .where(HistoricalSignalMaterialization.signal_id == signal_id)
            .order_by(HistoricalSignalMaterialization.id)
        ).scalars().first()
        if materialization is None:
            raise WebCommandError("Continuum handoff requires accepted signal materialization")

        latest_replay = None
        market_evidence = getattr(signal, "market_evidence", None) or []
        if market_evidence:
            latest_replay = max(
                market_evidence,
                key=lambda item: getattr(item, "created_at", None) or datetime.min,
            ).replay_run
        replay_verified = bool(
            latest_replay
            and str(getattr(latest_replay, "status", "")).upper() in {"COMPLETED", "VERIFIED"}
            and str(getattr(latest_replay, "quality_status", "")).upper() in {"VERIFIED", "ACCEPTED", "PASSED"}
            and str(getattr(latest_replay, "ambiguity_status", "NONE")).upper() in {"NONE", "RESOLVED"}
        )
        evidence_metadata = evidence.metadata_json or {}
        facts = ContinuumHandoffFacts(
            parse_complete=True,
            source_trusted=bool(evidence.ownership_proof_ref)
            and str(batch.status).upper() in {"EVIDENCE_INGESTED", "REPLAYED", "VERIFIED"},
            replay_evidence_verified=replay_verified,
            lifecycle_status=str(
                evidence_metadata.get("continuum_lifecycle_status")
                or getattr(signal, "status", "")
            ).upper(),
            # Missing duplicate proof is unsafe. Core must explicitly establish
            # that no pending/live entity exists before a future handoff command.
            duplicate_exists=bool(evidence_metadata.get("continuum_duplicate_exists", True)),
            protection_policy=evidence_metadata.get("continuum_protection_policy"),
            consent_given=bool(consent_given),
            idempotency_key=idempotency_key,
            audit_ready=True,
        )
        request_hash = self._fingerprint(
            command_type,
            actor_telegram_id,
            "HISTORICAL_SIGNAL",
            signal_id,
            {
                "materialization_id": materialization.id,
                "consent_given": bool(consent_given),
                "replay_run_id": getattr(latest_replay, "id", None),
            },
        )
        return owner, signal, evidence, materialization, facts, request_hash

    @staticmethod
    def _continuum_trade_data(signal: HistoricalSignal, evidence_metadata: dict) -> dict:
        targets = []
        for target in signal.targets or []:
            if isinstance(target, dict):
                targets.append({
                    "price": target.get("price", target.get("value")),
                    "close_percent": target.get("close_percent", target.get("percentage", 0.0)),
                })
            else:
                targets.append({"price": target, "close_percent": 0.0})
        policy = evidence_metadata.get("continuum_protection_policy") or {}
        return {
            "asset": signal.asset,
            "side": signal.side,
            "market": signal.market or "Futures",
            "entry": signal.entry,
            "stop_loss": signal.stop_loss,
            "targets": targets,
            "profit_stop_mode": policy.get("profit_stop_mode", "NONE"),
            "profit_stop_price": policy.get("profit_stop_price"),
            "profit_stop_trailing_value": policy.get("profit_stop_trailing_value"),
            "profit_stop_active": bool(policy.get("profit_stop_active", False)),
            "break_even_after_profit_pct": policy.get("break_even_after_profit_pct"),
            "break_even_buffer": policy.get("break_even_buffer", 0),
        }

    def assess_continuum_handoff(
        self,
        session: Session,
        *,
        actor_telegram_id: int,
        signal_id: int,
        consent_given: bool,
        idempotency_key: str,
    ) -> dict:
        """Persist a readiness decision only; it never creates a live entity."""
        owner, _signal, _evidence, materialization, facts, request_hash = self._continuum_handoff_context(
            session,
            actor_telegram_id=actor_telegram_id,
            signal_id=signal_id,
            consent_given=consent_given,
            idempotency_key=idempotency_key,
            command_type=self.CONTINUUM_HANDOFF_DECISION,
        )
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing
        decision = ContinuumHandoffGate().evaluate(facts)
        response = {
            "ok": True,
            "signal_id": signal_id,
            "materialization_id": materialization.id,
            "status": decision.status.value,
            "approved": decision.approved,
            "reason_codes": list(decision.reason_codes),
            "execution_allowed": False,
            "next_step": "EXPLICIT_PENDING_TRACKING_COMMAND_REQUIRED" if decision.approved else "REMEDIATE_BLOCK_REASONS",
            "replayed": False,
        }
        self._record(
            session,
            idempotency_key=idempotency_key,
            command_type=self.CONTINUUM_HANDOFF_DECISION,
            actor_user_id=owner.id,
            target_type="HISTORICAL_SIGNAL",
            target_id=signal_id,
            request_hash=request_hash,
            response=response,
            status=decision.status.value,
        )
        return response

    async def execute_continuum_handoff(
        self,
        session: Session,
        *,
        actor_telegram_id: int,
        signal_id: int,
        consent_given: bool,
        idempotency_key: str,
        creation_service,
    ) -> dict:
        """Create pending tracking only after a fresh, explicit gate approval.

        This reuses CreationService's existing forwarding path. It deliberately
        requests PENDING_ACTIVATION, never ACTIVATED, and never subscribes to a
        market stream directly.
        """
        owner, signal, evidence, materialization, facts, request_hash = self._continuum_handoff_context(
            session,
            actor_telegram_id=actor_telegram_id,
            signal_id=signal_id,
            consent_given=consent_given,
            idempotency_key=idempotency_key,
            command_type=self.CONTINUUM_HANDOFF_EXECUTE,
        )
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing
        decision = ContinuumHandoffGate().evaluate(facts)
        if not decision.approved:
            response = {
                "ok": True,
                "signal_id": signal_id,
                "materialization_id": materialization.id,
                "status": decision.status.value,
                "approved": False,
                "reason_codes": list(decision.reason_codes),
                "execution_allowed": False,
                "replayed": False,
            }
            self._record(
                session,
                idempotency_key=idempotency_key,
                command_type=self.CONTINUUM_HANDOFF_EXECUTE,
                actor_user_id=owner.id,
                target_type="HISTORICAL_SIGNAL",
                target_id=signal_id,
                request_hash=request_hash,
                response=response,
                status=decision.status.value,
            )
            return response
        if creation_service is None:
            raise WebCommandError("Continuum pending tracking service unavailable")

        evidence_metadata = evidence.metadata_json or {}
        channel_info = None
        if evidence.telegram_channel_id:
            channel_info = {
                "id": evidence.telegram_channel_id,
                "title": evidence_metadata.get("source_title"),
            }
        result = await creation_service.create_trade_from_forwarding_async(
            user_id=str(actor_telegram_id),
            trade_data=self._continuum_trade_data(signal, evidence_metadata),
            original_text=evidence.raw_text,
            db_session=session,
            status_to_set="PENDING_ACTIVATION",
            original_published_at=evidence.message_timestamp,
            channel_info=channel_info,
            source_type="CONTINUUM_HANDOFF",
        )
        if not result.get("success"):
            raise WebCommandError(result.get("error") or "Continuum pending tracking failed")
        evidence.metadata_json = {
            **evidence_metadata,
            "continuum_duplicate_exists": True,
            "continuum_handoff_trade_id": result.get("trade_id"),
            "continuum_handoff_status": "PENDING_ACTIVATION",
        }
        session.flush()
        response = {
            "ok": True,
            "signal_id": signal_id,
            "materialization_id": materialization.id,
            "trade_id": result.get("trade_id"),
            "public_ref": result.get("public_ref"),
            "status": "PENDING_ACTIVATION",
            "approved": True,
            "execution_allowed": False,
            "live_activation": False,
            "replayed": False,
        }
        self._record(
            session,
            idempotency_key=idempotency_key,
            command_type=self.CONTINUUM_HANDOFF_EXECUTE,
            actor_user_id=owner.id,
            target_type="HISTORICAL_SIGNAL",
            target_id=signal_id,
            request_hash=request_hash,
            response=response,
        )
        return response

    async def activate_continuum_user_trade(
        self,
        session: Session,
        *,
        actor_telegram_id: int,
        public_ref: str,
        idempotency_key: str,
        lifecycle_service,
        price_service,
    ) -> dict:
        """Explicitly activate a Continuum pending trade at a Core price only."""
        actor = UserRepository(session).find_by_telegram_id(actor_telegram_id)
        if actor is None:
            raise WebCommandError("Trader identity does not exist in Core")
        normalized_ref = public_ref.strip()
        if not normalized_ref:
            raise WebCommandError("Continuum UserTrade public reference is required")
        trade = session.query(UserTrade).filter(
            UserTrade.user_id == actor.id,
            UserTrade.public_ref == normalized_ref,
        ).with_for_update().first()
        if trade is None:
            raise WebCommandError("Continuum UserTrade was not found")
        request_hash = self._fingerprint(
            self.CONTINUUM_ACTIVATE_USER_TRADE,
            actor_telegram_id,
            "USER_TRADE",
            trade.id,
            {"public_ref": normalized_ref, "source_type": "CONTINUUM_HANDOFF"},
        )
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing
        if str(getattr(trade, "source_type", "")).upper() != "CONTINUUM_HANDOFF":
            raise WebCommandError("Only a Continuum pending UserTrade can be activated by this command")
        if trade.status == UserTradeStatusEnum.ACTIVATED:
            raise WebCommandError("Continuum UserTrade is already activated")
        if trade.status in (UserTradeStatusEnum.CLOSED, UserTradeStatusEnum.CANCELLED):
            raise WebCommandError("Continuum UserTrade is terminal")
        if trade.status not in (UserTradeStatusEnum.PENDING_ACTIVATION, UserTradeStatusEnum.WATCHLIST):
            raise WebCommandError("Continuum UserTrade is not pending activation")
        if lifecycle_service is None or price_service is None:
            raise WebCommandError("Continuum activation services unavailable")
        live_price = await price_service.get_cached_price(trade.asset, "Futures", True)
        if not isinstance(live_price, (int, float)) or live_price <= 0:
            raise WebCommandError("Trusted market price is unavailable; UserTrade was not changed")
        try:
            activated_trade = await lifecycle_service.activate_pending_user_trade_async(
                str(actor_telegram_id),
                trade.id,
                Decimal(str(live_price)),
                session,
            )
        except ValueError as exc:
            raise WebCommandError(str(exc)) from exc
        if activated_trade is None:
            raise WebCommandError("Continuum UserTrade activation failed")
        response = {
            "ok": True,
            "entity_type": "USER_TRADE",
            "public_ref": activated_trade.public_ref,
            "status": getattr(activated_trade.status, "value", str(activated_trade.status)),
            "activation_price": float(live_price),
            "live_activation": True,
            "replayed": False,
        }
        self._record(
            session,
            idempotency_key=idempotency_key,
            command_type=self.CONTINUUM_ACTIVATE_USER_TRADE,
            actor_user_id=actor.id,
            target_type="USER_TRADE",
            target_id=trade.id,
            request_hash=request_hash,
            response=response,
        )
        return response

    async def close_user_trade(
        self,
        session: Session,
        *,
        actor_telegram_id: int,
        public_ref: str,
        idempotency_key: str,
        lifecycle_service,
        price_service,
    ) -> dict:
        """Close only the caller-owned UserTrade using a Core-derived market price.

        Numeric identifiers never cross the command boundary.  The row is locked
        and scoped by owner before lifecycle invocation; the completed outcome is
        retained in WebCommandAudit for deterministic retries.
        """
        actor = UserRepository(session).find_by_telegram_id(actor_telegram_id)
        if actor is None:
            raise WebCommandError("Trader identity does not exist in Core")
        normalized_ref = public_ref.strip()
        if not normalized_ref:
            raise WebCommandError("UserTrade public reference is required")
        trade = session.query(UserTrade).filter(
            UserTrade.user_id == actor.id,
            UserTrade.public_ref == normalized_ref,
        ).with_for_update().first()
        if trade is None:
            raise WebCommandError("UserTrade was not found")
        request_hash = self._fingerprint(
            self.CLOSE_USER_TRADE,
            actor_telegram_id,
            "USER_TRADE",
            trade.id,
            {"public_ref": normalized_ref},
        )
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing
        if trade.status == UserTradeStatusEnum.CLOSED:
            raise WebCommandError("UserTrade is already closed")
        if trade.status == UserTradeStatusEnum.CANCELLED:
            raise WebCommandError("UserTrade is already cancelled")
        if trade.status != UserTradeStatusEnum.ACTIVATED:
            raise WebCommandError("Pending UserTrade must be cancelled without a market price")
        live_price = await price_service.get_cached_price(trade.asset, "Futures", True)
        if not isinstance(live_price, (int, float)) or live_price <= 0:
            raise WebCommandError("Trusted market price is unavailable; UserTrade was not changed")
        closed_trade = await lifecycle_service.close_user_trade_async(
            str(actor_telegram_id),
            trade.id,
            Decimal(str(live_price)),
            session,
        )
        response = {
            "ok": True,
            "entity_type": "USER_TRADE",
            "public_ref": closed_trade.public_ref,
            "status": getattr(closed_trade.status, "value", str(closed_trade.status)),
            "close_price": float(closed_trade.close_price),
            "replayed": False,
        }
        self._record(
            session,
            idempotency_key=idempotency_key,
            command_type=self.CLOSE_USER_TRADE,
            actor_user_id=actor.id,
            target_type="USER_TRADE",
            target_id=trade.id,
            request_hash=request_hash,
            response=response,
        )
        return response

    async def cancel_pending_user_trade(
        self,
        session: Session,
        *,
        actor_telegram_id: int,
        public_ref: str,
        idempotency_key: str,
        lifecycle_service,
    ) -> dict:
        actor = UserRepository(session).find_by_telegram_id(actor_telegram_id)
        if actor is None:
            raise WebCommandError("Trader identity does not exist in Core")
        normalized_ref = public_ref.strip()
        if not normalized_ref:
            raise WebCommandError("UserTrade public reference is required")
        trade = session.query(UserTrade).filter(
            UserTrade.user_id == actor.id,
            UserTrade.public_ref == normalized_ref,
        ).with_for_update().first()
        if trade is None:
            raise WebCommandError("UserTrade was not found")
        request_hash = self._fingerprint(
            self.CANCEL_USER_TRADE,
            actor_telegram_id,
            "USER_TRADE",
            trade.id,
            {"public_ref": normalized_ref},
        )
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing
        if trade.status == UserTradeStatusEnum.CANCELLED:
            raise WebCommandError("UserTrade is already cancelled")
        if trade.status not in (UserTradeStatusEnum.WATCHLIST, UserTradeStatusEnum.PENDING_ACTIVATION):
            raise WebCommandError("Only a pending UserTrade can be cancelled")
        cancelled_trade = await lifecycle_service.cancel_pending_user_trade_async(
            str(actor_telegram_id), trade.id, session,
        )
        response = {
            "ok": True,
            "entity_type": "USER_TRADE",
            "public_ref": cancelled_trade.public_ref,
            "status": getattr(cancelled_trade.status, "value", str(cancelled_trade.status)),
            "close_price": None,
            "pnl_percentage": None,
            "replayed": False,
        }
        self._record(
            session,
            idempotency_key=idempotency_key,
            command_type=self.CANCEL_USER_TRADE,
            actor_user_id=actor.id,
            target_type="USER_TRADE",
            target_id=trade.id,
            request_hash=request_hash,
            response=response,
        )
        return response

    async def partial_close_user_trade(
        self,
        session: Session,
        *,
        actor_telegram_id: int,
        public_ref: str,
        close_percent: Decimal,
        idempotency_key: str,
        lifecycle_service,
        price_service,
    ) -> dict:
        """Partially close an owned activated UserTrade using a Core market price only."""
        actor = UserRepository(session).find_by_telegram_id(actor_telegram_id)
        if actor is None:
            raise WebCommandError("Trader identity does not exist in Core")
        normalized_ref = public_ref.strip()
        if not normalized_ref:
            raise WebCommandError("UserTrade public reference is required")
        requested_percent = Decimal(str(close_percent))
        if not requested_percent.is_finite() or requested_percent <= Decimal("0"):
            raise WebCommandError("Partial close percentage must be a positive finite value")
        trade = session.query(UserTrade).filter(
            UserTrade.user_id == actor.id,
            UserTrade.public_ref == normalized_ref,
        ).with_for_update().first()
        if trade is None:
            raise WebCommandError("UserTrade was not found")
        request_hash = self._fingerprint(
            self.PARTIAL_CLOSE_USER_TRADE,
            actor_telegram_id,
            "USER_TRADE",
            trade.id,
            {"public_ref": normalized_ref, "close_percent": str(requested_percent)},
        )
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing
        if trade.status == UserTradeStatusEnum.CLOSED:
            raise WebCommandError("UserTrade is already closed")
        if trade.status == UserTradeStatusEnum.CANCELLED:
            raise WebCommandError("UserTrade is already cancelled")
        if trade.status != UserTradeStatusEnum.ACTIVATED:
            raise WebCommandError("Only an activated UserTrade can be partially closed at a market price")
        remaining_percent = Decimal(str(trade.open_size_percent))
        if requested_percent >= remaining_percent:
            raise WebCommandError("Partial close percentage must be less than the remaining open size; use full close")
        live_price = await price_service.get_cached_price(trade.asset, "Futures", True)
        if not isinstance(live_price, (int, float)) or live_price <= 0:
            raise WebCommandError("Trusted market price is unavailable; UserTrade was not changed")
        updated_trade = await lifecycle_service.partial_close_user_trade_async(
            str(actor_telegram_id),
            trade.id,
            requested_percent,
            Decimal(str(live_price)),
            session,
        )
        response = {
            "ok": True,
            "entity_type": "USER_TRADE",
            "public_ref": updated_trade.public_ref,
            "status": getattr(updated_trade.status, "value", str(updated_trade.status)),
            "closed_percent": float(requested_percent),
            "remaining_open_size_percent": float(updated_trade.open_size_percent),
            "partial_close_price": float(live_price),
            "replayed": False,
        }
        self._record(
            session,
            idempotency_key=idempotency_key,
            command_type=self.PARTIAL_CLOSE_USER_TRADE,
            actor_user_id=actor.id,
            target_type="USER_TRADE",
            target_id=trade.id,
            request_hash=request_hash,
            response=response,
        )
        return response

    async def move_user_trade_stop_to_breakeven(
        self,
        session: Session,
        *,
        actor_telegram_id: int,
        public_ref: str,
        idempotency_key: str,
        lifecycle_service,
    ) -> dict:
        """Move a caller-owned activated UserTrade stop to its Core-held entry only."""
        actor = UserRepository(session).find_by_telegram_id(actor_telegram_id)
        if actor is None:
            raise WebCommandError("Trader identity does not exist in Core")
        normalized_ref = public_ref.strip()
        if not normalized_ref:
            raise WebCommandError("UserTrade public reference is required")
        trade = session.query(UserTrade).filter(
            UserTrade.user_id == actor.id,
            UserTrade.public_ref == normalized_ref,
        ).with_for_update().first()
        if trade is None:
            raise WebCommandError("UserTrade was not found")
        request_hash = self._fingerprint(
            self.MOVE_USER_TRADE_STOP_TO_BREAKEVEN,
            actor_telegram_id,
            "USER_TRADE",
            trade.id,
            {"public_ref": normalized_ref},
        )
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing
        if trade.status == UserTradeStatusEnum.CLOSED:
            raise WebCommandError("UserTrade is already closed")
        if trade.status == UserTradeStatusEnum.CANCELLED:
            raise WebCommandError("UserTrade is already cancelled")
        if trade.status != UserTradeStatusEnum.ACTIVATED:
            raise WebCommandError("Only an activated UserTrade can move its stop to breakeven")
        updated_trade = await lifecycle_service.move_user_trade_stop_to_breakeven_async(
            str(actor_telegram_id), trade.id, session,
        )
        response = {
            "ok": True,
            "entity_type": "USER_TRADE",
            "public_ref": updated_trade.public_ref,
            "status": getattr(updated_trade.status, "value", str(updated_trade.status)),
            "stop_loss": float(updated_trade.stop_loss),
            "replayed": False,
        }
        self._record(
            session,
            idempotency_key=idempotency_key,
            command_type=self.MOVE_USER_TRADE_STOP_TO_BREAKEVEN,
            actor_user_id=actor.id,
            target_type="USER_TRADE",
            target_id=trade.id,
            request_hash=request_hash,
            response=response,
        )
        return response

    async def update_pending_user_trade_entry(
        self,
        session: Session,
        *,
        actor_telegram_id: int,
        public_ref: str,
        entry: Decimal,
        idempotency_key: str,
        lifecycle_service,
    ) -> dict:
        actor = UserRepository(session).find_by_telegram_id(actor_telegram_id)
        if actor is None:
            raise WebCommandError("Trader identity does not exist in Core")
        normalized_ref = public_ref.strip()
        requested_entry = Decimal(str(entry))
        if not normalized_ref:
            raise WebCommandError("UserTrade public reference is required")
        if not requested_entry.is_finite() or requested_entry <= Decimal("0"):
            raise WebCommandError("UserTrade entry must be a positive finite value")
        trade = session.query(UserTrade).filter(
            UserTrade.user_id == actor.id,
            UserTrade.public_ref == normalized_ref,
        ).with_for_update().first()
        if trade is None:
            raise WebCommandError("UserTrade was not found")
        request_hash = self._fingerprint(
            self.UPDATE_PENDING_USER_TRADE_ENTRY,
            actor_telegram_id,
            "USER_TRADE",
            trade.id,
            {"public_ref": normalized_ref, "entry": format(requested_entry.normalize(), "f")},
        )
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing
        if trade.status not in (UserTradeStatusEnum.WATCHLIST, UserTradeStatusEnum.PENDING_ACTIVATION):
            raise WebCommandError("Only a non-activated UserTrade entry can be amended")
        updated_trade = await lifecycle_service.update_pending_user_trade_entry_async(
            str(actor_telegram_id), trade.id, requested_entry, session,
        )
        response = {
            "ok": True,
            "entity_type": "USER_TRADE",
            "public_ref": updated_trade.public_ref,
            "status": getattr(updated_trade.status, "value", str(updated_trade.status)),
            "entry": float(updated_trade.entry),
            "replayed": False,
        }
        self._record(
            session,
            idempotency_key=idempotency_key,
            command_type=self.UPDATE_PENDING_USER_TRADE_ENTRY,
            actor_user_id=actor.id,
            target_type="USER_TRADE",
            target_id=trade.id,
            request_hash=request_hash,
            response=response,
        )
        return response

    async def confirm_analyst_recommendation(
        self,
        session: Session,
        *,
        actor_telegram_id: int,
        idempotency_key: str,
        creation_service,
        recommendation: dict,
    ) -> dict:
        """Persist a reviewed analyst recommendation exactly once through Core."""
        actor = UserRepository(session).find_by_telegram_id(actor_telegram_id)
        if (
            actor is None
            or not bool(getattr(actor, "is_active", False))
            or getattr(getattr(actor, "user_type", None), "value", "") != "ANALYST"
        ):
            raise WebCommandError("Analyst authorization required")

        normalized_channels = sorted({int(channel_id) for channel_id in recommendation.get("target_channel_ids", set())})
        fingerprint_payload = {
            "asset": str(recommendation["asset"]).strip().upper(),
            "side": str(recommendation["side"]).upper(),
            "market": str(recommendation.get("market", "Futures")),
            "order_type": str(recommendation["order_type"]).upper(),
            "entry": str(recommendation["entry"]),
            "stop_loss": str(recommendation["stop_loss"]),
            "targets": [
                {"price": str(target["price"]), "close_percent": target.get("close_percent", 0.0)}
                for target in recommendation["targets"]
            ],
            "notes": recommendation.get("notes"),
            "target_channel_ids": normalized_channels,
        }
        request_hash = self._fingerprint(
            self.CREATE_ANALYST_RECOMMENDATION,
            actor_telegram_id,
            "RECOMMENDATION_CREATE",
            0,
            fingerprint_payload,
        )
        existing = self._replay_or_reject(session, idempotency_key=idempotency_key, request_hash=request_hash)
        if existing is not None:
            return existing

        canonical = {
            "asset": fingerprint_payload["asset"],
            "direction": fingerprint_payload["side"],
            "market": fingerprint_payload["market"],
            "entry": fingerprint_payload["entry"],
            "stop_loss": fingerprint_payload["stop_loss"],
            "targets": [item["price"] for item in fingerprint_payload["targets"]],
        }
        decision = OperationalDecisionService().prepare_recommendation(
            canonical,
            actor_ref=str(actor.telegram_user_id),
            command_id=idempotency_key,
            evidence={
                "source_ref": f"web:analyst-recommendation:{idempotency_key}",
                "correlation_id": idempotency_key,
                "causation_id": str(actor.id),
            },
        )
        admission = OperationalAdmissionService().admit_recommendation(
            decision,
            actor_ref=str(actor.telegram_user_id),
            command_id=idempotency_key,
        )
        OperationalAdmissionService.validate_admission_payload(admission.payload)

        rec, report = await creation_service.create_and_publish_recommendation_async(
            user_id=str(actor_telegram_id),
            db_session=session,
            **recommendation,
        )
        if rec is None:
            raise WebCommandError("Recommendation was not created")
        response = {
            "ok": True,
            "entity_type": "RECOMMENDATION",
            "public_ref": rec.public_ref,
            "publication": {
                "state": "QUEUED" if normalized_channels else "SAVED",
                "queued_delivery_count": len(normalized_channels),
            },
            "decision": {
                "status": decision.status,
                "target": decision.target.value,
                "decision_fingerprint": decision.decision_fingerprint,
                "trace_id": decision.trace.trace_id,
                "admission_status": admission.status.value,
            },
            "replayed": False,
        }
        self._record(
            session,
            idempotency_key=idempotency_key,
            command_type=self.CREATE_ANALYST_RECOMMENDATION,
            actor_user_id=actor.id,
            target_type="RECOMMENDATION",
            target_id=rec.id,
            request_hash=request_hash,
            response=response,
        )
        return response
