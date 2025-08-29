# --- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
from __future__ import annotations
from typing import Tuple, List, Dict, Any, Callable, Awaitable
import re

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from capitalguard.application.services.trade_service import TradeService
from capitalguard.interfaces.telegram.keyboards import bot_control_keyboard
from capitalguard.interfaces.telegram.ui_texts import build_panel_caption, build_close_summary

# حالات محادثة إنشاء توصية
ASK_SYMBOL, ASK_SIDE, ASK_MARKET, ASK_ENTRY, ASK_SL, ASK_TPS, ASK_NOTES, CONFIRM = range(8)

# أدوات صغيرة
def _parse_float_list(txt: str) -> List[float]:
    items = re.split(r"[,\s]+", txt.strip())
    return [float(x) for x in items if x]

def _side_validates_prices(side: str, entry: float, sl: float) -> bool:
    side = side.upper()
    if side == "LONG":
        return sl < entry
    if side == "SHORT":
        return sl > entry
    return True

# --------------- إنشاء توصية ---------------
async def newrec_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("لنبدأ بإنشاء توصية جديدة. ما هو رمز الأصل؟ (مثال: BTCUSDT)")
    context.user_data.clear()
    return ASK_SYMBOL

async def newrec_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["asset"] = update.message.text.strip().upper()
    # أزرار مبسطة بالرسالة التالية: نطلب الاتجاه مباشرة
    await update.message.reply_text("اختر الاتجاه: أرسل LONG أو SHORT")
    return ASK_SIDE

async def newrec_side(update: Update, context: ContextTypes.DEFAULT_TYPE):
    side = update.message.text.strip().upper()
    if side not in ("LONG", "SHORT"):
        await update.message.reply_text("أرسل LONG أو SHORT.")
        return ASK_SIDE
    context.user_data["side"] = side
    await update.message.reply_text("اختر النوع: Spot أو Futures")
    return ASK_MARKET

async def newrec_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["market"] = update.message.text.strip().title()
    await update.message.reply_text("ما هو سعر الدخول؟")
    return ASK_ENTRY

async def newrec_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["entry"] = float(update.message.text.strip())
    except Exception:
        await update.message.reply_text("أرسل رقمًا صالحًا.")
        return ASK_ENTRY
    await update.message.reply_text("ما هو سعر وقف الخسارة؟")
    return ASK_SL

async def newrec_sl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sl = float(update.message.text.strip())
    except Exception:
        await update.message.reply_text("أرسل رقمًا صالحًا.")
        return ASK_SL
    entry = float(context.user_data["entry"])
    side  = context.user_data["side"]
    if not _side_validates_prices(side, entry, sl):
        hint = "SL يجب أن يكون أقل من الدخول في LONG وأعلى في SHORT."
        await update.message.reply_text(f"القيمة لا تتوافق مع الاتجاه. {hint}\nأرسل قيمة SL من جديد:")
        return ASK_SL
    context.user_data["stop_loss"] = sl
    await update.message.reply_text("أدخل الأهداف مفصولة بمسافة أو فاصلة (مثال: 70000 72000).")
    return ASK_TPS

async def newrec_tps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["targets"] = _parse_float_list(update.message.text)
    except Exception:
        await update.message.reply_text("صيغة غير صحيحة. أعد إرسال الأهداف.")
        return ASK_TPS
    await update.message.reply_text("أضف ملاحظة مختصرة أو اكتب '-' لتخطي.")
    return ASK_NOTES

async def newrec_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    note = update.message.text.strip()
    context.user_data["notes"] = None if note == "-" else note

    # عرض ملخص ونشر بالأوامر
    d = context.user_data
    tps = " • ".join(str(x) for x in d["targets"])
    txt = (
        "📝 <b>مراجعة التوصية</b>\n"
        f"{d['asset']} 💎\n"
        f"{d['side']} 🔶\n"
        f"{d['market']} 💼\n"
        f"الدخول: <code>{d['entry']}</code>\n"
        f"SL: <code>{d['stop_loss']}</code>\n"
        f"الأهداف:\n• {tps}\n\n"
        f"ملاحظة: <i>{d['notes'] or 'None'}</i>\n\n"
        "أرسل <code>/publish</code> للنشر أو <code>/cancel</code> للإلغاء."
    )
    await update.message.reply_text(txt)
    return CONFIRM

async def newrec_publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    svc: TradeService = context.application.bot_data["trade_service"]
    d = context.user_data

    rec = svc.create(
        asset=d["asset"],
        side=d["side"],
        entry=d["entry"],
        stop_loss=d["stop_loss"],
        targets=d["targets"],
        market=d["market"],
        notes=d["notes"],
        user_id=str(update.effective_user.id),
    )

    # إرسال لوحة التحكّم داخل المحادثة
    await update.message.reply_text(
        f"✅ تم إنشاء التوصية #{rec.id} ونشرها!",
        reply_markup=bot_control_keyboard(rec.id, is_open=True),
    )
    # ثم عنوان اللوحة/الوصف
    await update.message.reply_text(build_panel_caption(rec))
    return ConversationHandler.END

async def newrec_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم إلغاء العملية.")
    return ConversationHandler.END

# --------------- لوحات التحكّم (أزرار) ---------------
async def on_amend_tp_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    _, _, rec_id = q.data.partition("rec:amend_tp:")
    context.user_data["rec_edit_id"] = int(rec_id)
    await q.message.reply_text("🎯 أرسل قائمة الأهداف الجديدة مفصولة بمسافة أو فاصلة:")
    context.user_data["awaiting"] = "tp"
    return

async def on_amend_sl_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    _, _, rec_id = q.data.partition("rec:amend_sl:")
    context.user_data["rec_edit_id"] = int(rec_id)
    await q.message.reply_text("🛡️ أرسل قيمة SL الجديدة:")
    context.user_data["awaiting"] = "sl"
    return

async def on_close_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    _, _, rec_id = q.data.partition("rec:close:")
    context.user_data["rec_edit_id"] = int(rec_id)
    await q.message.reply_text("🔻 أرسل الآن <b>سعر الخروج</b> لإغلاق التوصية:")
    context.user_data["awaiting"] = "close"
    return

async def on_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.message.reply_text("🧾 السجل: قريبًا سيتم توفير سجل المعاملات للتوصية.")
    return

async def on_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يلتقط القيم المطلوبة بعد الضغط على الأزرار.
    """
    if "awaiting" not in context.user_data or "rec_edit_id" not in context.user_data:
        return  # ليس لدينا سياق مطلوب

    mode = context.user_data["awaiting"]
    rec_id = int(context.user_data["rec_edit_id"])
    svc: TradeService = context.application.bot_data["trade_service"]

    try:
        if mode == "tp":
            new_targets = _parse_float_list(update.message.text)
            rec = svc.update_targets(rec_id, new_targets)
            await update.message.reply_text("✅ تم تحديث الأهداف.", reply_markup=bot_control_keyboard(rec.id, is_open=(rec.status.upper()=="OPEN")))
            await update.message.reply_text(build_panel_caption(rec))
        elif mode == "sl":
            new_sl = float(update.message.text.strip())
            # تحقق من منطق الاتجاه
            rec_now = svc.get(rec_id)
            if rec_now:
                entry = float(getattr(rec_now.entry, "value", rec_now.entry))
                side  = rec_now.side.value
                if not _side_validates_prices(side, entry, new_sl):
                    await update.message.reply_text("⚠️ القيمة لا تتوافق مع الاتجاه (LONG: SL<ENTRY, SHORT: SL>ENTRY). أعد الإرسال:")
                    return
            rec = svc.update_stop_loss(rec_id, new_sl)
            await update.message.reply_text("✅ تم تحديث SL.", reply_markup=bot_control_keyboard(rec.id, is_open=(rec.status.upper()=="OPEN")))
            await update.message.reply_text(build_panel_caption(rec))
        elif mode == "close":
            exit_p = float(update.message.text.strip())
            rec = svc.close(rec_id, exit_p)
            # استبدال اللوحة بملخص الإغلاق
            await update.message.reply_text(build_close_summary(rec))
        else:
            return
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {e}")
        return
    finally:
        context.user_data.pop("awaiting", None)
        context.user_data.pop("rec_edit_id", None)

# --------------- بناء محادثة / ربط ---------------
def build_newrec_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("newrec", newrec_start)],
        states={
            ASK_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, newrec_symbol)],
            ASK_SIDE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, newrec_side)],
            ASK_MARKET: [MessageHandler(filters.TEXT & ~filters.COMMAND, newrec_market)],
            ASK_ENTRY:  [MessageHandler(filters.TEXT & ~filters.COMMAND, newrec_entry)],
            ASK_SL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, newrec_sl)],
            ASK_TPS:    [MessageHandler(filters.TEXT & ~filters.COMMAND, newrec_tps)],
            ASK_NOTES:  [MessageHandler(filters.TEXT & ~filters.COMMAND, newrec_notes)],
            CONFIRM:    [
                CommandHandler("publish", newrec_publish),
                CommandHandler("cancel", newrec_cancel),
            ],
        },
        fallbacks=[CommandHandler("cancel", newrec_cancel)],
        name="newrec",
        persistent=True,
    )

def register_panel_handlers(application: Application):
    application.add_handler(CallbackQueryHandler(on_amend_tp_start,  pattern=r"^rec:amend_tp:\d+$"))
    application.add_handler(CallbackQueryHandler(on_amend_sl_start,  pattern=r"^rec:amend_sl:\d+$"))
    application.add_handler(CallbackQueryHandler(on_close_start,     pattern=r"^rec:close:\d+$"))
    application.add_handler(CallbackQueryHandler(on_history,         pattern=r"^rec:history:\d+$"))
    # نص حر بعد الأزرار
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_free_text))
# --- END OF FILE ---