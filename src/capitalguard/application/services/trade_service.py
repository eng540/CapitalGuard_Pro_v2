# --- START OF FILE: src/capitalguard/application/services/trade_service.py ---
import logging
from typing import Iterable, List, Optional, Sequence, Tuple, Union, Dict, Any

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.db.repository import (
    RecommendationRepository,
    UserRepository,
    ChannelRepository,
)
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard

log = logging.getLogger(__name__)


class TradeService:
    """
    Service layer around Recommendations lifecycle:
    - Create (save only)
    - Publish (all active channels or a subset)
    - Update (move SL to BE, edit targets/SL, close, add partial-close note, etc.)
    """

    def __init__(self) -> None:
        self.notifier = TelegramNotifier()

        # Cache-like: avoid creating repo every call path repeatedly
        # NOTE: we create scoped sessions inside methods for safety.
        pass

    # ---------------------------
    # Creation helpers
    # ---------------------------
    def _build_rec_entity_from_inputs(
        self,
        *,
        asset: str,
        side: Union[str, Side],
        market: str,
        entry: Union[int, float],
        stop_loss: Union[int, float],
        targets: Sequence[Union[int, float]],
        order_type: Union[str, OrderType],
        user_id: Union[str, int],
        notes: Optional[str] = None,
        live_price: Optional[float] = None,
    ) -> Recommendation:
        return Recommendation(
            id=None,
            asset=Symbol(asset.upper().strip()),
            side=Side(side) if not isinstance(side, Side) else side,
            entry=Price(float(entry)),
            stop_loss=Price(float(stop_loss)),
            targets=Targets([float(t) for t in targets]),
            order_type=OrderType(order_type) if not isinstance(order_type, OrderType) else order_type,
            status=RecommendationStatus.PENDING,  # default until activation
            channel_id=None,
            message_id=None,
            published_at=None,
            market=market,
            notes=notes,
            user_id=str(user_id),
            created_at=None,
            updated_at=None,
            exit_price=None,
            activated_at=None,
            closed_at=None,
            alert_meta={"live_price": live_price} if live_price is not None else {},
        )

    # ---------------------------
    # Public API: Create & Publish
    # ---------------------------
    def create_recommendation(
        self,
        *,
        asset: str,
        side: Union[str, Side],
        market: str,
        entry: Union[int, float],
        stop_loss: Union[int, float],
        targets: Sequence[Union[int, float]],
        user_id: Union[str, int],
        order_type: Union[str, OrderType] = OrderType.LIMIT,
        notes: Optional[str] = None,
        live_price: Optional[float] = None,
    ) -> Recommendation:
        """
        Save only (no broadcast). Returns the saved Recommendation entity.
        """
        rec = self._build_rec_entity_from_inputs(
            asset=asset,
            side=side,
            market=market,
            entry=entry,
            stop_loss=stop_loss,
            targets=targets,
            order_type=order_type,
            user_id=user_id,
            notes=notes,
            live_price=live_price,
        )
        repo = RecommendationRepository()
        saved = repo.add(rec)
        return saved

    def create_and_publish_recommendation(
        self,
        *,
        asset: str,
        side: Union[str, Side],
        market: str,
        entry: Union[int, float],
        stop_loss: Union[int, float],
        targets: Sequence[Union[int, float]],
        user_id: Union[str, int],
        order_type: Union[str, OrderType] = OrderType.LIMIT,
        notes: Optional[str] = None,
        live_price: Optional[float] = None,
        publish: bool = True,
    ) -> Recommendation:
        """
        Save and (optionally) broadcast to ALL active channels of the owner.
        """
        rec = self.create_recommendation(
            asset=asset,
            side=side,
            market=market,
            entry=entry,
            stop_loss=stop_loss,
            targets=targets,
            user_id=user_id,
            order_type=order_type,
            notes=notes,
            live_price=live_price,
        )
        if publish:
            try:
                self.publish_existing(
                    rec_id=rec.id, user_id=user_id, target_channel_ids=None  # None => broadcast to all active
                )
            except Exception:
                # لا نكسر حفظ التوصية لو فشل النشر
                log.exception("Broadcast after create failed for rec_id=%s", rec.id)
        return rec

    # ---------------------------
    # Publish logic
    # ---------------------------
    def _fetch_owner_and_channels(
        self, user_id: Union[int, str], target_channel_ids: Optional[Sequence[int]]
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """
        Returns (internal_user_id, list of channel dicts:
            {'id', 'telegram_channel_id', 'username', 'title', 'is_active'}
        ). If `target_channel_ids` provided, filter by that subset (by telegram_channel_id).
        """
        with SessionLocal() as s:
            urepo = UserRepository(s)
            crepo = ChannelRepository(s)

            user = urepo.find_or_create(int(user_id))
            channels = crepo.list_by_user(user.id, only_active=True)

            rows = []
            for ch in channels:
                row = {
                    "id": ch.id,
                    "telegram_channel_id": int(ch.telegram_channel_id),
                    "username": getattr(ch, "username", None),
                    "title": getattr(ch, "title", None),
                    "is_active": bool(getattr(ch, "is_active", True)),
                }
                rows.append(row)

            if target_channel_ids:
                wanted = set(int(x) for x in target_channel_ids)
                rows = [r for r in rows if r["telegram_channel_id"] in wanted]

            return user.id, rows

    def publish_existing(
        self,
        *,
        rec_id: int,
        user_id: Union[str, int],
        target_channel_ids: Optional[Sequence[int]] = None,
    ) -> None:
        """
        Publish an existing recommendation to ALL active channels of the owner,
        or a subset specified in `target_channel_ids` (values are Telegram chat IDs).
        """
        repo = RecommendationRepository()
        rec = repo.get_by_id_for_user(rec_id, user_telegram_id=user_id)
        if not rec:
            raise ValueError("Recommendation not found or not owned by user.")

        internal_user_id, channels = self._fetch_owner_and_channels(user_id, target_channel_ids)
        if not channels:
            # لا توجد قنوات — لا نعتبره خطأ قاتل
            log.info("No active channels to publish for user=%s, rec_id=%s", user_id, rec_id)
            return

        keyboard = public_channel_keyboard(rec.id)

        # نحاول البث إلى كل القنوات؛ لا نفشل الكل لأجل قناة واحدة
        last_success: Optional[Tuple[int, int]] = None  # (channel_id, message_id)
        failures: List[Tuple[int, str]] = []

        for ch in channels:
            sent = self.notifier.post_to_channel(
                channel_id=ch["telegram_channel_id"],
                rec=rec,
                keyboard=keyboard,
            )
            if sent:
                last_success = sent
            else:
                failures.append((ch["telegram_channel_id"], "send failed"))

        # حدّث التوصية بآخر (channel_id/message_id) ناجحين حفاظًا على التوافق مع السكيما الحالية
        if last_success:
            ch_id, msg_id = last_success
            rec.channel_id = ch_id
            rec.message_id = msg_id
            repo.update(rec)

        if failures:
            log.warning("Some channels failed to publish for rec_id=%s: %s", rec_id, failures)

    # ---------------------------
    # Listing helpers for UI flows
    # ---------------------------
    def get_recent_assets_for_user(self, user_telegram_id: Union[str, int], limit: int = 5) -> List[str]:
        repo = RecommendationRepository()
        return repo.get_recent_assets_for_user(user_telegram_id, limit=limit)

    # ---------------------------
    # Update operations (placeholders / examples)
    # ---------------------------
    def move_sl_to_be(self, rec_id: int) -> None:
        """
        Example: set stop loss to entry for an ACTIVE/PENDING recommendation.
        """
        repo = RecommendationRepository()
        rec = repo.get(rec_id)
        if not rec:
            raise ValueError("Recommendation not found")
        rec.stop_loss = rec.entry
        repo.update(rec)

    def add_partial_close_note(self, rec_id: int) -> None:
        repo = RecommendationRepository()
        rec = repo.get(rec_id)
        if not rec:
            raise ValueError("Recommendation not found")
        notes = (rec.notes or "") + "\nPartial close noted."
        rec.notes = notes.strip()
        repo.update(rec)

    def update_sl(self, rec_id: int, new_sl: float) -> None:
        repo = RecommendationRepository()
        rec = repo.get(rec_id)
        if not rec:
            raise ValueError("Recommendation not found")
        rec.stop_loss = Price(float(new_sl))
        repo.update(rec)

    def update_targets(self, rec_id: int, new_targets: Sequence[Union[int, float]]) -> None:
        repo = RecommendationRepository()
        rec = repo.get(rec_id)
        if not rec:
            raise ValueError("Recommendation not found")
        rec.targets = Targets([float(x) for x in new_targets])
        repo.update(rec)

    def close(self, rec_id: int, exit_price: Union[int, float]) -> Recommendation:
        repo = RecommendationRepository()
        rec = repo.get(rec_id)
        if not rec:
            raise ValueError("Recommendation not found")

        rec.exit_price = float(exit_price)
        rec.status = RecommendationStatus.CLOSED
        rec.closed_at = None  # domain may set automatically on update trigger
        saved = repo.update(rec)

        # لو أردت تعديل بطاقة القناة عند الإغلاق يمكنك استخدام notifier.edit_recommendation_card(saved)
        # بشرط أن تكون channel_id/message_id متوفرة على التوصية.
        try:
            from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
            _ = public_channel_keyboard  # silence linter
            self.notifier.edit_recommendation_card(saved, keyboard=None)
        except Exception:
            # لا نكسر الإغلاق إن فشل التعديل
            log.debug("Edit recommendation card at close failed (maybe no channel/message).", exc_info=True)

        return saved
# --- END OF FILE: src/capitalguard/application/services/trade_service.py ---