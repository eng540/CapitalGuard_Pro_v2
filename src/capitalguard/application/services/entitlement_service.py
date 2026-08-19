"""R3 non-commercial entitlements and zero-value ledger service."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from capitalguard.config import settings
from capitalguard.infrastructure.db.models import EntitlementGrant, SubscriptionLedgerEntry


class BillingDisabledError(RuntimeError):
    """Raised when a commercial operation is attempted before the R3 gate opens."""


class EntitlementService:
    """Auditable feature grants for Alpha; deliberately contains no charge operation."""

    def __init__(self, billing_enabled: bool | None = None):
        self.billing_enabled = settings.BILLING_ENABLED if billing_enabled is None else bool(billing_enabled)

    def assert_commercially_disabled(self) -> None:
        if self.billing_enabled:
            raise BillingDisabledError(
                "Commercial billing is not implemented in this R3 slice; keep BILLING_ENABLED=false."
            )

    @staticmethod
    def _utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def grant_alpha(
        self,
        session: Session,
        user_id: int,
        feature_codes: Iterable[str],
        *,
        idempotency_key: str,
        actor_user_id: int | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> list[EntitlementGrant]:
        """Grant zero-cost Alpha features; repeated keys are safe and do not duplicate rows."""
        self.assert_commercially_disabled()
        features = sorted({str(code).strip().upper() for code in feature_codes if str(code).strip()})
        if not features:
            raise ValueError("At least one feature code is required")
        if len(idempotency_key.strip()) < 8:
            raise ValueError("idempotency_key must contain at least 8 characters")

        now = self._utc(starts_at) or datetime.now(timezone.utc)
        expiry = self._utc(ends_at)
        grants: list[EntitlementGrant] = []
        for feature_code in features:
            grant_key = f"{idempotency_key.strip()}:{feature_code}"
            existing = session.execute(
                select(EntitlementGrant).where(EntitlementGrant.idempotency_key == grant_key)
            ).scalar_one_or_none()
            if existing is not None:
                grants.append(existing)
                continue
            ledger_key = f"{grant_key}:ledger"
            ledger = session.execute(
                select(SubscriptionLedgerEntry).where(
                    SubscriptionLedgerEntry.idempotency_key == ledger_key
                )
            ).scalar_one_or_none()
            if ledger is None:
                session.add(
                    SubscriptionLedgerEntry(
                        user_id=user_id,
                        entry_type="ALPHA_GRANT",
                        plan_code="ALPHA",
                        feature_code=feature_code,
                        amount_minor=0,
                        currency="USD",
                        provider="INTERNAL",
                        status="RECORDED",
                        actor_user_id=actor_user_id,
                        idempotency_key=ledger_key,
                        metadata_json=metadata or {},
                        occurred_at=now,
                    )
                )
            grant = EntitlementGrant(
                user_id=user_id,
                feature_code=feature_code,
                source="ALPHA_GRANT",
                status="GRANTED",
                starts_at=now,
                ends_at=expiry,
                actor_user_id=actor_user_id,
                idempotency_key=grant_key,
                metadata_json=metadata or {},
            )
            session.add(grant)
            grants.append(grant)
        session.flush()
        return grants

    def revoke(
        self,
        session: Session,
        user_id: int,
        feature_code: str,
        *,
        idempotency_key: str,
        actor_user_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EntitlementGrant:
        """Append a revoke decision; no grant row is deleted."""
        self.assert_commercially_disabled()
        feature = str(feature_code).strip().upper()
        if not feature:
            raise ValueError("feature_code is required")
        if len(idempotency_key.strip()) < 8:
            raise ValueError("idempotency_key must contain at least 8 characters")
        revoke_key = f"{idempotency_key.strip()}:{feature}:revoke"
        existing = session.execute(
            select(EntitlementGrant).where(EntitlementGrant.idempotency_key == revoke_key)
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        now = datetime.now(timezone.utc)
        session.add(
            SubscriptionLedgerEntry(
                user_id=user_id,
                entry_type="REVOKE",
                plan_code="ALPHA",
                feature_code=feature,
                amount_minor=0,
                currency="USD",
                provider="INTERNAL",
                status="RECORDED",
                actor_user_id=actor_user_id,
                idempotency_key=f"{revoke_key}:ledger",
                metadata_json=metadata or {},
                occurred_at=now,
            )
        )
        decision = EntitlementGrant(
            user_id=user_id,
            feature_code=feature,
            source="INTERNAL",
            status="REVOKED",
            starts_at=now,
            revoked_at=now,
            actor_user_id=actor_user_id,
            idempotency_key=revoke_key,
            metadata_json=metadata or {},
        )
        session.add(decision)
        session.flush()
        return decision

    def has_feature(self, session: Session, user_id: int, feature_code: str, *, at: datetime | None = None) -> bool:
        feature = str(feature_code).strip().upper()
        moment = self._utc(at) or datetime.now(timezone.utc)
        rows = session.execute(
            select(EntitlementGrant)
            .where(
                EntitlementGrant.user_id == user_id,
                EntitlementGrant.feature_code == feature,
                EntitlementGrant.starts_at <= moment,
            )
            .order_by(desc(EntitlementGrant.created_at), desc(EntitlementGrant.id))
        ).scalars().all()
        if not rows:
            return False
        latest = rows[0]
        return latest.status == "GRANTED" and (latest.ends_at is None or latest.ends_at >= moment)

    def list_active(self, session: Session, user_id: int, *, at: datetime | None = None) -> list[str]:
        moment = self._utc(at) or datetime.now(timezone.utc)
        rows = session.execute(
            select(EntitlementGrant)
            .where(
                EntitlementGrant.user_id == user_id,
                EntitlementGrant.starts_at <= moment,
            )
            .order_by(EntitlementGrant.feature_code.asc(), desc(EntitlementGrant.created_at), desc(EntitlementGrant.id))
        ).scalars().all()
        active: dict[str, bool] = {}
        for row in rows:
            if row.feature_code in active:
                continue
            active[row.feature_code] = (
                row.status == "GRANTED"
                and (row.ends_at is None or row.ends_at >= moment)
            )
        return sorted(feature for feature, is_active in active.items() if is_active)
