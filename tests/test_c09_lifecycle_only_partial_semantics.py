from decimal import Decimal
from types import SimpleNamespace

from capitalguard.application.services.lifecycle_service import LifecycleService
from capitalguard.infrastructure.db.models import UserTradeStatusEnum


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def options(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, trade):
        self.trade = trade
        self.added = []

    def query(self, model):
        return _Query([self.trade])

    def add(self, value):
        self.added.append(value)


def test_source_partial_updates_tracked_lifecycle_without_execution_claim():
    trade = SimpleNamespace(
        id=7,
        source_recommendation_id=42,
        source_type="TRACKED_RECOMMENDATION",
        status=UserTradeStatusEnum.ACTIVATED,
        open_size_percent=Decimal("100"),
        entry=Decimal("100"),
        side="LONG",
        activated_at=None,
        user_id=1,
        user=SimpleNamespace(user_code="USR-000001"),
        watched_channel=None,
        trader_sequence=None,
        public_ref="TRD-7",
    )
    recommendation = SimpleNamespace(id=42)
    session = _Session(trade)
    service = LifecycleService(SimpleNamespace(), SimpleNamespace())
    notifications = []

    async def capture_notification(*args, **kwargs):
        notifications.append((args, kwargs))

    service._notify_trade_event = capture_notification

    import asyncio
    asyncio.run(
        service._sync_tracked_trades_from_recommendation(
            session,
            recommendation,
            "SOURCE_PARTIAL",
            {
                "price": 110.0,
                "lifecycle_close_percent": 50.0,
                "execution_status": "NOT_FILLED",
                "execution_evidence": False,
            },
        )
    )

    assert trade.open_size_percent == Decimal("50")
    assert len(session.added) == 1
    event = session.added[0]
    assert event.event_type == "SOURCE_PARTIAL"
    assert event.event_data["execution_status"] == "NOT_FILLED"
    assert event.event_data["execution_evidence"] is False
    assert not any("FILLED" in str(item) or "EXECUTED" in str(item) for item in event.event_data.values())
    assert notifications
    detail = str(notifications[0][0])
    assert "Filled" not in detail
    assert "Executed" not in detail
