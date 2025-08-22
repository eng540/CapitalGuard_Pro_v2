from capitalguard.infrastructure.db.repository import RecommendationRepository
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.report_service import ReportService

class DummyNotifier:
    def publish(self, text: str): pass

def test_create_and_report():
    repo = RecommendationRepository()
    svc = TradeService(repo, DummyNotifier())
    rep = ReportService(repo)
    rec = svc.create("BTCUSDT","LONG",65000,63000,[66000,67000])
    assert rec.id is not None
    r = rep.summary()
    assert r["total"] >= 1
