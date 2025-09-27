# src/capitalguard/application/services/audit_service.py (New File)
"""
AuditService - A dedicated service for retrieving and formatting historical data
for auditing and review purposes. It provides read-only access to system events.
"""

from typing import List, Dict, Any

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository
from capitalguard.infrastructure.db.models import RecommendationEvent

class AuditService:
    def __init__(self, rec_repo: RecommendationRepository, user_repo_class: type[UserRepository]):
        self.rec_repo = rec_repo
        self.user_repo_class = user_repo_class

    def get_recommendation_events_for_user(self, rec_id: int, user_telegram_id: str) -> List[Dict[str, Any]]:
        """
        Retrieves the full, formatted event log for a specific recommendation,
        ensuring the user has permission to view it.
        """
        with session_scope() as session:
            # First, verify the user has access to this recommendation by fetching the recommendation itself.
            user_repo = self.user_repo_class(session)
            user = user_repo.find_by_telegram_id(int(user_telegram_id))
            if not user:
                raise ValueError("User not found.")
            
            rec = self.rec_repo.get(session, rec_id)
            # Note: rec.user_id from the entity is the telegram_user_id as a string
            if not rec or rec.user_id != str(user.telegram_user_id):
                raise ValueError(f"Recommendation #{rec_id} not found or access denied.")

            # If access is confirmed, fetch the events.
            events_orm = self.rec_repo.get_events_for_recommendation(session, rec_id)
            
            # Format the events into a clean list of dictionaries for display
            formatted_events = []
            for event in events_orm:
                formatted_events.append({
                    "timestamp": event.event_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "type": event.event_type,
                    "data": event.event_data
                })
            return formatted_events