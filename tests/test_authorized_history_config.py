from types import SimpleNamespace

import pytest

from capitalguard.application.services.authorized_history_config import build_reader_policy
from capitalguard.application.services.authorized_history_connector import ConnectorAccessError


def settings(**overrides):
    values = {
        "HISTORY_CONNECTOR_ENABLED": False,
        "HISTORY_READER_ACCOUNT_ALIAS": None,
        "HISTORY_SESSION_SECRET_REF": None,
        "HISTORY_ALLOWED_CHANNEL_IDS": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_history_connector_is_disabled_by_default():
    with pytest.raises(ConnectorAccessError, match="disabled"):
        build_reader_policy(settings())


def test_history_connector_requires_session_ref_and_allowlist():
    with pytest.raises(ConnectorAccessError, match="secret reference"):
        build_reader_policy(settings(HISTORY_CONNECTOR_ENABLED=True, HISTORY_READER_ACCOUNT_ALIAS="reader"))
    with pytest.raises(ConnectorAccessError, match="allow-listed"):
        build_reader_policy(
            settings(
                HISTORY_CONNECTOR_ENABLED=True,
                HISTORY_READER_ACCOUNT_ALIAS="reader",
                HISTORY_SESSION_SECRET_REF="railway://history/session",
            )
        )


def test_history_policy_parses_integer_allowlist():
    policy = build_reader_policy(
        settings(
            HISTORY_CONNECTOR_ENABLED=True,
            HISTORY_READER_ACCOUNT_ALIAS="reader",
            HISTORY_SESSION_SECRET_REF="railway://history/session",
            HISTORY_ALLOWED_CHANNEL_IDS="-1001, -1002",
        )
    )

    assert policy.allowed_channel_ids == frozenset({-1001, -1002})
