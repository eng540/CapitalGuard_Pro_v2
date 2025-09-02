# --- START OF FILE: src/capitalguard/application/services/trade_service.py ---
import logging
from typing import List, Optional
from datetime import datetime
from capitalguard.domain.entities import Recommendation
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import RecommendationRepoPort, NotifierPort
from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard, analyst_control_panel_keyboard

log = logging.getLogger(__name__)

class TradeService:
    def __init__(self, repo: RecommendationRepoPort, notifier: NotifierPort):
        self.repo = repo
        self.notifier = notifier

    def _validate_sl_vs_entry(self, side: str, entry: float, sl: float):
        # ... (unchanged)
        side_upper = side.upper()
        if side_upper == "LONG" and not (sl < entry):
            raise ValueError("For LONG trades, Stop Loss must be less than the Entry price.")
        if side_upper == "SHORT" and not (sl > entry):
            raise ValueError("For SHORT trades, Stop Loss must be greater than the Entry price.")

    def _validate_targets(self, side: str, entry: float, tps: List[float]):
        # ... (unchanged)
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
        user_id: Optional[str]
    ) -> Recommendation:
        # ... (unchanged from last version)
        log.info(f"Attempting to create recommendation for {asset} by analyst {user_id}")
        self._validate_sl_vs_entry(side, entry, stop_loss)
        self._validate_targets(side, entry, targets)
        rec_to_save = Recommendation(
            asset=Symbol(asset), side=Side(side), entry=Price(entry),
            stop_loss=Price(stop_loss), targets=Targets(targets),
            market=market, notes=notes, user_id=user_id
        )
        try:
            saved_rec = self.repo.add(rec_to_save)
            log.info(f"Successfully saved recommendation #{saved_rec.id} to DB.")
        except Exception as e:
            log.error(f"Critical: DB save failed before publishing. Aborting. Error: {e}", exc_info=True)
            raise RuntimeError("Failed to save to the database.")
        public_keyboard = public_channel_keyboard(saved_rec.id)
        posted_location = self.notifier.post_recommendation_card(saved_rec, keyboard=public_keyboard)
        if posted_location:
            channel_id, message_id = posted_location
            saved_rec.channel_id = channel_id
            saved_rec.message_id = message_id
            self.repo.update(saved_rec)
        else:
            log.error(f"Failed to publish card to Telegram channel for rec #{saved_rec.id}.")
            self.notifier.send_admin_alert(f"Failed to publish rec #{saved_rec.id}. It is saved in the DB but not on the channel.")
        if user_id and user_id.isdigit():
            analyst_keyboard = analyst_control_panel_keyboard(saved_rec.id)
            self.notifier.send_private_message(
                chat_id=int(user_id),
                rec=saved_rec,
                keyboard=analyst_keyboard,
                text_header="🚀 Published! Here is your private control panel:"
            )
            log.info(f"Sent private control panel to analyst {user_id} for rec #{saved_rec.id}")
        return saved_rec

    def close(self, rec_id: int, exit_price: float) -> Recommendation:
        # ... (unchanged)
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError(f"Recommendation {rec_id} not found.")
        rec.close(exit_price)
        updated_rec = self.repo.update(rec)
        public_keyboard = public_channel_keyboard(updated_rec.id)
        self.notifier.edit_recommendation_card(updated_rec, keyboard=public_keyboard)
        return updated_rec

    def list_open(self) -> List[Recommendation]:
        # ... (unchanged)
        return self.repo.list_open()

    def list_all(self, symbol: Optional[str] = None, status: Optional[str] = None) -> List[Recommendation]:
        # ... (unchanged)
        return self.repo.list_all(symbol=symbol, status=status)

    # ✅ --- NEW METHODS FOR THE CONTROL PANEL ---

    def move_sl_to_be(self, rec_id: int) -> Optional[Recommendation]:
        """Moves the Stop Loss to the Entry price."""
        rec = self.repo.get(rec_id)
        if not rec or rec.status.upper() == "CLOSED":
            return None
        
        # Move SL to entry price
        rec.stop_loss = rec.entry
        # Add a note about the action
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        note = f"\n- SL moved to BE on {timestamp}."
        rec.notes = (rec.notes or "") + note
        
        updated_rec = self.repo.update(rec)
        
        # Update cards
        public_keyboard = public_channel_keyboard(updated_rec.id)
        self.notifier.edit_recommendation_card(updated_rec, keyboard=public_keyboard)
        
        return updated_rec

    def add_partial_close_note(self, rec_id: int) -> Optional[Recommendation]:
        """Adds a note indicating a partial close action."""
        rec = self.repo.get(rec_id)
        if not rec or rec.status.upper() == "CLOSED":
            return None
            
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        note = f"\n- 50% of position closed on {timestamp} to secure profits."
        rec.notes = (rec.notes or "") + note
        
        updated_rec = self.repo.update(rec)

        # Update cards
        public_keyboard = public_channel_keyboard(updated_rec.id)
        self.notifier.edit_recommendation_card(updated_rec, keyboard=public_keyboard)
        
        return updated_rec
# --- END OF FILE ---