# --- START OF FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
from __future__ import annotations
from typing import List, Tuple, Optional
import logging
from telegram import Update
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

from capitalguard.config import settings
from capitalguard.interfaces.telegram.keyboards import remove_reply_keyboard
# الخدمات تُحقن عبر bot_data في main.py:
# - "trade_service"
# - "repo"

log = logging.getLogger(__name__)

# مفاتيح حالة المحادثة في الخاص
AWAITING_TP = "awaiting_tp_for_rec"
AWAITING_SL = "awaiting_sl_for_rec"
AWAITING_CLOSE = "awaiting_close_for_rec"

def _allowed_user(user_id: Optional[int]) -> bool:
    if user_id is None:
        return False
    raw = (settings.TELEGRAM_ALLOWED_USERS or "").strip()
    if not raw:
        return True  # لا توجد قائمة = السماح للجميع (للمرحلة التطويرية)
    whitelist = {u.strip() for u in raw.replace(",", " ").split() if u.strip()}
    return str(user_id) in whitelist

def _ensure_private_admin(update: Update) -> Tuple[bool, Optional[int]]:
    """يتأكد أن التفاعل في الخاص ومن مستخدم مصرح، ويرد Toast عند الرفض."""
    q = update.callback_query
    user_id = q.from_user.id if q else (update.effective_user.id if update.effective_user else None)
    chat = update.effective_chat
    if chat and chat.type != "private":
        if q:
            q.answer("⚠️ استخدم البوت في الخاص لإدارة التوصيات.", show_alert=False)
        return False, user_id
    if not _allowed_user(user_id):
        if q:
            q.answer("❌ غير مصرح لك بهذه العملية.", show_alert=False)
        return False, user_id
    return True, user_id

# ---------------------------
# Callbacks: من لوحة التحكم الخاصة فقط
# ---------------------------
async def click_amend_tp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, uid = _ensure_private_admin(update)
    if not ok:
        return
    q = update.callback_query
    rec_id = int(q.data.split(":")[-1])
    context.user_data[AWAITING_TP] = rec_id
    await q.answer()
    await q.edit_message_text("🎯 أرسل الأهداف الجديدة مفصولة بمسافة أو فاصلة (مثال: 120000 130000).")

async def click_amend_sl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, uid = _ensure_private_admin(update)
    if not ok:
        return
    q = update.callback_query
    rec_id = int(q.data.split(":")[-1])
    context.user_data[AWAITING_SL] = rec_id
    await q.answer()
    await q.edit_message_text("🛡️ أرسل قيمة SL الجديدة:")

async def click_close_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, uid = _ensure_private_admin(update)
    if not ok:
        return
    q = update.callback_query
    rec_id = int(q.data.split(":")[-1])
    context.user_data[AWAITING_CLOSE] = rec_id
    await q.answer()
    await q.edit_message_text("🚨 أرسل الآن سعر الخروج لإغلاق التوصية:")

async def click_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, _ = _ensure_private_admin(update)
    if not ok:
        return
    q = update.callback_query
    rec_id = int(q.data.split(":")[-1])
    await q.answer()
    await q.edit_message_text(f"📜 السجل: قريبًا سيتم عرض سجل المعاملات للتوصية #{rec_id}.")

# ---------------------------
# Messages to complete actions (in private)
# ---------------------------
def _parse_floats(text: str) -> List[float]:
    seps = [",", " "]
    for s in seps:
        text = text.replace(s, " ")
    parts = [p for p in text.split(" ") if p]
    arr: List[float] = []
    for p in parts:
        try:
            arr.append(float(p))
        except Exception:
            pass
    return arr

async def submit_new_tp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AWAITING_TP not in context.user_data:
        return
    rec_id = context.user_data.pop(AWAITING_TP)
    values = _parse_floats(update.effective_message.text)
    if not values:
        await update.effective_message.reply_text("⚠️ صيغة غير صحيحة. أرسل أرقامًا مفصولة بمسافة أو فاصلة.")
        return
    trade = context.application.bot_data["trade_service"]
    rec = trade.update_targets(rec_id, values)
    await update.effective_message.reply_text(f"✅ تم تحديث الأهداف لـ #{rec.id}.", reply_markup=remove_reply_keyboard())

async def submit_new_sl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AWAITING_SL not in context.user_data:
        return
    rec_id = context.user_data.pop(AWAITING_SL)
    try:
        new_sl = float(update.effective_message.text.strip())
    except Exception:
        await update.effective_message.reply_text("⚠️ صيغة غير صحيحة. أرسل رقمًا صحيحًا.")
        return
    trade = context.application.bot_data["trade_service"]
    rec = trade.update_stop_loss(rec_id, new_sl)
    await update.effective_message.reply_text(f"✅ تم تحديث SL للتوصية #{rec.id}.", reply_markup=remove_reply_keyboard())

async def submit_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AWAITING_CLOSE not in context.user_data:
        return
    rec_id = context.user_data.pop(AWAITING_CLOSE)
    try:
        exit_price = float(update.effective_message.text.strip())
    except Exception:
        await update.effective_message.reply_text("⚠️ صيغة غير صحيحة. أرسل رقمًا صحيحًا لسعر الخروج.")
        return
    trade = context.application.bot_data["trade_service"]
    rec = trade.close(rec_id, exit_price)
    await update.effective_message.reply_text(f"✅ تم إغلاق التوصية #{rec.id} على {exit_price:g}.", reply_markup=remove_reply_keyboard())

def register_management_handlers(application):
    application.add_handler(CallbackQueryHandler(click_amend_tp, pattern=r"^rec:amend_tp:\d+$"))
    application.add_handler(CallbackQueryHandler(click_amend_sl, pattern=r"^rec:amend_sl:\d+$"))
    application.add_handler(CallbackQueryHandler(click_close_now, pattern=r"^rec:close:\d+$"))
    application.add_handler(CallbackQueryHandler(click_history, pattern=r"^rec:history:\d+$"))

    # رسائل إتمام الإجراءات في الخاص
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, submit_new_tp))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, submit_new_sl))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, submit_close))
# --- END OF FILE ---