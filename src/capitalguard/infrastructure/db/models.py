# src/capitalguard/infrastructure/db/models.py (Updated for R1-S1)
"""
✅ THE FIX (R1-S1): Added WatchedChannel to the main export list.
       Updated UserTradeStatus import to reflect new domain logic.
"""

from .models.base import Base
from .models.auth import User, UserType
from .models.recommendation import (
    RecommendationStatusEnum,
    OrderTypeEnum,
    ExitStrategyEnum,
    UserTradeStatus, # ✅ R1-S1: This Enum is now expanded
    AnalystProfile,
    Channel,
    Recommendation,
    UserTrade,
    RecommendationEvent,
    Subscription,
    AnalystStats,
    PublishedMessage,
)
# ✅ R1-S1: Import the new model
from .models.watched_channel import WatchedChannel

# The old Role and UserRole are deprecated and no longer exported.
__all__ = [
    "Base",
    "User",
    "UserType",
    "AnalystProfile",
    "Channel",
    "Recommendation",
    "UserTrade",
    "RecommendationEvent",
    "Subscription",
    "AnalystStats",
    "PublishedMessage",
    "RecommendationStatusEnum",
    "OrderTypeEnum",
    "ExitStrategyEnum",
    "UserTradeStatus", # ✅ R1-S1: Exporting the expanded Enum
    "WatchedChannel", # ✅ R1-S1: Exporting the new model
]