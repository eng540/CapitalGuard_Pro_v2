# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/management_handlers.py ---
# File: src/capitalguard/interfaces/telegram/management_handlers.py
# Version: v71.0.0-SAFE-SYNC (DB Crash Fix)
# ✅ THE FIX: Removed 'asyncio.to_thread' for DB calls sharing the same session.
#    - SQLAlchemy sessions are not thread-safe. Passing them to threads causes "prepared state" errors.
#    - We now run DB queries synchronously within the handler. This is safer and prevents crashes.
# 🎯 IMPACT: Fixes "InvalidRequestError: This session is in prepared state".

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
from capitalguard.infrastructure.core_engine import core_cache

# --- ARCHITECTURE COMPONENTS ---
from capitalguard.interfaces.telegram.schemas import TypedCallback, ManagementAction, ManagementNamespace
from capitalguard.interfaces.telegram.session import SessionContext, KEY_AWAITING_INPUT, KEY_PENDING_CHANGE
from capitalguard.interfaces.telegram.presenters import ManagementPresenter

from capitalguard.interfaces.telegram.helpers import get_service, _get_attr
from capitalguard.interfaces.telegram.keyboards import (
    CallbackNamespace, CallbackAction, CallbackBuilder,
    analyst_control_panel_keyboard, build_open_recs_keyboard,
    build_user_trade_control_keyboard, build_channels_list_keyboard,
    build_trade_data_edit_keyboard, build_close_options_keyboard,
    build_partial_close_keyboard, build_exit_management_keyboard,
    public_channel_keyboard,
    ButtonTexts
)
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text, PortfolioViews
from capitalguard.interfaces.telegram.auth import require_active_user, require_analyst_user
from capitalguard.domain.entities import RecommendationStatus, UserType as UserTypeEntity

from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.performance_service import PerformanceService
from capitalguard.application.services.lifecycle_service import LifecycleService

log = logging.getLogger(__name__)

async def safe_edit_message(
    bot: Bot, chat_id: int, message_id: int, text: str = None, reply_markup=None, parse_mode: str = ParseMode.MARKDOWN
) -> bool:
    if not chat_id or not message_id: return False
    try:
        if text is not None:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True)
        elif reply_markup is not None:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)
        return True
    except BadRequest as e:
        if "message is not modified" in str(e).lower(): return True
        return False
    except Exception as e:
        log.warning(f"Failed to edit message: {e}")
        return False

class PortfolioController:
    @staticmethod
    async def show_hub(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, *args):
        session = SessionContext(context)
        session.touch()
        user_id = str(db_user.id)
        tg_id = str(db_user.telegram_user_id)
        
        perf_service = get_service(context, "performance_service", PerformanceService)
        trade_service = get_service(context, "trade_service", TradeService)

        try:
            # ✅ FIX: Direct Sync Call (Safe)
            report = perf_service.get_trader_performance_report(db_session, db_user.id)
            items = trade_service.get_open_positions_for_user(db_session, tg_id)
            
            if not isinstance(items, list): items = []
            active_count = sum(1 for i in items if getattr(i, 'unified_status', None) == "ACTIVE")
            watchlist_count = sum(1 for i in items if getattr(i, 'unified_status', None) == "WATCHLIST")
            
            view_data = {
                "user_name": db_user.username, "report": report, "active_count": active_count,
                "watchlist_count": watchlist_count, "is_analyst": db_user.user_type == UserTypeEntity.ANALYST
            }
            await PortfolioViews.render_hub(update, **view_data)
        except Exception as e:
            log.error(f"Portfolio load failed: {e}", exc_info=True)
            await update.effective_message.reply_text("⚠️ Error loading portfolio.")

    @staticmethod
    async def handle_list_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        list_type = callback.get_str(0) or "activated"
        page = callback.get_int(1) or 1
        if list_type == "channels":
            await PortfolioController._render_channels_list(update, context, db_session, db_user, page)
        elif list_type == "analyst":
            await PortfolioController._render_analyst_dashboard(update, context, db_session, db_user)
        else:
            await PortfolioController._render_list_view(update, context, db_session, db_user, list_type, page, None)

    @staticmethod
    async def _render_list_view(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, list_type: str, page: int, channel_id_filter: Any = None):
        query = update.callback_query
        price_service = get_service(context, "price_service", PriceService)
        trade_service = get_service(context, "trade_service", TradeService)
        
        # ✅ FIX: Direct Sync Call
        if list_type == "history":
            items = trade_service.get_analyst_history_for_user(db_session, str(db_user.telegram_user_id))
        else:
            items = trade_service.get_open_positions_for_user(db_session, str(db_user.telegram_user_id))
        
        target_status = {"activated": "ACTIVE", "watchlist": "WATCHLIST", "history": "CLOSED"}.get(list_type, "ACTIVE")
        headers_map = {"activated": "🚀 Active", "watchlist": "👁️ Watchlist", "history": "📜 History"}
        
        filtered_items = [i for i in items if getattr(i, 'unified_status', None) == target_status]
        
        keyboard = await build_open_recs_keyboard(filtered_items, page, price_service, list_type)
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=f"📋 *{headers_map.get(list_type)}*", reply_markup=keyboard)

    @staticmethod
    async def _render_channels_list(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, page: int):
        query = update.callback_query
        trade_service = get_service(context, "trade_service", TradeService)
        # ✅ FIX: Direct Sync Call
        summary = trade_service.get_watched_channels_summary(db_session, db_user.id)
        keyboard = build_channels_list_keyboard(summary, page, "channels")
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="📡 *Channels*", reply_markup=keyboard)

    @staticmethod
    async def _render_analyst_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user):
        query = update.callback_query
        trade_service = get_service(context, "trade_service", TradeService)
        uid = str(db_user.telegram_user_id)
        # ✅ FIX: Direct Sync Calls
        active_items = trade_service.get_open_positions_for_user(db_session, uid)
        history_items = trade_service.get_analyst_history_for_user(db_session, uid)
        
        active_count = sum(1 for i in active_items if getattr(i, 'unified_status', '') == "ACTIVE")
        pending_count = sum(1 for i in active_items if getattr(i, 'unified_status', '') == "WATCHLIST")
        closed_count = len(history_items)
        
        ns = CallbackNamespace.MGMT
        keyboard = [
            [InlineKeyboardButton(f"🟢 Active ({active_count})", callback_data=CallbackBuilder.create(ns, "show_list", "activated", 1))],
            [InlineKeyboardButton(f"📜 History ({closed_count})", callback_data=CallbackBuilder.create(ns, "show_list", "history", 1))],
            [InlineKeyboardButton("🏠 Hub", callback_data=CallbackBuilder.create(ns, "hub"))]
        ]
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text="📈 *Analyst Panel*", reply_markup=InlineKeyboardMarkup(keyboard))

    @staticmethod
    async def show_position(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        query = update.callback_query
        p_type, p_id = callback.get_str(0), callback.get_int(1)
        source_list, source_page = callback.get_str(2) or "activated", callback.get_int(3) or 1
        
        trade_service = get_service(context, "trade_service", TradeService)
        price_service = get_service(context, "price_service", PriceService)
        
        try:
            # ✅ FIX: Direct Sync Call
            pos = trade_service.get_position_details_for_user(db_session, str(db_user.telegram_user_id), p_type, p_id)
            if not pos:
                await query.answer("⚠️ Item not found.", show_alert=True)
                return

            try:
                lp = await price_service.get_cached_price(pos.asset.value, pos.market, force_refresh=True)
                if lp: pos.live_price = lp
            except Exception: pass

            text = build_trade_card_text(pos)
            is_trade = getattr(pos, "is_user_trade", False)
            unified_status = getattr(pos, "unified_status", "CLOSED")
            orm_status = getattr(pos, "orm_status_value", None)
            
            back_btn = InlineKeyboardButton("⬅️ Back", callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "show_list", source_list, source_page))
            keyboard_rows = []
            
            if unified_status in ["ACTIVE", "WATCHLIST"]:
                if is_trade:
                    kb = build_user_trade_control_keyboard(p_id, orm_status_value=orm_status)
                else:
                    kb = analyst_control_panel_keyboard(pos)
                if kb: keyboard_rows.extend(kb.inline_keyboard)
            
            keyboard_rows.append([back_btn])
            await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard_rows))
        except Exception as e:
            log.error(f"Error showing position: {e}")
            await query.answer("❌ Error.", show_alert=True)

    @staticmethod
    async def show_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        query = update.callback_query
        rec_id = callback.get_int(0)
        
        trade_service = get_service(context, "trade_service", TradeService)
        # ✅ FIX: Run DB call synchronously
        position = trade_service.get_position_details_for_user(db_session, str(db_user.telegram_user_id), "rec", rec_id)
        if not position: return

        text = build_trade_card_text(position)
        kb_rows = []
        back = InlineKeyboardButton("⬅️ Back", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id, "activated", 1))

        if position.unified_status in ["ACTIVE", "WATCHLIST"]:
            if callback.namespace == CallbackNamespace.RECOMMENDATION.value:
                if callback.action == ManagementAction.EDIT_MENU.value:
                    text = "✏️ *Edit Recommendation*"
                    kb = build_trade_data_edit_keyboard(rec_id)
                    kb_rows.extend(kb.inline_keyboard)
                elif (callback.action == ManagementAction.CLOSE_MENU.value or callback.action == "close_menu") and position.unified_status == "ACTIVE":
                    text = "❌ *Close Position*"
                    kb = build_close_options_keyboard(rec_id)
                    kb_rows.extend(kb.inline_keyboard)
                elif callback.action == ManagementAction.PARTIAL_CLOSE_MENU.value and position.unified_status == "ACTIVE":
                    text = "💰 *Partial Close*"
                    kb = build_partial_close_keyboard(rec_id)
                    kb_rows.extend(kb.inline_keyboard)
            elif callback.namespace == CallbackNamespace.EXIT_STRATEGY.value and (callback.action == ManagementAction.SHOW_MENU.value or callback.action == "show_menu"):
                text = "📈 *Risk Management*"
                kb = build_exit_management_keyboard(position)
                kb_rows.extend(kb.inline_keyboard)

        kb_rows.append([back])
        await safe_edit_message(context.bot, query.message.chat_id, query.message.message_id, text=text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)

    @staticmethod
    async def handle_edit_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        query = update.callback_query
        await query.answer()
        session = SessionContext(context)
        session.touch()
        rec_id = callback.get_int(0)
        
        if callback.action == ManagementAction.EDIT_ENTRY.value:
            lifecycle_service = get_service(context, "lifecycle_service", LifecycleService)
            # ✅ FIX: Run DB call synchronously
            rec = lifecycle_service.repo.get(db_session, rec_id)
            if rec and rec.status.name == RecommendationStatus.ACTIVE.name:
                await query.answer("⚠️ لا يمكن تعديل سعر الدخول للصفقات النشطة.", show_alert=True)
                return

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
        SessionContext(context).clear_input_state() 
        rec_id = callback.get_int(0)
        await PortfolioController.show_position(update, context, db_session, db_user, TypedCallback("pos", "sh", ["rec", str(rec_id)]))

    @staticmethod
    async def handle_confirm_change(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        query = update.callback_query
        session = SessionContext(context)
        pending = context.user_data.get(KEY_PENDING_CHANGE)
        item_id = callback.get_int(2)
        
        if not pending or "value" not in pending:
            await query.answer("❌ Session expired.", show_alert=True)
            return

        value = pending["value"]
        target_action = callback.get_str(1)
        user_id = str(db_user.telegram_user_id)
        lifecycle = get_service(context, "lifecycle_service", LifecycleService)

        try:
            if target_action == "edit_tp":
                await lifecycle.update_targets_for_user_async(item_id, user_id, value, db_session)
            elif target_action == "edit_sl":
                await lifecycle.update_sl_for_user_async(item_id, user_id, value, db_session)
            elif target_action == "edit_entry":
                await lifecycle.update_entry_and_notes_async(item_id, user_id, new_entry=value, new_notes=None, db_session=db_session)
            elif target_action == "edit_notes":
                await lifecycle.update_entry_and_notes_async(item_id, user_id, new_entry=None, new_notes=value, db_session=db_session)
            elif target_action == "close_manual":
                await lifecycle.close_recommendation_async(item_id, user_id, exit_price=value, db_session=db_session, reason="MANUAL_PRICE_CLOSE")
            elif target_action == "set_fixed":
                await lifecycle.set_exit_strategy_async(item_id, user_id, mode="FIXED", price=value, active=True, session=db_session)
            elif target_action == "set_trailing":
                await lifecycle.set_exit_strategy_async(item_id, user_id, mode="TRAILING", trailing_value=value, active=True, session=db_session)
            
            await query.answer("✅ Updated successfully!")
            session.clear_all()
            await PortfolioController.show_position(update, context, db_session, db_user, TypedCallback("pos", "sh", ["rec", str(item_id)]))
            
        except Exception as e:
            log.error(f"Failed to apply change {target_action}: {e}", exc_info=True)
            await query.answer(f"❌ Error: {str(e)}", show_alert=True)

    @staticmethod
    async def handle_immediate_action(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        query = update.callback_query
        await query.answer("Processing...")
        rec_id = callback.get_int(0)
        
        lifecycle = get_service(context, "lifecycle_service", LifecycleService)
        price_service = get_service(context, "price_service", PriceService)
        user_id = str(db_user.telegram_user_id)
        
        try:
            # ✅ FIX: Run DB call synchronously
            pos = lifecycle.repo.get(db_session, rec_id)
            if not pos or pos.analyst_id != db_user.id: raise ValueError("Denied")

            msg = None
            if callback.action == ManagementAction.MOVE_TO_BE.value:
                await lifecycle.move_sl_to_breakeven_async(rec_id, db_session)
                msg = "✅ SL moved to BE"
            elif callback.action == ManagementAction.CANCEL_STRATEGY.value:
                await lifecycle.set_exit_strategy_async(rec_id, user_id, "NONE", active=False, session=db_session)
                msg = "❌ Strategy Cancelled"
            elif callback.action == ManagementAction.CLOSE_MARKET.value:
                lp = await price_service.get_cached_price(pos.asset, pos.market, True)
                await lifecycle.close_recommendation_async(rec_id, user_id, Decimal(str(lp or 0)), db_session, "MANUAL")
                msg = "✅ Closed at Market"
            
            if msg: await query.answer(msg)
            await PortfolioController.show_position(update, context, db_session, db_user, TypedCallback("pos", "sh", ["rec", str(rec_id)]))
            
        except Exception as e:
            await query.answer(f"❌ Error: {str(e)[:50]}", show_alert=True)

    @staticmethod
    async def handle_partial_close_fixed(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        query = update.callback_query
        await query.answer("Processing...")
        rec_id = callback.get_int(0)
        pct = callback.get_str(1)

        lifecycle = get_service(context, "lifecycle_service", LifecycleService)
        price_service = get_service(context, "price_service", PriceService)
        user_id = str(db_user.telegram_user_id)
        
        try:
            # ✅ FIX: Run DB call synchronously
            pos = lifecycle.repo.get(db_session, rec_id)
            if not pos or pos.analyst_id != db_user.id: raise ValueError("Denied")
            lp = await price_service.get_cached_price(pos.asset, pos.market, True)
            await lifecycle.partial_close_async(rec_id, user_id, Decimal(pct), Decimal(str(lp or 0)), db_session, "MANUAL")
            await query.answer(f"✅ Closed {pct}%")
            await PortfolioController.show_position(update, context, db_session, db_user, TypedCallback("pos", "sh", ["rec", str(rec_id)]))
        except Exception as e:
            await query.answer(f"❌ Error: {str(e)[:50]}", show_alert=True)

    @staticmethod
    async def handle_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, callback: TypedCallback):
        """Handles the Refresh button on public cards."""
        query = update.callback_query
        rec_id = callback.get_int(0)
        
        lifecycle_service = get_service(context, "lifecycle_service", LifecycleService)
        price_service = get_service(context, "price_service", PriceService)
        
        try:
            # ✅ FIX: Run DB call synchronously
            rec_orm = lifecycle_service.repo.get(db_session, rec_id)
            if not rec_orm:
                await query.answer("⚠️ Signal not found.", show_alert=True)
                return

            rec_entity = lifecycle_service.repo._to_entity(rec_orm)
            if not rec_entity:
                 await query.answer("⚠️ Error processing signal data.", show_alert=True)
                 return

            asset_val = _get_attr(rec_entity.asset, 'value')
            market_val = getattr(rec_entity, 'market', 'Futures')
            
            lp = await price_service.get_cached_price(asset_val, market_val, force_refresh=True)
            if lp: rec_entity.live_price = lp
            
            text = build_trade_card_text(rec_entity)
            keyboard = public_channel_keyboard(rec_entity.id, context.bot.username)
            
            await safe_edit_message(
                context.bot, query.message.chat_id, query.message.message_id, 
                text=text, reply_markup=keyboard, parse_mode=ParseMode.HTML
            )
            await query.answer("✅ Updated!")
            
        except Exception as e:
            log.error(f"Refresh failed: {e}", exc_info=True)
            await query.answer("❌ Update failed.", show_alert=True)

class ActionRouter:
    _MGMT_ROUTES = {
        ManagementAction.HUB.value: PortfolioController.show_hub,
        ManagementAction.SHOW_LIST.value: PortfolioController.handle_list_navigation,
        ManagementAction.CANCEL_INPUT.value: PortfolioController.handle_cancel_input,
        ManagementAction.CONFIRM_CHANGE.value: PortfolioController.handle_confirm_change,
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
        ManagementAction.SET_FIXED.value: PortfolioController.handle_edit_selection,
        ManagementAction.SET_TRAILING.value: PortfolioController.handle_edit_selection,
        ManagementAction.CLOSE_MARKET.value: PortfolioController.handle_immediate_action,
        ManagementAction.PARTIAL.value: PortfolioController.handle_partial_close_fixed,
        ManagementAction.REFRESH.value: PortfolioController.handle_refresh,
    }
    _SUBMENU_ROUTES = {
        ManagementAction.EDIT_MENU.value: PortfolioController.show_submenu,
        ManagementAction.PARTIAL_CLOSE_MENU.value: PortfolioController.show_submenu,
        ManagementAction.SHOW_MENU.value: PortfolioController.show_submenu,
        ManagementAction.CLOSE_MENU.value: PortfolioController.show_submenu,
        "close_menu": PortfolioController.show_submenu, 
        "show_menu": PortfolioController.show_submenu,
    }
    _EXIT_ROUTES = {
        ManagementAction.MOVE_TO_BE.value: PortfolioController.handle_immediate_action,
        ManagementAction.CANCEL_STRATEGY.value: PortfolioController.handle_immediate_action,
    }

    @classmethod
    async def dispatch(cls, update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user):
        try:
            query = update.callback_query
            data = TypedCallback.parse(query.data)
            SessionContext(context).touch()
            
            if data.namespace == CallbackNamespace.MGMT.value and data.action in cls._MGMT_ROUTES:
                return await cls._MGMT_ROUTES[data.action](update, context, db_session, db_user, data)
            
            if data.namespace == CallbackNamespace.POSITION.value and data.action in cls._POSITION_ROUTES:
                return await cls._POSITION_ROUTES[data.action](update, context, db_session, db_user, data)

            if data.namespace == CallbackNamespace.RECOMMENDATION.value:
                if data.action in cls._EDIT_ROUTES:
                    return await cls._EDIT_ROUTES[data.action](update, context, db_session, db_user, data)
                if data.action in cls._SUBMENU_ROUTES:
                    return await cls._SUBMENU_ROUTES[data.action](update, context, db_session, db_user, data)
            
            if data.namespace == CallbackNamespace.EXIT_STRATEGY.value:
                if data.action in cls._EXIT_ROUTES:
                    return await cls._EXIT_ROUTES[data.action](update, context, db_session, db_user, data)
                if data.action in cls._SUBMENU_ROUTES:
                    return await cls._SUBMENU_ROUTES[data.action](update, context, db_session, db_user, data)
                if data.action in cls._EDIT_ROUTES:
                    return await cls._EDIT_ROUTES[data.action](update, context, db_session, db_user, data)

            log.warning(f"Unmatched Action: ns={data.namespace}, act={data.action}")
            await query.answer("⚠️ Action not implemented yet.", show_alert=False)

        except Exception as e:
            log.error(f"Router Dispatch Error: {e}", exc_info=True)
            try: await update.callback_query.answer("❌ System Error", show_alert=True)
            except: pass

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