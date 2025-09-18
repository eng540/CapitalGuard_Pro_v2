# --- START OF FINAL, CONFIRMED AND PRODUCTION-READY FILE (Version 8.1.4) ---
# src/capitalguard/application/services/risk_service.py

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any
import math

@dataclass
class SizingResult:
    qty: float
    notional: float
    risk_usdt: float
    entry: float
    sl: float
    side: str
    symbol: str
    step_size: float
    tick_size: float

@dataclass
class RiskService:
    """
    Calculates trade size based on risk parameters, respecting exchange filters
    like stepSize, tickSize, and minNotional.
    """
    exec_spot: Any
    exec_futu: Any

    def _round_step(self, value: float, step: float) -> float:
        """Rounds a quantity down to the nearest multiple of step size."""
        if step <= 0: return value
        return math.floor(value / step) * step

    def _round_tick(self, value: float, tick: float) -> float:
        """Rounds a price to the nearest multiple of tick size."""
        if tick <= 0: return value
        # A simple round is sufficient for tick size.
        precision = abs(int(math.log10(tick))) if tick > 0 else 8
        return round(value, precision)

    def _filters(self, info: Dict[str, Any]) -> tuple[float, float, float]:
        """Extracts LOT_SIZE, PRICE_FILTER, and MIN_NOTIONAL filters from exchange info."""
        step, tick, min_notional = 0.0, 0.0, 0.0
        for f in info.get("filters", []):
            t = f.get("filterType")
            if t == "LOT_SIZE":
                step = float(f.get("stepSize", 0))
            elif t in ("PRICE_FILTER", "PRICE_FILTER "):
                tick = float(f.get("tickSize", 0))
            elif t in ("MIN_NOTIONAL", "NOTIONAL"):
                min_notional = float(f.get("minNotional", 0))
        return step, tick, min_notional

    async def compute_qty_async(self, *, symbol: str, side: str, market: str, account_usdt: float, risk_pct: float, entry: float, sl: float) -> SizingResult:
        """
        Asynchronously computes the appropriate trade quantity.
        It fetches exchange info asynchronously to avoid blocking.
        """
        side = side.upper()
        is_spot = str(market or "Spot").lower().startswith("spot")
        bex = self.exec_spot if is_spot else self.exec_futu
        
        # Asynchronously fetch exchange info
        info = await bex.exchange_info(symbol) or {}
        step, tick, min_notional = self._filters(info)

        risk_usdt = account_usdt * (max(0.0, risk_pct) / 100.0)
        price_diff = abs(entry - sl)
        if price_diff <= 0:
            raise ValueError("Invalid SL vs Entry for sizing: price difference must be greater than zero.")
            
        raw_qty = risk_usdt / price_diff
        
        # Ensure quantity respects the minimum notional value if specified
        if min_notional > 0 and (raw_qty * entry) < min_notional:
            raw_qty = (min_notional / entry) * 1.001 # Add a small buffer to ensure it passes the filter

        qty = self._round_step(raw_qty, step or 0.000001)
        notional = qty * entry
        
        entry_rounded = self._round_tick(entry, tick or 0.000001)
        
        return SizingResult(
            qty=qty, 
            notional=notional, 
            risk_usdt=risk_usdt, 
            entry=entry_rounded, 
            sl=sl,
            side=side, 
            symbol=symbol.upper(), 
            step_size=step or 0.0, 
            tick_size=tick or 0.0
        )

# --- END OF FINAL, CONFIRMED AND PRODUCTION-READY FILE (Version 8.1.4) ---