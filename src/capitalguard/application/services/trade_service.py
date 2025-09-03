# --- START of FILE: src/capitalguard/application/services/trade_service.py ---
import logging
from typing import List, Optional
from datetime import datetime
from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import RecommendationRepoPort, NotifierPort
from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard, analyst_control_panel_keyboard

log = logging.getLogger(__name__)

class TradeService:
    def __init__(self, repo: RecommendationRepoPort, notifier: NotifierPort):
        self.repo = repo
        self.notifier = notifier

    def _update_cards(self, rec: Recommendation):
        """Private helper to update public and private cards after a change."""
        public_keyboard = public_channel_keyboard(rec.id)
        self.notifier.edit_recommendation_card(rec, keyboard=public_keyboard)
        if rec.user_id and rec.user_id.isdigit():
            analyst_keyboard = analyst_control_panel_keyboard(rec.id)
            self.notifier.send_private_message(
                chat_id=int(rec.user_id),
                rec=rec,
                keyboard=analyst_keyboard,
                text_header="✅ Recommendation updated successfully:"
            )

    def _validate_sl_vs_entry(self, side: str, entry: float, sl: float):
        side_upper = side.upper()
        if side_upper == "LONG" and not (sl < entry):
            raise ValueError("For LONG trades, Stop Loss must be less than the Entry price.")
        if side_upper == "SHORT" and not (sl > entry):
            raise ValueError("For SHORT trades, Stop Loss must be greater than the Entry price.")

    def _validate_targets(self, side: str, entry: float, tps: List[float]):
        if not tps:
            raise ValueError("At least one target price is required.")
        side_upper = side.upper()
        if side_upper == "LONG":
            if not all(tp > entry for tp in tps):
                raise ValueError("For LONG trades, all targets must be greater than the Entry price.")
        elif side_upper == "SHORT":
            if not all(tp < entry for tp in tps):
                raise ValueError("For SHORT trades, all targets must be less than the Entry price.")

    def create_and_publish_recommendation(
        self, asset: str, side: str, market: str, entry: float, 
        stop_loss: float, targets: List[float], notes: Optional[str], 
        user_id: Optional[str], order_type: str, live_price: Optional[float] = None
    ) -> Recommendation:
        log.info(f"Creating recommendation for {asset} with order_type={order_type} by user={user_id}")
        
        order_type_enum = OrderType(order_type.upper())
        
        # Determine initial status and entry price based on order type
        if order_type_enum == OrderType.MARKET:
            if not live_price:
                raise ValueError("Live price is required for Market orders to set the entry price.")
            status = RecommendationStatus.ACTIVE
            final_entry = live_price  # The official entry is the current market price
        else: # LIMIT or STOP_MARKET
            status = RecommendationStatus.PENDING
            final_entry = entry

        # Validate business rules with the final entry price
        self._validate_sl_vs_entry(side, final_entry, stop_loss)
        self._validate_targets(side, final_entry, targets)
        
        rec_to_save = Recommendation(
            asset=Symbol(asset), side=Side(side), entry=Price(final_entry),
            stop_loss=Price(stop_loss), targets=Targets(targets),
            order_type=order_type_enum, status=status,
            market=market, notes=notes, user_id=user_id
        )

        # If it's an active market order, set activation time immediately
        if rec_to_save.status == RecommendationStatus.ACTIVE:
            rec_to_save.activated_at = datetime.utcnow()

        saved_rec = self.repo.add(rec_to_save)
        
        public_keyboard = public_channel_keyboard(saved_rec.id)
        posted_location = self.notifier.post_recommendation_card(saved_rec, keyboard=public_keyboard)
        
        if posted_location:
            channel_id, message_id = posted_location
            saved_rec.channel_id = channel_id
            saved_rec.message_id = message_id
            self.repo.update(saved_rec) # Save the message_id
        else:
            self.notifier.send_admin_alert(f"Failed to publish rec #{saved_rec.id} to channel.")
        
        if user_id and user_id.isdigit():
            analyst_keyboard = analyst_control_panel_keyboard(saved_rec.id)
            self.notifier.send_private_message(
                chat_id=int(user_id), rec=saved_rec, keyboard=analyst_keyboard,
                text_header="🚀 Published! Here is your private control panel:"
            )
        
        return saved_rec

    def close(self, rec_id: int, exit_price: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec: raise ValueError(f"Recommendation {rec_id} not found.")
        rec.close(exit_price)
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        return updated_rec

    def list_open(self) -> List[Recommendation]:
        return self.repo.list_open()

    def list_all(self, symbol: Optional[str] = None, status: Optional[str] = None) -> List[Recommendation]:
        return self.repo.list_all(symbol=symbol, status=status)

    def move_sl_to_be(self, rec_id: int) -> Optional[Recommendation]:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED: return None
        rec.stop_loss = rec.entry
        note = f"\n- SL moved to BE on {datetime.now().strftime('%Y-%m-%d %H:%M')}."
        rec.notes = (rec.notes or "") + note
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        return updated_rec

    def add_partial_close_note(self, rec_id: int) -> Optional[Recommendation]:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED: return None
        note = f"\n- 50% of position closed on {datetime.now().strftime('%Y-%m-%d %H:%M')}."
        rec.notes = (rec.notes or "") + note
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        return updated_rec

    def update_sl(self, rec_id: int, new_sl: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED:
            raise ValueError("Recommendation not found or is closed.")
        self._validate_sl_vs_entry(rec.side.value, rec.entry.value, new_sl)
        rec.stop_loss = Price(new_sl)
        rec.notes = (rec.notes or "") + f"\n- SL updated to {new_sl}."
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        return updated_rec

    def update_targets(self, rec_id: int, new_targets: List[float]) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec or rec.status == RecommendationStatus.CLOSED:
            raise ValueError("Recommendation not found or is closed.")
        self._validate_targets(rec.side.value, rec.entry.value, new_targets)
        rec.targets = Targets(new_targets)
        targets_str = ", ".join(map(str, new_targets))
        rec.notes = (rec.notes or "") + f"\n- TPs updated to [{targets_str}]."
        updated_rec = self.repo.update(rec)
        self._update_cards(updated_rec)
        return updated_rec
# --- END OF FILE ---