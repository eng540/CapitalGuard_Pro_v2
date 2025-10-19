# src/capitalguard/interfaces/telegram/keyboards.py (v21.3 - Production Ready & Final)
"""
هندسة لوحات المفاتيح المستدامة - إصدار إنتاجي متكامل ونهائي
✅ إصلاح حاسم: إضافة استيراد 'Enum' المفقود الذي كان يمنع بدء تشغيل التطبيق.
✅ إصلاح جميع مشاكل الأداء والتوافق (بما في ذلك تجميد لوحة القنوات).
✅ تحسين استجابة الأزرار والواجهات عبر بنية CallbackBuilder الموحدة.
✅ دعم كامل لنظام اختيار القنوات مع ترقيم الصفحات.
✅ منطق عرض ديناميكي للحالات (ربح/خسارة/معلق).
✅ آليات أمان لمنع النقرات من الجلسات القديمة.
✅ توافق عكسي كامل مع الإصدارات السابقة.
"""

import math
import logging
import hashlib
from decimal import Decimal
from typing import List, Iterable, Set, Optional, Any, Dict, Tuple, Union
from dataclasses import dataclass
from enum import Enum  # ✅ الإصلاح الحاسم: تمت إضافة الاستيراد المفقود

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from capitalguard.domain.entities import Recommendation, RecommendationStatus, ExitStrategy
from capitalguard.application.services.price_service import PriceService
from capitalguard.interfaces.telegram.ui_texts import _pct

# ==================== CONSTANTS & CONFIGURATION ====================
ITEMS_PER_PAGE = 8
MAX_BUTTON_TEXT_LENGTH = 40
MAX_CALLBACK_DATA_LENGTH = 64
CALLBACK_DATA_VERSION = "2.1"

logger = logging.getLogger(__name__)

# ==================== CORE ARCHITECTURE ====================

class CallbackNamespace(Enum):
    """مساحات الأسماء المنطقية لبيانات الاستدعاء"""
    POSITION = "pos"
    RECOMMENDATION = "rec"
    TRADE = "trd"
    PUBLICATION = "pub"
    NAVIGATION = "nav"
    SETTINGS = "set"
    ADMIN = "adm"
    TRACKING = "trk"
    SYSTEM = "sys"

class CallbackAction(Enum):
    """الإجراءات القياسية المعرفة مسبقاً"""
    SHOW = "sh"
    EDIT = "ed"
    UPDATE = "up"
    DELETE = "del"
    CONFIRM = "cf"
    CANCEL = "cn"
    TOGGLE = "tg"
    NAVIGATE = "nv"
    BACK = "bk"
    CREATE = "cr"
    CLOSE = "cl"
    STRATEGY = "st"
    PARTIAL = "pt"

@dataclass(frozen=True)
class CallbackSchema:
    """نموذج بيانات الاستدعاء المعيارية"""
    namespace: CallbackNamespace
    action: Union[CallbackAction, str]
    params: Tuple[Any, ...] = ()
    version: str = CALLBACK_DATA_VERSION
    
    def build(self) -> str:
        """بناء بيانات الاستدعاء بشكل آمن"""
        base = f"{self.namespace.value}:{self.action.value if isinstance(self.action, CallbackAction) else self.action}"
        
        if self.params:
            param_str = ":".join(str(p) for p in self.params)
            base = f"{base}:{param_str}"
            
        if self.version != "1.0":
            base = f"{base}:v{self.version}"
            
        return self._ensure_length(base)
    
    def _ensure_length(self, data: str) -> str:
        """التأكد من عدم تجاوز الطول المسموح"""
        if len(data) <= MAX_CALLBACK_DATA_LENGTH:
            return data
            
        logger.warning(f"Callback data truncated: {data}")
        
        if len(self.params) > 2:
            essential_params = self.params[:2]
            truncated = f"{self.namespace.value}:{self.action.value}:{':'.join(str(p) for p in essential_params)}"
            return truncated[:MAX_CALLBACK_DATA_LENGTH]
            
        return data[:MAX_CALLBACK_DATA_LENGTH]

class CallbackBuilder:
    """منشئ بيانات الاستدعاء المركزي"""
    
    @staticmethod
    def create(namespace: CallbackNamespace, action: Union[CallbackAction, str], *params) -> str:
        """إنشاء بيانات استدعاء جديدة"""
        return CallbackSchema(namespace, action, params).build()
    
    @staticmethod
    def parse(callback_data: str) -> Dict[str, Any]:
        """تحليل بيانات الاستدعاء إلى مكوناتها"""
        try:
            parts = callback_data.split(':')
            result = {
                'raw': callback_data,
                'version': '1.0',
                'namespace': None,
                'action': None,
                'params': []
            }
            
            if not parts:
                return result
                
            if parts[-1].startswith('v'):
                result['version'] = parts.pop()[1:]
                
            if len(parts) >= 1:
                result['namespace'] = parts[0]
            if len(parts) >= 2:
                result['action'] = parts[1]
            if len(parts) >= 3:
                result['params'] = parts[2:]
                
            return result
            
        except Exception as e:
            logger.error(f"Failed to parse callback data: {callback_data}, error: {e}")
            return {'raw': callback_data, 'error': str(e)}
    
    @staticmethod
    def parse_cq_parts(callback_data: str) -> List[str]:
        """تحليل بيانات الاستدعاء إلى أجزاء"""
        try:
            parts = callback_data.split(':')
            if parts and parts[-1].startswith('v'):
                parts = parts[:-1]
            return parts
        except Exception as e:
            logger.error(f"Failed to parse callback data parts: {callback_data}, error: {e}")
            return []

# ==================== DOMAIN MODELS ====================

class StatusIcons:
    """رموز الحالات المختلفة للتوصيات والصفقات"""
    PENDING = "⏳"
    ACTIVE = "▶️"
    BREAK_EVEN = "🛡️"
    PROFIT = "🟢"
    LOSS = "🔴"
    ERROR = "⚠️"
    SHADOW = "👻"
    CLOSED = "🏁"

class ButtonTexts:
    """نصوص الأزرار القياسية"""
    BACK = "⬅️ عودة"
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

# ==================== CORE UTILITIES ====================

def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """الحصول على خاصية بشكل آمن مع دعم متعدد"""
    try:
        if hasattr(obj, attr):
            val = getattr(obj, attr)
            return val.value if hasattr(val, 'value') else val
        elif isinstance(obj, dict) and attr in obj:
            return obj[attr]
        return default
    except Exception as e:
        logger.debug(f"Attribute access failed: {attr} from {type(obj).__name__}: {e}")
        return default

def _safe_get_display_id(item: Any) -> int:
    """الحصول على معرف العرض بشكل آمن"""
    return _get_attr(item, "analyst_rec_id") or _get_attr(item, "id", 0) or 0

def _safe_get_asset(item: Any) -> str:
    """الحصول على اسم الأصل بشكل آمن"""
    asset = _get_attr(item, 'asset', 'UNKNOWN')
    return asset.value if hasattr(asset, 'value') else str(asset)

def _safe_get_market(item: Any) -> str:
    """الحصول على السوق بشكل آمن"""
    return str(_get_attr(item, 'market', 'Futures'))

def _truncate_text(text: str, max_length: int = MAX_BUTTON_TEXT_LENGTH) -> str:
    """تقصير النص مع الحفاظ على المعنى"""
    if not text:
        return ""
    text = str(text)
    return text if len(text) <= max_length else text[:max_length-3] + "..."

def _create_short_token(full_token: str, length: int = 10) -> str:
    """إنشاء token مختصر"""
    if len(full_token) <= length:
        return full_token
    return hashlib.md5(full_token.encode()).hexdigest()[:length]

def parse_cq_parts(callback_data: str) -> List[str]:
    """تحليل بيانات الاستدعاء إلى أجزاء"""
    return CallbackBuilder.parse_cq_parts(callback_data)

# ==================== BUSINESS LOGIC LAYER ====================

class StatusDeterminer:
    """محلل الحالات المتقدم"""
    
    @staticmethod
    def determine_icon(item: Any, live_price: Optional[float] = None) -> str:
        """تحديد الرمز المناسب لحالة العنصر"""
        try:
            status = _get_attr(item, 'status')
            side = _get_attr(item, 'side')
            entry = float(_get_attr(item, 'entry', 0))
            
            if _get_attr(item, 'is_shadow', False):
                return StatusIcons.SHADOW
            
            status_value = status.value if hasattr(status, 'value') else status

            if status_value == RecommendationStatus.PENDING.value:
                return StatusIcons.PENDING

            if status_value == RecommendationStatus.ACTIVE.value:
                return StatusDeterminer._analyze_active_status(item, live_price, entry, side)

            if status_value in ['OPEN', 'OPEN.value']:
                return StatusDeterminer._analyze_user_trade_status(item, live_price, entry, side)
                
            if status_value in ['CLOSED', 'CLOSED.value']:
                return StatusIcons.CLOSED

            return StatusIcons.ACTIVE

        except Exception as e:
            logger.error(f"Status analysis failed: {e}")
            return StatusIcons.ERROR
    
    @staticmethod
    def _analyze_active_status(item: Any, live_price: Optional[float], entry: float, side: str) -> str:
        """تحليل الحالة النشطة"""
        stop_loss = float(_get_attr(item, 'stop_loss', 0))
        
        if entry > 0 and stop_loss > 0 and abs(entry - stop_loss) < 0.0001:
            return StatusIcons.BREAK_EVEN
            
        if live_price is not None and entry > 0:
            pnl = _pct(entry, float(live_price), side)
            return StatusIcons.PROFIT if pnl >= 0 else StatusIcons.LOSS
            
        return StatusIcons.ACTIVE
    
    @staticmethod
    def _analyze_user_trade_status(item: Any, live_price: Optional[float], entry: float, side: str) -> str:
        """تحليل حالة صفقة المستخدم"""
        if live_price is not None and entry > 0:
            pnl = _pct(entry, float(live_price), side)
            return StatusIcons.PROFIT if pnl >= 0 else StatusIcons.LOSS
        return StatusIcons.ACTIVE

class NavigationBuilder:
    """منشئ أنظمة التنقل"""
    
    @staticmethod
    def build_pagination(
        current_page: int, 
        total_pages: int, 
        base_namespace: CallbackNamespace = CallbackNamespace.NAVIGATION,
        additional_params: Tuple[Any, ...] = (),
        show_page_info: bool = True
    ) -> List[List[InlineKeyboardButton]]:
        """بناء أزرار الترقيم"""
        buttons = []
        
        if current_page > 1:
            buttons.append(InlineKeyboardButton(
                ButtonTexts.PREVIOUS,
                callback_data=CallbackBuilder.create(
                    base_namespace, CallbackAction.NAVIGATE, 
                    current_page - 1, *additional_params
                )
            ))
        
        if show_page_info and total_pages > 1:
            buttons.append(InlineKeyboardButton(
                f"صفحة {current_page}/{total_pages}", 
                callback_data="noop"
            ))
        
        if current_page < total_pages:
            buttons.append(InlineKeyboardButton(
                ButtonTexts.NEXT,
                callback_data=CallbackBuilder.create(
                    base_namespace, CallbackAction.NAVIGATE,
                    current_page + 1, *additional_params
                )
            ))
        
        return [buttons] if buttons else []

# ==================== KEYBOARD FACTORIES ====================

async def build_open_recs_keyboard(
    items: List[Any],
    current_page: int,
    price_service: PriceService,
) -> InlineKeyboardMarkup:
    """بناء لوحة الصفقات المفتوحة"""
    try:
        keyboard = []
        total_items = len(items)
        total_pages = math.ceil(total_items / ITEMS_PER_PAGE) or 1
        start_index = (current_page - 1) * ITEMS_PER_PAGE
        paginated_items = items[start_index:start_index + ITEMS_PER_PAGE]
        
        prices_map = {}
        try:
            for item in paginated_items:
                asset = _safe_get_asset(item)
                market = _safe_get_market(item)
                price = await price_service.get_cached_price(asset, market)
                prices_map[asset] = price
        except Exception as e:
            logger.warning(f"Price fetch failed: {e}")
        
        for item in paginated_items:
            rec_id = _get_attr(item, 'id')
            asset = _safe_get_asset(item)
            side = _get_attr(item, 'side')
            entry = float(_get_attr(item, 'entry', 0))
            status = _get_attr(item, 'status')
            display_id = _safe_get_display_id(item)
            
            button_text = f"#{display_id} - {asset} ({side})"
            live_price = prices_map.get(asset)
            status_icon = StatusDeterminer.determine_icon(item, live_price)
            
            status_value = status.value if hasattr(status, 'value') else status
            
            if status_value in [RecommendationStatus.ACTIVE.value, 'ACTIVE', 'OPEN'] and live_price is not None and entry > 0:
                pnl = _pct(entry, float(live_price), side)
                button_text = f"{status_icon} {button_text} | PnL: {pnl:+.2f}%"
            elif status_value in [RecommendationStatus.PENDING.value, 'PENDING']:
                button_text = f"{status_icon} {button_text} | معلق"
            elif status_value in ['CLOSED']:
                button_text = f"{status_icon} {button_text} | مغلق"
            else:
                button_text = f"{status_icon} {button_text} | نشط"

            is_trade = getattr(item, 'is_user_trade', False)
            item_type = 'trade' if is_trade else 'rec'
            
            keyboard.append([InlineKeyboardButton(
                _truncate_text(button_text),
                callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, item_type, rec_id)
            )])
        
        pagination = NavigationBuilder.build_pagination(current_page, total_pages)
        keyboard.extend(pagination)
        
        return InlineKeyboardMarkup(keyboard)
        
    except Exception as e:
        logger.error(f"Open recs keyboard build failed: {e}", exc_info=True)
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ خطأ في تحميل البيانات", callback_data="noop")],
            [InlineKeyboardButton("🔄 إعادة تحميل", callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.SHOW, "1"))]
        ])

def main_creation_keyboard() -> InlineKeyboardMarkup:
    """القائمة الرئيسية لاختيار طريقة إنشاء التوصية"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 المنشئ التفاعلي", callback_data="method_interactive")],
        [InlineKeyboardButton("⚡️ الأمر السريع", callback_data="method_quick")],
        [InlineKeyboardButton("📋 المحرر النصي", callback_data="method_editor")],
    ])

def public_channel_keyboard(rec_id: int, bot_username: str) -> InlineKeyboardMarkup:
    """بناء لوحة المفاتيح لرسالة القناة العامة"""
    buttons = []
    
    if bot_username:
        buttons.append(InlineKeyboardButton(
            "📊 تتبّع الإشارة", 
            url=f"https://t.me/{bot_username}?start=track_{rec_id}"
        ))
    
    buttons.append(InlineKeyboardButton(
        "🔄 تحديث البيانات الحية", 
        callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.UPDATE, "public", rec_id)
    ))

    return InlineKeyboardMarkup([buttons])

def analyst_control_panel_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    """بناء لوحة التحكم الديناميكية بناءً على حالة التوصية"""
    rec_id = rec.id
    
    if rec.status == RecommendationStatus.PENDING:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ إلغاء التوصية", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "cancel_pending", rec_id))],
            [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, "1"))],
        ])
    
    keyboard = [
        [
            InlineKeyboardButton("🔄 تحديث السعر", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.UPDATE, "private", rec_id)),
            InlineKeyboardButton("✏️ تعديل", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_menu", rec_id)),
        ],
        [
            InlineKeyboardButton("📈 استراتيجية الخروج", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "strategy_menu", rec_id)),
            InlineKeyboardButton("💰 إغلاق جزئي", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.PARTIAL, rec_id)),
        ],
        [InlineKeyboardButton("❌ إغلاق كلي", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_menu", rec_id))],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, "1"))],
    ]
    
    return InlineKeyboardMarkup(keyboard)

def build_close_options_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """بناء لوحة خيارات الإغلاق"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 إغلاق بسعر السوق", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_market", rec_id))],
        [InlineKeyboardButton("✍️ إغلاق بسعر محدد", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_manual", rec_id))],
        [InlineKeyboardButton(ButtonTexts.BACK, callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "back_to_main", rec_id))],
    ])

def analyst_edit_menu_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """بناء قائمة التعديل للمحلل"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛑 تعديل الوقف", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_sl", rec_id)),
            InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_tp", rec_id)),
        ],
        [InlineKeyboardButton(ButtonTexts.BACK, callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "back_to_main", rec_id))],
    ])

def build_exit_strategy_keyboard(rec: Recommendation) -> InlineKeyboardMarkup:
    """بناء لوحة استراتيجية الخروج"""
    rec_id = rec.id
    current_strategy = rec.exit_strategy
    
    auto_close_text = "🎯 الإغلاق عند الهدف الأخير"
    if current_strategy == ExitStrategy.CLOSE_AT_FINAL_TP: 
        auto_close_text = f"✅ {auto_close_text}"
    
    manual_close_text = "✍️ الإغلاق اليدوي فقط"
    if current_strategy == ExitStrategy.MANUAL_CLOSE_ONLY: 
        manual_close_text = f"✅ {manual_close_text}"

    keyboard = [
        [InlineKeyboardButton(
            auto_close_text, 
            callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.STRATEGY, rec_id, ExitStrategy.CLOSE_AT_FINAL_TP.value)
        )],
        [InlineKeyboardButton(
            manual_close_text, 
            callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.STRATEGY, rec_id, ExitStrategy.MANUAL_CLOSE_ONLY.value)
        )],
        [InlineKeyboardButton("🛡️ وضع/تعديل وقف الربح", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "set_profit_stop", rec_id))],
    ]
    
    if getattr(rec, "profit_stop_price", None) is not None:
        keyboard.append([InlineKeyboardButton(
            "🗑️ إزالة وقف الربح", 
            callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "remove_profit_stop", rec_id)
        )])
        
    keyboard.append([InlineKeyboardButton(
        ButtonTexts.BACK, 
        callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "back_to_main", rec_id)
    )])
    
    return InlineKeyboardMarkup(keyboard)

def confirm_close_keyboard(rec_id: int, exit_price: Decimal) -> InlineKeyboardMarkup:
    """بناء لوحة تأكيد الإغلاق"""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ تأكيد الإغلاق", 
            callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "confirm_close", rec_id, f"{float(exit_price):.8f}")
        ),
        InlineKeyboardButton(
            "❌ تراجع", 
            callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "cancel_close", rec_id)
        ),
    ]])

def asset_choice_keyboard(recent_assets: List[str]) -> InlineKeyboardMarkup:
    """بناء لوحة اختيار الأصل"""
    if not recent_assets:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✍️ اكتب أصلاً جديدًا", callback_data="asset_new")
        ]])
    
    buttons = [InlineKeyboardButton(asset, callback_data=f"asset_{asset}") for asset in recent_assets]
    keyboard_layout = [buttons[i: i + 3] for i in range(0, len(buttons), 3)]
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
            f"🔄 تغيير السوق", 
            callback_data="side_menu"
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
        [InlineKeyboardButton("⚡ Market", callback_data="type_MARKET")],
        [InlineKeyboardButton("🎯 Limit", callback_data="type_LIMIT")],
        [InlineKeyboardButton("🚨 Stop Market", callback_data="type_STOP_MARKET")],
    ])

def review_final_keyboard(review_token: str) -> InlineKeyboardMarkup:
    """بناء لوحة المراجعة النهائية"""
    short_token = review_token[:12]
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✅ نشر الآن", 
            callback_data=CallbackBuilder.create(
                CallbackNamespace.RECOMMENDATION, 
                "publish", 
                short_token
            )
        )],
        [
            InlineKeyboardButton(
                "📢 اختيار القنوات", 
                callback_data=CallbackBuilder.create(
                    CallbackNamespace.RECOMMENDATION, 
                    "choose_channels", 
                    short_token
                )
            ),
            InlineKeyboardButton(
                "📝 إضافة ملاحظات", 
                callback_data=CallbackBuilder.create(
                    CallbackNamespace.RECOMMENDATION, 
                    "add_notes", 
                    short_token
                )
            ),
        ],
        [InlineKeyboardButton(
            "❌ إلغاء", 
            callback_data=CallbackBuilder.create(
                CallbackNamespace.RECOMMENDATION, 
                "cancel", 
                short_token
            )
        )],
    ])

def build_channel_picker_keyboard(
    review_token: str,
    channels: Iterable[dict],
    selected_ids: Set[int],
    page: int = 1,
    per_page: int = 6,
) -> InlineKeyboardMarkup:
    """بناء لوحة اختيار القنوات - الإصدار المصحح"""
    try:
        ch_list = list(channels)
        total = len(ch_list)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        page_items = ch_list[start_idx:end_idx]

        rows = []
        short_token = review_token[:12]

        for ch in page_items:
            try:
                tg_chat_id = int(_get_attr(ch, 'telegram_channel_id', 0))
                if tg_chat_id == 0:
                    continue
                    
                label = _get_attr(ch, 'title') or f"@{_get_attr(ch, 'username')}" or f"قناة {tg_chat_id}"
                status = "✅" if tg_chat_id in selected_ids else "☑️"
                
                if len(label) > 25:
                    label = label[:22] + "..."
                
                callback_data = CallbackBuilder.create(
                    CallbackNamespace.PUBLICATION, 
                    CallbackAction.TOGGLE, 
                    short_token, tg_chat_id, page
                )
                
                rows.append([InlineKeyboardButton(
                    f"{status} {label}", 
                    callback_data=callback_data
                )])
            except Exception as e:
                logger.warning(f"Skipping channel due to error: {e}")
                continue

        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton(
                "⬅️", 
                callback_data=CallbackBuilder.create(
                    CallbackNamespace.PUBLICATION, 
                    "nav", 
                    short_token, page-1
                )
            ))
        
        if total_pages > 1:
            nav_buttons.append(InlineKeyboardButton(
                f"{page}/{total_pages}", 
                callback_data="noop"
            ))
        
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton(
                "➡️", 
                callback_data=CallbackBuilder.create(
                    CallbackNamespace.PUBLICATION, 
                    "nav", 
                    short_token, page+1
                )
            ))
        
        if nav_buttons:
            rows.append(nav_buttons)

        action_buttons = [
            InlineKeyboardButton(
                "🚀 نشر المحدد", 
                callback_data=CallbackBuilder.create(
                    CallbackNamespace.PUBLICATION, 
                    CallbackAction.CONFIRM, 
                    short_token
                )
            ),
            InlineKeyboardButton(
                "⬅️ عودة", 
                callback_data=CallbackBuilder.create(
                    CallbackNamespace.PUBLICATION, 
                    CallbackAction.BACK, 
                    short_token
                )
            ),
        ]
        rows.append(action_buttons)

        return InlineKeyboardMarkup(rows)
        
    except Exception as e:
        logger.error(f"Error building channel picker: {e}", exc_info=True)
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ خطأ في التحميل - العودة", 
                callback_data=CallbackBuilder.create(
                    CallbackNamespace.PUBLICATION, 
                    CallbackAction.BACK, 
                    review_token[:12]
                )
            )
        ]])

def build_subscription_keyboard(channel_link: Optional[str]) -> Optional[InlineKeyboardMarkup]:
    """بناء لوحة الاشتراك"""
    if channel_link:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("➡️ الانضمام للقناة", url=channel_link)
        ]])
    return None

def build_signal_tracking_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """بناء لوحة تتبع الإشارة"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔔 نبهني عند الهدف الأول", callback_data=CallbackBuilder.create(CallbackNamespace.TRACKING, "notify_tp1", rec_id)),
            InlineKeyboardButton("🔔 نبهني عند وقف الخسارة", callback_data=CallbackBuilder.create(CallbackNamespace.TRACKING, "notify_sl", rec_id))
        ],
        [
            InlineKeyboardButton("🎯 نبهني عند جميع الأهداف", callback_data=CallbackBuilder.create(CallbackNamespace.TRACKING, "notify_all_tp", rec_id)),
            InlineKeyboardButton("📊 إحصائيات الأداء", callback_data=CallbackBuilder.create(CallbackNamespace.TRACKING, "stats", rec_id))
        ],
        [
            InlineKeyboardButton("➕ أضف إلى محفظتي", callback_data=CallbackBuilder.create(CallbackNamespace.TRACKING, "add_portfolio", rec_id)),
            InlineKeyboardButton("📋 تفاصيل الصفقة", callback_data=CallbackBuilder.create(CallbackNamespace.TRACKING, "details", rec_id))
        ]
    ])

def build_user_trade_control_keyboard(trade_id: int) -> InlineKeyboardMarkup:
    """بناء لوحة تحكم صفقة المستخدم"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 تحديث السعر", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.UPDATE, "trade", trade_id)),
            InlineKeyboardButton("❌ إغلاق الصفقة", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.CLOSE, "trade", trade_id)),
        ],
        [InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, "1"))],
    ])

def build_confirmation_keyboard(
    action: str, 
    item_id: int, 
    confirm_text: str = "✅ تأكيد",
    cancel_text: str = "❌ إلغاء"
) -> InlineKeyboardMarkup:
    """بناء لوحة تأكيد عامة"""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(confirm_text, callback_data=CallbackBuilder.create(CallbackNamespace(action), CallbackAction.CONFIRM, item_id)),
        InlineKeyboardButton(cancel_text, callback_data=CallbackBuilder.create(CallbackNamespace(action), CallbackAction.CANCEL, item_id)),
    ]])

def build_settings_keyboard() -> InlineKeyboardMarkup:
    """بناء لوحة الإعدادات"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 إعدادات التنبيهات", callback_data=CallbackBuilder.create(CallbackNamespace.SETTINGS, "alerts"))],
        [InlineKeyboardButton("📊 إعدادات التقارير", callback_data=CallbackBuilder.create(CallbackNamespace.SETTINGS, "reports"))],
        [InlineKeyboardButton("🌐 إعدادات اللغة", callback_data=CallbackBuilder.create(CallbackNamespace.SETTINGS, "language"))],
        [InlineKeyboardButton("⚙️ إعدادات متقدمة", callback_data=CallbackBuilder.create(CallbackNamespace.SETTINGS, "advanced"))],
        [InlineKeyboardButton(ButtonTexts.BACK, callback_data=CallbackBuilder.create(CallbackNamespace.SETTINGS, CallbackAction.BACK))],
    ])

def build_quick_actions_keyboard() -> InlineKeyboardMarkup:
    """بناء لوحة الإجراءات السريعة"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📈 صفقاتي", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "my_trades")),
            InlineKeyboardButton("📊 الإحصائيات", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "stats")),
        ],
        [
            InlineKeyboardButton("⚡ توصية سريعة", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "new_trade")),
            InlineKeyboardButton("🔍 استكشاف", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "explore")),
        ],
        [
            InlineKeyboardButton("🆘 المساعدة", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "help")),
            InlineKeyboardButton("⚙️ الإعدادات", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "settings")),
        ]
    ])

def build_admin_panel_keyboard() -> InlineKeyboardMarkup:
    """بناء لوحة تحكم المشرف"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 إحصائيات النظام", callback_data=CallbackBuilder.create(CallbackNamespace.ADMIN, "stats"))],
        [InlineKeyboardButton("👥 إدارة المستخدمين", callback_data=CallbackBuilder.create(CallbackNamespace.ADMIN, "users"))],
        [InlineKeyboardButton("📢 إدارة القنوات", callback_data=CallbackBuilder.create(CallbackNamespace.ADMIN, "channels"))],
        [InlineKeyboardButton("🔔 الإشعارات النظامية", callback_data=CallbackBuilder.create(CallbackNamespace.ADMIN, "notifications"))],
        [InlineKeyboardButton("📈 أداء المحللين", callback_data=CallbackBuilder.create(CallbackNamespace.ADMIN, "analysts"))],
        [InlineKeyboardButton("🚪 العودة", callback_data=CallbackBuilder.create(CallbackNamespace.ADMIN, CallbackAction.BACK))],
    ])

def build_trader_dashboard_keyboard() -> InlineKeyboardMarkup:
    """بناء لوحة تحكم المتداول"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 صفقاتي المفتوحة", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "open_trades")),
            InlineKeyboardButton("📈 أداء المحفظة", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "portfolio")),
        ],
        [
            InlineKeyboardButton("🔔 متابعة إشارة", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "track_signal")),
            InlineKeyboardButton("📋 سجل الصفقات", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "trade_history")),
        ],
        [
            InlineKeyboardButton("⚡ صفقة سريعة", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "quick_trade")),
            InlineKeyboardButton("⚙️ إعداداتي", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "settings")),
        ]
    ])

def build_trade_edit_keyboard(trade_id: int) -> InlineKeyboardMarkup:
    """بناء لوحة تعديل الصفقة الشخصية"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛑 تعديل الوقف", callback_data=CallbackBuilder.create(CallbackNamespace.TRADE, "edit_sl", trade_id)),
            InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=CallbackBuilder.create(CallbackNamespace.TRADE, "edit_tp", trade_id)),
        ],
        [
            InlineKeyboardButton("📊 تعديل سعر الدخول", callback_data=CallbackBuilder.create(CallbackNamespace.TRADE, "edit_entry", trade_id)),
            InlineKeyboardButton("🏷️ تعديل الملاحظات", callback_data=CallbackBuilder.create(CallbackNamespace.TRADE, "edit_notes", trade_id)),
        ],
        [InlineKeyboardButton(ButtonTexts.BACK, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, "trade", trade_id))],
    ])

def build_partial_close_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    """بناء لوحة إغلاق جزئي"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "💰 إغلاق 25%", 
            callback_data=CallbackBuilder.create(
                CallbackNamespace.RECOMMENDATION, 
                CallbackAction.PARTIAL, 
                rec_id, "25"
            )
        )],
        [InlineKeyboardButton(
            "💰 إغلاق 50%", 
            callback_data=CallbackBuilder.create(
                CallbackNamespace.RECOMMENDATION, 
                CallbackAction.PARTIAL, 
                rec_id, "50"
            )
        )],
        [InlineKeyboardButton(
            "✍️ نسبة مخصصة", 
            callback_data=CallbackBuilder.create(
                CallbackNamespace.RECOMMENDATION, 
                "partial_close_custom", 
                rec_id
            )
        )],
        [InlineKeyboardButton(
            ButtonTexts.BACK, 
            callback_data=CallbackBuilder.create(
                CallbackNamespace.RECOMMENDATION, 
                "back_to_main", 
                rec_id
            )
        )],
    ])

def build_analyst_dashboard_keyboard() -> InlineKeyboardMarkup:
    """بناء لوحة تحكم المحلل"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 توصياتي النشطة", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "open_recs")),
            InlineKeyboardButton("📈 أداء التوصيات", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "performance")),
        ],
        [
            InlineKeyboardButton("💬 توصية جديدة", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "new_recommendation")),
            InlineKeyboardButton("📋 سجل التوصيات", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "rec_history")),
        ],
        [
            InlineKeyboardButton("📢 إدارة القنوات", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "manage_channels")),
            InlineKeyboardButton("⚙️ إعدادات المحلل", callback_data=CallbackBuilder.create(CallbackNamespace.SYSTEM, "settings")),
        ]
    ])

# ==================== EXPORTS ====================

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
    'CallbackBuilder',
    'StatusDeterminer',
    'NavigationBuilder',
    'StatusIcons',
    'ButtonTexts',
    'CallbackNamespace',
    'CallbackAction',
    'parse_cq_parts',
]