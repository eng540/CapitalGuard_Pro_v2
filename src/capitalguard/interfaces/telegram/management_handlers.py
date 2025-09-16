# --- START OF FINAL, APPROVED, AND PRODUCTION-READY FILE ---
# src/capitalguard/interfaces/telegram/management_handlers.py

import logging
from typing import Optional

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
import time
import asyncio

from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.trade_service import TradeService
from capitalguard.domain.entities import ExitStrategy, RecommendationStatus
from capitalguard.interfaces.telegram.helpers import get_service
from capitalguard.interfaces.telegram.keyboards import (
    analyst_control_panel_keyboard,
    analyst_edit_menu_keyboard,
    build_close_options_keyboard,
    build_exit_strategy_keyboard,
    build_open_recs_keyboard,
    public_channel_keyboard
)
from capitalguard.interfaces.telegram.parsers import parse_number, parse_targets_list
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text

log = logging.getLogger(__name__)

# --- Conversation Handler States ---
(
    MAIN_PANEL, EDIT_MENU, STRATEGY_MENU, CLOSE_MENU,
    AWAIT_MANUAL_PRICE_INPUT, AWAIT_SL_INPUT, AWAIT_TP_INPUT, AWAIT_PROFIT_STOP_INPUT,
    AWAIT_PARTIAL_PERCENT_INPUT, AWAIT_PARTIAL_PRICE_INPUT, CONFIRM_MARKET_CLOSE,
) = range(11)

# --- Helper Functions ---

def _recently_updated(context: ContextTypes.DEFAULT_TYPE, key: str, duration_seconds: int = 2) -> bool:
    """A more generic rate-limiter to prevent spamming buttons."""
    last_update = context.bot_data.get(key, 0)
    now = time.time()
    if (now - last_update) < duration_seconds:
        return True
    context.bot_data[key] = now
    return False

async def _update_ui_panel(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, rec_id: int, user_id: str,
    custom_text: Optional[str] = None, keyboard: Optional[InlineKeyboardMarkup] = None,
) -> bool:
    """Centralized function to refresh the control panel UI, ensuring user ownership."""
    trade_service: TradeService = get_service(context, "trade_service")
    price_service: PriceService = get_service(context, "price_service")
    try:
        rec = trade_service.get_recommendation_for_user(rec_id, user_id)
        if not rec:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="❌ **خطأ:** التوصية غير موجودة أو لا تملك صلاحية الوصول إليها.", parse_mode=ParseMode.HTML)
            return False

        final_text = custom_text
        if not final_text:
            live_price = await price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True)
            if live_price: setattr(rec, "live_price", live_price)
            final_text = build_trade_card_text(rec)

        final_keyboard = keyboard if keyboard is not None else (analyst_control_panel_keyboard(rec_id) if rec.status != RecommendationStatus.CLOSED else None)
        
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=final_text,
            reply_markup=final_keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        return True
    except BadRequest as e:
        if "Message is not modified" not in str(e): log.warning(f"Failed to edit message for rec panel {rec_id}: {e}")
    except Exception as e:
        log.exception(f"Unexpected error in _update_ui_panel for rec {rec_id}")
        await context.bot.send_message(chat_id=chat_id, text=f"حدث خطأ غير متوقع أثناء تحديث الواجهة: {e}")
    return False

# --- Conversation Entry Point & General Handlers ---

async def show_rec_panel_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for the recommendation management conversation."""
    query = update.callback_query
    await query.answer("⏳ جارٍ التحميل...")
    try:
        rec_id = int(query.data.split(":")[-1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ طلب غير صالح.")
        return ConversationHandler.END

    context.user_data["managed_rec_id"] = rec_id
    context.user_data["original_message_id"] = query.message.message_id
    
    success = await _update_ui_panel(context, query.message.chat_id, query.message.message_id, rec_id, str(query.from_user.id))
    return MAIN_PANEL if success else ConversationHandler.END

async def back_to_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Returns the user to the main control panel view from any sub-menu."""
    query = update.callback_query
    await query.answer()
    rec_id = context.user_data.get("managed_rec_id")
    await _update_ui_panel(context, query.message.chat_id, query.message.message_id, rec_id, str(query.from_user.id))
    return MAIN_PANEL

# --- Menu Navigation ---

async def navigate_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, menu_builder, new_state: int, menu_text: str) -> int:
    query = update.callback_query
    await query.answer()
    rec_id = context.user_data.get("managed_rec_id")
    trade_service: TradeService = get_service(context, "trade_service")
    
    rec = trade_service.get_recommendation_for_user(rec_id, str(query.from_user.id))
    if not rec:
        await query.edit_message_text("❌ التوصية لم تعد موجودة.")
        return ConversationHandler.END
        
    keyboard = menu_builder(rec)
    await query.edit_message_text(menu_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    return new_state

# --- Input Prompting & Handling ---

async def prompt_for_input(update: Update, context: ContextTypes.DEFAULT_TYPE, next_state: int, prompt_text: str) -> int:
    query = update.callback_query
    await query.answer()
    rec_id = context.user_data.get("managed_rec_id")
    full_prompt = (
        f"{query.message.text_html}\n\n"
        f"<b>--- ⏳ في انتظار الرد ---</b>\n"
        f"✍️ {prompt_text} للتوصية #{rec_id}.\n"
        f"<i>أو استخدم /cancel للإلغاء.</i>"
    )
    await query.edit_message_text(full_prompt, parse_mode=ParseMode.HTML)
    return next_state

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE, service_method_name: str, success_message: str, parse_func, is_close_action: bool = False) -> int:
    trade_service: TradeService = get_service(context, "trade_service")
    rec_id = context.user_data.get("managed_rec_id")
    original_message_id = context.user_data.get("original_message_id")
    user_id = str(update.effective_user.id)
    
    processing_msg = await update.message.reply_text("⚡ جارٍ معالجة الإدخال...")
    
    try:
        parsed_value = parse_func(update.message.text)
        service_method = getattr(trade_service, service_method_name)
        
        # Handle methods that might require extra arguments, like the close reason
        if is_close_action:
            service_method(rec_id, user_id, parsed_value, reason="MANUAL_PRICE_CLOSE")
        else:
            service_method(rec_id, user_id, parsed_value)
            
        await processing_msg.edit_text(f"✅ {success_message}")
        
    except Exception as e:
        log.warning(f"Input handling failed for {service_method_name}: {e}")
        await processing_msg.edit_text(f"❌ خطأ: {e}")
    finally:
        await asyncio.sleep(2) # Give user time to read the feedback
        try:
            await update.message.delete()
            await processing_msg.delete()
        except BadRequest:
            pass
        await _update_ui_panel(context, update.message.chat_id, original_message_id, rec_id, user_id)
        
    return MAIN_PANEL if not is_close_action else ConversationHandler.END


# --- Specific Action Handlers ---

async def set_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("⏳ جارٍ تغيير الاستراتيجية...")
    rec_id = context.user_data.get("managed_rec_id")
    user_id = str(query.from_user.id)
    trade_service: TradeService = get_service(context, "trade_service")
    try:
        strategy_value = query.data.split(":")[-1]
        trade_service.update_exit_strategy(rec_id, user_id, ExitStrategy(strategy_value))
    except Exception as e:
        await query.answer(f"❌ فشل: {e}", show_alert=True)
    
    return await navigate_to_menu(update, context, build_exit_strategy_keyboard, STRATEGY_MENU, "📈 **إدارة استراتيجية الخروج**")

async def confirm_market_close_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("⏳ جارٍ الإغلاق...")
    rec_id = context.user_data.get("managed_rec_id")
    user_id = str(query.from_user.id)
    trade_service: TradeService = get_service(context, "trade_service")
    try:
        trade_service.close_recommendation_at_market_for_user(rec_id, user_id)
        await _update_ui_panel(context, query.message.chat_id, query.message.message_id, rec_id, user_id)
    except Exception as e:
        await query.edit_message_text(f"❌ فشل الإغلاق: {e}")
    
    context.user_data.clear()
    return ConversationHandler.END

async def received_partial_percent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        percentage = parse_number(update.message.text)
        if not (0 < percentage <= 100): raise ValueError("النسبة يجب أن تكون بين 1 و 100.")
        context.user_data['partial_profit_percent'] = percentage
        await update.message.reply_text(f"✅ النسبة: {percentage}%. الآن، أرسل السعر الذي تم عنده جني الربح.")
        return AWAIT_PARTIAL_PRICE_INPUT
    except (ValueError, IndexError) as e:
        await update.message.reply_text(f"❌ قيمة غير صالحة: {e}. أرسل رقمًا صحيحًا.")
        return AWAIT_PARTIAL_PERCENT_INPUT

async def received_partial_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    rec_id = context.user_data.get("managed_rec_id")
    original_message_id = context.user_data.get("original_message_id")
    user_id = str(update.effective_user.id)
    trade_service: TradeService = get_service(context, "trade_service")
    
    processing_msg = await update.message.reply_text("⚡ جارٍ تسجيل جني الربح الجزئي...")
    try:
        price = parse_number(update.message.text)
        percentage = context.user_data['partial_profit_percent']
        trade_service.take_partial_profit_for_user(rec_id, user_id, percentage, price)
        await processing_msg.edit_text("✅ تم تسجيل العملية بنجاح.")
    except Exception as e:
        await processing_msg.edit_text(f"❌ حدث خطأ: {e}")
    finally:
        await asyncio.sleep(2)
        try:
             await update.message.delete()
             await processing_msg.delete()
        except BadRequest:
            pass
        await _update_ui_panel(context, update.message.chat_id, original_message_id, rec_id, user_id)
        context.user_data.pop('partial_profit_percent', None)
    return MAIN_PANEL

# --- Standalone Public Card Update ---

async def update_public_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the refresh button on public channel messages. Read-only and rate-limited."""
    query = update.callback_query
    try:
        rec_id = int(query.data.split(":")[-1])
    except (ValueError, IndexError):
        await query.answer("طلب غير صالح.", show_alert=True); return

    if _recently_updated(context, f"public_card_{query.message.chat_id}_{query.message.message_id}", duration_seconds=15):
        await query.answer("البيانات محدثة بالفعل. يرجى المحاولة بعد قليل.", show_alert=False); return

    await query.answer("جارٍ تحديث السعر...")
    trade_service: TradeService = get_service(context, "trade_service")
    price_service: PriceService = get_service(context, "price_service")
    try:
        # Note: Uses a read-only, non-user-specific getter. This is a deliberate design choice.
        rec = trade_service.get_recommendation_public(rec_id)
        if not rec:
            await query.answer("لم يتم العثور على التوصية.", show_alert=True); return
        if rec.status == RecommendationStatus.CLOSED:
            await query.answer("هذه الصفقة مغلقة.", show_alert=False); return

        live_price = await price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True)
        if not live_price:
            await query.answer("لا يمكن جلب السعر الحالي.", show_alert=True); return

        setattr(rec, "live_price", live_price)
        new_text = build_trade_card_text(rec)
        await query.edit_message_text(text=new_text, reply_markup=public_channel_keyboard(rec_id), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e:
        log.error(f"Error updating public card for rec {rec_id}: {e}", exc_info=True)
        await query.answer("حدث خطأ أثناء التحديث.", show_alert=True)

# --- Conversation Termination ---

async def conversation_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rec_id = context.user_data.get("managed_rec_id")
    chat_id = context._chat_id
    if rec_id and chat_id:
        await context.bot.send_message(chat_id=chat_id, text=f"⏰ انتهت مهلة جلسة التعديل للتوصية #{rec_id}.")
    context.user_data.clear()

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("تم إلغاء العملية الحالية.")
    rec_id = context.user_data.get("managed_rec_id")
    original_message_id = context.user_data.get("original_message_id")
    if rec_id and original_message_id:
        await _update_ui_panel(context, update.message.chat_id, original_message_id, rec_id, str(update.effective_user.id))
    context.user_data.clear()
    return ConversationHandler.END

# --- Handler Registration ---

def register_management_handlers(application: Application):
    """Registers the main conversation handler for managing recommendations."""
    management_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(show_rec_panel_entry, pattern=r"^rec:show_panel:")],
        states={
            MAIN_PANEL: [
                CallbackQueryHandler(back_to_main_panel, pattern=r"^rec:update_private:"),
                CallbackQueryHandler(lambda u, c: navigate_to_menu(u, c, lambda rec: analyst_edit_menu_keyboard(rec.id), EDIT_MENU, "✏️ **قائمة التعديل**"), pattern=r"^rec:edit_menu:"),
                CallbackQueryHandler(lambda u, c: navigate_to_menu(u, c, build_exit_strategy_keyboard, STRATEGY_MENU, "📈 **إدارة استراتيجية الخروج**"), pattern=r"^rec:strategy_menu:"),
                CallbackQueryHandler(lambda u, c: navigate_to_menu(u, c, lambda rec: build_close_options_keyboard(rec.id), CLOSE_MENU, "❌ **قائمة الإغلاق**"), pattern=r"^rec:close_menu:"),
                CallbackQueryHandler(lambda u, c: prompt_for_input(u, c, AWAIT_PARTIAL_PERCENT_INPUT, "أرسل **النسبة المئوية** من الصفقة التي تريد إغلاقها"), pattern=r"^rec:close_partial:"),
            ],
            EDIT_MENU: [CallbackQueryHandler(lambda u, c: prompt_for_input(u, c, AWAIT_SL_INPUT, "أرسل **وقف الخسارة** الجديد"), pattern=r"^rec:edit_sl:"), CallbackQueryHandler(lambda u, c: prompt_for_input(u, c, AWAIT_TP_INPUT, "أرسل **الأهداف** الجديدة (مفصولة بمسافات)"), pattern=r"^rec:edit_tp:")],
            STRATEGY_MENU: [CallbackQueryHandler(set_strategy, pattern=r"^rec:set_strategy:"), CallbackQueryHandler(lambda u, c: prompt_for_input(u, c, AWAIT_PROFIT_STOP_INPUT, "أرسل **سعر وقف الربح** الجديد (أو 'remove' للإزالة)"), pattern=r"^rec:set_profit_stop:")],
            CLOSE_MENU: [
                 CallbackQueryHandler(lambda u, c: navigate_to_menu(u, c, lambda rec: InlineKeyboardMarkup([[InlineKeyboardButton("✅ نعم، تأكيد الإغلاق الفوري", callback_data="confirm_now"), InlineKeyboardButton("➡️ تراجع", callback_data="back_to_main")]]), CONFIRM_MARKET_CLOSE, "⚠️ **تأكيد الإغلاق بسعر السوق؟**\nهذا الإجراء نهائي."), pattern=r"^rec:close_market:"),
                 CallbackQueryHandler(lambda u, c: prompt_for_input(u, c, AWAIT_MANUAL_PRICE_INPUT, "أرسل **سعر الإغلاق** اليدوي"), pattern=r"^rec:close_manual:"),
            ],
            CONFIRM_MARKET_CLOSE: [CallbackQueryHandler(confirm_market_close_action, pattern=r"^confirm_now$"), CallbackQueryHandler(back_to_main_panel, pattern=r"^back_to_main$")],
            AWAIT_SL_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_text_input(u, c, "update_sl_for_user", "تم تحديث وقف الخسارة.", parse_number))],
            AWAIT_TP_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_text_input(u, c, "update_targets_for_user", "تم تحديث الأهداف.", lambda t: parse_targets_list(t.split())))],
            AWAIT_PROFIT_STOP_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_text_input(u, c, "update_profit_stop_for_user", "تم تحديث وقف الربح.", lambda t: None if t.lower().strip() == 'remove' else parse_number(t)))],
            AWAIT_MANUAL_PRICE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_text_input(u, c, "close_recommendation_for_user", "تم تسجيل الإغلاق.", parse_number, is_close_action=True))],
            AWAIT_PARTIAL_PERCENT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_partial_percent)],
            AWAIT_PARTIAL_PRICE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_partial_price)],
        },
        fallbacks=[CallbackQueryHandler(back_to_main_panel, pattern=r"^rec:back_to_main:"), CommandHandler("cancel", cancel_conversation)],
        conversation_timeout=600, name="recommendation_management", per_user=True, per_chat=True,
    )
    application.add_handler(management_conv)
    application.add_handler(CallbackQueryHandler(navigate_open_recs_handler, pattern=r"^open_nav:page:"))
    application.add_handler(CallbackQueryHandler(update_public_card, pattern=r"^rec:update_public:"))