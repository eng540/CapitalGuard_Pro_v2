# src/capitalguard/interfaces/telegram/management_handlers.py (v28.2 - FINAL STABLE)
"""
Management handlers with complete callback data system integration.
✅ Fixed portfolio navigation
✅ Stable position details handling
✅ Full compatibility with new keyboard system
"""

import logging
from typing import List, Optional

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from capitalguard.infrastructure.db.uow import session_scope
from .helpers import get_service
from .keyboards import (
    build_open_recs_keyboard,
    analyst_control_panel_keyboard,
    build_user_trade_control_keyboard,
    CallbackBuilder,
    CallbackNamespace,
    CallbackAction
)
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService

logger = logging.getLogger(__name__)

async def open_positions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالجة عرض الصفقات المفتوحة"""
    try:
        with session_scope() as db_session:
            trade_service = get_service(context, "trade_service", TradeService)
            user_telegram_id = str(update.effective_user.id)
            
            open_positions = trade_service.get_open_positions_for_user(db_session, user_telegram_id)
            
            if not open_positions:
                await update.message.reply_text("📭 No open positions found.")
                return
            
            # استخدام الصفحة الأولى
            keyboard = await build_open_recs_keyboard(
                open_positions, 
                1,  # الصفحة الأولى
                get_service(context, "price_service", PriceService)
            )
            
            await update.message.reply_text(
                "📊 Your Open Positions\nSelect one to manage:",
                reply_markup=keyboard
            )
            
    except Exception as e:
        logger.exception(f"Error in open_positions_handler: {e}")
        await update.message.reply_text("❌ Error loading open positions.")

async def position_details_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالجة تفاصيل الموضع"""
    try:
        query = update.callback_query
        await query.answer()
        
        # 🔧 إصلاح: تحليل callback_data الجديدة
        parsed = CallbackBuilder.parse(query.data)
        
        namespace = parsed.get('namespace')
        action = parsed.get('action') 
        params = parsed.get('params', [])
        
        if namespace != "pos" or action != "sh" or len(params) < 2:
            await query.message.reply_text("❌ Invalid position selection.")
            return
            
        position_type = params[0]  # 'rec' أو 'trade'
        position_id = int(params[1])
        
        with session_scope() as db_session:
            trade_service = get_service(context, "trade_service", TradeService)
            user_telegram_id = str(update.effective_user.id)
            
            position = trade_service.get_position_details_for_user(
                db_session, user_telegram_id, position_type, position_id
            )
            
            if not position:
                await query.message.reply_text("❌ Position not found or access denied.")
                return
            
            if position_type == 'rec':
                # عرض لوحة تحكم المحلل
                keyboard = analyst_control_panel_keyboard(position)
                
                # بناء نص التوصية
                price_service = get_service(context, "price_service", PriceService)
                current_price = await price_service.get_cached_price(position.asset.value, getattr(position, 'market', 'Futures'))
                
                message_text = f"📊 Recommendation #{position.id}\n"
                message_text += f"Asset: {position.asset.value} | Side: {position.side.value}\n"
                message_text += f"Entry: {position.entry.value} | Current: {current_price or 'N/A'}\n"
                message_text += f"Stop Loss: {position.stop_loss.value} | Status: {position.status.value}"
                
                await query.message.edit_text(message_text, reply_markup=keyboard)
                
            else:  # trade
                keyboard = build_user_trade_control_keyboard(position_id)
                
                message_text = f"💼 Your Trade #{position_id}\n"
                message_text += f"Asset: {position.asset.value} | Side: {position.side.value}\n" 
                message_text += f"Entry: {position.entry.value} | Stop Loss: {position.stop_loss.value}"
                
                await query.message.edit_text(message_text, reply_markup=keyboard)
                
    except Exception as e:
        logger.exception(f"Error in position_details_handler: {e}")
        await update.message.reply_text("❌ Error loading position details.")

async def open_positions_navigation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالجة التنقل بين صفحات الصفقات المفتوحة"""
    try:
        query = update.callback_query
        await query.answer()
        
        parsed = CallbackBuilder.parse(query.data)
        
        namespace = parsed.get('namespace')
        action = parsed.get('action')
        params = parsed.get('params', [])
        
        if namespace == "nav" and action == "nv" and params:
            page = int(params[0])
            
            with session_scope() as db_session:
                trade_service = get_service(context, "trade_service", TradeService)
                user_telegram_id = str(update.effective_user.id)
                
                open_positions = trade_service.get_open_positions_for_user(db_session, user_telegram_id)
                
                if open_positions:
                    keyboard = await build_open_recs_keyboard(
                        open_positions, 
                        page,
                        get_service(context, "price_service", PriceService)
                    )
                    
                    await query.edit_message_reply_markup(reply_markup=keyboard)
                else:
                    await query.message.reply_text("📭 No open positions found.")
                    
    except Exception as e:
        logger.exception(f"Error in open_positions_navigation_handler: {e}")

def register_management_handlers(app: Application):
    """تسجيل معالجات الإدارة"""
    
    # 🔧 إصلاح: تحديث أنماط callback_data
    app.add_handler(CallbackQueryHandler(
        position_details_handler, 
        pattern=r"^pos:"  # جميع callback_data التي تبدأ بـ pos:
    ))
    
    app.add_handler(CallbackQueryHandler(
        open_positions_navigation_handler,
        pattern=r"^nav:"  # للتنقل بين الصفحات
    ))
    
    # الأوامر النصية
    app.add_handler(CommandHandler(["myportfolio", "open"], open_positions_handler))