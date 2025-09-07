import logging
from telegram import Update
from telegram.ext.filters import BaseFilter
from capitalguard.infrastructure.db.repository import RecommendationRepository

log = logging.getLogger(__name__)


class _DatabaseAuthFilter(BaseFilter):
    """
    DB-only authentication filter:
      - Ensures a DB user exists for every Telegram user interacting with the bot.
      - Repository auto-generates placeholder email to satisfy NOT NULL constraints.
    """

    def __init__(self) -> None:
        super().__init__(name="DB_Auth_Filter")
        self.repo = RecommendationRepository()

    def filter(self, update: Update) -> bool:  # type: ignore[override]
        if not update or not getattr(update, "effective_user", None):
            log.debug("DB_Auth_Filter: No effective_user on update; rejecting.")
            return False

        u = update.effective_user
        tg_id = u.id

        try:
            # Pass simple profile fields (repo handles placeholder email)
            self.repo.find_or_create_user(
                tg_id,
                username=getattr(u, "username", None),
                first_name=getattr(u, "first_name", None),
                last_name=getattr(u, "last_name", None),
                user_type="trader",
            )
            return True
        except Exception as e:
            log.error("DB_Auth_Filter: DB error while ensuring user %s: %s", tg_id, e, exc_info=True)
            return False


ALLOWED_USER_FILTER = _DatabaseAuthFilter()