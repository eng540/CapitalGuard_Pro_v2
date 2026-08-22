from datetime import datetime

from capitalguard.infrastructure.db.models import Recommendation, UserTrade
from capitalguard.interfaces.api.routers.webapp import (
    _find_owned_user_trade_by_public_ref,
    _serialize_trade_read_model,
)


def test_trade_read_model_uses_typed_public_identity_and_source_reference():
    source = Recommendation(
        id=31,
        public_ref="AN-000007/R-0009",
        analyst_id=7,
        asset="BTCUSDT",
        side="LONG",
        entry=65000,
        stop_loss=64000,
        targets=[],
    )
    trade = UserTrade(
        id=19,
        public_ref="USR-000012/T-0003",
        user_id=12,
        source_recommendation_id=31,
        source_recommendation=source,
        asset="BTCUSDT",
        side="LONG",
        entry=65000,
        stop_loss=64000,
        open_size_percent=75,
        targets=[],
        source_type="TRACKED_RECOMMENDATION",
        created_at=datetime(2026, 8, 21, 12, 0, 0),
    )

    payload = _serialize_trade_read_model(trade)

    assert payload["entity_type"] == "USER_TRADE"
    assert payload["public_ref"] == "USR-000012/T-0003"
    assert payload["display_ref"] == "USR-000012/T-0003"
    assert payload["open_size_percent"] == 75.0
    assert payload["source"] == {
        "entity_type": "RECOMMENDATION",
        "public_ref": "AN-000007/R-0009",
        "analyst_id": 7,
    }


class _EmptyScalarResult:
    def scalar_one_or_none(self):
        return None


class _CapturingSession:
    def __init__(self):
        self.statement = None

    def execute(self, statement):
        self.statement = statement
        return _EmptyScalarResult()


def test_owned_public_ref_lookup_always_scopes_to_trader_and_public_reference():
    session = _CapturingSession()

    assert _find_owned_user_trade_by_public_ref(session, 12, "USR-000012/T-0003") is None

    compiled = str(session.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "user_trades.user_id = 12" in compiled
    assert "user_trades.public_ref = 'USR-000012/T-0003'" in compiled
