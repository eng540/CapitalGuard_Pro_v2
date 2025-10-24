# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/keyboards.py ---
# src/capitalguard/interfaces/telegram/keyboards.py (v21.10 - Final UX Hotfix)
"""
Builds all Telegram keyboards for the bot.
✅ UX HOTFIX: Restored direct access to "Partial Close" and "Full Close" buttons
[cite_start]on the main analyst control panel for better usability[cite: 702].
- [cite_start]Implements the new unified Exit Management control panel and all its sub-panels[cite: 703].
- [cite_start]All callback data now uses the unified CallbackBuilder for maximum reliability[cite: 704].
"""

import math
import logging
from decimal import Decimal
from typing import List, Iterable, Set, Optional, Any, Dict, Tuple, Union
from enum import Enum

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from capitalguard.domain.entities import Recommendation, RecommendationStatus
from capitalguard.application.services.price_service import PriceService
from capitalguard.interfaces.telegram.ui_texts import _pct

[cite_start]logger = logging.getLogger(__name__) [cite: 705]
[cite_start]ITEMS_PER_PAGE = 8 [cite: 705]
[cite_start]MAX_BUTTON_TEXT_LENGTH = 40 [cite: 705]
[cite_start]MAX_CALLBACK_DATA_LENGTH = 64 [cite: 705]

# --- Core Callback Architecture ---

[cite_start]class CallbackNamespace(Enum): [cite: 705]
    [cite_start]POSITION = "pos" [cite: 705]
    [cite_start]RECOMMENDATION = "rec" [cite: 705]
    [cite_start]EXIT_STRATEGY = "exit" [cite: 705]
    [cite_start]NAVIGATION = "nav" [cite: 705]
    [cite_start]PUBLICATION = "pub" [cite: 705]
    [cite_start]FORWARD_PARSE = "fwd_parse" [cite: 705]
    [cite_start]FORWARD_CONFIRM = "fwd_confirm" [cite: 705]

[cite_start]class CallbackAction(Enum): [cite: 705]
    [cite_start]SHOW = "sh" [cite: 705]
    [cite_start]UPDATE = "up" [cite: 706]
    [cite_start]NAVIGATE = "nv" [cite: 706]
    [cite_start]BACK = "bk" [cite: 706]
    [cite_start]CLOSE = "cl" [cite: 706]
    [cite_start]PARTIAL = "pt" [cite: 706]
    [cite_start]CONFIRM = "cf" [cite: 706]
    [cite_start]CANCEL = "cn" [cite: 706]

[cite_start]class CallbackBuilder: [cite: 706]
    @staticmethod
    [cite_start]def create(namespace: Union[CallbackNamespace, str], action: Union[CallbackAction, str], *params) -> str: [cite: 706]
        [cite_start]ns_val = namespace.value if isinstance(namespace, CallbackNamespace) else namespace [cite: 706]
        [cite_start]act_val = action.value if isinstance(action, CallbackAction) else action [cite: 706]
        [cite_start]param_str = ":".join(map(str, params)) [cite: 706]
        [cite_start]base = f"{ns_val}:{act_val}" [cite: 707]
        [cite_start]if param_str: base = f"{base}:{param_str}" [cite: 707]
        [cite_start]if len(base) > MAX_CALLBACK_DATA_LENGTH: [cite: 707]
            [cite_start]logger.warning(f"Callback data truncated: {base}") [cite: 707]
            [cite_start]return base[:MAX_CALLBACK_DATA_LENGTH] [cite: 707]
        [cite_start]return base [cite: 707]

    @staticmethod
    [cite_start]def parse(callback_data: str) -> Dict[str, Any]: [cite: 707]
        try:
            [cite_start]parts = callback_data.split(':') [cite: 708]
            [cite_start]return {'raw': callback_data, 'namespace': parts[0] if parts else None, 'action': parts[1] if len(parts) > 1 else None, 'params': parts[2:] if len(parts) > 2 else []} [cite: 708]
        [cite_start]except Exception: [cite: 708]
            [cite_start]return {'raw': callback_data, 'error': 'Parsing failed'} [cite: 708]

# --- UI Constants and Helpers ---

[cite_start]class StatusIcons: [cite: 708]
    [cite_start]PENDING = "⏳"; [cite: 709]
    ACTIVE = "▶️"; PROFIT = "🟢"; LOSS = "🔴"; [cite_start]CLOSED = "🏁"; [cite: 709]
    [cite_start]ERROR = "⚠️" [cite: 710]

[cite_start]class ButtonTexts: [cite: 710]
    BACK_TO_LIST = "⬅️ العودة للقائمة"; [cite_start]BACK_TO_MAIN = "⬅️ العودة للوحة التحكم"; [cite: 710]
    PREVIOUS = "⬅️ السابق"; [cite_start]NEXT = "التالي ➡️" [cite: 711]

[cite_start]def _get_attr(obj: Any, attr: str, default: Any = None) -> Any: [cite: 711]
    [cite_start]val = getattr(obj, attr, default) [cite: 711]
    [cite_start]return val.value if hasattr(val, 'value') else val [cite: 711]

[cite_start]def _truncate_text(text: str, max_length: int = MAX_BUTTON_TEXT_LENGTH) -> str: [cite: 711]
    [cite_start]return text if len(text) <= max_length else text[:max_length-3] + "..." [cite: 711]

[cite_start]class StatusDeterminer: [cite: 711]
    @staticmethod
    [cite_start]def determine_icon(item: Any, live_price: Optional[float] = None) -> str: [cite: 711]
        try:
            [cite_start]status = _get_attr(item, 'status') [cite: 711]
            [cite_start]if status in [RecommendationStatus.PENDING, 'PENDING']: return StatusIcons.PENDING [cite: 712]
            [cite_start]if status in [RecommendationStatus.CLOSED, 'CLOSED']: return StatusIcons.CLOSED [cite: 712]
            [cite_start]if status in [RecommendationStatus.ACTIVE, 'ACTIVE', 'OPEN']: [cite: 712]
                [cite_start]if live_price is not None: [cite: 712]
                    [cite_start]entry = float(_get_attr(item, 'entry', 0)) [cite: 712]
                    [cite_start]side = _get_attr(item, 'side') [cite: 713]
                    [cite_start]if entry > 0: [cite: 713]
                        [cite_start]pnl = _pct(entry, live_price, side) [cite: 713]
                        [cite_start]return StatusIcons.PROFIT if pnl >= 0 else StatusIcons.LOSS [cite: 713]
                [cite_start]return StatusIcons.ACTIVE [cite: 714]
            [cite_start]return StatusIcons.ERROR [cite: 714]
        [cite_start]except Exception: return StatusIcons.ERROR [cite: 714]

[cite_start]class NavigationBuilder: [cite: 714]
    @staticmethod
    [cite_start]def build_pagination(current_page: int, total_pages: int) -> List[List[InlineKeyboardButton]]: [cite: 714]
        [cite_start]buttons = [] [cite: 714]
        [cite_start]if current_page > 1: buttons.append(InlineKeyboardButton(ButtonTexts.PREVIOUS, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, current_page - 1))) [cite: 714]
        [cite_start]if total_pages > 1: buttons.append(InlineKeyboardButton(f"{current_page}/{total_pages}", callback_data="noop")) [cite: 714]
        [cite_start]if current_page < total_pages: buttons.append(InlineKeyboardButton(ButtonTexts.NEXT, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, current_page + 1))) [cite: 715]
        [cite_start]return [buttons] if buttons else [] [cite: 715]

# --- Keyboard Factories ---

[cite_start]def analyst_control_panel_keyboard(rec: Recommendation) -> InlineKeyboardMarkup: [cite: 715]
    """The unified control panel for active recommendations."""
    [cite_start]rec_id = rec.id [cite: 715]
    [cite_start]keyboard = [ [cite: 715]
        [
            [cite_start]InlineKeyboardButton("🔄 تحديث السعر", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id)), [cite: 715]
            [cite_start]InlineKeyboardButton("💰 إغلاق جزئي", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "partial_close_menu", rec_id)), [cite: 715]
            [cite_start]InlineKeyboardButton("❌ إغلاق كلي", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_menu", rec_id)), [cite: 716]
        ],
        [
            [cite_start]InlineKeyboardButton("📈 إدارة الخروج والمخاطر", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "show_menu", rec_id)), [cite: 716]
            [cite_start]InlineKeyboardButton("✏️ تعديل بيانات الصفقة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_menu", rec_id)), [cite: 716]
        ],
        [cite_start][InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))], [cite: 716]
    ]
    [cite_start]return InlineKeyboardMarkup(keyboard) [cite: 716]

[cite_start]def build_exit_management_keyboard(rec: Recommendation) -> InlineKeyboardMarkup: [cite: 716]
    [cite_start]"""The exit strategy management panel.""" [cite: 717]
    [cite_start]rec_id = rec.id [cite: 717]
    [cite_start]keyboard = [ [cite: 717]
        [cite_start][InlineKeyboardButton("⚖️ نقل الوقف إلى التعادل (فوري)", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "move_to_be", rec_id))], [cite: 717]
        [cite_start][InlineKeyboardButton("🔒 تفعيل حجز ربح ثابت", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "set_fixed", rec_id))], [cite: 717]
        [cite_start][InlineKeyboardButton("📈 تفعيل الوقف المتحرك", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "set_trailing", rec_id))], [cite: 717]
    ]
    [cite_start]if _get_attr(rec, 'profit_stop_active', False): [cite: 717]
        [cite_start]keyboard.append([InlineKeyboardButton("❌ إلغاء الاستراتيجية الآلية", callback_data=CallbackBuilder.create(CallbackNamespace.EXIT_STRATEGY, "cancel", rec_id))]) [cite: 717]

    [cite_start]keyboard.append([InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))]) [cite: 717-718]
    [cite_start]return InlineKeyboardMarkup(keyboard) [cite: 718]

[cite_start]def build_trade_data_edit_keyboard(rec_id: int) -> InlineKeyboardMarkup: [cite: 718]
    """The trade data editing panel."""
    [cite_start]return InlineKeyboardMarkup([ [cite: 718]
        [cite_start][InlineKeyboardButton("💰 تعديل سعر الدخول", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_entry", rec_id))], [cite: 718]
        [cite_start][InlineKeyboardButton("🛑 تعديل وقف الخسارة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_sl", rec_id))], [cite: 718]
        [cite_start][InlineKeyboardButton("🎯 تعديل الأهداف", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_tp", rec_id))], [cite: 718]
        [cite_start][InlineKeyboardButton("📝 تعديل الملاحظات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "edit_notes", rec_id))], [cite: 718]
        [cite_start][InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))], [cite: 718]
    ])

[cite_start]async def build_open_recs_keyboard(items: List[Any], current_page: int, price_service: PriceService) -> InlineKeyboardMarkup: [cite: 718]
    try:
        [cite_start]total_items = len(items) [cite: 719]
        [cite_start]total_pages = math.ceil(total_items / ITEMS_PER_PAGE) or 1 [cite: 719]
        [cite_start]start_index = (current_page - 1) * ITEMS_PER_PAGE [cite: 719]
        [cite_start]paginated_items = items[start_index:start_index + ITEMS_PER_PAGE] [cite: 719]
        [cite_start]prices_map = {_get_attr(item, 'asset'): await price_service.get_cached_price(_get_attr(item, 'asset'), _get_attr(item, 'market', 'Futures')) for item in paginated_items} [cite: 719]
        [cite_start]keyboard_rows = [] [cite: 719]
        [cite_start]for item in paginated_items: [cite: 719]
            [cite_start]rec_id, asset, side = _get_attr(item, 'id'), _get_attr(item, 'asset'), _get_attr(item, 'side') [cite: 720]
            [cite_start]live_price = prices_map.get(asset) [cite: 720]
            [cite_start]status_icon = StatusDeterminer.determine_icon(item, live_price) [cite: 720]
            [cite_start]button_text = f"#{rec_id} - {asset} ({side})" [cite: 720]
            [cite_start]if live_price is not None and status_icon in [StatusIcons.PROFIT, StatusIcons.LOSS]: [cite: 720]
                [cite_start]pnl = _pct(float(_get_attr(item, 'entry', 0)), live_price, side) [cite: 721]
                [cite_start]button_text = f"{status_icon} {button_text} | PnL: {pnl:+.2f}%" [cite: 721-722]
            [cite_start]else: [cite: 722]
                [cite_start]button_text = f"{status_icon} {button_text}" [cite: 722]
            [cite_start]item_type = 'trade' if getattr(item, 'is_user_trade', False) else 'rec' [cite: 722]
            [cite_start]callback_data = CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, item_type, rec_id) [cite: 722]
            [cite_start]keyboard_rows.append([InlineKeyboardButton(_truncate_text(button_text), callback_data=callback_data)]) [cite: 722]
        [cite_start]keyboard_rows.extend(NavigationBuilder.build_pagination(current_page, total_pages)) [cite: 722]
        [cite_start]return InlineKeyboardMarkup(keyboard_rows) [cite: 723]
    [cite_start]except Exception as e: [cite: 723]
        [cite_start]logger.error(f"Open recs keyboard build failed: {e}", exc_info=True) [cite: 723]
        [cite_start]return InlineKeyboardMarkup([[InlineKeyboardButton("⚠️ خطأ في تحميل البيانات", callback_data="noop")]]) [cite: 723]

[cite_start]def build_close_options_keyboard(rec_id: int) -> InlineKeyboardMarkup: [cite: 723]
    [cite_start]return InlineKeyboardMarkup([ [cite: 723]
        [cite_start][InlineKeyboardButton("📉 إغلاق بسعر السوق", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_market", rec_id))], [cite: 723]
        [cite_start][InlineKeyboardButton("✍️ إغلاق بسعر محدد", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "close_manual", rec_id))], [cite: 723]
        [cite_start][InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))], [cite: 723]
    ])

[cite_start]def build_partial_close_keyboard(rec_id: int) -> InlineKeyboardMarkup: [cite: 723]
    [cite_start]return InlineKeyboardMarkup([ [cite: 723]
        [cite_start][InlineKeyboardButton("💰 إغلاق 25%", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.PARTIAL, rec_id, "25"))], [cite: 724]
        [cite_start][InlineKeyboardButton("💰 إغلاق 50%", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, CallbackAction.PARTIAL, rec_id, "50"))], [cite: 724]
        [cite_start][InlineKeyboardButton("✍️ نسبة مخصصة", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "partial_close_custom", rec_id))], [cite: 724]
        [cite_start][InlineKeyboardButton(ButtonTexts.BACK_TO_MAIN, callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.SHOW, 'rec', rec_id))], [cite: 724]
    ])

[cite_start]def build_confirmation_keyboard(namespace: str, item_id: int, confirm_text: str = "✅ Confirm", cancel_text: str = "❌ Cancel") -> InlineKeyboardMarkup: [cite: 724]
    [cite_start]return InlineKeyboardMarkup([[ [cite: 724]
        [cite_start]InlineKeyboardButton(confirm_text, callback_data=CallbackBuilder.create(namespace, CallbackAction.CONFIRM, item_id)), [cite: 724]
        [cite_start]InlineKeyboardButton(cancel_text, callback_data=CallbackBuilder.create(namespace, CallbackAction.CANCEL, item_id)), [cite: 724]
    [cite_start]]]) [cite: 725]

[cite_start]def public_channel_keyboard(rec_id: int, bot_username: Optional[str]) -> InlineKeyboardMarkup: [cite: 725]
    [cite_start]buttons = [] [cite: 725]
    [cite_start]if bot_username: [cite: 725]
        [cite_start]buttons.append(InlineKeyboardButton("📊 تتبّع الإشارة", url=f"https://t.me/{bot_username}?start=track_{rec_id}")) [cite: 725]
    [cite_start]return InlineKeyboardMarkup([buttons]) [cite: 725]

[cite_start]def build_user_trade_control_keyboard(trade_id: int) -> InlineKeyboardMarkup: [cite: 725]
    [cite_start]return InlineKeyboardMarkup([ [cite: 725]
        [cite_start][InlineKeyboardButton("🔄 تحديث السعر", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.UPDATE, "trade", trade_id)), InlineKeyboardButton("❌ إغلاق الصفقة", callback_data=CallbackBuilder.create(CallbackNamespace.POSITION, CallbackAction.CLOSE, "trade", trade_id))], [cite: 725]
        [cite_start][InlineKeyboardButton(ButtonTexts.BACK_TO_LIST, callback_data=CallbackBuilder.create(CallbackNamespace.NAVIGATION, CallbackAction.NAVIGATE, 1))], [cite: 725]
    ])

[cite_start]def build_subscription_keyboard(channel_link: Optional[str]) -> Optional[InlineKeyboardMarkup]: [cite: 725]
    [cite_start]if channel_link: [cite: 725]
        [cite_start]return InlineKeyboardMarkup([[InlineKeyboardButton("➡️ الانضمام للقناة", url=channel_link)]]) [cite: 726]
    [cite_start]return None [cite: 726]

[cite_start]def main_creation_keyboard() -> InlineKeyboardMarkup: [cite: 726]
    [cite_start]return InlineKeyboardMarkup([ [cite: 726]
        [cite_start][InlineKeyboardButton("💬 المنشئ التفاعلي", callback_data="method_interactive")], [cite: 726]
        [cite_start][InlineKeyboardButton("⚡️ الأمر السريع", callback_data="method_quick")], [cite: 726]
        [cite_start][InlineKeyboardButton("📋 المحرر النصي", callback_data="method_editor")], [cite: 726]
    ])

[cite_start]def asset_choice_keyboard(recent_assets: List[str]) -> InlineKeyboardMarkup: [cite: 726]
    [cite_start]buttons = [InlineKeyboardButton(asset, callback_data=f"asset_{asset}") for asset in recent_assets] [cite: 726]
    [cite_start]keyboard = [buttons[i: i + 3] for i in range(0, len(buttons), 3)] [cite: 726]
    [cite_start]keyboard.append([InlineKeyboardButton("✍️ اكتب أصلاً جديدًا", callback_data="asset_new")]) [cite: 726]
    [cite_start]return InlineKeyboardMarkup(keyboard) [cite: 726]

[cite_start]def side_market_keyboard(current_market: str = "Futures") -> InlineKeyboardMarkup: [cite: 726]
    [cite_start]return InlineKeyboardMarkup([ [cite: 727]
        [cite_start][InlineKeyboardButton(f"🟢 LONG / {current_market}", callback_data="side_LONG"), InlineKeyboardButton(f"🔴 SHORT / {current_market}", callback_data="side_SHORT")], [cite: 727]
        [cite_start][InlineKeyboardButton(f"🔄 تغيير السوق", callback_data="side_menu")], [cite: 727]
    ])

[cite_start]def market_choice_keyboard() -> InlineKeyboardMarkup: [cite: 727]
    [cite_start]return InlineKeyboardMarkup([ [cite: 727]
        [cite_start][InlineKeyboardButton("📈 Futures", callback_data="market_Futures"), InlineKeyboardButton("💎 Spot", callback_data="market_Spot")], [cite: 727]
        [cite_start][InlineKeyboardButton("⬅️ عودة", callback_data="market_back")], [cite: 727]
    ])

[cite_start]def order_type_keyboard() -> InlineKeyboardMarkup: [cite: 727]
    [cite_start]return InlineKeyboardMarkup([ [cite: 727]
        [cite_start][InlineKeyboardButton("⚡ Market", callback_data="type_MARKET")], [cite: 727]
        [cite_start][InlineKeyboardButton("🎯 Limit", callback_data="type_LIMIT")], [cite: 727]
        [cite_start][InlineKeyboardButton("🚨 Stop Market", callback_data="type_STOP_MARKET")], [cite: 728]
    ])

[cite_start]def review_final_keyboard(review_token: str) -> InlineKeyboardMarkup: [cite: 728]
    [cite_start]short_token = review_token[:12] [cite: 728]
    [cite_start]return InlineKeyboardMarkup([ [cite: 728]
        [cite_start][InlineKeyboardButton("✅ نشر الآن", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "publish", short_token))], [cite: 728]
        [cite_start][InlineKeyboardButton("📢 اختيار القنوات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "choose_channels", short_token)), InlineKeyboardButton("📝 إضافة ملاحظات", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "add_notes", short_token))], [cite: 728]
        [cite_start][InlineKeyboardButton("❌ إلغاء", callback_data=CallbackBuilder.create(CallbackNamespace.RECOMMENDATION, "cancel", short_token))], [cite: 728]
    ])

[cite_start]def build_channel_picker_keyboard(review_token: str, channels: Iterable[Any], selected_ids: Set[int], page: int = 1, per_page: int = 6) -> InlineKeyboardMarkup: [cite: 728]
    try:
        [cite_start]ch_list = list(channels) [cite: 729]
        [cite_start]total = len(ch_list) [cite: 729]
        [cite_start]total_pages = max(1, math.ceil(total / per_page)) [cite: 729]
        [cite_start]page = max(1, min(page, total_pages)) [cite: 729]
        [cite_start]start_idx, end_idx = (page - 1) * per_page, page * per_page [cite: 729]
        [cite_start]page_items = ch_list[start_idx:end_idx] [cite: 729]
        [cite_start]rows = [] [cite: 729]
        [cite_start]short_token = review_token[:12] [cite: 729]
        [cite_start]for ch in page_items: [cite: 729-730]
            [cite_start]tg_chat_id = int(_get_attr(ch, 'telegram_channel_id', 0)) [cite: 730]
            [cite_start]if not tg_chat_id: continue [cite: 730]
            [cite_start]label = _truncate_text(_get_attr(ch, 'title') or f"قناة {tg_chat_id}", 25) [cite: 730]
            [cite_start]status = "✅" if tg_chat_id in selected_ids else "☑️" [cite: 730]
            [cite_start]callback_data = CallbackBuilder.create(CallbackNamespace.PUBLICATION, "toggle", short_token, tg_chat_id, page) [cite: 730]
            [cite_start]rows.append([InlineKeyboardButton(f"{status} {label}", callback_data=callback_data)]) [cite: 731]
        [cite_start]nav_buttons = [] [cite: 731]
        [cite_start]if page > 1: nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, "nav", short_token, page - 1))) [cite: 731]
        [cite_start]if total_pages > 1: nav_buttons.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop")) [cite: 731]
        [cite_start]if page < total_pages: nav_buttons.append(InlineKeyboardButton("➡️", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, "nav", short_token, page + 1))) [cite: 731]
        [cite_start]if nav_buttons: rows.append(nav_buttons) [cite: 731]
        [cite_start]rows.append([InlineKeyboardButton("🚀 نشر المحدد", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.CONFIRM, short_token)), InlineKeyboardButton("⬅️ عودة", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.BACK, short_token))]) [cite: 731]
        [cite_start]return InlineKeyboardMarkup(rows) [cite: 731]
    [cite_start]except Exception as e: [cite: 732]
        [cite_start]logger.error(f"Error building channel picker: {e}", exc_info=True) [cite: 732]
        [cite_start]return InlineKeyboardMarkup([[InlineKeyboardButton("❌ خطأ - عودة", callback_data=CallbackBuilder.create(CallbackNamespace.PUBLICATION, CallbackAction.BACK, review_token[:12]))]]) [cite: 732]

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/keyboards.py ---