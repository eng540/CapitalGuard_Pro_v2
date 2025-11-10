# src/capitalguard/infrastructure/db/models.py (Updated for R1-S1)
"""
✅ THE FIX (R1-S1 HOTFIX): Corrected import path.
    - Changed import from 'UserTradeStatus' to 'UserTradeStatusEnum' to match
      the updated recommendation.py model file, resolving the ImportError.
"""

from .models.base import Base
from .models.auth import User, UserType
from .models.recommendation import (
    RecommendationStatusEnum,
    OrderTypeEnum,
    ExitStrategyEnum,
    # ✅ R1-S1 HOTFIX: Import the correct Enum name
    UserTradeStatus as UserTradeStatusEnum, 
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
from .models.parsing import ParsingTemplate, ParsingAttempt

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
    # ✅ R1-S1 HOTFIX: Export the correct Enum name
    "UserTradeStatusEnum", 
    "WatchedChannel", 
    "ParsingTemplate",
    "ParsingAttempt",
]