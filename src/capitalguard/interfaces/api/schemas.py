#--- START OF FILE: src/capitalguard/interfaces/api/schemas.py ---
from __future__ import annotations
from typing import List, Optional, Any, Iterable
from pydantic import BaseModel, ConfigDict, field_validator
from datetime import datetime
from decimal import Decimal

# -------- Helpers لتطبيع القيم القادمة من الدومين --------
def _to_str(v: Any) -> Optional[str]:
    if v is None: return None
    if hasattr(v, "value"):
        try: return str(v.value)
        except Exception: pass
    try: return str(v)
    except Exception: return None

def _to_float(v: Any) -> Optional[float]:
    if v is None: return None
    if isinstance(v, (int, float)): return float(v)
    if isinstance(v, Decimal): return float(v)
    if hasattr(v, "value"):
        vv = getattr(v, "value")
        if isinstance(vv, (int, float, Decimal)): return float(vv)
    try: return float(v)
    except Exception: return None

def _to_float_list(v: Any) -> Optional[List[float]]:
    if v is None: return None
    for attr in ("values", "to_list"):
        if hasattr(v, attr):
            try:
                seq = getattr(v, attr)
                return [ _to_float(x) for x in list(seq) ]
            except Exception: pass
    if isinstance(v, Iterable) and not isinstance(v, (str, bytes)):
        return [ _to_float(x) for x in list(v) ]
    return None

# -------- Schemas --------
class RecommendationIn(BaseModel):
    asset: str
    side: str
    entry: float
    stop_loss: float
    targets: List[float]
    channel_id: Optional[int] = None
    user_id: Optional[str] = None

class RecommendationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: Optional[int] = None
    asset: str
    side: str
    entry: float
    stop_loss: float
    targets: List[float]
    status: str
    channel_id: Optional[int] = None
    user_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    exit_price: Optional[float] = None
    closed_at: Optional[datetime] = None

    @field_validator("asset", "side", mode="before")
    @classmethod
    def _v_str(cls, v):
        s = _to_str(v)
        if s is None: raise ValueError("invalid string-like value")
        return s

    @field_validator("entry", "stop_loss", "exit_price", mode="before")
    @classmethod
    def _v_price(cls, v):
        f = _to_float(v)
        if f is None:
             # Allow exit_price to be None
            if v is None: return None
            raise ValueError("invalid price-like value")
        return f

    @field_validator("targets", mode="before")
    @classmethod
    def _v_targets(cls, v):
        arr = _to_float_list(v)
        if arr is None: raise ValueError("invalid targets-like value")
        return arr

class CloseIn(BaseModel):
    exit_price: float

class ReportOut(BaseModel):
    total: int
    open: int
    closed: int
    top_asset: Optional[str] = None
#--- END OF FILE ---