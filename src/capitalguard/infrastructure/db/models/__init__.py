# --- START OF CORRECTED FILE: src/capitalguard/infrastructure/db/models/__init__.py ---
# This file makes the 'models' directory a package and ensures all models are discoverable.
from .base import Base
from .auth import User, Role, UserRole
from .recommendation import RecommendationORM
from .channel import Channel
from .published_message import PublishedMessage
from .recommendation_event import RecommendationEvent # Make sure this file exists

__all__ = [
    "Base", 
    "User", 
    "Role", 
    "UserRole", 
    "RecommendationORM", 
    "Channel", 
    "PublishedMessage",
    "RecommendationEvent"
]
# --- END OF CORRECTED FILE ---