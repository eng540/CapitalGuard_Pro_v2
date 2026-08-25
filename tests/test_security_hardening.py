import pytest
from fastapi import HTTPException

from capitalguard.interfaces.api.security import auth
from capitalguard.interfaces.api import deps
from capitalguard.interfaces.api.main import _PersistenceCodec


def test_jwt_validation_rejects_missing_or_default_secret(monkeypatch):
    monkeypatch.setattr(auth, "JWT_SECRET", "")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        auth.validate_security_settings()

    monkeypatch.setattr(auth, "JWT_SECRET", "short-unsafe-secret")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        auth.validate_security_settings()


def test_access_token_round_trip_works_with_valid_security_settings(monkeypatch):
    monkeypatch.setattr(auth, "JWT_SECRET", "x" * 48)
    monkeypatch.setattr(auth, "JWT_ALG", "HS256")
    monkeypatch.setattr(auth, "JWT_EXPIRE_MIN", 60)

    token = auth.create_access_token("user-42", roles=["TRADER"])
    claims = auth.decode_token(token)

    assert claims["sub"] == "user-42"
    assert claims["roles"] == ["TRADER"]


def test_jwt_validation_accepts_strong_hs256_secret(monkeypatch):
    monkeypatch.setattr(auth, "JWT_SECRET", "x" * 48)
    monkeypatch.setattr(auth, "JWT_ALG", "HS256")
    monkeypatch.setattr(auth, "JWT_EXPIRE_MIN", 60)
    auth.validate_security_settings()


def test_api_key_dependency_fails_closed_when_unconfigured(monkeypatch):
    monkeypatch.setattr(deps.settings, "API_KEY", None)
    with pytest.raises(HTTPException) as exc:
        deps.require_api_key(None)
    assert exc.value.status_code == 503


def test_api_key_dependency_rejects_invalid_key(monkeypatch):
    monkeypatch.setattr(deps.settings, "API_KEY", "configured-secret")
    with pytest.raises(HTTPException) as exc:
        deps.require_api_key("wrong-secret")
    assert exc.value.status_code == 401
    assert deps.require_api_key("configured-secret") is True


def test_persistence_codec_round_trip_preserves_supported_types():
    original = {1: {"tags": {"one", "two"}, "state": ("OPEN", 1)}}
    encoded = _PersistenceCodec.encode(original)
    assert _PersistenceCodec.decode(encoded) == original


def test_persistence_codec_rejects_unknown_types():
    with pytest.raises(TypeError, match="Unsupported persistence type"):
        _PersistenceCodec.encode(object())
