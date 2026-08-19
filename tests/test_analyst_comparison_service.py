from decimal import Decimal

from capitalguard.application.services.analyst_comparison_service import AnalystComparisonService
from capitalguard.application.services.identity_service import IdentityService
from capitalguard.domain.entities import ExitStrategy, OrderType, RecommendationStatus, UserType
from capitalguard.infrastructure.db.models import (
    AnalystProfile,
    Recommendation,
    RecommendationChannelRef,
)
from capitalguard.infrastructure.db.repository import ChannelRepository, UserRepository


def _closed_rec(analyst_id, entry, exit_price):
    return Recommendation(
        analyst_id=analyst_id,
        asset="BTCUSDT",
        side="LONG",
        entry=entry,
        stop_loss=Decimal("90"),
        targets=[{"price": "120", "close_percent": 100}],
        status=RecommendationStatus.CLOSED,
        order_type=OrderType.MARKET,
        exit_strategy=ExitStrategy.CLOSE_AT_FINAL_TP,
        exit_price=exit_price,
        is_shadow=False,
    )


def test_compare_channels_keeps_channel_local_outcomes_separate(db_session):
    analyst = UserRepository(db_session).find_or_create(
        telegram_id=5101,
        user_type=UserType.ANALYST,
        first_name="Channel Analyst",
    )
    db_session.add(AnalystProfile(user_id=analyst.id, public_name="Channel Analyst", is_public=True))
    channel_one = IdentityService.ensure_channel_catalog(db_session, -1007001, "Alpha")
    channel_two = IdentityService.ensure_channel_catalog(db_session, -1007002, "Beta")

    first = _closed_rec(analyst.id, 100, 110)
    second = _closed_rec(analyst.id, 100, 90)
    db_session.add_all([first, second])
    db_session.flush()
    db_session.add_all([
        RecommendationChannelRef(
            recommendation_id=first.id,
            channel_catalog_id=channel_one.id,
            channel_sequence=IdentityService.channel_recommendation_sequence(db_session, channel_one.id),
        ),
        RecommendationChannelRef(
            recommendation_id=first.id,
            channel_catalog_id=channel_two.id,
            channel_sequence=IdentityService.channel_recommendation_sequence(db_session, channel_two.id),
        ),
        RecommendationChannelRef(
            recommendation_id=second.id,
            channel_catalog_id=channel_one.id,
            channel_sequence=IdentityService.channel_recommendation_sequence(db_session, channel_one.id),
        ),
    ])
    db_session.flush()

    rows = AnalystComparisonService(minimum_sample_size=2).compare_channels(db_session, analyst.id)
    by_code = {row["channel_code"]: row for row in rows}

    assert by_code[channel_one.channel_code]["sample_size"] == 2
    assert by_code[channel_one.channel_code]["win_rate_pct"] == Decimal("50")
    assert by_code[channel_two.channel_code]["sample_size"] == 1
    assert by_code[channel_two.channel_code]["eligible_for_comparison"] is False
