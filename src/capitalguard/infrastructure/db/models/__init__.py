# --- src/capitalguard/infrastructure/db/models/__init__.py ---
"""
This file makes the 'models' directory a package and ensures all SQLAlchemy ORM
models are discoverable by Alembic and the application.
✅ THE FIX (R1-S1 HOTFIX 10): Added UserTradeEvent to the imports and __all__ list.
"""

from .base import Base 
from .auth import User, UserType 
from .recommendation import (
    RecommendationStatusEnum,
    OrderTypeEnum,
    ExitStrategyEnum,
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
from .parsing import ParsingTemplate, ParsingAttempt 
from .watched_channel import WatchedChannel
# ✅ R1-S1 HOTFIX 10: Import the new event model
from .user_trade_event import UserTradeEvent

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
    "UserTradeStatusEnum", 
    "ParsingTemplate", 
    "ParsingAttempt", 
    "WatchedChannel",
    # ✅ R1-S1 HOTFIX 10: Export the new event model
    "UserTradeEvent",
]
# --- END of models init ---