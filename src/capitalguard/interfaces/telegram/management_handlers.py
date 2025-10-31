# capitalguard/interfaces/telegram/management_handlers.py
# CapitalGuard Telegram Management Handlers - النسخة الكاملة الكاملة

"""
Handles all post-creation management of recommendations AND UserTrades.
✅ النسخة الكاملة مع جميع الدوال المفقودة
"""

import logging
import time
from decimal import Decimal
from typing import Optional, Any
from telegram import (
    Update, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, Bot
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, MessageHandler, CommandHandler,
    ContextTypes, ConversationHandler, filters
)

from capitalguard.infrastructure.db.uow import uow_transaction
from capitalguard.interfaces.telegram.helpers import get_service
from capitalguard.interfaces.telegram.keyboards import (
    analyst_control_panel_keyboard, build_open_recs_keyboard,
    build_user_trade_control_keyboard, build_close_options_keyboard,
    build_trade_data_edit_keyboard, build_exit_management_keyboard,
    build_partial_close_keyboard, CallbackAction, CallbackNamespace,
    build_confirmation_keyboard, CallbackBuilder, ButtonTexts
)
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text
from capitalguard.interfaces.telegram.auth import require_active_user, require_analyst_user
from capitalguard.interfaces.telegram.parsers import parse_number, parse_targets_list, parse_trailing_distance
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.domain.entities import RecommendationStatus
from capitalguard.infrastructure.db.models import UserType as UserTypeEntity

# ---------------- Constants ----------------
AWAITING_INPUT_KEY = "awaiting_management_input"
PENDING_CHANGE_KEY = "pending_management_change"
LAST_ACTIVITY_KEY = "last_activity_management"
MANAGEMENT_TIMEOUT = 1800

(AWAIT_PARTIAL_PERCENT, AWAIT_PARTIAL_PRICE) = range(2)
(AWAIT_USER_TRADE_CLOSE_PRICE,) = range(AWAIT_PARTIAL_PRICE + 1, AWAIT_PARTIAL_PRICE + 2)

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors")

# ---------------- Helper Functions ----------------
def clean_management_state(context: ContextTypes.DEFAULT_TYPE):
    """تنظيف حالة الإدارة"""
    for key in [
        AWAITING_INPUT_KEY, PENDING_CHANGE_KEY, LAST_ACTIVITY_KEY,
        'partial_close_rec_id', 'partial_close_percent',
        'user_trade_close_id', 'user_trade_close_msg_id', 'user_trade_close_chat_id'
    ]:
        context.user_data.pop(key, None)

async def safe_edit_message(bot: Bot, chat_id: int, message_id: int, text: str = None, reply_markup=None, parse_mode: str = ParseMode.HTML) -> bool:
    """تعديل الرسالة بشكل آمن"""
    try:
        if text:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text, 
                reply_markup=reply_markup, parse_mode=parse_mode, 
                disable_web_page_preview=True
            )
        elif reply_markup:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
        return True
    except BadRequest as e:
        if "message is not modified" not in str(e).lower():
            loge.warning(f"Handled BadRequest editing msg {chat_id}:{message_id}: {e}")
        return False
    except TelegramError as e:
        loge.error(f"TelegramError editing msg {chat_id}:{message_id}: {e}")
        return False
    except Exception as e:
        loge.exception(f"Unexpected error editing msg {chat_id}:{message_id}: {e}")
        return False

# ---------------- Core Management Handlers ----------------
@require_active_user
async def management_entry_point_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نقطة الدخول لإدارة المحفظة"""
    try:
        user_id = update.effective_user.id
        
        # تنظيف الحالة السابقة
        clean_management_state(context)
        
        # تحديث النشاط
        context.user_data[LAST_ACTIVITY_KEY] = time.time()
        
        # الحصول على الخدمات
        trade_service = get_service(TradeService, context)
        price_service = get_service(PriceService, context)
        
        # الحصول على التوصيات المفتوحة
        with uow_transaction() as uow:
            open_recs = trade_service.get_open_recommendations(uow)
            
            if not open_recs:
                await update.message.reply_text(
                    "📭 لا توجد توصيات مفتوحة حالياً.\n\n"
                    "استخدم /newrec لإنشاء توصية جديدة.",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
        
        # بناء لوحة التحكم
        keyboard = build_open_recs_keyboard(open_recs)
        
        await update.message.reply_text(
            "📊 **لوحة إدارة التوصيات المفتوحة**\n\n"
            "اختر التوصية التي تريد إدارتها:",
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        loge.exception("Error in management entry point")
        await update.message.reply_text("❌ حدث خطأ في تحميل لوحة الإدارة.")

@require_active_user
async def navigate_open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التنقل بين الصفحات في التوصيات المفتوحة"""
    query = update.callback_query
    await query.answer()
    
    try:
        # تحديث النشاط
        context.user_data[LAST_ACTIVITY_KEY] = time.time()
        
        # استخراج بيانات الصفحة
        data = query.data.split(':')
        page = int(data[2]) if len(data) > 2 else 0
        
        # الحصول على التوصيات المفتوحة
        trade_service = get_service(TradeService, context)
        
        with uow_transaction() as uow:
            open_recs = trade_service.get_open_recommendations(uow)
            
            if not open_recs:
                await query.edit_message_text("❌ لا توجد توصيات مفتوحة.")
                return
            
            # بناء لوحة التوصيات مع الصفحة المطلوبة
            keyboard = build_open_recs_keyboard(open_recs, page)
            await query.edit_message_text(
                "📊 **لوحة إدارة التوصيات المفتوحة**\n\n"
                "اختر التوصية التي تريد إدارتها:",
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN
            )
            
    except Exception as e:
        loge.exception("Error in navigation handler")
        await query.edit_message_text("❌ حدث خطأ في التنقل.")

@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض لوحة التحكم في التوصية"""
    query = update.callback_query
    await query.answer()
    
    try:
        # تحديث النشاط
        context.user_data[LAST_ACTIVITY_KEY] = time.time()
        
        # استخراج معرف التوصية
        data = query.data.split(':')
        rec_id = int(data[2])
        
        # الحصول على بيانات التوصية
        trade_service = get_service(TradeService, context)
        price_service = get_service(PriceService, context)
        
        with uow_transaction() as uow:
            recommendation = trade_service.get_recommendation_by_id(uow, rec_id)
            if not recommendation:
                await query.edit_message_text("❌ التوصية غير موجودة.")
                return
            
            # بناء نص التوصية ولوحة التحكم
            card_text = build_trade_card_text(recommendation, price_service)
            keyboard = build_user_trade_control_keyboard(recommendation)
            
            await query.edit_message_text(
                card_text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            
    except Exception as e:
        loge.exception("Error showing position panel")
        await query.edit_message_text("❌ حدث خطأ في تحميل لوحة التحكم.")

@require_active_user
async def show_submenu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض القوائم الفرعية"""
    query = update.callback_query
    await query.answer()
    
    try:
        context.user_data[LAST_ACTIVITY_KEY] = time.time()
        
        data = query.data.split(':')
        namespace = data[0]
        action = data[1]
        rec_id = int(data[2])
        
        trade_service = get_service(TradeService, context)
        
        with uow_transaction() as uow:
            recommendation = trade_service.get_recommendation_by_id(uow, rec_id)
            if not recommendation:
                await query.edit_message_text("❌ التوصية غير موجودة.")
                return
            
            if namespace == CallbackNamespace.RECOMMENDATION.value:
                if action == CallbackAction.EDIT.value:
                    # عرض خيارات التعديل
                    keyboard = build_trade_data_edit_keyboard(recommendation)
                    await query.edit_message_reply_markup(reply_markup=keyboard)
                    
                elif action == CallbackAction.CLOSE.value:
                    # عرض خيارات الإغلاق
                    keyboard = build_close_options_keyboard(recommendation)
                    await query.edit_message_reply_markup(reply_markup=keyboard)
                    
            elif namespace == CallbackNamespace.EXIT_STRATEGY.value:
                if action == CallbackAction.EDIT.value:
                    # عرض خيارات تعديل استراتيجية الخروج
                    keyboard = build_exit_management_keyboard(recommendation)
                    await query.edit_message_reply_markup(reply_markup=keyboard)
                    
    except Exception as e:
        loge.exception("Error in submenu handler")
        await query.edit_message_text("❌ حدث خطأ في تحميل القائمة.")

# ---------------- Input Handlers ----------------
@require_active_user
async def prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة طلبات الإدخال"""
    query = update.callback_query
    await query.answer()
    
    try:
        context.user_data[LAST_ACTIVITY_KEY] = time.time()
        
        data = query.data.split(':')
        namespace = data[0]
        action = data[1]
        rec_id = int(data[2])
        field = data[3] if len(data) > 3 else None
        
        # حفظ حالة الانتظار
        context.user_data[AWAITING_INPUT_KEY] = {
            'namespace': namespace,
            'action': action,
            'rec_id': rec_id,
            'field': field,
            'message_id': query.message.message_id,
            'chat_id': query.message.chat_id
        }
        
        # بناء رسالة الطلب بناءً على الحقل
        prompt_messages = {
            'stop_loss': "🛑 أرسل سعر Stop Loss الجديد:",
            'take_profit': "🎯 أرسل أسعار Take Profit الجديدة (مفصولة بفاصلة):",
            'trailing_stop': "📏 أرسل مسافة Trailing Stop الجديدة:",
            'entry_price': "💰 أرسل سعر الدخول الجديد:"
        }
        
        prompt_text = prompt_messages.get(field, "📝 أرسل القيمة الجديدة:")
        
        # إرسال رسالة الطلب
        await query.message.reply_text(
            f"{prompt_text}\n\n"
            "أو استخدم /cancel للإلغاء.",
            reply_markup=ReplyKeyboardRemove()
        )
        
    except Exception as e:
        loge.exception("Error in prompt handler")
        await query.edit_message_text("❌ حدث خطأ في طلب الإدخال.")

@require_active_user
async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الردود على طلبات الإدخال"""
    try:
        user_id = update.effective_user.id
        user_input = update.message.text.strip()
        
        # التحقق من وجود حالة انتظار
        if AWAITING_INPUT_KEY not in context.user_data:
            await update.message.reply_text("❌ لا توجد عملية انتظار نشطة.")
            return
        
        await update.message.delete()
        
        waiting_data = context.user_data[AWAITING_INPUT_KEY]
        rec_id = waiting_data['rec_id']
        field = waiting_data['field']
        message_id = waiting_data['message_id']
        chat_id = waiting_data['chat_id']
        
        # تحديث النشاط
        context.user_data[LAST_ACTIVITY_KEY] = time.time()
        
        trade_service = get_service(TradeService, context)
        
        with uow_transaction() as uow:
            recommendation = trade_service.get_recommendation_by_id(uow, rec_id)
            if not recommendation:
                await context.bot.send_message(chat_id, "❌ التوصية غير موجودة.")
                clean_management_state(context)
                return
            
            # معالجة الإدخال بناءً على الحقل
            try:
                if field == 'stop_loss':
                    new_value = parse_number(user_input)
                    if not new_value or new_value <= 0:
                        raise ValueError("سعر Stop Loss غير صالح")
                    
                elif field == 'take_profit':
                    new_value = parse_targets_list(user_input)
                    if not new_value:
                        raise ValueError("أسعار Take Profit غير صالحة")
                    
                elif field == 'trailing_stop':
                    new_value = parse_trailing_distance(user_input)
                    if not new_value or new_value <= 0:
                        raise ValueError("مسافة Trailing Stop غير صالحة")
                    
                elif field == 'entry_price':
                    new_value = parse_number(user_input)
                    if not new_value or new_value <= 0:
                        raise ValueError("سعر الدخول غير صالح")
                    
                else:
                    new_value = user_input
                
                # حفظ التغيير المعلق
                context.user_data[PENDING_CHANGE_KEY] = {
                    'rec_id': rec_id,
                    'field': field,
                    'new_value': new_value,
                    'message_id': message_id,
                    'chat_id': chat_id
                }
                
                # بناء لوحة التأكيد
                confirmation_text = f"⚠️ **تأكيد التغيير**\n\n"
                confirmation_text += f"**الحقل:** {field}\n"
                confirmation_text += f"**القيمة الجديدة:** {new_value}\n\n"
                confirmation_text += "هل تريد تأكيد هذا التغيير؟"
                
                keyboard = build_confirmation_keyboard("mgmt:confirm_change", "mgmt:cancel_input")
                
                await context.bot.send_message(
                    chat_id,
                    confirmation_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN
                )
                
            except ValueError as e:
                await context.bot.send_message(
                    chat_id,
                    f"❌ {str(e)}\n\nيرجى إعادة المحاولة:",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
        
    except Exception as e:
        loge.exception("Error in reply handler")
        await update.message.reply_text("❌ حدث خطأ في معالجة الإدخال.")

@require_active_user
async def confirm_change_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تأكيد التغييرات"""
    query = update.callback_query
    await query.answer()
    
    try:
        context.user_data[LAST_ACTIVITY_KEY] = time.time()
        
        if PENDING_CHANGE_KEY not in context.user_data:
            await query.edit_message_text("❌ لا يوجد تغيير معلق.")
            return
        
        change_data = context.user_data[PENDING_CHANGE_KEY]
        rec_id = change_data['rec_id']
        field = change_data['field']
        new_value = change_data['new_value']
        
        trade_service = get_service(TradeService, context)
        price_service = get_service(PriceService, context)
        
        with uow_transaction() as uow:
            recommendation = trade_service.get_recommendation_by_id(uow, rec_id)
            if not recommendation:
                await query.edit_message_text("❌ التوصية غير موجودة.")
                clean_management_state(context)
                return
            
            # تطبيق التغيير
            update_data = {field: new_value}
            success = trade_service.update_recommendation(uow, rec_id, update_data)
            
            if success:
                # تحديث التوصية
                recommendation = trade_service.get_recommendation_by_id(uow, rec_id)
                card_text = build_trade_card_text(recommendation, price_service)
                keyboard = build_user_trade_control_keyboard(recommendation)
                
                await query.edit_message_text(
                    "✅ تم التحديث بنجاح!",
                    reply_markup=None
                )
                
                # إرسال الرسالة المحدثة
                await context.bot.send_message(
                    change_data['chat_id'],
                    card_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )
            else:
                await query.edit_message_text("❌ فشل في تحديث التوصية.")
        
        # تنظيف الحالة
        clean_management_state(context)
        
    except Exception as e:
        loge.exception("Error in confirm change handler")
        await query.edit_message_text("❌ حدث خطأ في تأكيد التغيير.")

@require_active_user
async def cancel_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء عملية الإدخال"""
    query = update.callback_query
    await query.answer()
    
    try:
        context.user_data[LAST_ACTIVITY_KEY] = time.time()
        
        # تنظيف الحالة
        clean_management_state(context)
        
        await query.edit_message_text("❌ تم الإلغاء.")
        
    except Exception as e:
        loge.exception("Error in cancel input handler")
        await query.edit_message_text("❌ حدث خطأ في الإلغاء.")

@require_active_user
async def cancel_all_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء جميع العمليات"""
    query = update.callback_query
    await query.answer()
    
    try:
        context.user_data[LAST_ACTIVITY_KEY] = time.time()
        
        # تنظيف الحالة بالكامل
        clean_management_state(context)
        
        await query.edit_message_text(
            "🗑️ تم إلغاء جميع العمليات.\n\n"
            "استخدم /open للعودة إلى لوحة الإدارة.",
            reply_markup=ReplyKeyboardRemove()
        )
        
    except Exception as e:
        loge.exception("Error in cancel all handler")
        await query.edit_message_text("❌ حدث خطأ في الإلغاء.")

# ---------------- Immediate Actions ----------------
@require_active_user
async def immediate_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الإجراءات الفورية"""
    query = update.callback_query
    await query.answer()
    
    try:
        context.user_data[LAST_ACTIVITY_KEY] = time.time()
        
        data = query.data.split(':')
        namespace = data[0]
        action = data[1]
        rec_id = int(data[2])
        
        trade_service = get_service(TradeService, context)
        price_service = get_service(PriceService, context)
        
        with uow_transaction() as uow:
            recommendation = trade_service.get_recommendation_by_id(uow, rec_id)
            if not recommendation:
                await query.edit_message_text("❌ التوصية غير موجودة.")
                return
            
            if namespace == CallbackNamespace.RECOMMENDATION.value:
                if action == CallbackAction.CLOSE_NOW.value:
                    # إغلاق التوصية فوراً
                    success = trade_service.close_recommendation(uow, rec_id)
                    
                    if success:
                        await query.edit_message_text(
                            "✅ تم إغلاق التوصية بنجاح!",
                            reply_markup=ReplyKeyboardRemove()
                        )
                    else:
                        await query.edit_message_text("❌ فشل في إغلاق التوصية.")
                        
            elif namespace == CallbackNamespace.EXIT_STRATEGY.value:
                if action == CallbackAction.ACTIVATE.value:
                    # تفعيل استراتيجية الخروج
                    success = trade_service.activate_exit_strategy(uow, rec_id)
                    
                    if success:
                        await query.edit_message_text(
                            "✅ تم تفعيل استراتيجية الخروج!",
                            reply_markup=ReplyKeyboardRemove()
                        )
                    else:
                        await query.edit_message_text("❌ فشل في تفعيل استراتيجية الخروج.")
        
    except Exception as e:
        loge.exception("Error in immediate action handler")
        await query.edit_message_text("❌ حدث خطأ في تنفيذ الإجراء.")

# ---------------- Partial Close Handlers ----------------
@require_active_user
async def partial_close_fixed_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الإغلاق الجزئي بنسبة ثابتة"""
    query = update.callback_query
    await query.answer()
    
    try:
        context.user_data[LAST_ACTIVITY_KEY] = time.time()
        
        data = query.data.split(':')
        rec_id = int(data[2])
        percent = Decimal(data[3])
        
        trade_service = get_service(TradeService, context)
        
        with uow_transaction() as uow:
            success = trade_service.partial_close_recommendation(uow, rec_id, percent)
            
            if success:
                await query.edit_message_text(
                    f"✅ تم الإغلاق الجزئي بنسبة {percent}% بنجاح!",
                    reply_markup=ReplyKeyboardRemove()
                )
            else:
                await query.edit_message_text("❌ فشل في الإغلاق الجزئي.")
                
    except Exception as e:
        loge.exception("Error in partial close fixed handler")
        await query.edit_message_text("❌ حدث خطأ في الإغلاق الجزئي.")

@require_active_user
async def partial_close_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء محادثة الإغلاق الجزئي المخصص"""
    query = update.callback_query
    await query.answer()
    
    try:
        context.user_data[LAST_ACTIVITY_KEY] = time.time()
        
        data = query.data.split(':')
        rec_id = int(data[2])
        
        # حفظ حالة الإغلاق الجزئي
        context.user_data['partial_close_rec_id'] = rec_id
        
        await query.message.reply_text(
            "📊 **الإغلاق الجزئي المخصص**\n\n"
            "أدخل النسبة المئوية للإغلاق (مثال: 25):\n\n"
            "استخدم /cancel للإلغاء.",
            reply_markup=ReplyKeyboardRemove()
        )
        
        return AWAIT_PARTIAL_PERCENT
        
    except Exception as e:
        loge.exception("Error starting partial close conversation")
        await query.edit_message_text("❌ حدث خطأ في بدء الإغلاق الجزئي.")
        return ConversationHandler.END

@require_active_user
async def partial_close_percent_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام نسبة الإغلاق الجزئي"""
    try:
        user_input = update.message.text.strip()
        
        # محاولة تحويل الإدخال إلى رقم
        try:
            percent = Decimal(user_input)
            if percent <= 0 or percent > 100:
                raise ValueError("النسبة يجب أن تكون بين 0 و 100")
                
        except (ValueError, ArithmeticError):
            await update.message.reply_text(
                "❌ نسبة غير صالحة. يرجى إدخال رقم بين 0 و 100:\n\n"
                "استخدم /cancel للإلغاء."
            )
            return AWAIT_PARTIAL_PERCENT
        
        # حفظ النسبة
        context.user_data['partial_close_percent'] = percent
        
        await update.message.reply_text(
            f"📈 النسبة: {percent}%\n\n"
            "أدخل سعر الإغلاق:\n\n"
            "استخدم /cancel للإلغاء.",
            reply_markup=ReplyKeyboardRemove()
        )
        
        return AWAIT_PARTIAL_PRICE
        
    except Exception as e:
        loge.exception("Error receiving partial close percent")
        await update.message.reply_text("❌ حدث خطأ في معالجة النسبة.")
        return ConversationHandler.END

@require_active_user
async def partial_close_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام سعر الإغلاق الجزئي"""
    try:
        user_input = update.message.text.strip()
        
        # محاولة تحويل الإدخال إلى رقم
        try:
            price = Decimal(user_input)
            if price <= 0:
                raise ValueError("السعر يجب أن يكون أكبر من الصفر")
                
        except (ValueError, ArithmeticError):
            await update.message.reply_text(
                "❌ سعر غير صالح. يرجى إدخال رقم صحيح:\n\n"
                "استخدم /cancel للإلغاء."
            )
            return AWAIT_PARTIAL_PRICE
        
        # الحصول على البيانات المحفوظة
        rec_id = context.user_data.get('partial_close_rec_id')
        percent = context.user_data.get('partial_close_percent')
        
        if not rec_id or not percent:
            await update.message.reply_text("❌ بيانات غير مكتملة. يرجى البدء من جديد.")
            clean_management_state(context)
            return ConversationHandler.END
        
        trade_service = get_service(TradeService, context)
        
        with uow_transaction() as uow:
            success = trade_service.partial_close_recommendation(uow, rec_id, percent, price)
            
            if success:
                await update.message.reply_text(
                    f"✅ تم الإغلاق الجزئي بنسبة {percent}% بسعر {price} بنجاح!",
                    reply_markup=ReplyKeyboardRemove()
                )
            else:
                await update.message.reply_text("❌ فشل في الإغلاق الجزئي.")
        
        # تنظيف الحالة
        clean_management_state(context)
        return ConversationHandler.END
        
    except Exception as e:
        loge.exception("Error receiving partial close price")
        await update.message.reply_text("❌ حدث خطأ في معالجة السعر.")
        clean_management_state(context)
        return ConversationHandler.END

@require_active_user
async def partial_close_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء محادثة الإغلاق الجزئي"""
    try:
        clean_management_state(context)
        
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text("❌ تم إلغاء الإغلاق الجزئي.")
        else:
            await update.message.reply_text("❌ تم إلغاء الإغلاق الجزئي.")
            
        return ConversationHandler.END
        
    except Exception as e:
        loge.exception("Error cancelling partial close")
        return ConversationHandler.END

# ---------------- User Trade Close Handlers ----------------
@require_active_user
async def user_trade_close_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء محادثة إغلاق صفقة المستخدم"""
    query = update.callback_query
    await query.answer()
    
    try:
        context.user_data[LAST_ACTIVITY_KEY] = time.time()
        
        data = query.data.split(':')
        trade_id = int(data[3])
        
        # حفظ بيانات الصفقة
        context.user_data['user_trade_close_id'] = trade_id
        context.user_data['user_trade_close_msg_id'] = query.message.message_id
        context.user_data['user_trade_close_chat_id'] = query.message.chat_id
        
        await query.message.reply_text(
            "💼 **إغلاق صفقة المستخدم**\n\n"
            "أدخل سعر الإغلاق:\n\n"
            "استخدم /cancel للإلغاء.",
            reply_markup=ReplyKeyboardRemove()
        )
        
        return AWAIT_USER_TRADE_CLOSE_PRICE
        
    except Exception as e:
        loge.exception("Error starting user trade close")
        await query.edit_message_text("❌ حدث خطأ في بدء إغلاق الصفقة.")
        return ConversationHandler.END

@require_active_user
async def user_trade_close_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام سعر إغلاق صفقة المستخدم"""
    try:
        user_input = update.message.text.strip()
        
        # محاولة تحويل الإدخال إلى رقم
        try:
            close_price = Decimal(user_input)
            if close_price <= 0:
                raise ValueError("السعر يجب أن يكون أكبر من الصفر")
                
        except (ValueError, ArithmeticError):
            await update.message.reply_text(
                "❌ سعر غير صالح. يرجى إدخال رقم صحيح:\n\n"
                "استخدم /cancel للإلغاء."
            )
            return AWAIT_USER_TRADE_CLOSE_PRICE
        
        # الحصول على بيانات الصفقة
        trade_id = context.user_data.get('user_trade_close_id')
        
        if not trade_id:
            await update.message.reply_text("❌ بيانات الصفقة غير موجودة. يرجى البدء من جديد.")
            clean_management_state(context)
            return ConversationHandler.END
        
        trade_service = get_service(TradeService, context)
        
        with uow_transaction() as uow:
            success = trade_service.close_user_trade(uow, trade_id, close_price)
            
            if success:
                await update.message.reply_text(
                    f"✅ تم إغلاق الصفقة بسعر {close_price} بنجاح!",
                    reply_markup=ReplyKeyboardRemove()
                )
            else:
                await update.message.reply_text("❌ فشل في إغلاق الصفقة.")
        
        # تنظيف الحالة
        clean_management_state(context)
        return ConversationHandler.END
        
    except Exception as e:
        loge.exception("Error receiving user trade close price")
        await update.message.reply_text("❌ حدث خطأ في معالجة السعر.")
        clean_management_state(context)
        return ConversationHandler.END

@require_active_user
async def cancel_user_trade_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء محادثة إغلاق صفقة المستخدم"""
    try:
        clean_management_state(context)
        
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text("❌ تم إلغاء إغلاق الصفقة.")
        else:
            await update.message.reply_text("❌ تم إلغاء إغلاق الصفقة.")
            
        return ConversationHandler.END
        
    except Exception as e:
        loge.exception("Error cancelling user trade close")
        return ConversationHandler.END

# ---------------- Management Handlers Core Module ----------------
# هذا القسم يحاكي الملف management_handlers_core.py المذكور في الاستيراد

# جميع الدوال المطلوبة موجودة أعلاه، لكننا نعيد تعريفها هنا للتأكد من التوافق
management_handlers_core = {
    'management_entry_point_handler': management_entry_point_handler,
    'navigate_open_positions_handler': navigate_open_positions_handler,
    'show_position_panel_handler': show_position_panel_handler,
    'show_submenu_handler': show_submenu_handler,
    'prompt_handler': prompt_handler,
    'reply_handler': reply_handler,
    'confirm_change_handler': confirm_change_handler,
    'cancel_input_handler': cancel_input_handler,
    'cancel_all_handler': cancel_all_handler,
    'immediate_action_handler': immediate_action_handler,
    'partial_close_fixed_handler': partial_close_fixed_handler,
    'partial_close_custom_start': partial_close_custom_start,
    'partial_close_percent_received': partial_close_percent_received,
    'partial_close_price_received': partial_close_price_received,
    'partial_close_cancel': partial_close_cancel,
    'user_trade_close_start': user_trade_close_start,
    'user_trade_close_price_received': user_trade_close_price_received,
    'cancel_user_trade_close': cancel_user_trade_close
}

# ---------------- Register Handlers ----------------
def register_management_handlers(app: Application):
    """تسجيل جميع معالجات الإدارة"""
    
    # التسجيل الأساسي
    app.add_handler(CommandHandler(["myportfolio", "open"], management_entry_point_handler))
    app.add_handler(CallbackQueryHandler(navigate_open_positions_handler, pattern=rf"^{CallbackNamespace.NAVIGATION.value}:{CallbackAction.NAVIGATE.value}:"))
    app.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=rf"^{CallbackNamespace.POSITION.value}:{CallbackAction.SHOW.value}:"))
    app.add_handler(CallbackQueryHandler(show_submenu_handler, pattern=rf"^(?:{CallbackNamespace.RECOMMENDATION.value}|{CallbackNamespace.EXIT_STRATEGY.value}):"))
    app.add_handler(CallbackQueryHandler(prompt_handler, pattern=rf"^(?:{CallbackNamespace.RECOMMENDATION.value}|{CallbackNamespace.EXIT_STRATEGY.value}):"))
    app.add_handler(CallbackQueryHandler(confirm_change_handler, pattern=rf"^mgmt:confirm_change:"))
    app.add_handler(CallbackQueryHandler(cancel_input_handler, pattern=rf"^mgmt:cancel_input:"))
    app.add_handler(CallbackQueryHandler(cancel_all_handler, pattern=rf"^mgmt:cancel_all:"))
    app.add_handler(CallbackQueryHandler(immediate_action_handler, pattern=rf"^(?:{CallbackNamespace.EXIT_STRATEGY.value}|{CallbackNamespace.RECOMMENDATION.value}):"))
    app.add_handler(CallbackQueryHandler(partial_close_fixed_handler, pattern=rf"^{CallbackNamespace.RECOMMENDATION.value}:{CallbackAction.PARTIAL.value}:"))

    # معالج الردود
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, reply_handler))

    # محادثة الإغلاق الجزئي
    partial_close_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(partial_close_custom_start, pattern=rf"^{CallbackNamespace.RECOMMENDATION.value}:partial_close_custom:")],
        states={
            AWAIT_PARTIAL_PERCENT: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, partial_close_percent_received)],
            AWAIT_PARTIAL_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, partial_close_price_received)],
        },
        fallbacks=[
            CommandHandler("cancel", partial_close_cancel),
            CallbackQueryHandler(partial_close_cancel, pattern=rf"^mgmt:cancel_input:")
        ],
        name="partial_close_conversation",
        per_user=True, per_chat=True, per_message=False
    )
    app.add_handler(partial_close_conv)

    # محادثة إغلاق صفقة المستخدم
    user_trade_close_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(user_trade_close_start, pattern=rf"^{CallbackNamespace.POSITION.value}:{CallbackAction.CLOSE.value}:trade:")],
        states={
            AWAIT_USER_TRADE_CLOSE_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, user_trade_close_price_received)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_user_trade_close),
            CallbackQueryHandler(cancel_user_trade_close, pattern=rf"^mgmt:cancel_input:")
        ],
        name="user_trade_close_conversation",
        per_user=True, per_chat=True, per_message=False
    )
    app.add_handler(user_trade_close_conv)

    log.info("✅ تم تحميل معالجات الإدارة بنجاح")

# ---------------- Timeout Cleanup ----------------
async def cleanup_management_timeouts(app: Application):
    """تنظيف الجلسات المنتهية"""
    try:
        for user_id, user_data in app.user_data.items():
            last_activity = user_data.get(LAST_ACTIVITY_KEY)
            if last_activity and time.time() - last_activity > MANAGEMENT_TIMEOUT:
                clean_management_state(user_data)
                log.info(f"🧹 تم تنظيف جلسة المستخدم {user_id} (انتهت المهلة)")
    except Exception as e:
        loge.exception("Error in management timeout cleanup")

# تصدير جميع الدوال
__all__ = [
    'management_entry_point_handler',
    'navigate_open_positions_handler', 
    'show_position_panel_handler',
    'show_submenu_handler',
    'prompt_handler',
    'reply_handler',
    'confirm_change_handler',
    'cancel_input_handler',
    'cancel_all_handler',
    'immediate_action_handler',
    'partial_close_fixed_handler',
    'partial_close_custom_start',
    'partial_close_percent_received',
    'partial_close_price_received',
    'partial_close_cancel',
    'user_trade_close_start',
    'user_trade_close_price_received',
    'cancel_user_trade_close',
    'register_management_handlers',
    'cleanup_management_timeouts'
]