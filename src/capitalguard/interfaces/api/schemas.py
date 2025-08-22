from pydantic import BaseModel, Field
from typing import List, Optional

class RecommendationIn(BaseModel):
    asset: str = Field(..., examples=["BTCUSDT"])
    side: str = Field(..., examples=["LONG","SHORT","SPOT"])
    entry: float
    stop_loss: float
    targets: List[float]
    channel_id: Optional[int] = None
    user_id: Optional[int] = None

class CloseIn(BaseModel):
    exit_price: float

class RecommendationOut(BaseModel):
    id: int
    asset: str
    side: str
    entry: float
    stop_loss: float
    targets: List[float]
    status: str
    channel_id: Optional[int] = None
    user_id: Optional[int] = None

class ReportOut(BaseModel):
    total: int
    open: int
    closed: int
    top_asset: Optional[str]
    top_count: int
