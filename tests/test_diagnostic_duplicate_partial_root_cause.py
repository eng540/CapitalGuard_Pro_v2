"""DIAGNOSTIC TEST — TEMPORARY.

Purpose: prove exactly what retry_g6() returns or raises when a duplicate
source points to a previous REPLAY_PARTIAL run.

This file MUST be removed or converted to a proper regression after root cause
is identified. It intentionally fails so that CI displays its DIAG_* logs.
"""
from __future__ import annotations

import logging
import traceback
from datetime import timedelta

from sqlalchemy import select

from capitalguard.application.services.historical_forwarding_service import (
    ForwardedMessageInput,
    HistoricalForwardingService,
)
from capitalguard.application.services.historical_market_replay_service import (
    HistoricalMarketReplayService,
    MarketCandle,
)
from capitalguard.infrastructure.db.models import HistoricalReplayRun
from tests.test_historical_forwarding_auto_progression import (
    SOURCE_TIME,
    FakeProvider,
    _auto_batch,
)


LOGGER_NAME = "DIAGNOSTIC_DUPLICATE_RETRY"
logger = logging.getLogger(LOGGER_NAME)


def _one_candle() -> MarketCandle:
    return MarketCandle(
        asset="BTCUSDT",
        market="Futures",
        open_time=SOURCE_TIME,
        open=100,
        high=101,
        low=99,
        close=100,
        volume=1,
        data_source="FAKE",
    )


def _dump_run(run: HistoricalReplayRun) -> dict:
    return {
        "id": run.id,
        "status": run.status,
        "fingerprint": run.request_fingerprint,
        "reprocess_of_run_id": run.reprocess_of_run_id,
        "window_start": str(run.window_start),
        "window_start_type": type(run.window_start).__name__ if run.window_start else None,
        "window_start_tzinfo": str(run.window_start.tzinfo) if run.window_start else None,
        "window_end": str(run.window_end),
        "window_end_type": type(run.window_end).__name__ if run.window_end else None,
        "window_end_tzinfo": str(run.window_end.tzinfo) if run.window_end else None,
    }


def test_diagnostic_duplicate_partial_root_cause(db_session, caplog):
    """DIAGNOSTIC ONLY — intentionally fails to display captured logs."""
    caplog.set_level(logging.ERROR, logger=LOGGER_NAME)

    # ------------------------------------------------------------------
    # STEP 1: create a REPLAY_PARTIAL state via auto_progress
    # ------------------------------------------------------------------
    batch, first_receipt, _, _ = _auto_batch(
        db_session,
        raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures",
    )
    logger.error(
        "DIAG_STEP1 first_receipt id=%r evidence_id=%r status=%r",
        first_receipt.id,
        first_receipt.evidence_id,
        first_receipt.validation_status,
    )

    first = HistoricalForwardingService().auto_progress_canonical_batch(
        db_session,
        batch_id=batch.id,
        replay_end=SOURCE_TIME + timedelta(days=9),
        limit=1,
        provider=FakeProvider([_one_candle()]),
    )
    logger.error(
        "DIAG_STEP1 aggregate=%r",
        {k: v for k, v in first.items() if k != "items"},
    )
    logger.error("DIAG_STEP1 items=%r", first["items"])

    # ------------------------------------------------------------------
    # STEP 2: read raw ReplayRun rows (this exposes tz-info after SQLite)
    # ------------------------------------------------------------------
    runs_after_first = db_session.execute(
        select(HistoricalReplayRun).order_by(HistoricalReplayRun.id)
    ).scalars().all()
    logger.error(
        "DIAG_STEP2 raw_runs_after_first=%r",
        [_dump_run(r) for r in runs_after_first],
    )

    # ------------------------------------------------------------------
    # STEP 3: stage a duplicate and read its resolution metadata
    # ------------------------------------------------------------------
    duplicate_batch = HistoricalForwardingService().start_batch(
        db_session,
        channel_catalog_id=batch.channel_catalog_id,
        requested_by_user_id=77,
        expected_source_chat_id=-1007001,
        mode="SINGLE",
        max_records=1,
    )
    duplicate = HistoricalForwardingService().stage_message(
        db_session,
        batch_id=duplicate_batch.id,
        message=ForwardedMessageInput(
            receiver_chat_id=701,
            receiver_message_id=8001,
            forwarding_user_id=77,
            source_chat_id=-1007001,
            source_message_id=7001,
            source_origin_type="CHANNEL",
            source_message_timestamp=SOURCE_TIME,
            raw_text="#BTCUSDT LONG Entry 100 Stop 90 TP1 110 Futures",
            metadata={"source_title": "Canonical historical source"},
        ),
    )
    resolution = (duplicate.metadata_json or {}).get("duplicate_resolution") or {}
    logger.error(
        "DIAG_STEP3 duplicate_status=%r",
        duplicate.validation_status,
    )
    logger.error(
        "DIAG_STEP3 duplicate_resolution=%r",
        resolution,
    )

    previous_receipt_id = resolution.get("previous_receipt_id")
    logger.error("DIAG_STEP3 previous_receipt_id=%r", previous_receipt_id)

    if previous_receipt_id is None:
        logger.error("DIAG_ABORT: no previous_receipt_id detected")
        assert False, "DIAGNOSTIC: no previous_receipt_id — cannot proceed"

    # ------------------------------------------------------------------
    # STEP 4: call retry_g6 DIRECTLY to expose raw behavior
    # ------------------------------------------------------------------
    service = HistoricalMarketReplayService()

    try:
        healed = service.retry_g6(
            db_session,
            receipt_id=int(previous_receipt_id),
            provider=FakeProvider([_one_candle()]),
        )
        healed_run = healed.get("run")
        decision = healed.get("decision")
        logger.error(
            "DIAG_STEP4 retry_g6 SUCCEEDED healed_run_id=%r healed_status=%r healed_reprocess_of=%r",
            getattr(healed_run, "id", None),
            getattr(healed_run, "status", None),
            getattr(healed_run, "reprocess_of_run_id", None),
        )
        logger.error(
            "DIAG_STEP4 decision_action=%r decision_reason=%r",
            getattr(decision, "action", None),
            getattr(decision, "reason", None),
        )
        logger.error(
            "DIAG_STEP4 result_keys=%r",
            sorted(healed.keys()) if isinstance(healed, dict) else type(healed).__name__,
        )
    except Exception as exc:  # noqa: BLE001 — diagnostic only
        logger.error(
            "DIAG_STEP4 retry_g6 RAISED type=%s message=%s",
            type(exc).__name__,
            str(exc),
        )
        logger.error("DIAG_STEP4 traceback=\n%s", traceback.format_exc())

    # ------------------------------------------------------------------
    # STEP 5: read raw ReplayRun rows after retry attempt
    # ------------------------------------------------------------------
    runs_after_retry = db_session.execute(
        select(HistoricalReplayRun).order_by(HistoricalReplayRun.id)
    ).scalars().all()
    logger.error(
        "DIAG_STEP5 raw_runs_after_retry=%r",
        [_dump_run(r) for r in runs_after_retry],
    )

    # ------------------------------------------------------------------
    # Force failure so pytest displays captured DIAG_* logs in CI
    # ------------------------------------------------------------------
    assert False, "DIAGNOSTIC COMPLETE — see DIAG_* logs above"