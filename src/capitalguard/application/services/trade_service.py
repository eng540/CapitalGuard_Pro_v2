# --- START OF FILE: src/capitalguard/application/services/trade_service.py ---
# (imports and validation helpers remain the same)
import logging
from typing import List, Optional
from capitalguard.domain.entities import Recommendation
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import RecommendationRepoPort, NotifierPort
from capitalguard.interfaces.telegram.keyboards import public_channel_keyboard, analyst_control_panel_keyboard

log = logging.getLogger(__name__)

class TradeService:
    def __init__(self, repo: RecommendationRepoPort, notifier: NotifierPort):
        self.repo = repo
        self.notifier = notifier
    
    # ... (Validation helpers _validate_sl_vs_entry, _validate_targets are unchanged)

    def create_and_publish_recommendation(
        self, asset: str, side: str, market: str, entry: float,
        stop_loss: float, targets: List[float], notes: Optional[str],
        user_id: Optional[str] # This is the analyst's Telegram ID
    ) -> Recommendation:
        log.info(f"Attempting to create recommendation for {asset} by analyst {user_id}")

        self._validate_sl_vs_entry(side, entry, stop_loss)
        self._validate_targets(side, entry, targets)
        
        # 1. Create and save the entity first to get an ID.
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

        # 2. Post the final, clean card to the public channel.
        public_keyboard = public_channel_keyboard(saved_rec.id)
        posted_location = self.notifier.post_recommendation_card(saved_rec, keyboard=public_keyboard)
        
        if not posted_location:
            log.error(f"Failed to publish card to Telegram channel for rec #{saved_rec.id}. The recommendation is saved but not published.")
            # We don't raise an error here because the data is safe in the DB. We send an alert.
            self.notifier.send_admin_alert(f"Failed to publish rec #{saved_rec.id}. Please check.")
            # We still proceed to send the control panel to the analyst.
        else:
            # Update the message ID in the DB
            _, message_id = posted_location
            self.repo.set_channel_message(saved_rec.id, int(self.notifier.channel_id), message_id)

        # 3. Send the private control panel to the analyst.
        if user_id and user_id.isdigit():
            analyst_keyboard = analyst_control_panel_keyboard(saved_rec.id)
            self.notifier.send_private_message(
                chat_id=int(user_id), 
                rec=saved_rec, 
                keyboard=analyst_keyboard
            )
            log.info(f"Sent private control panel to analyst {user_id} for rec #{saved_rec.id}")

        return saved_rec
        
    # ... (close, list_open, list_all methods remain the same for now)
# --- END OF FILE ---