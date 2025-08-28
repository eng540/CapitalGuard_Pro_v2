from __future__ import annotations
from typing import List, Optional, Any, Iterable
from pydantic import BaseModel, ConfigDict, field_validator
from datetime import datetime
from decimal import Decimal

def _to_str(v: Any) -> Optional[str]:
    if v is None: return None
    if hasattr(v, "value"):
        try: return str(getattr(v, "value"))
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
    for attr in ("to_list", "values", "targets", "all"):
        if hasattr(v, attr):
            seq = getattr(v, attr)
            seq = seq() if callable(seq) else seq
            try: return [ _to_float(x) for x in list(seq) ]
            except Exception: pass
    if isinstance(v, Iterable) and not isinstance(v, (str, bytes)):
        return [ _to_float(x) for x in list(v) ]
    return None

class RecommendationIn(BaseModel):
    asset: str
    side: str
    entry: float
    stop_loss: float
    targets: List[float]
    channel_id: Optional[int] = None
    user_id: Optional[str] = None

class RecommendationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, json_encoders={Decimal: float})

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

    @field_validator("asset", mode="before")
    @classmethod
    def _v_asset(cls, v): 
        s = _to_str(v); 
        if s is None: raise ValueError("invalid asset"); 
        return s

    @field_validator("side", mode="before")
    @classmethod
    def _v_side(cls, v): 
        s = _to_str(v); 
        if s is None: raise ValueError("invalid side"); 
        return s

    @field_validator("entry", "stop_loss", "exit_price", mode="before")
    @classmethod
    def _v_price(cls, v): 
        f = _to_float(v); 
        if f is None: raise ValueError("invalid price"); 
        return f

    @field_validator("targets", mode="before")
    @classmethod
    def _v_targets(cls, v): 
        arr = _to_float_list(v); 
        if arr is None: raise ValueError("invalid targets"); 
        return arr

class CloseIn(BaseModel):
    exit_price: float

class ReportOut(BaseModel):
    total: int
    open: int
    closed: int
    top_asset: Optional[str] = None