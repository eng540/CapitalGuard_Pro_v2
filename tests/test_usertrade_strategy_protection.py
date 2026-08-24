import asyncio
from decimal import Decimal

from capitalguard.application.strategy.engine import MoveSLAction, StrategyEngine


def test_activated_user_trade_can_generate_trailing_stop_action():
    engine = StrategyEngine(lifecycle_service=None)
    trigger = {
        "id": 901,
        "status": "ACTIVATED",
        "side": "LONG",
        "entry": Decimal("100"),
        "stop_loss": Decimal("95"),
        "profit_stop_active": True,
        "profit_stop_mode": "TRAILING",
        "profit_stop_trailing_value": Decimal("5"),
    }

    actions = asyncio.run(engine.evaluate(trigger, {"high": "120", "low": "118", "close": "119", "ts": 1}))

    assert len(actions) == 1
    assert isinstance(actions[0], MoveSLAction)
    assert actions[0].rec_id == 901
    assert actions[0].new_sl == Decimal("114.0")
    assert actions[0].metadata["mode"] == "TRAILING"


def test_rebuild_index_keeps_activated_user_trade_state():
    engine = StrategyEngine(lifecycle_service=None)
    engine.rebuild_index([{"id": 902, "status": "ACTIVATED", "entry": "100"}])

    assert "902" in engine.serialize_state()["items"]
