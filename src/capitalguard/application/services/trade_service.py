from typing import List, Optional
from capitalguard.domain.entities import Recommendation
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import RecommendationRepoPort, NotifierPort
from capitalguard.interfaces.formatting.telegram_templates import format_signal, format_closed

class TradeService:
    def __init__(self, repo: RecommendationRepoPort, notifier: Optional[NotifierPort] = None) -> None:
        self.repo = repo
        self.notifier = notifier

    def create(self, asset: str, side: str, entry: float, stop_loss: float,
               targets: List[float], channel_id: Optional[int] = None,
               user_id: Optional[int] = None) -> Recommendation:
        rec = Recommendation(
            asset=Symbol(asset),
            side=Side(side),
            entry=Price(entry),
            stop_loss=Price(stop_loss),
            targets=Targets(targets),
            channel_id=channel_id,
            user_id=user_id,
        )
        saved = self.repo.add(rec)
        if self.notifier:
            try:
                msg = format_signal(
                    rec_id=saved.id,
                    symbol=saved.asset.value,
                    side=saved.side.value,
                    entry=saved.entry.value,
                    sl=saved.stop_loss.value,
                    targets=saved.targets.values,
                    notes=None,
                )
                self.notifier.publish(msg)  # يذهب إلى TELEGRAM_CHANNEL_ID
            except Exception:
                pass
        return saved

    def close(self, rec_id: int, exit_price: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError("Recommendation not found")
        rec.close(exit_price)
        saved = self.repo.update(rec)
        if self.notifier:
            try:
                self.notifier.publish(format_closed(saved.id, saved.asset.value, exit_price))
            except Exception:
                pass
        return saved

    def list_open(self, channel_id: Optional[int] = None):
        return self.repo.list_open(channel_id)

    def list_all(self, channel_id: Optional[int] = None):
        return self.repo.list_all(channel_id)