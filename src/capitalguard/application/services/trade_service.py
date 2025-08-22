from typing import List, Optional
from capitalguard.domain.entities import Recommendation
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import RecommendationRepoPort, NotifierPort

class TradeService:
    def __init__(self, repo: RecommendationRepoPort, notifier: NotifierPort | None = None) -> None:
        self.repo = repo
        self.notifier = notifier

    def create(self, asset: str, side: str, entry: float, stop_loss: float, targets: List[float], channel_id: int | None = None, user_id: int | None = None) -> Recommendation:
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
            msg = (f"📌 <b>New Recommendation</b>\n"
                   f"Asset: <b>{saved.asset.value}</b> | Side: <b>{saved.side.value}</b>\n"
                   f"Entry: <b>{saved.entry.value}</b> | SL: <b>{saved.stop_loss.value}</b>\n"
                   f"Targets: <b>{', '.join(map(str, saved.targets.values))}</b>\n"
                   f"ID: <code>{saved.id}</code>")
            self.notifier.publish(msg)
        return saved

    def close(self, rec_id: int, exit_price: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError("Recommendation not found")
        rec.close(exit_price)
        saved = self.repo.update(rec)
        if self.notifier:
            self.notifier.publish(f"✅ <b>Closed</b> | ID: <code>{saved.id}</code>")
        return saved

    def list_open(self, channel_id: int | None = None):
        return self.repo.list_open(channel_id)

    def list_all(self, channel_id: int | None = None):
        return self.repo.list_all(channel_id)
