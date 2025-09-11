# --- START OF FILE: src/capitalguard/infrastructure/db/models/__init__.py ---
"""
Lightweight initializer for ORM models.

Best practice:
- Keep this file minimal to avoid import-time crashes during Alembic setup.
- Import models explicitly from their modules where needed.
  e.g.:
    from capitalguard.infrastructure.db.models.recommendation import RecommendationORM
"""

from .base import Base

__all__ = ["Base"]
# --- END OF FILE ---