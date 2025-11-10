# --- src/capitalguard/infrastructure/db/models/__init__.py ---
"""
This file makes the 'models' directory a package and ensures all SQLAlchemy ORM
models are discoverable by Alembic and the application.
✅ THE FIX (R1-S1 HOTFIX): Corrected import path.
    - Changed import from 'UserTradeStatus' to 'UserTradeStatusEnum' to match
      the updated recommendation.py model file, resolving the ImportError.
"""

from .base import Base 
from .auth import User, UserType 
from .recommendation import (
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
# ✅ NEW: Import parsing models
from .parsing import ParsingTemplate, ParsingAttempt 
# ✅ R1-S1: Import the new WatchedChannel model
from .watched_channel import WatchedChannel

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
    # ✅ NEW: Export parsing models
    "ParsingTemplate", 
    "ParsingAttempt", 
    # ✅ R1-S1: Export the new WatchedChannel model
    "WatchedChannel",
]
# --- END of models init ---