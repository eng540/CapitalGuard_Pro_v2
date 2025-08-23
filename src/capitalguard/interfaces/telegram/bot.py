# -*- coding: utf-8 -*-
from __future__ import annotations
import os
import re
import json
import time
import requests
from typing import Dict, Tuple, List, Optional

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
BOT_CHAT_ID = os.getenv("TELEGRAM_CHANNEL_ID")  # ليس ضروريًا هنا
API_BASE = os.getenv("PUBLIC_API_BASE", "http://localhost:8080")  # مثال: https://xxx.up.railway.app
API_KEY = os.getenv("API_KEY", "")

TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


def _tg_send(chat_id: int, text: str) -> None:
    try:
        requests.post(
            f"{TG_URL}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass


def _auth_headers() -> Dict[str, str]:
    return {"x-api-key": API_KEY} if API_KEY else {}


# ---------- Parser مرن لأمر /newrec ----------
def parse_newrec(text: str) -> Tuple[str, str, float, float, List[float], Optional[str]]:
    """
    يدعم:
      /newrec BTCUSDT LONG 117000 116000 119000,120000
      /newrec asset=BTCUSDT side=LONG entry=117000 sl=116000 targets=119000,120000 notes=Good
    يعيد: (asset, side, entry, sl, targets[], notes)
    """
    t = text.strip()
    # أولوية لصيغة key=value
    if "=" in t:
        kv = {}
        for part in t.split():
            if "=" in part:
                k, v = part.split("=", 1)
                kv[k.strip().lower()] = v.strip()
        asset = kv["asset"].upper()
        side = kv["side"].upper()
        entry = float(kv["entry"])
        sl = float(kv.get("sl") or kv.get("stop_loss"))
        targets = [float(x) for x in kv["targets"].split(",") if x]
        notes = kv.get("notes")
        return asset, side, entry, sl, targets, notes

    # صيغة مسافات
    parts = t.split()
    # مثال: ['/newrec', 'BTCUSDT', 'LONG', '117000', '116000', '119000,120000']
    if len(parts) < 6:
        raise ValueError("bad-format")
    _, asset, side, entry, sl, targets = parts[:6]
    asset = asset.upper()
    side = side.upper()
    entry = float(entry)
    sl = float(sl)
    targets_arr = [float(x) for x in targets.split(",") if x]
    return asset, side, entry, sl, targets_arr, None


# ---------- Polling بسيط (بدون Webhook لتجنّب تعقيد DNS) ----------
def run_polling():
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")
    offset = 0
    while True:
        try:
            r = requests.get(f"{TG_URL}/getUpdates", params={"timeout": 30, "offset": offset + 1}, timeout=40)
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
                    _tg_send(chat_id, "👋 أهلاً بك في CapitalGuard Bot.\nاستخدم /help للمساعدة.")
                    continue

                if text.startswith("/help"):
                    _tg_send(chat_id,
                             "الأوامر:\n"
                             "/newrec <asset> <side> <entry> <sl> <t1,t2,...>\n"
                             "أو: /newrec asset=... side=... entry=... sl=... targets=...\n"
                             "/close <id> <exit_price>\n"
                             "/report")
                    continue

                # /newrec
                if text.startswith("/newrec"):
                    try:
                        payload = text[len("/newrec"):].strip()
                        asset, side, entry, sl, targets, notes = parse_newrec(payload)
                        res = requests.post(
                            f"{API_BASE}/recommendations",
                            headers=_auth_headers(),
                            json={
                                "asset": asset, "side": side,
                                "entry": entry, "stop_loss": sl,
                                "targets": targets,
                                "channel_id": chat_id, "user_id": chat_id
                            },
                            timeout=15,
                        )
                        if res.status_code == 200:
                            rec = res.json()
                            _tg_send(chat_id, f"✅ تم إنشاء توصية: ID=<code>{rec['id']}</code>")
                        else:
                            _tg_send(chat_id, f"⚠️ فشل إنشاء التوصية: <code>{res.text}</code>")
                    except Exception as e:
                        _tg_send(chat_id, f"⚠️ تنسيق الأمر غير صحيح: {e}")
                    continue

                # /close
                if text.startswith("/close"):
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
                    try:
                        res = requests.get(f"{API_BASE}/report", headers=_auth_headers(), timeout=15)
                        if res.status_code == 200:
                            rep = res.json()
                            msg = (
                                "📈 تقرير مختصر\n"
                                f"• إجمالي التوصيات: {rep['total']}\n"
                                f"• المفتوحة: {rep['open']} | المغلقة: {rep['closed']}\n"
                                f"• أكثر أصل تكرارًا: {rep.get('top_asset') or '-'}"
                            )
                            _tg_send(chat_id, msg)
                        else:
                            _tg_send(chat_id, f"⚠️ فشل جلب التقرير: <code>{res.text}</code>")
                    except Exception as e:
                        _tg_send(chat_id, f"⚠️ خطأ: {e}")
                    continue
        except Exception:
            # ننتظر قليلاً ثم نحاول مجددًا لتفادي crash-loop
            time.sleep(3)