import asyncio
import inspect
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import (
    PublicationDelivery,
    PublicationDeliveryOperation,
    PublicationDeliveryStatus,
    PublishedMessage,
    RecommendationChannelRef,
)
from capitalguard.application.services.identity_service import IdentityService
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.observability.metrics import (
    OUTBOX_ATTEMPTS_TOTAL,
    OUTBOX_DELIVERIES_TOTAL,
    OUTBOX_QUEUE_SIZE,
)

logger = logging.getLogger(__name__)


class PublicationOutboxService:
    """Durable Telegram publication ledger and retry processor."""

    def __init__(self, repo: Any, notifier: Any, max_attempts: int = 5):
        self.repo = repo
        self.notifier = notifier
        self.max_attempts = max_attempts
        self._stop_event = asyncio.Event()
        self._worker_task: Optional[asyncio.Task] = None

    @staticmethod
    def _key(recommendation_id: int, channel_id: int, operation: str, event_key: str) -> str:
        return (
            f"recommendation:{recommendation_id}:channel:{channel_id}:"
            f"operation:{operation}:event:{event_key}"
        )

    def _ensure_channel_refs(
        self,
        session: Session,
        recommendation_id: int,
        channel_ids: Iterable[int],
    ) -> None:
        """Create one stable local recommendation reference per canonical channel."""
        for channel_id in sorted({int(value) for value in channel_ids}):
            catalog = IdentityService.ensure_channel_catalog(session, channel_id)
            ref = session.query(RecommendationChannelRef).filter_by(
                recommendation_id=recommendation_id,
                channel_catalog_id=catalog.id,
            ).one_or_none()
            if ref is None:
                ref = RecommendationChannelRef(
                    recommendation_id=recommendation_id,
                    channel_catalog_id=catalog.id,
                    channel_sequence=IdentityService.channel_recommendation_sequence(
                        session, catalog.id
                    ),
                )
                session.add(ref)
                session.flush()

    def enqueue_operation(
        self,
        session: Session,
        recommendation_id: int,
        channel_ids: Iterable[int],
        operation: str,
        event_key: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> List[PublicationDelivery]:
        normalized_channel_ids = sorted({int(value) for value in channel_ids})
        self._ensure_channel_refs(session, recommendation_id, normalized_channel_ids)
        deliveries: List[PublicationDelivery] = []
        for channel_id in normalized_channel_ids:
            key = self._key(recommendation_id, channel_id, operation, event_key)
            delivery = session.query(PublicationDelivery).filter_by(idempotency_key=key).one_or_none()
            if delivery is None:
                delivery = PublicationDelivery(
                    recommendation_id=recommendation_id,
                    telegram_channel_id=channel_id,
                    operation=operation,
                    status=PublicationDeliveryStatus.PENDING.value,
                    idempotency_key=key,
                    payload_json=payload or {},
                )
                session.add(delivery)
                session.flush()
            deliveries.append(delivery)
        return deliveries

    def enqueue_create_deliveries(
        self,
        session: Session,
        recommendation_id: int,
        channel_ids: Iterable[int],
    ) -> List[PublicationDelivery]:
        return self.enqueue_operation(
            session,
            recommendation_id,
            channel_ids,
            PublicationDeliveryOperation.CREATE.value,
            event_key="initial",
        )

    async def publish_for_recommendation(
        self,
        session: Session,
        recommendation_entity: Any,
        channel_ids: Iterable[int],
    ) -> Dict[str, List[Dict[str, Any]]]:
        deliveries = self.enqueue_create_deliveries(session, recommendation_entity.id, channel_ids)
        session.commit()
        report: Dict[str, List[Dict[str, Any]]] = {"success": [], "failed": [], "skipped": []}
        for delivery in deliveries:
            if delivery.status == PublicationDeliveryStatus.SENT.value:
                report["skipped"].append({"channel_id": delivery.telegram_channel_id, "reason": "Already sent"})
                continue
            result = await self._deliver_one(delivery.id, recommendation_entity)
            report[result["bucket"]].append(result["item"])
        return report

    async def _deliver_one(self, delivery_id: int, recommendation_entity: Any) -> Dict[str, Any]:
        with session_scope() as session:
            delivery = session.query(PublicationDelivery).filter_by(id=delivery_id).with_for_update().first()
            if not delivery:
                return {"bucket": "failed", "item": {"delivery_id": delivery_id, "error": "Delivery not found"}}
            if delivery.status == PublicationDeliveryStatus.SENT.value:
                OUTBOX_DELIVERIES_TOTAL.labels(operation=delivery.operation, status="SKIPPED").inc()
                return {"bucket": "skipped", "item": {"channel_id": delivery.telegram_channel_id, "reason": "Already sent"}}
            delivery.status = PublicationDeliveryStatus.PROCESSING.value
            delivery.attempts = int(delivery.attempts or 0) + 1
            session.commit()
            recommendation_id = delivery.recommendation_id
            channel_id = delivery.telegram_channel_id
            operation = delivery.operation
            payload = delivery.payload_json or {}
            attempt = delivery.attempts
            OUTBOX_ATTEMPTS_TOTAL.labels(operation=operation).inc()
            logger.info(
                "PublicationOutbox delivery processing delivery_id=%s recommendation_id=%s "
                "channel_id=%s operation=%s attempt=%s",
                delivery_id,
                recommendation_id,
                channel_id,
                operation,
                attempt,
            )

        try:
            with session_scope() as session:
                existing = session.query(PublishedMessage).filter_by(
                    recommendation_id=recommendation_id,
                    telegram_channel_id=channel_id,
                ).one_or_none()
                target_message_id = existing.telegram_message_id if existing else None

            if operation == PublicationDeliveryOperation.CREATE.value:
                from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard

                keyboard = public_channel_keyboard(
                    recommendation_entity.id,
                    getattr(self.notifier, "bot_username", None),
                )
                fn = self.notifier.post_to_channel
                if inspect.iscoroutinefunction(fn):
                    result = await fn(channel_id, recommendation_entity, keyboard)
                else:
                    result = await asyncio.get_running_loop().run_in_executor(
                        None,
                        lambda: fn(channel_id, recommendation_entity, keyboard),
                    )
                if not isinstance(result, tuple) or len(result) != 2:
                    raise RuntimeError("Telegram notifier returned no message identifier")
                target_message_id = result[1]
            elif operation == PublicationDeliveryOperation.UPDATE.value:
                if target_message_id is None:
                    raise RuntimeError("Cannot update a channel without a published message")
                fn = self.notifier.edit_recommendation_card_by_ids
                args = (
                    channel_id,
                    target_message_id,
                    recommendation_entity,
                    getattr(self.notifier, "bot_username", "CapitalGuardBot"),
                )
                if inspect.iscoroutinefunction(fn):
                    await fn(*args)
                else:
                    await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*args))
            elif operation in {
                PublicationDeliveryOperation.REPLY.value,
                PublicationDeliveryOperation.CLOSE.value,
            }:
                if target_message_id is None:
                    raise RuntimeError("Cannot reply to a channel without a published message")
                text = str(payload.get("text") or "")
                if not text:
                    raise RuntimeError("Outbox reply payload is empty")
                fn = self.notifier.post_notification_reply
                args = (channel_id, target_message_id, text)
                if inspect.iscoroutinefunction(fn):
                    await fn(*args)
                else:
                    await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*args))
            else:
                raise RuntimeError(f"Unsupported publication operation: {operation}")

            with session_scope() as session:
                delivery = session.query(PublicationDelivery).filter_by(id=delivery_id).with_for_update().one()
                existing = session.query(PublishedMessage).filter_by(
                    recommendation_id=recommendation_id,
                    telegram_channel_id=channel_id,
                ).one_or_none()
                if operation == PublicationDeliveryOperation.CREATE.value and existing is None:
                    session.add(PublishedMessage(
                        recommendation_id=recommendation_id,
                        telegram_channel_id=channel_id,
                        telegram_message_id=target_message_id,
                    ))
                delivery.status = PublicationDeliveryStatus.SENT.value
                delivery.telegram_message_id = target_message_id
                delivery.sent_at = datetime.now(timezone.utc)
                delivery.last_error = None
                session.commit()
            OUTBOX_DELIVERIES_TOTAL.labels(operation=operation, status="SENT").inc()
            logger.info(
                "PublicationOutbox delivery sent delivery_id=%s recommendation_id=%s "
                "channel_id=%s operation=%s message_id=%s attempt=%s",
                delivery_id,
                recommendation_id,
                channel_id,
                operation,
                target_message_id,
                attempt,
            )
            return {"bucket": "success", "item": {"channel_id": channel_id, "attempt": attempt}}
        except Exception as exc:
            next_status = (
                PublicationDeliveryStatus.FAILED.value
                if attempt >= self.max_attempts
                else PublicationDeliveryStatus.RETRY.value
            )
            with session_scope() as session:
                delivery = session.query(PublicationDelivery).filter_by(id=delivery_id).with_for_update().one_or_none()
                if delivery:
                    delivery.status = next_status
                    delivery.last_error = str(exc)[:4000]
                    delivery.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                        seconds=min(300, 2 ** max(0, attempt - 1))
                    )
                    session.commit()
            OUTBOX_DELIVERIES_TOTAL.labels(operation=operation, status=next_status).inc()
            logger.warning(
                "PublicationOutbox delivery %s delivery_id=%s recommendation_id=%s "
                "channel_id=%s operation=%s attempt=%s error=%s",
                next_status,
                delivery_id,
                recommendation_id,
                channel_id,
                operation,
                attempt,
                str(exc),
            )
            return {"bucket": "failed", "item": {"channel_id": channel_id, "attempt": attempt, "error": str(exc)}}

    async def process_due_once(self, limit: int = 25) -> int:
        with session_scope() as session:
            now = datetime.now(timezone.utc)
            deliveries = session.query(PublicationDelivery).filter(
                PublicationDelivery.status.in_([
                    PublicationDeliveryStatus.PENDING.value,
                    PublicationDeliveryStatus.RETRY.value,
                ]),
                PublicationDelivery.next_attempt_at <= now,
            ).order_by(PublicationDelivery.next_attempt_at.asc()).limit(limit).all()
            work = [(delivery.id, delivery.recommendation_id) for delivery in deliveries]
            OUTBOX_QUEUE_SIZE.set(len(work))
        processed = 0
        for delivery_id, recommendation_id in work:
            with session_scope() as session:
                rec = self.repo.get(session, recommendation_id)
                entity = self.repo._to_entity(rec) if rec else None
            if entity:
                await self._deliver_one(delivery_id, entity)
                processed += 1
        return processed

    async def run_worker(self, interval_seconds: float = 15.0) -> None:
        self._stop_event.clear()
        while not self._stop_event.is_set():
            try:
                await self.process_due_once()
            except Exception:
                logger.exception("Publication outbox worker iteration failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def start(self, interval_seconds: float = 15.0) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self.run_worker(interval_seconds))
            logger.info("PublicationOutbox worker started interval_seconds=%s", interval_seconds)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._worker_task:
            await self._worker_task
            self._worker_task = None
            logger.info("PublicationOutbox worker stopped")
