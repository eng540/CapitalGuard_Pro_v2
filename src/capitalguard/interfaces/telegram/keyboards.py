# src/capitalguard/interfaces/telegram/keyboards.py (v19.1 - FINAL PRODUCTION FIXED)
"""
واجهة لوحات المفاتيح للتليجرام - الإصدار النهائي المعدل والمتين
إصلاح جذري لمشكلة Button_data_invalid مع الحفاظ على أفضل الممارسات
"""

import math
import logging
from typing import List, Iterable, Set, Optional, Any, Dict, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy
from capitalguard.application.services.price_service import PriceService
from capitalguard.interfaces.telegram.ui_texts import _pct

# ثوابت النظام
ITEMS_PER_PAGE = 8
MAX_BUTTON_TEXT_LENGTH = 40
MAX_CALLBACK_DATA_LENGTH = 64  # الحد الأقصى المسموح في تليجرام
logger = logging.getLogger(__name__)

class StatusIcons:
    """رموز الحالات المختلفة للتوصيات والصفقات"""
    PENDING = "⏳"
    ACTIVE = "▶️"
    BREAK_EVEN = "🛡️"
    PROFIT = "🟢"
    LOSS = "🔴"
    ERROR = "⚠️"
    SHADOW = "👻"
    CLOSED = "🔒"

class ButtonTexts:
    """نصوص الأزرار القياسية باللغة العربية"""
    BACK = "⬅️ العودة"
    BACK_TO_LIST = "⬅️ العودة للقائمة"
    BACK_TO_MAIN = "⬅️ العودة للوحة التحكم"
    CONFIRM = "✅ تأكيد"
    CANCEL = "❌ إلغاء"
    PREVIOUS = "⬅️ السابق"
    NEXT = "التالي ➡️"
    EDIT = "✏️ تعديل"
    UPDATE = "🔄 تحديث"
    CLOSE = "❌ إغلاق"
    SAVE = "💾 حفظ"
    DELETE = "🗑️ حذف"

class CallbackPrefixes:
    """بادئات مختصرة لبيانات الاستدعاء لتجنب تجاوز الحد المسموح"""
    # بادئات عامة
    POSITION = "pos"
    RECOMMENDATION = "rec"
    TRADE = "trd"
    PUBLISH_SELECT = "ps"
    OPEN_NAV = "onav"
    SETTINGS = "set"
    ADMIN = "adm"
    
    # إجراءات مختصرة
    SHOW = "sh"
    TOGGLE = "tg"
    NAVIGATE = "nav"
    CONFIRM = "cf"
    CANCEL = "cn"
    BACK = "bk"
    EDIT = "ed"
    UPDATE = "up"
    CLOSE = "cl"
    STRATEGY = "str"
    PARTIAL = "part"

def _validate_callback_data(callback_data: str) -> str:
    """
    التحقق من صحة callback_data وتقصيرها إذا تجاوزت الحد المسموح.
    يضمن عدم تجاوز 64 بايت مع الحفاظ على الوظيفة.
    """
    if len(callback_data) <= MAX_CALLBACK_DATA_LENGTH:
        return callback_data
    
    logger.warning(f"Callback data too long ({len(callback_data)} chars), truncating: {callback_data}")
    
    # تقصير مع الحفاظ على البنية الأساسية
    parts = callback_data.split(':')
    if len(parts) >= 3:
        # الحفاظ على البادئة والإجراء والمعرف الأساسي
        shortened = ':'.join(parts[:3])
        if len(shortened) <= MAX_CALLBACK_DATA_LENGTH - 10:
            # إضافة معلومات إضافية إذا كان هناك مساحة
            additional = ':'.join(parts[3:])[:8]
            return f"{shortened}:{additional}"
        return shortened[:MAX_CALLBACK_DATA_LENGTH]
    
    return callback_data[:MAX_CALLBACK_DATA_LENGTH]

def _build_callback_data(prefix: str, action: str, *args) -> str:
    """
    بناء callback_data بشكل آمن مع التحقق من الطول.
    """
    base_data = f"{prefix}:{action}"
    if args:
        base_data += ":" + ":".join(str(arg) for arg in args)
    
    return _validate_callback_data(base_data)

def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """
    الحصول على خاصية بشكل آمن مع دعم الكائنات والقواميس والقيم المتداخلة.
    - يدعم إرجاع .value إذا كانت الخاصية كائن Enum-like.
    """
    try:
        if hasattr(obj, attr):
            val = getattr(obj, attr)
            if hasattr(val, 'value'):
                return val.value
            return val
        elif isinstance(obj, dict) and attr in obj:
            return obj[attr]
        return default
    except Exception as e:
        logger.debug("خطأ في الحصول على الخاصية %s من %s: %s", attr, type(obj).__name__, e)
        return default

def _safe_get_display_id(item: Any) -> int:
    """الحصول على معرف العرض بشكل آمن (يدعم حقول بديلة مثل analyst_rec_id)"""
    display_id = _get_attr(item, "analyst_rec_id")
    if display_id is None:
        display_id = _get_attr(item, "id", 0)
    return display_id or 0

def _safe_get_asset(item: Any) -> str:
    """الحصول على اسم الأصل بشكل آمن"""
    asset = _get_attr(item, 'asset', 'UNKNOWN')
    if hasattr(asset, 'value'):
        return asset.value
    return str(asset)

def _safe_get_market(item: Any) -> str:
    """الحصول على السوق بشكل آمن"""
    market = _get_attr(item, 'market', 'Futures')
    return str(market)

def _truncate_text(text: str, max_length: int = MAX_BUTTON_TEXT_LENGTH) -> str:
    """تقصير النص إذا تجاوز الطول المسموح"""
    if len(text) <= max_length:
        return text
    return text[:max_length-3] + "..."

def _determine_status_icon(item: Any, live_price: Optional[float] = None) -> str:
    """تحديد الرمز المناسب لحالة العنصر"""
    try:
        status = _get_attr(item, 'status')
        side = _get_attr(item, 'side')
        entry = float(_get_attr(item, 'entry', 0))
        stop_loss = float(_get_attr(item, 'stop_loss', 0))
        
        # التحقق إذا كانت صفقة ظل
        is_shadow = _get_attr(item, 'is_shadow', False)
        if is_shadow:
            return StatusIcons.SHADOW
        
        # استخرج قيمة الحالة بشكل آمن
        status_value = status.value if hasattr(status, 'value') else status

        # حالة معلقة
        if status_value == RecommendationStatus.PENDING.value:
            return StatusIcons.PENDING

        # حالة نشطة - احسب PnL عند توفر السعر
        if status_value == RecommendationStatus.ACTIVE.value:
            if entry > 0 and stop_loss > 0 and abs(entry - stop_loss) < 0.0001:
                return StatusIcons.BREAK_EVEN
            if live_price is not None and entry > 0:
                pnl = _pct(entry, float(live_price), side)
                return StatusIcons.PROFIT if pnl >= 0 else StatusIcons.LOSS
            return StatusIcons.ACTIVE

        # دعم حالات صفقة المستخدم (قد تكون نصية أو Enums حسب النوع)
        if status_value in ['OPEN', 'CLOSED', 'OPEN.value', 'CLOSED.value']:
            if status_value == 'OPEN' and live_price is not None and entry > 0:
                pnl = _pct(entry, float(live_price), side)
                return StatusIcons.PROFIT if pnl >= 0 else StatusIcons.LOSS
            if status_value == 'CLOSED':
                return StatusIcons.CLOSED
            return StatusIcons.ACTIVE

        return StatusIcons.ACTIVE

    except Exception as e:
        logger.error("خطأ في تحديد رمز الحالة: %s", e, exc_info=True)
        return StatusIcons.ERROR

def _build_navigation_buttons(
    current_page: int, 
    total_pages: int, 
    callback_prefix: str,
    show_page_info: bool = True
) -> List[List[InlineKeyboardButton]]:
    """دالة مساعدة لبناء أزرار التنقل"""
    nav_buttons: List[List[InlineKeyboardButton]] = []
    page_nav_row: List[InlineKeyboardButton] = []
    
    if current_page > 1:
        page_nav_row.append(InlineKeyboardButton(
            ButtonTexts.PREVIOUS, 
            callback_data=_build_callback_data(callback_prefix, str(current_page - 1))
        ))
    
    if show_page_info and total_pages > 1:
        page_nav_row.append(InlineKeyboardButton(
            f"صفحة {current_page}/{total_pages}", 
            callback_data="noop"
        ))
    
    if current_page < total_pages:
        page_nav_row.append(InlineKeyboardButton(
            ButtonTexts.NEXT, 
            callback_data=_build_callback_data(callback_prefix, str(current_page + 1))
        ))
    
    if page_nav_row:
        nav_buttons.append(page_nav_row)
    
    return nav_buttons

async def build_open_recs_keyboard(
    items: List[Any],
    current_page: int,
    price_service: PriceService,
) -> InlineKeyboardMarkup:
    """بناء لوحة المفاتيح للصفقات المفتوحة مع دعم أنواع متعددة"""
    try:
        keyboard: List[List[InlineKeyboardButton]] = []
        total_items = len(items)
        total_pages = math.ceil(total_items / ITEMS_PER_PAGE) if total_items else 1
        start_index = (current_page - 1) * ITEMS_PER_PAGE
        paginated_items = items[start_index: start_index + ITEMS_PER_PAGE]

        # تجهيز طلبات الأسعار الجماعية
        price_requests: List[Tuple[str, str]] = []
        for item in paginated_items:
            asset = _safe_get_asset(item)
            market = _safe_get_market(item)
            price_requests.append((asset, market))
        
        # الحصول على الأسعار بشكل جماعي (إذا كانت الخدمة تدعمه)
        prices_map: Dict[str, Optional[float]] = {}
        try:
            if hasattr(price_service, 'get_batch_prices'):
                prices_list = await price_service.get_batch_prices(price_requests)
                # افترض أن get_batch_prices يعيد قائمة من الأسعار متوافقة بالترتيب
                prices_map = dict(zip([asset for asset, _ in price_requests], prices_list))
            else:
                # الرجوع إلى الطلبات الفردية
                for asset, market in price_requests:
                    price = await price_service.get_cached_price(asset, market)
                    prices_map[asset] = price
        except Exception as e:
            logger.warning("خطأ في جلب الأسعار: %s", e)

        for item in paginated_items:
            rec_id = _get_attr(item, 'id')
            asset = _safe_get_asset(item)
            side = _get_attr(item, 'side')
            entry = float(_get_attr(item, 'entry', 0))
            status = _get_attr(item, 'status')
            display_id = _safe_get_display_id(item)

            # بناء نص الزر
            button_text = f"#{display_id} - {asset} ({side})"
            live_price = prices_map.get(asset)
            status_icon = _determine_status_icon(item, live_price)
            
            # إضافة معلومات الربح/الخسارة أو الحالة
            status_value = status.value if hasattr(status, 'value') else status
            
            if (status_value in [RecommendationStatus.ACTIVE.value, 'ACTIVE', 'OPEN'] and 
                live_price is not None and entry > 0):
                pnl = _pct(entry, float(live_price), side)
                button_text = f"{status_icon} {button_text} | PnL: {pnl:+.2f}%"
            elif status_value in [RecommendationStatus.PENDING.value, 'PENDING']:
                button_text = f"{status_icon} {button_text} | معلق"
            elif status_value in ['CLOSED']:
                button_text = f"{status_icon} {button_text} | مغلق"
            else:
                button_text = f"{status_icon} {button_text} | نشط"

            # تحديد نوع العنصر وبناء callback_data المناسب
            is_trade = getattr(item, 'is_user_trade', False)
            item_type = 'trade' if is_trade else 'rec'
            callback_data = _build_callback_data(CallbackPrefixes.POSITION, CallbackPrefixes.SHOW, item_type, rec_id)

            keyboard.append([InlineKeyboardButton(
                _truncate_text(button_text), 
                callback_data=callback_data
            )])

        # إضافة أزرار التنقل
        nav_buttons = _build_navigation_buttons(current_page, total_pages, CallbackPrefixes.OPEN_NAV)
        keyboard.extend(nav_buttons)

        return InlineKeyboardMarkup(keyboard)

    except Exception as e:
        logger.error("خطأ في بناء لوحة الصفقات المفتوحة: %s", e, exc_info=True)
        # لوحة مفاتيح احتياطية في حالة الخطأ
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ خطأ في تحميل البيانات", callback_data="noop")],
            [InlineKeyboardButton("🔄 إعادة تحميل", callback_data=_build_callback_data(CallbackPrefixes.OPEN_NAV, "1"))]
        ])

def main_creation_keyboard() -> InlineKeyboardMarkup:
    """القائمة الرئيسية لاختيار طريقة إنشاء التوصية"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 المنشئ التفاعلي (/new)", callback_data="method_interactive")],
        [InlineKeyboardButton("⚡️ الأمر السريع (/rec)", callback_data="method_quick")],
        [InlineKeyboardButton("📋 المحرر النصي (/editor)", callback_data="method_editor")],
    ])

def public_channel_keyboard(rec_id: int, bot_username: str) -> InlineKeyboardMarkup:
    """بناء لوحة المفاتيح لرسالة القناة العامة"""
    buttons: List[InlineKeyboardButton] = []
    
    if bot_username:
        buttons.append(InlineKeyboardButton(
            "📊 تتبّع الإشارة", 
            url=f"https://t.me/{bot_username}?start=track_{rec_id}"
        ))
    
    buttons.append(InlineKeyboardButton(
        "🔄 تحديث البيانات الحية", 
        callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, CallbackPrefixes.UPDATE, "public", rec_id)
    ))

    return InlineKeyboardMarkup([buttons])

def analyst_control_panel_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    """بناء لوحة التحكم الديناميكية بناءً على حالة التوصية"""
    rec_id = rec.id
    
    if rec.status == RecommendationStatus.PENDING:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ إلغاء التوصية", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "cancel_pending", rec_id))],
            [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=_build_callback_data(CallbackPrefixes.OPEN_NAV, "1"))],
        ])
    
    # لوحة المفاتيح الافتراضية للتوصيات النشطة
    keyboard: List[List[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("🔄 تحديث السعر", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, CallbackPrefixes.UPDATE, "private", rec_id)),
            InlineKeyboardButton("✏️ تعديل", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "edit_menu", rec_id)),
        ],
        [
            InlineKeyboardButton("📈 استراتيجية الخروج", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "strategy_menu", rec_id)),
            InlineKeyboardButton("💰 إغلاق جزئي", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, CallbackPrefixes.PARTIAL, rec_id)),
        ],
        [InlineKeyboardButton("❌ إغلاق كلي", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "close_menu", rec_id))],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=_build_callback_data(CallbackPrefixes.OPEN_NAV, "1"))],
    ]
    
    return InlineKeyboardMarkup(keyboard)

def build_close_options_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """بناء لوحة خيارات الإغلاق"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 إغلاق بسعر السوق الآن", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "close_market", rec_id))],
        [InlineKeyboardButton("✍️ إغلاق بسعر محدد", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "close_manual", rec_id))],
        [InlineKeyboardButton(ButtonTexts.BACK, callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "back_to_main", rec_id))],
    ])

def analyst_edit_menu_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """بناء قائمة التعديل للمحلل"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛑 تعديل الوقف", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "edit_sl", rec_id)),
            InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "edit_tp", rec_id)),
        ],
        [
            InlineKeyboardButton("📊 تعديل سعر الدخول", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "edit_entry", rec_id)),
            InlineKeyboardButton("🏷️ تعديل الملاحظات", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "edit_notes", rec_id)),
        ],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "back_to_main", rec_id))],
    ])

def build_exit_strategy_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    """بناء لوحة استراتيجية الخروج"""
    rec_id = rec.id
    current_strategy = rec.exit_strategy
    
    # أزرار الاستراتيجية مع مؤشرات التحديد
    auto_close_text = "🎯 الإغلاق عند الهدف الأخير"
    if current_strategy == ExitStrategy.CLOSE_AT_FINAL_TP: 
        auto_close_text = f"✅ {auto_close_text}"
    
    manual_close_text = "✍️ الإغلاق اليدوي فقط"
    if current_strategy == ExitStrategy.MANUAL_CLOSE_ONLY: 
        manual_close_text = f"✅ {manual_close_text}"

    keyboard: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            auto_close_text, 
            callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, CallbackPrefixes.STRATEGY, rec_id, ExitStrategy.CLOSE_AT_FINAL_TP.value)
        )],
        [InlineKeyboardButton(
            manual_close_text, 
            callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, CallbackPrefixes.STRATEGY, rec_id, ExitStrategy.MANUAL_CLOSE_ONLY.value)
        )],
        [InlineKeyboardButton("🛡️ وضع/تعديل وقف الربح", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "set_profit_stop", rec_id))],
    ]
    
    # إضافة زر إزالة وقف الربح إذا كان موجوداً
    if getattr(rec, "profit_stop_price", None) is not None:
        keyboard.append([InlineKeyboardButton(
            "🗑️ إزالة وقف الربح", 
            callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "remove_profit_stop", rec_id)
        )])
        
    keyboard.append([InlineKeyboardButton(
        ButtonTexts.BACK_TO_MAIN, 
        callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "back_to_main", rec_id)
    )])
    
    return InlineKeyboardMarkup(keyboard)

def confirm_close_keyboard(rec_id: int, exit_price: float) -> InlineKeyboardMarkup:
    """بناء لوحة تأكيد الإغلاق"""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ تأكيد الإغلاق", 
            callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "confirm_close", rec_id, f"{exit_price:.8f}")
        ),
        InlineKeyboardButton(
            "❌ تراجع", 
            callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "cancel_close", rec_id)
        ),
    ]])

def asset_choice_keyboard(recent_assets: List[str]) -> InlineKeyboardMarkup:
    """بناء لوحة اختيار الأصل"""
    if not recent_assets:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✍️ اكتب أصلاً جديدًا", callback_data="asset_new")
        ]])
    
    buttons = [InlineKeyboardButton(asset, callback_data=f"asset_{asset}") for asset in recent_assets]
    keyboard_layout: List[List[InlineKeyboardButton]] = [buttons[i: i + 3] for i in range(0, len(buttons), 3)]
    keyboard_layout.append([
        InlineKeyboardButton("✍️ اكتب أصلاً جديدًا", callback_data="asset_new")
    ])
    return InlineKeyboardMarkup(keyboard_layout)

def side_market_keyboard(current_market: str = "Futures") -> InlineKeyboardMarkup:
    """بناء لوحة اختيار الاتجاه والسوق"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🟢 LONG / {current_market}", callback_data=f"side_LONG"),
            InlineKeyboardButton(f"🔴 SHORT / {current_market}", callback_data=f"side_SHORT"),
        ],
        [InlineKeyboardButton(
            f"🔄 تغيير السوق (الحالي: {current_market})", 
            callback_data="change_market_menu"
        )],
    ])

def market_choice_keyboard() -> InlineKeyboardMarkup:
    """بناء لوحة اختيار السوق"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📈 Futures", callback_data="market_Futures"), 
            InlineKeyboardButton("💎 Spot", callback_data="market_Spot")
        ],
        [InlineKeyboardButton(ButtonTexts.BACK, callback_data="market_back")],
    ])

def order_type_keyboard() -> InlineKeyboardMarkup:
    """بناء لوحة اختيار نوع الطلب"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Market (دخول فوري)", callback_data="type_MARKET")],
        [InlineKeyboardButton("🎯 Limit (انتظار سعر أفضل)", callback_data="type_LIMIT")],
        [InlineKeyboardButton("🚨 Stop Market (دخول بعد اختراق)", callback_data="type_STOP_MARKET")],
    ])

def review_final_keyboard(review_token: str) -> InlineKeyboardMarkup:
    """بناء لوحة المراجعة النهائية"""
    # استخدام token مختصر للتقليل من الطول
    short_token = review_token[:12]
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نشر في القنوات الفعّالة", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "publish", short_token))],
        [
            InlineKeyboardButton("📢 اختيار القنوات", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "choose_channels", short_token)),
            InlineKeyboardButton("📝 إضافة/تعديل ملاحظات", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "add_notes", short_token)),
        ],
        [
            InlineKeyboardButton("✏️ تعديل البيانات", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "edit_data", short_token)),
            InlineKeyboardButton("👁️ معاينة", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "preview", short_token)),
        ],
        [InlineKeyboardButton("❌ إلغاء", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "cancel", short_token))],
    ])

def build_channel_picker_keyboard(
    review_token: str,
    channels: Iterable[dict],
    selected_ids: Set[int],
    page: int = 1,
    per_page: int = 5,
) -> InlineKeyboardMarkup:
    """بناء لوحة اختيار القنوات مع الترقيم - الإصدار المعدل"""
    ch_list = list(channels)
    total = len(ch_list)
    page = max(page, 1)
    start = (page - 1) * per_page
    end = start + per_page
    page_items = ch_list[start:end]

    rows: List[List[InlineKeyboardButton]] = []

    # استخدام token مختصر
    short_token = review_token[:10]
    
    # أزرار اختيار القنوات
    for ch in page_items:
        tg_chat_id = int(_get_attr(ch, 'telegram_channel_id', 0))
        label = _get_attr(ch, 'title') or (
            f"@{_get_attr(ch, 'username')}" if _get_attr(ch, 'username') else str(tg_chat_id)
        )
        mark = "✅" if tg_chat_id in selected_ids else "☑️"
        
        # استخدام callback_data قصيرة ومضمونة
        callback_data = _build_callback_data(CallbackPrefixes.PUBLISH_SELECT, CallbackPrefixes.TOGGLE, short_token, tg_chat_id, page)
        
        rows.append([InlineKeyboardButton(
            f"{mark} {_truncate_text(label)}", 
            callback_data=callback_data
        )])

    # التنقل مع callback_data قصيرة
    max_page = max(1, math.ceil(total / per_page))
    nav_buttons = _build_navigation_buttons(page, max_page, f"{CallbackPrefixes.PUBLISH_SELECT}:{CallbackPrefixes.NAVIGATE}:{short_token}")
    rows.extend(nav_buttons)

    # أزرار الإجراءات مع callback_data قصيرة
    rows.append([
        InlineKeyboardButton("🚀 نشر المحدد", callback_data=_build_callback_data(CallbackPrefixes.PUBLISH_SELECT, CallbackPrefixes.CONFIRM, short_token)),
        InlineKeyboardButton(ButtonTexts.BACK, callback_data=_build_callback_data(CallbackPrefixes.PUBLISH_SELECT, CallbackPrefixes.BACK, short_token)),
    ])

    return InlineKeyboardMarkup(rows)

# ... باقي الدوال بنفس المنطق مع استخدام _build_callback_data

def build_subscription_keyboard(channel_link: Optional[str]) -> Optional[InlineKeyboardMarkup]:
    """بناء لوحة الاشتراك إذا كان رابط القناة متوفراً"""
    if channel_link:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("➡️ الانضمام للقناة", url=channel_link)
        ]])
    return None

def build_signal_tracking_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """بناء لوحة تتبع الإشارة"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔔 نبهني عند الهدف الأول", callback_data=_build_callback_data("track", "notify_tp1", rec_id)),
            InlineKeyboardButton("🔔 نبهني عند وقف الخسارة", callback_data=_build_callback_data("track", "notify_sl", rec_id))
        ],
        [
            InlineKeyboardButton("🎯 نبهني عند جميع الأهداف", callback_data=_build_callback_data("track", "notify_all_tp", rec_id)),
            InlineKeyboardButton("📊 إحصائيات الأداء", callback_data=_build_callback_data("track", "stats", rec_id))
        ],
        [
            InlineKeyboardButton("➕ أضف إلى محفظتي", callback_data=_build_callback_data("track", "add_portfolio", rec_id)),
            InlineKeyboardButton("📋 تفاصيل الصفقة", callback_data=_build_callback_data("track", "details", rec_id))
        ]
    ])

def build_user_trade_control_keyboard(trade_id: int) -> InlineKeyboardMarkup:
    """بناء لوحة تحكم صفقة المستخدم"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 تحديث السعر", callback_data=_build_callback_data(CallbackPrefixes.TRADE, CallbackPrefixes.UPDATE, trade_id)),
            InlineKeyboardButton("✏️ تعديل", callback_data=_build_callback_data(CallbackPrefixes.TRADE, CallbackPrefixes.EDIT, trade_id)),
        ],
        [
            InlineKeyboardButton("📊 تفاصيل الأداء", callback_data=_build_callback_data(CallbackPrefixes.TRADE, "performance", trade_id)),
            InlineKeyboardButton("❌ إغلاق الصفقة", callback_data=_build_callback_data(CallbackPrefixes.TRADE, CallbackPrefixes.CLOSE, trade_id)),
        ],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=_build_callback_data(CallbackPrefixes.OPEN_NAV, "1"))],
    ])

def build_confirmation_keyboard(
    action: str, 
    item_id: int, 
    confirm_text: str = "✅ تأكيد",
    cancel_text: str = "❌ إلغاء"
) -> InlineKeyboardMarkup:
    """بناء لوحة تأكيد عامة"""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(confirm_text, callback_data=_build_callback_data(action, CallbackPrefixes.CONFIRM, item_id)),
        InlineKeyboardButton(cancel_text, callback_data=_build_callback_data(action, CallbackPrefixes.CANCEL, item_id)),
    ]])

def build_settings_keyboard() -> InlineKeyboardMarkup:
    """بناء لوحة الإعدادات"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 إعدادات التنبيهات", callback_data=_build_callback_data(CallbackPrefixes.SETTINGS, "alerts"))],
        [InlineKeyboardButton("📊 إعدادات التقارير", callback_data=_build_callback_data(CallbackPrefixes.SETTINGS, "reports"))],
        [InlineKeyboardButton("🌐 إعدادات اللغة", callback_data=_build_callback_data(CallbackPrefixes.SETTINGS, "language"))],
        [InlineKeyboardButton("⚙️ إعدادات متقدمة", callback_data=_build_callback_data(CallbackPrefixes.SETTINGS, "advanced"))],
        [InlineKeyboardButton(ButtonTexts.BACK, callback_data=_build_callback_data(CallbackPrefixes.SETTINGS, CallbackPrefixes.BACK))],
    ])

def build_quick_actions_keyboard() -> InlineKeyboardMarkup:
    """بناء لوحة الإجراءات السريعة"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📈 صفقاتي", callback_data=_build_callback_data("quick", "my_trades")),
            InlineKeyboardButton("📊 الإحصائيات", callback_data=_build_callback_data("quick", "stats")),
        ],
        [
            InlineKeyboardButton("⚡ توصية سريعة", callback_data=_build_callback_data("quick", "new_trade")),
            InlineKeyboardButton("🔍 استكشاف", callback_data=_build_callback_data("quick", "explore")),
        ],
        [
            InlineKeyboardButton("🆘 المساعدة", callback_data=_build_callback_data("quick", "help")),
            InlineKeyboardButton("⚙️ الإعدادات", callback_data=_build_callback_data("quick", "settings")),
        ]
    ])

def build_admin_panel_keyboard() -> InlineKeyboardMarkup:
    """بناء لوحة تحكم المشرف"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 إحصائيات النظام", callback_data=_build_callback_data(CallbackPrefixes.ADMIN, "stats"))],
        [InlineKeyboardButton("👥 إدارة المستخدمين", callback_data=_build_callback_data(CallbackPrefixes.ADMIN, "users"))],
        [InlineKeyboardButton("📢 إدارة القنوات", callback_data=_build_callback_data(CallbackPrefixes.ADMIN, "channels"))],
        [InlineKeyboardButton("🔔 الإشعارات النظامية", callback_data=_build_callback_data(CallbackPrefixes.ADMIN, "notifications"))],
        [InlineKeyboardButton("📈 أداء المحللين", callback_data=_build_callback_data(CallbackPrefixes.ADMIN, "analysts"))],
        [InlineKeyboardButton("🚪 العودة", callback_data=_build_callback_data(CallbackPrefixes.ADMIN, CallbackPrefixes.BACK))],
    ])

def build_trader_dashboard_keyboard() -> InlineKeyboardMarkup:
    """بناء لوحة تحكم المتداول"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 صفقاتي المفتوحة", callback_data=_build_callback_data("trader", "open_trades")),
            InlineKeyboardButton("📈 أداء المحفظة", callback_data=_build_callback_data("trader", "portfolio")),
        ],
        [
            InlineKeyboardButton("🔔 متابعة إشارة", callback_data=_build_callback_data("trader", "track_signal")),
            InlineKeyboardButton("📋 سجل الصفقات", callback_data=_build_callback_data("trader", "trade_history")),
        ],
        [
            InlineKeyboardButton("⚡ صفقة سريعة", callback_data=_build_callback_data("trader", "quick_trade")),
            InlineKeyboardButton("⚙️ إعداداتي", callback_data=_build_callback_data("trader", "settings")),
        ]
    ])

def build_trade_edit_keyboard(trade_id: int) -> InlineKeyboardMarkup:
    """بناء لوحة تعديل الصفقة الشخصية"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛑 تعديل الوقف", callback_data=_build_callback_data(CallbackPrefixes.TRADE, "edit_sl", trade_id)),
            InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=_build_callback_data(CallbackPrefixes.TRADE, "edit_tp", trade_id)),
        ],
        [
            InlineKeyboardButton("📊 تعديل سعر الدخول", callback_data=_build_callback_data(CallbackPrefixes.TRADE, "edit_entry", trade_id)),
            InlineKeyboardButton("🏷️ تعديل الملاحظات", callback_data=_build_callback_data(CallbackPrefixes.TRADE, "edit_notes", trade_id)),
        ],
        [InlineKeyboardButton(ButtonTexts.BACK, callback_data=_build_callback_data(CallbackPrefixes.POSITION, CallbackPrefixes.SHOW, "trade", trade_id))],
    ])

def build_partial_close_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """بناء لوحة إغلاق جزئي محايدة"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 إغلاق 25%", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, CallbackPrefixes.PARTIAL, rec_id, "25"))],
        [InlineKeyboardButton("💰 إغلاق 50%", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, CallbackPrefixes.PARTIAL, rec_id, "50"))],
        [InlineKeyboardButton("💰 إغلاق 75%", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, CallbackPrefixes.PARTIAL, rec_id, "75"))],
        [InlineKeyboardButton("✍️ نسبة مخصصة", callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "partial_close_custom", rec_id))],
        [InlineKeyboardButton(ButtonTexts.BACK, callback_data=_build_callback_data(CallbackPrefixes.RECOMMENDATION, "back_to_main", rec_id))],
    ])

def build_analyst_dashboard_keyboard() -> InlineKeyboardMarkup:
    """بناء لوحة تحكم المحلل"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 توصياتي النشطة", callback_data=_build_callback_data("analyst", "open_recs")),
            InlineKeyboardButton("📈 أداء التوصيات", callback_data=_build_callback_data("analyst", "performance")),
        ],
        [
            InlineKeyboardButton("💬 توصية جديدة", callback_data=_build_callback_data("analyst", "new_recommendation")),
            InlineKeyboardButton("📋 سجل التوصيات", callback_data=_build_callback_data("analyst", "rec_history")),
        ],
        [
            InlineKeyboardButton("📢 إدارة القنوات", callback_data=_build_callback_data("analyst", "manage_channels")),
            InlineKeyboardButton("⚙️ إعدادات المحلل", callback_data=_build_callback_data("analyst", "settings")),
        ]
    ])

# تصدير الدوال الرئيسية
__all__ = [
    'build_open_recs_keyboard',
    'main_creation_keyboard', 
    'public_channel_keyboard',
    'analyst_control_panel_keyboard',
    'build_close_options_keyboard',
    'analyst_edit_menu_keyboard',
    'build_exit_strategy_keyboard',
    'confirm_close_keyboard',
    'asset_choice_keyboard',
    'side_market_keyboard',
    'market_choice_keyboard',
    'order_type_keyboard',
    'review_final_keyboard',
    'build_channel_picker_keyboard',
    'build_subscription_keyboard',
    'build_signal_tracking_keyboard',
    'build_user_trade_control_keyboard',
    'build_confirmation_keyboard',
    'build_settings_keyboard',
    'build_quick_actions_keyboard',
    'build_admin_panel_keyboard',
    'build_trader_dashboard_keyboard',
    'build_trade_edit_keyboard',
    'build_partial_close_keyboard',
    'build_analyst_dashboard_keyboard',
    'StatusIcons',
    'ButtonTexts',
    'CallbackPrefixes'
]