# --- START OF FILE: src/capitalguard/application/services/trade_service.py ---
import logging
from typing import List, Optional
from datetime import datetime

from capitalguard.domain.entities import Recommendation
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import RecommendationRepoPort, NotifierPort
from capitalguard.config import settings

class TradeService:
    def __init__(self, repo: RecommendationRepoPort, notifier: Optional[NotifierPort] = None) -> None:
        self.repo = repo
        self.notifier = notifier

    def create(self, asset: str, side: str, entry: float, stop_loss: float,
               targets: List[float], user_id: Optional[str] = None) -> Recommendation:
        rec = Recommendation(
            asset=Symbol(asset),
            side=Side(side),
            entry=Price(entry),
            stop_loss=Price(stop_loss),
            targets=Targets(targets),
            user_id=user_id,
            channel_id=None,
        )
        saved = self.repo.add(rec)

        if self.notifier:
            try:
                result = getattr(self.notifier, "post_recommendation_card", None)
                if callable(result):
                    posted = self.notifier.post_recommendation_card(saved)  # type: ignore
                    if posted:
                        ch_id, msg_id = posted
                        saved.channel_id = ch_id
                        saved.message_id = msg_id
                        saved.published_at = datetime.utcnow()
                        try:
                            set_msg = getattr(self.repo, "set_channel_message", None)
                            if callable(set_msg):
                                saved = self.repo.set_channel_message(saved.id, ch_id, msg_id, saved.published_at)  # type: ignore
                            else:
                                saved = self.repo.update(saved)
                        except Exception:
                            saved = self.repo.update(saved)
                else:
                    self.notifier.send_message(text=f"#REC{saved.id:04d} {saved.asset.value} published", chat_id=settings.TELEGRAM_CHAT_ID)
            except Exception as e:
                logging.error(f"Failed to post recommendation card for rec #{saved.id}: {e}")
        return saved

    def close(self, rec_id: int, exit_price: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError("Recommendation not found")
        rec.close(exit_price)
        saved = self.repo.update(rec)

        if self.notifier:
            try:
                edit = getattr(self.notifier, "edit_recommendation_card", None)
                if callable(edit):
                    ok = self.notifier.edit_recommendation_card(saved)  # type: ignore
                    if not ok:
                        self.notifier.send_message(text=f"✅ Closed — #REC{saved.id:04d} • {saved.asset.value} @ {exit_price:g}", chat_id=settings.TELEGRAM_CHAT_ID)
                else:
                    self.notifier.send_message(text=f"✅ Closed — #REC{saved.id:04d} • {saved.asset.value} @ {exit_price:g}", chat_id=settings.TELEGRAM_CHAT_ID)
            except Exception as e:
                logging.error(f"Failed to update channel card for rec #{saved.id}: {e}")
        return saved

    def list_open(self, channel_id: int | None = None) -> List[Recommendation]:
        return self.repo.list_open(channel_id)

    def list_all(self, channel_id: int | None = None) -> List[Recommendation]:
        return self.repo.list_all(channel_id)
# --- END OF FILE ---