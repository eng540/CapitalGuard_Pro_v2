from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.application.services.historical_evidence_ingestion_service import HistoricalEvidenceIngestionService
from capitalguard.application.services.historical_owner_review_service import HistoricalOwnerReviewService
from capitalguard.config import settings
from capitalguard.infrastructure.db.models import HistoricalImportBatch, HistoricalSignal, UserTrade, UserTradeStatusEnum, WebCommandAudit
from capitalguard.infrastructure.db.repository import UserRepository


class WebCommandError(ValueError):
    """Raised when a privileged Web command violates the Core command boundary."""


class WebCommandService:
    REVIEW = "HISTORICAL_OWNER_REVIEW"
    INGEST = "HISTORICAL_EVIDENCE_INGEST"
    REPLAY_BINANCE = "HISTORICAL_BINANCE_REPLAY"
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
    def _fingerprint(command_type: str, actor_telegram_id: int, target_type: str, target_id: int, payload: dict) -> str:
        raw = json.dumps({"command_type": command_type, "actor": actor_telegram_id, "target_type": target_type, "target_id": target_id, "payload": payload}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def _replay_or_reject(session: Session, *, idempotency_key: str, request_hash: str) -> dict | None:
        existing = session.execute(select(WebCommandAudit).where(WebCommandAudit.idempotency_key == idempotency_key)).scalar_one_or_none()
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise WebCommandError("Idempotency key cannot be reused with a different command")
        return dict(existing.response_json or {})

    @staticmethod
    def _record(session: Session, *, idempotency_key: str, command_type: str, actor_user_id: int, target_type: str, target_id: int, request_hash: str, response: dict) -> None:
        session.add(WebCommandAudit(
            idempotency_key=idempotency_key,
            command_type=command_type,
            actor_user_id=actor_user_id,
            target_type=target_type,
            target_id=target_id,
            request_hash=request_hash,
            status="COMPLETED",
            response_json=response,
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
        return [
            {
                "id": batch.id,
                "ref": batch.batch_ref,
                "status": batch.status,
                "source_kind": batch.source_kind,
                "total_records": batch.total_records,
                "accepted_records": batch.accepted_records,
                "rejected_records": batch.rejected_records,
                "created_at": batch.created_at.isoformat() if batch.created_at else None,
                "owner_review": (batch.metadata_json or {}).get("owner_review"),
            }
            for batch in batches
        ]

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
        if actor is None or getattr(getattr(actor, "user_type", None), "value", "") != "ANALYST":
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
