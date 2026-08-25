import asyncio
from decimal import Decimal

import pytest

from capitalguard.application.strategy.engine import MoveSLAction, StrategyEngine
from capitalguard.domain.protection_policy import ProtectionPolicy, ProtectionPolicyError


def test_long_trailing_policy_is_valid():
    policy = ProtectionPolicy.from_record({
        "profit_stop_mode": "TRAILING",
        "profit_stop_active": True,
        "side": "LONG",
        "entry": "100",
        "stop_loss": "95",
        "profit_stop_trailing_value": "5",
    })
    policy.validate()
    assert policy.side == "LONG"


def test_short_break_even_policy_is_valid():
    policy = ProtectionPolicy.from_record({
        "profit_stop_mode": "BREAK_EVEN",
        "profit_stop_active": True,
        "side": "SHORT",
        "entry": "100",
        "stop_loss": "105",
        "break_even_after_profit_pct": "2",
        "break_even_buffer": "0.5",
    })
    policy.validate()
    assert policy.break_even_buffer == Decimal("0.5")


@pytest.mark.parametrize(
    "record, message",
    [
        ({"profit_stop_mode": "TRAILING", "profit_stop_active": True, "side": "LONG", "entry": 100, "stop_loss": 101, "profit_stop_trailing_value": 5}, "LONG stop_loss"),
        ({"profit_stop_mode": "BREAK_EVEN", "profit_stop_active": True, "side": "SHORT", "entry": 100, "stop_loss": 99, "break_even_after_profit_pct": 2}, "SHORT stop_loss"),
        ({"profit_stop_mode": "TRAILING", "profit_stop_active": True, "side": "LONG", "entry": 100, "stop_loss": 95, "profit_stop_trailing_value": 0}, "positive trailing"),
        ({"profit_stop_mode": "BREAK_EVEN", "profit_stop_active": True, "side": "LONG", "entry": 100, "stop_loss": 95, "break_even_after_profit_pct": 0}, "positive profit threshold"),
    ],
)
def test_invalid_protection_policy_is_rejected(record, message):
    with pytest.raises(ProtectionPolicyError, match=message):
        ProtectionPolicy.from_record(record).validate()


def test_engine_skips_invalid_policy_without_emitting_action():
    engine = StrategyEngine(lifecycle_service=None)
    actions = asyncio.run(engine.evaluate({
        "id": 903,
        "status": "ACTIVATED",
        "side": "LONG",
        "entry": "100",
        "stop_loss": "101",
        "profit_stop_active": True,
        "profit_stop_mode": "TRAILING",
        "profit_stop_trailing_value": "5",
    }, {"high": "120", "low": "118", "close": "119", "ts": 1}))
    assert actions == []


def test_engine_uses_policy_for_short_trailing():
    engine = StrategyEngine(lifecycle_service=None)
    actions = asyncio.run(engine.evaluate({
        "id": 904,
        "status": "ACTIVATED",
        "side": "SHORT",
        "entry": "100",
        "stop_loss": "105",
        "profit_stop_active": True,
        "profit_stop_mode": "TRAILING",
        "profit_stop_trailing_value": "5",
    }, {"high": "82", "low": "80", "close": "81", "ts": 1}))
    assert len(actions) == 1
    assert isinstance(actions[0], MoveSLAction)
    assert actions[0].new_sl == Decimal("84.0")
