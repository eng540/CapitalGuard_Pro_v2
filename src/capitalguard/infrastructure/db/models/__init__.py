# src/capitalguard/infrastructure/db/models/__init__.py (v25.0 - FINAL & UNIFIED)
"""
This file makes the 'models' directory a package and ensures all SQLAlchemy ORM
models are discoverable by Alembic and the application.
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
]