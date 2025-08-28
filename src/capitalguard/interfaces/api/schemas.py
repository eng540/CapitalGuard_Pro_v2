#--- START OF FILE: src/capitalguard/interfaces/api/schemas.py ---
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, ConfigDict

class RecommendationIn(BaseModel):
    asset: str
    side: str
    entry: float
    stop_loss: float
    targets: List[float]
    channel_id: Optional[int] = None
    user_id: Optional[str] = None

class RecommendationOut(BaseModel):
    # ✅ الإصلاح النهائي: هذا السطر يسمح لـ Pydantic بقراءة البيانات
    # من كائنات بايثون المخصصة (وليس فقط القواميس).
    model_config = ConfigDict(from_attributes=True)

    id: int
    asset: str
    side: str
    entry: float
    stop_loss: float
    targets: List[float]
    status: str
    channel_id: Optional[int] = None
    user_id: Optional[str] = None

class CloseIn(BaseModel):
    exit_price: float

class ReportOut(BaseModel):
    total: int
    open: int
    closed: int
    top_asset: Optional[str] = None
#--- END OF FILE ---```