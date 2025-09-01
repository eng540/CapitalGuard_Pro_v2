--- START OF FILE: src/capitalguard/interfaces/telegram/conversation_handlers.py ---
from __future__ import annotations
from typing import List, Dict, Any, Optional
import logging, re
from telegram import Update
from telegram.constants import ParseMode, ChatType
from telegram.ext import (
    ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from capitalguard.config import settings
from .keyboards import (
    choose_side_keyboard, choose_market_keyboard, remove_reply_keyboard,
    confirm_recommendation_keyboard, skip_notes_keyboard, control_panel_keyboard
)
from .ui_texts import build_review_text_with_price, build_trade_card_text

log = logging.getLogger(__name__)

# حالات المحادثة
ASK_ASSET, ASK_SIDE, ASK_MARKET, ASK_ENTRY, ASK_SL, ASK_TARGETS, ASK_NOTES, REVIEW = range(8)

_num_re = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")

def _parse_float(s: str) -> Optional[float]:
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
    if not _is_private(update):  # تجاهل من القنوات/المجموعات
        return False
    uid = update.effective_user.id if update.effective_user else None
    if not _allowed_user(uid):
        await update.effective_message.reply_text("🚫 غير مصرح لك باستخدام هذا البوت.")
        return False
    return True

def S(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.application.bot_data.get("services", {})

def _draft_init(context: ContextTypes.DEFAULT_TYPE):
    context.user_data["draft"] = {"asset":"", "side":"", "market":"Futures", "entry":0.0, "stop_loss":0.0, "targets":[], "notes":"-"}

def _draft(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.get("draft", {})

# ========== نقاط سير المحادثة ==========
async def newrec_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return ConversationHandler.END
    _draft_init(context)
    await update.message.reply_text("أرسل رمز الأصل (مثل: BTCUSDT):", reply_markup=remove_reply_keyboard())
    return ASK_ASSET

async def ask_asset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return ConversationHandler.END
    asset = (update.message.text or "").strip().upper()
    if not asset or not asset.isalnum():
        await update.message.reply_text("⚠️ أرسل رمزًا صالحًا مثل: BTCUSDT")
        return ASK_ASSET
    d = _draft(context); d["asset"] = asset
    await update.message.reply_text("اختر الاتجاه:", reply_markup=choose_side_keyboard())
    return ASK_SIDE

async def pick_side(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return ConversationHandler.END
    side = (update.message.text or "").strip().upper()
    if side not in ("LONG","SHORT"):
        await update.message.reply_text("اختر من الأزرار: LONG أو SHORT.")
        return ASK_SIDE
    d = _draft(context); d["side"] = side
    await update.message.reply_text("اختر نوع السوق:", reply_markup=choose_market_keyboard())
    return ASK_MARKET

async def pick_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return ConversationHandler.END
    market = (update.message.text or "").strip().capitalize()
    if market not in ("Spot","Futures"):
        await update.message.reply_text("اختر من الأزرار: Spot أو Futures.")
        return ASK_MARKET
    d = _draft(context); d["market"] = market
    await update.message.reply_text(f"أدخل سعر الدخول Entry لـ {d['asset']}:", reply_markup=remove_reply_keyboard())
    return ASK_ENTRY

async def ask_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return ConversationHandler.END
    v = _parse_float(update.message.text or "")
    if v is None or v <= 0:
        await update.message.reply_text("⚠️ أدخل رقمًا صالحًا لـ Entry.")
        return ASK_ENTRY
    d = _draft(context); d["entry"] = float(v)
    await update.message.reply_text("أدخل وقف الخسارة SL:")
    return ASK_SL

async def ask_sl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return ConversationHandler.END
    v = _parse_float(update.message.text or "")
    d = _draft(context)
    if v is None or v <= 0 or not _validate_sl_vs_entry(d["side"], d["entry"], float(v)):
        rule = "SL < Entry (للـ LONG)" if d["side"]=="LONG" else "SL > Entry (للـ SHORT)"
        await update.message.reply_text(f"⚠️ أدخل SL صالحًا. القاعدة: {rule}")
        return ASK_SL
    d["stop_loss"] = float(v)
    await update.message.reply_text("أدخل الأهداف Targets (قائمة أرقام مفصولة بمسافات أو فواصل):")
    return ASK_TARGETS

async def ask_targets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return ConversationHandler.END
    tps = _parse_float_list(update.message.text or "")
    d = _draft(context)
    if not tps or not _validate_targets(d["side"], d["entry"], tps):
        hint = "تصاعدي ≥ Entry" if d["side"]=="LONG" else "تنازلي ≤ Entry"
        await update.message.reply_text(f"⚠️ قائمة أهداف غير منطقية. المعيار: {hint}. جرّب مجددًا.")
        return ASK_TARGETS
    d["targets"] = tps
    await update.message.reply_text("أرسل الملاحظات (اختياري) أو اضغط تخطي:", reply_markup=skip_notes_keyboard())
    return ASK_NOTES

async def notes_or_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return ConversationHandler.END
    if update.callback_query:
        q = update.callback_query; await q.answer()
        _, val = q.data.split("|", 1)
        _draft(context)["notes"] = "-" if val == "-" else val
        await _show_review(update, context, edit=True)
    else:
        txt = (update.message.text or "").strip()
        _draft(context)["notes"] = txt if txt else "-"
        await _show_review(update, context, edit=False)
    return REVIEW

async def _show_review(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool):
    d = _draft(context)
    price = S(context)["price_service"].get_preview_price(d["asset"], d["market"])
    text = build_review_text_with_price(d, price)
    if edit:
        q = update.callback_query
        await q.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=confirm_recommendation_keyboard(),
                                  disable_web_page_preview=True)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML,
                                        reply_markup=confirm_recommendation_keyboard(),
                                        disable_web_page_preview=True)

async def publish_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update, context): return ConversationHandler.END
    q = update.callback_query; await q.answer()
    _, ans = q.data.split("|")
    if ans != "yes":
        await q.edit_message_text("تم الإلغاء.")
        return ConversationHandler.END
    d = _draft(context)
    try:
        trade = S(context)["trade_service"]
        notifier = S(context)["notifier"]
        rec = trade.create(
            asset=d["asset"], side=d["side"], market=d["market"],
            entry=d["entry"], stop_loss=d["stop_loss"], targets=d["targets"],
            notes=d.get("notes","-"), user_id=update.effective_user.id
        )
        ok, ref = notifier.publish_or_update(rec)
        if ok and ref and hasattr(trade, "attach_channel_message"):
            ch_id, msg_id = ref
            rec = trade.attach_channel_message(rec.id, ch_id, msg_id)
        await q.edit_message_text("✅ تم النشر إلى القناة.")
        await q.message.reply_text(
            build_trade_card_text(rec), parse_mode=ParseMode.HTML,
            reply_markup=control_panel_keyboard(rec.id), disable_web_page_preview=True
        )
    except Exception as e:
        log.exception("publish error: %s", e)
        await q.edit_message_text(f"❌ فشل النشر: {e}")
    return ConversationHandler.END

# واجهة بناء الـ ConversationHandler للتسجيل من handlers.py
def build_newrec_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("newrec", newrec_entry)],
        states={
            ASK_ASSET:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_asset)],
            ASK_SIDE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, pick_side)],
            ASK_MARKET:  [MessageHandler(filters.TEXT & ~filters.COMMAND, pick_market)],
            ASK_ENTRY:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_entry)],
            ASK_SL:      [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_sl)],
            ASK_TARGETS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_targets)],
            ASK_NOTES:   [
                MessageHandler(filters.TEXT & ~filters.COMMAND, notes_or_skip),
                CallbackQueryHandler(notes_or_skip, pattern=r"^notes\|"),
            ],
            REVIEW:      [CallbackQueryHandler(publish_decision, pattern=r"^pub\|")],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: None)],
        per_user=True, per_chat=True, per_message=False,
        name="conv_newrec", persistent=False
    )

# دعم on_free_text (لتستقبله handlers.py) عند انتظار قيم SL/TP أو غير ذلك
async def on_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # يُستخدم في management_handlers لتصريف نصوص الوارد بعد طلب SL/TP
    pass

# Callbacks مرتبطة بالإدارة تُسجّل فعليًا من management_handlers
def management_callback_handlers():
    return [
        CallbackQueryHandler(lambda u,c: None, pattern=r"^$")  # placeholder
    ]
--- END OF FILE ---