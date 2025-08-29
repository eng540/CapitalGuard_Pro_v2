# --- START OF FILE: src/capitalguard/infrastructure/notify/telegram.py ---
import logging
from typing import Optional, Any, Dict, Tuple
import httpx

from capitalguard.config import settings
from capitalguard.domain.entities import Recommendation
from capitalguard.interfaces.telegram.ui_texts import RecCard

class TelegramNotifier:
    def __init__(self, token: Optional[str] = None):
        self.token = (token or settings.TELEGRAM_BOT_TOKEN or "").strip()
        if not self.token:
            logging.warning("TelegramNotifier: TELEGRAM_BOT_TOKEN is missing.")

    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def _post(self, method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.token:
            return None
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(self._api(method), json=payload)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logging.error("Telegram API Error (%s): %s", method, data)
                return None
            return data.get("result")
        except Exception as e:
            logging.exception("Telegram sending exception (%s): %s", method, e)
        return None

    def _build_card_text(self, rec: Recommendation) -> str:
        card = RecCard(
            id=rec.id or 0,
            asset=str(rec.asset.value if hasattr(rec.asset, "value") else rec.asset),
            side=str(rec.side.value if hasattr(rec.side, "value") else rec.side),
            status=str(rec.status),
            entry=float(rec.entry.value if hasattr(rec.entry, "value") else rec.entry),
            stop_loss=float(rec.stop_loss.value if hasattr(rec.stop_loss, "value") else rec.stop_loss),
            targets=list(rec.targets.values if hasattr(rec.targets, "values") else (rec.targets or [])),
            exit_price=rec.exit_price,
        )
        return card.to_text()

    def _build_actions_keyboard(self, rec: Recommendation) -> Dict[str, Any]:
        if str(rec.status).upper() == "CLOSED":
            return {"inline_keyboard": [[{"text": "📜 السجل", "callback_data": f"rec:history:{rec.id}"}]]}
        return {
            "inline_keyboard": [
                [
                    {"text": "🛑 إغلاق الآن", "callback_data": f"rec:close:{rec.id}"},
                    {"text": "🎯 تعديل الأهداف", "callback_data": f"rec:amend_tp:{rec.id}"},
                    {"text": "🛡️ تعديل SL", "callback_data": f"rec:amend_sl:{rec.id}"},
                ],
                [{"text": "📜 السجل", "callback_data": f"rec:history:{rec.id}"}],
            ]
        }

    def post_recommendation_card(self, rec: Recommendation) -> Optional[Tuple[int, int]]:
        chat_id = settings.TELEGRAM_CHAT_ID
        if not chat_id:
            logging.warning("TelegramNotifier.post_recommendation_card: TELEGRAM_CHAT_ID not set.")
            return None

        payload = {
            "chat_id": str(chat_id),
            "text": self._build_card_text(rec),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": self._build_actions_keyboard(rec),
        }
        result = self._post("sendMessage", payload)
        if not result:
            return None
        channel_id = int(result["chat"]["id"])
        message_id = int(result["message_id"])
        return channel_id, message_id

    def edit_recommendation_card(self, rec: Recommendation) -> bool:
        if not rec.channel_id or not rec.message_id:
            return False
        payload = {
            "chat_id": int(rec.channel_id),
            "message_id": int(rec.message_id),
            "text": self._build_card_text(rec),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": self._build_actions_keyboard(rec),
        }
        result = self._post("editMessageText", payload)
        return bool(result)

    def send_message(self, text: str, chat_id: Optional[str | int] = None, parse_mode: str = "HTML") -> None:
        target_chat_id = chat_id or settings.TELEGRAM_CHAT_ID
        if not target_chat_id:
            logging.warning("TelegramNotifier: No chat_id provided and TELEGRAM_CHAT_ID is not set.")
            return
        payload = {
            "chat_id": str(target_chat_id),
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        self._post("sendMessage", payload)
# --- END OF FILE ---