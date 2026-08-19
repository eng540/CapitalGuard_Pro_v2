"""R2 analyst discovery and sample-aware performance calculations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.domain.entities import RecommendationStatus, UserType
from capitalguard.infrastructure.db.models import AnalystProfile, Recommendation, User


class AnalystDiscoveryService:
    """Read-only discovery service with conservative sample-size handling."""

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

    def _stats(
        self,
        recommendations: list[Recommendation],
        *,
        window_days: int | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        closed = [
            (recommendation, self._pnl_pct(recommendation))
            for recommendation in recommendations
            if recommendation.status == RecommendationStatus.CLOSED
        ]
        valid = [(recommendation, pnl) for recommendation, pnl in closed if pnl is not None]
        pnls = [pnl for _, pnl in valid]
        sample_size = len(pnls)
        winning = sum(1 for pnl in pnls if pnl > 0)
        total_pnl = sum(pnls, Decimal("0"))

        cumulative = Decimal("0")
        peak = Decimal("0")
        max_drawdown = Decimal("0")
        for _, pnl in sorted(valid, key=lambda pair: (pair[0].closed_at or pair[0].created_at, pair[0].id)):
            cumulative += pnl
            peak = max(peak, cumulative)
            max_drawdown = max(max_drawdown, peak - cumulative)

        active = [
            recommendation
            for recommendation in recommendations
            if recommendation.status in {RecommendationStatus.PENDING, RecommendationStatus.ACTIVE}
            and not recommendation.is_shadow
        ]
        activated = [
            recommendation
            for recommendation in active
            if recommendation.status == RecommendationStatus.ACTIVE
        ]
        risk_exposure_pct = Decimal("0")
        for recommendation in activated:
            entry = Decimal(str(recommendation.entry or 0))
            stop_loss = Decimal(str(recommendation.stop_loss or 0))
            if entry > 0:
                risk_exposure_pct += abs(entry - stop_loss) / entry * Decimal("100")

        freshness_source = [
            timestamp
            for recommendation, _ in valid
            for timestamp in (recommendation.closed_at, recommendation.created_at)
            if timestamp is not None
        ]
        latest_data_at = max(freshness_source) if freshness_source else None
        effective_as_of = as_of or datetime.now(timezone.utc)
        if latest_data_at is not None and latest_data_at.tzinfo is None:
            latest_data_at = latest_data_at.replace(tzinfo=timezone.utc)
        freshness_days = None
        if latest_data_at is not None:
            freshness_days = max(
                Decimal("0"),
                Decimal(str((effective_as_of - latest_data_at).total_seconds())) / Decimal("86400"),
            )

        assets = sorted({recommendation.asset for recommendation in active})
        return {
            "sample_size": sample_size,
            "win_rate_pct": (Decimal(winning) / Decimal(sample_size) * Decimal("100")) if sample_size else Decimal("0"),
            "total_pnl_pct": total_pnl,
            "max_drawdown_pct": max_drawdown,
            "active_recommendations": len(active),
            "activated_recommendations": len(activated),
            "active_assets": assets,
            # Kept for compatibility; consumers should prefer risk_exposure_pct.
            "exposure_proxy": len(active),
            "risk_exposure_pct": risk_exposure_pct,
            "window_days": window_days,
            "as_of": effective_as_of,
            "latest_data_at": latest_data_at,
            "freshness_days": freshness_days,
            "eligible_for_ranking": sample_size >= self.minimum_sample_size,
            "minimum_sample_size": self.minimum_sample_size,
        }

    def _recommendations_for(
        self,
        session: Session,
        analyst_id: int,
        *,
        window_days: int | None = None,
        as_of: datetime | None = None,
    ) -> list[Recommendation]:
        query = (
            select(Recommendation)
            .where(
                Recommendation.analyst_id == analyst_id,
                Recommendation.is_shadow.is_(False),
            )
            .order_by(Recommendation.created_at.asc(), Recommendation.id.asc())
        )
        if window_days:
            effective_as_of = as_of or datetime.now(timezone.utc)
            query = query.where(
                Recommendation.created_at >= effective_as_of - timedelta(days=max(1, int(window_days)))
            )
        return session.execute(query).scalars().all()

    def get_analyst(
        self,
        session: Session,
        analyst_id: int,
        *,
        window_days: int | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any] | None:
        row = session.execute(
            select(User, AnalystProfile)
            .outerjoin(AnalystProfile, AnalystProfile.user_id == User.id)
            .where(User.id == analyst_id, User.user_type == UserType.ANALYST)
        ).first()
        if row is None:
            return None
        user, profile = row
        stats = self._stats(
            self._recommendations_for(session, analyst_id, window_days=window_days, as_of=as_of),
            window_days=window_days,
            as_of=as_of,
        )
        return {
            "analyst_id": user.id,
            "analyst_code": user.analyst_code,
            "public_ref": user.public_ref,
            "public_name": (profile.public_name if profile else None) or user.first_name or user.username or user.analyst_code,
            "bio": profile.bio if profile else None,
            "specialty_market": profile.specialty_market if profile else None,
            "strategy_style": profile.strategy_style if profile else None,
            "is_public": bool(profile and profile.is_public),
            **stats,
        }

    def find_analysts(
        self,
        session: Session,
        search: str | None = None,
        include_ineligible: bool = False,
        limit: int = 20,
        window_days: int | None = None,
    ) -> list[dict[str, Any]]:
        query = (
            select(User.id)
            .outerjoin(AnalystProfile, AnalystProfile.user_id == User.id)
            .where(User.user_type == UserType.ANALYST)
            .where((AnalystProfile.is_public.is_(True)) | (AnalystProfile.id.is_(None)))
            .order_by(User.id.asc())
        )
        candidate_ids = [row[0] for row in session.execute(query).all()]
        results = []
        needle = search.strip().lower() if search else None
        for analyst_id in candidate_ids:
            record = self.get_analyst(session, analyst_id, window_days=window_days)
            if record is None:
                continue
            haystack = " ".join(
                str(record.get(key) or "")
                for key in (
                    "analyst_code",
                    "public_ref",
                    "public_name",
                    "bio",
                    "specialty_market",
                    "strategy_style",
                )
            ).lower()
            if needle and needle not in haystack:
                continue
            if not include_ineligible and not record["eligible_for_ranking"]:
                continue
            results.append(record)

        results.sort(
            key=lambda item: (
                item["eligible_for_ranking"],
                item["win_rate_pct"],
                item["sample_size"],
                -item["max_drawdown_pct"],
            ),
            reverse=True,
        )
        return results[: max(1, min(int(limit or 20), 50))]
