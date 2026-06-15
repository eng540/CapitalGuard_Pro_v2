# --- START OF FINAL, COMPLETE, AND ARCHITECTURALLY-CORRECT FILE (Version 12.0.0) ---
# src/capitalguard/application/services/report_service.py

from collections import Counter
from typing import Dict, Any
from sqlalchemy.orm import Session

from capitalguard.domain.ports import RecommendationRepoPort

class ReportService:
    def __init__(self, repo: RecommendationRepoPort) -> None:
        self.repo = repo

    # ✅ UoW FIX: The method now accepts a 'session' argument and passes it down.
    def summary(self, session: Session, channel_id: int | None = None) -> Dict[str, Any]:
        """
        Generates a summary report using the provided database session.
        """
        items = self.repo.list_all(session, channel_id=channel_id) # Pass the session here
        total = len(items)
        by_asset = Counter([i.asset.value for i in items])
        top_asset, top_count = (by_asset.most_common(1)[0] if by_asset else (None, 0))
        
        # Note: The original code had a bug here, comparing status to "OPEN".
        # The domain entity uses RecommendationStatus.ACTIVE or PENDING.
        # This logic assumes the repository returns domain entities.
        open_count = sum(1 for i in items if i.status.value in ("ACTIVE", "PENDING"))
        closed_count = total - open_count
        
        return {
            "total": total,
            "open": open_count,
            "closed": closed_count,
            "top_asset": top_asset,
            "top_count": top_count,
        }

# --- END OF FINAL, COMPLETE, AND ARCHITECTURALLY-CORRECT FILE ---