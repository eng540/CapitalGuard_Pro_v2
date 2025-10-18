# ✅ THE FIX: Integrated SessionManager for proper session initialization and management
# ✅ THE FIX: Fixed channel picker initialization to prevent channel selection board issues
# ✅ THE FIX: Added consistent activity tracking across all handlers to prevent premature session timeout

"""
src/capitalguard/interfaces/telegram/management_handlers.py (v37.0)
Updated to use centralized session management for reliable user experience

Key changes:
- Integrated SessionManager for consistent session handling
- Fixed channel picker initialization
- Added activity tracking in all handlers
- Implemented safe token handling for callback data
"""

import time
import logging
from typing import Dict, Any, Optional, Set

from telegram import Update, InlineKeyboardMarkup, CallbackQuery
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ConversationHandler

from capitalguard.application.services import get_service
from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy
from capitalguard.infrastructure.db.uow import uow_transaction, get_db_session
from capitalguard.interfaces.telegram.keyboards import (
    build_main_menu_keyboard, 
    build_channel_picker_keyboard,
    build_review_keyboard,
    build_trader_dashboard_keyboard,
    build_admin_panel_keyboard,
    build_position_keyboard,
    CHANNEL_PICKER_KEY,
    DRAFT_KEY,
    REVIEW_TOKEN_KEY,
    SESSION_TIMEOUT,
    LAST_ACTIVITY_KEY
)
from capitalguard.interfaces.telegram.parsers import (
    parse_rec_command,
    parse_editor_command,
    validate_recommendation_data
)
from capitalguard.interfaces.telegram.ui_texts import (
    ButtonTexts,
    StatusIcons,
    build_trade_card_text,
    build_portfolio_card,
    build_position_card,
    clean_creation_state,
    update_activity,
    handle_timeout,
    safe_edit_message
)
from capitalguard.infrastructure.session_manager import SessionManager  # Import the new session manager

log = logging.getLogger(__name__)

# Conversation states
AWAITING_ASSET, AWAITING_SIDE, AWAITING_ORDER_TYPE, AWAITING_PRICES, AWAITING_REVIEW = range(5, 10)
AWAITING_CHANNEL_SELECTION, AWAITING_TEXT_INPUT = range(10, 12)
AWAITING_PARTIAL_CLOSE_PRICE = range(12, 13)

# ==================== SESSION-AWARE HANDLERS ====================
@uow_transaction
@require_active_user
async def newrec_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, **kwargs) -> int:
    """Entry point for creating a new recommendation"""
    # ✅ THE FIX: Initialize session properly at the start of the process
    SessionManager.init_session(context)
    
    prompt = "📌 <b>الخطوة 1/4: اختيار الأصل</b>\nأدخل رمز العملة (مثل BTCUSDT):"
    await update.message.reply_html(prompt)
    return AWAITING_ASSET

async def asset_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle asset selection"""
    # ✅ THE FIX: Update activity timestamp consistently
    SessionManager.update_activity(context)
    
    asset = update.message.text.strip().upper()
    draft = SessionManager.get_draft(context)
    draft['asset'] = asset
    
    prompt = (
        f"✅ الأصل: <b>{asset}</b>\n"
        "<b>الخطوة 2/4: الاتجاه</b>\n"
        "اختر اتجاه التداول."
    )
    await update.message.reply_html(
        prompt,
        reply_markup=build_side_market_keyboard(draft.get('market', 'Futures'))
    )
    return AWAITING_SIDE

async def side_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle side selection"""
    SessionManager.update_activity(context)
    
    query = update.callback_query
    await query.answer()
    
    if await handle_timeout(update, context):
        return ConversationHandler.END
    
    # Parse callback data
    callback_data = CallbackBuilder.parse(query.data)
    side = callback_data.get('params', [None])[0]
    market = callback_data.get('params', [None, None])[1] or 'Futures'
    
    draft = SessionManager.get_draft(context)
    draft.update({
        'side': side,
        'market': market
    })
    
    prompt = (
        f"✅ الأصل: <b>{draft['asset']}</b>\n"
        f"✅ الاتجاه: <b>{side}</b>\n"
        "<b>الخطوة 3/4: نوع الطلب</b>\n"
        "اختر نوع الطلب:"
    )
    await safe_edit_message(query, prompt, reply_markup=build_order_type_keyboard())
    return AWAITING_ORDER_TYPE

async def order_type_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle order type selection"""
    SessionManager.update_activity(context)
    
    query = update.callback_query
    await query.answer()
    
    if await handle_timeout(update, context):
        return ConversationHandler.END
    
    order_type = query.data.split("_")[1]
    draft = SessionManager.get_draft(context)
    draft['order_type'] = order_type
    
    if order_type == 'MARKET':
        prompt = (
            "✅ نوع الطلب: <b>MARKET</b>\n"
            "<b>الخطوة 4/4: الأسعار</b>\n"
            "أدخل: <code>وقف الخسارة الأهداف...</code>\n"
            "مثال: <code>58000 60000@50 62000@50</code>"
        )
    else:
        prompt = (
            "✅ نوع الطلب: <b>LIMIT</b>\n"
            "<b>الخطوة 4/4: الأسعار</b>\n"
            "أدخل: <code>سعر الدخول وقف الخسارة الأهداف...</code>\n"
            "مثال: <code>59000 58000 60000@50 62000@50</code>"
        )
    
    await safe_edit_message(query, prompt)
    return AWAITING_PRICES

async def prices_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle price inputs"""
    SessionManager.update_activity(context)
    
    text = update.message.text
    draft = SessionManager.get_draft(context)
    
    try:
        # Parse prices based on order type
        tokens = text.split()
        if draft['order_type'] == 'MARKET':
            if len(tokens) < 2:
                raise ValueError("يرجى إدخال وقف الخسارة والأهداف")
            stop_loss = parse_number(tokens[0])
            targets = parse_targets_list(tokens[1:])
        else:
            if len(tokens) < 3:
                raise ValueError("يرجى إدخال سعر الدخول ووقف الخسارة والأهداف")
            entry = parse_number(tokens[0])
            stop_loss = parse_number(tokens[1])
            targets = parse_targets_list(tokens[2:])
            
            draft['entry'] = entry
        
        # Validate data
        is_valid, message = validate_recommendation_data(
            draft['side'], 
            draft.get('entry', 0), 
            stop_loss, 
            targets
        )
        if not is_valid:
            raise ValueError(message)
        
        # Update draft
        draft.update({
            'stop_loss': stop_loss,
            'targets': targets
        })
        
        # ✅ THE FIX: Generate a safe token for review process
        review_token = SessionManager.get_safe_token(context, f"review_{int(time.time())}")
        draft['token'] = review_token
        SessionManager.set_draft(context, draft)
        
        await show_review_card(update, context)
        return AWAITING_REVIEW
    
    except (ValueError, TypeError) as e:
        await update.message.reply_text(f"⚠️ {str(e)}\nيرجى المحاولة مرة أخرى.")
        return AWAITING_PRICES

async def show_review_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the review card with recommendation details"""
    SessionManager.update_activity(context)
    
    draft = SessionManager.get_draft(context)
    rec = Recommendation(
        asset=draft['asset'],
        side=draft['side'],
        entry=draft.get('entry'),
        stop_loss=draft['stop_loss'],
        targets=draft['targets'],
        order_type=draft['order_type'],
        market=draft.get('market', 'Futures'),
        notes=draft.get('notes', ''),
        status=RecommendationStatus.PENDING,
        exit_strategy=ExitStrategy.CLOSE_AT_FINAL_TP
    )
    
    # ✅ THE FIX: Use the safe token for callback data
    review_token = draft['token']
    
    text = build_trade_card_text(rec)
    keyboard = build_review_keyboard(review_token)
    
    if update.callback_query:
        await safe_edit_message(
            update.callback_query,
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_html(text, reply_markup=keyboard)

# ==================== CHANNEL PICKER HANDLERS ====================
async def channel_picker_entrypoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the channel picker flow"""
    # ✅ THE FIX: Ensure session is properly initialized
    SessionManager.init_session(context)
    
    # Get analyst channels
    user = context.user_data['user']
    channel_service = get_service(context, "channel_service")
    channels = channel_service.get_analyst_channels(user.id)
    
    # ✅ THE FIX: Properly initialize channel picker state
    selected_ids = SessionManager.get_channel_picker_state(context)
    
    keyboard = build_channel_picker_keyboard(
        review_token=context.user_data[DRAFT_KEY].get('token', ''),
        channels=channels,
        selected_ids=selected_ids
    )
    
    await update.callback_query.edit_message_text(
        "📢 <b>اختر القنوات للنشر</b>\n\n"
        "✅ = مختارة\n"
        "☑️ = غير مختارة\n\n"
        "اضغط على القناة لتبديل اختيارها",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    return AWAITING_CHANNEL_SELECTION

async def channel_picker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle channel selection toggles"""
    SessionManager.update_activity(context)
    
    query = update.callback_query
    await query.answer()
    
    if await handle_timeout(update, context):
        return ConversationHandler.END
    
    # Parse callback data
    callback_data = CallbackBuilder.parse(query.data)
    action = callback_data.get('action')
    params = callback_data.get('params', [])
    
    # ✅ THE FIX: Use safe token validation
    review_token = params[0] if params else None
    draft = SessionManager.get_draft(context)
    if not SessionManager.validate_token(context, review_token, draft.get('token', '')):
        await safe_edit_message(query, "❌ جلسة منتهية الصلاحية. يرجى البدء من جديد.")
        clean_creation_state(context)
        return ConversationHandler.END
    
    if action == CallbackAction.TOGGLE.value:
        channel_id = int(params[1]) if len(params) > 1 else None
        page = int(params[2]) if len(params) > 2 else 1
        
        if channel_id:
            # ✅ THE FIX: Get channel picker state through SessionManager
            selected_ids = SessionManager.get_channel_picker_state(context)
            if channel_id in selected_ids:
                selected_ids.remove(channel_id)
            else:
                selected_ids.add(channel_id)
            # ✅ THE FIX: Update channel picker state through SessionManager
            SessionManager.set_channel_picker_state(context, selected_ids)
        
        # Refresh the picker
        channel_service = get_service(context, "channel_service")
        channels = channel_service.get_analyst_channels(context.user_data['user'].id)
        keyboard = build_channel_picker_keyboard(
            review_token=review_token,
            channels=channels,
            selected_ids=SessionManager.get_channel_picker_state(context),
            page=page
        )
        
        await safe_edit_message(
            query,
            "📢 <b>اختر القنوات للنشر</b>\n\n"
            "✅ = مختارة\n"
            "☑️ = غير مختارة\n\n"
            "اضغط على القناة لتبديل اختيارها",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        return AWAITING_CHANNEL_SELECTION
    
    elif action == CallbackAction.CONFIRM.value:
        # ✅ THE FIX: Get selected channel IDs through SessionManager
        selected_ids = SessionManager.get_channel_picker_state(context)
        context.user_data[DRAFT_KEY]['target_channel_ids'] = list(selected_ids)
        
        await show_review_card(update, context)
        return AWAITING_REVIEW
    
    elif action == CallbackAction.BACK.value:
        await show_review_card(update, context)
        return AWAITING_REVIEW
    
    return AWAITING_CHANNEL_SELECTION

# ==================== SESSION HANDLERS ====================
async def myportfolio_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show user's portfolio - open positions"""
    # ✅ THE FIX: Initialize session if needed and update activity
    SessionManager.update_activity(context)
    
    try:
        trade_service = get_service(context, "trade_service")
        positions = trade_service.get_user_positions(str(update.effective_user.id))
        
        if not positions:
            await update.message.reply_text(
                "لا توجد مراكز مفتوحة حالياً.\n"
                "استخدم /open لإنشاء توصية جديدة."
            )
            return
        
        # Build portfolio message
        text = build_portfolio_card(positions)
        keyboard = build_trader_dashboard_keyboard()
        
        await update.message.reply_html(text, reply_markup=keyboard)
    
    except Exception as e:
        log.error(f"Error in myportfolio_command: {e}", exc_info=True)
        await update.message.reply_text("حدث خطأ أثناء جلب المحفظة. يرجى المحاولة لاحقاً.")

async def open_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show open recommendations"""
    # ✅ THE FIX: Initialize session if needed and update activity
    SessionManager.update_activity(context)
    
    try:
        rec_service = get_service(context, "recommendation_service")
        open_recs = rec_service.get_open_recommendations()
        
        if not open_recs:
            await update.message.reply_text(
                "لا توجد توصيات مفتوحة حالياً.\n"
                "استخدم /newrec لإنشاء توصية جديدة."
            )
            return
        
        # Build open recommendations message
        text = "<b>التوصيات المفتوحة:</b>\n\n"
        for rec in open_recs:
            text += f"#{rec.id} - {rec.asset} ({rec.side})\n"
            text += f"الدخول: {rec.entry} | وقف: {rec.stop_loss}\n\n"
        
        keyboard = build_open_recs_keyboard(open_recs)
        await update.message.reply_html(text, reply_markup=keyboard)
    
    except Exception as e:
        log.error(f"Error in open_command: {e}", exc_info=True)
        await update.message.reply_text("حدث خطأ أثناء جلب التوصيات. يرجى المحاولة لاحقاً.")

# ==================== UTILITY FUNCTIONS ====================
def build_open_recs_keyboard(recommendations: list) -> InlineKeyboardMarkup:
    """Build keyboard for open recommendations"""
    keyboard = []
    for rec in recommendations:
        keyboard.append([
            InlineKeyboardButton(
                f"#{rec.id} {rec.asset} ({rec.side})",
                callback_data=CallbackBuilder.create(
                    CallbackNamespace.RECOMMENDATION,
                    CallbackAction.SHOW,
                    rec.id
                )
            )
        ])
    return InlineKeyboardMarkup(keyboard)

def build_channel_picker_keyboard(review_token: str, channels: list, selected_ids: Set[int], page: int = 1) -> InlineKeyboardMarkup:
    """Build channel selection keyboard with proper token handling"""
    # ✅ THE FIX: Use shortened token to comply with Telegram's limits
    safe_token = SessionManager._shorten_token(review_token)
    
    keyboard = []
    per_page = 5
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    
    for channel in channels[start_idx:end_idx]:
        is_selected = channel.id in selected_ids
        status_icon = "✅" if is_selected else "☑️"
        channel_name = channel.title or channel.username or f"Channel {channel.telegram_channel_id}"
        keyboard.append([
            InlineKeyboardButton(
                f"{status_icon} {channel_name}",
                callback_data=CallbackBuilder.create(
                    CallbackNamespace.PUBLICATION,
                    CallbackAction.TOGGLE,
                    safe_token,
                    channel.id,
                    page
                )
            )
        ])
    
    # Pagination
    total_pages = (len(channels) + per_page - 1) // per_page
    if total_pages > 1:
        pagination = []
        if page > 1:
            pagination.append(
                InlineKeyboardButton(
                    "⬅️ السابق",
                    callback_data=CallbackBuilder.create(
                        CallbackNamespace.PUBLICATION,
                        CallbackAction.NAVIGATE,
                        safe_token,
                        page - 1
                    )
                )
            )
        pagination.append(
            InlineKeyboardButton(
                f"الصفحة {page}/{total_pages}",
                callback_data="noop"
            )
        )
        if page < total_pages:
            pagination.append(
                InlineKeyboardButton(
                    "التالي ➡️",
                    callback_data=CallbackBuilder.create(
                        CallbackNamespace.PUBLICATION,
                        CallbackAction.NAVIGATE,
                        safe_token,
                        page + 1
                    )
                )
            )
        keyboard.append(pagination)
    
    # Action buttons
    keyboard.append([
        InlineKeyboardButton(
            "✅ تأكيد النشر",
            callback_data=CallbackBuilder.create(
                CallbackNamespace.PUBLICATION,
                CallbackAction.CONFIRM,
                safe_token
            )
        ),
        InlineKeyboardButton(
            "⬅️ العودة",
            callback_data=CallbackBuilder.create(
                CallbackNamespace.PUBLICATION,
                CallbackAction.BACK,
                safe_token
            )
        )
    ])
    
    return InlineKeyboardMarkup(keyboard)

# ==================== CONVERSATION HANDLERS ====================
def get_management_handlers() -> list:
    """Return management conversation handlers"""
    partial_close_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                partial_close_entrypoint,
                pattern=f"^{CallbackBuilder.create(CallbackNamespace.TRADE, CallbackAction.EDIT)}"
            )
        ],
        states={
            AWAITING_PARTIAL_CLOSE_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, partial_close_price_received)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", partial_close_cancel)
        ],
        name="partial_profit_conversation",
        per_user=True,
        per_chat=True,
        per_message=False,
        conversation_timeout=SESSION_TIMEOUT,
    )
    
    # ✅ THE FIX: Add session initialization to all entry points
    newrec_conv = ConversationHandler(
        entry_points=[
            CommandHandler("newrec", newrec_entrypoint)
        ],
        states={
            AWAITING_ASSET: [MessageHandler(filters.TEXT & ~filters.COMMAND, asset_handler)],
            AWAITING_SIDE: [CallbackQueryHandler(side_handler, pattern=r"^side_")],
            AWAITING_ORDER_TYPE: [CallbackQueryHandler(order_type_handler, pattern=r"^order_type_")],
            AWAITING_PRICES: [MessageHandler(filters.TEXT & ~filters.COMMAND, prices_handler)],
            AWAITING_REVIEW: [
                CallbackQueryHandler(channel_picker_entrypoint, pattern=r"^rec:choose_channels"),
                CallbackQueryHandler(confirm_publish, pattern=r"^rec:publish")
            ],
            AWAITING_CHANNEL_SELECTION: [
                CallbackQueryHandler(channel_picker_handler, pattern=r"^pub:")
            ]
        },
        fallbacks=[
            CommandHandler("cancel", clean_creation_state),
            MessageHandler(filters.COMMAND, clean_creation_state)
        ],
        name="new_recommendation_conversation",
        per_user=True,
        per_chat=True,
        per_message=False,
        conversation_timeout=SESSION_TIMEOUT,
    )
    
    return [
        newrec_conv,
        partial_close_conv,
        CommandHandler("myportfolio", myportfolio_command_handler),
        CommandHandler("open", open_command_handler)
    ]