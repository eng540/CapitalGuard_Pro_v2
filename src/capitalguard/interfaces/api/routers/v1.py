"""Versioned, non-financial Core API surface.

This router establishes the public contract boundary for future R4 APIs.  It
contains only service metadata and deliberately exposes no trader, analyst,
recommendation, portfolio, historical, or operational data.
"""

from collections import defaultdict, deque
from threading import Lock
from time import monotonic

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/api/v1", tags=["v1"])
STATUS_RATE_LIMIT_PER_MINUTE = 60
_status_requests: dict[str, deque[float]] = defaultdict(deque)
_status_requests_lock = Lock()


def reset_status_rate_limiter() -> None:
    """Test-only reset for the in-process public status burst guard."""
    with _status_requests_lock:
        _status_requests.clear()


def enforce_public_status_rate_limit(request: Request) -> int:
    """Apply a per-process, client-address burst guard to the public metadata route.

    Web service-to-service endpoints and Telegram webhooks are intentionally outside
    this guard. Edge infrastructure remains responsible for distributed limiting.
    """
    client_key = request.client.host if request.client else "unknown"
    now = monotonic()
    with _status_requests_lock:
        bucket = _status_requests[client_key]
        while bucket and now - bucket[0] >= 60:
            bucket.popleft()
        if len(bucket) >= STATUS_RATE_LIMIT_PER_MINUTE:
            retry_after = max(1, int(60 - (now - bucket[0])))
            raise HTTPException(status_code=429, detail="Public status rate limit exceeded", headers={"Retry-After": str(retry_after), "X-RateLimit-Limit": str(STATUS_RATE_LIMIT_PER_MINUTE), "X-RateLimit-Remaining": "0"})
        bucket.append(now)
        return STATUS_RATE_LIMIT_PER_MINUTE - len(bucket)


@router.get("/status")
def status(request: Request) -> dict[str, str]:
    """Return a versioned readiness contract without financial data."""
    remaining = enforce_public_status_rate_limit(request)
    app = request.app
    if not app.state.ready or not app.state.ptb_app or not app.state.services:
        raise HTTPException(status_code=503, detail="Service is not ready")
    payload = {
        "api_version": "v1",
        "service": "capitalguard-core",
        "status": "ok",
        "commercial_mode": "noncommercial",
    }
    request.state.status_rate_limit_remaining = remaining
    return payload
