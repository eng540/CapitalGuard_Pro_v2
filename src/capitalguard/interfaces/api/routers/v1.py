"""Versioned, non-financial Core API surface.

This router establishes the public contract boundary for future R4 APIs.  It
contains only service metadata and deliberately exposes no trader, analyst,
recommendation, portfolio, historical, or operational data.
"""

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/api/v1", tags=["v1"])


@router.get("/status")
def status(request: Request) -> dict[str, str]:
    """Return a versioned readiness contract without financial data."""
    app = request.app
    if not app.state.ready or not app.state.ptb_app or not app.state.services:
        raise HTTPException(status_code=503, detail="Service is not ready")
    return {
        "api_version": "v1",
        "service": "capitalguard-core",
        "status": "ok",
        "commercial_mode": "noncommercial",
    }
