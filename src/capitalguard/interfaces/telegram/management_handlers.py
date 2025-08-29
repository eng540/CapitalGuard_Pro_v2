# --- START OF FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
from __future__ import annotations
from typing import Dict, Any, List, Optional
import logging

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, CommandHandler, filters

from capitalguard.domain.entities import Recommendation
from capitalguard.application.services.trade_service import TradeService
from .keyboards import (
    control_panel_keyboard,
    side_reply_keyboard,
    remove_reply_keyboard,
)

log = logging.getLogger(__name__)

# =========================
# أدوات مشتركة / صلاحيات
# =========================

def _allowed_ids(context: ContextTypes.DEFAULT_TYPE) -> List[int]:
    raw = (context.application.bot_data.get("settings_allowed_users")  # إن وُضع مسبقًا
           or context.application.bot_data.get("TELEGRAM_ALLOWED_USERS")
           or "")
    if not raw:
        # أثناء التطوير: السماح للجميع (يمكنك إلزامه لاحقًا)
        return []
    parts = [p.strip() for p in str(raw).replace(",", " ").split() if p.strip()]
    out: List[int] = []
    for p in parts:
        try:
            out.append(int(p))
        except Exception:
            pass
    return out

def _is_authorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_chat and update.effective_chat.type != ChatType.PRIVATE:
        return False
    allowed = _allowed_ids(context)
    if not allowed:
        return True
    uid = update.effective_user.id if update.effective_user else None
    return bool(uid and uid in allowed)

def _svc(context: ContextTypes.DEFAULT_TYPE) -> TradeService:
    return context.application.bot_data["trade_service"]  # type: ignore

def _fmt_row(r: Recommendation) -> str:
    sym = getattr(getattr(r, "asset", None), "value", getattr(r, "asset", ""))
    side = getattr(getattr(r, "side", None), "value", getattr(r, "side", ""))
    status = getattr(r, "status", "-")
    entry = getattr(getattr(r, "entry", None), "value", getattr(r, "entry", "-"))
    sl = getattr(getattr(r, "stop_loss", None), "value", getattr(r, "stop_loss", "-"))
    tps = getattr(getattr(r, "targets", None), "values", getattr(r, "targets", [])) or []
    tps_str = " • ".join(str(x) for x in tps[:4]) + (" …" if len(tps) > 4 else "")
    return f"#{r.id} — {sym} ({side})\nEntry: {entry} | SL: {sl}\nTPs: {tps_str}\nStatus: {status}"

# =========================
# أوامر نصية عامة
# =========================

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update, context):
        return
    msg = (
        "👋 الأوامر المتاحة:\n"
        "• /newrec — إنشاء توصية جديدة\n"
        "• /open — عرض التوصيات المفتوحة (موجز)\n"
        "• /list [SYMBOL] [STATUS] — تصفية (مثال: /list BTCUSDT OPEN)\n"
        "• /analytics — لمحة سريعة عن أرقام اليوم\n\n"
        "ملاحظة: الإدارة (تعديل SL/الأهداف/الإغلاق) تتم من لوحة التحكّم الخاصة التي تصلك بعد النشر."
    )
    await update.effective_message.reply_text(msg)

async def list_open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update, context):
        return
    svc = _svc(context)
    items = svc.list_open()
    if not items:
        await update.effective_message.reply_text("لا توجد توصيات مفتوحة حاليًا.")
        return
    # تقسيم دفعات كي لا نتجاوز حدود تيليجرام
    chunks: List[str] = []
    buf: List[str] = []
    total = 0
    for r in items:
        txt = _fmt_row(r)
        if sum(len(x)+1 for x in buf) + len(txt) > 3500:
            chunks.append("\n\n".join(buf))
            buf = []
        buf.append(txt); total += 1
    if buf:
        chunks.append("\n\n".join(buf))
    for ch in chunks:
        await update.effective_message.reply_text(ch)

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update, context):
        return
    args = context.args or []
    symbol = None
    status = None
    if args:
        symbol = args[0].upper()
    if len(args) >= 2:
        status = args[1].upper()
    svc = _svc(context)
    items = svc.list_all(symbol=symbol, status=status)
    if not items:
        await update.effective_message.reply_text("لا توجد نتائج مطابقة.")
        return
    chunks: List[str] = []
    buf: List[str] = []
    for r in items:
        txt = _fmt_row(r)
        if sum(len(x)+1 for x in buf) + len(txt) > 3500:
            chunks.append("\n\n".join(buf)); buf = []
        buf.append(txt)
    if buf:
        chunks.append("\n\n".join(buf))
    for ch in chunks:
        await update.effective_message.reply_text(ch)

async def analytics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update, context):
        return
    svc = _svc(context)
    all_recs = svc.list_all()
    open_cnt = len([r for r in all_recs if r.status.upper() == "OPEN"])
    closed_cnt = len([r for r in all_recs if r.status.upper() == "CLOSED"])
    msg = (
        "📈 لمحة سريعة:\n"
        f"• مفتوحة: {open_cnt}\n"
        f"• مغلقة : {closed_cnt}\n"
        "— مزيد من التحليلات التفصيلية سنضيفها لاحقًا."
    )
    await update.effective_message.reply_text(msg)

# ==================================
# (القسم الموجود سابقًا) لوحات الإدارة
# ==================================

# مفاتيح الحالات المؤقتة في user_data
AWAITING_CLOSE_PRICE_KEY = "await_close_price_for"
AWAITING_NEW_SL_KEY = "await_new_sl_for"
AWAITING_NEW_TPS_KEY = "await_new_tps_for"

def register_management_callbacks(app) -> None:
    """
    تسجيل أزرار لوحة التحكّم الخاصة (Inline) التي تصل للمحلّل في الخاص.
    """
    app.add_handler(CallbackQueryHandler(click_close_now, pattern=r"^rec:close:(\d+)$"))
    app.add_handler(CallbackQueryHandler(click_amend_sl, pattern=r"^rec:amend_sl:(\d+)$"))
    app.add_handler(CallbackQueryHandler(click_amend_tp, pattern=r"^rec:amend_tp:(\d+)$"))

    # استقبال القيم النصية بعد الضغط على الأزرار
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, receive_followup_values))


def _expect_for(user_data: Dict[str, Any], key: str, rec_id: Optional[int] = None) -> Optional[int]:
    if rec_id is None:
        return user_data.get(key)
    user_data[key] = rec_id
    # حذف المفاتيح الأخرى لتجنّب تداخل الطلبات
    for k in (AWAITING_CLOSE_PRICE_KEY, AWAITING_NEW_SL_KEY, AWAITING_NEW_TPS_KEY):
        if k != key and k in user_data:
            user_data.pop(k, None)
    return rec_id

def _ensure_private_and_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not _is_authorized(update, context):
        return False
    if update.effective_chat and update.effective_chat.type != ChatType.PRIVATE:
        return False
    return True


# --------- أزرار لوحة الإدارة ---------

async def click_close_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _ensure_private_and_auth(update, context):
        return
    query = update.callback_query
    await query.answer()
    rec_id = int(query.data.split(":")[-1])
    _expect_for(context.user_data, AWAITING_CLOSE_PRICE_KEY, rec_id)
    await query.edit_message_text("⛔ أرسل الآن **سعر الخروج** لإغلاق التوصية:", parse_mode="Markdown")

async def click_amend_sl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _ensure_private_and_auth(update, context):
        return
    query = update.callback_query
    await query.answer()
    rec_id = int(query.data.split(":")[-1])
    _expect_for(context.user_data, AWAITING_NEW_SL_KEY, rec_id)
    await query.edit_message_text("🛡️ أرسل قيمة **SL الجديدة**:", parse_mode="Markdown")

async def click_amend_tp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _ensure_private_and_auth(update, context):
        return
    query = update.callback_query
    await query.answer()
    rec_id = int(query.data.split(":")[-1])
    _expect_for(context.user_data, AWAITING_NEW_TPS_KEY, rec_id)
    await query.edit_message_text("🎯 أرسل الأهداف الجديدة **مفصولة بمسافة أو فاصلة**:", parse_mode="Markdown")


# --------- استقبال النص بعد الضغط على الأزرار ---------

def _parse_floats(text: str) -> List[float]:
    parts = [p for p in text.replace(",", " ").split() if p.strip()]
    out: List[float] = []
    for p in parts:
        try:
            out.append(float(p))
        except Exception:
            pass
    return out

async def receive_followup_values(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _ensure_private_and_auth(update, context):
        return
    text = (update.effective_message.text or "").strip()
    svc = _svc(context)

    # إغلاق
    rec_id = context.user_data.get(AWAITING_CLOSE_PRICE_KEY)
    if rec_id:
        try:
            price = float(text)
            svc.close(int(rec_id), price)
            await update.effective_message.reply_text(f"✅ تم إغلاق التوصية #{rec_id} على {price}.")
        except Exception as e:
            await update.effective_message.reply_text(f"❌ فشل الإغلاق: {e}")
        finally:
            context.user_data.pop(AWAITING_CLOSE_PRICE_KEY, None)
        return

    # SL
    rec_id = context.user_data.get(AWAITING_NEW_SL_KEY)
    if rec_id:
        try:
            sl = float(text)
            svc.update_stop_loss(int(rec_id), sl)
            await update.effective_message.reply_text(f"✅ تم تحديث SL للتوصية #{rec_id} إلى {sl}.")
        except Exception as e:
            await update.effective_message.reply_text(f"❌ فشل تحديث SL: {e}")
        finally:
            context.user_data.pop(AWAITING_NEW_SL_KEY, None)
        return

    # TPs
    rec_id = context.user_data.get(AWAITING_NEW_TPS_KEY)
    if rec_id:
        try:
            vals = _parse_floats(text)
            if not vals:
                raise ValueError("لم يتم اكتشاف أرقام صالحة.")
            svc.update_targets(int(rec_id), vals)
            await update.effective_message.reply_text(f"✅ تم تحديث الأهداف للتوصية #{rec_id}.")
        except Exception as e:
            await update.effective_message.reply_text(f"❌ فشل تحديث الأهداف: {e}")
        finally:
            context.user_data.pop(AWAITING_NEW_TPS_KEY, None)
        return