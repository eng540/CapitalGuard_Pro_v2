# --- START OF FILE: src/capitalguard/interfaces/telegram/commands.py ---
import io
import csv
import logging
from typing import Optional, Tuple

from telegram import Update, InputFile, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, ContextTypes, CommandHandler
from telegram.error import BadRequest

from .helpers import get_service
from .auth import ALLOWED_USER_FILTER
from .ui_texts import build_analyst_stats_text
from .keyboards import build_open_recs_keyboard

from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.price_service import PriceService

# ✅ نحتاج DB هنا لأوامر إدارة القنوات
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.infrastructure.db.repository import UserRepository, ChannelRepository

log = logging.getLogger(__name__)

# Conversation steps (إن كنت تستخدم محادثة إنشاء التوصية)
(CHOOSE_METHOD, QUICK_COMMAND, TEXT_EDITOR) = range(3)
(I_ASSET_CHOICE, I_SIDE_MARKET, I_ORDER_TYPE, I_PRICES, I_NOTES, I_REVIEW) = range(3, 9)
USER_PREFERENCE_KEY = "preferred_creation_method"


def main_creation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 المنشئ التفاعلي", callback_data="method_interactive")],
        [InlineKeyboardButton("⚡️ الأمر السريع", callback_data="method_quick")],
        [InlineKeyboardButton("📋 المحرر النصي", callback_data="method_editor")],
    ])


def change_method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ تغيير طريقة الإدخال", callback_data="change_method")]])


async def newrec_entry_point(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    preferred_method = context.user_data.get(USER_PREFERENCE_KEY)
    if preferred_method == "interactive":
        await update.message.reply_text(
            "🚀 سنبدأ المُنشئ التفاعلي.\n(اختر الأصل من الأزرار أو اكتب الرمز مباشرة)",
            reply_markup=change_method_keyboard()
        )
        return CHOOSE_METHOD
    if preferred_method == "quick":
        await update.message.reply_text(
            "⚡️ وضع الأمر السريع.\n\n"
            "أرسل توصيتك برسالة واحدة تبدأ بـ /rec\n"
            "مثال: /rec BTCUSDT LONG 65000 64000 66k",
            reply_markup=change_method_keyboard()
        )
        return QUICK_COMMAND
    if preferred_method == "editor":
        await update.message.reply_text(
            "📋 وضع المحرّر النصي.\n\n"
            "ألصق توصيتك بشكل حقول:\n"
            "Asset: BTCUSDT\nSide: LONG\nEntry: 65000\nStop: 64000\nTargets: 66k 68k",
            reply_markup=change_method_keyboard()
        )
        return TEXT_EDITOR

    await update.message.reply_text(
        "🚀 إنشاء توصية جديدة.\n\nاختر طريقتك المفضلة للإدخال:",
        reply_markup=main_creation_keyboard()
    )
    return CHOOSE_METHOD


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # عبر ALLOWED_USER_FILTER سيتم إنشاء سجل المستخدم تلقائياً إن لم يكن موجوداً
    await update.message.reply_html("👋 أهلاً بك في <b>CapitalGuard Bot</b>.\nاستخدم /help للمساعدة.")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(
        "<b>Available Commands:</b>\n\n"
        "• <code>/newrec</code> — إنشاء توصية جديدة (حفظ فقط افتراضيًا، مع خيار النشر لاحقًا).\n"
        "• <code>/open [filter]</code> — عرض توصياتك المفتوحة (btc/long/short/pending/active).\n"
        "• <code>/stats</code> — ملخّص أدائك الشخصي.\n"
        "• <code>/export</code> — تصدير توصياتك.\n"
        "• <code>/settings</code> — إدارة التفضيلات.\n"
        "• <code>/link_channel @YourChannel</code> — ربط قناة عامة (أنت والبوت مسؤولان).\n"
        "• <code>/channels</code> — عرض قنواتك المرتبطة وحالتها.\n"
        "• <code>/toggle_channel &lt;@username|chat_id&gt;</code> — تفعيل/تعطيل قناة.\n"
        "• <code>/unlink_channel &lt;@username|chat_id&gt;</code> — فك ربط قناة."
    )


async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    عرض توصيات المستخدم المفتوحة مع دعم الفلاتر من وسيطات الأمر.
    الفلاتر المدعومة:
      - الرمز: btc / eth ... (مطابقة جزئية)
      - الاتجاه: long / short
      - الحالة: pending / active
    """
    trade_service: TradeService = get_service(context, "trade_service")
    price_service: PriceService = get_service(context, "price_service")
    user_telegram_id = update.effective_user.id

    # Parse filters from command arguments
    filters = {}
    filter_text_parts = []
    if context.args:
        for arg in context.args:
            a = arg.strip().lower()
            if a in ("long", "short"):
                filters["side"] = a
                filter_text_parts.append(f"الاتجاه: {a.upper()}")
            elif a in ("pending", "active"):
                filters["status"] = a
                filter_text_parts.append(f"الحالة: {a.upper()}")
            else:
                filters["symbol"] = a
                filter_text_parts.append(f"الرمز: {a.upper()}")

    # Save the filter for pagination (إن كان لديك تنقّل صفحات)
    context.user_data["last_open_filters"] = filters

    # ✅ استعلام مقيّد بالمستخدم (بالـ Telegram ID) — لا نحتاج db_user_id
    items = trade_service.repo.list_open_for_user(
        user_telegram_id,
        symbol=filters.get("symbol"),
        side=filters.get("side"),
        status=filters.get("status"),
    )

    if not items:
        await update.message.reply_text("✅ لا توجد توصيات مفتوحة تطابق الفلتر الحالي.")
    else:
        keyboard = build_open_recs_keyboard(items, current_page=1, price_service=price_service)

        header_text = "<b>📊 لوحة قيادة توصياتك المفتوحة</b>"
        if filter_text_parts:
            header_text += f"\n<i>فلترة حسب: {', '.join(filter_text_parts)}</i>"

        await update.message.reply_html(
            f"{header_text}\nاختر توصية لعرض لوحة التحكم الخاصة بها:",
            reply_markup=keyboard
        )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ملخص أداء المستخدم الشخصي."""
    analytics_service: AnalyticsService = get_service(context, "analytics_service")
    user_id_str = str(update.effective_user.id)
    stats = analytics_service.performance_summary_for_user(user_id_str)
    text = build_analyst_stats_text(stats)
    await update.message.reply_html(text)


async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تصدير توصيات المستخدم فقط إلى CSV (مقيّدة بالمستخدم)."""
    await update.message.reply_text("جاري تجهيز ملف التصدير...")
    trade_service: TradeService = get_service(context, "trade_service")
    user_telegram_id = update.effective_user.id

    all_recs = trade_service.repo.list_all_for_user(user_telegram_id)
    if not all_recs:
        await update.message.reply_text("لا توجد بيانات للتصدير.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    header = [
        "id","asset","side","status","market","entry_price","stop_loss",
        "targets","exit_price","notes","created_at","closed_at"
    ]
    writer.writerow(header)
    for rec in all_recs:
        row = [
            rec.id,
            rec.asset.value,
            rec.side.value,
            rec.status.value,
            rec.market,
            rec.entry.value,
            rec.stop_loss.value,
            ", ".join(map(str, rec.targets.values)),
            rec.exit_price,
            rec.notes,
            rec.created_at.strftime('%Y-%m-%d %H:%M:%S') if rec.created_at else "",
            rec.closed_at.strftime('%Y-%m-%d %H:%M:%S') if rec.closed_at else ""
        ]
        writer.writerow(row)

    output.seek(0)
    bytes_buffer = io.BytesIO(output.getvalue().encode("utf-8"))
    csv_file = InputFile(bytes_buffer, filename="capitalguard_export.csv")
    await update.message.reply_document(document=csv_file, caption="تم إنشاء التصدير.")


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "⚙️ الإعدادات\n\n"
        "اختر طريقتك المفضلة للوضع الافتراضي لأمر /newrec:",
        reply_markup=main_creation_keyboard()
    )
    return CHOOSE_METHOD


# -----------------------------
# أدوات مساعدة لأوامر القنوات
# -----------------------------
def _parse_channel_ref(raw: str) -> Tuple[Optional[int], Optional[str]]:
    """
    يقبل:
      - @username أو username  → يرجع (None, username_without_at)
      - chat_id (int)          → يرجع (chat_id, None)
    """
    s = (raw or "").strip()
    if not s:
        return None, None
    if s.startswith("@"):
        return None, s[1:]
    # محاولة تفسيره كـ chat_id
    try:
        return int(s), None
    except ValueError:
        # ربما بدون @
        return None, s


async def _get_current_user(session, user_tg_id: int):
    user_repo = UserRepository(session)
    return user_repo.find_or_create(telegram_id=user_tg_id)


# ✅ جديد: أمر ربط القناة العامة عبر @username
async def link_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يربط قناة عامة بالحساب الحالي:
    الشروط:
      - أن يكون البوت Admin في القناة
      - أن يكون المستخدم مُرسل الأمر Admin/Creator في القناة
    الاستخدام:
      /link_channel @YourChannelUsername
    """
    user_tg_id = int(update.effective_user.id)

    # تعليمات الاستخدام إن لم تُمرَّر وسيطة
    if not context.args:
        await update.message.reply_html(
            "<b>طريقة الاستخدام:</b>\n"
            "1) أضف هذا البوت كمسؤول في قناتك العامة مع صلاحية النشر.\n"
            "2) أرسل: <code>/link_channel @اسم_القناة</code>\n"
            "مثال: <code>/link_channel @MySignalChannel</code>\n\n"
            "💡 للربط عبر إعادة التوجيه (يدعم القنوات الخاصة والعامة)، استخدم التدفق الموحّد في محادثة الإدارة."
        )
        return

    raw = context.args[0].strip()
    # نقبل @name أو name
    channel_username_display = raw if raw.startswith("@") else f"@{raw}"
    channel_username_store = channel_username_display.lstrip("@")

    await update.message.reply_text(f"⏳ جارِ محاولة ربط {channel_username_display} ...")

    try:
        # جلب هوية البوت مرة واحدة
        me = await context.bot.get_me()

        # التحقق: البوت والمستخدم Admin في القناة
        admins = await context.bot.get_chat_administrators(chat_id=channel_username_display)
        bot_is_admin = any(a.user.id == me.id for a in admins)
        user_is_admin = any(a.user.id == user_tg_id for a in admins)

        if not bot_is_admin:
            await update.message.reply_text(f"❌ فشل: البوت ليس مسؤولاً في {channel_username_display}.")
            return
        if not user_is_admin:
            await update.message.reply_text(f"❌ فشل: لا تبدو مديرًا في {channel_username_display}.")
            return

        # الحصول على معرّف القناة الحقيقي
        channel_chat = await context.bot.get_chat(chat_id=channel_username_display)
        channel_id = int(channel_chat.id)

        # حفظ القناة في قاعدة البيانات
        with SessionLocal() as session:
            user = await _get_current_user(session, user_tg_id)
            channel_repo = ChannelRepository(session)

            try:
                channel_repo.add(
                    user_id=user.id,
                    telegram_channel_id=channel_id,
                    username=channel_username_store,  # نخزن بدون @
                    title=getattr(channel_chat, "title", None),
                )
            except Exception as e:
                msg = str(e)
                if "unique" in msg.lower() or "already" in msg.lower() or "exists" in msg.lower():
                    await update.message.reply_text(
                        f"ℹ️ القناة {channel_username_display} مرتبطة مسبقًا. "
                        f"إن كانت مملوكة بحساب آخر، يرجى فك ارتباطها هناك أولاً."
                    )
                    return
                raise

        await update.message.reply_text(f"✅ تم ربط القناة {channel_username_display} بحسابك.")

    except BadRequest as e:
        await update.message.reply_text(
            f"❌ خطأ من تيليجرام: {e.message}.\n"
            f"تأكد أن القناة عامة وأن اسم المستخدم صحيح، وأن البوت مُضاف كمسؤول."
        )
    except ValueError as e:
        await update.message.reply_text(f"❌ خطأ: {e}")
    except Exception as e:
        log.exception("Error during channel linking")
        await update.message.reply_text(f"❌ حدث خطأ غير متوقع: {e}")


# ✅ جديد: عرض قنوات المستخدم
async def channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_tg_id = int(update.effective_user.id)
    with SessionLocal() as session:
        user = await _get_current_user(session, user_tg_id)
        channel_repo = ChannelRepository(session)
        channels = channel_repo.list_by_user(user.id, only_active=False) or []

    if not channels:
        await update.message.reply_text(
            "📭 لا توجد قنوات مرتبطة بحسابك.\n"
            "اربط قناة عامة عبر: /link_channel @YourChannel\n"
            "أو استخدم تدفق إعادة التوجيه لربط قناة خاصة."
        )
        return

    lines = ["<b>📡 قنواتك المرتبطة</b>"]
    for ch in channels:
        uname = f"@{ch.username}" if getattr(ch, "username", None) else "—"
        title = getattr(ch, "title", None) or "—"
        status = "✅ فعّالة" if ch.is_active else "⏸️ معطّلة"
        lines.append(f"• <b>{title}</b> ({uname} / <code>{ch.telegram_channel_id}</code>) — {status}")

    lines.append("\nℹ️ للتحكم السريع:")
    lines.append("— تفعيل/تعطيل: <code>/toggle_channel &lt;@username|chat_id&gt;</code>")
    lines.append("— فك الربط: <code>/unlink_channel &lt;@username|chat_id&gt;</code>")

    await update.message.reply_html("\n".join(lines))


# ✅ جديد: تفعيل/تعطيل قناة
async def toggle_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❗️الاستخدام: /toggle_channel <@username|chat_id>")
        return

    user_tg_id = int(update.effective_user.id)
    chat_id, uname = _parse_channel_ref(context.args[0])

    with SessionLocal() as session:
        user = await _get_current_user(session, user_tg_id)
        channel_repo = ChannelRepository(session)

        # إيجاد القناة المملوكة للمستخدم فقط
        ch = None
        if chat_id is not None:
            ch = channel_repo.find_by_chat_id_for_user(user.id, chat_id)
        elif uname:
            ch = channel_repo.find_by_username_for_user(user.id, uname)

        if not ch:
            await update.message.reply_text("❌ لم يتم العثور على القناة ضمن حسابك.")
            return

        new_state = not ch.is_active
        channel_repo.set_active(ch.id, user.id, new_state)

    await update.message.reply_text(
        f"✅ تم {'تفعيل' if new_state else 'تعطيل'} القناة بنجاح."
    )


# ✅ جديد: فك الربط
async def unlink_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❗️الاستخدام: /unlink_channel <@username|chat_id>")
        return

    user_tg_id = int(update.effective_user.id)
    chat_id, uname = _parse_channel_ref(context.args[0])

    with SessionLocal() as session:
        user = await _get_current_user(session, user_tg_id)
        channel_repo = ChannelRepository(session)

        # إيجاد القناة المملوكة للمستخدم فقط
        ch = None
        if chat_id is not None:
            ch = channel_repo.find_by_chat_id_for_user(user.id, chat_id)
        elif uname:
            ch = channel_repo.find_by_username_for_user(user.id, uname)

        if not ch:
            await update.message.reply_text("❌ لم يتم العثور على القناة ضمن حسابك.")
            return

        channel_repo.remove(ch.id, user.id)

    await update.message.reply_text("🗑️ تم فك ربط القناة من حسابك.")

    # تلميح: إن أردت الاحتفاظ بالربط مع إيقاف النشر، استخدم /toggle_channel بدلًا من الحذف.


def register_commands(app: Application):
    # نمرر فلتر قاعدة البيانات لضمان إنشاء/التحقق من المستخدم قبل كل أمر
    app.add_handler(CommandHandler("start", start_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("help", help_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("open", open_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("stats", stats_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("export", export_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("settings", settings_cmd, filters=ALLOWED_USER_FILTER))

    # ✅ إدارة القنوات
    app.add_handler(CommandHandler("link_channel", link_channel_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("channels", channels_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("toggle_channel", toggle_channel_cmd, filters=ALLOWED_USER_FILTER))
    app.add_handler(CommandHandler("unlink_channel", unlink_channel_cmd, filters=ALLOWED_USER_FILTER))
# --- END OF FILE: src/capitalguard/interfaces/telegram/commands.py ---