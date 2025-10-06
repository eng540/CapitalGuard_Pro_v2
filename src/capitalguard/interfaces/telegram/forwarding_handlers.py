"""
ForwardingHandlers - معالجات إعادة توجيه الرسائل لإنشاء صفقات شخصية
"""

import logging
from typing import Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, 
    MessageHandler, 
    CallbackQueryHandler, 
    filters, 
    ConversationHandler
)

from .helpers import get_service, unit_of_work
from .auth import require_active_user
from capitalguard.application.services.image_parsing_service import ImageParsingService
from capitalguard.application.services.trade_service import TradeService

log = logging.getLogger(__name__)

# حالات المحادثة
AWAITING_CONFIRMATION = 1

class ForwardingHandlers:
    """يدير معالجة الرسائل المعاد توجيهها"""
    
    def __init__(self):
        self.parsing_service = None
        
    async def get_parsing_service(self, context: ContextTypes.DEFAULT_TYPE) -> ImageParsingService:
        """الحصول على خدمة التحليل مع التهيئة"""
        if not self.parsing_service:
            self.parsing_service = get_service(context, "image_parsing_service", ImageParsingService)
            await self.parsing_service.initialize()
        return self.parsing_service
        
    @require_active_user
    async def handle_forwarded_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """معالجة الرسائل المعاد توجيهها"""
        user = update.effective_user
        message = update.message
        
        log.info(f"🔄 Processing forwarded message from user {user.id}")
        
        parsing_service = await self.get_parsing_service(context)
        
        content = message.text or ""
        if not content:
            await update.message.reply_text("❌ Forwarded message contains no text to parse.")
            return ConversationHandler.END
            
        processing_msg = await update.message.reply_text("🔄 Analyzing signal...")
            
        trade_data = await parsing_service.extract_trade_data(content, is_image=False)
        
        if not trade_data:
            await processing_msg.edit_text("❌ Could not recognize trade data in this message.")
            return ConversationHandler.END
            
        context.user_data['pending_trade'] = trade_data
        
        confirmation_text = self._build_confirmation_text(trade_data)
        keyboard = self._build_confirmation_keyboard()
        
        await processing_msg.edit_text(
            confirmation_text,
            reply_markup=keyboard,
            parse_mode='HTML'
        )
        
        return AWAITING_CONFIRMATION
        
    def _build_confirmation_text(self, trade_data: Dict[str, Any]) -> str:
        """بناء نص تأكيد البيانات المستخرجة"""
        asset = trade_data['asset']
        side = trade_data['side']
        entry = trade_data['entry']
        sl = trade_data['stop_loss']
        targets = trade_data['targets']
        
        side_emoji = "📈" if side == "LONG" else "📉"
        
        text = f"{side_emoji} <b>Signal Parsed Successfully</b>\n\n"
        text += f"<b>Asset:</b> {asset}\n"
        text += f"<b>Direction:</b> {side}\n"
        text += f"<b>Entry:</b> {entry:g}\n"
        text += f"<b>Stop Loss:</b> {sl:g}\n"
        
        text += "<b>🎯 Targets:</b>\n"
        for i, target in enumerate(targets, 1):
            text += f"  TP{i}: {target['price']:g}\n"
            
        return text
        
    def _build_confirmation_keyboard(self) -> InlineKeyboardMarkup:
        """بناء زر التأكيد"""
        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm & Add to Portfolio", callback_data="confirm_forwarded_trade"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_forwarded_trade")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
        
    @unit_of_work
    async def handle_confirmation(self, update: Update, context: ContextTypes.DEFAULT_TYPE, db_session) -> int:
        """معالجة تأكيد إضافة الصفقة"""
        query = update.callback_query
        await query.answer()
        
        user_id = str(query.from_user.id)
        trade_data = context.user_data.get('pending_trade')
        
        if not trade_data:
            await query.edit_message_text("❌ Data expired. Please forward the message again.")
            return ConversationHandler.END
            
        trade_service = get_service(context, "trade_service", TradeService)
        result = await trade_service.track_forwarded_trade(user_id, trade_data, db_session)
        
        if result.get('success'):
            await query.edit_message_text(
                f"✅ <b>Trade #{result['trade_id']} for {result['asset']} added to your portfolio!</b>\n\n"
                f"Use <code>/myportfolio</code> to view your open trades.",
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text(f"❌ Error: {result.get('error', 'Unknown error')}")
            
        context.user_data.pop('pending_trade', None)
        return ConversationHandler.END
        
    async def handle_cancellation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """معالجة إلغاء العملية"""
        query = update.callback_query
        await query.answer("Cancelled")
        context.user_data.pop('pending_trade', None)
        await query.edit_message_text("❌ Operation cancelled.")
        return ConversationHandler.END

# إنشاء instance عالمي
forwarding_handlers = ForwardingHandlers()

def create_forwarding_conversation_handler():
    """إنشاء معالج المحادثة الخاص بإعادة التوجيه"""
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.FORWARDED & filters.TEXT,
                forwarding_handlers.handle_forwarded_message
            )
        ],
        states={
            AWAITING_CONFIRMATION: [
                CallbackQueryHandler(
                    forwarding_handlers.handle_confirmation,
                    pattern="^confirm_forwarded_trade$"
                ),
                CallbackQueryHandler(
                    forwarding_handlers.handle_cancellation, 
                    pattern="^cancel_forwarded_trade$"
                )
            ]
        },
        fallbacks=[
            CallbackQueryHandler(
                forwarding_handlers.handle_cancellation,
                pattern="^cancel_forwarded_trade$"
            )
        ],
        name="forwarding_conversation",
        persistent=False
    )