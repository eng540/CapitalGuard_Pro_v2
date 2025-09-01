#--- START OF FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
from __future__ import annotations
from typing import List
import logging, re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatType
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, CommandHandler, filters

from capitalguard.config import settings
from .keyboards import control_panel_keyboard
from .ui_texts import build_trade_card_text

log = logging.getLogger(__name__)
_num_re = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")

def _is_private(update: Update) -> bool:
    try:
        return update.effective_chat and update.effective_chat.type == ChatType.PRIVATE
    except Exception:
        return False

def _allowed_user(user_id: int | None) -> bool:
    if user_id is None: return False
    allow_env = (getattr(settings, "TELEGRAM_ALLOWED_USERS", "") or "").strip()
    if not allow_env: return True
    try:
        allowed_ids = {int(x.strip()) for x in allow_env.replace(";", ",").split(",") if x.strip()}
        return user_id in allowed_ids
    except Exception:
        return False

async def _guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not _is_private(update): return False
    uid = update.effective_user.id if update.effective_user else None
    if not _allowed_user(uid):
        await update.effective_message.reply_text("🚫 غير مصرح لك باستخدام هذا البوت.")
        return False
    return True

def S(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.application.bot_data.get("services", {})

def _parse_float(s: str):
    try:
        m = _num_re.search(s.replace("٫", ".").replace("،", ","))
        return float(m.group(0)) if m else None
    except Exception:
        return None

def _parse_float_list(s: str) -> List[float]:
    s = s.replace("٫", ".").replace("،", ",").replace("/", " ")
    raw = [x for x in s.replace(",", " ").split() if x]
    out: List[float] = []
    for x in raw:
        try: out.append(float(x))
        except: pass
    return out

def _validate_sl_vs_entry(side: str, entry: float, sl: float) -> bool:
    return (sl < entry) if side.upper()=="LONG" else (sl > entry)

def _validate_targets(side: str, entry: float, tps: List[float]) -> bool:
    if not tps: return False
    if side.upper()=="LONG":
        return all(tps[i] <= tps[i+1] for i in range(len(tps)-1)) and all(tp >= entry for tp in tps)
    else:
        return all(tps[i] >= tps[i+1] for i in range(len(tps)-1)) and all(tp <= entry for tp in tps)

# ====== أزرار الإدارة: SL ======
async def sl_edit_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return
    q = update.callback_query; await q.answer()
    _, rec_id = q.data.split("|"); rec_id = int(rec_id)
    context.user_data["await_new_sl_for"] = rec_id
    await q.edit_message_text(f"أرسل قيمة SL الجديدة للتوصية #{rec_id}:")

async def sl_edit_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return
    if "await_new_sl_for" not in context.user_data: return
    rec_id = int(context.user_data.pop("await_new_sl_for"))
    v = _parse_float(update.message.text or "")
    trade = S(context)["trade_service"]
    rec = trade.get(rec_id)
    if v is None or v <= 0:
        await update.message.reply_text("⚠️ أرسل رقمًا صالحًا.")
        return
    side = getattr(rec.side,"value",rec.side)
    entry = float(getattr(rec.entry,"value",rec.entry))
    if not _validate_sl_vs_entry(side, entry, float(v)):
        rule = "SL < Entry (للـ LONG)" if side=="LONG" else "SL > Entry (للـ SHORT)"
        await update.message.reply_text(f"⚠️ غير صالح. القاعدة: {rule}")
        return
    try:
        trade.update_sl(rec_id, float(v), publish=True)
        await update.message.reply_text("✅ تم التحديث.", reply_markup=control_panel_keyboard(rec_id))
    except Exception as e:
        await update.message.reply_text(f"❌ فشل التحديث: {e}")

# ====== أزرار الإدارة: TPs ======
async def tp_edit_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return
    q = update.callback_query; await q.answer()
    _, rec_id = q.data.split("|"); rec_id = int(rec_id)
    context.user_data["await_new_tps_for"] = rec_id
    await q.edit_message_text(f"أرسل قائمة الأهداف الجديدة للتوصية #{rec_id} (مثال: 65000 66000 67000):")

async def tp_edit_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return
    if "await_new_tps_for" not in context.user_data: return
    rec_id = int(context.user_data.pop("await_new_tps_for"))
    tps = _parse_float_list(update.message.text or "")
    trade = S(context)["trade_service"]
    rec = trade.get(rec_id)
    side = getattr(rec.side,"value",rec.side)
    entry = float(getattr(rec.entry,"value",rec.entry))
    if not tps or not _validate_targets(side, entry, tps):
        hint = "تصاعدي ≥ Entry" if side=="LONG" else "تنازلي ≤ Entry"
        await update.message.reply_text(f"⚠️ قائمة أهداف غير منطقية. المعيار: {hint}.")
        return
    try:
        trade.update_targets(rec_id, tps, publish=True)
        await update.message.reply_text("✅ تم تحديث الأهداف.", reply_markup=control_panel_keyboard(rec_id))
    except Exception as e:
        await update.message.reply_text(f"❌ فشل التحديث: {e}")

# ====== الإغلاق ======
async def close_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return
    q = update.callback_query; await q.answer()
    _, rec_id = q.data.split("|"); rec_id = int(rec_id)
    context.user_data["await_close_for"] = rec_id
    await q.edit_message_text(f"أرسل سعر الخروج لإغلاق التوصية #{rec_id}:")

async def close_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return
    if "await_close_for" not in context.user_data: return
    rec_id = int(context.user_data.pop("await_close_for"))
    v = _parse_float(update.message.text or "")
    if v is None or v <= 0:
        await update.message.reply_text("⚠️ أدخل رقمًا صالحًا لسعر الخروج.")
        return
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد الإغلاق", callback_data=f"closeconf|{rec_id}|{v}")],
        [InlineKeyboardButton("❌ إلغاء", callback_data=f"closecancel|{rec_id}")]
    ])
    await update.message.reply_text(f"تأكيد إغلاق #{rec_id} بسعر {v:g}؟", reply_markup=kbd)

async def close_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return
    q = update.callback_query; await q.answer()
    _, rec_id, price = q.data.split("|"); rec_id = int(rec_id); price = float(price)
    trade = S(context)["trade_service"]
    try:
        rec = trade.close(rec_id, price)
        await q.edit_message_text("✅ تم الإغلاق.")
        await q.message.reply_text(build_trade_card_text(rec), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e:
        await q.edit_message_text(f"❌ فشل الإغلاق: {e}")

async def close_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return
    q = update.callback_query; await q.answer()
    await q.edit_message_text("تم إلغاء الإغلاق.")

# ====== السجل ======
async def history_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return
    q = update.callback_query; await q.answer()
    _, rec_id = q.data.split("|"); rec_id = int(rec_id)
    repo = S(context)["repo"]
    text = ""
    try:
        if hasattr(repo, "history"):
            events = repo.history(rec_id)  # صيغة متوقعة: [{ts, action, before, after, user_id}, ...]
            if not events:
                text = "لا يوجد سجل تغييرات."
            else:
                lines = []
                for ev in events:
                    ts = ev.get("ts") or ev.get("time") or ""
                    act = ev.get("action","")
                    who = ev.get("user_id","")
                    lines.append(f"• [{ts}] {act} (by {who})")
                text = "\n".join(lines)
        else:
            text = "السجل غير متاح في نسخة المستودع الحالية."
    except Exception as e:
        text = f"تعذّر جلب السجل: {e}"
    await q.edit_message_text(f"📜 السجل للتوصية #{rec_id}:\n{text}")

# ====== Quick Adjust ======
async def quick_adjust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return
    q = update.callback_query; await q.answer()
    _, kind, rec_id, delta = q.data.split("|")
    rec_id = int(rec_id); delta = float(delta)
    trade = S(context)["trade_service"]
    try:
        rec = trade.get(rec_id)
        side = getattr(rec.side,"value",rec.side)
        entry = float(getattr(rec.entry,"value",rec.entry))
        if kind == "SL":
            sl = float(getattr(rec.stop_loss,"value",rec.stop_loss))
            new_sl = sl * (1.0 + delta/100.0)
            if (side == "LONG" and not (new_sl < entry)) or (side == "SHORT" and not (new_sl > entry)):
                await q.edit_message_text("⚠️ تعديل غير صالح بالنسبة للدخول.")
                return
            trade.update_sl(rec_id, new_sl, publish=True)
            await q.edit_message_text("✅ تم ضبط SL سريعًا.")
        else:
            tps = list(getattr(rec.targets,"values",rec.targets or []))
            if not tps:
                await q.edit_message_text("⚠️ لا توجد أهداف لتعديلها.")
                return
            tps[0] = float(tps[0]) * (1.0 + delta/100.0)
            trade.update_targets(rec_id, tps, publish=True)
            await q.edit_message_text("✅ تم ضبط TP1 سريعًا.")
    except Exception:
        await q.edit_message_text("❌ فشل الضبط السريع.")

# ====== القوائم والتحليلات ======
async def cmd_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return
    trade = S(context)["trade_service"]
    items = trade.list_open()
    if not items:
        await update.message.reply_text("لا توجد توصيات مفتوحة.")
        return
    for r in items:
        await update.message.reply_text(
            build_trade_card_text(r), parse_mode=ParseMode.HTML,
            reply_markup=control_panel_keyboard(r.id), disable_web_page_preview=True
        )

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return
    args = (update.message.text or "").split()
    symbol = args[1].upper() if len(args) > 1 else None
    status = args[2].upper() if len(args) > 2 else None
    repo = S(context)["repo"]
    items = repo.list_all()
    out = []
    for r in items:
        if symbol and str(getattr(r.asset,"value",r.asset)).upper() != symbol: continue
        if status and str(getattr(r.status,"value",r.status)).upper() != status: continue
        out.append(r)
    if not out:
        await update.message.reply_text("لا نتائج.")
        return
    for r in out[:20]:
        await update.message.reply_text(
            build_trade_card_text(r), parse_mode=ParseMode.HTML,
            reply_markup=control_panel_keyboard(r.id), disable_web_page_preview=True
        )

async def cmd_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return
    analytics = S(context)["analytics_service"]
    repo = S(context)["repo"]
    items = repo.list_all()
    win = analytics.win_rate(items)
    curve = analytics.pnl_curve(items)
    by_market = analytics.summary_by_market(items)
    text = (
        f"📈 Analytics\n"
        f"Win Rate: {win:.2f}%\n"
        f"Markets: {by_market}\n"
        f"Curve Points: {len(curve)}"
    )
    await update.message.reply_text(text)

# ====== مُجمّع تسجيل callbacks/commands لاستخدامه من handlers.py ======
def build_management_callbacks():
    return [
        CallbackQueryHandler(sl_edit_request, pattern=r"^sl\|"),
        CallbackQueryHandler(tp_edit_request, pattern=r"^tp\|"),
        CallbackQueryHandler(close_request, pattern=r"^close\|"),
        CallbackQueryHandler(close_confirm, pattern=r"^closeconf\|"),
        CallbackQueryHandler(close_cancel, pattern=r"^closecancel\|"),
        CallbackQueryHandler(history_show, pattern=r"^hist\|"),
        CallbackQueryHandler(quick_adjust, pattern=r"^qa\|"),
    ]

def build_management_text_receivers():
    # استقبال النصوص اللاحقة لطلبات SL/TP/Close
    return [
        MessageHandler(filters.TEXT & ~filters.COMMAND, sl_edit_receive),
        MessageHandler(filters.TEXT & ~filters.COMMAND, tp_edit_receive),
        MessageHandler(filters.TEXT & ~filters.COMMAND, close_receive),
    ]

def build_management_commands():
    return [
        CommandHandler("open", cmd_open),
        CommandHandler("list", cmd_list),
        CommandHandler("analytics", cmd_analytics),
    ]
#--- END OF FILE ---