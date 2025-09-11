# --- START OF FILE: src/capitalguard/infrastructure/db/models/__init__.py ---
"""
Lightweight package initializer for ORM models.

WHY:
- Alembic imports `capitalguard.infrastructure.db.models` during env setup.
- Keep this file minimal to avoid importing non-existing/heavy modules at startup.
- Import specific models explicitly at their usage sites instead of relying on package exports.
"""

from .base import Base

__all__ = ["Base"]
# --- END OF FILE ---