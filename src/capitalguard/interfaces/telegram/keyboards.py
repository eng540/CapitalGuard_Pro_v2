# src/capitalguard/interfaces/telegram/keyboards.py (v21.3 - Production Ready & Final)
"""
هندسة لوحات المفاتيح المستدامة - إصدار إنتاجي متكامل ونهائي
✅ إصلاح حاسم: إضافة استيراد 'Enum' المفقود الذي كان يمنع بدء تشغيل التطبيق.
✅ إصلاح جميع مشاكل الأداء والتوافق (بما في ذلك تجميد لوحة القنوات).
✅ تحسين استجابة الأزرار والواجهات عبر بنية CallbackBuilder الموحدة.
✅ دعم كامل لنظام اختيار القنوات مع ترقيم الصفحات.
✅ منطق عرض ديناميكي للحالات (ربح/خسارة/معلق).
✅ آليات أمان لمنع النقرات من الجلسات القديمة.
"""

import math
import logging
from decimal import Decimal
from typing import List, Iterable, Set, Optional, Any, Dict, Tuple, Union
from enum import Enum  # ✅ الإصلاح الحاسم: تمت إضافة الاستيراد المفقود

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy
from capitalguard.application.services.price_service import PriceService
from capitalguard.interfaces.telegram.ui_texts import _pct

# ==================== CONSTANTS & CONFIGURATION ====================
ITEMS_PER_PAGE = 8
MAX_BUTTON_TEXT_LENGTH = 40
MAX_CALLBACK_DATA_LENGTH = 64

logger = logging.getLogger(__name__)

# ==================== CORE ARCHITECTURE (CallbackBuilder) ====================

class CallbackNamespace(Enum):
    POSITION = "pos"
    RECOMMENDATION = "rec"
    PUBLICATION = "pub"
    NAVIGATION = "nav"
    # ... (يمكن إضافة مساحات أسماء أخرى حسب الحاجة)

class CallbackAction(Enum):
    SHOW = "sh"
    UPDATE = "up"
    NAVIGATE = "nv"
    BACK = "bk"
    CLOSE = "cl"
    PARTIAL = "pt"
    STRATEGY = "st"
    TOGGLE = "tg"
    CONFIRM = "cf"
    # ... (يمكن إضافة إجراءات أخرى)

class CallbackBuilder:
    @staticmethod
    def create(namespace: CallbackNamespace, action: Union[CallbackAction, str], *params) -> str:
        """إنشاء بيانات استدعاء جديدة بشكل آمن وموحد."""
        param_str = ":".join(map(str, params))
        base = f"{namespace.value}:{action.value if isinstance(action, CallbackAction) else action}"
        if param_str:
            base = f"{base}:{param_str}"
        
        if len(base) > MAX_CALLBACK_DATA_LENGTH:
            logger.warning(f"Callback data truncated: {base}")
            return base[:MAX_CALLBACK_DATA_LENGTH]
        return base

    @staticmethod
    def parse(callback_data: str) -> Dict[str, Any]:
        """تحليل بيانات الاستدعاء إلى مكوناتها."""
        try:
            parts = callback_data.split(':')
            return {
                'raw': callback_data,
                'namespace': parts[0] if parts else None,
                'action': parts[1] if len(parts) > 1 else None,
                'params': parts[2:] if len(parts) > 2 else []
            }
        except Exception:
            return {'raw': callback_data, 'error': 'Parsing failed'}

# ==================== DOMAIN MODELS & UTILITIES ====================

class StatusIcons:
    PENDING = "⏳"
    ACTIVE = "▶️"
    PROFIT = "🟢"
    LOSS = "🔴"
    CLOSED = "🏁"
    ERROR = "⚠️"

class ButtonTexts:
    BACK_TO_LIST = "⬅️ العودة للقائمة"
    BACK_TO_MAIN = "⬅️ العودة للوحة التحكم"
    PREVIOUS = "⬅️ السابق"
    NEXT = "التالي ➡️"

def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    val = getattr(obj, attr, default)
    return val.value if hasattr(val, 'value') else val

def _truncate_text(text: str, max_length: int = MAX_BUTTON_TEXT_LENGTH) -> str:
    return text if len(text) <= max_length else text[:max_length-3] + "..."

# ==================== BUSINESS LOGIC LAYER ====================

class StatusDeterminer:
    @staticmethod
    def determine_icon(item: Any, live_price: Optional[float] = None) -> str:
        try:
            status = _get_attr(item, 'status')
            if status in [RecommendationStatus.PENDING, 'PENDING']:
                return StatusIcons.PENDING
            if status in [RecommendationStatus.CLOSED, 'CLOSED']:
                return StatusIcons.CLOSED
            
            if status in [RecommendationStatus.ACTIVE, 'ACTIVE', 'OPEN']:
                if live_price is not None:
                    entry = float(_get_attr(item, 'entry', 0))
                    side = _get_attr(item, 'side')
                    if entry > 0:
                        pnl = _pct(entry, live_price, side)
                        return StatusIcons.PROFIT if pnl >= 0 else StatusIcons.LOSS
                return StatusIcons.ACTIVE
            return StatusIcons.ERROR
        except Exception:
            return StatusIcons.ERROR

class NavigationBuilder:
    @staticmethod
    def build_pagination(current_page: int, total_pages: int) -> List[List[InlineKeyboardButton]]:
        buttons = []
        if current_page > 1:
            buttons.append(InlineKeyboardButton(ButtonTexts.PREVIOUS, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, current_page - 1)))
        if total_pages > 1:
            buttons.append(InlineKeyboardButton(f"{current_page}/{total_pages}", callback_data="noop"))
        if current_page < total_pages:
            buttons.append(InlineKeyboardButton(ButtonTexts.NEXT, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, current_page + 1)))
        return [buttons] if buttons else []

# ==================== KEYBOARD FACTORIES (COMPLETE & FINAL) ====================

async def build_open_recs_keyboard(items: List[Any], current_page: int, price_service: PriceService) -> InlineKeyboardMarkup:
    """بناء لوحة الصفقات المفتوحة - الإصدار النهائي."""
    try:
        total_items = len(items)
        total_pages = math.ceil(total_items / ITEMS_PER_PAGE) or 1
        start_index = (current_page - 1) * ITEMS_PER_PAGE
        paginated_items = items[start_index:start_index + ITEMS_PER_PAGE]
        
        prices_map = {
            _get_attr(item, 'asset'): await price_service.get_cached_price(_get_attr(item, 'asset'), _get_attr(item, 'market', 'Futures'))
            for item in paginated_items
        }

        keyboard_rows = []
        for item in paginated_items:
            rec_id = _get_attr(item, 'id')
            asset = _get_attr(item, 'asset')
            side = _get_attr(item, 'side')
            
            live_price = prices_map.get(asset)
            status_icon = StatusDeterminer.determine_icon(item, live_price)
            
            button_text = f"#{rec_id} - {asset} ({side})"
            if live_price is not None and status_icon in [StatusIcons.PROFIT, StatusIcons.LOSS]:
                pnl = _pct(float(_get_attr(item, 'entry', 0)), live_price, side)
                button_text = f"{status_icon} {button_text} | PnL: {pnl:+.2f}%"
            else:
                button_text = f"{status_icon} {button_text}"

            item_type = 'trade' if getattr(item, 'is_user_trade', False) else 'rec'
            callback_data = CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, item_type, rec_id)
            keyboard_rows.append([InlineKeyboardButton(_truncate_text(button_text), callback_data=callback_data)])
        
        keyboard_rows.extend(NavigationBuilder.build_pagination(current_page, total_pages))
        return InlineKeyboardMarkup(keyboard_rows)
        
    except Exception as e:
        logger.error(f"Open recs keyboard build failed: {e}", exc_info=True)
        return InlineKeyboardMarkup([[InlineKeyboardButton("⚠️ خطأ في تحميل البيانات", callback_data="noop")]])

def main_creation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 المنشئ التفاعلي", callback_data="method_interactive")],
        [InlineKeyboardButton("⚡️ الأمر السريع", callback_data="method_quick")],
        [InlineKeyboardButton("📋 المحرر النصي", callback_data="method_editor")],
    ])

def public_channel_keyboard(rec_id: int, bot_username: Optional[str]) -> InlineKeyboardMarkup:
    buttons = []
    if bot_username:
        buttons.append(InlineKeyboardButton("📊 تتبّع الإشارة", url=f"https://t.me/{bot_username}?start=track_{rec_id}"))
    return InlineKeyboardMarkup([buttons])

def analyst_control_panel_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    rec_id = rec.id
    if rec.status == RecommendationStatus.PENDING:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ إلغاء التوصية", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "cancel_pending", rec_id))],
            [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))],
        ])
    
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 تحديث السعر", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.UPDATE, "private", rec_id)),
            InlineKeyboardButton("✏️ تعديل", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_menu", rec_id)),
        ],
        [
            InlineKeyboardButton("📈 استراتيجية الخروج", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "strategy_menu", rec_id)),
            InlineKeyboardButton("💰 إغلاق جزئي", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.PARTIAL, rec_id)),
        ],
        [InlineKeyboardButton("❌ إغلاق كلي", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_menu", rec_id))],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))],
    ])

def build_close_options_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 إغلاق بسعر السوق", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_market", rec_id))],
        [InlineKeyboardButton("✍️ إغلاق بسعر محدد", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_manual", rec_id))],
        [InlineKeyboardButton("⬅️ عودة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "back_to_main", rec_id))],
    ])

def analyst_edit_menu_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛑 تعديل الوقف", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_sl", rec_id)),
            InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_tp", rec_id)),
        ],
        [InlineKeyboardButton("⬅️ عودة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "back_to_main", rec_id))],
    ])

def build_exit_strategy_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    rec_id, current_strategy = rec.id, rec.exit_strategy
    auto_close_text = f"{'✅ ' if current_strategy == ExitStrategy.CLOSE_AT_FINAL_TP else ''}🎯 الإغلاق عند الهدف الأخير"
    manual_close_text = f"{'✅ ' if current_strategy == ExitStrategy.MANUAL_CLOSE_ONLY else ''}✍️ الإغلاق اليدوي فقط"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(auto_close_text, callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.STRATEGY, rec_id, ExitStrategy.CLOSE_AT_FINAL_TP.value))],
        [InlineKeyboardButton(manual_close_text, callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.STRATEGY, rec_id, ExitStrategy.MANUAL_CLOSE_ONLY.value))],
        [InlineKeyboardButton("⬅️ عودة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "back_to_main", rec_id))],
    ])

def build_partial_close_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 إغلاق 25%", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.PARTIAL, rec_id, "25"))],
        [InlineKeyboardButton("💰 إغلاق 50%", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.PARTIAL, rec_id, "50"))],
        [InlineKeyboardButton("✍️ نسبة مخصصة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "partial_close_custom", rec_id))],
        [InlineKeyboardButton("⬅️ عودة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "back_to_main", rec_id))],
    ])

def asset_choice_keyboard(recent_assets: List[str]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(asset, callback_data=f"asset_{asset}") for asset in recent_assets]
    keyboard = [buttons[i: i + 3] for i in range(0, len(buttons), 3)]
    keyboard.append([InlineKeyboardButton("✍️ اكتب أصلاً جديدًا", callback_data="asset_new")])
    return InlineKeyboardMarkup(keyboard)

def side_market_keyboard(current_market: str = "Futures") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🟢 LONG / {current_market}", callback_data="side_LONG"),
            InlineKeyboardButton(f"🔴 SHORT / {current_market}", callback_data="side_SHORT"),
        ],
        [InlineKeyboardButton(f"🔄 تغيير السوق", callback_data="side_menu")],
    ])

def market_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 Futures", callback_data="market_Futures"), InlineKeyboardButton("💎 Spot", callback_data="market_Spot")],
        [InlineKeyboardButton("⬅️ عودة", callback_data="market_back")],
    ])

def order_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Market", callback_data="type_MARKET")],
        [InlineKeyboardButton("🎯 Limit", callback_data="type_LIMIT")],
        [InlineKeyboardButton("🚨 Stop Market", callback_data="type_STOP_MARKET")],
    ])

def review_final_keyboard(review_token: str) -> InlineKeyboardMarkup:
    short_token = review_token[:12]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نشر الآن", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "publish", short_token))],
        [
            InlineKeyboardButton("📢 اختيار القنوات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "choose_channels", short_token)),
            InlineKeyboardButton("📝 إضافة ملاحظات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "add_notes", short_token)),
        ],
        [InlineKeyboardButton("❌ إلغاء", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "cancel", short_token))],
    ])

def build_channel_picker_keyboard(review_token: str, channels: Iterable[Any], selected_ids: Set[int], page: int = 1, per_page: int = 6) -> InlineKeyboardMarkup:
    """بناء لوحة اختيار القنوات - الإصدار النهائي المصحح."""
    try:
        ch_list = list(channels)
        total = len(ch_list)
        total_pages = max(1, math.ceil(total / per_page))
        page = max(1, min(page, total_pages))
        
        start_idx, end_idx = (page - 1) * per_page, page * per_page
        page_items = ch_list[start_idx:end_idx]

        rows = []
        short_token = review_token[:12]

        for ch in page_items:
            tg_chat_id = int(_get_attr(ch, 'telegram_channel_id', 0))
            if not tg_chat_id: continue
            
            label = _truncate_text(_get_attr(ch, 'title') or f"قناة {tg_chat_id}", 25)
            status = "✅" if tg_chat_id in selected_ids else "☑️"
            
            callback_data = CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.TOGGLE, short_token, tg_chat_id, page)
            rows.append([InlineKeyboardButton(f"{status} {label}", callback_data=callback_data)])

        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, "nav", short_token, page - 1)))
        if total_pages > 1:
            nav_buttons.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("➡️", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, "nav", short_token, page + 1)))
        if nav_buttons:
            rows.append(nav_buttons)

        rows.append([
            InlineKeyboardButton("🚀 نشر المحدد", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.CONFIRM, short_token)),
            InlineKeyboardButton("⬅️ عودة", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.BACK, short_token)),
        ])
        return InlineKeyboardMarkup(rows)
        
    except Exception as e:
        logger.error(f"Error building channel picker: {e}", exc_info=True)
        return InlineKeyboardMarkup([[InlineKeyboardButton("❌ خطأ - عودة", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.BACK, review_token[:12]))]])

def build_subscription_keyboard(channel_link: Optional[str]) -> Optional[InlineKeyboardMarkup]:
    if channel_link:
        return InlineKeyboardMarkup([[InlineKeyboardButton("➡️ الانضمام للقناة", url=channel_link)]])
    return None

def build_user_trade_control_keyboard(trade_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 تحديث السعر", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.UPDATE, "trade", trade_id)),
            InlineKeyboardButton("❌ إغلاق الصفقة", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.CLOSE, "trade", trade_id)),
        ],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))],
    ])

# ==================== EXPORTS ====================
__all__ = [
    'build_open_recs_keyboard', 'main_creation_keyboard', 'public_channel_keyboard',
    'analyst_control_panel_keyboard', 'build_close_options_keyboard', 'analyst_edit_menu_keyboard',
    'build_exit_strategy_keyboard', 'build_partial_close_keyboard', 'asset_choice_keyboard',
    'side_market_keyboard', 'market_choice_keyboard', 'order_type_keyboard',
    'review_final_keyboard', 'build_channel_picker_keyboard', 'build_subscription_keyboard',
    'build_user_trade_control_keyboard', 'CallbackBuilder', 'CallbackNamespace', 'CallbackAction'
]