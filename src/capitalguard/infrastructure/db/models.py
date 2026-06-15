# src/capitalguard/infrastructure/db/models.py (Updated for R1-S1)
"""
✅ THE FIX (R1-S1 HOTFIX 3): Corrected import path.
    - `models/__init__.py` now correctly exports `UserTradeStatusEnum`.
    - This file now correctly imports and exports `UserTradeStatusEnum`.
"""

from .models.base import Base
from .models.auth import User, UserType
from .models.recommendation import (
    RecommendationStatusEnum,
    OrderTypeEnum,
    ExitStrategyEnum,
    # ✅ R1-S1 HOTFIX 3: Import the correct Enum name
    UserTradeStatusEnum, 
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
    # ✅ R1-S1 HOTFIX 3: Export the correct Enum name
    "UserTradeStatusEnum", 
    "WatchedChannel", 
    "ParsingTemplate",
    "ParsingAttempt",
]