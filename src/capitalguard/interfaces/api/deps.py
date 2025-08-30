# --- START OF FILE: src/capitalguard/interfaces/api/deps.py ---
from __future__ import annotations
from fastapi import Header, HTTPException
from typing import Optional, List
from dataclasses import dataclass

from capitalguard.config import settings
from capitalguard.infrastructure.db.base import SessionLocal, engine

@dataclass
class CurrentUser:
    id: Optional[int]
    roles: List[str]

def require_api_key(x_api_key: str | None = Header(default=None)):
    if settings.API_KEY and x_api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True

def get_current_user(x_user_id: Optional[int] = Header(default=None)) -> CurrentUser:
    roles: List[str] = []
    uid = None
    try:
        if x_user_id is not None:
            uid = int(x_user_id)
            db = SessionLocal()
            try:
                from capitalguard.infrastructure.db.models.auth import User, Role, UserRole  # noqa
                u = db.query(User).filter(User.id == uid).one_or_none()
                if u:
                    rs = (
                        db.query(Role.name)
                        .join(UserRole, UserRole.role_id == Role.id)
                        .filter(UserRole.user_id == uid)
                        .all()
                    )
                    roles = [r[0] for r in rs]
            finally:
                db.close()
    except Exception:
        pass
    return CurrentUser(id=uid, roles=[r.upper() for r in roles])

def is_admin(user: CurrentUser) -> bool:
    return "ADMIN" in (user.roles or [])

def ping_db() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False
# --- END OF FILE ---