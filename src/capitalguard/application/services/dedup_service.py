from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import DedupLedger


class DedupDecision:
    def __init__(self, duplicate: bool, fingerprint: str, ledger: Optional[DedupLedger] = None):
        self.duplicate = duplicate
        self.fingerprint = fingerprint
        self.ledger = ledger


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


class DedupLedgerService:
    """R1 durable deduplication for one user/channel/window."""

    def __init__(self, window_seconds: int = 300):
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.window_seconds = int(window_seconds)

    def fingerprint(
        self,
        *,
        asset: str,
        side: str,
        entry: Any,
        stop_loss: Any,
        targets: Any,
        source_text: Optional[str] = None,
    ) -> str:
        payload = {
            "asset": (asset or "").strip().upper(),
            "side": (side or "").strip().upper(),
            "entry": _json_default(entry),
            "stop_loss": _json_default(stop_loss),
            "targets": targets,
            "source_text": (source_text or "").strip(),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def window_start(self, now: Optional[datetime] = None) -> datetime:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        epoch = int(current.timestamp())
        bucket = epoch - (epoch % self.window_seconds)
        return datetime.fromtimestamp(bucket, tz=timezone.utc)

    def check_and_record(
        self,
        session: Session,
        *,
        user_id: int,
        source_channel_id: Optional[int],
        asset: str,
        side: str,
        entry: Any,
        stop_loss: Any,
        targets: Any,
        source_text: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> DedupDecision:
        fingerprint = self.fingerprint(
            asset=asset,
            side=side,
            entry=entry,
            stop_loss=stop_loss,
            targets=targets,
            source_text=source_text,
        )
        started = self.window_start(now)
        stmt = select(DedupLedger).where(
            DedupLedger.user_id == user_id,
            DedupLedger.source_channel_id == source_channel_id,
            DedupLedger.fingerprint == fingerprint,
            DedupLedger.window_started_at == started,
        )
        existing = session.execute(stmt).scalar_one_or_none()
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        if existing:
            existing.last_seen_at = current
            existing.outcome = "duplicate"
            return DedupDecision(True, fingerprint, existing)

        ledger = DedupLedger(
            user_id=user_id,
            source_channel_id=source_channel_id,
            fingerprint=fingerprint,
            window_started_at=started,
            first_seen_at=current,
            last_seen_at=current,
            outcome="accepted",
        )
        try:
            with session.begin_nested():
                session.add(ledger)
                session.flush()
        except IntegrityError:
            existing = session.execute(stmt).scalar_one_or_none()
            if existing:
                existing.last_seen_at = current
                existing.outcome = "duplicate"
                return DedupDecision(True, fingerprint, existing)
            raise
        return DedupDecision(False, fingerprint, ledger)

    def mark_entity(self, ledger: DedupLedger, *, entity_type: str, entity_id: int, outcome: str = "accepted") -> None:
        ledger.entity_type = entity_type
        ledger.entity_id = entity_id
        ledger.outcome = outcome
