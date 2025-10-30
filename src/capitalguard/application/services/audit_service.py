# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/audit_service.py ---
# src/capitalguard/application/services/audit_service.py (v2.2 - DI Signature Fix)
"""
AuditService - A dedicated service for retrieving and formatting historical data
for auditing and review purposes.
It provides read-only access to system events.
✅ FIX: Corrected DI signature to match usage in boot.py and to reflect architectural intent:
       Accepts a concrete instance of RecommendationRepository (rec_repo).
       Accepts the Class type for UserRepository (user_repo_class) for runtime instantiation.
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
                raise ValueError(f"Recommendation #{rec_id}
