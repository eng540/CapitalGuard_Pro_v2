# --- src/capitalguard/infrastructure/db/models/__init__.py ---
"""
This file makes the 'models' directory a package and ensures all SQLAlchemy ORM
models are discoverable by Alembic and the application[cite: 2565].
"""

from .base import Base # [cite: 2571]
from .auth import User, UserType # [cite: 2567]
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
) # [cite: 2585]
# ✅ NEW: Import parsing models
from .parsing import ParsingTemplate, ParsingAttempt # 

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
    "ParsingTemplate", # 
    "ParsingAttempt", # 
]
# --- END of models init ---