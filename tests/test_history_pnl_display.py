from types import SimpleNamespace
from decimal import Decimal

from capitalguard.domain.entities import RecommendationStatus
from capitalguard.interfaces.telegram.ui_texts import calculate_real_pnl


def test_user_trade_history_uses_stored_pnl():
    user_trade_entity = SimpleNamespace(
        entry=Decimal("76.900"),
        side=SimpleNamespace(value="SHORT"),
        status=RecommendationStatus.CLOSED,
        events=[],
        exit_price=Decimal("77.000"),
        final_pnl_percentage=Decimal("-0.13"),
    )

    result = calculate_real_pnl(user_trade_entity)

    assert result["total_pnl"] == -0.13
    assert result["weighted_exit_price"] == 77.0
