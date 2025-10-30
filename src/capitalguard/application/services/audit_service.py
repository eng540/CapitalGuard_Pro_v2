# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/audit_service.py ---
# src/capitalguard/application/services/audit_service.py (v2.3 - Final Syntax Fix)
"""
AuditService - A dedicated service for retrieving and formatting historical data
for auditing and review purposes.
It provides read-only access to system events.
✅ FIX: Corrected DI signature to match usage in boot.py and to reflect architectural intent:
       Accepts a concrete instance of RecommendationRepository (rec_repo).
       Accepts the Class type for UserRepository (user_repo_class) for runtime instantiation.
✅ FIX: Closed unterminated f-string literal on line 40 which caused module import failure (SyntaxError).
✅ READY FOR PRODUCTION.
"""

from typing import List, Dict, Any
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository
from capitalguard.domain.ports import RecommendationRepoPort

class AuditService:
    # ✅ FIX: Use precise type hints that reflect the architectural intent in boot.py
    def __init__(self, rec_repo: RecommendationRepoPort, user_repo_class: type[UserRepository]):
        self.rec_repo = rec_repo
        self.user_repo_class = user_repo_class

    def get_recommendation_events_for_user(self, rec_id: int, user_telegram_id: str) -> List[Dict[str, Any]]:
        """
        Retrieves all events for a given recommendation if the user has permission.
        Only the recommendation’s owning analyst can view its event log.
        """
        with session_scope() as session:
            # Instantiate the UserRepository using the class reference
            user_repo = self.user_repo_class(session)
            user = user_repo.find_by_telegram_id(int(user_telegram_id))
            if not user:
                raise ValueError("User not found.")

            # Use the injected repository instance (which doesn't hold a session)
            # but pass the current session to the method call.
            rec = self.rec_repo.get(session, rec_id)
            if not rec:
                # ✅ SYNTAX FIX: Completed the f-string literal.
                raise ValueError(f"Recommendation #{rec_id} not found.")

            # ✅ FIX: Compare analyst_id (not user_id)
            if rec.analyst_id != user.id:
                raise ValueError("Access denied. You do not own this recommendation.")

            events_orm = self.rec_repo.get_events_for_recommendation(session, rec_id)
            formatted_events = []
            for event in events_orm:
                formatted_events.append({
                    "timestamp": event.event_timestamp.strftime("%Y-%m-%d %H:%M:%S") if event.event_timestamp else "N/A",
                    "type": event.event_type,
                    "data": event.event_data or {}
                })
            return formatted_events
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/audit_service.py ---
