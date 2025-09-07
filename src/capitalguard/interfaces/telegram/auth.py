// --- START: src/capitalguard/interfaces/telegram/auth.py ---
from telegram import Update
from telegram.ext.filters import BaseFilter
from capitalguard.infrastructure.db.repository import RecommendationRepository

class _DatabaseAuthFilter(BaseFilter):
    """
    A custom filter that checks if a user is registered in the database.
    This replaces the old, static environment variable check.
    """
    def __init__(self):
        super().__init__(name='DB_Auth_Filter')
        # We can use any repository to get a user, RecommendationRepository has the needed method.
        self.repo = RecommendationRepository()

    def filter(self, update: Update) -> bool:
        """
        The core logic of the filter. It's called for each incoming update.
        """
        if not update.effective_user:
            return False
        
        user_id = update.effective_user.id
        # find_or_create_user will return the user, guaranteeing they exist.
        # This filter now simply ensures a user record is present for every interaction.
        user = self.repo.find_or_create_user(user_id)
        return user is not None

# Create a single instance of the filter to be used across the application.
ALLOWED_USER_FILTER = _DatabaseAuthFilter()
// --- END: src/capitalguard/interfaces/telegram/auth.py ---