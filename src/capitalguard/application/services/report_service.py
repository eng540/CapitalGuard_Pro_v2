from collections import Counter
from typing import Dict, Any
from capitalguard.domain.ports import RecommendationRepoPort

class ReportService:
    def __init__(self, repo: RecommendationRepoPort) -> None:
        self.repo = repo

    def summary(self, channel_id: int | None = None) -> Dict[str, Any]:
        items = self.repo.list_all(channel_id)
        total = len(items)
        by_asset = Counter([i.asset.value for i in items])
        top_asset, top_count = (by_asset.most_common(1)[0] if by_asset else (None, 0))
        open_count = sum(1 for i in items if i.status == "OPEN")
        closed_count = total - open_count
        return {
            "total": total,
            "open": open_count,
            "closed": closed_count,
            "top_asset": top_asset,
            "top_count": top_count,
        }
