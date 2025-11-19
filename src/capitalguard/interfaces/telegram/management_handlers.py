# File: src/capitalguard/interfaces/telegram/management_handlers.py
# Version: v34.3.3-R2-FINAL (Production Stable - Tuple Crash & Safe Import Fix)
# ✅ STATUS: GOLD MASTER - CRASH FIXED
#    - Fixed AttributeError: 'tuple' object has no attribute 'append' (Ensured list initialization).
#    - Fixed NameError: PerformanceService (Explicit Import added).
#    - Pure View Logic maintained.

import logging
import re 
from typing import Optional, Any, Union, List

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Bot,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    CommandHandler,
)
from decimal import Decimal

# Infrastructure
from capitalguard.infrastructure.db.uow import uow_transaction
from capitalguard.interfaces.telegram.helpers import get_service, _get_attr
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
    ButtonTexts
)
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text
from capitalguard.interfaces.telegram.auth import require_active_user, require_analyst_user
from capitalguard.domain.entities import UserType as UserTypeEntity

# Services (All required services must be imported)
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.lifecycle_service import LifecycleService
from capitalguard.application.services.performance_service import PerformanceService # Explicitly imported

log = logging.getLogger(__name__)
loge = logging.getLogger("capitalguard.errors")

# --- Helper: Safe Message Editing ---
def _safe_escape_markdown(text: str) -> str:
    if not isinstance(text, str): text = str(text)
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

async def safe_edit_message(
    bot: Bot, chat_id: int, message_id: int, text: str = None, reply_markup=None, parse_mode: str = ParseMode.HTML
) -> bool:
    if not chat_id or not message_id: return False
    try:
        if text is not None:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup,
                parse_mode=parse_mode, disable_web_page_preview=True
            )
        elif reply_markup is not None:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
        return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower(): return True
        return True 
    except Exception as e:
        loge.warning(f"Failed to edit message {chat_id}:{message_id}: {e}", exc_info=True)
        return False

# --- Entry Point ---
@uow_transaction
@require_active_user
async def management_entry_point_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Handles /myportfolio."""
    try:
        performance_service = get_service(context, "performance_service", PerformanceService)
        report = performance_service.get_trader_performance_report(db_session, db_user.id)
        
        trade_service = get_service(context, "trade_service", TradeService)
        items = trade_service.get_open_positions_for_user(db_session, str(db_user.telegram_user_id))
        
        activated_count = sum(1 for i in items if getattr(i, 'unified_status', None) == "ACTIVE")
        watchlist_count = sum(1 for i in items if getattr(i, 'unified_status', None) == "WATCHLIST")

        header = "📊 *CapitalGuard — My Portfolio*\n" \
                 "منطقة التحكم الذكية لجميع صفقاتك."
        
        stats_card = (
            "━━━━━━━━━━━━━━━━━━\n"
            "📈 *الأداء العام (Activated)*\n"
            f" • الصفقات المفعّلة: `{report.get('total_trades', '0')}`\n"
            f" • صافي PnL: `{report.get('total_pnl_pct', 'N/A')}`\n"
            f" • نسبة النجاح: `{report.get('win_rate_pct', 'N/A')}`\n" 
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
        
        user_type_entity = UserTypeEntity(_get_attr(db_user, 'user_type', UserTypeEntity.TRADER.value))
        if user_type_entity == UserTypeEntity.ANALYST:
            keyboard.append([InlineKeyboardButton("📈 لوحة المحلل", callback_data=CallbackBuilder.create(ns, "show_list", "analyst", 1))])

        keyboard.append([InlineKeyboardButton("🔄 تحديث البيانات", callback_data=CallbackBuilder.create(ns, "hub"))])

        safe_text = _safe_escape_markdown(main_message)
        # CRITICAL FIX: Ensure to use update.effective_message for robust reply handling in case of command
        await update.effective_message.reply_markdown_v2(safe_text, reply_markup=InlineKeyboardMarkup(keyboard))
        
    except Exception as e:
        loge.error(f"Error in management entry point: {e}", exc_info=True)
        # CRITICAL FIX: Use update.effective_message for safe error response
        await update.effective_message.reply_text("❌ Error loading portfolio hub.")

@uow_transaction
@require_active_user
async def management_callback_hub_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer()
    parsed_data = CallbackBuilder.parse(query.data)
    action = parsed_data.get("action")
    params = parsed_data.get("params", [])
    
    try:
        if action == "hub":
            await management_entry_point_handler(update, context, db_session=db_session, db_user=db_user)
            return
        
        if action == "show_list":
            list_type = params[0] if params else "activated"
            page = int(params[1]) if len(params) > 1 and params[1].isdigit() else 1
            
            if list_type in ["activated", "watchlist", "history"]:
                await _render_list_view(update, context, db_session, db_user, list_type, page)
            elif list_type == "channels":
                await _render_channels_list(update, context, db_session, db_user, page)
            elif list_type == "analyst":
                await _render_analyst_dashboard(update, context, db_session, db_user)
            elif list_type.startswith("channel_detail_"):
                channel_id_str = list_type.split("_")[-1]
                channel_id = int(channel_id_str) if channel_id_str.isdigit() else (channel_id_str if channel_id_str == "direct" else None)
                await _render_list_view(update, context, db_session, db_user, "activated", page, channel_id_filter=channel_id)

    except Exception as e:
        loge.error(f"Error in hub navigation handler: {e}", exc_info=True)
        # CRITICAL FIX: Use query.message for safe editing
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Error loading view.")

async def _render_list_view(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, list_type: str, page: int, channel_id_filter: Union[int, str, None] = None):
    query = update.callback_query
    price_service = get_service(context, "price_service", PriceService)
    trade_service = get_service(context, "trade_service", TradeService)
    
    if list_type == "history":
        items = trade_service.get_analyst_history_for_user(db_session, str(db_user.telegram_user_id))
    else:
        items = trade_service.get_open_positions_for_user(db_session, str(db_user.telegram_user_id))
    
    target_status = {
        "activated": "ACTIVE", "watchlist": "WATCHLIST", "history": "CLOSED"
    }.get(list_type, "ACTIVE")

    headers_map = {
        "activated": "🚀 *Activated Trades & Signals*",
        "watchlist": "👁️ *Watchlist & Pending*",
        "history": "📜 *Analyst History (Closed)*"
    }
    header_text = headers_map.get(list_type, "📋 *Items*")

    channel_title_filter = None
    if channel_id_filter:
        if channel_id_filter == "direct":
            channel_title_filter = "Direct Input"
        else:
            info = trade_service.get_channel_info(db_session, int(channel_id_filter))
            channel_title_filter = info.get("title", f"Channel {channel_id_filter}")

    filtered_items = []
    for item in items:
        if getattr(item, 'unified_status', None) != target_status: continue
        if channel_id_filter:
            item_channel = getattr(item, 'watched_channel_id', None)
            if channel_id_filter == "direct":
                if item_channel is not None: continue
            else:
                if item_channel != channel_id_filter: continue
        filtered_items.append(item)

    if channel_title_filter:
        header_text = f"📡 *{_safe_escape_markdown(channel_title_filter)}* | {header_text}"

    keyboard = await build_open_recs_keyboard(
        items_list=filtered_items, current_page=page, price_service=price_service, list_type=list_type
    )
    
    await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id,
                            text=_safe_escape_markdown(header_text), reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2)

async def _render_channels_list(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, page: int):
    query = update.callback_query
    trade_service = get_service(context, "trade_service", TradeService)
    summary = trade_service.get_watched_channels_summary(db_session, db_user.id)
    keyboard = build_channels_list_keyboard(channels_summary=summary, current_page=page, list_type="channels")
    header_text = "📡 *قنواتك*\n(هذه هي القنوات التي تتابعها)"
    await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id,
                            text=_safe_escape_markdown(header_text), reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2)

async def _render_analyst_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user):
    query = update.callback_query
    trade_service = get_service(context, "trade_service", TradeService)
    uid = str(db_user.telegram_user_id)

    active_items = trade_service.get_open_positions_for_user(db_session, uid)
    history_items = trade_service.get_analyst_history_for_user(db_session, uid)
    
    active_count = sum(1 for i in active_items if getattr(i, 'unified_status', '') == "ACTIVE")
    pending_count = sum(1 for i in active_items if getattr(i, 'unified_status', '') == "WATCHLIST")
    closed_count = len(history_items)
    total = active_count + pending_count + closed_count

    text = (
        "📈 *Analyst Control Panel*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Analyst:* `{_safe_escape_markdown(db_user.username or 'Me')}`\n\n"
        "📊 *Signal Statistics:*\n"
        f" • Total Signals: `{total}`\n"
        f" • Active Now: `{active_count}`\n"
        f" • Pending: `{pending_count}`\n"
        f" • Archived: `{closed_count}`\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚙️ *Manage:*"
    )
    
    ns = CallbackNamespace.MGMT
    keyboard = [
        [
            InlineKeyboardButton(f"🟢 Active ({active_count})", callback_data=CallbackBuilder.create(ns, "show_list", "activated", 1)),
            InlineKeyboardButton(f"🟡 Pending ({pending_count})", callback_data=CallbackBuilder.create(ns, "show_list", "watchlist", 1))
        ],
        [InlineKeyboardButton(f"📜 History ({closed_count})", callback_data=CallbackBuilder.create(ns, "show_list", "history", 1))],
        [InlineKeyboardButton("🏠 Hub", callback_data=CallbackBuilder.create(ns, "hub"))]
    ]

    await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id,
                            text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)

async def _send_or_edit_position_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, position_type: str, position_id: int, source_list: str = "activated", source_page: int = 1):
    query = update.callback_query
    target_msg = query.message if query and query.message else update.effective_message
    if not target_msg: return

    try:
        trade_service = get_service(context, "trade_service", TradeService)
        user_id = str(update.effective_user.id) if update.effective_user else None
        position = trade_service.get_position_details_for_user(db_session, user_id, position_type, position_id)
        
        if not position:
            await safe_edit_message(context.bot, target_msg.chat.id, target_msg.message_id, text="❌ Position not found.")
            return

        price_service = get_service(context, "price_service", PriceService)
        lp = await price_service.get_cached_price(_get_attr(position.asset, "value"), _get_attr(position, "market", "Futures"), force_refresh=True)
        if lp: setattr(position, "live_price", lp)

        text = build_trade_card_text(position)
        is_trade = getattr(position, "is_user_trade", False)
        unified_status = getattr(position, "unified_status", "CLOSED")
        orm_status = getattr(position, "orm_status_value", None)

        back_btn = InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "show_list", source_list, source_page))
        
        # CRITICAL FIX: Ensure keyboard_rows is always initialized as a list
        keyboard_rows: List[List[InlineKeyboardButton]] = []
        keyboard_markup = None

        if unified_status == "ACTIVE":
            if is_trade:
                keyboard_markup = build_user_trade_control_keyboard(position_id, orm_status_value=orm_status)
            else:
                keyboard_markup = analyst_control_panel_keyboard(position)
        elif unified_status == "WATCHLIST":
             if is_trade:
                keyboard_markup = build_user_trade_control_keyboard(position_id, orm_status_value=orm_status)
             else:
                 keyboard_markup = analyst_control_panel_keyboard(position)
        
        if keyboard_markup: 
             # Ensure inline_keyboard is appended as individual lists
             keyboard_rows.extend(keyboard_markup.inline_keyboard)
             
        keyboard_rows.append([back_btn])
        
        await safe_edit_message(context.bot, target_msg.chat.id, target_msg.message_id, 
                                text=_safe_escape_markdown(text), 
                                reply_markup=InlineKeyboardMarkup(keyboard_rows), 
                                parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        loge.error(f"Error rendering panel: {e}", exc_info=True)
        await safe_edit_message(context.bot, target_msg.chat.id, target_msg.message_id, text=f"❌ Error: {str(e)}")

@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer()
    data = CallbackBuilder.parse(query.data)
    p = data.get("params", [])
    if len(p) >= 2:
        # CRITICAL FIX: The target message for safe_edit_message is resolved internally in _send_or_edit_position_panel
        await _send_or_edit_position_panel(update, context, db_session, p[0], int(p[1]), p[2] if len(p)>2 else "activated", int(p[3]) if len(p)>3 else 1)

@uow_transaction
@require_active_user
@require_analyst_user
async def show_submenu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer()
    data = CallbackBuilder.parse(query.data)
    ns = data.get("namespace")
    action = data.get("action")
    rec_id = int(data.get("params")[0])
    
    trade_service = get_service(context, "trade_service", TradeService)
    position = trade_service.get_position_details_for_user(db_session, str(query.from_user.id), "rec", rec_id)
    if not position: return

    text = build_trade_card_text(position)
    kb_rows: List[List[InlineKeyboardButton]] = [] # CRITICAL FIX: Initialize as list
    back = InlineKeyboardButton("⬅️ Back", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id, "activated", 1))

    if position.unified_status in ["ACTIVE", "WATCHLIST"]:
        if ns == CallbackNamespace.RECOMMENDATION.value:
            if action == "edit_menu":
                text = "✏️ *Edit Recommendation*"
                kb = build_trade_data_edit_keyboard(rec_id)
                kb_rows.extend(kb.inline_keyboard)
            elif action == "close_menu" and position.unified_status == "ACTIVE":
                text = "❌ *Close Position*"
                kb = build_close_options_keyboard(rec_id)
                kb_rows.extend(kb.inline_keyboard)
            elif action == "partial_close_menu" and position.unified_status == "ACTIVE":
                text = "💰 *Partial Close*"
                kb = build_partial_close_keyboard(rec_id)
                kb_rows.extend(kb.inline_keyboard)
        elif ns == CallbackNamespace.EXIT_STRATEGY.value and action == "show_menu" and position.unified_status == "ACTIVE":
            text = "📈 *Risk Management*"
            kb = build_exit_management_keyboard(position)
            kb_rows.extend(kb.inline_keyboard)

    kb_rows.append([back])
    await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=_safe_escape_markdown(text), reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN_V2)

@uow_transaction
@require_active_user
async def immediate_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer("Processing...")
    data = CallbackBuilder.parse(query.data)
    ns = data.get("namespace")
    action = data.get("action")
    rec_id = int(data.get("params")[0])

    lifecycle = get_service(context, "lifecycle_service", LifecycleService)
    msg = None

    try:
        pos = lifecycle.repo.get(db_session, rec_id)
        if not pos or pos.analyst_id != db_user.id: raise ValueError("Denied")

        if ns == CallbackNamespace.EXIT_STRATEGY.value:
            if action == "move_to_be":
                await lifecycle.move_sl_to_breakeven_async(rec_id, db_session)
                msg = "✅ SL moved to BE"
            elif action == "cancel":
                await lifecycle.set_exit_strategy_async(rec_id, str(db_user.telegram_user_id), "NONE", active=False, session=db_session)
                msg = "❌ Strategy Cancelled"
        elif ns == CallbackNamespace.RECOMMENDATION.value and action == "close_market":
             price_service = get_service(context, "price_service", PriceService)
             lp = await price_service.get_cached_price(pos.asset, pos.market, True)
             await lifecycle.close_recommendation_async(rec_id, str(db_user.telegram_user_id), Decimal(str(lp or 0)), db_session, "MANUAL")
             msg = "✅ Closed at Market"
        
        if msg: await query.answer(msg)
        await _send_or_edit_position_panel(update, context, db_session, "rec", rec_id)
    except Exception as e:
        await query.answer(f"❌ Error: {str(e)[:50]}", show_alert=True)

@uow_transaction
@require_active_user
async def partial_close_fixed_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer("Processing...")
    data = CallbackBuilder.parse(query.data)
    rec_id = int(data.get("params")[0])
    pct = data.get("params")[1]

    lifecycle = get_service(context, "lifecycle_service", LifecycleService)
    price_service = get_service(context, "price_service", PriceService)
    
    try:
        pos = lifecycle.repo.get(db_session, rec_id)
        if not pos or pos.analyst_id != db_user.id: raise ValueError("Denied")
        lp = await price_service.get_cached_price(pos.asset, pos.market, True)
        await lifecycle.partial_close_async(rec_id, str(db_user.telegram_user_id), Decimal(pct), Decimal(str(lp or 0)), db_session, "MANUAL")
        await query.answer(f"✅ Closed {pct}%")
        await _send_or_edit_position_panel(update, context, db_session, "rec", rec_id)
    except Exception as e:
        await query.answer(f"❌ Error: {str(e)[:50]}", show_alert=True)

def register_management_handlers(app: Application):
    app.add_handler(CommandHandler(["myportfolio", "open"], management_entry_point_handler))
    app.add_handler(CallbackQueryHandler(management_callback_hub_handler, pattern=rf"^{CallbackNamespace.MGMT.value}:"), group=1)
    app.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=rf"^{CallbackNamespace.POSITION.value}:{CallbackAction.SHOW.value}:"), group=1)
    app.add_handler(CallbackQueryHandler(show_submenu_handler, pattern=rf"^(?:{CallbackNamespace.RECOMMENDATION.value}|{CallbackNamespace.EXIT_STRATEGY.value}):(?:edit_menu|close_menu|partial_close_menu|show_menu):"), group=1)
    app.add_handler(CallbackQueryHandler(immediate_action_handler, pattern=rf"^(?:{CallbackNamespace.EXIT_STRATEGY.value}:(?:move_to_be|cancel):|{CallbackNamespace.RECOMMENDATION.value}:close_market)"), group=1)
    app.add_handler(CallbackQueryHandler(partial_close_fixed_handler, pattern=rf"^{CallbackNamespace.RECOMMENDATION.value}:{CallbackAction.PARTIAL.value}:\d+:(?:25|50)$"), group=1)