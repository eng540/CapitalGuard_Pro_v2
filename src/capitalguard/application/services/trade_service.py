# --- START OF FILE: src/capitalguard/application/services/trade_service.py ---
import logging
from typing import List, Optional
from capitalguard.domain.entities import Recommendation
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import RecommendationRepoPort, NotifierPort

log = logging.getLogger(__name__)

class TradeService:
    def __init__(self, repo: RecommendationRepoPort, notifier: NotifierPort):
        self.repo = repo
        self.notifier = notifier

    # ... (الدوال الخاصة بالتحقق وإنشاء التوصيات تبقى كما هي) ...
    # --- Private Validation Helpers ---
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

    # --- Core Business Logic ---
    def create_and_publish_recommendation(
        self, asset: str, side: str, market: str, entry: float,
        stop_loss: float, targets: List[float], notes: Optional[str],
        user_id: Optional[str]
    ) -> Recommendation:
        # ... (هذا الجزء لا يتغير)
        log.info(f"Attempting to create recommendation for {asset} by user {user_id}")
        self._validate_sl_vs_entry(side, entry, stop_loss)
        self._validate_targets(side, entry, targets)
        temp_rec = Recommendation(
            asset=Symbol(asset), side=Side(side), entry=Price(entry),
            stop_loss=Price(stop_loss), targets=Targets(targets),
            market=market, notes=notes, user_id=user_id
        )
        posted_location = self.notifier.post_recommendation_card(temp_rec)
        if not posted_location:
            log.error("Failed to publish card to Telegram channel. Aborting creation.")
            raise RuntimeError("Could not publish to Telegram. The recommendation was not saved.")
        channel_id, message_id = posted_location
        temp_rec.channel_id = channel_id
        temp_rec.message_id = message_id
        try:
            saved_rec = self.repo.add(temp_rec)
            log.info(f"Successfully created and saved recommendation #{saved_rec.id}")
        except Exception as e:
            log.error(f"DB save failed after publishing message {message_id}. Critical error!", exc_info=True)
            self.notifier.send_admin_alert(
                f"CRITICAL ERROR: Failed to save recommendation for {asset} to DB after posting message {message_id}. "
                f"Please manually delete the message from the channel. Error: {e}"
            )
            raise
        try:
            success = self.notifier.edit_recommendation_card(saved_rec)
            if not success:
                log.warning(
                    f"Failed to edit Telegram message {message_id} for recommendation #{saved_rec.id}. "
                    "The card in the channel will be missing its ID."
                )
        except Exception as e:
            log.error(
                f"An exception occurred while editing Telegram message {message_id} for rec #{saved_rec.id}.",
                exc_info=True
            )
        return saved_rec

    def close(self, rec_id: int, exit_price: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError(f"Recommendation {rec_id} not found.")
        
        rec.close(exit_price)
        updated_rec = self.repo.update(rec)
        
        self.notifier.edit_recommendation_card(updated_rec)
        
        return updated_rec

    # ✅ إضافة: الدوال الجديدة التي كانت مفقودة
    def list_open(self) -> List[Recommendation]:
        """Returns a list of all recommendations with OPEN status."""
        return self.repo.list_open()

    def list_all(self, symbol: Optional[str] = None, status: Optional[str] = None) -> List[Recommendation]:
        """Returns a list of all recommendations, with optional filters."""
        return self.repo.list_all(symbol=symbol, status=status)
#--- END OF FILE ---