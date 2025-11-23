# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/presenters.py ---
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.constants import ParseMode
from capitalguard.interfaces.telegram.keyboards import CallbackBuilder, CallbackNamespace, CallbackAction

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
            "partial_close_custom": "📉 <b>إغلاق جزئي مخصص</b>\nأدخل النسبة المئوية للإغلاق (مثال: 30):",
            # ✅ ADDED PROMPTS FOR RISK MANAGEMENT
            "set_fixed": "🔒 <b>تحديد هدف ربح ثابت (Fixed Stop)</b>\nأدخل السعر الذي تريد الخروج عنده بالكامل:",
            "set_trailing": "📈 <b>تحديد وقف متحرك (Trailing Stop)</b>\nأدخل قيمة التحرك (مثال: `100` للنقاط أو `1.5%` للنسبة):"
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

        await update.callback_query.message.edit_text(
            text=prompt_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/presenters.py ---