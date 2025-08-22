from fastapi import Header, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from capitalguard.config import settings

limiter = Limiter(key_func=get_remote_address)

def require_api_key(x_api_key: str | None = Header(default=None)):
    if settings.API_KEY and x_api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True
