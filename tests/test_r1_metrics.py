from datetime import datetime, timezone
from decimal import Decimal

import pytest

from capitalguard.domain.entities import UserTradeStatus
from capitalguard.infrastructure.db.models import UserTrade
from capitalguard.infrastructure.db.repository import UserRepository

pytestmark = pytest.mark.asyncio


async def test_r1_funnel_and_activated_only_report(db_session, services):
    user = UserRepository(db_session).find_or_create(
        telegram_id=555,
        first_name="MetricsUser",
    )
    user.is_active = True
    db_session.flush()

    db_session.add_all(
        [
            UserTrade(
                user_id=user.id,
                asset="BTCUSDT",
                side="LONG",
                entry=Decimal("60000"),
                stop_loss=Decimal("59000"),
                targets=[{"price": "61000", "close_percent": 100.0}],
                status=UserTradeStatus.WATCHLIST,
                source_type="DIRECT_INPUT",
            ),
            UserTrade(
                user_id=user.id,
                asset="ETHUSDT",
                side="SHORT",
                entry=Decimal("3000"),
                stop_loss=Decimal("3100"),
                targets=[{"price": "2900", "close_percent": 100.0}],
                status=UserTradeStatus.CLOSED,
                source_type="FORWARD",
                activated_at=datetime.now(timezone.utc),
                closed_at=datetime.now(timezone.utc),
                pnl_percentage=Decimal("5.00"),
            ),
        ]
    )
    db_session.commit()

    performance = services["performance_service"]
    funnel = performance.get_trader_funnel_metrics(db_session, user.id)
    report = performance.get_trader_performance_report(db_session, user.id)

    assert funnel["total_logged"] == 2
    assert funnel["direct_input_logged"] == 1
    assert funnel["forward_logged"] == 1
    assert funnel["activated"] == 1
    assert funnel["closed_activated"] == 1
    assert funnel["watchlist_to_activated_rate_pct"] == 50.0
    assert report["total_trades"] == 1
    assert report["data_source"] == "Activated Portfolio Only"
