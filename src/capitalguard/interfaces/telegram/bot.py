# -*- coding: utf-8 -*-
from __future__ import annotations
import os, time, requests
from typing import Dict, Tuple, List, Optional
from capitalguard.interfaces.formatting.telegram_templates import format_signal, format_report

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
API_KEY = os.getenv("API_KEY", "")

_public = os.getenv("PUBLIC_API_BASE")
if _public:
    API_BASE = _public.rstrip("/")
else:
    port = os.getenv("PORT", "8080")
    API_BASE = f"http://127.0.0.1:{port}"

def _tg_send(chat_id: int, text: str) -> None:
    if not BOT_TOKEN:
        return
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

def parse_newrec(payload: str) -> Tuple[str, str, float, float, List[float], Optional[str]]:
    t = payload.strip()
    if "=" in t:
        kv: Dict[str, str] = {}
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

    parts = t.split()
    if len(parts) < 5:
        raise ValueError("bad-format")
    # /newrec BTCUSDT LONG 117000 116000 119000,120000
    asset, side, entry, sl = parts[0], parts[1], parts[2], parts[3]
    targets = parts[4] if len(parts) > 4 else ""
    asset = asset.upper()
    side = side.upper()
    entry = float(entry)
    sl = float(sl)
    targets_arr = [float(x) for x in targets.split(",") if x]
    return asset, side, entry, sl, targets_arr, None

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
                            # إرسال القالب الجميل للمستخدم مباشرة (اختياري)
                            try:
                                txt = format_signal(
                                    rec_id=rec["id"], symbol=rec["asset"], side=rec["side"],
                                    entry=rec["entry"], sl=rec["stop_loss"], targets=rec["targets"], notes=notes
                                )
                                _tg_send(chat_id, txt)
                            except Exception:
                                _tg_send(chat_id, f"✅ تم إنشاء توصية: ID=<code>{rec['id']}</code>")
                        else:
                            _tg_send(chat_id, f"⚠️ فشل إنشاء التوصية: <code>{res.text}</code>")
                    except Exception as e:
                        _tg_send(chat_id, f"⚠️ تنسيق الأمر غير صحيح: {e}")
                    continue

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

                if text.startswith("/report"):
                    try:
                        res = requests.get(f"{API_BASE}/report", headers=_auth_headers(), timeout=15)
                        if res.status_code == 200:
                            rep = res.json()
                            _tg_send(chat_id, format_report(rep["total"], rep["open"], rep["closed"], rep.get("top_asset")))
                        else:
                            _tg_send(chat_id, f"⚠️ فشل جلب التقرير: <code>{res.text}</code>")
                    except Exception as e:
                        _tg_send(chat_id, f"⚠️ خطأ: {e}")
                    continue
        except Exception:
            time.sleep(3)

if __name__ == "__main__":
    run_polling()