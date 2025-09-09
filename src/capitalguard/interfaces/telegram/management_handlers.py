# --- START OF COMPLETE FINAL CORRECTED FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
import logging
import types
import re
import unicodedata
from time import time
from typing import Optional, List, Dict

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from .helpers import get_service
from .keyboards import (
    public_channel_keyboard,
    analyst_control_panel_keyboard,
    analyst_edit_menu_keyboard,
    confirm_close_keyboard,
    build_open_recs_keyboard,
)
from .ui_texts import build_trade_card_text
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.trade_service import TradeService
from capitalguard.domain.entities import RecommendationStatus

log = logging.getLogger(__name__)

AWAITING_INPUT_KEY = "awaiting_user_input_for"

# ... (All helper functions like parse_number, etc. are here and unchanged) ...
_AR_TO_EN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_SUFFIXES = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
_SEPARATORS_REGEX = re.compile(r"[,\u060C;:|\t\r\n]+")
def _normalize_text(s: str) -> str:
    if not s: return ""
    s = unicodedata.normalize("NFKC", s).translate(_AR_TO_EN_DIGITS).replace("،", ",")
    return re.sub(r"\s+", " ", s).strip()
def _parse_one_number(token: str) -> float:
    if not token: raise ValueError("قيمة رقمية فارغة")
    t = re.sub(r"^[^\d+-.]+|[^\dA-Z.+-]+$", "", token.strip().upper()).replace(",", "")
    m = re.match(r"^([+\-]?\d+(?:\.\d+)?)([KMB])?$", t)
    if not m: raise ValueError(f"قيمة رقمية غير صالحة: '{token}'")
    num_str, suf = m.groups()
    return float(num_str) * _SUFFIXES.get(suf or "", 1)
def _tokenize_numbers(s: str) -> List[str]:
    s = _SEPARATORS_REGEX.sub(" ", _normalize_text(s))
    return [p for p in s.split(" ") if p]
def _coalesce_num_suffix_tokens(tokens: List[str]) -> List[str]:
    out: List[str] = []; i = 0
    while i < len(tokens):
        cur = tokens[i].strip()
        nxt = tokens[i + 1].strip() if i + 1 < len(tokens) else None
        if nxt and re.fullmatch(r"[KMBkmb]", nxt): out.append(cur + nxt.upper()); i += 2
        else: out.append(cur); i += 1
    return out
def parse_number(s: str) -> float:
    tokens = _coalesce_num_suffix_tokens(_tokenize_numbers(s))
    if not tokens: raise ValueError("لم يتم العثور على قيمة رقمية.")
    return _parse_one_number(tokens[0])
def parse_number_list(s: str) -> List[float]:
    tokens = _coalesce_num_suffix_tokens(_tokenize_numbers(s))
    if not tokens: raise ValueError("لم يتم العثور على أي أرقام.")
    return [_parse_one_number(t) for t in tokens]
def _parse_tail_int(data: str) -> Optional[int]:
    try: return int(data.split(":")[-1])
    except (ValueError, IndexError): return None
def _parse_cq_parts(data: str, expected: int) -> Optional[list]:
    try:
        parts = data.split(":")
        return parts if len(parts) >= expected else None
    except Exception: return None
async def _noop_answer(*args, **kwargs): pass

# --- Telegram Handlers ---

async def navigate_open_recs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = _parse_tail_int(query.data) or 1
    trade_service: TradeService = get_service(context, "trade_service")
    price_service: PriceService = get_service(context, "price_service")
    filters_map = context.user_data.get("last_open_filters", {}) or {}
    user_tg_id = update.effective_user.id
    items = trade_service.repo.list_open_for_user(user_telegram_id=user_tg_id, **filters_map)
    if not items:
        await query.edit_message_text("✅ لا توجد توصيات مفتوحة تطابق الفلتر الحالي.")
        return
    seq_map: Dict[int, int] = {rec.id: i for i, rec in enumerate(items, start=1)}
    keyboard = build_open_recs_keyboard(items, current_page=page, price_service=price_service, seq_map=seq_map)
    header_text = "<b>📊 لوحة قيادة التوصيات المفتوحة</b>"
    if filters_map:
        filter_text_parts = [f"{k.capitalize()}: {str(v).upper()}" for k, v in filters_map.items()]
        header_text += f"\n<i>فلترة حسب: {', '.join(filter_text_parts)}</i>"
    try:
        await query.edit_message_text(f"{header_text}\nاختر توصية لعرض لوحة التحكم الخاصة بها:", reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except BadRequest as e:
        if "Message is not modified" not in str(e): raise e

async def show_rec_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rec_id = _parse_tail_int(query.data)
    if rec_id is None:
        await query.edit_message_text("❌ خطأ: لم يتم العثور على رقم التوصية."); return
    trade_service: TradeService = get_service(context, "trade_service")
    price_service: PriceService = get_service(context, "price_service")
    rec = trade_service.repo.get_by_id_for_user(rec_id, update.effective_user.id)
    if not rec:
        log.warning(f"Security: User {update.effective_user.id} tried to access rec #{rec_id}.")
        await query.edit_message_text(f"❌ لا يمكنك الوصول إلى هذه التوصية."); return
    live_price = price_service.get_cached_price(rec.asset.value, rec.market)
    if live_price: setattr(rec, "live_price", live_price)
    text = build_trade_card_text(rec)
    keyboard = analyst_control_panel_keyboard(rec.id) if rec.status != RecommendationStatus.CLOSED else None
    await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

_RATE_KEY = "pub_rate_limit"; _RATE_WINDOW_SEC = 20
def _recently_updated(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int) -> bool:
    key = (_RATE_KEY, chat_id, message_id)
    last = context.bot_data.get(key)
    now = time()
    if last and (now - last) < _RATE_WINDOW_SEC: return True
    context.bot_data[key] = now
    return False

# ✅ --- FIX: Restored the try...except block correctly ---
async def update_public_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        rec_id = _parse_tail_int(query.data)
        if rec_id is None:
            await query.answer("Bad request.", show_alert=True); return

        if _recently_updated(context, query.message.chat_id, query.message.message_id):
            await query.answer("البيانات محدثة للتو.", show_alert=False); return

        trade_service: TradeService = get_service(context, "trade_service")
        price_service: PriceService = get_service(context, "price_service")
        rec = trade_service.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED:
            await query.answer("الصفقة مغلقة أو غير موجودة.", show_alert=False); return

        live_price = price_service.get_cached_price(rec.asset.value, rec.market)
        if not live_price:
            await query.answer("تعذر جلب السعر.", show_alert=True); return
        
        setattr(rec, "live_price", live_price)
        new_text = build_trade_card_text(rec)
        new_keyboard = public_channel_keyboard(rec.id)

        try:
            await query.edit_message_text(
                text=new_text, reply_markup=new_keyboard,
                parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
            await query.answer("تم التحديث ✅")
        except BadRequest as e:
            if "Message is not modified" in str(e): await query.answer("البيانات محدثة بالفعل.")
            else: raise e
    except Exception as e:
        log.error(f"Error in update_public_card for query data '{getattr(query, 'data', '')}': {e}", exc_info=True)
        try:
            await query.answer("حدث خطأ.", show_alert=True)
        except Exception: pass
# --- END OF FIX ---

async def update_private_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("يجري التحديث...")
    await show_rec_panel_handler(update, context)

async def move_sl_to_be_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer("جاري النقل...")
    rec_id = _parse_tail_int(query.data)
    if rec_id: get_service(context, "trade_service").move_sl_to_be(rec_id)
    await show_rec_panel_handler(update, context)

async def partial_close_note_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer("جاري الإضافة...")
    rec_id = _parse_tail_int(query.data)
    if rec_id: get_service(context, "trade_service").add_partial_close_note(rec_id)
    await show_rec_panel_handler(update, context)

async def start_close_flow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; rec_id = _parse_tail_int(query.data)
    if not rec_id: await query.answer("Bad request.", show_alert=True); return
    context.user_data[AWAITING_INPUT_KEY] = {"action": "close", "rec_id": rec_id, "original_message": query.message}
    await query.answer(); await query.edit_message_text(f"{query.message.text}\n\n<b>🔻 الرجاء <u>الرد</u> بسعر الخروج للتوصية #{rec_id}.</b>", parse_mode=ParseMode.HTML)

async def confirm_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; parts = _parse_cq_parts(query.data, 4)
    if not parts: await query.answer("Bad request.", show_alert=True); return
    try: rec_id, exit_price = int(parts[2]), parse_number(parts[3])
    except (ValueError, IndexError) as e: await query.answer(f"قيمة غير صالحة: {e}", show_alert=True); return
    await query.answer("جاري إغلاق التوصية...")
    trade_service = get_service(context, "trade_service")
    try:
        rec = trade_service.close(rec_id, exit_price)
        await query.edit_message_text(text="✅ تم إغلاق التوصية.\n\n" + build_trade_card_text(rec), parse_mode=ParseMode.HTML, reply_markup=None)
    except Exception as e: await query.edit_message_text(f"❌ فشل: {e}")
    finally: context.user_data.pop(AWAITING_INPUT_KEY, None)

async def cancel_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer(); context.user_data.pop(AWAITING_INPUT_KEY, None); await show_rec_panel_handler(update, context)

async def show_edit_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; rec_id = _parse_tail_int(query.data)
    if rec_id: await query.answer(); await query.edit_message_reply_markup(reply_markup=analyst_edit_menu_keyboard(rec_id))

async def back_to_main_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer(); await show_rec_panel_handler(update, context)

async def start_edit_sl_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; rec_id = _parse_tail_int(query.data)
    if not rec_id: return
    context.user_data[AWAITING_INPUT_KEY] = {"action": "edit_sl", "rec_id": rec_id, "original_message": query.message}
    await query.answer(); await query.edit_message_text(f"{query.message.text}\n\n<b>✏️ <u>الرد</u> بوقف الخسارة الجديد للتوصية #{rec_id}.</b>", parse_mode=ParseMode.HTML)

async def start_edit_tp_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; rec_id = _parse_tail_int(query.data)
    if not rec_id: return
    context.user_data[AWAITING_INPUT_KEY] = {"action": "edit_tp", "rec_id": rec_id, "original_message": query.message}
    await query.answer(); await query.edit_message_text(f"{query.message.text}\n\n<b>🎯 <u>الرد</u> بالأهداف الجديدة للتوصية #{rec_id}.</b>", parse_mode=ParseMode.HTML)

async def received_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AWAITING_INPUT_KEY not in context.user_data or not update.message.reply_to_message: return
    state = context.user_data.get(AWAITING_INPUT_KEY)
    original_message = state.get("original_message")
    if not original_message or update.message.reply_to_message.message_id != original_message.message_id: return
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    action, rec_id, user_input = state["action"], state["rec_id"], update.message.text.strip()
    user_id = update.effective_user.id
    try: await update.message.delete()
    except Exception: pass
    trade_service = get_service(context, "trade_service")
    try:
        if action == "close":
            exit_price = parse_number(user_input)
            await original_message.edit_text(f"هل تؤكد إغلاق <b>#{rec_id}</b> عند <b>{exit_price:g}</b>؟", reply_markup=confirm_close_keyboard(rec_id, exit_price), parse_mode=ParseMode.HTML)
            return
        elif action == "edit_sl":
            trade_service.update_sl(rec_id, parse_number(user_input))
        elif action == "edit_tp":
            new_targets = parse_number_list(user_input)
            if not new_targets: raise ValueError("لم يتم توفير أهداف.")
            trade_service.update_targets(rec_id, new_targets)
    except Exception as e:
        log.error(f"Error processing input for {action} on rec #{rec_id}: {e}", exc_info=True)
        await context.bot.send_message(user_id, f"❌ حدث خطأ: {e}")
    
    rec = trade_service.repo.get(rec_id)
    if not rec:
        await original_message.edit_text("لم يعد ممكنا العثور على هذه التوصية."); return
    price_service: PriceService = get_service(context, "price_service")
    live_price = price_service.get_cached_price(rec.asset.value, rec.market)
    if live_price: setattr(rec, "live_price", live_price)
    text = build_trade_card_text(rec)
    keyboard = analyst_control_panel_keyboard(rec.id) if rec.status != RecommendationStatus.CLOSED else None
    await original_message.edit_text(text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

def register_management_handlers(application: Application):
    application.add_handler(CallbackQueryHandler(navigate_open_recs_handler, pattern=r"^open_nav:page:"))
    application.add_handler(CallbackQueryHandler(show_rec_panel_handler, pattern=r"^rec:show_panel:"))
    application.add_handler(CallbackQueryHandler(update_public_card, pattern=r"^rec:update_public:"))
    application.add_handler(CallbackQueryHandler(update_private_card, pattern=r"^rec:update_private:"))
    application.add_handler(CallbackQueryHandler(move_sl_to_be_handler, pattern=r"^rec:move_be:"))
    application.add_handler(CallbackQueryHandler(partial_close_note_handler, pattern=r"^rec:close_partial:"))
    application.add_handler(CallbackQueryHandler(start_close_flow_handler, pattern=r"^rec:close_start:"))
    application.add_handler(CallbackQueryHandler(show_edit_menu_handler, pattern=r"^rec:edit_menu:"))
    application.add_handler(CallbackQueryHandler(back_to_main_panel_handler, pattern=r"^rec:back_to_main:"))
    application.add_handler(CallbackQueryHandler(start_edit_sl_handler, pattern=r"^rec:edit_sl:"))
    application.add_handler(CallbackQueryHandler(start_edit_tp_handler, pattern=r"^rec:edit_tp:"))
    application.add_handler(CallbackQueryHandler(confirm_close_handler, pattern=r"^rec:confirm_close:"))
    application.add_handler(CallbackQueryHandler(cancel_close_handler, pattern=r"^rec:cancel_close:"))
    application.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, received_input_handler, group=1))
# --- END OF COMPLETE FINAL CORRECTED FILE ---