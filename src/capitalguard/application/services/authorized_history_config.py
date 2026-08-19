"""Build authorized history reader policy from server-side configuration."""
from __future__ import annotations

from typing import Protocol

from .authorized_history_connector import ConnectorAccessError, ReaderAccountPolicy


class HistorySettings(Protocol):
    HISTORY_CONNECTOR_ENABLED: bool
    HISTORY_READER_ACCOUNT_ALIAS: str | None
    HISTORY_SESSION_SECRET_REF: str | None
    HISTORY_ALLOWED_CHANNEL_IDS: str


def build_reader_policy(settings: HistorySettings) -> ReaderAccountPolicy:
    if not settings.HISTORY_CONNECTOR_ENABLED:
        raise ConnectorAccessError("Historical connector is disabled")
    alias = (settings.HISTORY_READER_ACCOUNT_ALIAS or "").strip()
    secret_ref = (settings.HISTORY_SESSION_SECRET_REF or "").strip()
    if not alias or not secret_ref:
        raise ConnectorAccessError("Reader account alias and session secret reference are required")
    channel_ids: set[int] = set()
    for token in (settings.HISTORY_ALLOWED_CHANNEL_IDS or "").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            channel_ids.add(int(token))
        except ValueError as exc:
            raise ConnectorAccessError("HISTORY_ALLOWED_CHANNEL_IDS must contain integers") from exc
    if not channel_ids:
        raise ConnectorAccessError("At least one history channel must be allow-listed")
    return ReaderAccountPolicy(
        account_alias=alias,
        session_secret_ref=secret_ref,
        allowed_channel_ids=frozenset(channel_ids),
    )
