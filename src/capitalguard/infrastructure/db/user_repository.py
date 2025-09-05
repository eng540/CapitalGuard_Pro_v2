#--- START OF FILE: src/capitalguard/infrastructure/db/user_repository.py ---
from typing import Optional
import logging

from .base import SessionLocal
from .models import User

log = logging.getLogger(__name__)

class UserRepository:
    """Handles all database operations related to the User model."""

    def find_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """Finds a user by their Telegram ID."""
        with SessionLocal() as session:
            return session.query(User).filter(User.telegram_user_id == telegram_id).first()

    def register_user(self, telegram_id: int, user_type: str = 'analyst') -> User:
        """
        Creates a new user with the given Telegram ID.
        If the user already exists, it returns the existing user.
        """
        with SessionLocal() as session:
            existing_user = session.query(User).filter(User.telegram_user_id == telegram_id).first()
            if existing_user:
                log.info(f"User with telegram_id {telegram_id} already exists.")
                return existing_user

            new_user = User(telegram_user_id=telegram_id, user_type=user_type)
            session.add(new_user)
            session.commit()
            session.refresh(new_user)
            log.info(f"Successfully registered new user with telegram_id: {telegram_id}")
            return new_user

    def is_user_active(self, telegram_id: int) -> bool:
        """Checks if a user with the given telegram_id exists and is active."""
        user = self.find_by_telegram_id(telegram_id)
        return user is not None and user.is_active
#--- END OF FILE ---