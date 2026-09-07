from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from capitalguard.infrastructure.db.models.dedup import DedupLedger
from capitalguard.infrastructure.db.models.historical_financial_candidate import (
    HistoricalFinancialCandidate,
)
from capitalguard.infrastructure.db.models.historical_forwarding import (
    HistoricalForwardBatch,
    HistoricalForwardReceipt,
)
from capitalguard.infrastructure.db.models.historical_message import HistoricalMessage
from capitalguard.infrastructure.db.models.historical_replay_run import HistoricalReplayRun
from capitalguard.infrastructure.db.uow import UnitOfWork

logger = logging.getLogger(__name__)


@dataclass
class ItemDiagnostic:
    receipt_id: int
    status: str
    reason: Optional[str] = None
    replay_status: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "status": self.status,
            "reason": self.reason,
            "replay_status": self.replay_status,
        }


@dataclass
class BatchProgressionResult:
    batch_id: int
    status: str
    progressed: int
    review_required: int
    failed: int
    items: int
    replay_statuses: List[str]
    details: List[ItemDiagnostic] = field(default_factory=list)


class HistoricalForwardingService:
    """
    خدمة استقبال الإشارات التاريخية عبر التلجرام.
    تدير فحص التكرار، الترطيب الحتمي للبيانات، والاستكمال الذاتي للمحاكاة الفاشلة (Self-Healing).
    """

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        parser_service: Any,
        replay_service: Any,
        dedup_service: Optional[Any] = None,
        adjudication_service: Optional[Any] = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.parser_service = parser_service
        self.replay_service = replay_service
        self.dedup_service = dedup_service
        self.adjudication_service = adjudication_service

    def _utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    async def ingest_forward_batch(
        self,
        messages: List[Dict[str, Any]],
        channel_id: Optional[str] = None,
        channel_title: Optional[str] = None,
        forwarded_from: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> Tuple[HistoricalForwardBatch, List[HistoricalForwardReceipt]]:
        """
        استقبال دفعة رسائل وإصدار إيصالات أولية.
        """
        async with self.uow_factory() as uow:
            batch = HistoricalForwardBatch(
                channel_id=channel_id,
                channel_title=channel_title,
                forwarded_from=forwarded_from,
                user_id=user_id,
                total_items=len(messages),
                status="INGESTED",
                created_at=self._utc_now(),
            )
            uow.session.add(batch)
            await uow.session.flush()

            receipts: List[HistoricalForwardReceipt] = []
            for msg in messages:
                raw_text = msg.get("text") or msg.get("caption") or ""
                source_msg_id = str(msg.get("message_id") or "")
                date_val = msg.get("date")

                if isinstance(date_val, (int, float)):
                    msg_time = datetime.fromtimestamp(date_val, tz=timezone.utc)
                elif isinstance(date_val, datetime):
                    msg_time = date_val if date_val.tzinfo else date_val.replace(tzinfo=timezone.utc)
                else:
                    msg_time = self._utc_now()

                receipt = HistoricalForwardReceipt(
                    batch_id=batch.id,
                    source_message_id=source_msg_id,
                    channel_id=channel_id,
                    raw_text=raw_text,
                    message_timestamp=msg_time,
                    status="PENDING",
                    created_at=self._utc_now(),
                )
                uow.session.add(receipt)
                receipts.append(receipt)

            await uow.commit()
            return batch, receipts

    async def auto_progress_batch(self, batch_id: int) -> BatchProgressionResult:
        """
        معالجة الدفعة خطوة بخطوة وتطبيق الفحص السببي والاستكمال الذاتي للتكرار.
        """
        async with self.uow_factory() as uow:
            stmt = (
                select(HistoricalForwardReceipt)
                .where(HistoricalForwardReceipt.batch_id == batch_id)
                .order_by(HistoricalForwardReceipt.id.asc())
            )
            res = await uow.session.execute(stmt)
            receipts = list(res.scalars().all())

            if not receipts:
                return BatchProgressionResult(
                    batch_id=batch_id,
                    status="EMPTY",
                    progressed=0,
                    review_required=0,
                    failed=0,
                    items=0,
                    replay_statuses=[],
                    details=[],
                )

            progressed_count = 0
            review_count = 0
            failed_count = 0
            replay_statuses: List[str] = []
            diagnostics: List[ItemDiagnostic] = []

            for receipt in receipts:
                diag = await self._progress_single_receipt(uow=uow, receipt=receipt)
                diagnostics.append(diag)
                replay_statuses.append(diag.replay_status or diag.status)

                if diag.status in {"COMPLETED", "PROGRESSED", "ALREADY_REGISTERED"}:
                    if diag.replay_status not in {"FAILED", "REPLAY_FAILED"}:
                        progressed_count += 1
                    else:
                        failed_count += 1
                elif diag.status == "REVIEW_REQUIRED":
                    review_count += 1
                else:
                    failed_count += 1

            batch_stmt = select(HistoricalForwardBatch).where(HistoricalForwardBatch.id == batch_id)
            b_res = await uow.session.execute(batch_stmt)
            batch = b_res.scalar_one_or_none()

            batch_status = "COMPLETED"
            if failed_count > 0 and progressed_count > 0:
                batch_status = "PARTIAL"
            elif failed_count > 0 and progressed_count == 0:
                batch_status = "FAILED"
            elif review_count > 0:
                batch_status = "REVIEW_REQUIRED"
            elif progressed_count == 0 and all(d.status == "ALREADY_REGISTERED" for d in diagnostics):
                batch_status = "ALREADY_REGISTERED"

            if batch:
                batch.status = batch_status
                batch.updated_at = self._utc_now()

            await uow.commit()

            logger.info(
                f"Historical auto progression finalized batch={batch_id} "
                f"status={batch_status} progressed={progressed_count} "
                f"review_required={review_count} failed={failed_count} "
                f"items={len(receipts)} replay_statuses={replay_statuses}"
            )
            logger.info(f"Historical progression item diagnostics batch={batch_id} details={[d.to_dict() for d in diagnostics]}")

            return BatchProgressionResult(
                batch_id=batch_id,
                status=batch_status,
                progressed=progressed_count,
                review_required=review_count,
                failed=failed_count,
                items=len(receipts),
                replay_statuses=replay_statuses,
                details=diagnostics,
            )

    async def _progress_single_receipt(
        self,
        uow: UnitOfWork,
        receipt: HistoricalForwardReceipt,
    ) -> ItemDiagnostic:
        """
        معالجة الإيصال الفردي مع تطبيق التحقق من التكرار والترطيب الحتمي.
        """
        source_msg_id = receipt.source_message_id
        channel_id = receipt.channel_id

        # 1. التحقق من التكرار في dedup_ledger
        is_duplicate, prior_candidate, prior_replay = await self._duplicate_resolution(
            uow=uow,
            source_message_id=source_msg_id,
            channel_id=channel_id,
            raw_text=receipt.raw_text,
        )

        if is_duplicate:
            # ترطيب كائن المرشح المالي دائماً لمنع ظهور الشرط الفارغة (—)
            receipt.candidate = prior_candidate
            if prior_candidate:
                receipt.candidate_id = prior_candidate.id

            # فحص حالة المحاكاة السابقة
            replay_status = prior_replay.status if prior_replay else None

            # إذا كانت المحاكاة السابقة فاشلة أو غير مكتملة، نفعل الاستكمال الذاتي فوراً
            if replay_status in {"FAILED", "REPLAY_FAILED", "REPLAY_PARTIAL", None}:
                logger.info(
                    f"Self-Healing: Receipt {receipt.id} (source={source_msg_id}) is duplicate "
                    f"with failed replay ({replay_status}). Triggering retry_g6..."
                )
                healed_status, heal_reason = await self._execute_self_healing(
                    uow=uow,
                    candidate=prior_candidate,
                    previous_replay=prior_replay,
                )
                receipt.status = "ALREADY_REGISTERED"
                receipt.replay_status = healed_status
                receipt.reason = heal_reason
                return ItemDiagnostic(
                    receipt_id=receipt.id,
                    status="ALREADY_REGISTERED",
                    reason=heal_reason,
                    replay_status=healed_status,
                )

            # إذا كانت المحاكاة السابقة ناجحة ومكتملة، نسلم النتيجة المعتمدة
            receipt.status = "ALREADY_REGISTERED"
            receipt.replay_status = replay_status
            return ItemDiagnostic(
                receipt_id=receipt.id,
                status="ALREADY_REGISTERED",
                reason=None,
                replay_status=replay_status,
            )

        # 2. إشارة جديدة تماماً -> المعالجة الطبيعية
        return await self._process_new_signal_receipt(uow=uow, receipt=receipt)

    async def _execute_self_healing(
        self,
        uow: UnitOfWork,
        candidate: Optional[HistoricalFinancialCandidate],
        previous_replay: Optional[HistoricalReplayRun],
    ) -> Tuple[str, Optional[str]]:
        """
        إعادة تشغيل المحاكاة التاريخية للتوصية الفاشلة دون إنشاء سجل مالي مكرر.
        """
        if not candidate:
            return "REPLAY_FAILED", "Candidate not found for self-healing"

        try:
            # 1. إذا وجد تشغيل سابق، نستخدم retry_g6 المعتمد في النواة
            if previous_replay and hasattr(self.replay_service, "retry_g6"):
                logger.info(f"Self-Healing: Invoking retry_g6 for replay_run_id={previous_replay.id}")
                res = await self.replay_service.retry_g6(replay_run_id=previous_replay.id)
                status_val = getattr(res, "status", "REPLAY_FAILED")
                return status_val, None

            # 2. إذا لم يوجد تشغيل سابق أو تعذر، نستدعي evaluate_replay مباشرة
            if hasattr(self.replay_service, "evaluate_replay"):
                res = await self.replay_service.evaluate_replay(signal=candidate)
                status_val = getattr(res, "status", "COMPLETED" if getattr(res, "is_terminal", False) else "ACTIVE")
                return status_val, None

            if hasattr(self.replay_service, "replay_g6"):
                res = await self.replay_service.replay_g6(candidate_id=candidate.id)
                status_val = getattr(res, "status", "REPLAY_FAILED")
                return status_val, None

        except Exception as exc:
            logger.error(f"Self-Healing failed for candidate {candidate.id}: {exc}", exc_info=True)
            return "REPLAY_FAILED", str(exc)

        return "REPLAY_FAILED", "No suitable replay engine method available"

    async def _duplicate_resolution(
        self,
        uow: UnitOfWork,
        source_message_id: str,
        channel_id: Optional[str],
        raw_text: str,
    ) -> Tuple[bool, Optional[HistoricalFinancialCandidate], Optional[HistoricalReplayRun]]:
        """
        فحص التكرار بدقة واسترجاع الكيان الأصلي وأحدث محاكاة مرتبطة به.
        """
        candidate: Optional[HistoricalFinancialCandidate] = None

        # 1. البحث عبر source_message_id
        if source_message_id:
            c_stmt = (
                select(HistoricalFinancialCandidate)
                .where(HistoricalFinancialCandidate.source_message_id == str(source_message_id))
                .order_by(HistoricalFinancialCandidate.id.desc())
            )
            c_res = await uow.session.execute(c_stmt)
            candidate = c_res.scalars().first()

        # 2. البحث عبر dedup_ledger بالهاش
        if not candidate and self.dedup_service and raw_text:
            text_hash = self.dedup_service.compute_hash(raw_text)
            dedup_stmt = select(DedupLedger).where(DedupLedger.message_hash == text_hash)
            d_res = await uow.session.execute(dedup_stmt)
            entry = d_res.scalars().first()
            if entry and entry.entity_id:
                cand_stmt = select(HistoricalFinancialCandidate).where(
                    HistoricalFinancialCandidate.id == entry.entity_id
                )
                cand_res = await uow.session.execute(cand_stmt)
                candidate = cand_res.scalars().first()

        if candidate:
            # جلب أحدث محاكاة سوق مرتبطة بالمرشح
            r_stmt = (
                select(HistoricalReplayRun)
                .where(HistoricalReplayRun.candidate_id == candidate.id)
                .order_by(HistoricalReplayRun.id.desc())
            )
            r_res = await uow.session.execute(r_stmt)
            latest_replay = r_res.scalars().first()
            return True, candidate, latest_replay

        return False, None, None

    async def _process_new_signal_receipt(
        self,
        uow: UnitOfWork,
        receipt: HistoricalForwardReceipt,
    ) -> ItemDiagnostic:
        """
        معالجة الإشارة الجديدة: استخراج البيانات، بناء الكيان، وتسجيل المحاكاة.
        """
        try:
            # 1. تحليل النص واستخراج البيانات المالية
            parsed = await self.parser_service.parse_text(receipt.raw_text)
            if not parsed or not getattr(parsed, "is_signal", False):
                receipt.status = "NOT_A_SIGNAL"
                receipt.reason = "Text did not match trading signal structure"
                return ItemDiagnostic(receipt_id=receipt.id, status="NOT_A_SIGNAL", reason=receipt.reason)

            # 2. حفظ المرشح المالي
            candidate = HistoricalFinancialCandidate(
                source_message_id=receipt.source_message_id,
                channel_id=receipt.channel_id,
                symbol=parsed.symbol,
                direction=parsed.direction,
                entry_price=parsed.entry_price,
                stop_loss=parsed.stop_loss,
                targets=parsed.targets,
                decision_timestamp=receipt.message_timestamp,
                raw_text=receipt.raw_text,
                status="ACCEPTED",
                created_at=self._utc_now(),
            )
            uow.session.add(candidate)
            await uow.session.flush()

            receipt.candidate = candidate
            receipt.candidate_id = candidate.id

            # 3. تسجيل الهاش في dedup_ledger
            if self.dedup_service and receipt.raw_text:
                await self.dedup_service.record(
                    raw_text=receipt.raw_text,
                    entity_id=candidate.id,
                    entity_type="HISTORICAL_CANDIDATE",
                    session=uow.session,
                )

            # 4. تشغيل محاكاة السوق السببية
            replay_res = await self.replay_service.evaluate_replay(signal=candidate)
            rep_status = getattr(replay_res, "status", "COMPLETED")

            receipt.status = "PROGRESSED"
            receipt.replay_status = rep_status

            return ItemDiagnostic(
                receipt_id=receipt.id,
                status="PROGRESSED",
                reason=None,
                replay_status=rep_status,
            )

        except Exception as exc:
            logger.error(f"Failed to process new signal receipt {receipt.id}: {exc}", exc_info=True)
            receipt.status = "REPLAY_FAILED"
            receipt.reason = str(exc)
            receipt.replay_status = "FAILED"
            return ItemDiagnostic(
                receipt_id=receipt.id,
                status="REPLAY_FAILED",
                reason=str(exc),
                replay_status="FAILED",
            )