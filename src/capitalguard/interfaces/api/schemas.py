# --- START OF FILE: src/capitalguard/interfaces/api/schemas.py ---
from __future__ import annotations
from typing import List, Any
from pydantic import BaseModel, ConfigDict, field_validator
from datetime import datetime

def _to_str(v: Any) -> str | None:
    if v is None: return None
    if hasattr(v, "value"): return str(v.value)
    return str(v)

def _to_float(v: Any) -> float:
    if hasattr(v, "value"): v = getattr(v, "value")
    return float(v)

def _to_float_list(v: Any) -> List[float]:
    if v is None: return []
    if hasattr(v, "values"): v = getattr(v, "values")
    if isinstance(v, str): v = [x for x in v.replace(",", " ").split() if x]
    return [float(x) for x in v]

class RecommendationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    asset: str
    side: str
    entry: float
    stop_loss: float
    targets: List[float]
    status: str
    exit_price: float | None = None
    channel_id: int | None = None
    message_id: int | None = None
    published_at: datetime | None = None
    closed_at: datetime | None = None
    user_id: int | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("asset", mode="before")
    def _v_asset(cls, v): return _to_str(v) or ""
    @field_validator("side", mode="before")
    def _v_side(cls, v): return _to_str(v) or ""
    @field_validator("entry", "stop_loss", mode="before")
    def _v_price(cls, v): return _to_float(v)
    @field_validator("targets", mode="before")
    def _v_targets(cls, v): return _to_float_list(v)

class CloseIn(BaseModel):
    exit_price: float

class ReportRow(BaseModel):
    id: int
    asset: str
    side: str
    status: str
    entry: float
    stop_loss: float
    exit_price: float | None = None
    created_at: datetime
    closed_at: datetime | None = None
    pnl_percent: float | None = None
    rr_actual: float | None = None
# --- END OF FILE ---