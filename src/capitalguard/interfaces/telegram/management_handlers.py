# src/capitalguard/interfaces/telegram/management_handlers.py

import logging
from typing import Optional
import asyncio
import time
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
)
from capitalguard.interfaces.telegram.parsers import parse_number, parse_targets_list
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text

log = logging.getLogger(__name__)

# --- Conversation Handler States ---
(
    MAIN_PANEL,
    EDIT_MENU,
    STRATEGY_MENU,
    CLOSE_MENU,
    AWAIT_MANUAL_PRICE_INPUT,
    AWAIT_SL_INPUT,
    AWAIT_TP_INPUT,
    AWAIT_PROFIT_STOP_INPUT,
    AWAIT_PARTIAL_PERCENT_INPUT,
    AWAIT_PARTIAL_PRICE_INPUT,
    CONFIRM_MARKET_CLOSE,
) = range(11)

# --- Input State Management System ---
INPUT_TIMEOUT_SECONDS = 300  # 5 دقائق

class InputStateManager:
    """مدير مركزي لحالة الإدخالات لمنع التراكم"""
    
    def __init__(self):
        self.active_sessions = {}
        self.max_sessions_per_user = 3
    
    def start_session(self, user_id: str, session_id: str, data: dict) -> bool:
        """بدء جلسة جديدة"""
        if user_id not in self.active_sessions:
            self.active_sessions[user_id] = {}
        
        # التحقق من الحد الأقصى للجلسات
        if len(self.active_sessions[user_id]) >= self.max_sessions_per_user:
            return False
        
        self.active_sessions[user_id][session_id] = {
            **data,
            "start_time": time.time(),
            "last_activity": time.time()
        }
        return True
    
    def end_session(self, user_id: str, session_id: str):
        """إنهاء جلسة"""
        if user_id in self.active_sessions and session_id in self.active_sessions[user_id]:
            del self.active_sessions[user_id][session_id]
            if not self.active_sessions[user_id]:
                del self.active_sessions[user_id]
    
    def cleanup_expired_sessions(self):
        """تنظيف الجلسات المنتهية"""
        current_time = time.time()
        expired_sessions = []
        
        for user_id, sessions in self.active_sessions.items():
            for session_id, session_data in sessions.items():
                if current_time - session_data["start_time"] > INPUT_TIMEOUT_SECONDS:
                    expired_sessions.append((user_id, session_id))
        
        for user_id, session_id in expired_sessions:
            self.end_session(user_id, session_id)

# مدير الحالة العالمي
input_state_manager = InputStateManager()

# --- Helper Functions ---

def _recently_updated(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int) -> bool:
    """منع الطلبات المتكررة بسرعة"""
    key = f"rate_limit_{chat_id}_{message_id}"
    last_update = context.bot_data.get(key, 0)
    now = time.time()
    if (now - last_update) < 2:
        return True
    context.bot_data[key] = now
    return False

async def _update_ui_panel(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    rec_id: int,
    user_id: str,
    custom_text: Optional[str] = None,
    keyboard: Optional[InlineKeyboardMarkup] = None,
) -> bool:
    """
    Centralized function to refresh the control panel UI.
    It fetches the latest recommendation data from the service layer and edits the message.
    Returns True on success, False on failure (e.g., recommendation not found).
    """
    trade_service: TradeService = get_service(context, "trade_service")
    price_service: PriceService = get_service(context, "price_service")
    try:
        rec = trade_service.get_recommendation_for_user(rec_id, user_id)
        if not rec:
            await context.bot.edit_message_text(
                chat_id=chat_id, 
                message_id=message_id, 
                text="❌ **خطأ:** التوصية غير موجودة أو لا تملك صلاحية الوصول إليها.",
                parse_mode=ParseMode.HTML
            )
            return False

        final_text = custom_text
        if not final_text:
            live_price = await price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True)
            if live_price:
                setattr(rec, "live_price", live_price)
            final_text = build_trade_card_text(rec)

        final_keyboard = keyboard if keyboard is not None else (
            analyst_control_panel_keyboard(rec_id) if rec.status != RecommendationStatus.CLOSED else None
        )
        
        await context.bot.edit_message_text(
            chat_id=chat_id, 
            message_id=message_id, 
            text=final_text,
            reply_markup=final_keyboard, 
            parse_mode=ParseMode.HTML, 
            disable_web_page_preview=True
        )
        return True
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            log.warning(f"Failed to edit message for rec panel {rec_id}: {e}")
    except Exception as e:
        log.exception(f"Unexpected error in _update_ui_panel for rec {rec_id}")
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"حدث خطأ غير متوقع أثناء تحديث الواجهة: {e}",
            parse_mode=ParseMode.HTML
        )
    return False

async def _show_visual_feedback(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message: str, duration: int = 2):
    """إظهار تأثير بصري مؤقت"""
    try:
        temp_message = await context.bot.send_message(chat_id=chat_id, text=f"✨ {message}")
        await asyncio.sleep(duration)
        await temp_message.delete()
    except:
        pass

# --- Conversation Entry Point & General Handlers ---

async def show_rec_panel_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for the recommendation management conversation."""
    query = update.callback_query
    await query.answer()
    
    # إظهار مؤشر التحميل
    await query.edit_message_text("⏳ جارٍ تحميل بيانات التوصية...", parse_mode=ParseMode.HTML)
    
    try:
        rec_id = int(query.data.split(":")[-1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ طلب غير صالح.", parse_mode=ParseMode.HTML)
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
    """Generic helper to navigate to a sub-menu safely."""
    query = update.callback_query
    await query.answer()
    rec_id = context.user_data.get("managed_rec_id")
    trade_service: TradeService = get_service(context, "trade_service")
    
    # إظهار مؤشر التحميل
    loading_text = f"{menu_text}\n\n🔄 جارٍ تحميل الخيارات..."
    await query.edit_message_text(loading_text, parse_mode=ParseMode.HTML)
    
    rec = trade_service.get_recommendation_for_user(rec_id, str(query.from_user.id))
    if not rec:
        await query.edit_message_text("❌ التوصية لم تعد موجودة.", parse_mode=ParseMode.HTML)
        return ConversationHandler.END
        
    keyboard = menu_builder(rec)
    await query.edit_message_text(menu_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    return new_state

# --- Input Prompting & Handling ---

async def prompt_for_input(update: Update, context: ContextTypes.DEFAULT_TYPE, next_state: int, prompt_text: str) -> int:
    """Generic helper to ask the user for text input via a reply."""
    query = update.callback_query
    await query.answer()
    rec_id = context.user_data.get("managed_rec_id")
    
    enhanced_prompt = (
        f"{query.message.text_html}\n\n"
        f"⚡ <b>إدخال سريع</b>\n"
        f"📝 {prompt_text}\n"
        f"⏱️ <i>الوقت المتبقي: 5 دقائق</i>\n"
        f"↩️ <b>يرجى الرد على هذه الرسالة مباشرة</b>\n"
        f"❌ <code>/cancel</code> للإلغاء"
    )
    
    await query.edit_message_text(enhanced_prompt, parse_mode=ParseMode.HTML)
    return next_state

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE, service_method_name: str, success_message: str, parse_func) -> int:
    """Generic handler for processing user's text reply and calling a service method."""
    trade_service: TradeService = get_service(context, "trade_service")
    rec_id = context.user_data.get("managed_rec_id")
    original_message_id = context.user_data.get("original_message_id")
    user_id = str(update.effective_user.id)
    
    # إرسال رسالة مؤقتة للمعالجة
    processing_msg = await update.message.reply_text("⚡ جارٍ معالجة الإدخال...")
    
    try:
        parsed_value = parse_func(update.message.text)
        service_method = getattr(trade_service, service_method_name)
        service_method(rec_id, user_id, parsed_value)
        
        # تحديث رسالة المعالجة مع التأكيد
        await processing_msg.edit_text(f"✅ {success_message}")
        
    except Exception as e:
        log.warning(f"Input handling failed for {service_method_name}: {e}")
        await processing_msg.edit_text(f"❌ خطأ في الإدخال أو المعالجة: {e}")
    finally:
        try:
            await update.message.delete()
            # حذف رسالة المعالجة بعد 2 ثانية
            await asyncio.sleep(2)
            await processing_msg.delete()
        except BadRequest:
            pass
        await _update_ui_panel(context, update.message.chat_id, original_message_id, rec_id, user_id)
        
    return MAIN_PANEL

# --- Specific Action Handlers ---

async def set_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles changing the exit strategy."""
    query = update.callback_query
    await query.answer("⏳ جارٍ تغيير الاستراتيجية...", show_alert=True)
    rec_id = context.user_data.get("managed_rec_id")
    user_id = str(query.from_user.id)
    trade_service: TradeService = get_service(context, "trade_service")
    try:
        strategy_value = query.data.split(":")[-1]
        trade_service.update_exit_strategy(rec_id, user_id, ExitStrategy(strategy_value))
        await query.answer("✅ تم تغيير الاستراتيجية بنجاح!", show_alert=True)
    except Exception as e:
        await query.answer(f"❌ فشل: {e}", show_alert=True)
    
    return await navigate_to_menu(update, context, build_exit_strategy_keyboard, STRATEGY_MENU, "📈 **إدارة استراتيجية الخروج**")

async def confirm_market_close_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the final confirmation and execution of a market close."""
    query = update.callback_query
    await query.answer("⏳ جارٍ الإغلاق بسعر السوق...", show_alert=True)
    rec_id = context.user_data.get("managed_rec_id")
    user_id = str(query.from_user.id)
    trade_service: TradeService = get_service(context, "trade_service")
    try:
        trade_service.close_recommendation_at_market_for_user(rec_id, user_id)
        await _update_ui_panel(context, query.message.chat_id, query.message.message_id, rec_id, user_id)
        await query.answer("✅ تم الإغلاق بنجاح!", show_alert=True)
    except Exception as e:
        await query.edit_message_text(f"❌ فشل الإغلاق: {e}", parse_mode=ParseMode.HTML)
    
    context.user_data.clear()
    return ConversationHandler.END

async def received_partial_percent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles receiving the percentage for a partial profit take."""
    try:
        percentage = parse_number(update.message.text)
        if not (0 < percentage <= 100): 
            raise ValueError("النسبة يجب أن تكون بين 1 و 100.")
        context.user_data['partial_profit_percent'] = percentage
        await update.message.reply_text(
            f"✅ النسبة: {percentage}%. الآن، أرسل السعر الذي تم عنده جني الربح.",
            parse_mode=ParseMode.HTML
        )
        return AWAIT_PARTIAL_PRICE_INPUT
    except (ValueError, IndexError) as e:
        await update.message.reply_text(
            f"❌ قيمة غير صالحة: {e}. أرسل رقمًا صحيحًا.",
            parse_mode=ParseMode.HTML
        )
        return AWAIT_PARTIAL_PERCENT_INPUT

async def received_partial_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles receiving the price and executing the partial profit take."""
    rec_id = context.user_data.get("managed_rec_id")
    original_message_id = context.user_data.get("original_message_id")
    user_id = str(update.effective_user.id)
    trade_service: TradeService = get_service(context, "trade_service")
    try:
        price = parse_number(update.message.text)
        percentage = context.user_data['partial_profit_percent']
        trade_service.take_partial_profit_for_user(rec_id, user_id, percentage, price)
        await update.message.reply_text("✅ تم تسجيل جني الربح الجزئي بنجاح.", parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {e}", parse_mode=ParseMode.HTML)
    finally:
        await _update_ui_panel(context, update.message.chat_id, original_message_id, rec_id, user_id)
        context.user_data.pop('partial_profit_percent', None)
    return MAIN_PANEL

# --- Public Card Update (من النسخة 2) ---

async def update_public_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحديث البطاقة العامة مع نظام Rate Limiting"""
    query = update.callback_query
    rec_id = None
    try:
        rec_id = int(query.data.split(":")[-1])
    except (ValueError, IndexError):
        await query.answer("طلب غير صالح.", show_alert=True)
        return
    
    if not rec_id: 
        await query.answer("طلب غير صالح.", show_alert=True)
        return
    
    if _recently_updated(context, query.message.chat_id, query.message.message_id): 
        await query.answer("البيانات محدثة بالفعل.", show_alert=False)
        return
    
    await query.answer("جارٍ تحديث السعر...", show_alert=True)
    trade_service: TradeService = get_service(context, "trade_service")
    price_service: PriceService = get_service(context, "price_service")
    
    try:
        rec = trade_service.get_recommendation(rec_id)  # أو الطريقة المناسبة للحصول على التوصية
        if not rec: 
            await query.answer("التوصية غير موجودة.", show_alert=True)
            return
        if rec.status == RecommendationStatus.CLOSED: 
            await query.answer("هذه الصفقة مغلقة بالفعل.", show_alert=False)
            return
            
        live_price = await price_service.get_cached_price(rec.asset.value, rec.market, force_refresh=True)
        if not live_price: 
            await query.answer("لا يمكن جلب السعر المباشر.", show_alert=True)
            return
        
        # تحديث الواجهة
        setattr(rec, "live_price", live_price)
        new_text = build_trade_card_text(rec)
        
        await query.edit_message_text(
            text=new_text, 
            parse_mode=ParseMode.HTML, 
            disable_web_page_preview=True
        )
    except Exception as e:
        log.error(f"Error updating public card for rec {rec_id}: {e}", exc_info=True)
        await query.answer(f"خطأ في التحديث: {str(e)}", show_alert=True)

# --- Conversation Termination ---

async def conversation_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Called when the conversation times out, cleaning up the state."""
    rec_id = context.user_data.get("managed_rec_id")
    chat_id = update.effective_chat.id if update.effective_chat else (await context.bot.get_chat(context._user_id_and_chat_id[0])).id
    
    timeout_message = (
        f"⏰ <b>انتهت مهلة الجلسة</b>\n\n"
        f"📝 <b>التوصية #{rec_id}</b>\n"
        f"⚠️ انتهت مهلة جلسة التعديل بعد 10 دقائق من عدم النشاط.\n\n"
        f"🔄 <i>يرجى إعادة فتح لوحة التحكم لمواصلة التعديل.</i>"
    )
    
    if rec_id:
        await context.bot.send_message(
            chat_id=chat_id, 
            text=timeout_message,
            parse_mode=ParseMode.HTML
        )
    
    # تنظيف البيانات بعناية
    context.user_data.clear()

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels the current operation and exits the conversation gracefully."""
    await update.message.reply_text("تم إلغاء العملية الحالية.", parse_mode=ParseMode.HTML)
    rec_id = context.user_data.get("managed_rec_id")
    original_message_id = context.user_data.get("original_message_id")
    if rec_id and original_message_id:
        await _update_ui_panel(context, update.message.chat_id, original_message_id, rec_id, str(update.effective_user.id))
    context.user_data.clear()
    return ConversationHandler.END

# --- Handler Registration ---

def register_management_handlers(application: Application):
    """
    Registers the main conversation handler for managing recommendations.
    This centralized handler provides a robust, stateful, and user-friendly way to interact with trades.
    """
    management_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(show_rec_panel_entry, pattern=r"^rec:show_panel:")],
        states={
            MAIN_PANEL: [
                CallbackQueryHandler(back_to_main_panel, pattern=r"^rec:update_private:"),
                CallbackQueryHandler(
                    lambda u, c: navigate_to_menu(
                        u, c, 
                        lambda rec: analyst_edit_menu_keyboard(rec.id), 
                        EDIT_MENU, 
                        "✏️ **قائمة التعديل**"
                    ), 
                    pattern=r"^rec:edit_menu:"
                ),
                CallbackQueryHandler(
                    lambda u, c: navigate_to_menu(
                        u, c, 
                        build_exit_strategy_keyboard, 
                        STRATEGY_MENU, 
                        "📈 **إدارة استراتيجية الخروج**"
                    ), 
                    pattern=r"^rec:strategy_menu:"
                ),
                CallbackQueryHandler(
                    lambda u, c: navigate_to_menu(
                        u, c, 
                        lambda rec: build_close_options_keyboard(rec.id), 
                        CLOSE_MENU, 
                        "❌ **قائمة الإغلاق**"
                    ), 
                    pattern=r"^rec:close_menu:"
                ),
                CallbackQueryHandler(
                    lambda u, c: prompt_for_input(
                        u, c, 
                        AWAIT_PARTIAL_PERCENT_INPUT, 
                        "أرسل **النسبة المئوية** من الصفقة التي تريد إغلاقها (مثال: 50):"
                    ), 
                    pattern=r"^rec:close_partial:"
                ),
            ],
            EDIT_MENU: [
                CallbackQueryHandler(
                    lambda u, c: prompt_for_input(
                        u, c, 
                        AWAIT_SL_INPUT, 
                        "أرسل **وقف الخسارة** الجديد:"
                    ), 
                    pattern=r"^rec:edit_sl:"
                ),
                CallbackQueryHandler(
                    lambda u, c: prompt_for_input(
                        u, c, 
                        AWAIT_TP_INPUT, 
                        "أرسل **الأهداف** الجديدة (مفصولة بمسافات):"
                    ), 
                    pattern=r"^rec:edit_tp:"
                ),
            ],
            STRATEGY_MENU: [
                CallbackQueryHandler(set_strategy, pattern=r"^rec:set_strategy:"),
                CallbackQueryHandler(
                    lambda u, c: prompt_for_input(
                        u, c, 
                        AWAIT_PROFIT_STOP_INPUT, 
                        "أرسل **سعر وقف الربح** الجديد (أو 'remove' للإزالة):"
                    ), 
                    pattern=r"^rec:set_profit_stop:"
                ),
            ],
            CLOSE_MENU: [
                CallbackQueryHandler(
                    lambda u, c: navigate_to_menu(
                        u, c, 
                        lambda rec: InlineKeyboardMarkup([[
                            InlineKeyboardButton("✅ نعم، تأكيد الإغلاق الفوري", callback_data="confirm_now"),
                            InlineKeyboardButton("➡️ تراجع", callback_data="back_to_main")
                        ]]), 
                        CONFIRM_MARKET_CLOSE, 
                        "⚠️ **تأكيد الإغلاق بسعر السوق؟**\nهذا الإجراء نهائي ولا يمكن التراجع عنه."
                    ), 
                    pattern=r"^rec:close_market:"
                ),
                CallbackQueryHandler(
                    lambda u, c: prompt_for_input(
                        u, c, 
                        AWAIT_MANUAL_PRICE_INPUT, 
                        "أرسل **سعر الإغلاق** اليدوي:"
                    ), 
                    pattern=r"^rec:close_manual:"
                ),
            ],
            CONFIRM_MARKET_CLOSE: [
                CallbackQueryHandler(confirm_market_close_action, pattern=r"^confirm_now$"),
                CallbackQueryHandler(back_to_main_panel, pattern=r"^back_to_main$"),
            ],
            AWAIT_SL_INPUT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, 
                    lambda u, c: handle_text_input(
                        u, c, 
                        "update_sl_for_user", 
                        "تم تحديث وقف الخسارة.", 
                        parse_number
                    )
                )
            ],
            AWAIT_TP_INPUT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, 
                    lambda u, c: handle_text_input(
                        u, c, 
                        "update_targets_for_user", 
                        "تم تحديث الأهداف.", 
                        lambda t: parse_targets_list(t.split())
                    )
                )
            ],
            AWAIT_PROFIT_STOP_INPUT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, 
                    lambda u, c: handle_text_input(
                        u, c, 
                        "update_profit_stop_for_user", 
                        "تم تحديث وقف الربح.", 
                        lambda t: None if t.lower() == 'remove' else parse_number(t)
                    )
                )
            ],
            AWAIT_MANUAL_PRICE_INPUT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, 
                    lambda u, c: handle_text_input(
                        u, c, 
                        "close_recommendation_for_user", 
                        "تم تسجيل الإغلاق بنجاح.", 
                        parse_number
                    )
                )
            ],
            AWAIT_PARTIAL_PERCENT_INPUT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, 
                    received_partial_percent
                )
            ],
            AWAIT_PARTIAL_PRICE_INPUT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, 
                    received_partial_price
                )
            ],
        },
        fallbacks=[
            CallbackQueryHandler(back_to_main_panel, pattern=r"^rec:back_to_main:"),
            CommandHandler("cancel", cancel_conversation),
        ],
        conversation_timeout=600,  # 10 minutes
        name="recommendation_management",
        per_user=True, 
        per_chat=True,
    )
    
    application.add_handler(management_conv)
    
    # Standalone handlers (outside the conversation)
    application.add_handler(CallbackQueryHandler(update_public_card, pattern=r"^rec:update_public:"))
    
    # Setup periodic cleanup
    if application.job_queue:
        application.job_queue.run_repeating(
            callback=lambda context: input_state_manager.cleanup_expired_sessions(),
            interval=60,  # كل دقيقة
            first=60
        )

# --- END OF FINAL, COMPLETE, AND PRODUCTION-READY FILE ---