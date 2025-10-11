--- START OF FILE: tests/test_domain.py ---
import pytest
from datetime import datetime
from capitalguard.domain.entities import Recommendation
from capitalguard.domain.value_objects import Symbol, Side, Price, Targets

@pytest.fixture
def sample_recommendation() -> Recommendation:
    return Recommendation(
        asset=Symbol("BTCUSDT"),
        side=Side("LONG"),
        entry=Price(60000),
        stop_loss=Price(59000),
        targets=Targets([61000, 62000]),
        id=1,
        channel_id=12345
    )

def test_recommendation_initial_state(sample_recommendation: Recommendation):
    assert sample_recommendation.status == "OPEN"
    assert sample_recommendation.exit_price is None
    assert sample_recommendation.closed_at is None

def test_recommendation_close_updates_fields_correctly(sample_recommendation: Recommendation):
    exit_price = 61500.0
    sample_recommendation.close(exit_price)
    assert sample_recommendation.status == "CLOSED"
    assert sample_recommendation.exit_price == exit_price
    assert sample_recommendation.closed_at is not None
    assert isinstance(sample_recommendation.closed_at, datetime)
    assert sample_recommendation.updated_at == sample_recommendation.closed_at

def test_closing_an_already_closed_recommendation_does_nothing(sample_recommendation: Recommendation):
    sample_recommendation.close(61000.0)
    first_closed_time = sample_recommendation.closed_at
    sample_recommendation.close(62000.0)
    assert sample_recommendation.exit_price == 61000.0
    assert sample_recommendation.closed_at == first_closed_time
--- END OF FILE ---