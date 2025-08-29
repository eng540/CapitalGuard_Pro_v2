# --- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
from __future__ import annotations
import uuid
import logging
from typing import Dict, Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from capitalguard.application.services.trade_service import TradeService
from .keyboards import side_reply_keyboard, market_reply_keyboard, remove_reply_keyboard

log = logging.getLogger(__name__)

# Conversation States
ASSET, SIDE, ENTRY, STOP_LOSS, TARGETS, MARKET, NOTES = range(7)

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
        "إرسال /publish لنشرها أو /cancel للإلغاء."
    )

# -------- Flow --------
async def cmd_newrec(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    context.user_data["recommendation"] = {}
    await update.message.reply_html(
        "لنبدأ بإنشاء توصية جديدة. ما هو رمز الأصل؟ (مثال: BTCUSDT)",
    )
    return ASSET

async def received_asset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["recommendation"]["asset"] = (update.message.text or "").upper().strip()
    await update.message.reply_text("اختر الاتجاه:", reply_markup=side_reply_keyboard())
    return SIDE

async def received_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    side = (update.message.text or "").upper().strip()
    if side not in {"LONG", "SHORT"}:
        await update.message.reply_text("الرجاء اختيار LONG أو SHORT من الأزرار.", reply_markup=side_reply_keyboard())
        return SIDE
    context.user_data["recommendation"]["side"] = side
    await update.message.reply_text("ما هو سعر الدخول؟", reply_markup=remove_reply_keyboard())
    return ENTRY

async def received_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        context.user_data["recommendation"]["entry"] = float((update.message.text or "").strip())
    except Exception:
        await update.message.reply_text("سعر دخول غير صالح. أدخل رقمًا.")
        return ENTRY
    await update.message.reply_text("ما هو سعر وقف الخسارة؟")
    return STOP_LOSS

async def received_stop_loss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        context.user_data["recommendation"]["stop_loss"] = float((update.message.text or "").strip())
    except Exception:
        await update.message.reply_text("سعر وقف غير صالح. أدخل رقمًا.")
        return STOP_LOSS
    await update.message.reply_text("أدخل الأهداف مفصولة بمسافة أو فاصلة (مثال: 68000 70000).")
    return TARGETS

async def received_targets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        targets = [float(t) for t in (update.message.text or "").replace(",", " ").split() if t]
        if not targets:
            raise ValueError
        context.user_data["recommendation"]["targets"] = targets
    except Exception:
        await update.message.reply_text("الأهداف غير صالحة. أدخل قائمة أرقام.")
        return TARGETS
    await update.message.reply_text("اختر نوع السوق:", reply_markup=market_reply_keyboard())
    return MARKET

async def received_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    market = (update.message.text or "").title().strip()
    if market not in {"Spot", "Futures"}:
        await update.message.reply_text("اختر Spot أو Futures من الأزرار.", reply_markup=market_reply_keyboard())
        return MARKET
    context.user_data["recommendation"]["market"] = market
    await update.message.reply_text("أدخل ملاحظة (اختياري). أرسل '-' لتجاوز.", reply_markup=remove_reply_keyboard())
    return NOTES

async def received_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    txt = (update.message.text or "").strip()
    context.user_data["recommendation"]["notes"] = None if txt in {"", "-"} else txt

    recap = _recap(context.user_data["recommendation"])
    await update.message.reply_html(recap)
    return ConversationHandler.END

# أوامر مساعدة بعد المراجعة
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
        entry=data["entry"],
        stop_loss=data["stop_loss"],
        targets=data["targets"],
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
    return ConversationHandler(
        entry_points=[CommandHandler("newrec", cmd_newrec, filters=filters.ChatType.PRIVATE & allowed_filter)],
        states={
            ASSET:     [MessageHandler(filters.TEXT & ~filters.COMMAND, received_asset)],
            SIDE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, received_side)],
            ENTRY:     [MessageHandler(filters.TEXT & ~filters.COMMAND, received_entry)],
            STOP_LOSS: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_stop_loss)],
            TARGETS:   [MessageHandler(filters.TEXT & ~filters.COMMAND, received_targets)],
            MARKET:    [MessageHandler(filters.TEXT & ~filters.COMMAND, received_market)],
            NOTES:     [MessageHandler(filters.TEXT & ~filters.COMMAND, received_notes)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        persistent=True,
        name="new_recommendation_conversation",
    )
# --- END OF FILE ---