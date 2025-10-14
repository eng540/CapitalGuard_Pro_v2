# src/capitalguard/interfaces/telegram/conversation_handlers.py (v31.0 - FINAL COMPLETE RELEASE)
"""
الإصدار النهائي الكامل الشامل - محرك إنشاء التوصيات المتكامل
✅ معالجة كاملة لجميع أنماط بيانات الاستدعاء
✅ نظام أمان متكامل للمحادثات
✅ تحقق شامل من صحة البيانات
✅ تكامل تام مع CallbackBuilder v2.0
✅ سجلات تفصيلية ومراقبة الأداء
"""

import logging
import uuid
import time
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Set, Optional, Tuple

from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from telegram.error import BadRequest, TelegramError

from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service
from .ui_texts import build_review_text_with_price
from .keyboards import (
    main_creation_keyboard,
    asset_choice_keyboard,
    side_market_keyboard,
    market_choice_keyboard,
    order_type_keyboard,
    review_final_keyboard,
    build_channel_picker_keyboard,
    CallbackNamespace,
    CallbackAction,
    CallbackBuilder
)
from .auth import require_active_user, require_analyst_user
from capitalguard.infrastructure.db.models import UserType
from .parsers import parse_number, parse_targets_list
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.infrastructure.db.repository import ChannelRepository, UserRepository
from .commands import start_cmd, myportfolio_cmd, help_cmd

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors")

# حالات المحادثة
(SELECT_METHOD, I_ASSET, I_SIDE_MARKET, I_MARKET_CHOICE, I_ORDER_TYPE, I_PRICES, I_REVIEW, I_NOTES, I_CHANNEL_PICKER) = range(9)

class ConversationSafetyManager:
    """مدير أمان متكامل للمحادثات"""
    
    @staticmethod
    def generate_secure_token() -> str:
        """إنشاء رمز آمن فريد"""
        return str(uuid.uuid4())
    
    @staticmethod
    def validate_token(stored_token: Optional[str], provided_token: str) -> bool:
        """التحقق من صحة الرمز"""
        if not stored_token or not provided_token:
            return False
        return stored_token.startswith(provided_token) or stored_token == provided_token
    
    @staticmethod
    async def disable_previous_keyboard(context: ContextTypes.DEFAULT_TYPE):
        """تعطيل لوحة المفاتيح السابقة"""
        if last_msg_info := context.user_data.get("last_conv_message"):
            chat_id, message_id = last_msg_info
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=chat_id, 
                    message_id=message_id, 
                    reply_markup=None
                )
            except (BadRequest, TelegramError):
                pass  # تجاهل الآمن للأخطاء

def get_user_draft(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    """الحصول على مسودة التوصية الحالية"""
    return context.user_data.setdefault("new_rec_draft", {
        "asset": "",
        "market": "Futures",
        "side": "",
        "order_type": "",
        "entry": None,
        "stop_loss": None,
        "targets": [],
        "notes": ""
    })

def clean_user_state(context: ContextTypes.DEFAULT_TYPE):
    """تنظيف كامل لحالة المستخدم"""
    keys_to_remove = [
        "new_rec_draft", 
        "last_conv_message", 
        "review_token", 
        "channel_picker_selection",
        "conversation_start_time"
    ]
    for key in keys_to_remove:
        context.user_data.pop(key, None)

async def safe_message_operation(operation_func, *args, **kwargs) -> bool:
    """تنفيذ آمن لعمليات الرسائل"""
    try:
        await operation_func(*args, **kwargs)
        return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            return True  # تجاهل الخطأ الآمن
        log.warning(f"Safe message operation failed: {e}")
        return False
    except TelegramError as e:
        log.error(f"Telegram error in safe operation: {e}")
        return False
    except Exception as e:
        log.error(f"Unexpected error in safe operation: {e}")
        return False

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

@uow_transaction
@require_active_user
@require_analyst_user
async def newrec_menu_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs) -> int:
    """نقطة بدء إنشاء توصية جديدة - الإصدار النهائي"""
    try:
        # تعطيل أي لوحات مفاتيح سابقة
        await ConversationSafetyManager.disable_previous_keyboard(context)
        clean_user_state(context)
        
        # تسجيل وقت بدء المحادثة
        context.user_data["conversation_start_time"] = time.time()
        
        user_id = update.effective_user.id
        log.info(f"🚀 User {user_id} started new recommendation creation")
        
        # إرسال رسالة البداية
        sent_message = await update.message.reply_html(
            "🚀 <b>إنشاء توصية جديدة</b>\n\nاختر طريقة إنشاء التوصية:",
            reply_markup=main_creation_keyboard()
        )
        
        # حفظ رسالة المحادثة الأخيرة
        context.user_data["last_conv_message"] = (sent_message.chat_id, sent_message.message_id)
        
        return SELECT_METHOD
        
    except Exception as e:
        loge.exception(f"❌ Critical failure in newrec_menu_entrypoint: {e}")
        await update.message.reply_text("❌ حدث خطأ حرج في بدء إنشاء التوصية. يرجى المحاولة مرة أخرى.")
        return ConversationHandler.END

@uow_transaction
@require_active_user
@require_analyst_user
async def start_interactive_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """بدء الوضع التفاعلي - الإصدار النهائي"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        log.info(f"🔧 User {user_id} selected interactive creation method")
        
        # جلب الأصول الحديثة للمستخدم
        trade_service = get_service(context, "trade_service", TradeService)
        recent_assets = trade_service.get_recent_assets_for_user(db_session, str(user_id))
        
        # تحديث الرسالة للخطوة الأولى
        success = await safe_message_operation(
            query.edit_message_text,
            text="<b>الخطوة 1/5: اختيار الأصل</b>\n\nاختر من القائمة أو اكتب رمز الأصل (مثال: <code>BTCUSDT</code>):",
            reply_markup=asset_choice_keyboard(recent_assets),
            parse_mode="HTML"
        )
        
        if success:
            context.user_data["last_conv_message"] = (query.message.chat_id, query.message.message_id)
            return I_ASSET
        else:
            await query.message.reply_text("❌ فشل في بدء الوضع التفاعلي. يرجى المحاولة مرة أخرى.")
            return ConversationHandler.END
            
    except Exception as e:
        loge.exception(f"❌ Critical failure in start_interactive_entrypoint: {e}")
        await update.callback_query.message.reply_text("❌ حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى.")
        return ConversationHandler.END

async def asset_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالج اختيار الأصل - الإصدار النهائي"""
    try:
        draft = get_user_draft(context)
        user_id = update.effective_user.id
        
        if update.callback_query:
            # معالجة اختيار من القائمة
            query = update.callback_query
            await query.answer()
            
            callback_data = parse_callback_data_advanced(query.data)
            asset_value = callback_data.get('action', '').replace('asset_', '')
            
            if asset_value.lower() == "new":
                # طلب إدخال أصلاً جديداً
                await query.edit_message_text(
                    "✍️ <b>إدخال أصلاً جديداً</b>\n\nاكتب رمز الأصل (مثال: <code>BTCUSDT</code>):",
                    parse_mode="HTML"
                )
                return I_ASSET
            else:
                asset = asset_value.upper()
                message_obj = query.message
        else:
            # معالجة الإدخال النصي
            asset = (update.message.text or "").strip().upper()
            message_obj = update.message
            
            # حذف رسالة المستخدم إن أمكن
            try:
                await update.message.delete()
            except (BadRequest, TelegramError):
                pass

        # التحقق من صحة الرمز
        market_data_service = get_service(context, "market_data_service", MarketDataService)
        if not market_data_service.is_valid_symbol(asset, draft.get("market", "Futures")):
            error_text = f"❌ الرمز '<b>{asset}</b>' غير صالح أو غير مدعوم في سوق {draft.get('market', 'Futures')}.\n\nيرجى إدخال رمز صالح (مثال: <code>BTCUSDT</code>):"
            
            if update.callback_query:
                await query.edit_message_text(error_text, parse_mode="HTML")
            else:
                await message_obj.reply_html(error_text)
            return I_ASSET

        # حفظ الأصل وانتقال للخطوة التالية
        draft["asset"] = asset
        log.info(f"✅ User {user_id} selected asset: {asset}")
        
        next_step_text = (
            f"✅ <b>تم اختيار الأصل:</b> {asset}\n\n"
            f"<b>الخطوة 2/5: اختيار الاتجاه والسوق</b>\n\n"
            f"اختر اتجاه التداول:"
        )
        
        if update.callback_query:
            await query.edit_message_text(
                next_step_text,
                reply_markup=side_market_keyboard(draft["market"]),
                parse_mode="HTML"
            )
        else:
            new_message = await message_obj.reply_html(
                next_step_text,
                reply_markup=side_market_keyboard(draft["market"])
            )
            context.user_data["last_conv_message"] = (new_message.chat_id, new_message.message_id)
        
        return I_SIDE_MARKET
        
    except Exception as e:
        loge.exception(f"❌ Error in asset_chosen: {e}")
        error_msg = "❌ حدث خطأ في معالجة الأصل. يرجى المحاولة مرة أخرى."
        if update.callback_query:
            await update.callback_query.message.reply_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
        return I_ASSET

async def side_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالج اختيار الاتجاه - الإصدار النهائي"""
    try:
        query = update.callback_query
        await query.answer()
        
        draft = get_user_draft(context)
        user_id = query.from_user.id
        
        callback_data = parse_callback_data_advanced(query.data)
        action = callback_data.get('action', '')
        
        if action == "change_market_menu":
            # عرض قائمة اختيار السوق
            await query.edit_message_text(
                "🔄 <b>تغيير السوق</b>\n\nاختر سوق التداول:",
                reply_markup=market_choice_keyboard(),
                parse_mode="HTML"
            )
            return I_MARKET_CHOICE
        else:
            # معالجة اختيار الاتجاه
            side = action.replace('side_', '')
            draft["side"] = side
            
            log.info(f"✅ User {user_id} selected side: {side} for market: {draft['market']}")
            
            await query.edit_message_text(
                f"✅ <b>الاتجاه:</b> {side} | <b>السوق:</b> {draft['market']}\n\n"
                f"<b>الخطوة 3/5: نوع أمر الدخول</b>\n\n"
                f"اختر نوع أمر الدخول:",
                reply_markup=order_type_keyboard(),
                parse_mode="HTML"
            )
            return I_ORDER_TYPE
            
    except Exception as e:
        loge.exception(f"❌ Error in side_chosen: {e}")
        await update.callback_query.message.reply_text("❌ حدث خطأ في اختيار الاتجاه. يرجى المحاولة مرة أخرى.")
        return I_SIDE_MARKET

async def market_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالج اختيار السوق - الإصدار النهائي"""
    try:
        query = update.callback_query
        await query.answer()
        
        draft = get_user_draft(context)
        user_id = query.from_user.id
        
        callback_data = parse_callback_data_advanced(query.data)
        action = callback_data.get('action', '')
        
        if action == "market_back":
            # العودة لاختيار الاتجاه
            await query.edit_message_text(
                "<b>الخطوة 2/5: اختيار الاتجاه والسوق</b>\n\nاختر اتجاه التداول:",
                reply_markup=side_market_keyboard(draft["market"]),
                parse_mode="HTML"
            )
            return I_SIDE_MARKET
        else:
            # تحديث السوق
            market = action.replace('market_', '')
            old_market = draft["market"]
            draft["market"] = market
            
            log.info(f"🔄 User {user_id} changed market: {old_market} -> {market}")
            
            await query.edit_message_text(
                f"✅ <b>تم تحديث السوق:</b> {market}\n\n"
                f"<b>الخطوة 2/5: اختيار الاتجاه والسوق</b>\n\n"
                f"اختر اتجاه التداول:",
                reply_markup=side_market_keyboard(market),
                parse_mode="HTML"
            )
            return I_SIDE_MARKET
            
    except Exception as e:
        loge.exception(f"❌ Error in market_chosen: {e}")
        await update.callback_query.message.reply_text("❌ حدث خطأ في اختيار السوق. يرجى المحاولة مرة أخرى.")
        return I_MARKET_CHOICE

async def order_type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالج اختيار نوع الطلب - الإصدار النهائي"""
    try:
        query = update.callback_query
        await query.answer()
        
        draft = get_user_draft(context)
        user_id = query.from_user.id
        
        callback_data = parse_callback_data_advanced(query.data)
        order_type = callback_data.get('action', '').replace('type_', '')
        draft["order_type"] = order_type
        
        log.info(f"✅ User {user_id} selected order type: {order_type}")
        
        # جلب السعر الحالي للسوق
        price_service = get_service(context, "price_service", PriceService)
        current_price = await price_service.get_cached_price(draft["asset"], draft["market"])
        
        current_price_info = ""
        price_instructions = ""
        
        if current_price:
            current_price_info = f"\n\n💰 <b>السعر الحالي لـ {draft['asset']}:</b> ~{current_price:g}"
        
        if order_type == "MARKET":
            price_instructions = (
                f"أدخل في سطر واحد:\n"
                f"<code>وقف_الخسارة الهدف1@نسبة1 الهدف2@نسبة2 ...</code>\n\n"
                f"<b>مثال:</b>\n<code>58000 60000@30 62000@50 65000@20</code>\n\n"
                f"💡 <b>ملاحظة:</b> سيتم الدخول بالسعر الحالي للسوق"
            )
        else:
            price_instructions = (
                f"أدخل في سطر واحد:\n"
                f"<code>سعر_الدخول وقف_الخسارة الهدف1@نسبة1 الهدف2@نسبة2 ...</code>\n\n"
                f"<b>مثال:</b>\n<code>59000 58000 60000@30 62000@50 65000@20</code>"
            )
        
        await query.edit_message_text(
            f"✅ <b>نوع الطلب:</b> {order_type}\n\n"
            f"<b>الخطوة 4/5: إدخال الأسعار</b>\n\n"
            f"{price_instructions}"
            f"{current_price_info}",
            parse_mode="HTML"
        )
        return I_PRICES
        
    except Exception as e:
        loge.exception(f"❌ Error in order_type_chosen: {e}")
        await update.callback_query.message.reply_text("❌ حدث خطأ في اختيار نوع الطلب. يرجى المحاولة مرة أخرى.")
        return I_ORDER_TYPE

async def prices_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالج استقبال الأسعار - الإصدار النهائي"""
    try:
        draft = get_user_draft(context)
        user_id = update.effective_user.id
        user_input = (update.message.text or "").strip()
        tokens = user_input.split()
        
        log.info(f"🔢 User {user_id} entered prices: {user_input}")
        
        # حذف رسالة المستخدم
        try:
            await update.message.delete()
        except (BadRequest, TelegramError):
            pass
        
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        
        try:
            if draft["order_type"] == "MARKET":
                # تحليل صيغة MARKET: STOP TARGETS...
                if len(tokens) < 2:
                    raise ValueError(
                        "❌ <b>تنسيق غير صحيح</b>\n\n"
                        "لأوامر MARKET:\n"
                        "أدخل <code>وقف_الخسارة</code> ثم <code>الأهداف</code>\n\n"
                        "<b>مثال:</b>\n<code>58000 60000@30 62000@50</code>"
                    )
                
                stop_loss = parse_number(tokens[0])
                targets = parse_targets_list(tokens[1:])
                
                # جلب السعر الحالي للسوق
                live_price_float = await price_service.get_cached_price(
                    draft["asset"], draft["market"], True
                )
                
                if not live_price_float:
                    raise ValueError("❌ تعذر جلب سعر السوق الحالي. يرجى المحاولة لاحقاً.")
                
                live_price = Decimal(str(live_price_float))
                entry_price = live_price
                
                # التحقق من صحة الأهداف بالنسبة للسعر الحالي
                target_prices = [t['price'] for t in targets]
                if draft["side"] == "LONG":
                    invalid_targets = [f"{p:g}" for p in target_prices if p <= live_price]
                    if invalid_targets:
                        raise ValueError(
                            f"❌ <b>أهداف غير صالحة للشراء (LONG)</b>\n\n"
                            f"💰 <b>السعر الحالي:</b> {live_price:g}\n"
                            f"🎯 <b>أهداف أقل من السعر الحالي:</b> {', '.join(invalid_targets)}\n\n"
                            f"💡 <b>ملاحظة:</b> جميع أهداف الشراء يجب أن تكون <b>أعلى</b> من السعر الحالي"
                        )
                else:  # SHORT
                    invalid_targets = [f"{p:g}" for p in target_prices if p >= live_price]
                    if invalid_targets:
                        raise ValueError(
                            f"❌ <b>أهداف غير صالحة للبيع (SHORT)</b>\n\n"
                            f"💰 <b>السعر الحالي:</b> {live_price:g}\n"
                            f"🎯 <b>أهداف أعلى من السعر الحالي:</b> {', '.join(invalid_targets)}\n\n"
                            f"💡 <b>ملاحظة:</b> جميع أهداف البيع يجب أن تكون <b>أقل</b> من السعر الحالي"
                        )
                
                # التحقق من صحة البيانات
                trade_service._validate_recommendation_data(
                    draft["side"], entry_price, stop_loss, targets
                )
                
                draft.update({
                    "entry": entry_price,
                    "stop_loss": stop_loss,
                    "targets": targets
                })
                
            else:
                # تحليل صيغة LIMIT/STOP: ENTRY STOP TARGETS...
                if len(tokens) < 3:
                    raise ValueError(
                        "❌ <b>تنسيق غير صحيح</b>\n\n"
                        "لأوامر LIMIT/STOP:\n"
                        "أدخل <code>سعر_الدخول وقف_الخسارة</code> ثم <code>الأهداف</code>\n\n"
                        "<b>مثال:</b>\n<code>59000 58000 60000@30 62000@50</code>"
                    )
                
                entry = parse_number(tokens[0])
                stop_loss = parse_number(tokens[1])
                targets = parse_targets_list(tokens[2:])
                
                # التحقق من صحة البيانات
                trade_service._validate_recommendation_data(
                    draft["side"], entry, stop_loss, targets
                )
                
                draft.update({
                    "entry": entry,
                    "stop_loss": stop_loss,
                    "targets": targets
                })
            
            if not draft.get("targets"):
                raise ValueError("❌ لم يتم تحديد أهداف صالحة. يرجى إدخال أهداف على الأقل.")
            
            log.info(f"✅ Prices validated successfully for user {user_id}")
            
        except (ValueError, InvalidOperation, TypeError) as e:
            error_msg = str(e)
            if "Risk/Reward ratio" in error_msg:
                error_msg = (
                    f"❌ <b>نسبة المخاطرة/العائد غير كافية</b>\n\n"
                    f"{error_msg}\n\n"
                    f"💡 <b>نصيحة:</b> حاول تعديل وقف الخسارة أو الأهداف لتحسين النسبة"
                )
            
            await update.message.reply_html(error_msg)
            return I_PRICES
            
        except Exception as e:
            loge.exception(f"Validation error for user {user_id}: {e}")
            await update.message.reply_html("❌ <b>خطأ في تحليل الأسعار</b>\n\nيرجى التأكد من التنسيق والمحاولة مرة أخرى.")
            return I_PRICES
        
        # الانتقال لبطاقة المراجعة
        return await show_review_card(update, context)
        
    except Exception as e:
        loge.exception(f"❌ Unexpected error in prices_received: {e}")
        await update.message.reply_text("❌ حدث خطأ غير متوقع في معالجة الأسعار. يرجى المحاولة مرة أخرى.")
        return I_PRICES

async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """عرض بطاقة المراجعة النهائية - الإصدار النهائي"""
    try:
        draft = get_user_draft(context)
        user_id = update.effective_user.id
        
        # إنشاء أو استخدام الرمز الحالي
        review_token = context.user_data.get("review_token") or ConversationSafetyManager.generate_secure_token()
        context.user_data["review_token"] = review_token
        
        # جلب السعر الحالي للمعاينة
        price_service = get_service(context, "price_service", PriceService)
        preview_price = await price_service.get_cached_price(draft["asset"], draft["market"])
        
        # بناء نص المراجعة
        review_text = build_review_text_with_price(draft, preview_price)
        
        # تحديد الرسالة المستهدفة للتعديل
        if update.callback_query:
            message = update.callback_query.message
            await update.callback_query.answer()
        else:
            message = update.message
        
        target_chat_id, target_message_id = context.user_data.get(
            "last_conv_message", 
            (message.chat_id, message.message_id)
        )
        
        # محاولة تعديل الرسالة الحالية
        try:
            sent_message = await context.bot.edit_message_text(
                chat_id=target_chat_id,
                message_id=target_message_id,
                text=review_text,
                reply_markup=review_final_keyboard(review_token),
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                # تجاهل الخطأ الآمن
                sent_message = message
            else:
                # إنشاء رسالة جديدة
                sent_message = await context.bot.send_message(
                    chat_id=target_chat_id,
                    text=review_text,
                    reply_markup=review_final_keyboard(review_token),
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
        
        # حفظ رسالة المحادثة الأخيرة
        context.user_data["last_conv_message"] = (sent_message.chat_id, sent_message.message_id)
        
        log.info(f"📋 Review card shown for user {user_id}")
        
        return I_REVIEW
        
    except Exception as e:
        loge.exception(f"❌ Error in show_review_card: {e}")
        error_msg = "❌ حدث خطأ في عرض بطاقة المراجعة. يرجى المحاولة مرة أخرى."
        if update.callback_query:
            await update.callback_query.message.reply_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
        return I_PRICES

@uow_transaction
async def add_notes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs) -> int:
    """معالج إضافة الملاحظات - الإصدار النهائي"""
    try:
        query = update.callback_query
        await query.answer()
        
        await query.edit_message_text(
            f"{query.message.text}\n\n"
            f"✍️ <b>إضافة الملاحظات</b>\n\n"
            f"أرسل الملاحظات الإضافية لهذه التوصية (اختياري):",
            parse_mode="HTML"
        )
        return I_NOTES
        
    except Exception as e:
        loge.exception(f"❌ Error in add_notes_handler: {e}")
        await update.callback_query.message.reply_text("❌ حدث خطأ في فتح محرر الملاحظات.")
        return I_REVIEW

async def notes_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالج استقبال الملاحظات - الإصدار النهائي"""
    try:
        draft = get_user_draft(context)
        user_id = update.effective_user.id
        
        notes_text = (update.message.text or "").strip()
        draft["notes"] = notes_text
        
        # حذف رسالة المستخدم
        try:
            await update.message.delete()
        except (BadRequest, TelegramError):
            pass
        
        log.info(f"📝 User {user_id} added notes: {len(notes_text)} characters")
        
        # العودة لبطاقة المراجعة
        return await show_review_card(update, context)
        
    except Exception as e:
        loge.exception(f"❌ Error in notes_received: {e}")
        await update.message.reply_text("❌ حدث خطأ في حفظ الملاحظات. يرجى المحاولة مرة أخرى.")
        return I_NOTES

@uow_transaction
async def choose_channels_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """معالج اختيار القنوات - الإصدار النهائي"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        review_token = context.user_data.get("review_token", "")
        
        log.info(f"📢 User {user_id} opening channel picker")
        
        # جلب القنوات المتاحة
        user = UserRepository(db_session).find_by_telegram_id(user_id)
        all_channels = ChannelRepository(db_session).list_by_analyst(user.id, only_active=False)
        
        # تهيئة القنوات المختارة
        selected_ids = context.user_data.setdefault(
            "channel_picker_selection", 
            {ch.telegram_channel_id for ch in all_channels if ch.is_active}
        )
        
        # بناء لوحة المفاتيح
        keyboard = build_channel_picker_keyboard(review_token, all_channels, selected_ids)
        
        await query.edit_message_text(
            "📢 <b>اختيار قنوات النشر</b>\n\n"
            "اختر القنوات التي تريد نشر التوصية فيها:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return I_CHANNEL_PICKER
        
    except Exception as e:
        loge.exception(f"❌ Error in choose_channels_handler: {e}")
        await update.callback_query.message.reply_text("❌ حدث خطأ في تحميل القنوات. يرجى المحاولة مرة أخرى.")
        return I_REVIEW

@uow_transaction
async def channel_picker_logic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """معالج منطق اختيار القنوات - الإصدار النهائي"""
    try:
        query = update.callback_query
        await query.answer()
        
        callback_data = parse_callback_data_advanced(query.data)
        if not callback_data['is_valid']:
            await query.answer("❌ بيانات غير صالحة", show_alert=True)
            return I_CHANNEL_PICKER
        
        action = callback_data['action']
        params = callback_data['params']
        
        if not params:
            await query.answer("❌ بيانات ناقصة", show_alert=True)
            return I_CHANNEL_PICKER
        
        token = params[0]
        selected_ids = context.user_data.get("channel_picker_selection", set())
        page = 1
        
        # معالجة الإجراءات
        if action == CallbackAction.TOGGLE.value and len(params) >= 3:
            channel_id = int(params[1])
            page = int(params[2]) if len(params) > 2 else 1
            
            if channel_id in selected_ids:
                selected_ids.remove(channel_id)
            else:
                selected_ids.add(channel_id)
                
        elif action == CallbackAction.NAVIGATE.value and len(params) >= 2:
            page = int(params[1])
        
        # جلب القنوات وبناء اللوحة
        user = UserRepository(db_session).find_by_telegram_id(query.from_user.id)
        all_channels = ChannelRepository(db_session).list_by_analyst(user.id, only_active=False)
        keyboard = build_channel_picker_keyboard(token, all_channels, selected_ids, page=page)
        
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return I_CHANNEL_PICKER
        
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            await query.answer()
            return I_CHANNEL_PICKER
        else:
            loge.exception(f"❌ Unhandled BadRequest in channel_picker: {e}")
            await query.answer("❌ فشل في تحديث القنوات", show_alert=True)
            return I_CHANNEL_PICKER
    except Exception as e:
        loge.exception(f"❌ Error in channel_picker_logic_handler: {e}")
        await query.answer("❌ حدث خطأ في معالجة الاختيار", show_alert=True)
        return I_CHANNEL_PICKER

@uow_transaction
async def publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """معالج النشر النهائي - الإصدار النهائي"""
    try:
        query = update.callback_query
        await query.answer("🔄 جاري النشر...")
        
        user_id = query.from_user.id
        callback_data = parse_callback_data_advanced(query.data)
        params = callback_data.get('params', [])
        token_in_callback = params[0] if params else ""
        
        # التحقق من صحة الرمز
        stored_token = context.user_data.get("review_token", "")
        if not ConversationSafetyManager.validate_token(stored_token, token_in_callback):
            await query.edit_message_text(
                "❌ <b>إجراء منتهي الصلاحية</b>\n\n"
                "انتهت صلاحية هذه العملية. يرجى بدء توصية جديدة.",
                parse_mode="HTML",
                reply_markup=None
            )
            clean_user_state(context)
            return ConversationHandler.END
        
        # تجهيز البيانات للنشر
        draft = get_user_draft(context)
        draft["target_channel_ids"] = context.user_data.get("channel_picker_selection", set())
        
        log.info(f"🚀 User {user_id} publishing recommendation for {draft['asset']}")
        
        # إنشاء التوصية ونشرها
        trade_service = get_service(context, "trade_service", TradeService)
        rec, report = await trade_service.create_and_publish_recommendation_async(
            user_id=str(user_id), db_session=db_session, **draft
        )
        
        # معالجة النتيجة
        if report.get("success"):
            success_count = len(report["success"])
            await query.edit_message_text(
                f"✅ <b>تم النشر بنجاح</b>\n\n"
                f"📊 <b>التوصية:</b> #{rec.id}\n"
                f"💎 <b>الأصل:</b> {rec.asset.value}\n"
                f"📈 <b>تم النشر في:</b> {success_count} قناة\n"
                f"🕒 <b>الوقت:</b> {rec.created_at.strftime('%Y-%m-%d %H:%M')}",
                parse_mode="HTML",
                reply_markup=None
            )
            log.info(f"✅ Recommendation #{rec.id} published successfully by user {user_id}")
        else:
            failed_reason = report.get('failed', [{}])[0].get('reason', 'سبب غير معروف')
            await query.edit_message_text(
                f"⚠️ <b>تم الحفظ مع أخطاء في النشر</b>\n\n"
                f"📊 <b>التوصية:</b> #{rec.id}\n"
                f"💎 <b>الأصل:</b> {rec.asset.value}\n"
                f"❌ <b>سبب الفشل:</b> {failed_reason}\n\n"
                f"💡 <b>ملاحظة:</b> التوصية محفوظة ولكن تحتاج نشر يدوي",
                parse_mode="HTML",
                reply_markup=None
            )
            log.warning(f"⚠️ Recommendation #{rec.id} publication failed: {failed_reason}")
        
        # حساب وقت المحادثة
        start_time = context.user_data.get("conversation_start_time", 0)
        conversation_duration = time.time() - start_time if start_time else 0
        log.info(f"⏱️ Conversation completed in {conversation_duration:.2f} seconds for user {user_id}")
        
        return ConversationHandler.END
        
    except Exception as e:
        loge.exception(f"❌ Critical failure in publish_handler: {e}")
        await query.edit_message_text(
            f"❌ <b>حدث خطأ حرج أثناء النشر</b>\n\n"
            f"الخطأ: {str(e)[:100]}...\n\n"
            f"يرجى المحاولة مرة أخرى أو الاتصال بالدعم.",
            parse_mode="HTML",
            reply_markup=None
        )
        return ConversationHandler.END
    finally:
        clean_user_state(context)

async def cancel_conv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالج إلغاء المحادثة - الإصدار النهائي"""
    try:
        user_id = update.effective_user.id
        
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            message = query.message
        else:
            message = update.message
        
        # تعطيل لوحات المفاتيح السابقة
        await ConversationSafetyManager.disable_previous_keyboard(context)
        
        # إرسال رسالة الإلغاء
        if context.user_data.get("last_conv_message"):
            try:
                await context.bot.edit_message_text(
                    "❌ <b>تم إلغاء العملية</b>\n\n"
                    "يمكنك البدء من جديد باستخدام /newrec",
                    chat_id=context.user_data["last_conv_message"][0],
                    message_id=context.user_data["last_conv_message"][1],
                    parse_mode="HTML",
                    reply_markup=None
                )
            except (BadRequest, TelegramError):
                await message.reply_text(
                    "❌ تم إلغاء العملية",
                    reply_markup=ReplyKeyboardRemove()
                )
        else:
            await message.reply_text(
                "❌ تم إلغاء العملية", 
                reply_markup=ReplyKeyboardRemove()
            )
        
        log.info(f"❌ User {user_id} cancelled conversation")
        
        return ConversationHandler.END
        
    except Exception as e:
        loge.exception(f"❌ Error in cancel_conv_handler: {e}")
        return ConversationHandler.END
    finally:
        clean_user_state(context)

def register_conversation_handlers(app: Application):
    """تسجيل معالجات المحادثة - الإصدار النهائي"""
    
    # مساحات الأسماء
    rec_ns = CallbackNamespace.RECOMMENDATION.value
    pub_ns = CallbackNamespace.PUBLICATION.value
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("newrec", newrec_menu_entrypoint)],
        states={
            SELECT_METHOD: [
                CallbackQueryHandler(start_interactive_entrypoint, pattern="^method_")
            ],
            I_ASSET: [
                CallbackQueryHandler(asset_chosen, pattern="^asset_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, asset_chosen),
            ],
            I_SIDE_MARKET: [
                CallbackQueryHandler(side_chosen, pattern="^side_"),
                CallbackQueryHandler(market_chosen, pattern="^change_market_menu"),
            ],
            I_MARKET_CHOICE: [
                CallbackQueryHandler(market_chosen, pattern="^market_")
            ],
            I_ORDER_TYPE: [
                CallbackQueryHandler(order_type_chosen, pattern="^type_")
            ],
            I_PRICES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, prices_received)
            ],
            I_REVIEW: [
                CallbackQueryHandler(publish_handler, pattern=rf"^{rec_ns}:publish:"),
                CallbackQueryHandler(choose_channels_handler, pattern=rf"^{rec_ns}:choose_channels:"),
                CallbackQueryHandler(add_notes_handler, pattern=rf"^{rec_ns}:add_notes:"),
                CallbackQueryHandler(cancel_conv_handler, pattern=rf"^{rec_ns}:cancel"),
            ],
            I_NOTES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, notes_received)
            ],
            I_CHANNEL_PICKER: [
                CallbackQueryHandler(channel_picker_logic_handler, pattern=rf"^{pub_ns}:"),
                CallbackQueryHandler(show_review_card, pattern=rf"^{pub_ns}:{CallbackAction.BACK.value}:"),
                CallbackQueryHandler(publish_handler, pattern=rf"^{pub_ns}:{CallbackAction.CONFIRM.value}:"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conv_handler),
            CommandHandler("start", start_cmd),
            CommandHandler(["myportfolio", "open"], myportfolio_cmd),
            CommandHandler("help", help_cmd),
        ],
        name="recommendation_creation",
        persistent=False,
        per_user=True,
        per_chat=True,
        per_message=True,
    )
    
    app.add_handler(conv_handler)
    log.info("✅ Conversation handlers registered successfully - FINAL VERSION")

# تصدير الوظائف العامة
__all__ = [
    'register_conversation_handlers',
    'newrec_menu_entrypoint',
    'start_interactive_entrypoint',
    'asset_chosen',
    'side_chosen',
    'market_chosen',
    'order_type_chosen',
    'prices_received',
    'show_review_card',
    'add_notes_handler',
    'notes_received',
    'choose_channels_handler',
    'channel_picker_logic_handler',
    'publish_handler',
    'cancel_conv_handler',
    'ConversationSafetyManager',
    'parse_callback_data_advanced'
]