# --- START OF FILE: src/capitalguard/application/services/price_service.py ---
from __future__ import annotations
from dataclasses import dataclass
from capitalguard.infrastructure.pricing.binance import BinancePricing

@dataclass
class PriceService:
    """طبقة رفيعة لتجريد مزوّد السعر (حاليًا BinancePricing)."""

    def get_preview_price(self, symbol: str, market: str) -> float | None:
        spot = (str(market or "Spot").lower().startswith("spot"))
        return BinancePricing.get_price(symbol, spot=spot)
# --- END OF FILE ---