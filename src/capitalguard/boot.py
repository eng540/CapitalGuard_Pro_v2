# --- START OF FILE: src/capitalguard/boot.py ---
from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.infrastructure.notify.telegram import TelegramNotifier
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.report_service import ReportService
from capitalguard.application.services.analytics_service import AnalyticsService

def build_services() -> dict:
    """
    Composition Root: يبني كل الخدمات مرة واحدة ويعيدها في dict.
    ملاحظة: TelegramNotifier يعتمد على settings داخله، لذا لا نمرّر bot_token/chat_id هنا.
    """
    repo = RecommendationRepository()
    notifier = TelegramNotifier()  # ✅ بدون معاملات (يقرأ من settings)

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
# --- END OF FILE ---