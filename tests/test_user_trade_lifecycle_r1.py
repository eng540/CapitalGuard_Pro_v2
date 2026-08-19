from contextlib import nullcontext
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from capitalguard.application.services import lifecycle_service as lifecycle_module
from capitalguard.application.services.lifecycle_service import LifecycleService
from capitalguard.infrastructure.db.models import UserTrade, UserTradeEvent, UserTradeStatus
from capitalguard.infrastructure.db.repository import RecommendationRepository, UserRepository


pytestmark = pytest.mark.asyncio


@pytest.fixture
def lifecycle_service():
    service = LifecycleService(repo=RecommendationRepository(), notifier=MagicMock())
    service._notify_user_trade_update = AsyncMock()

    async def commit_and_dispatch(session, obj, rebuild_alerts=True):
        session.commit()

    service._commit_and_dispatch = AsyncMock(side_effect=commit_and_dispatch)
    return service


def _trade(db_session, telegram_id, status, side="LONG"):
    user = UserRepository(db_session).find_or_create(
        telegram_id=telegram_id,
        first_name="Lifecycle",
    )
    user.is_active = True
    trade = UserTrade(
        user_id=user.id,
        asset="BTCUSDT",
        side=side,
        entry=Decimal("100"),
        stop_loss=Decimal("95") if side == "LONG" else Decimal("105"),
        targets=[{"price": "105" if side == "LONG" else "95", "close_percent": 100.0}],
        status=status,
    )
    db_session.add(trade)
    db_session.commit()
    return trade


def _patch_session_scope(monkeypatch, db_session):
    monkeypatch.setattr(
        lifecycle_module,
        "session_scope",
        lambda: nullcontext(db_session),
    )


async def test_watchlist_activates_once_and_records_event(
    db_session, lifecycle_service, monkeypatch
):
    trade = _trade(db_session, 92101, UserTradeStatus.WATCHLIST)
    _patch_session_scope(monkeypatch, db_session)

    await lifecycle_service.process_user_trade_activation_event(trade.id)
    await lifecycle_service.process_user_trade_activation_event(trade.id)

    db_session.expire_all()
    saved = db_session.query(UserTrade).filter(UserTrade.id == trade.id).one()
    events = (
        db_session.query(UserTradeEvent)
        .filter(
            UserTradeEvent.user_trade_id == trade.id,
            UserTradeEvent.event_type == "ACTIVATED",
        )
        .all()
    )
    assert saved.status == UserTradeStatus.ACTIVATED
    assert saved.activated_at is not None
    assert len(events) == 1
    assert lifecycle_service._notify_user_trade_update.await_count == 1


async def test_sl_close_persists_pnl_and_is_idempotent(
    db_session, lifecycle_service, monkeypatch
):
    trade = _trade(db_session, 92102, UserTradeStatus.ACTIVATED)
    _patch_session_scope(monkeypatch, db_session)

    await lifecycle_service.process_user_trade_sl_hit_event(trade.id, Decimal("95"))
    await lifecycle_service.process_user_trade_sl_hit_event(trade.id, Decimal("94"))

    db_session.expire_all()
    saved = db_session.query(UserTrade).filter(UserTrade.id == trade.id).one()
    events = (
        db_session.query(UserTradeEvent)
        .filter(
            UserTradeEvent.user_trade_id == trade.id,
            UserTradeEvent.event_type == "SL_HIT",
        )
        .all()
    )
    assert saved.status == UserTradeStatus.CLOSED
    assert saved.close_price == Decimal("95")
    assert saved.pnl_percentage == Decimal("-5.0")
    assert len(events) == 1
    assert lifecycle_service._notify_user_trade_update.await_count == 1


async def test_final_tp_close_persists_pnl_and_is_idempotent(
    db_session, lifecycle_service, monkeypatch
):
    trade = _trade(db_session, 92103, UserTradeStatus.ACTIVATED)
    _patch_session_scope(monkeypatch, db_session)

    await lifecycle_service.process_user_trade_tp_hit_event(trade.id, 1, Decimal("105"))
    await lifecycle_service.process_user_trade_tp_hit_event(trade.id, 1, Decimal("106"))

    db_session.expire_all()
    saved = db_session.query(UserTrade).filter(UserTrade.id == trade.id).one()
    events = (
        db_session.query(UserTradeEvent)
        .filter(
            UserTradeEvent.user_trade_id == trade.id,
            UserTradeEvent.event_type == "TP1_HIT",
        )
        .all()
    )
    assert saved.status == UserTradeStatus.CLOSED
    assert saved.close_price == Decimal("105")
    assert saved.pnl_percentage == Decimal("5.0")
    assert len(events) == 1


async def test_partial_tp_updates_open_size_before_final_close(
    db_session, lifecycle_service, monkeypatch
):
    user = UserRepository(db_session).find_or_create(telegram_id=92104, first_name="Partial")
    user.is_active = True
    trade = UserTrade(
        user_id=user.id,
        asset="BTCUSDT",
        side="LONG",
        entry=Decimal("100"),
        stop_loss=Decimal("95"),
        targets=[
            {"price": "105", "close_percent": 50.0},
            {"price": "110", "close_percent": 50.0},
        ],
        status=UserTradeStatus.ACTIVATED,
    )
    db_session.add(trade)
    db_session.commit()
    _patch_session_scope(monkeypatch, db_session)

    await lifecycle_service.process_user_trade_tp_hit_event(trade.id, 1, Decimal("105"))
    db_session.expire_all()
    after_tp1 = db_session.query(UserTrade).filter(UserTrade.id == trade.id).one()
    assert after_tp1.status == UserTradeStatus.ACTIVATED
    assert after_tp1.open_size_percent == Decimal("50.00")

    await lifecycle_service.process_user_trade_tp_hit_event(trade.id, 2, Decimal("110"))
    db_session.expire_all()
    after_tp2 = db_session.query(UserTrade).filter(UserTrade.id == trade.id).one()
    assert after_tp2.status == UserTradeStatus.CLOSED
    assert after_tp2.open_size_percent == Decimal("0.00")
