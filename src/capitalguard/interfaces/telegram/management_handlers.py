# src/capitalguard/interfaces/telegram/management_handlers.py (v30.0 - FINAL COMPLETE RELEASE)
"""
الإصدار النهائي الكامل الشامل - نظام إدارة التوصيات المتكامل
✅ معالجة كاملة لجميع أنماط بيانات الاستدعاء v1.0 و v2.0
✅ نظام أمان متكامل للتحقق من الملكية
✅ معالجة شاملة لأخطاء Telegram API
✅ تكامل تام مع لوحات المفاتيح المركزية
✅ محادثة الإغلاق الجزئي المكتملة
"""

import logging
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Optional

from telegram import Update, ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application, CallbackQueryHandler, MessageHandler, 
    ContextTypes, filters, ConversationHandler, CommandHandler
)

from capitalguard.domain.entities import RecommendationStatus, ExitStrategy
from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service
from .keyboards import (
    analyst_control_panel_keyboard, 
    build_open_recs_keyboard, 
    build_user_trade_control_keyboard, 
    build_close_options_keyboard, 
    analyst_edit_menu_keyboard, 
    build_exit_strategy_keyboard, 
    build_partial_close_keyboard,
    build_confirmation_keyboard,
    CallbackNamespace, 
    CallbackAction
)
from .ui_texts import build_trade_card_text
from .auth import require_active_user, require_analyst_user
from .parsers import parse_number, parse_targets_list
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors")

# حالات محادثة الإغلاق الجزئي
(AWAIT_PARTIAL_PERCENT, AWAIT_PARTIAL_PRICE) = range(2)

# مفتاح تخزين حالة انتظار الإدخال
AWAITING_INPUT_KEY = "awaiting_user_input_for"

class ManagementSafetyManager:
    """مدير أمان متكامل لإدارة التوصيات"""
    
    @staticmethod
    def parse_callback_data_advanced(callback_data: str) -> Dict[str, Any]:
        """تحليل متقدم لبيانات الاستدعاء مع دعم جميع الأنماط"""
        try:
            if not callback_data or callback_data == "noop":
                return {"raw": callback_data, "is_noop": True}
                
            parts = callback_data.split(':')
            result = {
                'raw': callback_data,
                'namespace': parts[0] if len(parts) > 0 else None,
                'action': parts[1] if len(parts) > 1 else None,
                'params': [],
                'version': '1.0',
                'is_valid': False
            }
            
            # معالجة الإصدار v2.0
            if parts and parts[-1].startswith('v'):
                result['version'] = parts[-1][1:]
                result['params'] = parts[2:-1] if len(parts) > 3 else []
            else:
                result['params'] = parts[2:] if len(parts) > 2 else []
            
            # التحقق من الصحة الأساسية
            if result['namespace'] and result['action']:
                result['is_valid'] = True
                
            return result
            
        except Exception as e:
            log.error(f"Advanced callback parsing failed: {callback_data}, error: {e}")
            return {'raw': callback_data, 'error': str(e), 'is_valid': False}
    
    @staticmethod
    async def safe_edit_message(query, text: str = None, reply_markup=None, parse_mode: str = None) -> bool:
        """تعديل آمن للرسالة"""
        try:
            if text and reply_markup:
                await query.edit_message_text(
                    text=text, 
                    reply_markup=reply_markup, 
                    parse_mode=parse_mode
                )
            elif reply_markup:
                await query.edit_message_reply_markup(reply_markup=reply_markup)
            elif text:
                await query.edit_message_text(text=text, parse_mode=parse_mode)
            return True
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                await query.answer()
                return True
            log.warning(f"Safe edit failed: {e}")
            return False
        except TelegramError as e:
            log.error(f"Telegram error in safe edit: {e}")
            return False
    
    @staticmethod
    def extract_position_info(callback_data: str) -> Dict[str, Any]:
        """استخراج معلومات الموضع من بيانات الاستدعاء"""
        parsed = ManagementSafetyManager.parse_callback_data_advanced(callback_data)
        
        if not parsed['is_valid']:
            return {'error': 'Invalid callback data', 'raw': callback_data}
        
        namespace = parsed['namespace']
        action = parsed['action']
        params = parsed['params']
        
        result = {
            'namespace': namespace,
            'action': action,
            'position_type': 'rec',
            'position_id': 0,
            'is_valid': False
        }
        
        try:
            if namespace == CallbackNamespace.RECOMMENDATION.value:
                if action in [CallbackAction.STRATEGY.value, "back_to_main"]:
                    # rec:st:3:MANUAL_CLOSE_ONLY أو rec:back_to_main:3
                    result['position_id'] = int(params[0]) if params else 0
                    result['is_valid'] = result['position_id'] > 0
                    
                elif action in ["edit_menu", "close_menu", "strategy_menu", CallbackAction.PARTIAL.value]:
                    # rec:edit_menu:3 أو rec:pt:3
                    result['position_id'] = int(params[0]) if params else 0
                    result['is_valid'] = result['position_id'] > 0
                    
                elif len(params) >= 2:
                    # rec:action:rec:3 أو rec:action:trade:5
                    result['position_type'] = params[0]
                    result['position_id'] = int(params[1])
                    result['is_valid'] = result['position_id'] > 0
                    
            elif namespace == CallbackNamespace.POSITION.value and action == CallbackAction.SHOW.value:
                # pos:sh:rec:3 أو pos:sh:trade:5
                if len(params) >= 2:
                    result['position_type'] = params[0]
                    result['position_id'] = int(params[1])
                    result['is_valid'] = result['position_id'] > 0
            
            return result
            
        except (ValueError, IndexError) as e:
            log.error(f"Position info extraction failed: {callback_data}, error: {e}")
            return {'error': f'Extraction failed: {e}', 'raw': callback_data}

async def _send_or_edit_position_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session):
    """إرسال أو تعديل لوحة التحكم بالموقع - الإصدار النهائي"""
    query = update.callback_query
    user_id = query.from_user.id
    
    try:
        # استخراج معلومات الموضع
        position_info = ManagementSafetyManager.extract_position_info(query.data)
        
        if not position_info.get('is_valid'):
            log.error(f"Invalid position info from user {user_id}: {query.data}")
            await query.answer("❌ بيانات غير صالحة", show_alert=True)
            return
        
        position_type = position_info['position_type']
        position_id = position_info['position_id']
        
        log.info(f"📊 User {user_id} accessing {position_type} #{position_id}")
        
        # جلب بيانات الموضع
        trade_service = get_service(context, "trade_service", TradeService)
        position = trade_service.get_position_details_for_user(
            db_session, str(user_id), position_type, position_id
        )
        
        if not position:
            log.warning(f"Position not found: {position_type} #{position_id} for user {user_id}")
            await ManagementSafetyManager.safe_edit_message(
                query, 
                text="❌ <b>لم يتم العثور على الموضع</b>\n\nقد يكون الموضع مغلقاً أو ليس لديك صلاحية الوصول.",
                parse_mode=ParseMode.HTML
            )
            return
        
        # جلب السعر الحي
        price_service = get_service(context, "price_service", PriceService)
        live_price = await price_service.get_cached_price(
            position.asset.value, position.market, force_refresh=True
        )
        if live_price:
            setattr(position, "live_price", live_price)
        
        # بناء نص البطاقة
        card_text = build_trade_card_text(position)
        is_trade = getattr(position, 'is_user_trade', False)
        
        # بناء لوحة المفاتيح المناسبة
        keyboard = None
        if is_trade:
            keyboard = build_user_trade_control_keyboard(position_id)
        elif position.status != RecommendationStatus.CLOSED:
            keyboard = analyst_control_panel_keyboard(position)
        else:
            # عرض بطاقة بدون أزرار تحكم للمواقع المغلقة
            card_text += "\n\n🔒 <b>هذا الموضع مغلق</b>"
        
        # إرسال/تعديل الرسالة
        success = await ManagementSafetyManager.safe_edit_message(
            query,
            text=card_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        
        if not success:
            await query.answer("⚠️ تعذر تحديث العرض", show_alert=True)
            
    except Exception as e:
        loge.exception(f"❌ Error in _send_or_edit_position_panel for user {user_id}: {e}")
        await query.answer("❌ حدث خطأ في تحميل الموضع", show_alert=True)

@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالج عرض لوحة التحكم - الإصدار النهائي"""
    try:
        query = update.callback_query
        await query.answer()
        await _send_or_edit_position_panel(update, context, db_session)
    except Exception as e:
        loge.exception(f"❌ Error in show_position_panel_handler: {e}")
        await update.callback_query.answer("❌ حدث خطأ في تحميل اللوحة", show_alert=True)

@uow_transaction
@require_active_user
async def navigate_open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالج التنقل بين المواضع المفتوحة - الإصدار النهائي"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        
        # استخراج رقم الصفحة
        callback_data = ManagementSafetyManager.parse_callback_data_advanced(query.data)
        params = callback_data.get('params', [])
        page = int(params[0]) if params else 1
        
        log.info(f"📄 User {user_id} navigating to page {page}")
        
        # جلب المواضع المفتوحة
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        
        items = trade_service.get_open_positions_for_user(db_session, str(user_id))
        
        if not items:
            await ManagementSafetyManager.safe_edit_message(
                query,
                text="✅ <b>لا توجد مواضع مفتوحة</b>\n\nليس لديك أي توصيات أو صفقات مفتوحة حالياً.",
                parse_mode=ParseMode.HTML
            )
            return
        
        # بناء لوحة المفاتيح
        keyboard = await build_open_recs_keyboard(items, current_page=page, price_service=price_service)
        
        await ManagementSafetyManager.safe_edit_message(
            query,
            text=f"<b>📊 المواضع المفتوحة</b>\n\nاختر موضعاً للإدارة:",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        loge.exception(f"❌ Error in navigate_open_positions_handler: {e}")
        await update.callback_query.answer("❌ حدث خطأ في تحميل القائمة", show_alert=True)

@uow_transaction
@require_active_user
@require_analyst_user
async def show_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالج عرض القوائم - الإصدار النهائي"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        callback_data = ManagementSafetyManager.parse_callback_data_advanced(query.data)
        
        if not callback_data['is_valid'] or not callback_data['params']:
            await query.answer("❌ بيانات غير صالحة", show_alert=True)
            return
        
        action = callback_data['action']
        rec_id = int(callback_data['params'][0])
        
        log.info(f"📝 User {user_id} opening {action} menu for rec #{rec_id}")
        
        # التحقق من ملكية التوصية
        trade_service = get_service(context, "trade_service", TradeService)
        rec = trade_service.repo.get(db_session, rec_id)
        
        if not rec or rec.analyst.telegram_user_id != user_id:
            await query.answer("❌ ليس لديك صلاحية لهذا الإجراء", show_alert=True)
            return
        
        # معالجة أنواع القوائم المختلفة
        if action == "edit_menu":
            keyboard = analyst_edit_menu_keyboard(rec_id)
            await ManagementSafetyManager.safe_edit_message(query, reply_markup=keyboard)
            
        elif action == "close_menu":
            keyboard = build_close_options_keyboard(rec_id)
            await ManagementSafetyManager.safe_edit_message(query, reply_markup=keyboard)
            
        elif action == "strategy_menu":
            rec_entity = trade_service.repo._to_entity(rec)
            keyboard = build_exit_strategy_keyboard(rec_entity)
            await ManagementSafetyManager.safe_edit_message(query, reply_markup=keyboard)
            
        elif action == CallbackAction.PARTIAL.value:
            keyboard = build_partial_close_keyboard(rec_id)
            await ManagementSafetyManager.safe_edit_message(query, reply_markup=keyboard)
        
    except Exception as e:
        loge.exception(f"❌ Error in show_menu_handler: {e}")
        await update.callback_query.answer("❌ حدث خطأ في فتح القائمة", show_alert=True)

@uow_transaction
@require_active_user
@require_analyst_user
async def set_strategy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالج تعيين استراتيجية الخروج - الإصدار النهائي"""
    try:
        query = update.callback_query
        await query.answer("🔄 تحديث الاستراتيجية...")
        
        user_id = query.from_user.id
        callback_data = ManagementSafetyManager.parse_callback_data_advanced(query.data)
        
        if not callback_data['is_valid'] or len(callback_data['params']) < 2:
            await query.answer("❌ بيانات غير صالحة", show_alert=True)
            return
        
        rec_id = int(callback_data['params'][0])
        strategy_value = callback_data['params'][1]
        
        log.info(f"🎯 User {user_id} setting strategy {strategy_value} for rec #{rec_id}")
        
        # تطبيق استراتيجية الخروج
        trade_service = get_service(context, "trade_service", TradeService)
        await trade_service.update_exit_strategy_async(
            rec_id, str(user_id), ExitStrategy(strategy_value), db_session
        )
        
        await query.answer("✅ تم تحديث استراتيجية الخروج")
        
        # تحديث العرض
        await _send_or_edit_position_panel(update, context, db_session)
        
    except Exception as e:
        loge.exception(f"❌ Error in set_strategy_handler: {e}")
        await update.callback_query.answer("❌ فشل في تحديث الاستراتيجية", show_alert=True)

@uow_transaction
@require_active_user
@require_analyst_user
async def close_at_market_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالج الإغلاق بسعر السوق - الإصدار النهائي"""
    try:
        query = update.callback_query
        await query.answer("🔄 جلب سعر السوق والإغلاق...")
        
        user_id = query.from_user.id
        callback_data = ManagementSafetyManager.parse_callback_data_advanced(query.data)
        
        if not callback_data['is_valid'] or not callback_data['params']:
            await query.answer("❌ بيانات غير صالحة", show_alert=True)
            return
        
        rec_id = int(callback_data['params'][0])
        
        log.info(f"📉 User {user_id} closing rec #{rec_id} at market price")
        
        # جلب التوصية والسعر الحالي
        trade_service = get_service(context, "trade_service", TradeService)
        rec_orm = trade_service.repo.get(db_session, rec_id)
        
        if not rec_orm:
            await query.answer("❌ التوصية غير موجودة", show_alert=True)
            return
        
        rec_entity = trade_service.repo._to_entity(rec_orm)
        price_service = get_service(context, "price_service", PriceService)
        
        live_price = await price_service.get_cached_price(
            rec_entity.asset.value, rec_entity.market, force_refresh=True
        )
        
        if live_price is None:
            await query.answer(f"❌ تعذر جلب سعر السوق لـ {rec_entity.asset.value}", show_alert=True)
            return
        
        # تنفيذ الإغلاق
        await trade_service.close_recommendation_async(
            rec_id, str(user_id), Decimal(str(live_price)), db_session
        )
        
        await query.answer("✅ تم الإغلاق بنجاح")
        
        # تحديث العرض
        await _send_or_edit_position_panel(update, context, db_session)
        
    except Exception as e:
        loge.exception(f"❌ Error in close_at_market_handler: {e}")
        await update.callback_query.answer("❌ فشل في الإغلاق", show_alert=True)

@uow_transaction
@require_active_user
@require_analyst_user
async def partial_close_fixed_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالج الإغلاق الجزئي بنسب ثابتة - الإصدار النهائي"""
    try:
        query = update.callback_query
        await query.answer("🔄 جلب السعر وإغلاق جزئي...")
        
        user_id = query.from_user.id
        callback_data = ManagementSafetyManager.parse_callback_data_advanced(query.data)
        
        if not callback_data['is_valid'] or len(callback_data['params']) < 2:
            await query.answer("❌ بيانات غير صالحة", show_alert=True)
            return
        
        rec_id = int(callback_data['params'][0])
        percent_to_close = Decimal(callback_data['params'][1])
        
        log.info(f"💰 User {user_id} partial closing {percent_to_close}% of rec #{rec_id}")
        
        # جلب التوصية والسعر الحالي
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        
        rec_orm = trade_service.repo.get(db_session, rec_id)
        if not rec_orm:
            await query.answer("❌ التوصية غير موجودة", show_alert=True)
            return
        
        rec_entity = trade_service.repo._to_entity(rec_orm)
        live_price = await price_service.get_cached_price(
            rec_entity.asset.value, rec_entity.market, force_refresh=True
        )
        
        if live_price is None:
            await query.answer(f"❌ تعذر جلب سعر السوق لـ {rec_entity.asset.value}", show_alert=True)
            return
        
        # تنفيذ الإغلاق الجزئي
        await trade_service.partial_close_async(
            rec_id, str(user_id), percent_to_close, Decimal(str(live_price)), db_session
        )
        
        await query.answer(f"✅ تم إغلاق {percent_to_close}% بنجاح")
        
        # تحديث العرض
        await _send_or_edit_position_panel(update, context, db_session)
        
    except Exception as e:
        loge.exception(f"❌ Error in partial_close_fixed_handler: {e}")
        await update.callback_query.answer("❌ فشل في الإغلاق الجزئي", show_alert=True)

async def prompt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج فتح محرر الإدخال - الإصدار النهائي"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        callback_data = ManagementSafetyManager.parse_callback_data_advanced(query.data)
        
        if not callback_data['is_valid'] or not callback_data['params']:
            await query.answer("❌ بيانات غير صالحة", show_alert=True)
            return
        
        action = callback_data['action']
        rec_id = int(callback_data['params'][0])
        
        log.info(f"⌨️ User {user_id} opening input prompt for {action} on rec #{rec_id}")
        
        # تحديد رسالة الطلب المناسبة
        prompts = {
            "edit_sl": "✏️ <b>تعديل وقف الخسارة</b>\n\nأرسل سعر وقف الخسارة الجديد:",
            "edit_tp": "🎯 <b>تعديل الأهداف</b>\n\nأرسل قائمة الأهداف الجديدة (مثال: <code>50000 52000@50 55000@30</code>):",
            "close_manual": "✍️ <b>الإغلاق اليدوي</b>\n\nأرسل سعر الإغلاق النهائي:"
        }
        
        prompt_text = prompts.get(action, "الرجاء إرسال القيمة الجديدة:")
        
        # حفظ حالة انتظار الإدخال
        context.user_data[AWAITING_INPUT_KEY] = {
            "action": action,
            "rec_id": rec_id,
            "original_message": query.message
        }
        
        # عرض محرر الإدخال
        full_prompt = f"{query.message.text}\n\n{prompt_text}"
        await ManagementSafetyManager.safe_edit_message(
            query,
            text=full_prompt,
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        loge.exception(f"❌ Error in prompt_handler: {e}")
        await update.callback_query.answer("❌ فشل في فتح المحرر", show_alert=True)

@uow_transaction
async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالج ردود المستخدم - الإصدار النهائي"""
    try:
        # التحقق من وجود حالة انتظار
        if not context.user_data or not (state := context.user_data.pop(AWAITING_INPUT_KEY, None)):
            return
        
        # التحقق من أن الرد على الرسالة الصحيحة
        orig_msg = state.get("original_message")
        if not orig_msg or not update.message.reply_to_message:
            return
            
        if update.message.reply_to_message.message_id != orig_msg.message_id:
            # ليس رداً على الرسالة الصحيحة، إعادة الحالة
            context.user_data[AWAITING_INPUT_KEY] = state
            return

        # استخراج البيانات
        action = state["action"]
        rec_id = state["rec_id"]
        user_input = update.message.text.strip()
        chat_id = orig_msg.chat_id
        user_id = str(update.effective_user.id)
        
        log.info(f"📨 User {user_id} replied for {action} on rec #{rec_id}: {user_input}")
        
        # حذف رسالة المستخدم
        try:
            await update.message.delete()
        except (BadRequest, TelegramError):
            pass
        
        # معالجة الإدخال حسب نوع الإجراء
        trade_service = get_service(context, "trade_service", TradeService)
        
        try:
            if action == "close_manual":
                price = parse_number(user_input)
                if price is None:
                    raise ValueError("❌ تنسيق السعر غير صالح. يرجى إدخال رقم صحيح (مثال: 50000)")
                
                await trade_service.close_recommendation_async(rec_id, user_id, price, db_session=db_session)
                await context.bot.send_message(chat_id=chat_id, text=f"✅ تم الإغلاق بسعر {price:g}")
                
            elif action == "edit_sl":
                price = parse_number(user_input)
                if price is None:
                    raise ValueError("❌ تنسيق السعر غير صالح. يرجى إدخال رقم صحيح (مثال: 48000)")
                
                await trade_service.update_sl_for_user_async(rec_id, user_id, price, db_session=db_session)
                await context.bot.send_message(chat_id=chat_id, text=f"✅ تم تحديث وقف الخسارة إلى {price:g}")
                
            elif action == "edit_tp":
                targets_list = parse_targets_list(user_input.split())
                if not targets_list:
                    raise ValueError("❌ تنسيق الأهداف غير صالح. يرجى استخدام التنسيق: <code>50000 52000@50 55000@30</code>")
                
                await trade_service.update_targets_for_user_async(rec_id, user_id, targets_list, db_session=db_session)
                await context.bot.send_message(chat_id=chat_id, text="✅ تم تحديث الأهداف بنجاح")
            
            log.info(f"✅ User {user_id} successfully processed {action} for rec #{rec_id}")
            
        except ValueError as e:
            error_msg = str(e)
            await context.bot.send_message(chat_id=chat_id, text=error_msg)
            # إعادة حالة الانتظار للسماح بالمحاولة مرة أخرى
            context.user_data[AWAITING_INPUT_KEY] = state
            
        except Exception as e:
            loge.exception(f"❌ Error processing reply for {action} on rec #{rec_id}: {e}")
            await context.bot.send_message(chat_id=chat_id, text="❌ حدث خطأ غير متوقع في المعالجة")
        
    except Exception as e:
        loge.exception(f"❌ Unexpected error in reply_handler: {e}")

# محادثة الإغلاق الجزئي المخصص
async def partial_close_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بدء محادثة الإغلاق الجزئي المخصص - الإصدار النهائي"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        callback_data = ManagementSafetyManager.parse_callback_data_advanced(query.data)
        
        if not callback_data['is_valid'] or not callback_data['params']:
            await query.answer("❌ بيانات غير صالحة", show_alert=True)
            return ConversationHandler.END
        
        rec_id = int(callback_data['params'][0])
        context.user_data['partial_close_rec_id'] = rec_id
        
        log.info(f"🔢 User {user_id} starting custom partial close for rec #{rec_id}")
        
        await ManagementSafetyManager.safe_edit_message(
            query,
            text=f"{query.message.text}\n\n"
                 f"💰 <b>الإغلاق الجزئي المخصص</b>\n\n"
                 f"أرسل نسبة الإغلاق (مثال: <code>25.5</code>):",
            parse_mode=ParseMode.HTML
        )
        return AWAIT_PARTIAL_PERCENT
        
    except Exception as e:
        loge.exception(f"❌ Error in partial_close_custom_start: {e}")
        await update.callback_query.answer("❌ فشل في بدء الإغلاق الجزئي", show_alert=True)
        return ConversationHandler.END

async def partial_close_percent_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """استقبال نسبة الإغلاق الجزئي - الإصدار النهائي"""
    try:
        user_id = update.effective_user.id
        percent_text = update.message.text.strip()
        
        # تحليل النسبة
        percent = parse_number(percent_text)
        if percent is None or not (0 < percent <= 100):
            await update.message.reply_text(
                "❌ <b>نسبة غير صالحة</b>\n\n"
                "يجب أن تكون النسبة رقم بين 0 و 100.\n"
                "مثال: <code>25.5</code>\n\n"
                "يرجى المحاولة مرة أخرى:",
                parse_mode=ParseMode.HTML
            )
            return AWAIT_PARTIAL_PERCENT
        
        context.user_data['partial_close_percent'] = percent
        
        log.info(f"📊 User {user_id} set partial close percent: {percent}%")
        
        await update.message.reply_html(
            f"✅ <b>تم تحديد النسبة:</b> {percent:g}%\n\n"
            f"<b>الآن أرسل سعر الإغلاق:</b>"
        )
        return AWAIT_PARTIAL_PRICE
        
    except Exception as e:
        loge.exception(f"❌ Error in partial_close_percent_received: {e}")
        await update.message.reply_text("❌ حدث خطأ في معالجة النسبة. يرجى المحاولة مرة أخرى أو /cancel")
        return AWAIT_PARTIAL_PERCENT

@uow_transaction
async def partial_close_price_received(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """استقبال سعر الإغلاق الجزئي - الإصدار النهائي"""
    try:
        user_id = update.effective_user.id
        
        # استخراج البيانات المحفوظة
        percent = context.user_data.get('partial_close_percent')
        rec_id = context.user_data.get('partial_close_rec_id')
        
        if not percent or not rec_id:
            await update.message.reply_text("❌ فقدت بيانات الجلسة. يرجى البدء من جديد.")
            return ConversationHandler.END
        
        # تحليل سعر الإغلاق
        price_text = update.message.text.strip()
        price = parse_number(price_text)
        if price is None:
            await update.message.reply_text(
                "❌ <b>سعر غير صالح</b>\n\n"
                "يجب أن يكون السعر رقم صحيح.\n"
                "مثال: <code>50000</code>\n\n"
                "يرجى المحاولة مرة أخرى:",
                parse_mode=ParseMode.HTML
            )
            return AWAIT_PARTIAL_PRICE
        
        # تنفيذ الإغلاق الجزئي
        trade_service = get_service(context, "trade_service", TradeService)
        await trade_service.partial_close_async(rec_id, str(user_id), percent, price, db_session)
        
        log.info(f"✅ User {user_id} executed custom partial close: {percent}% at {price}")
        
        await update.message.reply_html(
            f"✅ <b>تم الإغلاق الجزئي بنجاح</b>\n\n"
            f"📊 <b>النسبة:</b> {percent:g}%\n"
            f"💰 <b>السعر:</b> {price:g}\n"
            f"💎 <b>التوصية:</b> #{rec_id}"
        )
        
        return ConversationHandler.END
        
    except Exception as e:
        loge.exception(f"❌ Error in partial_close_price_received: {e}")
        await update.message.reply_text("❌ حدث خطأ في تنفيذ الإغلاق الجزئي. يرجى المحاولة مرة أخرى أو /cancel")
        return AWAIT_PARTIAL_PRICE
    finally:
        # تنظيف البيانات المؤقتة
        context.user_data.pop('partial_close_rec_id', None)
        context.user_data.pop('partial_close_percent', None)

async def partial_close_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """إلغاء محادثة الإغلاق الجزئي - الإصدار النهائي"""
    try:
        user_id = update.effective_user.id
        log.info(f"❌ User {user_id} cancelled partial close conversation")
        
        # تنظيف البيانات
        context.user_data.pop('partial_close_rec_id', None)
        context.user_data.pop('partial_close_percent', None)
        
        await update.message.reply_text(
            "❌ تم إلغاء عملية الإغلاق الجزئي.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END
    except Exception as e:
        loge.exception(f"❌ Error in partial_close_cancel: {e}")
        return ConversationHandler.END

def register_management_handlers(app: Application):
    """تسجيل معالجات الإدارة - الإصدار النهائي"""
    
    # مساحات الأسماء
    rec_ns = CallbackNamespace.RECOMMENDATION.value
    pos_ns = CallbackNamespace.POSITION.value
    nav_ns = CallbackNamespace.NAVIGATION.value
    
    # تسجيل المعالجات الأساسية
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
        per_message=False,
    )
    app.add_handler(partial_close_conv)
    
    log.info("✅ Management handlers registered successfully - FINAL VERSION")

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
    'partial_close_cancel',
    'ManagementSafetyManager'
]