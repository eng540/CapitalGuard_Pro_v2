# --- START OF FILE: src/capitalguard/application/services/trade_service.py ---
# ... (imports including OrderType)
from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType

class TradeService:
    # ... (init and other methods)

    def create_and_publish_recommendation(
        self, asset: str, side: str, market: str, entry: float, 
        stop_loss: float, targets: List[float], notes: Optional[str], 
        user_id: Optional[str], order_type: str, live_price: Optional[float] = None
    ) -> Recommendation:
        log.info(f"Creating rec for {asset} with order_type {order_type}")
        
        order_type_enum = OrderType(order_type.upper())
        
        # For Market orders, entry is initially symbolic (e.g., 0)
        # and status becomes ACTIVE immediately.
        if order_type_enum == OrderType.MARKET:
            if not live_price:
                raise ValueError("Live price is required for Market orders.")
            status = RecommendationStatus.ACTIVE
            entry = live_price # The official entry is the current market price
        else:
            status = RecommendationStatus.PENDING

        self._validate_sl_vs_entry(side, entry, stop_loss)
        self._validate_targets(side, entry, targets)
        
        rec_to_save = Recommendation(
            asset=Symbol(asset), side=Side(side), entry=Price(entry),
            stop_loss=Price(stop_loss), targets=Targets(targets),
            order_type=order_type_enum, status=status,
            market=market, notes=notes, user_id=user_id
        )

        # If it's an active market order, set activation time now
        if rec_to_save.status == RecommendationStatus.ACTIVE:
            rec_to_save.activated_at = datetime.utcnow()

        # ... (Rest of the saving and publishing logic is largely the same)
        saved_rec = self.repo.add(rec_to_save)
        # ...
        return saved_rec
    # ... (other methods updated to use Enum correctly)
# --- END OF FILE ---