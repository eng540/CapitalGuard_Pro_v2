# --- START OF FILE: src/capitalguard/application/services/trade_service.py ---
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.db.repository import (
    UserRepository,
    ChannelRepository,
    RecommendationRepository,
)
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
from capitalguard.domain.entities import Recommendation, RecommendationStatus

log = logging.getLogger(__name__)


class TradeService:
    """
    TradeService (Phase 9.x)
    - يعالج الربط الصحيح بين توصيات المستخدم وقنواته المرتبطة.
    - يمنع أي fallback إلى TELEGRAM_CHAT_ID عند عدم وجود قنوات.
    - يعزل أخطاء النشر قناةً قناة.
    """

    def __init__(
        self,
        notifier: Optional[TelegramNotifier] = None,
        session_factory=SessionLocal,
    ) -> None:
        self._session_factory = session_factory
        self._notifier = notifier or TelegramNotifier()

    # ------------- إنشاء ثم نشر (اختياري) -------------

    def create_and_publish_recommendation(
        self,
        telegram_user_id: int,
        rec_data: Dict[str, Any],
        *,
        target_channel_ids: Optional[Sequence[int]] = None,
        send_private_preview: bool = True,
    ) -> Recommendation:
        """
        ينشئ توصية جديدة لمستخدم (معرّف بتلغرام) ثم ينشرها وفق قنواته المرتبطة.
        - لا ينشر في قناة افتراضية إن لم توجد قنوات مرتبطة: يرسل تنبيه خاص بدلًا من ذلك.
        """
        with self._session_factory() as session:
            users = UserRepository(session)
            chans = ChannelRepository(session)
            recs = RecommendationRepository(session)

            user = users.find_or_create(telegram_id=int(telegram_user_id))
            # نفترض أن المستودع يوفّر دالة create_for_user_id أو create(...)
            rec = recs.create_for_user_id(user_id=user.id, **rec_data)

            # نشر للقنوات
            self._broadcast_to_user_channels(
                user_id=user.id,
                telegram_user_id=telegram_user_id,
                rec=rec,
                chans_repo=chans,
                target_channel_ids=target_channel_ids,
                send_private_preview=send_private_preview,
            )

            session.commit()
            return rec

    # ------------- نشر توصية موجودة -------------

    def publish_existing_recommendation(
        self,
        rec_id: int,
        telegram_user_id: int,
        *,
        target_channel_ids: Optional[Sequence[int]] = None,
        send_private_preview: bool = True,
    ) -> Optional[Recommendation]:
        """
        ينشر توصية موجودة بالفعل للمستخدم الحالي عبر قنواته المرتبطة.
        يُستخدم عندما تكون التوصية منشأة مسبقًا وتريد بثّها الآن.
        """
        with self._session_factory() as session:
            users = UserRepository(session)
            chans = ChannelRepository(session)
            recs = RecommendationRepository(session)

            user = users.find_or_create(telegram_id=int(telegram_user_id))
            rec = recs.get(rec_id)
            if not rec:
                log.warning("publish_existing_recommendation: rec %s not found", rec_id)
                return None

            # تأكد أن التوصية تخص هذا المستخدم
            if getattr(rec, "user_id", None) != user.id:
                log.warning(
                    "publish_existing_recommendation: user %s tried to publish rec %s not owned by them",
                    user.id,
                    rec_id,
                )
                return None

            self._broadcast_to_user_channels(
                user_id=user.id,
                telegram_user_id=telegram_user_id,
                rec=rec,
                chans_repo=chans,
                target_channel_ids=target_channel_ids,
                send_private_preview=send_private_preview,
            )
            session.commit()
            return rec

    # ------------- عمليات إدارة التوصية (تفويض للمستودع) -------------

    def update_sl(self, rec_id: int, new_sl: float) -> Recommendation:
        with self._session_factory() as session:
            recs = RecommendationRepository(session)
            rec = recs.update_sl(rec_id, new_sl)
            session.commit()
            return rec

    def update_targets(self, rec_id: int, targets: Sequence[float]) -> Recommendation:
        with self._session_factory() as session:
            recs = RecommendationRepository(session)
            rec = recs.update_targets(rec_id, list(targets))
            session.commit()
            return rec

    def move_sl_to_be(self, rec_id: int) -> Recommendation:
        with self._session_factory() as session:
            recs = RecommendationRepository(session)
            rec = recs.move_sl_to_be(rec_id)
            session.commit()
            return rec

    def close(self, rec_id: int, exit_price: float) -> Recommendation:
        with self._session_factory() as session:
            recs = RecommendationRepository(session)
            rec = recs.close(rec_id, exit_price)
            session.commit()
            return rec

    # ------------- استرجاع للمستخدم (للاستخدام في /open و/export) -------------

    def list_open_for_user_id(
        self,
        user_id: int,
        *,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Recommendation]:
        with self._session_factory() as session:
            recs = RecommendationRepository(session)
            return recs.list_open_for_user_id(
                user_id, symbol=symbol, side=side, status=status
            )

    def list_all_for_user_id(self, user_id: int) -> List[Recommendation]:
        with self._session_factory() as session:
            recs = RecommendationRepository(session)
            return recs.list_all_for_user_id(user_id)

    # ------------- منطق داخلي للنشر -------------

    def _broadcast_to_user_channels(
        self,
        *,
        user_id: int,
        telegram_user_id: int,
        rec: Recommendation,
        chans_repo: ChannelRepository,
        target_channel_ids: Optional[Sequence[int]],
        send_private_preview: bool,
    ) -> None:
        """
        ينشر التوصية إلى قنوات المستخدم المرتبطة.
        - إذا لم توجد قنوات: لا fallback — يُرسل تنبيه خاص فقط.
        - إذا قدمت target_channel_ids: ننشر في تقاطعها مع قنوات المستخدم.
        """
        # اجلب قنوات المستخدم الفعالة
        channels = chans_repo.list_by_user(user_id)

        if target_channel_ids:
            target_set = {int(c) for c in target_channel_ids}
            channels = [c for c in channels if int(c.telegram_channel_id) in target_set]

        if not channels:
            # لا ننشر في قناة افتراضية إطلاقًا ضمن 9.x
            try:
                self._notifier.send_private_message(
                    chat_id=int(telegram_user_id),
                    rec=rec,
                    text_header=(
                        "ℹ️ لم يتم النشر للقنوات: لا توجد قنوات مرتبطة بحسابك.\n"
                        "استخدم /link_channel لربط قناة عامة ثم أعد النشر."
                    ),
                )
            except Exception:
                log.exception("Failed to send no-channel private notice to %s", telegram_user_id)
            return

        # نشر بطاقة عامة في كل قناة مع عزل الأخطاء
        kb = public_channel_keyboard(getattr(rec, "id", 0))
        for ch in channels:
            try:
                res = self._notifier.post_to_channel(
                    channel_id=int(ch.telegram_channel_id),
                    rec=rec,
                    keyboard=kb,
                )
                if not res:
                    log.error(
                        "Broadcast failed: rec=%s channel=%s (no result)",
                        getattr(rec, "id", None),
                        getattr(ch, "username", ch.telegram_channel_id),
                    )
            except Exception:
                log.exception(
                    "Broadcast exception: rec=%s channel=%s",
                    getattr(rec, "id", None),
                    getattr(ch, "username", ch.telegram_channel_id),
                )

        # إرسال معاينة خاصة للمحلل (اختياري)
        if send_private_preview:
            try:
                self._notifier.send_private_message(
                    chat_id=int(telegram_user_id),
                    rec=rec,
                    text_header="✅ تم نشر بطاقتك على قنواتك المرتبطة.",
                )
            except Exception:
                log.exception("Failed to send private preview to %s", telegram_user_id)
# --- END OF FILE: src/capitalguard/application/services/trade_service.py ---