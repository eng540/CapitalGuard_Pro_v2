# File: src/capitalguard/interfaces/telegram/management_handlers.py
# Version: v34.1.2-R2 (PTB UI Hotfix)
# ✅ THE FIX: (Priority 1)
#    - 1. (CRITICAL) إصلاح 'InlineKeyboardMarkup' object has no attribute 'keyboard'
#       عبر تحديث الوصول إلى .keyboard_rows = keyboard_markup.inline_keyboard.
# 🎯 IMPACT: واجهات المستخدم الخاصة بلوحة التحكم (My Portfolio) تعمل الآن بشكل سليم.

import logging
import time
import math 
import asyncio 
import re 
from decimal import Decimal
from typing import Optional, Dict, Any, Union, List, Tuple

from telegram import (
    Update,
    ReplyKeyboardRemove,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Bot,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
    CommandHandler,
)
# Infrastructure & Application specific imports
from capitalguard.infrastructure.db.uow import uow_transaction
from capitalguard.interfaces.telegram.helpers import get_service, parse_cq_parts, _get_attr
from capitalguard.interfaces.telegram.keyboards import (
    analyst_control_panel_keyboard,
    build_open_recs_keyboard, 
    build_channels_list_keyboard, 
    build_user_trade_control_keyboard,
    build_close_options_keyboard,
    build_trade_data_edit_keyboard,
    build_exit_management_keyboard,
    build_partial_close_keyboard,
    CallbackAction,
    CallbackNamespace,
    CallbackBuilder,
    ButtonTexts,
    StatusIcons 
)
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text
from capitalguard.interfaces.telegram.auth import require_active_user, require_analyst_user, get_db_user
# Services
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.lifecycle_service import LifecycleService
from capitalguard.application.services.performance_service import PerformanceService
from capitalguard.domain.entities import RecommendationStatus, ExitStrategy
from capitalguard.infrastructure.db.models import UserTradeStatusEnum, RecommendationStatusEnum, UserType as UserTypeEntity
from capitalguard.infrastructure.db.repository import RecommendationRepository 

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors")

# --- Helper: Safe Message Editing & Markdown Escaping ---
def _safe_escape_markdown(text: str) -> str:
    """Escapes text for Telegram's MarkdownV2 parse mode."""
    if not isinstance(text, str):
        text = str(text)
    
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

async def safe_edit_message(
    bot: Bot, chat_id: int, message_id: int, text: str = None, reply_markup=None, parse_mode: str = ParseMode.HTML
) -> bool:
    """Edits a message safely using chat_id and message_id, handling common errors."""
    if not chat_id or not message_id:
        log.warning("safe_edit_message called without valid chat_id or message_id.")
        return False
    try:
        if text is not None:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
        elif reply_markup is not None:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
        return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            return True
        loge.warning(f"Handled BadRequest editing msg {chat_id}:{message_id}: {e}")
        return False
    except TelegramError as e:
        loge.error(f"TelegramError editing msg {chat_id}:{message_id}: {e}")
        return False
    except Exception as e:
        loge.exception(f"Unexpected error editing msg {chat_id}:{message_id}: {e}")
        return False


# --- Helper: Render Position Panel (REFACTORED for R2 Nav) ---
async def _send_or_edit_position_panel(
    update: Update, 
    context: ContextTypes.DEFAULT_TYPE, 
    db_session, 
    position_type: str, 
    position_id: int, 
    source_list: str = "activated", 
    source_page: int = 1
):
    """
    Fetches position details and renders the appropriate control panel.
    """
    query = update.callback_query
    message_target = query.message if query and query.message else update.effective_message
    if not message_target:
        log.error(f"_send_or_edit_position_panel failed for {position_type} #{position_id}: No message target found.")
        return

    chat_id = message_target.chat_id
    message_id = message_target.message_id

    try:
        trade_service = get_service(context, "trade_service", TradeService)
        user_id = str(update.effective_user.id) if update.effective_user else None
        position = trade_service.get_position_details_for_user(db_session, user_id, position_type, position_id)

        if not position:
            await safe_edit_message(context.bot, chat_id, message_id, text="❌ Position not found or has been closed.", reply_markup=None)
            return

        price_service = get_service(context, "price_service", PriceService)
        live_price = await price_service.get_cached_price(
            _get_attr(position.asset, "value"),
            _get_attr(position, "market", "Futures"),
            force_refresh=True,
        )
        if live_price is not None:
            setattr(position, "live_price", live_price)

        text = build_trade_card_text(position)
        keyboard_rows = None
        
        is_trade = getattr(position, "is_user_trade", False)
        current_status = _get_attr(position, 'status')
        status_value = current_status.value if hasattr(current_status, 'value') else str(current_status)

        back_to_list_button = InlineKeyboardButton(
            ButtonTexts.BACK_TO_LIST,
            callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "show_list", source_list, source_page)
        )

        if status_value == RecommendationStatus.ACTIVE.value:
            if is_trade:
                status_val = _get_attr(position, 'orm_status_value', UserTradeStatusEnum.CLOSED.value)
                keyboard_markup = build_user_trade_control_keyboard(position_id, orm_status_value=status_val)
                # ✅ FIX 1: Use .inline_keyboard
                keyboard_rows = keyboard_markup.inline_keyboard if keyboard_markup else []
            else:
                keyboard_markup = analyst_control_panel_keyboard(position)
                # ✅ FIX 1: Use .inline_keyboard
                keyboard_rows = keyboard_markup.inline_keyboard
            keyboard_rows.append([back_to_list_button])
            
        else:
            if is_trade:
                status_val = _get_attr(position, 'orm_status_value', UserTradeStatusEnum.CLOSED.value)
                if status_val in (UserTradeStatusEnum.PENDING_ACTIVATION.value, UserTradeStatusEnum.WATCHLIST.value):
                    keyboard_markup = build_user_trade_control_keyboard(position_id, orm_status_value=status_val)
                    # ✅ FIX 1: Use .inline_keyboard
                    keyboard_rows = keyboard_markup.inline_keyboard if keyboard_markup else []
                    keyboard_rows.append([back_to_list_button])
            
            if keyboard_rows is None:
                keyboard_rows = [[back_to_list_button]]

        safe_text = _safe_escape_markdown(text)
        await safe_edit_message(context.bot, chat_id, message_id, text=safe_text, reply_markup=InlineKeyboardMarkup(keyboard_rows), parse_mode=ParseMode.MARKDOWN_V2)

    except Exception as e:
        loge.error(f"Error rendering position panel for {position_type} #{position_id}: {e}", exc_info=True)
        await safe_edit_message(context.bot, chat_id, message_id, text=f"❌ Error loading position data: {str(e)}", reply_markup=None)


# --- Entry Point (REFACTORED for R2 Hub) ---
@uow_transaction
@require_active_user
async def management_entry_point_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """
    [R2 - REFACTORED]
    Handles /myportfolio.
    Shows the new "Integrated Hub" (التصميم 1).
    """
    try:
        # ✅ R2: Get the new PerformanceService
        performance_service = get_service(context, "performance_service", PerformanceService)
        trade_service = get_service(context, "trade_service", TradeService) # (Facade)
        user_id = db_user.id
        
        # 1. Fetch Performance Report (from Activated only)
        report = performance_service.get_trader_performance_report(db_session, user_id)
        
        # 2. Fetch Counts
        items = trade_service.get_open_positions_for_user(db_session, str(db_user.telegram_user_id))
        
        activated_count = 0
        watchlist_count = 0
        
        for item in items:
            is_trade = getattr(item, 'is_user_trade', False)
            status_value = _get_attr(item, 'orm_status_value') if is_trade else _get_attr(item, 'status')

            if status_value in (UserTradeStatusEnum.ACTIVATED.value, RecommendationStatusEnum.ACTIVE.value):
                activated_count += 1
            elif status_value in (UserTradeStatusEnum.WATCHLIST.value, UserTradeStatusEnum.PENDING_ACTIVATION.value, RecommendationStatusEnum.PENDING.value):
                watchlist_count += 1

        # 3. Build the Hub Message (Design 1)
        header = "📊 *CapitalGuard — My Portfolio*\n" \
                 "منطقة التحكم الذكية لجميع صفقاتك."
        
        stats_card = (
            "━━━━━━━━━━━━━━━━━━\n"
            "📈 *الأداء العام (Activated)*\n"
            f" • الصفقات المفعّلة: `{report.get('total_trades', '0')}`\n"
            f" • صافي PnL: `{report.get('total_pnl_pct', 'N/A')}`\n"
            f" • نسبة النجاح: `{report.get('win_rate_pct', 'N/A')}`\n" 
            f" • معامل الربح: `{report.get('profit_factor', 'N/A')}`\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "*طرق العرض:*"
        )
        
        main_message = f"{header}\n\n{stats_card}"
        
        # 4. Build Keyboard (Design 1)
        ns = CallbackNamespace.MGMT
        keyboard = [
            [InlineKeyboardButton(f"🚀 الصفقات المفعّلة ({activated_count})", callback_data=CallbackBuilder.create(ns, "show_list", "activated", 1))],
            [InlineKeyboardButton(f"👁️ صفقات المتابعة ({watchlist_count})", callback_data=CallbackBuilder.create(ns, "show_list", "watchlist", 1))],
            [InlineKeyboardButton("📡 حسب القناة", callback_data=CallbackBuilder.create(ns, "show_list", "channels", 1))],
        ]
        
        if db_user.user_type == UserTypeEntity.ANALYST:
            keyboard.append([InlineKeyboardButton("📈 لوحة المحلل*", callback_data=CallbackBuilder.create(ns, "show_list", "analyst", 1))])

        keyboard.append([InlineKeyboardButton("🔄 تحديث البيانات", callback_data=CallbackBuilder.create(ns, "hub"))])

        safe_text = _safe_escape_markdown(main_message)

        await update.message.reply_markdown_v2(safe_text, reply_markup=InlineKeyboardMarkup(keyboard))
        
    except Exception as e:
        loge.error(f"Error in management entry point: {e}", exc_info=True)
        await update.message.reply_text("❌ Error loading portfolio hub.")


@uow_transaction
@require_active_user
async def management_callback_hub_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """
    [R2 - REFACTORED]
    Handles all callbacks from the "Integrated Hub" (Design 1).
    Now routes to _render_list_view and _render_channels_list.
    """
    query = update.callback_query
    await query.answer()
    
    parsed_data = CallbackBuilder.parse(query.data)
    action = parsed_data.get("action")
    params = parsed_data.get("params", [])
    
    try:
        if action == "hub":
            # --- User clicked "🔄 تحديث البيانات" or "🏠 الرئيسية" ---
            
            performance_service = get_service(context, "performance_service", PerformanceService)
            report = performance_service.get_trader_performance_report(db_session, db_user.id)
            
            trade_service = get_service(context, "trade_service", TradeService)
            items = trade_service.get_open_positions_for_user(db_session, str(db_user.telegram_user_id))
            activated_count = 0
            watchlist_count = 0
            for item in items:
                is_trade = getattr(item, 'is_user_trade', False)
                status_value = _get_attr(item, 'orm_status_value') if is_trade else _get_attr(item, 'status')
                if status_value in (UserTradeStatusEnum.ACTIVATED.value, RecommendationStatusEnum.ACTIVE.value):
                    activated_count += 1
                elif status_value in (UserTradeStatusEnum.WATCHLIST.value, UserTradeStatusEnum.PENDING_ACTIVATION.value, RecommendationStatusEnum.PENDING.value):
                    watchlist_count += 1

            header = "📊 *CapitalGuard — My Portfolio*\n" \
                     "منطقة التحكم الذكية لجميع صفقاتك."
            stats_card = (
                "━━━━━━━━━━━━━━━━━━\n"
                "📈 *الأداء العام (Activated)*\n"
                f" • الصفقات المفعّلة: `{report.get('total_trades', '0')}`\n"
                f" • صافي PnL: `{report.get('total_pnl_pct', 'N/A')}`\n"
                f" • نسبة النجاح: `{report.get('win_rate_pct', 'N/A')}`\n"
                f" • معامل الربح: `{report.get('profit_factor', 'N/A')}`\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                "*طرق العرض:*"
            )
            main_message = f"{header}\n\n{stats_card}"
            
            ns = CallbackNamespace.MGMT
            keyboard = [
                [InlineKeyboardButton(f"🚀 الصفقات المفعّلة ({activated_count})", callback_data=CallbackBuilder.create(ns, "show_list", "activated", 1))],
                [InlineKeyboardButton(f"👁️ صفقات المتابعة ({watchlist_count})", callback_data=CallbackBuilder.create(ns, "show_list", "watchlist", 1))],
                [InlineKeyboardButton("📡 حسب القناة", callback_data=CallbackBuilder.create(ns, "show_list", "channels", 1))],
            ]
            if db_user.user_type == UserTypeEntity.ANALYST:
                keyboard.append([InlineKeyboardButton("📈 لوحة المحلل*", callback_data=CallbackBuilder.create(ns, "show_list", "analyst", 1))])
            keyboard.append([InlineKeyboardButton("🔄 تحديث البيانات", callback_data=CallbackBuilder.create(ns, "hub"))])

            safe_text = _safe_escape_markdown(main_message)
            
            await safe_edit_message(
                context.bot, query.message.chat_id, query.message.message_id,
                text=safe_text, 
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            return

        elif action == "show_list":
            list_type = params[0] if params else "activated"
            page = int(params[1]) if len(params) > 1 and params[1].isdigit() else 1
            
            if list_type in ["activated", "watchlist"]:
                await _render_list_view(update, context, db_session, db_user, list_type, page)
            
            elif list_type == "channels":
                await _render_channels_list(update, context, db_session, db_user, page)
            
            elif list_type == "analyst":
                await _render_analyst_dashboard(update, context, db_session, db_user)
            
            elif list_type.startswith("channel_detail_"):
                channel_id_str = list_type.split("_")[-1]
                channel_id: Any = int(channel_id_str) if channel_id_str.isdigit() else None
                if channel_id is None and channel_id_str == "direct":
                    channel_id = "direct"
                
                await _render_list_view(update, context, db_session, db_user, "activated", page, channel_id_filter=channel_id)

    except Exception as e:
        loge.error(f"Error in hub navigation handler: {e}", exc_info=True)
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Error loading view.")

async def _render_list_view(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, list_type: str, page: int, channel_id_filter: Optional[Union[int, str]] = None):
    """
    [R2 - REFACTORED]
    Helper function to render the "Dynamic List" view (Design 2, 4, 6).
    """
    query = update.callback_query
    price_service = get_service(context, "price_service", PriceService)
    trade_service = get_service(context, "trade_service", TradeService)
    
    items = trade_service.get_open_positions_for_user(db_session, str(db_user.telegram_user_id))
    
    header_text = ""
    filtered_items = []
    channel_title_filter = None
    
    # 1. Filter items based on list_type AND channel_id_filter
    if list_type == "activated":
        header_text = "🚀 *Activated Trades*"
        for item in items:
            is_trade = getattr(item, 'is_user_trade', False)
            if not is_trade: continue
            
            status_value = _get_attr(item, 'orm_status_value')
            if status_value == UserTradeStatusEnum.ACTIVATED.value:
                if channel_id_filter:
                    watched_channel_id = _get_attr(item, 'watched_channel_id')
                    if channel_id_filter == "direct" and watched_channel_id is None:
                        filtered_items.append(item)
                        channel_title_filter = "Direct Input"
                    elif watched_channel_id == channel_id_filter:
                        filtered_items.append(item)
                        RepoClass = context.bot_data["services"]["recommendation_repo_class"]
                        repo = RepoClass(db_session)
                        channel_obj = repo.get_watched_channel_model().get(db_session.bind, channel_id_filter)
                        channel_title_filter = channel_obj.channel_title if channel_obj else f"Channel ID {channel_id_filter}"
                else:
                    filtered_items.append(item)
                
    elif list_type == "watchlist":
        header_text = "👁️ *Watchlist Trades*"
        for item in items:
            is_trade = getattr(item, 'is_user_trade', False)
            if not is_trade: continue

            status_value = _get_attr(item, 'orm_status_value')
            if status_value in (UserTradeStatusEnum.WATCHLIST.value, UserTradeStatusEnum.PENDING_ACTIVATION.value):
                if channel_id_filter:
                    watched_channel_id = _get_attr(item, 'watched_channel_id')
                    if channel_id_filter == "direct" and watched_channel_id is None:
                        filtered_items.append(item)
                        channel_title_filter = "Direct Input"
                    elif watched_channel_id == channel_id_filter:
                        filtered_items.append(item)
                        RepoClass = context.bot_data["services"]["recommendation_repo_class"]
                        repo = RepoClass(db_session)
                        channel_obj = repo.get_watched_channel_model().get(db_session.bind, channel_id_filter)
                        channel_title_filter = channel_obj.channel_title if channel_obj else f"Channel ID {channel_id_filter}"
                else:
                    filtered_items.append(item)

    if channel_title_filter:
        header_text = f"📡 *{_safe_escape_markdown(channel_title_filter)}* | {header_text}"

    # 2. Build Keyboard 
    keyboard = await build_open_recs_keyboard(
        items_list=filtered_items,
        current_page=page,
        price_service=price_service,
        list_type=list_type
    )
    
    # 6. Edit the message
    safe_text_header = _safe_escape_markdown(header_text)
    await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id,
                            text=safe_text_header, 
                            reply_markup=keyboard,
                            parse_mode=ParseMode.MARKDOWN_V2)

async def _render_channels_list(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, page: int):
    """
    [R2 - NEW]
    Renders the list of watched channels (Design 5).
    """
    query = update.callback_query
    
    RepoClass = context.bot_data["services"]["recommendation_repo_class"]
    repo = RepoClass(db_session)
    
    channels_summary = repo.get_watched_channels_summary(db_session, db_user.id)
    
    keyboard = build_channels_list_keyboard(
        channels_summary=channels_summary,
        current_page=page,
        list_type="channels"
    )
    
    header_text = "📡 *قنواتك*\n(هذه هي القنوات التي تتابعها)"
    safe_text_header = _safe_escape_markdown(header_text)
    await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id,
                            text=safe_text_header, 
                            reply_markup=keyboard,
                            parse_mode=ParseMode.MARKDOWN_V2)

async def _render_analyst_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user):
    """
    [R2 - STUB]
    Renders the analyst dashboard (Design 7).
    """
    query = update.callback_query
    stub_text = "📈 *لوحة المحلل*\n\n(هذه الميزة قيد التطوير)"
    safe_text_header = _safe_escape_markdown(stub_text)
    await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id,
                            text=safe_text_header,
                            reply_markup=InlineKeyboardMarkup([[
                                InlineKeyboardButton("🏠 الرئيسية", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "hub"))
                            ]]),
                            parse_mode=ParseMode.MARKDOWN_V2)


@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """
    Shows the detailed control panel for a selected position (Design 3).
    """
    query = update.callback_query
    await query.answer()
    
    parsed_data = CallbackBuilder.parse(query.data)
    params = parsed_data.get("params", [])
    try:
        if len(params) >= 2:
            position_type, position_id_str = params[0], params[1]
            position_id = int(position_id_str)
            source_list = params[2] if len(params) > 2 else "activated"
            source_page = int(params[3]) if len(params) > 3 and params[3].isdigit() else 1
        else:
            raise ValueError("Insufficient parameters in callback")

        await _send_or_edit_position_panel(update, context, db_session, position_type, position_id, source_list, source_page)
    except (IndexError, ValueError, TypeError) as e:
        loge.error(f"Could not parse position info from callback: {query.data}, error: {e}")
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Invalid request data.", reply_markup=None)


@uow_transaction
@require_active_user
@require_analyst_user
async def show_submenu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """
    Displays specific submenus (Edit, Close, Partial, Exit).
    """
    query = update.callback_query
    await query.answer()
    
    parsed_data = CallbackBuilder.parse(query.data)
    namespace = parsed_data.get("namespace")
    action = parsed_data.get("action")
    params = parsed_data.get("params", [])
    rec_id = int(params[0]) if params and params[0].isdigit() else None

    if rec_id is None:
        loge.error(f"Could not get rec_id from submenu callback: {query.data}")
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Invalid request.", reply_markup=None)
        return

    trade_service = get_service(context, "trade_service", TradeService)
    position = trade_service.get_position_details_for_user(db_session, str(query.from_user.id), "rec", rec_id)
    if not position:
        await query.answer("❌ Recommendation not found or closed.", show_alert=True)
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Recommendation not found or closed.", reply_markup=None)
        return

    keyboard_rows = None
    text = build_trade_card_text(position) 

    can_modify = position.status == RecommendationStatus.ACTIVE
    
    back_button = InlineKeyboardButton(
        "⬅️ Back to Trade",
        callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id, "activated", 1)
    )

    if namespace == CallbackNamespace.RECOMMENDATION.value:
        if action == "edit_menu":
            text = "✏️ *Edit Recommendation Data*\nSelect field to edit:"
            if position.status == RecommendationStatus.ACTIVE or position.status == RecommendationStatus.PENDING:
                keyboard_markup = build_trade_data_edit_keyboard(rec_id)
                keyboard_rows = keyboard_markup.inline_keyboard
                keyboard_rows.append([back_button])
            else:
                keyboard_rows = [[back_button]]
                text = f"✏️ *Edit Recommendation Data*\n Cannot edit a recommendation with status {position.status.value}"

        elif action == "close_menu":
            text = "❌ *Close Position Fully*\nSelect closing method:"
            if can_modify:
                keyboard_markup = build_close_options_keyboard(rec_id)
                keyboard_rows = keyboard_markup.inline_keyboard
                keyboard_rows.append([back_button])
            else:
                keyboard_rows = [[back_button]]
                text = f"❌ *Close Position Fully*\n Cannot close a recommendation with status {position.status.value}"

        elif action == "partial_close_menu":
            text = "💰 *Partial Close Position*\nSelect percentage:"
            if can_modify:
                keyboard_markup = build_partial_close_keyboard(rec_id)
                keyboard_rows = keyboard_markup.inline_keyboard
                keyboard_rows.append([back_button])
            else:
                keyboard_rows = [[back_button]]
                text = f"💰 *Partial Close Position*\n Cannot partially close a recommendation with status {position.status.value}"

    elif namespace == CallbackNamespace.EXIT_STRATEGY.value:
        if action == "show_menu":
            text = "📈 *Manage Exit & Risk*\nSelect action:"
            if can_modify:
                keyboard_markup = build_exit_management_keyboard(position)
                keyboard_rows = keyboard_markup.inline_keyboard
                keyboard_rows.append([back_button])
            else:
                keyboard_rows = [[back_button]]
                text = f"📈 *Manage Exit & Risk*\n Cannot manage exit for recommendation with status {position.status.value}"

    if keyboard_rows:
        safe_text = _safe_escape_markdown(text)
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=safe_text, reply_markup=InlineKeyboardMarkup(keyboard_rows), parse_mode=ParseMode.MARKDOWN_V2)
    else:
        log.warning(f"No valid submenu keyboard for action '{action}' on rec #{rec_id} with status {position.status}")
        await _send_or_edit_position_panel(update, context, db_session, "rec", rec_id)


# --- Immediate Action Handlers (Stateless) ---
@uow_transaction
@require_active_user
async def immediate_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Handles actions executing immediately (Move BE, Cancel Strategy, Close Market)."""
    query = update.callback_query
    await query.answer("Processing...")
    
    parsed_data = CallbackBuilder.parse(query.data)
    namespace = parsed_data.get("namespace")
    action = parsed_data.get("action")
    params = parsed_data.get("params", [])
    rec_id = int(params[0]) if params and params[0].isdigit() else None

    if rec_id is None:
        loge.error(f"Could not get rec_id from immediate action callback: {query.data}")
        await query.answer("❌ Invalid request.", show_alert=True)
        return

    lifecycle_service = get_service(context, "lifecycle_service", LifecycleService)
    user_telegram_id = str(db_user.telegram_user_id)
    success_message = None
    item_type = "rec" 

    try:
        position = lifecycle_service.repo.get(db_session, rec_id)
        if not position: raise ValueError("Recommendation not found.")
        
        is_analyst_action = namespace in [CallbackNamespace.RECOMMENDATION.value, CallbackNamespace.EXIT_STRATEGY.value]
        if is_analyst_action and (not db_user or db_user.user_type != UserTypeEntity.ANALYST or position.analyst_id != db_user.id):
            raise ValueError("Access denied.")
        
        if action != "cancel" and position.status != RecommendationStatusEnum.ACTIVE:
            raise ValueError(f"Action '{action}' requires ACTIVE status.")

        # --- Execute Action ---
        if namespace == CallbackNamespace.EXIT_STRATEGY.value:
            if action == "move_to_be":
                await lifecycle_service.move_sl_to_breakeven_async(rec_id, db_session)
                success_message = "✅ SL moved to Break Even."
            elif action == "cancel":
                if position.profit_stop_active:
                    await lifecycle_service.set_exit_strategy_async(rec_id, user_telegram_id, "NONE", active=False, session=db_session)
                    success_message = "❌ Automated exit strategy cancelled."
                else:
                    success_message = "ℹ️ No active exit strategy to cancel."

        elif namespace == CallbackNamespace.RECOMMENDATION.value:
            if action == "close_market":
                price_service = get_service(context, "price_service", PriceService)
                live_price = await price_service.get_cached_price(position.asset, position.market, force_refresh=True)
                if not live_price:
                    raise ValueError(f"Could not fetch market price for {position.asset}.")
                
                await lifecycle_service.close_recommendation_async(rec_id, user_telegram_id, Decimal(str(live_price)), db_session, reason="MARKET_CLOSE_MANUAL")
                success_message = f"✅ Position closed at market price ~{_format_price(live_price)}."
        
        if success_message:
            await query.answer(success_message)
        
        await _send_or_edit_position_panel(update, context, db_session, item_type, rec_id)

    except (ValueError, Exception) as e:
        error_text = f"❌ Action Failed: {str(e)[:150]}"
        loge.error(f"Error in immediate action {namespace}:{action} for {item_type} #{rec_id}: {e}", exc_info=True)
        await query.answer(error_text, show_alert=True)
        if rec_id:
            await _send_or_edit_position_panel(update, context, db_session, item_type, rec_id)

@uow_transaction
@require_active_user
@require_analyst_user
async def partial_close_fixed_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Handles partial close buttons with fixed percentages."""
    query = update.callback_query
    await query.answer("Processing...")
    
    parsed_data = CallbackBuilder.parse(query.data) # rec:pt:<rec_id>:<percentage>
    params = parsed_data.get("params", [])
    rec_id, close_percent_str = None, None
    item_type = "rec"
    try:
        rec_id = int(params[0])
        close_percent_str = params[1]
        close_percent = Decimal(close_percent_str)
        if not (0 < close_percent <= 100): raise ValueError("Invalid percentage")
    except (ValueError, IndexError, TypeError) as e:
        loge.error(f"Could not parse partial close fixed callback: {query.data}, error: {e}")
        await query.answer("❌ Invalid request.", show_alert=True)
        return

    lifecycle_service = get_service(context, "lifecycle_service", LifecycleService)
    price_service = get_service(context, "price_service", PriceService)
    user_telegram_id = str(db_user.telegram_user_id)

    try:
        position = lifecycle_service.repo.get(db_session, rec_id)
        if not position: raise ValueError("Recommendation not found.")
        if position.analyst_id != db_user.id: raise ValueError("Access denied.")
        if position.status != RecommendationStatusEnum.ACTIVE:
            raise ValueError("Can only partially close ACTIVE positions.")

        live_price = await price_service.get_cached_price(position.asset, position.market, force_refresh=True)
        if not live_price:
            raise ValueError(f"Could not fetch market price for {position.asset}.")

        await lifecycle_service.partial_close_async(rec_id, user_telegram_id, close_percent, Decimal(str(live_price)), db_session, triggered_by="MANUAL_FIXED")
        await query.answer(f"✅ Closed {close_percent:g}% at market price ~{_format_price(live_price)}.")

        await _send_or_edit_position_panel(update, context, db_session, item_type, rec_id)
    except (ValueError, Exception) as e:
        loge.error(f"Error in partial close fixed handler for rec #{rec_id}: {e}", exc_info=True)
        await query.answer(f"❌ Partial Close Failed: {str(e)[:150]}", show_alert=True)
        await _send_or_edit_position_panel(update, context, db_session, item_type, rec_id)


# --- Registration ---
def register_management_handlers(app: Application):
    """
    [R2 - REFACTORED]
    Registers all *stateless* management handlers.
    (Stateful handlers are now in conversation_handlers.py)
    """
    # --- Entry Point Command ---
    app.add_handler(CommandHandler(["myportfolio", "open"], management_entry_point_handler))

    # --- Main Callback Handlers (Group 1 - After Conversations) ---
    
    # 1. Hub Navigation (New R2 Handler)
    app.add_handler(CallbackQueryHandler(management_callback_hub_handler, pattern=rf"^{CallbackNamespace.MGMT.value}:"), group=1)
    
    # 2. Show Position Detail
    app.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=rf"^{CallbackNamespace.POSITION.value}:{CallbackAction.SHOW.value}:"), group=1)

    # 3. Show Submenus (Analyst only)
    app.add_handler(
        CallbackQueryHandler(
            show_submenu_handler,
            pattern=rf"^(?:{CallbackNamespace.RECOMMENDATION.value}|{CallbackNamespace.EXIT_STRATEGY.value}):(?:edit_menu|close_menu|partial_close_menu|show_menu):",
        ),
        group=1,
    )
    
    # 4. Immediate Actions (Stateless)
    app.add_handler(
        CallbackQueryHandler(
            immediate_action_handler,
            pattern=rf"^(?:{CallbackNamespace.EXIT_STRATEGY.value}:(?:move_to_be|cancel):|{CallbackNamespace.RECOMMENDATION.value}:close_market)",
        ),
        group=1,
    )
    
    # 5. Partial Close Fixed Percentages (Stateless)
    app.add_handler(
        CallbackQueryHandler(partial_close_fixed_handler, pattern=rf"^{CallbackNamespace.RECOMMENDATION.value}:{CallbackAction.PARTIAL.value}:\d+:(?:25|50)$"),
        group=1,
    )
    
    # (All stateful handlers are now correctly registered in conversation_handlers.py)