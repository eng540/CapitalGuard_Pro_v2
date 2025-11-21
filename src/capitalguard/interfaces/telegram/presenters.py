# --- START OF NEW FILE: src/capitalguard/interfaces/telegram/presenters.py --- v1
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.constants import ParseMode
from capitalguard.interfaces.telegram.keyboards import CallbackBuilder, CallbackNamespace, CallbackAction
from capitalguard.interfaces.telegram.ui_texts import build_trade_card_text
from capitalguard.interfaces.telegram.presenters import ManagementPresenter

# Assumed helper for safe markdown escape (implementing it here for completeness)
def _safe_escape_markdown(text: str) -> str:
    import re
    if not isinstance(text, str): text = str(text)
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

class ManagementPresenter:
    """
    Responsible for creating UI elements (Text & Keyboards) for the Management Module.
    """

    @staticmethod
    def get_edit_prompt(action: str) -> str:
        prompts = {
            "edit_entry": "💰 <b>تعديل سعر الدخول</b>\nالرجاء إدخال السعر الجديد:",
            "edit_sl": "🛑 <b>تعديل وقف الخسارة</b>\nالرجاء إدخال السعر الجديد:",
            "edit_tp": "🎯 <b>تعديل الأهداف</b>\nالرجاء إدخال الأهداف (مثال: `61000 62000@50`):",
            "edit_notes": "📝 <b>تعديل الملاحظات</b>\nأدخل الملاحظة الجديدة (أو 'clear' للمسح):",
            "close_manual": "✍️ <b>إغلاق يدوي</b>\nأدخل سعر الخروج:",
            "partial_close_custom": "📉 <b>إغلاق جزئي مخصص</b>\nأدخل النسبة المئوية للإغلاق (مثال: 30):"
        }
        return prompts.get(action, "✍️ الرجاء إدخال القيمة الجديدة:")

    @staticmethod
    def get_cancel_keyboard(rec_id: int) -> InlineKeyboardMarkup:
        cancel_btn = InlineKeyboardButton(
            "❌ إلغاء (Cancel)", 
            callback_data=CallbackBuilder.create(CallbackNamespace.MGMT, "cancel_input", rec_id)
        )
        return InlineKeyboardMarkup([[cancel_btn]])

    @staticmethod
    def get_error_view(error_message: str) -> str:
        return f"⚠️ <b>خطأ:</b>\n{error_message}"

    @staticmethod
    async def render_edit_prompt(update: Update, action: str, rec_id: int):
        """Renders the prompt message for input."""
        prompt_text = ManagementPresenter.get_edit_prompt(action)
        keyboard = ManagementPresenter.get_cancel_keyboard(rec_id)

        # Assuming safe_edit_message utility is available/imported globally
        await update.get_bot().edit_message_text(
            chat_id=update.callback_query.message.chat_id, 
            message_id=update.callback_query.message.message_id,
            text=prompt_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
# --- END OF NEW FILE ---