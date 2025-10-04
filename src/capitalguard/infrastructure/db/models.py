# src/capitalguard/infrastructure/db/models.py (Updated for v3.0)

from .models.base import Base
from .models.auth import User, UserType
from .models.recommendation import (
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
    "UserTradeStatus",
]