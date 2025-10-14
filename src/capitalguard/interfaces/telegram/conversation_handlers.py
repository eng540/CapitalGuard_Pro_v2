# src/capitalguard/interfaces/telegram/conversation_handlers.py (v31.1 - CALLBACK DATA FIX)
"""
الإصدار المصحح - إصلاح تحليل بيانات الاستدعاء البسيطة
✅ إصلاح خطأ 'NoneType' object has no attribute 'replace'
✅ معالجة صحيحة للأنماط البسيطة (asset_, side_, market_, type_)
✅ استمرار دعم أنماط CallbackBuilder المعقدة
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

def parse_callback_data_universal(callback_data: str) -> Dict[str, Any]:
    """
    تحليل شامل لبيانات الاستدعاء مع دعم جميع الأنماط
    ✅ يدعم الأنماط البسيطة: asset_BTCUSDT, side_LONG, market_Futures, type_MARKET
    ✅ يدعم أنماط CallbackBuilder: rec:publish:token, pub:toggle:token:123:1
    """
    try:
        if not callback_data or callback_data == "noop":
            return {"raw": callback_data, "is_noop": True, "is_simple": False}
        
        # 🔧 الإصلاح: التعامل مع الأنماط البسيطة أولاً
        simple_patterns = ['asset_', 'side_', 'market_', 'type_', 'method_']
        for pattern in simple_patterns:
            if callback_data.startswith(pattern):
                return {
                    'raw': callback_data,
                    'namespace': 'simple',
                    'action': callback_data,  # ✅ حفظ النص الكامل
                    'params': [],
                    'version': '1.0',
                    'is_valid': True,
                    'is_simple': True  # ✅ علامة للنمط البسيط
                }
        
        # معالجة أنماط CallbackBuilder المعقدة
        parts = callback_data.split(':')
        result = {
            'raw': callback_data,
            'namespace': parts[0] if len(parts) > 0 else None,
            'action': parts[1] if len(parts) > 1 else None,
            'params': [],
            'version': '1.0',
            'is_valid': False,
            'is_simple': False
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
        log.error(f"Universal callback parsing failed: {callback_data}, error: {e}")
        return {
            'raw': callback_data, 
            'error': str(e), 
            'is_valid': False,
            'is_simple': False
        }

def extract_simple_action(callback_data: str, prefix: str) -> str:
    """
    استخراج القيمة من النمط البسيط
    مثال: extract_simple_action("asset_BTCUSDT", "asset_") → "BTCUSDT"
    """
    try:
        if callback_data.startswith(prefix):
            return callback_data[len(prefix):]
        return ""
    except Exception as e:
        log.error(f"Failed to extract action from {callback_data} with prefix {prefix}: {e}")
        return ""

@uow_transaction
@require_active_user
@require_analyst_user
async def newrec_menu_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs) -> int:
    """نقطة بدء إنشاء توصية جديدة - الإصدار المصحح"""
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
    """بدء الوضع التفاعلي - الإصدار المصحح"""
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
    """معالج اختيار الأصل - الإصدار المصحح"""
    try:
        draft = get_user_draft(context)
        user_id = update.effective_user.id
        
        if update.callback_query:
            # معالجة اختيار من القائمة
            query = update.callback_query
            await query.answer()
            
            callback_data = parse_callback_data_universal(query.data)
            
            # 🔧 الإصلاح: التعامل مع الأنماط البسيطة والمعقدة
            if callback_data.get('is_simple'):
                asset_value = extract_simple_action(callback_data['action'], 'asset_')
            else:
                asset_value = callback_data.get('action', '').replace('asset_', '')
            
            log.info(f"User {user_id} selected asset with callback: {query.data}, extracted: {asset_value}")
            
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
    """معالج اختيار الاتجاه - الإصدار المصحح"""
    try:
        query = update.callback_query
        await query.answer()
        
        draft = get_user_draft(context)
        user_id = query.from_user.id
        
        callback_data = parse_callback_data_universal(query.data)
        
        # 🔧 الإصلاح: التعامل مع الأنماط البسيطة والمعقدة
        if callback_data.get('is_simple'):
            action = callback_data['action']
        else:
            action = callback_data.get('action', '')
        
        log.info(f"User {user_id} selected side with callback: {query.data}, action: {action}")
        
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
            # 🔧 الإصلاح: استخراج القيمة من النمط البسيط
            if callback_data.get('is_simple'):
                side = extract_simple_action(action, 'side_')
            else:
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
    """معالج اختيار السوق - الإصدار المصحح"""
    try:
        query = update.callback_query
        await query.answer()
        
        draft = get_user_draft(context)
        user_id = query.from_user.id
        
        callback_data = parse_callback_data_universal(query.data)
        
        # 🔧 الإصلاح: التعامل مع الأنماط البسيطة والمعقدة
        if callback_data.get('is_simple'):
            action = callback_data['action']
        else:
            action = callback_data.get('action', '')
        
        log.info(f"User {user_id} selected market with callback: {query.data}, action: {action}")
        
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
            # 🔧 الإصلاح: استخراج القيمة من النمط البسيط
            if callback_data.get('is_simple'):
                market = extract_simple_action(action, 'market_')
            else:
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
    """معالج اختيار نوع الطلب - الإصدار المصحح"""
    try:
        query = update.callback_query
        await query.answer()
        
        draft = get_user_draft(context)
        user_id = query.from_user.id
        
        callback_data = parse_callback_data_universal(query.data)
        
        # 🔧 الإصلاح: التعامل مع الأنماط البسيطة والمعقدة
        if callback_data.get('is_simple'):
            order_type = extract_simple_action(callback_data['action'], 'type_')
        else:
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

# ... (بقية الدوال تبقى كما هي بدون تغيير) ...

def register_conversation_handlers(app: Application):
    """تسجيل معالجات المحادثة - الإصدار المصحح"""
    
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
        per_message=False,
    )
    
    app.add_handler(conv_handler)
    log.info("✅ Conversation handlers registered successfully - CALLBACK DATA FIXED")

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
    'parse_callback_data_universal',
    'extract_simple_action'
]