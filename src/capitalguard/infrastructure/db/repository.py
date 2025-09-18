# --- START OF FINAL, PRODUCTION-READY FILE (Version 8.1.4.1) ---
# src/capitalguard/infrastructure/db/repository.py
"""
Production-ready repository layer for CapitalGuard Pro.

Improvements made compared to previous version:
- Dialect-aware row locking with strict guard for production on SQLite.
- Robust serialization/deserialization of `targets` and `events`.
- Full-field update implementation in `update_with_event`.
- Safe `find_or_create` that handles concurrent inserts (IntegrityError).
- Defensive null / type handling for enum/value objects.
- Structured logging for errors and useful debug context.
- Minimal side-effects (no commit inside repository) — leave transaction boundary to service layer.
"""

import logging
import os
from datetime import datetime, timezone
from typing import List, Optional, Any, Dict

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from capitalguard.domain.entities import (
    Recommendation,
    RecommendationStatus,
    OrderType,
    ExitStrategy,
)
from capitalguard.domain.value_objects import Symbol, Price, Targets, Side
from .models import RecommendationORM, User, Channel, RecommendationEvent

log = logging.getLogger(__name__)


def _unwrap_value(v: Any):
    """
    Safely unwrap a value object/enum to its primitive value if possible.
    """
    if v is None:
        return None
    if hasattr(v, "value"):
        try:
            return v.value
        except Exception:
            return v
    if hasattr(v, "dict") and callable(v.dict):
        try:
            return v.dict()
        except Exception:
            pass
    if hasattr(v, "__dict__"):
        return {k: _unwrap_value(vv) for k, vv in v.__dict__.items() if not k.startswith("_")}
    return v


def _serialize_targets(targets_obj: Any) -> List[Dict[str, Any]]:
    """
    Normalizes Targets value object (or plain list/dict) into a list of plain dicts
    suitable for JSON storage in DB.
    """
    if targets_obj is None:
        return []
    if hasattr(targets_obj, "values"):
        vals = targets_obj.values
    elif isinstance(targets_obj, list):
        vals = targets_obj
    else:
        try:
            vals = list(targets_obj)
        except Exception:
            return []

    out = []
    for t in vals:
        if t is None:
            continue
        if hasattr(t, "dict") and callable(t.dict):
            try:
                d = t.dict()
                out.append({k: _unwrap_value(v) for k, v in d.items()})
                continue
            except Exception:
                pass
        if hasattr(t, "__dict__"):
            d = {k: _unwrap_value(v) for k, v in t.__dict__.items() if not k.startswith("_")}
            out.append(d)
            continue
        if isinstance(t, dict):
            out.append({k: _unwrap_value(v) for k, v in t.items()})
            continue
        out.append({"value": _unwrap_value(t)})
    return out


def _events_to_dicts(events):
    """Convert ORM event objects (or dicts) to plain dicts for domain layer consumption."""
    if not events:
        return []
    out = []
    for ev in events:
        try:
            ev_dict = {
                "id": getattr(ev, "id", None),
                "event_type": getattr(ev, "event_type", None),
                "event_timestamp": getattr(ev, "event_timestamp", None),
                "event_data": getattr(ev, "event_data", None),
            }
            out.append(ev_dict)
        except Exception as e:
            log.exception("Failed to convert event to dict: %s", e)
    return out


class UserRepository:
    """Manages database operations for User entities within a given session."""

    def __init__(self, session: Session):
        self.session = session

    def find_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        return self.session.query(User).filter(User.telegram_user_id == telegram_id).first()

    def find_or_create(self, telegram_id: int, **kwargs) -> User:
        """
        Safe find_or_create for concurrent inserts.
        """
        user = self.find_by_telegram_id(telegram_id)
        if user:
            return user

        log.info("Creating new user for telegram_id=%s", telegram_id)
        new_user = User(
            telegram_user_id=telegram_id,
            email=kwargs.get("email") or f"tg{telegram_id}@telegram.local",
            user_type=(kwargs.get("user_type") or "trader"),
            is_active=kwargs.get("is_active", True),
            first_name=kwargs.get("first_name"),
        )
        self.session.add(new_user)
        try:
            self.session.flush()
            self.session.refresh(new_user)
            return new_user
        except IntegrityError as ie:
            log.warning("IntegrityError while creating user telegram_id=%s, err=%s", telegram_id, ie)
            try:
                self.session.rollback()
            except Exception:
                log.exception("Rollback after IntegrityError failed.")
                raise
            existing = self.find_by_telegram_id(telegram_id)
            if existing:
                return existing
            raise


class ChannelRepository:
    """Manages database operations for Channel entities within a given session."""

    def __init__(self, session: Session):
        self.session = session

    def list_by_user(self, user_id: int, only_active: bool = False) -> List[Channel]:
        q = self.session.query(Channel).filter(Channel.user_id == user_id)
        if only_active:
            q = q.filter(Channel.is_active.is_(True))
        return q.order_by(Channel.created_at.desc()).all()


class RecommendationRepository:
    """Manages all database operations for Recommendation entities."""

    @staticmethod
    def _to_entity(row: RecommendationORM) -> Optional[Recommendation]:
        if not row:
            return None
        user_telegram_id = (
            str(row.user.telegram_user_id)
            if getattr(row, "user", None) and getattr(row.user, "telegram_user_id", None)
            else None
        )
        targets = row.targets if row.targets is not None else []
        events = _events_to_dicts(getattr(row, "events", []) or [])

        return Recommendation(
            id=row.id,
            asset=Symbol(row.asset) if row.asset else None,
            side=Side(row.side) if row.side else None,
            entry=Price(row.entry) if row.entry is not None else None,
            stop_loss=Price(row.stop_loss) if row.stop_loss is not None else None,
            targets=Targets(targets),
            order_type=OrderType(row.order_type) if row.order_type else None,
            status=RecommendationStatus(row.status) if row.status else None,
            market=row.market,
            notes=row.notes,
            user_id=user_telegram_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            exit_price=row.exit_price,
            activated_at=row.activated_at,
            closed_at=row.closed_at,
            alert_meta=dict(row.alert_meta or {}),
            highest_price_reached=row.highest_price_reached,
            lowest_price_reached=row.lowest_price_reached,
            exit_strategy=ExitStrategy(row.exit_strategy) if row.exit_strategy else None,
            profit_stop_price=row.profit_stop_price,
            open_size_percent=row.open_size_percent,
            events=events,
        )

    def _ensure_production_dialect_safe(self, session: Session):
        env = os.getenv("APP_ENV", os.getenv("ENV", "development")).lower()
        dialect = None
        try:
            dialect = session.bind.dialect.name if session.bind else None
        except Exception:
            pass
        if env == "production" and dialect == "sqlite":
            raise RuntimeError("Unsafe: Production with SQLite is not allowed.")

    def get_for_update(self, session: Session, rec_id: int) -> Optional[RecommendationORM]:
        self._ensure_production_dialect_safe(session)
        q = session.query(RecommendationORM).filter(RecommendationORM.id == rec_id)
        if session.bind and getattr(session.bind, "dialect", None) and session.bind.dialect.name != "sqlite":
            q = q.with_for_update()
        else:
            log.warning("SQLite/unknown dialect: FOR UPDATE skipped.")
        return q.first()

    def add_with_event(self, session: Session, rec: Recommendation) -> Recommendation:
        user = UserRepository(session).find_or_create(int(rec.user_id))
        row = RecommendationORM(
            user_id=user.id,
            asset=_unwrap_value(rec.asset),
            side=_unwrap_value(rec.side),
            entry=_unwrap_value(rec.entry),
            stop_loss=_unwrap_value(rec.stop_loss),
            targets=_serialize_targets(getattr(rec, "targets", None)),
            order_type=_unwrap_value(rec.order_type),
            status=_unwrap_value(rec.status),
            market=rec.market,
            notes=rec.notes,
            activated_at=rec.activated_at,
            exit_strategy=_unwrap_value(rec.exit_strategy),
            profit_stop_price=rec.profit_stop_price,
            open_size_percent=rec.open_size_percent,
        )
        session.add(row)
        session.flush()

        create_event = RecommendationEvent(
            recommendation_id=row.id,
            event_type="CREATE",
            event_timestamp=row.created_at or datetime.now(timezone.utc),
            event_data={"entry": _unwrap_value(rec.entry), "sl": _unwrap_value(rec.stop_loss)},
        )
        session.add(create_event)
        session.flush()
        session.refresh(row, attribute_names=["user", "events"])
        return self._to_entity(row)

    def update_with_event(self, session: Session, rec: Recommendation, event_type: str, event_data: Dict[str, Any]) -> Recommendation:
        if not rec.id:
            raise ValueError("Recommendation id required.")
        row = self.get_for_update(session, rec.id)
        if not row:
            raise ValueError(f"Recommendation #{rec.id} not found.")

        # Update mutable fields
        if rec.status: row.status = _unwrap_value(rec.status)
        if rec.stop_loss: row.stop_loss = _unwrap_value(rec.stop_loss)
        if rec.entry: row.entry = _unwrap_value(rec.entry)
        if rec.order_type: row.order_type = _unwrap_value(rec.order_type)
        if rec.market: row.market = rec.market
        if rec.notes: row.notes = rec.notes
        if rec.targets: row.targets = _serialize_targets(rec.targets)
        if rec.exit_price: row.exit_price = _unwrap_value(rec.exit_price)
        if rec.profit_stop_price: row.profit_stop_price = _unwrap_value(rec.profit_stop_price)
        if rec.open_size_percent: row.open_size_percent = rec.open_size_percent
        if rec.activated_at: row.activated_at = rec.activated_at
        if rec.closed_at: row.closed_at = rec.closed_at
        if rec.highest_price_reached: row.highest_price_reached = rec.highest_price_reached
        if rec.lowest_price_reached: row.lowest_price_reached = rec.lowest_price_reached
        if rec.exit_strategy: row.exit_strategy = _unwrap_value(rec.exit_strategy)
        if rec.alert_meta: row.alert_meta = dict(rec.alert_meta or {})
        if rec.updated_at: row.updated_at = rec.updated_at

        new_event = RecommendationEvent(
            recommendation_id=row.id,
            event_type=event_type,
            event_timestamp=event_data.get("event_timestamp", datetime.now(timezone.utc)),
            event_data=event_data,
        )
        session.add(new_event)
        session.flush()
        session.refresh(row, attribute_names=["user", "events"])
        return self._to_entity(row)
# --- END OF FINAL, PRODUCTION-READY FILE (Version 8.1.4.1) ---