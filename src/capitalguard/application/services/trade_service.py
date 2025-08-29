# --- START OF FILE: src/capitalguard/application/services/trade_service.py ---
from __future__ import annotations
from typing import List, Optional, Tuple

from capitalguard.domain.entities import Recommendation
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier

class TradeService:
    def __init__(self, repo: RecommendationRepository, notifier: TelegramNotifier):
        self.repo = repo
        self.notifier = notifier

    # ---------- CRUD ----------
    def create(
        self,
        *,
        asset: str,
        side: str,
        entry: float,
        stop_loss: float,
        targets: List[float],
        user_id: Optional[str] = None,
        market: Optional[str] = None,   # "Spot" | "Futures"
        notes: Optional[str] = None,
    ) -> Recommendation:
        rec = Recommendation(
            asset=Symbol(asset),
            side=Side(side.upper()),
            entry=Price(entry),
            stop_loss=Price(stop_loss),
            targets=Targets(targets),
            user_id=user_id,
            market=(market or "Futures").title(),
            notes=(notes or None),
        )
        rec = self.repo.add(rec)
        # نشر بطاقة القناة
        posted = self.notifier.post_recommendation_card(rec)
        if posted:
            ch_id, msg_id = posted
            rec = self.repo.set_channel_message(rec.id, ch_id, msg_id)
        return rec

    def get(self, rec_id: int) -> Recommendation | None:
        return self.repo.get(rec_id)

    def list_open(self, symbol: Optional[str] = None) -> List[Recommendation]:
        items = self.repo.list_open()
        if symbol:
            s = symbol.upper()
            items = [r for r in items if str(getattr(r.asset, "value", r.asset)).upper() == s]
        return items

    def list_all(self, channel_id: int | None = None, symbol: Optional[str] = None, status: Optional[str] = None) -> List[Recommendation]:
        items = self.repo.list_all(channel_id)
        if symbol:
            s = symbol.upper()
            items = [r for r in items if str(getattr(r.asset, "value", r.asset)).upper() == s]
        if status:
            st = status.upper()
            items = [r for r in items if r.status.upper() == st]
        return items

    # ---------- Updates ----------
    def update_stop_loss(self, rec_id: int, new_stop: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError("Recommendation not found")
        rec.stop_loss = Price(new_stop)
        rec = self.repo.update(rec)
        self.notifier.edit_recommendation_card(rec)
        return rec

    def update_targets(self, rec_id: int, new_targets: List[float]) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError("Recommendation not found")
        rec.targets = Targets(new_targets)
        rec = self.repo.update(rec)
        self.notifier.edit_recommendation_card(rec)
        return rec

    def close(self, rec_id: int, exit_price: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError("Recommendation not found")
        rec.close(exit_price)
        rec = self.repo.update(rec)
        self.notifier.edit_recommendation_card(rec)
        return rec
# --- END OF FILE ---