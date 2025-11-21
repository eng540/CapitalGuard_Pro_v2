# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
# File: src/capitalguard/interfaces/telegram/management_handlers.py
# Version: v42.0.0-FORTIFIED (Production Ready)
# ✅ THE FIX:
#    1. Implemented "Defense in Depth": Try/Except blocks around Cache, DB, and View layers.
#    2. Fixed 'TypeError: coroutine not iterable' by using async wrappers.
#    3. Added Fallback Mechanism: If PortfolioViews fails, sends a basic text message.
#    4. Type Safety: Explicit checks for list types before iteration.

import logging
import asyncio
from typing import Optional, Any, Union, List, Dict
from decimal import Decimal

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    CommandHandler,
)

# --- INFRASTRUCTURE & CORE ---
from capitalguard.infrastructure.db.uow import uow_transaction
from capitalguard.infrastructure.core_engine import core_cache, cb_db, AsyncPipeline 

# --- ARCHITECTURE COMPONENTS ---
from capitalguard.interfaces.telegram.schemas import TypedCallback, ManagementAction, ManagementNamespace
from capitalguard.interfaces.telegram.session import SessionContext, KEY_AWAITING_INPUT
from capitalguard.interfaces.telegram.presenters import ManagementPresenter

# --- EXISTING IMPORTS ---
from capitalguard.interfaces.telegram.helpers import get_service, _get_attr
from capitalguard.interfaces.telegram.keyboards import (
    CallbackNamespace, CallbackAction, CallbackBuilder,
    analyst_control_panel_keyboard, build_open_recs_keyboard,
    build_user_trade_control_keyboard, build_channels_list_keyboard,
    ButtonTexts
)
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text, PortfolioViews
from capitalguard.interfaces.telegram.auth import require_active_user, require_analyst_user
from capitalguard.domain.entities import RecommendationStatus, UserType as UserTypeEntity

# --- SERVICES ---
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.performance_service import PerformanceService
from capitalguard.application.services.lifecycle_service import LifecycleService

log = logging.getLogger(__name__)

# --- Helper: Safe Message Editing ---
async def safe_edit_message(
    bot: Bot, chat_id: int, message_id: int, text: str = None, reply_markup=None, parse_mode: str = ParseMode.MARKDOWN
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
        if "message is not modified" in str(e).lower() or "not found" in str(e).lower(): return True
        return False
    except Exception as e:
        log.warning(f"Failed to edit message {chat_id}:{message_id}: {e}", exc_info=True)
        return False

# ==============================================================================
# 1. ENTRY POINT & HUB HANDLERS (CQRS + Resilience)
# ==============================================================================

class PortfolioController:
    """
    Orchestrates business logic with high resilience.
    """
    @staticmethod
    async def show_hub(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, *args):
        session = SessionContext(context)
        session.touch()

        user_id = str(db_user.id)
        tg_id = str(db_user.telegram_user_id)
        cache_key = f"portfolio_view:{user_id}"

        # --- Layer 1: Cache ---
        try:
            cached_view = await core_cache.get(cache_key)
            if cached_view:
                await PortfolioViews.render_hub(update, **cached_view)
                return
        except Exception as e:
            log.warning(f"Cache retrieval failed (proceeding to DB): {e}")

        # --- Layer 2: Data Fetching (Async Pipeline) ---
        perf_service = get_service(context, "performance_service", PerformanceService)
        trade_service = get_service(context, "trade_service", TradeService)

        # Async wrappers to ensure coroutines are created correctly
        async def fetch_report():
            return await cb_db.execute(perf_service.get_trader_performance_report, db_session, db_user.id)

        async def fetch_positions():
            return await cb_db.execute(trade_service.get_open_positions_for_user, db_session, tg_id)

        tasks = {
            "report": fetch_report,
            "positions": fetch_positions
        }

        report = {}
        items = []

        try:
            results = await AsyncPipeline.execute_parallel(tasks)
            report = results.get("report") or {}
            items = results.get("positions")
            
            # Safety check: Ensure items is a list
            if not isinstance(items, list):
                items = []
                
        except Exception as e:
            log.error(f"AsyncPipeline execution failed: {e}", exc_info=True)
            # Fallback: Try fetching sequentially if pipeline fails
            try:
                items = trade_service.get_open_positions_for_user(db_session, tg_id)
            except Exception as ex_seq:
                log.error(f"Sequential fallback failed: {ex_seq}")
                await update.effective_message.reply_text("⚠️ System is currently under heavy load. Please try again later.")
                return

        # --- Layer 3: Processing ---
        try:
            active_count = sum(1 for i in items if getattr(i, 'unified_status', None) == "ACTIVE")
            watchlist_count = sum(1 for i in items if getattr(i, 'unified_status', None) == "WATCHLIST")
            
            view_data = {
                "user_name": db_user.username,
                "report": report,
                "active_count": active_count,
                "watchlist_count": watchlist_count,
                "is_analyst": db_user.user_type == UserTypeEntity.ANALYST
            }
        except Exception as e:
            log.error(f"Data processing error: {e}", exc_info=True)
            await update.effective_message.reply_text("⚠️ Error processing portfolio data.")
            return

        # --- Layer 4: Rendering (With Fallback) ---
        try:
            await PortfolioViews.render_hub(update, **view_data)
            # Cache only on success
            await core_cache.set(cache_key, view_data, ttl=30)
        except Exception as e:
            log.error(f"Rendering failed: {e}", exc_info=True)
            # Basic Text Fallback
            fallback_text = (
                f"📊 **Portfolio Overview**\n"
                f"Active: {active_count} | Watchlist: {watchlist_count}\n"
                f"*(Detailed view unavailable)*"
            )
            await update.effective_message.reply_text(fallback_text, parse_mode=ParseMode.MARKDOWN)

        
    @staticmethod
    async def show_position(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        """
        Detailed Position View.
        """
        query = update.callback_query
        p_type = callback.get_str(0)
        p_id = callback.get_int(1)
        source_list = callback.get_str(2) or "activated"
        source_page = callback.get_int(3) or 1
        
        if not p_type or not p_id: 
            await query.answer("❌ Missing position ID.", show_alert=True)
            return

        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        user_id = str(db_user.id)
        
        try:
            # Fetch Position
            pos = await asyncio.to_thread(trade_service.get_position_details_for_user, db_session, user_id, p_type, p_id)
            
            if not pos:
                await query.answer("⚠️ Item no longer exists.", show_alert=True)
                await PortfolioController.show_hub(update, context, db_session, db_user)
                return

            # Fetch Price (Best Effort)
            try:
                lp = await price_service.get_cached_price(pos.asset.value, pos.market, force_refresh=True)
                if lp: pos.live_price = lp
            except Exception as e:
                log.warning(f"Price fetch failed for {pos.asset.value}: {e}")

            text = build_trade_card_text(pos)
            
            is_trade = getattr(pos, "is_user_trade", False)
            unified_status = getattr(pos, "unified_status", "CLOSED")
            orm_status = getattr(pos, "orm_status_value", None)
            
            back_btn = InlineKeyboardButton("⬅️ Back", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "show_list", source_list, source_page))
            
            keyboard_rows: List[List[InlineKeyboardButton]] = []
            keyboard_markup = None
            
            if unified_status in ["ACTIVE", "WATCHLIST"]:
                if is_trade:
                    keyboard_markup = build_user_trade_control_keyboard(p_id, orm_status_value=orm_status)
                else:
                    keyboard_markup = analyst_control_panel_keyboard(pos)
                    
            if keyboard_markup: 
                 keyboard_rows.extend(keyboard_markup.inline_keyboard)
                 
            keyboard_rows.append([back_btn])
            
            await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, 
                                    text=text, 
                                    reply_markup=InlineKeyboardMarkup(keyboard_rows), 
                                    parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            log.error(f"Error showing position {p_id}: {e}", exc_info=True)
            await query.answer("❌ Error loading position details.", show_alert=True)


    @staticmethod
    async def handle_edit_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        query = update.callback_query
        await query.answer()
        
        session = SessionContext(context)
        session.touch()

        rec_id = callback.get_int(0)
        
        # --- VALIDATION ---
        if callback.action == ManagementAction.EDIT_ENTRY.value:
            lifecycle_service = get_service(context, "lifecycle_service", LifecycleService)
            rec = await asyncio.to_thread(lifecycle_service.repo.get, db_session, rec_id)
            if rec and rec.status.name == RecommendationStatus.ACTIVE.name:
                await query.answer("⚠️ لا يمكن تعديل سعر الدخول للصفقات النشطة (Active).", show_alert=True)
                return
        # ------------------

        state_data = {
            "namespace": callback.namespace,
            "action": callback.action,
            "item_id": rec_id,
            "item_type": "rec",
            "original_message_chat_id": query.message.chat_id,
            "original_message_message_id": query.message.message_id,
            "previous_callback": query.data
        }
        session.set_input_state(state_data)
        
        await ManagementPresenter.render_edit_prompt(update, callback.action, rec_id)

    @staticmethod
    async def handle_cancel_input(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        query = update.callback_query
        await query.answer("Cancelled")
        
        session = SessionContext(context)
        session.clear_input_state() 
        
        rec_id = callback.get_int(0)
        
        # Return to position view
        await PortfolioController.show_position(update, context, db_session, db_user, TypedCallback("pos", "sh", ["rec", str(rec_id)]))

# ==============================================================================
# 2. ROUTER LAYER (Dispatcher)
# ==============================================================================

class ActionRouter:
    """Centralized Dispatcher with Error Boundaries."""
    
    _MGMT_ROUTES = {
        ManagementAction.HUB.value: PortfolioController.show_hub,
        ManagementAction.CANCEL_INPUT.value: PortfolioController.handle_cancel_input,
    }
    
    _POSITION_ROUTES = {
        CallbackAction.SHOW.value: PortfolioController.show_position,
    }

    _EDIT_ROUTES = {
        ManagementAction.EDIT_ENTRY.value: PortfolioController.handle_edit_selection,
        ManagementAction.EDIT_SL.value: PortfolioController.handle_edit_selection,
        ManagementAction.EDIT_TP.value: PortfolioController.handle_edit_selection,
        ManagementAction.EDIT_NOTES.value: PortfolioController.handle_edit_selection,
        ManagementAction.CLOSE_MANUAL.value: PortfolioController.handle_edit_selection,
    }

    @classmethod
    async def dispatch(cls, update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user):
        try:
            query = update.callback_query
            data = TypedCallback.parse(query.data)
            
            SessionContext(context).touch()
            
            if data.namespace == CallbackNamespace.MGMT.value and data.action in cls._MGMT_ROUTES:
                if data.action == ManagementAction.HUB.value:
                     return await cls._MGMT_ROUTES[data.action](update, context, db_session, db_user)
                return await cls._MGMT_ROUTES[data.action](update, context, db_session, db_user, data)
            
            if data.namespace == CallbackNamespace.POSITION.value and data.action in cls._POSITION_ROUTES:
                return await cls._POSITION_ROUTES[data.action](update, context, db_session, db_user, data)

            if data.namespace == CallbackNamespace.RECOMMENDATION.value and data.action in cls._EDIT_ROUTES:
                return await cls._EDIT_ROUTES[data.action](update, context, db_session, db_user, data)

            # Fallback
            await query.answer("⚠️ Action not implemented yet.", show_alert=False)
            await PortfolioController.show_hub(update, context, db_session, db_user)

        except Exception as e:
            log.error(f"Router Dispatch Error: {e}", exc_info=True)
            try:
                await update.callback_query.answer("❌ System Error", show_alert=True)
            except: pass

# ==============================================================================
# 3. HANDLERS WIRING
# ==============================================================================

@uow_transaction
@require_active_user
async def portfolio_command_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    await PortfolioController.show_hub(update, context, db_session, db_user)

@uow_transaction
@require_active_user
async def router_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs):
    await ActionRouter.dispatch(update, context, db_session, db_user)

def register_management_handlers(app: Application):
    app.add_handler(CommandHandler(["myportfolio", "open"], portfolio_command_entry))
    app.add_handler(CallbackQueryHandler(router_callback, pattern=rf"^(?:{CallbackNamespace.MGMT.value}|{CallbackNamespace.RECOMMENDATION.value}|{CallbackNamespace.POSITION.value}|{CallbackNamespace.EXIT_STRATEGY.value}):"), group=1)
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---