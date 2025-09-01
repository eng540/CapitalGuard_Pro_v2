#--- START OF FILE: src/capitalguard/application/services/trade_service.py ---
from typing import List, Optional
from capitalguard.domain.entities import Recommendation
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from capitalguard.domain.ports import RecommendationRepoPort, NotifierPort

class TradeService:
    def __init__(self, repo: RecommendationRepoPort, notifier: NotifierPort):
        self.repo = repo
        self.notifier = notifier

    def create(self, asset: str, side: str, market: str, entry: float,
               stop_loss: float, targets: List[float], notes: Optional[str],
               user_id: Optional[str]) -> Recommendation:
        
        rec = Recommendation(
            asset=Symbol(asset), side=Side(side), entry=Price(entry),
            stop_loss=Price(stop_loss), targets=Targets(targets),
            market=market, notes=notes, user_id=user_id
        )
        
        # 1. حفظ أولي للحصول على ID
        saved_rec = self.repo.add(rec)
        
        # 2. نشر البطاقة في القناة
        posted_location = self.notifier.post_recommendation_card(saved_rec)
        
        # 3. تحديث التوصية بمعرف الرسالة إذا نجح النشر
        if posted_location:
            channel_id, message_id = posted_location
            return self.repo.set_channel_message(saved_rec.id, channel_id, message_id)
        
        return saved_rec

    def close(self, rec_id: int, exit_price: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError(f"Recommendation {rec_id} not found.")
        
        rec.close(exit_price)
        updated_rec = self.repo.update(rec)
        
        # تحديث البطاقة في القناة لتعكس الإغلاق
        self.notifier.edit_recommendation_card(updated_rec)
        
        return updated_rec

    def update_stop_loss(self, rec_id: int, new_sl: float) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError(f"Recommendation {rec_id} not found.")
        
        rec.stop_loss = Price(new_sl)
        updated_rec = self.repo.update(rec)
        
        self.notifier.edit_recommendation_card(updated_rec)
        return updated_rec
        
    def update_targets(self, rec_id: int, new_targets: List[float]) -> Recommendation:
        rec = self.repo.get(rec_id)
        if not rec:
            raise ValueError(f"Recommendation {rec_id} not found.")
            
        rec.targets = Targets(new_targets)
        updated_rec = self.repo.update(rec)
        
        self.notifier.edit_recommendation_card(updated_rec)
        return updated_rec

    def get(self, rec_id: int) -> Optional[Recommendation]:
        return self.repo.get(rec_id)

    def list_open(self) -> List[Recommendation]:
        return self.repo.list_open()

    def list_all(self, symbol: Optional[str] = None, status: Optional[str] = None) -> List[Recommendation]:
        return self.repo.list_all(symbol=symbol, status=status)
#--- END OF FILE ---