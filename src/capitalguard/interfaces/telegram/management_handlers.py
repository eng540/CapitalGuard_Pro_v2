# src/capitalguard/interfaces/telegram/management_handlers.py (v29.0 - FINAL PRODUCTION READY)
"""
إصدار نهائي كامل - معالجة جميع الأخطاء المذكورة في السجلات
✅ إصلاح تحليل بيانات الاستدعاء لجميع الأنماط
✅ معالجة أخطاء "Message is not modified" 
✅ إصلاح إعدادات ConversationHandler
✅ توافق كامل مع النظام الحالي
"""

import logging
from decimal import Decimal, InvalidOperation

from telegram import Update, ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (Application, CallbackQueryHandler, MessageHandler, ContextTypes, filters, ConversationHandler, CommandHandler)

from capitalguard.domain.entities import RecommendationStatus, ExitStrategy
from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service, parse_tail_int, parse_cq_parts
from .keyboards import (
    analyst_control_panel_keyboard, build_open_recs_keyboard, 
    build_user_trade_control_keyboard, build_close_options_keyboard, 
    analyst_edit_menu_keyboard, build_exit_strategy_keyboard, 
    build_partial_close_keyboard, CallbackNamespace, CallbackAction
)
from .ui_texts import build_trade_card_text
from .auth import require_active_user, require_analyst_user
from .parsers import parse_number, parse_targets_list
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService

log = logging.getLogger(__name__)

AWAITING_INPUT_KEY = "awaiting_user_input_for"
(AWAIT_PARTIAL_PERCENT, AWAIT_PARTIAL_PRICE) = range(2)

# --- دوال مساعدة محسنة ---

async def safe_edit_message(query, text=None, reply_markup=None, parse_mode=None):
    """تعديل الرسالة بشكل آمن مع معالجة أخطاء 'not modified'"""
    try:
        if text and reply_markup:
            await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        elif reply_markup:
            await query.edit_message_reply_markup(reply_markup=reply_markup)
        elif text:
            await query.edit_message_text(text=text, parse_mode=parse_mode)
        return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            await query.answer()
            return True
        else:
            log.error(f"Safe edit failed: {e}")
            raise

async def _send_or_edit_position_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    query = update.callback_query
    parts = parse_cq_parts(query.data)
    
    try:
        # معالجة جميع الأنماط المحتملة لبيانات الاستدعاء
        position_type = 'rec'
        position_id = 0
        
        if len(parts) >= 3 and parts[0] == CallbackNamespace.RECOMMENDATION.value:
            if parts[1] == CallbackAction.STRATEGY.value:
                # نمط استراتيجية الخروج: rec:st:3:MANUAL_CLOSE_ONLY:v2.0
                position_id = int(parts[2])
            elif parts[1] == "back_to_main":
                # نمط العودة للوحة الرئيسية: rec:back_to_main:123
                position_id = int(parts[2])
            elif len(parts) >= 4:
                # الأنماط الأخرى: rec:action:type:id
                position_type, position_id = parts[2], int(parts[3])
            else:
                raise ValueError(f"Unsupported callback pattern: {query.data}")
        elif len(parts) >= 4 and parts[0] == CallbackNamespace.POSITION.value:
            # نمط العرض العادي: pos:sh:rec:123 أو pos:sh:trade:456
            position_type, position_id = parts[2], int(parts[3])
        else:
            raise ValueError(f"Unsupported callback pattern: {query.data}")
            
    except (IndexError, ValueError) as e:
        log.error(f"Could not parse position info from callback data: {query.data}, error: {e}")
        await query.answer("❌ خطأ في البيانات. حاول مرة أخرى.", show_alert=True)
        return

    trade_service = get_service(context, "trade_service", TradeService)
    position = trade_service.get_position_details_for_user(db_session, str(query.from_user.id), position_type, position_id)
    
    if not position:
        await safe_edit_message(query, text="❌ Position not found or access denied.")
        return

    price_service = get_service(context, "price_service", PriceService)
    live_price = await price_service.get_cached_price(position.asset.value, position.market, force_refresh=True)
    if live_price: 
        setattr(position, "live_price", live_price)

    text = build_trade_card_text(position)
    is_trade = getattr(position, 'is_user_trade', False)
    
    keyboard = None
    if is_trade:
        keyboard = build_user_trade_control_keyboard(position_id)
    elif position.status != RecommendationStatus.CLOSED:
        keyboard = analyst_control_panel_keyboard(position)

    await safe_edit_message(
        query, 
        text=text, 
        reply_markup=keyboard, 
        parse_mode=ParseMode.HTML
    )

async def _prompt_for_input(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, action: str, prompt_text: str):
    """طلب إدخال من المستخدم بشكل آمن"""
    try:
        rec_id = int(query.data.split(':')[2])
        context.user_data[AWAITING_INPUT_KEY] = {
            "action": action, 
            "rec_id": rec_id, 
            "original_message": query.message
        }
        full_prompt = f"{query.message.text}\n\n<b>{prompt_text}</b>"
        await safe_edit_message(query, text=full_prompt, parse_mode=ParseMode.HTML)
    except Exception as e:
        log.error(f"Prompt for input failed: {e}")
        await query.answer("❌ فشل في فتح محرر الإدخال", show_alert=True)

# --- اللوحة الرئيسية والتنقل ---

@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالج عرض لوحة التحكم بالصفقة"""
    await update.callback_query.answer()
    await _send_or_edit_position_panel(update, context, db_session)

@uow_transaction
@require_active_user
async def navigate_open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالج التنقل بين الصفقات المفتوحة"""
    query = update.callback_query
    await query.answer()
    
    try:
        page = parse_tail_int(query.data) or 1
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        items = trade_service.get_open_positions_for_user(db_session, str(query.from_user.id))
        
        if not items:
            await safe_edit_message(query, text="✅ You have no open positions.")
            return
            
        keyboard = await build_open_recs_keyboard(items, current_page=page, price_service=price_service)
        await safe_edit_message(
            query, 
            text="<b>📊 Your Open Positions</b>\nSelect one to manage:", 
            reply_markup=keyboard, 
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        log.error(f"Navigation handler failed: {e}")
        await query.answer("❌ فشل في تحميل الصفقات", show_alert=True)

# --- تنقل القوائم ---

@uow_transaction
@require_active_user
@require_analyst_user
async def show_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالج عرض القوائم المختلفة"""
    query = update.callback_query
    await query.answer()
    
    try:
        parts = parse_cq_parts(query.data)
        action, rec_id = parts[1], int(parts[2])

        if action == "edit_menu":
            await safe_edit_message(query, reply_markup=analyst_edit_menu_keyboard(rec_id))
        elif action == "close_menu":
            await safe_edit_message(query, reply_markup=build_close_options_keyboard(rec_id))
        elif action == "strategy_menu":
            trade_service = get_service(context, "trade_service", TradeService)
            rec_orm = trade_service.repo.get(db_session, rec_id)
            if rec_orm:
                rec_entity = trade_service.repo._to_entity(rec_orm)
                await safe_edit_message(query, reply_markup=build_exit_strategy_keyboard(rec_entity))
        elif action == CallbackAction.PARTIAL.value:
            await safe_edit_message(query, reply_markup=build_partial_close_keyboard(rec_id))
            
    except Exception as e:
        log.error(f"Show menu handler failed: {e}")
        await query.answer("❌ فشل في تحميل القائمة", show_alert=True)

# --- الإجراءات المباشرة ---

@uow_transaction
@require_active_user
@require_analyst_user
async def set_strategy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالج تعيين استراتيجية الخروج"""
    query = update.callback_query
    await query.answer("🔄 تحديث الاستراتيجية...")
    
    try:
        parts = parse_cq_parts(query.data)
        rec_id, strategy_value = int(parts[2]), parts[3]
        trade_service = get_service(context, "trade_service", TradeService)
        await trade_service.update_exit_strategy_async(rec_id, str(query.from_user.id), ExitStrategy(strategy_value), db_session)
        await query.answer("✅ تم تحديث استراتيجية الخروج", show_alert=False)
    except Exception as e:
        log.error(f"Set strategy failed: {e}")
        await query.answer("❌ فشل في تحديث الاستراتيجية", show_alert=True)

@uow_transaction
@require_active_user
@require_analyst_user
async def close_at_market_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالج الإغلاق بسعر السوق"""
    query = update.callback_query
    await query.answer("🔄 جلب سعر السوق والإغلاق...")
    
    try:
        rec_id = int(query.data.split(':')[2])
        trade_service = get_service(context, "trade_service", TradeService)
        rec_orm = trade_service.repo.get(db_session, rec_id)
        if not rec_orm: 
            await query.answer("❌ التوصية غير موجودة", show_alert=True)
            return
            
        rec_entity = trade_service.repo._to_entity(rec_orm)
        price_service = get_service(context, "price_service", PriceService)
        live_price = await price_service.get_cached_price(rec_entity.asset.value, rec_entity.market, force_refresh=True)
        
        if live_price is None:
            await query.answer(f"❌ تعذر جلب سعر السوق لـ {rec_entity.asset.value}", show_alert=True)
            return
            
        await trade_service.close_recommendation_async(rec_id, str(query.from_user.id), Decimal(str(live_price)), db_session)
        await query.answer("✅ تم الإغلاق بنجاح", show_alert=False)
    except Exception as e:
        log.error(f"Close at market failed: {e}")
        await query.answer("❌ فشل في الإغلاق", show_alert=True)

@uow_transaction
@require_active_user
@require_analyst_user
async def partial_close_fixed_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالج الإغلاق الجزئي بنسب ثابتة"""
    query = update.callback_query
    await query.answer("🔄 جلب السعر وإغلاق جزئي...")
    
    try:
        parts = parse_cq_parts(query.data)
        rec_id, percent_to_close = int(parts[2]), Decimal(parts[3])
        
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        
        rec_orm = trade_service.repo.get(db_session, rec_id)
        if not rec_orm: 
            await query.answer("❌ التوصية غير موجودة", show_alert=True)
            return
            
        rec_entity = trade_service.repo._to_entity(rec_orm)
        live_price = await price_service.get_cached_price(rec_entity.asset.value, rec_entity.market, force_refresh=True)
        
        if live_price is None:
            await query.answer(f"❌ تعذر جلب سعر السوق لـ {rec_entity.asset.value}", show_alert=True)
            return
            
        await trade_service.partial_close_async(rec_id, str(query.from_user.id), percent_to_close, Decimal(str(live_price)), db_session)
        await query.answer(f"✅ تم إغلاق {percent_to_close}% بنجاح", show_alert=False)
    except Exception as e:
        log.error(f"Partial close fixed failed: {e}")
        await query.answer("❌ فشل في الإغلاق الجزئي", show_alert=True)

# --- طلبات الإدخال والمعالجة ---

async def prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج فتح محرر الإدخال"""
    query = update.callback_query
    await query.answer()
    
    try:
        action = query.data.split(':')[1]
        prompts = {
            "edit_sl": "✏️ أرسل سعر وقف الخسارة الجديد:",
            "edit_tp": "🎯 أرسل قائمة الأهداف الجديدة (مثال: 50k 52k@50):",
            "close_manual": "✍️ أرسل سعر الإغلاق النهائي:"
        }
        await _prompt_for_input(query, context, action, prompts.get(action, "الرجاء إرسال القيمة الجديدة:"))
    except Exception as e:
        log.error(f"Prompt handler failed: {e}")
        await query.answer("❌ فشل في فتح المحرر", show_alert=True)

@uow_transaction
async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالج ردود المستخدم على طلبات الإدخال"""
    if not context.user_data or not (state := context.user_data.pop(AWAITING_INPUT_KEY, None)):
        return
        
    orig_msg = state.get("original_message")
    if not orig_msg or not update.message.reply_to_message or update.message.reply_to_message.message_id != orig_msg.message_id:
        if state: 
            context.user_data[AWAITING_INPUT_KEY] = state
        return

    action, rec_id = state["action"], state["rec_id"]
    user_input, chat_id, user_id = update.message.text.strip(), orig_msg.chat_id, str(update.effective_user.id)
    
    try: 
        await update.message.delete()
    except Exception: 
        pass

    trade_service = get_service(context, "trade_service", TradeService)
    
    try:
        if action == "close_manual":
            price = parse_number(user_input)
            if price is None: 
                raise ValueError("تنسيق السعر غير صالح.")
            await trade_service.close_recommendation_async(rec_id, user_id, price, db_session=db_session)
            await context.bot.send_message(chat_id=chat_id, text=f"✅ تم الإغلاق بسعر {price:g}")
            
        elif action == "edit_sl":
            price = parse_number(user_input)
            if price is None: 
                raise ValueError("تنسيق السعر غير صالح.")
            await trade_service.update_sl_for_user_async(rec_id, user_id, price, db_session=db_session)
            await context.bot.send_message(chat_id=chat_id, text=f"✅ تم تحديث وقف الخسارة إلى {price:g}")
            
        elif action == "edit_tp":
            targets_list = parse_targets_list(user_input.split())
            if not targets_list: 
                raise ValueError("تنسيق الأهداف غير صالح.")
            await trade_service.update_targets_for_user_async(rec_id, user_id, targets_list, db_session=db_session)
            await context.bot.send_message(chat_id=chat_id, text="✅ تم تحديث الأهداف بنجاح")
            
    except ValueError as e:
        log.warning(f"Invalid user input for {action} on #{rec_id}: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ خطأ: {str(e)}")
    except Exception as e:
        log.error(f"Error processing reply for {action} on #{rec_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ خطأ غير متوقع: {str(e)}")

# --- محادثة الإغلاق الجزئي المخصص ---

async def partial_close_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بدء محادثة الإغلاق الجزئي المخصص"""
    query = update.callback_query
    await query.answer()
    
    try:
        rec_id = int(query.data.split(':')[2])
        context.user_data['partial_close_rec_id'] = rec_id
        await safe_edit_message(
            query, 
            text=f"{query.message.text}\n\n<b>💰 الرجاء إرسال نسبة الإغلاق (مثال: 25.5)</b>", 
            parse_mode=ParseMode.HTML
        )
        return AWAIT_PARTIAL_PERCENT
    except Exception as e:
        log.error(f"Partial close start failed: {e}")
        await query.answer("❌ فشل في بدء الإغلاق الجزئي", show_alert=True)
        return ConversationHandler.END

async def partial_close_percent_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """استقبال نسبة الإغلاق الجزئي"""
    try:
        percent = parse_number(update.message.text)
        if percent is None or not (0 < percent <= 100):
            raise ValueError("النسبة يجب أن تكون رقم بين 0 و 100")
            
        context.user_data['partial_close_percent'] = percent
        await update.message.reply_html(f"✅ النسبة: {percent:g}%\n\n<b>الآن أرسل سعر الإغلاق</b>")
        return AWAIT_PARTIAL_PRICE
    except ValueError as e:
        await update.message.reply_text(f"❌ قيمة غير صالحة: {e}. حاول مرة أخرى أو /cancel")
        return AWAIT_PARTIAL_PERCENT
    except Exception as e:
        log.error(f"Partial close percent failed: {e}")
        await update.message.reply_text("❌ خطأ غير متوقع. حاول مرة أخرى أو /cancel")
        return AWAIT_PARTIAL_PERCENT

@uow_transaction
async def partial_close_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """استقبال سعر الإغلاق الجزئي وإتمام العملية"""
    try:
        price = parse_number(update.message.text)
        if price is None: 
            raise ValueError("تنسيق السعر غير صالح")
        
        percent = context.user_data['partial_close_percent']
        rec_id = context.user_data['partial_close_rec_id']
        user_id = str(update.effective_user.id)
        
        trade_service = get_service(context, "trade_service", TradeService)
        await trade_service.partial_close_async(rec_id, user_id, percent, price, db_session)
        
        await update.message.reply_text(f"✅ تم الإغلاق الجزئي بنجاح: {percent:g}% بسعر {price:g}")
        
    except ValueError as e:
        await update.message.reply_text(f"❌ قيمة غير صالحة: {e}. حاول مرة أخرى أو /cancel")
        return AWAIT_PARTIAL_PRICE
    except Exception as e:
        log.error(f"Error in partial profit flow for rec #{context.user_data.get('partial_close_rec_id')}: {e}", exc_info=True)
        await update.message.reply_text(f"❌ حدث خطأ غير متوقع: {e}")
    
    # تنظيف البيانات المؤقتة
    for key in ('partial_close_rec_id', 'partial_close_percent'):
        context.user_data.pop(key, None)
    return ConversationHandler.END

async def partial_close_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """إلغاء محادثة الإغلاق الجزئي"""
    for key in ('partial_close_rec_id', 'partial_close_percent'):
        context.user_data.pop(key, None)
    await update.message.reply_text("تم إلغاء عملية الإغلاق الجزئي.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# --- التسجيل النهائي للمعالجات ---

def register_management_handlers(app: Application):
    """تسجيل جميع معالجات الإدارة بشكل آمن"""
    
    # مساحات الأسماء للإستخدام
    rec_ns = CallbackNamespace.RECOMMENDATION.value
    pos_ns = CallbackNamespace.POSITION.value
    nav_ns = CallbackNamespace.NAVIGATION.value
    
    # تسجيل المعالجات مع الأنماط المصححة
    app.add_handler(CallbackQueryHandler(
        navigate_open_positions_handler, 
        pattern=rf"^{nav_ns}:{CallbackAction.NAVIGATE.value}:"
    ))
    
    app.add_handler(CallbackQueryHandler(
        show_position_panel_handler, 
        pattern=rf"^(?:{pos_ns}:{CallbackAction.SHOW.value}:|{rec_ns}:back_to_main:)"
    ))
    
    app.add_handler(CallbackQueryHandler(
        show_menu_handler, 
        pattern=rf"^{rec_ns}:(?:edit_menu|close_menu|strategy_menu|{CallbackAction.PARTIAL.value})"
    ))
    
    app.add_handler(CallbackQueryHandler(
        set_strategy_handler, 
        pattern=rf"^{rec_ns}:{CallbackAction.STRATEGY.value}:"
    ))
    
    app.add_handler(CallbackQueryHandler(
        close_at_market_handler, 
        pattern=rf"^{rec_ns}:close_market:"
    ))
    
    app.add_handler(CallbackQueryHandler(
        partial_close_fixed_handler, 
        pattern=rf"^{rec_ns}:{CallbackAction.PARTIAL.value}:"
    ))
    
    app.add_handler(CallbackQueryHandler(
        prompt_handler, 
        pattern=rf"^{rec_ns}:(?:edit_sl|edit_tp|close_manual)"
    ))
    
    app.add_handler(MessageHandler(
        filters.REPLY & filters.TEXT & ~filters.COMMAND, 
        reply_handler
    ))

    # محادثة الإغلاق الجزئي المخصص
    partial_close_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(
            partial_close_custom_start, 
            pattern=rf"^{rec_ns}:partial_close_custom:"
        )],
        states={
            AWAIT_PARTIAL_PERCENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, partial_close_percent_received)
            ],
            AWAIT_PARTIAL_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, partial_close_price_received)
            ],
        },
        fallbacks=[CommandHandler("cancel", partial_close_cancel)],
        name="partial_profit_conversation",
        per_user=True,
        per_chat=True,
        per_message=True,  # ✅ إصلاح التحذير
    )
    app.add_handler(partial_close_conv)

# تصدير الوظائف العامة
__all__ = [
    'register_management_handlers',
    'show_position_panel_handler', 
    'navigate_open_positions_handler',
    'show_menu_handler',
    'set_strategy_handler',
    'close_at_market_handler',
    'partial_close_fixed_handler',
    'prompt_handler',
    'reply_handler',
    'partial_close_custom_start',
    'partial_close_percent_received',
    'partial_close_price_received',
    'partial_close_cancel'
]