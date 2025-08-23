# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Iterable, Optional
import os
import html
import requests

TELEGRAM_API = "https://api.telegram.org"


class TelegramNotifier:
    """
    مرسل تيليجرام بسيط باستخدام HTTP (بدون مكتبات ثقيلة).
    يدعم الدوال:
      - send_recommendation
      - send_close
      - send_report
    ويمكن استدعاء publish(text) للتوافق الخلفي.
    """

    def __init__(self, bot_token: Optional[str] = None, default_chat_id: Optional[int] = None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.default_chat_id = default_chat_id or int(os.getenv("TELEGRAM_CHANNEL_ID", "0") or "0")
        if not self.bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    # ---------- Low-level ----------
    def _send(self, text: str, chat_id: Optional[int] = None, disable_web_page_preview: bool = True) -> None:
        cid = chat_id or self.default_chat_id
        if not cid:
            # لا نكسر المنطق إن لم يُعطَ chat_id — فقط نتجاهل الإرسال.
            return
        url = f"{TELEGRAM_API}/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": cid,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": disable_web_page_preview,
        }
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception:
            # لا نفجّر الخدمة عند فشل الإرسال
            pass

    # ---------- Public API ----------
    def send_recommendation(
        self,
        rec_id: int,
        asset: str,
        side: str,
        entry: float,
        stop_loss: float,
        targets: Iterable[float],
        notes: Optional[str] = None,
        chat_id: Optional[int] = None,
    ) -> None:
        # تنسيق “البطاقة” كما طلبت
        # مثال: / Futures / SHORT  + هاشتاقات متكررة للأصل
        tags = f"#{asset} #{asset} #Futures #{side.capitalize()}"
        header = f"┌────────────────────────┐\n│ 📣 Trade Signal — #REC{rec_id:04d} │  {tags}\n└────────────────────────┘"

        tps_str = " • ".join([f"{t:g}" for t in targets])
        body = (
            f"💎 <b>Symbol</b> : <code>{html.escape(asset)}</code>\n"
            f"📌 <b>Type</b>   : <code>Futures / {side.upper()}</code>\n"
            f"────────────────────────\n"
            f"💰 <b>Entry</b>  : <code>{entry:g}</code>\n"
            f"🛑 <b>SL</b>     : <code>{stop_loss:g}</code>\n\n"
            f"🎯 <b>TPs</b>   : {tps_str}\n"
            f"────────────────────────\n"
            f"📊 <b>R/R</b>   : -\n"
            f"📝 <b>Notes</b> : {html.escape(notes) if notes else '-'}\n\n"
            f"(Disclaimer: Not financial advice. Manage your risk.)\n\n"
            f"🔗 <i>Futures Watcher Bot</i>  |  📣 <i>Official Channel</i>  |  📬 <i>Contact</i>"
        )
        self._send(f"{header}\n{body}", chat_id=chat_id)

    def send_close(
        self,
        rec_id: int,
        asset: str,
        exit_price: float,
        pnl_pct: Optional[float] = None,
        chat_id: Optional[int] = None,
    ) -> None:
        pnl_text = f"{pnl_pct:+.2f}%" if pnl_pct is not None else "-"
        msg = (
            f"✅ <b>Closed</b> — #REC{rec_id:04d}\n"
            f"🔸 <b>Symbol</b>: <code>{html.escape(asset)}</code>\n"
            f"🔸 <b>Exit</b>  : <code>{exit_price:g}</code>\n"
            f"🔸 <b>PNL</b>   : {pnl_text}"
        )
        self._send(msg, chat_id=chat_id)

    def send_report(self, total: int, open_cnt: int, closed_cnt: int, most_asset: Optional[str]) -> None:
        msg = (
            "📈 <b>تقرير مختصر</b>\n"
            f"• <b>إجمالي التوصيات</b>: <code>{total}</code>\n"
            f"• <b>المفتوحة</b>: <code>{open_cnt}</code> | <b>المغلقة</b>: <code>{closed_cnt}</code>\n"
            f"• <b>أكثر أصل تكرارًا</b>: <code>{most_asset or '-'} </code>"
        )
        self._send(msg)

    # ---------- Backward compatibility ----------
    def publish(self, text_or_rec):
        # دعم publish القديم لو موجود استدعاءات قديمة
        if isinstance(text_or_rec, str):
            self._send(text_or_rec)
            return
        try:
            rec = text_or_rec
            self.send_recommendation(
                rec_id=rec.id,
                asset=rec.asset.value,
                side=rec.side.value,
                entry=rec.entry.value,
                stop_loss=rec.stop_loss.value,
                targets=rec.targets.values,
                notes=None,
                chat_id=getattr(rec, "channel_id", None),
            )
        except Exception:
            pass