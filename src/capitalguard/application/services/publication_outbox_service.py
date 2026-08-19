from __future__ import annotations

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
)
from capitalguard.infrastructure.db.uow import session_scope

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
    def _key(recommendation_id: int, channel_id: int, operation: str) -> str:
        return f"recommendation:{recommendation_id}:channel:{channel_id}:operation:{operation}"

    def enqueue_create_deliveries(
        self,
        session: Session,
        recommendation_id: int,
        channel_ids: Iterable[int],
    ) -> List[PublicationDelivery]:
        deliveries: List[PublicationDelivery] = []
        for channel_id in sorted({int(value) for value in channel_ids}):
            key = self._key(recommendation_id, channel_id, PublicationDeliveryOperation.CREATE.value)
            delivery = session.query(PublicationDelivery).filter_by(idempotency_key=key).one_or_none()
            if delivery is None:
                delivery = PublicationDelivery(
                    recommendation_id=recommendation_id,
                    telegram_channel_id=channel_id,
                    operation=PublicationDeliveryOperation.CREATE.value,
                    status=PublicationDeliveryStatus.PENDING.value,
                    idempotency_key=key,
                )
                session.add(delivery)
                session.flush()
            deliveries.append(delivery)
        return deliveries

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
                return {"bucket": "skipped", "item": {"channel_id": delivery.telegram_channel_id, "reason": "Already sent"}}
            delivery.status = PublicationDeliveryStatus.PROCESSING.value
            delivery.attempts = int(delivery.attempts or 0) + 1
            session.commit()
            channel_id = delivery.telegram_channel_id
            attempt = delivery.attempts

        try:
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

            with session_scope() as session:
                delivery = session.query(PublicationDelivery).filter_by(id=delivery_id).with_for_update().one()
                existing = session.query(PublishedMessage).filter_by(
                    recommendation_id=delivery.recommendation_id,
                    telegram_channel_id=channel_id,
                ).one_or_none()
                if existing is None:
                    session.add(PublishedMessage(
                        recommendation_id=delivery.recommendation_id,
                        telegram_channel_id=result[0],
                        telegram_message_id=result[1],
                    ))
                delivery.status = PublicationDeliveryStatus.SENT.value
                delivery.telegram_message_id = result[1]
                delivery.sent_at = datetime.now(timezone.utc)
                delivery.last_error = None
                session.commit()
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

    async def stop(self) -> None:
        self._stop_event.set()
        if self._worker_task:
            await self._worker_task
            self._worker_task = None
