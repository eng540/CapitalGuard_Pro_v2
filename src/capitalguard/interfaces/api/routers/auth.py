from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel

from capitalguard.config import settings
from capitalguard.infrastructure.db.base import get_session

router = APIRouter(prefix="/auth", tags=["Authentication"])


class UserCreate(BaseModel):
    email: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str


def _local_auth_disabled() -> None:
    if not settings.ENABLE_LOCAL_AUTH:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Local authentication is disabled; use the approved Telegram identity flow.",
        )


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_user(user_in: UserCreate, db: Session = Depends(get_session)):
    """Local registration is opt-in and remains disabled until its schema is enabled."""
    _local_auth_disabled()
    raise HTTPException(status_code=501, detail="Local authentication is not implemented")


@router.post("/token", response_model=Token)
def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_session),
):
    """Local password login is opt-in; Telegram authentication is the supported flow."""
    _local_auth_disabled()
    raise HTTPException(status_code=501, detail="Local authentication is not implemented")
