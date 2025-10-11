import pytest
from datetime import datetime
from decimal import Decimal
from capitalguard.domain.entities import Recommendation, RecommendationStatus, OrderType
from capitalguard.domain.value_objects import Symbol, Side, Price, Targets

@pytest.fixture
def sample_recommendation() -> Recommendation:
    """Provides a default recommendation in a PENDING state."""
    return Recommendation(
        asset=Symbol("BTCUSDT"),
        side=Side("LONG"),
        entry=Price(Decimal("60000")),
        stop_loss=Price(Decimal("59000")),
        targets=Targets([{"price": Decimal("61000")}, {"price": Decimal("62000")}]),
        order_type=OrderType.LIMIT,
        id=1,
    )

def test_recommendation_initial_state(sample_recommendation: Recommendation):
    assert sample_recommendation.status == RecommendationStatus.PENDING
    assert sample_recommendation.exit_price is None
    assert sample_recommendation.closed_at is None
    assert sample_recommendation.activated_at is None

def test_recommendation_activate(sample_recommendation: Recommendation):
    sample_recommendation.activate()
    assert sample_recommendation.status == RecommendationStatus.ACTIVE
    assert sample_recommendation.activated_at is not None
    assert isinstance(sample_recommendation.activated_at, datetime)
    assert sample_recommendation.updated_at == sample_recommendation.activated_at

def test_recommendation_close_updates_fields_correctly(sample_recommendation: Recommendation):
    sample_recommendation.activate() # Can only close an active rec
    exit_price = Decimal("61500.0")
    sample_recommendation.close(exit_price)
    assert sample_recommendation.status == RecommendationStatus.CLOSED
    assert sample_recommendation.exit_price == exit_price
    assert sample_recommendation.closed_at is not None
    assert isinstance(sample_recommendation.closed_at, datetime)
    assert sample_recommendation.updated_at == sample_recommendation.closed_at

def test_closing_an_already_closed_recommendation_does_nothing(sample_recommendation: Recommendation):
    sample_recommendation.activate()
    sample_recommendation.close(Decimal("61000.0"))
    first_closed_time = sample_recommendation.closed_at
    first_updated_time = sample_recommendation.updated_at
    sample_recommendation.close(Decimal("62000.0")) # Try to close again
    assert sample_recommendation.exit_price == Decimal("61000.0") # Price should not change
    assert sample_recommendation.closed_at == first_closed_time
    assert sample_recommendation.updated_at == first_updated_time