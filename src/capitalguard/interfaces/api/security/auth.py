from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
import os

import jwt
from passlib.context import CryptContext


ALLOWED_JWT_ALGORITHMS = {"HS256"}
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALG = os.getenv("JWT_ALG", "HS256").upper()
JWT_EXPIRE_MIN = int(os.getenv("JWT_EXPIRE_MIN", "60"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def validate_security_settings() -> None:
    """Fail closed when JWT is configured with an unsafe secret or algorithm."""
    if not JWT_SECRET or len(JWT_SECRET) < 32:
        raise RuntimeError(
            "JWT_SECRET must be set to a random value of at least 32 characters."
        )
    if JWT_ALG not in ALLOWED_JWT_ALGORITHMS:
        raise RuntimeError(
            f"Unsupported JWT_ALG={JWT_ALG!r}; allowed values: {sorted(ALLOWED_JWT_ALGORITHMS)}"
        )
    if not 1 <= JWT_EXPIRE_MIN <= 24 * 60:
        raise RuntimeError("JWT_EXPIRE_MIN must be between 1 and 1440 minutes.")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str, roles: Optional[Iterable[str]] = None) -> str:
    validate_security_settings()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=JWT_EXPIRE_MIN)
    payload = {
        "sub": subject,
        "roles": list(roles or []),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token: str) -> dict:
    validate_security_settings()
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
