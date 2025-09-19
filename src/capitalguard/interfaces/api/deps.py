# --- START OF FINAL, ROBUST FILE USING SERVICE REGISTRY (Version 9.3.0) ---
# src/capitalguard/interfaces/api/deps.py

from __future__ import annotations
from fastapi import Header, HTTPException, Request, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional, List, Set
from dataclasses import dataclass

from capitalguard.config import settings
from capitalguard.interfaces.api.security.auth import decode_token
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.analytics_service import AnalyticsService
# ✅ Import the new global service getter, which is now the single source of truth.
from capitalguard.service_registry import get_global_service

# --- Security & Auth Dependencies ---

bearer_scheme = HTTPBearer(auto_error=False)

@dataclass
class CurrentUser:
    """A unified user object representing the authenticated user."""
    sub: str  # Subject, usually the email
    roles: List[str]
    is_authenticated: bool = False

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> CurrentUser:
    """
    Dependency to get the current user from a JWT Bearer token.
    Provides a guest user object if no token is present.
    """
    if creds is None:
        return CurrentUser(sub="guest", roles=[], is_authenticated=False)
    try:
        payload = decode_token(creds.credentials)
        return CurrentUser(
            sub=payload.get("sub", ""),
            roles=[role.upper() for role in payload.get("roles", [])],
            is_authenticated=True
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid or expired token"
        )

def require_roles(required: Set[str]):
    """
    Dependency that requires the current user to have at least one of the specified roles.
    """
    def _dependency(user: CurrentUser = Depends(get_current_user)):
        if not user.is_authenticated:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
        
        user_roles = set(user.roles)
        if not user_roles.intersection(required):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user
    return _dependency

def is_admin(user: CurrentUser = Depends(get_current_user)) -> bool:
    """Dependency that returns True if the current user is an admin."""
    return "ADMIN" in user.roles


# --- Service Dependencies ---

def get_trade_service() -> TradeService:
    """Dependency to get the TradeService instance from the global registry."""
    service = get_global_service("trade_service", TradeService)
    if not service:
        # This error indicates a fundamental problem with the application startup.
        raise HTTPException(status_code=503, detail="Trade service is currently unavailable.")
    return service

def get_analytics_service() -> AnalyticsService:
    """Dependency to get the AnalyticsService instance from the global registry."""
    service = get_global_service("analytics_service", AnalyticsService)
    if not service:
        # This error indicates a fundamental problem with the application startup.
        raise HTTPException(status_code=503, detail="Analytics service is currently unavailable.")
    return service

# --- API Key Dependency ---

def require_api_key(x_api_key: str | None = Header(default=None)):
    """Dependency to protect endpoints with a static API key."""
    if settings.API_KEY and x_api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True

# --- END OF FINAL, ROBUST FILE USING SERVICE REGISTRY (Version 9.3.0) ---