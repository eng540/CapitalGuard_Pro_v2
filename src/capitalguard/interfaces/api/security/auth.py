from datetime import datetime, timedelta
from typing import Optional, Iterable

from jose import jwt
from passlib.context import CryptContext
from os import getenv

JWT_SECRET = getenv("JWT_SECRET", "change-me-please")
JWT_ALG = getenv("JWT_ALG", "HS256")
JWT_EXPIRE_MIN = int(getenv("JWT_EXPIRE_MIN", "43200"))  # 30 days

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(subject: str, roles: Optional[Iterable[str]] = None) -> str:
    now = datetime.utcnow()
    exp = now + timedelta(minutes=JWT_EXPIRE_MIN)
    payload = {
        "sub": subject,
        "roles": list(roles or []),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def decode_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])