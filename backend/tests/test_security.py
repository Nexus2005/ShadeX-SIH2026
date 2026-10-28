import jwt
import pytest
from fastapi import HTTPException

from app.config import get_settings
from app.security import _decode_token, hash_password, verify_password


def test_password_hash_roundtrip():
    digest = hash_password("Password123!")
    assert digest != "Password123!"
    assert verify_password("Password123!", digest) is True
    assert verify_password("wrong-password", digest) is False


def test_password_hash_is_salted():
    assert hash_password("same") != hash_password("same")


def test_verify_password_survives_malformed_hash():
    assert verify_password("x", "not-a-bcrypt-hash") is False


def test_decode_token_rejects_tampering():
    settings = get_settings()
    token = jwt.encode({"sub": "u", "sid": "s"}, "some-other-key", algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        _decode_token(token)
    assert exc.value.status_code == 401


def test_decode_token_rejects_expired_session():
    settings = get_settings()
    token = jwt.encode(
        {"sub": "u", "sid": "s", "iat": 1, "exp": 2},
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        _decode_token(token)
    assert exc.value.status_code == 401
