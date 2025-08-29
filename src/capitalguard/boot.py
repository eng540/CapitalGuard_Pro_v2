#--- START OF FILE: src/capitalguard/boot.py ---
from __future__ import annotations
from typing import TypedDict

from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.report_service import ReportService
from capitalguard.application.services.analytics_service import AnalyticsService

class ServicesPack(TypedDict):
    repo: RecommendationRepository
    notifier: TelegramNotifier
    trade_service: TradeService
    report_service: ReportService
    analytics_service: AnalyticsService

def build_services() -> ServicesPack:
    """
    Composition Root: يبني كل الخدمات مرة واحدة ويعيدها في dict.
    ملاحظة: TelegramNotifier يقرأ إعداداته من settings داخليًا (token/chat_id)،
    لذا لا نمرّر معاملات هنا. مشاركة نفس النسخ بين API والبوت مقصودة.
    """
    repo = RecommendationRepository()
    notifier = TelegramNotifier()  # يقرأ من settings داخليًا

    trade = TradeService(repo=repo, notifier=notifier)
    report = ReportService(repo=repo)
    analytics = AnalyticsService(repo=repo)

    return {
        "repo": repo,
        "notifier": notifier,
        "trade_service": trade,
        "report_service": report,
        "analytics_service": analytics,
    }
#--- END OF FILE ---