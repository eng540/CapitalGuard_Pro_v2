"""
Regression tests for smart_safe_edit bounded retry behavior.

Closes the verification gap identified during G6 Production Closure review:
smart_safe_edit() had implementation coverage but no dedicated regression test.

Target implementation:
    src/capitalguard/interfaces/telegram/forward_parsing_handler.py

Design notes
------------
- pyproject.toml does not enable pytest-asyncio, so coroutines are driven with
  asyncio.run() from synchronous tests.
- asyncio.sleep is patched so the suite runs without real delays.
- Bot is a MagicMock and edit_message_text is an AsyncMock.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import BadRequest, TelegramError

from capitalguard.interfaces.telegram.forward_parsing_handler import smart_safe_edit

_MODULE = "capitalguard.interfaces.telegram.forward_parsing_handler"


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.edit_message_text = AsyncMock()
    return bot


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def no_sleep():
    """Replace handler asyncio.sleep with an inert AsyncMock."""
    with patch(f"{_MODULE}.asyncio.sleep", new_callable=AsyncMock) as sleeper:
        yield sleeper


@pytest.fixture(autouse=True)
def _quiet_loggers():
    with patch(f"{_MODULE}.log"), patch(f"{_MODULE}.loge"):
        yield


def test_first_telegram_error_then_success(no_sleep):
    bot = _make_bot()
    bot.edit_message_text.side_effect = [TelegramError("flaky network"), None]

    result = _run(smart_safe_edit(bot, chat_id=1, message_id=2, text="hi"))

    assert result is True
    assert bot.edit_message_text.call_count == 2
    no_sleep.assert_awaited_once_with(0.25)


def test_all_attempts_fail_returns_false(no_sleep):
    bot = _make_bot()
    bot.edit_message_text.side_effect = TelegramError("always down")

    result = _run(smart_safe_edit(bot, chat_id=1, message_id=2, text="hi"))

    assert result is False
    assert bot.edit_message_text.call_count == 3
    assert no_sleep.await_count == 2
    assert [call.args for call in no_sleep.await_args_list] == [(0.25,), (0.5,)]


def test_message_not_modified_returns_true_no_retry(no_sleep):
    bot = _make_bot()
    bot.edit_message_text.side_effect = BadRequest("Message is not modified")

    result = _run(smart_safe_edit(bot, chat_id=1, message_id=2, text="hi"))

    assert result is True
    assert bot.edit_message_text.call_count == 1
    no_sleep.assert_not_awaited()


def test_parse_entity_error_falls_back_to_no_parse_mode(no_sleep):
    bot = _make_bot()
    bot.edit_message_text.side_effect = [
        BadRequest("Can't parse entities: unsupported start tag"),
        None,
    ]

    result = _run(
        smart_safe_edit(
            bot,
            chat_id=1,
            message_id=2,
            text="<b>Hello</b>",
            parse_mode="HTML",
        )
    )

    assert result is True
    assert bot.edit_message_text.call_count == 2
    assert bot.edit_message_text.call_args_list[1].kwargs["parse_mode"] is None
    no_sleep.assert_not_awaited()


def test_unexpected_exception_retries_within_bound(no_sleep):
    bot = _make_bot()
    bot.edit_message_text.side_effect = [ValueError("unexpected"), None]

    result = _run(smart_safe_edit(bot, chat_id=1, message_id=2, text="hi"))

    assert result is True
    assert bot.edit_message_text.call_count == 2
    no_sleep.assert_awaited_once_with(0.25)


def test_retry_only_invokes_edit_message_text(no_sleep):
    bot = _make_bot()
    bot.edit_message_text.side_effect = TelegramError("flaky")

    _run(smart_safe_edit(bot, chat_id=1, message_id=2, text="hi"))

    assert [name for name, _args, _kwargs in bot.method_calls] == [
        "edit_message_text",
        "edit_message_text",
        "edit_message_text",
    ]
