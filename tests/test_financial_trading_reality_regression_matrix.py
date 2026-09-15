import asyncio
import inspect
from decimal import Decimal
from types import SimpleNamespace

from capitalguard.application.services.alert_service import AlertService
from capitalguard.application.services.historical_signal_service import HistoricalSignalService
from capitalguard.application.services.lifecycle_service import LifecycleService
from capitalguard.domain.financial_metrics import price_return_pct
from capitalguard.infrastructure.db.models import RecommendationStatusEnum
from capitalguard.interfaces.telegram.ui_texts import _build_status_dashboard


def test_r01_r04_canonical_price_return_matrix():
    assert price_return_pct(Decimal("100"), Decimal("110"), "LONG") == Decimal("10")
    assert price_return_pct(Decimal("100"), Decimal("90"), "LONG") == Decimal("-10")
    assert price_return_pct(Decimal("100"), Decimal("90"), "SHORT") == Decimal("10")
    assert price_return_pct(Decimal("100"), Decimal("110"), "SHORT") == Decimal("-10")


def test_c01_market_observation_does_not_render_filled():
    rec = SimpleNamespace(
        status=RecommendationStatusEnum.ACTIVE,
        live_price=Decimal("100"),
        entry=Decimal("100"),
        side="LONG",
    )
    text = _build_status_dashboard(rec, is_initial_publish=True)
    assert "ACTIVE" in text
    assert "PRICE OBSERVED" in text
    assert "Filled" not in text
    assert "FILLED" not in text
    assert "Executed" not in text
    assert "EXECUTED" not in text


def test_c07_replay_event_recording_does_not_refresh_ranking_by_default():
    parameter = inspect.signature(HistoricalSignalService.record_event).parameters["refresh_ranking"]
    assert parameter.default is False


class _FakeRepo:
    def __init__(self, record):
        self.record = record

    def get_for_update(self, session, record_id):
        return self.record

    def get(self, session, record_id):
        return self.record

    def get_published_messages(self, session, record_id):
        return []

    def _to_entity(self, record):
        return record


class _FakeSession:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)

    def commit(self):
        return None

    def refresh(self, value):
        return None


def test_c09_partial_lifecycle_milestone_is_not_execution_fill():
    record = SimpleNamespace(
        id=1,
        public_ref="REC-1",
        status=RecommendationStatusEnum.ACTIVE,
        analyst_id=1,
        open_size_percent=Decimal("100"),
        entry=Decimal("100"),
        side="LONG",
    )
    service = LifecycleService(_FakeRepo(record), SimpleNamespace())
    session = _FakeSession()

    async def no_tracked_trade_sync(*args, **kwargs):
        return None

    service._sync_tracked_trades_from_recommendation = no_tracked_trade_sync
    asyncio.run(
        service.partial_close_async(
            1,
            None,
            Decimal("50"),
            Decimal("110"),
            session,
            triggered_by="AUTO",
        )
    )

    partial_events = [
        event for event in session.added
        if getattr(event, "event_type", None) == "PARTIAL"
    ]
    assert len(partial_events) == 1
    event_data = partial_events[0].event_data
    assert event_data["execution_status"] == "NOT_FILLED"
    assert event_data["execution_evidence"] is False
    assert "observed_return_pct" in event_data


def test_c05_symbol_queue_saturation_is_visible_not_silent():
    service = AlertService.__new__(AlertService)
    service._symbol_queues = {"BTCUSDT:Futures": asyncio.Queue(maxsize=1)}
    service._symbol_workers = {}
    service._workers_lock = asyncio.Lock()
    service._symbol_queues["BTCUSDT:Futures"].put_nowait({"symbol": "BTCUSDT"})

    async def run():
        try:
            await service._dispatch_to_symbol("BTCUSDT:Futures", {"symbol": "BTCUSDT"})
        except asyncio.QueueFull:
            return True
        return False

    assert asyncio.run(run()) is True
