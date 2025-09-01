from __future__ import annotations
import os, json, logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

# نحاول استخدام requests لأنّه الأكثر شيوعًا
try:
    import requests
except Exception:  # احتياط
    requests = None

from capitalguard.config import settings
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text

log = logging.getLogger(__name__)

def _bool_env(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")

@dataclass
class TelegramNotifier:
    """
    طبقة نشر/تحديث رسائل القناة على Telegram.
    واجهة متوافقة مع احتياجات الخدمات:
      - post_recommendation_card(rec)  -> نشر بطاقة جديدة
      - publish_or_update(rec)         -> تحديث الرسالة إن أمكن، وإلا إعادة نشر مع (Updated)
      - _post(method, payload)         -> استدعاء منخفض المستوى (مستخدم من AlertService)
    """
    token: str = settings.TELEGRAM_BOT_TOKEN
    chat_id: int = int(getattr(settings, "TELEGRAM_CHAT_ID", "0") or 0)
    parse_mode: str = "HTML"

    def __post_init__(self):
        if not self.token:
            log.warning("TelegramNotifier: BOT token is not set.")
        if not self.chat_id:
            log.warning("TelegramNotifier: TELEGRAM_CHAT_ID is not set.")
        self.base = f"https://api.telegram.org/bot{self.token}"

    # -------- Low-level HTTP call (sync) --------
    def _post(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        استدعاء مباشر لـ Telegram HTTP API.
        يُعاد قاموس {ok: bool, result: {...}} أو {ok: False, description: "..."}.
        """
        if not self.token:
            return {"ok": False, "description": "bot token not set"}
        if method.startswith("http"):
            url = method
        else:
            url = f"{self.base}/{method}"
        try:
            if requests is None:
                # fallback بسيط عبر urllib (في حالات نادرة)
                import urllib.request
                req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            else:
                data = requests.post(url, json=payload, timeout=15).json()
            if not data.get("ok"):
                log.warning("Telegram API error (%s): %s", method, data)
            return data
        except Exception as e:
            log.warning("Telegram API request failed (%s): %s", method, e)
            return {"ok": False, "description": str(e)}

    # -------- High-level helpers --------
    def _render_text(self, rec) -> str:
        try:
            return build_trade_card_text(rec)
        except Exception as e:
            log.warning("build_trade_card_text failed: %s", e)
            # fallback نصّي
            asset = getattr(rec.asset, "value", rec.asset)
            side  = getattr(rec.side, "value", rec.side)
            entry = float(getattr(rec.entry, "value", rec.entry))
            sl    = float(getattr(rec.stop_loss, "value", rec.stop_loss))
            return f"<b>{asset}</b> — {side}\nEntry: {entry:g}\nSL: {sl:g}"

    # نشر بطاقة توصية جديدة في القناة
    def post_recommendation_card(self, rec) -> Dict[str, Any]:
        """
        يُستخدم من TradeService.create / محادثة النشر.
        يعيد: {"ok": True, "chat_id": int, "message_id": int} عند النجاح.
        """
        if not self.chat_id:
            return {"ok": False, "msg": "TELEGRAM_CHAT_ID not set"}
        text = self._render_text(rec)
        resp = self._post("sendMessage", {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": self.parse_mode,
            "disable_web_page_preview": True,
        })
        if resp.get("ok") and resp.get("result"):
            msg = resp["result"]
            return {"ok": True, "chat_id": int(msg["chat"]["id"]), "message_id": int(msg["message_id"])}
        return {"ok": False, "msg": resp.get("description") or "sendMessage failed"}

    # محاولة تحرير البطاقة القائمة، وإلا إعادة نشر "(Updated)"
    def publish_or_update(self, rec) -> Dict[str, Any]:
        """
        يُستخدم بعد تعديل/إغلاق: يحرّر الرسالة إن كانت متاحة،
        وإذا فشل التحرير (قدم الرسالة/صلاحيات)، يُعاد نشر نسخة جديدة موسومة (Updated).
        """
        if not self.chat_id:
            return {"ok": False, "msg": "TELEGRAM_CHAT_ID not set"}
        text = self._render_text(rec)
        channel_id = int(getattr(rec, "channel_id", 0) or 0)
        message_id = int(getattr(rec, "message_id", 0) or 0)

        # لو لدينا مرجع الرسالة، نجرب التعديل
        if channel_id and message_id:
            edit = self._post("editMessageText", {
                "chat_id": channel_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": self.parse_mode,
                "disable_web_page_preview": True,
            })
            if edit.get("ok"):
                return {"ok": True, "chat_id": channel_id, "message_id": message_id, "edited": True}
            # إن فشل، نكمل بإعادة النشر

        # إعادة نشر نسخة محدّثة
        resp = self._post("sendMessage", {
            "chat_id": self.chat_id,
            "text": f"(Updated)\n{text}",
            "parse_mode": self.parse_mode,
            "disable_web_page_preview": True,
        })
        if resp.get("ok") and resp.get("result"):
            msg = resp["result"]
            return {"ok": True, "chat_id": int(msg["chat"]["id"]), "message_id": int(msg["message_id"]), "reposted": True}
        return {"ok": False, "msg": resp.get("description") or "update/repost failed"}

    # مساعدة اختيارية: حذف رسالة (نادراً)
    def delete_message(self, chat_id: int, message_id: int) -> Dict[str, Any]:
        return self._post("deleteMessage", {"chat_id": chat_id, "message_id": message_id})