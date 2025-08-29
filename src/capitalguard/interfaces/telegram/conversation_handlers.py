# --- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
from __future__ import annotations
from typing import List, Dict, Any
import logging
from telegram import Update
from telegram.ext import (
    ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from .auth import ALLOWED_FILTER
from .keyboards import (
    choose_side_keyboard, choose_market_keyboard, remove_reply_keyboard,
    confirm_recommendation_keyboard, control_panel_keyboard, close_confirmation_keyboard
)
from .ui_texts import build_review_text
log = logging.getLogger(__name__)

# حالات المحادثة
ASK_SYMBOL, ASK_SIDE, ASK_MARKET, ASK_ENTRY, ASK_SL, ASK_TPS, ASK_NOTES, CONFIRM = range(8)

DRAFT_KEY = "draft_rec"           # داخل user_data
AWAIT_CLOSE_FOR = "await_close_for"  # rec_id أثناء طلب سعر الإغلاق

def _parse_float_list(text: str) -> List[float]:
    raw = [p for p in text.replace(",", " ").split() if p.strip()]
    return [float(x) for x in raw]

# —————— إنشاء توصية ——————
async def start_newrec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop(DRAFT_KEY, None)
    await update.message.reply_text("لنبدأ بإنشاء توصية جديدة. ما هو رمز الأصل؟ (مثال: BTCUSDT)")
    return ASK_SYMBOL

async def ask_side(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[DRAFT_KEY] = {"asset": update.message.text.strip()}
    await update.message.reply_text("اختر الاتجاه:", reply_markup=choose_side_keyboard())
    return ASK_SIDE

async def ask_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[DRAFT_KEY]["side"] = update.message.text.strip().upper()
    await update.message.reply_text("اختر النوع:", reply_markup=choose_market_keyboard())
    return ASK_MARKET

async def ask_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[DRAFT_KEY]["market"] = update.message.text.strip().title()
    await update.message.reply_text("ما هو سعر الدخول؟", reply_markup=remove_reply_keyboard())
    return ASK_ENTRY

async def ask_sl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[DRAFT_KEY]["entry"] = float(update.message.text.strip())
    await update.message.reply_text("ما هو سعر وقف الخسارة؟")
    return ASK_SL

async def ask_tps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[DRAFT_KEY]["stop_loss"] = float(update.message.text.strip())
    await update.message.reply_text("أدخل الأهداف مفصولة بمسافة أو فاصلة (مثال: 68000 70000).")
    return ASK_TPS

async def ask_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[DRAFT_KEY]["targets"] = _parse_float_list(update.message.text)
    await update.message.reply_text("أضف ملاحظة مختصرة أو اكتب '-' لتخطي.")
    return ASK_NOTES

async def preview_and_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    note = update.message.text.strip()
    if note != "-":
        context.user_data[DRAFT_KEY]["notes"] = note
    else:
        context.user_data[DRAFT_KEY]["notes"] = None

    key = str(update.effective_user.id)  # مفتاح محلي مباشر
    text = build_review_text(context.user_data[DRAFT_KEY])
    await update.message.reply_html(text, reply_markup=confirm_recommendation_keyboard(key))
    return CONFIRM

# نشر/إلغاء
async def on_publish_click(update: Update, context: ContextTypes.DEFAULT_TYPE, *, trade_service):
    query = update.callback_query
    await query.answer()
    draft = context.user_data.get(DRAFT_KEY)
    if not draft:
        await query.edit_message_text("لا توجد مسودة.")
        return ConversationHandler.END

    rec = trade_service.create(
        asset=draft["asset"],
        side=draft["side"],
        entry=float(draft["entry"]),
        stop_loss=float(draft["stop_loss"]),
        targets=list(draft["targets"]),
        market=draft["market"],
        notes=draft.get("notes"),
        user_id=str(update.effective_user.id),
    )

    # لوحة تحكم داخل البوت فقط
    await query.edit_message_text("✅ تم إنشاء التوصية ونشرها بنجاح!")
    await query.message.reply_html(
        f"<b>#REC{rec.id:04d}</b> — {rec.asset.value} ({rec.side.value})",
        reply_markup=control_panel_keyboard(rec.id, is_open=(rec.status.upper() == "OPEN"))
    )
    context.user_data.pop(DRAFT_KEY, None)
    return ConversationHandler.END

async def on_cancel_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop(DRAFT_KEY, None)
    await query.edit_message_text("تم الإلغاء.")
    return ConversationHandler.END

# —————— إدارة التوصية داخل البوت ——————
async def click_amend_sl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    rec_id = int(q.data.split(":")[2])
    context.user_data["await_sl_for"] = rec_id
    await q.message.reply_text("🛡️ أرسل قيمة SL الجديدة:")
    # لا ننهي؛ ننتظر رسالة المستخدم
    return ConversationHandler.END

async def click_amend_tp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    rec_id = int(q.data.split(":")[2])
    context.user_data["await_tp_for"] = rec_id
    await q.message.reply_text("🎯 أرسل الأهداف الجديدة مفصولة بمسافة أو فاصلة:")
    return ConversationHandler.END

async def click_close_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    rec_id = int(q.data.split(":")[2])
    context.user_data[AWAIT_CLOSE_FOR] = rec_id
    await q.message.reply_text("🔻 أرسل الآن سعر الخروج لإغلاق التوصية:")
    return ConversationHandler.END

async def on_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE, *, trade_service):
    """يلتقط رسائل المتابعة بعد ضغط أزرار الإدارة."""
    text = update.message.text.strip()

    # تعديل SL
    if "await_sl_for" in context.user_data:
        rec_id = context.user_data.pop("await_sl_for")
        try:
            new_sl = float(text)
        except ValueError:
            await update.message.reply_text("الرجاء إرسال رقم صالح.")
            return
        rec = trade_service.update_stop_loss(rec_id, new_sl)
        await update.message.reply_html(
            f"تم تحديث SL للتوصية <b>#{rec.id}</b> إلى <b>{new_sl:g}</b>."
        )
        return

    # تعديل TPs
    if "await_tp_for" in context.user_data:
        rec_id = context.user_data.pop("await_tp_for")
        try:
            tps = _parse_float_list(text)
            if not tps:
                raise ValueError
        except Exception:
            await update.message.reply_text("الرجاء إرسال قائمة أرقام مفصولة بمسافة أو فاصلة.")
            return
        rec = trade_service.update_targets(rec_id, tps)
        await update.message.reply_html(
            f"تم تحديث الأهداف للتوصية <b>#{rec.id}</b>."
        )
        return

    # إغلاق
    if AWAIT_CLOSE_FOR in context.user_data:
        rec_id = int(context.user_data.pop(AWAIT_CLOSE_FOR))
        try:
            price = float(text)
        except ValueError:
            await update.message.reply_text("أرسل رقمًا صالحًا لسعر الخروج.")
            return
        # تأكيد
        await update.message.reply_text(
            f"هل تؤكد إغلاق التوصية #{rec_id} على سعر {price:g}؟",
            reply_markup=close_confirmation_keyboard(rec_id, price)
        )
        return

async def on_confirm_close(update: Update, context: ContextTypes.DEFAULT_TYPE, *, trade_service):
    q = update.callback_query
    await q.answer()
    _, _, rec_id_s, price_s = q.data.split(":")
    rec = trade_service.close(int(rec_id_s), float(price_s))
    await q.edit_message_text(f"✅ تم إغلاق التوصية #{rec.id} على {float(price_s):g}.")

async def on_cancel_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("تم إلغاء الإغلاق.")

# —————— بناء محادثة وإنشاء مُعالِجات الاستدعاء ——————
def build_newrec_conversation(*, trade_service) -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("newrec", start_newrec, filters=ALLOWED_FILTER)],
        states={
            ASK_SYMBOL: [MessageHandler(ALLOWED_FILTER & filters.TEXT & ~filters.COMMAND, ask_side)],
            ASK_SIDE:   [MessageHandler(ALLOWED_FILTER & filters.TEXT & ~filters.COMMAND, ask_market)],
            ASK_MARKET: [MessageHandler(ALLOWED_FILTER & filters.TEXT & ~filters.COMMAND, ask_entry)],
            ASK_ENTRY:  [MessageHandler(ALLOWED_FILTER & filters.TEXT & ~filters.COMMAND, ask_sl)],
            ASK_SL:     [MessageHandler(ALLOWED_FILTER & filters.TEXT & ~filters.COMMAND, ask_tps)],
            ASK_TPS:    [MessageHandler(ALLOWED_FILTER & filters.TEXT & ~filters.COMMAND, ask_notes)],
            ASK_NOTES:  [MessageHandler(ALLOWED_FILTER & filters.TEXT & ~filters.COMMAND, preview_and_confirm)],
            CONFIRM:    [
                CallbackQueryHandler(lambda u,c: on_publish_click(u,c,trade_service=trade_service), pattern=r"^rec:publish:"),
                CallbackQueryHandler(on_cancel_click, pattern=r"^rec:cancel:"),
            ],
        },
        fallbacks=[CommandHandler("cancel", on_cancel_click, filters=ALLOWED_FILTER)],
        name="newrec_conversation",
        persistent=False,
    )

def management_callback_handlers(*, trade_service) -> List[CallbackQueryHandler]:
    return [
        CallbackQueryHandler(click_amend_sl, pattern=r"^rec:amend_sl:\d+$"),
        CallbackQueryHandler(click_amend_tp, pattern=r"^rec:amend_tp:\d+$"),
        CallbackQueryHandler(click_close_now, pattern=r"^rec:close:\d+$"),
        CallbackQueryHandler(lambda u,c: on_confirm_close(u,c,trade_service=trade_service), pattern=r"^rec:confirm_close:\d+:\d+(\.\d+)?$"),
        CallbackQueryHandler(on_cancel_close, pattern=r"^rec:cancel_close:\d+$"),
        # رسائل المتابعة (أرقام) تُلتقط عبر on_free_text في handlers.py
    ]
# --- END OF FILE ---