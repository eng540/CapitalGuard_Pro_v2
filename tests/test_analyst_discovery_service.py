from decimal import Decimal

from capitalguard.application.services.analyst_discovery_service import AnalystDiscoveryService
from capitalguard.domain.entities import ExitStrategy, OrderType, RecommendationStatus, UserType
from capitalguard.infrastructure.db.models import AnalystProfile, Recommendation
from capitalguard.infrastructure.db.repository import UserRepository


def _recommendation(analyst_id, entry, exit_price, status=RecommendationStatus.CLOSED):
    return Recommendation(
        analyst_id=analyst_id,
        asset="BTCUSDT",
        side="LONG",
        entry=entry,
        stop_loss=Decimal("90"),
        targets=[{"price": "120", "close_percent": 100}],
        status=status,
        order_type=OrderType.MARKET,
        exit_strategy=ExitStrategy.CLOSE_AT_FINAL_TP,
        exit_price=exit_price,
        is_shadow=False,
    )


def test_small_sample_is_not_eligible_for_ranking(db_session):
    analyst = UserRepository(db_session).find_or_create(
        telegram_id=4101,
        user_type=UserType.ANALYST,
        first_name="Small Sample",
    )
    db_session.add(AnalystProfile(user_id=analyst.id, public_name="Small Sample", is_public=True))
    db_session.add(_recommendation(analyst.id, 100, 110))
    db_session.flush()

    record = AnalystDiscoveryService(minimum_sample_size=5).get_analyst(db_session, analyst.id)

    assert record["sample_size"] == 1
    assert record["win_rate_pct"] == Decimal("100")
    assert record["eligible_for_ranking"] is False


def test_analyst_metrics_include_drawdown_and_active_exposure(db_session):
    analyst = UserRepository(db_session).find_or_create(
        telegram_id=4102,
        user_type=UserType.ANALYST,
        first_name="Measured Analyst",
    )
    db_session.add(AnalystProfile(user_id=analyst.id, public_name="Measured Analyst", is_public=True))
    for entry, exit_price in [(100, 110), (100, 90), (100, 120), (100, 80), (100, 105)]:
        db_session.add(_recommendation(analyst.id, entry, exit_price))
    db_session.add(_recommendation(analyst.id, 100, None, status=RecommendationStatus.ACTIVE))
    db_session.flush()

    record = AnalystDiscoveryService(minimum_sample_size=5).get_analyst(db_session, analyst.id)

    assert record["sample_size"] == 5
    assert record["eligible_for_ranking"] is True
    assert record["active_recommendations"] == 1
    assert record["max_drawdown_pct"] > Decimal("0")
    assert record["exposure_proxy"] == 1
