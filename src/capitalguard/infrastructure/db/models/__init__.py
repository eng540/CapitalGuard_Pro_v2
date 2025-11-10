# --- src/capitalguard/infrastructure/db/models/__init__.py ---
"""
This file makes the 'models' directory a package and ensures all SQLAlchemy ORM
models are discoverable by Alembic and the application.
✅ THE FIX (R1-S1): Added WatchedChannel to the imports and __all__ list.
"""

from .base import Base 
from .auth import User, UserType 
from .recommendation import (
    RecommendationStatusEnum,
    OrderTypeEnum,
    ExitStrategyEnum,
    UserTradeStatus,
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
    "UserTradeStatus",
    # ✅ NEW: Export parsing models
    "ParsingTemplate", 
    "ParsingAttempt", 
    # ✅ R1-S1: Export the new WatchedChannel model
    "WatchedChannel",
]
# --- END of models init ---