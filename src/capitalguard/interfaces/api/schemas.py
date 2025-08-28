from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, ConfigDict
from datetime import datetime

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

class CloseIn(BaseModel):
    exit_price: float

class ReportOut(BaseModel):
    total: int
    open: int
    closed: int
    top_asset: Optional[str] = None