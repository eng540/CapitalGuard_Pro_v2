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
from .dedup import DedupLedger
from .publication_delivery import PublicationDelivery, PublicationDeliveryOperation, PublicationDeliveryStatus
from .identity import ScopedIdentityCounter
from .channel_catalog import ChannelCatalog
from .recommendation_channel_ref import RecommendationChannelRef
from .entitlement import EntitlementGrant, SubscriptionLedgerEntry
from .historical_signal import (
    HistoricalImportBatch,
    HistoricalSignalEvidence,
    HistoricalSignal,
    HistoricalSignalEvent,
    HistoricalSignalAttribution,
)
from .historical_forwarding import HistoricalForwardReceipt
from .historical_shadow_channel import HistoricalShadowChannel
from .temporal_forward_decision import TemporalForwardDecision
from .web_command_audit import WebCommandAudit

# Backward-compatible export used by existing service tests and integrations.
UserTradeStatus = UserTradeStatusEnum

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
    "UserTradeStatus",
    "ParsingTemplate", 
    "ParsingAttempt", 
    "WatchedChannel",
    # ✅ R1-S1 HOTFIX 10: Export the new event model
    "UserTradeEvent",
    "DedupLedger",
    "PublicationDelivery",
    "PublicationDeliveryOperation",
    "PublicationDeliveryStatus",
    "ScopedIdentityCounter",
    "ChannelCatalog",
    "RecommendationChannelRef",
    "EntitlementGrant",
    "SubscriptionLedgerEntry",
    "HistoricalImportBatch",
    "HistoricalSignalEvidence",
    "HistoricalSignal",
    "HistoricalSignalEvent",
    "HistoricalSignalAttribution",
    "HistoricalForwardReceipt",
    "HistoricalShadowChannel",
    "TemporalForwardDecision",
    "WebCommandAudit",
]
# --- END of models init ---
