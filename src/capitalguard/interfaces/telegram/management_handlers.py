# --- START OF FINAL, REVIEWED, AND ROBUST FILE (V14): src/capitalguard/interfaces/telegram/management_handlers.py ---
import logging
import types
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
    ConversationHandler,
    CommandHandler,
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
from .parsers import parse_number, parse_number_list
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.domain.entities import RecommendationStatus

log = logging.getLogger(__name__)

AWAITING_INPUT_KEY = "awaiting_user_input_for"
(PARTIAL_PROFIT_PERCENT, PARTIAL_PROFIT_PRICE) = range(2)

# --- Helper Functions ---
def _parse_tail_int(data: str) -> Optional[int]:
    try: return int(data.split(":")[-1])
    except (ValueError, IndexError): return None

def _parse_cq_parts(data: str, expected: int) -> Optional[list]:
    try:
        parts = data.split(":")
        return parts if len(parts) >= expected else None
    except Exception: return None

async def _noop_answer(*args, **kwargs): return None

def _recently_updated(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int) -> bool:
    key = (f"rate_limit_{chat_id}_{message_id}",)
    last_update = context.bot_data.get(key, 0)
    now = time()
    if (now - last_update) < 20:
        return True
    context.bot_data[key] = now
    return False

def _notify_all_channels(context: ContextTypes.DEFAULT_TYPE, rec_id: int, text: str):
    repo = get_service(context, "trade_service").repo
    notifier = get_service(context, "notifier")
    published_messages = repo.get_published_messages(rec_id)
    for msg_meta in published_messages:
        try:
            notifier.post_notification_reply(
                chat_id=msg_meta.telegram_channel_id, message_id=msg_meta.telegram_message_id, text=text
            )
        except Exception as e:
            log.warning("Failed to send reply notification for rec #%s to channel %s: %s", rec_id, msg_meta.telegram_channel_id, e)

# --- Handler Functions ---

async def navigate_open_recs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = _parse_tail_int(query.data) or 1
    trade_service: TradeService = get_service(context, "trade_service")
    price_service: PriceService = get_service(context, "price_service")
    filters_map = context.user_data.get("last_open_filters", {}) or {}
    items = trade_service.repo.list_open_for_user(user_telegram_id=update.effective_user.id, **filters_map)
    try:
        if not items:
            await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text="✅ لا توجد توصيات مفتوحة تطابق الفلتر الحالي.")
            return
        seq_map = {rec.id: i for i, rec in enumerate(items, start=1)}
        keyboard = build_open_recs_keyboard(items, current_page=page, price_service=price_service, seq_map=seq_map)
        header_text = "<b>📊 لوحة قيادة التوصيات المفتوحة</b>"
        if filters_map:
            filter_text_parts = [f"{k.capitalize()}: {str(v).upper()}" for k, v in filters_map.items()]
            header_text += f"\n<i>فلترة حسب: {', '.join(filter_text_parts)}</i>"
        await context.bot.edit_message_text(
            chat_id=query.message.chat_id, message_id=query.message.message_id,
            text=f"{header_text}\nاختر توصية لعرض لوحة التحكم الخاصة بها:",
            reply_markup=keyboard, parse_mode=ParseMode.HTML
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e): log.warning(f"Error in navigate_open_recs_handler: {e}")
    except Exception as e:
        log.error(f"Unexpected error in navigate_open_recs_handler: {e}", exc_info=True)

async def show_rec_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rec_id = _parse_tail_int(query.data)
    try:
        if rec_id is None:
            await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text="❌ خطأ: لم يتم العثور على رقم التوصية.")
            return
        trade_service: TradeService = get_service(context, "trade_service")
        price_service: PriceService = get_service(context, "price_service")
        rec = trade_service.repo.get_by_id_for_user(rec_id, update.effective_user.id)
        if not rec:
            log.warning("Security: User %s tried to access rec #%s", update.effective_user.id, rec_id)
            await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text=f"❌ لا يمكنك الوصول إلى هذه التوصية.")
            return
        live_price = price_service.get_cached_price(rec.asset.value, rec.market)
        if live_price: setattr(rec, "live_price", live_price)
        text = build_trade_card_text(rec)
        keyboard = analyst_control_panel_keyboard(rec.id) if rec.status != RecommendationStatus.CLOSED else None
        await context.bot.edit_message_text(
            chat_id=query.message.chat_id, message_id=query.message.message_id,
            text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e): log.warning(f"Error in show_rec_panel_handler: {e}")
    except Exception as e:
        log.error(f"Unexpected error in show_rec_panel_handler: {e}", exc_info=True)

async def update_public_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        rec_id = _parse_tail_int(query.data)
        if rec_id is None: await query.answer("Bad request.", show_alert=True); return
        if _recently_updated(context, query.message.chat_id, query.message.message_id):
            await query.answer("البيانات محدثة للتو.", show_alert=False); return
        trade_service: TradeService = get_service(context, "trade_service")
        price_service: PriceService = get_service(context, "price_service")
        rec = trade_service.repo.get(rec_id)
        if not rec: await query.answer("التوصية غير موجودة.", show_alert=True); return
        if rec.status == RecommendationStatus.CLOSED: await query.answer("الصفقة مغلقة بالفعل.", show_alert=False); return
        live_price = price_service.get_cached_price(rec.asset.value, rec.market)
        if not live_price: await query.answer("تعذر جلب السعر.", show_alert=True); return
        setattr(rec, "live_price", live_price)
        new_text = build_trade_card_text(rec)
        new_keyboard = public_channel_keyboard(rec.id)
        await context.bot.edit_message_text(
            chat_id=query.message.chat_id, message_id=query.message.message_id,
            text=new_text, reply_markup=new_keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        await query.answer("تم التحديث ✅")
    except BadRequest as e:
        if "Message is not modified" in str(e): await query.answer("البيانات محدثة بالفعل.")
        else: log.warning(f"Error in update_public_card: {e}")
    except Exception as e:
        log.error(f"Unexpected error in update_public_card: {e}", exc_info=True)
        try: await query.answer("حدث خطأ.", show_alert=True)
        except Exception: pass

async def update_private_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("يجري التحديث...")
    await show_rec_panel_handler(update, context)

async def move_sl_to_be_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("جاري النقل...")
    rec_id = _parse_tail_int(query.data)
    if rec_id:
        trade_service: TradeService = get_service(context, "trade_service")
        rec = trade_service.repo.get(rec_id)
        if rec:
            updated_rec = trade_service.update_sl(rec_id, rec.entry.value)
            if updated_rec:
                notification_text = f"<b>🛡️ تأمين صفقة #{updated_rec.asset.value}</b>\nتم نقل وقف الخسارة إلى نقطة الدخول."
                _notify_all_channels(context, rec_id, notification_text)
    await show_rec_panel_handler(update, context)

async def start_close_flow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if rec_id is None: await query.answer("Bad request.", show_alert=True); return
    context.user_data[AWAITING_INPUT_KEY] = {"action": "close", "rec_id": rec_id, "original_message": query.message}
    await query.answer()
    await context.bot.edit_message_text(
        chat_id=query.message.chat_id, message_id=query.message.message_id,
        text=f"{query.message.text}\n\n<b>🔻 الرجاء <u>الرد على هذه الرسالة ↩️</u> بسعر الخروج للتوصية #{rec_id}.</b>",
        parse_mode=ParseMode.HTML,
    )

async def confirm_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = _parse_cq_parts(query.data, expected=4)
    if not parts: await query.answer("Bad request.", show_alert=True); return
    try: rec_id, exit_price = int(parts[2]), parse_number(parts[3])
    except (ValueError, IndexError) as e: await query.answer(f"قيمة غير صالحة: {e}", show_alert=True); return
    await query.answer("جاري إغلاق التوصية...")
    trade_service: TradeService = get_service(context, "trade_service")
    try:
        rec = trade_service.close(rec_id, exit_price)
        final_text = "✅ تم إغلاق التوصية بنجاح.\n\n" + build_trade_card_text(rec)
        await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text=final_text, parse_mode=ParseMode.HTML, reply_markup=None)
    except Exception as e:
        await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=query.message.message_id, text=f"❌ فشل إغلاق التوصية: {e}")
    finally: context.user_data.pop(AWAITING_INPUT_KEY, None)

async def cancel_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data.pop(AWAITING_INPUT_KEY, None)
    await show_rec_panel_handler(update, context)

async def show_edit_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if rec_id is None: return
    keyboard = analyst_edit_menu_keyboard(rec_id)
    await query.answer()
    await context.bot.edit_message_reply_markup(chat_id=query.message.chat_id, message_id=query.message.message_id, reply_markup=keyboard)

async def back_to_main_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await show_rec_panel_handler(update, context)

async def start_edit_sl_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if rec_id is None: return
    context.user_data[AWAITING_INPUT_KEY] = {"action": "edit_sl", "rec_id": rec_id, "original_message": query.message}
    await query.answer()
    await context.bot.edit_message_text(
        chat_id=query.message.chat_id, message_id=query.message.message_id,
        text=f"{query.message.text}\n\n<b>✏️ الرجاء <u>الرد على هذه الرسالة ↩️</u> بقيمة وقف الخسارة الجديدة للتوصية #{rec_id}.</b>",
        parse_mode=ParseMode.HTML,
    )

async def start_edit_tp_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if rec_id is None: return
    context.user_data[AWAITING_INPUT_KEY] = {"action": "edit_tp", "rec_id": rec_id, "original_message": query.message}
    await query.answer()
    await context.bot.edit_message_text(
        chat_id=query.message.chat_id, message_id=query.message.message_id,
        text=f"{query.message.text}\n\n<b>🎯 الرجاء <u>الرد على هذه الرسالة ↩️</u> بالأهداف الجديدة للتوصية #{rec_id} (افصل بينها بمسافة).</b>",
        parse_mode=ParseMode.HTML,
    )

async def received_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if AWAITING_INPUT_KEY not in context.user_data or not update.message.reply_to_message: return
    state = context.user_data.pop(AWAITING_INPUT_KEY, None)
    if not state: return
    original_message = state.get("original_message")
    if not original_message or update.message.reply_to_message.message_id != original_message.message_id: return
    
    action, rec_id = state["action"], state["rec_id"]
    user_input = update.message.text.strip()
    try: await update.message.delete()
    except Exception: pass
    
    trade_service: TradeService = get_service(context, "trade_service")
    try:
        if action == "close":
            exit_price = parse_number(user_input)
            text = f"هل تؤكد إغلاق <b>#{rec_id}</b> عند <b>{exit_price:g}</b>؟"
            keyboard = confirm_close_keyboard(rec_id, exit_price)
            await context.bot.edit_message_text(
                chat_id=original_message.chat_id, message_id=original_message.message_id,
                text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML
            )
            return
        elif action == "edit_sl":
            new_sl = parse_number(user_input)
            trade_service.update_sl(rec_id, new_sl)
        elif action == "edit_tp":
            new_targets = parse_number_list(user_input)
            if not new_targets: raise ValueError("لم يتم توفير أهداف.")
            trade_service.update_targets(rec_id, new_targets)
        
        dummy_query = types.SimpleNamespace(
            message=original_message, data=f"rec:show_panel:{rec_id}",
            answer=_noop_answer, from_user=update.effective_user
        )
        dummy_update = Update(update.update_id, callback_query=dummy_query)
        await show_rec_panel_handler(dummy_update, context)
    except Exception as e:
        log.error(f"Error processing input for action {action}, rec_id {rec_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ خطأ: {e}")
        dummy_query = types.SimpleNamespace(
            message=original_message, data=f"rec:show_panel:{rec_id}",
            answer=_noop_answer, from_user=update.effective_user
        )
        dummy_update = Update(update.update_id, callback_query=dummy_query)
        await show_rec_panel_handler(dummy_update, context)

# --- Partial Profit Conversation ---
async def partial_profit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    rec_id = _parse_tail_int(query.data)
    if rec_id is None: return ConversationHandler.END
    
    context.user_data['partial_profit_rec_id'] = rec_id
    context.user_data['original_message'] = query.message
    await query.answer()
    await context.bot.edit_message_text(
        chat_id=query.message.chat_id, message_id=query.message.message_id,
        text=f"{query.message.text}\n\n<b>💰 الرجاء الرد بالنسبة المئوية التي تم جنيها (مثال: 50).</b>",
        parse_mode=ParseMode.HTML
    )
    return PARTIAL_PROFIT_PERCENT

async def received_partial_percent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        percentage = parse_number(update.message.text)
        if not (0 < percentage <= 100): raise ValueError("Percentage must be between 1 and 100.")
        context.user_data['partial_profit_percent'] = percentage
        await update.message.reply_text(f"✅ النسبة: {percentage}%. الآن، الرجاء إرسال سعر جني الربح.")
        return PARTIAL_PROFIT_PRICE
    except (ValueError, IndexError) as e:
        await update.message.reply_text(f"❌ قيمة غير صالحة: {e}. الرجاء إرسال رقم فقط.")
        return PARTIAL_PROFIT_PERCENT

async def received_partial_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        price = parse_number(update.message.text)
        rec_id = context.user_data['partial_profit_rec_id']
        percentage = context.user_data['partial_profit_percent']
        original_message = context.user_data['original_message']
        
        trade_service: TradeService = get_service(context, "trade_service")
        rec = trade_service.take_partial_profit(rec_id, percentage, price)
        
        notification_text = f"💰 جني أرباح جزئي لـ #{rec.asset.value} | تم إغلاق {percentage}% من الصفقة عند سعر {price:g}."
        _notify_all_channels(context, rec_id, notification_text)
        
        await update.message.reply_text("✅ تم تسجيل جني الأرباح الجزئي بنجاح.")
        
        dummy_query = types.SimpleNamespace(
            message=original_message, data=f"rec:show_panel:{rec_id}",
            answer=_noop_answer, from_user=update.effective_user
        )
        dummy_update = Update(update.update_id, callback_query=dummy_query)
        await show_rec_panel_handler(dummy_update, context)
        
    except (ValueError, IndexError) as e:
        await update.message.reply_text(f"❌ قيمة غير صالحة: {e}. الرجاء إرسال سعر صحيح.")
        return PARTIAL_PROFIT_PRICE
    except Exception as e:
        log.error(f"Error in partial profit flow: {e}", exc_info=True)
        await update.message.reply_text(f"❌ حدث خطأ: {e}")
    finally:
        for key in ('partial_profit_rec_id', 'partial_profit_percent', 'original_message'):
            context.user_data.pop(key, None)
            
    return ConversationHandler.END

async def cancel_partial_profit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    original_message = context.user_data.get('original_message')
    rec_id = context.user_data.get('partial_profit_rec_id')
    
    for key in ('partial_profit_rec_id', 'partial_profit_percent', 'original_message'):
        context.user_data.pop(key, None)

    await update.message.reply_text("تم إلغاء عملية جني الأرباح.")
    
    if original_message and rec_id:
        dummy_query = types.SimpleNamespace(
            message=original_message, data=f"rec:show_panel:{rec_id}",
            answer=_noop_answer, from_user=update.effective_user
        )
        dummy_update = Update(update.update_id, callback_query=dummy_query)
        await show_rec_panel_handler(dummy_update, context)

    return ConversationHandler.END

def register_management_handlers(application: Application):
    application.add_handler(CallbackQueryHandler(navigate_open_recs_handler, pattern=r"^open_nav:page:"))
    application.add_handler(CallbackQueryHandler(show_rec_panel_handler, pattern=r"^rec:show_panel:"))
    application.add_handler(CallbackQueryHandler(update_public_card, pattern=r"^rec:update_public:"))
    application.add_handler(CallbackQueryHandler(update_private_card, pattern=r"^rec:update_private:"))
    application.add_handler(CallbackQueryHandler(move_sl_to_be_handler, pattern=r"^rec:move_be:"))
    application.add_handler(CallbackQueryHandler(start_close_flow_handler, pattern=r"^rec:close_start:"))
    application.add_handler(CallbackQueryHandler(show_edit_menu_handler, pattern=r"^rec:edit_menu:"))
    application.add_handler(CallbackQueryHandler(back_to_main_panel_handler, pattern=r"^rec:back_to_main:"))
    application.add_handler(CallbackQueryHandler(start_edit_sl_handler, pattern=r"^rec:edit_sl:"))
    application.add_handler(CallbackQueryHandler(start_edit_tp_handler, pattern=r"^rec:edit_tp:"))
    application.add_handler(CallbackQueryHandler(confirm_close_handler, pattern=r"^rec:confirm_close:"))
    application.add_handler(CallbackQueryHandler(cancel_close_handler, pattern=r"^rec:cancel_close:"))
    
    partial_profit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(partial_profit_start, pattern=r"^rec:close_partial:")],
        states={
            PARTIAL_PROFIT_PERCENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_partial_percent)],
            PARTIAL_PROFIT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_partial_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel_partial_profit)],
        name="partial_profit_conversation",
        per_user=True, per_chat=False, per_message=False,
    )
    application.add_handler(partial_profit_conv)
    
    application.add_handler(
        MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, received_input_handler),
        group=1
    )
# --- END OF FINAL, CORRECTED FILE (V8) ---