import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from capitalguard.application.services.historical_forwarding_service import (
    ForwardedMessageInput,
    HistoricalForwardingService,
)
from capitalguard.domain.entities import UserType
from capitalguard.infrastructure.db.models import ChannelCatalog, Recommendation, UserTrade
from capitalguard.infrastructure.db.repository import UserRepository
from capitalguard.interfaces.telegram.historical_forwarding_handler import (
    _preview_action_markup,
    historical_preview_decision_callback,
)


class _Message:
    def __init__(self):
        self.replies = []
        self.markup_removed = False

    async def reply_text(self, text):
        self.replies.append(text)


class _Query:
    def __init__(self, data):
        self.data = data
        self.message = _Message()
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_reply_markup(self, reply_markup=None):
        self.message.markup_removed = reply_markup is None


def test_preview_markup_exposes_explicit_historical_actions():
    markup = _preview_action_markup(24, {"IMPORT_HISTORICAL", "TRACK_ONLY", "DISMISS"})
    callbacks = [button.callback_data for button in markup.inline_keyboard[0]]
    assert callbacks == [
        "historical-preview:24:IMPORT_HISTORICAL",
        "historical-preview:24:TRACK_ONLY",
        "historical-preview:24:DISMISS",
    ]


def test_import_callback_moves_dry_run_to_owner_review_and_removes_buttons(db_session):
    requester = UserRepository(db_session).find_or_create(
        telegram_id=930004,
        user_type=UserType.ANALYST,
        first_name="Preview Callback Requester",
    )
    catalog = ChannelCatalog(
        telegram_channel_id=-1009304,
        channel_code="CH-CALLBACK",
        public_ref="CH-CALLBACK",
        title="Callback Channel",
    )
    db_session.add(catalog)
    db_session.flush()
    service = HistoricalForwardingService()
    batch = service.start_batch(
        db_session,
        channel_catalog_id=catalog.id,
        requested_by_user_id=requester.id,
        expected_source_chat_id=catalog.telegram_channel_id,
    )
    service.stage_message(
        db_session,
        batch_id=batch.id,
        message=ForwardedMessageInput(
            receiver_chat_id=requester.telegram_user_id,
            receiver_message_id=401,
            forwarding_user_id=requester.id,
            source_chat_id=catalog.telegram_channel_id,
            source_message_id=401,
            source_origin_type="CHANNEL",
            source_message_timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            raw_text="#BTCUSDT LONG Entry 100 SL 95 TP1 105@100%",
        ),
    )
    service.preview_batch(db_session, batch_id=batch.id)
    batch.metadata_json = {
        **(batch.metadata_json or {}),
        "parser_preview": {
            "review_actions_by_mode": {
                "HISTORICAL_RECONSTRUCTION": ["IMPORT_HISTORICAL", "TRACK_ONLY", "DISMISS"]
            }
        },
    }
    db_session.flush()

    query = _Query(f"historical-preview:{batch.id}:IMPORT_HISTORICAL")
    update = SimpleNamespace(callback_query=query)
    raw_callback = historical_preview_decision_callback.__wrapped__.__wrapped__
    asyncio.run(raw_callback(update, SimpleNamespace(), db_session=db_session, db_user=requester))

    db_session.refresh(batch)
    assert batch.status == "REVIEW_REQUIRED"
    assert batch.metadata_json["preview_decision"]["action"] == "IMPORT_HISTORICAL"
    assert query.message.markup_removed is True
    assert "Owner Review" in query.message.replies[0]
    assert db_session.query(Recommendation).count() == 0
    assert db_session.query(UserTrade).count() == 0
