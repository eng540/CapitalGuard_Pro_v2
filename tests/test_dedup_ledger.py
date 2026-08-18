from datetime import datetime, timedelta, timezone
from decimal import Decimal

from capitalguard.application.services.dedup_service import DedupLedgerService


def _payload():
    return {
        "asset": "BTCUSDT",
        "side": "LONG",
        "entry": Decimal("60000"),
        "stop_loss": Decimal("59000"),
        "targets": [{"price": "61000", "close_percent": 100.0}],
        "source_text": "BTCUSDT LONG Entry 60000 SL 59000 TP 61000",
    }


def test_first_signal_is_accepted_and_recorded(db_session):
    service = DedupLedgerService(window_seconds=300)
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)

    decision = service.check_and_record(
        db_session,
        user_id=1,
        source_channel_id=123,
        now=now,
        **_payload(),
    )

    assert decision.duplicate is False
    assert len(decision.fingerprint) == 64
    assert decision.ledger.outcome == "accepted"
    db_session.commit()


def test_same_signal_is_rejected_inside_window(db_session):
    service = DedupLedgerService(window_seconds=300)
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)

    first = service.check_and_record(
        db_session,
        user_id=1,
        source_channel_id=123,
        now=now,
        **_payload(),
    )
    db_session.commit()

    second = service.check_and_record(
        db_session,
        user_id=1,
        source_channel_id=123,
        now=now + timedelta(seconds=60),
        **_payload(),
    )

    assert first.fingerprint == second.fingerprint
    assert second.duplicate is True
    assert second.ledger.id == first.ledger.id
    assert second.ledger.outcome == "duplicate"


def test_same_signal_is_accepted_in_next_window(db_session):
    service = DedupLedgerService(window_seconds=300)
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)

    service.check_and_record(
        db_session,
        user_id=1,
        source_channel_id=123,
        now=now,
        **_payload(),
    )
    db_session.commit()

    next_window = service.check_and_record(
        db_session,
        user_id=1,
        source_channel_id=123,
        now=now + timedelta(seconds=301),
        **_payload(),
    )

    assert next_window.duplicate is False
