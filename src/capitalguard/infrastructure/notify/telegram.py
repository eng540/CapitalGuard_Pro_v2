from __future__ import annotations
import os
import math
import html
from typing import List, Optional

import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DEFAULT_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")  # يمكن تمرير channel_id مع التوصية

# Telegram MarkdownV2 يحتاج هروب محارف خاصة
_MD_V2_SPECIAL = r'_*[]()~`>#+-=|{}.!'

def _esc(s: str) -> str:
    return ''.join('\\' + c if c in _MD_V2_SPECIAL else c for c in s)

def _fmt_pct(p: float) -> str:
    sign = "+" if p >= 0 else ""
    return f"{sign}{p:.2f}%"

def _rr_ratio(entry: float, sl: float, targets: List[float], side: str) -> Optional[float]:
    """حساب R/R بسيط: المسافة للهدف الأول على المسافة لوقف الخسارة."""
    try:
        if side.upper() == "LONG":
            risk = abs(entry - sl)
            reward = abs(targets[0] - entry)
        else:  # SHORT
            risk = abs(sl - entry)
            reward = abs(entry - targets[0])
        if risk == 0:
            return None
        return reward / risk
    except Exception:
        return None

def _tp_percents(entry: float, tps: List[float], side: str) -> List[float]:
    """نسبة الربح لكل هدف مقارنة بسعر الدخول."""
    percs = []
    for tp in tps:
        if side.upper() == "LONG":
            percs.append((tp - entry) / entry * 100.0)
        else:
            percs.append((entry - tp) / entry * 100.0)
    return percs

class TelegramNotifier:
    API_BASE = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, token: str | None = None, default_chat_id: str | None = None):
        self.token = token or TELEGRAM_BOT_TOKEN
        self.default_chat_id = default_chat_id or DEFAULT_CHAT_ID

    # --- إرسال عام ---
    def _send(self, text: str, chat_id: Optional[int | str] = None, reply_markup: dict | None = None):
        if not self.token:
            return
        payload = {
            "chat_id": chat_id or self.default_chat_id,
            "text": text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        url = self.API_BASE.format(token=self.token, method="sendMessage")
        try:
            requests.post(url, json=payload, timeout=15).raise_for_status()
        except Exception:
            # لا نرفع الاستثناء حتى لا يُفشل المنطق الأساسي للتداول
            pass

    # --- أزرار سفلية (روابط/أوامر) ---
    def _cta_keyboard(self) -> dict:
        # عدّل الروابط حسب قنواتك/بوتك
        return {
            "inline_keyboard": [
                [
                    {"text": "🔗 Futures Watcher Bot", "url": "https://t.me/your_futures_bot"},
                    {"text": "📣 Official Channel", "url": "https://t.me/your_channel"},
                ],
                [
                    {"text": "📬 Contact", "url": "https://t.me/your_support"},
                ]
            ]
        }

    # --- رسائل توصية/إغلاق/تقرير ---
    def send_recommendation(
        self,
        rec_id: int,
        asset: str,
        side: str,
        entry: float,
        stop_loss: float,
        targets: List[float],
        notes: str | None = None,
        chat_id: Optional[int | str] = None,
    ):
        # الحسابات
        tps_pct = _tp_percents(entry, targets, side)
        rr = _rr_ratio(entry, stop_loss, targets, side)

        # تهيئة أقسام التنسيق
        tag_asset = f"#{_esc(asset)}"
        hdr = (
            f"┌────────────────────────┐\n"
            f"│ 📣 {_esc('Trade Signal')} — #{_esc(f'REC{rec_id:04d}')} │  "
            f"{tag_asset} {tag_asset} {_esc('#Futures')} #{_esc(side.capitalize())}\n"
            f"└────────────────────────┘"
        )

        body_top = (
            f"💎 {_esc('Symbol')} : {_esc(asset)}\n"
            f"📌 {_esc('Type')}   : {_esc('Futures')} / {_esc(side.upper())}\n"
            f"────────────────────────\n"
            f"💰 {_esc('Entry')}  : {_esc(str(entry))}\n"
            f"🛑 {_esc('SL')}     : {_esc(str(stop_loss))}\n\n"
        )

        # قائمة الأهداف مع النِّسَب
        tps_lines = []
        for i, (tp, pc) in enumerate(zip(targets, tps_pct), start=1):
            tps_lines.append(f"{i}) {_esc(str(tp))} ({_esc(_fmt_pct(pc))})")
        tps_block = "🎯 " + _esc("TPs") + "   : " + " • ".join(tps_lines) + "\n\n"

        body_mid = "────────────────────────\n"
        rr_text = "-" if rr is None else f"{rr:.2f}"
        body_mid += f"📊 {_esc('R/R')}   : {_esc(rr_text)}\n"
        body_mid += f"📝 {_esc('Notes')} : {_esc(notes or '—')}\n\n"

        disclaimer = _esc("(Disclaimer: Not financial advice. Manage your risk.)")

        # روابط أسفل الرسالة عبر أزرار
        text = "\n".join([hdr, body_top, tps_block, body_mid, disclaimer])

        self._send(text, chat_id=chat_id, reply_markup=self._cta_keyboard())

    def send_close(
        self,
        rec_id: int,
        asset: str,
        exit_price: float,
        pnl_pct: Optional[float] = None,
        chat_id: Optional[int | str] = None,
    ):
        pnl_str = f"{_fmt_pct(pnl_pct)}" if pnl_pct is not None else "-"
        text = (
            f"✅ {_esc('Position Closed')} — #{_esc(f'REC{rec_id:04d}')}\n"
            f"🔔 {_esc('Symbol')} : {_esc(asset)}\n"
            f"💸 {_esc('Exit')}   : {_esc(str(exit_price))}\n"
            f"📈 {_esc('PnL')}    : {_esc(pnl_str)}"
        )
        self._send(text, chat_id=chat_id, reply_markup=self._cta_keyboard())

    def send_report(
        self,
        total: int,
        open_count: int,
        closed_count: int,
        top_asset: Optional[str],
        chat_id: Optional[int | str] = None,
    ):
        text = (
            f"📊 {_esc('Summary Report')}\n"
            f"• {_esc('Total')}   : {_esc(str(total))}\n"
            f"• {_esc('Open')}    : {_esc(str(open_count))}\n"
            f"• {_esc('Closed')}  : {_esc(str(closed_count))}\n"
            f"• {_esc('Top Asset')}: {_esc(top_asset or '-')} "
        )
        self._send(text, chat_id=chat_id, reply_markup=self._cta_keyboard())