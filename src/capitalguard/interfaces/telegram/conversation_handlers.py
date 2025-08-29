# --- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
from __future__ import annotations
import logging
from typing import Dict, Any, List

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from capitalguard.application.services.trade_service import TradeService
from .keyboards import side_inline_keyboard, market_inline_keyboard, notes_inline_keyboard

log = logging.getLogger(__name__)

# ===== States (للرسائل النصية فقط) =====
ASSET, ENTRY, STOP_LOSS, TARGETS, NOTES = range(5)

# ===== Helpers =====
def _svc(context: ContextTypes.DEFAULT_TYPE, name: str):
    svc = context.application.bot_data.get(name)
    if not svc:
        raise RuntimeError(f"Service '{name}' not available in bot_data")
    return svc

def _recap(data: Dict[str, Any]) -> str:
    tps = data.get("targets", [])
    tps_str = ", ".join(f"{t:g}" for t in tps) if tps else "—"
    return (
        "📝 <b>مراجعة التوصية</b>\n\n"
        f"🔹 الأصل: <code>{data.get('asset','')}</code>\n"
        f"🔸 الاتجاه: <code>{data.get('side','')}</code>\n"
        f"🏷️ السوق: <code>{data.get('market','Futures')}</code>\n"
        f"💰 الدخول: <code>{data.get('entry','')}</code>\n"
        f"🛑 وقف: <code>{data.get('stop_loss','')}</code>\n"
        f"🎯 الأهداف: <code>{tps_str}</code>\n"
        f"📝 ملاحظة: <i>{data.get('notes','-')}</i>\n\n"
        "أرسل /publish للنشر أو /cancel للإلغاء."
    )

def _validate_sl(side: str, entry: float, sl: float) -> str | None:
    """
    قواعد الاتجاه:
      LONG  => يجب أن يكون SL < Entry
      SHORT => يجب أن يكون SL > Entry
    """
    s = side.upper()
    if s == "LONG" and not (sl < entry):
        return "في صفقات LONG يجب أن يكون <b>وقف الخسارة أقل من سعر الدخول</b>."
    if s == "SHORT" and not (sl > entry):
        return "في صفقات SHORT يجب أن يكون <b>وقف الخسارة أعلى من سعر الدخول</b>."
    return None

# ===== Flow =====
async def cmd_newrec(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    context.user_data["recommendation"] = {"market": "Futures"}  # افتراضيًا
    await update.message.reply_html(
        "لنبدأ بإنشاء توصية جديدة. أرسل <b>رمز الأصل</b> (مثال: <code>BTCUSDT</code>)."
    )
    # بعد وصول الأصل سنعرض أزرار الاتجاه Inline
    return ASSET

async def received_asset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["recommendation"]["asset"] = (update.message.text or "").upper().strip()
    await update.message.reply_html(
        "اختر <b>الاتجاه</b>:",
        reply_markup=side_inline_keyboard()
    )
    # ننتظر الضغط على زر الاتجاه (Callback) ثم نطلب الدخول → ENTRY
    return ENTRY

# --- اختيار الاتجاه (Inline) ---
async def choose_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    parts = (q.data or "").split(":")  # newrec:side:<LONG|SHORT>
    side = parts[2] if len(parts) == 3 else None
    if side not in {"LONG", "SHORT"}:
        await q.edit_message_text("اختيار غير صالح. أعد المحاولة بالأمر /newrec")
        return ConversationHandler.END

    context.user_data.setdefault("recommendation", {})["side"] = side
    await q.edit_message_text(f"الاتجاه: <b>{side}</b> ✅\n\nالآن أرسل <b>سعر الدخول</b>.", parse_mode=ParseMode.HTML)
    return ENTRY

async def received_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        entry = float((update.message.text or "").strip())
    except Exception:
        await update.message.reply_text("سعر دخول غير صالح. أدخل رقمًا.")
        return ENTRY
    context.user_data["recommendation"]["entry"] = entry
    await update.message.reply_text("أرسل <b>سعر وقف الخسارة</b>.", parse_mode=ParseMode.HTML)
    return STOP_LOSS

async def received_stop_loss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data.get("recommendation", {})
    try:
        sl = float((update.message.text or "").strip())
    except Exception:
        await update.message.reply_text("سعر وقف غير صالح. أدخل رقمًا.")
        return STOP_LOSS

    entry = float(data.get("entry", 0.0) or 0.0)
    side  = str(data.get("side", "")).upper()
    err = _validate_sl(side, entry, sl)
    if err:
        await update.message.reply_html(f"⚠️ {err}\n\nأعد إدخال <b>سعر وقف الخسارة</b> الصحيح.")
        return STOP_LOSS

    data["stop_loss"] = sl
    context.user_data["recommendation"] = data
    await update.message.reply_text("أرسل <b>الأهداف</b> مفصولة بمسافة أو فاصلة (مثال: 68000 70000).", parse_mode=ParseMode.HTML)
    return TARGETS

async def received_targets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        targets = [float(t) for t in (update.message.text or "").replace(",", " ").split() if t]
        if not targets:
            raise ValueError
    except Exception:
        await update.message.reply_text("الأهداف غير صالحة. أدخل قائمة أرقام.")
        return TARGETS

    context.user_data["recommendation"]["targets"] = targets
    await update.message.reply_html("اختر <b>نوع السوق</b>:", reply_markup=market_inline_keyboard())
    # ننتظر اختيار السوق (Callback)، ثم نطلب الملاحظة مع زر تخطي
    return NOTES

# --- اختيار السوق (Inline) ---
async def choose_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    parts = (q.data or "").split(":")  # newrec:market:<Spot|Futures>
    market = parts[2] if len(parts) == 3 else None
    if market not in {"Spot", "Futures"}:
        await q.edit_message_text("اختيار سوق غير صالح. أعد المحاولة بالأمر /newrec")
        return ConversationHandler.END

    context.user_data.setdefault("recommendation", {})["market"] = market
    await q.edit_message_text(
        f"السوق: <b>{market}</b> ✅\n\nأرسل <b>ملاحظة</b> (اختياري)، أو اضغط <b>تخطي</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=notes_inline_keyboard()
    )
    return NOTES

# --- تخطي الملاحظة (Inline) ---
async def skip_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    context.user_data.setdefault("recommendation", {})["notes"] = None
    recap = _recap(context.user_data["recommendation"])
    await q.edit_message_text(recap, parse_mode=ParseMode.HTML)
    return ConversationHandler.END

async def received_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    txt = (update.message.text or "").strip()
    context.user_data.setdefault("recommendation", {})["notes"] = (None if txt in {"", "-"} else txt)
    recap = _recap(context.user_data["recommendation"])
    await update.message.reply_html(recap)
    return ConversationHandler.END

# --- أوامر بعد المراجعة ---
async def cmd_publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data.get("recommendation") or {}
    required = {"asset", "side", "entry", "stop_loss", "targets"}
    if not required.issubset(data.keys()):
        await update.message.reply_text("لا توجد توصية جاهزة للنشر. ابدأ بـ /newrec")
        return
    trade: TradeService = _svc(context, "trade_service")
    rec = trade.create(
        asset=data["asset"],
        side=data["side"],
        entry=float(data["entry"]),
        stop_loss=float(data["stop_loss"]),
        targets=list(data["targets"]),
        market=data.get("market"),
        notes=data.get("notes"),
        user_id=str(update.effective_user.id),
    )
    await update.message.reply_html(f"✅ تم إنشاء التوصية <b>#{rec.id}</b> ونشرها.")
    context.user_data.clear()

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("تم إلغاء العملية.")

def get_recommendation_conversation_handler(allowed_filter) -> ConversationHandler:
    """
    ملاحظة: نُدرج CallbackQueryHandlers ضمن حالات Conversation لالتقاط أزرار الاتجاه/السوق/تخطي.
    """
    return ConversationHandler(
        entry_points=[
            CommandHandler("newrec", cmd_newrec, filters=filters.ChatType.PRIVATE & allowed_filter)
        ],
        states={
            ASSET:   [MessageHandler(filters.TEXT & ~filters.COMMAND, received_asset)],
            ENTRY:   [
                CallbackQueryHandler(choose_side,   pattern=r"^newrec:side:(LONG|SHORT)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_entry),
            ],
            STOP_LOSS: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_stop_loss)],
            TARGETS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, received_targets)],
            NOTES:    [
                CallbackQueryHandler(choose_market, pattern=r"^newrec:market:(Spot|Futures)$"),
                CallbackQueryHandler(skip_notes,    pattern=r"^newrec:notes:skip$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_notes),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        persistent=True,
        name="new_recommendation_conversation",
    )
# --- END OF FILE ---