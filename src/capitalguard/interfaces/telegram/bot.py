# src/capitalguard/interfaces/telegram/bot.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import time
import json
import requests
from typing import Dict, Tuple, List, Optional

# ========= الإعدادات (من متغيرات البيئة) =========
BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN") or ""
API_BASE    = os.getenv("PUBLIC_API_BASE", "http://127.0.0.1:8080").rstrip("/")
API_KEY     = os.getenv("API_KEY", "")
TG_URL      = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ========= أدوات مساعدة =========
def _auth_headers() -> Dict[str, str]:
    return {"x-api-key": API_KEY} if API_KEY else {}

def _tg_send(chat_id: int, text: str) -> None:
    """إرسال رسالة تيليغرام مع HTML parse."""
    try:
        requests.post(
            f"{TG_URL}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=12,
        )
    except Exception:
        pass

def _tg_typing(chat_id: int) -> None:
    try:
        requests.post(f"{TG_URL}/sendChatAction",
                      json={"chat_id": chat_id, "action": "typing"},
                      timeout=6)
    except Exception:
        pass

# ========= تنسيق الرسائل =========
def _fmt_signal(asset: str, side: str, entry: float, sl: float,
                targets: List[float], rec_id: Optional[int] = None,
                notes: Optional[str] = None) -> str:
    """
    تنسيق التوصية كما طلبت.
    مثال الرأس:  ┌────────────────────────┐
                  │ 📣 Trade Signal — #REC0021 │  #ADAUSDT #Futures #Short
                  └────────────────────────┘
    """
    side_up = side.upper()
    is_short = "SHORT" in side_up
    hash_tags = f"#{asset} #{asset} #Futures #{'Short' if is_short else 'Long'}"

    # حساب نسب الأهداف إن وُجدت
    tps_parts = []
    for t in targets:
        if t is None:
            continue
        try:
            pct = (abs(t - entry) / entry) * 100.0
            tps_parts.append(f"{t:g} ({'+' if ((t-entry)*(1 if not is_short else -1))>=0 else ''}{pct:.2f}%)")
        except Exception:
            tps_parts.append(f"{t:g}")

    header_id = f"#REC{rec_id:04d}" if rec_id is not None else ""
    header = (
        "┌────────────────────────┐\n"
        f"│ 📣 Trade Signal — {header_id} │  {hash_tags}\n"
        "└────────────────────────┘"
    )

    body = [
        f"💎 <b>Symbol</b> : {asset}",
        f"📌 <b>Type</b>   : Futures / {side_up}",
        "────────────────────────",
        f"💰 <b>Entry</b>  : {entry:g}",
        f"🛑 <b>SL</b>     : {sl:g}",
        "",
        ("🎯 <b>TPs</b>   : " + " • ".join(tps_parts)) if tps_parts else "🎯 <b>TPs</b>   : -",
        "",
        "────────────────────────",
        "📊 <b>R/R</b>   : -",
        f"📝 <b>Notes</b> : {notes or '-'}",
        "",
        "(Disclaimer: Not financial advice. Manage your risk.)",
        "",
        "🔗 <a href=\"https://t.me/\">Futures Watcher Bot</a>  |  "
        "📣 <a href=\"https://t.me/\">Official Channel</a>  |  "
        "📬 <a href=\"https://t.me/\">Contact</a>",
    ]
    return header + "\n" + "\n".join(body)

# ========= Parser لأمر /newrec =========
def parse_newrec(text: str) -> Tuple[str, str, float, float, List[float], Optional[str]]:
    """
    يدعم:
      /newrec BTCUSDT LONG 117000 116000 119000,120000 [ملاحظات حرة...]
      /newrec asset=BTCUSDT side=LONG entry=117000 sl=116000 targets=119000,120000 notes=... 
    يعيد: (asset, side, entry, sl, targets[], notes)
    """
    t = text.strip()

    # 1) صيغة key=value
    if "=" in t:
        kv: Dict[str, str] = {}
        # نقسم على المسافات مع الحفاظ على notes التي قد تحتوي فراغات
        parts = re.findall(r'(\w+=("[^"]+"|\S+))', t)
        if parts:
            for p, _ in parts:
                k, v = p.split("=", 1)
                v = v.strip().strip('"')
                kv[k.lower()] = v
        else:
            # تقسيم بسيط احتياطي
            for part in t.split():
                if "=" in part:
                    k, v = part.split("=", 1)
                    kv[k.strip().lower()] = v.strip().strip('"')

        asset   = kv["asset"].upper()
        side    = kv["side"].upper()
        entry   = float(kv["entry"])
        sl      = float(kv.get("sl") or kv.get("stop_loss"))
        targets = [float(x) for x in kv["targets"].split(",") if x]
        notes   = kv.get("notes")
        return asset, side, entry, sl, targets, notes

    # 2) صيغة مسافات: /newrec BTCUSDT LONG 117000 116000 119000,120000 [notes...]
    parts = t.split()
    if len(parts) < 6:
        raise ValueError("bad-format")
    _, asset, side, entry, sl, targets = parts[:6]
    notes = " ".join(parts[6:]) if len(parts) > 6 else None

    return (
        asset.upper(),
        side.upper(),
        float(entry),
        float(sl),
        [float(x) for x in targets.split(",") if x],
        notes,
    )

# ========= حلقة Polling =========
def run_polling():
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")
    offset = 0

    while True:
        try:
            r = requests.get(
                f"{TG_URL}/getUpdates",
                params={"timeout": 30, "offset": offset + 1},
                timeout=40,
            )
            data = r.json()
            for upd in data.get("result", []):
                offset = upd["update_id"]
                msg = upd.get("message") or {}
                chat_id = msg.get("chat", {}).get("id")
                text = (msg.get("text") or "").strip()

                if not chat_id or not text:
                    continue

                # /start
                if text.startswith("/start"):
                    _tg_send(
                        chat_id,
                        "👋 أهلاً بك في <b>CapitalGuard Bot</b>.\n"
                        "استخدم /help لعرض المساعدة.",
                    )
                    continue

                # /help
                if text.startswith("/help"):
                    _tg_send(
                        chat_id,
                        "الأوامر:\n"
                        "• /newrec <asset> <side> <entry> <sl> <t1,t2,...> [notes]\n"
                        "  أو /newrec asset=... side=... entry=... sl=... targets=... notes=\"...\"\n"
                        "• /close <id> <exit_price>\n"
                        "• /report",
                    )
                    continue

                # /newrec
                if text.startswith("/newrec"):
                    _tg_typing(chat_id)
                    try:
                        payload = text[len("/newrec"):].strip()
                        asset, side, entry, sl, targets, notes = parse_newrec(payload)

                        res = requests.post(
                            f"{API_BASE}/recommendations",
                            headers=_auth_headers(),
                            json={
                                "asset": asset,
                                "side": side,
                                "entry": entry,
                                "stop_loss": sl,
                                "targets": targets,
                                "channel_id": chat_id,
                                "user_id": chat_id,
                            },
                            timeout=15,
                        )

                        if res.status_code == 200:
                            rec = res.json()
                            # إرسال التوصية بالتنسيق الجميل
                            msg = _fmt_signal(
                                asset=asset,
                                side=side,
                                entry=entry,
                                sl=sl,
                                targets=targets,
                                rec_id=rec.get("id"),
                                notes=notes,
                            )
                            _tg_send(chat_id, msg)
                        else:
                            _tg_send(chat_id, f"⚠️ فشل إنشاء التوصية: <code>{res.text}</code>")
                    except Exception as e:
                        _tg_send(chat_id, f"⚠️ تنسيق الأمر غير صحيح: {e}")
                    continue

                # /close
                if text.startswith("/close"):
                    _tg_typing(chat_id)
                    try:
                        parts = text.split()
                        if len(parts) != 3:
                            raise ValueError("الاستخدام: /close <id> <exit_price>")
                        rec_id = int(parts[1])
                        exit_price = float(parts[2])
                        res = requests.post(
                            f"{API_BASE}/recommendations/{rec_id}/close",
                            headers=_auth_headers(),
                            json={"exit_price": exit_price},
                            timeout=15,
                        )
                        if res.status_code == 200:
                            _tg_send(chat_id, f"✅ Closed ID={rec_id}")
                        else:
                            _tg_send(chat_id, f"⚠️ لم يتم الإغلاق: <code>{res.text}</code>")
                    except Exception as e:
                        _tg_send(chat_id, f"⚠️ تنسيق الأمر غير صحيح: {e}")
                    continue

                # /report
                if text.startswith("/report"):
                    _tg_typing(chat_id)
                    try:
                        res = requests.get(f"{API_BASE}/report",
                                           headers=_auth_headers(), timeout=15)
                        if res.status_code == 200:
                            rep = res.json()
                            msg = (
                                "📈 <b>تقرير مختصر</b>\n"
                                f"• إجمالي التوصيات: {rep.get('total', 0)}\n"
                                f"• المفتوحة: {rep.get('open', 0)} | المغلقة: {rep.get('closed', 0)}\n"
                                f"• أكثر أصل تكرارًا: {rep.get('top_asset') or '-'}"
                            )
                            _tg_send(chat_id, msg)
                        else:
                            _tg_send(chat_id, f"⚠️ فشل جلب التقرير: <code>{res.text}</code>")
                    except Exception as e:
                        _tg_send(chat_id, f"⚠️ خطأ: {e}")
                    continue

        except Exception:
            # لا ننهار، نمهل قليلاً ونعيد المحاولة
            time.sleep(3)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    run_polling()

if __name__ == "__main__":
    main()