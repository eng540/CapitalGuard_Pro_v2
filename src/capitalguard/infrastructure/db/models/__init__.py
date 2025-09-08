# --- START OF FILE: src/capitalguard/infrastructure/db/models/__init__.py ---
# This file makes the 'models' directory a package and ensures all models are discoverable.
from .base import Base
from .auth import User, Role, UserRole
from .recommendation import RecommendationORM
# ✅ New: expose Channel model so Alembic and the app can discover it
from .channel import Channel

__all__ = ["Base", "User", "Role", "UserRole", "RecommendationORM", "Channel"]
# --- END OF FILE ---