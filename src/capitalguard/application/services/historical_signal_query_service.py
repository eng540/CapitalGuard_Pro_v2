"""Read-only historical signal and wallet queries."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import (
    HistoricalSignal,
    HistoricalSignalAttribution,
)


class HistoricalSignalQueryService:
    @staticmethod
    def _utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def search(
        self,
        session: Session,
        *,
        analyst_id: int | None = None,
        channel_id: int | None = None,
        trader_user_id: int | None = None,
        asset: str | None = None,
        trust_tiers: Iterable[str] | None = None,
        eligible_for_ranking: bool | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 50,
    ) -> list[HistoricalSignal]:
        limit = max(1, min(int(limit), 200))
        statement = select(HistoricalSignal)
        if trader_user_id is not None:
            statement = statement.join(
                HistoricalSignalAttribution,
                HistoricalSignalAttribution.signal_id == HistoricalSignal.id,
            ).where(
                HistoricalSignalAttribution.trader_user_id == trader_user_id,
                HistoricalSignalAttribution.attribution_kind == "TRADER_FOLLOW",
                HistoricalSignalAttribution.status == "RECORDED",
            )
        if analyst_id is not None:
            statement = statement.where(HistoricalSignal.analyst_id == analyst_id)
        if channel_id is not None:
            statement = statement.where(HistoricalSignal.channel_id == channel_id)
        if asset:
            statement = statement.where(HistoricalSignal.asset == asset.strip().upper())
        if trust_tiers:
            normalized = [str(tier).strip().upper() for tier in trust_tiers if str(tier).strip()]
            if normalized:
                statement = statement.where(HistoricalSignal.trust_tier.in_(normalized))
        if eligible_for_ranking is not None:
            statement = statement.where(HistoricalSignal.eligible_for_ranking == eligible_for_ranking)
        if start_at is not None:
            statement = statement.where(HistoricalSignal.decision_timestamp >= self._utc(start_at))
        if end_at is not None:
            statement = statement.where(HistoricalSignal.decision_timestamp <= self._utc(end_at))
        statement = statement.order_by(HistoricalSignal.decision_timestamp.desc(), HistoricalSignal.id.desc()).limit(limit)
        return list(session.execute(statement).scalars().unique().all())
