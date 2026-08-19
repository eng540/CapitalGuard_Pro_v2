"""Channel-aware analyst performance comparisons for R2."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.domain.entities import RecommendationStatus
from capitalguard.infrastructure.db.models import (
    ChannelCatalog,
    Recommendation,
    RecommendationChannelRef,
)


class AnalystComparisonService:
    """Compare an analyst's published recommendation outcomes by canonical channel."""

    def __init__(self, minimum_sample_size: int = 5):
        self.minimum_sample_size = max(1, int(minimum_sample_size))

    @staticmethod
    def _pnl_pct(recommendation: Recommendation) -> Decimal | None:
        if recommendation.exit_price is None or recommendation.entry is None:
            return None
        entry = Decimal(str(recommendation.entry))
        exit_price = Decimal(str(recommendation.exit_price))
        if entry <= 0:
            return None
        if str(recommendation.side).upper().endswith("SHORT"):
            return (entry - exit_price) / entry * Decimal("100")
        return (exit_price - entry) / entry * Decimal("100")

    def compare_channels(
        self,
        session: Session,
        analyst_id: int,
        channel_codes: Iterable[str] | None = None,
        asset: str | None = None,
        market: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        window_days: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = (
            select(Recommendation, ChannelCatalog)
            .join(RecommendationChannelRef, RecommendationChannelRef.recommendation_id == Recommendation.id)
            .join(ChannelCatalog, ChannelCatalog.id == RecommendationChannelRef.channel_catalog_id)
            .where(
                Recommendation.analyst_id == analyst_id,
                Recommendation.is_shadow.is_(False),
                Recommendation.status == RecommendationStatus.CLOSED,
            )
        )
        if channel_codes:
            normalized_codes = {str(code).strip().upper() for code in channel_codes if str(code).strip()}
            query = query.where(ChannelCatalog.channel_code.in_(normalized_codes))
        if asset:
            query = query.where(Recommendation.asset == asset.strip().upper())
        if market:
            query = query.where(Recommendation.market == market.strip())
        effective_to = created_to or datetime.now(timezone.utc)
        if window_days and not created_from:
            created_from = effective_to - timedelta(days=max(1, int(window_days)))
        if created_from:
            query = query.where(Recommendation.created_at >= created_from)
        if created_to:
            query = query.where(Recommendation.created_at <= created_to)

        grouped: dict[int, dict[str, Any]] = defaultdict(dict)
        for recommendation, channel in session.execute(query).all():
            group = grouped.setdefault(
                channel.id,
                {
                    "channel_id": channel.id,
                    "channel_code": channel.channel_code,
                    "channel_public_ref": channel.public_ref,
                    "channel_title": channel.title,
                    "market_filter": market,
                    "asset_filter": asset,
                    "window_days": window_days,
                    "pnls": [],
                    "active_assets": set(),
                },
            )
            pnl = self._pnl_pct(recommendation)
            if pnl is not None:
                group["pnls"].append((recommendation, pnl))

        results = []
        for group in grouped.values():
            valid = sorted(
                group["pnls"],
                key=lambda pair: (pair[0].closed_at or pair[0].created_at, pair[0].id),
            )
            pnls = [pnl for _, pnl in valid]
            sample_size = len(pnls)
            winning = sum(1 for pnl in pnls if pnl > 0)
            cumulative = Decimal("0")
            peak = Decimal("0")
            max_drawdown = Decimal("0")
            for pnl in pnls:
                cumulative += pnl
                peak = max(peak, cumulative)
                max_drawdown = max(max_drawdown, peak - cumulative)

            results.append(
                {
                    "channel_id": group["channel_id"],
                    "channel_code": group["channel_code"],
                    "channel_public_ref": group["channel_public_ref"],
                    "channel_title": group["channel_title"],
                    "sample_size": sample_size,
                    "win_rate_pct": (Decimal(winning) / Decimal(sample_size) * Decimal("100")) if sample_size else Decimal("0"),
                    "total_pnl_pct": sum(pnls, Decimal("0")),
                    "max_drawdown_pct": max_drawdown,
                    "eligible_for_comparison": sample_size >= self.minimum_sample_size,
                    "minimum_sample_size": self.minimum_sample_size,
                    "market_filter": group["market_filter"],
                    "asset_filter": group["asset_filter"],
                    "window_days": group["window_days"],
                }
            )

        results.sort(
            key=lambda item: (
                item["eligible_for_comparison"],
                item["total_pnl_pct"],
                item["sample_size"],
                -item["max_drawdown_pct"],
            ),
            reverse=True,
        )
        return results[: max(1, min(int(limit or 50), 100))]
