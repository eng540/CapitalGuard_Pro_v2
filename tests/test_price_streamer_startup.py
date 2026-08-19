import asyncio
from contextlib import nullcontext
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from capitalguard.infrastructure.db.models import UserTrade, UserTradeStatus
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository
from capitalguard.infrastructure.sched import price_streamer as streamer_module
from capitalguard.infrastructure.sched.price_streamer import PriceStreamer


pytestmark = pytest.mark.asyncio


async def test_initial_load_subscribes_watchlist_symbol(db_session, monkeypatch):
    user = UserRepository(db_session).find_or_create(
        telegram_id=92201,
        first_name="Streamer",
    )
    user.is_active = True
    trade = UserTrade(
        user_id=user.id,
        asset="BTCUSDT",
        side="LONG",
        entry=Decimal("100"),
        stop_loss=Decimal("95"),
        targets=[{"price": "105", "close_percent": 100.0}],
        status=UserTradeStatus.WATCHLIST,
    )
    db_session.add(trade)
    db_session.commit()

    monkeypatch.setattr(
        streamer_module,
        "session_scope",
        lambda: nullcontext(db_session),
    )

    streamer = PriceStreamer(asyncio.Queue(), RecommendationRepository())
    streamer.client = MagicMock()
    streamer.client.update_subscriptions = AsyncMock()

    await streamer._load_initial_symbols()

    streamer.client.update_subscriptions.assert_awaited_once_with(["BTCUSDT"])
