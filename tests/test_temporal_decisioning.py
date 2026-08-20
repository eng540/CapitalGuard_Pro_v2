from datetime import datetime, timedelta, timezone
from decimal import Decimal

from capitalguard.application.services.forward_intake_router import ApplicationRoute, ForwardIntakeRouter
from capitalguard.application.services.historical_forwarding_service import ForwardedMessageInput
from capitalguard.application.services.frictionless_ingestion_service import FrictionlessIngestionService
from capitalguard.application.services.price_validity_service import PriceValidityService
from capitalguard.application.services.signal_timeline_resolver import SignalTimelineResolver, TimelineCandidate
from capitalguard.application.services.temporal_decision_service import TemporalDecisionService
from capitalguard.application.services.temporal_normalizer import TemporalNormalizer
from capitalguard.domain.temporal import TemporalMode, TimelineRelation


BASE = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
PAYLOAD = {
    "asset": "BTCUSDT",
    "side": "LONG",
    "entry": "70000",
    "stop_loss": "69000",
    "targets": [{"price": "71000", "close_percent": 100}],
}


def context(*, age_seconds=30, event_time=None, edit_time=None, origin="CHANNEL"):
    return TemporalNormalizer().normalize(
        source_time=BASE - timedelta(seconds=age_seconds),
        received_time=BASE,
        ingested_time=BASE,
        event_time=event_time,
        edit_time=edit_time,
        source_origin_type=origin,
        source_chat_id="-100123",
        source_message_id="77",
    )


def test_timeline_resolver_matches_reply_parent():
    candidate = TimelineCandidate(
        signal_id=10,
        source_chat_id=-100123,
        source_message_id=77,
        asset="BTCUSDT",
        side="LONG",
        source_time=BASE - timedelta(minutes=5),
    )
    result = SignalTimelineResolver().resolve(
        source_chat_id=-100123,
        source_message_id=78,
        reply_to_message_id=77,
        source_time=BASE,
        asset="BTCUSDT",
        side="LONG",
        event_kind="STOP_UPDATE",
        candidates=[candidate],
    )
    assert result.parent_signal_id == 10
    assert result.relation == TimelineRelation.STOP_UPDATE
    assert result.conflict is False


def test_timeline_resolver_flags_multiple_parent_candidates():
    candidates = [
        TimelineCandidate(10, -100123, 77, "BTCUSDT", "LONG", BASE - timedelta(minutes=5)),
        TimelineCandidate(11, -100123, 76, "BTCUSDT", "LONG", BASE - timedelta(minutes=4)),
    ]
    result = SignalTimelineResolver().resolve(
        source_chat_id=-100123,
        source_message_id=78,
        reply_to_message_id=None,
        source_time=BASE,
        asset="BTCUSDT",
        side="LONG",
        event_kind="UPDATE",
        candidates=candidates,
    )
    assert result.conflict is True
    assert result.parent_signal_id is None
    assert "MULTIPLE_ASSET_TIME_PARENTS" in result.reason_codes


def test_timeline_resolver_keeps_initial_signal_without_parent():
    result = SignalTimelineResolver().resolve(
        source_chat_id=-100123,
        source_message_id=78,
        reply_to_message_id=None,
        source_time=BASE,
        asset="BTCUSDT",
        side="LONG",
        event_kind="INITIAL_SIGNAL",
        candidates=[],
    )
    assert result.relation == TimelineRelation.INITIAL_SIGNAL
    assert result.parent_signal_id is None
    assert result.conflict is False


def test_frictionless_temporal_metadata_classifies_close_as_timeline_event():
    service = FrictionlessIngestionService()
    message = ForwardedMessageInput(
        receiver_chat_id=10,
        receiver_message_id=20,
        forwarding_user_id=1,
        source_chat_id=-100123,
        source_message_id=77,
        source_origin_type="CHANNEL",
        source_message_timestamp=BASE - timedelta(hours=2),
        raw_text="#BTCUSDT TRADE CLOSED Exit 70000",
        metadata={"receiver_date": BASE.isoformat()},
    )
    metadata = service.temporal_metadata_for_message(message)
    assert metadata["event_kind"] == "CLOSE"
    assert metadata["timeline_relation"] == "CLOSE"
    assert metadata["temporal_decision"]["mode"] == "CLOSED_EVENT"
    assert metadata["temporal_decision"]["reason_codes"] == ["TERMINAL_EVENT", "APPEND_ONLY_TIMELINE"]


def test_frictionless_temporal_metadata_marks_fresh_signal_live_eligible_with_snapshot():
    service = FrictionlessIngestionService()
    message = ForwardedMessageInput(
        receiver_chat_id=10,
        receiver_message_id=22,
        forwarding_user_id=1,
        source_chat_id=-100123,
        source_message_id=79,
        source_origin_type="CHANNEL",
        source_message_timestamp=BASE,
        raw_text="#BTCUSDT LONG Entry 70000 Stop 69000 TP1 71000",
        metadata={"receiver_date": (BASE + timedelta(seconds=1)).isoformat()},
    )
    metadata = service.temporal_metadata_for_message(
        message,
        parsed_payload=PAYLOAD,
        current_price="70050",
        market_data_available=True,
        market_snapshot_time=BASE + timedelta(seconds=1),
    )
    assert metadata["temporal_decision"]["mode"] == "LIVE_ELIGIBLE"
    assert metadata["temporal_decision"]["route"] == "LIVE_REVIEW"
    assert metadata["temporal_decision"]["price_validity"] is not None
    assert metadata["market_snapshot"]["available"] is True


def test_frictionless_temporal_metadata_routes_initial_forward_to_historical_without_price():
    service = FrictionlessIngestionService()
    message = ForwardedMessageInput(
        receiver_chat_id=10,
        receiver_message_id=21,
        forwarding_user_id=1,
        source_chat_id=-100123,
        source_message_id=78,
        source_origin_type="CHANNEL",
        source_message_timestamp=BASE,
        raw_text="#BTCUSDT LONG Entry 70000 Stop 69000 TP1 71000",
        metadata={"receiver_date": (BASE + timedelta(seconds=1)).isoformat()},
    )
    metadata = service.temporal_metadata_for_message(message)
    assert metadata["event_kind"] == "INITIAL_SIGNAL"
    assert metadata["temporal_decision"]["mode"] == "LIVE_STALE"
    assert metadata["temporal_decision"]["route"] == "HISTORICAL_CANDIDATE"


def test_normalizer_preserves_temporal_roles_and_normalizes_channel_origin():
    result = TemporalNormalizer().normalize(
        source_time=BASE - timedelta(minutes=5),
        received_time=BASE,
        ingested_time=BASE + timedelta(seconds=1),
        source_origin_type="MESSAGE_ORIGIN_CHANNEL",
        source_chat_id="-100123",
        source_message_id="44",
        source_message_revision="2",
    )
    assert result.source_origin_type == "CHANNEL"
    assert result.source_chat_id == -100123
    assert result.source_message_id == 44
    assert result.source_message_revision == 2
    assert result.age_seconds == 300
    assert result.effective_market_as_of == BASE - timedelta(minutes=5)


def test_price_validity_accepts_fresh_price_inside_risk_envelope():
    result = PriceValidityService().evaluate(
        source_time=BASE - timedelta(seconds=30),
        received_time=BASE,
        current_price="70050",
        reference_price="70000",
        stop_loss="69000",
    )
    assert result.valid_for_live is True
    assert "PRICE_WITHIN_ENVELOPE" in result.reason_codes
    assert result.drift_pct == Decimal("0.0007142857142857142857142857143")


def test_decision_routes_old_message_to_historical_reconstruction():
    result = TemporalDecisionService().decide(
        temporal=context(age_seconds=3600),
        parsed_payload=PAYLOAD,
        current_price="70050",
    )
    assert result.mode == TemporalMode.HISTORICAL_RECONSTRUCTION
    assert "REPLAY_REQUIRED" in result.reason_codes
    assert result.temporal.effective_market_as_of == BASE - timedelta(seconds=3600)


def test_decision_routes_fresh_valid_message_to_live_review():
    result = TemporalDecisionService().decide(
        temporal=context(age_seconds=30),
        parsed_payload=PAYLOAD,
        current_price="70050",
    )
    assert result.mode == TemporalMode.LIVE_ELIGIBLE
    assert result.requires_review is True


def test_decision_routes_timeline_update_without_creating_new_signal():
    result = TemporalDecisionService().decide(
        temporal=context(age_seconds=60),
        parsed_payload=PAYLOAD,
        event_kind="STOP_UPDATE",
        timeline_relation=TimelineRelation.STOP_UPDATE,
    )
    assert result.mode == TemporalMode.UPDATE_EVENT
    assert "APPEND_ONLY_TIMELINE" in result.reason_codes


def test_decision_routes_close_as_terminal_event():
    result = TemporalDecisionService().decide(
        temporal=context(age_seconds=60, event_time=BASE - timedelta(seconds=55)),
        parsed_payload=PAYLOAD,
        event_kind="FINAL_CLOSE",
        timeline_relation=TimelineRelation.CLOSE,
    )
    assert result.mode == TemporalMode.CLOSED_EVENT
    assert result.requires_review is True


def test_decision_flags_edit_after_event_as_new_revision():
    result = TemporalDecisionService().decide(
        temporal=context(
            age_seconds=60,
            event_time=BASE - timedelta(seconds=55),
            edit_time=BASE - timedelta(seconds=10),
        ),
        parsed_payload=PAYLOAD,
    )
    assert result.mode == TemporalMode.EDITED_AFTER_MARKET
    assert "NEW_REVISION_REQUIRED" in result.reason_codes


def test_router_selects_live_review_for_fresh_valid_signal():
    plan = ForwardIntakeRouter().plan(
        temporal=context(age_seconds=30),
        parsed_payload=PAYLOAD,
        current_price="70050",
    )
    assert plan.route == ApplicationRoute.LIVE_REVIEW
    assert plan.creates_live_entity is True
    assert plan.creates_historical_candidate is False


def test_router_selects_historical_candidate_for_stale_signal():
    plan = ForwardIntakeRouter().plan(
        temporal=context(age_seconds=3600),
        parsed_payload=PAYLOAD,
        current_price="70050",
    )
    assert plan.route == ApplicationRoute.HISTORICAL_CANDIDATE
    assert plan.creates_live_entity is False
    assert plan.creates_historical_candidate is True


def test_router_quarantines_timeline_parent_conflict():
    plan = ForwardIntakeRouter().plan(
        temporal=context(age_seconds=60),
        parsed_payload=PAYLOAD,
        event_kind="UPDATE",
        timeline_relation=TimelineRelation.AMENDMENT,
        timeline_conflict=True,
    )
    assert plan.decision.mode == TemporalMode.CONFLICT_REVIEW
    assert plan.route == ApplicationRoute.REVISION_REVIEW
    assert plan.creates_live_entity is False
    assert plan.creates_historical_candidate is True


def test_router_selects_timeline_event_for_stop_update():
    plan = ForwardIntakeRouter().plan(
        temporal=context(age_seconds=60),
        parsed_payload=PAYLOAD,
        event_kind="STOP_UPDATE",
        timeline_relation=TimelineRelation.STOP_UPDATE,
    )
    assert plan.route == ApplicationRoute.TIMELINE_EVENT
    assert plan.appends_timeline_event is True


def test_unverified_origin_never_becomes_live():
    result = TemporalDecisionService().decide(
        temporal=context(age_seconds=10, origin="UNKNOWN"),
        parsed_payload=PAYLOAD,
        current_price="70000",
    )
    assert result.mode == TemporalMode.UNVERIFIED_TIME
    assert result.requires_review is True
