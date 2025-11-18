# File: src/capitalguard/interfaces/telegram/management_handlers.py
# Version: v34.2.0-R2 (R2 Completion - Analyst Dashboard Restored)
# ✅ STATUS: R2 COMPLETION CANDIDATE
#    - Full Analyst Dashboard Logic Implemented.
#    - Repository calls completely removed (Pure Service Layer interaction).
#    - Unified Status Logic fully applied.

import logging
import re 
from typing import Optional, Any, Union, List, Dict

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Bot,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    CommandHandler,
)

# Infrastructure & Application specific imports
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
# Services
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.lifecycle_service import LifecycleService
from capitalguard.application.services.performance_service import PerformanceService
from capitalguard.domain.entities import RecommendationStatus, UserType as UserTypeEntity

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
        loge.warning(f"Handled BadRequest editing msg {chat_id}:{message_id}: {e}")
        return False
    except TelegramError as e:
        loge.error(f"TelegramError editing msg {chat_id}:{message_id}: {e}")
        return False
    except Exception as e:
        loge.exception(f"Unexpected error editing msg {chat_id}:{message_id}: {e}")
        return False

# --- Entry Point ---
@uow_transaction
@require_active_user
async def management_entry_point_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    """Handles /myportfolio. Serves as the Unified Hub."""
    try:
        performance_service = get_service(context, "performance_service", PerformanceService)
        report = performance_service.get_trader_performance_report(db_session, db_user.id)
        
        trade_service = get_service(context, "trade_service", TradeService)
        items = trade_service.get_open_positions_for_user(db_session, str(db_user.telegram_user_id))
        
        activated_count = 0
        watchlist_count = 0
        
        for item in items:
            u_status = getattr(item, 'unified_status', None)
            if u_status == "ACTIVE": activated_count += 1
            elif u_status == "WATCHLIST": watchlist_count += 1

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
        
        user_type_entity = UserTypeEntity(_get_attr(db_user, 'user_type', UserTypeEntity.TRADER.value))
        if user_type_entity == UserTypeEntity.ANALYST:
            keyboard.append([InlineKeyboardButton("📈 لوحة المحلل", callback_data=CallbackBuilder.create(ns, "show_list", "analyst", 1))])

        keyboard.append([InlineKeyboardButton("🔄 تحديث البيانات", callback_data=CallbackBuilder.create(ns, "hub"))])

        safe_text = _safe_escape_markdown(main_message)
        await update.message.reply_markdown_v2(safe_text, reply_markup=InlineKeyboardMarkup(keyboard))
        
    except Exception as e:
        loge.error(f"Error in management entry point: {e}", exc_info=True)
        await update.message.reply_text("❌ Error loading portfolio hub.")

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
        elif action == "show_list":
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
                channel_id: Any = int(channel_id_str) if channel_id_str.isdigit() else None
                if channel_id is None and channel_id_str == "direct": channel_id = "direct"
                await _render_list_view(update, context, db_session, db_user, "activated", page, channel_id_filter=channel_id)
    except Exception as e:
        loge.error(f"Error in hub navigation handler: {e}", exc_info=True)
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="❌ Error loading view.")

async def _render_list_view(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, list_type: str, page: int, channel_id_filter: Optional[Union[int, str]] = None):
    """Renders lists based on unified_status."""
    query = update.callback_query
    price_service = get_service(context, "price_service", PriceService)
    trade_service = get_service(context, "trade_service", TradeService)
    
    # Fetch items based on list type
    if list_type == "history":
        # New R2 feature: Fetch history via Service
        items = trade_service.get_analyst_history_for_user(db_session, str(db_user.telegram_user_id))
    else:
        items = trade_service.get_open_positions_for_user(db_session, str(db_user.telegram_user_id))
    
    header_text = ""
    filtered_items = []
    channel_title_filter = None
    
    # Mapping list_type to unified_status
    target_status_map = {
        "activated": "ACTIVE",
        "watchlist": "WATCHLIST",
        "history": "CLOSED"
    }
    target_status = target_status_map.get(list_type, "ACTIVE")
    
    headers_map = {
        "activated": "🚀 *Activated Trades & Signals*",
        "watchlist": "👁️ *Watchlist & Pending*",
        "history": "📜 *Analyst History (Closed)*"
    }
    header_text = headers_map.get(list_type, "📋 *Items*")

    if channel_id_filter:
        if channel_id_filter == "direct":
            channel_title_filter = "Direct Input"
        else:
            channel_info = trade_service.get_channel_info(db_session, channel_id_filter)
            channel_title_filter = channel_info.get('title', f"Channel {channel_id_filter}")

    for item in items:
        if getattr(item, 'unified_status', None) != target_status: continue
        if channel_id_filter:
            item_channel_id = getattr(item, 'watched_channel_id', None)
            if channel_id_filter == "direct" and item_channel_id is None:
                filtered_items.append(item)
            elif item_channel_id == channel_id_filter:
                filtered_items.append(item)
        else:
            filtered_items.append(item)

    if channel_title_filter:
        header_text = f"📡 *{_safe_escape_markdown(channel_title_filter)}* | {header_text}"

    keyboard = await build_open_recs_keyboard(
        items_list=filtered_items,
        current_page=page,
        price_service=price_service,
        list_type=list_type
    )
    
    await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id,
                            text=_safe_escape_markdown(header_text), 
                            reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2)

async def _render_channels_list(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, page: int):
    query = update.callback_query
    trade_service = get_service(context, "trade_service", TradeService)
    channels_summary = trade_service.get_watched_channels_summary(db_session, db_user.id)
    
    keyboard = build_channels_list_keyboard(channels_summary=channels_summary, current_page=page, list_type="channels")
    header_text = "📡 *قنواتك*\n(هذه هي القنوات التي تتابعها)"
    await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id,
                            text=_safe_escape_markdown(header_text), reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2)

async def _render_analyst_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user):
    """
    ✅ R2 RESTORED: Full Analyst Dashboard Logic.
    Calculates stats from TradeService data and provides Analyst controls.
    """
    query = update.callback_query
    trade_service = get_service(context, "trade_service", TradeService)
    user_id_str = str(db_user.telegram_user_id)

    # 1. Fetch Data
    active_items = trade_service.get_open_positions_for_user(db_session, user_id_str)
    history_items = trade_service.get_analyst_history_for_user(db_session, user_id_str)
    
    # 2. Calculate Stats (Simple R2 Logic)
    total_active = 0
    total_pending = 0
    total_closed = len(history_items)
    
    for item in active_items:
        u_status = getattr(item, 'unified_status', None)
        if u_status == "ACTIVE": total_active += 1
        elif u_status == "WATCHLIST": total_pending += 1
    
    total_recs = total_active + total_pending + total_closed
    
    # 3. Build Dashboard Text
    text = (
        "📈 *Analyst Control Panel*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Analyst:* `{_safe_escape_markdown(db_user.username or 'Me')}`\n\n"
        "📊 *Signal Statistics:*\n"
        f" • Total Signals: `{total_recs}`\n"
        f" • Active Now: `{total_active}`\n"
        f" • Pending: `{total_pending}`\n"
        f" • Closed/Archived: `{total_closed}`\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚙️ *Quick Actions:*"
    )
    
    # 4. Build Analyst-Specific Keyboard
    ns = CallbackNamespace.MGMT
    keyboard = [
        [
            InlineKeyboardButton(f"🟢 Active ({total_active})", callback_data=CallbackBuilder.create(ns, "show_list", "activated", 1)),
            InlineKeyboardButton(f"🟡 Pending ({total_pending})", callback_data=CallbackBuilder.create(ns, "show_list", "watchlist", 1))
        ],
        [
            InlineKeyboardButton(f"📜 History ({total_closed})", callback_data=CallbackBuilder.create(ns, "show_list", "history", 1))
        ],
        [InlineKeyboardButton("🏠 Return to Hub", callback_data=CallbackBuilder.create(ns, "hub"))]
    ]

    await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id,
                            text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2)

async def _send_or_edit_position_panel(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, 
    position_type: str, position_id: int, source_list: str = "activated", source_page: int = 1
):
    query = update.callback_query
    message_target = query.message if query and query.message else update.effective_message
    if not message_target: return

    try:
        trade_service = get_service(context, "trade_service", TradeService)
        user_id = str(update.effective_user.id) if update.effective_user else None
        position = trade_service.get_position_details_for_user(db_session, user_id, position_type, position_id)

        if not position:
            await safe_edit_message(context.bot, message_target.chat.id, message_target.message_id, text="❌ Position not found.", reply_markup=None)
            return

        price_service = get_service(context, "price_service", PriceService)
        live_price = await price_service.get_cached_price(
            _get_attr(position.asset, "value"), _get_attr(position, "market", "Futures"), force_refresh=True
        )
        if live_price is not None: setattr(position, "live_price", live_price)

        text = build_trade_card_text(position)
        keyboard_rows = []
        
        is_trade = getattr(position, "is_user_trade", False)
        unified_status = getattr(position, "unified_status", "UNKNOWN")

        back_to_list_button = InlineKeyboardButton(
            ButtonTexts.BACK_TO_LIST,
            callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "show_list", source_list, source_page)
        )

        if unified_status == "ACTIVE":
            if is_trade:
                status_val = _get_attr(position, 'orm_status_value')
                keyboard_markup = build_user_trade_control_keyboard(position_id, orm_status_value=status_val)
                keyboard_rows = keyboard_markup.inline_keyboard if keyboard_markup else []
            else:
                keyboard_markup = analyst_control_panel_keyboard(position)
                keyboard_rows = keyboard_markup.inline_keyboard
        elif unified_status == "WATCHLIST":
             if is_trade:
                status_val = _get_attr(position, 'orm_status_value')
                keyboard_markup = build_user_trade_control_keyboard(position_id, orm_status_value=status_val)
                keyboard_rows = keyboard_markup.inline_keyboard if keyboard_markup else []
             else:
                 # Analyst Pending (Watchlist) control
                 keyboard_markup = analyst_control_panel_keyboard(position)
                 keyboard_rows = keyboard_markup.inline_keyboard
        
        keyboard_rows.append([back_to_list_button])
        
        await safe_edit_message(context.bot, message_target.chat.id, message_target.message_id, 
                                text=_safe_escape_markdown(text), 
                                reply_markup=InlineKeyboardMarkup(keyboard_rows), 
                                parse_mode=ParseMode.MARKDOWN_V2)

    except Exception as e:
        loge.error(f"Error rendering position panel: {e}", exc_info=True)
        await safe_edit_message(context.bot, message_target.chat.id, message_target.message_id, text=f"❌ Error: {str(e)}", reply_markup=None)

@uow_transaction
@require_active_user
async def show_position_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer()
    parsed_data = CallbackBuilder.parse(query.data)
    params = parsed_data.get("params", [])
    if len(params) >= 2:
        await _send_or_edit_position_panel(update, context, db_session, params[0], int(params[1]), 
                                           params[2] if len(params)>2 else "activated", 
                                           int(params[3]) if len(params)>3 else 1)

@uow_transaction
@require_active_user
@require_analyst_user
async def show_submenu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer()
    parsed_data = CallbackBuilder.parse(query.data)
    namespace = parsed_data.get("namespace")
    action = parsed_data.get("action")
    params = parsed_data.get("params", [])
    rec_id = int(params[0]) if params else None
    if not rec_id: return

    trade_service = get_service(context, "trade_service", TradeService)
    position = trade_service.get_position_details_for_user(db_session, str(query.from_user.id), "rec", rec_id)
    
    if not position:
        await safe_edit_message(context.bot, query.message.chat.id, query.message.message_id, text="❌ Position not found.")
        return

    keyboard_rows = []
    text = build_trade_card_text(position)
    back_button = InlineKeyboardButton("⬅️ Back", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id, "activated", 1))

    if namespace == CallbackNamespace.RECOMMENDATION.value:
        if action == "edit_menu":
            text = "✏️ *Edit Recommendation*"
            if position.unified_status in ["ACTIVE", "WATCHLIST"]:
                kb = build_trade_data_edit_keyboard(rec_id)
                keyboard_rows.extend(kb.inline_keyboard)
        elif action == "close_menu":
            text = "❌ *Close Position*"
            if position.unified_status == "ACTIVE":
                kb = build_close_options_keyboard(rec_id)
                keyboard_rows.extend(kb.inline_keyboard)
        elif action == "partial_close_menu":
            text = "💰 *Partial Close*"
            if position.unified_status == "ACTIVE":
                kb = build_partial_close_keyboard(rec_id)
                keyboard_rows.extend(kb.inline_keyboard)
    elif namespace == CallbackNamespace.EXIT_STRATEGY.value:
        if action == "show_menu":
            text = "📈 *Risk Management*"
            if position.unified_status == "ACTIVE":
                kb = build_exit_management_keyboard(position)
                keyboard_rows.extend(kb.inline_keyboard)

    keyboard_rows.append([back_button])
    await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=_safe_escape_markdown(text), reply_markup=InlineKeyboardMarkup(keyboard_rows), parse_mode=ParseMode.MARKDOWN_V2)

# --- Immediate & Partial Handlers (Stateless) ---
@uow_transaction
@require_active_user
async def immediate_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer("Processing...")
    parsed_data = CallbackBuilder.parse(query.data)
    namespace = parsed_data.get("namespace")
    action = parsed_data.get("action")
    params = parsed_data.get("params", [])
    rec_id = int(params[0]) if params else None
    if not rec_id: return

    lifecycle = get_service(context, "lifecycle_service", LifecycleService)
    user_id = str(db_user.telegram_user_id)
    success_msg = None

    try:
        # Check ownership via repo
        pos = lifecycle.repo.get(db_session, rec_id)
        if not pos or pos.analyst_id != db_user.id: raise ValueError("Access Denied")

        if namespace == CallbackNamespace.EXIT_STRATEGY.value:
            if action == "move_to_be":
                await lifecycle.move_sl_to_breakeven_async(rec_id, db_session)
                success_msg = "✅ SL moved to Break Even"
            elif action == "cancel":
                await lifecycle.set_exit_strategy_async(rec_id, user_id, "NONE", active=False, session=db_session)
                success_msg = "❌ Strategy Cancelled"
        elif namespace == CallbackNamespace.RECOMMENDATION.value:
             if action == "close_market":
                 price_service = get_service(context, "price_service", PriceService)
                 lp = await price_service.get_cached_price(pos.asset, pos.market, True)
                 if not lp: raise ValueError("No Price")
                 await lifecycle.close_recommendation_async(rec_id, user_id, Decimal(str(lp)), db_session, "MANUAL")
                 success_msg = "✅ Closed at Market"
        
        if success_msg: await query.answer(success_msg)
        await _send_or_edit_position_panel(update, context, db_session, "rec", rec_id)

    except Exception as e:
        await query.answer(f"❌ Error: {str(e)[:100]}", show_alert=True)
        await _send_or_edit_position_panel(update, context, db_session, "rec", rec_id)

@uow_transaction
@require_active_user
async def partial_close_fixed_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    query = update.callback_query
    await query.answer("Processing...")
    parsed_data = CallbackBuilder.parse(query.data)
    rec_id = int(parsed_data.get("params")[0])
    pct = Decimal(parsed_data.get("params")[1])
    
    lifecycle = get_service(context, "lifecycle_service", LifecycleService)
    price_service = get_service(context, "price_service", PriceService)
    
    try:
        pos = lifecycle.repo.get(db_session, rec_id)
        if not pos or pos.analyst_id != db_user.id: raise ValueError("Access Denied")
        lp = await price_service.get_cached_price(pos.asset, pos.market, True)
        
        await lifecycle.partial_close_async(rec_id, str(db_user.telegram_user_id), pct, Decimal(str(lp)), db_session, "MANUAL")
        await query.answer(f"✅ Closed {pct}%")
        await _send_or_edit_position_panel(update, context, db_session, "rec", rec_id)
    except Exception as e:
        await query.answer(f"❌ Error: {str(e)[:100]}", show_alert=True)

def register_management_handlers(app: Application):
    app.add_handler(CommandHandler(["myportfolio", "open"], management_entry_point_handler))
    app.add_handler(CallbackQueryHandler(management_callback_hub_handler, pattern=rf"^{CallbackNamespace.MGMT.value}:"), group=1)
    app.add_handler(CallbackQueryHandler(show_position_panel_handler, pattern=rf"^{CallbackNamespace.POSITION.value}:{CallbackAction.SHOW.value}:"), group=1)
    app.add_handler(CallbackQueryHandler(show_submenu_handler, pattern=rf"^(?:{CallbackNamespace.RECOMMENDATION.value}|{CallbackNamespace.EXIT_STRATEGY.value}):(?:edit_menu|close_menu|partial_close_menu|show_menu):"), group=1)
    app.add_handler(CallbackQueryHandler(immediate_action_handler, pattern=rf"^(?:{CallbackNamespace.EXIT_STRATEGY.value}:(?:move_to_be|cancel):|{CallbackNamespace.RECOMMENDATION.value}:close_market)"), group=1)
    app.add_handler(CallbackQueryHandler(partial_close_fixed_handler, pattern=rf"^{CallbackNamespace.RECOMMENDATION.value}:{CallbackAction.PARTIAL.value}:\d+:(?:25|50)$"), group=1)