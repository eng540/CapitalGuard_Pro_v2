from capitalguard.application.services.identity_query_service import IdentityFilters, IdentityQueryService
from capitalguard.application.services.identity_service import IdentityService
from capitalguard.domain.entities import (
    ExitStrategy,
    OrderType,
    RecommendationStatus,
    UserTradeStatus,
    UserType,
)
from capitalguard.infrastructure.db.models import (
    Recommendation,
    RecommendationChannelRef,
    UserTrade,
    WatchedChannel,
)
from capitalguard.infrastructure.db.repository import UserRepository


def _users(db_session):
    repo = UserRepository(db_session)
    analyst = repo.find_or_create(telegram_id=3101, user_type=UserType.ANALYST)
    trader = repo.find_or_create(telegram_id=3201, user_type=UserType.TRADER)
    return analyst, trader


def test_query_filters_by_public_ref_and_owner(db_session):
    analyst, trader = _users(db_session)
    rec_ref, rec_seq = IdentityService.recommendation_identity(db_session, analyst.id)
    rec = Recommendation(
        public_ref=rec_ref,
        analyst_sequence=rec_seq,
        analyst_id=analyst.id,
        asset="BTCUSDT",
        side="LONG",
        entry=60000,
        stop_loss=59000,
        targets=[{"price": "61000", "close_percent": 100}],
        status=RecommendationStatus.ACTIVE,
        order_type=OrderType.MARKET,
        exit_strategy=ExitStrategy.CLOSE_AT_FINAL_TP,
        is_shadow=False,
    )
    db_session.add(rec)
    db_session.flush()

    results = IdentityQueryService.search(
        db_session,
        IdentityFilters(entity_type="recommendation", scope_code=analyst.analyst_code),
    )
    by_ref = IdentityQueryService.search(
        db_session,
        IdentityFilters(entity_type="recommendation", public_ref=rec_ref),
    )

    assert [item.record.id for item in results] == [rec.id]
    assert [item.record.id for item in by_ref] == [rec.id]
    assert trader.id != analyst.id


def test_query_separates_direct_and_tracked_trades(db_session):
    analyst, trader = _users(db_session)
    catalog = IdentityService.ensure_channel_catalog(db_session, -100555001, "Signals")
    watched = WatchedChannel(
        user_id=trader.id,
        channel_catalog_id=catalog.id,
        telegram_channel_id=-100555001,
        channel_title="Signals",
    )
    db_session.add(watched)
    db_session.flush()

    direct_ref, direct_seq = IdentityService.trade_identity(db_session, trader.id)
    tracked_ref, tracked_seq = IdentityService.trade_identity(db_session, trader.id)
    direct = UserTrade(
        public_ref=direct_ref,
        trader_sequence=direct_seq,
        user_id=trader.id,
        asset="ETHUSDT",
        side="LONG",
        entry=3000,
        stop_loss=2900,
        targets=[{"price": "3200", "close_percent": 100}],
        status=UserTradeStatus.WATCHLIST,
        source_type="DIRECT_INPUT",
    )
    tracked = UserTrade(
        public_ref=tracked_ref,
        trader_sequence=tracked_seq,
        user_id=trader.id,
        watched_channel_id=watched.id,
        asset="BTCUSDT",
        side="SHORT",
        entry=60000,
        stop_loss=61000,
        targets=[{"price": "58000", "close_percent": 100}],
        status=UserTradeStatus.ACTIVATED,
        source_type="TRACKED_RECOMMENDATION",
    )
    db_session.add_all([direct, tracked])
    db_session.flush()

    direct_results = IdentityQueryService.search(
        db_session,
        IdentityFilters(entity_type="user_trade", source_type="DIRECT_INPUT", owner_id=trader.id),
    )
    channel_results = IdentityQueryService.search(
        db_session,
        IdentityFilters(entity_type="user_trade", channel_code=catalog.channel_code),
    )

    assert [item.record.id for item in direct_results] == [direct.id]
    assert [item.record.id for item in channel_results] == [tracked.id]
