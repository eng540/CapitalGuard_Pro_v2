"""Layered identity allocation for public references and scoped sequences."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models.channel_catalog import ChannelCatalog
from capitalguard.infrastructure.db.models.identity import ScopedIdentityCounter


class IdentityService:
    """Allocates opaque public references and transactional human-readable IDs."""

    @staticmethod
    def public_ref(prefix: str) -> str:
        """Return a non-sequential public reference that does not expose row volume."""
        return f"{prefix.upper()}-{uuid4().hex[:26].upper()}"

    @staticmethod
    def _next_value(session: Session, scope_type: str, scope_id: int = 0) -> int:
        """Allocate the next value for a scope; locking also covers first-row creation."""
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:scope_key))"),
                {"scope_key": f"capitalguard:{scope_type}:{scope_id}"},
            )
        stmt = (
            select(ScopedIdentityCounter)
            .where(
                ScopedIdentityCounter.scope_type == scope_type,
                ScopedIdentityCounter.scope_id == scope_id,
            )
            .with_for_update()
        )
        counter = session.execute(stmt).scalar_one_or_none()
        if counter is None:
            counter = ScopedIdentityCounter(
                scope_type=scope_type,
                scope_id=scope_id,
                next_value=2,
            )
            session.add(counter)
            session.flush()
            return 1

        allocated = int(counter.next_value)
        session.execute(
            update(ScopedIdentityCounter)
            .where(
                ScopedIdentityCounter.scope_type == scope_type,
                ScopedIdentityCounter.scope_id == scope_id,
            )
            .values(next_value=allocated + 1)
        )
        return allocated

    @classmethod
    def user_identity(cls, session: Session) -> tuple[str, str]:
        return cls.public_ref("USR"), f"USR-{cls._next_value(session, 'USER_CODE'):06d}"

    @classmethod
    def analyst_identity(cls, session: Session) -> tuple[str, str]:
        return cls.public_ref("AN"), f"AN-{cls._next_value(session, 'ANALYST_CODE'):06d}"

    @classmethod
    def ensure_channel_catalog(cls, session: Session, telegram_channel_id: int, title: str | None = None) -> ChannelCatalog:
        """Return the canonical channel row for a Telegram channel."""
        catalog = session.execute(
            select(ChannelCatalog).where(ChannelCatalog.telegram_channel_id == telegram_channel_id)
        ).scalar_one_or_none()
        if catalog:
            if title and not catalog.title:
                catalog.title = title
                session.flush()
            return catalog

        public_ref = cls.public_ref("CH")
        sequence = cls._next_value(session, "CHANNEL_CODE")
        catalog = ChannelCatalog(
            public_ref=public_ref,
            channel_code=f"CH-{sequence:06d}",
            telegram_channel_id=telegram_channel_id,
            title=title,
            is_active=True,
        )
        session.add(catalog)
        session.flush()
        return catalog

    @classmethod
    def recommendation_identity(cls, session: Session, analyst_id: int) -> tuple[str, int]:
        return cls.public_ref("REC"), cls._next_value(session, "ANALYST_RECOMMENDATION", analyst_id)

    @classmethod
    def trade_identity(cls, session: Session, user_id: int) -> tuple[str, int]:
        return cls.public_ref("TRD"), cls._next_value(session, "TRADER_TRADE", user_id)

    @classmethod
    def channel_recommendation_sequence(cls, session: Session, channel_catalog_id: int) -> int:
        return cls._next_value(session, "CHANNEL_RECOMMENDATION", channel_catalog_id)

    @staticmethod
    def display_ref(owner_code: str, kind: str, sequence: int) -> str:
        return f"{owner_code}/{kind}-{int(sequence):06d}"

    @staticmethod
    def attach_identity(entity: Any, public_ref: str, scope_code: str | None, kind: str, sequence: int) -> None:
        """Attach presentation metadata without changing domain lifecycle behavior."""
        setattr(entity, "public_ref", public_ref)
        setattr(entity, "scope_code", scope_code)
        setattr(entity, "scoped_sequence", sequence)
        setattr(entity, "display_ref", IdentityService.display_ref(scope_code, kind, sequence) if scope_code else public_ref)
