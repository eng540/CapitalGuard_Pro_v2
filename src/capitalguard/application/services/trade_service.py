# --- START OF FILE: src/capitalguard/application/services/trade_service.py ---
import logging
import time
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timezone
import httpx

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import RecommendationRepoPort, NotifierPort
from capitalguard.interfaces.telegram.keyboards import (
    public_channel_keyboard,
    analyst_control_panel_keyboard,
)
# ✅ استيراد دالة حساب النسبة المئوية لاستخدامها في رسائل الإشعارات
from capitalguard.interfaces.telegram.ui_texts import _pct

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
        try:
            public_keyboard = public_channel_keyboard(rec.id)
            # ملاحظة: edit_recommendation_card تعدّل البطاقة العامة إذا كان لدينا channel_id/message_id
            self.notifier.edit_recommendation_card(rec, keyboard=public_keyboard)
        except Exception as e:
            log.debug("Skipping public card update (maybe no message published yet): %s", e)

        uid = _parse_int_user_id(rec.user_id)
        if uid is not None:
            try:
                analyst_keyboard = analyst_control_panel_keyboard(rec.id)
                self.notifier.send_private_message(
                    chat_id=uid,
                    rec=rec,
                    keyboard=analyst_keyboard,
                    text_header="✅ تم تحديث التوصية بنجاح:",
                )
            except Exception as e:
                log.debug("Failed to send private update message to user %s: %s", uid, e)

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

    # =========================
    # Core save/publish actions
    # =========================
    def create_recommendation(
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
    ) -> Recommendation:
        """
        يحفظ التوصية فقط (بدون نشر).
        - دائمًا تربط بالمستخدم (حتى لو لا توجد قنوات).
        - MARKET ⇒ ACTIVE مع entry=live_price (مطلوب).
        - غير ذلك ⇒ PENDING مع entry=entry.
        """
        log.info(
            "Saving recommendation ONLY: asset=%s side=%s order_type=%s user=%s",
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
            asset=Symbol(asset),
            side=Side(side),
            entry=Price(final_entry),
            stop_loss=Price(stop_loss),
            targets=Targets(targets),
            order_type=order_type_enum,
            status=status,
            market=market,
            notes=notes,
            user_id=user_id,
        )
        if rec_to_save.status == RecommendationStatus.ACTIVE:
            rec_to_save.activated_at = datetime.now(timezone.utc)

        saved = self.repo.add(rec_to_save)

        # رسالة خاصة للمستخدم للتأكيد + لوحة التحكم
        uid = _parse_int_user_id(user_id)
        if uid is not None:
            try:
                self.notifier.send_private_message(
                    chat_id=uid,
                    rec=saved,
                    keyboard=analyst_control_panel_keyboard(saved.id),
                    text_header="💾 تم حفظ التوصية بنجاح (بدون نشر)."
                )
            except Exception as e:
                log.debug("Failed to send private save-confirmation to user %s: %s", uid, e)
        return saved

    def _load_user_linked_channels(self, uid_int: int, only_active: bool = True) -> List[Any]:
        """
        يرجع قائمة سجلات قنوات المستخدم المرتبطة (ORM rows).
        يفضّل only_active=True للنشر.
        """
        try:
            with SessionLocal() as session:
                user_repo = UserRepository(session)
                channel_repo = ChannelRepository(session)
                user = user_repo.find_by_telegram_id(uid_int)
                if not user:
                    return []
                channels = channel_repo.list_by_user(user.id, only_active=only_active)
                return channels or []
        except Exception as e:
            log.error("Failed to load linked channels for user %s: %s", uid_int, e, exc_info=True)
            return []

    def publish_recommendation(
        self,
        rec_id: int,
        user_id: Optional[str],
        channel_ids: Optional[List[int]] = None,
    ) -> Tuple[Recommendation, Dict[str, List[Dict[str, Any]]]]:
        """
        ينشر توصية موجودة إلى قنوات المستخدم المرتبطة (أو subset محدد).
        يرجع (التوصية بعد أي تحديث، تقرير: {success:[...], failed:[...]}).

        - لا قناة افتراضية مطلقًا؛ لا استخدام لأي TELEGRAM_CHAT_ID من البيئة.
        - فشل قناة لا يوقف بقية القنوات.
        """
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError(f"Recommendation {rec_id} not found.")
        if rec.status == RecommendationStatus.CLOSED:
            raise ValueError("Cannot publish a closed recommendation.")

        uid_int = _parse_int_user_id(user_id or rec.user_id)
        report: Dict[str, List[Dict[str, Any]]] = {"success": [], "failed": []}

        if uid_int is None:
            # لا يمكن تحديد المستخدم ⇒ لا نشر
            report["failed"].append({"channel_id": None, "reason": "USER_NOT_RESOLVED"})
            return rec, report

        # جلب قنوات المستخدم المفعلة
        linked_channels = self._load_user_linked_channels(uid_int, only_active=True)

        # فلترة اختيارية بالقنوات المستهدفة
        if channel_ids:
            # بعض مستودعاتك ترجع الحقل telegram_channel_id
            linked_channels = [
                ch for ch in linked_channels
                if getattr(ch, "telegram_channel_id", None) in set(channel_ids)
            ]

        if not linked_channels:
            # لا قنوات ⇒ لا نشر مع إشعار ودّي
            try:
                self.notifier.send_private_message(
                    chat_id=uid_int,
                    rec=rec,
                    keyboard=analyst_control_panel_keyboard(rec.id),
                    text_header=(
                        "ℹ️ لا توجد قنوات مرتبطة بحسابك بعد، لذا لن يتم النشر.\n"
                        "استخدم الأمر: /link_channel @اسم_القناة ثم أعد المحاولة."
                    ),
                )
            except Exception as e:
                log.debug("Failed to notify user %s about no channels: %s", uid_int, e)
            return rec, report

        public_keyboard = public_channel_keyboard(rec.id)
        first_success_msg: Optional[Tuple[int, int]] = None  # (chat_id, message_id)

        for ch in linked_channels:
            cid = getattr(ch, "telegram_channel_id", None)
            try:
                result = self.notifier.post_to_channel(
                    channel_id=cid,
                    rec=rec,
                    keyboard=public_keyboard
                )
                if result:
                    # result: (chat_id, message_id)
                    report["success"].append({"channel_id": cid, "message_id": result[1]})
                    if first_success_msg is None:
                        first_success_msg = result
                else:
                    report["failed"].append({"channel_id": cid, "reason": "UNKNOWN"})
            except Exception as ch_err:
                log.error("Failed to publish rec #%s to channel %s: %s", rec.id, cid, ch_err, exc_info=True)
                report["failed"].append({"channel_id": cid, "reason": str(ch_err)})

        # احفظ أول رسالة منشورة (حقل واحد legacy) إن وُجدت
        if first_success_msg:
            channel_id, message_id = first_success_msg
            rec.channel_id = channel_id
            rec.message_id = message_id
            rec.published_at = datetime.now(timezone.utc)
            rec = self.repo.update(rec)

            # إشعار خاص بنجاح النشر
            try:
                self.notifier.send_private_message(
                    chat_id=uid_int,
                    rec=rec,
                    keyboard=analyst_control_panel_keyboard(rec.id),
                    text_header="🚀 تم النشر! هذه لوحة التحكم الخاصة بك:",
                )
            except Exception as e:
                log.debug("Failed to notify user %s after publish: %s", uid_int, e)
        else:
            # لم ينجح أي نشر
            try:
                self.notifier.send_private_message(
                    chat_id=uid_int,
                    rec=rec,
                    keyboard=analyst_control_panel_keyboard(rec.id),
                    text_header="❌ تعذر النشر في قنواتك المرتبطة. تحقق من صلاحيات البوت في القنوات.",
                )
            except Exception as e:
                log.debug("Failed to notify user %s about publish failure: %s", uid_int, e)

        return rec, report

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
        channel_ids: Optional[List[int]] = None,
        publish: bool = True,  # ← حفظ فقط إن False
    ) -> Recommendation:
        """
        سلوك مرن:
        - publish=False ⇒ حفظ فقط وإرجاع التوصية.
        - publish=True  ⇒ حفظ ثم محاولة النشر لقنوات المستخدم المفعّلة (أو subset محدد).
        """
        saved = self.create_recommendation(
            asset=asset,
            side=side,
            market=market,
            entry=entry,
            stop_loss=stop_loss,
            targets=targets,
            notes=notes,
            user_id=user_id,
            order_type=order_type,
            live_price=live_price,
        )
        if not publish:
            return saved

        # النشر (مع تقرير داخلي غير مستخدم هنا)
        self.publish_recommendation(
            rec_id=saved.id,
            user_id=user_id,
            channel_ids=channel_ids,
        )
        return saved

    # ✅ واجهة توافقية مع conversation_handlers: publish_existing(...)
    def publish_existing(
        self,
        rec_id: int,
        user_id: Optional[str],
        target_channel_ids: Optional[List[int]] = None,
    ) -> Tuple[Recommendation, Dict[str, List[Dict[str, Any]]]]:
        """
        غلاف/اختصار لاستدعاء publish_recommendation مع اسم وسيط متوافق.
        """
        return self.publish_recommendation(
            rec_id=rec_id,
            user_id=user_id,
            channel_ids=target_channel_ids,
        )

    # =========================
    # Other actions
    # =========================
    def activate_recommendation(self, rec_id: int) -> Optional[Recommendation]:
        """
        Centralized activation for PENDING recommendations.
        Entry price is already set for LIMIT/STOP orders (no price argument).
        """
        rec = self.repo.get(rec_id)
        if not rec or rec.status != RecommendationStatus.PENDING:
            return None

        log.info("Activating recommendation #%s for %s", rec.id, rec.asset.value)
        rec.activate()
        updated_rec = self.repo.update(rec)

        self._update_cards(updated_rec)
        
        # ✅ --- NEW: Send a threaded notification to the public channel ---
        if updated_rec.channel_id and updated_rec.message_id:
            asset = updated_rec.asset.value
            entry = updated_rec.entry.value
            side = updated_rec.side.value
            notification_text = (
                f"<b>✅ تفعيل #{asset}</b>\n"
                f"تم الدخول في صفقة {side.upper()} عند سعر ~{entry:g}."
            )
            try:
                self.notifier.post_notification_reply(
                    chat_id=updated_rec.channel_id,
                    message_id=updated_rec.message_id,
                    text=notification_text
                )
            except Exception as e:
                log.warning("Failed to send activation notification for rec #%s: %s", rec_id, e)
        # --- END OF NEW LOGIC ---

        uid = _parse_int_user_id(rec.user_id)
        if uid is not None:
            try:
                self.notifier.send_private_message(
                    chat_id=uid,
                    rec=updated_rec,
                    text_header=f"🔥 أصبحت توصيتك #{rec.id} ({rec.asset.value}) مفعلة الآن!"
                )
            except Exception as e:
                log.debug("Failed to notify user %s about activation: %s", uid, e)
        return updated_rec

    def close(self, rec_id: int, exit_price: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError(f"Recommendation {rec_id} not found.")

        rec.close(exit_price)
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        
        # ✅ --- NEW: Send a threaded notification for the closure ---
        if updated_rec.channel_id and updated_rec.message_id:
            asset = updated_rec.asset.value
            entry = updated_rec.entry.value
            side = updated_rec.side.value
            pnl = _pct(entry, exit_price, side)
            result_emoji = "🏆" if pnl >= 0 else "💔"
            result_text = "ربح" if pnl >= 0 else "خسارة"
            notification_text = (
                f"<b>{result_emoji} إغلاق صفقة #{asset}</b>\n"
                f"تم إغلاق الصفقة عند سعر {exit_price:g} بنتيجة {result_text} <b>{pnl:+.2f}%</b>."
            )
            try:
                self.notifier.post_notification_reply(
                    chat_id=updated_rec.channel_id,
                    message_id=updated_rec.message_id,
                    text=notification_text
                )
            except Exception as e:
                log.warning("Failed to send closure notification for rec #%s: %s", rec_id, e)
        # --- END OF NEW LOGIC ---
        
        log.info("Rec #%s closed at price=%s (status=%s)", rec.id, exit_price, updated_rec.status.value)
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
        log.info("Rec #%s partial close note added", rec.id)
        return updated_rec

    def update_sl(self, rec_id: int, new_sl: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED:
            raise ValueError("Recommendation not found or is closed.")
        self._validate_sl_vs_entry(rec.side.value, rec.entry.value, new_sl)
        rec.stop_loss = Price(new_sl)
        
        is_move_to_be = (new_sl == rec.entry.value)
        note_text = (
            "\n- تم نقل وقف الخسارة إلى نقطة الدخول."
            if is_move_to_be
            else f"\n- تم تحديث وقف الخسارة إلى {new_sl}."
        )
        rec.notes = (rec.notes or "") + note_text
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        
        # ✅ --- NEW: Send a notification if SL was moved to BE ---
        if is_move_to_be and updated_rec.channel_id and updated_rec.message_id:
            asset = updated_rec.asset.value
            notification_text = (
                f"<b>🛡️ تأمين صفقة #{asset}</b>\n"
                f"تم نقل وقف الخسارة إلى نقطة الدخول. لا مخاطرة في هذه الصفقة بعد الآن."
            )
            try:
                self.notifier.post_notification_reply(
                    chat_id=updated_rec.channel_id,
                    message_id=updated_rec.message_id,
                    text=notification_text
                )
            except Exception as e:
                log.warning("Failed to send SL-to-BE notification for rec #%s: %s", rec_id, e)
        # --- END OF NEW LOGIC ---

        log.info("Rec #%s SL updated to %s", rec.id, new_sl)
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
        log.info("Rec #%s targets updated to [%s]", rec.id, targets_str)
        return updated_rec

    def get_recent_assets_for_user(self, user_id: str, limit: int = 5) -> List[str]:
        return self.repo.get_recent_assets_for_user(user_id, limit)
# --- END OF FILE: src/capitalguard/application/services/trade_service.py ---