"""Verify G5 materialization idempotency under real PostgreSQL concurrency."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from capitalguard.application.services.historical_signal_materialization_service import (
    HistoricalSignalMaterializationService,
)
from capitalguard.domain.entities import UserType
from capitalguard.infrastructure.db.models import HistoricalSignal, HistoricalSignalMaterialization, User
from tests.test_historical_signal_materialization_service import accepted_g5_draft


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    engine = create_engine(database_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    setup_session = Session()
    try:
        setup_session.add(
            User(
                id=99,
                telegram_user_id=990000099,
                user_type=UserType.TRADER,
                username="g5_concurrency_fixture",
                first_name="G5 Fixture",
                is_active=True,
            )
        )
        setup_session.flush()
        draft, _ = accepted_g5_draft(setup_session)
        setup_session.commit()
        draft_id = draft.id
    finally:
        setup_session.close()

    start = threading.Barrier(2)

    def materialize_once() -> int:
        session = Session()
        try:
            start.wait(timeout=10)
            signal = HistoricalSignalMaterializationService().materialize(session, draft_id=draft_id)
            session.commit()
            return signal.id
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        signal_ids = list(executor.map(lambda _: materialize_once(), range(2)))

    verify_session = Session()
    try:
        signals = verify_session.execute(select(HistoricalSignal)).scalars().all()
        bridges = verify_session.execute(
            select(HistoricalSignalMaterialization).where(HistoricalSignalMaterialization.draft_id == draft_id)
        ).scalars().all()
        assert len(signals) == 1, f"expected one HistoricalSignal, found {len(signals)}"
        assert len(bridges) == 1, f"expected one materialization, found {len(bridges)}"
        assert signal_ids[0] == signal_ids[1] == signals[0].id
    finally:
        verify_session.close()

    print("G5 PostgreSQL concurrency verification: PASS")


if __name__ == "__main__":
    main()
