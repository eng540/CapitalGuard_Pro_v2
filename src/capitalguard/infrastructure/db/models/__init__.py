# --- src/capitalguard/infrastructure/db/models/__init__.py ---
"""
This file makes the 'models' directory a package and ensures all SQLAlchemy ORM
models are discoverable by Alembic and the application.
✅ THE FIX (R1-S1 HOTFIX 3): Corrected the import name.
    - The file `recommendation.py` now imports `UserTradeStatus as UserTradeStatusEnum`.
    - We must therefore import `UserTradeStatusEnum` from it, NOT `UserTradeStatus`.
    - This finally resolves the ImportError that was crashing Alembic.
"""

from .base import Base 
from .auth import User, UserType 
from .recommendation import (
    RecommendationStatusEnum,
    OrderTypeEnum,
    ExitStrategyEnum,
    # ✅ R1-S1 HOTFIX 3: Import the *correct* name that recommendation.py now holds
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
    # ✅ R1-S1 HOTFIX 3: Export the correct Enum name
    "UserTradeStatusEnum", 
    # ✅ NEW: Export parsing models
    "ParsingTemplate", 
    "ParsingAttempt", 
    # ✅ R1-S1: Export the new WatchedChannel model
    "WatchedChannel",
]
# --- END of models init ---