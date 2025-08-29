# --- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
from __future__ import annotations
from typing import List, Optional
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, filters

from capitalguard.interfaces.telegram.keyboards import (
    side_reply_keyboard,
    market_reply_keyboard,
    yes_no_keyboard,
    remove_reply_keyboard,
    control_panel_keyboard,
)
from capitalguard.interfaces.telegram.ui_texts import build_admin_panel_caption

# مفاتيح الحالة
ASK_SYMBOL, ASK_SIDE, ASK_MARKET, ASK_ENTRY, ASK_SL, ASK_TPS, ASK_NOTES, ASK_CONFIRM = range(8)
NEW_REC = "new_rec_data"

def _ensure_admin_private(update: Update) -> bool:
    chat = update.effective_chat
    return chat and chat.type == "private"

async def start_newrec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _ensure_admin_private(update):
        return ConversationHandler.END
    context.user_data[NEW_REC] = {}
    await update.message.reply_text("لنبدأ بإنشاء توصية جديدة. ما هو رمز الأصل؟ (مثال: BTCUSDT)")
    return ASK_SYMBOL

async def ask_side(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[NEW_REC]["asset"] = update.message.text.strip().upper()
    await update.message.reply_text("اختر الاتجاه:", reply_markup=side_reply_keyboard())
    return ASK_SIDE

async def ask_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    side = update.message.text.strip().upper()
    if side not in ("LONG", "SHORT"):
        await update.message.reply_text("اختر من الأزرار: LONG أو SHORT.")
        return ASK_SIDE
    context.user_data[NEW_REC]["side"] = side
    await update.message.reply_text("اختر النوع:", reply_markup=market_reply_keyboard())
    return ASK_MARKET

async def ask_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    market = update.message.text.strip().title()
    if market not in ("Spot", "Futures"):
        await update.message.reply_text("اختر من الأزرار: Spot أو Futures.")
        return ASK_MARKET
    context.user_data[NEW_REC]["market"] = market
    await update.message.reply_text("ما هو سعر الدخول؟", reply_markup=remove_reply_keyboard())
    return ASK_ENTRY

async def ask_sl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        entry = float(update.message.text.strip())
    except Exception:
        await update.message.reply_text("أرسل رقمًا صحيحًا لسعر الدخول.")
        return ASK_ENTRY
    context.user_data[NEW_REC]["entry"] = entry
    await update.message.reply_text("ما هو سعر وقف الخسارة؟")
    return ASK_SL

async def ask_tps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sl = float(update.message.text.strip())
    except Exception:
        await update.message.reply_text("أرسل رقمًا صحيحًا لوقف الخسارة.")
        return ASK_SL
    context.user_data[NEW_REC]["stop_loss"] = sl
    await update.message.reply_text("أدخل الأهداف مفصولة بمسافة أو فاصلة (مثال: 68000 70000).")
    return ASK_TPS

def _parse_targets(text: str) -> List[float]:
    text = text.replace(",", " ")
    vals = []
    for p in text.split():
        try:
            vals.append(float(p))
        except Exception:
            pass
    return vals

async def ask_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tps = _parse_targets(update.message.text)
    if not tps:
        await update.message.reply_text("أرسل أهدافًا صحيحة، مثل: 68000 70000")
        return ASK_TPS
    context.user_data[NEW_REC]["targets"] = tps
    await update.message.reply_text("أضف ملاحظة مختصرة أو اكتب '-' لتخطي.")
    return ASK_NOTES

async def ask_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    note = update.message.text.strip()
    context.user_data[NEW_REC]["notes"] = None if note == "-" else note

    d = context.user_data[NEW_REC]
    preview = (
        "مراجعة التوصية 📝\n\n"
        f"الأصل 💎: {d['asset']}\n"
        f"النوع 📌: {d['market']} / {d['side']}\n"
        f"الدخول 💰: {d['entry']}\n"
        f"ووقف الخسارة 🛑: {d['stop_loss']}\n"
        "الأهداف 🎯:\n" + "\n".join([f"• TP{i+1}: {v}" for i, v in enumerate(d['targets'])]) +
        f"\n\nملاحظة 📝: {d['notes'] or '—'}\n\n"
        "هل تريد نشر هذه التوصية في القناة؟"
    )
    await update.message.reply_text(preview, reply_markup=yes_no_keyboard())
    return ASK_CONFIRM

async def create_and_publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip()
    if choice != "نشر في القناة ✅":
        await update.message.reply_text("تم الإلغاء.", reply_markup=remove_reply_keyboard())
        return ConversationHandler.END

    trade = context.application.bot_data["trade_service"]
    d = context.user_data.get(NEW_REC, {})
    rec = trade.create(
        asset=d["asset"],
        side=d["side"],
        entry=d["entry"],
        stop_loss=d["stop_loss"],
        targets=d["targets"],
        market=d["market"],
        notes=d["notes"],
        user_id=str(update.effective_user.id) if update.effective_user else None,
    )
    await update.message.reply_text(f"✅ تم إنشاء التوصية #{rec.id:02d} ونشرها بنجاح!", reply_markup=remove_reply_keyboard())

    # إرسال لوحة التحكم الخاصة للمنشئ
    await update.message.reply_text(
        build_admin_panel_caption(rec),
        reply_markup=control_panel_keyboard(rec.id, is_open=(rec.status.upper() == "OPEN"))
    )
    context.user_data.pop(NEW_REC, None)
    return ConversationHandler.END

def register_newrec_conversation(application):
    conv = ConversationHandler(
        entry_points=[CommandHandler("newrec", start_newrec)],
        states={
            ASK_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_side)],
            ASK_SIDE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_market)],
            ASK_MARKET: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_entry)],
            ASK_ENTRY:  [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_sl)],
            ASK_SL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_tps)],
            ASK_TPS:    [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_notes)],
            ASK_NOTES:  [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_confirm)],
            ASK_CONFIRM:[MessageHandler(filters.TEXT & ~filters.COMMAND, create_and_publish)],
        },
        fallbacks=[],
        allow_reentry=False,
    )
    application.add_handler(conv)
# --- END OF FILE ---