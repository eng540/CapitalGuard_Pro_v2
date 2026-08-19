"""Shared identity-aware search contract for portfolio, history, exports, and admin views."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import (
    ChannelCatalog,
    Recommendation,
    RecommendationChannelRef,
    User,
    UserTrade,
    WatchedChannel,
    RecommendationStatusEnum,
    UserTradeStatusEnum,
)


@dataclass(frozen=True)
class IdentityFilters:
    entity_type: str = "all"
    owner_type: Optional[str] = None
    owner_id: Optional[int] = None
    scope_code: Optional[str] = None
    source_type: Optional[str] = None
    channel_code: Optional[str] = None
    status: Optional[str] = None
    public_ref: Optional[str] = None
    scoped_sequence: Optional[int] = None
    asset: Optional[str] = None
    side: Optional[str] = None
    created_from: Optional[datetime] = None
    created_to: Optional[datetime] = None
    limit: int = 50


@dataclass(frozen=True)
class IdentityRecord:
    entity_type: str
    record: Any


class IdentityQueryService:
    """Applies the same semantic filters across recommendations and user trades."""

    @staticmethod
    def _bounded_limit(value: int) -> int:
        return max(1, min(int(value or 50), 200))

    @classmethod
    def search(cls, session: Session, filters: IdentityFilters) -> list[IdentityRecord]:
        results: list[IdentityRecord] = []
        entity_type = (filters.entity_type or "all").lower()
        channel_catalog_id = None
        if filters.channel_code:
            channel_catalog_id = session.execute(
                select(ChannelCatalog.id).where(ChannelCatalog.channel_code == filters.channel_code)
            ).scalar_one_or_none()
            if channel_catalog_id is None:
                return []

        if entity_type in {"all", "recommendation"}:
            query = select(Recommendation).join(User, User.id == Recommendation.analyst_id)
            conditions = []
            if filters.owner_type in {None, "analyst"} and filters.owner_id is not None:
                conditions.append(Recommendation.analyst_id == filters.owner_id)
            elif filters.owner_type == "trader":
                conditions.append(Recommendation.id == -1)
            if filters.scope_code:
                conditions.append(User.analyst_code == filters.scope_code)
            if filters.public_ref:
                conditions.append(Recommendation.public_ref == filters.public_ref)
            if filters.scoped_sequence is not None:
                conditions.append(Recommendation.analyst_sequence == filters.scoped_sequence)
            if filters.source_type and filters.source_type != "ANALYST_RECOMMENDATION":
                conditions.append(Recommendation.id == -1)
            if filters.status:
                status_key = str(filters.status).upper()
                conditions.append(Recommendation.status == RecommendationStatusEnum[status_key])
            if filters.asset:
                conditions.append(Recommendation.asset == filters.asset.upper())
            if filters.side:
                conditions.append(Recommendation.side == filters.side.upper())
            if filters.created_from:
                conditions.append(Recommendation.created_at >= filters.created_from)
            if filters.created_to:
                conditions.append(Recommendation.created_at <= filters.created_to)
            if channel_catalog_id is not None:
                query = query.join(
                    RecommendationChannelRef,
                    RecommendationChannelRef.recommendation_id == Recommendation.id,
                ).where(RecommendationChannelRef.channel_catalog_id == channel_catalog_id)
            if conditions:
                query = query.where(*conditions)
            for row in session.execute(query.order_by(Recommendation.created_at.desc(), Recommendation.id.desc())).scalars():
                results.append(IdentityRecord("recommendation", row))

        if entity_type in {"all", "user_trade"}:
            query = select(UserTrade).join(User, User.id == UserTrade.user_id)
            conditions = []
            if filters.owner_type in {None, "trader"} and filters.owner_id is not None:
                conditions.append(UserTrade.user_id == filters.owner_id)
            elif filters.owner_type == "analyst":
                conditions.append(UserTrade.id == -1)
            if filters.scope_code:
                conditions.append(User.user_code == filters.scope_code)
            if filters.public_ref:
                conditions.append(UserTrade.public_ref == filters.public_ref)
            if filters.scoped_sequence is not None:
                conditions.append(UserTrade.trader_sequence == filters.scoped_sequence)
            if filters.source_type:
                conditions.append(UserTrade.source_type == filters.source_type)
            if filters.status:
                status_key = str(filters.status).upper()
                conditions.append(UserTrade.status == UserTradeStatusEnum[status_key])
            if filters.asset:
                conditions.append(UserTrade.asset == filters.asset.upper())
            if filters.side:
                conditions.append(UserTrade.side == filters.side.upper())
            if filters.created_from:
                conditions.append(UserTrade.created_at >= filters.created_from)
            if filters.created_to:
                conditions.append(UserTrade.created_at <= filters.created_to)
            if channel_catalog_id is not None:
                query = query.join(
                    WatchedChannel,
                    WatchedChannel.id == UserTrade.watched_channel_id,
                ).where(WatchedChannel.channel_catalog_id == channel_catalog_id)
            if conditions:
                query = query.where(*conditions)
            for row in session.execute(query.order_by(UserTrade.created_at.desc(), UserTrade.id.desc())).scalars():
                results.append(IdentityRecord("user_trade", row))

        results.sort(key=lambda item: (item.record.created_at, item.record.id), reverse=True)
        return results[: cls._bounded_limit(filters.limit)]
