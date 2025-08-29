# --- START OF FILE: src/capitalguard/infrastructure/notify/telegram.py ---
from __future__ import annotations
from typing import Optional, Tuple, List, Dict, Any

import logging
import requests

from capitalguard.config import settings
from capitalguard.domain.entities import Recommendation
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text

log = logging.getLogger(__name__)


def _channel_keyboard_json(rec_id: int, *, is_open: bool) -> Dict[str, Any]:
    """
    يبني JSON خام لأزرار Inline كما تتوقع Telegram HTTP API بدون الاعتماد على كائنات PTB.
    """
    if is_open:
        inline_keyboard: List[List[Dict[str, str]]] = [
            [
                {"text": "🎯 تعديل الأهداف", "callback_data": f"rec:amend_tp:{rec_id}"},
                {"text": "🛡️ تعديل SL", "callback_data": f"rec:amend_sl:{rec_id}"},
            ],
            [
                {"text": "📜 السجل", "callback_data": f"rec:history:{rec_id}"},
                {"text": "🛑 إغلاق الآن", "callback_data": f"rec:close:{rec_id}"},
            ],
        ]
    else:
        inline_keyboard = [[{"text": "📜 السجل", "callback_data": f"rec:history:{rec_id}"}]]
    return {"inline_keyboard": inline_keyboard}


class TelegramNotifier:
    """
    ناشر ومحرّر بطاقات التوصيات إلى قناة تيليجرام باستخدام Telegram HTTP API (متزامن).
    يعتمد على:
      - TELEGRAM_BOT_TOKEN
      - TELEGRAM_CHAT_ID
    لا يستخدم coroutines ولا PTB داخل هذا الملف لتفادي مشاكل await ودورات الاستيراد.
    """

    def __init__(self) -> None:
        self.bot_token: Optional[str] = settings.TELEGRAM_BOT_TOKEN
        self.channel_id: Optional[int] = (
            int(settings.TELEGRAM_CHAT_ID) if getattr(settings, "TELEGRAM_CHAT_ID", None) else None
        )
        self.api_base = f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else None

    # -------------------------
    # أدوات داخلية
    # -------------------------
    def _post(self, method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.api_base:
            log.warning("TelegramNotifier disabled — missing TELEGRAM_BOT_TOKEN")
            return None
        try:
            resp = requests.post(f"{self.api_base}/{method}", json=payload, timeout=15)
            if resp.status_code != 200:
                log.error("Telegram API error (%s): %s", method, resp.text)
                return None
            data = resp.json()
            if not data.get("ok"):
                log.error("Telegram API not ok (%s): %s", method, data)
                return None
            return data.get("result")
        except Exception:
            log.exception("Telegram API call failed (%s)", method)
            return None

    # -------------------------
    # رسائل عامة
    # -------------------------
    def send_message(self, text: str, chat_id: Optional[int | str] = None) -> Optional[int]:
        """
        يرسل رسالة نصية بسيطة. يعيد message_id عند النجاح.
        """
        target = chat_id or self.channel_id
        if not target:
            log.warning("TelegramNotifier: no chat id to send message")
            return None
        res = self._post(
            "sendMessage",
            {
                "chat_id": target,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        if res and "message_id" in res:
            return int(res["message_id"])
        return None

    # -------------------------
    # بطاقات التوصيات
    # -------------------------
    def post_recommendation_card(self, rec: Recommendation) -> Optional[Tuple[int, int]]:
        """
        ينشر بطاقة توصية إلى القناة ويعيد (channel_id, message_id).
        يُستدعى عند إنشاء توصية جديدة.
        """
        if not self.channel_id:
            log.warning("TelegramNotifier: TELEGRAM_CHAT_ID is not set; skipping publish")
            return None

        text = build_trade_card_text(rec)
        markup = _channel_keyboard_json(rec.id, is_open=str(getattr(rec, "status", "OPEN")).upper() == "OPEN")

        res = self._post(
            "sendMessage",
            {
                "chat_id": self.channel_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": markup,
            },
        )
        if not res:
            return None
        msg_id = int(res.get("message_id", 0)) if "message_id" in res else 0
        return (self.channel_id, msg_id) if msg_id else None

    def edit_recommendation_card(self, rec: Recommendation) -> bool:
        """
        يحرّر البطاقة (إن أمكن) أو يعيد النشر عند الفشل.
        يتطلّب أن تكون rec.channel_id و rec.message_id موجودتين.
        """
        ch_id = getattr(rec, "channel_id", None)
        msg_id = getattr(rec, "message_id", None)
        if not ch_id or not msg_id:
            return False

        text = build_trade_card_text(rec)
        markup = _channel_keyboard_json(rec.id, is_open=str(getattr(rec, "status", "OPEN")).upper() == "OPEN")

        res = self._post(
            "editMessageText",
            {
                "chat_id": ch_id,
                "message_id": msg_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": markup,
            },
        )
        if res:
            return True

        # إذا فشل التحرير (قيود زمنية مثلاً) نحاول إعادة النشر
        posted = self.post_recommendation_card(rec)
        return bool(posted)