# --- START OF FILE: src/capitalguard/interfaces/telegram/auth.py ---
from telegram import Update
from telegram.ext.filters import BaseFilter

# ✅ NEW: Import the UserRepository to check the database.
from capitalguard.infrastructure.db.user_repository import UserRepository

class _DatabaseAuthFilter(BaseFilter):
    """
    A custom filter that checks if a user is registered and active in the database.
    This replaces the old, static environment variable check.
    """
    def __init__(self):
        # The name is used for logging and debugging by the PTB library.
        super().__init__(name='DB_Auth_Filter')
        self.user_repo = UserRepository()

    def filter(self, update: Update) -> bool:
        """
        The core logic of the filter. It's called by PTB for each incoming update.
        """
        if not update.effective_user:
            return False
        
        user_id = update.effective_user.id
        # ✅ CORE CHANGE: Instead of checking a list, we query the database.
        is_authorized = self.user_repo.is_user_active(user_id)
        return is_authorized

# Create a single instance of the filter to be used across the application.
# This is the new, database-backed authentication filter.
ALLOWED_USER_FILTER = _DatabaseAuthFilter()
# --- END OF FILE ---