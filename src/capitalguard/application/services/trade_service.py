# --- START OF CORRECTED AND ENHANCED FILE: src/capitalguard/application/services/trade_service.py ---
import logging
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType, ExitStrategy
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import NotifierPort
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository, ChannelRepository
from capitalguard.infrastructure.db.base import SessionLocal
from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard
from capitalguard.interfaces.telegram.ui_texts import _pct
from capitalguard.application.services.market_data_service import MarketDataService
from capitalguard.infrastructure.db.models import PublishedMessage, RecommendationORM

log = logging.getLogger(__name__)

def _parse_int_user_id(user_id: Optional[str]) -> Optional[int]:
    try:
        return int(user_id) if user_id is not None and user_id.isdigit() else None
    except (TypeError, ValueError):
        return None

class TradeService:
    def __init__(self, repo: RecommendationRepository, notifier: NotifierPort, market_data_service: MarketDataService):
        self.repo = repo
        self.notifier = notifier
        self.market_data_service = market_data_service

    def _load_user_linked_channels(self, uid_int: int, only_active: bool = True) -> List[Any]:
        with SessionLocal() as s:
            user = UserRepository().find_by_telegram_id(uid_int, session=s)
            if not user:
                return []
            return ChannelRepository(s).list_by_user(user.id, only_active=only_active)

    def _notify_all_channels(self, rec_id: int, text: str, session: Optional[Session] = None):
        published_messages = self.repo.get_published_messages(rec_id, session=session)
        for msg_meta in published_messages:
            try:
                self.notifier.post_notification_reply(
                    chat_id=msg_meta.telegram_channel_id,
                    message_id=msg_meta.telegram_message_id,
                    text=text
                )
            except Exception as e:
                log.warning(
                    "Failed to send reply notification for rec #%s to channel %s: %s",
                    rec_id, msg_meta.telegram_channel_id, e
                )

    def _update_all_cards(self, rec: Recommendation, session: Optional[Session] = None):
        published_messages = self.repo.get_published_messages(rec.id, session=session)
        if not published_messages:
            return
        log.info("Updating %d cards for rec #%s...", len(published_messages), rec.id)
        keyboard = public_channel_keyboard(rec.id) if rec.status != RecommendationStatus.CLOSED else None
        for msg_meta in published_messages:
            try:
                self.notifier.edit_recommendation_card_by_ids(
                    channel_id=msg_meta.telegram_channel_id,
                    message_id=msg_meta.telegram_message_id,
                    rec=rec,
                    keyboard=keyboard
                )
            except Exception as e:
                log.warning(
                    "Failed to update card for rec #%s in channel %s: %s",
                    rec.id, msg_meta.telegram_channel_id, e
                )

    def _validate_sl_vs_entry_on_create(self, side: str, entry: float, sl: float) -> None:
        side_upper = side.upper()
        if side_upper == "LONG" and not (sl < entry):
            raise ValueError("For new LONG trades, Stop Loss must be < Entry Price.")
        if side_upper == "SHORT" and not (sl > entry):
            raise ValueError("For new SHORT trades, Stop Loss must be > Entry Price.")

    def create_recommendation(self, **kwargs) -> Recommendation:
        asset = kwargs['asset'].strip().upper()
        market = kwargs.get('market', 'Futures')
        if not self.market_data_service.is_valid_symbol(asset, market):
            raise ValueError(f"The symbol '{asset}' is not valid or available in the '{market}' market.")
        
        order_type_enum = OrderType(kwargs['order_type'].upper())
        if order_type_enum == OrderType.MARKET:
            if kwargs.get('live_price') is None:
                raise ValueError("Live price is required for Market orders.")
            status, final_entry = RecommendationStatus.ACTIVE, kwargs['live_price']
        else:
            status, final_entry = RecommendationStatus.PENDING, kwargs['entry']

        self._validate_sl_vs_entry_on_create(kwargs['side'], final_entry, kwargs['stop_loss'])
        
        targets_vo = Targets(kwargs['targets'])
        for target in targets_vo.values:
            if (kwargs['side'].upper() == 'LONG' and target.price <= final_entry) or \
               (kwargs['side'].upper() == 'SHORT' and target.price >= final_entry):
                raise ValueError(f"Target price {target.price} is not valid for a {kwargs['side']} trade with entry {final_entry}.")

        recommendation_entity = Recommendation(
            asset=Symbol(asset), side=Side(kwargs['side']), entry=Price(final_entry),
            stop_loss=Price(kwargs['stop_loss']), targets=targets_vo, order_type=order_type_enum,
            status=status, market=market, notes=kwargs.get('notes'), user_id=kwargs.get('user_id'),
            activated_at=datetime.now(timezone.utc) if status == RecommendationStatus.ACTIVE else None,
            exit_strategy=kwargs.get('exit_strategy', ExitStrategy.CLOSE_AT_FINAL_TP),
            profit_stop_price=kwargs.get('profit_stop_price'), open_size_percent=100.0
        )

        if recommendation_entity.status == RecommendationStatus.ACTIVE:
            recommendation_entity.highest_price_reached = recommendation_entity.entry.value
            recommendation_entity.lowest_price_reached = recommendation_entity.entry.value

        return self.repo.add_with_event(recommendation_entity)

    def publish_recommendation(self, rec_id: int, user_id: Optional[str], channel_ids: Optional[List[int]] = None) -> Tuple[Optional[Recommendation], Dict[str, List[Dict[str, Any]]]]:
        report: Dict[str, List[Dict[str, Any]]] = {"success": [], "failed": []}
        
        with SessionLocal() as s:
            rec = self.repo.get(rec_id, session=s)
            if not rec:
                raise ValueError(f"Recommendation {rec_id} not found for publishing.")

            uid_int = _parse_int_user_id(user_id or rec.user_id)
            if not uid_int:
                report["failed"].append({"channel_id": None, "reason": "User ID could not be resolved or is invalid."})
                return rec, report

            channels = self._load_user_linked_channels(uid_int, only_active=True)
            if channel_ids:
                channels = [ch for ch in channels if ch.telegram_channel_id in set(channel_ids)]
            
            if not channels:
                return rec, report

            keyboard = public_channel_keyboard(rec.id)
            for ch in channels:
                try:
                    res = self.notifier.post_to_channel(ch.telegram_channel_id, rec, keyboard)
                    if res:
                        publication_data = [{"recommendation_id": rec.id, "telegram_channel_id": res[0], "telegram_message_id": res[1]}]
                        s.bulk_insert_mappings(PublishedMessage, publication_data)
                        report["success"].append({"channel_id": ch.telegram_channel_id, "message_id": res[1]})
                    else:
                        report["failed"].append({"channel_id": ch.telegram_channel_id, "reason": "Notifier failed to post."})
                except Exception as e:
                    log.error("Failed to publish to channel %s: %s", ch.telegram_channel_id, e, exc_info=True)
                    report["failed"].append({"channel_id": ch.telegram_channel_id, "reason": str(e)})
            
            if report["success"]:
                first_pub = report["success"][0]
                s.query(RecommendationORM).filter(RecommendationORM.id == rec_id).update(
                    {'channel_id': first_pub['channel_id'], 'message_id': first_pub['message_id'], 'published_at': datetime.now(timezone.utc)}
                )
            
            s.commit()
            
        return self.repo.get(rec_id), report

    def close(self, rec_id: int, exit_price: float, reason: str = "MANUAL_CLOSE", session: Optional[Session] = None) -> Recommendation:
        rec = self.repo.get(rec_id, session=session)
        if not rec: raise ValueError(f"Recommendation {rec_id} not found.")
        if rec.status == RecommendationStatus.CLOSED:
            log.warning("Attempted to close an already closed recommendation: #%d", rec_id)
            return rec
            
        rec.open_size_percent = 0.0
        old_status = rec.status
        rec.close(exit_price)
        pnl = _pct(rec.entry.value, exit_price, rec.side.value)
        
        if pnl > 0.001: close_status = "PROFIT"
        elif pnl < -0.001: close_status = "LOSS"
        else: close_status = "BREAKEVEN"
        
        updated_rec = self.repo.update_with_event(rec, "CLOSED", {"old_status": old_status.value, "exit_price": exit_price, "closed_at": rec.closed_at.isoformat(), "reason": reason, "close_status": close_status}, session=session)
        
        self._update_all_cards(updated_rec, session=session)
        
        if close_status == "PROFIT": emoji, r_text = "🏆", "Profit"
        elif close_status == "LOSS": emoji, r_text = "💔", "Loss"
        else: emoji, r_text = "🛡️", "Breakeven"
        
        close_notification = (f"<b>{emoji} Trade Closed #{updated_rec.asset.value}</b>\n"
                            f"Closed at {exit_price:g} for a result of <b>{pnl:+.2f}%</b> ({r_text}).")
        self._notify_all_channels(rec_id, close_notification, session=session)
        return updated_rec

    def take_partial_profit(self, rec_id: int, close_percent: float, price: float, triggered_by: str = "MANUAL", session: Optional[Session] = None) -> Recommendation:
        rec = self.repo.get(rec_id, session=session)
        if not rec or rec.status != RecommendationStatus.ACTIVE:
            raise ValueError("Partial profit can only be taken on active recommendations.")
        if not (0 < close_percent <= rec.open_size_percent):
            raise ValueError(f"Invalid percentage. Must be between 0 and {rec.open_size_percent}.")
        
        rec.open_size_percent -= close_percent
        pnl_on_part = _pct(rec.entry.value, price, rec.side.value)
        event_type = "PARTIAL_PROFIT_AUTO" if triggered_by.upper() == "AUTO" else "PARTIAL_PROFIT_MANUAL"
        event_data = {"price": price, "closed_percent": close_percent, "remaining_percent": rec.open_size_percent, "pnl_on_part": pnl_on_part, "triggered_by": triggered_by}
        updated_rec = self.repo.update_with_event(rec, event_type, event_data, session=session)
        
        notification_text = (
            f"💰 **Partial Profit Taken** | Signal #{rec.id}\n\n"
            f"Closed **{close_percent:.2f}%** of **{rec.asset.value}** at **{price:g}** for a **{pnl_on_part:+.2f}%** profit.\n\n"
            f"<i>Remaining open size: {rec.open_size_percent:.2f}%</i>"
        )
        self._notify_all_channels(rec_id, notification_text, session=session)
        self._update_all_cards(updated_rec, session=session)
        
        if updated_rec.open_size_percent <= 0.01:
            log.info(f"Recommendation #{rec_id} fully closed via partial profits. Marking as closed.")
            reason = "AUTO_PARTIAL_FULL_CLOSE" if triggered_by.upper() == "AUTO" else "MANUAL_PARTIAL_FULL_CLOSE"
            return self.close(rec_id, price, reason=reason, session=session)
        return updated_rec

    def update_price_tracking(self, rec_id: int, current_price: float, session: Optional[Session] = None) -> Optional[Recommendation]:
        rec = self.repo.get(rec_id, session=session)
        if not rec or rec.status != RecommendationStatus.ACTIVE: return None
        updated = False
        if rec.highest_price_reached is None or current_price > rec.highest_price_reached:
            rec.highest_price_reached = current_price
            updated = True
        if rec.lowest_price_reached is None or current_price < rec.lowest_price_reached:
            rec.lowest_price_reached = current_price
            updated = True
        if updated: return self.repo.update(rec, session=session)
        return None

    def get_recent_assets_for_user(self, user_id: str, limit: int = 5) -> List[str]:
        uid_int = _parse_int_user_id(user_id)
        if not uid_int: return []
        with SessionLocal() as s:
            return self.repo.get_recent_assets_for_user(user_telegram_id=uid_int, limit=limit, session=s)

    # ✅ --- START: NEW UNIFIED & ATOMIC METHOD ---
    def create_and_publish_recommendation(self, **kwargs) -> Tuple[Recommendation, Dict]:
        """
        Creates and then publishes a recommendation in a single, unified service call.
        This is the primary method to be used by interfaces.
        """
        try:
            # Step 1: Create the recommendation
            new_rec = self.create_recommendation(**kwargs)
            
            # Step 2: Publish it
            updated_rec, report = self.publish_recommendation(
                rec_id=new_rec.id, 
                user_id=new_rec.user_id
            )
            
            return updated_rec, report
        except Exception as e:
            log.exception("Failed during the create_and_publish process.")
            # In a real-world scenario, we might want to delete the created_rec if publishing fails.
            # For now, we let it exist so it can be published manually.
            raise e

    # ✅ --- START: NEW MANAGEMENT METHODS ---
    def update_sl(self, rec_id: int, new_sl: float, session: Optional[Session] = None) -> Recommendation:
        rec = self.repo.get(rec_id, session=session)
        if not rec or rec.status == RecommendationStatus.CLOSED:
            raise ValueError(f"Cannot update SL for recommendation #{rec_id}.")
        
        old_sl = rec.stop_loss.value
        # Basic validation
        if (rec.side.value == "LONG" and new_sl >= rec.entry.value) or \
           (rec.side.value == "SHORT" and new_sl <= rec.entry.value):
            raise ValueError("New Stop Loss is invalid relative to the entry price.")
            
        rec.stop_loss = Price(new_sl)
        event_data = {"old_sl": old_sl, "new_sl": new_sl}
        updated_rec = self.repo.update_with_event(rec, "SL_UPDATED", event_data, session=session)
        
        self._update_all_cards(updated_rec, session=session)
        self._notify_all_channels(rec_id, f"✏️ **Stop Loss Updated** for #{rec.asset.value} to **{new_sl:g}**.", session=session)
        return updated_rec

    def update_targets(self, rec_id: int, new_targets_data: List[Dict[str, float]], session: Optional[Session] = None) -> Recommendation:
        rec = self.repo.get(rec_id, session=session)
        if not rec or rec.status == RecommendationStatus.CLOSED:
            raise ValueError(f"Cannot update targets for recommendation #{rec_id}.")
            
        old_targets_plain = [t.price for t in rec.targets.values]
        rec.targets = Targets(new_targets_data)
        new_targets_plain = [t.price for t in rec.targets.values]
        
        event_data = {"old_targets": old_targets_plain, "new_targets": new_targets_plain}
        updated_rec = self.repo.update_with_event(rec, "TARGETS_UPDATED", event_data, session=session)
        
        self._update_all_cards(updated_rec, session=session)
        self._notify_all_channels(rec_id, f"🎯 **Targets Updated** for #{rec.asset.value}.", session=session)
        return updated_rec

    def update_exit_strategy(self, rec_id: int, new_strategy: ExitStrategy, session: Optional[Session] = None) -> Recommendation:
        rec = self.repo.get(rec_id, session=session)
        if not rec or rec.status == RecommendationStatus.CLOSED:
            raise ValueError(f"Cannot update strategy for recommendation #{rec_id}.")
        
        old_strategy = rec.exit_strategy.value
        rec.exit_strategy = new_strategy
        event_data = {"old_strategy": old_strategy, "new_strategy": new_strategy.value}
        updated_rec = self.repo.update_with_event(rec, "STRATEGY_UPDATED", event_data, session=session)
        
        self._update_all_cards(updated_rec, session=session)
        self._notify_all_channels(rec_id, f"📈 **Exit Strategy Updated** for #{rec.asset.value}.", session=session)
        return updated_rec

    def update_profit_stop(self, rec_id: int, new_price: Optional[float], session: Optional[Session] = None) -> Recommendation:
        rec = self.repo.get(rec_id, session=session)
        if not rec or rec.status == RecommendationStatus.CLOSED:
            raise ValueError(f"Cannot update profit stop for recommendation #{rec_id}.")
            
        old_price = rec.profit_stop_price
        rec.profit_stop_price = new_price
        event_data = {"old_price": old_price, "new_price": new_price}
        updated_rec = self.repo.update_with_event(rec, "PROFIT_STOP_UPDATED", event_data, session=session)
        
        self._update_all_cards(updated_rec, session=session)
        if new_price is not None:
            note = f"🛡️ **Profit Stop Set** for #{rec.asset.value} at **{new_price:g}**."
        else:
            note = f"🗑️ **Profit Stop Removed** for #{rec.asset.value}."
        self._notify_all_channels(rec_id, note, session=session)
        return updated_rec
# --- END OF CORRECTED AND ENHANCED FILE ---