# --- START OF FINAL, CORRECTED, AND PRODUCTION-READY FILE (Version 9.3.0) ---
# src/capitalguard/interfaces/telegram/auth.py

import logging
from telegram import Update
from telegram.ext.filters import BaseFilter

# ✅ FIX: Import the specific repositories and SessionLocal for correct DB access
from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.infrastructure.db.base import SessionLocal

log = logging.getLogger(__name__)


class _DatabaseAuthFilter(BaseFilter):
    """
    A robust, DB-backed authentication filter.

    This filter ensures that a user record exists in the database for every
    Telegram user interacting with the bot. It correctly handles database
    sessions for each check, ensuring transactional integrity and preventing
    application-wide state issues.
    """

    def __init__(self) -> None:
        super().__init__(name="DB_Auth_Filter")
        # ❌ CRITICAL: Do NOT create repository instances here.
        # Repositories must be instantiated with a valid session inside the filter method.

    def filter(self, update: Update) -> bool:
        """
        This method is called by PTB for each incoming update.
        It checks for a user and creates one if not found, all within a safe DB session.
        """
        if not update or not getattr(update, "effective_user", None):
            log.debug("DB_Auth_Filter: Update has no effective_user; rejecting.")
            return False

        u = update.effective_user
        tg_id = u.id

        try:
            # ✅ BEST PRACTICE: Use a new, short-lived session for each filter check.
            # This ensures thread safety and proper transaction management.
            with SessionLocal() as session:
                user_repo = UserRepository(session)
                # The find_or_create method handles the logic of checking and creating the user.
                user_repo.find_or_create(
                    telegram_id=tg_id,
                    first_name=getattr(u, "first_name", None),
                )
            # If no exception was raised, the user exists or was created successfully.
            return True
        except Exception as e:
            # If any database error occurs, log it and deny access for this update.
            log.error(
                f"DB_Auth_Filter: Database error while ensuring user {tg_id} exists: {e}",
                exc_info=True
            )
            return False


# Create a single instance of the filter to be used throughout the application.
ALLOWED_USER_FILTER = _DatabaseAuthFilter()

# --- END OF FINAL, CORRECTED, AND PRODUCTION-READY FILE (Version 9.3.0) ---