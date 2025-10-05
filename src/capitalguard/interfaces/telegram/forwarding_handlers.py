"""
ForwardingHandlers - معالجات إعادة توجيه الرسائل لإنشاء صفقات شخصية
"""

import logging
from typing import Dict, Any, Optional

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
from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.infrastructure.db.models import UserTrade, UserTradeStatus

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
        
        # الحصول على خدمة التحليل
        parsing_service = await self.get_parsing_service(context)
        
        # تحديد نوع المحتوى
        is_image = bool(message.photo)
        content = ""
        
        if is_image:
            # في الإصدار الحالي، نتعامل مع الصور كنص تجريبي
            # TODO: في المستقبل، ننزل الصورة ونعالجها
            if message.caption:
                content = message.caption
            else:
                content = "صورة تحتوي على إشارة تداول"
        else:
            content = message.text or ""
            
        if not content:
            await update.message.reply_text(
                "❌ لا يمكن معالجة هذه الرسالة.\n\n"
                "⚠️ يرجى إعادة توجيه رسالة تحتوي على:\n"
                "• نص واضح لبيانات التداول\n"
                "• أو صورة تحتوي على نص واضح"
            )
            return ConversationHandler.END
            
        # عرض رسالة "جاري المعالجة"
        processing_msg = await update.message.reply_text("🔄 جاري تحليل الرسالة...")
            
        # استخراج بيانات التداول
        trade_data = await parsing_service.extract_trade_data(content, is_image)
        
        if not trade_data:
            await processing_msg.edit_text(
                "❌ لم أتمكن من التعرف على بيانات التداول في هذه الرسالة.\n\n"
                "📋 تأكد من أن الرسالة تحتوي على:\n"
                "• رمز الأصل (مثل: BTCUSDT)\n" 
                "• الاتجاه (LONG أو SHORT)\n"
                "• سعر الدخول\n"
                "• وقف الخسارة\n"
                "• أهداف الربح\n\n"
                "💡 أمثلة للتنسيقات المدعومة:\n"
                "• BTCUSDT LONG 50000 49000 52000 54000\n"
                "• ETHUSDT SHORT Entry: 3500 SL: 3400 TP1: 3300 TP2: 3200"
            )
            return ConversationHandler.END
            
        # حفظ البيانات مؤقتاً في context
        context.user_data['pending_trade'] = trade_data
        context.user_data['original_message'] = message.message_id
        
        # عرض البيانات المستخرجة للمستخدم
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
        confidence = trade_data.get('confidence', 'unknown')
        parser = trade_data.get('parser', 'unknown')
        
        side_emoji = "📈" if side == "LONG" else "📉"
        side_arabic = "شراء" if side == "LONG" else "بيع"
        
        text = f"{side_emoji} <b>تم استخراج بيانات التداول بنجاح</b>\n\n"
        text += f"<b>الأصل:</b> {asset}\n"
        text += f"<b>الاتجاه:</b> {side} ({side_arabic})\n"
        text += f"<b>سعر الدخول:</b> {entry:g}\n"
        text += f"<b>وقف الخسارة:</b> {sl:g}\n"
        
        # حساب نسبة المخاطرة
        if entry > 0 and sl > 0:
            if side == "LONG":
                risk_pct = ((entry - sl) / entry) * 100
            else:
                risk_pct = ((sl - entry) / entry) * 100
            text += f"<b>نسبة المخاطرة:</b> {risk_pct:.2f}%\n"
        
        text += f"<b>ثقة التحليل:</b> {confidence}\n\n"
        
        text += "<b>🎯 أهداف الربح:</b>\n"
        total_percent = 0
        for i, target in enumerate(targets, 1):
            # حساب نسبة الربح لكل هدف
            if side == "LONG" and entry > 0:
                profit_pct = ((target['price'] - entry) / entry) * 100
            elif side == "SHORT" and entry > 0:
                profit_pct = ((entry - target['price']) / entry) * 100
            else:
                profit_pct = 0
                
            text += f"  TP{i}: {target['price']:g} (+{profit_pct:.2f}%)"
            if target['close_percent'] > 0:
                text += f" 🔹 إغلاق {target['close_percent']}%"
            text += "\n"
            total_percent += target['close_percent']
            
        if total_percent != 100 and total_percent > 0:
            text += f"<i>ملاحظة: مجموع نسب الإغلاق: {total_percent}%</i>\n"
            
        text += f"\n📊 <i>تم التحليل باستخدام: {parser}</i>"
        
        return text
        
    def _build_confirmation_keyboard(self) -> InlineKeyboardMarkup:
        """بناء زر التأكيد"""
        keyboard = [
            [
                InlineKeyboardButton("✅ تأكيد وإضافة للمحفظة", callback_data="confirm_forwarded_trade"),
                InlineKeyboardButton("❌ إلغاء", callback_data="cancel_forwarded_trade")
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
            await query.edit_message_text("❌ انتهت صلاحية البيانات. يرجى إعادة التوجيه مرة أخرى.")
            return ConversationHandler.END
            
        try:
            # إضافة الصفقة إلى قاعدة البيانات
            result = await self._add_forwarded_trade(user_id, trade_data, db_session, context)
            
            if result['success']:
                await query.edit_message_text(
                    f"✅ <b>تمت إضافة الصفقة إلى محفظتك بنجاح!</b>\n\n"
                    f"<b>الأصل:</b> {trade_data['asset']}\n"
                    f"<b>الاتجاه:</b> {trade_data['side']}\n"
                    f"<b>رقم الصفقة:</b> #{result['trade_id']}\n\n"
                    f"📱 استخدم <code>/myportfolio</code> لعرض جميع صفقاتك.\n"
                    f"🔔 ستتلقى تنبيهات تلقائية عند تحقيق الأهداف.",
                    parse_mode='HTML'
                )
                
                # تحديث فهارس التنبيهات
                alert_service = get_service(context, "alert_service")
                if alert_service:
                    await alert_service.build_triggers_index()
                    
            else:
                await query.edit_message_text(f"❌ {result['message']}")
                
        except Exception as e:
            log.error(f"Error confirming forwarded trade: {e}", exc_info=True)
            await query.edit_message_text("❌ حدث خطأ غير متوقع أثناء إضافة الصفقة. يرجى المحاولة لاحقاً.")
            
        finally:
            # تنظيف البيانات المؤقتة
            context.user_data.pop('pending_trade', None)
            context.user_data.pop('original_message', None)
            
        return ConversationHandler.END
        
    async def _add_forwarded_trade(self, user_id: str, trade_data: Dict[str, Any], db_session, context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
        """إضافة الصفقة المعاد توجيهها إلى قاعدة البيانات"""
        try:
            # البحث عن المستخدم
            user_repo = UserRepository(db_session)
            user = user_repo.find_by_telegram_id(int(user_id))
            
            if not user:
                return {'success': False, 'message': 'المستخدم غير موجود'}
                
            # إنشاء سجل UserTrade جديد
            new_trade = UserTrade(
                user_id=user.id,
                asset=trade_data['asset'],
                side=trade_data['side'],
                entry=float(trade_data['entry']),
                stop_loss=float(trade_data['stop_loss']),
                targets=trade_data['targets'],
                status=UserTradeStatus.OPEN,
                source_forwarded_text=str(trade_data)  # حفظ البيانات الأصلية للرجوع إليها
            )
            
            db_session.add(new_trade)
            db_session.flush()
            
            log.info(f"✅ Added forwarded trade #{new_trade.id} for user {user_id} - {trade_data['asset']} {trade_data['side']}")
            
            return {
                'success': True,
                'trade_id': new_trade.id,
                'message': 'تمت الإضافة بنجاح'
            }
            
        except Exception as e:
            log.error(f"❌ Failed to add forwarded trade for user {user_id}: {e}")
            return {'success': False, 'message': f'فشل في إضافة الصفقة: {str(e)}'}
        
    async def handle_cancellation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """معالجة إلغاء العملية"""
        query = update.callback_query
        await query.answer("تم الإلغاء")
        
        # تنظيف البيانات المؤقتة
        context.user_data.pop('pending_trade', None)
        context.user_data.pop('original_message', None)
        
        await query.edit_message_text("❌ تم إلغاء العملية.")
        return ConversationHandler.END

# إنشاء instance عالمي
forwarding_handlers = ForwardingHandlers()

def create_forwarding_conversation_handler():
    """إنشاء معالج المحادثة الخاص بإعادة التوجيه"""
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.FORWARDED & (filters.TEXT | filters.PHOTO | filters.CAPTION),
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