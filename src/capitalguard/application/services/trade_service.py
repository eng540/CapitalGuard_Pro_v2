# --- START OF FILE: src/capitalguard/application/services/trade_service.py ---
import logging
import time
from typing import List, Optional
from datetime import datetime, timezone
import httpx

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import RecommendationRepoPort, NotifierPort
from capitalguard.interfaces.telegram.keyboards import (
    public_channel_keyboard,
    analyst_control_panel_keyboard,
)

# ✅ مستودعات وإدارة جلسة DB لقنوات المستخدم
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.db.repository import UserRepository, ChannelRepository

log = logging.getLogger(__name__)


def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    """Safely parse a user_id string to int, or return None if invalid."""
    try:
        return int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        return None


class TradeService:
    # ------------------------------
    # Symbol validation cache (Binance spot)
    # ------------------------------
    _SYMBOLS_CACHE: set[str] = set()
    _SYMBOLS_CACHE_TS: float = 0.0
    _SYMBOLS_CACHE_TTL_SEC: int = 6 * 60 * 60  # 6 hours

    def __init__(self, repo: RecommendationRepoPort, notifier: NotifierPort):
        self.repo = repo
        self.notifier = notifier

    # -------- Symbol validation helpers --------
    def _ensure_symbols_cache(self) -> None:
        """Fetch & cache Binance symbols (spot) if cache is empty/expired."""
        now = time.time()
        if self._SYMBOLS_CACHE and (now - self._SYMBOLS_CACHE_TS) < self._SYMBOLS_CACHE_TTL_SEC:
            return
        try:
            url = "https://api.binance.com/api/v3/exchangeInfo"
            with httpx.Client(timeout=10) as client:
                r = client.get(url)
                r.raise_for_status()
                data = r.json()
            symbols = {
                s["symbol"].upper()
                for s in data.get("symbols", [])
                if s.get("status") == "TRADING"
            }
            if symbols:
                self._SYMBOLS_CACHE = symbols
                self._SYMBOLS_CACHE_TS = now
                log.info("Loaded %s Binance symbols into cache.", len(symbols))
            else:
                log.warning("exchangeInfo returned empty symbol list; keeping previous cache.")
        except Exception as e:
            log.exception("Failed to refresh Binance symbols: %s", e)

    def _validate_symbol_exists(self, asset: str) -> str:
        """
        Normalize + validate that asset exists on Binance (spot);
        raises ValueError otherwise. Returns normalized symbol (uppercased).
        """
        norm = asset.strip().upper()
        self._ensure_symbols_cache()
        if self._SYMBOLS_CACHE and norm not in self._SYMBOLS_CACHE:
            raise ValueError(
                f'Invalid symbol "{asset}". Not found on Binance (spot). '
                "Use a valid trading pair like BTCUSDT, ETHUSDT, etc."
            )
        return norm

    # -------- UI card updates --------
    def _update_cards(self, rec: Recommendation) -> None:
        """Private helper to update public and private cards after a change."""
        public_keyboard = public_channel_keyboard(rec.id)
        # ملاحظة: edit_recommendation_card تعدّل البطاقة العامة إذا كان لدينا channel_id/message_id
        self.notifier.edit_recommendation_card(rec, keyboard=public_keyboard)

        uid = _parse_int_user_id(rec.user_id)
        if uid is not None:
            analyst_keyboard = analyst_control_panel_keyboard(rec.id)
            self.notifier.send_private_message(
                chat_id=uid,
                rec=rec,
                keyboard=analyst_keyboard,
                text_header="✅ تم تحديث التوصية بنجاح:",
            )

    # -------- Validation helpers --------
    def _validate_sl_vs_entry(self, side: str, entry: float, sl: float) -> None:
        """Validates that stop loss is logical compared to entry price."""
        side_upper = side.upper()
        if side_upper == "LONG" and not (sl <= entry):
            raise ValueError("في صفقات الشراء (LONG)، يجب أن يكون وقف الخسارة ≤ سعر الدخول.")
        if side_upper == "SHORT" and not (sl >= entry):
            raise ValueError("في صفقات البيع (SHORT)، يجب أن يكون وقف الخسارة ≥ سعر الدخول.")

    def _validate_targets(self, side: str, entry: float, tps: List[float]) -> None:
        """Validates that targets are logical compared to entry price."""
        if not tps:
            raise ValueError("مطلوب على الأقل هدف واحد.")
        side_upper = side.upper()
        if side_upper == "LONG":
            if not all(tp > entry for tp in tps):
                raise ValueError("في صفقات الشراء، يجب أن تكون جميع الأهداف > سعر الدخول.")
        elif side_upper == "SHORT":
            if not all(tp < entry for tp in tps):
                raise ValueError("في صفقات البيع، يجب أن تكون جميع الأهداف < سعر الدخول.")

    # -------- Core business actions --------
    def create_and_publish_recommendation(
        self,
        asset: str,
        side: str,
        market: str,
        entry: float,
        stop_loss: float,
        targets: List[float],
        notes: Optional[str],
        user_id: Optional[str],
        order_type: str,
        live_price: Optional[float] = None,
        # ✅ مستقبلاً: يمكننا قبول قائمة قنوات محددة للنشر الانتقائي
        target_channel_ids: Optional[List[int]] = None,
    ) -> Recommendation:
        """
        سلوك النشر (بعد هذا التعديل):
        - ننشر فقط إلى قنوات المستخدم المرتبطة.
        - إذا لم يملك أي قناة مرتبطة: لا ننشر علنًا، ونرسل للمستخدم رسالة خاصة تشرح ذلك.
        - في حالة وجود عدة قنوات: نخزّن أول رسالة ناجحة في حقول recommendation.channel_id/message_id (قيود التصميم الحالية).
        """
        log.info(
            "Creating recommendation: asset=%s side=%s order_type=%s user=%s",
            asset, side, order_type, user_id
        )
        asset = self._validate_symbol_exists(asset)
        try:
            order_type_enum = OrderType(order_type.upper())
        except ValueError:
            valid = ", ".join(ot.value for ot in OrderType)
            raise ValueError(f"Invalid order_type: {order_type}. Must be one of {valid}")

        if order_type_enum == OrderType.MARKET:
            if live_price is None:
                raise ValueError("Live price is required for Market orders.")
            status, final_entry = RecommendationStatus.ACTIVE, live_price
        else:
            status, final_entry = RecommendationStatus.PENDING, entry

        self._validate_sl_vs_entry(side, final_entry, stop_loss)
        self._validate_targets(side, final_entry, targets)

        rec_to_save = Recommendation(
            asset=Symbol(asset), side=Side(side), entry=Price(final_entry),
            stop_loss=Price(stop_loss), targets=Targets(targets),
            order_type=order_type_enum, status=status, market=market,
            notes=notes, user_id=user_id,
        )
        if rec_to_save.status == RecommendationStatus.ACTIVE:
            rec_to_save.activated_at = datetime.now(timezone.utc)

        # 1) حفظ في قاعدة البيانات
        saved_rec = self.repo.add(rec_to_save)

        # 2) تحديد قنوات المستخدم المرتبطة
        uid_int = _parse_int_user_id(user_id)
        linked_channels: List[int] = []
        if uid_int is not None:
            try:
                with SessionLocal() as session:
                    user_repo = UserRepository(session)
                    channel_repo = ChannelRepository(session)
                    user = user_repo.find_by_telegram_id(uid_int)
                    if user:
                        channels = channel_repo.list_by_user(user.id)
                        if channels:
                            linked_channels = [ch.telegram_channel_id for ch in channels]
            except Exception as e:
                log.error("Failed to load linked channels for user %s: %s", uid_int, e, exc_info=True)

        # إذا تم تمرير target_channel_ids نفلتر بها
        if target_channel_ids:
            linked_channels = [cid for cid in linked_channels if cid in target_channel_ids]

        # 3) لا ننشر إن لم توجد قنوات — نُبلغ المستخدم فقط
        if not linked_channels:
            uid = _parse_int_user_id(user_id)
            if uid is not None:
                analyst_keyboard = analyst_control_panel_keyboard(saved_rec.id)
                self.notifier.send_private_message(
                    chat_id=uid,
                    rec=saved_rec,
                    keyboard=analyst_keyboard,
                    text_header=(
                        "ℹ️ لم يتم النشر في أي قناة لأنه لا توجد قنوات مرتبطة بحسابك بعد.\n"
                        "استخدم الأمر: /link_channel @اسم_القناة ثم أعد المحاولة."
                    ),
                )
            log.info("Rec #%s not published (no linked channels).", saved_rec.id)
            return saved_rec

        # 4) النشر في قنوات المستخدم المرتبطة فقط
        public_keyboard = public_channel_keyboard(saved_rec.id)

        first_success: Optional[tuple[int, int]] = None
        for channel_id in linked_channels:
            try:
                result = self.notifier.post_to_channel(
                    channel_id=channel_id,
                    rec=saved_rec,
                    keyboard=public_keyboard
                )
                if result and first_success is None:
                    first_success = result  # (chat_id, message_id)
            except Exception as ch_err:
                log.error(
                    "Failed to publish rec #%s to channel %s: %s",
                    saved_rec.id, channel_id, ch_err, exc_info=True
                )
                continue

        # 5) إن نجح نشر واحد على الأقل نخزّن أول رسالة ناجحة
        if first_success:
            channel_id, message_id = first_success
            saved_rec.channel_id = channel_id
            saved_rec.message_id = message_id
            saved_rec.published_at = datetime.now(timezone.utc)
            saved_rec = self.repo.update(saved_rec)
        else:
            # لم ينجح أي نشر — نرسل تنبيهًا خاصًا
            uid = _parse_int_user_id(user_id)
            if uid is not None:
                self.notifier.send_private_message(
                    chat_id=uid,
                    rec=saved_rec,
                    keyboard=analyst_control_panel_keyboard(saved_rec.id),
                    text_header="❌ تعذر النشر في قنواتك المرتبطة. تحقق من صلاحيات البوت في القنوات.",
                )
            return saved_rec

        # 6) تنبيه خاص للمحلل بلوحة التحكم (نجاح النشر)
        uid = _parse_int_user_id(user_id)
        if uid is not None:
            analyst_keyboard = analyst_control_panel_keyboard(saved_rec.id)
            self.notifier.send_private_message(
                chat_id=uid, rec=saved_rec, keyboard=analyst_keyboard,
                text_header="🚀 تم النشر! هذه لوحة التحكم الخاصة بك:",
            )
        return saved_rec

    def activate_recommendation(self, rec_id: int) -> Optional[Recommendation]:
        """
        Centralized activation for PENDING recommendations.
        Entry price is already set for LIMIT/STOP orders (no price argument).
        """
        rec = self.repo.get(rec_id)
        if not rec or rec.status != RecommendationStatus.PENDING:
            return None

        log.info(f"Activating recommendation #{rec.id} for {rec.asset.value}")
        rec.activate()
        updated_rec = self.repo.update(rec)

        self._update_cards(updated_rec)

        uid = _parse_int_user_id(rec.user_id)
        if uid is not None:
            self.notifier.send_private_message(
                chat_id=uid,
                rec=updated_rec,
                text_header=f"🔥 أصبحت توصيتك #{rec.id} ({rec.asset.value}) مفعلة الآن!"
            )
        return updated_rec

    def close(self, rec_id: int, exit_price: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError(f"Recommendation {rec_id} not found.")

        rec.close(exit_price)
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        log.info(f"Rec #{rec.id} closed at price={exit_price} (status={updated_rec.status.value})")
        return updated_rec

    # -------- Queries & small helpers --------
    def list_open(
        self,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Recommendation]:
        return self.repo.list_open(symbol=symbol, side=side, status=status)

    def list_all(self, symbol: Optional[str] = None, status: Optional[str] = None) -> List[Recommendation]:
        return self.repo.list_all(symbol=symbol, status=status)

    def move_sl_to_be(self, rec_id: int) -> Optional[Recommendation]:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED:
            return None
        return self.update_sl(rec_id, rec.entry.value)

    def add_partial_close_note(self, rec_id: int) -> Optional[Recommendation]:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED:
            return None
        note = f"\n- تم إغلاق 50% من الصفقة في {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC."
        rec.notes = (rec.notes or "") + note
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        log.info(f"Rec #{rec.id} partial close note added")
        return updated_rec

    def update_sl(self, rec_id: int, new_sl: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED:
            raise ValueError("Recommendation not found or is closed.")
        self._validate_sl_vs_entry(rec.side.value, rec.entry.value, new_sl)
        rec.stop_loss = Price(new_sl)
        note_text = "\n- تم نقل وقف الخسارة إلى نقطة الدخول." if new_sl == rec.entry.value else f"\n- تم تحديث وقف الخسارة إلى {new_sl}."
        rec.notes = (rec.notes or "") + note_text
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        log.info(f"Rec #{rec.id} SL updated to {new_sl}")
        return updated_rec

    def update_targets(self, rec_id: int, new_targets: List[float]) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED:
            raise ValueError("Recommendation not found or is closed.")
        self._validate_targets(rec.side.value, rec.entry.value, new_targets)
        rec.targets = Targets(new_targets)
        targets_str = ", ".join(map(str, new_targets))
        rec.notes = (rec.notes or "") + f"\n- تم تحديث الأهداف إلى [{targets_str}]."
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        log.info(f"Rec #{rec.id} targets updated to [{targets_str}]")
        return updated_rec

    def get_recent_assets_for_user(self, user_id: str, limit: int = 5) -> List[str]:
        return self.repo.get_recent_assets_for_user(user_id, limit)
# --- END OF FILE ---