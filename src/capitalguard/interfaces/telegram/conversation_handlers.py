# src/capitalguard/interfaces/telegram/conversation_handlers.py (v35.2 - Production Ready)
import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Set

from telegram import Update, ReplyKeyboardRemove
from telegram.ext import (
    Application, ContextTypes, ConversationHandler, CommandHandler,
    CallbackQueryHandler, MessageHandler, filters
)
from telegram.error import BadRequest
from telegram.constants import ParseMode

from capitalguard.infrastructure.db.uow import uow_transaction
from .helpers import get_service, parse_cq_parts
from .ui_texts import build_review_text_with_price
from .keyboards import (
    main_creation_keyboard, asset_choice_keyboard, side_market_keyboard,
    market_choice_keyboard, order_type_keyboard, review_final_keyboard,
    build_channel_picker_keyboard
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

def clean_creation_state(context: ContextTypes.DEFAULT_TYPE):
    """تنظيف حالة إنشاء التوصية بشكل كامل"""
    context.user_data.pop(DRAFT_KEY, None)
    context.user_data.pop(CHANNEL_PICKER_KEY, None)

# --- Simple Callback Parser ---
def parse_callback_data(callback_data: str) -> Dict[str, Any]:
    """تحليل بيانات الاستدعاء بشكل آمن"""
    if not callback_data or ':' not in callback_data:
        return {"action": callback_data, "params": []}
    
    parts = callback_data.split(':')
    return {
        "action": parts[1] if len(parts) > 1 else "",
        "params": parts[2:] if len(parts) > 2 else []
    }

# --- Entry Points ---
@uow_transaction
@require_active_user
@require_analyst_user
async def newrec_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """نقطة بدء إنشاء توصية جديدة"""
    clean_creation_state(context)
    await update.message.reply_html(
        "🚀 <b>توصية جديدة</b>\nاختر طريقة الإدخال:",
        reply_markup=main_creation_keyboard()
    )
    return SELECT_METHOD

@uow_transaction
@require_active_user
@require_analyst_user
async def start_text_input_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """بدء الإدخال النصي السريع"""
    clean_creation_state(context)
    command = (update.message.text or "").lstrip('/').split()[0].lower()
    context.user_data[DRAFT_KEY] = {'input_mode': command}
    
    if command == 'rec':
        prompt = "⚡️ وضع الأمر السريع\n\nأدخل توصيتك الكاملة (مثال: BTCUSDT LONG 59000 58000 60000@50 62000@50)"
    else:
        prompt = "📋 وضع المحرر النصي\n\nالصق توصيتك بتنسيق مفتاح:قيمة"
    
    await update.message.reply_text(prompt)
    return AWAIT_TEXT_INPUT

# --- State Handlers ---

@uow_transaction
@require_active_user
@require_analyst_user
async def method_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """معالجة اختيار طريقة الإدخال"""
    query = update.callback_query
    await query.answer()
    choice = query.data.split('_')[1]
    
    if choice == "interactive":
        trade_service = get_service(context, "trade_service", TradeService)
        recent_assets = trade_service.get_recent_assets_for_user(db_session, str(query.from_user.id))
        await query.edit_message_text(
            "<b>الخطوة 1/4: الأصل</b>\nاختر أو اكتب رمز الأصل (مثال: BTCUSDT).",
            reply_markup=asset_choice_keyboard(recent_assets),
            parse_mode=ParseMode.HTML,
        )
        context.user_data[DRAFT_KEY] = {}
        return AWAITING_ASSET
        
    context.user_data[DRAFT_KEY] = {'input_mode': 'rec' if choice == 'quick' else 'editor'}
    
    if choice == "quick":
        prompt = "⚡️ وضع الأمر السريع\n\nأدخل توصيتك الكاملة (مثال: BTCUSDT LONG 59000 58000 60000@50 62000@50)"
    else:
        prompt = "📋 وضع المحرر النصي\n\nالصق توصيتك بتنسيق مفتاح:قيمة"
        
    await query.edit_message_text(prompt)
    return AWAIT_TEXT_INPUT

async def received_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة الإدخال النصي"""
    draft = context.user_data.get(DRAFT_KEY, {})
    mode = draft.get('input_mode')
    text = update.message.text
    
    if mode == 'rec':
        data = parse_quick_command(text)
    else:
        data = parse_text_editor(text)
        
    if data:
        draft.update(data)
        await show_review_card(update, context)
        return AWAITING_REVIEW
        
    await update.message.reply_text("❌ تنسيق غير صالح. يرجى المحاولة مرة أخرى أو /cancel.")
    return AWAIT_TEXT_INPUT

async def asset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة اختيار الأصل"""
    draft = context.user_data[DRAFT_KEY]
    query = update.callback_query
    message = update.effective_message
    
    if query:
        await query.answer()
        asset = query.data.split("_", 1)[1]
        if asset.lower() == "new":
            await query.edit_message_text("✍️ الرجاء كتابة رمز الأصل الجديد.")
            return AWAITING_ASSET
    else:
        asset = (update.message.text or "").strip().upper()

    # التحقق من صحة الرمز
    market_data_service = get_service(context, "market_data_service", MarketDataService)
    if not market_data_service.is_valid_symbol(asset, draft.get("market", "Futures")):
        await message.reply_text(f"❌ الرمز '<b>{asset}</b>' غير صالح. يرجى المحاولة مرة أخرى.", parse_mode=ParseMode.HTML)
        return AWAITING_ASSET

    draft['asset'] = asset
    draft['market'] = draft.get('market', 'Futures')
    
    await message.reply_html(
        f"✅ الأصل: <b>{asset}</b>\n\n<b>الخطوة 2/4: الاتجاه</b>\nاختر اتجاه التداول.",
        reply_markup=side_market_keyboard(draft['market'])
    )
    return AWAITING_SIDE

async def side_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة اختيار الاتجاه"""
    query = update.callback_query
    await query.answer()
    draft = context.user_data[DRAFT_KEY]
    
    action = query.data.split("_")[1]
    if action in ("LONG", "SHORT"):
        draft['side'] = action
        await query.edit_message_text(
            f"✅ الاتجاه: <b>{action}</b>\n\n<b>الخطوة 3/4: نوع الطلب</b>\nاختر نوع أمر الدخول.",
            reply_markup=order_type_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return AWAITING_TYPE
    elif action == "menu": # تغيير السوق
        await query.edit_message_reply_markup(reply_markup=market_choice_keyboard())
        return AWAITING_SIDE

async def market_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة اختيار السوق"""
    query = update.callback_query
    await query.answer()
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
    draft = context.user_data[DRAFT_KEY]
    order_type = query.data.split("_")[1]
    draft['order_type'] = order_type
    
    if order_type == 'MARKET':
        prompt = "<b>الخطوة 4/4: الأسعار</b>\nأدخل: <code>وقف الخسارة الأهداف...</code>\nمثال: <code>58000 60000@50 62000@50</code>"
    else:
        prompt = "<b>الخطوة 4/4: الأسعار</b>\nأدخل: <code>سعر الدخول وقف الخسارة الأهداف...</code>\nمثال: <code>59000 58000 60000@50 62000@50</code>"
        
    await query.edit_message_text(f"✅ نوع الطلب: <b>{order_type}</b>\n\n{prompt}", parse_mode=ParseMode.HTML)
    return AWAITING_PRICES

async def prices_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة إدخال الأسعار"""
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
    
    await update.effective_message.reply_html(review_text, reply_markup=review_final_keyboard(draft["token"]))

@uow_transaction
@require_active_user
@require_analyst_user
async def review_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالجة مراجعة التوصية"""
    query = update.callback_query
    await query.answer()
    draft = context.user_data.get(DRAFT_KEY)
    
    callback_data = parse_callback_data(query.data)
    action = callback_data.get('action')
    token_in_callback = callback_data.get('params', [None])[0]

    if not draft or draft.get('token') != token_in_callback:
        await query.edit_message_text("❌ إجراء قديم. يرجى بدء توصية جديدة باستخدام /newrec.")
        clean_creation_state(context)
        return ConversationHandler.END

    if action == "publish":
        trade_service = get_service(context, "trade_service", TradeService)
        rec = None
        try:
            draft['target_channel_ids'] = context.user_data.get(CHANNEL_PICKER_KEY, set())
            rec, report = await trade_service.create_and_publish_recommendation_async(
                user_id=str(query.from_user.id), 
                db_session=db_session, 
                **draft
            )
            
            if report.get("success"):
                await query.edit_message_text(
                    f"✅ تم نشر التوصية #{rec.id} لـ <b>{rec.asset.value}</b>.", 
                    parse_mode=ParseMode.HTML
                )
            else:
                failed_reason = report.get('failed', [{}])[0].get('reason', 'غير معروف')
                await query.edit_message_text(
                    f"⚠️ تم حفظ التوصية #{rec.id}، لكن فشل النشر: {failed_reason}"
                )
        except Exception as e:
            log.exception("فشل معالج النشر")
            error_msg = f"❌ حدث خطأ حرج: {e}"
            if rec:
                error_msg = f"❌ فشل نشر التوصية #{rec.id}: {e}"
            await query.edit_message_text(error_msg)
        finally:
            clean_creation_state(context)
        return ConversationHandler.END
    
    elif action == "add_notes":
        await query.edit_message_text(
            f"{query.message.text}\n\n✍️ الرجاء إرسال ملاحظاتك لهذه التوصية.", 
            parse_mode=ParseMode.HTML
        )
        return AWAITING_NOTES

    elif action == "choose_channels":
        user = UserRepository(db_session).find_by_telegram_id(query.from_user.id)
        all_channels = ChannelRepository(db_session).list_by_analyst(user.id, only_active=False)
        selected_ids = context.user_data.setdefault(CHANNEL_PICKER_KEY, {
            ch.telegram_channel_id for ch in all_channels if ch.is_active
        })
        keyboard = build_channel_picker_keyboard(draft['token'], all_channels, selected_ids)
        await query.edit_message_text("📢 اختر القنوات للنشر:", reply_markup=keyboard)
        return AWAITING_CHANNELS

    elif action == "cancel":
        await query.edit_message_text("تم الإلغاء.")
        clean_creation_state(context)
        return ConversationHandler.END

async def notes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة إدخال الملاحظات"""
    draft = context.user_data[DRAFT_KEY]
    draft['notes'] = (update.message.text or '').strip()
    await show_review_card(update, context)
    return AWAITING_REVIEW

@uow_transaction
@require_active_user
@require_analyst_user
async def channel_picker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs):
    """معالجة اختيار القنوات"""
    query = update.callback_query
    await query.answer()
    draft = context.user_data.get(DRAFT_KEY)
    
    callback_data = parse_callback_data(query.data)
    action = callback_data.get('action')
    params = callback_data.get('params', [])
    token_in_callback = params[0] if params else None

    if not draft or draft.get('token') != token_in_callback:
        await query.edit_message_text("❌ إجراء قديم. يرجى بدء توصية جديدة باستخدام /newrec.")
        clean_creation_state(context)
        return ConversationHandler.END

    if action == "back":
        await show_review_card(update, context)
        return AWAITING_REVIEW

    if action == "confirm":
        await show_review_card(update, context)
        return AWAITING_REVIEW

    if action == "toggle":
        selected_ids = context.user_data.get(CHANNEL_PICKER_KEY, set())
        channel_id, page = int(params[1]), int(params[2])
        if channel_id in selected_ids: 
            selected_ids.remove(channel_id)
        else: 
            selected_ids.add(channel_id)
        context.user_data[CHANNEL_PICKER_KEY] = selected_ids
        
        user = UserRepository(db_session).find_by_telegram_id(query.from_user.id)
        all_channels = ChannelRepository(db_session).list_by_analyst(user.id, only_active=False)
        keyboard = build_channel_picker_keyboard(draft['token'], all_channels, selected_ids, page=page)
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return AWAITING_CHANNELS

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """معالجة إلغاء العملية"""
    clean_creation_state(context)
    await update.message.reply_text("تم إلغاء العملية.", reply_markup=ReplyKeyboardRemove())
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
        name="recommendation_creation_v3.5",
        per_user=True,
        per_chat=True,
        per_message=True,  # ✅ إصلاح التحذير
    )
    app.add_handler(conv_handler)