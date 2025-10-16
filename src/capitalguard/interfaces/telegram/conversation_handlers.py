# src/capitalguard/interfaces/telegram/conversation_handlers.py
# ✅ THE FIX: Added robust user_data initialization and state recovery mechanism

import logging
from typing import Dict, Any, Optional, Callable
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, filters

from capitalguard.application.services import TradeService, AnalyticsService
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import UserRepository, RecommendationRepository
from capitalguard.domain.entities import RecommendationStatus, Side, OrderType, ExitStrategy
from capitalguard.interfaces.telegram.keyboards import get_recommendation_keyboard, get_confirmation_keyboard
from capitalguard.interfaces.telegram.validators import validate_price_input, validate_asset_input

log = logging.getLogger(__name__)

# Conversation states
ASSET, SIDE, ENTRY, STOP_LOSS, TARGETS, ORDER_TYPE, EXIT_STRATEGY, MARKET, NOTES, CONFIRMATION = range(10)

# ✅ NEW: Helper function to ensure user_data is properly initialized
def ensure_user_data_initialized(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    """
    Ensures user_data is properly initialized and returns it.
    This prevents the 'NoneType' error that was causing crashes.
    """
    if context.user_data is None:
        log.warning("user_data was None, initializing new dictionary")
        context.user_data = {}
    
    # Initialize recommendation data structure if it doesn't exist
    if "rec_data" not in context.user_data:
        context.user_data["rec_data"] = {}
    
    # Initialize conversation step if it doesn't exist
    if "rec_creation_step" not in context.user_data:
        context.user_data["rec_creation_step"] = ASSET
    
    return context.user_data

# ✅ NEW: Intelligent state recovery function
def recover_conversation_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """
    Attempts to recover the conversation state based on message context.
    Returns the appropriate conversation state or None if recovery is not possible.
    """
    user_data = ensure_user_data_initialized(context)
    rec_data = user_data.get("rec_data", {})
    
    # Check what data we already have to determine current step
    if not rec_data.get("asset"):
        return ASSET
    elif not rec_data.get("side"):
        return SIDE
    elif not rec_data.get("entry"):
        return ENTRY
    elif not rec_data.get("stop_loss"):
        return STOP_LOSS
    elif not rec_data.get("targets"):
        return TARGETS
    elif not rec_data.get("order_type"):
        return ORDER_TYPE
    elif not rec_data.get("exit_strategy"):
        return EXIT_STRATEGY
    elif not rec_data.get("market"):
        return MARKET
    elif not rec_data.get("notes"):
        return NOTES
    else:
        return CONFIRMATION

async def start_recommendation_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the recommendation creation conversation."""
    # ✅ FIX: Ensure user_data is initialized
    user_data = ensure_user_data_initialized(context)
    
    # Reset conversation state
    user_data["rec_data"] = {}
    user_data["rec_creation_step"] = ASSET
    
    await update.message.reply_text(
        "📊 *إنشاء توصية جديدة*\n\n"
        "الرجاء إدخال رمز الأصل (مثال: BTCUSDT):",
        parse_mode="Markdown"
    )
    
    return ASSET

async def text_input_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """
    Routes text input based on the current conversation step.
    ✅ FIXED: Now safely handles user_data initialization and state recovery.
    """
    try:
        # ✅ THE FIX: Ensure user_data is properly initialized before accessing it
        user_data = ensure_user_data_initialized(context)
        
        # ✅ ENHANCEMENT: Add state recovery if step is missing
        step = user_data.get("rec_creation_step")
        if step is None:
            log.warning("Conversation step was None, attempting recovery")
            step = recover_conversation_state(update, context)
            user_data["rec_creation_step"] = step
        
        # Route to appropriate handler based on step
        handlers = {
            ASSET: handle_asset_input,
            SIDE: handle_side_input,
            ENTRY: handle_entry_input,
            STOP_LOSS: handle_stop_loss_input,
            TARGETS: handle_targets_input,
            ORDER_TYPE: handle_order_type_input,
            EXIT_STRATEGY: handle_exit_strategy_input,
            MARKET: handle_market_input,
            NOTES: handle_notes_input,
        }
        
        handler = handlers.get(step)
        if handler:
            return await handler(update, context)
        else:
            log.error(f"Unknown conversation step: {step}")
            await update.message.reply_text(
                "❌ حدث خطأ في تدفق المحادثة. يرجى البدء من جديد باستخدام /new_rec"
            )
            return ConversationHandler.END
            
    except Exception as e:
        log.error(f"Error in text_input_router: {e}", exc_info=True)
        # ✅ ENHANCEMENT: Graceful error handling with user feedback
        try:
            await update.message.reply_text(
                "❌ حدث خطأ غير متوقع. يرجى المحاولة مرة أخرى أو البدء من جديد باستخدام /new_rec"
            )
        except Exception:
            log.error("Failed to send error message to user")
        
        return ConversationHandler.END

async def handle_asset_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle asset symbol input."""
    user_data = ensure_user_data_initialized(context)
    asset = update.message.text.strip().upper()
    
    if not validate_asset_input(asset):
        await update.message.reply_text(
            "❌ رمز الأصل غير صالح. الرجاء إدخال رمز صحيح (مثال: BTCUSDT):"
        )
        return ASSET
    
    user_data["rec_data"]["asset"] = asset
    user_data["rec_creation_step"] = SIDE
    
    keyboard = [
        [InlineKeyboardButton("📈 LONG (شراء)", callback_data="SIDE_LONG")],
        [InlineKeyboardButton("📉 SHORT (بيع)", callback_data="SIDE_SHORT")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"✅ تم تحديد الأصل: {asset}\n\n"
        "الرجاء اختيار اتجاه التداول:",
        reply_markup=reply_markup
    )
    
    return SIDE

async def handle_side_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle side selection via callback."""
    query = update.callback_query
    await query.answer()
    
    user_data = ensure_user_data_initialized(context)
    side_value = query.data.replace("SIDE_", "")
    
    user_data["rec_data"]["side"] = side_value
    user_data["rec_creation_step"] = ENTRY
    
    await query.edit_message_text(
        f"✅ تم تحديد الاتجاه: {side_value}\n\n"
        "الرجاء إدخال سعر الدخول:"
    )
    
    return ENTRY

async def handle_entry_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle entry price input."""
    user_data = ensure_user_data_initialized(context)
    entry_text = update.message.text.strip()
    
    try:
        entry_price = float(entry_text)
        if not validate_price_input(entry_price):
            raise ValueError("Invalid price")
    except ValueError:
        await update.message.reply_text(
            "❌ سعر الدخول غير صالح. الرجاء إدخال رقم صحيح:"
        )
        return ENTRY
    
    user_data["rec_data"]["entry"] = entry_price
    user_data["rec_creation_step"] = STOP_LOSS
    
    await update.message.reply_text(
        f"✅ تم تحديد سعر الدخول: {entry_price}\n\n"
        "الرجاء إدخال سعر وقف الخسارة:"
    )
    
    return STOP_LOSS

async def handle_stop_loss_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle stop loss input with validation."""
    user_data = ensure_user_data_initialized(context)
    sl_text = update.message.text.strip()
    
    try:
        sl_price = float(sl_text)
        if not validate_price_input(sl_price):
            raise ValueError("Invalid price")
        
        # ✅ ENHANCEMENT: Better validation logic
        entry_price = user_data["rec_data"]["entry"]
        side = user_data["rec_data"]["side"]
        
        if side == "LONG" and sl_price >= entry_price:
            await update.message.reply_text(
                "❌ لصفقات الشراء (LONG)، يجب أن يكون وقف الخسارة أقل من سعر الدخول.\n"
                f"سعر الدخول: {entry_price}\n"
                "الرجاء إدخال سعر وقف خسارة صحيح:"
            )
            return STOP_LOSS
        elif side == "SHORT" and sl_price <= entry_price:
            await update.message.reply_text(
                "❌ لصفقات البيع (SHORT)، يجب أن يكون وقف الخسارة أعلى من سعر الدخول.\n"
                f"سعر الدخول: {entry_price}\n"
                "الرجاء إدخال سعر وقف خسارة صحيح:"
            )
            return STOP_LOSS
            
    except ValueError:
        await update.message.reply_text(
            "❌ سعر وقف الخسارة غير صالح. الرجاء إدخال رقم صحيح:"
        )
        return STOP_LOSS
    
    user_data["rec_data"]["stop_loss"] = sl_price
    user_data["rec_creation_step"] = TARGETS
    
    await update.message.reply_text(
        f"✅ تم تحديد وقف الخسارة: {sl_price}\n\n"
        "الرجاء إدخال أهداف الربح (مثال: 115000, 120000, 125000):"
    )
    
    return TARGETS

async def handle_targets_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle targets input."""
    user_data = ensure_user_data_initialized(context)
    targets_text = update.message.text.strip()
    
    try:
        target_prices = [float(t.strip()) for t in targets_text.split(",")]
        if not target_prices:
            raise ValueError("No targets provided")
        
        # Validate targets
        entry_price = user_data["rec_data"]["entry"]
        side = user_data["rec_data"]["side"]
        
        for i, target in enumerate(target_prices, 1):
            if side == "LONG" and target <= entry_price:
                await update.message.reply_text(
                    f"❌ الهدف {i} ({target}) يجب أن يكون أعلى من سعر الدخول ({entry_price}) للصفقات الطويلة.\n"
                    "الرجاء إدخال الأهداف مرة أخرى:"
                )
                return TARGETS
            elif side == "SHORT" and target >= entry_price:
                await update.message.reply_text(
                    f"❌ الهدف {i} ({target}) يجب أن يكون أقل من سعر الدخول ({entry_price}) للصفقات القصيرة.\n"
                    "الرجاء إدخال الأهداف مرة أخرى:"
                )
                return TARGETS
        
        # Format targets as required by the system
        targets = [{"price": target, "percentage": None} for target in target_prices]
        user_data["rec_data"]["targets"] = targets
        
    except ValueError:
        await update.message.reply_text(
            "❌ صيغة الأهداف غير صالحة. الرجاء إدخال الأهداف مفصولة بفواصل (مثال: 115000, 120000):"
        )
        return TARGETS
    
    user_data["rec_creation_step"] = ORDER_TYPE
    
    keyboard = [
        [InlineKeyboardButton("🎯 MARKET", callback_data="ORDER_MARKET")],
        [InlineKeyboardButton("⏰ LIMIT", callback_data="ORDER_LIMIT")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"✅ تم تحديد الأهداف: {targets_text}\n\n"
        "الرجاء اختيار نوع الأمر:",
        reply_markup=reply_markup
    )
    
    return ORDER_TYPE

async def handle_order_type_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle order type selection."""
    query = update.callback_query
    await query.answer()
    
    user_data = ensure_user_data_initialized(context)
    order_type = query.data.replace("ORDER_", "")
    
    user_data["rec_data"]["order_type"] = order_type
    user_data["rec_creation_step"] = EXIT_STRATEGY
    
    keyboard = [
        [InlineKeyboardButton("🔄 إغلاق عند الهدف النهائي", callback_data="EXIT_CLOSE_AT_FINAL_TP")],
        [InlineKeyboardButton("✋ إغلاق يدوي فقط", callback_data="EXIT_MANUAL_CLOSE_ONLY")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"✅ تم تحديد نوع الأمر: {order_type}\n\n"
        "الرجاء اختيار استراتيجية الخروج:",
        reply_markup=reply_markup
    )
    
    return EXIT_STRATEGY

async def handle_exit_strategy_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle exit strategy selection."""
    query = update.callback_query
    await query.answer()
    
    user_data = ensure_user_data_initialized(context)
    exit_strategy = query.data.replace("EXIT_", "")
    
    user_data["rec_data"]["exit_strategy"] = exit_strategy
    user_data["rec_creation_step"] = MARKET
    
    keyboard = [
        [InlineKeyboardButton("🔥 Futures", callback_data="MARKET_FUTURES")],
        [InlineKeyboardButton("💰 Spot", callback_data="MARKET_SPOT")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"✅ تم تحديد استراتيجية الخروج: {exit_strategy}\n\n"
        "الرجاء اختيار السوق:",
        reply_markup=reply_markup
    )
    
    return MARKET

async def handle_market_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle market selection."""
    query = update.callback_query
    await query.answer()
    
    user_data = ensure_user_data_initialized(context)
    market = query.data.replace("MARKET_", "")
    
    user_data["rec_data"]["market"] = market
    user_data["rec_creation_step"] = NOTES
    
    await query.edit_message_text(
        f"✅ تم تحديد السوق: {market}\n\n"
        "الرجاء إدخال ملاحظات (اختياري، أرسل /skip لتخطي):"
    )
    
    return NOTES

async def handle_notes_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle notes input."""
    user_data = ensure_user_data_initialized(context)
    
    if update.message.text == "/skip":
        notes = ""
    else:
        notes = update.message.text.strip()
    
    user_data["rec_data"]["notes"] = notes
    user_data["rec_creation_step"] = CONFIRMATION
    
    # Show confirmation with all details
    rec_data = user_data["rec_data"]
    
    confirmation_text = (
        f"📊 *تأكيد التوصية*\n\n"
        f"🔹 الأصل: {rec_data['asset']}\n"
        f"🔹 الاتجاه: {rec_data['side']}\n"
        f"🔹 الدخول: {rec_data['entry']}\n"
        f"🔹 وقف الخسارة: {rec_data['stop_loss']}\n"
        f"🔹 الأهداف: {', '.join([str(t['price']) for t in rec_data['targets']])}\n"
        f"🔹 نوع الأمر: {rec_data['order_type']}\n"
        f"🔹 استراتيجية الخروج: {rec_data['exit_strategy']}\n"
        f"🔹 السوق: {rec_data['market']}\n"
        f"🔹 ملاحظات: {notes if notes else 'لا توجد'}\n\n"
        f"هل تريد نشر هذه التوصية؟"
    )
    
    reply_markup = get_confirmation_keyboard()
    await update.message.reply_text(confirmation_text, parse_mode="Markdown", reply_markup=reply_markup)
    
    return CONFIRMATION

async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle final confirmation and create the recommendation."""
    query = update.callback_query
    await query.answer()
    
    user_data = ensure_user_data_initialized(context)
    
    if query.data == "CONFIRM_YES":
        try:
            rec_data = user_data["rec_data"]
            
            with session_scope() as session:
                user_repo = UserRepository(session)
                rec_repo = RecommendationRepository(session)
                
                # Get user from telegram
                telegram_user = update.effective_user
                user = user_repo.find_by_telegram_id(telegram_user.id)
                
                if not user:
                    await query.edit_message_text(
                        "❌ لم يتم العثور على حسابك. يرجى التسجيل أولاً."
                    )
                    return ConversationHandler.END
                
                # Create recommendation
                recommendation = rec_repo.create_recommendation(
                    analyst_id=user.id,
                    asset=rec_data["asset"],
                    side=Side(rec_data["side"]),
                    entry=rec_data["entry"],
                    stop_loss=rec_data["stop_loss"],
                    targets=rec_data["targets"],
                    order_type=OrderType(rec_data["order_type"]),
                    exit_strategy=ExitStrategy(rec_data["exit_strategy"]),
                    market=rec_data["market"],
                    notes=rec_data["notes"] if rec_data["notes"] else None
                )
                
                await query.edit_message_text(
                    f"✅ تم نشر التوصية بنجاح!\n\n"
                    f"رقم التوصية: #{recommendation.id}\n"
                    f"الأصل: {recommendation.asset.value}\n"
                    f"الاتجاه: {recommendation.side.value}"
                )
                
        except Exception as e:
            log.error(f"Error creating recommendation: {e}", exc_info=True)
            await query.edit_message_text(
                "❌ حدث خطأ أثناء إنشاء التوصية. يرجى المحاولة مرة أخرى."
            )
    
    else:  # CONFIRM_NO
        await query.edit_message_text(
            "❌ تم إلغاء إنشاء التوصية."
        )
    
    # Clean up conversation data
    user_data.pop("rec_data", None)
    user_data.pop("rec_creation_step", None)
    
    return ConversationHandler.END

async def cancel_recommendation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the recommendation creation."""
    user_data = ensure_user_data_initialized(context)
    
    # Clean up conversation data
    user_data.pop("rec_data", None)
    user_data.pop("rec_creation_step", None)
    
    await update.message.reply_text(
        "❌ تم إلغاء إنشاء التوصية."
    )
    
    return ConversationHandler.END

# Create the conversation handler
def get_recommendation_conversation_handler() -> ConversationHandler:
    """Returns the configured conversation handler for recommendation creation."""
    
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, start_recommendation_creation)
        ],
        
        states={
            ASSET: [MessageHandler(filters.TEXT & ~filters.COMMAND, text_input_router)],
            SIDE: [CallbackQueryHandler(text_input_router, pattern="^SIDE_")],
            ENTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, text_input_router)],
            STOP_LOSS: [MessageHandler(filters.TEXT & ~filters.COMMAND, text_input_router)],
            TARGETS: [MessageHandler(filters.TEXT & ~filters.COMMAND, text_input_router)],
            ORDER_TYPE: [CallbackQueryHandler(text_input_router, pattern="^ORDER_")],
            EXIT_STRATEGY: [CallbackQueryHandler(text_input_router, pattern="^EXIT_")],
            MARKET: [CallbackQueryHandler(text_input_router, pattern="^MARKET_")],
            NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, text_input_router)],
            CONFIRMATION: [CallbackQueryHandler(handle_confirmation, pattern="^CONFIRM_")],
        },
        
        fallbacks=[
            CommandHandler("cancel", cancel_recommendation),
            MessageHandler(filters.COMMAND, cancel_recommendation)
        ],
        
        per_message=False
    )