from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from capitalguard.config import settings
from capitalguard.domain.entities import UserType
from capitalguard.interfaces.telegram.admin_commands import admin_panel_cmd
from capitalguard.interfaces.telegram.commands import commands_cmd
from capitalguard.interfaces.telegram.ui_texts import _record_identity


@pytest.mark.parametrize(
    ("source_type", "expected_badge", "expected_id"),
    [
        ("DIRECT_INPUT", "Trader Log", "UserTrade #11"),
        ("TRACKED_RECOMMENDATION", "Tracked Signal", "UserTrade #12"),
        ("ANALYST_RECOMMENDATION", "Analyst Recommendation", "Recommendation #13"),
    ],
)
def test_record_identity_by_source(source_type, expected_badge, expected_id):
    record = SimpleNamespace(record_id=10 + int(expected_id.split("#")[1]) - 10, source_type=source_type)

    badge, stable_id = _record_identity(record)

    assert expected_badge in badge
    assert expected_id in stable_id


@pytest.mark.asyncio
async def test_commands_cmd_shows_trader_directory():
    update = MagicMock()
    update.message.reply_html = AsyncMock()
    trader = SimpleNamespace(user_type=UserType.TRADER, telegram_user_id=101)

    # Unwrap UoW and active-user decorators; this test targets rendering only.
    handler = commands_cmd.__wrapped__.__wrapped__
    await handler(update, None, db_session=None, db_user=trader)

    rendered = update.message.reply_html.await_args.args[0]
    assert "<b>Trader</b>" in rendered
    assert "/log" in rendered
    assert "Tracked Signals" in rendered
    assert "/newrec" not in rendered


@pytest.mark.asyncio
async def test_commands_cmd_shows_analyst_directory():
    update = MagicMock()
    update.message.reply_html = AsyncMock()
    analyst = SimpleNamespace(user_type=UserType.ANALYST, telegram_user_id=102)

    handler = commands_cmd.__wrapped__.__wrapped__
    await handler(update, None, db_session=None, db_user=analyst)

    rendered = update.message.reply_html.await_args.args[0]
    assert "<b>Analyst</b>" in rendered
    assert "/newrec" in rendered
    assert "/channels" in rendered
    assert "/log" not in rendered


@pytest.mark.asyncio
async def test_admin_panel_lists_operations_for_admin(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "777")
    update = MagicMock()
    update.effective_chat.id = 777
    update.message.reply_html = AsyncMock()

    await admin_panel_cmd(update, None)

    rendered = update.message.reply_html.await_args.args[0]
    assert "/grantaccess" in rendered
    assert "/revokeaccess" in rendered
    assert "/makeanalyst" in rendered
    assert "/backup" in rendered
    assert "تأكيد مزدوج" in rendered


@pytest.mark.asyncio
async def test_admin_panel_is_silent_for_non_admin(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "777")
    update = MagicMock()
    update.effective_chat.id = 778
    update.message.reply_html = AsyncMock()

    await admin_panel_cmd(update, None)

    update.message.reply_html.assert_not_awaited()
