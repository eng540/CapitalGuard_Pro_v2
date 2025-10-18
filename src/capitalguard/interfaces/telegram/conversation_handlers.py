# src/capitalguard/interfaces/telegram/conversation_handlers.py (v35.5 - Production Fixed)
import logging
import uuid
import time
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Set

from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    Application, ContextTypes, ConversationHandler, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters
)
from telegram.error import BadRequest, TelegramError
from telegram.constants import ParseMode

from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service, parse_cq_parts
from .ui_texts import build_review_text_with_price
from .keyboards import (
    main_creation_keyboard, asset_choice_keyboard, side_market_keyboard,
    market_choice_keyboard, order_type_keyboard, review_final_keyboard,
    build_channel_picker_keyboard, CallbackBuilder, CallbackNamespace, CallbackAction
)
from .auth import require_active_user, require_analyst_user
from .parsers import parse_quick_command, parse_text_editor, parse_number, parse_targets_list
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.infrastructure.db.repository import ChannelRepository, UserRepository

log = logging.getLogger(__name__)

# --- Conversation States ---
(
    SELECT_METHOD, AWAIT_TEXT_INPUT, AWAITING_ASSET, AWAITING_SIDE, AWAITING_TYPE,
    AWAITING_PRICES, AWAITING_REVIEW, AWAITING_NOTES, AWAITING_CHANNELS
) = range(9)

# --- State Management Keys ---
DRAFT_KEY = "rec_creation_draft"
CHANNEL_PICKER_KEY = "channel_picker_selection"
LAST_ACTIVITY_KEY = "last_activity"

# --- Timeout Configuration ---
CONVERSATION_TIMEOUT = 1800  # 30 دقيقة

def clean_creation_state(context: ContextTypes.DEFAULT_TYPE):
    """تنظيف حالة إنشاء التوصية بشكل كامل"""
    keys_to_remove = [DRAFT_KEY, CHANNEL_PICKER_KEY, LAST_ACTIVITY_KEY]
    for key in keys_to_remove:
        context.user_data.pop(key, None)

def update_activity(context: ContextTypes.DEFAULT_TYPE):
    """تحديث وقت النشاط الأخير"""
    context.user_data[LAST_ACTIVITY_KEY] = time.time()

def check_conversation_timeout(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """التحقق من انتهاء مدة المحادثة"""
    last_activity = context.user_data.get(LAST_ACTIVITY_KEY, 0)
    current_time = time.time()
    return current_time - last_activity > CONVERSATION_TIMEOUT

async def safe_edit_message(query, text=None, reply_markup=None, parse_mode=None):
    """تحرير الرسالة بشكل آمن مع استعادة الأخطاء"""
    try:
        if text is not None:
            await query.edit_message_text(
                text=text, 
                reply_markup=reply_markup, 
                parse_mode=parse_mode,
                disable_web_page_preview=True
            )
        elif reply_markup is not None:
            await query.edit_message_reply_markup(reply_markup=reply_markup)
        return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            return True
        log.warning(f"BadRequest in safe_edit_message: {e}")
        return False
    except Exception as e:
        log.error(f"Error in safe_edit_message: {e}")
        return False

async def handle_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة انتهاء مدة المحادثة"""
    if check_conversation_timeout(context):
        clean_creation_state(context)
        if update.callback_query:
            await update.callback_query.answer("انتهت مدة الجلسة", show_alert=True)
            await safe_edit_message(update.callback_query, "⏰ انتهت مدة الجلسة. يرجى البدء من جديد باستخدام /newrec")
        elif update.message:
            await update.message.reply_text("⏰ انتهت مدة الجلسة. يرجى البدء من جديد باستخدام /newrec")
        return True
    return False

# --- Entry Points ---
@uow_transaction
@require_active_user
@require_analyst_user
async def newrec_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """نقطة بدء إنشاء توصية جديدة"""
    clean_creation_state(context)
    update_activity(context)
    
    welcome_text = """
🚀 <b>منشئ التوصيات الجديدة</b>

اختر طريقة الإدخال المناسبة لك:

• <b>💬 المنشئ التفاعلي</b> - دليل خطوة بخطوة
• <b>⚡️ الأمر السريع</b> - سطر واحد سريع  
• <b>📋 المحرر النصي</b> - تنسيق مفتاح:قيمة

يمكنك الإلغاء في أي وقت باستخدام /cancel
    """
    
    await update.message.reply_html(welcome_text, reply_markup=main_creation_keyboard())
    return SELECT_METHOD

@uow_transaction
@require_active_user
@require_analyst_user
async def start_text_input_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """بدء الإدخال النصي السريع"""
    clean_creation_state(context)
    command = (update.message.text or "").lstrip('/').split()[0].lower()
    context.user_data[DRAFT_KEY] = {'input_mode': command}
    update_activity(context)
    
    if command == 'rec':
        prompt = """
⚡️ <b>وضع الأمر السريع</b>

أدخل توصيتك الكاملة في سطر واحد:

<code>الأصل الاتجاه سعر_الدخول وقف_الخسارة الهدف1@نسبة الهدف2@نسبة</code>

<b>مثال:</b>
<code>BTCUSDT LONG 59000 58000 60000@50 62000@50</code>
        """
    else:
        prompt = """
📋 <b>وضع المحرر النصي</b>

الصق توصيتك بالتنسيق التالي:

<code>الأصل: BTCUSDT
الاتجاه: LONG
سعر الدخول: 59000
وقف الخسارة: 58000
الأهداف: 60000@50 62000@50</code>
        """
    
    await update.message.reply_html(prompt)
    return AWAIT_TEXT_INPUT

# --- State Handlers ---
@uow_transaction
@require_active_user
@require_analyst_user
async def method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """معالجة اختيار طريقة الإدخال"""
    query = update.callback_query
    await query.answer()
    
    if await handle_timeout(update, context):
        return ConversationHandler.END
    
    choice = query.data.split('_')[1]
    update_activity(context)
    
    if choice == "interactive":
        trade_service = get_service(context, "trade_service", TradeService)
        recent_assets = trade_service.get_recent_assets_for_user(db_session, str(query.from_user.id))
        context.user_data[DRAFT_KEY] = {}
        
        await safe_edit_message(
            query,
            "<b>الخطوة 1/4: الأصل</b>\nاختر أو اكتب رمز الأصل (مثال: BTCUSDT).",
            reply_markup=asset_choice_keyboard(recent_assets)
        )
        return AWAITING_ASSET
        
    context.user_data[DRAFT_KEY] = {'input_mode': 'rec' if choice == 'quick' else 'editor'}
    
    if choice == "quick":
        prompt = "⚡️ أدخل توصيتك الكاملة في سطر واحد..."
    else:
        prompt = "📋 الصق توصيتك بتنسيق مفتاح:قيمة..."
        
    await safe_edit_message(query, prompt)
    return AWAIT_TEXT_INPUT

async def received_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة الإدخال النصي"""
    if await handle_timeout(update, context):
        return ConversationHandler.END
        
    update_activity(context)
    draft = context.user_data.get(DRAFT_KEY, {})
    mode = draft.get('input_mode')
    text = update.message.text
    
    data = parse_quick_command(text) if mode == 'rec' else parse_text_editor(text)
    if data:
        draft.update(data)
        await show_review_card(update, context)
        return AWAITING_REVIEW
        
    await update.message.reply_text("❌ تنسيق غير صالح. يرجى المحاولة مرة أخرى أو /cancel.")
    return AWAIT_TEXT_INPUT

async def asset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة اختيار الأصل"""
    if await handle_timeout(update, context):
        return ConversationHandler.END
        
    update_activity(context)
    draft = context.user_data[DRAFT_KEY]
    
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        asset = query.data.split("_", 1)[1]
        if asset.lower() == "new":
            await safe_edit_message(query, "✍️ الرجاء كتابة رمز الأصل الجديد.")
            return AWAITING_ASSET
    else:
        asset = (update.message.text or "").strip().upper()

    # التحقق من صحة الرمز
    market_data_service = get_service(context, "market_data_service", MarketDataService)
    if not market_data_service.is_valid_symbol(asset, draft.get("market", "Futures")):
        if update.callback_query:
            await safe_edit_message(update.callback_query, f"❌ الرمز '<b>{asset}</b>' غير صالح. يرجى المحاولة مرة أخرى.")
        else:
            await update.message.reply_html(f"❌ الرمز '<b>{asset}</b>' غير صالح. يرجى المحاولة مرة أخرى.")
        return AWAITING_ASSET

    draft['asset'] = asset
    draft['market'] = draft.get('market', 'Futures')
    
    if update.callback_query:
        await safe_edit_message(
            update.callback_query,
            f"✅ الأصل: <b>{asset}</b>\n\n<b>الخطوة 2/4: الاتجاه</b>\nاختر اتجاه التداول.",
            reply_markup=side_market_keyboard(draft['market'])
        )
    else:
        await update.message.reply_html(
            f"✅ الأصل: <b>{asset}</b>\n\n<b>الخطوة 2/4: الاتجاه</b>\nاختر اتجاه التداول.",
            reply_markup=side_market_keyboard(draft['market'])
        )
    return AWAITING_SIDE

async def side_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة اختيار الاتجاه"""
    query = update.callback_query
    await query.answer()
    
    if await handle_timeout(update, context):
        return ConversationHandler.END
        
    update_activity(context)
    draft = context.user_data[DRAFT_KEY]
    
    action = query.data.split("_")[1]
    if action in ("LONG", "SHORT"):
        draft['side'] = action
        await safe_edit_message(
            query,
            f"✅ الاتجاه: <b>{action}</b>\n\n<b>الخطوة 3/4: نوع الطلب</b>\nاختر نوع أمر الدخول.",
            reply_markup=order_type_keyboard()
        )
        return AWAITING_TYPE
    elif action == "menu":
        await query.edit_message_reply_markup(reply_markup=market_choice_keyboard())
        return AWAITING_SIDE

async def market_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة اختيار السوق"""
    query = update.callback_query
    await query.answer()
    
    if await handle_timeout(update, context):
        return ConversationHandler.END
        
    update_activity(context)
    draft = context.user_data[DRAFT_KEY]
    
    if "back" in query.data:
        await query.edit_message_reply_markup(reply_markup=side_market_keyboard(draft.get('market', 'Futures')))
        return AWAITING_SIDE
        
    market = query.data.split("_")[1]
    draft['market'] = market
    await query.edit_message_reply_markup(reply_markup=side_market_keyboard(market))
    return AWAITING_SIDE

async def type_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة اختيار نوع الطلب"""
    query = update.callback_query
    await query.answer()
    
    if await handle_timeout(update, context):
        return ConversationHandler.END
        
    update_activity(context)
    draft = context.user_data[DRAFT_KEY]
    order_type = query.data.split("_")[1]
    draft['order_type'] = order_type
    
    if order_type == 'MARKET':
        prompt = "<b>الخطوة 4/4: الأسعار</b>\nأدخل: <code>وقف الخسارة الأهداف...</code>\nمثال: <code>58000 60000@50 62000@50</code>"
    else:
        prompt = "<b>الخطوة 4/4: الأسعار</b>\nأدخل: <code>سعر الدخول وقف الخسارة الأهداف...</code>\nمثال: <code>59000 58000 60000@50 62000@50</code>"
        
    await safe_edit_message(query, f"✅ نوع الطلب: <b>{order_type}</b>\n\n{prompt}")
    return AWAITING_PRICES

async def prices_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة إدخال الأسعار"""
    if await handle_timeout(update, context):
        return ConversationHandler.END
        
    update_activity(context)
    draft = context.user_data[DRAFT_KEY]
    tokens = (update.message.text or "").strip().split()
    
    try:
        trade_service = get_service(context, "trade_service", TradeService)
        
        if draft["order_type"] == 'MARKET':
            if len(tokens) < 2: 
                raise ValueError("تنسيق السوق: وقف الخسارة ثم الأهداف...")
                
            stop_loss, targets = parse_number(tokens[0]), parse_targets_list(tokens[1:])
            price_service = get_service(context, "price_service", PriceService)
            live_price_float = await price_service.get_cached_price(draft["asset"], draft.get("market", "Futures"), True)
            
            if not live_price_float:
                raise ValueError("تعذر جلب سعر السوق الحالي.")
                
            live_price = Decimal(str(live_price_float))
            trade_service._validate_recommendation_data(draft["side"], live_price, stop_loss, targets)
            draft.update({"entry": live_price, "stop_loss": stop_loss, "targets": targets})
        else:
            if len(tokens) < 3: 
                raise ValueError("تنسيق LIMIT/STOP: سعر الدخول، وقف الخسارة، ثم الأهداف...")
                
            entry, stop_loss = parse_number(tokens[0]), parse_number(tokens[1])
            targets = parse_targets_list(tokens[2:])
            trade_service._validate_recommendation_data(draft["side"], entry, stop_loss, targets)
            draft.update({"entry": entry, "stop_loss": stop_loss, "targets": targets})
            
        if not draft.get("targets"):
            raise ValueError("لم يتم تحليل أي أهداف صالحة.")
        
        await show_review_card(update, context)
        return AWAITING_REVIEW
        
    except (ValueError, InvalidOperation, TypeError) as e:
        await update.message.reply_text(f"⚠️ {str(e)}\nيرجى المحاولة مرة أخرى.")
        return AWAITING_PRICES

async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض بطاقة المراجعة النهائية"""
    draft = context.user_data[DRAFT_KEY]
    if not draft.get("token"):
        draft["token"] = str(uuid.uuid4())[:8]
    
    price_service = get_service(context, "price_service", PriceService)
    preview_price = await price_service.get_cached_price(draft["asset"], draft.get("market", "Futures"))
    
    review_text = build_review_text_with_price(draft, preview_price)
    
    if update.callback_query:
        await safe_edit_message(update.callback_query, review_text, reply_markup=review_final_keyboard(draft["token"]))
    else:
        await update.effective_message.reply_html(review_text, reply_markup=review_final_keyboard(draft["token"]))

@uow_transaction
@require_active_user
@require_analyst_user
async def review_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالجة مراجعة التوصية"""
    query = update.callback_query
    await query.answer()
    
    if await handle_timeout(update, context):
        return ConversationHandler.END
        
    update_activity(context)
    draft = context.user_data.get(DRAFT_KEY)
    
    if not draft:
        await safe_edit_message(query, "❌ انتهت الجلسة. يرجى /newrec للبدء من جديد.")
        return ConversationHandler.END

    try:
        # استخدام CallbackBuilder للتحليل المتسق
        callback_data = CallbackBuilder.parse(query.data)
        action = callback_data.get('action')
        params = callback_data.get('params', [])
        token_in_callback = params[0] if params else None

        if not token_in_callback or draft.get('token') != token_in_callback:
            await safe_edit_message(query, "❌ جلسة منتهية الصلاحية.")
            clean_creation_state(context)
            return ConversationHandler.END

        if action == "publish":
            # النشر المباشر في القنوات النشطة
            selected_ids = context.user_data.get(CHANNEL_PICKER_KEY)
            if not selected_ids:
                user = UserRepository(db_session).find_by_telegram_id(query.from_user.id)
                all_channels = ChannelRepository(db_session).list_by_analyst(user.id, only_active=True)
                selected_ids = {ch.telegram_channel_id for ch in all_channels}
                context.user_data[CHANNEL_PICKER_KEY] = selected_ids
            
            draft['target_channel_ids'] = selected_ids
            
            trade_service = get_service(context, "trade_service", TradeService)
            rec, report = await trade_service.create_and_publish_recommendation_async(
                user_id=str(query.from_user.id), 
                db_session=db_session, 
                **draft
            )
            
            if report.get("success"):
                success_count = len(report.get('success', []))
                await safe_edit_message(
                    query,
                    f"✅ تم النشر في {success_count} قناة\n\nالتوصية #{rec.id} - {rec.asset.value}"
                )
            else:
                await safe_edit_message(query, "⚠️ تم الحفظ ولكن فشل النشر في القنوات")
                
            clean_creation_state(context)
            return ConversationHandler.END
            
        elif action == "choose_channels":
            user = UserRepository(db_session).find_by_telegram_id(query.from_user.id)
            all_channels = ChannelRepository(db_session).list_by_analyst(user.id, only_active=False)
            
            selected_ids = context.user_data.setdefault(CHANNEL_PICKER_KEY, {
                ch.telegram_channel_id for ch in all_channels if ch.is_active
            })
            
            keyboard = build_channel_picker_keyboard(draft['token'], all_channels, selected_ids)
            await safe_edit_message(
                query,
                "📢 **اختر القنوات للنشر**\n\n✅ = مختارة\n☑️ = غير مختارة\n\nاضغط على القناة لتبديل اختيارها",
                reply_markup=keyboard
            )
            return AWAITING_CHANNELS
            
        elif action == "add_notes":
            await safe_edit_message(query, "📝 **أضف ملاحظاتك**\n\nأرسل الملاحظات الإضافية لهذه التوصية:")
            return AWAITING_NOTES
            
        elif action == "cancel":
            await safe_edit_message(query, "❌ تم إلغاء العملية.")
            clean_creation_state(context)
            return ConversationHandler.END

    except Exception as e:
        log.exception("Review handler error")
        await safe_edit_message(query, f"❌ خطأ غير متوقع: {str(e)}")
        clean_creation_state(context)
        return ConversationHandler.END

async def notes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة إدخال الملاحظات"""
    if await handle_timeout(update, context):
        return ConversationHandler.END
        
    update_activity(context)
    draft = context.user_data[DRAFT_KEY]
    draft['notes'] = (update.message.text or '').strip()
    await show_review_card(update, context)
    return AWAITING_REVIEW

@uow_transaction
@require_active_user
@require_analyst_user
async def channel_picker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالجة اختيار القنوات - الإصدار المصحح"""
    query = update.callback_query
    await query.answer()
    
    if await handle_timeout(update, context):
        return ConversationHandler.END
        
    update_activity(context)
    draft = context.user_data.get(DRAFT_KEY)
    
    if not draft:
        await safe_edit_message(query, "❌ انتهت الجلسة. يرجى /newrec للبدء من جديد.")
        return ConversationHandler.END

    try:
        # استخدام CallbackBuilder للتحليل المتسق
        callback_data = CallbackBuilder.parse(query.data)
        action = callback_data.get('action')
        params = callback_data.get('params', [])
        token_in_callback = params[0] if params else None

        if not token_in_callback or draft.get('token') != token_in_callback:
            await safe_edit_message(query, "❌ جلسة منتهية. يرجى البدء من جديد.")
            clean_creation_state(context)
            return ConversationHandler.END

        if action == "back":
            await show_review_card(update, context)
            return AWAITING_REVIEW

        elif action == "confirm":
            # النشر المباشر عند التأكيد
            selected_channel_ids = context.user_data.get(CHANNEL_PICKER_KEY, set())
            draft['target_channel_ids'] = selected_channel_ids
            
            if not selected_channel_ids:
                await query.answer("❌ لم يتم اختيار أي قنوات", show_alert=True)
                return AWAITING_CHANNELS
            
            trade_service = get_service(context, "trade_service", TradeService)
            rec, report = await trade_service.create_and_publish_recommendation_async(
                user_id=str(query.from_user.id), 
                db_session=db_session, 
                **draft
            )
            
            if report.get("success"):
                success_count = len(report.get('success', []))
                await safe_edit_message(
                    query,
                    f"✅ تم النشر في {success_count} قناة\n\nالتوصية #{rec.id} - {rec.asset.value}"
                )
            else:
                await safe_edit_message(query, "❌ فشل النشر في جميع القنوات")
                
            clean_creation_state(context)
            return ConversationHandler.END

        elif action == "toggle":
            if len(params) < 3:
                await query.answer("❌ معرّف قناة غير صالح", show_alert=True)
                return AWAITING_CHANNELS
            
            channel_id = int(params[1])
            page = int(params[2]) if len(params) > 2 else 1
            
            # تبديل اختيار القناة
            selected_ids = context.user_data.get(CHANNEL_PICKER_KEY, set())
            if channel_id in selected_ids:
                selected_ids.remove(channel_id)
                await query.answer("❌ تم إزالة القناة")
            else:
                selected_ids.add(channel_id)
                await query.answer("✅ تم إضافة القناة")
            
            context.user_data[CHANNEL_PICKER_KEY] = selected_ids
            
            # تحديث الواجهة
            user = UserRepository(db_session).find_by_telegram_id(query.from_user.id)
            all_channels = ChannelRepository(db_session).list_by_analyst(user.id, only_active=False)
            keyboard = build_channel_picker_keyboard(draft['token'], all_channels, selected_ids, page=page)
            
            await query.edit_message_reply_markup(reply_markup=keyboard)
            return AWAITING_CHANNELS

    except Exception as e:
        log.error(f"Error in channel picker: {e}")
        await safe_edit_message(query, f"❌ حدث خطأ: {str(e)}")
        return AWAITING_CHANNELS

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة الإلغاء"""
    clean_creation_state(context)
    await update.message.reply_text(
        "❌ تم إلغاء العملية.\n\nيمكنك البدء من جديد باستخدام /newrec",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

def register_conversation_handlers(app: Application):
    """تسجيل معالجات المحادثة"""
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newrec", newrec_entrypoint),
            CommandHandler("rec", start_text_input_entrypoint),
            CommandHandler("editor", start_text_input_entrypoint),
        ],
        states={
            SELECT_METHOD: [CallbackQueryHandler(method_chosen, pattern="^method_")],
            AWAIT_TEXT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_text_input)],
            AWAITING_ASSET: [
                CallbackQueryHandler(asset_handler, pattern="^asset_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, asset_handler)
            ],
            AWAITING_SIDE: [
                CallbackQueryHandler(side_handler, pattern="^side_"),
                CallbackQueryHandler(market_handler, pattern="^market_")
            ],
            AWAITING_TYPE: [CallbackQueryHandler(type_handler, pattern="^type_")],
            AWAITING_PRICES: [MessageHandler(filters.TEXT & ~filters.COMMAND, prices_handler)],
            AWAITING_REVIEW: [CallbackQueryHandler(review_handler, pattern=r"^rec:")],
            AWAITING_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, notes_handler)],
            AWAITING_CHANNELS: [CallbackQueryHandler(channel_picker_handler, pattern=r"^pub:")],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
        name="recommendation_creation",
        per_user=True,
        per_chat=True,
        per_message=False,
        conversation_timeout=CONVERSATION_TIMEOUT,
    )
    app.add_handler(conv_handler)