# --- START OF MODIFIED FILE: src/capitalguard/infrastructure/db/models/__init__.py ---
# This file makes the 'models' directory a package and ensures all models are discoverable.
from .base import Base
from .auth import User, Role, UserRole
from .recommendation import RecommendationORM
from .channel import Channel
from .published_message import PublishedMessage

# ✅ --- START: NEW MODEL IMPORT ---
# Expose the new RecommendationEvent model so Alembic and the app can discover it.
from .recommendation_event import RecommendationEvent
# ✅ --- END: NEW MODEL IMPORT ---


__all__ = [
    "Base", 
    "User", 
    "Role", 
    "UserRole", 
    "RecommendationORM", 
    "Channel", 
    "PublishedMessage",
    "RecommendationEvent"  # ✅ Add the new model to __all__
]
# --- END OF MODIFIED FILE ---