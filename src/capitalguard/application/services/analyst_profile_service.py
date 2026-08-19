"""R2 analyst profile read/update service."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.domain.entities import UserType
from capitalguard.infrastructure.db.models import AnalystProfile, User


class AnalystProfileService:
    """Owns analyst profile validation and mutation; no public ranking logic lives here."""

    MAX_NAME_LENGTH = 120
    MAX_BIO_LENGTH = 2000
    MAX_MARKET_LENGTH = 80
    MAX_STYLE_LENGTH = 80

    @staticmethod
    def _clean(value: Any, limit: int, field_name: str) -> str | None:
        if value is None:
            return None
        cleaned = str(value).strip()
        if not cleaned:
            return None
        if len(cleaned) > limit:
            raise ValueError(f"{field_name} exceeds the {limit}-character limit")
        return cleaned

    def get_or_create(self, session: Session, analyst: User) -> AnalystProfile:
        if analyst.user_type != UserType.ANALYST:
            raise ValueError("Only analysts can have an analyst profile")
        profile = session.execute(
            select(AnalystProfile).where(AnalystProfile.user_id == analyst.id)
        ).scalar_one_or_none()
        if profile is None:
            profile = AnalystProfile(user_id=analyst.id)
            session.add(profile)
            session.flush()
        return profile

    def update_profile(
        self,
        session: Session,
        analyst: User,
        *,
        public_name: Any = None,
        bio: Any = None,
        specialty_market: Any = None,
        strategy_style: Any = None,
        is_public: bool | None = None,
    ) -> AnalystProfile:
        profile = self.get_or_create(session, analyst)
        if public_name is not None:
            profile.public_name = self._clean(public_name, self.MAX_NAME_LENGTH, "public_name")
        if bio is not None:
            profile.bio = self._clean(bio, self.MAX_BIO_LENGTH, "bio")
        if specialty_market is not None:
            profile.specialty_market = self._clean(
                specialty_market, self.MAX_MARKET_LENGTH, "specialty_market"
            )
        if strategy_style is not None:
            profile.strategy_style = self._clean(
                strategy_style, self.MAX_STYLE_LENGTH, "strategy_style"
            )
        if is_public is not None:
            profile.is_public = bool(is_public)
        profile.profile_updated_at = datetime.now(timezone.utc)
        session.flush()
        return profile

    @staticmethod
    def as_dict(profile: AnalystProfile, analyst: User | None = None) -> dict[str, Any]:
        return {
            "analyst_id": profile.user_id,
            "analyst_code": getattr(analyst, "analyst_code", None),
            "public_name": profile.public_name,
            "bio": profile.bio,
            "specialty_market": profile.specialty_market,
            "strategy_style": profile.strategy_style,
            "is_public": bool(profile.is_public),
            "profile_updated_at": profile.profile_updated_at,
        }
