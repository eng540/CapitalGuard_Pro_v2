# --- START OF FILE: src/capitalguard/application/services/risk_service.py ---
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
    """حساب حجم الصفقة واحترام stepSize/tickSize/minNotional (تقريبي)."""
    exec_spot: any
    exec_futu: any

    def _round_step(self, value: float, step: float) -> float:
        if step <= 0: return value
        return math.floor(value / step) * step

    def _round_tick(self, value: float, tick: float) -> float:
        if tick <= 0: return value
        return round(self._round_step(value, tick), 8)

    def _filters(self, info: Dict[str, Any]) -> tuple[float, float, float]:
        step, tick, min_notional = 0.0, 0.0, 0.0
        for f in info.get("filters", []):
            t = f.get("filterType")
            if t == "LOT_SIZE":
                step = float(f.get("stepSize", 0))
            elif t in ("PRICE_FILTER","PRICE_FILTER "):
                tick = float(f.get("tickSize", 0))
            elif t in ("MIN_NOTIONAL","NOTIONAL"):
                min_notional = float(f.get("minNotional", 0))
        return step, tick, min_notional

    def compute_qty(self, *, symbol: str, side: str, market: str, account_usdt: float, risk_pct: float, entry: float, sl: float) -> SizingResult:
        side = side.upper()
        is_spot = str(market or "Spot").lower().startswith("spot")
        bex = self.exec_spot if is_spot else self.exec_futu
        info = bex.exchange_info(symbol) or {}
        step, tick, min_notional = self._filters(info)

        risk_usdt = account_usdt * (max(0.0, risk_pct) / 100.0)
        price_diff = abs((entry - sl) if side == "LONG" else (sl - entry))
        if price_diff <= 0:
            raise ValueError("Invalid SL vs Entry for sizing")
        raw_qty = risk_usdt / price_diff
        qty = self._round_step(raw_qty, step or 0.000001)
        notional = qty * entry
        if min_notional and notional < min_notional:
            qty = self._round_step(min_notional / entry, step or 0.000001)
            notional = qty * entry

        entry_rounded = self._round_tick(entry, tick or 0.000001)
        return SizingResult(qty=qty, notional=notional, risk_usdt=risk_usdt, entry=entry_rounded, sl=sl,
                            side=side, symbol=symbol.upper(), step_size=step or 0.0, tick_size=tick or 0.0)
# --- END OF FILE ---