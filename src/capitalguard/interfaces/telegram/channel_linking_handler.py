# src/capitalguard/interfaces/telegram/channel_linking_handler.py
# (v1.6 - PRODUCTION READY WITH ENHANCED STABILITY)
"""
نظام ربط وفك ربط القنوات المحسَن - إصدار إنتاجي مستقر
✅ معالجة محسنة للأخطاء والاستثناءات
✅ تحقق متقدم من صلاحيات البوت
✅ نظام مهلات للمحادثات
✅ توافق كامل مع النظام الحالي
"""

import logging
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ConversationHandler,
)

from capitalguard.infrastructure.db.uow import uow_transaction
from .auth import require_active_user, require_analyst_user
from capitalguard.infrastructure.db.repository import ChannelRepository

log = logging.getLogger(__name__)

# --- Conversation States ---
AWAITING_CHANNEL_FORWARD = 1
AWAITING_UNLINK_SELECTION = 2
AWAIT_UNLINK_CONFIRM = 3

# --- Timeout Configuration ---
LINKING_TIMEOUT = 600  # 10 دقائق
LAST_ACTIVITY_KEY = "last_linking_activity"

def clean_linking_state(context: ContextTypes.DEFAULT_TYPE):
    """تنظيف حالة ربط القنوات"""
    context.user_data.pop(LAST_ACTIVITY_KEY, None)

def update_linking_activity(context: ContextTypes.DEFAULT_TYPE):
    """تحديث وقت النشاط الأخير"""
    context.user_data[LAST_ACTIVITY_KEY] = time.time()

def check_linking_timeout(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """التحقق من انتهاء مدة محادثة الربط"""
    last_activity = context.user_data.get(LAST_ACTIVITY_KEY, 0)
    current_time = time.time()
    return current_time - last_activity > LINKING_TIMEOUT

async def handle_linking_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة انتهاء مدة محادثة الربط"""
    if check_linking_timeout(context):
        clean_linking_state(context)
        if update.callback_query:
            await update.callback_query.answer("انتهت مدة الجلسة", show_alert=True)
            await update.callback_query.edit_message_text("⏰ انتهت مدة الجلسة. يرجى البدء من جديد.")
        elif update.message:
            await update.message.reply_text("⏰ انتهت مدة الجلسة. يرجى البدء من جديد.")
        return True
    return False

# --- Conversation Entry Point (Link) ---
@uow_transaction
@require_active_user
@require_analyst_user
async def link_channel_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, **kwargs) -> int:
    """بدء محادثة ربط قناة جديدة"""
    clean_linking_state(context)
    update_linking_activity(context)
    
    help_text = """
🔗 <b>ربط قناة جديدة</b>

لربط قناة حيث يمكن للبوت نشر الإشارات:

1️⃣ <b>أضف البوت كمسؤول في قناتك</b> مع الصلاحيات التالية:
   • ✏️ نشر الرسائل
   • 🗑️ حذف الرسائل
   • 👁️ مشاهدة المعلومات الأساسية

2️⃣ <b>اعرض أي رسالة</b> من تلك القناة إلى هذه الدردشة.

3️⃣ <b>انتظر التحقق التلقائي</b> من صلاحيات البوت.

<code>يمكنك الإلغاء في أي وقت باستخدام /cancel</code>
    """
    
    await update.message.reply_html(help_text)
    return AWAITING_CHANNEL_FORWARD

# --- Permission Verification ---
async def _bot_has_required_rights(context: ContextTypes.DEFAULT_TYPE, channel_id: int) -> tuple[bool, str]:
    """التحقق من أن البوت لديه الصلاحيات المطلوبة في القناة"""
    try:
        # محاولة إرسال رسالة اختبار
        test_message = await context.bot.send_message(
            chat_id=channel_id,
            text="🔍 جاري التحقق من صلاحيات البوت... (هذه الرسالة ستُحذف تلقائياً)"
        )
        
        # محاولة حذف الرسالة
        await context.bot.delete_message(chat_id=channel_id, message_id=test_message.message_id)
        
        return True, "✅ الصلاحيات كافية"
        
    except Exception as e:
        error_msg = str(e).lower()
        
        if "chat not found" in error_msg:
            return False, "❌ البوت غير موجود في القناة"
        elif "not enough rights" in error_msg or "rights" in error_msg:
            return False, "❌ صلاحيات البوت غير كافية. تأكد من أنه مسؤول مع صلاحية 'نشر الرسائل'"
        elif "bot was blocked" in error_msg:
            return False, "❌ البوت محظور في القناة"
        else:
            return False, f"❌ خطأ غير متوقع: {e}"

# --- Linking Flow ---
@uow_transaction
@require_active_user
@require_analyst_user
async def received_channel_forward(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    """معالجة الرسالة المعاد توجيهها وربط القناة إذا كانت صالحة"""
    
    if await handle_linking_timeout(update, context):
        return ConversationHandler.END
        
    update_linking_activity(context)
    msg = update.message

    # الكشف عن القناة المصدر (متوافق مع API v7+)
    forwarded_from_chat = (
        getattr(msg, "forward_from_chat", None)
        or getattr(getattr(msg, "forward_origin", None), "chat", None)
        or getattr(msg, "sender_chat", None)
    )

    # التحقق من أن المصدر قناة
    is_from_channel = forwarded_from_chat and str(getattr(forwarded_from_chat, "id", 0)).startswith("-100")
    if not is_from_channel:
        await msg.reply_html(
            "❌ <b>هذه ليست رسالة من قناة</b>\n\n"
            "يرجى عرض رسالة من <b>قناة تليجرام</b> وليس مجموعة أو دردشة خاصة.\n"
            "يمكنك المحاولة مرة أخرى أو /cancel للإلغاء."
        )
        return AWAITING_CHANNEL_FORWARD

    chat_id = int(forwarded_from_chat.id)
    title = forwarded_from_chat.title or "بدون عنوان"
    username = getattr(forwarded_from_chat, 'username', None)

    # التحقق من أن القناة غير مربوطة مسبقاً
    repo = ChannelRepository(db_session)
    existing_channel = repo.find_by_telegram_id_and_analyst(channel_id=chat_id, analyst_id=db_user.id)
    if existing_channel:
        status = "✅ نشطة" if existing_channel.is_active else "❌ غير نشطة"
        await msg.reply_html(
            f"☑️ <b>القناة مربوطة بالفعل</b>\n\n"
            f"• <b>اسم القناة:</b> {title}\n"
            f"• <b>المعرف:</b> <code>{chat_id}</code>\n"
            f"• <b>الحالة:</b> {status}\n\n"
            f"استخدم /unlink_channel لفك الربط إذا needed."
        )
        return ConversationHandler.END

    # التحقق من صلاحيات البوت
    await msg.reply_html(f"⏳ <b>جاري التحقق من الصلاحيات في '{title}'...</b>")
    
    has_rights, rights_message = await _bot_has_required_rights(context, chat_id)
    
    if not has_rights:
        await msg.reply_html(
            f"❌ <b>فشل التحقق من الصلاحيات</b>\n\n"
            f"<b>القناة:</b> {title}\n"
            f"<b>الخطأ:</b> {rights_message}\n\n"
            f"يرجى التأكد من:\n"
            f"1. إضافة البوت كمسؤول في القناة\n"
            f"2. منحه صلاحية <b>نشر الرسائل</b>\n"
            f"3. إعادة تجربة عرض الرسالة\n\n"
            f"أو /cancel للإلغاء."
        )
        return AWAITING_CHANNEL_FORWARD

    # ربط القناة
    try:
        repo.add(
            analyst_id=db_user.id, 
            telegram_channel_id=chat_id, 
            username=username, 
            title=title
        )
        
        username_display = f"(@{username})" if username else "(قناة خاصة)"
        success_message = (
            f"✅ <b>تم ربط القناة بنجاح!</b>\n\n"
            f"• <b>اسم القناة:</b> {title}\n"
            f"• <b>المعرف:</b> <code>{chat_id}</code>\n"
            f"• <b>المستخدم:</b> {username_display}\n\n"
            f"يمكنك الآن نشر التوصيات في هذه القناة عبر نظام التوصيات."
        )
        
        await msg.reply_html(success_message)
        return ConversationHandler.END
        
    except Exception as e:
        log.error(f"Failed to link channel {chat_id}: {e}")
        await msg.reply_html(
            f"❌ <b>فشل ربط القناة</b>\n\n"
            f"حدث خطأ غير متوقع: {str(e)}\n"
            f"يرجى المحاولة مرة أخرى أو التواصل مع الدعم."
        )
        return AWAITING_CHANNEL_FORWARD

# --- Unlink Flow Entry ---
@uow_transaction
@require_active_user
@require_analyst_user
async def start_unlink_channel(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    """عرض قائمة القنوات المربوطة لفك الربط"""
    
    if await handle_linking_timeout(update, context):
        return ConversationHandler.END
        
    update_linking_activity(context)
    
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.message

    repo = ChannelRepository(db_session)
    channels = repo.list_by_analyst(db_user.id, only_active=False)

    if not channels:
        no_channels_text = "❌ <b>لا توجد قنوات مرتبطة بحسابك</b>\n\nاستخدم /link_channel لربط قناة جديدة."
        if query:
            await query.edit_message_text(no_channels_text, parse_mode="HTML")
        else:
            await message.reply_html(no_channels_text)
        return ConversationHandler.END

    keyboard = []
    for channel in channels:
        channel_name = f"{channel.title or 'بدون عنوان'}"
        if channel.username:
            channel_name += f" (@{channel.username})"
        else:
            channel_name += " (خاص)"
            
        status = "✅" if channel.is_active else "❌"
        channel_name = f"{status} {channel_name}"
        
        callback_data = f"confirm_unlink:{channel.telegram_channel_id}"
        keyboard.append([InlineKeyboardButton(channel_name, callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancel_unlink")])
    
    markup = InlineKeyboardMarkup(keyboard)
    
    text = """
<b>🔗 إدارة القنوات المربوطة</b>

اختر القناة التي تريد فك ربطها:
• ✅ = نشطة ومتاحة للنشر
• ❌ = غير نشطة

<code>سيتم إزالة القناة من قائمة النشر التلقائي</code>
    """
    
    if query:
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await message.reply_text(text, reply_markup=markup, parse_mode="HTML")
    
    return AWAIT_UNLINK_CONFIRM

# --- Handle Unlink Confirmation ---
@uow_transaction
@require_active_user
@require_analyst_user
async def confirm_unlink_channel(update: Update, context: ContextTypes.DEFAULT_TYPE, db_session, db_user, **kwargs) -> int:
    """معالجة تأكيد فك الربط وإزالة القناة"""
    query = update.callback_query
    await query.answer()
    
    if await handle_linking_timeout(update, context):
        return ConversationHandler.END
        
    update_linking_activity(context)

    if not query.data.startswith("confirm_unlink:"):
        await query.edit_message_text("❌ اختيار غير صالح.")
        return ConversationHandler.END

    channel_id = int(query.data.split(":", 1)[1])
    repo = ChannelRepository(db_session)
    channel = repo.find_by_telegram_id_and_analyst(channel_id, db_user.id)

    if not channel:
        await query.edit_message_text("⚠️ القناة غير موجودة أو غير مرتبطة بحسابك.")
        return ConversationHandler.END

    channel_title = channel.title or "بدون عنوان"
    channel_username = channel.username or "خاص"
    
    try:
        repo.delete(channel)
        
        success_text = (
            f"✅ <b>تم فك الربط بنجاح</b>\n\n"
            f"• <b>القناة:</b> {channel_title}\n"
            f"• <b>المستخدم:</b> @{channel_username}\n"
            f"• <b>المعرف:</b> <code>{channel_id}</code>\n\n"
            f"لم تعد هذه القناة متاحة للنشر التلقائي."
        )
        
        await query.edit_message_text(success_text, parse_mode="HTML")
        return ConversationHandler.END
        
    except Exception as e:
        log.error(f"Failed to unlink channel {channel_id}: {e}")
        await query.edit_message_text(
            f"❌ <b>فشل فك الربط</b>\n\nحدث خطأ غير متوقع: {str(e)}"
        )
        return ConversationHandler.END

# --- Cancel Unlink ---
async def cancel_unlink_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """إلغاء عملية فك الربط"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("❌ تم إلغاء عملية فك الربط.")
    else:
        await update.message.reply_text("❌ تم إلغاء عملية فك الربط.")
    
    clean_linking_state(context)
    return ConversationHandler.END

# --- Fallback / Cancel for Linking ---
async def cancel_link_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """إلغاء عملية الربط"""
    await update.message.reply_text("❌ تم إلغاء عملية ربط القناة.")
    clean_linking_state(context)
    return ConversationHandler.END

# --- Registration ---
def register_channel_linking_handler(app: Application):
    """تسجيل معالجات ربط وفك ربط القنوات"""
    
    # محادثة الربط
    link_conv = ConversationHandler(
        entry_points=[CommandHandler("link_channel", link_channel_entry)],
        states={
            AWAITING_CHANNEL_FORWARD: [
                MessageHandler(filters.FORWARDED & filters.ChatType.PRIVATE, received_channel_forward)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_link_handler)],
        name="channel_linking_conversation",
        persistent=False,
        per_user=True,
        per_chat=True,
        per_message=False,
        conversation_timeout=LINKING_TIMEOUT,
    )

    # محادثة فك الربط
    unlink_conv = ConversationHandler(
        entry_points=[
            CommandHandler("unlink_channel", start_unlink_channel),
            CallbackQueryHandler(start_unlink_channel, pattern=r"^admin:unlink_channel$")
        ],
        states={
            AWAIT_UNLINK_CONFIRM: [
                CallbackQueryHandler(confirm_unlink_channel, pattern=r"^confirm_unlink:"),
                CallbackQueryHandler(cancel_unlink_channel, pattern=r"^cancel_unlink$")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_unlink_channel)],
        name="unlink_channel_conversation",
        persistent=False,
        per_user=True,
        per_chat=True,
        per_message=False,
        conversation_timeout=LINKING_TIMEOUT,
    )

    app.add_handler(link_conv)
    app.add_handler(unlink_conv)
    
    log.info("✅ Channel linking handler registered successfully - PRODUCTION READY")

# التصديرات
__all__ = [
    'register_channel_linking_handler',
    'link_channel_entry',
    'received_channel_forward', 
    'start_unlink_channel',
    'confirm_unlink_channel',
    'cancel_link_handler',
    'cancel_unlink_channel'
]