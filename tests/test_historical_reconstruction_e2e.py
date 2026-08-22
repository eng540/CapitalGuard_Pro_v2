import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from capitalguard.application.services.financial_consistency_service import FinancialConsistencyService
from capitalguard.application.services.historical_import_service import HistoricalImportService
from capitalguard.application.services.historical_market_replay_service import CandleCache, HistoricalMarketReplayService, MarketCandle
from capitalguard.application.services.historical_parser_service import HistoricalParserService
from capitalguard.application.services.historical_reputation_service import HistoricalReputationService
from capitalguard.application.services.historical_signal_query_service import HistoricalSignalQueryService
from capitalguard.application.services.historical_signal_service import HistoricalSignalService
from capitalguard.application.services.parsing_service import ParsingService
from capitalguard.application.services.telegram_history_adapter import TelegramExportAdapter
from capitalguard.domain.entities import UserType
from capitalguard.infrastructure.db.models import (
    Channel,
    ChannelCatalog,
    PublicationDelivery,
    Recommendation,
    UserTrade,
)
from capitalguard.infrastructure.db.repository import ParsingRepository, UserRepository


ROOT = Path(__file__).resolve().parents[1]


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def test_demo_historical_reconstruction_end_to_end_isolated(db_session):
    export_path = ROOT / "fixtures" / "demo_telegram_export.json"
    market_path = ROOT / "fixtures" / "demo_market_1m_ohlcv.json"
    export_payload = json.loads(export_path.read_text(encoding="utf-8"))
    market_payload = json.loads(market_path.read_text(encoding="utf-8"))

    analyst = UserRepository(db_session).find_or_create(
        telegram_id=920001,
        user_type=UserType.ANALYST,
        first_name="Demo Analyst",
    )
    reviewer = UserRepository(db_session).find_or_create(
        telegram_id=920002,
        user_type=UserType.ANALYST,
        first_name="Demo Reviewer",
    )
    trader = UserRepository(db_session).find_or_create(
        telegram_id=920003,
        user_type=UserType.TRADER,
        first_name="Demo Trader",
    )
    channel = Channel(
        analyst_id=analyst.id,
        telegram_channel_id=-1001234567890,
        username="demo_shadow_channel",
        title="Demo Shadow Channel",
        is_active=True,
    )
    catalog = ChannelCatalog(
        telegram_channel_id=-1001234567890,
        channel_code="CH-DEMO-01",
        public_ref="CH-DEMO-01",
        title="Demo Shadow Channel",
    )
    db_session.add_all([channel, catalog])
    db_session.flush()

    manifest = TelegramExportAdapter().to_manifest(
        export_payload,
        telegram_channel_id=-1001234567890,
        source_uri="fixture://demo_telegram_export.json",
    )
    import_service = HistoricalImportService()
    batch, report = import_service.register_validated_batch(
        db_session,
        payload=manifest,
        requested_by_user_id=reviewer.id,
        channel_catalog_id=catalog.id,
    )
    assert report.is_valid
    assert report.total_records == 11
    assert report.rejected_records == 0
    assert batch.status == "VALIDATED"

    records_by_id = {record["telegram_message_id"]: record for record in manifest["records"]}
    assert records_by_id[4]["message_revision"] == 1
    assert records_by_id[2]["metadata"]["reply_to_message_id"] == 1

    signal_service = HistoricalSignalService()
    parser = HistoricalParserService(
        ParsingService(ParsingRepository),
        consistency_service=FinancialConsistencyService(),
    )
    parsed_signals = {}
    for record in manifest["records"]:
        evidence = signal_service.ingest_evidence(
            db_session,
            source_kind=manifest["source_kind"],
            batch_id=batch.id,
            channel_catalog_id=catalog.id,
            telegram_channel_id=record["telegram_channel_id"],
            telegram_message_id=record["telegram_message_id"],
            message_revision=record["message_revision"],
            message_timestamp=_utc(record["message_timestamp"]),
            raw_text=record["raw_text"],
            source_uri=record["source_uri"],
            ownership_proof_type="DEMO_CHANNEL_OWNER",
            ownership_proof_ref="fixture://demo-owner-approval",
            metadata=record["metadata"],
        )
        parsed = parser.parse(record["raw_text"])
        if parsed.parse_status != "PARSED":
            continue
        consistency = parsed.data["financial_consistency"]
        assert consistency["is_consistent"] is True
        signal = signal_service.create_signal(
            db_session,
            evidence_id=evidence.id,
            decision_timestamp=_utc(record["message_timestamp"]),
            channel_catalog_id=catalog.id,
            channel_id=channel.id,
            analyst_id=analyst.id,
            asset=parsed.data["asset"],
            side=parsed.data["side"],
            entry=parsed.data["entry"],
            stop_loss=parsed.data["stop_loss"],
            targets=parsed.data["targets"],
            market="Spot",
        )
        signal_service.add_attribution(
            db_session,
            signal_id=signal.id,
            attribution_kind="CHANNEL",
            analyst_id=analyst.id,
            channel_id=channel.id,
            proof_type="DEMO_CHANNEL_OWNER",
            proof_ref="fixture://demo-owner-approval",
            confidence_score="1.0000",
            status="PROPOSED",
            dedup_key=f"demo:channel:{signal.id}",
        )
        parsed_signals[record["telegram_message_id"]] = signal

    assert set(parsed_signals) == {1, 4, 6, 7}
    signal_service.review_attribution(
        db_session,
        attribution_id=parsed_signals[1].attributions[0].id,
        reviewer_user_id=reviewer.id,
        status="VERIFIED",
        note="Demo channel ownership approved",
    )
    signal_service.record_trader_follow(
        db_session,
        signal_id=parsed_signals[1].id,
        trader_user_id=trader.id,
        dedup_key=f"demo:trader-follow:{trader.id}:{parsed_signals[1].id}",
        proof_ref="fixture://demo-trader-follow",
    )

    cache = CandleCache()
    candles_by_asset: dict[str, list[MarketCandle]] = {}
    for item in market_payload["candles"]:
        candle = MarketCandle(
            asset=item["asset"],
            market=market_payload["market"],
            open_time=_utc(item["open_time"]),
            open=Decimal(str(item["open"])),
            high=Decimal(str(item["high"])),
            low=Decimal(str(item["low"])),
            close=Decimal(str(item["close"])),
            volume=Decimal(str(item["volume"])),
            data_source=market_payload["provider"],
        )
        candles_by_asset.setdefault(candle.asset, []).append(candle)
        cache.put_many([candle])

    replay = HistoricalMarketReplayService(candle_cache=cache)
    replay_end = _utc("2025-01-01T11:00:00+00:00")
    events_by_message = {}
    for message_id, signal in parsed_signals.items():
        events_by_message[message_id] = replay.replay_candles(
            db_session,
            signal_id=signal.id,
            candles=candles_by_asset.get(signal.asset, []),
            replay_end=replay_end,
        )

    assert [event.event_type for event in events_by_message[1]] == ["ACTIVATED", "TP1", "TP2"]
    assert [event.event_type for event in events_by_message[4]] == ["ACTIVATED", "TP1", "TP2"]
    assert [event.event_type for event in events_by_message[6]] == ["ACTIVATED", "SL"]
    assert events_by_message[6][-1].event_data["candle_rule"] == "PESSIMISTIC_SL_FIRST"
    assert events_by_message[7] == []

    summary = HistoricalReputationService.summarize(db_session, analyst_id=analyst.id, channel_id=channel.id)
    assert summary.total_signals == 4
    assert summary.winning_signals == 2
    assert summary.losing_signals == 1
    assert summary.unfilled_signals == 1
    assert summary.rank_eligible_signals == 1
    assert summary.win_rate_percent == Decimal("66.6667")
    assert summary.pnl_sum_percent == Decimal("9.0000")

    historical_wallet = HistoricalSignalQueryService().search(db_session, trader_user_id=trader.id)
    assert [signal.id for signal in historical_wallet] == [parsed_signals[1].id]

    assert db_session.scalar(select(func.count()).select_from(Recommendation)) == 0
    assert db_session.scalar(select(func.count()).select_from(UserTrade)) == 0
    assert db_session.scalar(select(func.count()).select_from(PublicationDelivery)) == 0
