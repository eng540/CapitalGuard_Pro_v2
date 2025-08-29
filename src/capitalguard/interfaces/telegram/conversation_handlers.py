# --- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
from __future__ import annotations
from typing import Dict, Any, List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from capitalguard.application.services.trade_service import TradeService
from .keyboards import control_panel_keyboard  # لوحة تحكّم خاصة تُرسل في الخاص بعد النشر


# =========================
# صلاحيات وأدوات مساعدة
# =========================

def _allowed_ids(context: ContextTypes.DEFAULT_TYPE) -> List[int]:
    raw = (context.application.bot_data.get("settings_allowed_users")
           or context.application.bot_data.get("TELEGRAM_ALLOWED_USERS")
           or "")
    if not raw:
        return []  # بدون تقييد (طور التطوير)
    parts = [p.strip() for p in str(raw).replace(",", " ").split() if p.strip()]
    out: List[int] = []
    for p in parts:
        try:
            out.append(int(p))
        except Exception:
            pass
    return out

def _is_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    # خاص فقط + (اختياري) قائمة المسموحين
    if update.effective_chat and update.effective_chat.type != ChatType.PRIVATE:
        return False
    allowed = _allowed_ids(context)
    if not allowed:
        return True
    uid = update.effective_user.id if update.effective_user else None
    return bool(uid and uid in allowed)

def _svc(context: ContextTypes.DEFAULT_TYPE) -> TradeService:
    return context.application.bot_data["trade_service"]  # type: ignore


# =========================
# حالات المحادثة
# =========================
(
    S_SYMBOL,
    S_SIDE,
    S_MARKET,
    S_ENTRY,
    S_SL,
    S_TPS,
    S_NOTES,
    S_REVIEW,
) = range(8)

NEWREC_KEY = "newrec_data"


# =========================
# بناء نص المراجعة (Preview)
# =========================
def _review_text(d: Dict[str, Any]) -> str:
    sym = d.get("asset", "")
    side = d.get("side", "")
    market = d.get("market", "")
    entry = d.get("entry", "")
    sl = d.get("stop_loss", "")
    tps: List[float] = d.get("targets", []) or []
    notes = d.get("notes", "-") or "-"
    lines = [
        "📝 مراجعة التوصية",
        f"{sym} 💎 الأصل:",
        f"{market} / {side} 📌 النوع:",
        f"{entry} 💰 الدخول:",
        f"{sl} 🛑 ووقف الخسارة:",
        "🎯 الأهداف:",
    ]
    for i, tp in enumerate(tps, start=1):
        lines.append(f"• TP{i}: {tp}")
    lines.append(f"\n📜 ملاحظة: {notes}")
    return "\n".join(lines)


# =========================
# نقاط الدخول/التعامل
# =========================

async def start_newrec(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_authorized(update, context):
        return ConversationHandler.END
    context.user_data[NEWREC_KEY] = {}
    await update.effective_message.reply_text("لنبدأ بإنشاء توصية جديدة. ما هو رمز الأصل؟ (مثال: BTCUSDT)")
    return S_SYMBOL


async def ask_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_authorized(update, context):
        return ConversationHandler.END
    sym = (update.effective_message.text or "").strip().upper()
    context.user_data[NEWREC_KEY]["asset"] = sym
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("LONG", callback_data="side:LONG"),
         InlineKeyboardButton("SHORT", callback_data="side:SHORT")]
    ])
    await update.effective_message.reply_text("اختر الاتجاه:", reply_markup=kb)
    return S_SIDE


async def set_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_authorized(update, context):
        return ConversationHandler.END
    q = update.callback_query
    await q.answer()
    side = q.data.split(":")[1]
    context.user_data[NEWREC_KEY]["side"] = side
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Spot", callback_data="mkt:Spot"),
         InlineKeyboardButton("Futures", callback_data="mkt:Futures")]
    ])
    await q.edit_message_text("اختر النوع:", reply_markup=kb)
    return S_MARKET


async def set_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_authorized(update, context):
        return ConversationHandler.END
    q = update.callback_query
    await q.answer()
    market = q.data.split(":")[1]
    context.user_data[NEWREC_KEY]["market"] = market
    await q.edit_message_text("ما هو سعر الدخول؟")
    return S_ENTRY


async def set_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_authorized(update, context):
        return ConversationHandler.END
    txt = (update.effective_message.text or "").strip()
    context.user_data[NEWREC_KEY]["entry"] = float(txt)
    await update.effective_message.reply_text("ما هو سعر وقف الخسارة؟")
    return S_SL


async def set_sl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_authorized(update, context):
        return ConversationHandler.END
    txt = (update.effective_message.text or "").strip()
    context.user_data[NEWREC_KEY]["stop_loss"] = float(txt)
    await update.effective_message.reply_text("أدخل الأهداف مفصولة بمسافة أو فاصلة (مثال: 68000 70000 72000).")
    return S_TPS


def _parse_floats(text: str) -> List[float]:
    parts = [p for p in text.replace(",", " ").split() if p.strip()]
    out: List[float] = []
    for p in parts:
        try:
            out.append(float(p))
        except Exception:
            pass
    return out


async def set_tps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_authorized(update, context):
        return ConversationHandler.END
    vals = _parse_floats(update.effective_message.text or "")
    context.user_data[NEWREC_KEY]["targets"] = vals
    await update.effective_message.reply_text("أضف ملاحظة مختصرة أو اكتب '-' لتخطي.")
    return S_NOTES


async def set_notes_and_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_authorized(update, context):
        return ConversationHandler.END
    note = (update.effective_message.text or "").strip()
    context.user_data[NEWREC_KEY]["notes"] = (None if note == "-" else note)

    d = context.user_data[NEWREC_KEY]
    preview = _review_text(d)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نشر في القناة", callback_data="newrec:publish"),
         InlineKeyboardButton("❌ إلغاء", callback_data="newrec:cancel")]
    ])
    await update.effective_message.reply_text(preview, reply_markup=kb, parse_mode=ParseMode.HTML)
    return S_REVIEW


async def publish_recommendation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_authorized(update, context):
        return ConversationHandler.END
    q = update.callback_query
    await q.answer()
    d = context.user_data.get(NEWREC_KEY) or {}
    try:
        svc = _svc(context)
        rec = svc.create(
            asset=d["asset"],
            side=d["side"],
            entry=d["entry"],
            stop_loss=d["stop_loss"],
            targets=d.get("targets", []) or [],
            market=d.get("market", "Futures"),
            notes=d.get("notes"),
        )
        await q.edit_message_text(f"✅ تم إنشاء التوصية #{rec.id} ونشرها بنجاح!")
        # إرسال لوحة تحكم خاصة لإدارة هذه التوصية
        await q.message.reply_text(
            f"لوحة تحكّم #{rec.id} — {getattr(getattr(rec, 'asset', None), 'value', getattr(rec, 'asset', ''))}"
            f" ({getattr(getattr(rec, 'side', None), 'value', getattr(rec, 'side', ''))})",
            reply_markup=control_panel_keyboard(rec.id, is_open=(rec.status.upper() == "OPEN")),
        )
    except Exception as e:
        await q.edit_message_text(f"❌ فشل في النشر: {e}")
    finally:
        context.user_data.pop(NEWREC_KEY, None)
    return ConversationHandler.END


async def cancel_newrec(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("تم الإلغاء.")
    else:
        await update.effective_message.reply_text("تم الإلغاء.")
    context.user_data.pop(NEWREC_KEY, None)
    return ConversationHandler.END


# =========================
# مُنشئ الـ ConversationHandler
# =========================
def build_newrec_conversation() -> ConversationHandler:
    private = filters.ChatType.PRIVATE

    return ConversationHandler(
        entry_points=[CommandHandler("newrec", start_newrec, filters=private)],
        states={
            S_SYMBOL: [MessageHandler(private & filters.TEXT, ask_side)],
            S_SIDE: [CallbackQueryHandler(set_side, pattern=r"^side:(LONG|SHORT)$")],
            S_MARKET: [CallbackQueryHandler(set_market, pattern=r"^mkt:(Spot|Futures)$")],
            S_ENTRY: [MessageHandler(private & filters.TEXT, set_entry)],
            S_SL: [MessageHandler(private & filters.TEXT, set_sl)],
            S_TPS: [MessageHandler(private & filters.TEXT, set_tps)],
            S_NOTES: [MessageHandler(private & filters.TEXT, set_notes_and_review)],
            S_REVIEW: [
                CallbackQueryHandler(publish_recommendation, pattern=r"^newrec:publish$"),
                CallbackQueryHandler(cancel_newrec, pattern=r"^newrec:cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_newrec, filters=private)],
        allow_reentry=False,
    )